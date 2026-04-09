"""P5-28 VB 개선 필터 단위 테스트.

참조: docs/00.보고/20260409_일일작업.md (P4-05/06 DoD + P5-28)

필터 A(하락장)는 realtime_monitor에 통합되어 있어 이 파일은 B/C/D의
순수 함수 단위 테스트만 담당한다.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from services.execution.vb_filters import (
    compute_dead_symbols,
    iso_week,
    weekly_count_exceeded,
    bump_weekly_count,
    recent_consecutive_losses,
    is_in_loss_cooldown,
    set_loss_cooldown,
)


# ══════════════════════════════════════════════════════════
# B. 데드 종목 블랙리스트
# ══════════════════════════════════════════════════════════

class TestComputeDeadSymbols:
    def test_empty_history(self):
        assert compute_dead_symbols([]) == []

    def test_single_zero_not_dead(self):
        history = [{"symbol": "ELF/KRW", "return_pct": 0}]
        assert compute_dead_symbols(history, threshold=3) == []

    def test_three_consecutive_zeros_dead(self):
        """ELF 3회 연속 0% → 데드 (실제 04-07 케이스)."""
        history = [
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
        ]
        assert compute_dead_symbols(history, threshold=3) == ["ELF/KRW"]

    def test_recovery_after_zeros_not_dead(self):
        """0/0/0/+2% → 마지막이 0이 아니므로 데드 아님."""
        history = [
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 2.0},
        ]
        assert compute_dead_symbols(history, threshold=3) == []

    def test_multiple_dead_symbols(self):
        history = [
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "AAA/KRW", "return_pct": 0},
            {"symbol": "AAA/KRW", "return_pct": 0},
            {"symbol": "AAA/KRW", "return_pct": 0},
            {"symbol": "POLYX/KRW", "return_pct": 5.0},
        ]
        assert compute_dead_symbols(history, threshold=3) == ["AAA/KRW", "ELF/KRW"]

    def test_polyx_winner_not_dead(self):
        """POLYX가 수익을 내면 당연히 데드 아님."""
        history = [{"symbol": "POLYX/KRW", "return_pct": r} for r in [14.92, 5.83, -3.75, -3.27, 11.78]]
        assert compute_dead_symbols(history, threshold=3) == []


# ══════════════════════════════════════════════════════════
# C. 종목 집중도 캡 (주간)
# ══════════════════════════════════════════════════════════

class TestWeeklyCountCap:
    def test_iso_week_format(self):
        dt = datetime(2026, 4, 9, tzinfo=timezone.utc)
        assert iso_week(dt) == "2026-W15"

    def test_empty_weekly_count(self):
        wc = {}
        assert not weekly_count_exceeded(wc, "POLYX/KRW", 3)

    def test_under_limit(self):
        wc = {"2026-W15": {"POLYX/KRW": 2}}
        dt = datetime(2026, 4, 9, tzinfo=timezone.utc)
        assert not weekly_count_exceeded(wc, "POLYX/KRW", 3, dt)

    def test_at_limit_exceeded(self):
        wc = {"2026-W15": {"POLYX/KRW": 3}}
        dt = datetime(2026, 4, 9, tzinfo=timezone.utc)
        assert weekly_count_exceeded(wc, "POLYX/KRW", 3, dt)

    def test_bump_increments(self):
        wc = {}
        dt = datetime(2026, 4, 9, tzinfo=timezone.utc)
        bump_weekly_count(wc, "POLYX/KRW", dt)
        bump_weekly_count(wc, "POLYX/KRW", dt)
        bump_weekly_count(wc, "POLYX/KRW", dt)
        assert wc["2026-W15"]["POLYX/KRW"] == 3
        assert weekly_count_exceeded(wc, "POLYX/KRW", 3, dt)

    def test_different_week_isolated(self):
        wc = {}
        dt1 = datetime(2026, 4, 1, tzinfo=timezone.utc)  # W14
        dt2 = datetime(2026, 4, 9, tzinfo=timezone.utc)  # W15
        bump_weekly_count(wc, "POLYX/KRW", dt1)
        bump_weekly_count(wc, "POLYX/KRW", dt1)
        bump_weekly_count(wc, "POLYX/KRW", dt1)
        # W14에 3회지만 W15 기준으로는 0회
        assert not weekly_count_exceeded(wc, "POLYX/KRW", 3, dt2)

    def test_old_weeks_pruned(self):
        """현재 주 기준 3주 이전 버킷은 정리된다."""
        wc = {"2026-W10": {"X/KRW": 5}, "2026-W14": {"Y/KRW": 1}}
        dt = datetime(2026, 4, 9, tzinfo=timezone.utc)  # W15
        bump_weekly_count(wc, "Z/KRW", dt)
        # W10은 너무 오래되어 제거
        assert "2026-W10" not in wc
        assert "2026-W14" in wc
        assert "2026-W15" in wc


# ══════════════════════════════════════════════════════════
# D. 연패 쿨다운
# ══════════════════════════════════════════════════════════

class TestLossCooldown:
    def test_no_history_zero_losses(self):
        assert recent_consecutive_losses([]) == 0

    def test_single_loss(self):
        h = [{"reason": "손절 -2.1%", "return_pct": -2.1}]
        assert recent_consecutive_losses(h) == 1

    def test_three_consecutive_losses(self):
        """4/7 CELO/ANIME/F 3연손 실제 케이스."""
        h = [
            {"symbol": "CELO/KRW", "reason": "손절 -2.3%", "return_pct": -2.31},
            {"symbol": "ANIME/KRW", "reason": "손절 -2.1%", "return_pct": -2.06},
            {"symbol": "F/KRW", "reason": "손절 -2.1%", "return_pct": -2.07},
        ]
        assert recent_consecutive_losses(h) == 3

    def test_win_breaks_streak(self):
        h = [
            {"reason": "손절 -2.1%", "return_pct": -2.1},
            {"reason": "1일 회전", "return_pct": 5.0},
            {"reason": "손절 -2.1%", "return_pct": -2.1},
        ]
        assert recent_consecutive_losses(h) == 1  # 마지막 한 건만

    def test_rotation_loss_not_counted(self):
        """1일 회전 로스는 연패로 치지 않음 (구조적 청산)."""
        h = [
            {"reason": "1일 회전", "return_pct": -5.76},
            {"reason": "1일 회전", "return_pct": -3.27},
        ]
        assert recent_consecutive_losses(h) == 0

    def test_is_in_cooldown_none(self):
        assert not is_in_loss_cooldown(None)
        assert not is_in_loss_cooldown("")

    def test_is_in_cooldown_future(self):
        now = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        assert is_in_loss_cooldown(future, now=now)

    def test_is_in_cooldown_past(self):
        now = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        assert not is_in_loss_cooldown(past, now=now)

    def test_set_loss_cooldown_returns_future(self):
        now = datetime(2026, 4, 9, 12, 0, tzinfo=timezone.utc)
        until = set_loss_cooldown(24, now=now)
        assert is_in_loss_cooldown(until, now=now + timedelta(hours=23))
        assert not is_in_loss_cooldown(until, now=now + timedelta(hours=25))

    def test_invalid_iso_string_not_in_cooldown(self):
        assert not is_in_loss_cooldown("not-a-date")


# ══════════════════════════════════════════════════════════
# 시나리오 통합: 실제 P4-05 데이터 근사 재현
# ══════════════════════════════════════════════════════════

class TestRealScenario:
    def test_p405_actual_4_7_triggers_cooldown(self):
        """P4-05 실제 데이터: 4/7에 CELO/ANIME/F 3연속 손절 → 쿨다운 발동 조건."""
        # history 순서: POLYX 승 → 이후 4/7 3연손
        h = [
            {"symbol": "POLYX/KRW", "reason": "1일 회전", "return_pct": 14.92},
            {"symbol": "CELO/KRW", "reason": "손절 -2.3%", "return_pct": -2.31},
            {"symbol": "ANIME/KRW", "reason": "손절 -2.1%", "return_pct": -2.06},
            {"symbol": "F/KRW", "reason": "손절 -2.1%", "return_pct": -2.07},
        ]
        consec = recent_consecutive_losses(h)
        assert consec == 3
        assert consec >= 3  # VB_LOSS_COOLDOWN_N 트리거

    def test_p405_actual_elf_detected_as_dead(self):
        """P4-05 실제: ELF 3회 0% → 데드 판정."""
        h = [
            {"symbol": "POLYX/KRW", "return_pct": 14.92},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
            {"symbol": "ELF/KRW", "return_pct": 0},
        ]
        dead = compute_dead_symbols(h, threshold=3)
        assert "ELF/KRW" in dead
        assert "POLYX/KRW" not in dead
