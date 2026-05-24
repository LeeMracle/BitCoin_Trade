# -*- coding: utf-8 -*-
"""REGIME 깊이 게이트 백테스트 — ADR 20260524-2 발의용.

기존 `backtest_regime_filters.py`는 BTC<EMA200 단순 boolean(필터 B)만 평가.
본 스크립트는 그 확장으로, **gap depth 임계** 5종 변형 백테스트한다.

변형:
  - Baseline      : 필터 없음 (DC20 원본)
  - Gap_OFF       : gap > -inf (필터 효과 0, sanity check)
  - Gap_-3pct     : gap < -3% 차단 (얕은 BEAR도 차단)
  - Gap_-5pct     : gap < -5% 차단
  - Gap_-7pct     : gap < -7% 차단 (권고 후보)
  - Gap_-10pct    : gap < -10% 차단
  - Gap_OLD       : gap < 0% 차단 (기존 ADR 20260516-2 Filter B, 비교용)

기간 (Phase 2 결과와 비교 가능):
  워밍업  2017-10-01 ~ 2018-05-31
  IS      2018-06-01 ~ 2023-12-31
  OOS     2024-01-01 ~ 2026-04-04
  하락장 별도 (BEAR_PERIOD): 2022-04-01 ~ 2022-12-31 (UST/Luna ~ FTX), 2024-08-01 ~ 2024-10-31

평가:
  Sharpe, MDD, n_trades, win_rate, avg_return, 차단 거래 가상 PnL

합격선 (ADR 20260524-1 §5.2):
  Sharpe ≥ 1.0, MDD < 20%, 5/3~5/16 패턴(승률 ≤ 30%) 미재현

실행:
  PYTHONUTF8=1 python scripts/backtest_regime_gap_thresholds.py
산출:
  output/regime_gap_threshold_comparison.md  (Markdown 비교 보고서)
  output/regime_gap_threshold_comparison.json (raw metrics)
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

# 프로젝트 루트
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics
from services.strategies.advanced import (
    _calc_atr,
    _calc_donchian_upper,
    _calc_ema,
    _calc_rsi,
    _calc_vol_sma,
)

# 경로
DB_PATH = Path(PROJECT_ROOT) / "data" / "cache.duckdb"
OUTPUT_DIR = Path(PROJECT_ROOT) / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = OUTPUT_DIR / "regime_gap_threshold_comparison.md"
JSON_PATH = OUTPUT_DIR / "regime_gap_threshold_comparison.json"


def _ts_ms(date_str: str) -> int:
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=9, minute=0, second=0, microsecond=0,
        tzinfo=datetime.timezone(datetime.timedelta(hours=9)),
    )
    return int(dt.timestamp() * 1000)


def ts_to_date(ts_ms: int) -> str:
    dt = datetime.datetime.fromtimestamp(
        ts_ms / 1000, tz=datetime.timezone(datetime.timedelta(hours=9))
    )
    return dt.strftime("%Y-%m-%d")


WARMUP_START = _ts_ms("2017-10-01")
IS_START = _ts_ms("2018-06-01")
IS_END = _ts_ms("2023-12-31")
OOS_START = _ts_ms("2024-01-01")
OOS_END = _ts_ms("2026-04-05")

# 하락장 별도 (BEAR_PERIOD)
BEAR_PERIODS = [
    ("2022_UST_FTX", _ts_ms("2022-04-01"), _ts_ms("2022-12-31")),
    ("2024_Q3_dip", _ts_ms("2024-08-01"), _ts_ms("2024-10-31")),
]

# 변형 정의
GAP_VARIANTS = [
    # (label, threshold_pct or None)
    # threshold_pct: gap < threshold면 차단. None=필터 없음(baseline)
    ("Baseline", None),
    ("Gap_OFF", -999.0),  # sanity: 효과 0
    ("Gap_-3pct", -3.0),
    ("Gap_-5pct", -5.0),
    ("Gap_-7pct", -7.0),
    ("Gap_-10pct", -10.0),
    ("Gap_OLD_below_ema", 0.0),  # 기존 ADR 20260516-2 Filter B (boolean BTC<EMA200)
]


def load_ohlcv() -> pd.DataFrame:
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


def build_gap_mask(ohlcv: pd.DataFrame, threshold_pct: float | None) -> np.ndarray:
    """gap = (close/EMA200 - 1) * 100 (단위 %). gap < threshold_pct면 mask=0.

    threshold_pct=None → 전부 1 (필터 없음)
    threshold_pct=-999 → 전부 1 (필터 없음과 동일, sanity check)
    threshold_pct=0 → 기존 ADR 20260516-2 Filter B 와 동치 (close < EMA200 차단)
    threshold_pct=-7 → close가 EMA200 대비 -7% 이상 빠진 깊은 BEAR만 차단
    """
    n = len(ohlcv)
    if threshold_pct is None:
        return np.ones(n, dtype=np.int8)

    ema200 = _calc_ema(ohlcv["close"], 200).values
    close = ohlcv["close"].values
    gap_pct = (close / ema200 - 1.0) * 100.0

    mask = np.ones(n, dtype=np.int8)
    for i in range(n):
        if not np.isnan(gap_pct[i]) and gap_pct[i] < threshold_pct:
            mask[i] = 0
    return mask


def _make_composite_dc20_with_filter(
    entry_mask: np.ndarray | None = None,
    dc_period: int = 20,
    rsi_period: int = 10,
    rsi_threshold: float = 50.0,
    vol_ma: int = 20,
    vol_mult: float = 1.5,
    atr_period: int = 14,
    vol_lookback: int = 60,
) -> Callable[[pd.DataFrame], pd.Series]:
    """Composite DC20 (백테스트 검증된 변형) + 진입 마스크.

    `backtest_regime_filters.py:_make_composite_dc20_with_filter` 와 동형 (size_mask 제거).
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

            if np.isnan(pr):
                adaptive_mult = 3.0
            else:
                adaptive_mult = 2.0 + 2.0 * pr

            can_enter = True
            if entry_mask is not None:
                can_enter = bool(entry_mask[i])

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


