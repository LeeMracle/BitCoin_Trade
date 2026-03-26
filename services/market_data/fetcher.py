"""업비트 OHLCV 조회 — ccxt 기반, 공개 시세 API (인증 불필요)."""
import asyncio
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt

from .cache import init_schema, upsert_ohlcv, select_ohlcv
from .models import OHLCVRecord

# 업비트 캔들 1회 최대 200개
_MAX_CANDLES_PER_REQUEST = 200
# 요청 간 최소 간격 (Rate Limit: 29 req/sec → 0.05s 여유)
_REQUEST_INTERVAL = 0.12  # seconds


def _exchange() -> ccxt.upbit:
    return ccxt.upbit({"enableRateLimit": True})


def _iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


async def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    use_cache: bool = True,
) -> list[dict]:
    """업비트 OHLCV 조회. 캐시 히트 구간은 재요청하지 않음."""
    init_schema()

    start_ms = _iso_to_ms(start)
    end_ms = _iso_to_ms(end)

    if use_cache:
        cached = select_ohlcv("upbit", symbol, timeframe, start_ms, end_ms)
        if cached:
            cached_start = cached[0]["ts"]
            cached_end = cached[-1]["ts"]
            # 캐시가 요청 범위를 완전히 커버하면 바로 반환
            if cached_start <= start_ms and cached_end >= end_ms:
                return [r for r in cached if start_ms <= r["ts"] <= end_ms]

    rows = await _fetch_range("upbit", symbol, timeframe, start_ms, end_ms)
    upsert_ohlcv("upbit", symbol, timeframe, rows)
    return rows


async def _fetch_range(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """페이지네이션으로 전체 범위 fetch."""
    exchange = _exchange()
    all_rows: list[dict] = []
    since = start_ms

    try:
        while since < end_ms:
            candles = await exchange.fetch_ohlcv(
                symbol, timeframe, since=since, limit=_MAX_CANDLES_PER_REQUEST
            )
            if not candles:
                break

            for c in candles:
                ts = c[0]
                if ts < start_ms:
                    continue
                if ts > end_ms:
                    break
                all_rows.append({
                    "ts": ts,
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                })

            last_ts = candles[-1][0]
            if last_ts <= since:
                break  # 진행 없음 — 무한루프 방지
            since = last_ts + 1
            await asyncio.sleep(_REQUEST_INTERVAL)
    finally:
        await exchange.close()

    return all_rows


async def fetch_fear_greed(start: str, end: str) -> list[dict]:
    """공포탐욕지수 — Alternative.me API (무인증)."""
    import aiohttp

    start_date = start[:10]
    end_date = end[:10]

    # Alternative.me는 최근 N일 단위로만 제공
    from datetime import date
    days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1
    days = max(days, 1)

    url = f"https://api.alternative.me/fng/?limit={days}&format=json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()

    rows = []
    for item in data.get("data", []):
        d = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d")
        if start_date <= d <= end_date:
            rows.append({
                "date": d,
                "value": float(item["value"]),
                "label": item.get("value_classification"),
            })

    return sorted(rows, key=lambda x: x["date"])
