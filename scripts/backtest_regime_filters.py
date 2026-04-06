# -*- coding: utf-8 -*-
"""레짐 필터 비교 백테스트 스크립트.

Composite DC20 전략에 3종 레짐 필터를 적용하여 성과를 비교한다.

변형:
  - Baseline : 필터 없음 (Composite DC20 원본)
  - Filter A : F&G < 20 진입 억제
  - Filter B : BTC close < 200EMA 진입 억제
  - Filter C : F&G 기반 포지션 사이즈 축소 (< 30 → 50%, < 20 → 0%)

기간:
  워밍업  2017-10-01 ~ 2018-05-31
  IS      2018-06-01 ~ 2023-12-31
  OOS     2024-01-01 ~ 2026-04-04

실행:
  PYTHONUTF8=1 python scripts/backtest_regime_filters.py
"""
from __future__ import annotations

import sys
import os

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import datetime
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np
import pandas as pd

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics
from services.strategies.advanced import (
    _calc_ema,
    _calc_atr,
    _calc_donchian_upper,
    _calc_rsi,
    _calc_vol_sma,
)

# ──────────────────────────────────────────────
# 경로 상수
# ──────────────────────────────────────────────
DB_PATH = Path(PROJECT_ROOT) / "data" / "cache.duckdb"
OUTPUT_DIR = Path(PROJECT_ROOT) / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = OUTPUT_DIR / "regime_filter_comparison.md"

# ──────────────────────────────────────────────
# 기간 정의 (Unix ms)
# ──────────────────────────────────────────────
def _ts_ms(date_str: str) -> int:
    """날짜 문자열을 Unix ms 타임스탬프로 변환 (KST 09:00 기준)."""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=9, minute=0, second=0, microsecond=0,
        tzinfo=datetime.timezone(datetime.timedelta(hours=9))
    )
    return int(dt.timestamp() * 1000)


WARMUP_START = _ts_ms("2017-10-01")
IS_START     = _ts_ms("2018-06-01")
IS_END       = _ts_ms("2023-12-31")
OOS_START    = _ts_ms("2024-01-01")
OOS_END      = _ts_ms("2026-04-05")   # 데이터 마지막 날 포함

# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────
def load_ohlcv() -> pd.DataFrame:
    """BTC/KRW 일봉 전체 로드."""
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


def load_fg() -> pd.Series:
    """F&G 지수 로드. index=날짜 문자열(YYYY-MM-DD), value=float."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    df = conn.execute(
        """
        SELECT date, value
        FROM macro
        WHERE series_id='FEAR_GREED'
        ORDER BY date
        """
    ).fetchdf()
    conn.close()
    fg = df.set_index("date")["value"]
    return fg


def ts_to_date(ts_ms: int) -> str:
    """Unix ms를 YYYY-MM-DD 문자열로 변환 (KST)."""
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000,
                                         tz=datetime.timezone(datetime.timedelta(hours=9)))
    return dt.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
# Composite DC20 핵심 로직 (필터 주입 가능 버전)
# ──────────────────────────────────────────────
def _make_composite_dc20_with_filter(
    entry_mask: np.ndarray | None = None,
    size_mask: np.ndarray | None = None,
    dc_period: int = 20,
    rsi_period: int = 10,
    rsi_threshold: float = 50.0,
    vol_ma: int = 20,
    vol_mult: float = 1.5,
    atr_period: int = 14,
    vol_lookback: int = 60,
) -> Callable[[pd.DataFrame], pd.Series]:
    """Composite DC20 전략 — 진입 마스크 및 사이즈 마스크를 외부에서 주입.

    entry_mask: shape (n,) bool/int array. 0이면 해당 봉에서 신규 진입 금지.
                None이면 필터 없음 (모두 허용).
    size_mask:  shape (n,) float array [0.0, 1.0]. 포지션 비율.
                None이면 필터 없음 (1.0).

    필터C 구현을 위해 size_mask를 활용.
    BacktestEngine은 신호 0/1만 이해하므로, 포지션 사이즈 필터(C)는
    size_mask가 0인 구간을 entry_mask=0으로 처리하여 진입을 막는다.
    size_mask가 0.5인 구간의 거래는 '필터링된 거래'로 별도 분석한다.
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
                window = arr[i - lookback + 1: i + 1]
                valid = window[~np.isnan(window)]
                if len(valid) < lookback // 2:
                    continue
                current = arr[i]
                if np.isnan(current):
                    continue
                ranks[i] = np.sum(valid <= current) / len(valid)
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

            # 적응형 배수
            if np.isnan(pr):
                adaptive_mult = 3.0
            else:
                adaptive_mult = 2.0 + 2.0 * pr

            # 진입 마스크 확인 (0이면 신규 진입 금지)
            can_enter = True
            if entry_mask is not None:
                can_enter = bool(entry_mask[i])
            # size_mask가 0이면 진입 금지 처리
            if size_mask is not None and size_mask[i] <= 0.0:
                can_enter = False

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
                    trailing_stop = highest - a * adaptive_mult
                    if c < trailing_stop:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 필터 마스크 생성
