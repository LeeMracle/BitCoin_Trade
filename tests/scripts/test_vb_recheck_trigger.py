#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vb_recheck_trigger.py 단위 테스트.

케이스:
  1) BTC 7일 연속 EMA200 위 + 쿨다운 경과 → 트리거 발동
  2) BTC 6일만 EMA200 위 (7일 미충족) → 발동 안 함
  3) 7일 충족이지만 마지막 트리거로부터 3일밖에 안 지남 → 발동 안 함
  4) --force → 무조건 발동 (조건 무시)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# scripts/ 디렉토리를 sys.path에 추가
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_PROJECT_ROOT))

import vb_recheck_trigger as vbr  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 헬퍼: 테스트용 closes 생성
# ════════════════════════════════════════════════════════════════════

def _make_closes(n: int, ema_period: int = 200, above_last: int = 7) -> list[float]:
    """EMA200 계산 후 마지막 above_last 개만 EMA 위에 오도록 조정한 closes 생성.

    ema_period + 50 개의 기초 가격을 만들고,
    마지막 above_last 개는 EMA + 1000 (확실히 위),
    나머지는 EMA - 1000 (확실히 아래).
    """
    total = ema_period + 50 + n
    # 기본 가격: 일정하게 50_000_000 유지 → EMA ≈ 50_000_000
    base_price = 50_000_000.0
    closes = [base_price] * total

    # 마지막 above_last 개: EMA 위
    for i in range(above_last):
        closes[-(i + 1)] = base_price + 1_000_000  # +2%

    # above_last+1 번째부터 뒤에서 (7-above_last) 개: EMA 아래
    below_count = 7 - above_last
    if below_count > 0:
        for i in range(above_last, above_last + below_count):
            closes[-(i + 1)] = base_price - 1_000_000  # -2%

    return closes


# ════════════════════════════════════════════════════════════════════
# 케이스 1: 7일 연속 충족 + 쿨다운 경과 → 트리거 발동
# ════════════════════════════════════════════════════════════════════

def test_trigger_fires_when_7day_above_and_cooldown_passed(tmp_path):
    """BTC 7일 연속 EMA200 상향 + 쿨다운(7일) 경과 → 트리거 발동."""
    state_file = tmp_path / "vb_recheck_last.json"
    reports_dir = tmp_path / "reports"

    closes = _make_closes(0, ema_period=200, above_last=7)

    with (
        patch.object(vbr, "_STATE_FILE", state_file),
        patch.object(vbr, "_REPORTS_DIR", reports_dir),
        patch.object(vbr, "fetch_btc_daily_closes", return_value=closes),
    ):
        # 상태 파일 없음 (최초 실행)
        triggered = vbr.run(notify=False, force=False)

    assert triggered is True, "7일 연속 + 쿨다운 경과 시 트리거 발동 필요"

    # 보고서 생성 확인
    report_files = list(reports_dir.glob("*_vb_drymake_auto_recheck.md"))
    assert len(report_files) == 1, "보고서 1개 생성 필요"

    # 상태 파일 갱신 확인
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_trigger_ts"] > 0
    assert state["last_7day_above"] is True


# ════════════════════════════════════════════════════════════════════
# 케이스 2: 6일만 충족 → 발동 안 함
# ════════════════════════════════════════════════════════════════════

def test_no_trigger_when_only_6day_above(tmp_path):
    """BTC 6일만 EMA200 위 (7일 미충족) → 트리거 발동 안 함."""
    state_file = tmp_path / "vb_recheck_last.json"
    reports_dir = tmp_path / "reports"

    closes = _make_closes(0, ema_period=200, above_last=6)

    with (
        patch.object(vbr, "_STATE_FILE", state_file),
        patch.object(vbr, "_REPORTS_DIR", reports_dir),
        patch.object(vbr, "fetch_btc_daily_closes", return_value=closes),
    ):
        triggered = vbr.run(notify=False, force=False)

    assert triggered is False, "6일 충족 시 트리거 발동 안 함"

    # 보고서 미생성 확인
    if reports_dir.exists():
        report_files = list(reports_dir.glob("*_vb_drymake_auto_recheck.md"))
        assert len(report_files) == 0


