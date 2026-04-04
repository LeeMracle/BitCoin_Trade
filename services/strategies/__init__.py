# -*- coding: utf-8 -*-
"""전략 레지스트리 — services/strategies/__init__.py

사용 예:
    from services.strategies import get_strategy

    strategy_fn = get_strategy("dc_atr")                  # 기본 파라미터
    strategy_fn = get_strategy("composite", dc_period=30)  # 파라미터 오버라이드

    # BacktestEngine 과 함께 사용
    from services.backtest.engine import BacktestEngine
    result = BacktestEngine().run(strategy_fn, ohlcv_df)
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from .advanced import (
    make_strategy_alt_pump_surf,
    make_strategy_composite,
    make_strategy_daytrading,
    make_strategy_dc_atr,
    make_strategy_ensemble,
    make_strategy_mtf,
    make_strategy_regime,
    make_strategy_rsi_divergence,
    make_strategy_rsi_ema,
    make_strategy_vol_reversal,
    make_strategy_volatility_breakout,
    make_strategy_volume,
)

# 전략 이름 → make 함수 매핑
STRATEGY_REGISTRY: dict[str, Callable] = {
    "dc_atr":     make_strategy_dc_atr,      # 기존 메인 전략 (기준선)
    "rsi_ema":    make_strategy_rsi_ema,     # 기존 보조 전략 (기준선)
    "ensemble":   make_strategy_ensemble,    # 앙상블 투표
    "regime":     make_strategy_regime,      # 변동성 레짐 스위칭
    "mtf":        make_strategy_mtf,         # 다중 타임프레임
    "volume":     make_strategy_volume,      # 거래량 확인
    "composite":  make_strategy_composite,   # 최종 복합 전략 (추천)
    "daytrading": make_strategy_daytrading,  # 4시간봉 단타 (거래량돌파+트레일)
    "vol_reversal": make_strategy_vol_reversal,  # 하락장 거래량 반전 단타
    "volatility_breakout": make_strategy_volatility_breakout,  # 변동성 돌파 일중회전
    "alt_pump_surf": make_strategy_alt_pump_surf,  # 알트 펌프 서핑
    "rsi_divergence": make_strategy_rsi_divergence,  # RSI 다이버전스 반전
}


def get_strategy(name: str, **kwargs) -> Callable[[pd.DataFrame], pd.Series]:
    """전략 이름으로 strategy_fn 을 반환한다.

    Args:
        name: STRATEGY_REGISTRY 키 중 하나.
              ("dc_atr", "rsi_ema", "ensemble", "regime", "mtf", "volume", "composite")
        **kwargs: 해당 make_strategy_XXX 함수의 파라미터 (기본값 오버라이드 가능).

    Returns:
        strategy_fn: pd.DataFrame -> pd.Series (0 또는 1)

    Raises:
        KeyError: 등록되지 않은 전략 이름 지정 시.

    Examples:
        >>> fn = get_strategy("dc_atr")
        >>> fn = get_strategy("composite", dc_period=30, vol_lookback=90)
    """
    if name not in STRATEGY_REGISTRY:
        available = ", ".join(STRATEGY_REGISTRY.keys())
        raise KeyError(
            f"전략 '{name}' 을 찾을 수 없습니다. "
            f"사용 가능한 전략: {available}"
        )
    make_fn = STRATEGY_REGISTRY[name]
    return make_fn(**kwargs)


__all__ = [
    "STRATEGY_REGISTRY",
    "get_strategy",
    "make_strategy_dc_atr",
    "make_strategy_rsi_ema",
    "make_strategy_ensemble",
    "make_strategy_regime",
    "make_strategy_mtf",
    "make_strategy_volume",
    "make_strategy_composite",
    "make_strategy_daytrading",
    "make_strategy_vol_reversal",
    "make_strategy_volatility_breakout",
    "make_strategy_alt_pump_surf",
    "make_strategy_rsi_divergence",
]
