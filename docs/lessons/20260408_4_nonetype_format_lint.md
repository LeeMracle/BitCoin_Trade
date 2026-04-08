# NoneType.__format__ 오류 재발 방지 린트 도입

- **발생일**: 2026-04-08
- **심각도**: MEDIUM (재발 방지 / 운영 안정성)
- **카테고리**: 코드 품질 / 정적 분석

## 배경

2026-04-08 BTC 분할매도 TP1 수동 실행 시 `unsupported format string passed to NoneType.__format__` 오류 발생. 원인은 ccxt 업비트 시장가 주문 접수 응답에서 `cost`·`average`·`price`가 모두 `None`으로 돌아오는데, f-string 포매팅이 `{result.get('cost', 0):,.0f}` 형태라 **`.get(key, default)`의 default가 "키 부재"일 때만 작동하고 값이 None이면 None을 그대로 반환** → 포매팅 크래시.

주문 자체는 정상 체결되었고 상태 파일도 저장되었으나, 텔레그램 알림과 로그만 깨짐. 조용한 실패라 발견이 늦어질 수 있음.

## 뿌리 원인

Python `dict.get(key, default)`의 의미는 "key가 없을 때 default" — **None인 key는 None을 돌려준다**. 개발자가 자주 혼동하는 함정.

동일 패턴이 코드베이스에 이미 10건 잠복해 있었음 (`backtest_composite_9yr.py`, `hourly_monitor.py`, `jarvis_executor.py`, `realtime_monitor.py`, `telegram_bot.py`).

## 수정

1. **`scripts/jarvis_executor.py`**:
   - `_fmt_num(v, spec, fallback)` None-safe 포매터 추가
   - `_resolve_fill(exchange, order, symbol, amount_hint)` — 시장가 응답 체결정보 해석 (우선순위: order field → fetch_order 재조회 → ticker 추정)
   - 모든 포매팅을 `_fmt_num` 경유로 전환

2. **린트 도입 — `scripts/lint_none_format.py`** (AST 기반):
   - **R1 (ERROR)**: f-string 숫자 포매팅에 `x.get(...)` 직접 사용 금지
   - **R2 (ERROR)**: `format(x.get(...), "<numeric>")` 직접 사용 금지
   - **R3 (WARN)**: ccxt 주문 응답 위험 키(`cost`/`price`/`average`/`filled`) `.get()` 접근 — `_resolve_fill` 경유 권장
   - 탐지 범위: `scripts/`, `services/`
   - 최초 실행 결과: **ERROR 10건 + WARN 28건**

3. **기존 코드베이스의 R1 위반 10건 모두 수정**:
   - 패턴: `d.get('key', 0):,.0f` → `(d.get('key') or 0):,.0f`
   - `(x or 0)` 는 `ast.BoolOp(Or)` 이라 린터가 flag 하지 않음 (의도적 허용 패턴)
   - 수정 파일: `backtest_composite_9yr.py`, `hourly_monitor.py`, `jarvis_executor.py`, `realtime_monitor.py`, `telegram_bot.py`

4. **`scripts/pre_deploy_check.py`에 `check_none_format_lint()` 통합**:
   - 배포 전 자동 린트 실행
   - ERROR 있으면 배포 중단

## 검증 규칙

1. `python scripts/lint_none_format.py` 종료코드 0
2. `python scripts/pre_deploy_check.py` 가 린트를 내부 호출하여 ERROR 0건 유지
3. 새 포매팅 코드 추가 시 다음 패턴 준수:
   ```python
   # BAD
   f"{d.get('key', 0):,.0f}"
   f"{d.get('key'):,.0f}"

   # GOOD (옵션 A: or 기본값)
   f"{(d.get('key') or 0):,.0f}"

   # GOOD (옵션 B: 전용 래퍼)
   f"{_fmt_num(d.get('key'))}"
   ```

## 교훈

1. **`.get(key, default)` 함정** — default는 키 부재일 때만 작동. 값이 None이면 None 반환. 숫자 포매팅 컨텍스트에서 이 차이를 모르면 런타임 크래시.
2. **정적 분석은 교훈을 코드로 집행하는 가장 값싼 방법** — 사람이 같은 실수를 반복하지 않도록 AST 린터를 lessons 단위로 추가하는 것이 가장 레버리지가 높다.
3. **조용한 실패(silent failure)가 가장 위험** — 주문은 체결됐으나 알림이 깨져서 사람이 결과를 확인 못 하는 상황은, 오히려 "큰 오류로 전체가 멈추는 것"보다 위험하다. 린트는 이런 "조용한 실패" 계열을 막는 방어선.