def run_split_backtest(
    strategy_fn: Callable[[pd.DataFrame], pd.Series],
    ohlcv_full: pd.DataFrame,
) -> dict:
    """IS/OOS/BEAR_PERIOD 별 백테스트.

    워밍업 데이터는 신호 계산에만 포함하고 성과 계산에서는 제외.
    """
    engine = BacktestEngine()

    # IS
    ohlcv_for_is = ohlcv_full[ohlcv_full["ts"] <= IS_END].copy().reset_index(drop=True)
    is_result = engine.run(strategy_fn, ohlcv_for_is)
    is_equity = is_result.equity_curve[is_result.equity_curve["ts"] >= IS_START].copy()
    is_trades = (
        is_result.trade_log[is_result.trade_log["entry_ts"] >= IS_START].copy()
        if len(is_result.trade_log) > 0
        else is_result.trade_log.copy()
    )

    # OOS
    ohlcv_for_oos = ohlcv_full.copy().reset_index(drop=True)
    oos_result = engine.run(strategy_fn, ohlcv_for_oos)
    oos_equity = oos_result.equity_curve[oos_result.equity_curve["ts"] >= OOS_START].copy()
    oos_trades = (
        oos_result.trade_log[oos_result.trade_log["entry_ts"] >= OOS_START].copy()
        if len(oos_result.trade_log) > 0
        else oos_result.trade_log.copy()
    )

    # BEAR 별도
    bear_metrics_by_period = {}
    for label, b_start, b_end in BEAR_PERIODS:
        # 신호는 전체로 계산 (oos_result에서 추출)
        # 다만 BEAR 구간이 IS 이전이면 is_result 사용 (2022는 IS 안)
        if b_end <= IS_END:
            src_result = is_result
        else:
            src_result = oos_result
        b_equity = src_result.equity_curve[
            (src_result.equity_curve["ts"] >= b_start)
            & (src_result.equity_curve["ts"] <= b_end)
        ].copy()
        b_trades = (
            src_result.trade_log[
                (src_result.trade_log["entry_ts"] >= b_start)
                & (src_result.trade_log["entry_ts"] <= b_end)
            ].copy()
            if len(src_result.trade_log) > 0
            else src_result.trade_log.copy()
        )
        b_equity_reset = b_equity.reset_index(drop=True)
        if len(b_equity_reset) > 1:
            b_m = compute_metrics(b_equity_reset, b_trades)
        else:
            from services.backtest.models import Metrics
            b_m = Metrics(0, 0, 0, 0, 0, 0, 0)
        bear_metrics_by_period[label] = {"metrics": b_m, "n_trades": int(b_m.n_trades)}

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
        "bear_metrics": bear_metrics_by_period,
        "is_trades": is_trades,
        "oos_trades": oos_trades,
        "all_is_result": is_result,
        "all_oos_result": oos_result,
    }


