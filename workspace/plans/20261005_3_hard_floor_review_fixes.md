# 손절 -6% 교차검증 지적 4건 수정

- **작성일(KST)**: 2026-10-05 12:30
- **작성자/세션**: 에이전트(Claude) + 사용자 지시("1~4번 다 수정해줘")
- **예상 소요**: 1.5시간
- **관련 이슈/결정문서**: [plan 20261005_1](20261005_1_hard_stop6_grandfather_deploy.md) · 독립 리뷰(서브에이전트, 조건부 PASS, 이슈 4건) · ADR 20260930-1 · lessons #19, #45, #49, #51

## 1. 목표

독립 교차검증이 지적한 4건을 고친다.
1. `fix_entry_price_from_fills.py` 가 현행 6% 캡을 **기존 포지션에 소급** 적용(Medium)
2. `check_hard_floor_grandfathered` 가 정규식이라 **우회 가능** + 역방향 테스트가 저장소에 없음(Medium)
3. `fix_state_balance_mismatch.py` 복원 포지션에 `hard_stop_pct` 없음, stop 6% / 바닥 10% 비일관(Low)
4. 낡은 주석, `hard_stop_pct` 가 0/문자열일 때의 방어 부재(Low)

## 2. 성공기준 (Acceptance Criteria)

- [ ] 캡 계산이 **공용 모듈 1곳**(`services/execution/hard_stop.py`)에만 있고, realtime_monitor·수동 스크립트가 같은 함수를 쓴다
- [ ] `pos_hard_cap`: 필드 없음/None/0/음수/1 이상/NaN/문자열 → LEGACY(0.10), 정상 float → 그 값
- [ ] fix_entry_price: 레거시 포지션(필드 없음) 보정 시 새 `trail_stop` 이 `real_price*0.90` (6% 아님), 신규 포지션은 그 포지션 캡
- [ ] fix_state_balance_mismatch: 복원 포지션이 `hard_stop_pct=LEGACY` 를 기록하고 stop 도 같은 캡
- [ ] 룰이 **AST** 로 `HARD_STOP_LOSS_PCT` 의 모든 참조(Name/Attribute/import alias)가 `realtime_monitor._execute_buy` 밖에 없음을 묻는다 — 별칭·`.get()`·줄바꿈·docstring 우회가 모두 잡힌다
- [ ] 역방향 테스트가 **저장소에 파일로 존재**(`tests/execution/test_hard_floor_grandfather.py`)하고 `pytest` 로 재현된다
- [ ] 기존 테스트 전부 통과, 전체 `pre_deploy_check` 새 오류 0 (로컬 `.env` 1건 제외)
- [ ] 서버 반영(파일만, **봇 재시작 없음** — 서비스가 import 하는 `realtime_monitor` 변경은 다음 재시작에 로드) · 서버 게이트 통과

## 3. 단계

1. `hard_stop.py` 신설 → realtime_monitor 교체(함수 이동, 낡은 주석 수정)
2. 수동 스크립트 2개 수정
3. 룰을 AST 로 재작성
4. 테스트 파일 작성 (실제 함수 호출 + 변이 소스로 룰 역방향)
5. 로컬 pytest + pre_deploy_check
6. 서버 배포(LF, 재시작 없음) → 서버 게이트 → md5
7. 문서(ADR 정정 절, plan 1 후속) · 커밋

## 4. 리스크 & 사전 확인사항

- **재시작 시점 주의**: realtime_monitor 가 `hard_stop` 모듈을 import 하므로, 서버에 monitor 만 먼저 올리고 hard_stop.py 를 빠뜨린 채 재시작하면 기동 실패. 두 파일을 함께 올리고, 올린 뒤 `py_compile` + import 시험(재시작 없이)으로 확인
- 실행 중 프로세스는 구 코드를 메모리에 유지 → 이번 배포는 라이브 동작을 즉시 바꾸지 않는다. 다음 재시작 때 신 코드가 로드되므로 **import 실패 가능성을 지금 미리 시험**해야 한다
- 복원 포지션 캡 선택: LEGACY 유지(넓은 쪽, 예기치 않은 청산 방지). 신규 진입으로 확정된 포지션만 6%

## 5. 검증 주체 (교차검증)

- [x] 옵션 C — pytest + `pre_deploy_check`(서버 실행)
- [ ] 옵션 B — 이번 수정분 재리뷰는 미실시(요청 시)

```
검증 주체: C (pytest + pre_deploy_check 서버 실행) · 재리뷰(옵션 B)는 미실시
확인 항목: 8개 모두 충족 (공용 모듈 / 캡 폴백 표 / fix_entry 레거시 0.90 / fix_state 일관 / AST 룰 / 테스트 파일 37개 / 전체 pytest 169 + 새 오류 0 / 서버 반영·게이트)
발견 이슈: 0건 (진행 중 확인: 디스크에 위반을 심으면 룰이 잡고, 원복 후 통과)
판정: PASS — 단, 수정분은 같은 세션이 검증했으므로 독립 재리뷰 권장
```

## 6. 회고 (작업 종료 후 작성)

- **결과**: PASS — 서버 반영(무재시작), 다음 재시작 때 신 코드 로드
- **원인 귀속**: 실행 결함(룰을 정규식으로 쓰고 역방향 테스트를 저장하지 않음 — 메타교훈 #45/#49/#51 의 반복)
- **한 줄 회고**: 룰은 '옳은 식의 존재'가 아니라 '틀린 참조의 부재'를 AST 로 물어야 하고, 역방향 테스트는 말이 아니라 파일로 남겨야 한다.
- **후속 조치**: 서버 실행 프로세스는 아직 구 코드(`_pos_hard_floor` 인라인) — 다음 재시작 때 신 코드. 동작 동일이므로 급하지 않음 · hotfix_deploy.sh 정식화(ROADMAP ③④)
