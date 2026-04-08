#!/usr/bin/env python3
"""NoneType.__format__ 재발 방지 린터.

탐지 규칙:
  R1 (ERROR) f-string 숫자 포매팅에 `<obj>.get(...)` 직접 사용 금지
     f"{d.get('x', 0):,.0f}"  ← .get의 default는 '키 부재'일 때만 작동,
                                 값이 None이면 그대로 None → 포매팅 크래시
     → _fmt_num(d.get('x'))  로 교체

  R2 (ERROR) format(<obj>.get(...), "<numeric spec>") 직접 사용 금지

  R3 (WARN) ccxt 주문 응답 위험 키 직접 접근 (cost/price/average/filled)
     → _resolve_fill(...) 경유 권장

검출 범위: scripts/, services/
제외:     venv, __pycache__, lint_none_format.py 자기 자신

사용법:
  python scripts/lint_none_format.py           # 실행 (종료코드 0/1)
  python scripts/lint_none_format.py --warn    # 경고도 실패로 취급

참고: docs/lessons/20260408_4_nonetype_format_lint.md
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["scripts", "services"]
EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git"}
EXCLUDE_FILES = {"lint_none_format.py"}

# 숫자형 포매팅을 나타내는 타입 지정자
NUMERIC_TYPES = set("bcdeEfFgGnoxX%")

# ccxt 주문 응답에서 None으로 오는 대표 위험 키
CCXT_RISKY_KEYS = {"cost", "price", "average", "filled"}


class Finding:
    __slots__ = ("path", "line", "col", "rule", "severity", "msg")

    def __init__(self, path: Path, line: int, col: int, rule: str,
                 severity: str, msg: str) -> None:
        self.path = path
        self.line = line
        self.col = col
        self.rule = rule
        self.severity = severity
        self.msg = msg

    def format(self) -> str:
        rel = self.path.relative_to(PROJECT_ROOT)
        return f"  [{self.severity}] {rel}:{self.line}:{self.col} {self.rule} — {self.msg}"


def _is_numeric_format_spec(spec: str) -> bool:
    """포맷 스펙 문자열이 숫자형 포매팅인지 판정.

    예: ',.0f', '.2f', 'd', ',.8f', '>10,.2%', '', 's'
    """
    if not spec:
        return False
    # 타입 지정자는 보통 마지막 문자
    last = spec[-1]
    if last in NUMERIC_TYPES:
        return True
    # 타입 지정자 없이 `,` 또는 `_` 단독인 경우도 숫자 전제
    if any(c in spec for c in (",", "_")) and last not in ("s", "r", "a"):
        return True
    return False


def _unparse_spec(spec_node: ast.AST | None) -> str:
    """FormattedValue.format_spec (JoinedStr)에서 리터럴 문자열 추출."""
    if spec_node is None:
        return ""
    if isinstance(spec_node, ast.JoinedStr):
        out = []
        for v in spec_node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out.append(v.value)
            else:
                # 동적 스펙(f"{:.{n}f}") — 보수적으로 숫자로 간주
                out.append("f")
        return "".join(out)
    if isinstance(spec_node, ast.Constant) and isinstance(spec_node.value, str):
        return spec_node.value
    return ""


def _is_get_call(node: ast.AST) -> bool:
    """<expr>.get(...) 호출 여부."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    )


def _is_safe_get(node: ast.Call) -> bool:
    """.get(key, default)에서 default가 명시적으로 '안전'한 경우.

    _fmt_num 등 래퍼 내부에서는 어차피 호출 전에 값을 체크하므로 이 린트가
    필요 없지만, 여기서는 '.get의 default가 숫자 리터럴이라도 안전하지
    않다'는 규칙 1을 강제한다. 따라서 항상 False 반환 (규칙 1은 엄격).
    """
    return False


def _get_const_key(call: ast.Call) -> str | None:
    """.get('key', ...) 의 키 리터럴 반환."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for target in TARGETS:
        base = PROJECT_ROOT / target
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if p.name in EXCLUDE_FILES:
                continue
            files.append(p)
    return files


def _check_file(path: Path, findings: list[Finding]) -> None:
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        findings.append(Finding(path, 0, 0, "SYS", "WARN",
                                f"파일 읽기 실패: {e}"))
        return

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        findings.append(Finding(path, e.lineno or 0, e.offset or 0,
                                "SYS", "WARN", f"구문 오류: {e.msg}"))
        return

    for node in ast.walk(tree):
        # ─── R1: f-string 내부 .get() 직접 숫자 포매팅 ───
        if isinstance(node, ast.FormattedValue):
            spec = _unparse_spec(node.format_spec)
            if _is_numeric_format_spec(spec) and _is_get_call(node.value):
                key = _get_const_key(node.value)  # type: ignore[arg-type]
                key_desc = f"'{key}'" if key else "동적 키"
                findings.append(Finding(
                    path, node.lineno, node.col_offset, "R1", "ERROR",
                    f"f-string 숫자 포매팅(':{spec}')에 .get({key_desc}) 직접 사용 — "
                    f".get default는 키 부재일 때만 작동하며 값이 None이면 크래시. "
                    f"_fmt_num() 래퍼 또는 사전 None 체크 필요."
                ))

        # ─── R2: format(x.get(...), "<numeric>") ───
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "format"
            and len(node.args) >= 2
            and _is_get_call(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and _is_numeric_format_spec(node.args[1].value)
        ):
            findings.append(Finding(
                path, node.lineno, node.col_offset, "R2", "ERROR",
                f"format(x.get(...), '{node.args[1].value}') 직접 사용 — "
                f"_fmt_num() 사용 권장"
            ))

        # ─── R3: ccxt 주문 응답의 위험 키 .get() 접근 ───
        # (파일 이름이 jarvis_executor.py 라도 _resolve_fill 안이면 OK —
        #  여기서는 단순 WARN 으로 리포트만)
        if _is_get_call(node):
            key = _get_const_key(node)  # type: ignore[arg-type]
            if key in CCXT_RISKY_KEYS:
                # _resolve_fill 내부이거나 파일명이 린터/헬퍼면 제외
                if path.name == "lint_none_format.py":
                    continue
                findings.append(Finding(
                    path, node.lineno, node.col_offset, "R3", "WARN",
                    f".get('{key}') — ccxt 시장가 주문 직후 None 가능. "
                    f"_resolve_fill() 경유 또는 None 체크 확인"
                ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--warn", action="store_true",
                        help="경고도 실패로 취급 (종료코드 1)")
    parser.add_argument("--quiet", action="store_true",
                        help="WARN 출력 생략")
    args = parser.parse_args()

    files = _iter_python_files()
    findings: list[Finding] = []
    for f in files:
        _check_file(f, findings)

    errors = [f for f in findings if f.severity == "ERROR"]
    warns = [f for f in findings if f.severity == "WARN"]

    print("=" * 60)
    print("NoneType 포매팅 린터 (lint_none_format)")
    print("=" * 60)
    print(f"대상 파일: {len(files)}개")

    if errors:
        print(f"\nERROR {len(errors)}건:")
        for f in errors:
            print(f.format())

    if warns and not args.quiet:
        print(f"\nWARN {len(warns)}건:")
        for f in warns:
            print(f.format())

    if not errors and not warns:
        print("\n✅ 위반 없음")
        return 0

    if errors or (args.warn and warns):
        print(f"\n❌ 실패 — ERROR {len(errors)}, WARN {len(warns)}")
        return 1

    print(f"\n⚠ ERROR 없음 (WARN {len(warns)}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