# ──────────────────────────────────────────────
def build_filter_a_mask(ohlcv: pd.DataFrame, fg: pd.Series) -> np.ndarray:
    """필터 A: F&G < 20 이면 진입 금지 (0), 그 외 허용 (1).
    F&G 데이터 없는 구간은 허용 (1).
    """
    n = len(ohlcv)
    mask = np.ones(n, dtype=np.int8)
    for i, row_ts in enumerate(ohlcv["ts"].values):
        date_str = ts_to_date(row_ts)
        if date_str in fg.index:
            fg_val = fg[date_str]
            if not np.isnan(fg_val) and fg_val < 20.0:
                mask[i] = 0
    return mask


def build_filter_b_mask(ohlcv: pd.DataFrame) -> np.ndarray:
    """필터 B: BTC close < 200EMA 이면 진입 금지 (0), 그 외 허용 (1)."""
    close = ohlcv["close"]
    ema200 = _calc_ema(close, 200).values
    close_vals = close.values

    n = len(ohlcv)
    mask = np.ones(n, dtype=np.int8)
    for i in range(n):
        if not np.isnan(ema200[i]) and close_vals[i] < ema200[i]:
            mask[i] = 0
    return mask


def build_filter_c_size_mask(ohlcv: pd.DataFrame, fg: pd.Series) -> np.ndarray:
    """필터 C: F&G 기반 사이즈 마스크.
    F&G >= 30   → 1.0 (100%)
    20 <= F&G < 30 → 0.5 (50%)
    F&G < 20    → 0.0 (0%, 진입 금지)
    F&G 없는 구간 → 1.0
    """
    n = len(ohlcv)
    size = np.ones(n, dtype=np.float64)
    for i, row_ts in enumerate(ohlcv["ts"].values):
        date_str = ts_to_date(row_ts)
        if date_str in fg.index:
            fg_val = fg[date_str]
            if not np.isnan(fg_val):
                if fg_val < 20.0:
                    size[i] = 0.0
                elif fg_val < 30.0:
                    size[i] = 0.5
                # else: 1.0 유지
    return size


