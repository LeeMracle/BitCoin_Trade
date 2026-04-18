"""업비트 거래 클라이언트 — ccxt 기반.

기능:
  - 잔고 조회
  - 시장가 매수/매도
  - 주문 상태 조회

주의:
  - 출금 권한 절대 사용 금지
  - Rate Limit: 주문 4 req/sec, 조회 29 req/sec
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
import ccxt
from services.common.log_throttle import throttled_print

_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)


def _create_exchange() -> ccxt.upbit:
    access_key = os.environ.get("UPBIT_ACCESS_KEY")
    secret_key = os.environ.get("UPBIT_SECRET_KEY")
    if not access_key or not secret_key:
        raise RuntimeError("UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 환경변수 미설정")
    return ccxt.upbit({
        "apiKey": access_key,
        "secret": secret_key,
        "enableRateLimit": True,
    })


def get_balance() -> dict:
    """전체 자산 잔고 조회 (KRW + BTC + 알트코인).

    Returns:
        {"krw": float, "btc": float, "btc_krw_value": float,
         "alts_krw_value": float, "total_krw": float}
    """
    exchange = _create_exchange()
    balance = exchange.fetch_balance()
    krw = float(balance.get("KRW", {}).get("free", 0))
    btc = float(balance.get("BTC", {}).get("free", 0))

    # 현재 BTC 가격
    ticker = exchange.fetch_ticker("BTC/KRW")
    btc_price = float(ticker["last"])
    btc_krw_value = btc * btc_price

    # 알트코인 평가액 합산 (lessons/20260405_1)
    # CTO WARN-1: 종목별 fetch_ticker → fetch_tickers 일괄 조회로 교체
    # CTO WARN-5: except pass → 로깅 추가
    alts_krw_value = 0.0
    _SKIP = {"KRW", "BTC", "info", "free", "used", "total",
             "timestamp", "datetime"}
    alt_coins: dict[str, float] = {}
    for coin, amounts in balance.items():
        if coin in _SKIP or not isinstance(amounts, dict):
            continue
        total_amt = float(amounts.get("total", 0) or 0)
        if total_amt > 0:
            alt_coins[coin] = total_amt

    if alt_coins:
        # 유효 마켓만 필터링 후 일괄 조회
        try:
            markets = exchange.load_markets()
        except Exception:
            markets = {}
        valid_symbols = [f"{c}/KRW" for c in alt_coins if f"{c}/KRW" in markets]
        skipped = [c for c in alt_coins if f"{c}/KRW" not in markets]
        for c in skipped:
            throttled_print(
                f"balance_noMarket_{c}",
                f"  [잔고] {c}/KRW 마켓 없음 — 평가액 제외",
                interval_sec=60,
            )

        if valid_symbols:
            try:
                tickers = exchange.fetch_tickers(valid_symbols)
                for coin, amt in alt_coins.items():
                    sym = f"{coin}/KRW"
                    if sym in tickers and tickers[sym].get("last"):
                        alts_krw_value += amt * float(tickers[sym]["last"])
            except Exception as e:
                throttled_print(
                    "balance_ticker_fail",
                    f"  [잔고] 알트 시세 일괄조회 실패: {e} — KRW+BTC만으로 산출",
                    interval_sec=60,
                )

    return {
        "krw": krw,
        "btc": btc,
        "btc_price": btc_price,
        "btc_krw_value": round(btc_krw_value, 0),
        "alts_krw_value": round(alts_krw_value, 0),
        "total_krw": round(krw + btc_krw_value + alts_krw_value, 0),
    }


def buy_market(amount_krw: float) -> dict:
    """시장가 매수.

    Args:
        amount_krw: 매수 금액 (KRW). 업비트 최소 주문: 5,000 KRW.

    Returns:
        주문 결과 dict (id, price, amount, cost, status, ...)
    """
    if amount_krw < 5000:
        raise ValueError(f"최소 주문 금액 5,000 KRW 미달: {amount_krw}")

    exchange = _create_exchange()
    # ccxt 시장가 매수: amount는 KRW 금액 기준 (createMarketBuyOrder)
    order = exchange.create_market_buy_order("BTC/KRW", None, params={"cost": amount_krw})
    return _parse_order(order)


def sell_market(amount_btc: float) -> dict:
    """시장가 매도.

    Args:
        amount_btc: 매도 수량 (BTC).

    Returns:
        주문 결과 dict
    """
    if amount_btc <= 0:
        raise ValueError(f"매도 수량 0 이하: {amount_btc}")

    exchange = _create_exchange()
    order = exchange.create_market_sell_order("BTC/KRW", amount_btc)
    return _parse_order(order)


def _parse_order(order: dict) -> dict:
    return {
        "id": order.get("id"),
        "side": order.get("side"),
        "price": order.get("average") or order.get("price"),
        "amount": order.get("amount"),
        "cost": order.get("cost"),
        "status": order.get("status"),
        "timestamp": order.get("timestamp"),
    }
