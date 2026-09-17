"""연패 쿨다운 — 매도즉시 경로와 주기 경로의 정합 (lessons #51, ADR 20260829-1).

로직을 복제하지 않고 **실제 RealtimeMonitor 메서드**를 호출한다 (lessons #49:
복제 시뮬레이션은 실제 파일과 무관하게 통과한다). 네트워크/파일 I/O 만 막는다.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

# 로컬 개발 PC 에 ML 의존성이 없어도 모듈 import 가 되도록 (서버 venv 에는 존재)
for _n in ("joblib", "lightgbm", "sklearn", "xgboost"):
    try:
        __import__(_n)
    except ImportError:
        sys.modules[_n] = types.ModuleType(_n)

from services.execution import realtime_monitor as rm  # noqa: E402
from services.execution.config import (  # noqa: E402
    CONSEC_LOSS_COOLDOWN_HOURS, CONSEC_LOSS_LIMIT,
)


def _trade(day: int, ret: float) -> dict:
    return {"symbol": f"T{day}/KRW", "entry_date": f"2026-09-{day:02d} 00:00",
            "entry_price": 100.0, "exit_date": f"2026-09-{day:02d} 12:00",
            "exit_price": 100.0 * (1 + ret / 100), "return_pct": ret,
            "exit_reason": "trail_stop" if ret <= 0 else "tp2_full_exit"}


def _state(n_losses: int) -> dict:
    closed = [_trade(3, 7.5)] + [_trade(4 + i, -10.0) for i in range(n_losses)]
    return {"positions": {}, "closed_trades": closed, "strategy_start": "2026-09-02"}


@pytest.fixture
def mon(monkeypatch):
    sent: list[str] = []

    async def _send(msg, *a, **k):
        sent.append(msg)

    monkeypatch.setattr(rm, "save_state", lambda s: None)
    monkeypatch.setattr(rm, "send", _send)
    monkeypatch.setattr(rm, "get_balance", lambda: {"krw": 0, "total_krw": 0})
    m = rm.RealtimeMonitor.__new__(rm.RealtimeMonitor)
    m.sent = sent
    return m


def _run(coro):
    return asyncio.run(coro)


def test_limit_is_spec_value():
    assert CONSEC_LOSS_LIMIT == 5  # CLAUDE.md "5연패 72h 중단"
    assert CONSEC_LOSS_COOLDOWN_HOURS == 72


def test_below_limit_does_not_impose(mon):
    """2026-09-14 사고 재현: 3·4연패에서 걸리면 안 된다."""
    for n in (3, CONSEC_LOSS_LIMIT - 1):
        mon.state = _state(n)
        assert _run(mon._check_consec_loss_immediate()) is False
        assert not mon.state.get("cooldown_until")


def test_limit_imposes_with_fingerprint(mon):
    mon.state = _state(CONSEC_LOSS_LIMIT)
    before = time.time()
    assert _run(mon._check_consec_loss_immediate()) is True
    cd = mon.state["cooldown_until"]
    assert abs(cd - (before + 3600 * CONSEC_LOSS_COOLDOWN_HOURS)) < 60
    assert mon.state["consec_loss_alerted_until"] == cd  # lessons #30 invariant
    assert mon.state["cooldown_imposed_for"] == mon._consec_streak_tail() != ""


def test_active_cooldown_not_overwritten(mon):
    mon.state = _state(CONSEC_LOSS_LIMIT + 1)
    active = time.time() + 3600
    mon.state["cooldown_until"] = active
    assert _run(mon._check_consec_loss_immediate()) is False
    assert mon.state["cooldown_until"] == active


def _expire(state: dict) -> None:
    past = time.time() - 60
    state["cooldown_until"] = past
    state["consec_loss_alerted_until"] = past


def test_periodic_releases_after_immediate_imposition(mon):
    """즉시 경로가 건 72h 가 만료되면 주기 경로는 **해제**해야 한다 (재부과 X)."""
    mon.state = _state(CONSEC_LOSS_LIMIT)
    _run(mon._check_consec_loss_immediate())
    _expire(mon.state)
    _run(mon._send_periodic_report())
    assert mon.state["cooldown_until"] == 0
    assert mon.state.get("consec_loss_floor_date")
    assert mon.state.get("consec_loss_release_count") == 1
    assert "cooldown_imposed_for" not in mon.state
    assert not any("자동 연장" in s for s in mon.sent)


def test_without_fingerprint_periodic_reimposes(mon):
    """구 즉시경로(지문 미기록)가 왜 72h 를 이중으로 걸었는지 — 실제 주기경로로 입증."""
    mon.state = _state(CONSEC_LOSS_LIMIT)
    _run(mon._check_consec_loss_immediate())
    mon.state.pop("cooldown_imposed_for")
    _expire(mon.state)
    _run(mon._send_periodic_report())
    assert mon.state["cooldown_until"] > time.time() + 3600 * (CONSEC_LOSS_COOLDOWN_HOURS - 1)
