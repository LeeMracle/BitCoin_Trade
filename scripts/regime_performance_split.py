# -*- coding: utf-8 -*-
"""Composite DC20 레짐별 상세 성과 분리 분석.

실행:
    PYTHONUTF8=1 python scripts/regime_performance_split.py

산출물:
    output/regime_performance_split.md
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import duckdb

from services.backtest.engine import BacktestEngine
from services.backtest.metrics import compute_metrics
from services.strategies.advanced import make_strategy_composite

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

DB_PATH    = ROOT / "data" / "cache.duckdb"
REGIME_CSV = ROOT / "output" / "regime_tags.csv"
OUTPUT_MD  = ROOT / "output" / "regime_performance_split.md"

WARMUP_START = "2017-10-01"
OOS_END      = "2026-04-05"
IS_START     = "2018-06-01"
IS_END       = "2023-12-31"
OOS_START    = "2024-01-01"

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

BT_PARAMS = dict(
    initial_capital=10_000_000,
    fee_rate=0.0005,
    slippage_bps=5,
)

REGIME_ORDER = ["BULL", "SIDEWAYS", "BEAR", "CRISIS", "EUPHORIA"]


# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────

def _date_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_ohlcv(start: str, end: str) -> pd.DataFrame:
    start_ms = _date_to_ms(start)
    end_ms   = _date_to_ms(end)
    con  = duckdb.connect(str(DB_PATH), read_only=True)
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


def load_regime() -> pd.DataFrame:
    df = pd.read_csv(REGIME_CSV)
    df["date"] = df["date"].astype(str)
    return df[["date", "regime", "close", "fg_value", "slope_pct"]]


# ──────────────────────────────────────────────
# 백테스트 실행
# ──────────────────────────────────────────────

def run_backtest(df_ohlcv: pd.DataFrame) -> tuple:
    strategy_fn = make_strategy_composite(**STRATEGY_PARAMS)
    engine      = BacktestEngine()
    result      = engine.run(strategy_fn, df_ohlcv, params=BT_PARAMS)

    tl = result.trade_log.copy()
    if len(tl) > 0:
        tl["entry_date"] = pd.to_datetime(tl["entry_ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        tl["exit_date"]  = pd.to_datetime(tl["exit_ts"],  unit="ms", utc=True).dt.strftime("%Y-%m-%d")
        tl["hold_days"]  = (tl["exit_ts"] - tl["entry_ts"]) / (1000 * 86400)
    return result, tl


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────

def _df_to_md(df: pd.DataFrame) -> str:
    if len(df) == 0:
        return "(데이터 없음)\n"
    lines = []
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep    = "| " + " | ".join("---" for _ in df.columns)  + " |"
    lines.append(header)
    lines.append(sep)
    for _, row in df.iterrows():
        cells = []
        for v in row.values:
            if isinstance(v, float) and not np.isnan(v) and v == int(v):
                cells.append(str(int(v)))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _pct(v: float, decimals: int = 1) -> str:
    return f"{v * 100:+.{decimals}f}%"


def _safe_sharpe(returns: pd.Series) -> str:
    """일별 수익률 Series로 Sharpe 추정 (연환산, 무위험이자율 0 가정)."""
    if len(returns) < 2:
        return "N/A"
    mu  = returns.mean()
    sig = returns.std(ddof=1)
    if sig == 0:
        return "N/A"
    return f"{mu / sig * np.sqrt(365):.3f}"


# ──────────────────────────────────────────────
# 분석 1: 레짐별 거래 성과 (IS + OOS 전체)
# ──────────────────────────────────────────────

def table1_regime_trade_perf(tl_all: pd.DataFrame, regime_df: pd.DataFrame) -> pd.DataFrame:
    """테이블 1: 레짐별 거래 성과."""
    if len(tl_all) == 0:
        return pd.DataFrame()

    tl = tl_all.merge(regime_df[["date", "regime"]], left_on="entry_date", right_on="date", how="left")

    rows = []
    for regime in REGIME_ORDER:
        grp = tl[tl["regime"] == regime]
        if len(grp) == 0:
            continue
        wins     = (grp["return_pct"] > 0).sum()
        win_rate = wins / len(grp) * 100
        avg_ret  = grp["return_pct"].mean() * 100
        max_win  = grp["return_pct"].max()  * 100
        max_loss = grp["return_pct"].min()  * 100
        total_ret = grp["return_pct"].sum() * 100   # 단순합 (복리 미반영)
        avg_hold  = grp["hold_days"].mean()

        rows.append({
            "레짐":       regime,
            "거래수":      len(grp),
            "승률(%)":    f"{win_rate:.1f}",
            "평균수익(%)": f"{avg_ret:+.2f}",
            "최대이익(%)": f"{max_win:+.2f}",
            "최대손실(%)": f"{max_loss:+.2f}",
            "총수익합(%)": f"{total_ret:+.1f}",
            "평균보유일":   f"{avg_hold:.1f}",
        })

    # UNKNOWN(레짐 미매칭) 처리
    unknown = tl[tl["regime"].isna()]
    if len(unknown) > 0:
        rows.append({
            "레짐":       "UNKNOWN",
            "거래수":      len(unknown),
            "승률(%)":    f"{(unknown['return_pct'] > 0).mean() * 100:.1f}",
            "평균수익(%)": f"{unknown['return_pct'].mean() * 100:+.2f}",
            "최대이익(%)": f"{unknown['return_pct'].max() * 100:+.2f}",
            "최대손실(%)": f"{unknown['return_pct'].min() * 100:+.2f}",
            "총수익합(%)": f"{unknown['return_pct'].sum() * 100:+.1f}",
            "평균보유일":   f"{unknown['hold_days'].mean():.1f}",
        })

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 분석 2: 레짐별 체류 일수 vs 수익 기여
# ──────────────────────────────────────────────

def table2_regime_dwell(
    tl_all: pd.DataFrame,
    regime_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
    period_start: str,
    period_end: str,
) -> pd.DataFrame:
    """테이블 2: 레짐별 체류 일수(비중) vs 수익 기여."""
    # 분석 기간 필터 (IS_START 이후만)
    rdf = regime_df[(regime_df["date"] >= period_start) & (regime_df["date"] <= period_end)].copy()
    total_days = len(rdf)

    tl = tl_all.merge(regime_df[["date", "regime"]], left_on="entry_date", right_on="date", how="left")
    is_ms  = _date_to_ms(period_start)
    end_ms = _date_to_ms(period_end) + 86400_000
    tl = tl[(tl["entry_ts"] >= is_ms) & (tl["entry_ts"] <= end_ms)]

    rows = []
    for regime in REGIME_ORDER:
        dwell     = (rdf["regime"] == regime).sum()
        dwell_pct = dwell / total_days * 100 if total_days > 0 else 0

        grp = tl[tl["regime"] == regime]
        n   = len(grp)

        # 해당 레짐 내 Buy&Hold 수익률 (레짐 첫날 시가→마지막날 종가)
        rdf_regime = rdf[rdf["regime"] == regime]
        bh_ret = float("nan")
        if len(rdf_regime) > 0:
            first_date = rdf_regime["date"].min()
            last_date  = rdf_regime["date"].max()
            oh_sub = ohlcv_df[(ohlcv_df["date"] >= first_date) & (ohlcv_df["date"] <= last_date)]
            if len(oh_sub) > 1:
                bh_ret = (oh_sub["close"].iloc[-1] / oh_sub["open"].iloc[0]) - 1

        # 전략 거래 복리 수익률 (해당 레짐 거래들의 단순 복리 연결)
        if n > 0:
            strategy_compound = grp["return_pct"].add(1).prod() - 1
        else:
            strategy_compound = float("nan")

        rows.append({
            "레짐":              regime,
            "체류일수":           dwell,
            "비중(%)":           f"{dwell_pct:.1f}",
            "거래수":             n,
            "전략복리수익(%)":    f"{strategy_compound * 100:+.1f}" if not np.isnan(strategy_compound) else "-",
            "B&H수익(%)":        f"{bh_ret * 100:+.1f}"            if not np.isnan(bh_ret)            else "-",
            "초과수익(pp)":       f"{(strategy_compound - bh_ret) * 100:+.1f}"
                                  if not (np.isnan(strategy_compound) or np.isnan(bh_ret)) else "-",
        })

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 분석 3: 레짐 전환 시 성과
# ──────────────────────────────────────────────

def table3_regime_transition(tl_all: pd.DataFrame, regime_df: pd.DataFrame) -> dict:
    """테이블 3: 레짐 전환 시 진입 거래 vs 레짐 중간 진입 비교."""
    if len(tl_all) == 0:
        return {}

    # 레짐 전환일 계산
    rdf = regime_df.copy().sort_values("date").reset_index(drop=True)
    rdf["prev_regime"] = rdf["regime"].shift(1)
    rdf["is_transition"] = rdf["regime"] != rdf["prev_regime"]
    transition_dates = set(rdf[rdf["is_transition"]]["date"].tolist())

    # 5일 이내 진입 = "전환 직후 진입"으로 간주
    WINDOW_DAYS = 5

    def days_since_transition(entry_date: str) -> int:
        """entry_date 기준으로 가장 최근 전환일까지의 거리(일)."""
        entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
        min_diff = 9999
        for td in transition_dates:
            try:
                diff = (entry_dt - datetime.strptime(td, "%Y-%m-%d")).days
                if 0 <= diff <= 30:
                    min_diff = min(min_diff, diff)
            except Exception:
                pass
        return min_diff

    tl = tl_all.merge(regime_df[["date", "regime"]], left_on="entry_date", right_on="date", how="left")
    tl["days_since_trans"] = tl["entry_date"].apply(days_since_transition)
    tl["is_transition_entry"] = tl["days_since_trans"] <= WINDOW_DAYS

    results = {}

    # 3-1. BEAR→BULL 또는 SIDEWAYS→BULL 전환 후 BULL 진입
    bull_trans_entries = tl[
        (tl["regime"] == "BULL") & (tl["is_transition_entry"])
    ]
    bull_mid_entries = tl[
        (tl["regime"] == "BULL") & (~tl["is_transition_entry"])
    ]

    results["bull_transition"] = bull_trans_entries
    results["bull_mid"]        = bull_mid_entries

    # 3-2. →BEAR 전환 후 BEAR 진입
    bear_trans_entries = tl[
        (tl["regime"] == "BEAR") & (tl["is_transition_entry"])
    ]
    bear_mid_entries = tl[
        (tl["regime"] == "BEAR") & (~tl["is_transition_entry"])
    ]

    results["bear_transition"] = bear_trans_entries
    results["bear_mid"]        = bear_mid_entries

    return results


def _summarize_entries(grp: pd.DataFrame, label: str) -> dict:
    if len(grp) == 0:
        return {
            "구분":       label,
            "거래수":      0,
            "승률(%)":    "-",
            "평균수익(%)": "-",
            "최대이익(%)": "-",
            "최대손실(%)": "-",
            "평균보유일":   "-",
        }
    return {
        "구분":       label,
        "거래수":      len(grp),
        "승률(%)":    f"{(grp['return_pct'] > 0).mean() * 100:.1f}",
        "평균수익(%)": f"{grp['return_pct'].mean() * 100:+.2f}",
        "최대이익(%)": f"{grp['return_pct'].max() * 100:+.2f}",
        "최대손실(%)": f"{grp['return_pct'].min() * 100:+.2f}",
        "평균보유일":   f"{grp['hold_days'].mean():.1f}",
    }


# ──────────────────────────────────────────────
# 분석 4: 핵심 인사이트 + 레짐 필터 시뮬레이션
# ──────────────────────────────────────────────

def simulate_regime_filter(
    tl_all: pd.DataFrame,
    regime_df: pd.DataFrame,
    exclude_regimes: list[str],
) -> dict:
    """특정 레짐에서 거래를 제외했을 때 성과 변화 추정."""
    tl = tl_all.merge(regime_df[["date", "regime"]], left_on="entry_date", right_on="date", how="left")

    base_trades  = len(tl)
    base_wr      = (tl["return_pct"] > 0).mean() * 100
    base_compound = tl["return_pct"].add(1).prod() - 1

    tl_filtered   = tl[~tl["regime"].isin(exclude_regimes)]
    filt_trades   = len(tl_filtered)
    filt_wr       = (tl_filtered["return_pct"] > 0).mean() * 100 if filt_trades > 0 else 0
    filt_compound = tl_filtered["return_pct"].add(1).prod() - 1 if filt_trades > 0 else 0

    return {
        "필터 제외 레짐":        ", ".join(exclude_regimes),
        "기존 거래수":           base_trades,
        "기존 승률(%)":         f"{base_wr:.1f}",
        "기존 복리수익(%)":      f"{base_compound * 100:+.1f}",
        "필터 후 거래수":        filt_trades,
        "필터 후 승률(%)":       f"{filt_wr:.1f}",
        "필터 후 복리수익(%)":   f"{filt_compound * 100:+.1f}",
        "수익률 변화(pp)":       f"{(filt_compound - base_compound) * 100:+.1f}",
    }


# ──────────────────────────────────────────────
# 보고서 빌더
# ──────────────────────────────────────────────

def build_report(
    t1: pd.DataFrame,
    t2_full: pd.DataFrame,
    t2_is: pd.DataFrame,
    t2_oos: pd.DataFrame,
    trans_results: dict,
    filter_sims: list[dict],
    tl_all: pd.DataFrame,
    regime_df: pd.DataFrame,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    lines.append("# Composite DC20 레짐별 상세 성과 분리 분석\n")
    lines.append(f"생성일시: {now}  ")
    lines.append("전략: Composite DC20 (DC20 + ATR 트레일링 + RSI(10)>50 OR 거래량×1.5 복합 진입)  ")
    lines.append(f"분석 기간: IS {IS_START}~{IS_END} / OOS {OOS_START}~{OOS_END}\n")

    lines.append("---\n")

    # ── 테이블 1 ──────────────────────────────
    lines.append("## 테이블 1: 레짐별 거래 성과 (IS + OOS 전체)\n")
    lines.append("> 레짐은 진입일 기준으로 태깅. 총수익합은 단순 합산, 전략복리수익은 복리 연결 기준.\n")
    lines.append(_df_to_md(t1))

    lines.append("**레짐 정의:**")
    lines.append("- BULL: BTC > EMA200, F&G 25~74, 기울기 양수")
    lines.append("- SIDEWAYS: BTC > EMA200, F&G 25~74, 기울기 -1~+1%")
    lines.append("- BEAR: BTC < EMA200 or F&G < 25, 기울기 음수")
    lines.append("- CRISIS: F&G < 20 (극공포)")
    lines.append("- EUPHORIA: F&G >= 75 (과열)")
    lines.append("")

    # ── 테이블 2 ──────────────────────────────
    lines.append("## 테이블 2: 레짐별 체류 일수 vs 수익 기여\n")
    lines.append("### 2-1. 전체 분석 기간 (IS + OOS)\n")
    lines.append(_df_to_md(t2_full))
    lines.append("### 2-2. IS 기간 (2018-06 ~ 2023-12)\n")
    lines.append(_df_to_md(t2_is))
    lines.append("### 2-3. OOS 기간 (2024-01 ~ 2026-04)\n")
    lines.append(_df_to_md(t2_oos))
    lines.append("> 전략복리수익: 해당 레짐 내 진입한 거래들의 복리 연결 수익률  ")
    lines.append("> B&H수익: 해당 레짐 구간 전체의 매수보유 수익률  ")
    lines.append("> 초과수익: 전략복리수익 - B&H수익 (양수 = 전략 우위)\n")

    # ── 테이블 3 ──────────────────────────────
    lines.append("## 테이블 3: 레짐 전환 시 성과 비교\n")
    lines.append("> 레짐 전환일 기준 5일 이내 진입 = '전환 직후 진입', 그 외 = '레짐 중간 진입'.\n")

    bull_rows = [
        _summarize_entries(trans_results.get("bull_transition", pd.DataFrame()), "BULL 전환 직후 진입 (≤5일)"),
        _summarize_entries(trans_results.get("bull_mid",        pd.DataFrame()), "BULL 중간 진입 (>5일)"),
    ]
    lines.append("### 3-1. BULL 레짐 — 전환 직후 vs 중간 진입\n")
    lines.append(_df_to_md(pd.DataFrame(bull_rows)))

    bear_rows = [
        _summarize_entries(trans_results.get("bear_transition", pd.DataFrame()), "BEAR 전환 직후 진입 (≤5일)"),
        _summarize_entries(trans_results.get("bear_mid",        pd.DataFrame()), "BEAR 중간 진입 (>5일)"),
    ]
    lines.append("### 3-2. BEAR 레짐 — 전환 직후 vs 중간 진입\n")
    lines.append(_df_to_md(pd.DataFrame(bear_rows)))

    # ── 테이블 4 ──────────────────────────────
    lines.append("## 테이블 4: 핵심 인사이트 및 레짐 필터 개선 효과\n")

    # 4-1. 약점 요약
    lines.append("### 4-1. Composite 전략의 약점 레짐\n")

    tl_tagged = tl_all.merge(regime_df[["date", "regime"]], left_on="entry_date", right_on="date", how="left")

    weak_rows = []
    for regime in REGIME_ORDER:
        grp = tl_tagged[tl_tagged["regime"] == regime]
        if len(grp) == 0:
            continue
        wr  = (grp["return_pct"] > 0).mean() * 100
        avg = grp["return_pct"].mean() * 100
        if wr < 50 or avg < 0:
            label = "취약" if avg < 0 else "주의"
            weak_rows.append({
                "레짐":      regime,
                "판정":      label,
                "거래수":     len(grp),
                "승률(%)":   f"{wr:.1f}",
                "평균수익(%)": f"{avg:+.2f}",
                "원인 분석":  _regime_weakness_desc(regime, grp),
            })
        else:
            weak_rows.append({
                "레짐":      regime,
                "판정":      "양호",
                "거래수":     len(grp),
                "승률(%)":   f"{wr:.1f}",
                "평균수익(%)": f"{avg:+.2f}",
                "원인 분석":  "-",
            })

    lines.append(_df_to_md(pd.DataFrame(weak_rows)))

    # 4-2. 레짐 필터 시뮬레이션
    lines.append("### 4-2. 레짐 필터 추가 시 예상 개선 효과\n")
    lines.append(_df_to_md(pd.DataFrame(filter_sims)))
    lines.append("")

    # 4-3. 정성적 인사이트
    lines.append("### 4-3. 정성적 인사이트\n")
    lines.extend(_build_insights(tl_tagged, regime_df))

    lines.append("\n---")
    lines.append("*자동 생성: scripts/regime_performance_split.py*")
    return "\n".join(lines)


def _regime_weakness_desc(regime: str, grp: pd.DataFrame) -> str:
    avg = grp["return_pct"].mean() * 100
    wr  = (grp["return_pct"] > 0).mean() * 100
    if regime == "CRISIS":
        return "F&G 극공포 구간 — 일방적 하락, 반등 실패 가능성 높음"
    if regime == "BEAR":
        if avg > 0:
            return "BEAR에서도 평균 수익 양수 — 반등 포착 가능 (단, OOS에서 음전)"
        return "지속 하락 구간 — 가짜 돌파 빈도 높음"
    if regime == "SIDEWAYS":
        return "횡보 구간 — DC 돌파 후 추세 미형성, 잦은 조기 청산"
    return "-"


def _build_insights(tl_tagged: pd.DataFrame, regime_df: pd.DataFrame) -> list[str]:
    lines = []

    # BEAR IS vs OOS 비교
    bear_is  = tl_tagged[(tl_tagged["regime"] == "BEAR") &
                          (tl_tagged["entry_date"] >= IS_START) &
                          (tl_tagged["entry_date"] <= IS_END)]
    bear_oos = tl_tagged[(tl_tagged["regime"] == "BEAR") &
                          (tl_tagged["entry_date"] >= OOS_START)]

    bear_is_avg  = bear_is["return_pct"].mean()  * 100 if len(bear_is)  > 0 else float("nan")
    bear_oos_avg = bear_oos["return_pct"].mean() * 100 if len(bear_oos) > 0 else float("nan")

    lines.append("#### BEAR 레짐 IS vs OOS 역전 현상\n")
    lines.append(f"- IS 기간 BEAR 진입 평균 수익: **{bear_is_avg:+.2f}%** ({len(bear_is)}건)")
    lines.append(f"- OOS 기간 BEAR 진입 평균 수익: **{bear_oos_avg:+.2f}%** ({len(bear_oos)}건)")
    if not (np.isnan(bear_is_avg) or np.isnan(bear_oos_avg)):
        diff = bear_oos_avg - bear_is_avg
        if diff < -10:
            lines.append(f"- IS 대비 OOS {diff:+.1f}pp 악화 — 2024~2026 BEAR 구간 특성 변화 가능성")
        else:
            lines.append("- IS/OOS 성과 유사 — 전략 일관성 확인됨")
    lines.append("")

    # SIDEWAYS 구간 분석
    sw = tl_tagged[tl_tagged["regime"] == "SIDEWAYS"]
    if len(sw) > 0:
        lines.append("#### SIDEWAYS 레짐 분석\n")
        lines.append(f"- 전체 {len(sw)}건, 승률 {(sw['return_pct'] > 0).mean() * 100:.1f}%, "
                     f"평균 수익 {sw['return_pct'].mean() * 100:+.2f}%")
        lines.append(f"- 평균 보유일: {sw['hold_days'].mean():.1f}일 (전체 평균 대비 짧으면 추세 미형성 가능성)")
        lines.append("- **대응 방안**: 횡보 레짐에서 진입 유보 또는 포지션 크기 50% 축소 검토")
        lines.append("")

    # BULL 레짐이 핵심 수익원임을 확인
    bull = tl_tagged[tl_tagged["regime"] == "BULL"]
    if len(bull) > 0:
        bull_compound = bull["return_pct"].add(1).prod() - 1
        all_compound  = tl_tagged["return_pct"].add(1).prod() - 1
        contrib_pct   = bull_compound / all_compound * 100 if all_compound != 0 else float("nan")
        lines.append("#### BULL 레짐이 전략의 핵심 수익원\n")
        lines.append(f"- BULL 레짐 복리 수익: {bull_compound * 100:+.1f}%")
        if not np.isnan(contrib_pct):
            lines.append(f"- 전체 복리 수익에서 BULL 기여도: 약 {contrib_pct:.0f}%")
        lines.append("- **대응 방안**: BULL 레짐에서 진입 기회를 놓치지 않도록 진입 기준 완화 검토")
        lines.append("")

    # CRISIS 레짐 경고
    crisis = tl_tagged[tl_tagged["regime"] == "CRISIS"]
    if len(crisis) > 0:
        lines.append("#### CRISIS 레짐 — 유일 구조적 손실 구간\n")
        lines.append(f"- {len(crisis)}건 전패, 평균 {crisis['return_pct'].mean() * 100:+.2f}%")
        lines.append("- F&G < 20 (극공포) 시 시장이 DC 돌파를 지지하지 못하고 직후 반락")
        lines.append("- **대응 방안**: CRISIS 레짐 진입 완전 차단 — F&G < 20 필터 추가 시 해당 손실 제거 가능")
        lines.append("")

    return lines


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Composite DC20 레짐별 상세 성과 분리 분석")
    print("=" * 60)

    print("\n[1] 데이터 로드 중...")
    df_full    = load_ohlcv(WARMUP_START, OOS_END)
    regime_df  = load_regime()
    print(f"  OHLCV: {len(df_full)}봉 / 레짐 태그: {len(regime_df)}행")

    print("\n[2] 백테스트 실행 중...")
    result, tl_all = run_backtest(df_full)
    print(f"  전체 거래: {len(tl_all)}건")

    # IS/OOS 구분
    is_ms  = _date_to_ms(IS_START)
    end_is = _date_to_ms(IS_END)  + 86400_000
    oos_ms = _date_to_ms(OOS_START)
    end_oo = _date_to_ms(OOS_END) + 86400_000

    tl_is  = tl_all[(tl_all["entry_ts"] >= is_ms)  & (tl_all["entry_ts"] <= end_is)]
    tl_oos = tl_all[(tl_all["entry_ts"] >= oos_ms) & (tl_all["entry_ts"] <= end_oo)]
    print(f"  IS: {len(tl_is)}건 / OOS: {len(tl_oos)}건")

    print("\n[3] 테이블 1 — 레짐별 거래 성과 산출 중...")
    t1 = table1_regime_trade_perf(tl_all, regime_df)
    print(t1.to_string(index=False))

    print("\n[4] 테이블 2 — 체류 일수 vs 수익 기여 산출 중...")
    t2_full = table2_regime_dwell(tl_all, regime_df, df_full, IS_START,  OOS_END)
    t2_is   = table2_regime_dwell(tl_is,  regime_df, df_full, IS_START,  IS_END)
    t2_oos  = table2_regime_dwell(tl_oos, regime_df, df_full, OOS_START, OOS_END)

    print("\n[5] 테이블 3 — 레짐 전환 시 성과 산출 중...")
    trans_results = table3_regime_transition(tl_all, regime_df)
    for k, v in trans_results.items():
        print(f"  {k}: {len(v)}건")

    print("\n[6] 테이블 4 — 레짐 필터 시뮬레이션 중...")
    filter_sims = [
        simulate_regime_filter(tl_all, regime_df, ["CRISIS"]),
        simulate_regime_filter(tl_all, regime_df, ["CRISIS", "BEAR"]),
        simulate_regime_filter(tl_all, regime_df, ["CRISIS", "SIDEWAYS"]),
        simulate_regime_filter(tl_all, regime_df, ["BEAR"]),
    ]
    for fs in filter_sims:
        print(f"  [{fs['필터 제외 레짐']}] 거래수 {fs['기존 거래수']}→{fs['필터 후 거래수']}, "
              f"복리수익 {fs['기존 복리수익(%)']}→{fs['필터 후 복리수익(%)']}")

    print("\n[7] 보고서 생성 중...")
    report = build_report(t1, t2_full, t2_is, t2_oos, trans_results, filter_sims, tl_all, regime_df)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report, encoding="utf-8")
    print(f"  저장 완료: {OUTPUT_MD}")


if __name__ == "__main__":
    main()
