"""Step 2 v2 — 상위 40종목 4h + 1d 데이터 fetch (2024-01-01 ~ 2026-08-16).

v1의 문제: 캐시가 부분 커버 시 뒷부분만 fetch → 앞부분 gap 방치.
v2 개선:
- gap-fill: 캐시가 있어도 (min_ts > start_ms + 7d) 또는 (max_ts < end_ms - 7d) 이면 부족 구간 각각 backfill
- 상장 이전 감지: 첫 fetch 3회 연속 빈 응답 → 실제 상장 이후로 판정, 재fetch 스킵
- 진행 로그: 종목별 d1/h4 커버리지 · first/last · has_from_2024 · has_up_to_end 명시
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
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
DAY_MS = 86_400_000
TF_MS = {"1d": DAY_MS, "4h": 4 * 3600 * 1000}


def iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def ts_iso(ts: int | None) -> str:
    if ts is None:
        return "NA"
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()


async def paginate_forward(ex: ccxt.upbit, sym: str, tf: str,
                            since_ms: int, end_ms: int) -> int:
    """since_ms 부터 end_ms 까지 forward 페이지네이션. 새로 저장된 봉 수 반환."""
    added = 0
    since = since_ms
    empty_streak = 0
    while since < end_ms:
        for attempt in range(3):
            try:
                candles = await ex.fetch_ohlcv(sym, tf, since=since, limit=200)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"    [FAIL] {sym} {tf} since={ts_iso(since)}: {e}")
                    return added
                await asyncio.sleep(1.5)
        if not candles:
            empty_streak += 1
            if empty_streak >= 3:
                return added
            # skip forward one page
            since += 200 * TF_MS[tf]
            await asyncio.sleep(0.1)
            continue
        empty_streak = 0
        rows = []
        for c in candles:
            ts = c[0]
            if ts < since_ms or ts > end_ms:
                continue
            rows.append({"ts": ts, "open": c[1], "high": c[2],
                         "low": c[3], "close": c[4], "volume": c[5]})
        if rows:
            upsert_ohlcv("upbit", sym, tf, rows)
            added += len(rows)
        last_ts = candles[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1
        await asyncio.sleep(0.08)  # 12.5 req/sec ≤ 29 rate limit
    return added


async def fetch_and_gap_fill(ex: ccxt.upbit, sym: str, tf: str,
                              start_ms: int, end_ms: int) -> list[dict]:
    """캐시 확인 후 gap 부분(앞·뒤)만 forward fetch."""
    cached = select_ohlcv("upbit", sym, tf, start_ms, end_ms)
    if not cached:
        # 처음부터 전 구간
        added = await paginate_forward(ex, sym, tf, start_ms, end_ms)
        print(f"    fetch {tf}: [+{added}] full")
        return select_ohlcv("upbit", sym, tf, start_ms, end_ms)

    min_ts = cached[0]["ts"]
    max_ts = cached[-1]["ts"]
    total_added = 0

    # front gap (min_ts > start_ms + 7d)
    if min_ts > start_ms + 7 * DAY_MS:
        added = await paginate_forward(ex, sym, tf, start_ms, min_ts - 1)
        total_added += added
        print(f"    fetch {tf}: [+{added}] front gap (start~{ts_iso(min_ts)})")

    # tail gap (max_ts < end_ms - 7d)
    if max_ts < end_ms - 7 * DAY_MS:
        added = await paginate_forward(ex, sym, tf, max_ts + 1, end_ms)
        total_added += added
        print(f"    fetch {tf}: [+{added}] tail gap ({ts_iso(max_ts)}~end)")

    if total_added == 0:
        print(f"    fetch {tf}: cache OK (no gap)")
    return select_ohlcv("upbit", sym, tf, start_ms, end_ms)


async def main() -> None:
    init_schema()
    data = json.loads(UNIVERSE_JSON.read_text(encoding="utf-8"))
    candidates = [c["symbol"] for c in data["candidates"]][:TOP_N]

    if "BTC/KRW" not in candidates:
        candidates = ["BTC/KRW"] + candidates
    else:
        candidates = ["BTC/KRW"] + [c for c in candidates if c != "BTC/KRW"]

    print(f"[step2 v2] fetching {len(candidates)} symbols")
    print(f"[step2 v2] range: {START_ISO} ~ {END_ISO}")
    start_ms = iso_to_ms(START_ISO)
    end_ms = iso_to_ms(END_ISO)
    t0 = time.time()

    ex = ccxt.upbit({"enableRateLimit": True})
    results = []
    try:
        for i, sym in enumerate(candidates):
            print(f"\n[{i+1}/{len(candidates)}] {sym}")
            d1 = await fetch_and_gap_fill(ex, sym, "1d", start_ms, end_ms)
            h4 = await fetch_and_gap_fill(ex, sym, "4h", start_ms, end_ms)

            first_d1 = d1[0]["ts"] if d1 else None
            last_d1 = d1[-1]["ts"] if d1 else None
            first_h4 = h4[0]["ts"] if h4 else None
            last_h4 = h4[-1]["ts"] if h4 else None
            has_from_2024 = (first_d1 is not None and first_d1 <= start_ms + 7 * DAY_MS
                             and first_h4 is not None and first_h4 <= start_ms + 7 * DAY_MS)
            has_up_to_end = (last_d1 is not None and last_d1 >= end_ms - 7 * DAY_MS
                             and last_h4 is not None and last_h4 >= end_ms - 7 * DAY_MS)
            usable = has_from_2024 and has_up_to_end
            print(f"    -> d1={len(d1)} [{ts_iso(first_d1)}~{ts_iso(last_d1)}] "
                  f"h4={len(h4)} [{ts_iso(first_h4)}~{ts_iso(last_h4)}] "
                  f"from2024={has_from_2024} upToEnd={has_up_to_end} USABLE={usable}")
            results.append({"symbol": sym, "d1_count": len(d1), "h4_count": len(h4),
                             "first_d1": ts_iso(first_d1), "last_d1": ts_iso(last_d1),
                             "first_h4": ts_iso(first_h4), "last_h4": ts_iso(last_h4),
                             "has_from_2024": has_from_2024,
                             "has_up_to_end": has_up_to_end,
                             "usable": usable})
    finally:
        await ex.close()

    elapsed = time.time() - t0
    log_path = OUT_DIR / "fetch_ohlcv_log_v2.json"
    log_path.write_text(json.dumps(
        {"generated_at": datetime.now(tz=timezone.utc).isoformat(),
         "start": START_ISO, "end": END_ISO,
         "elapsed_sec": round(elapsed, 1),
         "results": results,
         "usable_symbols": [r["symbol"] for r in results if r["usable"]],
         }, ensure_ascii=False, indent=2), encoding="utf-8")
    usable = [r["symbol"] for r in results if r["usable"]]
    print(f"\n[step2 v2] elapsed: {elapsed:.1f}s")
    print(f"[step2 v2] USABLE (from 2024-01-01 to 2026-08-16): {len(usable)}")
    for s in usable:
        print(f"  - {s}")
    print(f"[step2 v2] saved: {log_path}")


if __name__ == "__main__":
    asyncio.run(main())