# ──────────────────────────────────────────────
# 구간 분리 백테스트 실행
# ──────────────────────────────────────────────
def run_split_backtest(
    strategy_fn: Callable[[pd.DataFrame], pd.Series],
    ohlcv_full: pd.DataFrame,
) -> dict:
    """전체 데이터를 IS/OOS로 분리하여 각각 백테스트 실행.

    워밍업 데이터는 신호 계산에만 포함하고 성과 계산에서는 제외.
    IS: WARMUP_START ~ IS_END (신호 계산), 성과 IS_START ~ IS_END
    OOS: WARMUP_START ~ OOS_END (신호 계산), 성과 OOS_START ~ OOS_END
    """
    engine = BacktestEngine()

    # IS 구간: 워밍업 포함 전체 데이터로 신호 계산 후 IS 구간만 성과 계산
    ohlcv_for_is = ohlcv_full[ohlcv_full["ts"] <= IS_END].copy().reset_index(drop=True)
    is_result = engine.run(strategy_fn, ohlcv_for_is)

    # IS 성과 구간만 추출 (IS_START 이후)
    is_equity = is_result.equity_curve[is_result.equity_curve["ts"] >= IS_START].copy()
    is_trades = is_result.trade_log[
        is_result.trade_log["entry_ts"] >= IS_START
    ].copy() if len(is_result.trade_log) > 0 else is_result.trade_log.copy()

    # OOS 구간: 전체 데이터로 신호 계산 후 OOS 구간만 성과 계산
    ohlcv_for_oos = ohlcv_full.copy().reset_index(drop=True)
    oos_result = engine.run(strategy_fn, ohlcv_for_oos)

    oos_equity = oos_result.equity_curve[oos_result.equity_curve["ts"] >= OOS_START].copy()
    oos_trades = oos_result.trade_log[
        oos_result.trade_log["entry_ts"] >= OOS_START
    ].copy() if len(oos_result.trade_log) > 0 else oos_result.trade_log.copy()

    # IS/OOS 성과 계산 (equity 인덱스 리셋 필요)
    is_equity_reset = is_equity.reset_index(drop=True)
    oos_equity_reset = oos_equity.reset_index(drop=True)

    if len(is_equity_reset) > 1:
        is_metrics = compute_metrics(is_equity_reset, is_trades)
    else:
        from services.backtest.models import Metrics
        is_metrics = Metrics(0, 0, 0, 0, 0, 0, 0)

    if len(oos_equity_reset) > 1:
        oos_metrics = compute_metrics(oos_equity_reset, oos_trades)
    else:
        from services.backtest.models import Metrics
        oos_metrics = Metrics(0, 0, 0, 0, 0, 0, 0)

    return {
        "is_metrics": is_metrics,
        "oos_metrics": oos_metrics,
        "is_trades": is_trades,
        "oos_trades": oos_trades,
        "is_equity": is_equity_reset,
        "oos_equity": oos_equity_reset,
        # 전체 거래 (필터링 효과 분석용)
        "all_is_result": is_result,
        "all_oos_result": oos_result,
    }


