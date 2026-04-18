"""tests/execution/test_filter_stats.py

filter_stats 모듈의 카운터 집계, 영구화, 롤오버 동작을 검증한다.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


# ── 헬퍼: 모듈을 완전히 재초기화하는 fixture ──────────────────────────────
@pytest.fixture()
def stats(tmp_path, monkeypatch):
    """filter_stats 모듈을 tmp_path 기반 경로로 격리하여 반환한다."""
    import services.execution.filter_stats as _mod

    # 경로를 tmp_path로 교체
    monkeypatch.setattr(_mod, "_STATS_FILE", tmp_path / "filter_stats.json")
    monkeypatch.setattr(_mod, "_HISTORY_FILE", tmp_path / "filter_stats_history.jsonl")
    # 내부 상태 초기화
    _mod._state = {}
    _mod._loaded = False
    _mod._last_flush = 0.0

    return _mod


# ── 테스트 케이스 ─────────────────────────────────────────────────────────

def test_record_increments_counter(stats):
    """record_block 호출 후 counters 값이 1 증가해야 한다."""
    stats.record_block("fg_gate", None)
    snap = stats.snapshot()
    assert snap["counters"].get("fg_gate") == 1


def test_record_increments_counter_multiple(stats):
    """동일 reason을 여러 번 호출하면 누적된다."""
    for _ in range(5):
        stats.record_block("atr_filter", "KRW-BTC")
    snap = stats.snapshot()
    assert snap["counters"]["atr_filter"] == 5


def test_by_symbol_nested_increment(stats):
    """symbol이 주어지면 by_symbol 중첩 카운터도 증가해야 한다."""
    stats.record_block("ema200_filter", "KRW-ETH")
    stats.record_block("ema200_filter", "KRW-ETH")
    stats.record_block("ema200_filter", "KRW-XRP")
    snap = stats.snapshot()
    by_sym = snap["by_symbol"].get("ema200_filter", {})
    assert by_sym.get("KRW-ETH") == 2
    assert by_sym.get("KRW-XRP") == 1


def test_by_symbol_none_skipped(stats):
    """symbol=None 이면 by_symbol에 키가 생성되지 않아야 한다."""
    stats.record_block("vb_gate_a_bearish", None)
    snap = stats.snapshot()
    assert "vb_gate_a_bearish" not in snap["by_symbol"]


def test_save_reload_roundtrip(stats):
    """record_block 후 파일을 강제 flush하고 재로딩해도 값이 유지된다."""
    stats.record_block("cb_l1", "KRW-SOL")
    stats.record_block("cb_l2", "KRW-SOL")
    # 강제 flush
    stats._flush(force=True)

    # 상태 초기화 후 재로딩
    stats._state = {}
    stats._loaded = False
    snap = stats.snapshot()
    assert snap["counters"].get("cb_l1") == 1
    assert snap["counters"].get("cb_l2") == 1
    assert snap["by_symbol"].get("cb_l1", {}).get("KRW-SOL") == 1


def test_reset_today_appends_history(stats):
    """reset_today() 호출 시 이전 상태가 history.jsonl에 append되어야 한다."""
    stats.record_block("fg_gate", None)
    stats.record_block("atr_filter", "KRW-DOGE")
    stats._flush(force=True)

    stats.reset_today()

    history_file = stats._HISTORY_FILE
    assert history_file.exists(), "history.jsonl 파일이 생성되어야 한다"
    lines = history_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert entry["counters"].get("fg_gate") == 1
    assert entry["counters"].get("atr_filter") == 1


def test_reset_today_clears_counters(stats):
    """reset_today() 후 counters가 초기화되어야 한다."""
    stats.record_block("cb_l1", "KRW-BTC")
    stats.reset_today()
    snap = stats.snapshot()
    assert snap["counters"] == {}
    assert snap["by_symbol"] == {}


def test_unknown_reason_recorded_with_prefix(stats):
    """VALID_REASONS에 없는 reason은 'unknown_' 접두어로 기록된다."""
    stats.record_block("nonexistent_filter", "KRW-BTC")
    snap = stats.snapshot()
    assert "unknown_nonexistent_filter" in snap["counters"]


def test_record_silent_on_exception(stats, monkeypatch):
    """내부 예외 발생 시 record_block이 서비스를 중단시키지 않아야 한다."""
    def _broken(*a, **kw):
        raise RuntimeError("의도적 예외")

    monkeypatch.setattr(stats, "_ensure_loaded", _broken)
    # 예외가 전파되지 않고 조용히 처리되어야 함
    stats.record_block("fg_gate", None)  # 여기서 raise되면 테스트 실패
