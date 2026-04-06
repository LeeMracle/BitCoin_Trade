# -*- coding: utf-8 -*-
"""Composite DC20 전략 9년 백테스트 + 레짐별/연도별 성과 분리.

실행:
    PYTHONUTF8=1 python scripts/backtest_composite_9yr.py

산출물:
    output/composite_9yr_backtest_result.md
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# UTF-8 강제 설정
os.environ.setdefault("PYTHONUTF8", "1")

# 프로젝트 루트를 Python 경로에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
import numpy as np
import pandas as pd

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics
from services.strategies.advanced import make_strategy_composite

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

DB_PATH = ROOT / "data" / "cache.duckdb"
REGIME_CSV = ROOT / "output" / "regime_tags.csv"
OUTPUT_MD = ROOT / "output" / "composite_9yr_backtest_result.md"

# 기간 정의
WARMUP_START = "2017-10-01"
WARMUP_END   = "2018-05-31"
IS_START     = "2018-06-01"
IS_END       = "2023-12-31"
OOS_START    = "2024-01-01"
OOS_END      = "2026-04-05"

# Composite DC20 파라미터
STRATEGY_PARAMS = dict(
    dc_period=20,
    rsi_period=10,
    rsi_threshold=50.0,
    vol_ma=20,
    vol_mult=1.5,
    atr_period=14,
    vol_lookback=60,
    fg_gate=None,
    btc_above_sma=True,
)

# 백테스트 파라미터
BT_PARAMS = dict(
    initial_capital=10_000_000,
    fee_rate=0.0005,
    slippage_bps=5,
)


# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────

def _date_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_ohlcv(start: str, end: str) -> pd.DataFrame:
    """DuckDB에서 BTC/KRW 일봉 데이터 로드."""
    start_ms = _date_to_ms(start)
    end_ms = _date_to_ms(end)

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
    df["ts"] = df["ts"].astype(np.int64)
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    return df


def load_regime() -> pd.DataFrame:
    """레짐 태그 CSV 로드."""
    df = pd.read_csv(REGIME_CSV)
    df["date"] = df["date"].astype(str)
    return df[["date", "regime"]]


# ──────────────────────────────────────────────
# 백테스트 실행
# ──────────────────────────────────────────────

def run_backtest(df_ohlcv: pd.DataFrame, label: str) -> tuple:
    """BacktestEngine으로 백테스트 실행. (RunResult, trade_log_with_date) 반환."""
    print(f"  [{label}] 봉 수: {len(df_ohlcv)}, 기간: {df_ohlcv['date'].iloc[0]} ~ {df_ohlcv['date'].iloc[-1]}")

    strategy_fn = make_strategy_composite(**STRATEGY_PARAMS)
    engine = BacktestEngine()
    result = engine.run(strategy_fn, df_ohlcv, params=BT_PARAMS)

    # trade_log에 날짜 컬럼 추가
    tl = result.trade_log.copy()
    if len(tl) > 0:
        tl["entry_date"] = pd.to_datetime(tl["entry_ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        tl["exit_date"] = pd.to_datetime(tl["exit_ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        tl["entry_year"] = pd.to_datetime(tl["entry_ts"], unit="ms", utc=True).dt.year

    return result, tl


# ──────────────────────────────────────────────
# 레짐별 성과 분리
# ──────────────────────────────────────────────

def regime_analysis(trade_log: pd.DataFrame, regime_df: pd.DataFrame) -> pd.DataFrame:
    """trade_log의 entry_date를 regime_tags와 조인하여 레짐별 성과 집계."""
    if len(trade_log) == 0:
        return pd.DataFrame()

    tl = trade_log.merge(regime_df, left_on="entry_date", right_on="date", how="left")

    results = []
    regime_order = ["BULL", "SIDEWAYS", "BEAR", "CRISIS", "EUPHORIA", "WARMUP"]
    for regime in regime_order:
        grp = tl[tl["regime"] == regime]
        if len(grp) == 0:
            continue
        win_rate = (grp["return_pct"] > 0).mean()
        avg_ret = grp["return_pct"].mean()
        max_loss = grp["return_pct"].min()
        results.append({
            "레짐": regime,
            "거래수": len(grp),
            "승률(%)": round(win_rate * 100, 1),
            "평균수익률(%)": round(avg_ret * 100, 2),
            "최대손실(%)": round(max_loss * 100, 2),
        })

    # 레짐 불명(NaN) 처리
    unknown = tl[tl["regime"].isna()]
    if len(unknown) > 0:
        results.append({
            "레짐": "UNKNOWN",
            "거래수": len(unknown),
            "승률(%)": round((unknown["return_pct"] > 0).mean() * 100, 1),
            "평균수익률(%)": round(unknown["return_pct"].mean() * 100, 2),
            "최대손실(%)": round(unknown["return_pct"].min() * 100, 2),
        })

    return pd.DataFrame(results)


# ──────────────────────────────────────────────
# 연도별 성과 분리
# ──────────────────────────────────────────────

def yearly_analysis(trade_log: pd.DataFrame, equity_df: pd.DataFrame, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    """연도별 수익률 집계.

    equity_curve를 연도별로 분할하여 Buy&Hold와 비교.
    """
    if len(equity_df) == 0:
        return pd.DataFrame()

    equity_df = equity_df.copy()
    equity_df["year"] = pd.to_datetime(equity_df["ts"], unit="ms", utc=True).dt.year

    # 연도별 시작/종료 equity 값으로 수익률 계산
    results = []
    for year, grp in equity_df.groupby("year"):
        grp_sorted = grp.sort_values("ts")
        eq_start = grp_sorted["equity"].iloc[0]
        eq_end = grp_sorted["equity"].iloc[-1]
        ret = (eq_end / eq_start) - 1

        # 해당 연도 거래 (trade_log가 있을 때만)
        if len(trade_log) > 0 and "entry_year" in trade_log.columns:
            year_trades = trade_log[trade_log["entry_year"] == year]
        else:
            year_trades = pd.DataFrame()
        n_trades = len(year_trades)
        win_rate = (year_trades["return_pct"] > 0).mean() if n_trades > 0 else float("nan")

        # Buy&Hold 수익률 (해당 연도 첫 봉 ~ 마지막 봉)
        oh_year = ohlcv_df[ohlcv_df["date"].str.startswith(str(int(year)))]
        if len(oh_year) > 0:
            bh_ret = (oh_year["close"].iloc[-1] / oh_year["open"].iloc[0]) - 1
        else:
            bh_ret = float("nan")

        results.append({
            "연도": int(year),
            "전략수익률(%)": round(ret * 100, 1),
            "B&H수익률(%)": round(bh_ret * 100, 1) if not np.isnan(bh_ret) else "-",
            "거래수": n_trades,
            "승률(%)": round(win_rate * 100, 1) if not np.isnan(win_rate) else "-",
        })

    return pd.DataFrame(results)


# ──────────────────────────────────────────────
# 전체 기간에서 IS/OOS 서브세트 메트릭 재계산
# ──────────────────────────────────────────────

def compute_subset_metrics(result, period_start: str, period_end: str, label: str) -> dict:
    """전체 실행 결과에서 특정 기간만 필터링하여 메트릭을 재계산."""
    start_ms = _date_to_ms(period_start)
    end_ms = _date_to_ms(period_end) + 86400_000  # 하루 더 포함

    eq = result.equity_curve
    eq_sub = eq[(eq["ts"] >= start_ms) & (eq["ts"] <= end_ms)].copy()

    tl = result.trade_log
    if len(tl) > 0:
        tl_sub = tl[(tl["entry_ts"] >= start_ms) & (tl["entry_ts"] <= end_ms)].copy()
    else:
        tl_sub = tl.copy()

    if len(eq_sub) < 2:
        print(f"  [{label}] 해당 기간 equity 데이터 부족: {len(eq_sub)}행")
        return {}

    m = compute_metrics(eq_sub, tl_sub)
    return {
        "기간": label,
        "시작": period_start,
        "종료": period_end,
        "Sharpe": m.sharpe,
        "MDD(%)": round(m.max_drawdown * 100, 1),
        "총수익률(%)": round(m.total_return * 100, 1),
        "거래수": m.n_trades,
        "승률(%)": round(m.win_rate * 100, 1),
        "평균수익률(%)": round(m.avg_trade_return * 100, 2),
        "Calmar": m.calmar,
    }


# ──────────────────────────────────────────────
# 마크다운 보고서 생성
# ──────────────────────────────────────────────

def _fmt_val(v) -> str:
    """DataFrame 셀 값을 표시용 문자열로 변환. float 정수 값은 int로 표시."""
    if isinstance(v, float) and v == int(v) and not np.isnan(v):
        return str(int(v))
    return str(v)


def _df_to_md(df: pd.DataFrame) -> str:
    if len(df) == 0:
        return "(데이터 없음)\n"
    lines = []
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "| " + " | ".join("---" for _ in df.columns) + " |"
    lines.append(header)
    lines.append(sep)
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(_fmt_val(v) for v in row.values) + " |")
    return "\n".join(lines) + "\n"


def build_report(
    is_metrics: dict,
    oos_metrics: dict,
    full_metrics: dict,
    regime_is: pd.DataFrame,
    regime_oos: pd.DataFrame,
    yearly_is: pd.DataFrame,
    yearly_oos: pd.DataFrame,
    trade_summary: dict,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    lines.append(f"# Composite DC20 9년 백테스트 결과\n")
    lines.append(f"생성일시: {now}  ")
    lines.append(f"전략: Composite DC20 (dc_period=20, ATR 14, 적응형 트레일링 2.0~4.0, RSI(10)>50 OR 거래량 20일평균×1.5)\n")

    # 기간 정의
    lines.append("## 1. 기간 정의\n")
    lines.append("| 구분 | 기간 | 목적 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 워밍업 | {WARMUP_START} ~ {WARMUP_END} | 200EMA 등 지표 계산 준비 |")
    lines.append(f"| IS (인샘플) | {IS_START} ~ {IS_END} | 전략 파라미터 최적화 기준 |")
    lines.append(f"| OOS (아웃오브샘플) | {OOS_START} ~ {OOS_END} | 실제 검증 구간 |")
    lines.append("")

    # IS/OOS/전체 메트릭
    lines.append("## 2. IS / OOS 종합 메트릭\n")
    metric_df = pd.DataFrame([is_metrics, oos_metrics, full_metrics])
    lines.append(_df_to_md(metric_df))

    # 레짐별 성과 (IS)
    lines.append("## 3. 레짐별 성과 분리\n")
    lines.append("### 3-1. IS 기간 (2018-06 ~ 2023-12)\n")
    lines.append(_df_to_md(regime_is))

    lines.append("### 3-2. OOS 기간 (2024-01 ~ 2026-04)\n")
    lines.append(_df_to_md(regime_oos))

    lines.append("**레짐 정의:**")
    lines.append("- BULL: EMA200 위, F&G 25~74, 기울기 양수")
    lines.append("- SIDEWAYS: EMA200 위, F&G 25~74, 기울기 -1~1%")
    lines.append("- BEAR: EMA200 아래 or F&G < 25, 기울기 음수")
    lines.append("- CRISIS: F&G < 20 (극공포)")
    lines.append("- EUPHORIA: F&G >= 75 (과열)")
    lines.append("")

    # 연도별 성과
    lines.append("## 4. 연도별 성과\n")
    lines.append("### 4-1. IS 기간 연도별\n")
    lines.append(_df_to_md(yearly_is))
    lines.append("### 4-2. OOS 기간 연도별\n")
    lines.append(_df_to_md(yearly_oos))

    # 거래 로그 요약
    lines.append("## 5. 거래 로그 요약\n")
    lines.append(f"- 전체 거래수: {trade_summary.get('total', 0)}건")
    lines.append(f"- IS 거래수: {trade_summary.get('is_count', 0)}건")
    lines.append(f"- OOS 거래수: {trade_summary.get('oos_count', 0)}건")
    lines.append(f"- 전체 승률: {trade_summary.get('win_rate', 0):.1f}%")
    lines.append(f"- 최대 단일 이익: {trade_summary.get('max_win', 0):.2f}%")
    lines.append(f"- 최대 단일 손실: {trade_summary.get('max_loss', 0):.2f}%")
    lines.append(f"- 평균 보유 기간: {trade_summary.get('avg_hold_days', 0):.1f}일")
    lines.append("")

    # 전략 파라미터
    lines.append("## 6. 전략 파라미터\n")
    lines.append("```")
    for k, v in STRATEGY_PARAMS.items():
        lines.append(f"  {k}: {v}")
    lines.append("```")
    lines.append("")

    lines.append("---")
    lines.append("*자동 생성: scripts/backtest_composite_9yr.py*")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Composite DC20 전략 9년 백테스트")
    print("=" * 60)

    # 1. 데이터 로드
    print("\n[1] 데이터 로드 중...")
    # 전체 기간 (워밍업 포함)
    df_full = load_ohlcv(WARMUP_START, OOS_END)
    regime_df = load_regime()
    print(f"  전체 OHLCV: {len(df_full)}봉 ({df_full['date'].iloc[0]} ~ {df_full['date'].iloc[-1]})")
    print(f"  레짐 태그: {len(regime_df)}행")

    # 2. 전체 기간 백테스트 (워밍업 포함하여 지표 안정화)
    print("\n[2] 전체 기간 백테스트 실행 중...")
    result_full, tl_full = run_backtest(df_full, "전체(워밍업 포함)")

    # 3. IS/OOS/전체 메트릭 산출
    print("\n[3] IS / OOS 메트릭 산출 중...")
    is_metrics = compute_subset_metrics(result_full, IS_START, IS_END, "IS (2018-06~2023-12)")
    oos_metrics = compute_subset_metrics(result_full, OOS_START, OOS_END, "OOS (2024-01~2026-04)")
    full_metrics = compute_subset_metrics(result_full, IS_START, OOS_END, "전체 (2018-06~2026-04)")

    print(f"  IS  → Sharpe: {is_metrics.get('Sharpe', 'N/A')}, MDD: {is_metrics.get('MDD(%)', 'N/A')}%, 거래수: {is_metrics.get('거래수', 'N/A')}")
    print(f"  OOS → Sharpe: {oos_metrics.get('Sharpe', 'N/A')}, MDD: {oos_metrics.get('MDD(%)', 'N/A')}%, 거래수: {oos_metrics.get('거래수', 'N/A')}")

    # 4. 레짐별 성과 분리
    print("\n[4] 레짐별 성과 분리 중...")
    # IS 거래 필터
    is_start_ms = _date_to_ms(IS_START)
    is_end_ms = _date_to_ms(IS_END) + 86400_000
    oos_start_ms = _date_to_ms(OOS_START)
    oos_end_ms = _date_to_ms(OOS_END) + 86400_000

    if len(tl_full) > 0:
        tl_is = tl_full[(tl_full["entry_ts"] >= is_start_ms) & (tl_full["entry_ts"] <= is_end_ms)]
        tl_oos = tl_full[(tl_full["entry_ts"] >= oos_start_ms) & (tl_full["entry_ts"] <= oos_end_ms)]
    else:
        tl_is = tl_full
        tl_oos = tl_full

    regime_is_df = regime_analysis(tl_is, regime_df)
    regime_oos_df = regime_analysis(tl_oos, regime_df)
    print(f"  IS 거래 {len(tl_is)}건 레짐 분리 완료")
    print(f"  OOS 거래 {len(tl_oos)}건 레짐 분리 완료")

    # 5. 연도별 성과
    print("\n[5] 연도별 성과 산출 중...")
    eq = result_full.equity_curve
    is_end_ms_strict = _date_to_ms(IS_END) + 86400_000 - 1  # IS_END 하루 끝
    eq_is = eq[(eq["ts"] >= is_start_ms) & (eq["ts"] <= is_end_ms_strict)]
    eq_oos = eq[(eq["ts"] >= oos_start_ms) & (eq["ts"] <= oos_end_ms)]
    oh_is = df_full[(df_full["date"] >= IS_START) & (df_full["date"] <= IS_END)]
    oh_oos = df_full[(df_full["date"] >= OOS_START) & (df_full["date"] <= OOS_END)]

    yearly_is_df = yearly_analysis(tl_is, eq_is, oh_is)
    yearly_oos_df = yearly_analysis(tl_oos, eq_oos, oh_oos)

    # 6. 거래 요약
    print("\n[6] 거래 로그 요약 산출 중...")
    if len(tl_full) > 0:
        hold_days = (tl_full["exit_ts"] - tl_full["entry_ts"]) / (1000 * 86400)
        trade_summary = {
            "total": len(tl_full),
            "is_count": len(tl_is),
            "oos_count": len(tl_oos),
            "win_rate": (tl_full["return_pct"] > 0).mean() * 100,
            "max_win": tl_full["return_pct"].max() * 100,
            "max_loss": tl_full["return_pct"].min() * 100,
            "avg_hold_days": hold_days.mean(),
        }
    else:
        trade_summary = {"total": 0, "is_count": 0, "oos_count": 0, "win_rate": 0,
                         "max_win": 0, "max_loss": 0, "avg_hold_days": 0}

    # 7. 보고서 생성
    print("\n[7] 보고서 생성 중...")
    report_text = build_report(
        is_metrics, oos_metrics, full_metrics,
        regime_is_df, regime_oos_df,
        yearly_is_df, yearly_oos_df,
        trade_summary,
    )

    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report_text, encoding="utf-8")
    print(f"  보고서 저장: {OUTPUT_MD}")

    # 콘솔 요약 출력
    print("\n" + "=" * 60)
    print("백테스트 완료 요약")
    print("=" * 60)
    print(f"IS  Sharpe: {is_metrics.get('Sharpe', 'N/A'):>8} | MDD: {is_metrics.get('MDD(%)', 'N/A'):>7}% | 수익률: {is_metrics.get('총수익률(%)', 'N/A'):>8}%")
    print(f"OOS Sharpe: {oos_metrics.get('Sharpe', 'N/A'):>8} | MDD: {oos_metrics.get('MDD(%)', 'N/A'):>7}% | 수익률: {oos_metrics.get('총수익률(%)', 'N/A'):>8}%")
    print(f"전체 거래수: {trade_summary['total']}건 | 승률: {trade_summary['win_rate']:.1f}%")
    print(f"산출물: {OUTPUT_MD}")

    if len(regime_is_df) > 0:
        print("\n레짐별 IS 성과 (거래수 기준):")
        for _, row in regime_is_df.iterrows():
            print(f"  {row['레짐']:12s}: {row['거래수']:3d}건, 승률 {row['승률(%)']:5.1f}%, 평균 {row['평균수익률(%)']:+6.2f}%")


if __name__ == "__main__":
    main()
