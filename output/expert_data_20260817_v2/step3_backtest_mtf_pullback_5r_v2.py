"""Step 3 (v2 relaxed) — MTF_Pullback_5R 백테스트 엔진.

v1 대비 완화 항목 3개:
- RSI lookback: 5봉 → 10봉
- EMA20 근접: 0.97 → 0.95
- Vol multiplier: 1.2 → 1.0
나머지는 v1과 동일 (plan 20260817_2 참조).

이벤트 루프: 4h 봉 종가 시점(t)에 트리거 판정 → 성립 시 t+1 봉의 open 에 체결.
청산 판정: 매 4h 봉의 high/low/close 를 순회하며 TP/SL/Time stop 체크. SL/TP 동시 SL 우선.

주의:
- 지표(EMA/RSI/ATR/거래대금/상관계수)는 t 시점까지의 데이터만 사용 (미래정보 누출 zero)
- REGIME 조건 C (BTC daily close > EMA200) 는 진입 후보 필터의 hard gate
- 다중 자산 이벤트를 시간축(4h) 병합 후 처리
"""
from __future__ import annotations
import argparse
import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import duckdb

# ---------- config ----------
CACHE_DB = Path(__file__).resolve().parents[2] / "data" / "cache.duckdb"

INITIAL_CAPITAL = 10_000_000
RISK_PER_TRADE = 0.005         # 자본 대비 0.5%
MAX_POSITIONS = 3
FEE_ROUND_TRIP = 0.001         # 0.05% x 2 = 0.1%
SLIPPAGE_ROUND_TRIP = 0.001    # 0.05% x 2 = 0.1%
# 실질 왕복 = 0.2% (fee + slip). engine 은 각 방향 절반씩 적용

FEE_ONE_SIDE = 0.0005
SLIP_ONE_SIDE = 0.0005

DEFAULT_TP = 0.05
DEFAULT_SL = -0.025
DEFAULT_RSI_TRIGGER = 40.0
TIME_STOP_HOURS = 48
TIME_STOP_MIN_UNREAL = 0.02  # +2% 미만이면 청산

MIN_VOLUME_KRW = 5e8
CORR_WINDOW = 20
CORR_MIN = 0.3
EMA_D_20 = 20
EMA_D_50 = 50
EMA_D_200 = 200
RSI_LEN = 14
# --- v2 (relaxed) 3개 변경 항목 ---
RSI_LOOKBACK_BARS = 10   # v1: 5 → v2: 10 (최근 10봉 내 RSI 터치)
EMA20_PROXIMITY = 0.95   # v1: 0.97 → v2: 0.95 (현재가 >= EMA20(D)*0.95)
VOL_MULT = 1.0           # v1: 1.2 → v2: 1.0 (Vol >= MA20 * 1.0)
# ---------------------------------
ATR_LEN = 14
ATR_MIN = 0.03
ATR_MAX = 0.08
VOL_MA_LEN = 20


# ---------- data loading ----------

