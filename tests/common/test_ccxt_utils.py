"""tests.common.test_ccxt_utils — 공용 None-safe 헬퍼 단위 테스트.

실행:
    pytest tests/common/test_ccxt_utils.py -v
    python -m unittest tests.common.test_ccxt_utils

대상:
    services.common.ccxt_utils.fmt_num
    services.common.ccxt_utils.resolve_fill
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from services.common.ccxt_utils import fmt_num, resolve_fill


class TestFmtNum(unittest.TestCase):
    """fmt_num — None-safe 숫자 포매터."""

    def test_none_returns_fallback(self):
        self.assertEqual(fmt_num(None), "N/A")

    def test_none_custom_fallback(self):
        self.assertEqual(fmt_num(None, fallback="-"), "-")

    def test_integer(self):
        self.assertEqual(fmt_num(1234567), "1,234,567")

    def test_float(self):
        self.assertEqual(fmt_num(0.00055193, ",.8f"), "0.00055193")

    def test_zero(self):
        self.assertEqual(fmt_num(0), "0")

    def test_negative(self):
        self.assertEqual(fmt_num(-29.2134, ",.2f"), "-29.21")

    def test_percent(self):
        self.assertEqual(fmt_num(0.35, ".0%"), "35%")

    def test_invalid_type_returns_fallback(self):
        # 문자열처럼 format spec이 안 맞는 경우
        self.assertEqual(fmt_num("abc", ",.0f"), "N/A")

    def test_bool_is_treated_as_int(self):
        # bool도 int 계열이라 포매팅 가능
        self.assertEqual(fmt_num(True, "d"), "1")


class TestResolveFillFromOrder(unittest.TestCase):
    """resolve_fill — order dict에 직접 값이 있는 케이스 (API 호출 없음)."""

    def test_average_and_cost_present(self):
        exchange = MagicMock()
        order = {"id": "X1", "average": 105807000.0, "price": None,
                 "cost": 58398.0, "amount": 0.00055193}
        cost, price = resolve_fill(exchange, order, "BTC/KRW",
                                   amount_hint=0.00055193)
        self.assertEqual(cost, 58398.0)
        self.assertEqual(price, 105807000.0)
        # 이미 값이 있으므로 fetch_order 호출되지 않아야 함
        exchange.fetch_order.assert_not_called()

    def test_price_fallback_to_price_field(self):
        exchange = MagicMock()
        order = {"id": "X2", "average": None, "price": 100.0, "cost": 1000.0}
        cost, price = resolve_fill(exchange, order, "ALT/KRW",
                                   amount_hint=10)
        self.assertEqual(cost, 1000.0)
        self.assertEqual(price, 100.0)


class TestResolveFillFetchOrderRecovery(unittest.TestCase):
    """resolve_fill — fetch_order 재조회로 복구하는 케이스."""

    def test_recover_via_fetch_order(self):
        exchange = MagicMock()
        exchange.fetch_order.return_value = {
            "id": "R1",
            "average": 105807000.0,
            "price": None,
            "cost": 58398.0,
            "filled": 0.00055193,
        }
        order = {"id": "R1", "average": None, "price": None, "cost": None,
                 "amount": 0.00055193}
        cost, price = resolve_fill(exchange, order, "BTC/KRW",
                                   amount_hint=0.00055193, wait_seconds=0)
        self.assertEqual(cost, 58398.0)
        self.assertEqual(price, 105807000.0)
        exchange.fetch_order.assert_called_once_with("R1", "BTC/KRW")

    def test_fetch_order_failure_falls_through(self):
        exchange = MagicMock()
        exchange.fetch_order.side_effect = Exception("rate limit")
        exchange.fetch_ticker.return_value = {"last": 105000000.0}
        order = {"id": "R2", "average": None, "price": None, "cost": None}
        cost, price = resolve_fill(exchange, order, "BTC/KRW",
                                   amount_hint=0.001, wait_seconds=0)
        # fetch_order 실패 → ticker 기반 추정
        self.assertEqual(price, 105000000.0)
        self.assertEqual(cost, 105000000.0 * 0.001)


class TestResolveFillTickerEstimate(unittest.TestCase):
    """resolve_fill — ticker 기반 추정 케이스."""

    def test_estimate_from_ticker(self):
        exchange = MagicMock()
        exchange.fetch_order.return_value = {
            "average": None, "price": None, "cost": None, "filled": None,
        }
        exchange.fetch_ticker.return_value = {"last": 50000.0}
        order = {"id": "T1", "average": None, "price": None, "cost": None}
        cost, price = resolve_fill(exchange, order, "ETH/KRW",
                                   amount_hint=2.0, wait_seconds=0)
        self.assertEqual(price, 50000.0)
        self.assertEqual(cost, 100000.0)

    def test_all_failures_returns_none(self):
        exchange = MagicMock()
        exchange.fetch_order.side_effect = Exception("x")
        exchange.fetch_ticker.side_effect = Exception("y")
        order = {"id": "F1", "average": None, "price": None, "cost": None}
        cost, price = resolve_fill(exchange, order, "X/KRW",
                                   amount_hint=None, wait_seconds=0)
        self.assertIsNone(price)
        self.assertIsNone(cost)


class TestResolveFillNoIdSkipsRefetch(unittest.TestCase):
    """order.id가 없으면 fetch_order 호출 자체를 하지 않아야 한다."""

    def test_no_id_no_refetch(self):
        exchange = MagicMock()
        exchange.fetch_ticker.return_value = {"last": 999.0}
        order = {"average": None, "price": None, "cost": None}  # no id
        cost, price = resolve_fill(exchange, order, "X/KRW",
                                   amount_hint=1.0, wait_seconds=0)
        self.assertEqual(price, 999.0)
        exchange.fetch_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
