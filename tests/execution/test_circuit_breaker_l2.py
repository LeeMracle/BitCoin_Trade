"""CB L2 + L1 자동해제 단위 테스트 — ADR 20260408_1 기반.

상태 파일은 tmp_path로 완전 격리하여 테스트 순서 독립성 보장.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.execution import circuit_breaker as cb


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """각 테스트마다 독립된 상태 파일 사용."""
    state_file = tmp_path / "circuit_breaker_state.json"
    monkeypatch.setattr(cb, "STATE_FILE", state_file)
    yield state_file


def _fake_config(monkeypatch, *, enabled=True, l1=-0.20, l2=-0.25,
                 initial=300_000, resume_pct=0.95):
    """config 상수를 임시로 오버라이드."""
    import services.execution.config as cfg
    monkeypatch.setattr(cfg, "CIRCUIT_BREAKER_ENABLED", enabled, raising=False)
    monkeypatch.setattr(cfg, "CIRCUIT_BREAKER_THRESHOLD", l1, raising=False)
    monkeypatch.setattr(cfg, "CIRCUIT_BREAKER_L2_THRESHOLD", l2, raising=False)
    monkeypatch.setattr(cfg, "CIRCUIT_BREAKER_INITIAL_CAPITAL", initial, raising=False)
    monkeypatch.setattr(cfg, "CIRCUIT_BREAKER_L1_AUTO_RESUME_PCT", resume_pct, raising=False)


# ── L1 기본 ───────────────────────────────────────────────

def test_l1_not_triggered_above_threshold(monkeypatch):
    _fake_config(monkeypatch)
    assert cb.check_and_trigger(250_000) is False  # -16.7% > -20%
    assert cb.is_triggered() is False


def test_l1_triggered_at_threshold(monkeypatch):
    _fake_config(monkeypatch)
    # 300k * 0.8 = 240k — 경계값
    assert cb.check_and_trigger(240_000) is True
    assert cb.is_triggered() is True


def test_l1_not_retrigger(monkeypatch):
    _fake_config(monkeypatch)
    assert cb.check_and_trigger(240_000) is True
    # 2회차 호출은 False (이미 발동 중)
    assert cb.check_and_trigger(230_000) is False


# ── L2 ────────────────────────────────────────────────────

def test_l2_not_triggered_above_threshold(monkeypatch):
    _fake_config(monkeypatch)
    assert cb.check_and_trigger_l2(230_000) is False  # -23% > -25%
    assert cb.is_l2_triggered() is False


def test_l2_triggered_at_threshold(monkeypatch):
    _fake_config(monkeypatch)
    # 300k * 0.75 = 225k
    assert cb.check_and_trigger_l2(225_000) is True
    assert cb.is_l2_triggered() is True


def test_l2_not_retrigger(monkeypatch):
    _fake_config(monkeypatch)
    assert cb.check_and_trigger_l2(220_000) is True
    assert cb.check_and_trigger_l2(210_000) is False


def test_l2_independent_of_l1(monkeypatch):
    """L2는 L1 발동 여부와 무관하게 독립 트리거 가능."""
    _fake_config(monkeypatch)
    # 처음부터 급락해서 L1 건너뛰고 바로 L2까지 도달
    cb.check_and_trigger(220_000)  # L1 발동
    assert cb.check_and_trigger_l2(220_000) is True


# ── L1 자동 해제 ──────────────────────────────────────────

def test_l1_auto_resume_when_recovered(monkeypatch):
    _fake_config(monkeypatch)
    cb.check_and_trigger(240_000)
    assert cb.is_triggered() is True
    # 95% 회복 = 285,000 이상
    assert cb.check_l1_auto_resume(285_000) is True
    assert cb.is_triggered() is False


def test_l1_auto_resume_blocked_below_threshold(monkeypatch):
    _fake_config(monkeypatch)
    cb.check_and_trigger(240_000)
    assert cb.check_l1_auto_resume(280_000) is False  # 93.3% < 95%
    assert cb.is_triggered() is True


def test_l1_auto_resume_blocked_when_l2_active(monkeypatch):
    """L2 발동 중이면 L1 자동 해제 금지."""
    _fake_config(monkeypatch)
    cb.check_and_trigger(220_000)       # L1
    cb.check_and_trigger_l2(220_000)    # L2
    # 총자산이 95% 회복되어도 L1 자동 해제 금지
    assert cb.check_l1_auto_resume(290_000) is False
    assert cb.is_triggered() is True
    assert cb.is_l2_triggered() is True


def test_l1_auto_resume_noop_when_not_triggered(monkeypatch):
    _fake_config(monkeypatch)
    assert cb.check_l1_auto_resume(290_000) is False


# ── disabled 경로 ─────────────────────────────────────────

def test_disabled_blocks_all_triggers(monkeypatch):
    _fake_config(monkeypatch, enabled=False)
    assert cb.check_and_trigger(100_000) is False
    assert cb.check_and_trigger_l2(100_000) is False
    assert cb.check_l1_auto_resume(300_000) is False


# ── 상태 스키마 ───────────────────────────────────────────

def test_state_file_schema_after_l1(monkeypatch, _isolated_state):
    _fake_config(monkeypatch)
    cb.check_and_trigger(240_000)
    data = json.loads(_isolated_state.read_text(encoding="utf-8"))
    assert data["triggered"] is True
    assert data["triggered_at"] is not None
    assert data["total_krw_at_trigger"] == 240_000
    assert data["l2_triggered"] is False


def test_state_file_schema_after_l2(monkeypatch, _isolated_state):
    _fake_config(monkeypatch)
    cb.check_and_trigger_l2(220_000)
    data = json.loads(_isolated_state.read_text(encoding="utf-8"))
    assert data["l2_triggered"] is True
    assert data["l2_triggered_at"] is not None
    assert data["l2_total_krw_at_trigger"] == 220_000


def test_legacy_state_file_backward_compat(monkeypatch, _isolated_state):
    """기존(L1 only) 스키마 로드 시 L2 필드가 기본값으로 보강된다."""
    _fake_config(monkeypatch)
    _isolated_state.parent.mkdir(parents=True, exist_ok=True)
    _isolated_state.write_text(json.dumps({
        "triggered": True,
        "triggered_at": "2026-04-05T21:01:00+00:00",
        "total_krw_at_trigger": 212_497,
    }), encoding="utf-8")
    state = cb.get_status()
    assert state["triggered"] is True
    assert state["l2_triggered"] is False  # 보강됨
    assert "l2_triggered_at" in state


def test_l1_auto_resume_records_timestamp(monkeypatch, _isolated_state):
    _fake_config(monkeypatch)
    cb.check_and_trigger(240_000)
    cb.check_l1_auto_resume(290_000)
    data = json.loads(_isolated_state.read_text(encoding="utf-8"))
    assert data["triggered"] is False
    assert data["l1_auto_resumed_at"] is not None
    assert data["l1_auto_resumed_total_krw"] == 290_000