def load_ohlcv(symbol: str, timeframe: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    con = duckdb.connect(str(CACHE_DB), read_only=True)
    try:
        rows = con.execute("""
            SELECT ts, open, high, low, close, volume FROM ohlcv
            WHERE exchange='upbit' AND symbol=? AND timeframe=?
              AND ts >= ? AND ts <= ?
            ORDER BY ts
        """, [symbol, timeframe, start_ms, end_ms]).fetchall()
    finally:
        con.close()
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = df["ts"].astype(np.int64)
    return df


def iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


# ---------- indicators (all use t 시점까지) ----------

def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    # Wilder smoothing
    avg_up = up.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_down = down.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        (high - low),
        (high - close_prev).abs(),
        (low - close_prev).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """현재 봉이 직전 봉을 감싸는 강세 캔들."""
    o = df["open"]
    c = df["close"]
    prev_o = o.shift(1)
    prev_c = c.shift(1)
    body = (c - o).abs()
    prev_body = (prev_c - prev_o).abs()
    cond = (c > prev_o) & (o < prev_c) & (body > prev_body) & (c > o)
    return cond.fillna(False)


def hammer(df: pd.DataFrame) -> pd.Series:
    """망치형: 하꼬리 >= 몸통 x 2 AND 종가 > 시가 AND 상꼬리 <= 몸통 x 0.5."""
    o = df["open"]
    c = df["close"]
    h = df["high"]
    lo = df["low"]
    body = (c - o).abs()
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - lo
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    cond = (lower_wick >= body * 2) & (c > o) & (upper_wick <= body * 0.5)
    return cond.fillna(False)


# ---------- Daily 지표 준비 ----------

def build_daily_features(sym_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """각 종목 daily EMA20/50, BTC daily EMA200 계산."""
    out = {}
    for sym, df in sym_data.items():
        if df.empty:
            out[sym] = df
            continue
        df = df.copy()
        df["ema20"] = ema(df["close"], EMA_D_20)
        df["ema50"] = ema(df["close"], EMA_D_50)
        if sym == "BTC/KRW":
            df["ema200"] = ema(df["close"], EMA_D_200)
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
        df["volume_krw"] = df["close"] * df["volume"]
        out[sym] = df
    return out


def build_4h_features(sym_4h: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """4h RSI/ATR/캔들패턴/거래량 MA."""
    out = {}
    for sym, df in sym_4h.items():
        if df.empty:
            out[sym] = df
            continue
        df = df.copy()
        df["rsi14"] = rsi(df["close"], RSI_LEN)
        df["atr14"] = atr(df, ATR_LEN)
        df["atr_pct"] = df["atr14"] / df["close"]
        df["engulf"] = bullish_engulfing(df)
        df["hammer"] = hammer(df)
        df["vol_ma20"] = df["volume"].rolling(VOL_MA_LEN, min_periods=VOL_MA_LEN).mean()
        # 최근 5봉 내 RSI 최저치가 40 이하인지 (t 시점 포함 lookback)
        df["rsi_min_5"] = df["rsi14"].rolling(RSI_LOOKBACK_BARS, min_periods=1).min()
        out[sym] = df
    return out


# ---------- 시점별 조건 계산 ----------

def daily_close_ts_le(daily: pd.DataFrame, target_ts_ms: int) -> Optional[pd.Series]:
    """4h 봉 종가 시점 t 이전의 최신 일봉을 반환 (KST 09:00 = UTC 00:00 마감)."""
    # 일봉의 ts는 해당일 UTC 00:00 시작 → 그 봉의 종가는 그 다음 UTC 00:00 이후 확정
    # 즉 4h 봉 종가 시점 t 에 사용 가능한 daily bar 는 close_ts <= t 인 마지막 bar
    # 여기서는 daily row.ts (=열림 시각) + 24h <= t 인 마지막 row
    ONE_DAY_MS = 86_400_000
    mask = daily["ts"] + ONE_DAY_MS <= target_ts_ms
    if not mask.any():
        return None
    idx = mask.sum() - 1  # 마지막 True index
    return daily.iloc[idx]


def compute_20d_return_series(daily: pd.DataFrame) -> pd.Series:
    """일봉 종가 기준 20일 log return (shift 없이, t 시점의 20일 return)."""
    return np.log(daily["close"] / daily["close"].shift(CORR_WINDOW))


# ---------- 백테스트 코어 ----------

@dataclass
class Position:
    symbol: str
    entry_ts_ms: int
    entry_price: float
    qty: float
    tp_price: float
    sl_price: float
    trigger_low: float
    risk_amount: float
    entered_capital: float  # 진입 시점 총자본 스냅샷


@dataclass
class ClosedTrade:
    symbol: str
    entry_ts_ms: int
    entry_price: float
    exit_ts_ms: int
    exit_price: float
    qty: float
    exit_reason: str  # TP / SL / TIME
    pnl_krw: float
    ret_pct: float
    hold_hours: float


@dataclass
class BacktestResult:
    trades: list[ClosedTrade]
    equity_curve: pd.DataFrame  # ts, equity
    entry_signal_log: list[dict]  # 진입 시도 로그 (필터 통과·차단 이유)


def run_backtest(
    universe: list[str],
    sym_daily: dict[str, pd.DataFrame],
    sym_4h: dict[str, pd.DataFrame],
    start_ms: int,
    end_ms: int,
    tp: float = DEFAULT_TP,
    sl: float = DEFAULT_SL,
    rsi_trigger: float = DEFAULT_RSI_TRIGGER,
) -> BacktestResult:
    """
    4h 시간축을 병합하여 이벤트 처리.

    for each 4h bar t (t=오픈ts 기준):
      1) 시가에 오픈 포지션의 SL/TP/Time-stop 판정 (봉 내 high/low 사용, gap 처리)
      2) 4h 봉 종가에 진입 판정 → 성립 시 t+1 봉 open 에 진입 (다음 이터레이션에서 처리 불가하니 pending 큐)
    """
    # 시간축 = 모든 종목 4h ts 유니온, [start_ms, end_ms]
    all_ts: set[int] = set()
    for sym in universe:
        df = sym_4h.get(sym, pd.DataFrame())
        if df.empty:
            continue
        all_ts.update(df["ts"].tolist())
    ts_axis = sorted([t for t in all_ts if start_ms <= t <= end_ms])
    if not ts_axis:
        raise RuntimeError("empty time axis")

    # symbol -> ts -> row 를 빠르게 lookup 하기 위해 index dict 생성
    sym_4h_idx: dict[str, dict[int, int]] = {}
    for sym, df in sym_4h.items():
        if df.empty:
            continue
        sym_4h_idx[sym] = {int(t): i for i, t in enumerate(df["ts"].values)}

    # BTC daily 캐시
    btc_d = sym_daily.get("BTC/KRW", pd.DataFrame())

    # 상태
    cash = float(INITIAL_CAPITAL)
    positions: dict[str, Position] = {}
    trades: list[ClosedTrade] = []
    equity_rows: list[dict] = []
    entry_log: list[dict] = []
    pending_entries: list[dict] = []  # {symbol, next_open_ts, size_hint}

    def total_equity_at(ts_ms: int) -> float:
        eq = cash
        for p in positions.values():
            df = sym_4h.get(p.symbol)
            if df is None or df.empty:
                eq += p.qty * p.entry_price
                continue
            idx = sym_4h_idx.get(p.symbol, {}).get(ts_ms)
            if idx is None:
                # 가장 최근 종가로 대체
                sub = df[df["ts"] <= ts_ms]
                if sub.empty:
                    eq += p.qty * p.entry_price
                else:
                    eq += p.qty * sub["close"].iloc[-1]
            else:
                eq += p.qty * df["close"].iloc[idx]
        return eq

    for ti, t_ms in enumerate(ts_axis):
        # 1) 이번 4h 봉 = ts=t_ms (open ts). 봉의 open/high/low/close 필요
        # 오픈 포지션 정리: 봉 내 SL/TP/Time-stop 순회
        # SL/TP 동시 = SL 우선 → 봉 low → sl 도달 여부 먼저
        exits_this_bar: list[tuple[str, str, float]] = []
        for sym, p in list(positions.items()):
            idx = sym_4h_idx.get(sym, {}).get(t_ms)
            if idx is None:
                continue
            row = sym_4h[sym].iloc[idx]
            bar_open = float(row["open"])
            bar_high = float(row["high"])
            bar_low = float(row["low"])
            bar_close = float(row["close"])
            bar_ts = int(row["ts"])
            bar_end_ts = bar_ts + 4 * 3600 * 1000

            # SL 우선 (봉 저가가 SL 도달)
            hit_sl = bar_low <= p.sl_price
            hit_tp = bar_high >= p.tp_price
            exit_price = None
            exit_reason = None
            if hit_sl:
                # 갭다운으로 시가가 이미 SL 아래면 시가 체결
                if bar_open <= p.sl_price:
                    exit_price = bar_open
                else:
                    exit_price = p.sl_price
                exit_reason = "SL"
            elif hit_tp:
                if bar_open >= p.tp_price:
                    exit_price = bar_open
                else:
                    exit_price = p.tp_price
                exit_reason = "TP"
            else:
                # Time stop 판정: bar_end 시점 기준
                held_hours = (bar_end_ts - p.entry_ts_ms) / (3600 * 1000)
                unrealized_ret = (bar_close / p.entry_price) - 1
                if held_hours >= TIME_STOP_HOURS and unrealized_ret < TIME_STOP_MIN_UNREAL:
                    exit_price = bar_close
                    exit_reason = "TIME"

            if exit_price is not None:
                # 청산
                sell_price = exit_price * (1 - SLIP_ONE_SIDE)
                proceeds = p.qty * sell_price * (1 - FEE_ONE_SIDE)
                # 원 진입 비용 재계산: entry_price 는 이미 slip+fee 반영된 buy 체결가
                # 우리는 buy에 자본 = qty * entry_price / (1 - FEE_ONE_SIDE) 를 지출
                # → 진입비용 = qty * entry_price / (1 - FEE_ONE_SIDE)
                entry_cost = p.qty * p.entry_price / (1 - FEE_ONE_SIDE)
                pnl_krw = proceeds - entry_cost
                ret_pct = pnl_krw / entry_cost
                hold_h = (bar_end_ts - p.entry_ts_ms) / (3600 * 1000)
                trades.append(ClosedTrade(
                    symbol=sym, entry_ts_ms=p.entry_ts_ms, entry_price=p.entry_price,
                    exit_ts_ms=bar_end_ts, exit_price=sell_price,
                    qty=p.qty, exit_reason=exit_reason, pnl_krw=pnl_krw,
                    ret_pct=ret_pct, hold_hours=hold_h,
                ))
                cash += proceeds
                del positions[sym]

        # 2) 이번 봉의 open 에서 pending_entries 체결
        still_pending = []
        for pe in pending_entries:
            if pe["next_open_ts"] != t_ms:
                # 시간대 다른 종목 = 다음 이터레이션에서 처리
                still_pending.append(pe)
                continue
            sym = pe["symbol"]
            if sym in positions:
                continue
            if len(positions) >= MAX_POSITIONS:
                continue  # 슬롯 없음 → 폐기
            idx = sym_4h_idx.get(sym, {}).get(t_ms)
            if idx is None:
                continue
            row = sym_4h[sym].iloc[idx]
            entry_open = float(row["open"])
            buy_price = entry_open * (1 + SLIP_ONE_SIDE)
            # SL 가격: max(entry * (1+sl), trigger_low). sl 은 음수 (-0.025)
            sl_from_pct = buy_price * (1 + sl)
            sl_price = max(sl_from_pct, pe["trigger_low"])
            # 스펙: max(entry*0.975, trigger_low). trigger_low 가 entry*0.975 보다 크면 (얕은 SL) trigger_low 사용
            # 하지만 그 경우 SL 거리가 -2.5% 보다 작음(= 손실폭 작음)
            sl_dist = (buy_price - sl_price) / buy_price
            if sl_dist <= 0:
                # 사이 이상 케이스
                continue
            # 사이즈: risk / sl_dist * entry
            equity_now = total_equity_at(t_ms)
            risk_amount = equity_now * RISK_PER_TRADE
            position_krw = risk_amount / sl_dist
            # 캐시 여유 확인 (부분 진입 허용)
            if position_krw > cash:
                position_krw = cash
            if position_krw < 10_000:  # 1만원 미만은 skip
                continue
            qty = position_krw * (1 - FEE_ONE_SIDE) / buy_price
            tp_price = buy_price * (1 + tp)
            entry_ts_ms = t_ms
            positions[sym] = Position(
                symbol=sym, entry_ts_ms=entry_ts_ms, entry_price=buy_price,
                qty=qty, tp_price=tp_price, sl_price=sl_price,
                trigger_low=pe["trigger_low"], risk_amount=risk_amount,
                entered_capital=equity_now,
            )
            cash -= position_krw
            entry_log.append({
                "ts": t_ms, "symbol": sym, "action": "ENTER",
                "price": buy_price, "qty": qty, "sl": sl_price, "tp": tp_price,
                "sl_dist_pct": sl_dist, "risk_krw": risk_amount,
                "position_krw": position_krw, "equity": equity_now,
            })
        pending_entries = still_pending

        # 3) 이번 봉 종가에서 신호 판정 → pending 등록 (t+1 open 체결)
        # 다음 4h 봉 ts = t_ms + 4h. universe 각 종목의 다음 ts 존재해야 함
        NEXT_TS = t_ms + 4 * 3600 * 1000

        # REGIME (BTC daily) 판정: t_ms 이전 최신 일봉의 close vs ema200
        btc_daily_row = daily_close_ts_le(btc_d, t_ms) if not btc_d.empty else None
        regime_ok = False
        if btc_daily_row is not None and not pd.isna(btc_daily_row.get("ema200", np.nan)):
            regime_ok = float(btc_daily_row["close"]) > float(btc_daily_row["ema200"])

        for sym in universe:
            if sym in positions:
                continue  # 이미 보유
            df4 = sym_4h.get(sym)
            if df4 is None or df4.empty:
                continue
            idx = sym_4h_idx.get(sym, {}).get(t_ms)
            if idx is None:
                continue
            # 다음 봉이 존재해야 진입 가능
            if idx + 1 >= len(df4):
                continue
            next_ts = int(df4["ts"].iloc[idx + 1])
            if next_ts != NEXT_TS:
                # 데이터 결측 시 스킵
                continue
            row4 = df4.iloc[idx]

            # 필터: REGIME 조건 C 실패 시 즉시 스킵 (기록 X, 대량 spam 방지)
            if not regime_ok:
                continue

            # 조건 A/B: symbol daily
            sd = sym_daily.get(sym)
            if sd is None or sd.empty:
                continue
            sd_row = daily_close_ts_le(sd, t_ms)
            if sd_row is None:
                continue
            if pd.isna(sd_row.get("ema20", np.nan)) or pd.isna(sd_row.get("ema50", np.nan)):
                continue
            cond_A = float(sd_row["close"]) > float(sd_row["ema20"])
            cond_B = float(sd_row["ema20"]) > float(sd_row["ema50"])
            if not (cond_A and cond_B):
                continue

            # 유니버스: 24h 거래대금 & 20일 상관계수
            # 24h 거래대금 = 최근 daily row 의 close*volume
            vol_krw_24h = float(sd_row.get("volume_krw", 0.0))
            if vol_krw_24h < MIN_VOLUME_KRW:
                continue

            # 20일 상관계수 (일봉 log return, 각각 t 시점까지 마지막 20개)
            corr_val = compute_pair_corr_at(sym, sd_row.name, sd, btc_d)
            if corr_val is None or corr_val < CORR_MIN:
                continue

            # 셋업 (4h)
            # D: 최근 5봉(=현봉 포함) 내 RSI(14) <= rsi_trigger 터치
            rsi_min5 = float(row4.get("rsi_min_5", np.nan))
            if math.isnan(rsi_min5) or rsi_min5 > rsi_trigger:
                continue
            # E: 현재가 >= EMA20(D) * 0.97
            price_now = float(row4["close"])
            ema20_d = float(sd_row["ema20"])
            if price_now < ema20_d * EMA20_PROXIMITY:
                continue
            # F: ATR(14, 4h) / price ∈ [ATR_MIN, ATR_MAX]
            atr_pct = float(row4.get("atr_pct", np.nan))
            if math.isnan(atr_pct) or atr_pct < ATR_MIN or atr_pct > ATR_MAX:
                continue

            # 트리거 (G): Engulfing OR Hammer
            trig_pattern = bool(row4.get("engulf", False)) or bool(row4.get("hammer", False))
            if not trig_pattern:
                continue
            # H: Vol >= MA20 * 1.2
            vol_now = float(row4["volume"])
            vol_ma = float(row4.get("vol_ma20", np.nan))
            if math.isnan(vol_ma) or vol_now < vol_ma * VOL_MULT:
                continue

            # 성립 → 다음 봉 open 진입 대기
            pending_entries.append({
                "symbol": sym, "next_open_ts": NEXT_TS,
                "trigger_low": float(row4["low"]),
                "trigger_ts": t_ms,
            })
            entry_log.append({
                "ts": t_ms, "symbol": sym, "action": "SIGNAL",
                "next_open_ts": NEXT_TS, "trigger_low": float(row4["low"]),
            })

        # 4) 자본곡선 기록 (매 4h)
        eq = total_equity_at(t_ms)
        equity_rows.append({"ts": t_ms, "equity": eq})

    # 종료 시 잔여 포지션 강제청산 (마지막 봉 종가)
    if positions:
        last_ts = ts_axis[-1]
        for sym, p in list(positions.items()):
            idx = sym_4h_idx.get(sym, {}).get(last_ts)
            if idx is None:
                # 마지막 데이터 사용
                df = sym_4h.get(sym)
                if df is None or df.empty:
                    continue
                exit_price = float(df["close"].iloc[-1])
                exit_ts_ms = int(df["ts"].iloc[-1])
            else:
                exit_price = float(sym_4h[sym]["close"].iloc[idx])
                exit_ts_ms = int(sym_4h[sym]["ts"].iloc[idx])
            sell_price = exit_price * (1 - SLIP_ONE_SIDE)
            proceeds = p.qty * sell_price * (1 - FEE_ONE_SIDE)
            entry_cost = p.qty * p.entry_price / (1 - FEE_ONE_SIDE)
            pnl = proceeds - entry_cost
            ret_pct = pnl / entry_cost
            hold_h = (exit_ts_ms - p.entry_ts_ms) / (3600 * 1000)
            trades.append(ClosedTrade(
                symbol=sym, entry_ts_ms=p.entry_ts_ms, entry_price=p.entry_price,
                exit_ts_ms=exit_ts_ms, exit_price=sell_price,
                qty=p.qty, exit_reason="END", pnl_krw=pnl,
                ret_pct=ret_pct, hold_hours=hold_h,
            ))
            cash += proceeds
            del positions[sym]

    return BacktestResult(
        trades=trades,
        equity_curve=pd.DataFrame(equity_rows),
        entry_signal_log=entry_log,
    )


# 상관계수 캐시 (심볼별)
_CORR_CACHE: dict[str, pd.Series] = {}


def compute_pair_corr_at(sym: str, daily_index_pos, sym_daily_df: pd.DataFrame,
                         btc_daily_df: pd.DataFrame) -> Optional[float]:
    """일봉 index 위치까지의 20일 rolling correlation (log return)."""
    if sym == "BTC/KRW":
        return 1.0
    key = f"{sym}::corr"
    if key not in _CORR_CACHE:
        sr = np.log(sym_daily_df["close"] / sym_daily_df["close"].shift(1))
        # BTC와 인덱스 정렬을 위해 ts 기반 merge
        btc_ret = np.log(btc_daily_df["close"] / btc_daily_df["close"].shift(1))
        btc_map = dict(zip(btc_daily_df["ts"], btc_ret))
        aligned_btc = sym_daily_df["ts"].map(btc_map)
        # rolling corr
        corr = sr.rolling(CORR_WINDOW).corr(aligned_btc)
        _CORR_CACHE[key] = corr
    corr_series = _CORR_CACHE[key]
    if daily_index_pos >= len(corr_series):
        return None
    v = corr_series.iloc[daily_index_pos]
    if pd.isna(v):
        return None
    return float(v)


# ---------- 성과 지표 ----------

def compute_perf_metrics(result: BacktestResult, initial_capital: float,
                         start_ms: int, end_ms: int) -> dict:
    trades = result.trades
    eq = result.equity_curve.copy()
    if eq.empty:
        return {"error": "no_equity_data"}

    eq["dt"] = pd.to_datetime(eq["ts"], unit="ms", utc=True)
    eq = eq.sort_values("dt").reset_index(drop=True)
    final_eq = float(eq["equity"].iloc[-1])
    total_ret = final_eq / initial_capital - 1

    days = (end_ms - start_ms) / (86_400 * 1000)
    years = max(days / 365.25, 0.01)
    cagr = (final_eq / initial_capital) ** (1 / years) - 1

    # 일별 리샘플 → 일 리턴
    daily = eq.set_index("dt")["equity"].resample("1D").last().ffill()
    daily_ret = daily.pct_change().fillna(0.0)
    if daily_ret.std() > 0:
        sharpe = np.sqrt(365) * daily_ret.mean() / daily_ret.std()
        downside = daily_ret[daily_ret < 0]
        sortino = np.sqrt(365) * daily_ret.mean() / (downside.std() if len(downside) > 0 else np.nan)
    else:
        sharpe = 0.0
        sortino = 0.0

    # MDD
    roll_max = daily.cummax()
    drawdown = daily / roll_max - 1
    mdd = float(drawdown.min())

    # trade metrics
    n = len(trades)
    if n > 0:
        rets = np.array([t.ret_pct for t in trades])
        pnls = np.array([t.pnl_krw for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        win_rate = len(wins) / n
        gross_win = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(-losses.sum()) if len(losses) > 0 else 0.0
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        win_loss_ratio = abs(avg_win / avg_loss) if avg_loss < 0 else float("inf")
        avg_hold = float(np.mean([t.hold_hours for t in trades]))
        reasons = {}
        for t in trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        # 연속 손실
        max_consec_loss = 0
        cur = 0
        for r in rets:
            if r <= 0:
                cur += 1
                max_consec_loss = max(max_consec_loss, cur)
            else:
                cur = 0
    else:
        win_rate = 0
        pf = 0
        avg_win = 0
        avg_loss = 0
        win_loss_ratio = 0
        avg_hold = 0
        reasons = {}
        max_consec_loss = 0
        rets = np.array([])
        pnls = np.array([])

    return {
        "n_trades": n,
        "total_return_pct": total_ret * 100,
        "cagr_pct": cagr * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "mdd_pct": mdd * 100,
        "profit_factor": pf,
        "win_rate_pct": win_rate * 100,
        "avg_win_pct": avg_win * 100 if isinstance(avg_win, float) else 0,
        "avg_loss_pct": avg_loss * 100 if isinstance(avg_loss, float) else 0,
        "win_loss_ratio": win_loss_ratio,
        "avg_hold_hours": avg_hold,
        "final_equity_krw": final_eq,
        "exit_reasons": reasons,
        "max_consec_losses": max_consec_loss,
    }


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["is", "oos", "full"], default="oos")
    ap.add_argument("--tp", type=float, default=DEFAULT_TP)
    ap.add_argument("--sl", type=float, default=DEFAULT_SL)
    ap.add_argument("--rsi", type=float, default=DEFAULT_RSI_TRIGGER)
    ap.add_argument("--out-tag", type=str, default="run")
    args = ap.parse_args()

    if args.phase == "is":
        start = "2024-01-01T00:00:00Z"
        end = "2025-06-30T23:59:59Z"
    elif args.phase == "oos":
        start = "2025-07-01T00:00:00Z"
        end = "2026-08-16T23:59:59Z"
    else:
        start = "2024-01-01T00:00:00Z"
        end = "2026-08-16T23:59:59Z"
    start_ms = iso_to_ms(start)
    end_ms = iso_to_ms(end)

    # 유니버스 로드: fetch_ohlcv_log_v2.json 의 usable_symbols 우선 사용
    fetch_log_v2 = Path(__file__).resolve().parent / "fetch_ohlcv_log_v2.json"
    fetch_log_v1 = Path(__file__).resolve().parent / "fetch_ohlcv_log.json"
    fetch_log_path = fetch_log_v2 if fetch_log_v2.exists() else fetch_log_v1
    if fetch_log_path.exists():
        flog = json.loads(fetch_log_path.read_text(encoding="utf-8"))
        universe = list(flog.get("usable_symbols", []))
        print(f"[universe] from {fetch_log_path.name} usable: {len(universe)} symbols")
    else:
        uni_path = Path(__file__).resolve().parent / "universe_screen.json"
        uni = json.loads(uni_path.read_text(encoding="utf-8"))
        universe = [c["symbol"] for c in uni["candidates"]]
    if "BTC/KRW" not in universe:
        universe.insert(0, "BTC/KRW")

    # daily/4h 로드 (여유 lookback 확보 위해 start 이전 250일부터)
    lookback_ms = 260 * 86_400_000
    data_start = start_ms - lookback_ms
    sym_daily = {}
    sym_4h = {}
    for sym in universe:
        sym_daily[sym] = load_ohlcv(sym, "1d", data_start, end_ms)
        sym_4h[sym] = load_ohlcv(sym, "4h", data_start, end_ms)

    # 지표 계산
    sym_daily = build_daily_features(sym_daily)
    sym_4h = build_4h_features(sym_4h)

    # 상관계수 캐시 초기화 (파라미터 sweep 재실행 시 필요하면 clear)
    global _CORR_CACHE
    _CORR_CACHE = {}

    result = run_backtest(
        universe=universe,
        sym_daily=sym_daily,
        sym_4h=sym_4h,
        start_ms=start_ms,
        end_ms=end_ms,
        tp=args.tp, sl=args.sl, rsi_trigger=args.rsi,
    )
    # ---- 사후 검증 (미래정보 누출·REGIME C) ----
    btc_d = sym_daily.get("BTC/KRW")
    regime_violations = 0
    signal_count = sum(1 for x in result.entry_signal_log if x.get("action") == "SIGNAL")
    enter_count = sum(1 for x in result.entry_signal_log if x.get("action") == "ENTER")
    for x in result.entry_signal_log:
        if x.get("action") != "SIGNAL":
            continue
        row = daily_close_ts_le(btc_d, x["ts"])
        if row is None or pd.isna(row.get("ema200", np.nan)):
            regime_violations += 1
            continue
        if not (float(row["close"]) > float(row["ema200"])):
            regime_violations += 1
    print(f"\n[assert] REGIME C violations: {regime_violations} / {signal_count} signals (must be 0)")
    print(f"[assert] SIGNAL={signal_count}, ENTER={enter_count}")
    assert regime_violations == 0, "REGIME C leak detected"

    metrics = compute_perf_metrics(result, INITIAL_CAPITAL, start_ms, end_ms)
    metrics["_assert_regime_violations"] = regime_violations
    metrics["_signal_count"] = signal_count
    metrics["_enter_count"] = enter_count
    print(f"\n=== {args.phase} phase, tp={args.tp:.3f} sl={args.sl:.3f} rsi={args.rsi} ===")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    # 저장 (v2: mtf_pullback_5r_v2 디렉토리)
    out_dir = Path(__file__).resolve().parents[2] / "workspace" / "runs" / "mtf_pullback_5r_v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    trades_df = pd.DataFrame([{
        "symbol": t.symbol,
        "entry_ts": t.entry_ts_ms,
        "entry_dt_utc": datetime.fromtimestamp(t.entry_ts_ms / 1000, tz=timezone.utc).isoformat(),
        "exit_ts": t.exit_ts_ms,
        "exit_dt_utc": datetime.fromtimestamp(t.exit_ts_ms / 1000, tz=timezone.utc).isoformat(),
        "entry_price": round(t.entry_price, 2),
        "exit_price": round(t.exit_price, 2),
        "qty": t.qty,
        "exit_reason": t.exit_reason,
        "pnl_krw": round(t.pnl_krw, 2),
        "ret_pct": round(t.ret_pct * 100, 4),
        "hold_hours": round(t.hold_hours, 2),
    } for t in result.trades])
    trades_csv = out_dir / f"trades_{args.phase}_{args.out_tag}.csv"
    trades_df.to_csv(trades_csv, index=False, encoding="utf-8-sig")

    eq_csv = out_dir / f"equity_{args.phase}_{args.out_tag}.csv"
    result.equity_curve.to_csv(eq_csv, index=False)

    metrics_json = out_dir / f"metrics_{args.phase}_{args.out_tag}.json"
    metrics_json.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")

    print(f"\nsaved trades: {trades_csv}")
    print(f"saved equity: {eq_csv}")
    print(f"saved metrics: {metrics_json}")

    return metrics


if __name__ == "__main__":
    main()
