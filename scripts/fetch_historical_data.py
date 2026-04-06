"""BTC/KRW 역사 데이터 수집 스크립트.

업비트 상장일(2017-10) 이후 데이터를 cache.duckdb에 upsert.
실행: PYTHONUTF8=1 python scripts/fetch_historical_data.py
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ccxt.async_support as ccxt

from services.market_data.cache import init_schema, upsert_ohlcv, select_ohlcv
import duckdb

# 수집 파라미터
SYMBOL = "BTC/KRW"
TIMEFRAME = "1d"
EXCHANGE_ID = "upbit"

# 업비트 BTC/KRW 상장: 2017-10-01 (예상)
START_DATE = "2017-10-01T00:00:00Z"
END_DATE   = "2019-01-01T00:00:00Z"  # 기존 캐시 시작점까지 (중복 upsert 허용)

MAX_CANDLES_PER_REQUEST = 200
REQUEST_INTERVAL = 0.12  # 초 (Rate Limit 29 req/sec 준수)

DB_PATH = PROJECT_ROOT / "data" / "cache.duckdb"


def _iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def get_current_count() -> int:
    """현재 DuckDB에 저장된 BTC/KRW 일봉 총 캔들 수 조회."""
    with duckdb.connect(str(DB_PATH)) as con:
        result = con.execute("""
            SELECT COUNT(*) FROM ohlcv
            WHERE exchange='upbit' AND symbol='BTC/KRW' AND timeframe='1d'
        """).fetchone()
    return result[0] if result else 0


def get_date_range() -> tuple[str, str]:
    """현재 DuckDB의 BTC/KRW 일봉 날짜 범위 조회."""
    with duckdb.connect(str(DB_PATH)) as con:
        result = con.execute("""
            SELECT MIN(ts), MAX(ts) FROM ohlcv
            WHERE exchange='upbit' AND symbol='BTC/KRW' AND timeframe='1d'
        """).fetchone()
    if result and result[0]:
        return _ms_to_date(result[0]), _ms_to_date(result[1])
    return "N/A", "N/A"


async def fetch_and_store(start_ms: int, end_ms: int) -> int:
    """지정 범위 OHLCV를 fetch하여 DuckDB에 upsert. 수집된 캔들 수 반환."""
    exchange = ccxt.upbit({"enableRateLimit": True})
    all_rows: list[dict] = []
    since = start_ms
    page = 0

    try:
        while since < end_ms:
            page += 1
            candles = await exchange.fetch_ohlcv(
                SYMBOL, TIMEFRAME, since=since, limit=MAX_CANDLES_PER_REQUEST
            )
            if not candles:
                print(f"  [페이지 {page}] 데이터 없음 — 수집 종료")
                break

            batch_rows = []
            for c in candles:
                ts = c[0]
                if ts > end_ms:
                    break
                batch_rows.append({
                    "ts":     ts,
                    "open":   c[1],
                    "high":   c[2],
                    "low":    c[3],
                    "close":  c[4],
                    "volume": c[5],
                })

            if batch_rows:
                upsert_ohlcv(EXCHANGE_ID, SYMBOL, TIMEFRAME, batch_rows)
                all_rows.extend(batch_rows)
                first_date = _ms_to_date(batch_rows[0]["ts"])
                last_date  = _ms_to_date(batch_rows[-1]["ts"])
                print(f"  [페이지 {page}] {first_date} ~ {last_date} ({len(batch_rows)}개 저장)")

            last_ts = candles[-1][0]
            if last_ts <= since:
                print(f"  [페이지 {page}] 진행 없음 — 무한루프 방지 종료")
                break
            since = last_ts + 1
            await asyncio.sleep(REQUEST_INTERVAL)

    finally:
        await exchange.close()

    return len(all_rows)


async def main() -> None:
    print("=" * 60)
    print("BTC/KRW 역사 데이터 수집")
    print(f"수집 범위: {START_DATE[:10]} ~ {END_DATE[:10]}")
    print("=" * 60)

    # 스키마 초기화
    init_schema()

    # 수집 전 현황
    before_count = get_current_count()
    before_start, before_end = get_date_range()
    print(f"\n[수집 전] 총 캔들: {before_count}개  ({before_start} ~ {before_end})")

    start_ms = _iso_to_ms(START_DATE)
    end_ms   = _iso_to_ms(END_DATE)

    print(f"\n수집 시작...")
    t0 = time.time()
    fetched = await fetch_and_store(start_ms, end_ms)
    elapsed = time.time() - t0

    # 수집 후 현황
    after_count = get_current_count()
    after_start, after_end = get_date_range()

    print("\n" + "=" * 60)
    print("수집 완료")
    print(f"  - 이번 수집: {fetched}개 (소요: {elapsed:.1f}초)")
    print(f"  - 수집 전:   {before_count}개")
    print(f"  - 수집 후:   {after_count}개  ({after_start} ~ {after_end})")
    print(f"  - 순 증가:   {after_count - before_count}개")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
