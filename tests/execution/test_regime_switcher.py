# -*- coding: utf-8 -*-
"""P5-04 레짐 자동 전환 단위 테스트 (≥ 10 케이스).

참조: services/execution/regime_switcher.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from services.execution.regime_switcher import (
    Regime,
    RegimeDecision,
    decide_regime,
    format_notification,
    load_state,
    save_state,
    should_notify,
    update_with_decision,
)


# ══════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════

def _decision(regime: str, fg: int = 50) -> RegimeDecision:
    """테스트용 RegimeDecision 팩토리."""
    btc = 100_000_000.0
    ema200 = 90_000_000.0 if regime == "BULL" else 110_000_000.0
    return RegimeDecision(
        regime=Regime(regime),
        reason="test",
        btc_close=btc,
        ema200=ema200,
        sma50=95_000_000.0,
        fg=fg,
        decided_at_ts=int(time.time()),
    )


def _fresh_state() -> dict:
    return {
        "current": "UNKNOWN",
        "since_ts": 0,
        "prev": "UNKNOWN",
        "last_decided_ts": 0,
        "recent_signals": [],
        "enabled": False,
    }


# ══════════════════════════════════════════════════════════
# 1. decide_regime — BULL 케이스
# ══════════════════════════════════════════════════════════

class TestDecideRegimeBull:
    def test_bull_basic(self):
        """BTC > EMA200 AND F&G=55 → BULL."""
        dec = decide_regime(120_000_000, 105_000_000, 110_000_000, 55)
        assert dec.regime == Regime.BULL

    def test_bull_fg_boundary_40(self):
        """F&G=40(경계값) → BULL (>=40 조건)."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 40)
        assert dec.regime == Regime.BULL

    def test_bull_reason_contains_ema200(self):
        """BULL reason 문자열에 EMA200 수치가 포함된다."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 50)
        assert "EMA200" in dec.reason


# ══════════════════════════════════════════════════════════
# 2. decide_regime — BEAR 케이스
# ══════════════════════════════════════════════════════════

class TestDecideRegimeBear:
    def test_bear_ema200_violation(self):
        """BTC < EMA200 → BEAR (F&G 관계없이)."""
        dec = decide_regime(90_000_000, 105_000_000, 110_000_000, 60)
        assert dec.regime == Regime.BEAR

    def test_bear_fg_violation(self):
        """BTC > EMA200 이지만 F&G < 20 → BEAR."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 15)
        assert dec.regime == Regime.BEAR

    def test_bear_fg_boundary_19(self):
        """F&G=19(경계값) → BEAR (< 20 조건)."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 19)
        assert dec.regime == Regime.BEAR

    def test_bear_both_conditions(self):
        """EMA200 위반 + F&G < 20 동시 — BEAR이고 reason에 두 조건 모두 포함."""
        dec = decide_regime(90_000_000, 100_000_000, 110_000_000, 10)
        assert dec.regime == Regime.BEAR
        assert "EMA200" in dec.reason
        assert "F&G" in dec.reason


# ══════════════════════════════════════════════════════════
# 3. decide_regime — SIDEWAYS 케이스
# ══════════════════════════════════════════════════════════

class TestDecideRegimeSideways:
    def test_sideways_between_conditions(self):
        """BTC > EMA200 이지만 F&G=30 (20≤fg<40) → SIDEWAYS."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 30)
        assert dec.regime == Regime.SIDEWAYS

    def test_sideways_fg_boundary_20(self):
        """F&G=20(경계값) → SIDEWAYS (BTC>EMA200 전제, bear 조건 fg<20 미충족)."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 20)
        assert dec.regime == Regime.SIDEWAYS

    def test_sideways_fg_boundary_39(self):
        """F&G=39(경계값) → SIDEWAYS (BULL fg>=40 미충족)."""
        dec = decide_regime(120_000_000, 100_000_000, 110_000_000, 39)
        assert dec.regime == Regime.SIDEWAYS


# ══════════════════════════════════════════════════════════
# 4. 히스테리시스 — 2회 동일은 전환 안 함 / 3회부터 전환
# ══════════════════════════════════════════════════════════

class TestHysteresis:
    def test_two_same_signals_no_switch(self, tmp_path):
        """동일 신호 2회 → current 변경 없음."""
        state = _fresh_state()
        state["current"] = "UNKNOWN"
        for _ in range(2):
            state = update_with_decision(_decision("BULL"), state=state, path=tmp_path / "s.json")
        # 2회만으로는 전환 발생 안 함
        assert state["current"] == "UNKNOWN"

    def test_three_same_signals_switch(self, tmp_path):
        """동일 신호 3회 → current 전환."""
        state = _fresh_state()
        state["current"] = "UNKNOWN"
        for _ in range(3):
            state = update_with_decision(_decision("BULL"), state=state, path=tmp_path / "s.json")
        assert state["current"] == "BULL"

    def test_mixed_signals_no_switch(self, tmp_path):
        """BULL/BEAR 번갈아 신호 → 전환 없음."""
        state = _fresh_state()
        state["current"] = "UNKNOWN"
        for regime in ["BULL", "BEAR", "BULL"]:
            state = update_with_decision(_decision(regime), state=state, path=tmp_path / "s.json")
        assert state["current"] == "UNKNOWN"

    def test_prev_updated_on_switch(self, tmp_path):
        """전환 시 prev에 이전 current가 저장된다."""
        state = _fresh_state()
        state["current"] = "SIDEWAYS"
        for _ in range(3):
            state = update_with_decision(_decision("BEAR"), state=state, path=tmp_path / "s.json")
        assert state["prev"] == "SIDEWAYS"
        assert state["current"] == "BEAR"

    def test_switch_then_back_requires_three(self, tmp_path):
        """BEAR → BULL 전환 후 다시 BEAR로 돌아가려면 BEAR 신호 3회 필요."""
        state = _fresh_state()
        state["current"] = "UNKNOWN"
        # BULL 3회 → current=BULL
        for _ in range(3):
            state = update_with_decision(_decision("BULL"), state=state, path=tmp_path / "s.json")
        assert state["current"] == "BULL"
        # BEAR 2회 → 아직 BULL 유지
        for _ in range(2):
            state = update_with_decision(_decision("BEAR"), state=state, path=tmp_path / "s.json")
        assert state["current"] == "BULL"
        # BEAR 1회 더 (총 3회) → BEAR 전환
        state = update_with_decision(_decision("BEAR"), state=state, path=tmp_path / "s.json")
        assert state["current"] == "BEAR"


# ══════════════════════════════════════════════════════════
# 5. load/save 라운드트립
# ══════════════════════════════════════════════════════════

class TestLoadSaveRoundtrip:
    def test_roundtrip(self, tmp_path):
        """저장 후 로드하면 동일 내용이 반환된다."""
        state = {
            "current": "BEAR",
            "since_ts": 1744934400,
            "prev": "SIDEWAYS",
            "last_decided_ts": 1744934400,
            "recent_signals": ["BEAR", "BEAR", "BEAR"],
            "enabled": False,
        }
        p = tmp_path / "regime_state.json"
        save_state(state, path=p)
        loaded = load_state(path=p)
        assert loaded["current"] == "BEAR"
        assert loaded["recent_signals"] == ["BEAR", "BEAR", "BEAR"]

    def test_missing_file_returns_default(self, tmp_path):
        """파일이 없으면 기본 상태를 반환한다."""
        p = tmp_path / "nonexistent.json"
        state = load_state(path=p)
        assert state["current"] == "UNKNOWN"
        assert state["recent_signals"] == []

    def test_corrupt_file_returns_default(self, tmp_path):
        """JSON 파싱 실패 시 기본 상태를 반환한다."""
        p = tmp_path / "bad.json"
        p.write_text("{invalid json", encoding="utf-8")
        state = load_state(path=p)
        assert state["current"] == "UNKNOWN"


# ══════════════════════════════════════════════════════════
# 6. should_notify
# ══════════════════════════════════════════════════════════

class TestShouldNotify:
    def test_notify_on_regime_change(self):
        """레짐이 바뀌면 True."""
        prev = {"current": "BEAR"}
        new = {"current": "BULL"}
        assert should_notify(prev, new) is True

    def test_no_notify_same_regime(self):
        """레짐이 그대로면 False."""
        prev = {"current": "BULL"}
        new = {"current": "BULL"}
        assert should_notify(prev, new) is False

    def test_notify_unknown_to_regime(self):
        """UNKNOWN → BULL 전환 시 True."""
        prev = {"current": "UNKNOWN"}
        new = {"current": "BULL"}
        assert should_notify(prev, new) is True


# ══════════════════════════════════════════════════════════
# 7. format_notification
# ══════════════════════════════════════════════════════════

class TestFormatNotification:
    def test_format_contains_old_new(self):
        """메시지에 old/new 레짐과 사유가 포함된다."""
        msg = format_notification("BEAR", "BULL", "BTC>EMA200 AND F&G=45>=40")
        assert "BEAR" in msg
        assert "BULL" in msg
        assert "BTC>EMA200" in msg

    def test_format_arrow(self):
        """화살표(→)가 old → new 순서로 존재한다."""
        msg = format_notification("SIDEWAYS", "BEAR", "BTC<EMA200")
        assert "SIDEWAYS → BEAR" in msg

    def test_format_bata_prefix(self):
        """[BATA] 접두사가 포함된다."""
        msg = format_notification("BULL", "SIDEWAYS", "F&G=35<40")
        assert "[BATA]" in msg


# ══════════════════════════════════════════════════════════
# 8. recent_signals 5개 캡
# ══════════════════════════════════════════════════════════

class TestRecentSignalsCap:
    def test_signals_capped_at_five(self, tmp_path):
        """10회 판정해도 recent_signals는 최대 5개다."""
        state = _fresh_state()
        for i in range(10):
            regime = "BULL" if i % 2 == 0 else "SIDEWAYS"
            state = update_with_decision(
                _decision(regime), state=state, path=tmp_path / "s.json"
            )
        assert len(state["recent_signals"]) <= 5

    def test_signals_order_fifo(self, tmp_path):
        """오래된 신호가 먼저 제거된다(FIFO)."""
        state = _fresh_state()
        # 5개 채움
        for _ in range(5):
            state = update_with_decision(
                _decision("BEAR"), state=state, path=tmp_path / "s.json"
            )
        # BULL 신호 추가 → 첫 BEAR가 빠지고 마지막이 BULL
        state = update_with_decision(
            _decision("BULL"), state=state, path=tmp_path / "s.json"
        )
        assert state["recent_signals"][-1] == "BULL"
        assert len(state["recent_signals"]) == 5
