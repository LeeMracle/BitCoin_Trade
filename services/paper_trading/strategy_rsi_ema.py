"""보조 전략: RSI(10) > 50 / < 45 + EMA(150) 트렌드 필터.

Phase 2 백테스트 결과:
  OOS: Sharpe 1.040, MDD -14.9%, 수익률 +56.0%, 승률 47%, 거래 15회

진입: EMA(150) 위에서 RSI(10) > 50 돌파
청산: RSI(10) < 45 하향
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RSI_PERIOD = 10
RSI_ENTRY = 50
RSI_EXIT = 45
EMA_PERIOD = 150


def calc_ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, min_periods=p, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def check_entry(df: pd.DataFrame) -> bool:
    if len(df) < EMA_PERIOD + 5:
        return False
    close = df["close"]
    rsi = calc_rsi(close, RSI_PERIOD)
    ema = calc_ema(close, EMA_PERIOD)
    latest_close = close.iloc[-1]
    latest_rsi = rsi.iloc[-1]
    latest_ema = ema.iloc[-1]
    if np.isnan(latest_rsi) or np.isnan(latest_ema):
        return False
    return latest_close > latest_ema and latest_rsi > RSI_ENTRY


def check_exit(df: pd.DataFrame) -> bool:
    if len(df) < RSI_PERIOD + 5:
        return False
    rsi = calc_rsi(df["close"], RSI_PERIOD)
    latest_rsi = rsi.iloc[-1]
    if np.isnan(latest_rsi):
        return False
    return latest_rsi < RSI_EXIT


def get_strategy_info() -> dict:
    return {
        "name": "RSI(10)>50/<45 + EMA(150)",
        "rsi_period": RSI_PERIOD,
        "rsi_entry": RSI_ENTRY,
        "rsi_exit": RSI_EXIT,
        "ema_period": EMA_PERIOD,
    }


def get_indicators(df: pd.DataFrame) -> dict:
    """현재 지표 값 반환."""
    close = df["close"]
    rsi = calc_rsi(close, RSI_PERIOD)
    ema = calc_ema(close, EMA_PERIOD)
    return {
        "rsi": round(float(rsi.iloc[-1]), 1) if not np.isnan(rsi.iloc[-1]) else None,
        "ema150": round(float(ema.iloc[-1]), 0) if not np.isnan(ema.iloc[-1]) else None,
        "close": float(close.iloc[-1]),
        "above_ema": float(close.iloc[-1]) > float(ema.iloc[-1]) if not np.isnan(ema.iloc[-1]) else None,
    }
