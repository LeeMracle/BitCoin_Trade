# 연패 쿨다운 매도즉시 경로 하드코딩(3연패) 수정

- **작성일(KST)**: 2026-09-17 12:30
- **작성자/세션**: 세션 20260917 (사용자 "매매현황 진단" → 결함 적발 → "b로")
- **예상 소요**: 90분
- **관련 이슈/결정문서**: [ADR 20260917-1](../../docs/decisions/20260917_1_consec3_defect_sample_retained.md) · ADR 20260829-1 · lessons #3 #19 #38 #49

## 1. 목표

`realtime_monitor._execute_sell` 말미 연패 즉시 체크가 `consec_loss >= 3` / `3 * 24 * 3600` 리터럴을 써서
사양(`CONSEC_LOSS_LIMIT=5`)보다 이르게 72h 매수 중단을 건다(09-14 실발동). 또한 이 경로는
`cooldown_imposed_for` 를 기록하지 않아, 만료 후 주기 경로(5연패 분기)가 **같은 연패에 72h 를 재부과**한다.
→ 두 경로를 같은 상수·같은 부과 기록으로 정렬한다. 표본 처리는 ADR 20260917-1 에서 재결정 대기(최초 B 선택의 근거가 불완전했음).

## 2. 성공기준 (Acceptance Criteria)

- [x] 즉시 경로가 `CONSEC_LOSS_LIMIT` / `CONSEC_LOSS_COOLDOWN_HOURS` 를 사용 (리터럴 0)
- [x] 즉시 경로 부과 시 `cooldown_imposed_for` + `consec_loss_alerted_until` 기록 → 만료 후 주기 경로가 **해제**(재부과 아님)
- [x] **실제 코드**를 호출하는 테스트: 4연패 미발동 / 5연패 발동 / 만료 후 주기경로 해제 / 복제 로직 없음 (lessons #49)
- [x] `pre_deploy_check` 룰: realtime_monitor 내 `consec*` 비교에 정수 리터럴 금지 (AST) + 즉시경로 `cooldown_imposed_for` 기록 — **역방향 테스트**(수정 전 파일로 FAIL)
- [x] hotfix 배포 후 서버 md5 일치 + 서비스 active + 서버 상태에서 `consec=4, cooldown 없음` 확인
- [ ] (진행중 — ADR 재결정 대기) lessons #51 · INDEX · CLAUDE.md 인덱스 · ADR(표본 유지 근거) · ROADMAP 갱신

## 3. 단계

1. 즉시 체크를 `_check_consec_loss_immediate()` 메서드로 추출 (테스트 가능하게) + 상수/부과기록 정렬
2. 테스트 `tests/execution/test_consec_loss_paths.py`
3. 룰 `check_consec_loss_no_literal_threshold()` + 역방향
4. `hotfix_deploy.sh` → 서버 실측
5. 문서

## 4. 리스크

- 현재 라이브 consec=4 — 배포 전 손실 1건이면 구 코드로 72h+재부과. 배포를 우선한다.
- 재시작: state 영속(포지션 2) — 재시작 후 포지션·구독 확인.

## 5. 교차검증

자동 검증 스크립트(pytest + pre_deploy_check 역방향). 구현 세션은 PASS 판정하지 않고 "확인 N / 이슈 M" 로 보고.

## 6. 진행 기록

- 테스트 6/6, 전체 132 passed, 룰 역방향 5/5, 서버 md5 d0c6f17d 일치, 재시작 후 구독 144 · 포지션 2 · consec 4 · cooldown 없음
- 로컬 `.env` 부재는 정상(08-25 인수인계 §6) — 임시로 만든 키이름 .env 는 삭제, 서버에서 pre_deploy_check 재실행(신규 룰 통과, 기존 `[ML-count]` 서버 미배포 1건)
- ADR 근거 정정: 발동 3회 복원(09-01, 09-10, 09-14) → 표본 처리 재결정 요청