# ════════════════════════════════════════════════════════════════════
# 케이스 3: 7일 충족이지만 쿨다운 미경과 (3일 전 트리거) → 발동 안 함
# ════════════════════════════════════════════════════════════════════

def test_no_trigger_when_cooldown_not_passed(tmp_path):
    """7일 충족이지만 마지막 트리거로부터 3일밖에 안 지남 → 발동 안 함."""
    state_file = tmp_path / "vb_recheck_last.json"
    reports_dir = tmp_path / "reports"

    # 3일 전 트리거 기록
    three_days_ago = int(time.time()) - (3 * 86400)
    state_file.write_text(
        json.dumps({"last_trigger_ts": three_days_ago, "last_7day_above": True}),
        encoding="utf-8",
    )

    closes = _make_closes(0, ema_period=200, above_last=7)

    with (
        patch.object(vbr, "_STATE_FILE", state_file),
        patch.object(vbr, "_REPORTS_DIR", reports_dir),
        patch.object(vbr, "fetch_btc_daily_closes", return_value=closes),
    ):
        triggered = vbr.run(notify=False, force=False)

    assert triggered is False, "쿨다운 미경과 시 트리거 발동 안 함"


# ════════════════════════════════════════════════════════════════════
# 케이스 4: --force → 무조건 발동
# ════════════════════════════════════════════════════════════════════

def test_force_triggers_regardless_of_condition(tmp_path):
    """--force 플래그 사용 시 조건 무시하고 무조건 발동."""
    state_file = tmp_path / "vb_recheck_last.json"
    reports_dir = tmp_path / "reports"

    # 쿨다운 미경과 + EMA 아래 상태 설정
    one_hour_ago = int(time.time()) - 3600
    state_file.write_text(
        json.dumps({"last_trigger_ts": one_hour_ago, "last_7day_above": False}),
        encoding="utf-8",
    )

    # fetch_btc_daily_closes는 force 시 호출하지 않음 → 빈 리스트로 mock
    with (
        patch.object(vbr, "_STATE_FILE", state_file),
        patch.object(vbr, "_REPORTS_DIR", reports_dir),
        patch.object(vbr, "fetch_btc_daily_closes", return_value=[]),
    ):
        triggered = vbr.run(notify=False, force=True)

    assert triggered is True, "--force 시 무조건 발동 필요"

    # 보고서 생성 확인
    report_files = list(reports_dir.glob("*_vb_drymake_auto_recheck.md"))
    assert len(report_files) == 1, "--force 시 보고서 1개 생성 필요"

    # 상태 파일 갱신 확인
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["last_trigger_ts"] >= one_hour_ago


# ════════════════════════════════════════════════════════════════════
# 보조 유닛 테스트: check_consecutive_above_ema
# ════════════════════════════════════════════════════════════════════

def test_check_consecutive_7days_true():
    """check_consecutive_above_ema: 7일 연속 → True."""
    closes = _make_closes(0, ema_period=200, above_last=7)
    result = vbr.check_consecutive_above_ema(closes, consec=7, ema_period=200)
    assert result is True


def test_check_consecutive_6days_false():
    """check_consecutive_above_ema: 6일 연속 → False."""
    closes = _make_closes(0, ema_period=200, above_last=6)
    result = vbr.check_consecutive_above_ema(closes, consec=7, ema_period=200)
    assert result is False


def test_check_consecutive_insufficient_data():
    """check_consecutive_above_ema: 데이터 부족 → False."""
    closes = [50_000_000.0] * 10  # 200봉 미만
    result = vbr.check_consecutive_above_ema(closes, consec=7, ema_period=200)
    assert result is False
