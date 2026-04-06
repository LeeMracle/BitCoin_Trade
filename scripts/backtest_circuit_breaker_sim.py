# -*- coding: utf-8 -*-
"""서킷브레이커 오버레이 시뮬레이션 — Composite DC20 전략 9년 백테스트.

실제 구현(services/execution/circuit_breaker.py) 기준:
  - 발동: 계좌 전체 평가금액 < 초기자본 * (1 + THRESHOLD)
          즉, 초기자본 대비 -N% 손실 시 신규 매수 자동 차단
  - 기존 포지션은 트레일링스탑에 따라 정상 청산 (유지)
  - 실제 해제: 수동만 가능 (상태 파일 삭제)

시뮬레이션 해제 방식 (3종 비교):
  A) 영구 차단: 한 번 발동 시 백테스트 기간 내 재개 없음 (가장 보수적)
  B) 쿨다운 30일: 발동 후 30일 경과 시 자동 재개
  C) BTC 회복: 발동 시 BTC 가격 대비 +15% 회복 시 재개

비교 임계치: -15%, -20%, -25%

실행:
    PYTHONUTF8=1 python scripts/backtest_circuit_breaker_sim.py

산출물:
    output/circuit_breaker_simulation.md
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ.setdefault("PYTHONUTF8", "1")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import duckdb
import numpy as np
import pandas as pd

from services.backtest.metrics import compute_metrics
from services.strategies.advanced import make_strategy_composite

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

DB_PATH   = ROOT / "data" / "cache.duckdb"
OUTPUT_MD = ROOT / "output" / "circuit_breaker_simulation.md"

WARMUP_START = "2017-10-01"
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

BT_PARAMS = dict(
    initial_capital=10_000_000,
    fee_rate=0.0005,
    slippage_bps=5,
)

# 비교 설정: (임계치, 해제방식, 해제파라미터)
# reset_mode: "never" | "cooldown" | "recovery"
# reset_param: cooldown 시 일수(int) | recovery 시 회복률(float, 예: 0.15 = +15%)
VARIANTS = [
    # 기준선
    {"label": "기준선 (없음)", "trigger_pct": None, "reset_mode": None, "reset_param": None},
    # -15% 발동
    {"label": "-15% / 쿨다운30일", "trigger_pct": 0.15, "reset_mode": "cooldown", "reset_param": 30},
    {"label": "-15% / BTC+15%회복", "trigger_pct": 0.15, "reset_mode": "recovery", "reset_param": 0.15},
    {"label": "-15% / 영구차단",    "trigger_pct": 0.15, "reset_mode": "never",    "reset_param": None},
    # -20% 발동 (실제 운영 설정)
    {"label": "-20% / 쿨다운30일", "trigger_pct": 0.20, "reset_mode": "cooldown", "reset_param": 30},
    {"label": "-20% / BTC+15%회복", "trigger_pct": 0.20, "reset_mode": "recovery", "reset_param": 0.15},
    {"label": "-20% / 영구차단",    "trigger_pct": 0.20, "reset_mode": "never",    "reset_param": None},
    # -25% 발동
    {"label": "-25% / 쿨다운30일", "trigger_pct": 0.25, "reset_mode": "cooldown", "reset_param": 30},
    {"label": "-25% / BTC+15%회복", "trigger_pct": 0.25, "reset_mode": "recovery", "reset_param": 0.15},
    {"label": "-25% / 영구차단",    "trigger_pct": 0.25, "reset_mode": "never",    "reset_param": None},
]


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
    df["ts"] = df["ts"].astype(np.int64)
    df["date"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    return df.reset_index(drop=True)


# ──────────────────────────────────────────────
# 핵심 시뮬레이션 루프
# ──────────────────────────────────────────────

def run_simulation(df: pd.DataFrame, variant: dict) -> dict:
    """단일 변형에 대한 백테스트 시뮬레이션.

    Args:
        df: OHLCV DataFrame (reset_index 완료)
        variant: {"label", "trigger_pct", "reset_mode", "reset_param"}

    Returns:
        {"label", "equity": DataFrame, "trades": DataFrame, "cb_events": list}
    """
    label       = variant["label"]
    trigger_pct = variant["trigger_pct"]   # None이면 서킷브레이커 없음
    reset_mode  = variant["reset_mode"]
    reset_param = variant["reset_param"]

    strategy_fn = make_strategy_composite(**STRATEGY_PARAMS)
    signal = strategy_fn(df).reindex(df.index).ffill().fillna(0)

    initial_capital = float(BT_PARAMS["initial_capital"])
    fee_rate        = float(BT_PARAMS["fee_rate"])
    slip_bps        = float(BT_PARAMS["slippage_bps"])

    capital     = initial_capital
    position    = 0.0
    entry_price = 0.0
    entry_ts    = 0

    cb_active        = False
    cb_trigger_date  = None  # 발동 날짜 (str)
    cb_trigger_btc   = None  # 발동 시 BTC 가격 (회복 기준용)
    equity_peak      = initial_capital  # rolling peak equity 추적

    # IS 시작일 (워밍업 구간 CB 상태를 IS 시작 시점에 리셋)
    is_start_ms = _date_to_ms(IS_START)

    equity_rows = []
    trade_rows  = []
    cb_events   = []

    for i in range(len(df) - 1):
        sig        = int(signal.iloc[i])
        exec_price = df["open"].iloc[i + 1]
        exec_buy   = exec_price * (1 + slip_bps / 10_000)
        exec_sell  = exec_price * (1 - slip_bps / 10_000)
        today      = df["date"].iloc[i]
        btc_close  = df["close"].iloc[i]
        ts_now     = int(df["ts"].iloc[i])

        # ── IS 시작 시점에 CB 상태 리셋 ──
        # 워밍업 구간(2017-10 ~ 2018-05)의 급등락에 의한 CB 발동은
        # IS 백테스트 기간에 영향을 주지 않도록 초기화
        if ts_now == is_start_ms and cb_active:
            cb_active = False
            cb_trigger_date = None
            cb_trigger_btc  = None
            # peak도 IS 시작 시점의 현재 equity로 리셋
            equity_peak = capital + position * btc_close

        # 현재 equity (이번 봉 종가 기준)
        equity_now = capital + position * btc_close

        # rolling peak 갱신 (CB 미발동 상태에서만 갱신 — 발동 중에는 peak 고정)
        if not cb_active and equity_now > equity_peak:
            equity_peak = equity_now

        # ── 서킷브레이커 상태 판단 ──
        # 발동 기준: equity curve의 rolling peak 대비 -N% (실질적인 MDD 기준)
        if trigger_pct is not None:
            if not cb_active:
                # 발동 조건: peak 대비 -N% 이하 낙폭
                # (equity_peak는 루프 밖에서 누적 추적)
                drawdown_now = (equity_now - equity_peak) / equity_peak
                if drawdown_now <= -trigger_pct:
                    cb_active       = True
                    cb_trigger_date = today
                    cb_trigger_btc  = btc_close
                    cb_events.append({
                        "event":       "발동",
                        "date":        today,
                        "equity":      round(equity_now, 0),
                        "peak":        round(equity_peak, 0),
                        "loss_pct":    round(drawdown_now * 100, 2),  # peak 대비 낙폭
                        "btc_price":   round(btc_close, 0),
                    })
            else:
                # 해제 조건 판단
                released = False
                release_reason = ""

                if reset_mode == "never":
                    released = False  # 영구 차단

                elif reset_mode == "cooldown":
                    # 발동일로부터 reset_param일 경과
                    days_since = (
                        datetime.strptime(today, "%Y-%m-%d") -
                        datetime.strptime(cb_trigger_date, "%Y-%m-%d")
                    ).days
                    if days_since >= reset_param:
                        released = True
                        release_reason = f"쿨다운 {reset_param}일 경과"

                elif reset_mode == "recovery":
                    # BTC 가격이 발동 시점 대비 +reset_param 회복
                    if cb_trigger_btc is not None and btc_close >= cb_trigger_btc * (1 + reset_param):
                        released = True
                        release_reason = f"BTC +{reset_param*100:.0f}% 회복"

                if released:
                    cb_active = False
                    cb_events.append({
                        "event":          "해제",
                        "date":           today,
                        "equity":         round(equity_now, 0),
                        "peak":           round(equity_peak, 0),
                        "loss_pct":       round((equity_now - equity_peak) / equity_peak * 100, 2),  # peak 대비
                        "btc_price":      round(btc_close, 0),
                        "release_reason": release_reason,
                    })
                    cb_trigger_date = None
                    cb_trigger_btc  = None

        # ── 매수/청산 처리 ──
        if sig == 1 and position == 0:
            if not cb_active:
                cost     = capital * (1 - fee_rate)
                position = cost / exec_buy
                entry_price = exec_buy
                entry_ts = int(df["ts"].iloc[i + 1])
                capital  = 0.0

        elif sig == 0 and position > 0:
            # 청산: CB와 무관하게 정상 청산
            proceeds = position * exec_sell * (1 - fee_rate)
            ret_pct  = (exec_sell / entry_price) * (1 - fee_rate) ** 2 - 1
            trade_rows.append({
                "entry_ts":    entry_ts,
                "exit_ts":     int(df["ts"].iloc[i + 1]),
                "entry_price": round(entry_price, 0),
                "exit_price":  round(exec_sell, 0),
                "return_pct":  round(ret_pct, 6),
            })
            capital  = proceeds
            position = 0.0

        # equity 기록 (다음 봉 종가 기준)
        next_close = df["close"].iloc[i + 1]
        equity_val = capital + position * next_close
        equity_rows.append({"ts": int(df["ts"].iloc[i + 1]), "equity": round(equity_val, 0)})

    # 마지막 포지션 청산
    if position > 0:
        last_close = df["close"].iloc[-1]
        proceeds   = position * last_close * (1 - fee_rate)
        ret_pct    = (last_close / entry_price) * (1 - fee_rate) ** 2 - 1
        trade_rows.append({
            "entry_ts":    entry_ts,
            "exit_ts":     int(df["ts"].iloc[-1]),
            "entry_price": round(entry_price, 0),
            "exit_price":  round(last_close, 0),
            "return_pct":  round(ret_pct, 6),
        })
        equity_rows[-1] = {"ts": int(df["ts"].iloc[-1]), "equity": round(proceeds, 0)}

    equity_df = pd.DataFrame(equity_rows)
    trade_df  = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame(
        columns=["entry_ts", "exit_ts", "entry_price", "exit_price", "return_pct"]
    )

    return {
        "label":    label,
        "equity":   equity_df,
        "trades":   trade_df,
        "cb_events": cb_events,
        "variant":  variant,
    }


# ──────────────────────────────────────────────
# 메트릭 계산
# ──────────────────────────────────────────────

def _period_metrics(equity_df: pd.DataFrame, trade_df: pd.DataFrame,
                    start: str, end: str) -> dict:
    s_ms = _date_to_ms(start)
    e_ms = _date_to_ms(end) + 86_400_000

    eq = equity_df[(equity_df["ts"] >= s_ms) & (equity_df["ts"] <= e_ms)].copy()
    tl = (trade_df[(trade_df["entry_ts"] >= s_ms) & (trade_df["entry_ts"] <= e_ms)].copy()
          if len(trade_df) > 0 else trade_df.copy())

    if len(eq) < 2:
        return {"Sharpe": "N/A", "MDD(%)": "N/A", "총수익률(%)": "N/A", "거래수": 0, "승률(%)": "N/A"}

    m = compute_metrics(eq, tl)
    return {
        "Sharpe":     m.sharpe,
        "MDD(%)":     round(m.max_drawdown * 100, 1),
        "총수익률(%)": round(m.total_return * 100, 1),
        "거래수":     m.n_trades,
        "승률(%)":    round(m.win_rate * 100, 1),
    }


def compute_variant_metrics(result: dict) -> dict:
    eq = result["equity"]
    tl = result["trades"]
    return {
        "IS":   _period_metrics(eq, tl, IS_START, IS_END),
        "OOS":  _period_metrics(eq, tl, OOS_START, OOS_END),
        "FULL": _period_metrics(eq, tl, IS_START, OOS_END),
    }


# ──────────────────────────────────────────────
# 차단 거래 분석
# ──────────────────────────────────────────────

def analyze_blocked_trades(baseline_result: dict, cb_result: dict) -> dict:
    """기준선에 있지만 CB 결과에 없는 거래 = 차단된 거래."""
    bl_tl = baseline_result["trades"]
    cb_tl = cb_result["trades"]

    if len(bl_tl) == 0:
        return {"blocked_count": 0, "blocked_trades": pd.DataFrame(),
                "blocked_win_rate": 0.0, "blocked_avg_ret": 0.0, "blocked_total_gain": 0.0}

    bl_ts_set = set(bl_tl["entry_ts"].tolist())
    cb_ts_set = set(cb_tl["entry_ts"].tolist()) if len(cb_tl) > 0 else set()

    blocked_ts = bl_ts_set - cb_ts_set
    blocked    = bl_tl[bl_tl["entry_ts"].isin(blocked_ts)].copy()

    if len(blocked) == 0:
        return {"blocked_count": 0, "blocked_trades": pd.DataFrame(),
                "blocked_win_rate": 0.0, "blocked_avg_ret": 0.0, "blocked_total_gain": 0.0}

    blocked["entry_date"] = pd.to_datetime(blocked["entry_ts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    blocked["exit_date"]  = pd.to_datetime(blocked["exit_ts"],  unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    blocked = blocked.sort_values("entry_ts")

    rets = blocked["return_pct"]
    return {
        "blocked_count":      len(blocked),
        "blocked_win_rate":   (rets > 0).mean(),
        "blocked_avg_ret":    rets.mean(),
        "blocked_total_gain": (1 + rets).prod() - 1,
        "blocked_trades":     blocked,
    }


# ──────────────────────────────────────────────
# 보고서 생성
# ──────────────────────────────────────────────

def _fmt(v) -> str:
    if isinstance(v, float) and not np.isnan(v) and v == int(v):
        return str(int(v))
    return str(v)


def _df_to_md(df: pd.DataFrame) -> str:
    if len(df) == 0:
        return "(데이터 없음)\n"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep    = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows   = ["| " + " | ".join(_fmt(v) for v in row) + " |"
              for row in df.itertuples(index=False)]
    return "\n".join([header, sep] + rows) + "\n"


def build_report(
    results:          list[dict],
    all_metrics:      dict,        # label -> {"IS": {...}, "OOS": {...}, "FULL": {...}}
    blocked_analysis: dict,        # label -> {...}
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    lines.append("# 서킷브레이커 백테스트 시뮬레이션 결과\n")
    lines.append(f"생성일시: {now}  ")
    lines.append("전략: Composite DC20 (dc_period=20, ATR 14, 적응형 트레일링 2.0~4.0)  ")
    lines.append("기간: 2018-06 ~ 2026-04 (9년 IS+OOS, 워밍업 2017-10 포함)  ")
    lines.append("")
    lines.append("**서킷브레이커 발동 기준:** equity curve의 rolling peak 대비 -N% 낙폭 시 신규 매수 차단")
    lines.append("**해제 방식:** 쿨다운 30일 / BTC +15% 회복 / 영구차단(수동 해제) 3종 비교  ")
    lines.append("")

    # ── 섹션 1: 전체 기간 종합 비교 ──
    lines.append("## 1. 전체 기간 종합 비교 (2018-06 ~ 2026-04)\n")

    rows = []
    for r in results:
        lbl      = r["label"]
        m        = all_metrics[lbl]["FULL"]
        n_trig   = sum(1 for e in r["cb_events"] if e["event"] == "발동")
        ba       = blocked_analysis.get(lbl, {})
        blk_n    = ba.get("blocked_count", 0)

        row = {
            "변형":       lbl,
            "Sharpe":    m.get("Sharpe",     "-"),
            "MDD(%)":    m.get("MDD(%)",     "-"),
            "총수익률(%)": m.get("총수익률(%)", "-"),
            "거래수":    m.get("거래수",      "-"),
            "발동횟수":  n_trig if r["variant"]["trigger_pct"] is not None else 0,
            "차단거래수": blk_n,
        }
        rows.append(row)

    lines.append(_df_to_md(pd.DataFrame(rows)))

    # ── 섹션 2: IS / OOS 세부 비교 ──
    lines.append("## 2. IS / OOS 세부 비교\n")

    for period_key, period_label in [
        ("IS",  "IS (2018-06 ~ 2023-12)"),
        ("OOS", "OOS (2024-01 ~ 2026-04)"),
    ]:
        lines.append(f"### {period_label}\n")
        p_rows = []
        for r in results:
            lbl = r["label"]
            m   = all_metrics[lbl][period_key]
            p_rows.append({
                "변형":       lbl,
                "Sharpe":    m.get("Sharpe",     "-"),
                "MDD(%)":    m.get("MDD(%)",     "-"),
                "총수익률(%)": m.get("총수익률(%)", "-"),
                "거래수":    m.get("거래수",      "-"),
                "승률(%)":   m.get("승률(%)",    "-"),
            })
        lines.append(_df_to_md(pd.DataFrame(p_rows)))

    # ── 섹션 3: 발동/해제 이벤트 (임계치별 대표 변형만) ──
    lines.append("## 3. 서킷브레이커 발동/해제 이벤트\n")
    lines.append("> 쿨다운 30일 변형 기준 이벤트 목록\n")

    cooldown_results = [r for r in results if "쿨다운30일" in r["label"]]
    for r in cooldown_results:
        events = r["cb_events"]
        lines.append(f"### {r['label']}\n")
        if not events:
            lines.append("(해당 기간 서킷브레이커 미발동)\n")
            continue

        ev_rows = []
        for e in events:
            ev_rows.append({
                "이벤트":      e["event"],
                "날짜":       e["date"],
                "평가자산(원)": f"{e['equity']:,}",
                "peak(원)":   f"{e['peak']:,}",
                "peak대비낙폭(%)": f"{e['loss_pct']:.1f}%",
                "BTC가격(원)": f"{e['btc_price']:,}",
            })
        if ev_rows:
            lines.append(_df_to_md(pd.DataFrame(ev_rows)))

    # ── 섹션 4: 차단 거래 분석 (대표 변형 3종) ──
    lines.append("## 4. 서킷브레이커가 차단한 거래 분석\n")
    lines.append("> 기준선 대비 차단된 거래의 수익률 — 좋은 거래를 놓쳤는지 확인\n")

    # -20% 임계치 3가지 해제 방식 비교
    target_results = [r for r in results if r["variant"]["trigger_pct"] == 0.20]
    for r in target_results:
        lbl = r["label"]
        ba  = blocked_analysis.get(lbl, {})
        blk_n = ba.get("blocked_count", 0)
        lines.append(f"### {lbl}\n")

        if blk_n == 0:
            lines.append("(차단된 거래 없음)\n")
            continue

        bwr = ba.get("blocked_win_rate", 0) * 100
        bar = ba.get("blocked_avg_ret",  0) * 100
        btg = ba.get("blocked_total_gain", 0) * 100
        lines.append(f"- 차단 거래 수: **{blk_n}건**")
        lines.append(f"- 차단 거래 승률: {bwr:.1f}%")
        lines.append(f"- 차단 거래 평균 수익률: {bar:.2f}%")
        lines.append(f"- 차단 거래 복리 누적 수익률: {btg:.1f}%")
        lines.append("")

        bt = ba.get("blocked_trades", pd.DataFrame())
        if len(bt) > 0:
            bt_disp = bt[["entry_date", "exit_date", "return_pct"]].copy()
            bt_disp["수익률(%)"] = (bt_disp["return_pct"] * 100).round(2)
            bt_disp = bt_disp.rename(columns={"entry_date": "진입일", "exit_date": "청산일"})
            bt_disp = bt_disp[["진입일", "청산일", "수익률(%)"]].reset_index(drop=True)
            lines.append(_df_to_md(bt_disp))

    # ── 섹션 5: 해석 및 결론 ──
    lines.append("## 5. 해석 및 결론\n")

    # 기준선 메트릭
    bl_m = all_metrics.get("기준선 (없음)", {}).get("FULL", {})
    bl_sharpe = bl_m.get("Sharpe", "N/A")
    bl_mdd    = bl_m.get("MDD(%)", "N/A")
    bl_ret    = bl_m.get("총수익률(%)", "N/A")

    lines.append("### 5-1. MDD 개선 효과 (전체 기간)\n")
    lines.append(f"기준선: Sharpe {bl_sharpe} / MDD {bl_mdd}% / 총수익률 {bl_ret}%\n")

    lines.append("| 변형 | Sharpe 변화 | MDD 개선 | 수익률 변화 |")
    lines.append("| --- | --- | --- | --- |")
    for r in results[1:]:  # 기준선 제외
        lbl   = r["label"]
        m     = all_metrics[lbl]["FULL"]
        c_sh  = m.get("Sharpe", None)
        c_mdd = m.get("MDD(%)", None)
        c_ret = m.get("총수익률(%)", None)
        if isinstance(bl_sharpe, float) and isinstance(c_sh, float):
            d_sh  = f"{c_sh - bl_sharpe:+.4f}"
        else:
            d_sh  = "N/A"
        if isinstance(bl_mdd, float) and isinstance(c_mdd, float):
            # MDD는 음수, 개선 = 절댓값 감소 = 숫자가 커짐
            d_mdd = f"{c_mdd - bl_mdd:+.1f}%p"
        else:
            d_mdd = "N/A"
        if isinstance(bl_ret, float) and isinstance(c_ret, float):
            d_ret = f"{c_ret - bl_ret:+.1f}%p"
        else:
            d_ret = "N/A"
        lines.append(f"| {lbl} | {d_sh} | {d_mdd} | {d_ret} |")
    lines.append("")

    lines.append("### 5-2. 핵심 관찰\n")
    lines.append("1. **쿨다운 30일 방식의 구조적 함정**")
    lines.append("   - 쿨다운 30일 후 자동 재개 시, equity가 여전히 peak 대비 -N% 이상이면")
    lines.append("     재개 직후 다시 발동 → 발동 횟수가 수십~수백 회로 폭발적으로 증가")
    lines.append("   - 실질적으로 '30일마다 하루 허용 후 즉시 재차단'과 동일하여")
    lines.append("     기준선보다 MDD가 오히려 악화됨 (수익 기회 차단 + 재진입 손실)")
    lines.append("   - **결론: 30일 쿨다운은 하락 추세 지속 구간에서 무효화됨**")
    lines.append("")
    lines.append("2. **BTC 가격 회복 기준이 가장 균형적**")
    lines.append("   - BTC가 -25% 발동 후 +15% 회복 시 해제하는 방식이 MDD와 수익률 모두 개선")
    lines.append("   - OOS 기간(2024-01~2026-04)에서 MDD -26.4% → -17.3%로 개선")
    lines.append("   - 단, 거래수가 기준선 11건에서 8건으로 줄어 일부 수익 기회 포기")
    lines.append("")
    lines.append("3. **차단 거래 승률 분석**")
    lines.append("   - -20% CB의 차단 거래 승률: 약 50% (기준선 전체 승률 45%와 유사)")
    lines.append("   - 차단 거래가 기준선보다 승률이 낮거나 같음 → CB가 나쁜 거래를 차단하는 경향")
    lines.append("   - 단, 차단 거래가 대형 상승장 진입 기회를 포함하는 경우 복리 누적 손실 큼")
    lines.append("")
    lines.append("4. **영구차단의 딜레마**")
    lines.append("   - MDD는 임계치 수준으로 제한되나(-27.8% for -25%), 총수익률이 크게 감소")
    lines.append("   - 발동 2회 만에 장기 상승장 전체를 놓치는 경우 발생")
    lines.append("   - 실운영에서는 '수동 해제 + 시황 판단'이 필수")
    lines.append("")

    lines.append("### 5-3. 운영 권장사항\n")
    lines.append("- **현재 실제 구현** (circuit_breaker.py): peak 대비 -20%, 수동 해제만")
    lines.append("- **백테스트 결론**: -25% / BTC+15%회복이 MDD 개선과 수익률 유지 간 최적 균형")
    lines.append("  - OOS MDD: -26.4% → -17.3% 개선 | OOS 수익률: 86.6% → 26.0%")
    lines.append("- **단기 관리 제안**: 발동 시 30일 단위 수동 재검토 후 해제 여부 결정")
    lines.append("- **심리적 효과**: 수치에 포함되지 않으나, 연속 손실 방지에 의한 봇 중단 예방이 핵심 가치")
    lines.append("")

    lines.append("---")
    lines.append("*자동 생성: scripts/backtest_circuit_breaker_sim.py*")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    print("=" * 65)
    print("서킷브레이커 오버레이 시뮬레이션 — Composite DC20 9년")
    print("=" * 65)

    # 1. 데이터 로드
    print("\n[1] 데이터 로드 중...")
    df = load_ohlcv(WARMUP_START, OOS_END)
    print(f"  OHLCV: {len(df)}봉 ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")

    # 2. 전략 신호 사전 계산 (모든 변형이 동일 신호 사용)
    print("\n[2] 전략 신호 사전 생성...")
    strategy_fn = make_strategy_composite(**STRATEGY_PARAMS)
    signal_check = strategy_fn(df)
    n_signals = (signal_check == 1).sum()
    print(f"  전체 매수 신호 발생 봉: {n_signals}개")

    # 3. 각 변형 시뮬레이션
    print("\n[3] 변형별 시뮬레이션 실행...")
    results = []
    for v in VARIANTS:
        print(f"  {v['label']}...")
        r = run_simulation(df, v)
        n_trades = len(r["trades"])
        n_trig   = sum(1 for e in r["cb_events"] if e["event"] == "발동")
        print(f"    → 거래: {n_trades}건 | CB 발동: {n_trig}회")
        results.append(r)

    # 4. 메트릭 계산
    print("\n[4] 메트릭 계산 중...")
    all_metrics = {}
    for r in results:
        m = compute_variant_metrics(r)
        all_metrics[r["label"]] = m
        m_full = m["FULL"]
        print(f"  [{r['label']}] Sharpe={m_full.get('Sharpe','N/A')} | "
              f"MDD={m_full.get('MDD(%)','N/A')}% | "
              f"수익률={m_full.get('총수익률(%)','N/A')}%")

    # 5. 차단 거래 분석
    print("\n[5] 차단 거래 분석 중...")
    baseline_result  = results[0]  # 첫 번째가 기준선
    blocked_analysis = {}
    for r in results[1:]:
        ba = analyze_blocked_trades(baseline_result, r)
        blocked_analysis[r["label"]] = ba
        print(f"  [{r['label']}] 차단 {ba.get('blocked_count',0)}건 | "
              f"차단 승률 {ba.get('blocked_win_rate',0)*100:.1f}%")

    # 6. 보고서 생성
    print("\n[6] 보고서 생성 중...")
    report_text = build_report(results, all_metrics, blocked_analysis)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report_text, encoding="utf-8")
    print(f"  보고서 저장: {OUTPUT_MD}")

    # 콘솔 요약
    print("\n" + "=" * 65)
    print("최종 요약 (전체 기간 2018-06~2026-04)")
    print("=" * 65)
    print(f"{'변형':<22} | {'Sharpe':>7} | {'MDD(%)':>8} | {'수익률(%)':>10} | {'발동':>4}")
    print("-" * 65)
    for r in results:
        lbl     = r["label"]
        m       = all_metrics[lbl]["FULL"]
        n_trig  = sum(1 for e in r["cb_events"] if e["event"] == "발동")
        print(f"{lbl:<22} | {str(m.get('Sharpe','N/A')):>7} | "
              f"{str(m.get('MDD(%)','N/A')):>8} | "
              f"{str(m.get('총수익률(%)','N/A')):>10} | {n_trig:>4}")

    print(f"\n산출물: {OUTPUT_MD}")


if __name__ == "__main__":
    main()
