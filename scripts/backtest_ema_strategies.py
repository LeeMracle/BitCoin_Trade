# -*- coding: utf-8 -*-
"""EMA 기반 투자 전략 3종 9년 백테스트.

전략:
  A. EMA Trend Follow  : 종가가 EMA(50) 위로 돌파 + 트레일링스탑 5%
  B. Golden Cross      : EMA(50) > EMA(200) 골든크로스/데드크로스
  C. EMA200 Invest     : 종가가 EMA(200) 위로 돌파 + 트레일링스탑 10%

실행:
    PYTHONUTF8=1 python scripts/backtest_ema_strategies.py

산출물:
    output/ema_strategy_screening.md
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
import numpy as np
import pandas as pd

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

DB_PATH    = ROOT / "data" / "cache.duckdb"
OUTPUT_MD  = ROOT / "output" / "ema_strategy_screening.md"

WARMUP_START = "2017-10-01"
WARMUP_END   = "2018-05-31"
IS_START     = "2018-06-01"
IS_END       = "2023-12-31"
OOS_START    = "2024-01-01"
OOS_END      = "2026-04-05"

BT_PARAMS = dict(
    initial_capital=10_000_000,
    fee_rate=0.0005,
    slippage_bps=5,
)

# Composite DC20 비교 기준값 (기존 백테스트 결과)
BASELINE = {
    "IS Sharpe":  1.52,
    "IS MDD":    -22.9,
    "OOS Sharpe": 1.11,
    "OOS MDD":   -11.3,
}


# ──────────────────────────────────────────────
# 공통 유틸
# ──────────────────────────────────────────────

def _date_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_ohlcv(start: str, end: str) -> pd.DataFrame:
    """DuckDB에서 BTC/KRW 일봉 로드."""
    start_ms = _date_to_ms(start)
    end_ms   = _date_to_ms(end)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = con.execute("""
        SELECT ts, open, high, low, close, volume
        FROM ohlcv
        WHERE exchange='upbit' AND symbol='BTC/KRW' AND timeframe='1d'
          AND ts >= ? AND ts <= ?
        ORDER BY ts
    """, [start_ms, end_ms]).fetchall()
    con.close()

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"]   = df["ts"].astype(np.int64)
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    return df


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def compute_subset_metrics(result, period_start: str, period_end: str, label: str) -> dict:
    """전체 RunResult에서 특정 기간만 추려 메트릭 재계산."""
    start_ms = _date_to_ms(period_start)
    end_ms   = _date_to_ms(period_end) + 86_400_000

    eq    = result.equity_curve
    eq_sub = eq[(eq["ts"] >= start_ms) & (eq["ts"] <= end_ms)].copy()

    tl = result.trade_log
    if len(tl) > 0:
        tl_sub = tl[(tl["entry_ts"] >= start_ms) & (tl["entry_ts"] <= end_ms)].copy()
    else:
        tl_sub = tl.copy()

    if len(eq_sub) < 2:
        return {}

    m = compute_metrics(eq_sub, tl_sub)
    return {
        "기간":          label,
        "Sharpe":        m.sharpe,
        "MDD(%)":        round(m.max_drawdown * 100, 1),
        "총수익률(%)":   round(m.total_return  * 100, 1),
        "거래수":        m.n_trades,
        "승률(%)":       round(m.win_rate       * 100, 1),
        "평균수익률(%)": round(m.avg_trade_return * 100, 2),
        "Calmar":        m.calmar,
    }


# ──────────────────────────────────────────────
# 전략 A: EMA Trend Follow
#   진입: 종가 EMA(50) 상향 돌파
#   청산: 트레일링스탑(-5%) OR EMA(50) 하향 이탈
# ──────────────────────────────────────────────

def make_strategy_ema_trend_follow(
    ema_period: int = 50,
    trail_pct:  float = 0.05,
):
    """전략 A — EMA 크로스 + 트레일링스탑."""

    def strategy(df: pd.DataFrame) -> pd.Series:
        close    = df["close"].values
        ema_vals = _calc_ema(df["close"], ema_period).values

        n          = len(df)
        signal     = np.zeros(n, dtype=np.int8)
        in_pos     = False
        highest    = 0.0

        for i in range(n):
            c = close[i]
            e = ema_vals[i]

            if np.isnan(e):
                signal[i] = 1 if in_pos else 0
                continue

            if not in_pos:
                # 상향 돌파: 전날 close < EMA, 오늘 close > EMA
                if i >= 1 and close[i - 1] < ema_vals[i - 1] and c > e:
                    in_pos  = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                trail_stop = highest * (1.0 - trail_pct)

                # 청산 조건 1: 트레일링스탑 이탈
                # 청산 조건 2: EMA 하향 이탈
                if c < trail_stop or c < e:
                    in_pos  = False
                    highest = 0.0
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 B: Golden Cross / Dead Cross
#   진입: EMA(50) > EMA(200) 크로스 (골든크로스)
#   청산: EMA(50) < EMA(200) 크로스 (데드크로스)
# ──────────────────────────────────────────────

def make_strategy_golden_cross(
    fast_ema: int = 50,
    slow_ema: int = 200,
):
    """전략 B — 골든크로스 / 데드크로스."""

    def strategy(df: pd.DataFrame) -> pd.Series:
        close      = df["close"].values
        ema_fast   = _calc_ema(df["close"], fast_ema).values
        ema_slow   = _calc_ema(df["close"], slow_ema).values

        n      = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_pos = False

        for i in range(n):
            ef = ema_fast[i]
            es = ema_slow[i]

            if np.isnan(ef) or np.isnan(es):
                signal[i] = 1 if in_pos else 0
                continue

            if not in_pos:
                # 골든크로스: 전날 fast <= slow, 오늘 fast > slow
                if i >= 1 and ema_fast[i - 1] <= ema_slow[i - 1] and ef > es:
                    in_pos     = True
                    signal[i]  = 1
            else:
                # 데드크로스: 전날 fast >= slow, 오늘 fast < slow
                if i >= 1 and ema_fast[i - 1] >= ema_slow[i - 1] and ef < es:
                    in_pos     = False
                    signal[i]  = 0
                else:
                    signal[i]  = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 C: EMA200 장기 투자
#   진입: 종가 EMA(200) 상향 돌파
#   청산: 트레일링스탑(-10%) OR EMA(200) 하향 이탈 (먼저 발생하는 쪽)
# ──────────────────────────────────────────────

def make_strategy_ema200_invest(
    ema_period: int  = 200,
    trail_pct:  float = 0.10,
):
    """전략 C — EMA200 장기 투자."""

    def strategy(df: pd.DataFrame) -> pd.Series:
        close    = df["close"].values
        ema_vals = _calc_ema(df["close"], ema_period).values

        n       = len(df)
        signal  = np.zeros(n, dtype=np.int8)
        in_pos  = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            e = ema_vals[i]

            if np.isnan(e):
                signal[i] = 1 if in_pos else 0
                continue

            if not in_pos:
                # 상향 돌파: 전날 close < EMA200, 오늘 close > EMA200
                if i >= 1 and close[i - 1] < ema_vals[i - 1] and c > e:
                    in_pos  = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                trail_stop = highest * (1.0 - trail_pct)

                # 청산 조건 1: 트레일링스탑
                # 청산 조건 2: EMA200 하향 이탈
                if c < trail_stop or c < e:
                    in_pos  = False
                    highest = 0.0
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 연도별 성과
# ──────────────────────────────────────────────

def yearly_analysis(result, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    eq     = result.equity_curve.copy()
    tl     = result.trade_log.copy()
    eq["year"] = pd.to_datetime(eq["ts"], unit="ms", utc=True).dt.year

    if len(tl) > 0:
        tl["entry_year"] = pd.to_datetime(tl["entry_ts"], unit="ms", utc=True).dt.year

    rows = []
    for year, grp in eq.groupby("year"):
        grp_sorted = grp.sort_values("ts")
        eq_start   = grp_sorted["equity"].iloc[0]
        eq_end     = grp_sorted["equity"].iloc[-1]
        ret        = (eq_end / eq_start) - 1

        if len(tl) > 0 and "entry_year" in tl.columns:
            yr_trades = tl[tl["entry_year"] == year]
        else:
            yr_trades = pd.DataFrame()
        n_t      = len(yr_trades)
        wr       = (yr_trades["return_pct"] > 0).mean() * 100 if n_t > 0 else float("nan")

        # Buy&Hold
        oh_yr = ohlcv_df[ohlcv_df["date"].str.startswith(str(int(year)))]
        bh    = (oh_yr["close"].iloc[-1] / oh_yr["open"].iloc[0]) - 1 if len(oh_yr) > 0 else float("nan")

        rows.append({
            "연도":            int(year),
            "전략수익률(%)":   round(ret * 100, 1),
            "B&H수익률(%)":    round(bh  * 100, 1) if not np.isnan(bh) else "-",
            "거래수":          n_t,
            "승률(%)":         round(wr, 1) if not np.isnan(wr) else "-",
        })

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 2022년 전쟁 구간 분석 (2022-02-01 ~ 2022-06-30)
# ──────────────────────────────────────────────

def war_zone_analysis(result, label: str) -> dict:
    """2022-02~06 우크라이나 전쟁·Luna 크래시 구간 포지션 상태."""
    WAR_START = "2022-02-01"
    WAR_END   = "2022-06-30"
    ws_ms = _date_to_ms(WAR_START)
    we_ms = _date_to_ms(WAR_END) + 86_400_000

    eq     = result.equity_curve
    eq_sub = eq[(eq["ts"] >= ws_ms) & (eq["ts"] <= we_ms)].copy()
    if len(eq_sub) < 2:
        return {"전략": label, "구간수익률(%)": "-", "구간MDD(%)": "-", "거래수": 0}

    eq_ret = (eq_sub["equity"].iloc[-1] / eq_sub["equity"].iloc[0]) - 1
    # 구간 MDD
    vals  = eq_sub["equity"].values
    peak  = np.maximum.accumulate(vals)
    mdd   = float(((vals - peak) / peak).min())

    tl    = result.trade_log
    if len(tl) > 0:
        tl_war = tl[(tl["entry_ts"] >= ws_ms) & (tl["entry_ts"] <= we_ms)]
        n_t    = len(tl_war)
    else:
        n_t = 0

    return {
        "전략":           label,
        "구간수익률(%)":  round(eq_ret * 100, 1),
        "구간MDD(%)":     round(mdd    * 100, 1),
        "거래수":         n_t,
    }


# ──────────────────────────────────────────────
# 마크다운 유틸
# ──────────────────────────────────────────────

def _fmt(v) -> str:
    if isinstance(v, float):
        if np.isnan(v):
            return "-"
        if v == int(v):
            return str(int(v))
    return str(v)


def _df_to_md(df: pd.DataFrame) -> str:
    if df is None or len(df) == 0:
        return "(데이터 없음)\n"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep    = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows   = []
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(_fmt(v) for v in row.values) + " |")
    return "\n".join([header, sep] + rows) + "\n"


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("EMA 기반 투자 전략 3종 9년 백테스트")
    print("=" * 60)

    # 1. 데이터 로드 (워밍업 포함 전체 기간)
    print("\n[1] 데이터 로드 중...")
    df_full = load_ohlcv(WARMUP_START, OOS_END)
    print(f"  전체 OHLCV: {len(df_full)}봉 ({df_full['date'].iloc[0]} ~ {df_full['date'].iloc[-1]})")

    # IS / OOS 구간 슬라이스 (연도별 분석용)
    df_is  = df_full[(df_full["date"] >= IS_START)  & (df_full["date"] <= IS_END)]
    df_oos = df_full[(df_full["date"] >= OOS_START) & (df_full["date"] <= OOS_END)]

    engine = BacktestEngine()

    # ── 전략 정의 ──
    strategies = {
        "A_EMA_TrendFollow": {
            "fn":     make_strategy_ema_trend_follow(ema_period=50, trail_pct=0.05),
            "label":  "전략 A: EMA Trend Follow (EMA50 + Trail 5%)",
            "params": "ema_period=50, trail_pct=0.05",
        },
        "B_GoldenCross": {
            "fn":     make_strategy_golden_cross(fast_ema=50, slow_ema=200),
            "label":  "전략 B: Golden Cross (EMA50 / EMA200)",
            "params": "fast_ema=50, slow_ema=200",
        },
        "C_EMA200_Invest": {
            "fn":     make_strategy_ema200_invest(ema_period=200, trail_pct=0.10),
            "label":  "전략 C: EMA200 Invest (EMA200 + Trail 10%)",
            "params": "ema_period=200, trail_pct=0.10",
        },
    }

    results        = {}   # key -> RunResult
    is_metrics_map = {}
    oos_metrics_map= {}

    # 2. 각 전략 백테스트
    print("\n[2] 전략 백테스트 실행 중...")
    for key, cfg in strategies.items():
        print(f"\n  --- {cfg['label']} ---")
        result = engine.run(cfg["fn"], df_full, params=BT_PARAMS)
        results[key] = result

        is_m  = compute_subset_metrics(result, IS_START,  IS_END,  "IS")
        oos_m = compute_subset_metrics(result, OOS_START, OOS_END, "OOS")
        is_metrics_map[key]  = is_m
        oos_metrics_map[key] = oos_m

        print(f"    IS  → Sharpe: {is_m.get('Sharpe','N/A')}, MDD: {is_m.get('MDD(%)','N/A')}%, "
              f"거래수: {is_m.get('거래수','N/A')}, 승률: {is_m.get('승률(%)','N/A')}%")
        print(f"    OOS → Sharpe: {oos_m.get('Sharpe','N/A')}, MDD: {oos_m.get('MDD(%)','N/A')}%, "
              f"거래수: {oos_m.get('거래수','N/A')}, 승률: {oos_m.get('승률(%)','N/A')}%")

    # 3. 연도별 성과
    print("\n[3] 연도별 성과 산출 중...")
    yearly_maps = {}
    for key, result in results.items():
        yearly_maps[key] = yearly_analysis(result, df_full)

    # 4. 2022 전쟁 구간
    print("\n[4] 2022 전쟁 구간 분석 중...")
    war_rows = []
    for key, result in results.items():
        war_rows.append(war_zone_analysis(result, strategies[key]["label"]))
    war_df = pd.DataFrame(war_rows)

    # 5. 보고서 생성
    print("\n[5] 보고서 생성 중...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append("# EMA 기반 투자 전략 3종 백테스트 결과\n")
    lines.append(f"생성일시: {now}  ")
    lines.append(f"데이터: BTC/KRW 일봉 (업비트), {df_full['date'].iloc[0]} ~ {df_full['date'].iloc[-1]}, {len(df_full)}봉\n")

    # 기간 정의
    lines.append("## 1. 기간 정의\n")
    lines.append("| 구분 | 기간 | 목적 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 워밍업 | {WARMUP_START} ~ {WARMUP_END} | EMA200 지표 안정화 |")
    lines.append(f"| IS (인샘플) | {IS_START} ~ {IS_END} | 전략 파라미터 기준 |")
    lines.append(f"| OOS (아웃오브샘플) | {OOS_START} ~ {OOS_END} | 실제 검증 구간 |")
    lines.append("")

    # 전략 개요
    lines.append("## 2. 전략 개요\n")
    lines.append("| 전략 | 진입 조건 | 청산 조건 | 파라미터 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append("| A: EMA Trend Follow | 종가 EMA(50) 상향 돌파 | 트레일링 -5% OR EMA(50) 하향 이탈 | ema_period=50, trail_pct=5% |")
    lines.append("| B: Golden Cross | EMA(50) > EMA(200) 골든크로스 | EMA(50) < EMA(200) 데드크로스 | fast=50, slow=200 |")
    lines.append("| C: EMA200 Invest | 종가 EMA(200) 상향 돌파 | 트레일링 -10% OR EMA(200) 하향 이탈 | ema_period=200, trail_pct=10% |")
    lines.append("")

    # 핵심 성과 비교 표
    lines.append("## 3. 핵심 성과 비교\n")
    lines.append("| 전략 | IS Sharpe | IS MDD | OOS Sharpe | OOS MDD | IS 거래수 | OOS 거래수 | IS 승률 | OOS 승률 | 판정 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    # Composite DC20 기준선 추가
    lines.append(f"| **Composite DC20** (기준) | **{BASELINE['IS Sharpe']}** | **{BASELINE['IS MDD']}%** | **{BASELINE['OOS Sharpe']}** | **{BASELINE['OOS MDD']}%** | - | - | - | - | 기준선 |")

    strategy_labels = {
        "A_EMA_TrendFollow": "A: EMA Trend Follow",
        "B_GoldenCross":     "B: Golden Cross",
        "C_EMA200_Invest":   "C: EMA200 Invest",
    }

    for key in strategies:
        im  = is_metrics_map[key]
        om  = oos_metrics_map[key]
        lbl = strategy_labels[key]

        is_s  = im.get("Sharpe", "-")
        is_m  = im.get("MDD(%)", "-")
        oos_s = om.get("Sharpe", "-")
        oos_m = om.get("MDD(%)", "-")
        is_t  = im.get("거래수", "-")
        oos_t = om.get("거래수", "-")
        is_w  = im.get("승률(%)", "-")
        oos_w = om.get("승률(%)", "-")

        # 판정: OOS Sharpe 기준
        if isinstance(oos_s, float):
            if oos_s >= 1.0:
                verdict = "PASS"
            elif oos_s >= 0.5:
                verdict = "보통"
            else:
                verdict = "FAIL"
        else:
            verdict = "-"

        lines.append(f"| {lbl} | {is_s} | {is_m}% | {oos_s} | {oos_m}% | {is_t} | {oos_t} | {is_w}% | {oos_w}% | {verdict} |")

    lines.append("")

    # 상세 메트릭 (전략별)
    lines.append("## 4. 전략별 상세 메트릭\n")
    for key, cfg in strategies.items():
        im  = is_metrics_map[key]
        om  = oos_metrics_map[key]
        lines.append(f"### {cfg['label']}\n")
        lines.append(f"파라미터: `{cfg['params']}`\n")
        if im and om:
            detail_df = pd.DataFrame([im, om])
            lines.append(_df_to_md(detail_df))
        else:
            lines.append("(데이터 부족)\n")
        lines.append("")

    # 연도별 성과 비교
    lines.append("## 5. 연도별 수익률 비교\n")
    lines.append("> 2022년 하락장(-65%), 2022-02~06 전쟁·Luna 크래시 구간 동작 주목\n")

    for key, cfg in strategies.items():
        yr_df = yearly_maps[key]
        yr_is  = yr_df[yr_df["연도"] <= 2023]
        yr_oos = yr_df[yr_df["연도"] >= 2024]

        lines.append(f"### {cfg['label']}\n")
        lines.append("**IS 기간 (2018~2023)**\n")
        lines.append(_df_to_md(yr_is))
        lines.append("**OOS 기간 (2024~2026)**\n")
        lines.append(_df_to_md(yr_oos))
        lines.append("")

    # 2022 전쟁 구간
    lines.append("## 6. 2022 전쟁·Luna 크래시 구간 (2022-02-01 ~ 2022-06-30)\n")
    lines.append("> BTC -55% 급락, Luna 사태, FOMC 금리인상 복합 위기\n")
    lines.append(_df_to_md(war_df))
    lines.append("")

    # Composite DC20 비교 분석
    lines.append("## 7. Composite DC20 비교 분석\n")
    lines.append(f"비교 기준: Composite DC20 (IS Sharpe {BASELINE['IS Sharpe']}, OOS Sharpe {BASELINE['OOS Sharpe']})\n")
    lines.append("| 항목 | Composite DC20 | A: EMA Trend | B: Golden Cross | C: EMA200 |")
    lines.append("| --- | --- | --- | --- | --- |")

    metrics_keys = [
        ("IS Sharpe",  "Sharpe",   is_metrics_map),
        ("IS MDD(%)",  "MDD(%)",   is_metrics_map),
        ("OOS Sharpe", "Sharpe",   oos_metrics_map),
        ("OOS MDD(%)", "MDD(%)",   oos_metrics_map),
        ("IS 승률",    "승률(%)",  is_metrics_map),
        ("OOS 승률",   "승률(%)",  oos_metrics_map),
    ]

    for row_label, metric_key, metrics_map in metrics_keys:
        if row_label == "IS Sharpe":
            baseline_val = str(BASELINE["IS Sharpe"])
        elif row_label == "IS MDD(%)":
            baseline_val = f"{BASELINE['IS MDD']}%"
        elif row_label == "OOS Sharpe":
            baseline_val = str(BASELINE["OOS Sharpe"])
        elif row_label == "OOS MDD(%)":
            baseline_val = f"{BASELINE['OOS MDD']}%"
        else:
            baseline_val = "-"

        vals = []
        for key in strategies:
            v = metrics_map[key].get(metric_key, "-")
            if "MDD" in row_label and isinstance(v, (int, float)):
                vals.append(f"{v}%")
            elif "승률" in row_label and isinstance(v, (int, float)):
                vals.append(f"{v}%")
            else:
                vals.append(str(v))

        lines.append(f"| {row_label} | {baseline_val} | {vals[0]} | {vals[1]} | {vals[2]} |")

    lines.append("")

    # 종합 평가
    lines.append("## 8. 종합 평가 및 결론\n")

    for key, cfg in strategies.items():
        im  = is_metrics_map[key]
        om  = oos_metrics_map[key]
        oos_s = om.get("Sharpe", 0)
        is_s  = im.get("Sharpe", 0)
        oos_m = om.get("MDD(%)", 0)

        if isinstance(oos_s, float) and oos_s >= 1.0:
            verdict = "PASS — Composite DC20 대비 경쟁력 있음"
        elif isinstance(oos_s, float) and oos_s >= 0.5:
            verdict = "보통 — 단독 운용보다 보조 전략으로 검토"
        else:
            verdict = "FAIL — OOS 성과 기준 미달"

        lines.append(f"### {cfg['label']}")
        lines.append(f"- IS Sharpe: {is_s}, OOS Sharpe: {oos_s}, OOS MDD: {oos_m}%")
        lines.append(f"- 판정: {verdict}")
        lines.append("")

    lines.append("### 비교 기준")
    lines.append(f"- Composite DC20: IS {BASELINE['IS Sharpe']} / OOS {BASELINE['OOS Sharpe']} (통과 기준)")
    lines.append("- OOS Sharpe >= 1.0: PASS, >= 0.5: 보통, < 0.5: FAIL")
    lines.append("")

    lines.append("---")
    lines.append("*자동 생성: scripts/backtest_ema_strategies.py*")

    report_text = "\n".join(lines)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report_text, encoding="utf-8")
    print(f"\n  보고서 저장: {OUTPUT_MD}")

    # 콘솔 요약
    print("\n" + "=" * 60)
    print("백테스트 완료 요약")
    print("=" * 60)
    print(f"{'전략':<30} {'IS Sharpe':>10} {'IS MDD':>8} {'OOS Sharpe':>11} {'OOS MDD':>8}")
    print("-" * 70)
    print(f"{'[기준] Composite DC20':<30} {BASELINE['IS Sharpe']:>10} {BASELINE['IS MDD']:>7}% {BASELINE['OOS Sharpe']:>11} {BASELINE['OOS MDD']:>7}%")
    for key in strategies:
        im  = is_metrics_map[key]
        om  = oos_metrics_map[key]
        lbl = strategy_labels[key]
        print(f"{lbl:<30} {im.get('Sharpe','-'):>10} {str(im.get('MDD(%)','–'))+' %':>8} {om.get('Sharpe','-'):>11} {str(om.get('MDD(%)','–'))+' %':>8}")

    print(f"\n산출물: {OUTPUT_MD}")


if __name__ == "__main__":
    main()
