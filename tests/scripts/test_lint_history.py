#!/usr/bin/env python3
"""lint_history.py 단위 테스트.

케이스:
  1) append 가 새 레코드를 추가한다
  2) --summary 는 마지막 7개만 집계한다
  3) --weekly 는 이번 주 시작일(월요일) 이후만 필터한다
  4) 파일 없을 때 자동 생성 후 append 성공

각 케이스는 subprocess 또는 내부 함수를 직접 호출하여 검증한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

# scripts/ 경로를 sys.path 에 추가하여 lint_history 직접 import
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import lint_history  # noqa: E402


# ────────────────────────────────────────────────────────────
# 헬퍼
# ────────────────────────────────────────────────────────────

def _write_records(history_file: Path, records: list[dict]) -> None:
    """테스트용 레코드 목록을 jsonl 파일에 기록한다."""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _make_record(date_str: str, nf_error: int = 0, nf_warn: int = 10,
                 mapped: int = 5, unmapped: int = 2, errors: int = 0) -> dict:
    """테스트용 레코드를 생성한다."""
    return {
        "ts": int(time.time()),
        "date": date_str,
        "lint_none_format": {"ERROR": nf_error, "WARN": nf_warn},
        "lint_meta": {"mapped": mapped, "unmapped": unmapped, "errors": errors},
    }


def _run_script(args: list[str], file_path: Path) -> subprocess.CompletedProcess:
    """lint_history.py 를 subprocess 로 실행한다."""
    lint_history_path = _SCRIPTS_DIR / "lint_history.py"
    return subprocess.run(
        [sys.executable, str(lint_history_path), "--file", str(file_path)] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# ════════════════════════════════════════════════════════════
# 케이스 1: append 가 새 레코드를 추가한다
# ════════════════════════════════════════════════════════════

class TestAppendAddsNewRecord:
    """append 호출 시 jsonl 파일에 레코드 1개가 추가되어야 한다."""

    def test_append_increases_record_count(self, tmp_path, monkeypatch):
        """기존 1개 레코드 → append 후 2개."""
        history_file = tmp_path / "lint_history.jsonl"
        existing = _make_record("2026-01-01", nf_error=0, nf_warn=5)
        _write_records(history_file, [existing])

        # 린터 실행 없이 테스트하기 위해 내부 함수를 모킹
        monkeypatch.setattr(
            lint_history, "_run_lint_none_format",
            lambda: {"ERROR": 0, "WARN": 108}
        )
        monkeypatch.setattr(
            lint_history, "_run_lint_meta",
            lambda: {"mapped": 11, "unmapped": 6, "errors": 0}
        )

        lint_history.cmd_append(history_file)

        records = lint_history._load_records(history_file)
        assert len(records) == 2, f"기대: 2, 실제: {len(records)}"

    def test_appended_record_schema(self, tmp_path, monkeypatch):
        """append 된 레코드가 필수 키를 모두 가진다."""
        history_file = tmp_path / "lint_history.jsonl"

        monkeypatch.setattr(
            lint_history, "_run_lint_none_format",
            lambda: {"ERROR": 1, "WARN": 50}
        )
        monkeypatch.setattr(
            lint_history, "_run_lint_meta",
            lambda: {"mapped": 10, "unmapped": 3, "errors": 1}
        )

        lint_history.cmd_append(history_file)

        records = lint_history._load_records(history_file)
        assert len(records) == 1
        r = records[0]

        for key in ("ts", "date", "lint_none_format", "lint_meta"):
            assert key in r, f"레코드에 '{key}' 키 없음"

        assert r["lint_none_format"]["ERROR"] == 1
        assert r["lint_none_format"]["WARN"] == 50
        assert r["lint_meta"]["mapped"] == 10
        assert r["lint_meta"]["errors"] == 1


# ════════════════════════════════════════════════════════════
# 케이스 2: --summary 는 마지막 7개만 집계한다
# ════════════════════════════════════════════════════════════

class TestSummaryLast7:
    """--summary 는 전체 레코드 중 마지막 7개만 집계해야 한다."""

    def test_summary_shows_only_last7(self, tmp_path, capsys):
        """10개 레코드가 있을 때 마지막 7개 날짜만 출력에 포함된다."""
        history_file = tmp_path / "lint_history.jsonl"

        # 2026-01-01 ~ 2026-01-10 (10개 레코드)
        records = []
        for i in range(1, 11):
            date_str = f"2026-01-{i:02d}"
            records.append(_make_record(date_str, nf_warn=i * 10))
        _write_records(history_file, records)

        lint_history.cmd_summary(history_file)
        captured = capsys.readouterr().out

        # 마지막 7개: 2026-01-04 ~ 2026-01-10
        for i in range(4, 11):
            assert f"2026-01-{i:02d}" in captured, (
                f"2026-01-{i:02d} 가 --summary 출력에 없음"
            )
        # 앞 3개는 출력에 없어야 함
        for i in range(1, 4):
            assert f"2026-01-{i:02d}" not in captured, (
                f"2026-01-{i:02d} 가 --summary 에 포함되면 안 됨"
            )

    def test_summary_fewer_than7(self, tmp_path, capsys):
        """3개 레코드만 있어도 --summary 가 오류 없이 동작한다."""
        history_file = tmp_path / "lint_history.jsonl"
        records = [_make_record(f"2026-02-{i:02d}") for i in range(1, 4)]
        _write_records(history_file, records)

        lint_history.cmd_summary(history_file)
        captured = capsys.readouterr().out
        assert "2026-02-01" in captured


# ════════════════════════════════════════════════════════════
# 케이스 3: --weekly 는 이번 주 시작일(월요일) 이후만 필터한다
# ════════════════════════════════════════════════════════════

class TestWeeklyFilter:
    """--weekly 는 이번 주 월요일 이후 레코드만 포함해야 한다."""

    def test_weekly_excludes_last_week(self, tmp_path, capsys):
        """지난 주 레코드는 --weekly 출력에서 제외되어야 한다."""
        history_file = tmp_path / "lint_history.jsonl"
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        last_monday = monday - timedelta(days=7)

        # 지난 주 레코드 1개 + 이번 주 레코드 2개
        records = [
            _make_record(last_monday.isoformat(), nf_warn=999),   # 지난 주
            _make_record(monday.isoformat(), nf_warn=10),          # 이번 주 시작
            _make_record(today.isoformat(), nf_warn=20),           # 이번 주 오늘
        ]
        _write_records(history_file, records)

        lint_history.cmd_weekly(history_file)
        captured = capsys.readouterr().out

        # "레코드 수: 2" 여야 함 (이번 주 2개만)
        assert "레코드 수: 2" in captured, (
            f"이번 주 레코드는 2개여야 함\n출력:\n{captured}"
        )

    def test_weekly_no_data(self, tmp_path, capsys):
        """이번 주 데이터가 없으면 '데이터 없음' 메시지를 출력한다."""
        history_file = tmp_path / "lint_history.jsonl"
        # 작년 레코드만 추가
        records = [_make_record("2025-01-01")]
        _write_records(history_file, records)

        lint_history.cmd_weekly(history_file)
        captured = capsys.readouterr().out
        assert "데이터 없음" in captured

    def test_weekly_stats_correctness(self, tmp_path, capsys):
        """이번 주 레코드 2개의 WARN 평균값이 정확히 계산되어야 한다."""
        history_file = tmp_path / "lint_history.jsonl"
        today = date.today()
        monday = today - timedelta(days=today.weekday())

        records = [
            _make_record(monday.isoformat(), nf_warn=100),
            _make_record(today.isoformat(), nf_warn=200),
        ]
        _write_records(history_file, records)

        lint_history.cmd_weekly(history_file)
        captured = capsys.readouterr().out
        # NF_WARN 평균은 150.0 이어야 함
        assert "150.0" in captured, (
            f"WARN 평균 150.0이 출력에 없음\n출력:\n{captured}"
        )


# ════════════════════════════════════════════════════════════
# 케이스 4: 파일 없을 때 자동 생성
# ════════════════════════════════════════════════════════════

class TestAutoCreateFile:
    """history 파일이 없어도 append 가 파일을 자동 생성해야 한다."""

    def test_creates_file_if_not_exists(self, tmp_path, monkeypatch):
        """파일이 없는 경로에 append 시 파일이 새로 생성된다."""
        history_file = tmp_path / "subdir" / "lint_history.jsonl"
        assert not history_file.exists(), "사전 조건: 파일이 없어야 함"

        monkeypatch.setattr(
            lint_history, "_run_lint_none_format",
            lambda: {"ERROR": 0, "WARN": 0}
        )
        monkeypatch.setattr(
            lint_history, "_run_lint_meta",
            lambda: {"mapped": 5, "unmapped": 0, "errors": 0}
        )

        lint_history.cmd_append(history_file)

        assert history_file.exists(), "append 후 파일이 생성되어야 함"
        records = lint_history._load_records(history_file)
        assert len(records) == 1

    def test_empty_file_load_returns_empty_list(self, tmp_path):
        """존재하지 않는 파일 로드 시 빈 목록을 반환한다."""
        history_file = tmp_path / "nonexistent.jsonl"
        records = lint_history._load_records(history_file)
        assert records == [], f"기대: [], 실제: {records}"

    def test_subprocess_creates_file(self, tmp_path, monkeypatch):
        """subprocess 로 실행해도 파일이 생성되는지 확인한다.

        실제 린터를 호출하므로 환경에 따라 결과가 달라질 수 있지만,
        파일 생성 여부만 검증한다.
        """
        history_file = tmp_path / "auto_create.jsonl"
        assert not history_file.exists()

        result = _run_script([], history_file)
        assert result.returncode == 0, (
            f"종료코드가 0이어야 함\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert history_file.exists(), "subprocess append 후 파일이 생성되어야 함"

        records = lint_history._load_records(history_file)
        assert len(records) == 1, f"레코드 1개여야 함, 실제: {len(records)}"
