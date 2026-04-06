# -*- coding: utf-8 -*-
"""신규 전략 후보 IS 스크리닝 백테스트
전략: BB 하단 평균회귀, P/MA200 저점매수, 절대 모멘텀 현금화

실행: PYTHONUTF8=1 python scripts/backtest_new_strategies_screening.py
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import duckdb

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics

# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────

def load_ohlcv() -> pd.DataFrame:
    db_path = ROOT / "data" / "cache.duckdb"
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute(
        "SELECT ts, open, high, low, close, volume "
        "FROM ohlcv WHERE symbol='BTC/KRW' AND timeframe='1d' ORDER BY ts"
    ).fetchdf()
    con.close()
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("Asia/Seoul").dt.date
    df["date"] = pd.to_datetime(df["date"])
    return df


# ──────────────────────────────────────────────
# 공통 지표 계산
# ──────────────────────────────────────────────

def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def _calc_bb(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    """볼린저밴드: (상단, 중단, 하단) 반환."""
    sma = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std(ddof=1)
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper, sma, lower


# ──────────────────────────────────────────────
# 전략 1: BB 하단 평균회귀
# ──────────────────────────────────────────────

def make_strategy_bb_mean_reversion(
    bb_period: int = 20,
    bb_std: float = 2.0,
    stop_loss: float = 0.05,
    use_ema_filter: bool = False,
    ema_period: int = 200,
):
    """BB 하단 평균회귀 전략.

    진입: 전날 close < BB_lower AND 오늘 close > BB_lower (반등 확인)
    청산: close >= BB_middle(SMA20) 또는 손절 -5%
    레짐 필터(선택): close > EMA(200)
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        _, bb_mid, bb_lower = _calc_bb(close, bb_period, bb_std)
        ema200 = _calc_ema(close, ema_period) if use_ema_filter else None

        close_v = close.values
        bb_mid_v = bb_mid.values
        bb_lower_v = bb_lower.values
        ema_v = ema200.values if use_ema_filter else None

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        entry_price = 0.0

        for i in range(n):
            c = close_v[i]
            mid = bb_mid_v[i]
            lower = bb_lower_v[i]

            # nan 체크
            if np.isnan(lower) or np.isnan(mid):
                signal[i] = 1 if in_position else 0
                continue

            if not in_position:
                # 전날 close < BB_lower (i >= 1 체크)
                if i < 1:
                    signal[i] = 0
                    continue
                prev_c = close_v[i - 1]
                prev_lower = bb_lower_v[i - 1]

                if np.isnan(prev_lower):
                    signal[i] = 0
                    continue

                # 레짐 필터
                if use_ema_filter and ema_v is not None:
                    if np.isnan(ema_v[i]) or c <= ema_v[i]:
                        signal[i] = 0
                        continue

                # 진입 조건: 전날 BB 하단 이탈 + 오늘 BB 하단 위로 반등
                if prev_c < prev_lower and c > lower:
                    in_position = True
                    entry_price = c
                    signal[i] = 1
            else:
                # 청산 조건 1: 목표가 (BB 중단 도달)
                if c >= mid:
                    in_position = False
                    entry_price = 0.0
                    signal[i] = 0
                # 청산 조건 2: 손절 -5%
                elif entry_price > 0 and (c / entry_price - 1) <= -stop_loss:
                    in_position = False
                    entry_price = 0.0
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 2: P/MA200 저점 분할매수 (단순화: 단일 진입)
# ──────────────────────────────────────────────

