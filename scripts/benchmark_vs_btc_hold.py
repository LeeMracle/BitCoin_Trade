"""알트 포트폴리오 전략 vs BTC 보유 — 같은 기간·같은 시뮬레이터 (plan 20261005_4).

질문: 15슬롯 DC12 알트 포트폴리오가 BTC 단순 보유 / BTC 레짐만 / 알트 바구니 보유보다
      수익·낙폭 면에서 나은가? (기존 분석은 BTC 단일종목뿐 — output/composite_9yr_backtest_result.md)

방법:
  - 전략 수익·MDD 는 `backtest_stop_validation.simulate` 를 **수정 없이** 호출한다(로직 복제 금지, lessons #49).
  - 데이터 범위는 시간봉 캐시 끝(2026-09-17 06:00 UTC)에 맞춘다. 그 뒤 일봉을 넣으면 시간봉이 없어
    '거래 없음'으로 계산돼 전략이 불리하게 왜곡된다.
  - 비교군은 같은 IS/OOS 일(day) 목록 위에서 계산한다.
      BTC 보유        : 구간 첫날 시가 매수 → 마지막날 종가
      BTC 레짐만      : 전일 종가 > EMA200 이면 보유, 아니면 현금. 전환마다 편도 수수료
      알트 바구니 보유 : 같은 종목들을 구간 첫날 시가에 동일가중 매수 후 보유(리밸런스 없음)
  - 한계: 시간봉 캐시 종목은 현재 상장 종목 → 생존편향(전략·알트 바구니 모두 낙관). 상대 비교에만 쓴다.

재현:  py scripts/benchmark_vs_btc_hold.py --runs 10 | tee output/benchmark_vs_btc_20261005.txt
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_stop_validation as SV  # noqa: E402
from backtest_param_sweep import P  # noqa: E402
from services.execution import config as C  # noqa: E402
from services.market_data.fetcher import fetch_ohlcv  # noqa: E402

CUT = pd.Timestamp("2026-09-17", tz="UTC")     # 이 날짜 '이전' 일봉만 사용 (시간봉 캐시 끝 = 09-17 06:00)
DAY_MS = 86_400_000


async def load_inputs() -> tuple[dict, dict, pd.DataFrame]:
    """SV.load_daily 와 같은 절차 + (1) CUT 으로 자르고 (2) 시간봉 캐시가 있는 종목만 쓴다."""
    now = datetime.now(tz=timezone.utc)
    s = (now - timedelta(days=SV.DAYS + 260)).strftime("%Y-%m-%dT00:00:00Z")
    e = now.strftime("%Y-%m-%dT00:00:00Z")
    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", s, e, use_cache=True))
    btc["ema"] = btc["close"].ewm(span=C.REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc["ema"]))
    raw: dict[str, pd.DataFrame] = {}
    # 유니버스 = 시간봉 캐시 종목 그대로. 오늘의 거래대금 순위(get_krw_market_coins)를 쓰면 09-17 연구 당시와
    # 종목이 달라져(35/52 만 겹침) 같은 조건이 아니게 된다.
    for f in sorted(SV.H1.glob("KRW-*.json")):
        sym = f.stem.replace("KRW-", "") + "/KRW"
        try:
            df = pd.DataFrame(await fetch_ohlcv(sym, "1d", s, e, use_cache=True))
        except Exception:
            continue
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df[df.index < CUT]
        if len(df) < 60:
            continue
        raw[sym] = df.tail(SV.DAYS)
    return raw, regime, btc


def _mdd(eq: np.ndarray) -> float:
    eq = np.concatenate([[1.0], eq])
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min() * 100)


def bench_btc_hold(btc: pd.DataFrame, ts_list: list[int]) -> tuple[float, float]:
    by = btc.set_index("ts")
    base = float(by.loc[ts_list[0], "open"])
    eq = np.array([float(by.loc[t, "close"]) / base for t in ts_list])
    return (eq[-1] - 1) * 100, _mdd(eq)


def bench_btc_regime(btc: pd.DataFrame, regime: dict, ts_list: list[int]) -> tuple[float, float, float]:
    """전일 종가 > EMA200 이면 오늘 보유. 반환: (수익%, MDD%, 보유일 비율%)."""
    by = btc.set_index("ts")
    prev_close = float(by.loc[ts_list[0], "open"])
    held_prev, eq, curve, held_days = False, 1.0, [], 0
    for t in ts_list:
        held = bool(regime.get(t - DAY_MS, False))
        if held != held_prev:
            eq *= (1 - C.FEE_RATE)               # 매수든 매도든 편도 수수료
        close = float(by.loc[t, "close"])
        if held:
            eq *= close / prev_close
            held_days += 1
        prev_close, held_prev = close, held
        curve.append(eq)
    if held_prev:
        curve[-1] *= (1 - C.FEE_RATE)            # 구간 종료 시 청산 수수료
    arr = np.array(curve)
    return (arr[-1] - 1) * 100, _mdd(arr), held_days / len(ts_list) * 100


def bench_alt_basket(raw: dict, ts_list: list[int]) -> tuple[float, float, int]:
    """첫날 시가 동일가중 매수 후 보유. 결측일은 직전값 유지. 반환: (수익%, MDD%, 종목수)."""
    cols = {}
    for sym, df in raw.items():
        by = df.set_index("ts")
        if ts_list[0] not in by.index:
            continue
        base = float(by.loc[ts_list[0], "open"])
        if base <= 0:
            continue
        cols[sym] = by["close"].reindex(ts_list).ffill() / base
    m = pd.DataFrame(cols).dropna(how="all")
    eq = m.mean(axis=1).to_numpy()
    return (eq[-1] - 1) * 100, _mdd(eq), m.shape[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()

    raw, regime, btc = asyncio.run(load_inputs())
    if len(raw) < 40:
        print(f"[중단] 종목 {len(raw)} < 40 — 시간봉 캐시/일봉 로드 확인")
        return 1
    t0 = int(min(df.index.min() for df in raw.values()).timestamp() // 3600)
    ctx = SV.build_ctx(raw, regime)
    p = P()

    def to_ts(days: list[int]) -> list[int]:
        return [(d + t0) * 3_600_000 for d in days]

    def span(days: list[int]) -> str:
        a = datetime.fromtimestamp((days[0] + t0) * 3600, tz=timezone.utc).date()
        b = datetime.fromtimestamp((days[-1] + t0) * 3600, tz=timezone.utc).date()
        return f"{a}~{b} ({len(days)}일)"

    print(f"\n종목 {len(raw)} · 데이터 끝 {CUT.date() - timedelta(days=1)} · 무작위 {args.runs}회 평균")
    print(f"IS  {span(ctx['is'])}\nOOS {span(ctx['oos'])}\n")

    rows: list[tuple[str, dict]] = []
    for label, hard in (("전략 SL6 (현행 -6%)", 0.06), ("전략 SL10 (이전 -10%)", 0.10)):
        res = {}
        for part in ("is", "oos"):
            rs = [SV.simulate(ctx, ctx[part], p, hard, True, s) for s in range(args.runs)]
            res[part] = (float(np.mean([r["ret"] for r in rs])), float(np.mean([r["mdd"] for r in rs])),
                         float(np.mean([r["n"] for r in rs])))
        rows.append((label, res))

    bench = {}
    for part in ("is", "oos"):
        ts = to_ts(ctx[part])
        h = bench_btc_hold(btc, ts)
        g = bench_btc_regime(btc, regime, ts)
        a = bench_alt_basket(raw, ts)
        bench[part] = {"btc": h, "reg": g, "alt": a}
    rows.append(("BTC 보유", {k: (bench[k]["btc"][0], bench[k]["btc"][1], 0) for k in ("is", "oos")}))
    rows.append(("BTC 레짐만 (EMA200 위에서만)", {k: (bench[k]["reg"][0], bench[k]["reg"][1], 0) for k in ("is", "oos")}))
    rows.append((f"알트 바구니 보유 (동일가중 {bench['is']['alt'][2]}/{bench['oos']['alt'][2]}종목)",
                 {k: (bench[k]["alt"][0], bench[k]["alt"][1], 0) for k in ("is", "oos")}))

    print(f"{'정책':<40}{'IS 수익':>9}{'IS MDD':>9}{'OOS 수익':>10}{'OOS MDD':>9}{'OOS 거래':>9}")
    for label, r in rows:
        n = f"{r['oos'][2]:.0f}" if r["oos"][2] else "-"
        print(f"{label:<40}{r['is'][0]:>8.1f}%{r['is'][1]:>8.1f}%{r['oos'][0]:>9.1f}%{r['oos'][1]:>8.1f}%{n:>9}")
    print(f"\nBTC 레짐만 보유일 비율: IS {bench['is']['reg'][2]:.1f}% / OOS {bench['oos']['reg'][2]:.1f}%")
    print("\n[수익/낙폭 비 = 수익% ÷ |MDD|%  — 클수록 같은 고통당 수익이 크다]")
    for label, r in rows:
        f = lambda x: x[0] / abs(x[1]) if x[1] else float("nan")  # noqa: E731
        print(f"  {label:<40} IS {f(r['is']):>6.2f}   OOS {f(r['oos']):>6.2f}")
    print("\n⚠ 생존편향(현재 상장 종목만) — 전략·알트 바구니 수치는 낙관. 시도한 정책 2개 + 비교군 3개(다중비교 최소).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
