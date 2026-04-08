"""ccxt/업비트 응답 해석용 None-safe 공용 헬퍼.

lessons/20260408_4_nonetype_format_lint.md 및 docs/lint_layer.md 참조.

핵심 문제
---------
Python `dict.get(key, default)` 는 "키가 없을 때"만 default를 돌려주며,
값이 None이면 None을 그대로 반환한다. 업비트 시장가 주문 접수 응답은
`cost`·`average`·`price`·`filled` 가 모두 None으로 오는 경우가 있어,
`{order.get('cost', 0):,.0f}` 같은 관용구가 런타임에 크래시한다.

본 모듈은 다음 두 가지 책임만 진다:

1. `fmt_num`  — None-safe 숫자 포매터 (로그/알림/보고 출력용)
2. `resolve_fill` — 시장가 주문 응답의 체결정보 해석 (cost, price)

사용 예
-------
    from services.common.ccxt_utils import fmt_num, resolve_fill

    order = exchange.create_market_sell_order(symbol, amount)
    cost, price = resolve_fill(exchange, order, symbol, amount_hint=amount)
    msg = f"체결: {fmt_num(cost)}원 @ {fmt_num(price)}"

본 모듈은 린터(`scripts/lint_none_format.py`)가 WARN으로 표시하는
ccxt 위험 키(`cost`/`price`/`average`/`filled`) 접근을 한 곳에 가둔다.
"""
from __future__ import annotations

import time
from typing import Any


__all__ = ["fmt_num", "resolve_fill"]


def fmt_num(v: Any, spec: str = ",.0f", fallback: str = "N/A") -> str:
    """None-safe 숫자 포매터.

    Parameters
    ----------
    v : Any
        포매팅 대상 값. None 또는 포매팅 불가 타입이면 `fallback` 반환.
    spec : str
        표준 format spec (예: ",.0f", ".2f", ",.8f", ".0%").
    fallback : str
        None 또는 포매팅 실패 시 반환할 문자열.

    Returns
    -------
    str
    """
    if v is None:
        return fallback
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return fallback


def resolve_fill(
    exchange: Any,
    order: dict,
    symbol: str,
    amount_hint: float | None = None,
    wait_seconds: float = 0.4,
) -> tuple[float | None, float | None]:
    """시장가 주문 응답에서 체결정보(cost, price)를 해석.

    업비트는 `create_market_*_order` 접수 응답에 cost/price/average가
    모두 None으로 오는 케이스가 있다. 본 함수는 다음 순서로 값을 복구한다:

    1. order dict 에서 average → price → cost 시도
    2. 여전히 None이면 `fetch_order(id)`로 재조회 (짧은 대기 후)
    3. 그래도 price가 없으면 `fetch_ticker(symbol)["last"]` 로 추정
    4. cost가 없으면 `price * amount_hint` 로 추정

    Parameters
    ----------
    exchange : ccxt.Exchange
        ccxt 거래소 인스턴스 (fetch_order, fetch_ticker 필요).
    order : dict
        `create_market_*_order` 의 반환 dict.
    symbol : str
        거래 심볼 (예: "BTC/KRW").
    amount_hint : float, optional
        체결 수량 힌트 — cost 추정이 필요할 때 사용.
    wait_seconds : float
        재조회 전 대기 시간 (업비트 반영 지연 완충).

    Returns
    -------
    (cost, price) : tuple[float | None, float | None]
        복구 가능한 최선의 값. 모두 실패하면 (None, None).
    """
    price = order.get("average") or order.get("price")
    cost = order.get("cost")

    # 1단계: 재조회
    if price is None or cost is None:
        oid = order.get("id")
        if oid:
            try:
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                fetched = exchange.fetch_order(oid, symbol)
                price = price or fetched.get("average") or fetched.get("price")
                cost = cost or fetched.get("cost")
                if amount_hint is None:
                    amount_hint = fetched.get("filled") or fetched.get("amount")
            except Exception:
                pass

    # 2단계: ticker 추정
    if price is None:
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker.get("last")
        except Exception:
            price = None

    # 3단계: cost 추정
    if cost is None and price is not None and amount_hint:
        try:
            cost = float(price) * float(amount_hint)
        except (TypeError, ValueError):
            cost = None

    return cost, price