def make_strategy_pma200_dip(
    entry_ratio: float = 0.85,   # close / EMA200 < 0.85 → 진입
    exit_ratio: float = 1.10,    # close / EMA200 > 1.10 → 청산
    stop_loss: float = 0.15,     # 손절 -15%
    ema_period: int = 200,
):
    """P/MA200 저점 분할매수 전략 (단일 진입으로 단순화).

    진입: close / EMA(200) < entry_ratio (0.85)
    청산: close / EMA(200) > exit_ratio (1.10) 또는 손절 -15%
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        ema200 = _calc_ema(close, ema_period)

        close_v = close.values
        ema_v = ema200.values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        entry_price = 0.0

        for i in range(n):
            c = close_v[i]
            e = ema_v[i]

            if np.isnan(e):
                signal[i] = 1 if in_position else 0
                continue

            ratio = c / e

            if not in_position:
                if ratio < entry_ratio:
                    in_position = True
                    entry_price = c
                    signal[i] = 1
            else:
                # 목표가 청산
                if ratio > exit_ratio:
                    in_position = False
                    entry_price = 0.0
                    signal[i] = 0
                # 손절
                elif entry_price > 0 and (c / entry_price - 1) <= -stop_loss:
                    in_position = False
                    entry_price = 0.0
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 3: 절대 모멘텀 현금화 (BTC 단일, 월별 리밸런싱)
# ──────────────────────────────────────────────

def make_strategy_abs_momentum(
    lookback_days: int = 30,
):
    """절대 모멘텀 현금화 전략.

    매월 말: return_30d > 0 → 다음달 보유(signal=1), <= 0 → 다음달 현금(signal=0)
    월별 신호 변경 — 월말에만 signal 재산정, 그 외는 유지
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        # 30일 수익률
        ret_30d = close.pct_change(periods=lookback_days)

        # 월말 여부 식별: ts를 날짜로 변환
        dates = pd.to_datetime(df["ts"], unit="ms", utc=True)
        # 다음 날이 다른 달이면 월말
        is_month_end = dates.dt.month != dates.dt.month.shift(-1)

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        current_sig = 0  # 초기: 현금

        for i in range(n):
            if np.isnan(ret_30d.iloc[i]):
                signal[i] = current_sig
                continue

            # 월말이면 다음달 신호 결정 (현재 봉 기준)
            if is_month_end.iloc[i]:
                current_sig = 1 if ret_30d.iloc[i] > 0 else 0

            signal[i] = current_sig

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 연도별 수익률 계산 (equity_curve 기반)
# ──────────────────────────────────────────────

def calc_yearly_returns(equity_curve: pd.DataFrame) -> dict:
    """equity_curve[ts, equity] → 연도별 수익률 dict."""
    eq = equity_curve.copy()
    eq["date"] = pd.to_datetime(eq["ts"], unit="ms", utc=True)
    eq["year"] = eq["date"].dt.year

    result = {}
    for year, grp in eq.groupby("year"):
        start_eq = grp["equity"].iloc[0]
        end_eq = grp["equity"].iloc[-1]
        result[int(year)] = round((end_eq / start_eq) - 1, 4)
    return result


# ──────────────────────────────────────────────
# 메인 백테스트 실행
# ──────────────────────────────────────────────

def run_backtest_period(
    strategy_fn,
    df: pd.DataFrame,
    start_date: str,
    end_date: str,
    warmup_start: str,
    label: str,
):
    """워밍업 포함 데이터로 백테스트 실행 후 지정 구간 메트릭 반환."""
    # 워밍업 포함 데이터 슬라이싱
    mask = (df["date"] >= pd.Timestamp(warmup_start)) & (df["date"] <= pd.Timestamp(end_date))
    df_slice = df[mask].copy().reset_index(drop=True)

    if len(df_slice) < 30:
        return None, None, None

    engine = BacktestEngine()
    result = engine.run(strategy_fn, df_slice)

    # 평가 구간만 추출 (IS 또는 OOS)
    eq = result.equity_curve.copy()
    eq["date"] = pd.to_datetime(eq["ts"], unit="ms", utc=True).dt.tz_localize(None)
    eq_eval = eq[(eq["date"] >= pd.Timestamp(start_date)) & (eq["date"] <= pd.Timestamp(end_date))]

    if len(eq_eval) < 2:
        return None, None, result

    # 평가 구간 trades 추출
    trades = result.trade_log.copy()
    if len(trades) > 0:
        trades["entry_date"] = pd.to_datetime(trades["entry_ts"], unit="ms", utc=True).dt.tz_localize(None)
        trades["exit_date"] = pd.to_datetime(trades["exit_ts"], unit="ms", utc=True).dt.tz_localize(None)
        trades_eval = trades[
            (trades["entry_date"] >= pd.Timestamp(start_date)) &
            (trades["exit_date"] <= pd.Timestamp(end_date))
        ]
    else:
        trades_eval = trades

    metrics = compute_metrics(eq_eval.reset_index(drop=True), trades_eval.reset_index(drop=True))
    yearly = calc_yearly_returns(eq_eval.reset_index(drop=True))

    return metrics, yearly, result


