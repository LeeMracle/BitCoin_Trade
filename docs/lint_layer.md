# BATA 린트 층 (Lint Layer)

> **한 줄 요약**: "같은 실수를 두 번 하지 않는다"는 교훈을 사람의 기억이 아닌 **정적 분석 코드로 강제 집행**하는 BATA 코드베이스의 방어선.

- **도입일**: 2026-04-08
- **도입 배경**: [lessons/20260408_4_nonetype_format_lint](lessons/20260408_4_nonetype_format_lint.md)
- **담당 파일**:
  - `scripts/lint_none_format.py` — AST 기반 린터 본체
  - `scripts/pre_deploy_check.py` — 배포 전 자동 호출 통합
  - `services/common/ccxt_utils.py` *(예정)* — 공용 None-safe 헬퍼

---

## 1. 무엇인가 (What)

**린트 층(Lint Layer)** 은 BATA가 운영 중 겪은 **"조용한 실패(silent failure)"** 패턴을
코드로 영구히 차단하기 위한 정적 분석 계층이다.

사건의 발단은 2026-04-08 BTC 분할매도 TP1 수동 실행 시 발생한 오류:

```
unsupported format string passed to NoneType.__format__
```

원인은 Python `dict.get(key, default)` 의미를 잘못 이해한 것 — **default는 키가
없을 때만 작동하며, 값이 None이면 None을 그대로 돌려준다**. ccxt 업비트
시장가 주문 응답은 `cost`·`average`·`price` 모두 None으로 돌아오는데, 코드가
`{order.get('cost', 0):,.0f}` 로 포매팅하여 크래시.

**더 큰 문제**: 주문은 정상 체결되었고 상태 파일도 저장되었으나 **텔레그램
알림과 로그만 깨졌다**. 사람이 확인할 신호(알림)가 깨진 채 봇은 계속 돌고
있었다. 이것이 "조용한 실패"다.

**더더 큰 문제**: 동일 패턴이 코드베이스에 **이미 10건 잠복**해 있었다.
한 개발자가 실수로 쓴 관용구가 복사-붙여넣기로 퍼져 있었던 것.

린트 층은 이런 종류의 실수를 **"한 번 겪은 뒤 영원히 방어하는"** 장치다.

---

## 2. 어떻게 동작하는가 (How)

### 2.1 3중 방어선

```
┌─────────────────────────────────────────────────────────┐
│ 1️⃣  scripts/lint_none_format.py  (AST 정적 린터)          │
│    ↓ 규칙 위반 탐지                                        │
├─────────────────────────────────────────────────────────┤
│ 2️⃣  scripts/pre_deploy_check.py                          │
│    ↓ 배포 전 자동 호출, ERROR 있으면 exit 1               │
├─────────────────────────────────────────────────────────┤
│ 3️⃣  런타임 None-safe 헬퍼 (_fmt_num / _resolve_fill)     │
│    ↓ 우회로가 뚫려도 런타임에서 마지막 방어               │
└─────────────────────────────────────────────────────────┘
```

1. **정적 분석** — 커밋/배포 전 린터가 AST를 파싱해 금지 패턴 탐지
2. **게이트** — pre_deploy_check가 린터를 호출해 ERROR 시 배포 중단
3. **런타임 방어** — 린터가 놓친 경로에서도 헬퍼 함수가 None을 안전 처리

### 2.2 린트 규칙

| ID | 수준 | 패턴 | 이유 |
|:---:|:---:|---|---|
| **R1** | ERROR | f-string 숫자 포매팅에 `x.get(...)` 직접 사용 | `.get`의 default는 키 부재일 때만 작동, 값이 None이면 그대로 None |
| **R2** | ERROR | `format(x.get(...), "<numeric>")` 직접 사용 | 동일 이유, 함수형 포매팅 경로 |
| **R3** | WARN | ccxt 주문 응답 위험 키 접근 (`cost`/`price`/`average`/`filled`) | 시장가 주문 접수 응답은 None 가능, `_resolve_fill` 경유 권장 |

### 2.3 허용/금지 패턴 사전

| 패턴 | 판정 | 이유 |
|---|:---:|---|
| `f"{d.get('x', 0):,.0f}"` | ❌ R1 | `d['x']=None`이면 default 무시됨 |
| `f"{d.get('x'):,.0f}"` | ❌ R1 | 명백히 None 가능 |
| `format(d.get('x'), '.2f')` | ❌ R2 | 함수형 포매팅도 동일 |
| **`f"{(d.get('x') or 0):,.0f}"`** | ✅ **권장 A** | `or` 연산은 AST BoolOp이라 린터가 flag 안함, None→0 안전 변환 |
| **`f"{_fmt_num(d.get('x'))}"`** | ✅ **권장 B** | None-safe 래퍼, fallback 문자열 사용자 지정 가능 |
| `f"{d['x']:,.0f}"` | ⚠️ 미검출 | 키가 없으면 `KeyError`, 별도 이슈 |

### 2.4 탐지 범위와 제외

