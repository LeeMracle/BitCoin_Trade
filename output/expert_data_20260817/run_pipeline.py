"""IS -> OOS 순차 실행 + 시장 국면 분포 리포트.

step2 fetch 완료 후 실행.
"""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

import duckdb
import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent
STEP3 = HERE / "step3_backtest_mtf_pullback_5r.py"


def market_regime_distribution(start_iso: str, end_iso: str) -> dict:
    """BTC daily close vs EMA200 국면 분포 (BULL/BEAR 비율)."""
    con = duckdb.connect(str(HERE.parents[1] / "data" / "cache.duckdb"), read_only=True)
    rows = con.execute("""
        SELECT ts, close FROM ohlcv
        WHERE exchange='upbit' AND symbol='BTC/KRW' AND timeframe='1d'
        ORDER BY ts
    """).fetchall()
    con.close()
    df = pd.DataFrame(rows, columns=["ts", "close"])
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df["ema200"] = df["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    df["regime"] = np.where(df["close"] > df["ema200"], "BULL", "BEAR")

    start = pd.Timestamp(start_iso.replace("Z", "+00:00"))
    end = pd.Timestamp(end_iso.replace("Z", "+00:00"))
    sub = df[(df["dt"] >= start) & (df["dt"] <= end)].copy()
    if sub.empty:
        return {}
    total = len(sub)
    bull_days = int((sub["regime"] == "BULL").sum())
    bear_days = int((sub["regime"] == "BEAR").sum())
    total_ret = float(sub["close"].iloc[-1] / sub["close"].iloc[0] - 1)
    peak = sub["close"].cummax()
    dd = sub["close"] / peak - 1
    mdd_btc = float(dd.min())
    return {
        "start": start_iso, "end": end_iso,
        "total_days": total,
        "bull_days": bull_days, "bear_days": bear_days,
        "bull_ratio": round(bull_days / total, 3) if total else 0,
        "bear_ratio": round(bear_days / total, 3) if total else 0,
        "btc_total_return_pct": round(total_ret * 100, 2),
        "btc_mdd_pct": round(mdd_btc * 100, 2),
    }


def main():
    # 시장 국면 리포트
    regimes = {
        "IS": market_regime_distribution("2024-01-01T00:00:00Z", "2025-06-30T23:59:59Z"),
        "OOS": market_regime_distribution("2025-07-01T00:00:00Z", "2026-08-16T23:59:59Z"),
        "FULL": market_regime_distribution("2024-01-01T00:00:00Z", "2026-08-16T23:59:59Z"),
    }
    (HERE / "market_regime.json").write_text(
        json.dumps(regimes, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== Market Regime Distribution ===")
    for k, v in regimes.items():
        print(f"  {k}: {v}")

    # IS -> OOS 순차
    env = {**{"PYTHONUTF8": "1"}}
    for phase in ["is", "oos"]:
        cmd = [sys.executable, str(STEP3), "--phase", phase, "--out-tag", "20260817"]
        print(f"\n>>> running {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, env={**env, **_get_env()})
        print(r.stdout)
        if r.returncode != 0:
            print(f"[ERR stderr] {r.stderr}")
            sys.exit(r.returncode)


def _get_env() -> dict:
    import os
    return dict(os.environ)


if __name__ == "__main__":
    main()
