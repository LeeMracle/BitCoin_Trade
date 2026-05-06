"""ML 신호 필터 OOS 백테스트 시뮬레이션 — 가속 LIVE 의사결정용.

목적: 실거래 표본 부족(13건) 보완을 위해, 학습 시점 이후 OOS 기간에
v2_real 모델을 가상 적용하여 임계별 PF / 승률 / Expectancy 비교.

흐름:
    1. OOS 기간(기본 2026-04-01 ~ 2026-05-05) 11종 코인 4h OHLCV 로드
    2. 각 코인 DC15 돌파 시점 추출 (실제 매수 신호)
    3. 각 신호:
        - v2_real 모델 score 계산 (compute_features → predict_proba)
        - 24봉(4일) horizon 시뮬레이션:
          a) +5%(slippage 포함) 도달 → +4.8% (TP 가정)
          b) -10% 도달 → -10% (하드 손절 캡)
          c) horizon 종료 → close 기준 손익
    4. 임계별(0.40/0.45/0.50/0.55/0.60) 차단/통과 시 시뮬 PnL 비교
    5. 무필터(현재) vs 임계별 PF/승률/Expectancy 출력

사용:
    PYTHONUTF8=1 python scripts/ml_backtest_sim.py
    PYTHONUTF8=1 python scripts/ml_backtest_sim.py --start 2026-04-01 --end 2026-05-05
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Shadow Mode 강제 OFF (passes() 분기 무시 — 우리는 score 직접 사용)
os.environ["ML_FILTER_ENABLED"] = "1"
os.environ["ML_SHADOW_MODE"] = "0"

from services.ml.features import MarketContext, compute_features  # noqa: E402
from services.ml.inference import MLFilter  # noqa: E402
from services.ml.config import MODEL_DIR  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("ml_sim")

DEFAULT_SYMBOLS = [
    "BTC/KRW", "ETH/KRW", "XRP/KRW", "SOL/KRW", "ADA/KRW",
    "DOGE/KRW", "DOT/KRW", "AVAX/KRW", "ATOM/KRW", "LINK/KRW", "NEAR/KRW",
]

# 백테스트 시뮬 파라미터
TP_PROFIT = 0.05         # +5% TP 가정
SLIPPAGE = 0.002         # 0.2% 슬리피지
HARD_STOP = 0.10         # -10% 하드 손절
HORIZON_BARS = 24        # 4h × 24 = 4일

# 임계 시나리오
THRESHOLDS = [0.40, 0.45, 0.50, 0.55, 0.60]


def load_ohlcv_4h(con, symbol: str) -> pd.DataFrame:
    rows = con.execute(
        "SELECT ts, open, high, low, close, volume FROM ohlcv "
        "WHERE symbol = ? AND timeframe = '4h' ORDER BY ts",
        [symbol],
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


def load_fear_greed(con) -> pd.Series:
    rows = con.execute(
        "SELECT date, value FROM macro WHERE series_id='FEAR_GREED' ORDER BY date"
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    return pd.Series(
        data=[float(v) for _, v in rows],
        index=pd.to_datetime([d for d, _ in rows], utc=True),
    )


def lookup_fg(fg: pd.Series, at_ts: pd.Timestamp, default: int = 50) -> int:
    if fg.empty:
        return default
    sub = fg[fg.index <= at_ts]
    return int(sub.iloc[-1]) if not sub.empty else default


def detect_dc_breakouts(df: pd.DataFrame, period: int = 15) -> list[pd.Timestamp]:
    if len(df) < period + 50:
        return []
    dc_upper = df["high"].shift(1).rolling(period).max()
    cross = (df["close"] > dc_upper) & (df["close"].shift(1) <= dc_upper.shift(1))
    return df.index[cross.fillna(False)].tolist()


def simulate_outcome(df: pd.DataFrame, entry_ts: pd.Timestamp, entry_price: float) -> tuple[float, str]:
    """24봉 horizon 시뮬 → (실 수익률, 청산 사유)."""
    forward = df[df.index > entry_ts].head(HORIZON_BARS)
    if len(forward) < HORIZON_BARS // 2:
        return 0.0, "no_data"

    tp_price = entry_price * (1.0 + TP_PROFIT + SLIPPAGE)
    sl_price = entry_price * (1.0 - HARD_STOP)

    for _, bar in forward.iterrows():
        # 같은 봉에서 SL/TP 동시 가정 → SL 우선 (lessons #25)
        if bar["low"] <= sl_price:
            return -HARD_STOP, "sl"
        if bar["high"] >= tp_price:
            return TP_PROFIT - SLIPPAGE, "tp"

    # horizon 종료 — 마지막 close 기준
    last_close = float(forward["close"].iloc[-1])
    return (last_close / entry_price) - 1.0 - SLIPPAGE, "expire"


def calc_metrics(rets: list[float], name: str) -> dict:
    if not rets:
        return {"name": name, "n": 0}
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    avg = np.mean(rets)
    gp = sum(wins) if wins else 0.0
    gl = -sum(losses) if losses else 0.0
    pf = gp / gl if gl > 0 else float("inf")
    pw = len(wins) / n
    aw = np.mean(wins) if wins else 0.0
    al = np.mean(losses) if losses else 0.0
    expectancy = pw * aw + (1 - pw) * al
    return {
        "name": name,
        "n": n,
        "win_rate": pw,
        "avg_win": aw,
        "avg_loss": al,
        "profit_factor": pf,
        "expectancy": expectancy,
        "total_return": sum(rets),
        "avg_return": avg,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-03-31")
    p.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    p.add_argument("--model", default="v2_oos",
                   help="모델 버전 (default v2_oos: 2025-12-31까지 학습, 진짜 OOS)")
    args = p.parse_args()

    start_ts = pd.Timestamp(args.start, tz="UTC")
    end_ts = pd.Timestamp(args.end, tz="UTC")

    log.info("=" * 70)
    log.info(f"OOS 백테스트 시뮬 [{args.start} ~ {args.end}]  ({(end_ts - start_ts).days}일)")
    log.info(f"임계: {THRESHOLDS}  / TP +{TP_PROFIT*100:.0f}% / SL -{HARD_STOP*100:.0f}% / horizon {HORIZON_BARS}봉(4h)")
    log.info("=" * 70)

    model_path = MODEL_DIR / f"signal_filter_{args.model}.pkl"
    meta_path = MODEL_DIR / f"signal_filter_{args.model}.meta.json"
    flt = MLFilter(model_path=model_path, meta_path=meta_path)
    if not flt.is_active:
        log.error(f"MLFilter 비활성: {flt.status}")
        return 1
    log.info(f"모델: {flt.meta.get('version')} (AUC {flt.meta['cv_metrics']['mean_auc']:.3f}, "
             f"학습 {flt.meta['train_start']} ~ {flt.meta['train_end']})")

    con = duckdb.connect("data/cache.duckdb", read_only=True)
    btc_df = load_ohlcv_4h(con, "BTC/KRW")
    fg_series = load_fear_greed(con)

    all_signals = []  # 각 신호의 (symbol, ts, entry, score, ret, exit_reason)

    for sym in args.symbols:
        df = load_ohlcv_4h(con, sym)
        if df.empty:
            continue
        breakouts = [t for t in detect_dc_breakouts(df, period=15) if start_ts <= t <= end_ts]
        if not breakouts:
            continue

        n_added = 0
        for at_ts in breakouts:
            entry_price = float(df.loc[at_ts, "close"])

            # 시장 컨텍스트
            btc_window = btc_df[btc_df.index <= at_ts].tail(180)
            btc_trend = 0.0
            btc_corr = 0.0
            if len(btc_window) >= 50:
                btc_trend = float((btc_window["close"].iloc[-1] / btc_window["close"].iloc[0]) - 1.0)
                coin_window = df[df.index <= at_ts].tail(180)
                join = pd.concat([coin_window["close"].rename("c"), btc_window["close"].rename("b")],
                                 axis=1, join="inner")
                if len(join) >= 30:
                    ret = join.pct_change().dropna()
                    if len(ret) >= 20:
                        c = ret["c"].corr(ret["b"])
                        btc_corr = float(c) if pd.notna(c) else 0.0

            ctx = MarketContext(
                btc_trend_30d=btc_trend, btc_dominance=50.0,
                fear_greed=lookup_fg(fg_series, at_ts), btc_corr_30d=btc_corr,
                market_cap_rank=1 if "BTC" in sym else 99,
                days_since_listing=2000,
            )

            # ML score
            try:
                score = flt.score(sym.replace("/", "-"), df, at_ts, ctx)
            except Exception:
                continue

            # outcome 시뮬
            ret, reason = simulate_outcome(df, at_ts, entry_price)
            all_signals.append({
                "symbol": sym, "ts": at_ts, "entry": entry_price,
                "score": score, "ret": ret, "reason": reason,
            })
            n_added += 1

        log.info(f"  {sym:10s}: 돌파 {len(breakouts)}건 → 시뮬 {n_added}건")

    if not all_signals:
        log.error("시뮬 신호 없음")
        return 2

    # ── 시나리오 평가 ──────────────────────────────────────
    rets_all = [s["ret"] for s in all_signals]
    base = calc_metrics(rets_all, "무필터(현재)")
    scenarios = [base]
    for thr in THRESHOLDS:
        rets_pass = [s["ret"] for s in all_signals if s["score"] >= thr]
        scenarios.append(calc_metrics(rets_pass, f"score≥{thr}"))

    log.info("")
    log.info("=" * 90)
    log.info(f"{'시나리오':<18} {'거래':<6} {'승률':<8} {'평균±':<10} {'평균-':<10} {'PF':<8} {'Exp':<10} {'누적':<10}")
    log.info("=" * 90)
    for s in scenarios:
        if s.get("n", 0) == 0:
            log.info(f"{s['name']:<18} {'0':<6} (해당 신호 없음)")
            continue
        log.info(
            f"{s['name']:<18} {s['n']:<6} "
            f"{s['win_rate']*100:>5.1f}%   "
            f"{s['avg_win']*100:>+6.2f}%   "
            f"{s['avg_loss']*100:>+6.2f}%   "
            f"{s['profit_factor']:<7.2f} "
            f"{s['expectancy']*100:>+6.2f}%   "
            f"{s['total_return']*100:>+6.1f}%"
        )
    log.info("=" * 90)

    # ── 추가: score 분포 + 사유별 ────────────────────────
    log.info("")
    log.info("score 분포:")
    for low, high in [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01)]:
        bucket = [s for s in all_signals if low <= s["score"] < high]
        if not bucket:
            continue
        rets_b = [s["ret"] for s in bucket]
        wr = sum(1 for r in rets_b if r > 0) / len(rets_b)
        log.info(f"  [{low:.2f}~{high:.2f}): n={len(bucket):3d}  승률 {wr*100:5.1f}%  평균 {np.mean(rets_b)*100:+5.2f}%")

    log.info("")
    log.info("사유별 (전체):")
    from collections import Counter
    reasons = Counter(s["reason"] for s in all_signals)
    for r, c in reasons.most_common():
        log.info(f"  {r}: {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
