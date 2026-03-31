"""업비트 KRW 마켓 전체 스캐너 — 전략 옵션 시스템 지원.

매일 전체 종목을 스캔하여:
  1. 선택된 전략의 signal=1 종목 탐색 (매수 후보)
  2. 보유 종목 ATR 트레일링스탑 확인 (청산 판단)
  3. 거래대금/유동성 필터 적용

필터 기준:
  - 24h 거래대금 10억원 이상 (유동성 확보)
  - 상장 60일 이상 (Donchian 50 계산 가능)
  - 스테이블코인 제외 (USDT, USDC 등)

전략 변경:
  services/execution/config.py 의 STRATEGY 값을 수정
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

from services.market_data.fetcher import fetch_ohlcv
from services.strategies import get_strategy
from services.execution.config import STRATEGY, STRATEGY_KWARGS, DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, MIN_VOLUME_KRW
# Donchian 상단 거리 및 ATR 계산 — NEAR 신호 표시 및 스탑 계산 목적
from services.paper_trading.strategy import calc_atr, calc_donchian_upper
MIN_LISTING_DAYS = DONCHIAN_PERIOD + 10  # 최소 상장일수
EXCLUDE_SYMBOLS = {"USDT/KRW", "USDC/KRW", "DAI/KRW", "BUSD/KRW"}  # 스테이블코인


def get_krw_market_coins() -> list[dict]:
    """업비트 KRW 마켓 전체 종목 + 24h 거래대금 조회."""
    exchange = ccxt.upbit({"enableRateLimit": True})
    markets = exchange.load_markets()

    krw_pairs = [
        s for s in markets
        if s.endswith("/KRW") and markets[s]["active"] and s not in EXCLUDE_SYMBOLS
    ]

    tickers = exchange.fetch_tickers(krw_pairs)
    coins = []
    for symbol, t in tickers.items():
        vol_krw = t.get("quoteVolume", 0) or 0
        price = t.get("last", 0) or 0
        if vol_krw >= MIN_VOLUME_KRW and price > 0:
            coins.append({
                "symbol": symbol,
                "price": price,
                "volume_krw": vol_krw,
            })

    coins.sort(key=lambda x: x["volume_krw"], reverse=True)
    return coins


async def scan_entry_signals(coins: list[dict]) -> list[dict]:
    """전체 종목 스캔 — 선택된 전략의 signal=1 종목 탐색.

    각 코인의 OHLCV 에 strategy_fn 을 적용하여 마지막 signal 이 1 이면 BUY 로 분류.
    Donchian 상단까지 거리 계산 (NEAR 신호)은 정보 제공 목적으로 유지.
    """
    end = datetime.now(tz=timezone.utc)
    # 넉넉한 워밍업 기간 (EMA 200 등 고려)
    lookback_days = max(MIN_LISTING_DAYS + 10, 220)
    start = end - timedelta(days=lookback_days)
    start_str = start.strftime("%Y-%m-%dT00:00:00Z")
    end_str = end.strftime("%Y-%m-%dT00:00:00Z")

    # daytrading 전략은 4시간봉, 나머지는 일봉
    timeframe = "4h" if STRATEGY == "daytrading" else "1d"

    # 전략 함수 초기화 (한 번만 생성)
    strategy_fn = get_strategy(STRATEGY, **STRATEGY_KWARGS)

    signals = []

    for coin in coins:
        symbol = coin["symbol"]
        try:
            raw = await fetch_ohlcv(symbol, timeframe, start_str, end_str, use_cache=False)
            df = pd.DataFrame(raw)

            if len(df) < MIN_LISTING_DAYS:
                continue

            latest_close = float(df["close"].iloc[-1])

            # 전략 신호 확인
            sig_series = strategy_fn(df)
            latest_signal = int(sig_series.iloc[-1])
            prev_signal = int(sig_series.iloc[-2]) if len(sig_series) > 1 else 0

            # Donchian 상단 거리 계산 — 정보 표시 목적
            upper = calc_donchian_upper(df, DONCHIAN_PERIOD)
            latest_upper = upper.iloc[-1]
            distance_pct = (
                (latest_upper - latest_close) / latest_close * 100
                if not np.isnan(latest_upper) else float("nan")
            )

            if prev_signal == 0 and latest_signal == 1:
                # 신규 매수 신호 발생
                atr = calc_atr(df, ATR_PERIOD)
                latest_atr = float(atr.iloc[-1])
                trail_stop = latest_close - latest_atr * ATR_MULTIPLIER

                signals.append({
                    "symbol": symbol,
                    "price": latest_close,
                    "donchian_upper": latest_upper if not np.isnan(latest_upper) else 0,
                    "atr": latest_atr,
                    "trail_stop": trail_stop,
                    "volume_krw": coin["volume_krw"],
                    "signal": "BUY",
                    "distance_pct": distance_pct,
                })
            elif (not np.isnan(distance_pct)) and distance_pct <= 3.0 and latest_signal == 0:
                # 근접 종목 (Donchian 상단 3% 이내, 아직 미돌파) — 정보 제공
                signals.append({
                    "symbol": symbol,
                    "price": latest_close,
                    "donchian_upper": latest_upper,
                    "volume_krw": coin["volume_krw"],
                    "signal": "NEAR",
                    "distance_pct": distance_pct,
                })

            # Rate limit 준수
            await asyncio.sleep(0.15)

        except Exception:
            continue

    return signals


async def check_exit_signal(symbol: str, entry_price: float,
                            highest: float, entry_date: str = "") -> dict:
    """보유 종목 청산 신호 확인."""
    end = datetime.now(tz=timezone.utc)
    lookback = max(MIN_LISTING_DAYS + 10, 220)
    start = end - timedelta(days=lookback)
    timeframe = "4h" if STRATEGY == "daytrading" else "1d"

    raw = await fetch_ohlcv(
        symbol, timeframe,
        start.strftime("%Y-%m-%dT00:00:00Z"),
        end.strftime("%Y-%m-%dT00:00:00Z"),
        use_cache=False,
    )
    df = pd.DataFrame(raw)

    if len(df) < ATR_PERIOD + 1:
        return {"should_exit": False, "reason": "데이터 부족"}

    # 전략 신호 기반 청산 판단
    strategy_fn = get_strategy(STRATEGY, **STRATEGY_KWARGS)
    sig_series = strategy_fn(df)
    latest_signal = int(sig_series.iloc[-1])

    latest_close = float(df["close"].iloc[-1])
    new_highest = max(highest, latest_close)
    unrealized_pct = (latest_close / entry_price - 1) * 100

    # 트레일링스탑 계산 (표시용)
    atr = calc_atr(df, ATR_PERIOD)
    latest_atr = float(atr.iloc[-1])
    if STRATEGY == "daytrading":
        trail_stop = new_highest * 0.98  # 2% 트레일링
    else:
        trail_stop = new_highest - latest_atr * ATR_MULTIPLIER

    # 청산 조건: 전략 신호가 0이면 청산
    should_exit = (latest_signal == 0)
    reason = "전략 신호 청산" if should_exit else "보유 유지"

    return {
        "symbol": symbol,
        "price": latest_close,
        "entry_price": entry_price,
        "highest": new_highest,
        "trail_stop": trail_stop,
        "unrealized_pct": unrealized_pct,
        "should_exit": should_exit,
        "reason": reason,
    }