def analyze_blocked_trades(
    baseline_trades: pd.DataFrame,
    filtered_trades: pd.DataFrame,
) -> dict:
    """Baseline 거래 중 필터에 의해 차단된 거래의 실제 성과 (가상 PnL)."""
    if len(baseline_trades) == 0:
        return {"blocked_count": 0, "blocked_win_rate": float("nan"), "blocked_avg_ret": float("nan")}
    baseline_ts = set(baseline_trades["entry_ts"].values)
    filtered_ts = set(filtered_trades["entry_ts"].values) if len(filtered_trades) > 0 else set()
    removed_ts = baseline_ts - filtered_ts
    removed = baseline_trades[baseline_trades["entry_ts"].isin(removed_ts)]
    if len(removed) == 0:
        return {"blocked_count": 0, "blocked_win_rate": float("nan"), "blocked_avg_ret": float("nan")}
    wr = (removed["return_pct"] > 0).mean()
    ar = removed["return_pct"].mean()
    return {
        "blocked_count": int(len(removed)),
        "blocked_win_rate": float(wr),
        "blocked_avg_ret": float(ar),
    }


def main():
    print("=" * 70)
    print("REGIME 깊이 게이트 비교 백테스트 (ADR 20260524-2 발의용)")
    print("=" * 70)

    print("\n[1] 데이터 로드 (BTC/KRW 1d)...")
    ohlcv_full = load_ohlcv()
    print(
        f"  rows={len(ohlcv_full)}  "
        f"{ts_to_date(int(ohlcv_full['ts'].iloc[0]))} ~ "
        f"{ts_to_date(int(ohlcv_full['ts'].iloc[-1]))}"
    )

    print("\n[2] gap 분포 (OOS)...")
    ema200_full = _calc_ema(ohlcv_full["close"], 200).values
    close_full = ohlcv_full["close"].values
    gap_full = (close_full / ema200_full - 1.0) * 100.0
    oos_idx = (ohlcv_full["ts"] >= OOS_START).values
    gap_oos = gap_full[oos_idx]
    gap_oos = gap_oos[~np.isnan(gap_oos)]
    print(f"  OOS days={len(gap_oos)}")
    for thr in [-3, -5, -7, -10, -15]:
        pct = (gap_oos < thr).mean() * 100
        print(f"   gap < {thr:+d}%: {pct:5.1f}% of OOS days")

    print("\n[3] 변형별 마스크 생성...")
    masks = {}
    for label, thr in GAP_VARIANTS:
        masks[label] = build_gap_mask(ohlcv_full, thr)
        block_pct_oos = (
            (masks[label][oos_idx] == 0).mean() * 100
            if thr is not None
            else 0.0
        )
        print(f"  {label:18s} (thr={thr}): OOS 차단일 {block_pct_oos:5.1f}%")

    print("\n[4] 백테스트 실행 (전 변형)...")
    results = {}
    for label, _thr in GAP_VARIANTS:
        print(f"  [{label}] 진행 중...")
        strat = _make_composite_dc20_with_filter(entry_mask=masks[label])
        results[label] = run_split_backtest(strat, ohlcv_full)
        is_m = results[label]["is_metrics"]
        oos_m = results[label]["oos_metrics"]
        bm = results[label]["bear_metrics"]
        print(
            f"    IS  Sharpe={is_m.sharpe:6.3f} MDD={is_m.max_drawdown*100:6.1f}% "
            f"trades={is_m.n_trades:3d}"
        )
        print(
            f"    OOS Sharpe={oos_m.sharpe:6.3f} MDD={oos_m.max_drawdown*100:6.1f}% "
            f"trades={oos_m.n_trades:3d} WR={oos_m.win_rate*100:4.1f}%"
        )
        for bp_label, bp_data in bm.items():
            bm_m = bp_data["metrics"]
            print(
                f"    BEAR[{bp_label:14s}] Sharpe={bm_m.sharpe:6.3f} "
                f"MDD={bm_m.max_drawdown*100:6.1f}% trades={bm_m.n_trades:3d} "
                f"WR={bm_m.win_rate*100:4.1f}%"
            )

    print("\n[5] 차단 거래 가상 PnL...")
    baseline_oos_trades = results["Baseline"]["oos_trades"]
    blocked_effects = {}
    for label, _thr in GAP_VARIANTS:
        if label == "Baseline":
            continue
        eff = analyze_blocked_trades(baseline_oos_trades, results[label]["oos_trades"])
        blocked_effects[label] = eff
        print(
            f"  {label:18s} 차단 거래 {eff['blocked_count']:3d}건 "
            f"승률 {eff['blocked_win_rate']*100:5.1f}% "
            f"평균 {eff['blocked_avg_ret']*100:+6.2f}%"
        )

    print("\n[6] 보고서 저장...")
    _write_report(results, blocked_effects, ohlcv_full, masks, gap_oos)

    # JSON raw 저장
    json_data = {}
    for label, _thr in GAP_VARIANTS:
        is_m = results[label]["is_metrics"]
        oos_m = results[label]["oos_metrics"]
        json_data[label] = {
            "is": {
                "sharpe": float(is_m.sharpe),
                "mdd_pct": float(is_m.max_drawdown * 100),
                "n_trades": int(is_m.n_trades),
                "win_rate_pct": float(is_m.win_rate * 100),
                "avg_return_pct": float(is_m.avg_trade_return * 100),
            },
            "oos": {
                "sharpe": float(oos_m.sharpe),
                "mdd_pct": float(oos_m.max_drawdown * 100),
                "n_trades": int(oos_m.n_trades),
                "win_rate_pct": float(oos_m.win_rate * 100),
                "avg_return_pct": float(oos_m.avg_trade_return * 100),
            },
            "bear": {
                bp_label: {
                    "sharpe": float(bp_data["metrics"].sharpe),
                    "mdd_pct": float(bp_data["metrics"].max_drawdown * 100),
                    "n_trades": int(bp_data["metrics"].n_trades),
                    "win_rate_pct": float(bp_data["metrics"].win_rate * 100),
                    "avg_return_pct": float(bp_data["metrics"].avg_trade_return * 100),
                }
                for bp_label, bp_data in results[label]["bear_metrics"].items()
            },
            "blocked_effect": blocked_effects.get(label, {}),
        }
    JSON_PATH.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  JSON: {JSON_PATH}")
    print(f"  보고서: {REPORT_PATH}")
    print("\n완료.")


