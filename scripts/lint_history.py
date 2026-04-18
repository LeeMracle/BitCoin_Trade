#!/usr/bin/env python3
"""린트 결과 시계열 누적 도구 (P6-13).

현재 lint_none_format.py 와 lint_meta.py 결과를 파싱해
workspace/lint_history.jsonl 에 한 줄 append 한다.

스키마 (1행 1 JSON):
  {
    "ts":   1744934400,
    "date": "2026-04-18",
    "lint_none_format": {"ERROR": 0, "WARN": 108},
    "lint_meta":        {"mapped": 11, "unmapped": 6, "errors": 0}
  }

사용법:
  python scripts/lint_history.py            # 현재 결과를 jsonl에 append
  python scripts/lint_history.py --summary  # 최근 7일 추세 출력
  python scripts/lint_history.py --weekly   # 이번 주 (월요일~오늘) 평균/최댓값
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
HISTORY_FILE = PROJECT_ROOT / "workspace" / "lint_history.jsonl"

# ────────────────────────────────────────────────────────────
# 린터 실행 및 파싱
# ────────────────────────────────────────────────────────────

def _run_lint_none_format() -> dict:
    """lint_none_format.py 를 실행하고 ERROR/WARN 건수를 반환한다."""
    script = SCRIPTS_DIR / "lint_none_format.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stdout = result.stdout

    errors = 0
    warns = 0

    # "ERROR N건:" 패턴 탐지
    m_err = re.search(r"ERROR\s+(\d+)건", stdout)
    if m_err:
        errors = int(m_err.group(1))

    # "WARN N건:" 패턴 탐지
    m_warn = re.search(r"WARN\s+(\d+)건", stdout)
    if m_warn:
        warns = int(m_warn.group(1))

    # "ERROR 없음 (WARN N건)" 패턴 — WARN만 있는 경우
    m_warn2 = re.search(r"ERROR 없음.*WARN\s+(\d+)건", stdout)
    if m_warn2 and warns == 0:
        warns = int(m_warn2.group(1))

    # "위반 없음" → 둘 다 0
    if "위반 없음" in stdout:
        errors = 0
        warns = 0

    return {"ERROR": errors, "WARN": warns}


def _run_lint_meta() -> dict:
    """lint_meta.py 를 실행하고 mapped/unmapped/errors 건수를 반환한다."""
    script = SCRIPTS_DIR / "lint_meta.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"mapped": 0, "unmapped": 0, "errors": 0}

    summary = data.get("summary", {})
    mapped = summary.get("ok", 0)
    warn_count = summary.get("warn", 0)
    error_count = summary.get("error", 0)
    # unmapped = WARN(섹션없음) + ERROR(미집행) 합산
    unmapped = warn_count + error_count

    return {"mapped": mapped, "unmapped": unmapped, "errors": error_count}


# ────────────────────────────────────────────────────────────
# append
# ────────────────────────────────────────────────────────────

def _build_record() -> dict:
    """현재 시각 기준 레코드를 생성한다."""
    now_utc = datetime.now(timezone.utc)
    ts = int(now_utc.timestamp())
    date_str = date.today().isoformat()

    lint_nf = _run_lint_none_format()
    lint_mt = _run_lint_meta()

    return {
        "ts": ts,
        "date": date_str,
        "lint_none_format": lint_nf,
        "lint_meta": lint_mt,
    }


def cmd_append(history_file: Path) -> None:
    """현재 린트 결과를 jsonl에 append 한다."""
    history_file.parent.mkdir(parents=True, exist_ok=True)
    record = _build_record()

    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"append 완료: {history_file}")
    print(f"레코드: {json.dumps(record, ensure_ascii=False)}")


# ────────────────────────────────────────────────────────────
# 읽기 헬퍼
# ────────────────────────────────────────────────────────────

def _load_records(history_file: Path) -> list[dict]:
    """jsonl 파일을 읽어 레코드 목록을 반환한다 (파싱 실패 행 무시)."""
    if not history_file.exists():
        return []
    records = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


# ────────────────────────────────────────────────────────────
# --summary
# ────────────────────────────────────────────────────────────

def cmd_summary(history_file: Path) -> None:
    """최근 7개 레코드 추세를 출력한다."""
    records = _load_records(history_file)
    last7 = records[-7:]

    if not last7:
        print("데이터 없음 (history 파일이 비어 있거나 존재하지 않습니다)")
        return

    print("=" * 56)
    print("린트 이력 — 최근 7일 추세")
    print("=" * 56)
    header = f"{'날짜':>12}  {'NF_ERR':>6}  {'NF_WARN':>7}  {'META_OK':>7}  {'META_ERR':>8}"
    print(header)
    print("-" * 56)
    for r in last7:
        nf = r.get("lint_none_format", {})
        mt = r.get("lint_meta", {})
        print(
            f"{r.get('date', '?'):>12}  "
            f"{nf.get('ERROR', '?'):>6}  "
            f"{nf.get('WARN', '?'):>7}  "
            f"{mt.get('mapped', '?'):>7}  "
            f"{mt.get('errors', '?'):>8}"
        )
    print("-" * 56)

    # 단순 추세 (첫→마지막)
    if len(last7) >= 2:
        first = last7[0]
        last = last7[-1]
        nf_f = first.get("lint_none_format", {})
        nf_l = last.get("lint_none_format", {})
        delta_err = nf_l.get("ERROR", 0) - nf_f.get("ERROR", 0)
        delta_warn = nf_l.get("WARN", 0) - nf_f.get("WARN", 0)
        sign_e = "+" if delta_err >= 0 else ""
        sign_w = "+" if delta_warn >= 0 else ""
        print(
            f"추세({first['date']}→{last['date']}): "
            f"NF_ERR {sign_e}{delta_err}  NF_WARN {sign_w}{delta_warn}"
        )


# ────────────────────────────────────────────────────────────
# --weekly
# ────────────────────────────────────────────────────────────

def cmd_weekly(history_file: Path) -> None:
    """이번 주 시작일(월요일) 이후 레코드의 평균/최댓값 요약을 출력한다."""
    records = _load_records(history_file)
    today = date.today()
    # 이번 주 월요일
    monday = today - __import__("datetime").timedelta(days=today.weekday())
    monday_str = monday.isoformat()

    week_records = [
        r for r in records
        if r.get("date", "") >= monday_str
    ]

    if not week_records:
        print(f"이번 주({monday_str} 이후) 데이터 없음")
        return

    print("=" * 56)
    print(f"이번 주 요약 ({monday_str} ~ {today.isoformat()})")
    print(f"레코드 수: {len(week_records)}")
    print("=" * 56)

    def _stats(key_path: tuple[str, str], label: str) -> None:
        vals = [
            r.get(key_path[0], {}).get(key_path[1], 0)
            for r in week_records
        ]
        if vals:
            avg = sum(vals) / len(vals)
            mx = max(vals)
            mn = min(vals)
            print(f"  {label:20s}  평균={avg:.1f}  최댓값={mx}  최솟값={mn}")

    _stats(("lint_none_format", "ERROR"), "NF_ERROR")
    _stats(("lint_none_format", "WARN"),  "NF_WARN")
    _stats(("lint_meta", "mapped"),       "META_mapped")
    _stats(("lint_meta", "unmapped"),     "META_unmapped")
    _stats(("lint_meta", "errors"),       "META_errors")


# ────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="린트 결과 시계열 누적 도구 (P6-13)"
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="최근 7일 추세 집계 출력 (append 없음)"
    )
    parser.add_argument(
        "--weekly", action="store_true",
        help="이번 주 평균/최댓값 요약 출력 (append 없음)"
    )
    parser.add_argument(
        "--file", type=Path, default=None,
        help="history 파일 경로 오버라이드 (테스트용)"
    )
    args = parser.parse_args()

    history_file = args.file if args.file else HISTORY_FILE

    if args.summary:
        cmd_summary(history_file)
    elif args.weekly:
        cmd_weekly(history_file)
    else:
        cmd_append(history_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
