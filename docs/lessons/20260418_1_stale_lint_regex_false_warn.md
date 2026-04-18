# 20260418_1 — pre_deploy_check 정규식이 코드 현실과 어긋나면 false WARN이 누적되어 실제 문제를 묻어버린다

## 발생 일시

- 발견: 2026-04-18 (2차 스윕 통합 단계)
- 실제 누적 시작: 정확히 알 수 없음 (검증 함수가 작성된 시점부터)

## 배경

lessons/20260329_3의 검증규칙을 코드로 집행하기 위해 `scripts/pre_deploy_check.py`의 `check_post_fill_safety_check`가 `trader.py`에서 아래 정규식을 찾도록 작성되어 있었다.

```python
loss_patterns = [
    r"consecutive_losses",
    r"_check_loss_streak",
    r"emergency_stop",
    r"max_consecutive",
]
```

그런데 실제 프로덕션 구현(realtime_monitor.py)은 아래 **다른 식별자**를 사용한다.

```python
# services/execution/realtime_monitor.py:51-53
from services.execution.vb_filters import (
    recent_consecutive_losses,   # ← 이게 실제 이름
    is_in_loss_cooldown,
    set_loss_cooldown,
)
# line 527
if consec >= 5:
    await notify_error("🛑 *5연패 자동 중단*\n...")
```

즉 **기능은 이미 완성되어 동작 중**이었음에도, 정규식이 `recent_consecutive_losses`(접두사 `recent_` 붙음)와 `연패 자동 중단`(한국어)을 매칭하지 못해 매 배포마다 같은 경고 2건이 찍혔다. 또한 config.py도 `MAX_CONSECUTIVE_LOSSES`가 아닌 `MAX_CONSECUTIVE_ERRORS`와 `VB_LOSS_COOLDOWN_HOURS`를 사용 중이라 역시 "미발견" 오탐지.

## 영향

- **진짜 문제를 묻어버림**: 매 배포마다 "연패 체크 로직 미발견"이라는 경고가 나옴 → 개발자가 "이건 원래 나오는 경고"라고 간주하게 됨 → 실제로 진짜 미구현이 추가되어도 같은 경고 속에 섞여 보이지 않게 됨(boy who cried wolf).
- **lint_meta 역효과**: `20260329_3`이 pre_deploy_check에 매핑되어 있다고 판단하지만 실제로는 검증이 실패 상태. "매핑 ✅"라는 결과가 현실과 어긋남.
- **시간 낭비**: CTO gate 때마다 "경고 2건은 기존 부채"라고 설명/무시 과정이 반복됨(오늘 2회 반복).

## 원인

1. lesson 작성 시점의 설계 이름(`_check_loss_streak`)과 실제 구현 이름(`recent_consecutive_losses`)이 다름
2. 검증 함수를 한 번 작성한 뒤 **실제 코드 변경과 함께 정규식을 갱신하지 않음**
3. 실제 식별자는 `grep`으로 쉽게 찾을 수 있는데도, pre_deploy_check의 loss_patterns는 lesson 초기 설계를 복붙한 채 방치됨

## 수정

`scripts/pre_deploy_check.py:check_post_fill_safety_check`:

1. 탐색 대상 파일을 `trader.py`에서 `trader.py + realtime_monitor.py`로 확대 (체결 경로는 두 파일 중 하나에 있으면 OK)
2. loss_patterns에 실제 식별자 + 한국어 주석 패턴 추가:
   ```python
   r"recent_consecutive_losses",
   r"is_in_loss_cooldown",
   r"set_loss_cooldown",
   r"연패\s*자동\s*중단",
   r"연패\s*쿨다운",
   ```
3. config 상수 패턴을 `MAX_CONSECUTIVE_ERRORS`, `VB_LOSS_COOLDOWN`, `LOSS_COOLDOWN_HOURS`로 확장 (실제 프로젝트 상수명과 일치)

결과: pre_deploy_check **경고 2건 → 0건**, 완전 GREEN.

## 검증규칙

- **R-meta-1**: pre_deploy_check의 모든 `check_*` 함수가 참조하는 정규식/식별자는 **실제 프로덕션 코드 `grep` 결과로 교차검증**해야 한다. lesson 작성 당시 설계 이름을 복붙하지 말 것.
- **R-meta-2**: "경고 2건은 기존 부채"가 2회 이상 반복되면 "정규식 stale 가능성"을 먼저 의심한다. (실제 미구현이면 구현을 우선, 정규식 stale이면 즉시 수정)
- **R-meta-3**: 새 lesson 작성 시 검증규칙 섹션에 **실제 식별자**(함수명/상수명)를 명시적으로 기재해 pre_deploy_check가 그 이름으로 찾을 수 있게 한다.

## 교훈 요약

> 검증 스크립트도 코드다. 코드가 바뀌면 검증도 따라 바뀌어야 하고, 바뀌지 않은 검증은 **"있다는 착각"**만 주는 함정이 된다. 반복되는 false WARN은 노이즈가 아니라 신호다 — 검증 코드 자체를 의심해야 한다.

## 연관 lesson/문서

- [20260329_3_auto_stop_delay.md](20260329_3_auto_stop_delay.md) — 원 검증규칙
- [20260408_4_nonetype_format_lint.md](20260408_4_nonetype_format_lint.md) — 린트 규칙 자체가 집행되는지 원칙
- [20260418_team_full_sweep.md](../../workspace/plans/20260418_team_full_sweep.md) — 오늘 1차 스윕
- [20260418_phase5_6_full_sweep.md](../../workspace/plans/20260418_phase5_6_full_sweep.md) — 2차 스윕 (여기서 발견·수정)
