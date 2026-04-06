"""알트코인 누락 구간 메우기 스크립트.

대상 종목의 현재 캐시 시작일 이전 구간을 업비트에서 수집하여 DuckDB에 upsert.
- 상장일이 불확실한 경우 2019-01-01부터 시도, 데이터 없으면 자동 스킵.
- 현재 캐시 끝일 이후 최신 데이터도 함께 보충.

실행: PYTHONUTF8=1 python scripts/fetch_alt_gaps.py
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import ccxt.async_support as ccxt
import duckdb

from services.market_data.cache import init_schema, upsert_ohlcv

# ── 수집 대상 ──────────────────────────────────────────────────
# (symbol, 실제 업비트 상장일) — 탐색으로 확인된 실제 데이터 시작일
TARGETS = [
    ("SOL/KRW",  "2021-10-15"),   # 업비트 SOL 상장: 2021-10-15 확인
    ("DOGE/KRW", "2021-02-24"),   # 업비트 DOGE 상장: 2021-02-24 확인
    ("AVAX/KRW", "2022-06-01"),   # 업비트 AVAX: 2022-06 이전 데이터 없음 (갭 없음)
    ("NEAR/KRW", "2021-12-15"),   # 업비트 NEAR 상장: 2021-12-15 확인
    ("LINK/KRW", "2020-08-04"),   # 업비트 LINK 상장: 2020-08-04 확인
]

TIMEFRAME   = "1d"
EXCHANGE_ID = "upbit"

# 오늘 날짜를 수집 끝점으로 사용
_TODAY_MS = int(datetime.now(tz=timezone.utc).replace(
    hour=0, minute=0, second=0, microsecond=0
).timestamp() * 1000)

MAX_CANDLES_PER_REQUEST = 200
REQUEST_INTERVAL        = 0.12   # 초 (Rate Limit 준수)
SYMBOL_INTERVAL         = 0.5    # 종목 간 대기 시간(초)

DB_PATH = PROJECT_ROOT / "data" / "cache.duckdb"


# ── 유틸 ──────────────────────────────────────────────────────
def _iso_to_ms(iso: str) -> int:
    if len(iso) == 10:          # "YYYY-MM-DD" 형식
        iso = iso + "T00:00:00Z"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _ms_to_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def get_cache_info(symbol: str) -> dict:
    """DuckDB에서 해당 종목의 캔들 수, 최소/최대 ts 조회."""
    with duckdb.connect(str(DB_PATH)) as con:
        row = con.execute("""
            SELECT MIN(ts), MAX(ts), COUNT(*) FROM ohlcv
            WHERE exchange=? AND symbol=? AND timeframe=?
        """, [EXCHANGE_ID, symbol, TIMEFRAME]).fetchone()
    if row and row[2] > 0:
        return {"min_ts": row[0], "max_ts": row[1], "count": row[2]}
    return {"min_ts": None, "max_ts": None, "count": 0}


# ── 핵심 fetch 로직 ───────────────────────────────────────────
def _ms_to_upbit_to(ms: int) -> str:
    """ms 타임스탬프를 업비트 to 파라미터 형식으로 변환."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_range(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """
    업비트 역방향(to 파라미터) 페이지네이션으로 start_ms ~ end_ms 범위 수집.
    업비트는 since 기반 과거 조회가 불안정하므로 to(끝점) 역방향 방식을 사용.
    """
    exchange = ccxt.upbit({"enableRateLimit": True})
    all_rows: list[dict] = []
    # 끝점에서 시작점 방향으로 역순 수집
    cursor_ms = end_ms
    page = 0
    seen_oldest = None   # 무한루프 방지

    try:
        while True:
            page += 1
            to_str = _ms_to_upbit_to(cursor_ms)
            try:
                candles = await exchange.fetch_ohlcv(
                    symbol, TIMEFRAME,
                    limit=MAX_CANDLES_PER_REQUEST,
                    params={"to": to_str}
                )
            except ccxt.BadSymbol:
                print(f"    [오류] {symbol} 업비트 미지원 종목 — 스킵")
                break
            except Exception as e:
                print(f"    [오류] 페이지 {page} 요청 실패: {e} — 스킵")
                break

            if not candles:
                print(f"    [페이지 {page}] 응답 없음 — 수집 종료")
                break

            batch: list[dict] = []
            reached_start = False
            for c in candles:
                ts = c[0]
                if ts > end_ms:
                    continue
                if ts < start_ms:
                    reached_start = True
                    continue
                batch.append({
                    "ts":     ts,
                    "open":   c[1],
                    "high":   c[2],
                    "low":    c[3],
                    "close":  c[4],
                    "volume": c[5],
                })

            if batch:
                upsert_ohlcv(EXCHANGE_ID, symbol, TIMEFRAME, batch)
                all_rows.extend(batch)
                first = _ms_to_date(batch[0]["ts"])
                last  = _ms_to_date(batch[-1]["ts"])
                print(f"    [페이지 {page}] {first} ~ {last} ({len(batch)}개 저장)")

            oldest_ts = candles[0][0]

            # 종료 조건: start_ms에 도달했거나 진행이 없는 경우
            if reached_start or oldest_ts <= start_ms:
                break
            if seen_oldest is not None and oldest_ts >= seen_oldest:
                print(f"    [페이지 {page}] 진행 없음 — 무한루프 방지 종료")
                break

            seen_oldest = oldest_ts
            # 다음 요청: 현재 배치의 가장 오래된 ts 하루 전을 to로 설정
            cursor_ms = oldest_ts - 1
            await asyncio.sleep(REQUEST_INTERVAL)

    finally:
        await exchange.close()

    return all_rows


async def fetch_range_forward(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    """
    최신 데이터 보충용 순방향(since 파라미터) 조회.
    since 기반은 최근 구간에서는 정상 동작하므로 뒤쪽 갭 전용으로 사용.
    """
    exchange = ccxt.upbit({"enableRateLimit": True})
    all_rows: list[dict] = []
    since = start_ms
    page  = 0

    try:
        while since < end_ms:
            page += 1
            try:
                candles = await exchange.fetch_ohlcv(
                    symbol, TIMEFRAME, since=since, limit=MAX_CANDLES_PER_REQUEST
                )
            except ccxt.BadSymbol:
                print(f"    [오류] {symbol} 업비트 미지원 종목 — 스킵")
                break
            except Exception as e:
                print(f"    [오류] 페이지 {page} 요청 실패: {e} — 스킵")
                break

            if not candles:
                print(f"    [페이지 {page}] 응답 없음 — 수집 종료")
                break

            batch: list[dict] = []
            for c in candles:
                ts = c[0]
                if ts < start_ms:
                    continue
                if ts > end_ms:
                    break
                batch.append({
                    "ts":     ts,
                    "open":   c[1],
                    "high":   c[2],
                    "low":    c[3],
                    "close":  c[4],
                    "volume": c[5],
                })

            if batch:
                upsert_ohlcv(EXCHANGE_ID, symbol, TIMEFRAME, batch)
                all_rows.extend(batch)
                first = _ms_to_date(batch[0]["ts"])
                last  = _ms_to_date(batch[-1]["ts"])
                print(f"    [페이지 {page}] {first} ~ {last} ({len(batch)}개 저장)")

            last_ts = candles[-1][0]
            if last_ts <= since:
                print(f"    [페이지 {page}] 진행 없음 — 무한루프 방지 종료")
                break
            since = last_ts + 1
            await asyncio.sleep(REQUEST_INTERVAL)

    finally:
        await exchange.close()

    return all_rows


# ── 종목별 처리 ───────────────────────────────────────────────
async def fill_gap(symbol: str, listing_date: str) -> dict:
    """
    갭 메우기 수행.
    - 앞쪽 갭: listing_date ~ (캐시 시작 전날)
    - 뒤쪽 갭: (캐시 끝 다음날) ~ 오늘
    반환: 결과 요약 딕셔너리
    """
    before = get_cache_info(symbol)
    fetched_total = 0

    # ── 앞쪽 갭 ───────────────────────────────────────────────
    listing_ms = _iso_to_ms(listing_date)
    if before["min_ts"] is None:
        # 캐시 전혀 없음 — 전체 수집
        front_start = listing_ms
        front_end   = _TODAY_MS
    elif before["min_ts"] > listing_ms:
        front_start = listing_ms
        front_end   = before["min_ts"] - 1
    else:
        front_start = None
        front_end   = None

    if front_start is not None:
        span = _ms_to_date(front_start) + " ~ " + _ms_to_date(front_end)
        print(f"  [앞쪽 갭] {span}")
        # 역방향(to 파라미터) 방식으로 과거 데이터 수집
        rows = await fetch_range(symbol, front_start, front_end)
        fetched_total += len(rows)
        if not rows:
            print(f"  [앞쪽 갭] 데이터 없음 (상장 전 또는 미지원)")
        await asyncio.sleep(SYMBOL_INTERVAL)
    else:
        print(f"  [앞쪽 갭] 없음 (캐시 시작: {_ms_to_date(before['min_ts'])})")

    # ── 뒤쪽 갭 ───────────────────────────────────────────────
    after_front = get_cache_info(symbol)   # 앞쪽 수집 후 재조회
    if after_front["max_ts"] is not None and after_front["max_ts"] < _TODAY_MS:
        back_start = after_front["max_ts"] + 1
        back_end   = _TODAY_MS
        span = _ms_to_date(back_start) + " ~ " + _ms_to_date(back_end)
        print(f"  [뒤쪽 갭] {span}")
        # 순방향(since 파라미터) 방식으로 최신 데이터 수집
        rows = await fetch_range_forward(symbol, back_start, back_end)
        fetched_total += len(rows)
        if not rows:
            print(f"  [뒤쪽 갭] 데이터 없음")
    else:
        print(f"  [뒤쪽 갭] 없음 (캐시 끝: {_ms_to_date(after_front['max_ts']) if after_front['max_ts'] else 'N/A'})")

    after = get_cache_info(symbol)

    return {
        "symbol":        symbol,
        "before_count":  before["count"],
        "before_start":  _ms_to_date(before["min_ts"]) if before["min_ts"] else "N/A",
        "before_end":    _ms_to_date(before["max_ts"]) if before["max_ts"] else "N/A",
        "after_count":   after["count"],
        "after_start":   _ms_to_date(after["min_ts"]) if after["min_ts"] else "N/A",
        "after_end":     _ms_to_date(after["max_ts"])  if after["max_ts"]  else "N/A",
        "fetched":       fetched_total,
        "net_gain":      after["count"] - before["count"],
    }


# ── 메인 ─────────────────────────────────────────────────────
async def main() -> None:
    print("=" * 65)
    print("알트코인 누락 구간 수집 (fetch_alt_gaps)")
    print(f"기준일: {_ms_to_date(_TODAY_MS)}")
    print("=" * 65)

    init_schema()
    results = []

    for i, (symbol, listing_date) in enumerate(TARGETS):
        print(f"\n[{i+1}/{len(TARGETS)}] {symbol}  (추정 상장일: {listing_date})")
        t0 = time.time()
        try:
            result = await fill_gap(symbol, listing_date)
        except Exception as e:
            print(f"  [치명 오류] {e} — 이 종목 건너뜀")
            result = {
                "symbol":       symbol,
                "before_count": 0, "before_start": "N/A", "before_end": "N/A",
                "after_count":  0, "after_start":  "N/A", "after_end":  "N/A",
                "fetched": 0, "net_gain": 0,
            }
        elapsed = time.time() - t0
        result["elapsed"] = elapsed
        results.append(result)
        print(f"  소요: {elapsed:.1f}초")

        if i < len(TARGETS) - 1:
            await asyncio.sleep(SYMBOL_INTERVAL)

    # ── 최종 요약 보고 ─────────────────────────────────────────
    print("\n" + "=" * 65)
    print("갭 메우기 결과 요약")
    print("=" * 65)
    print(f"{'종목':<12} {'수집 전':>6}  {'이전 범위':<25}  {'수집 후':>6}  {'이후 범위':<25}  {'순증가':>6}")
    print("-" * 65)
    for r in results:
        before_range = f"{r['before_start']} ~ {r['before_end']}"
        after_range  = f"{r['after_start']} ~ {r['after_end']}"
        print(
            f"{r['symbol']:<12} {r['before_count']:>6}  "
            f"{before_range:<25}  {r['after_count']:>6}  "
            f"{after_range:<25}  {r['net_gain']:>+6}"
        )
    print("=" * 65)
    total_gain = sum(r["net_gain"] for r in results)
    print(f"전체 순증가 캔들 수: {total_gain:+d}개")


if __name__ == "__main__":
    asyncio.run(main())
