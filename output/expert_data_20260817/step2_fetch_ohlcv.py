"""Step 2 — 상위 40종목 4h + 1d 데이터 fetch (2024-01-01 ~ 2026-08-16).

BTC/KRW 는 반드시 포함.
단일 ccxt 인스턴스 재사용, since 파라미터로 페이지네이션.
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import ccxt.async_support as ccxt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.market_data.cache import init_schema, upsert_ohlcv, select_ohlcv  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
UNIVERSE_JSON = OUT_DIR / "universe_screen.json"

START_ISO = "2024-01-01T00:00:00Z"
END_ISO = "2026-08-16T23:59:59Z"
TOP_N = 40


def iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


async def fetch_range(ex: ccxt.upbit, sym: str, tf: str, start_ms: int, end_ms: int) -> list[dict]:
    """페이지네이션 fetch → cache upsert. 캐시 있는 구간은 스킵."""
    # 캐시 확인
    cached = select_ohlcv("upbit", sym, tf, start_ms, end_ms)
    if cached:
        # 캐시가 완전 커버하는지
        if cached[0]["ts"] <= start_ms + 86_400_000 and cached[-1]["ts"] >= end_ms - 86_400_000:
            return cached
        # 부분 커버 → 뒷부분만 신규 요청
        fetch_from = cached[-1]["ts"] + 1
    else:
        fetch_from = start_ms

    all_rows = []
    since = fetch_from
    while since < end_ms:
        for attempt in range(3):
            try:
                candles = await ex.fetch_ohlcv(sym, tf, since=since, limit=200)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"    [FAIL] {sym} {tf} since={since}: {e}")
                    return cached + all_rows
                await asyncio.sleep(1.5)
        if not candles:
            break
        rows = []
        for c in candles:
            ts = c[0]
            if ts < start_ms or ts > end_ms:
                continue
            rows.append({"ts": ts, "open": c[1], "high": c[2],
                         "low": c[3], "close": c[4], "volume": c[5]})
        if rows:
            upsert_ohlcv("upbit", sym, tf, rows)
            all_rows.extend(rows)
        last_ts = candles[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1
        await asyncio.sleep(0.15)

    # 재조회로 정렬된 통합 반환
    return select_ohlcv("upbit", sym, tf, start_ms, end_ms)


async def main() -> None:
    init_schema()
    data = json.loads(UNIVERSE_JSON.read_text(encoding="utf-8"))
    candidates = [c["symbol"] for c in data["candidates"]][:TOP_N]

    if "BTC/KRW" not in candidates:
        candidates = ["BTC/KRW"] + candidates
    else:
        candidates = ["BTC/KRW"] + [c for c in candidates if c != "BTC/KRW"]

    print(f"[step2] fetching {len(candidates)} symbols")
    start_ms = iso_to_ms(START_ISO)
    end_ms = iso_to_ms(END_ISO)

    ex = ccxt.upbit({"enableRateLimit": True})
    results = []
    try:
        for i, sym in enumerate(candidates):
            print(f"[{i+1}/{len(candidates)}] {sym}")
            d1 = await fetch_range(ex, sym, "1d", start_ms, end_ms)
            h4 = await fetch_range(ex, sym, "4h", start_ms, end_ms)
            # 2024-01-01 부터 데이터가 있는지 확인
            first_d1 = d1[0]["ts"] if d1 else None
            first_h4 = h4[0]["ts"] if h4 else None
            has_from_2024 = (first_d1 is not None and first_d1 <= start_ms + 7 * 86_400_000
                             and first_h4 is not None and first_h4 <= start_ms + 7 * 86_400_000)
            print(f"    d1={len(d1)} h4={len(h4)} "
                  f"first_d1={datetime.fromtimestamp(first_d1/1000, tz=timezone.utc).date() if first_d1 else 'NA'} "
                  f"has_from_2024={has_from_2024}")
            results.append({"symbol": sym, "d1_count": len(d1), "h4_count": len(h4),
                             "first_d1_ts": first_d1, "first_h4_ts": first_h4,
                             "has_from_2024": has_from_2024})
    finally:
        await ex.close()

    log_path = OUT_DIR / "fetch_ohlcv_log.json"
    log_path.write_text(json.dumps(
        {"generated_at": datetime.now(tz=timezone.utc).isoformat(),
         "start": START_ISO, "end": END_ISO,
         "results": results,
         "usable_symbols": [r["symbol"] for r in results if r["has_from_2024"]],
         }, ensure_ascii=False, indent=2), encoding="utf-8")
    usable = [r["symbol"] for r in results if r["has_from_2024"]]
    print(f"\n[step2] usable (2024-01-01 부터 데이터 존재): {len(usable)}")
    print(f"[step2] saved: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())
