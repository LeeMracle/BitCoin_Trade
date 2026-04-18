"""tests.common.test_log_throttle — throttled_print 단위 테스트.

실행:
    PYTHONUTF8=1 python -m pytest tests/common/test_log_throttle.py -v

대상:
    services.common.log_throttle.throttled_print
    services.common.log_throttle.get_throttle_counters
    services.common.log_throttle.reset_throttle_state
"""
from __future__ import annotations

import time

import pytest

from services.common.log_throttle import (
    get_throttle_counters,
    reset_throttle_state,
    throttled_print,
)


@pytest.fixture(autouse=True)
def _clean_throttle_state():
    """각 테스트 전후로 전역 throttle 상태를 초기화해 테스트 간 격리 보장."""
    reset_throttle_state()
    yield
    reset_throttle_state()


# ── 케이스 1 ──────────────────────────────────────────────────────────────────
def test_same_key_prints_once_within_interval(capsys):
    """동일 key를 interval 내에 연속 호출하면 첫 번째만 실제 출력된다."""
    throttled_print("test_key", "첫 번째 메시지", interval_sec=60.0)
    throttled_print("test_key", "두 번째 메시지", interval_sec=60.0)
    throttled_print("test_key", "세 번째 메시지", interval_sec=60.0)

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert len(lines) == 1, f"출력 줄 수가 1이어야 하는데 {len(lines)}개 출력됨"
    assert lines[0] == "첫 번째 메시지"


# ── 케이스 2 ──────────────────────────────────────────────────────────────────
def test_different_keys_each_print_once(capsys):
    """서로 다른 key는 각각 독립적으로 출력된다."""
    throttled_print("key_alpha", "알파 메시지", interval_sec=60.0)
    throttled_print("key_beta",  "베타 메시지", interval_sec=60.0)
    throttled_print("key_gamma", "감마 메시지", interval_sec=60.0)

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln]
    assert len(lines) == 3, f"3개 key가 각각 1회씩 출력되어야 하는데 {len(lines)}개"
    assert "알파 메시지" in lines
    assert "베타 메시지" in lines
    assert "감마 메시지" in lines


# ── 케이스 3 ──────────────────────────────────────────────────────────────────
def test_prints_again_after_interval_elapsed(capsys):
    """interval 경과 후 동일 key를 호출하면 다시 출력된다."""
    # interval_sec=0.1 (100ms) 설정 — 테스트에서만 짧게 사용
    throttled_print("expiry_key", "최초 메시지", interval_sec=0.1)
    captured_first = capsys.readouterr()
    assert "최초 메시지" in captured_first.out

    # interval 경과 대기
    time.sleep(0.15)

    throttled_print("expiry_key", "재출력 메시지", interval_sec=0.1)
    captured_second = capsys.readouterr()
    assert "재출력 메시지" in captured_second.out, "interval 경과 후 재출력이 안 됨"


# ── 케이스 4 ──────────────────────────────────────────────────────────────────
def test_get_throttle_counters_returns_suppressed_count():
    """억제된 횟수가 get_throttle_counters에 정확히 집계된다."""
    throttled_print("count_key", "최초", interval_sec=60.0)   # 출력 (억제 아님)
    throttled_print("count_key", "두번", interval_sec=60.0)   # 억제 1
    throttled_print("count_key", "세번", interval_sec=60.0)   # 억제 2
    throttled_print("count_key", "네번", interval_sec=60.0)   # 억제 3

    counters = get_throttle_counters()
    assert "count_key" in counters, "count_key가 counters에 없음"
    assert counters["count_key"] == 3, (
        f"억제 횟수가 3이어야 하는데 {counters['count_key']}개"
    )


# ── 케이스 5 (보너스) ─────────────────────────────────────────────────────────
def test_reset_clears_state(capsys):
    """reset_throttle_state 호출 후에는 동일 key가 다시 출력된다."""
    throttled_print("reset_key", "리셋 전 첫 출력", interval_sec=60.0)
    captured = capsys.readouterr()
    assert "리셋 전 첫 출력" in captured.out

    # interval 내 두 번째 호출 — 억제됨
    throttled_print("reset_key", "억제될 메시지", interval_sec=60.0)
    captured = capsys.readouterr()
    assert captured.out == "", "리셋 전 두 번째 호출은 억제되어야 함"

    # 상태 초기화 후 재호출 — 출력 복구
    reset_throttle_state()
    throttled_print("reset_key", "리셋 후 재출력", interval_sec=60.0)
    captured = capsys.readouterr()
    assert "리셋 후 재출력" in captured.out, "reset 후 동일 key가 출력되어야 함"
