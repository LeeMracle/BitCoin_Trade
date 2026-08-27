"""분봉 기반 진입 슬리피지 실측 — 일봉 백테스트가 볼 수 없는 구간을 직접 잰다.

## 왜 별도 스크립트인가 (2026-08-27)

`backtest_param_sweep.py` 는 진입가를 `max(DC상단, 시가)` 로 가정한다. 즉 **밴드에서
정확히 체결된다**고 본다. 실거래는 다르다:

    09:55:16  KERNEL/KRW 돌파! 가격: 57  상단: 57
    09:55:16  매수 체결: 67.8                      <- +18.9%

일봉 대리지표(`진입가/상단 - 1`)로 이걸 재려 했으나 **죽은 변수**였다 — 암호화폐는
24시간 연속 거래라 일봉에 갭이 없어, 40종목/700일 1,733신호 중 이격>0 이 2건(0.1%)뿐.
`high` 로 바꾸면 변별력은 생기지만 진입 시점에 당일 고가를 알 수 없어 lookahead 다.

→ 현상이 일봉 **아래**에 살고 있으므로 분봉으로 내려가서 잰다.

## 측정 방법

일봉으로 돌파일과 그날의 DC 상단을 구한 뒤, 그날 1분봉을 받아 **상단을 처음 넘은 분**
을 찾고 체결 시나리오별로 `체결가/상단 - 1` 을 낸다:

    at_band     상단 정가            <- 일봉 백테스트의 가정 (기준선)
    cross_close 돌파분 종가          <- 봇이 그 분 안에 감지·체결
    cross_high  돌파분 고가          <- 그 분의 최악
    next_open   다음 분 시가         <- 감지가 분 경계를 넘긴 경우

봇은 웹소켓 틱으로 감지하므로 실제는 cross_close ~ next_open 사이다.
cross_high 는 비관 상한이며, 꼬리(급등 중 체결)의 크기를 보여준다.

## 한계

- 1분봉도 초 단위 급등을 완전히 담지 못한다. cross_high 가 그 대리 상한이다.
- 상장폐지 코인이 빠져 있어(생존편향) 꼬리는 **과소평가** 쪽이다.

## 사용

    python scripts/slippage_intraday.py --coins 40 --days 120
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution.scanner import get_krw_market_coins  # noqa: E402
from services.execution import config as C  # noqa: E402

DAY_MS = 86_400_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def breakout_days(ex, symbol: str, days: int) -> list[tuple[int, float, float]]:
    """(돌파일 ts, DC상단, 당일 시가) 목록. 일봉도 ccxt 직결로 받는다.

    공유 DuckDB 캐시를 쓰면 백테스트 스윙과 잠금 경합한다(단일 라이터).
    일봉은 종목당 1회 요청이라 직결이 더 싼다.

    상단은 `high.shift(1).rolling(DC).max()` — 봇의 refresh_levels 와 같은 정의(전일까지의
    최고가). 당일 데이터를 쓰면 자기참조가 된다.
    """
    raw = await ex.fetch_ohlcv(symbol, "1d", limit=min(days + C.DONCHIAN_PERIOD + 10, 200))
    if len(raw) < C.DONCHIAN_PERIOD + 5:
        return []
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    upper = df["high"].shift(1).rolling(C.DONCHIAN_PERIOD, min_periods=C.DONCHIAN_PERIOD).max()
    df["upper"] = upper
    df = df.tail(days)
    hit = df[(df["high"] > df["upper"]) & df["upper"].notna()]
    return [(int(r.ts), float(r.upper), float(r.open)) for r in hit.itertuples()]


async def fetch_minutes(ex, symbol: str, day_ts: int) -> pd.DataFrame:
    """하루치 1분봉 — **캐시를 거치지 않고** ccxt 직결로 받는다.

    공유 DuckDB 캐시(data/cache.duckdb)는 단일 라이터라 백테스트가 돌면 잠기고,
    분봉은 일봉의 1,440배 부피라 영속 캐시에 넣을 성격의 데이터가 아니다
    (일회성 조사용). 200개/요청 제한이므로 하루 = 최대 8회.
    """
    out: list[list] = []
    since = day_ts
    for _ in range(8):
        batch = await ex.fetch_ohlcv(symbol, "1m", since=since, limit=200)
        if not batch:
            break
        out.extend(b for b in batch if day_ts <= b[0] < day_ts + DAY_MS)
        nxt = batch[-1][0] + 60_000
        if nxt >= day_ts + DAY_MS or nxt <= since:
            break
        since = nxt
    if not out:
        return pd.DataFrame()
    return pd.DataFrame(out, columns=["ts", "open", "high", "low", "close", "volume"])


async def measure_day(ex, symbol: str, day_ts: int, upper: float) -> dict | None:
    """돌파일의 1분봉에서 상단을 처음 넘은 분을 찾아 체결 시나리오를 계산."""
    m = await fetch_minutes(ex, symbol, day_ts)
    if m.empty:
        return None
    m = m.sort_values("ts").reset_index(drop=True)
    cross = m.index[m["high"] >= upper]
    if len(cross) == 0:
        return None
    i = int(cross[0])
    nxt = m.iloc[i + 1] if i + 1 < len(m) else m.iloc[i]
    return {
        "symbol": symbol, "day": _iso(day_ts)[:10],
        "upper": upper,
        "cross_close": float(m.iloc[i]["close"]) / upper - 1,
        "cross_high": float(m.iloc[i]["high"]) / upper - 1,
        "next_open": float(nxt["open"]) / upper - 1,
        "minute": i,
    }


def report(rows: list[dict]) -> None:
    if not rows:
        print("표본 없음")
        return
    print("\n" + "=" * 74)
    print(f"체결 시나리오별 슬리피지 (체결가/DC상단 - 1) — 돌파 {len(rows):,}건")
    print("=" * 74)
    print(f"{'시나리오':<26}{'중앙값':>9}{'평균':>9}{'90분위':>9}{'99분위':>9}{'최대':>9}")
    print("-" * 74)
    scen = [("at_band (백테스트 가정)", None),
            ("cross_close (돌파분 종가)", "cross_close"),
            ("next_open (다음 분 시가)", "next_open"),
            ("cross_high (돌파분 고가)", "cross_high")]
    for label, key in scen:
        if key is None:
            print(f"{label:<26}{0.0:>8.2%}{0.0:>9.2%}{0.0:>9.2%}{0.0:>9.2%}{0.0:>9.2%}")
            continue
        a = np.array([r[key] for r in rows])
        print(f"{label:<26}{np.median(a):>8.2%}{a.mean():>9.2%}"
              f"{np.quantile(a, .90):>9.2%}{np.quantile(a, .99):>9.2%}{a.max():>9.2%}")

    print("\n[꼬리 — 급등 중 체결 빈도]")
    for key, label in (("next_open", "다음 분 시가"), ("cross_high", "돌파분 고가")):
        a = np.array([r[key] for r in rows])
        n = len(a)
        parts = " / ".join(f">{t:.0%}: {(a > t).sum()}건({(a > t).mean():.1%})"
                           for t in (0.01, 0.03, 0.05, 0.10))
        print(f"  {label:14s} {parts}")

    a = np.array([r["cross_high"] for r in rows])
    worst = sorted(rows, key=lambda r: -r["cross_high"])[:5]
    print("\n[최악 5건 — 돌파분 고가 기준]")
    for r in worst:
        print(f"  {r['day']} {r['symbol']:12s} 상단 {r['upper']:>12,.4g} "
              f"→ 분고가 {r['upper'] * (1 + r['cross_high']):>12,.4g}  {r['cross_high']:+7.1%}")

    print("\n[해석용] 백테스트는 at_band(0%)를 가정한다. 실제 진입이 next_open 이라면")
    print(f"  전 거래에 평균 {np.array([r['next_open'] for r in rows]).mean():+.2%} 의")
    print("  비용이 추가로 붙는다 — 이 값을 --axis slip 결과와 대조할 것.")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=40)
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--max-events", type=int, default=800,
                    help="분봉 요청 예산. 초과하면 중단한다(요청 1건당 최대 8회 API 호출)")
    args = ap.parse_args()

    now = datetime.now(tz=timezone.utc)
    start = (now - timedelta(days=args.days + 60)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT00:00:00Z")

    coins = [c["symbol"] for c in get_krw_market_coins()[: args.coins]]
    print(f"종목 {len(coins)} / 최근 {args.days}일 / DC({C.DONCHIAN_PERIOD})")

    import ccxt.async_support as ccxt_async
    ex = ccxt_async.upbit({"enableRateLimit": True})

    events: list[tuple[str, int, float]] = []
    try:
        for i, sym in enumerate(coins, 1):
            try:
                for ts, up, _op in await breakout_days(ex, sym, args.days):
                    events.append((sym, ts, up))
            except Exception:
                continue
            if i % 20 == 0:
                print(f"  일봉 스캔 {i}/{len(coins)} — 돌파 {len(events)}건", flush=True)
    except BaseException:
        await ex.close()
        raise

    if len(events) > args.max_events:
        print(f"돌파 {len(events)}건 → 예산 {args.max_events}건으로 균등 표집")
        idx = np.linspace(0, len(events) - 1, args.max_events).astype(int)
        events = [events[j] for j in idx]

    rows, fail, errs = [], 0, {}
    try:
        for i, (sym, ts, up) in enumerate(events, 1):
            try:
                r = await measure_day(ex, sym, ts, up)
            except Exception as exc:
                r = None
                k = type(exc).__name__
                errs[k] = errs.get(k, 0) + 1
            if r:
                rows.append(r)
            else:
                fail += 1
            if i % 50 == 0:
                print(f"  분봉 측정 {i}/{len(events)} (실패 {fail})", flush=True)
    finally:
        await ex.close()
    if errs:
        print("  예외 내역:", errs)

    print(f"\n돌파 {len(events)}건 중 분봉 측정 성공 {len(rows)}건 / 실패 {fail}건")
    report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