def main():
    print("=" * 60)
    print("신규 전략 후보 IS 스크리닝 백테스트")
    print("=" * 60)

    # 기간 설정
    WARMUP_START = "2017-10-01"
    IS_START = "2018-06-01"
    IS_END = "2023-12-31"
    OOS_START = "2024-01-01"
    OOS_END = "2026-04-05"

    IS_SHARPE_THRESHOLD = 0.5

    # 데이터 로드
    print("\n[1] 데이터 로드 중...")
    df = load_ohlcv()
    print(f"    총 {len(df)}개 봉 로드 완료 ({df['date'].min().date()} ~ {df['date'].max().date()})")

    # 전략 정의
    strategies = [
        {
            "name": "BB 하단 평균회귀",
            "code": "BB_MR",
            "fn": make_strategy_bb_mean_reversion(
                bb_period=20, bb_std=2.0, stop_loss=0.05, use_ema_filter=False
            ),
        },
        {
            "name": "P/MA200 저점매수",
            "code": "PMA200_DIP",
            "fn": make_strategy_pma200_dip(
                entry_ratio=0.85, exit_ratio=1.10, stop_loss=0.15, ema_period=200
            ),
        },
        {
            "name": "절대 모멘텀 현금화",
            "code": "ABS_MOM",
            "fn": make_strategy_abs_momentum(lookback_days=30),
        },
    ]

    results_table = []

    for strat in strategies:
        name = strat["name"]
        code = strat["code"]
        fn = strat["fn"]
        print(f"\n[2] {name} ({code}) 백테스트 실행...")

        # IS 백테스트
        is_metrics, is_yearly, is_result = run_backtest_period(
            fn, df,
            start_date=IS_START, end_date=IS_END,
            warmup_start=WARMUP_START,
            label="IS"
        )

        # OOS 백테스트 (IS와 동일 전략 함수, 다른 기간)
        oos_metrics, oos_yearly, oos_result = run_backtest_period(
            fn, df,
            start_date=OOS_START, end_date=OOS_END,
            warmup_start=WARMUP_START,
            label="OOS"
        )

        if is_metrics is None:
            print(f"    {name}: 데이터 부족으로 스킵")
            continue

        sharpe = is_metrics.sharpe
        pass_is = sharpe >= IS_SHARPE_THRESHOLD
        judgment = "OOS 검증 대상" if pass_is else f"탈락 (IS Sharpe {sharpe:.3f} < {IS_SHARPE_THRESHOLD})"

        print(f"    IS Sharpe: {sharpe:.4f} | IS MDD: {is_metrics.max_drawdown:.2%} | "
              f"IS 수익률: {is_metrics.total_return:.2%} | 거래수: {is_metrics.n_trades} | "
              f"승률: {is_metrics.win_rate:.2%}")
        print(f"    판정: {judgment}")

        results_table.append({
            "name": name,
            "code": code,
            "is_sharpe": sharpe,
            "is_mdd": is_metrics.max_drawdown,
            "is_return": is_metrics.total_return,
            "is_n_trades": is_metrics.n_trades,
            "is_win_rate": is_metrics.win_rate,
            "is_yearly": is_yearly or {},
            "oos_sharpe": oos_metrics.sharpe if oos_metrics else None,
            "oos_mdd": oos_metrics.max_drawdown if oos_metrics else None,
            "oos_return": oos_metrics.total_return if oos_metrics else None,
            "oos_n_trades": oos_metrics.n_trades if oos_metrics else None,
            "oos_yearly": oos_yearly or {},
            "pass_is": pass_is,
            "judgment": judgment,
        })

    # ──────────────────────────────────────────────
    # 보고서 생성
    # ──────────────────────────────────────────────
    print("\n[3] 보고서 생성 중...")

    output_path = ROOT / "output" / "new_strategy_screening.md"
    lines = []
    lines.append("# 신규 전략 후보 IS 스크리닝 백테스트 결과")
    lines.append("")
    lines.append(f"- 작성일: 2026-04-05")
    lines.append(f"- 워밍업: {WARMUP_START} ~ 2018-05-31")
    lines.append(f"- IS 기간: {IS_START} ~ {IS_END}")
    lines.append(f"- OOS 기간: {OOS_START} ~ {OOS_END}")
    lines.append(f"- 스크리닝 기준: IS Sharpe >= {IS_SHARPE_THRESHOLD} → OOS 검증 대상")
    lines.append("")

    # IS 요약 테이블
    lines.append("## IS 스크리닝 결과 요약")
    lines.append("")
    lines.append("| 전략 | IS Sharpe | IS MDD | IS 수익률 | IS 거래수 | IS 승률 | 판정 |")
    lines.append("|------|-----------|--------|-----------|-----------|---------|------|")

    for r in results_table:
        mdd_str = f"{r['is_mdd']:.2%}" if r['is_mdd'] is not None else "N/A"
        ret_str = f"{r['is_return']:.2%}" if r['is_return'] is not None else "N/A"
        win_str = f"{r['is_win_rate']:.2%}" if r['is_win_rate'] is not None else "N/A"
        sharpe_str = f"{r['is_sharpe']:.4f}"
        lines.append(
            f"| {r['name']} | {sharpe_str} | {mdd_str} | {ret_str} | "
            f"{r['is_n_trades']} | {win_str} | {r['judgment']} |"
        )

    lines.append("")

    # 연도별 수익률
    lines.append("## IS 기간 연도별 수익률")
    lines.append("")

    # IS 연도 범위
    is_years = list(range(2018, 2024))
    header = "| 전략 | " + " | ".join(str(y) for y in is_years) + " |"
    sep = "|------|" + "|".join(["------"] * len(is_years)) + "|"
    lines.append(header)
    lines.append(sep)
    for r in results_table:
        row_vals = []
        for y in is_years:
            val = r["is_yearly"].get(y)
            row_vals.append(f"{val:.2%}" if val is not None else "-")
        lines.append(f"| {r['name']} | " + " | ".join(row_vals) + " |")

    lines.append("")

    # OOS 참고 테이블
    lines.append("## OOS 참고 결과 (전략 검증용)")
    lines.append("")
    lines.append("| 전략 | OOS Sharpe | OOS MDD | OOS 수익률 | OOS 거래수 | IS 판정 |")
    lines.append("|------|------------|---------|------------|------------|---------|")
    for r in results_table:
        oos_sharpe_str = f"{r['oos_sharpe']:.4f}" if r['oos_sharpe'] is not None else "N/A"
        oos_mdd_str = f"{r['oos_mdd']:.2%}" if r['oos_mdd'] is not None else "N/A"
        oos_ret_str = f"{r['oos_return']:.2%}" if r['oos_return'] is not None else "N/A"
        oos_n_str = str(r['oos_n_trades']) if r['oos_n_trades'] is not None else "N/A"
        lines.append(
            f"| {r['name']} | {oos_sharpe_str} | {oos_mdd_str} | {oos_ret_str} | "
            f"{oos_n_str} | {r['judgment']} |"
        )

    lines.append("")

    # OOS 연도별 수익률
    lines.append("## OOS 기간 연도별 수익률")
    lines.append("")
    oos_years = [2024, 2025, 2026]
    header2 = "| 전략 | " + " | ".join(str(y) for y in oos_years) + " |"
    sep2 = "|------|" + "|".join(["------"] * len(oos_years)) + "|"
    lines.append(header2)
    lines.append(sep2)
    for r in results_table:
        row_vals = []
        for y in oos_years:
            val = r["oos_yearly"].get(y)
            row_vals.append(f"{val:.2%}" if val is not None else "-")
        lines.append(f"| {r['name']} | " + " | ".join(row_vals) + " |")

    lines.append("")

    # 전략별 상세 분석
    lines.append("## 전략별 상세 분석")
    lines.append("")

    for r in results_table:
        lines.append(f"### {r['name']} ({r['code']})")
        lines.append("")
        if r["pass_is"]:
            lines.append(f"**IS 통과 — OOS 검증 필요**")
            lines.append("")
            lines.append(f"- IS Sharpe {r['is_sharpe']:.4f} >= 기준 {IS_SHARPE_THRESHOLD}")
            lines.append(f"- IS MDD: {r['is_mdd']:.2%}")
            lines.append(f"- IS 총수익률: {r['is_return']:.2%}")
            lines.append(f"- IS 거래수: {r['is_n_trades']}회, 승률: {r['is_win_rate']:.2%}")
        else:
            lines.append(f"**IS 탈락**")
            lines.append("")
            lines.append(f"- 탈락 사유: IS Sharpe {r['is_sharpe']:.4f} < 기준 {IS_SHARPE_THRESHOLD}")
            lines.append(f"- IS MDD: {r['is_mdd']:.2%}")
            lines.append(f"- IS 총수익률: {r['is_return']:.2%}")
            lines.append(f"- IS 거래수: {r['is_n_trades']}회, 승률: {r['is_win_rate']:.2%}")
            lines.append(f"- OOS Sharpe (참고): {r['oos_sharpe']:.4f}" if r['oos_sharpe'] is not None else "- OOS: N/A")
        lines.append("")

    # 전략 구현 메모
    lines.append("## 전략 구현 메모")
    lines.append("")
    lines.append("### BB 하단 평균회귀")
    lines.append("- BB(20, 2.0) pandas rolling std(ddof=1) 직접 구현")
    lines.append("- 진입: 전봉 BB_lower 이탈 + 현봉 BB_lower 위 회복")
    lines.append("- 청산: close >= BB_middle(SMA20) 또는 -5% 손절")
    lines.append("- EMA(200) 필터 미적용 버전으로 실행 (더 많은 거래 기회 확보)")
    lines.append("")
    lines.append("### P/MA200 저점매수")
    lines.append("- EMA(200) 대비 가격 비율(ratio) 기반")
    lines.append("- 진입: ratio < 0.85 (EMA 대비 15% 이하)")
    lines.append("- 청산: ratio > 1.10 (EMA 대비 10% 초과) 또는 -15% 손절")
    lines.append("- 분할매수 단순화: 단일 진입으로 처리")
    lines.append("")
    lines.append("### 절대 모멘텀 현금화")
    lines.append("- 월말 봉에서 30일 수익률 계산 후 다음 달 신호 결정")
    lines.append("- return_30d > 0 → 보유(1), <= 0 → 현금(0)")
    lines.append("- 엔진 호환: 월별 신호 변경 방식으로 구현 (signal은 월말에만 갱신)")
    lines.append("")

    # 다음 단계
    lines.append("## 다음 단계")
    lines.append("")
    passed = [r for r in results_table if r["pass_is"]]
    failed = [r for r in results_table if not r["pass_is"]]

    if passed:
        lines.append("### OOS 검증 대상 전략")
        for r in passed:
            lines.append(f"- {r['name']}: IS Sharpe {r['is_sharpe']:.4f} (OOS Sharpe {r['oos_sharpe']:.4f})")
        lines.append("")
        lines.append("OOS 통과 기준: Sharpe >= 0.5, MDD <= -30%")
    else:
        lines.append("- 모든 전략이 IS 탈락 → 파라미터 조정 또는 신규 후보 탐색 필요")

    if failed:
        lines.append("")
        lines.append("### IS 탈락 전략")
        for r in failed:
            lines.append(f"- {r['name']}: IS Sharpe {r['is_sharpe']:.4f} (기준 미달)")

    report_text = "\n".join(lines)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"\n    보고서 저장 완료: {output_path}")

    # 콘솔 요약 출력
    print("\n" + "=" * 60)
    print("IS 스크리닝 결과 요약")
    print("=" * 60)
    print(f"{'전략':<22} {'IS Sharpe':>10} {'IS MDD':>8} {'IS 수익률':>10} {'거래수':>6} {'판정'}")
    print("-" * 70)
    for r in results_table:
        print(
            f"{r['name']:<22} {r['is_sharpe']:>10.4f} {r['is_mdd']:>8.2%} "
            f"{r['is_return']:>10.2%} {r['is_n_trades']:>6}  {r['judgment']}"
        )
    print("=" * 60)


if __name__ == "__main__":
    main()
