#!/usr/bin/env python3
"""lint_meta.py 단위 테스트.

tmp 디렉토리에 가짜 lesson .md / lint_none_format.py /
pre_deploy_check.py 를 생성하여 각 케이스를 검증한다.

케이스:
  1) 모두 연결됨 (OK)         → exit 0
  2) R 규칙 직접 참조 OK      → exit 0
  3) 검증규칙 섹션 없음        → exit 0 (경고만)
  4) 섹션 있음 + 규칙 미존재  → exit 1 (ERROR)
  5) --json 출력 파싱 검증     → exit 0, JSON 구조 검증
  6) 역방향(implicit) 매핑    → exit 0 (check 함수가 lesson 역참조)
  7) 여러 lesson 혼합 케이스   → ERROR lesson만 exit 1 유발 확인
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# lint_meta 직접 import를 위해 sys.path에 scripts/ 추가
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import lint_meta  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# 픽스처 헬퍼
# ═══════════════════════════════════════════════════════════════════════════

def _write(path: Path, content: str) -> Path:
    """내용을 UTF-8로 파일에 기록하고 경로를 반환한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def _make_lint_script(tmp_path: Path, rules: list[str]) -> Path:
    """가짜 lint_none_format.py 를 생성한다.

    rules 는 ["R1", "R3"] 처럼 존재할 규칙 ID 목록.
    """
    docstring_lines = []
    for r in rules:
        num = r[1:]
        docstring_lines.append(f"  {r} (ERROR) 가짜 규칙 {r} — 테스트용")

    rule_findings = "\n".join(
        f'    findings.append(Finding(path, 1, 0, "{r}", "ERROR", "테스트"))'
        for r in rules
    )

    content = f'''\
#!/usr/bin/env python3
"""가짜 린터 (테스트 전용).

탐지 규칙:
{chr(10).join(docstring_lines)}
"""
from pathlib import Path


class Finding:
    __slots__ = ("path", "line", "col", "rule", "severity", "msg")
    def __init__(self, path, line, col, rule, severity, msg):
        self.path = path
        self.line = line
        self.col = col
        self.rule = rule
        self.severity = severity
        self.msg = msg


def _check_file(path, findings):
{rule_findings if rule_findings.strip() else "    pass"}


def main():
    import sys
    print("가짜 린터 실행")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
'''
    p = tmp_path / "lint_none_format.py"
    _write(p, content)
    return p


def _make_predeploy_script(
    tmp_path: Path,
    check_funcs: list[tuple[str, str]],
) -> Path:
    """가짜 pre_deploy_check.py 를 생성한다.

    check_funcs: [(함수명, 본문에 포함할 텍스트), ...]
    """
    funcs_code = []
    for fname, body_comment in check_funcs:
        funcs_code.append(f'''\
def {fname}():
    """{fname} 가짜 검증 함수."""
    # {body_comment}
    pass
''')

    content = '''\
#!/usr/bin/env python3
"""가짜 pre_deploy_check (테스트 전용)."""
from pathlib import Path

errors = []
warnings = []

''' + "\n".join(funcs_code) + '''

def main():
    import sys
    sys.exit(0)


if __name__ == "__main__":
    main()
'''
    p = tmp_path / "pre_deploy_check.py"
    _write(p, content)
    return p


def _make_lesson(
    lessons_dir: Path,
    name: str,
    has_section: bool,
    rule_refs: list[str] | None = None,
    check_refs: list[str] | None = None,
) -> Path:
    """가짜 lesson .md 파일을 생성한다."""
    lines = [f"# lesson {name}\n\n## 원인\n테스트용 lesson.\n"]
    if has_section:
        lines.append("\n## 검증규칙\n\n")
        if rule_refs:
            for r in rule_refs:
                lines.append(f"- `{r}` 규칙으로 집행\n")
        if check_refs:
            for c in check_refs:
                lines.append(f"- pre_deploy_check: `{c}()` 로 확인\n")
        if not rule_refs and not check_refs:
            lines.append("- 수동 확인 사항만 있음\n")
    lines.append("\n## 교훈\n테스트.\n")
    content = "".join(lines)
    p = lessons_dir / f"{name}.md"
    _write(p, content)
    return p


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 1: 모두 연결됨 (OK)
# ═══════════════════════════════════════════════════════════════════════════

