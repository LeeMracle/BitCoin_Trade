"""업비트 KRW 마켓 전체 스캐너.

매일 전체 종목을 스캔하여:
  1. Donchian(50) 상단 돌파 종목 탐색 (매수 후보)
  2. 보유 종목 ATR 트레일링스탑 확인 (청산 판단)
  3. 거래대금/유동성 필터 적용

필터 기준:
  - 24h 거래대금 10억원 이상 (유동성 확보)
  - 상장 60일 이상 (Donchian 50 계산 가능)
  - 스테이블코인 제외 (USDT, USDC 등)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import ccxt
import numpy as np
import pandas as pd

from services.market_data.fetcher import fetch_ohlcv
from services.paper_trading.strategy import (
    DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER,
    calc_atr, calc_donchian_upper,
)

# 필터 설정
MIN_VOLUME_KRW = 1_000_000_000    # 24h 거래대금 최소 10억원
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
    """전체 종목 스캔 — Donchian 돌파 매수 신호 탐색."""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=MIN_LISTING_DAYS + 10)
    start_str = start.strftime("%Y-%m-%dT00:00:00Z")
    end_str = end.strftime("%Y-%m-%dT00:00:00Z")

    signals = []

    for coin in coins:
        symbol = coin["symbol"]
        try:
            raw = await fetch_ohlcv(symbol, "1d", start_str, end_str, use_cache=False)
            df = pd.DataFrame(raw)

            if len(df) < MIN_LISTING_DAYS:
                continue

            upper = calc_donchian_upper(df, DONCHIAN_PERIOD)
            latest_close = float(df["close"].iloc[-1])
            latest_upper = upper.iloc[-1]

            if np.isnan(latest_upper):
                continue

            distance_pct = (latest_upper - latest_close) / latest_close * 100

            if latest_close > latest_upper:
                # 돌파 신호!
                atr = calc_atr(df, ATR_PERIOD)
                latest_atr = float(atr.iloc[-1])
                trail_stop = latest_close - latest_atr * ATR_MULTIPLIER

                signals.append({
                    "symbol": symbol,
                    "price": latest_close,
                    "donchian_upper": latest_upper,
                    "atr": latest_atr,
                    "trail_stop": trail_stop,
                    "volume_krw": coin["volume_krw"],
                    "signal": "BUY",
                    "distance_pct": distance_pct,
                })
            elif distance_pct <= 3.0:
                # 근접 종목 (3% 이내)
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
                            highest: float) -> dict:
    """보유 종목 청산 신호 확인."""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=MIN_LISTING_DAYS + 10)

    raw = await fetch_ohlcv(
        symbol, "1d",
        start.strftime("%Y-%m-%dT00:00:00Z"),
        end.strftime("%Y-%m-%dT00:00:00Z"),
        use_cache=False,
    )
    df = pd.DataFrame(raw)

    if len(df) < ATR_PERIOD + 1:
        return {"should_exit": False, "reason": "데이터 부족"}

    atr = calc_atr(df, ATR_PERIOD)
    latest_close = float(df["close"].iloc[-1])
    latest_atr = float(atr.iloc[-1])

    new_highest = max(highest, latest_close)
    trail_stop = new_highest - latest_atr * ATR_MULTIPLIER
    unrealized_pct = (latest_close / entry_price - 1) * 100

    should_exit = latest_close < trail_stop

    return {
        "symbol": symbol,
        "price": latest_close,
        "entry_price": entry_price,
        "highest": new_highest,
        "trail_stop": trail_stop,
        "unrealized_pct": unrealized_pct,
        "should_exit": should_exit,
        "reason": "트레일링스탑 하향 이탈" if should_exit else "보유 유지",
    }