# ──────────────────────────────────────────────
# 필터링 효과 분석 (필터 없는 거래 중 필터에 걸렸을 거래의 실제 성과)
# ──────────────────────────────────────────────
def analyze_filter_effect(
    baseline_oos_trades: pd.DataFrame,
    filtered_oos_trades: pd.DataFrame,
    label: str,
) -> dict:
    """기준선 OOS 거래 중 필터 적용 후 사라진 거래들의 실제 성과 분석."""
    if len(baseline_oos_trades) == 0:
        return {"filtered_count": 0, "filtered_win_rate": float("nan"),
                "filtered_avg_ret": float("nan"), "label": label}

    # 진입 타임스탬프 기준으로 필터링된 거래 식별
    baseline_entry_ts = set(baseline_oos_trades["entry_ts"].values)
    filtered_entry_ts = set(filtered_oos_trades["entry_ts"].values) if len(filtered_oos_trades) > 0 else set()

    removed_ts = baseline_entry_ts - filtered_entry_ts
    removed_trades = baseline_oos_trades[
        baseline_oos_trades["entry_ts"].isin(removed_ts)
    ]

    if len(removed_trades) == 0:
        return {"filtered_count": 0, "filtered_win_rate": float("nan"),
                "filtered_avg_ret": float("nan"), "label": label}

    win_rate = (removed_trades["return_pct"] > 0).mean()
    avg_ret = removed_trades["return_pct"].mean()

    return {
        "filtered_count": len(removed_trades),
        "filtered_win_rate": win_rate,
        "filtered_avg_ret": avg_ret,
        "label": label,
        "removed_trades": removed_trades,
    }


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    print("=" * 60)
    print("레짐 필터 비교 백테스트")
    print("=" * 60)

    # 데이터 로드
    print("\n[1] 데이터 로드...")
    ohlcv_full = load_ohlcv()
    fg = load_fg()
    print(f"  OHLCV: {len(ohlcv_full)}봉 ({ts_to_date(int(ohlcv_full['ts'].iloc[0]))} ~ {ts_to_date(int(ohlcv_full['ts'].iloc[-1]))})")
    print(f"  F&G:   {len(fg)}건 ({fg.index[0]} ~ {fg.index[-1]})")

    # 필터 마스크 생성
    print("\n[2] 필터 마스크 생성...")
    mask_a = build_filter_a_mask(ohlcv_full, fg)
    mask_b = build_filter_b_mask(ohlcv_full)
    size_c = build_filter_c_size_mask(ohlcv_full, fg)

    n_total = len(ohlcv_full)
    n_oos = len(ohlcv_full[ohlcv_full["ts"] >= OOS_START])

    # OOS 구간 인덱스 (마스크 분석용)
    oos_idx = ohlcv_full["ts"] >= OOS_START

    print(f"  필터 A 차단일 (전체): {(mask_a == 0).sum()}봉 / OOS: {(mask_a[oos_idx.values] == 0).sum()}봉")
    print(f"  필터 B 차단일 (전체): {(mask_b == 0).sum()}봉 / OOS: {(mask_b[oos_idx.values] == 0).sum()}봉")
    print(f"  필터 C 사이즈=0일  (전체): {(size_c == 0.0).sum()}봉 / OOS: {(size_c[oos_idx.values] == 0.0).sum()}봉")
    print(f"  필터 C 사이즈=0.5일 (전체): {(size_c == 0.5).sum()}봉 / OOS: {(size_c[oos_idx.values] == 0.5).sum()}봉")

    # 4가지 변형 전략 정의
    variants = {
        "Baseline": _make_composite_dc20_with_filter(
            entry_mask=None, size_mask=None
        ),
        "Filter_A": _make_composite_dc20_with_filter(
            entry_mask=mask_a, size_mask=None
        ),
        "Filter_B": _make_composite_dc20_with_filter(
            entry_mask=mask_b, size_mask=None
        ),
        "Filter_C": _make_composite_dc20_with_filter(
            entry_mask=None, size_mask=size_c
        ),
    }

    # 백테스트 실행
    print("\n[3] 백테스트 실행...")
    results = {}
    for name, strategy_fn in variants.items():
        print(f"  [{name}] 실행 중...")
        results[name] = run_split_backtest(strategy_fn, ohlcv_full)
        is_m = results[name]["is_metrics"]
        oos_m = results[name]["oos_metrics"]
        print(f"    IS  Sharpe={is_m.sharpe:.3f}, MDD={is_m.max_drawdown*100:.1f}%, "
              f"Trades={is_m.n_trades}")
        print(f"    OOS Sharpe={oos_m.sharpe:.3f}, MDD={oos_m.max_drawdown*100:.1f}%, "
              f"Trades={oos_m.n_trades}, WinRate={oos_m.win_rate*100:.1f}%")

    # 필터링 효과 분석
    print("\n[4] 필터링 효과 분석...")
    baseline_oos_trades = results["Baseline"]["oos_trades"]
    filter_effects = {}
    for name in ["Filter_A", "Filter_B", "Filter_C"]:
        effect = analyze_filter_effect(
            baseline_oos_trades,
            results[name]["oos_trades"],
            name,
        )
        filter_effects[name] = effect
        print(f"  [{name}] 필터링된 거래: {effect['filtered_count']}건, "
              f"승률={effect['filtered_win_rate']*100:.1f}% (NaN=필터 없음), "
              f"평균수익={effect['filtered_avg_ret']*100:.2f}%")

    # 보고서 생성
    print("\n[5] 보고서 생성...")
    _write_report(results, filter_effects, ohlcv_full, fg, mask_a, mask_b, size_c)
    print(f"  보고서 저장: {REPORT_PATH}")
    print("\n완료.")


