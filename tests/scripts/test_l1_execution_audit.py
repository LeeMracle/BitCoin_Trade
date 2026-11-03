"""L1 실행 검증 스크립트(plan 20261030_1)의 역방향 테스트 — 결함을 주입하면 FAIL 이 나는가.

lessons #44: 룰은 통과가 아니라 **실패를 잡는지**로 검증한다.
각 검사마다 정상 케이스 PASS + 결함 주입 케이스 FAIL 을 짝으로 둔다.

재현: py -m pytest tests/scripts/test_l1_execution_audit.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import l1_execution_audit as A  # noqa: E402

T0 = datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc)


def order(side, minutes, qty, price):
    return {"ts": T0 + timedelta(minutes=minutes), "side": side, "qty": qty, "price": price}


def trade(entry_price=100.0, ret=None, reason="trail_stop", exit_min=120):
    return {"symbol": "X/KRW", "entry_date": T0.strftime("%Y-%m-%d %H:%M"),
            "exit_date": (T0 + timedelta(minutes=exit_min)).strftime("%Y-%m-%d %H:%M"),
            "entry_price": entry_price, "return_pct": ret, "exit_reason": reason}


# ── 매칭 ──

def test_match_excludes_next_entry_sells():
    orders = [order("buy", 0, 10, 100), order("sell", 60, 10, 94),
              order("buy", 62, 10, 100), order("sell", 63, 10, 110)]
    m = A.match_orders(trade(exit_min=60), orders)
    assert [s["price"] for s in m["sells"]] == [94]


def test_match_same_minute_reentry_keeps_last_sell():
    """API3 10-06 회귀: 청산 매도와 재진입 매수가 같은 분 — 매도는 첫 거래에 속한다."""
    base = [order("buy", 0, 10, 100), order("sell", 30, 5, 105.6)]
    tp2 = {**order("sell", 60, 5, 110.1), "ts": T0 + timedelta(minutes=60, seconds=10)}
    rebuy = {**order("buy", 60, 10, 110), "ts": T0 + timedelta(minutes=60, seconds=40)}
    m = A.match_orders(trade(exit_min=60, reason="tp2_full_exit"), base + [tp2, rebuy])
    assert [s["price"] for s in m["sells"]] == [105.6, 110.1]


def test_match_no_buy_returns_none():
    m = A.match_orders(trade(), [order("buy", 300, 10, 100)])
    assert m["buy"] is None


# ── C1 ──

def test_c1_pass_and_fail():
    b = order("buy", 0, 10, 100)
    assert A.check_c1(trade(entry_price=100.3), b)[0] == A.PASS
    assert A.check_c1(trade(entry_price=101.0), b)[0] == A.FAIL   # lessons #43 신호가 기록 재현


# ── C2 ──

def test_c2_matches_record_realized_formula():
    b, s = order("buy", 0, 10, 100), [order("sell", 60, 10, 94)]
    r = A.realized_return_pct(b, s)
    assert abs(r - (94 * 0.9995 - 100 * 1.0005) / 100 * 100) < 1e-9
    assert A.check_c2(trade(ret=round(r, 2)), b, s)[0] == A.PASS


def test_c2_fail_on_mismatch_and_residual():
    b = order("buy", 0, 10, 100)
    assert A.check_c2(trade(ret=+2.0), b, [order("sell", 60, 10, 94)])[0] == A.FAIL
    # 매도 수량 절반만 — TP 후 잔량이 기록 없이 사라진 경우 (lessons #45)
    assert A.check_c2(trade(ret=2.6), b, [order("sell", 60, 5, 105.5)])[0] == A.FAIL


# ── C3 ──

def test_c3_cap_boundary():
    b = order("buy", 0, 10, 100)
    assert A.check_c3(b, [order("sell", 9, 10, 93.5)], 0.06)[0] == A.PASS   # -6.5%
    assert A.check_c3(b, [order("sell", 9, 10, 92.5)], 0.06)[0] == A.FAIL   # -7.5%
    assert A.check_c3(b, [order("sell", 9, 10, 92.5)], 0.10)[0] == A.PASS   # 기존 캡


# ── C4 ──

def test_c4_tp2_full_exit_pass():
    b = order("buy", 0, 10, 100)
    s = [order("sell", 10, 5, 105.6), order("sell", 20, 5, 110.1)]
    assert A.check_c4(trade(reason="tp2_full_exit"), b, s)[0] == A.PASS


def test_c4_fail_on_early_tp_or_bad_ratio():
    b = order("buy", 0, 10, 100)
    early = [order("sell", 10, 5, 103.0), order("sell", 20, 5, 95)]
    assert A.check_c4(trade(), b, early)[0] == A.FAIL
    lopsided = [order("sell", 10, 8, 105.6), order("sell", 20, 2, 95)]
    assert A.check_c4(trade(), b, lopsided)[0] == A.FAIL


def test_c4_trail_last_leg_not_tp():
    b = order("buy", 0, 10, 100)
    s = [order("sell", 10, 5, 105.6), order("sell", 20, 5, 99)]
    assert A.check_c4(trade(), b, s)[0] == A.PASS


# ── C5 ──

def test_c5_signal_and_regime():
    b = order("buy", 0, 10, 100)
    log0 = T0 - timedelta(days=1)
    sig = [T0 + timedelta(minutes=2)]          # 버퍼링으로 늦게 찍힌 로그
    assert A.check_c5(trade(), b, sig, log0, True, False)[0] == A.PASS
    assert A.check_c5(trade(), b, [], log0, True, False)[0] == A.FAIL
    assert A.check_c5(trade(), b, sig, log0, False, False)[0] == A.FAIL
    assert A.check_c5(trade(), b, sig, log0, True, True)[0] == A.FAIL
    assert A.check_c5(trade(), b, sig, T0 + timedelta(days=1), True, False)[0] == A.NA


def test_regime_on_uses_ema():
    days = [T0.replace(hour=0, minute=0) - timedelta(days=300 - i) for i in range(301)]
    up = [(d, 100.0 + i) for i, d in enumerate(days)]
    down = [(d, 400.0 - i) for i, d in enumerate(days)]
    assert A.regime_on(T0, up) is True
    assert A.regime_on(T0, down) is False


# ── C6 ──

def test_c6_weight_and_slots():
    b = order("buy", 0, 1, 30_000)
    assert A.check_c6(b, 300_000, 5)[0] == A.PASS
    assert A.check_c6(b, 250_000, 5)[0] == A.FAIL     # 12%
    assert A.check_c6(b, 300_000, 16)[0] == A.FAIL
