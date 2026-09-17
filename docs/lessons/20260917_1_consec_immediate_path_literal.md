# 교훈 #51 — 같은 안전장치의 두 경로가 다른 임계값을 썼다: "옳은 분기가 있는가"는 "틀린 분기가 또 있는가"를 묻지 않는다

- **일자**: 2026-09-17
- **사고 유형**: 사양 불일치 (오래된 리터럴 잔존)
- **관련**: [ADR 20260917-1](../decisions/20260917_1_consec3_defect_sample_retained.md) · [ADR 20260829-1](../decisions/20260829_1_consec_cooldown_release.md) ·
  lessons #3(즉시 체크) #19(상수 자체정의) #38(경로 A/B 불일치) #48(이름으로 묻는 룰) #49(AST 배치)
- **실행계획**: `workspace/plans/20260917_1_consec_loss_immediate_path_fix.md`

## 무슨 일이 있었나

연패 쿨다운은 두 곳에서 부과된다.

| 경로 | 위치 | 임계값 | 부과 지문 `cooldown_imposed_for` |
| --- | --- | --- | --- |
| 주기 (4h 보고) | `_send_periodic_report` | `CONSEC_LOSS_LIMIT` (=5) | 기록 O |
| **매도 즉시** (교훈 #3) | `_execute_sell` 말미 | **`>= 3`, `3 * 24 * 3600` 리터럴** | **기록 X** |

2026-04 초기 코드의 리터럴이 08-29 상수화(`CONSEC_LOSS_LIMIT`) 때 **주기 경로에만** 반영됐다.
즉시 경로는 매도 체결마다 먼저 실행되므로 `cooldown_until` 을 항상 선점한다 → **실효 사양은 3연패였다.**

CLAUDE.md·config 는 "5연패 72h" 라고 적고 있었다. 검증 창(09-02~) 내 발동은 **3회**
(closed_trades 재생으로 복원 — journal 은 09-11 부터만 남아 로그로는 1회만 보였다):

| 발동 | 사양(5연패)이었다면 |
| --- | --- |
| 09-01 13:15 → 09-04 13:15 | 발동 없음 — 부당 차단 |
| 09-10 00:16 → 09-13 00:16 | 14h 늦게 발동 — 부당 차단 14h + 부당 개방 14h(그 사이 진입 7건) |
| 09-14 00:34 → 09-17 00:34 | 18분 늦게 발동 |

⚠ 진단 세션은 처음에 **로그에 보이는 09-14 1건만으로** "영향 18분" 이라 보고했고, 사용자는 그 근거로
표본 유지(B)를 골랐다. **로그 보존 기간 밖은 "없음"이 아니라 "안 보임"이다** — 결정적 상태(closed_trades)로 재생해야 한다.

## 동반 결함 2건

1. **지문 미기록 → 이중 부과.** 즉시 경로가 `cooldown_imposed_for` 를 남기지 않으므로, 만료 시점에
   연패가 5 이상이면 주기 경로는 ADR 20260829-1 의 (a)"처음 걸린 연패" 로 판정해 **72h 를 재부과**한다.
   `test_without_fingerprint_periodic_reimposes` 가 실제 메서드로 재현한다. (09-17 만료 시엔 연패 4라 미발생)
2. **해제 경로 크래시.** 주기 경로의 "자동 연장" 알림 `await send(...)` 가 부과 `else:` **밖**에 있어,
   해제(`_served`) 직후에도 실행되며 `cooldown_target` 미정의 `UnboundLocalError` 를 냈다
   (lessons #49 와 같은 배치 결함 — 그때 해제 분기만 옮기고 알림은 남았다). `_check_periodic_tasks` 의
   `except` 가 삼켜 로그 한 줄로 끝났고, 해제 자체는 state 에 저장된 뒤라 무증상에 가까웠다.
   **새 테스트를 짜자마자 이것이 먼저 터졌다** — 로직 복제가 아니라 실제 메서드를 호출한 덕이다.

## 왜 못 잡았나

`check_consec_loss_no_running_false` 는 `if consec >= (5|CONSEC_LOSS_LIMIT)` **가 존재하는지** 를 찾는다.
옳은 분기를 찾으면 끝나므로, **같은 판단을 하는 다른 분기가 틀린 값을 쓰는지** 는 질문에 없다.
메타 교훈 표 "정규식은 무엇이 있는가만" 의 8번째 사례다. 존재 검사는 **전수 검사가 아니다.**

## 수정

- 즉시 체크를 `_check_consec_loss_immediate()` 로 추출 — `CONSEC_LOSS_LIMIT`/`CONSEC_LOSS_COOLDOWN_HOURS`
  + `consec_loss_alerted_until`(lessons #30) + `cooldown_imposed_for`(ADR 20260829-1) 를 주기 경로와 동일하게 기록.
- 주기 경로 연장 알림을 부과 분기 안으로 이동.
- `tests/execution/test_consec_loss_paths.py` — 실제 `RealtimeMonitor` 메서드 호출 6건.

## 검증규칙 (pre_deploy_check)

`check_consec_loss_paths_aligned()` — AST 로 **전수** 검사:
(a) `consec_loss`/`consec` 변수·속성·호출과 정수 리터럴(≥2)의 비교 금지,
(b) `self.state["cooldown_until"] = <비0>` 를 하는 **모든 함수**는 `cooldown_imposed_for` 도 대입,
(c) 그 대입값의 산출 경로(지역변수 역추적)에 `CONSEC_LOSS_COOLDOWN_HOURS` 존재.

역방향 5/5: 수정 전 HEAD 파일(3건 적발) / `< 5` / 지문 줄 삭제 / `72 * 3600` / 호출식 `>= 3`.
⚠ 초안 (c) 는 **함수 안 아무 데나** 상수 이름이 있으면 통과해 `72 * 3600` 변이를 놓쳤다 —
알림 f-string 의 `{CONSEC_LOSS_COOLDOWN_HOURS}h` 가 답이 됐다(lessons #48 재발, 9회째). 대입값 역추적으로 교체.

## 교훈

- **안전장치 하나 = 부과 경로 전부.** 상수화·지문 도입 같은 변경은 `grep "cooldown_until\"\] ="` 로 **쓰는 곳 전수**에 적용한다(lessons #26 의 진입점 전수 열거를 상태 쓰기에도).
- **룰은 "옳은 것이 있다"가 아니라 "틀린 것이 없다"로 쓴다.** 전자는 첫 매치에서 멈춘다.
- **테스트는 실제 메서드를 부른다.** 복제 시뮬레이션이었다면 동반 결함 2(크래시)는 영원히 안 보였다.

## 부수 발견 (미수정, 기록만)

- `hotfix_deploy.sh` 의 cron 확인 단계는 lessons #44 이후 systemd timer 로 이전된 작업을 crontab 에서 찾아
  **항상 MISSING 6건** 을 출력한다(timer 9개 정상). 오경보가 상시화되면 진짜 경보도 무시된다.
- `hotfix_deploy.sh` 가 `python` 을 호출 — 로컬은 WindowsApps 스텁이라 PATH shim(`python → py`) 필요.
- 서버에 `scripts/ml_shadow_count.py` 미배포(09-09) → 서버에서 pre_deploy_check 실행 시 `[ML-count]` 오류.
