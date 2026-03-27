"""Donchian(50) + ATR(14)x3.0 추세추종 전략.

Phase 2 백테스트 결과:
  IS  (2020-2023): Sharpe 1.641, MDD -30.8%, 수익률 +1079%, 승률 67%
  OOS (2024-2026): Sharpe 1.123, MDD -18.7%, 수익률 +63.2%, 승률 57%

진입: 종가 > 직전 50일 최고가 (채널 상단 돌파)
청산: 종가 < ATR 트레일링스탑 (고점 - ATR(14) × 3.0)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# 전략 파라미터
DONCHIAN_PERIOD = 50
ATR_PERIOD = 14
ATR_MULTIPLIER = 3.0


def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Average True Range."""
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calc_donchian_upper(df: pd.DataFrame, period: int = DONCHIAN_PERIOD) -> pd.Series:
    """Donchian 상단 채널 (전일까지의 최고가)."""
    return df["high"].shift(1).rolling(window=period, min_periods=period).max()


def check_entry(df: pd.DataFrame) -> bool:
    """매수 신호 확인. df는 최소 DONCHIAN_PERIOD+1 봉 필요."""
    if len(df) < DONCHIAN_PERIOD + 1:
        return False
    upper = calc_donchian_upper(df)
    latest_close = df["close"].iloc[-1]
    latest_upper = upper.iloc[-1]
    if np.isnan(latest_upper):
        return False
    return latest_close > latest_upper


def check_exit(df: pd.DataFrame, highest_since_entry: float) -> tuple[bool, float]:
    """청산 신호 확인. (should_exit, new_trailing_stop) 반환."""
    if len(df) < ATR_PERIOD + 1:
        return False, 0.0

    atr = calc_atr(df)
    latest_close = df["close"].iloc[-1]
    latest_atr = atr.iloc[-1]

    if np.isnan(latest_atr):
        return False, 0.0

    # 고점 갱신
    new_highest = max(highest_since_entry, latest_close)
    trailing_stop = new_highest - latest_atr * ATR_MULTIPLIER

    should_exit = latest_close < trailing_stop
    return should_exit, trailing_stop


def get_strategy_info() -> dict:
    return {
        "name": "Donchian(50) + ATR(14)x3.0",
        "donchian_period": DONCHIAN_PERIOD,
        "atr_period": ATR_PERIOD,
        "atr_multiplier": ATR_MULTIPLIER,
        "entry_rule": "close > 직전 50일 최고가",
        "exit_rule": "close < trailing_stop (고점 - ATR(14) × 3.0)",
    }