- **대상**: `scripts/**/*.py`, `services/**/*.py`
- **제외**: `__pycache__`, `.venv`, `venv`, `node_modules`, `.git`, 린터 본체 자기 자신
- 구문 오류(`SyntaxError`)는 WARN(SYS)로 집계, ERROR로 취급하지 않음

### 2.5 실행 방법

```bash
# 단독 실행 (개발 중 빠른 확인)
python scripts/lint_none_format.py

# WARN까지 실패로 취급 (엄격 모드)
python scripts/lint_none_format.py --warn

# WARN 출력 생략 (ERROR만 보기)
python scripts/lint_none_format.py --quiet

# 배포 전 전체 검증 (린트 내장)
python scripts/pre_deploy_check.py
```

종료 코드: `0` = 통과, `1` = 실패

### 2.6 런타임 헬퍼

`scripts/jarvis_executor.py` 에 정의, 공용 헬퍼로 이전 예정 (`services/common/ccxt_utils.py`):

```python
def _fmt_num(v, spec: str = ",.0f", fallback: str = "N/A") -> str:
    """None-safe 숫자 포매터."""
    if v is None:
        return fallback
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return fallback


def _resolve_fill(exchange, order: dict, symbol: str,
                  amount_hint: float | None = None) -> tuple[float | None, float | None]:
    """시장가 주문 체결정보(cost, price) 해석.

    우선순위:
      1) order dict의 average/price/cost
      2) fetch_order(id) 재조회 (0.4s 대기 후)
      3) ticker(last) × amount_hint 로 추정
    """
```

---

## 3. 기대효과 (Expected Effects)

### 3.1 단기 (도입 직후 이미 확인됨)

- **즉시 탐지**: 최초 스캔에서 **ERROR 10건 + WARN 28건** 발견
- **전부 수정**: ERROR 10건은 동일 패턴이므로 `(x or 0)` 패턴으로 일괄 치환
- **영향 파일**: `backtest_composite_9yr.py`, `hourly_monitor.py`, `jarvis_executor.py`, `realtime_monitor.py`, `telegram_bot.py`
- **같은 종류의 크래시가 5개 파일에 잠복해 있었다** → 사실상 5개 이상의 "미래의 사건"을 선제적으로 제거

### 3.2 중기

- **조용한 실패 차단**: 알림·로그 포매팅 크래시로 인한 "봇은 도는데 사람이 모른다" 시나리오 제거
- **복사-붙여넣기 오염 방지**: 관용구가 퍼지기 전에 린터가 막음
- **교훈의 제도화**: `docs/lessons/`의 경험이 사람의 기억에만 머물지 않고 실행 가능한 검증 규칙으로 코드화됨
- **코드 리뷰 부담 감소**: "이 `.get()` 안전한가?"를 사람이 매번 볼 필요 없음

### 3.3 장기

- **린트 친화 코드 컬처**: 린트가 허용하는 패턴이 "권장 관용구"로 자리잡음 — `(x or 0)`, `_fmt_num(x)` 등이 팀 표준
- **린트 규칙 확장 가능**: 새 lesson이 생길 때마다 새 규칙을 추가하는 것이 자연스러운 워크플로우가 됨
- **회귀 0**: 이미 겪은 버그 카테고리는 두 번 나오지 않는다 (pre_deploy_check 강제)

---

## 4. 현재 상태 (as of 2026-04-08)

| 항목 | 상태 |
|---|:---:|
| 린터 본체 (`lint_none_format.py`) | ✅ 구현 완료 |
| R1 규칙 (f-string 숫자 포매팅) | ✅ 활성 |
| R2 규칙 (format 함수) | ✅ 활성 |
| R3 규칙 (ccxt 위험 키 WARN) | ✅ 활성 |
| `pre_deploy_check.py` 통합 | ✅ 완료 |
| R1 위반 10건 수정 | ✅ 완료 |
| R3 WARN 감축 | ✅ 28 → 1 (BOM 구문 오류 1건만 잔존) |
| 공용 헬퍼 `services/common/ccxt_utils.py` | ✅ 완료 (`fmt_num`, `resolve_fill`) |
| git pre-commit hook (`.githooks/pre-commit`) | ✅ 완료 (수동 활성화 필요) |
| CI(GitHub Actions) 훅 | ⏳ 예정 |

---

## 5. 로드맵 (Roadmap)

### Phase 1 — 기반 구축 ✅ *(2026-04-08 완료)*

- [x] `lint_none_format.py` 초안 (R1, R2, R3)
- [x] `pre_deploy_check.py` 통합
- [x] R1 위반 10건 전수 수정
- [x] lessons 기록 (`20260408_4_nonetype_format_lint.md`)

### Phase 2 — 공용 헬퍼 분리 ✅ *(2026-04-08 완료)*

- [x] `services/common/` 패키지 신설
- [x] `services/common/ccxt_utils.py`
  - [x] `fmt_num()` (None-safe 포매터)
  - [x] `resolve_fill()` (시장가 체결정보 해석)
  - [ ] 단위 테스트 (`tests/common/test_ccxt_utils.py`) — 후속 작업
