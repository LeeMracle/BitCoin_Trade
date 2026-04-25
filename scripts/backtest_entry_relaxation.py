# -*- coding: utf-8 -*-
"""composite 진입 조건 완화 그리드 백테스트.

plan: workspace/plans/20260425_2_increase_trade_frequency.md

레버 A — 거래 빈도와 수익 증대.
EMA200 게이트는 유지(직전 검증에서 OOS 단독 1위).
RSI 임계와 Vol 배수만 완화하여 거래 빈도 변화와 메트릭 영향 측정.

시나리오 (총 6개):
  G1: RSI=50, Vol=1.5  (현행 baseline)
  G2: RSI=50, Vol=1.2  (Vol 살짝 완화)
  G3: RSI=50, Vol=1.0  (Vol 조건 사실상 OFF)
  G4: RSI=45, Vol=1.5  (RSI 살짝 완화)
  G5: RSI=45, Vol=1.2  (둘 다 살짝)
  G6: RSI=40, Vol=1.0  (가장 공격적)

기간:
  IS  2018-06 ~ 2023-12
  OOS 2024-01 ~ 2026-04-25
  EMA200 게이트 항상 ON (close<EMA200 시 entry 차단)

실행:
  PYTHONUTF8=1 python scripts/backtest_entry_relaxation.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from services.backtest.engine import BacktestEngine  # noqa: E402
from services.backtest.metrics import compute_metrics  # noqa: E402
from services.backtest.models import Metrics  # noqa: E402
from services.strategies.advanced import (  # noqa: E402
    _calc_atr,
    _calc_donchian_upper,
    _calc_ema,
    _calc_rsi,
    _calc_vol_sma,
)


DB_PATH = Path(PROJECT_ROOT) / "data" / "cache.duckdb"
OUTPUT_DIR = Path(PROJECT_ROOT) / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _ts_ms(date_str: str) -> int:
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=9, minute=0, second=0, microsecond=0,
        tzinfo=datetime.timezone(datetime.timedelta(hours=9)),
    )
    return int(dt.timestamp() * 1000)


IS_START = _ts_ms("2018-06-01")
IS_END = _ts_ms("2023-12-31")
OOS_START = _ts_ms("2024-01-01")
OOS_END = _ts_ms("2026-04-25")


def ts_to_date(ts_ms: int) -> str:
    dt = datetime.datetime.fromtimestamp(
        ts_ms / 1000, tz=datetime.timezone(datetime.timedelta(hours=9))
    )
    return dt.strftime("%Y-%m-%d")


def load_btc() -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    df = conn.execute(
        """
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE exchange='upbit' AND symbol='BTC/KRW' AND timeframe='1d'
        ORDER BY ts
        """
    ).fetchdf()
    conn.close()
    return df.reset_index(drop=True)


def make_composite_relaxed(
    rsi_threshold: float,
    vol_mult: float,
    ema200_mask: np.ndarray,
    dc_period: int = 20,
    rsi_period: int = 10,
    vol_ma: int = 20,
    atr_period: int = 14,
    vol_lookback: int = 60,
) -> Callable[[pd.DataFrame], pd.Series]:
    """composite DC20에 EMA200 게이트(외부 mask) 적용.
    rsi_threshold 와 vol_mult 만 변경 — 다른 파라미터는 운영 기본값.
    """
    def strategy(df: pd.DataFrame) -> pd.Series:
        n = len(df)
        close_series = df["close"]
        close = close_series.values
        volume = df["volume"].values
        atr_series = _calc_atr(df, atr_period)
        atr = atr_series.values
        dc_upper = _calc_donchian_upper(df, dc_period).values
        rsi_vals = _calc_rsi(close_series, rsi_period).values
        vsma_vals = _calc_vol_sma(df, vol_ma).values
        norm_vol = (atr_series / close_series).values

        def _percentile_rank(arr: np.ndarray, lookback: int) -> np.ndarray:
            n_arr = len(arr)
            ranks = np.full(n_arr, np.nan)
            for i in range(lookback - 1, n_arr):
                window = arr[i - lookback + 1:i + 1]
                valid = window[~np.isnan(window)]
                if len(valid) < lookback // 2:
                    continue
                cur = arr[i]
                if np.isnan(cur):
                    continue
                ranks[i] = np.sum(valid <= cur) / len(valid)
            return ranks

        pct_rank = _percentile_rank(norm_vol, vol_lookback)

        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            v = volume[i]
            a = atr[i]
            u = dc_upper[i]
            r = rsi_vals[i]
            vs = vsma_vals[i]
            pr = pct_rank[i]

            adaptive_mult = 3.0 if np.isnan(pr) else 2.0 + 2.0 * pr

            can_enter = bool(ema200_mask[i])

            if not in_position:
                dc_ok = (not np.isnan(u)) and (c > u)
                rsi_ok = (not np.isnan(r)) and (r > rsi_threshold)
                vol_ok = (not np.isnan(vs)) and (v > vs * vol_mult)
                if can_enter and dc_ok and (rsi_ok or vol_ok):
                    in_position = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                if not np.isnan(a):
                    trail = highest - a * adaptive_mult
                    if c < trail:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1
        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


def build_ema200_mask(close: np.ndarray) -> np.ndarray:
    ema = _calc_ema(pd.Series(close), 200).values
    n = len(close)
    m = np.ones(n, dtype=np.int8)
    for i in range(n):
        if not np.isnan(ema[i]) and close[i] < ema[i]:
            m[i] = 0
    return m


def split_metrics(eq: pd.DataFrame, tr: pd.DataFrame, s: int, e: int):
    sub = eq[(eq["ts"] >= s) & (eq["ts"] <= e)].reset_index(drop=True)
    sub_tr = (
        tr[(tr["entry_ts"] >= s) & (tr["entry_ts"] <= e)].copy()
        if len(tr) > 0 else tr.copy()
    )
    if len(sub) > 1:
        return compute_metrics(sub, sub_tr), sub_tr
    return Metrics(0, 0, 0, 0, 0, 0, 0), sub_tr


def metrics_to_dict(m: Metrics) -> dict:
    return {
        "total_return_pct": float(getattr(m, "total_return", 0)) * 100.0,
        "sharpe": float(getattr(m, "sharpe", 0)),
        "max_drawdown_pct": float(getattr(m, "max_drawdown", 0)) * 100.0,
        "win_rate": float(getattr(m, "win_rate", 0)),
        "trade_count": int(getattr(m, "n_trades", 0)),
        "avg_trade_pct": float(getattr(m, "avg_trade_return", 0)) * 100.0,
        "calmar": float(getattr(m, "calmar", 0)),
    }


def main():
    print("[1/3] 데이터 로드…")
    btc = load_btc()
    n = len(btc)
    close = btc["close"].values
    ema200_mask = build_ema200_mask(close)
    print(f"      BTC rows={n}, EMA200 OPEN 비율={ema200_mask.mean():.3f}")

    grid = [
        ("G1_50_1.5_baseline", 50.0, 1.5),
        ("G2_50_1.2", 50.0, 1.2),
        ("G3_50_1.0", 50.0, 1.0),
        ("G4_45_1.5", 45.0, 1.5),
        ("G5_45_1.2", 45.0, 1.2),
        ("G6_40_1.0_aggressive", 40.0, 1.0),
    ]

    engine = BacktestEngine()
    results = {}

    for label, rsi_t, vol_m in grid:
        print(f"[2/3] {label} (RSI>{rsi_t}, Vol×{vol_m})…")
        strat = make_composite_relaxed(rsi_t, vol_m, ema200_mask)
        run = engine.run(strat, btc.copy())
        is_m, _ = split_metrics(run.equity_curve, run.trade_log, IS_START, IS_END)
        oos_m, oos_tr = split_metrics(run.equity_curve, run.trade_log, OOS_START, OOS_END)
        results[label] = {
            "rsi_threshold": rsi_t,
            "vol_mult": vol_m,
            "is": metrics_to_dict(is_m),
            "oos": metrics_to_dict(oos_m),
        }

    json_path = OUTPUT_DIR / "entry_relaxation_summary.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"      JSON: {json_path}")

    md = [
        "# composite 진입 조건 완화 그리드 — 백테스트 요약",
        "",
        f"- 데이터: BTC/KRW 일봉 {ts_to_date(int(btc['ts'].min()))} ~ {ts_to_date(int(btc['ts'].max()))}",
        "- IS: 2018-06 ~ 2023-12 / OOS: 2024-01 ~ 2026-04-25",
        "- EMA200 게이트 항상 ON (close<EMA200 시 entry 차단)",
        "- 변경 파라미터: RSI 임계, Vol 배수만",
        "",
        "## OOS 메트릭 비교",
        "",
        "| 시나리오 | RSI | Vol× | 총수익% | Sharpe | MDD% | 승률 | 거래수 | Calmar |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, r in results.items():
        oos = r["oos"]
        md.append(
            f"| {label} | {r['rsi_threshold']:.0f} | {r['vol_mult']} | "
            f"{oos['total_return_pct']:.2f} | {oos['sharpe']:.3f} | "
            f"{oos['max_drawdown_pct']:.2f} | {oos['win_rate']:.3f} | "
            f"{oos['trade_count']} | {oos['calmar']:.3f} |"
        )

    md += [
        "",
        "## IS 메트릭 비교 (참조)",
        "",
        "| 시나리오 | 총수익% | Sharpe | MDD% | 승률 | 거래수 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, r in results.items():
        is_ = r["is"]
        md.append(
            f"| {label} | {is_['total_return_pct']:.2f} | {is_['sharpe']:.3f} | "
            f"{is_['max_drawdown_pct']:.2f} | {is_['win_rate']:.3f} | {is_['trade_count']} |"
        )

    md_path = OUTPUT_DIR / "entry_relaxation_summary.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[3/3] MD: {md_path}")


if __name__ == "__main__":
    main()
