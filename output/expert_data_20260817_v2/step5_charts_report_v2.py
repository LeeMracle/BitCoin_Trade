"""Step 5 (v2) — 차트(자본곡선, 월별 히트맵) 및 리포트 부속 계산."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = Path(__file__).resolve().parent
RUN_DIR = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "mtf_pullback_5r_v2"


def load_equity(phase: str, tag: str = "run") -> pd.DataFrame:
    p = RUN_DIR / f"equity_{phase}_{tag}.csv"
    df = pd.read_csv(p)
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def load_trades(phase: str, tag: str = "run") -> pd.DataFrame:
    p = RUN_DIR / f"trades_{phase}_{tag}.csv"
    df = pd.read_csv(p)
    df["entry_dt"] = pd.to_datetime(df["entry_dt_utc"], utc=True)
    df["exit_dt"] = pd.to_datetime(df["exit_dt_utc"], utc=True)
    return df


def plot_equity_curve(is_eq: pd.DataFrame, oos_eq: pd.DataFrame, out_png: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    if not is_eq.empty:
        ax.plot(is_eq["dt"], is_eq["equity"], label="IS (2024-01 ~ 2025-06)", color="#3b82f6")
    if not oos_eq.empty:
        ax.plot(oos_eq["dt"], oos_eq["equity"], label="OOS (2025-07 ~ 2026-08)", color="#ef4444")
    ax.axhline(1e7, color="gray", linestyle="--", alpha=0.5, label="initial 10M")
    ax.set_title("MTF_Pullback_5R Equity Curve")
    ax.set_ylabel("Equity (KRW)")
    ax.set_xlabel("Date (UTC)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=100)
    plt.close(fig)


def monthly_heatmap(trades: pd.DataFrame, out_png: Path) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    trades["exit_month"] = trades["exit_dt"].dt.to_period("M")
    grp = trades.groupby("exit_month")["pnl_krw"].sum().sort_index()

    # to matrix year x month
    dfp = grp.reset_index()
    dfp["year"] = dfp["exit_month"].dt.year
    dfp["month"] = dfp["exit_month"].dt.month
    mat = dfp.pivot(index="year", columns="month", values="pnl_krw").fillna(0)

    fig, ax = plt.subplots(figsize=(11, 3.5))
    im = ax.imshow(mat.values, cmap="RdYlGn", aspect="auto",
                    vmin=-abs(mat.values).max(), vmax=abs(mat.values).max())
    ax.set_xticks(range(len(mat.columns)))
    ax.set_xticklabels(mat.columns)
    ax.set_yticks(range(len(mat.index)))
    ax.set_yticklabels(mat.index)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v != 0:
                ax.text(j, i, f"{v/1e4:.0f}만", ha="center", va="center",
                        fontsize=8, color="black")
    ax.set_title("Monthly PnL Heatmap (KRW)")
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(out_png, dpi=100)
    plt.close(fig)
    return mat


def bear_week_analysis(trades: pd.DataFrame, btc_daily: pd.DataFrame) -> dict:
    """BTC -10% 이상 급락 주(week) 판정 + 스윙 전략 성과."""
    if btc_daily.empty or trades.empty:
        return {}
    btc = btc_daily.copy()
    btc["dt"] = pd.to_datetime(btc["ts"], unit="ms", utc=True)
    btc = btc.sort_values("dt").reset_index(drop=True)
    btc["week"] = btc["dt"].dt.to_period("W")
    # 주 단위 open->close 수익률
    wg = btc.groupby("week").agg(
        first_close=("close", "first"),
        last_close=("close", "last"),
    )
    wg["ret"] = wg["last_close"] / wg["first_close"] - 1
    bear_weeks = wg[wg["ret"] <= -0.10].index.tolist()
    if not bear_weeks:
        return {"bear_weeks": 0, "trades_in_bear": 0, "pnl_in_bear": 0}

    trades = trades.copy()
    trades["week"] = trades["exit_dt"].dt.to_period("W")
    bt = trades[trades["week"].isin(bear_weeks)]
    return {
        "bear_weeks": len(bear_weeks),
        "trades_in_bear": len(bt),
        "pnl_in_bear_krw": float(bt["pnl_krw"].sum()) if not bt.empty else 0,
        "bear_week_periods": [str(w) for w in bear_weeks],
    }


def main():
    tag = "20260817"
    # phase IS
    try:
        is_eq = load_equity("is", tag)
        is_tr = load_trades("is", tag)
    except FileNotFoundError:
        is_eq = pd.DataFrame(); is_tr = pd.DataFrame()
    # phase OOS
    oos_eq = load_equity("oos", tag)
    oos_tr = load_trades("oos", tag)

    plot_equity_curve(is_eq, oos_eq, RUN_DIR / "equity_curve_20260817.png")

    # heatmap: OOS 중심
    combined = pd.concat([is_tr, oos_tr], ignore_index=True) if not is_tr.empty else oos_tr
    combined["exit_dt"] = pd.to_datetime(combined["exit_dt_utc"], utc=True)
    mat = monthly_heatmap(combined, RUN_DIR / "monthly_heatmap_20260817.png")

    # bear week analysis (OOS 만)
    import duckdb
    con = duckdb.connect(str(Path(__file__).resolve().parents[2] / "data" / "cache.duckdb"),
                          read_only=True)
    btc_rows = con.execute("""
        SELECT ts, close FROM ohlcv
        WHERE exchange='upbit' AND symbol='BTC/KRW' AND timeframe='1d'
        ORDER BY ts
    """).fetchall()
    con.close()
    btc_df = pd.DataFrame(btc_rows, columns=["ts", "close"])
    bear = bear_week_analysis(oos_tr, btc_df)

    # 종목별 PnL
    sym_pnl = oos_tr.groupby("symbol").agg(
        n=("pnl_krw", "count"),
        total_pnl=("pnl_krw", "sum"),
        win_rate=("ret_pct", lambda x: (x > 0).mean() * 100),
    ).sort_values("total_pnl", ascending=False)

    # top/bottom 10 trades
    top_wins = oos_tr.nlargest(10, "pnl_krw")
    top_losses = oos_tr.nsmallest(10, "pnl_krw")

    # Period/Timestamp를 str 로 정규화
    def _norm(df: pd.DataFrame) -> list:
        d = df.copy()
        for c in d.columns:
            if pd.api.types.is_datetime64_any_dtype(d[c]) or pd.api.types.is_period_dtype(d[c]):
                d[c] = d[c].astype(str)
        return d.to_dict("records")
    summary = {
        "bear_week": bear,
        "sym_pnl_top": _norm(sym_pnl.head(15).reset_index()),
        "sym_pnl_bottom": _norm(sym_pnl.tail(15).reset_index()),
        "top_wins": _norm(top_wins),
        "top_losses": _norm(top_losses),
        "monthly_matrix": mat.to_dict() if not mat.empty else {},
    }
    (RUN_DIR / "analysis_20260817.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"saved charts + analysis to {RUN_DIR}")


if __name__ == "__main__":
    main()
