#!/usr/bin/env python3
"""NoneType.__format__ / KeyError 재발 방지 린터.

탐지 규칙:
  R1 (ERROR) f-string 숫자 포매팅에 `<obj>.get(...)` 직접 사용 금지
     f"{d.get('x', 0):,.0f}"  ← .get의 default는 '키 부재'일 때만 작동,
                                 값이 None이면 그대로 None → 포매팅 크래시
     → _fmt_num(d.get('x'))  로 교체

  R2 (ERROR) format(<obj>.get(...), "<numeric spec>") 직접 사용 금지

  R3 (WARN) ccxt 주문 응답 위험 키 직접 접근 (cost/price/average/filled)
     → _resolve_fill(...) 경유 권장

  R4 (WARN) f-string 숫자 포매팅에 dict[key] subscript 직접 사용
     f"{d['x']:,.0f}"  ← 키 부재 시 KeyError 런타임 크래시
     → d.get('x', 0) 또는 사전 존재 확인 필요

  R5 (WARN) datetime.strptime()에 dict subscript / .get() 직접 전달
     datetime.strptime(d['date'], fmt)  ← 값이 None/빈문자열이면 ValueError
     → 사전 None/빈문자열 체크 필요 (업비트 API 빈 필드 방어)

  R6 (WARN) async 함수 내부에서 ccxt fetch_* 호출에 `await` 누락
     async def foo():
         exchange.fetch_balance()  ← coroutine 반환/동기 경로 혼용 위험
     → 동기 래퍼 사용 시에는 asyncio.to_thread() 또는 명시적 래퍼 사용 필요
     탐지 범위: async def 내부에서 `*.fetch_balance|fetch_ticker|fetch_tickers|fetch_ohlcv|fetch_order` 를
               expression statement(결과 미사용) 또는 동기 할당으로 사용한 경우.

  R7 (WARN) 상태 로드 직후 거래소 잔고 교차검증 누락
     load_state() / state["positions"] 참조가 있는 함수 내에서
     fetch_balance 호출이 전혀 없으면 경고.
     → lessons/20260408_2 (state ↔ balance mismatch) 방지
     탐지 범위: services/execution/*.py 한정

  R8 (WARN) 시장가 주문 직후 sleep 없는 fetch_order
     create_market_buy_order(...) 다음 2문장 이내에 sleep 없이 fetch_order 가 호출되면
     업비트 체결 반영 지연으로 None 필드 수령 위험.
     → asyncio.sleep(0.3) 또는 time.sleep(0.3) 삽입 필요

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

# R3 억제: 다음 함수의 인수로 전달되는 .get(...)은 안전(None-safe 래퍼)
SAFE_WRAPPERS = {"_fmt_num", "fmt_num", "resolve_fill", "_resolve_fill"}

# R6: async 컨텍스트에서 await 누락을 탐지할 ccxt 비동기 엔드포인트 후보
CCXT_ASYNC_METHODS = {
    "fetch_balance",
    "fetch_ticker",
    "fetch_tickers",
    "fetch_ohlcv",
    "fetch_order",
    "fetch_orders",
    "fetch_open_orders",
    "create_order",
    "create_market_buy_order",
    "create_market_sell_order",
}

# R7: services/execution 한정 — 상태 로드 식별자 및 거래소 조회 식별자
STATE_LOAD_NAMES = {"load_state", "_load_state"}
BALANCE_CHECK_NAMES = {"fetch_balance", "get_balance"}

# R8: 시장가 주문 이름
MARKET_ORDER_METHODS = {"create_market_buy_order", "create_market_sell_order"}


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


def _attach_parents(tree: ast.AST) -> None:
    """각 노드에 parent 참조를 심는다 (R3 문맥 판단용)."""
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]


def _inside_safe_wrapper(node: ast.AST) -> bool:
    """노드가 SAFE_WRAPPERS 함수 호출의 인수인지."""
    parent = getattr(node, "parent", None)
    if not isinstance(parent, ast.Call):
        return False
    if node not in parent.args:
        return False
    func = parent.func
    if isinstance(func, ast.Name) and func.id in SAFE_WRAPPERS:
        return True
    if isinstance(func, ast.Attribute) and func.attr in SAFE_WRAPPERS:
        return True
    return False


def _inside_or_chain(node: ast.AST) -> bool:
    """노드가 BoolOp(Or)의 피연산자 또는 그 하위에 있는지.

    예: `order.get('price') or price`  →  True
        `order.get('price') or order.get('average')`  →  True
        `f"{x.get('price'):,.0f}"`  →  False
    """
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, ast.BoolOp) and isinstance(cur.op, ast.Or):
            return True
        # 포매팅/함수 호출 레벨을 넘어가면 중단
        if isinstance(cur, (ast.FormattedValue, ast.Call, ast.Assign,
                            ast.Return, ast.FunctionDef, ast.Module)):
            # Call의 경우에도 그 Call이 BoolOp 안에 있을 수 있음 → 계속
            if isinstance(cur, (ast.FunctionDef, ast.Module, ast.FormattedValue)):
                return False
        cur = getattr(cur, "parent", None)
    return False


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

    _attach_parents(tree)

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

        # ─── R4: f-string 숫자 포매팅에 dict[key] subscript 직접 사용 ───
        if isinstance(node, ast.FormattedValue):
            spec = _unparse_spec(node.format_spec)
            val = node.value
            # BoolOp(Or) 내부(예: `d['x'] or 0`)는 안전 → 제외
            if (
                _is_numeric_format_spec(spec)
                and isinstance(val, ast.Subscript)
                and isinstance(val.slice, ast.Constant)
                and isinstance(val.slice.value, str)
                and not _inside_or_chain(val)
                and not _inside_safe_wrapper(val)
            ):
                key = val.slice.value
                findings.append(Finding(
                    path, node.lineno, node.col_offset, "R4", "WARN",
                    f"f-string 숫자 포매팅(':{spec}')에 dict['{key}'] "
                    f"subscript 직접 사용 — 키 부재 시 KeyError. "
                    f".get('{key}', 0) 또는 사전 존재 확인 필요"
                ))

        # ─── R3: ccxt 주문 응답의 위험 키 .get() 접근 ───
        if _is_get_call(node):
            key = _get_const_key(node)  # type: ignore[arg-type]
            if key in CCXT_RISKY_KEYS:
                # 제외 조건:
                #   (1) 린터/공용 헬퍼 본체
                #   (2) `or <fallback>` 체인 안 (이미 안전 패턴)
                #   (3) None-safe 래퍼(_fmt_num 등)의 인수
                #   (4) upbit_client._parse_order 의 의도된 파싱 레이어
                if path.name in ("lint_none_format.py", "ccxt_utils.py"):
                    continue
                if _inside_or_chain(node):
                    continue
                if _inside_safe_wrapper(node):
                    continue
                if path.name == "upbit_client.py":
                    # 파싱 책임 경계 — 호출자에서 resolve_fill 사용해야 함
                    # (문서화된 예외, docs/lint_layer.md 참조)
                    continue
                findings.append(Finding(
                    path, node.lineno, node.col_offset, "R3", "WARN",
                    f".get('{key}') — ccxt 시장가 주문 직후 None 가능. "
                    f"resolve_fill() 경유 또는 `or <fallback>` 패턴 필요"
                ))

        # ─── R5: datetime.strptime()에 dict subscript / .get() 직접 전달 ───
        if (
            isinstance(node, ast.Call)
            and _is_strptime_call(node)
            and node.args
        ):
            first_arg = node.args[0]
            unsafe = False
            desc = ""
            if isinstance(first_arg, ast.Subscript) and isinstance(
                getattr(first_arg, "slice", None), ast.Constant
            ):
                key = first_arg.slice.value
                desc = f"dict['{key}']"
                unsafe = True
            elif _is_get_call(first_arg):
                key = _get_const_key(first_arg)
                desc = f".get('{key}')" if key else ".get(동적 키)"
                unsafe = True
            if unsafe and not _inside_or_chain(first_arg):
                findings.append(Finding(
                    path, node.lineno, node.col_offset, "R5", "WARN",
                    f"datetime.strptime({desc}, ...) — 값이 None/빈문자열이면 "
                    f"ValueError. 사전 None/빈문자열 체크 필요"
                ))

    # R6 / R7 / R8 — 함수 단위 또는 문장-블록 단위 체크
    _check_r6_r7_r8(path, tree, findings)


def _is_strptime_call(node: ast.Call) -> bool:
    """datetime.strptime 또는 datetime.datetime.strptime 호출 여부."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "strptime":
        return True
    return False