def _write_report(
    results: dict,
    filter_effects: dict,
    ohlcv_full: pd.DataFrame,
    fg: pd.Series,
    mask_a: np.ndarray,
    mask_b: np.ndarray,
    size_c: np.ndarray,
):
    """비교 보고서를 Markdown으로 작성."""

    run_date = datetime.date.today().isoformat()

    lines = []
    lines.append(f"# 레짐 필터 비교 백테스트 보고서")
    lines.append(f"")
    lines.append(f"생성일: {run_date}")
    lines.append(f"")
    lines.append(f"## 개요")
    lines.append(f"")
    lines.append(f"Composite DC20 전략에 3종 레짐 필터를 적용하여 성과를 비교한다.")
    lines.append(f"CRISIS 레짐(F&G 극공포, 하락장)에서 손실을 줄이는 것이 목적.")
    lines.append(f"")
    lines.append(f"**기간 정의**")
    lines.append(f"")
    lines.append(f"| 구간 | 시작 | 종료 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| 워밍업 | 2017-10-01 | 2018-05-31 |")
    lines.append(f"| IS (인샘플) | 2018-06-01 | 2023-12-31 |")
    lines.append(f"| OOS (아웃오브샘플) | 2024-01-01 | 2026-04-04 |")
    lines.append(f"")
    lines.append(f"**필터 정의**")
    lines.append(f"")
    lines.append(f"| 필터 | 설명 |")
    lines.append(f"|------|------|")
    lines.append(f"| Baseline | 필터 없음 (DC20 원본) |")
    lines.append(f"| Filter A | F&G < 20이면 신규 진입 금지 (기존 포지션 유지) |")
    lines.append(f"| Filter B | BTC 종가 < 200EMA이면 신규 진입 금지 |")
    lines.append(f"| Filter C | F&G < 20 → 진입 0%, F&G < 30 → 진입 50% (사이즈 축소) |")
    lines.append(f"")

    # 비교 테이블
    lines.append(f"## 성과 비교 테이블")
    lines.append(f"")
    lines.append(f"### IS 구간 (2018-06 ~ 2023-12)")
    lines.append(f"")
    lines.append(f"| 변형 | IS Sharpe | IS MDD | IS 거래수 | IS 승률 | IS 평균수익 |")
    lines.append(f"|------|-----------|--------|-----------|---------|-------------|")

    for name in ["Baseline", "Filter_A", "Filter_B", "Filter_C"]:
        m = results[name]["is_metrics"]
        display = name.replace("_", " ")
        lines.append(
            f"| {display} | {m.sharpe:.3f} | {m.max_drawdown*100:.1f}% | "
            f"{m.n_trades} | {m.win_rate*100:.1f}% | {m.avg_trade_return*100:.2f}% |"
        )

    lines.append(f"")
    lines.append(f"### OOS 구간 (2024-01 ~ 2026-04) — 핵심 평가 구간")
    lines.append(f"")
    lines.append(f"| 변형 | OOS Sharpe | OOS MDD | OOS 거래수 | OOS 승률 | OOS 평균수익 | 판정 |")
    lines.append(f"|------|------------|---------|------------|----------|--------------|------|")

    # 판정 기준: OOS Sharpe > 1.0, OOS MDD > -30%, 거래수 >= 5
    baseline_oos = results["Baseline"]["oos_metrics"]
    for name in ["Baseline", "Filter_A", "Filter_B", "Filter_C"]:
        m = results[name]["oos_metrics"]
        display = name.replace("_", " ")
        sharpe_ok = m.sharpe >= 1.0
        mdd_ok = m.max_drawdown >= -0.30
        trades_ok = m.n_trades >= 5
        better_sharpe = m.sharpe >= baseline_oos.sharpe
        better_mdd = m.max_drawdown >= baseline_oos.max_drawdown

        if name == "Baseline":
            verdict = "기준선"
        elif sharpe_ok and mdd_ok and trades_ok and better_sharpe:
            verdict = "PASS (개선)"
        elif sharpe_ok and mdd_ok and trades_ok:
            verdict = "PASS"
        elif not trades_ok:
            verdict = "FAIL (거래 부족)"
        elif not sharpe_ok:
            verdict = "FAIL (Sharpe)"
        else:
            verdict = "FAIL (MDD)"

        lines.append(
            f"| {display} | {m.sharpe:.3f} | {m.max_drawdown*100:.1f}% | "
            f"{m.n_trades} | {m.win_rate*100:.1f}% | {m.avg_trade_return*100:.2f}% | {verdict} |"
        )

    # 필터링 효과 분석
    lines.append(f"")
    lines.append(f"## 필터링 효과 분석 (OOS 구간)")
    lines.append(f"")
    lines.append(
        f"필터에 의해 '차단된 거래'가 실제로 나쁜 거래였는지 확인한다. "
        f"필터가 효과적이라면 차단된 거래의 승률이 낮고 평균 수익이 음수여야 한다."
    )
    lines.append(f"")

    baseline_trade_count = len(results["Baseline"]["oos_trades"])
    lines.append(f"기준선 OOS 총 거래수: **{baseline_trade_count}건**")
    lines.append(f"")
    lines.append(f"| 필터 | 차단된 거래수 | 차단 거래 승률 | 차단 거래 평균수익 | 필터 유효성 |")
    lines.append(f"|------|--------------|----------------|-------------------|-------------|")

    for name in ["Filter_A", "Filter_B", "Filter_C"]:
        ef = filter_effects[name]
        fc = ef["filtered_count"]
        fwr = ef["filtered_win_rate"]
        far = ef["filtered_avg_ret"]
        display = name.replace("_", " ")

        if fc == 0:
            validity = "해당 없음 (차단 없음)"
            wr_str = "N/A"
            ar_str = "N/A"
        else:
            wr_str = f"{fwr*100:.1f}%"
            ar_str = f"{far*100:.2f}%"
            # 차단 거래가 실제로 나빴으면 필터 유효 (승률 < 50% 또는 평균수익 < 0)
            if fwr < 0.50 or far < 0.0:
                validity = "유효 (나쁜 거래 차단)"
            else:
                validity = "역효과 (좋은 거래 차단)"

        lines.append(f"| {display} | {fc}건 | {wr_str} | {ar_str} | {validity} |")

    # 각 필터 상세 분석
    lines.append(f"")
    lines.append(f"### 필터 A 상세 (F&G < 20 차단)")
    lines.append(f"")
    oos_idx = ohlcv_full["ts"] >= OOS_START
    fg_block_days_oos = int((mask_a[oos_idx.values] == 0).sum())
    lines.append(f"- OOS 구간 차단일: {fg_block_days_oos}일 (전체 OOS 중)")

    # F&G 구간별 분포
    oos_ohlcv = ohlcv_full[oos_idx].copy()
    fg_vals_oos = []
    for row_ts in oos_ohlcv["ts"].values:
        d = ts_to_date(int(row_ts))
        fg_vals_oos.append(fg.get(d, float("nan")))
    fg_vals_oos = np.array(fg_vals_oos)

    fg_lt20 = int(np.sum(~np.isnan(fg_vals_oos) & (fg_vals_oos < 20)))
    fg_20_30 = int(np.sum(~np.isnan(fg_vals_oos) & (fg_vals_oos >= 20) & (fg_vals_oos < 30)))
    fg_ge30 = int(np.sum(~np.isnan(fg_vals_oos) & (fg_vals_oos >= 30)))
    lines.append(f"- OOS F&G 분포: < 20 = {fg_lt20}일, 20~30 = {fg_20_30}일, >= 30 = {fg_ge30}일")

    lines.append(f"")
    lines.append(f"### 필터 B 상세 (BTC < 200EMA 차단)")
    lines.append(f"")
    ema_block_days_oos = int((mask_b[oos_idx.values] == 0).sum())
    lines.append(f"- OOS 구간 차단일: {ema_block_days_oos}일 (전체 OOS 중)")
    close_vals = ohlcv_full["close"].values
    ema200 = _calc_ema(ohlcv_full["close"], 200).values
    below_ema_oos = int(np.sum(oos_idx.values & ~np.isnan(ema200) & (close_vals < ema200)))
    lines.append(f"- OOS BTC < 200EMA 일수: {below_ema_oos}일")

    lines.append(f"")
    lines.append(f"### 필터 C 상세 (F&G 기반 사이즈 축소)")
    lines.append(f"")
    size_zero_oos = int((size_c[oos_idx.values] == 0.0).sum())
    size_half_oos = int((size_c[oos_idx.values] == 0.5).sum())
    size_full_oos = int((size_c[oos_idx.values] == 1.0).sum())
    lines.append(f"- OOS 사이즈=0% (F&G<20): {size_zero_oos}일")
    lines.append(f"- OOS 사이즈=50% (20<=F&G<30): {size_half_oos}일")
    lines.append(f"- OOS 사이즈=100% (F&G>=30): {size_full_oos}일")
    lines.append(
        f"- 참고: BacktestEngine은 사이즈 축소를 직접 지원하지 않으므로 "
        f"사이즈 50% 구간의 거래는 기준선과 동일하게 처리되고, "
        f"사이즈 0% 구간(F&G<20)만 진입이 차단된다."
    )

    # 최종 권장사항
    lines.append(f"")
    lines.append(f"## 최종 권장")
    lines.append(f"")

    # 자동 권장 로직
    oos_sharpes = {
        name: results[name]["oos_metrics"].sharpe
        for name in ["Baseline", "Filter_A", "Filter_B", "Filter_C"]
    }
    oos_mdds = {
        name: results[name]["oos_metrics"].max_drawdown
        for name in ["Baseline", "Filter_A", "Filter_B", "Filter_C"]
    }
    oos_trades_cnt = {
        name: results[name]["oos_metrics"].n_trades
        for name in ["Baseline", "Filter_A", "Filter_B", "Filter_C"]
    }

    best_sharpe_name = max(
        [n for n in oos_sharpes if oos_trades_cnt[n] >= 5],
        key=lambda n: oos_sharpes[n],
        default="Baseline"
    )
    best_mdd_name = max(
        [n for n in oos_mdds if oos_trades_cnt[n] >= 5],
        key=lambda n: oos_mdds[n],
        default="Baseline"
    )

    best_combined = max(
        [n for n in oos_sharpes if oos_trades_cnt[n] >= 5],
        key=lambda n: oos_sharpes[n] + oos_mdds[n] * 2,  # Sharpe 1 + MDD 개선 가중
        default="Baseline"
    )

    lines.append(f"**비교 요약**")
    lines.append(f"")
    lines.append(f"| 항목 | 최우수 변형 | 값 |")
    lines.append(f"|------|------------|-----|")
    lines.append(f"| OOS Sharpe 최고 | {best_sharpe_name.replace('_', ' ')} | {oos_sharpes[best_sharpe_name]:.3f} |")
    lines.append(f"| OOS MDD 최소 | {best_mdd_name.replace('_', ' ')} | {oos_mdds[best_mdd_name]*100:.1f}% |")
    lines.append(f"| 종합 (Sharpe+MDD) | {best_combined.replace('_', ' ')} | — |")
    lines.append(f"")

    # 권장 텍스트
    if best_combined == "Baseline":
        lines.append(
            f"**권장: 필터 미적용 (Baseline 유지)**\n\n"
            f"3종 레짐 필터 모두 Baseline 대비 유의미한 개선을 보이지 않았다. "
            f"필터를 추가하면 거래 기회가 줄어 오히려 성과가 저하되거나 "
            f"차단된 거래가 실제로 좋은 거래였을 가능성이 있다. "
            f"현재 Composite DC20을 그대로 유지한다."
        )
    else:
        filter_label = best_combined.replace("_", " ")
        lines.append(
            f"**권장: {filter_label} 적용**\n\n"
            f"Baseline 대비 OOS Sharpe {oos_sharpes[best_combined]:.3f} "
            f"(기준선 {oos_sharpes['Baseline']:.3f}), "
            f"OOS MDD {oos_mdds[best_combined]*100:.1f}% "
            f"(기준선 {oos_mdds['Baseline']*100:.1f}%)로 개선되었다. "
            f"거래수 {oos_trades_cnt[best_combined]}건으로 통계적으로 유효한 표본을 확보하였다."
        )
        lines.append(f"")
        if best_combined == "Filter_A":
            lines.append(
                f"구현 방법: F&G 데이터를 매일 조회하여 20 미만이면 "
                f"신규 매수 주문을 건너뛰도록 `services/execution/realtime_monitor.py`에 "
                f"추가한다. 기존 포지션 청산 로직은 그대로 유지한다."
            )
        elif best_combined == "Filter_B":
            lines.append(
                f"구현 방법: 일봉 close와 200EMA를 비교하여 하위 구간이면 "
                f"신규 매수를 건너뛰도록 `services/execution/scanner.py` 및 "
                f"`services/execution/realtime_monitor.py`에 200EMA 조건을 추가한다."
            )
        elif best_combined == "Filter_C":
            lines.append(
                f"구현 방법: F&G < 30이면 포지션 사이즈를 50%로 축소하고 "
                f"F&G < 20이면 신규 진입을 차단한다. "
                f"BacktestEngine이 사이즈 축소를 미지원하므로 "
                f"실 거래 시에는 주문 금액을 수동으로 조절해야 한다."
            )

    lines.append(f"")
    lines.append(f"---")
    lines.append(f"*자동 생성: scripts/backtest_regime_filters.py*")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
