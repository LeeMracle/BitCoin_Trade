"""Step 4 — 감도 분석 sweep.

RSI trigger x TP x SL = 3 x 3 x 3 = 27 조합. OOS 기준 성과 표.
step3의 함수를 재사용.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from step3_backtest_mtf_pullback_5r import (
    iso_to_ms, load_ohlcv, build_daily_features, build_4h_features,
    run_backtest, compute_perf_metrics, INITIAL_CAPITAL,
)
import step3_backtest_mtf_pullback_5r as step3  # for _CORR_CACHE

RSI_GRID = [35.0, 40.0, 45.0]
TP_GRID = [0.04, 0.05, 0.06]
SL_GRID = [-0.020, -0.025, -0.030]


def main():
    start = "2025-07-01T00:00:00Z"
    end = "2026-08-16T23:59:59Z"
    start_ms = iso_to_ms(start)
    end_ms = iso_to_ms(end)

    fetch_log_v2 = Path(__file__).resolve().parent / "fetch_ohlcv_log_v2.json"
    fetch_log_v1 = Path(__file__).resolve().parent / "fetch_ohlcv_log.json"
    fetch_log_path = fetch_log_v2 if fetch_log_v2.exists() else fetch_log_v1
    if fetch_log_path.exists():
        flog = json.loads(fetch_log_path.read_text(encoding="utf-8"))
        universe = list(flog.get("usable_symbols", []))
    else:
        uni_path = Path(__file__).resolve().parent / "universe_screen.json"
        uni = json.loads(uni_path.read_text(encoding="utf-8"))
        universe = [c["symbol"] for c in uni["candidates"]]
    if "BTC/KRW" not in universe:
        universe.insert(0, "BTC/KRW")
    print(f"[universe] {len(universe)} symbols")

    lookback_ms = 260 * 86_400_000
    data_start = start_ms - lookback_ms
    sym_daily = {}
    sym_4h = {}
    for sym in universe:
        sym_daily[sym] = load_ohlcv(sym, "1d", data_start, end_ms)
        sym_4h[sym] = load_ohlcv(sym, "4h", data_start, end_ms)
    sym_daily = build_daily_features(sym_daily)
    sym_4h = build_4h_features(sym_4h)

    rows = []
    for rsi_t in RSI_GRID:
        for tp in TP_GRID:
            for sl in SL_GRID:
                step3._CORR_CACHE = {}
                result = run_backtest(
                    universe=universe, sym_daily=sym_daily, sym_4h=sym_4h,
                    start_ms=start_ms, end_ms=end_ms,
                    tp=tp, sl=sl, rsi_trigger=rsi_t,
                )
                m = compute_perf_metrics(result, INITIAL_CAPITAL, start_ms, end_ms)
                rows.append({
                    "rsi_trigger": rsi_t, "tp": tp, "sl": sl,
                    **{k: v for k, v in m.items() if k not in ("exit_reasons",)},
                    "n_tp": m.get("exit_reasons", {}).get("TP", 0),
                    "n_sl": m.get("exit_reasons", {}).get("SL", 0),
                    "n_time": m.get("exit_reasons", {}).get("TIME", 0),
                })
                print(f"rsi={rsi_t} tp={tp:.3f} sl={sl:.3f} "
                      f"n={m['n_trades']} sharpe={m['sharpe']:.2f} "
                      f"mdd={m['mdd_pct']:.2f}% pf={m['profit_factor']:.2f}")

    df = pd.DataFrame(rows)
    out = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "mtf_pullback_5r" / "sensitivity_20260817.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