def _enclosing_func(node: ast.AST) -> ast.AST | None:
    """노드를 감싸는 가장 가까운 FunctionDef/AsyncFunctionDef 반환."""
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, (ast.AsyncFunctionDef, ast.FunctionDef)):
            return cur
        cur = getattr(cur, "parent", None)
    return None


def _is_inside_await(node: ast.AST) -> bool:
    """노드가 Await 또는 Await 식의 하위에 있는지."""
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, ast.Await):
            return True
        # 함수 정의 경계를 넘으면 중단
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        cur = getattr(cur, "parent", None)
    return False


def _check_r6_r7_r8(path: Path, tree: ast.AST, findings: list[Finding]) -> None:
    """R6/R7/R8 규칙 통합 체크.

    R6: AsyncFunctionDef 내부에서 CCXT_ASYNC_METHODS 호출이 await 없이 쓰이면 경고.
        다만, asyncio.to_thread / asyncio.get_event_loop().run_in_executor / loop.run_in_executor
        의 positional 인수로 전달된 경우는 OK.

    R7: services/execution/*.py 의 함수 내에서
         STATE_LOAD_NAMES 호출 또는 state["positions"] 접근이 있으면서
         BALANCE_CHECK_NAMES 호출이 전혀 없으면 경고.
         (해당 함수 + 호출하는 다른 함수의 본문은 정적으로 판단 어려우므로 함수 경계 기준)

    R8: 함수 본문에서 MARKET_ORDER_METHODS 호출 직후 같은 함수 내 다음 2 문장 안에
         sleep(*)/asyncio.sleep(*) 없이 fetch_order가 있으면 경고.
    """
    # R6
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        if attr not in CCXT_ASYNC_METHODS:
            continue
        fn = _enclosing_func(node)
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue  # 동기 함수에서는 R6 대상 아님 (R6는 async 전용)
        if _is_inside_await(node):
            continue
        # 안전 패턴: asyncio.to_thread(...) 또는 run_in_executor(...) 의 인수로 전달
        parent = getattr(node, "parent", None)
        safe = False
        if isinstance(parent, ast.Call):
            pfn = parent.func
            if isinstance(pfn, ast.Attribute) and pfn.attr in {"to_thread", "run_in_executor"}:
                safe = True
            elif isinstance(pfn, ast.Name) and pfn.id in {"to_thread"}:
                safe = True
        if safe:
            continue
        # 결과 할당이 coroutine 자체를 받는 경우도 위험이지만 기본적으로 경고 대상
        findings.append(Finding(
            path, node.lineno, node.col_offset, "R6", "WARN",
            f"async def 내부에서 .{attr}(...) 를 await 없이 호출 — "
            f"ccxt 비동기 또는 동기 래퍼 혼용 위험. await 또는 asyncio.to_thread() 사용"
        ))

    # R7 — services/execution 한정
    try:
        rel = path.relative_to(PROJECT_ROOT)
    except ValueError:
        rel = path
    in_execution = False
    try:
        parts = rel.parts
        in_execution = len(parts) >= 2 and parts[0] == "services" and parts[1] == "execution"
    except Exception:
        pass

    # R7 스코프 제한:
    #  - circuit_breaker.py / telegram_bot.py 등 CB/UI 파일은 자체 state(triggered 플래그)이므로 제외.
    #  - 대상 파일: realtime_monitor / multi_trader / trader / scanner 등 "거래 집행 경로"
    R7_FILES = {"realtime_monitor.py", "multi_trader.py", "trader.py", "scanner.py"}
    if in_execution and path.name in R7_FILES:
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_walk = list(ast.walk(fn))
            uses_positions_subscript = False
            uses_balance = False
            state_node: ast.AST | None = None
            has_buy_or_sell = False
            for n in body_walk:
                # 반드시 `["positions"]` subscript 가 **실제로** 등장해야 함 — 단순 load_state 호출만으로는 탐지 X
                if (
                    isinstance(n, ast.Subscript)
                    and isinstance(n.slice, ast.Constant)
                    and n.slice.value == "positions"
                ):
                    uses_positions_subscript = True
                    state_node = state_node or n
                if isinstance(n, ast.Call):
                    f = n.func
                    name = None
                    if isinstance(f, ast.Attribute):
                        name = f.attr
                    elif isinstance(f, ast.Name):
                        name = f.id
                    if name in BALANCE_CHECK_NAMES:
                        uses_balance = True
                    # 매수/매도 경로 지표 — buy_market_coin / sell_market_coin / _execute_buy / _execute_sell
                    if name in {"buy_market_coin", "sell_market_coin",
                                "_execute_buy", "_execute_sell",
                                "create_market_buy_order", "create_market_sell_order"}:
                        has_buy_or_sell = True

            # 매수/매도 경로에서만 + positions subscript 실사용 + balance 교차검증 미호출
            if uses_positions_subscript and has_buy_or_sell and not uses_balance and state_node is not None:
                # 순수 state 저장/표시 함수 제외
                if fn.name in {"save_state", "_save_state", "load_state", "_load_state",
                                "close_position", "open_position", "__init__",
                                "_load_vb_state", "_load_ema_trend_state",
                                "_save_vb_state", "_save_ema_trend_state",
                                "show_status", "_cmd_reset", "_cmd_status"}:
                    continue
                findings.append(Finding(
                    path, state_node.lineno, state_node.col_offset, "R7", "WARN",
                    f"함수 '{fn.name}' 가 state['positions'] 를 참조하고 매수/매도 경로를 가짐에도 "
                    f"fetch_balance/get_balance 교차검증이 없음 — lessons/20260408_2 (state ↔ balance mismatch) 위험"
                ))

    # R8
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stmts = fn.body
        for idx, stmt in enumerate(stmts):
            # market order 호출을 직접 포함하는 statement 찾기
            has_market = False
            market_attr: str | None = None
            market_ln = stmt.lineno
            market_col = stmt.col_offset
            for n in ast.walk(stmt):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr in MARKET_ORDER_METHODS
                ):
                    has_market = True
                    market_attr = n.func.attr
                    market_ln = n.lineno
                    market_col = n.col_offset
                    break
            if not has_market:
                continue

            # 다음 최대 2문장 안에 sleep/asyncio.sleep 없는 fetch_order 탐지
            followup = stmts[idx + 1: idx + 3]
            has_sleep = False
            has_fetch_order = False
            fetch_node_ln = 0
            fetch_node_col = 0
            for fstmt in followup:
                for n in ast.walk(fstmt):
                    if isinstance(n, ast.Call):
                        f = n.func
                        name = None
                        if isinstance(f, ast.Attribute):
                            name = f.attr
                        elif isinstance(f, ast.Name):
                            name = f.id
                        if name == "sleep":
                            has_sleep = True
                        if name == "fetch_order":
                            has_fetch_order = True
                            fetch_node_ln = n.lineno
                            fetch_node_col = n.col_offset
            if has_fetch_order and not has_sleep:
                findings.append(Finding(
                    path, fetch_node_ln or market_ln, fetch_node_col or market_col,
                    "R8", "WARN",
                    f".{market_attr}(...) 직후 sleep 없이 fetch_order 호출 — "
                    f"업비트 체결 반영 지연으로 None 필드 수령 위험. "
                    f"asyncio.sleep(0.3~1.0) 또는 time.sleep(0.3) 삽입 권장"
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
