"""업비트 거래 클라이언트 — ccxt 기반.

기능:
  - 잔고 조회 (Rate Limit 백오프 + 싱글톤)
  - 시장가 매수/매도
  - 주문 상태 조회
  - 마지막 성공 잔고 캐시 (CB fallback용)

주의:
  - 출금 권한 절대 사용 금지
  - Rate Limit: 주문 4 req/sec, 조회 29 req/sec

plan 20260503 P0:
  - _EXCHANGE_INSTANCE 모듈 레벨 싱글톤 → ccxt 내부 throttle 누적 보존
  - _retry_on_429() 백오프 1s → 4s → 16s + Retry-After 우선
  - get_balance() 성공 시 workspace/last_known_balance.json 갱신 → CB fallback에서 사용
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import ccxt
from dotenv import load_dotenv

from services.common.log_throttle import throttled_print

_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)

_LAST_KNOWN_BALANCE = Path(__file__).resolve().parents[2] / "workspace" / "last_known_balance.json"

# ════════════════════════════════════════════════════════════
# 싱글톤 + Rate Limit 백오프 (plan 20260503 P0 — AC1, AC2)
# ════════════════════════════════════════════════════════════

_EXCHANGE_INSTANCE: ccxt.upbit | None = None


class RateLimitExhausted(Exception):
    """업비트 429 백오프 max_retries 후에도 실패. CB는 매수 차단으로 처리."""


def _create_exchange() -> ccxt.upbit:
    """모듈 레벨 싱글톤 — ccxt 내부 throttle 상태 유지.

    이전엔 매번 새 인스턴스를 만들어 enableRateLimit=True 효과가 매번 리셋됨.
    싱글톤으로 변경하여 ccxt가 호출 빈도를 누적 추적하게 한다.
    """
    global _EXCHANGE_INSTANCE
    if _EXCHANGE_INSTANCE is None:
        access_key = os.environ.get("UPBIT_ACCESS_KEY")
        secret_key = os.environ.get("UPBIT_SECRET_KEY")
        if not access_key or not secret_key:
            raise RuntimeError("UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY 환경변수 미설정")
        _EXCHANGE_INSTANCE = ccxt.upbit({
            "apiKey": access_key,
            "secret": secret_key,
            "enableRateLimit": True,
        })
    return _EXCHANGE_INSTANCE


# plan 20260503 P3-2: 외부 모듈에서 사용할 공용 wrapper alias
def with_retry(fn, *args, max_retries: int = 3, base_delay: float = 1.0, **kwargs):
    """ccxt 호출 백오프 wrapper — _retry_on_429의 공용 alias.

    multi_trader/realtime_monitor 등 직접 ccxt 호출 경로에서 사용:
        balance = with_retry(exchange.fetch_balance)
        ticker  = with_retry(exchange.fetch_ticker, "BTC/KRW")

    주의: _execute_sell 같은 손절 경로엔 적용 금지 (lessons #3 — 즉시 체크 위배).
    """
    return _retry_on_429(fn, *args, max_retries=max_retries, base_delay=base_delay, **kwargs)


def _retry_on_429(fn, *args, max_retries: int = 3, base_delay: float = 1.0, **kwargs):
    """429 백오프 재시도 — 1s → 4s → 16s (base 4 지수).

    ccxt.RateLimitExceeded 시 Retry-After 헤더가 있으면 헤더값 우선 사용.
    max_retries 모두 실패 시 RateLimitExhausted raise.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except ccxt.RateLimitExceeded as e:
            last_exc = e
            if attempt >= max_retries:
                raise RateLimitExhausted(
                    f"{max_retries+1}회 호출 모두 429: {str(e)[:120]}"
                ) from e
            # Retry-After 헤더 우선 (ccxt 마지막 응답 보존 시)
            delay = base_delay * (4 ** attempt)
            try:
                last_response = getattr(_create_exchange(), "last_response_headers", None)
                if last_response and "Retry-After" in last_response:
                    delay = max(delay, float(last_response["Retry-After"]))
            except Exception:
                pass
            delay = min(delay, 30.0)  # 한도 30s
            throttled_print(
                "upbit_429_backoff",
                f"  [upbit] 429 → {delay:.1f}s 백오프 ({attempt+1}/{max_retries})",
                interval_sec=10,
            )
            time.sleep(delay)
        except ccxt.NetworkError as e:
            last_exc = e
            # plan 20260503 P0+ (cto Minor #7): NetworkError 첫 실패도 가시화 — 디버깅 시 추적
            throttled_print(
                "upbit_network_err",
                f"  [upbit] NetworkError attempt {attempt+1}: {str(e)[:80]}",
                interval_sec=10,
            )
            if attempt >= 1:
                raise
            time.sleep(2.0)
    if last_exc:
        raise last_exc


# ════════════════════════════════════════════════════════════
# last_known_balance — CB fallback용 캐시 (plan AC10)
# ════════════════════════════════════════════════════════════

def _save_last_known_balance(balance: dict) -> None:
    """잔고 조회 성공 시 캐시. CB는 24h 이내면 신뢰."""
    try:
        _LAST_KNOWN_BALANCE.parent.mkdir(parents=True, exist_ok=True)
        balance_with_ts = dict(balance)
        balance_with_ts["_saved_at"] = time.time()
        _LAST_KNOWN_BALANCE.write_text(
            json.dumps(balance_with_ts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass  # 캐시 저장 실패는 silent — 본 흐름 막지 않음


def load_last_known_balance(max_age_hours: float = 24) -> dict | None:
    """24h 이내 캐시된 잔고 반환. 없거나 오래되면 None.

    CB fallback에서 잔고 조회 실패 시 보수적 평가용으로 사용.
    """
    if not _LAST_KNOWN_BALANCE.exists():
        return None
    try:
        data = json.loads(_LAST_KNOWN_BALANCE.read_text(encoding="utf-8"))
        saved_at = float(data.get("_saved_at", 0))
        age_h = (time.time() - saved_at) / 3600
        if age_h > max_age_hours:
            return None
        data.pop("_saved_at", None)
        return data
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# 잔고 조회
# ════════════════════════════════════════════════════════════

def get_balance() -> dict:
    """전체 자산 잔고 조회 (KRW + BTC + 알트코인).

    Rate Limit 백오프 적용 — 429 시 1s/4s/16s 재시도 후 RateLimitExhausted.
    성공 시 workspace/last_known_balance.json에 캐시.

    Returns:
        {"krw": float, "btc": float, "btc_krw_value": float,
         "alts_krw_value": float, "total_krw": float}

    Raises:
        RateLimitExhausted: 429 max_retries 모두 실패
        ccxt.AuthenticationError, ccxt.NetworkError: 그 외 오류는 그대로 raise
    """
    exchange = _create_exchange()
    balance = _retry_on_429(exchange.fetch_balance)
    krw = float(balance.get("KRW", {}).get("free", 0))
    btc = float(balance.get("BTC", {}).get("free", 0))

    ticker = _retry_on_429(exchange.fetch_ticker, "BTC/KRW")
    btc_price = float(ticker["last"])
    btc_krw_value = btc * btc_price

    # 알트코인 평가액 합산 (lessons/20260405_1)
    alts_krw_value = 0.0
    _SKIP = {"KRW", "BTC", "info", "free", "used", "total",
             "timestamp", "datetime"}
    _DEAD_MARKETS = {"SOLO", "XCORE"}
    alt_coins: dict[str, float] = {}
    for coin, amounts in balance.items():
        if coin in _SKIP or coin in _DEAD_MARKETS or not isinstance(amounts, dict):
            continue
        total_amt = float(amounts.get("total", 0) or 0)
        if total_amt > 0:
            alt_coins[coin] = total_amt

    if alt_coins:
        try:
            markets = _retry_on_429(exchange.load_markets)
        except (RateLimitExhausted, Exception):
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
                tickers = _retry_on_429(exchange.fetch_tickers, valid_symbols)
                for coin, amt in alt_coins.items():
                    sym = f"{coin}/KRW"
                    if sym in tickers and tickers[sym].get("last"):
                        alts_krw_value += amt * float(tickers[sym]["last"])
            except RateLimitExhausted as e:
                # plan 20260503: 알트 시세 누락은 silent 산출 금지 → CB가 위험 평가 못 함
                # 호출자가 last_known_balance fallback 사용하도록 raise
                raise
            except Exception as e:
                throttled_print(
                    "balance_ticker_fail",
                    f"  [잔고] 알트 시세 일괄조회 실패: {e} — KRW+BTC만으로 산출",
                    interval_sec=60,
                )

    result = {
        "krw": krw,
        "btc": btc,
        "btc_price": btc_price,
        "btc_krw_value": round(btc_krw_value, 0),
        "alts_krw_value": round(alts_krw_value, 0),
        "total_krw": round(krw + btc_krw_value + alts_krw_value, 0),
    }
    _save_last_known_balance(result)
    return result


# ════════════════════════════════════════════════════════════
# 매수/매도
# ════════════════════════════════════════════════════════════

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
    order = _retry_on_429(
        exchange.create_market_buy_order,
        "BTC/KRW", None, params={"cost": amount_krw},
    )
    return _parse_order(order)


def sell_market(amount_btc: float) -> dict:
    """시장가 매도."""
    if amount_btc <= 0:
        raise ValueError(f"매도 수량 0 이하: {amount_btc}")

    exchange = _create_exchange()
    order = _retry_on_429(exchange.create_market_sell_order, "BTC/KRW", amount_btc)
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
