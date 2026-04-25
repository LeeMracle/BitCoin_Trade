# -*- coding: utf-8 -*-
"""신규매수 게이트 임계값/해제조건 검증 백테스트.

plan: workspace/plans/20260425_buy_gate_threshold_validation.md

6개 시나리오를 동일 composite DC20 전략에 entry_mask로 주입하여 비교.

  S1: EMA200 필터 ON (현행) — close < EMA200 시 진입 차단
  S2: EMA150 필터 ON           — close < EMA150 시 진입 차단
  S3: SMA50  필터 ON           — close < SMA50  시 진입 차단
  S4: 필터 OFF (Baseline)      — 게이트 없음
  S5: 완화 OR — close > EMA200 OR F&G >= 40 이면 통과 (한쪽만 만족해도 OPEN)
  S6: 강화 3일연속 — close > EMA200 이 3일 연속 충족된 시점부터 진입 허용

기간:
  워밍업  2017-10-01 ~ 2018-05-31 (신호 안정화)
  IS      2018-06-01 ~ 2023-12-31
  OOS     2024-01-01 ~ 2026-04-25 (전체)
  BEAR  : 동일 OOS 기간 중 close < EMA200 인 봉만 별도 추출 (메트릭 분리)

실행:
  PYTHONUTF8=1 python scripts/backtest_buy_gate_validation.py

산출:
  output/buy_gate_validation_summary.json
  output/buy_gate_validation_summary.md (요약 표)
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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from services.backtest.engine import BacktestEngine  # noqa: E402
from services.backtest.metrics import compute_metrics  # noqa: E402
from services.backtest.models import Metrics  # noqa: E402
from services.strategies.advanced import (  # noqa: E402
    _calc_atr,
    _calc_donchian_upper,
    _calc_ema,
    _calc_rsi,
    _calc_vol_sma,
)


DB_PATH = Path(PROJECT_ROOT) / "data" / "cache.duckdb"
OUTPUT_DIR = Path(PROJECT_ROOT) / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _ts_ms(date_str: str) -> int:
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=9, minute=0, second=0, microsecond=0,
        tzinfo=datetime.timezone(datetime.timedelta(hours=9)),
    )
    return int(dt.timestamp() * 1000)


WARMUP_START = _ts_ms("2017-10-01")
IS_START = _ts_ms("2018-06-01")
IS_END = _ts_ms("2023-12-31")
OOS_START = _ts_ms("2024-01-01")
OOS_END = _ts_ms("2026-04-25")


def ts_to_date(ts_ms: int) -> str:
    dt = datetime.datetime.fromtimestamp(
        ts_ms / 1000, tz=datetime.timezone(datetime.timedelta(hours=9))
    )
    return dt.strftime("%Y-%m-%d")


def load_btc() -> pd.DataFrame:
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
    return df.set_index("date")["value"]


# ──────────────────────────────────────────────
# Composite DC20 전략 — entry_mask 주입 가능 버전
# ──────────────────────────────────────────────
def make_composite_with_mask(
    entry_mask: np.ndarray,
    dc_period: int = 20,
    rsi_period: int = 10,
    rsi_threshold: float = 50.0,
    vol_ma: int = 20,
    vol_mult: float = 1.5,
    atr_period: int = 14,
    vol_lookback: int = 60,
) -> Callable[[pd.DataFrame], pd.Series]:
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
                window = arr[i - lookback + 1:i + 1]
                valid = window[~np.isnan(window)]
                if len(valid) < lookback // 2:
                    continue
                cur = arr[i]
                if np.isnan(cur):
                    continue
                ranks[i] = np.sum(valid <= cur) / len(valid)
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

            adaptive_mult = 3.0 if np.isnan(pr) else 2.0 + 2.0 * pr

            can_enter = bool(entry_mask[i]) if entry_mask is not None else True

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
                    trail = highest - a * adaptive_mult
                    if c < trail:
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
# entry_mask 빌더 — 시나리오별
# ──────────────────────────────────────────────
def mask_all_open(n: int) -> np.ndarray:
    return np.ones(n, dtype=np.int8)


def mask_close_below_ma(close: np.ndarray, ma: np.ndarray) -> np.ndarray:
    n = len(close)
    m = np.ones(n, dtype=np.int8)
    for i in range(n):
        if not np.isnan(ma[i]) and close[i] < ma[i]:
            m[i] = 0
    return m


def mask_close_below_sma(close: np.ndarray, period: int) -> np.ndarray:
    sma = pd.Series(close).rolling(period, min_periods=period).mean().values
    return mask_close_below_ma(close, sma)


def mask_or_relaxed(
    close: np.ndarray,
    ema200: np.ndarray,
    fg_aligned: np.ndarray,
    fg_threshold: float = 40.0,
) -> np.ndarray:
    """S5: (close > EMA200) OR (F&G >= threshold) 이면 OPEN.
    F&G 결측 시 EMA 조건만 사용.
    """
    n = len(close)
    m = np.ones(n, dtype=np.int8)
    for i in range(n):
        ema_ok = (not np.isnan(ema200[i])) and close[i] > ema200[i]
        fg_ok = (not np.isnan(fg_aligned[i])) and fg_aligned[i] >= fg_threshold
        m[i] = 1 if (ema_ok or fg_ok) else 0
    return m


def mask_consec_above_ema200(
    close: np.ndarray, ema200: np.ndarray, days: int = 3
) -> np.ndarray:
    """S6: close > EMA200 가 days 일 연속 충족된 시점부터 OPEN.
    (그 이전 시점에도 close > EMA200 이라면 EMA 체크는 일반 ON. 단 days 미만 연속이면 진입 차단.)
    """
    n = len(close)
    m = np.zeros(n, dtype=np.int8)
    streak = 0
    for i in range(n):
        if not np.isnan(ema200[i]) and close[i] > ema200[i]:
            streak += 1
        else:
            streak = 0
        m[i] = 1 if streak >= days else 0
    return m


def align_fg_to_ohlcv(ohlcv: pd.DataFrame, fg: pd.Series) -> np.ndarray:
    n = len(ohlcv)
    out = np.full(n, np.nan)
    for i, ts in enumerate(ohlcv["ts"].values):
        d = ts_to_date(ts)
        if d in fg.index:
            v = fg[d]
            if not np.isnan(v):
                out[i] = float(v)
    return out


# ──────────────────────────────────────────────
# 백테스트 실행 + 메트릭 분리(IS/OOS/BEAR-OOS)
# ──────────────────────────────────────────────
def split_metrics(equity: pd.DataFrame, trades: pd.DataFrame, start_ts: int, end_ts: int):
    eq = equity[(equity["ts"] >= start_ts) & (equity["ts"] <= end_ts)].reset_index(drop=True)
    tr = (
        trades[(trades["entry_ts"] >= start_ts) & (trades["entry_ts"] <= end_ts)].copy()
        if len(trades) > 0 else trades.copy()
    )
    if len(eq) > 1:
        return compute_metrics(eq, tr), tr
    return Metrics(0, 0, 0, 0, 0, 0, 0), tr


def bear_only_metrics(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    bear_ts_set: set,
    start_ts: int,
    end_ts: int,
):
    """OOS 거래 중 진입 ts가 BEAR(close<EMA200) 일자에 속한 거래만 별도 메트릭.
    equity 곡선은 OOS 전체 유지(상위 split_metrics 결과를 함께 보고)."""
    if len(trades) == 0:
        return {"trades": 0, "win_rate": 0.0, "avg_ret": 0.0, "sum_ret": 0.0}
    tr_oos = trades[(trades["entry_ts"] >= start_ts) & (trades["entry_ts"] <= end_ts)]
    bear_trades = tr_oos[tr_oos["entry_ts"].isin(bear_ts_set)]
    if len(bear_trades) == 0:
        return {"trades": 0, "win_rate": 0.0, "avg_ret": 0.0, "sum_ret": 0.0}
    return {
        "trades": int(len(bear_trades)),
        "win_rate": float((bear_trades["return_pct"] > 0).mean()),
        "avg_ret": float(bear_trades["return_pct"].mean()),
        "sum_ret": float(bear_trades["return_pct"].sum()),
    }


def metrics_to_dict(m: Metrics) -> dict:
    return {
        "total_return_pct": float(getattr(m, "total_return", 0)) * 100.0,
        "sharpe": float(getattr(m, "sharpe", 0)),
        "max_drawdown_pct": float(getattr(m, "max_drawdown", 0)) * 100.0,
        "win_rate": float(getattr(m, "win_rate", 0)),
        "trade_count": int(getattr(m, "n_trades", 0)),
        "avg_trade_pct": float(getattr(m, "avg_trade_return", 0)) * 100.0,
        "calmar": float(getattr(m, "calmar", 0)),
    }


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    print("[1/4] 데이터 로드…")
    btc = load_btc()
    fg = load_fg()
    n = len(btc)
    close = btc["close"].values
    ema200 = _calc_ema(btc["close"], 200).values
    ema150 = _calc_ema(btc["close"], 150).values
    fg_aligned = align_fg_to_ohlcv(btc, fg)

    print(f"      BTC rows={n}, range={ts_to_date(int(btc['ts'].min()))} ~ {ts_to_date(int(btc['ts'].max()))}")
    print(f"      F&G coverage={int(np.sum(~np.isnan(fg_aligned)))}/{n}")

    # BEAR 일자 ts 집합 (OOS 기간 한정)
    bear_ts_set = set()
    for i in range(n):
        ts = int(btc["ts"].iloc[i])
        if ts < OOS_START or ts > OOS_END:
            continue
        if not np.isnan(ema200[i]) and close[i] < ema200[i]:
            bear_ts_set.add(ts)

    scenarios = {
        "S1_EMA200": mask_close_below_ma(close, ema200),
        "S2_EMA150": mask_close_below_ma(close, ema150),
        "S3_SMA50": mask_close_below_sma(close, 50),
        "S4_OFF": mask_all_open(n),
        "S5_OR_FG40": mask_or_relaxed(close, ema200, fg_aligned, fg_threshold=40.0),
        "S6_3DAY_CONSEC": mask_consec_above_ema200(close, ema200, days=3),
    }

    engine = BacktestEngine()
    results = {}

    for label, mask in scenarios.items():
        print(f"[2/4] 백테스트 {label} (OPEN 비율 {mask.mean():.3f})…")
        strat = make_composite_with_mask(entry_mask=mask)
        run = engine.run(strat, btc.copy())
        eq = run.equity_curve
        tr = run.trade_log

        is_m, _ = split_metrics(eq, tr, IS_START, IS_END)
        oos_m, oos_trades = split_metrics(eq, tr, OOS_START, OOS_END)
        bear_m = bear_only_metrics(eq, tr, bear_ts_set, OOS_START, OOS_END)

        results[label] = {
            "open_ratio": float(mask.mean()),
            "is": metrics_to_dict(is_m),
            "oos": metrics_to_dict(oos_m),
            "oos_bear_trades_only": bear_m,
        }

    # ──────────────────────────────────────────────
    # 산출물
    # ──────────────────────────────────────────────
    json_path = OUTPUT_DIR / "buy_gate_validation_summary.json"
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[3/4] JSON 저장: {json_path}")

    md_lines = [
        "# 신규매수 게이트 임계값/해제조건 검증 — 백테스트 요약",
        "",
        f"- 데이터: BTC/KRW 일봉 ({ts_to_date(int(btc['ts'].min()))} ~ {ts_to_date(int(btc['ts'].max()))})",
        f"- IS: 2018-06-01 ~ 2023-12-31 / OOS: 2024-01-01 ~ 2026-04-25",
        f"- 전략: composite DC20 (RSI10>50 OR Vol×1.5, 적응형 ATR 트레일링)",
        f"- BEAR-OOS: OOS 기간 중 close<EMA200 일자에 진입한 거래만 분리",
        "",
        "## OOS 메트릭 비교",
        "",
        "| 시나리오 | OPEN비율 | 총수익% | Sharpe | MDD% | 승률 | 거래수 | Calmar |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, r in results.items():
        oos = r["oos"]
        md_lines.append(
            f"| {label} | {r['open_ratio']:.3f} | {oos['total_return_pct']:.2f} | "
            f"{oos['sharpe']:.3f} | {oos['max_drawdown_pct']:.2f} | "
            f"{oos['win_rate']:.3f} | {oos['trade_count']} | {oos['calmar']:.3f} |"
        )

    md_lines += [
        "",
        "## IS 메트릭 비교 (참조)",
        "",
        "| 시나리오 | 총수익% | Sharpe | MDD% | 승률 | 거래수 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, r in results.items():
        is_ = r["is"]
        md_lines.append(
            f"| {label} | {is_['total_return_pct']:.2f} | {is_['sharpe']:.3f} | "
            f"{is_['max_drawdown_pct']:.2f} | {is_['win_rate']:.3f} | {is_['trade_count']} |"
        )

    md_lines += [
        "",
        "## BEAR-OOS 진입 거래 분리 (close<EMA200 시점에만 진입한 거래)",
        "",
        "| 시나리오 | BEAR 거래수 | BEAR 승률 | BEAR 평균수익률 | BEAR 합계수익률 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, r in results.items():
        b = r["oos_bear_trades_only"]
        md_lines.append(
            f"| {label} | {b['trades']} | {b['win_rate']:.3f} | "
            f"{b['avg_ret']:.4f} | {b['sum_ret']:.4f} |"
        )

    md_lines += [
        "",
        "## 시나리오 정의",
        "",
        "- **S1_EMA200** (현행): close < EMA200 시 신규 진입 차단",
        "- **S2_EMA150**: close < EMA150 시 신규 진입 차단 (임계값 단축)",
        "- **S3_SMA50**: close < SMA50 시 신규 진입 차단 (단순 이평·짧은 기간)",
        "- **S4_OFF**: 추세 게이트 없음 (Baseline)",
        "- **S5_OR_FG40**: (close > EMA200) OR (F&G ≥ 40) 시 OPEN — 비대칭 완화",
        "- **S6_3DAY_CONSEC**: close > EMA200 가 3일 연속 충족 시점부터 OPEN (강화)",
        "",
        "## 해석 가이드",
        "- OPEN 비율: 백테스트 전 구간에서 게이트가 OPEN 상태였던 비율",
        "- BEAR-OOS 거래수가 0이면 게이트가 BEAR 구간 진입을 100% 차단",
        "- BEAR 평균수익률이 음수면 \"게이트가 손실을 막아줌\"",
        "- BEAR 평균수익률이 양수면 \"게이트가 기회비용 발생\"",
    ]
    md_path = OUTPUT_DIR / "buy_gate_validation_summary.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[4/4] MD 저장: {md_path}")


if __name__ == "__main__":
    main()
