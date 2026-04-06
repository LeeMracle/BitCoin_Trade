# -*- coding: utf-8 -*-
"""EMA Trend Follow 전략 심화 검증 — 9종 파라미터 변형 스크리닝.

검증 목표:
  - MDD를 낮추면서 Sharpe를 유지할 수 있는 파라미터 조합 탐색
  - Composite DC20과의 상관관계 분석으로 병행 운용 시 분산 효과 확인

실행:
    PYTHONUTF8=1 python scripts/backtest_ema_deep_screening.py

산출물:
    output/ema_deep_screening.md
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
from services.strategies.advanced import make_strategy_composite

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

DB_PATH    = ROOT / "data" / "cache.duckdb"
REGIME_CSV = ROOT / "output" / "regime_tags.csv"
OUTPUT_MD  = ROOT / "output" / "ema_deep_screening.md"

WARMUP_START = "2017-10-01"   # EMA200 필터 변형 워밍업
IS_START     = "2018-06-01"
IS_END       = "2023-12-31"
OOS_START    = "2024-01-01"
OOS_END      = "2026-04-05"

BT_PARAMS = dict(
    initial_capital=10_000_000,
    fee_rate=0.0005,
    slippage_bps=5,
)

# Composite DC20 파라미터
COMPOSITE_PARAMS = dict(
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

# 판정 기준
PASS_SHARPE  = 1.0   # OOS Sharpe 통과 기준
PASS_MDD     = -30.0 # IS MDD 허용 하한 (%)


# ──────────────────────────────────────────────
# 데이터 로드
# ──────────────────────────────────────────────

def _date_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def load_ohlcv(start: str, end: str) -> pd.DataFrame:
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


def load_regime() -> pd.DataFrame:
    df = pd.read_csv(REGIME_CSV)
    df["date"] = df["date"].astype(str)
    return df[["date", "regime"]]


# ──────────────────────────────────────────────
# EMA Trend Follow 전략 팩토리
# ──────────────────────────────────────────────

def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def make_ema_trend_follow(
    ema_period: int   = 50,
    trail_pct:  float = 0.05,
    ema200_filter: bool = False,
):
    """EMA Trend Follow 전략.

    Args:
        ema_period:    진입/청산 기준 EMA 기간
        trail_pct:     트레일링스탑 비율 (0.05 = 5%)
        ema200_filter: True이면 BTC > EMA(200)일 때만 진입 허용
    """
    def strategy(df: pd.DataFrame) -> pd.Series:
        close    = df["close"].values
        ema_vals = _calc_ema(df["close"], ema_period).values

        ema200_vals = None
        if ema200_filter:
            ema200_vals = _calc_ema(df["close"], 200).values

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
                # 진입 조건: 전날 close < EMA, 오늘 close > EMA
                if i >= 1 and close[i - 1] < ema_vals[i - 1] and c > e:
                    # EMA200 필터 적용 시: EMA200 위에 있을 때만 진입
                    if ema200_filter and ema200_vals is not None:
                        e200 = ema200_vals[i]
                        if np.isnan(e200) or c <= e200:
                            signal[i] = 0
                            continue
                    in_pos  = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                trail_stop = highest * (1.0 - trail_pct)

                # 청산: 트레일링스탑 OR EMA 하향 이탈
                if c < trail_stop or c < e:
                    in_pos  = False
                    highest = 0.0
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 메트릭 추출 헬퍼
# ──────────────────────────────────────────────

def extract_period_metrics(result, period_start: str, period_end: str) -> dict:
    """특정 기간의 Sharpe, MDD, 거래수, 승률 추출."""
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
        return {"sharpe": 0.0, "mdd": 0.0, "n_trades": 0, "win_rate": 0.0}

    m = compute_metrics(eq_sub, tl_sub)
    return {
        "sharpe":   m.sharpe,
        "mdd":      round(m.max_drawdown * 100, 1),
        "n_trades": m.n_trades,
        "win_rate": round(m.win_rate * 100, 1),
    }


def get_daily_returns(result, period_start: str, period_end: str) -> pd.Series:
    """equity_curve에서 일별 수익률 시리즈 추출 (날짜 인덱스)."""
    start_ms = _date_to_ms(period_start)
    end_ms   = _date_to_ms(period_end) + 86_400_000

    eq = result.equity_curve.copy()
    eq_sub = eq[(eq["ts"] >= start_ms) & (eq["ts"] <= end_ms)].copy()
    eq_sub = eq_sub.sort_values("ts")
    eq_sub["date"] = pd.to_datetime(eq_sub["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    eq_sub = eq_sub.drop_duplicates("date").set_index("date")
    returns = eq_sub["equity"].pct_change().dropna()
    return returns


def get_position_flags(result, period_start: str, period_end: str) -> pd.Series:
    """포지션 보유 여부 플래그 (날짜 인덱스, 1=보유 / 0=현금)."""
    start_ms = _date_to_ms(period_start)
    end_ms   = _date_to_ms(period_end) + 86_400_000

    eq = result.equity_curve.copy()
    eq_sub = eq[(eq["ts"] >= start_ms) & (eq["ts"] <= end_ms)].copy()
    eq_sub = eq_sub.sort_values("ts")
    eq_sub["date"] = pd.to_datetime(eq_sub["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    eq_sub = eq_sub.drop_duplicates("date").set_index("date")

    tl = result.trade_log
    if len(tl) == 0:
        return pd.Series(0, index=eq_sub.index)

    # 날짜별 포지션 플래그 생성
    pos_flags = pd.Series(0, index=eq_sub.index)
    for _, trade in tl.iterrows():
        entry_date = pd.to_datetime(trade["entry_ts"], unit="ms", utc=True).strftime("%Y-%m-%d")
        exit_date  = pd.to_datetime(trade["exit_ts"],  unit="ms", utc=True).strftime("%Y-%m-%d")
        # 진입일 ~ 청산일 전날까지 포지션 보유
        mask = (pos_flags.index >= entry_date) & (pos_flags.index < exit_date)
        pos_flags[mask] = 1

    return pos_flags


# ──────────────────────────────────────────────
# 레짐별 성과
# ──────────────────────────────────────────────

def regime_analysis(result, regime_df: pd.DataFrame) -> pd.DataFrame:
    tl = result.trade_log.copy()
    if len(tl) == 0:
        return pd.DataFrame()

    tl["entry_date"] = pd.to_datetime(tl["entry_ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    tl = tl.merge(regime_df, left_on="entry_date", right_on="date", how="left")

    rows = []
    regime_order = ["BULL", "SIDEWAYS", "BEAR", "CRISIS", "EUPHORIA", "WARMUP"]
    for reg in regime_order:
        grp = tl[tl["regime"] == reg]
        if len(grp) == 0:
            continue
        rows.append({
            "레짐":          reg,
            "거래수":        len(grp),
            "승률(%)":       round((grp["return_pct"] > 0).mean() * 100, 1),
            "평균수익률(%)": round(grp["return_pct"].mean() * 100, 2),
        })

    unknown = tl[tl["regime"].isna()]
    if len(unknown) > 0:
        rows.append({
            "레짐":          "UNKNOWN",
            "거래수":        len(unknown),
            "승률(%)":       round((unknown["return_pct"] > 0).mean() * 100, 1),
            "평균수익률(%)": round(unknown["return_pct"].mean() * 100, 2),
        })

    return pd.DataFrame(rows)


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
# 변형 정의 (9종)
# ──────────────────────────────────────────────

VARIANTS = [
    # 축 1: EMA 기간 변형
    dict(name="기준선",         ema=50, trail=0.05, ema200_filter=False),
    dict(name="빠른반응",       ema=30, trail=0.05, ema200_filter=False),
    dict(name="느린반응",       ema=75, trail=0.05, ema200_filter=False),
    # 축 2: 트레일링 % 변형 (EMA 50 고정)
    dict(name="좁은스탑",       ema=50, trail=0.03, ema200_filter=False),
    # 기준선은 위에서 이미 포함 (ema=50, trail=0.05)
    dict(name="넓은스탑",       ema=50, trail=0.08, ema200_filter=False),
    dict(name="아주넓은스탑",   ema=50, trail=0.10, ema200_filter=False),
    # 축 3: 복합 변형
    dict(name="EMA50+EMA200필터", ema=50, trail=0.05, ema200_filter=True),
    dict(name="EMA75+넓은스탑",   ema=75, trail=0.08, ema200_filter=False),
    dict(name="EMA30+좁은스탑",   ema=30, trail=0.03, ema200_filter=False),
]


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    print("=" * 70)
    print("EMA Trend Follow 전략 심화 검증 — 9종 파라미터 변형")
    print("=" * 70)

    # 1. 데이터 로드
    print("\n[1] 데이터 로드 중...")
    df_full = load_ohlcv(WARMUP_START, OOS_END)
    print(f"  전체 OHLCV: {len(df_full)}봉 ({df_full['date'].iloc[0]} ~ {df_full['date'].iloc[-1]})")

    regime_df = load_regime()
    print(f"  레짐 태그: {len(regime_df)}행")

    engine = BacktestEngine()

    # 2. 9종 변형 백테스트
    print("\n[2] 9종 변형 백테스트 실행 중...")
    screening_rows = []
    variant_results = {}   # name -> RunResult

    for v in VARIANTS:
        label = (
            f"EMA{v['ema']} Trail{int(v['trail']*100)}%"
            + (" +EMA200필터" if v["ema200_filter"] else "")
        )
        print(f"\n  --- {v['name']} ({label}) ---")

        fn = make_ema_trend_follow(
            ema_period=v["ema"],
            trail_pct=v["trail"],
            ema200_filter=v["ema200_filter"],
        )
        result = engine.run(fn, df_full, params=BT_PARAMS)
        variant_results[v["name"]] = result

        is_m  = extract_period_metrics(result, IS_START,  IS_END)
        oos_m = extract_period_metrics(result, OOS_START, OOS_END)

        print(f"    IS  → Sharpe: {is_m['sharpe']}, MDD: {is_m['mdd']}%, "
              f"거래수: {is_m['n_trades']}, 승률: {is_m['win_rate']}%")
        print(f"    OOS → Sharpe: {oos_m['sharpe']}, MDD: {oos_m['mdd']}%, "
              f"거래수: {oos_m['n_trades']}, 승률: {oos_m['win_rate']}%")

        # 판정
        oos_pass = oos_m['sharpe'] >= PASS_SHARPE
        is_mdd_ok = is_m['mdd'] >= PASS_MDD  # MDD는 음수이므로 >= (예: -25 >= -30)
        if oos_pass and is_mdd_ok:
            verdict = "PASS"
        elif oos_m['sharpe'] >= 0.7:
            verdict = "보통"
        else:
            verdict = "FAIL"

        filter_str = "EMA200" if v["ema200_filter"] else "-"
        screening_rows.append({
            "변형":         v["name"],
            "EMA":          v["ema"],
            "Trail(%)":     int(v["trail"] * 100),
            "추가필터":     filter_str,
            "IS Sharpe":    is_m["sharpe"],
            "IS MDD(%)":    is_m["mdd"],
            "OOS Sharpe":   oos_m["sharpe"],
            "OOS MDD(%)":   oos_m["mdd"],
            "거래수(IS)":   is_m["n_trades"],
            "승률(IS,%)":   is_m["win_rate"],
            "판정":         verdict,
        })

    screening_df = pd.DataFrame(screening_rows)

    # 3. 최선 변형 선택 — OOS Sharpe 최대, IS MDD >= PASS_MDD 조건 우선
    print("\n[3] 최선 변형 선택 중...")
    pass_df = screening_df[
        (screening_df["OOS Sharpe"] >= PASS_SHARPE) &
        (screening_df["IS MDD(%)"] >= PASS_MDD)
    ].copy()

    if len(pass_df) > 0:
        # PASS 중 IS MDD가 가장 낮은(절댓값 작은) 것 선택
        best_idx = pass_df["IS MDD(%)"].idxmax()  # MDD는 음수이므로 idxmax = 가장 낮은 절댓값
        best_name = screening_df.loc[best_idx, "변형"]
    else:
        # PASS 없으면 OOS Sharpe 최대
        best_idx  = screening_df["OOS Sharpe"].idxmax()
        best_name = screening_df.loc[best_idx, "변형"]

    print(f"  최선 변형: {best_name}")
    best_result = variant_results[best_name]

    # 4. 최선 변형 레짐별 성과 (IS 전체 기간 기준)
    print("\n[4] 최선 변형 레짐별 성과 분석 중...")
    # IS 기간 거래만 필터
    is_start_ms = _date_to_ms(IS_START)
    is_end_ms   = _date_to_ms(IS_END) + 86_400_000
    best_tl = best_result.trade_log.copy()
    if len(best_tl) > 0:
        best_tl_is = best_tl[
            (best_tl["entry_ts"] >= is_start_ms) &
            (best_tl["entry_ts"] <= is_end_ms)
        ].copy()
    else:
        best_tl_is = best_tl.copy()

    # regime_analysis용 임시 RunResult-like 객체
    class _FakeResult:
        def __init__(self, tl):
            self.trade_log = tl
    regime_df_result = regime_analysis(_FakeResult(best_tl_is), regime_df)

    # 5. Composite DC20 백테스트 (상관관계용 equity curve 필요)
    print("\n[5] Composite DC20 백테스트 실행 중 (상관관계 계산용)...")
    composite_fn = make_strategy_composite(**COMPOSITE_PARAMS)
    composite_result = engine.run(composite_fn, df_full, params=BT_PARAMS)

    comp_is_m  = extract_period_metrics(composite_result, IS_START,  IS_END)
    comp_oos_m = extract_period_metrics(composite_result, OOS_START, OOS_END)
    print(f"  Composite IS  → Sharpe: {comp_is_m['sharpe']}, MDD: {comp_is_m['mdd']}%")
    print(f"  Composite OOS → Sharpe: {comp_oos_m['sharpe']}, MDD: {comp_oos_m['mdd']}%")

    # 6. 상관관계 분석
    print("\n[6] 상관관계 분석 중...")

    def calc_correlation(ema_result, comp_result, start: str, end: str, label: str):
        ema_ret  = get_daily_returns(ema_result,  start, end)
        comp_ret = get_daily_returns(comp_result, start, end)

        # 날짜 정렬 후 공통 날짜만
        common_idx = ema_ret.index.intersection(comp_ret.index)
        if len(common_idx) < 10:
            return {"기간": label, "상관계수": "-", "공통일수": len(common_idx), "동시보유비율(%)": "-"}

        r_ema  = ema_ret[common_idx]
        r_comp = comp_ret[common_idx]
        corr   = float(r_ema.corr(r_comp))

        # 동시 보유 비율
        ema_pos  = get_position_flags(ema_result,  start, end)
        comp_pos = get_position_flags(comp_result, start, end)
        common_pos_idx = ema_pos.index.intersection(comp_pos.index)
        if len(common_pos_idx) > 0:
            both_in  = ((ema_pos[common_pos_idx] == 1) & (comp_pos[common_pos_idx] == 1)).sum()
            sim_ratio = round(both_in / len(common_pos_idx) * 100, 1)
        else:
            sim_ratio = "-"

        return {
            "기간":             label,
            "일별수익률 상관계수": round(corr, 4),
            "공통일수":         len(common_idx),
            "동시보유비율(%)":  sim_ratio,
        }

    corr_is  = calc_correlation(best_result, composite_result, IS_START,  IS_END,  "IS (2018-2023)")
    corr_oos = calc_correlation(best_result, composite_result, OOS_START, OOS_END, "OOS (2024-2026)")
    corr_df  = pd.DataFrame([corr_is, corr_oos])

    print(f"  IS 상관계수:  {corr_is['일별수익률 상관계수']}, 동시보유: {corr_is['동시보유비율(%)']}%")
    print(f"  OOS 상관계수: {corr_oos['일별수익률 상관계수']}, 동시보유: {corr_oos['동시보유비율(%)']}%")

    # ──────────────────────────────────────────────
    # 7. 보고서 생성
    # ──────────────────────────────────────────────
    print("\n[7] 보고서 생성 중...")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    best_row = screening_df[screening_df["변형"] == best_name].iloc[0]

    # 최선 변형 레짐별 분석에 OOS도 추가
    best_tl_oos = best_tl[
        (best_tl["entry_ts"] >= _date_to_ms(OOS_START)) &
        (best_tl["entry_ts"] <= _date_to_ms(OOS_END) + 86_400_000)
    ].copy() if len(best_result.trade_log) > 0 else best_result.trade_log.copy()

    regime_oos_result = regime_analysis(_FakeResult(best_tl_oos), regime_df)

    lines = []
    lines.append("# EMA Trend Follow 전략 심화 검증 결과\n")
    lines.append(f"생성일시: {now}  ")
    lines.append(f"데이터: BTC/KRW 일봉 (업비트), {df_full['date'].iloc[0]} ~ {df_full['date'].iloc[-1]}, {len(df_full)}봉  ")
    lines.append(f"IS 기간: {IS_START} ~ {IS_END} / OOS 기간: {OOS_START} ~ {OOS_END}\n")

    lines.append("## 검증 목표\n")
    lines.append("- 기존 전략 A (EMA50 + Trail 5%)의 IS MDD -50%를 개선할 수 있는 파라미터 조합 탐색")
    lines.append("- Composite DC20 (IS 1.52 / OOS 1.11 / MDD -22.9% / -11.3%)과 병행 운용 시 분산 효과 확인\n")

    lines.append("---\n")

    # 테이블 1: 9종 변형 비교
    lines.append("## 테이블 1: 9종 변형 비교\n")
    lines.append("> 판정 기준: OOS Sharpe >= 1.0 AND IS MDD >= -30%\n")

    # 열 순서 맞춘 DataFrame
    t1_cols = ["변형", "EMA", "Trail(%)", "추가필터", "IS Sharpe", "IS MDD(%)", "OOS Sharpe", "OOS MDD(%)", "거래수(IS)", "승률(IS,%)", "판정"]
    t1_df   = screening_df[t1_cols].copy()
    lines.append(_df_to_md(t1_df))

    # Composite DC20 기준선 추가 행 (메모)
    lines.append(f"> **Composite DC20 기준**: IS Sharpe 1.52 / IS MDD -22.9% / OOS Sharpe 1.11 / OOS MDD -11.3%\n")

    # 최선 변형 강조
    lines.append(f"**최선 변형: {best_name}** (EMA{best_row['EMA']} Trail{best_row['Trail(%)']}%"
                 + (" +EMA200필터" if best_row['추가필터'] == "EMA200" else "") + ")\n")

    lines.append("---\n")

    # 테이블 2: 최선 변형 레짐별 성과
    lines.append(f"## 테이블 2: 최선 변형 레짐별 성과 — {best_name}\n")

    lines.append("### IS 기간 (2018-06-01 ~ 2023-12-31)\n")
    if len(regime_df_result) > 0:
        lines.append(_df_to_md(regime_df_result))
    else:
        lines.append("(거래 없음)\n")

    lines.append("### OOS 기간 (2024-01-01 ~ 2026-04-05)\n")
    if len(regime_oos_result) > 0:
        lines.append(_df_to_md(regime_oos_result))
    else:
        lines.append("(거래 없음)\n")

    lines.append("---\n")

    # 테이블 3: Composite DC20과 상관관계
    lines.append("## 테이블 3: Composite DC20과 상관관계\n")
    lines.append(f"> 최선 변형 ({best_name})과 Composite DC20 비교\n")
    lines.append(_df_to_md(corr_df))

    lines.append("")

    # 상관계수 해석
    corr_is_val  = corr_is.get("일별수익률 상관계수", "-")
    corr_oos_val = corr_oos.get("일별수익률 상관계수", "-")
    sim_is   = corr_is.get("동시보유비율(%)", "-")
    sim_oos  = corr_oos.get("동시보유비율(%)", "-")

    lines.append("**해석 가이드**\n")
    lines.append("| 상관계수 범위 | 분산 효과 |")
    lines.append("| --- | --- |")
    lines.append("| < 0.3 | 높음 — 독립적 움직임 |")
    lines.append("| 0.3 ~ 0.6 | 보통 — 부분적 분산 |")
    lines.append("| > 0.6 | 낮음 — 유사한 움직임 |")
    lines.append("")

    lines.append("---\n")

    # 최종 판정
    lines.append("## 최종 판정\n")

    # 독립 운용 가능성
    best_oos_sharpe = best_row["OOS Sharpe"]
    best_is_mdd     = best_row["IS MDD(%)"]
    best_oos_mdd    = best_row["OOS MDD(%)"]

    solo_ok = (
        isinstance(best_oos_sharpe, (int, float)) and best_oos_sharpe >= PASS_SHARPE and
        isinstance(best_is_mdd, (int, float)) and best_is_mdd >= PASS_MDD
    )

    lines.append("### 독립 운용 가능성\n")
    lines.append(f"최선 변형: **{best_name}** (EMA{best_row['EMA']} Trail{best_row['Trail(%)']}%)\n")
    lines.append(f"| 지표 | 값 | 기준 | 통과 |")
    lines.append(f"| --- | --- | --- | --- |")

    def pass_icon(cond):
        return "YES" if cond else "NO"

    lines.append(f"| OOS Sharpe | {best_oos_sharpe} | >= {PASS_SHARPE} | {pass_icon(isinstance(best_oos_sharpe, (int,float)) and best_oos_sharpe >= PASS_SHARPE)} |")
    lines.append(f"| IS MDD | {best_is_mdd}% | >= {PASS_MDD}% | {pass_icon(isinstance(best_is_mdd, (int,float)) and best_is_mdd >= PASS_MDD)} |")
    lines.append(f"| OOS MDD | {best_oos_mdd}% | >= -20% | {pass_icon(isinstance(best_oos_mdd, (int,float)) and best_oos_mdd >= -20.0)} |")
    lines.append("")

    if solo_ok:
        lines.append("**독립 운용 판정: PASS** — Sharpe 및 MDD 기준 통과, 단독 전략으로 채택 가능\n")
    else:
        lines.append("**독립 운용 판정: FAIL** — OOS Sharpe 또는 IS MDD 기준 미달, 단독 운용 비권장\n")

    # 병행 운용 분산 효과
    lines.append("### Composite DC20과 병행 시 분산 효과\n")

    if isinstance(corr_is_val, (int, float)) and isinstance(corr_oos_val, (int, float)):
        avg_corr = (corr_is_val + corr_oos_val) / 2
        if avg_corr < 0.3:
            div_verdict = "높음 — 포트폴리오 분산 효과 우수"
        elif avg_corr < 0.6:
            div_verdict = "보통 — 부분적 분산 효과, 함께 운용 시 리스크 완화"
        else:
            div_verdict = "낮음 — 유사한 움직임, 추가 분산 효과 제한적"
    else:
        div_verdict = "계산 불가"

    lines.append(f"- IS 상관계수: **{corr_is_val}**, OOS 상관계수: **{corr_oos_val}**")
    lines.append(f"- IS 동시보유: **{sim_is}%**, OOS 동시보유: **{sim_oos}%**")
    lines.append(f"- **분산 효과: {div_verdict}**\n")

    # 전체 변형 결과 요약
    pass_variants = screening_df[screening_df["판정"] == "PASS"]
    lines.append("### 9종 변형 요약\n")
    lines.append(f"- PASS 변형 수: **{len(pass_variants)}종** / 전체 9종")
    if len(pass_variants) > 0:
        for _, row in pass_variants.iterrows():
            lines.append(f"  - {row['변형']}: OOS Sharpe {row['OOS Sharpe']}, IS MDD {row['IS MDD(%)']}%, OOS MDD {row['OOS MDD(%)']}%")
    lines.append("")

    # 기존 기준선 대비 개선 여부
    lines.append("### 기존 전략 A (EMA50 Trail5%) 대비 개선 여부\n")
    base_row = screening_df[screening_df["변형"] == "기준선"]
    if len(base_row) > 0:
        b = base_row.iloc[0]
        lines.append(f"| 항목 | 기존(EMA50 Trail5%) | 최선({best_name}) | 개선 |")
        lines.append(f"| --- | --- | --- | --- |")
        lines.append(f"| IS Sharpe | {b['IS Sharpe']} | {best_row['IS Sharpe']} | {'YES' if best_row['IS Sharpe'] >= b['IS Sharpe'] else 'NO'} |")
        lines.append(f"| IS MDD(%) | {b['IS MDD(%)']}% | {best_row['IS MDD(%)']}% | {'YES (개선)' if best_row['IS MDD(%)'] > b['IS MDD(%)'] else 'NO'} |")
        lines.append(f"| OOS Sharpe | {b['OOS Sharpe']} | {best_row['OOS Sharpe']} | {'YES' if best_row['OOS Sharpe'] >= b['OOS Sharpe'] else 'NO'} |")
        lines.append(f"| OOS MDD(%) | {b['OOS MDD(%)']}% | {best_row['OOS MDD(%)']}% | {'YES (개선)' if best_row['OOS MDD(%)'] > b['OOS MDD(%)'] else 'NO'} |")
    lines.append("")

    lines.append("---")
    lines.append("*자동 생성: scripts/backtest_ema_deep_screening.py*")

    report_text = "\n".join(lines)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report_text, encoding="utf-8")
    print(f"\n  보고서 저장: {OUTPUT_MD}")

    # 콘솔 요약
    print("\n" + "=" * 70)
    print("심화 검증 완료 요약")
    print("=" * 70)
    print(f"{'변형':<22} {'IS Sharpe':>10} {'IS MDD':>8} {'OOS Sharpe':>11} {'OOS MDD':>9} {'판정':>6}")
    print("-" * 70)
    print(f"{'[기준] Composite DC20':<22} {'1.52':>10} {'-22.9%':>8} {'1.11':>11} {'-11.3%':>9} {'기준선':>6}")
    for _, row in screening_df.iterrows():
        print(
            f"{row['변형']:<22} "
            f"{str(row['IS Sharpe']):>10} "
            f"{str(row['IS MDD(%)'])+'%':>8} "
            f"{str(row['OOS Sharpe']):>11} "
            f"{str(row['OOS MDD(%)'])+'%':>9} "
            f"{row['판정']:>6}"
        )

    print(f"\n최선 변형: {best_name}")
    print(f"산출물:    {OUTPUT_MD}")


if __name__ == "__main__":
    main()
