# G6 패치 — 배포 검증 우회 차단

- **작성일(KST)**: 2026-05-24 17:00
- **작성자/세션**: pm (Claude Opus 4.7), v0.5 갭 분석 후속
- **예상 소요**: 1시간
- **관련 문서**:
  - `output/team_v0.5_gap_review.md` (갭 분석)
  - `docs/lessons/20260520_1_cron_registration_missing.md` (#31)

## 1. 목표

`deploy_to_aws.sh`는 이미 `pre_deploy_check.py`를 호출하지만, **직접 scp+restart로 우회**하면 검증 누락 — lessons #31 재발 위험. 우회 경로에도 최소 검증 의무를 강제한다.

## 2. 성공기준 (Acceptance Criteria)

- [x] `pre_deploy_check.py` 신규 함수 `check_cron_var_echo_consistency()` 추가 → 활성 `CRON_xxx` 변수 정의가 `echo "$CRON_xxx"` 라인에 모두 등록되어 있는지 검증
- [x] `scripts/hotfix_deploy.sh` 신규 — scp+restart 우회 시에도 (a) 사전 `pre_deploy_check` 강제, (b) 사후 헬스 강제, (c) 24h lessons 보강 경고 출력
- [x] `pre_deploy_check.py` main()에 신규 함수 등록
- [x] `docs/lessons/20260524_1_g6_deploy_guard.md` 작성
- [ ] 신규 검증 함수 로컬 실행 통과 (별도 세션/사용자 검증 — 자기평가 금지)

## 3. 단계

1. `pre_deploy_check.py`에 `check_cron_var_echo_consistency()` 추가
   - `deploy_to_aws.sh`에서 `^CRON_(\w+)=` 매칭 → 활성 변수 추출 (주석 제외)
   - `echo "\$CRON_\w+"` 매칭 → echo 등록 변수 추출
   - 차집합 발생 시 errors 추가
2. main()에 함수 등록
3. `scripts/hotfix_deploy.sh` 신규 작성:
   - 인자: `<파일1> [파일2 ...]`
   - 단계: pre_deploy_check → scp → systemctl restart → 30초 대기 → cron 등록 상태 ssh 조회 → 결과 출력
4. lessons #32 (이번 G6 패치) 작성

## 4. 리스크 & 사전 확인사항

- 리스크: `check_cron_var_echo_consistency()`가 주석 처리된 `# CRON_DIGEST` 같은 비활성 변수를 false positive로 잡을 수 있음 → 주석 라인 제외 정규식 필수
- 리스크: `hotfix_deploy.sh`가 부분 동작(예: 일부 파일만 변경) 시 cron은 검증해야 하나 cron 갱신은 안 함 → 출력에 "운영 파라미터 변경이면 deploy_to_aws.sh 전체 실행 권장" 경고 포함
- 사전 확인: `deploy_to_aws.sh` 142~163줄 cron echo 블록 구조 (이미 읽음)

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [x] 옵션 C — 자동 검증 스크립트: `scripts/pre_deploy_check.py` 로컬 실행 (구현 후 사용자 또는 별도 세션이 PASS 확인)
- [ ] 옵션 A — 별도 세션 검토 권고

**검증 기록 형식 (사용자/별도 세션 작성)**
```
검증 주체: (작성)
확인 항목: 5개 (§2 체크박스 4개 + pre_deploy_check 로컬 PASS)
발견 이슈: (작성)
판정: PASS / FAIL / 조건부 PASS
```

> 구현 세션은 자기평가 안 함. 마지막 체크박스(로컬 PASS 검증)는 사용자 또는 별도 세션 몫.

## 6. 회고 (작업 종료 후 작성)

- **결과**: (사용자 검증 후)
- **원인 귀속**: (해당 시)
- **한 줄 회고**: (작성 예정)
- **후속 조치**: G1(hotfix 워크플로우 정식화) → G3(알람 카탈로그)