- [x] `scripts/jarvis_executor.py` 가 공용 헬퍼 import
- [x] **린터 규칙 개선** — R3 억제 조건 추가:
  - `BoolOp(Or)` 하위 (예: `order.get('price') or fallback`) 자동 허용
  - `SAFE_WRAPPERS` (`_fmt_num`, `fmt_num`, `resolve_fill`)의 인수 자동 허용
  - `upbit_client.py` `_parse_order` 는 문서화된 파싱 경계로 예외 처리
- [x] `multi_trader.py`, `hourly_monitor.py` 의 `.get(key, default)` 잠재 버그 3건을 `or` 패턴으로 수정
- [x] 린트 재실행 → **WARN 28 → 1** (BOM 구문 오류 1건만 잔존, 범위 외)

### Phase 3 — 자동화 강화 🟡 *(진행 중)*

- [x] **git pre-commit hook** (`.githooks/pre-commit`)
  - [x] `lint_none_format.py --quiet` 실행
  - [x] `.githooks/README.md` — 활성화 안내 (`git config core.hooksPath .githooks`)
  - [ ] `pre_deploy_check.py`의 빠른 하위집합 실행 (후속)
- [ ] **CI 통합** (GitHub Actions 또는 로컬 CI)
  - [ ] PR 시 린트 자동 실행
  - [ ] 실패 시 머지 차단
- [ ] **배포 스크립트**(`deploy_to_aws.sh`)가 `pre_deploy_check` 호출 여부 검증
      (이미 호출하지만 문서화 필요)

### Phase 4 — 규칙 확장 (경험 기반 점증)

교훈이 쌓이는 대로 규칙 추가. 현재 후보:

- [x] **R4** — `dict[key]` subscript 숫자 포매팅 시 KeyError 방어 확인 (WARN, 04-10 구현)
- [x] **R5** — `datetime.strptime()` 인자에 dict subscript/.get() 직접 전달 시 WARN (04-10 구현)
      (업비트 API의 일부 필드가 빈 문자열로 오는 케이스 방어)
- [ ] **R6** — ccxt `fetch_*` 결과를 await 없이 바로 사용하는 실수 탐지 (async 경로)
- [ ] **R7** — 상태 파일(`*_state.json`) 진입 경로에서 `fetch_balance()`
      교차검증 누락 탐지 (lessons/20260408_2 연계)
- [ ] **R8** — 시장가 주문 직후 `sleep` 없이 `fetch_order` 호출 방지
      (업비트는 반영 지연이 있음)

### Phase 5 — 메타 린트 ("린트의 린트")

- [ ] `docs/lessons/*.md` 에 **검증 규칙** 섹션이 있는지 검사
- [ ] **검증 규칙이 있는데 린터/pre_deploy_check에 대응 코드가 없는** lesson 탐지
- [ ] "lessons ↔ 린트 규칙" 매핑 표를 `docs/lint_layer.md` 에 자동 생성
- [ ] 빠진 lesson은 경고로 표시 → **"교훈이 코드로 집행되지 않은 잔여 위험"** 가시화

### Phase 6 (스트레치) — 패턴 데이터베이스

- [ ] 위반 탐지 이력을 `workspace/lint_history.jsonl` 에 누적
- [ ] 자주 위반되는 패턴 상위 N개를 주간 보고에 포함
- [ ] 린트 규칙의 효과성 측정 (탐지 건수 추이, 수정 속도 등)

---

## 6. 참고 문서

- **발단 사건**: [lessons/20260408_4_nonetype_format_lint.md](lessons/20260408_4_nonetype_format_lint.md)
- **시행착오 관리 정책**: [CLAUDE.md 시행착오 관리](../CLAUDE.md#시행착오-관리)
- **관련 lessons**:
  - [20260408_1 jarvis cron 미등록](lessons/20260408_1_jarvis_cron_missing.md) — 자동화 누락
  - [20260408_2 state ↔ balance 불일치](lessons/20260408_2_state_balance_mismatch.md) — 정합성
  - [20260408_3 CB 기존 포지션 정책](lessons/20260408_3_cb_existing_positions_policy.md) — 리스크 정책

---

## 7. 기여 가이드 (Contributing)

새 린트 규칙 추가 시:

1. **lesson 기록 선행** — 실제 겪은 사건을 `docs/lessons/YYYYMMDD_N_*.md`에 먼저 기록
2. **최소 재현 케이스 확보** — AST 레벨에서 어떻게 탐지할지 분명히
3. **오탐/미탐 분석** — 허용 패턴 명시 (예: R1은 `(x or 0)` 를 허용)
4. `scripts/lint_none_format.py` 에 규칙 추가 (ID는 R1, R2, ... 순차)
5. 본 문서의 "린트 규칙" 표와 "로드맵 Phase 4"를 갱신
6. 기존 코드에서 탐지된 모든 위반을 **동일 커밋에서** 수정 (린트 도입 시 회귀 금지)