class TestCase1AllLinked:
    """lesson이 R1을 참조하고, lint 스크립트에 R1이 존재 → OK, exit 0."""

    def test_status_ok(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260101_1_test_ok", has_section=True, rule_refs=["R1"])
        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lessons = lint_meta._parse_lessons(lessons_dir)
        lint_rules = lint_meta._parse_lint_rules(lint_script)
        check_funcs = lint_meta._parse_predeploy_functions(predeploy)
        lesson_ref_map = lint_meta._build_lesson_ref_map(predeploy)

        mappings = [
            lint_meta._map_lesson_to_rules(l, lint_rules, check_funcs, lesson_ref_map)
            for l in lessons
        ]

        assert len(mappings) == 1
        m = mappings[0]
        assert m["status"] == "OK", f"기대: OK, 실제: {m['status']} — {m['status_reason']}"
        assert "R1" in m["linked_rules"]

    def test_exit_code_0(self, tmp_path):
        """실제 프로세스로 실행 시 exit 0 반환 확인."""
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260101_1_test_ok", has_section=True, rule_refs=["R1"])
        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 2: R 규칙 직접 참조 OK (lint 스크립트에 실제 존재)
# ═══════════════════════════════════════════════════════════════════════════

class TestCase2RuleRefOK:
    """R3을 참조하고, 린트 스크립트에 R3이 존재 → OK."""

    def test_multiple_rules_linked(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260102_1_multi_rule", has_section=True,
                     rule_refs=["R1", "R3"])
        lint_script = _make_lint_script(tmp_path, rules=["R1", "R2", "R3"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lessons = lint_meta._parse_lessons(lessons_dir)
        lint_rules = lint_meta._parse_lint_rules(lint_script)
        check_funcs = lint_meta._parse_predeploy_functions(predeploy)
        lesson_ref_map = lint_meta._build_lesson_ref_map(predeploy)

        mappings = [
            lint_meta._map_lesson_to_rules(l, lint_rules, check_funcs, lesson_ref_map)
            for l in lessons
        ]

        assert mappings[0]["status"] == "OK"
        assert set(mappings[0]["linked_rules"]) == {"R1", "R3"}
        assert mappings[0]["missing_rules"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 3: 검증규칙 섹션 없음 → exit 0 (경고만)
# ═══════════════════════════════════════════════════════════════════════════

class TestCase3NoSection:
    """검증규칙 섹션이 없는 lesson → WARN, exit 0."""

    def test_warn_no_section(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260103_1_no_section", has_section=False)
        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lessons = lint_meta._parse_lessons(lessons_dir)
        lint_rules = lint_meta._parse_lint_rules(lint_script)
        check_funcs = lint_meta._parse_predeploy_functions(predeploy)
        lesson_ref_map = lint_meta._build_lesson_ref_map(predeploy)

        mappings = [
            lint_meta._map_lesson_to_rules(l, lint_rules, check_funcs, lesson_ref_map)
            for l in lessons
        ]

        assert mappings[0]["status"] == "WARN"

    def test_exit_code_0_when_only_warn(self, tmp_path):
        """WARN만 있으면 exit 0."""
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260103_1_no_section", has_section=False)
        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 0, (
            f"섹션 없는 lesson만 있어도 exit 0이어야 함\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 4: 섹션 있음 + 참조 규칙이 코드에 없음 → exit 1 (ERROR)
# ═══════════════════════════════════════════════════════════════════════════

class TestCase4MissingRule:
    """검증규칙 섹션에서 R9를 참조하지만 lint 스크립트에 R9가 없음 → ERROR, exit 1."""

    def test_error_on_missing_rule(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260104_1_missing_rule", has_section=True,
                     rule_refs=["R9"])
        lint_script = _make_lint_script(tmp_path, rules=["R1", "R2"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lessons = lint_meta._parse_lessons(lessons_dir)
        lint_rules = lint_meta._parse_lint_rules(lint_script)
        check_funcs = lint_meta._parse_predeploy_functions(predeploy)
        lesson_ref_map = lint_meta._build_lesson_ref_map(predeploy)

        mappings = [
            lint_meta._map_lesson_to_rules(l, lint_rules, check_funcs, lesson_ref_map)
            for l in lessons
        ]

        assert mappings[0]["status"] == "ERROR"
        assert "R9" in mappings[0]["missing_rules"]

    def test_exit_code_1_on_error(self, tmp_path):
        """ERROR lesson이 있으면 exit 1."""
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260104_1_missing_rule", has_section=True,
                     rule_refs=["R9"])
        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 1, (
            f"미집행 규칙 참조 시 exit 1이어야 함\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 5: --json 출력 파싱 검증
# ═══════════════════════════════════════════════════════════════════════════

class TestCase5JsonOutput:
    """--json 옵션 시 유효한 JSON이 출력되고 필수 키가 존재해야 한다."""

    def test_json_structure(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260105_1_json_test", has_section=True,
                     rule_refs=["R1"])
        _make_lesson(lessons_dir, "20260105_2_no_section", has_section=False)
        lint_script = _make_lint_script(tmp_path, rules=["R1", "R2"])
        predeploy = _make_predeploy_script(tmp_path, [("check_foo", "ref: lessons/20260105_1")])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--json",
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        data = json.loads(result.stdout)

        # 필수 최상위 키 확인
        for key in ("summary", "lessons", "rules", "check_functions",
                    "missing_rules", "unmapped_lessons"):
            assert key in data, f"JSON에 '{key}' 키가 없음"

        # summary 내부 키
        summary = data["summary"]
        for key in ("total_lessons", "total_rules", "total_check_functions",
                    "ok", "warn", "error"):
            assert key in summary, f"summary에 '{key}' 키가 없음"

        # lesson 항목 구조
        assert len(data["lessons"]) == 2
        for lesson_item in data["lessons"]:
            for key in ("name", "has_section", "linked_rules", "linked_checks",
                        "implicit_checks", "missing_rules", "status", "status_reason"):
                assert key in lesson_item, f"lesson 항목에 '{key}' 키가 없음"

    def test_json_exit_code_reflects_error(self, tmp_path):
        """JSON 모드에서도 ERROR lesson이 있으면 exit 1."""
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260105_3_error", has_section=True, rule_refs=["R99"])
        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--json",
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 1

        data = json.loads(result.stdout)
        assert data["summary"]["error"] >= 1
        assert "R99" in data["missing_rules"]


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 6: 역방향(implicit) 매핑 — pre_deploy_check가 lesson을 역참조
# ═══════════════════════════════════════════════════════════════════════════

class TestCase6ImplicitMapping:
    """lesson이 직접 규칙을 참조하지 않아도 pre_deploy_check에서
    해당 lesson을 역참조하면 OK로 판정된다."""

    def test_implicit_ok(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        # lesson 본문에는 check_ 함수 직접 언급 없음, 규칙 참조도 없음
        # 하지만 pre_deploy_check.py에서 이 lesson을 참조
        _make_lesson(lessons_dir, "20260106_1_implicit", has_section=True)

        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(
            tmp_path,
            [("check_implicit_test", "ref: docs/lessons/20260106_1_implicit")]
        )

        lessons = lint_meta._parse_lessons(lessons_dir)
        lint_rules = lint_meta._parse_lint_rules(lint_script)
        check_funcs = lint_meta._parse_predeploy_functions(predeploy)
        lesson_ref_map = lint_meta._build_lesson_ref_map(predeploy)

        mappings = [
            lint_meta._map_lesson_to_rules(l, lint_rules, check_funcs, lesson_ref_map)
            for l in lessons
        ]

        assert len(mappings) == 1
        m = mappings[0]
        # 역방향 참조가 있으므로 OK
        assert m["status"] == "OK", f"역방향 참조 있으면 OK여야 함: {m}"
        assert "check_implicit_test" in m["implicit_checks"]


# ═══════════════════════════════════════════════════════════════════════════
# 케이스 7: 혼합 케이스 — OK + WARN + ERROR가 섞여 있을 때 exit 1
# ═══════════════════════════════════════════════════════════════════════════

class TestCase7MixedLessons:
    """OK lesson, WARN lesson, ERROR lesson이 공존하면 exit 1이어야 한다."""

    def test_mixed_exit_1(self, tmp_path):
        lessons_dir = tmp_path / "lessons"
        # OK: R1 참조, 린트에 R1 존재
        _make_lesson(lessons_dir, "20260107_1_ok", has_section=True, rule_refs=["R1"])
        # WARN: 섹션 없음
        _make_lesson(lessons_dir, "20260107_2_warn", has_section=False)
        # ERROR: R99 참조, 린트에 없음
        _make_lesson(lessons_dir, "20260107_3_error", has_section=True, rule_refs=["R99"])

        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 1

    def test_mixed_json_counts(self, tmp_path):
        """JSON 모드에서 ok=1, warn=1, error=1 집계 확인."""
        lessons_dir = tmp_path / "lessons"
        _make_lesson(lessons_dir, "20260107_1_ok", has_section=True, rule_refs=["R1"])
        _make_lesson(lessons_dir, "20260107_2_warn", has_section=False)
        _make_lesson(lessons_dir, "20260107_3_error", has_section=True, rule_refs=["R99"])

        lint_script = _make_lint_script(tmp_path, rules=["R1"])
        predeploy = _make_predeploy_script(tmp_path, [])

        lint_meta_path = _SCRIPTS_DIR / "lint_meta.py"
        result = subprocess.run(
            [
                sys.executable, str(lint_meta_path),
                "--json",
                "--lessons-dir", str(lessons_dir),
                "--lint-script", str(lint_script),
                "--predeploy-script", str(predeploy),
            ],
            capture_output=True, text=True, encoding="utf-8",
        )
        data = json.loads(result.stdout)
        summary = data["summary"]
        assert summary["ok"] == 1, f"ok 기대 1, 실제 {summary['ok']}"
        assert summary["warn"] == 1, f"warn 기대 1, 실제 {summary['warn']}"
        assert summary["error"] == 1, f"error 기대 1, 실제 {summary['error']}"
        assert summary["total_lessons"] == 3
