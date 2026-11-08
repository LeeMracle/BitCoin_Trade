"""가드 3개를 포함한 시간봉 수집 wrapper (plan 20261107_1 재실행용).

가드:
  1) Resume — output/h1/KRW-<sym>.json 이미 존재(비어 있지 않은 경우)하면 skip
  2) RequestTimeout 종목별 최대 3회 재시도, 3회 실패 시 skip + 로그 기록
  3) 진척 로그 line-buffered flush — 매 종목마다 [ts] fetched=N/TOTAL last=sym skipped=M

사용:
    PYTHONUTF8=1 py output/expert_data_20261108/fetch_hourly_resumable.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

OUT = ROOT / "output" / "expert_data_20261108"
H1 = ROOT / "output" / "h1"
LOG = OUT / "fetch_log.txt"
DAYS = 700  # backtest_stop_validation.py 와 동일

MAX_RETRIES = 3
RETRY_SLEEP = 3.0  # 3회 재시도 간 sleep (초)


def log(msg: str) -> None:
    """stdout + fetch_log.txt 동시, line-buffered."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def fetch_hourly_resilient(symbol: str, start: datetime, end: datetime) -> tuple[bool, int, str]:
    """시간봉 수집 — RequestTimeout 포함 모든 예외 3회 재시도.

    반환: (success, candles_count, reason)
    """
    market = "KRW-" + symbol.split("/")[0]
    path = H1 / f"{market}.json"
    if path.exists() and path.stat().st_size > 10:
        # Resume — 기존 파일 재사용
        try:
            rows = json.loads(path.read_text())
            if rows:
                return True, len(rows), "cached"
        except Exception:
            pass  # 깨진 캐시는 재수집

    out: dict[int, list[float]] = {}
    to = end
    timeout_count = 0

    while to > start:
        url = (f"https://api.upbit.com/v1/candles/minutes/60?market={market}&count=200"
               f"&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        # 1 페이지 당 최대 MAX_RETRIES 회 재시도
        rows = None
        page_err = None
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers={"accept": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    rows = json.loads(r.read())
                break
            except urllib.error.URLError as e:
                page_err = f"URLError: {e}"
                time.sleep(RETRY_SLEEP * (attempt + 1))
            except TimeoutError as e:
                page_err = f"Timeout: {e}"
                timeout_count += 1
                time.sleep(RETRY_SLEEP * (attempt + 1))
            except Exception as e:
                page_err = f"{type(e).__name__}: {e}"
                time.sleep(RETRY_SLEEP * (attempt + 1))
        time.sleep(0.11)

        if not rows:
            # 3회 재시도도 실패 — 지금까지 모은 데이터로 저장하고 skip 처리
            if not out:
                return False, 0, f"3회 재시도 실패: {page_err}"
            # 일부만 받은 경우: 저장하고 성공 처리 (부분 데이터도 쓸모 있음)
            break

        for rec in rows:
            ts = int(datetime.fromisoformat(rec["candle_date_time_utc"]).replace(
                tzinfo=timezone.utc).timestamp() * 1000)
            out[ts] = [ts, rec["opening_price"], rec["high_price"],
                       rec["low_price"], rec["trade_price"]]
        oldest = min(out) / 1000
        nxt = datetime.fromtimestamp(oldest, tz=timezone.utc)
        if nxt >= to:
            break
        to = nxt

    rows = [out[k] for k in sorted(out)]
    H1.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return True, len(rows), "fetched"


async def load_universe() -> list[str]:
    """backtest_stop_validation.load_daily() 와 동일 종목군 — 일봉 로드 후 60봉 이상."""
    from services.execution.scanner import get_krw_market_coins
    from services.market_data.fetcher import fetch_ohlcv

    now = datetime.now(tz=timezone.utc)
    s = (now - timedelta(days=DAYS + 260)).strftime("%Y-%m-%dT00:00:00Z")
    e = now.strftime("%Y-%m-%dT00:00:00Z")

    syms = []
    for c in get_krw_market_coins()[:120]:
        try:
            import pandas as pd
            df = pd.DataFrame(await fetch_ohlcv(c["symbol"], "1d", s, e, use_cache=True))
            if len(df) >= 60:
                syms.append(c["symbol"])
        except Exception as ex:
            log(f"[DAILY-SKIP] {c['symbol']}: {type(ex).__name__}: {ex}")
            continue
    return sorted(syms)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    H1.mkdir(parents=True, exist_ok=True)

    # Resume 시작 — 기존 로그는 보존하되 재개 마크
    log("=" * 60)
    log(f"FETCH RESUME 시작 — H1 dir: {H1}")

    # 1) 일봉 로드 → 종목군 확정
    log("일봉 로드 중 (DuckDB 캐시)...")
    syms = asyncio.run(load_universe())
    log(f"종목군 확정: {len(syms)}개")

    if not syms:
        log("[FATAL] 종목군 0개 — 일봉 캐시 또는 scanner 오류")
        return 1

    # 2) 시간봉 수집 범위 계산
    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=DAYS)
    end = now

    fetched = 0
    skipped = 0
    cached = 0
    partial = 0

    for i, sym in enumerate(syms, 1):
        try:
            ok, n_candles, reason = fetch_hourly_resilient(sym, start, end)
        except Exception as ex:
            log(f"[SKIP] {sym}: 예외 {type(ex).__name__}: {ex}")
            skipped += 1
            continue

        if ok:
            if reason == "cached":
                cached += 1
            else:
                fetched += 1
            # 매 10종목마다 진척 로그
            if i % 10 == 0 or i == len(syms):
                log(f"progress: {i}/{len(syms)} last={sym} candles={n_candles} "
                    f"({reason}) fetched={fetched} cached={cached} skipped={skipped}")
        else:
            log(f"[SKIP] {sym}: {reason}")
            skipped += 1

    log("=" * 60)
    log(f"FETCH 완료 — fetched={fetched} cached={cached} skipped={skipped} total={len(syms)}")
    log(f"H1 dir 최종: {len(list(H1.glob('*.json')))} files")
    return 0 if (fetched + cached) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
