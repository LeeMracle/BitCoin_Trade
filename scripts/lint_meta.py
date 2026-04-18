#!/usr/bin/env python3
"""메타 린트 — lessons ↔ 린트 규칙 매핑 검증 도구.

docs/lessons/*.md 의 "검증규칙" 섹션이 실제로
  - scripts/lint_none_format.py (R1~Rn 규칙)
  - scripts/pre_deploy_check.py (check_* 함수)
중 하나로 집행되고 있는지 매핑 리포트를 생성한다.

exit code:
  0 — 문제 없거나, 미연결 lesson이 있어도 경고에 그치는 경우
  1 — 검증규칙 섹션이 명시적으로 있는 lesson이 린터/pre_deploy_check
      어디에도 대응 코드가 없을 때 (ERROR 성격)

사용법:
  python scripts/lint_meta.py          # 표준 리포트 출력
  python scripts/lint_meta.py --json   # JSON 출력
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

# ── 경로 상수 ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LESSONS_DIR = PROJECT_ROOT / "docs" / "lessons"
LINT_SCRIPT = PROJECT_ROOT / "scripts" / "lint_none_format.py"
PRE_DEPLOY_SCRIPT = PROJECT_ROOT / "scripts" / "pre_deploy_check.py"

# ── 검증규칙 섹션 패턴 ────────────────────────────────────────────────────
# "## 검증규칙", "### 검증규칙", "## 검증 규칙", "### 검증규칙 (..."  등을 모두 포착
_SECTION_RE = re.compile(
    r"^#{2,3}\s*검증\s*규칙",
    re.MULTILINE,
)

# 린트 규칙 참조 패턴: "R1", "R2", ..., "R6" 등 대문자 R + 숫자
_RULE_REF_RE = re.compile(r"\bR(\d+)\b")

# pre_deploy_check에서 찾을 함수명 패턴: `check_...` 이름 키워드
# lesson 본문에서 명시적으로 언급하는 check_ 함수
_CHECK_FUNC_REF_RE = re.compile(r"\bcheck_[a-z_]+\b")

# pre_deploy_check.py의 함수 본문에서 lesson 식별자 참조 패턴
# 예: "ref: docs/lessons/20260408_4", "lessons/20260410_1", "(lessons/20260408_2 참조)"
_LESSON_REF_IN_CODE_RE = re.compile(r"lessons/(\d{8}_\d+)")


# ═══════════════════════════════════════════════════════════════════════════
# 파싱 유틸
# ═══════════════════════════════════════════════════════════════════════════

def _parse_lessons(lessons_dir: Path) -> list[dict]:
    """lessons 디렉토리의 각 .md 파일을 파싱한다.

    반환 목록의 각 항목:
      {
        "name":        "20260408_4_nonetype_format_lint",
        "path":        Path(...),
        "has_section": bool,    # 검증규칙 섹션 존재 여부
        "rule_refs":   ["R1", "R2"],       # 본문에서 명시적으로 참조한 린트 규칙
        "check_refs":  ["check_none_format_lint"],  # 본문에서 언급한 check_ 함수
        "section_text": "...",  # 검증규칙 섹션 텍스트 (있을 때만)
      }
    """
    result = []
    for md in sorted(lessons_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue

        name = md.stem  # 파일명 확장자 제거

        # 검증규칙 섹션 존재 여부 + 섹션 텍스트 추출
        m = _SECTION_RE.search(text)
        has_section = m is not None
        section_text = ""
        if m:
            # 섹션 헤더부터 다음 동급/상위 헤더까지 추출
            start = m.start()
            rest = text[start:]
            # 다음 ## / # 헤더 전까지
            end_m = re.search(r"(?m)^#{1,2}\s+(?!검증)", rest)
            section_text = rest[: end_m.start()] if end_m else rest

        # 규칙 참조 추출 (R1 ~ R99)
        rule_refs = sorted({
            f"R{g}" for g in _RULE_REF_RE.findall(section_text)
        }, key=lambda x: int(x[1:]))

        # check_ 함수 참조 추출
        check_refs = sorted(set(_CHECK_FUNC_REF_RE.findall(section_text)))

        result.append({
            "name": name,
            "path": md,
            "has_section": has_section,
            "rule_refs": rule_refs,
            "check_refs": check_refs,
            "section_text": section_text,
        })
    return result


def _parse_lint_rules(lint_script: Path) -> dict[str, str]:
    """lint_none_format.py에서 R1~Rn 규칙 ID와 설명을 추출한다.

    반환: {"R1": "ERROR — f-string 숫자 포매팅...", "R2": ..., ...}
    """
    if not lint_script.exists():
        return {}

    text = lint_script.read_text(encoding="utf-8")
    rules: dict[str, str] = {}

    # docstring 내 "R1 (ERROR) ..." 또는 인라인 주석 "# ─── R1:" 형태 탐지
    # 패턴 1: docstring 블록 — "  R\d (LEVEL) 설명"
    for m in re.finditer(
        r"^[ \t]*R(\d+)\s+\(([^)]+)\)\s+(.+)", text, re.MULTILINE
    ):
        rid = f"R{m.group(1)}"
        rules[rid] = f"{m.group(2)} — {m.group(3).strip()}"

    # 패턴 2: 인라인 주석 "# ─── R1: ..." 또는 "# R1"
    for m in re.finditer(
        r"#\s*[─-]*\s*R(\d+)[:\s]+(.+)", text, re.MULTILINE
    ):
        rid = f"R{m.group(1)}"
        if rid not in rules:
            rules[rid] = m.group(2).strip()

    # 패턴 3: Finding(..., "R1", ...) 호출 — 실제 집행 증거
    for m in re.finditer(r'Finding\([^,]+,[^,]+,[^,]+,\s*"(R\d+)"', text):
        rid = m.group(1)
        if rid not in rules:
            rules[rid] = "(코드 집행 확인됨)"

    return rules


def _parse_predeploy_functions(script: Path) -> dict[str, str]:
    """pre_deploy_check.py에서 check_* 함수명과 docstring 첫 줄을 추출한다.

    반환: {"check_none_format_lint": "scripts/lint_none_format.py 를 호출...", ...}
    반환값에는 해당 함수가 참조하는 lesson 식별자도 포함.
    """
    if not script.exists():
        return {}

    text = script.read_text(encoding="utf-8")
    functions: dict[str, str] = {}

    try:
        tree = ast.parse(text, filename=str(script))
    except SyntaxError:
        return {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("check_"):
            continue
        # docstring 추출
        docstring = ast.get_docstring(node) or ""
        functions[node.name] = docstring[:120].replace("\n", " ")

    return functions


def _build_lesson_ref_map(script: Path) -> dict[str, list[str]]:
    """pre_deploy_check.py 내부 각 check_* 함수와 연관된
    lesson 식별자(YYYYMMDD_N) 매핑을 추출한다.

    두 가지 방식으로 매핑:
    1) 함수 본문 내부의 'lessons/YYYYMMDD_N' 텍스트
    2) 함수 직전 섹션 주석 블록(# ══...# ref: docs/lessons/...)에서 역추적
       — pre_deploy_check.py의 '# 검증 N: ...' 섹션이 함수 바로 위에 있으므로,
         섹션의 ref 주석을 다음에 나오는 check_* 함수와 연결한다.

    반환: {"check_cb_log_throttle": ["20260410_1"], ...}
    """
    if not script.exists():
        return {}

    text = script.read_text(encoding="utf-8")
    lines = text.splitlines()
    result: dict[str, list[str]] = {}

    try:
        tree = ast.parse(text, filename=str(script))
    except SyntaxError:
        return {}

    # check_* 함수 목록과 시작 라인 수집
    check_nodes: list[tuple[int, str]] = []  # (lineno, func_name)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("check_"):
            continue
        check_nodes.append((node.lineno, node.name))
        # 함수 본문에서 직접 참조
        func_lines = lines[node.lineno - 1: node.end_lineno]
        func_text = "\n".join(func_lines)
        refs = list(dict.fromkeys(_LESSON_REF_IN_CODE_RE.findall(func_text)))
        result[node.name] = refs

    check_nodes.sort(key=lambda x: x[0])

    # 섹션 주석 블록에서 ref: 참조를 찾아 바로 다음 check_* 함수와 연결
    # 패턴: '# ref: docs/lessons/YYYYMMDD_N_...'  (함수 외부에도 존재)
    section_ref_re = re.compile(r"#\s*ref:\s*docs/lessons/(\d{8}_\d+)")
    # 각 ref 주석이 어떤 check_ 함수 직전에 있는지 파악
    for lineno_0, line_text in enumerate(lines, 1):
        m = section_ref_re.search(line_text)
        if not m:
            continue
        lesson_id = m.group(1)
        # 이 주석 라인 이후에 나오는 첫 번째 check_* 함수에 연결
        # (섹션 블록과 함수 사이에 최대 20줄까지만 허용)
        for fn_lineno, fn_name in check_nodes:
            if fn_lineno > lineno_0 and fn_lineno <= lineno_0 + 20:
                if lesson_id not in result.get(fn_name, []):
                    result.setdefault(fn_name, []).append(lesson_id)
                break

    return result


# ═══════════════════════════════════════════════════════════════════════════
# 매핑 로직
# ═══════════════════════════════════════════════════════════════════════════

def _map_lesson_to_rules(
    lesson: dict,
    lint_rules: dict[str, str],
    check_funcs: dict[str, str],
    lesson_ref_map: dict[str, list[str]],
) -> dict:
    """하나의 lesson에 대해 매핑 결과를 반환한다.

    반환 구조:
      {
        "name": str,
        "has_section": bool,
        "linked_rules": ["R1", "R2"],        # 실제로 존재하는 규칙 참조
        "linked_checks": ["check_xxx"],       # 실제로 존재하는 함수 참조
        "missing_rules": ["R5"],              # 참조하지만 코드에 없는 규칙
        "implicit_checks": ["check_cb_..."],  # lesson 본문에 없지만 코드에서 역참조
        "status": "OK" | "WARN" | "ERROR",
        "status_reason": str,
      }
    """
    name = lesson["name"]
    # lesson 식별자 (날짜_번호 부분만 추출)
    id_match = re.match(r"(\d{8}_\d+)", name)
    lesson_id = id_match.group(1) if id_match else name

    linked_rules: list[str] = []
    missing_rules: list[str] = []
    linked_checks: list[str] = []

    # 1) 직접 규칙 참조 (R1, R2 등)
    for rref in lesson["rule_refs"]:
        if rref in lint_rules:
            linked_rules.append(rref)
        else:
            missing_rules.append(rref)

    # 2) 직접 check_ 함수 참조
    for cref in lesson["check_refs"]:
        if cref in check_funcs:
            linked_checks.append(cref)
        # check_ 함수가 코드에 없어도 "존재하지 않는 참조"로만 기록
        # (lesson 측에서 언급한 것이지, 실제 집행 여부는 별도)

    # 3) pre_deploy_check.py가 역방향으로 이 lesson을 참조하는지
    implicit_checks: list[str] = []
    for func_name, refs in lesson_ref_map.items():
        # "20260408_4" 가 "20260408_4_nonetype_format_lint" lesson_id 에 포함되는지
        for ref in refs:
            if ref == lesson_id or lesson_id.startswith(ref):
                if func_name not in linked_checks and func_name not in implicit_checks:
                    implicit_checks.append(func_name)

    # 4) 상태 판정
    has_section = lesson["has_section"]
    all_linked = linked_rules + linked_checks + implicit_checks

    if not has_section:
        status = "WARN"
        status_reason = "검증규칙 섹션 없음 (경고)"
    elif missing_rules:
        status = "ERROR"
        status_reason = (
            f"검증규칙 섹션에 명시된 규칙 {missing_rules}이(가) "
            "lint_none_format.py와 pre_deploy_check.py 어디에도 없음"
        )
    elif not all_linked:
        status = "ERROR"
        status_reason = "검증규칙 섹션이 있으나 코드 매핑 없음 (미집행)"
    else:
        status = "OK"
        status_reason = "매핑됨"

    return {
        "name": name,
        "lesson_id": lesson_id,
        "has_section": has_section,
        "linked_rules": linked_rules,
        "linked_checks": linked_checks,
        "implicit_checks": implicit_checks,
        "missing_rules": missing_rules,
        "status": status,
        "status_reason": status_reason,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 리포트 출력
# ═══════════════════════════════════════════════════════════════════════════

def _print_report(
    lessons: list[dict],
    mappings: list[dict],
    lint_rules: dict[str, str],
    check_funcs: dict[str, str],
) -> int:
    """표준 리포트를 stdout에 출력하고 exit code를 반환한다."""
    total = len(lessons)
    n_rules = len(lint_rules)
    n_checks = len(check_funcs)

    ok_count = sum(1 for m in mappings if m["status"] == "OK")
    warn_count = sum(1 for m in mappings if m["status"] == "WARN")
    error_count = sum(1 for m in mappings if m["status"] == "ERROR")

    print(f"[메타린트] lessons={total} / 규칙 R1~R{n_rules}={n_rules} / "
          f"pre_deploy_check 함수={n_checks}개")
    print()
    print("[매핑]")

    for mp in mappings:
        name = mp["name"]
        linked = mp["linked_rules"] + mp["linked_checks"] + mp["implicit_checks"]
        st = mp["status"]

        if st == "OK":
            tag = "OK"
            refs_str = "/".join(linked) if linked else "(직접 참조 없음)"
            symbol = "✅"
        elif st == "WARN":
            tag = "WARN"
            refs_str = mp["status_reason"]
            symbol = "⚠"
        else:
            tag = "ERROR"
            refs_str = mp["status_reason"]
            symbol = "❌"

        # 묵시적 체크(역방향)는 훅 표시
        implicit = mp["implicit_checks"]
        implicit_str = ""
        if implicit:
            implicit_str = f" (훅: {', '.join(implicit)})"

        direct_str = ""
        direct = mp["linked_rules"] + mp["linked_checks"]
        if direct:
            direct_str = "/".join(direct) + " "

        if st == "OK":
            print(f"  lesson {name} -> {direct_str}{implicit_str} {symbol}")
        else:
            print(f"  lesson {name} -> {symbol} [{tag}] {refs_str}")

    # 미연결 상세
    errors = [m for m in mappings if m["status"] == "ERROR"]
    warns = [m for m in mappings if m["status"] == "WARN"]

    if warns:
        print()
        print(f"[미연결 lesson — 경고 {warn_count}건]")
        for m in warns:
            print(f"  - {m['name']}: {m['status_reason']}")

    if errors:
        print()
        print(f"[오류 lesson — ERROR {error_count}건]")
        for m in errors:
            print(f"  - {m['name']}: {m['status_reason']}")

    print()
    print("[요약]")
    print(f"  총 lessons          : {total}")
    print(f"  매핑됨 (OK)         : {ok_count}")
    print(f"  경고 (섹션 없음 등) : {warn_count}")
    print(f"  오류 (미집행 규칙)  : {error_count}")

    all_missing: list[str] = []
    for m in mappings:
        all_missing.extend(m["missing_rules"])
    unique_missing = sorted(set(all_missing))
    print(f"  존재하지 않는 규칙 참조: {len(unique_missing)}건 {unique_missing}")

    return 1 if error_count > 0 else 0


def _print_json(
    lessons: list[dict],
    mappings: list[dict],
    lint_rules: dict[str, str],
    check_funcs: dict[str, str],
) -> int:
    """JSON 리포트를 stdout에 출력하고 exit code를 반환한다."""
    all_missing_rules: list[str] = []
    unmapped_names: list[str] = []
    error_count = 0

    for m in mappings:
        all_missing_rules.extend(m["missing_rules"])
        if m["status"] == "ERROR":
            error_count += 1
            unmapped_names.append(m["name"])

    output = {
        "summary": {
            "total_lessons": len(lessons),
            "total_rules": len(lint_rules),
            "total_check_functions": len(check_funcs),
            "ok": sum(1 for m in mappings if m["status"] == "OK"),
            "warn": sum(1 for m in mappings if m["status"] == "WARN"),
            "error": error_count,
        },
        "lessons": [
            {
                "name": m["name"],
                "lesson_id": m["lesson_id"],
                "has_section": m["has_section"],
                "linked_rules": m["linked_rules"],
                "linked_checks": m["linked_checks"],
                "implicit_checks": m["implicit_checks"],
                "missing_rules": m["missing_rules"],
                "status": m["status"],
                "status_reason": m["status_reason"],
            }
            for m in mappings
        ],
        "rules": sorted(lint_rules.keys()),
        "check_functions": sorted(check_funcs.keys()),
        "missing_rules": sorted(set(all_missing_rules)),
        "unmapped_lessons": unmapped_names,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if error_count > 0 else 0


# ═══════════════════════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="lessons ↔ 린트 규칙 매핑 검증 도구"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_mode",
        help="JSON 형식으로 출력"
    )
    # 테스트용 경로 오버라이드
    parser.add_argument("--lessons-dir", type=Path, default=None,
                        help="lessons 디렉토리 오버라이드 (테스트용)")
    parser.add_argument("--lint-script", type=Path, default=None,
                        help="lint_none_format.py 경로 오버라이드 (테스트용)")
    parser.add_argument("--predeploy-script", type=Path, default=None,
                        help="pre_deploy_check.py 경로 오버라이드 (테스트용)")
    args = parser.parse_args()

    lessons_dir = args.lessons_dir or LESSONS_DIR
    lint_script = args.lint_script or LINT_SCRIPT
    predeploy_script = args.predeploy_script or PRE_DEPLOY_SCRIPT

    # 1) 파싱
    lessons = _parse_lessons(lessons_dir)
    lint_rules = _parse_lint_rules(lint_script)
    check_funcs = _parse_predeploy_functions(predeploy_script)
    lesson_ref_map = _build_lesson_ref_map(predeploy_script)

    # 2) 매핑
    mappings = [
        _map_lesson_to_rules(lesson, lint_rules, check_funcs, lesson_ref_map)
        for lesson in lessons
    ]

    # 3) 출력
    if args.json_mode:
        return _print_json(lessons, mappings, lint_rules, check_funcs)
    else:
        return _print_report(lessons, mappings, lint_rules, check_funcs)


if __name__ == "__main__":
    sys.exit(main())