def _write_report(results, blocked_effects, ohlcv_full, masks, gap_oos):
    lines = []
    lines.append("# REGIME 깊이 게이트 비교 백테스트 (ADR 20260524-2 발의용)")
    lines.append("")
    lines.append(f"생성일: {datetime.date.today().isoformat()}")
    lines.append("")
    lines.append("## 1. 변형 정의")
    lines.append("")
    lines.append("| 변형 | gap 임계 | 의미 |")
    lines.append("|------|---------|------|")
    lines.append("| Baseline | — | 필터 없음 (DC20 + 1.5 vol_mult) |")
    lines.append("| Gap_OFF | -999% | sanity check (필터 무효) |")
    lines.append("| Gap_-3pct | -3% | 얕은 BEAR도 차단 (보수적) |")
    lines.append("| Gap_-5pct | -5% | |")
    lines.append("| Gap_-7pct | -7% | ADR 20260524-1 권고 후보 |")
    lines.append("| Gap_-10pct | -10% | 깊은 BEAR만 차단 (공격적) |")
    lines.append("| Gap_OLD_below_ema | 0% | 기존 ADR 20260516-2 (close<EMA200 boolean) |")
    lines.append("")

    lines.append("## 2. OOS gap 분포")
    lines.append("")
    lines.append(f"OOS days: {len(gap_oos)}")
    lines.append("")
    lines.append("| 임계 | OOS 차단일 % |")
    lines.append("|------|--------------|")
    for thr in [-3, -5, -7, -10, -15]:
        pct = (gap_oos < thr).mean() * 100
        lines.append(f"| gap < {thr:+d}% | {pct:.1f}% |")
    lines.append("")

    lines.append("## 3. 성과 비교 — IS")
    lines.append("")
    lines.append("| 변형 | Sharpe | MDD | trades | 승률 | 평균수익 |")
    lines.append("|------|--------|-----|--------|------|----------|")
    for label, _thr in GAP_VARIANTS:
        m = results[label]["is_metrics"]
        lines.append(
            f"| {label} | {m.sharpe:.3f} | {m.max_drawdown*100:.1f}% | "
            f"{m.n_trades} | {m.win_rate*100:.1f}% | {m.avg_trade_return*100:+.2f}% |"
        )
    lines.append("")

    lines.append("## 4. 성과 비교 — OOS (2024-01 ~ 2026-04)")
    lines.append("")
    lines.append("| 변형 | Sharpe | MDD | trades | 승률 | 평균수익 | 판정 |")
    lines.append("|------|--------|-----|--------|------|----------|------|")
    baseline_oos_sharpe = results["Baseline"]["oos_metrics"].sharpe
    for label, _thr in GAP_VARIANTS:
        m = results[label]["oos_metrics"]
        sharpe_ok = m.sharpe >= 1.0
        mdd_ok = m.max_drawdown >= -0.20  # MDD < 20%
        trades_ok = m.n_trades >= 5
        winrate_ok = m.win_rate >= 0.30  # 5/3~5/16 패턴(17.4%) 미재현
        if label == "Baseline":
            verdict = "기준선"
        elif sharpe_ok and mdd_ok and trades_ok and winrate_ok:
            verdict = "PASS"
        elif not trades_ok:
            verdict = "FAIL(trades<5)"
        elif not sharpe_ok:
            verdict = "FAIL(Sharpe<1.0)"
        elif not mdd_ok:
            verdict = "FAIL(MDD)"
        elif not winrate_ok:
            verdict = "FAIL(WR<30%)"
        else:
            verdict = "FAIL"
        lines.append(
            f"| {label} | {m.sharpe:.3f} | {m.max_drawdown*100:.1f}% | "
            f"{m.n_trades} | {m.win_rate*100:.1f}% | {m.avg_trade_return*100:+.2f}% | {verdict} |"
        )
    lines.append("")
    lines.append("**합격선 (ADR 20260524-1 §5.2)**: Sharpe ≥ 1.0 AND MDD < 20% AND trades ≥ 5 AND 승률 ≥ 30%")
    lines.append("")

    lines.append("## 5. 하락장 별도 검증 (BEAR_PERIOD)")
    lines.append("")
    for bp_label, _, _ in BEAR_PERIODS:
        lines.append(f"### BEAR [{bp_label}]")
        lines.append("")
        lines.append("| 변형 | Sharpe | MDD | trades | 승률 |")
        lines.append("|------|--------|-----|--------|------|")
        for label, _thr in GAP_VARIANTS:
            bm = results[label]["bear_metrics"][bp_label]["metrics"]
            lines.append(
                f"| {label} | {bm.sharpe:.3f} | {bm.max_drawdown*100:.1f}% | "
                f"{bm.n_trades} | {bm.win_rate*100:.1f}% |"
            )
        lines.append("")

    lines.append("## 6. 차단 거래 가상 PnL (OOS)")
    lines.append("")
    lines.append("Baseline 거래 중 필터에 의해 차단된 거래의 실제 성과. ")
    lines.append("차단 거래 평균수익이 음수이고 승률이 낮을수록 **필터가 유효**.")
    lines.append("")
    lines.append("| 변형 | 차단 거래 | 차단 승률 | 차단 평균수익 | 유효성 |")
    lines.append("|------|----------|-----------|---------------|--------|")
    for label, _thr in GAP_VARIANTS:
        if label == "Baseline":
            continue
        eff = blocked_effects.get(label, {})
        bc = eff.get("blocked_count", 0)
        bwr = eff.get("blocked_win_rate", float("nan"))
        bar = eff.get("blocked_avg_ret", float("nan"))
        if bc == 0:
            valid = "차단 없음"
            wr_s = ar_s = "—"
        else:
            wr_s = f"{bwr*100:.1f}%"
            ar_s = f"{bar*100:+.2f}%"
            if bar < 0 or bwr < 0.50:
                valid = "유효 (나쁜 거래 차단)"
            else:
                valid = "역효과 (좋은 거래 차단)"
        lines.append(f"| {label} | {bc} | {wr_s} | {ar_s} | {valid} |")
    lines.append("")

    lines.append("## 7. 자동 권고")
    lines.append("")
    # OOS 합격(Sharpe≥1.0, MDD<20%, trades≥5, WR≥30%) 변형 중 가장 좋은 거 추출
    candidates = []
    for label, _thr in GAP_VARIANTS:
        if label == "Baseline":
            continue
        m = results[label]["oos_metrics"]
        if (m.sharpe >= 1.0 and m.max_drawdown >= -0.20 and m.n_trades >= 5 and m.win_rate >= 0.30):
            candidates.append((label, m.sharpe, m.max_drawdown))
    if candidates:
        best = max(candidates, key=lambda x: x[1] + x[2] * 2)  # sharpe + 2*mdd
        lines.append(f"**자동 권고: `{best[0]}` (OOS Sharpe={best[1]:.3f}, MDD={best[2]*100:.1f}%)**")
        lines.append("")
        lines.append("주의: 본 백테스트는 BTC/KRW 단일 종목·DC20 변형 기준. 실거래 다종목 운영 + DC15·DC12 변형 효과는 별도 추정.")
    else:
        lines.append("**자동 권고: 합격 변형 없음 — Baseline 유지 또는 임계 추가 탐색**")
    lines.append("")
    lines.append("## 8. 한계")
    lines.append("")
    lines.append("- 단일 종목 (BTC/KRW) 백테스트 — 알트코인 다종목 실거래와 분포 다를 수 있음")
    lines.append("- DC20 기반 — 실거래는 DC12 (config.py:DONCHIAN_PERIOD=12). DC 기간 짧으면 신호 빈도 ↑ → REGIME 게이트 영향 ↑ 가능")
    lines.append("- 4h봉 ATR/거래량 필터 미반영 (일봉 기반 백테스트)")
    lines.append("- BEAR_PERIOD 두 구간(2022 UST/FTX, 2024 Q3 dip)은 표본 작음 — 통계 신뢰도 제한")
    lines.append("")
    lines.append("---")
    lines.append("*자동 생성: scripts/backtest_regime_gap_thresholds.py*")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
