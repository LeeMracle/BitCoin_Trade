# 미커밋 lessons/plans 정리 커밋

- **작성일(KST)**: 2026-08-01 18:40
- **작성자/세션**: bata-pm (사용자 지시)
- **예상 소요**: 30~60분
- **관련 이슈/결정문서**: 오늘(2026-08-01) P1 close 후 미커밋 방치 확인

## 1. 목표

로컬에 방치된 lessons #33~#36 산출물 및 신규 plans/decisions/ssh_access 문서를 lessons별로 분리 순차 커밋. 오늘 P1 #36 산출물이 로컬 소실 리스크에 노출되지 않도록 최우선 저장.

## 2. 성공기준 (Acceptance Criteria)

- [ ] 오늘 P1 #36 산출물 4개(신규 lessons md + `CLAUDE.md` 라인 #36-08 + `deploy_to_aws.sh` 사후 실측 게이트 + `pre_deploy_check.py` `check_deploy_post_check_remote_cron`) 단일 커밋으로 저장
- [ ] 이전 lessons(#33/#34/#35/#38) 등재분 각각 lessons별 커밋 (`CLAUDE.md` 등재 + 관련 룰 hunk + 신규 md 파일 묶음)
- [ ] ADR 20260607-1 (MIN_VOLUME_KRW 5억 환원) 관련 커밋 (decisions md + config 반영분)
- [ ] `docs/ssh_access.md` 신규 문서 커밋 (lessons #35 짝)
- [ ] `git status` 결과 — output/ 및 임시 py 파일 제외 정리 완료
- [ ] 각 커밋 hash + 한 줄 요약을 회고에 기록

## 3. 단계

1. `git status` + `git diff` 재확인 (전날 이후 추가 변경 없는지)
2. 파일별 hunk 그룹화 계획 수립 (lessons 번호 → hunks 매핑 표 작성)
3. 오늘 #36 커밋 먼저 (`git add -p` hunk 선택) — **최우선**
4. 이전 lessons 순차 커밋 (#33 → #34 → #35 → #38 → ADR 20260607-1 순)
5. `docs/ssh_access.md` 커밋
6. 잔여 output/ · 임시 파일은 별도 판단 (커밋 대상 여부 사용자 확인)
7. 푸시는 사용자 별도 승인 대기

## 4. 리스크 & 사전 확인사항

- `git add -p` hunk 선택 실수 시 반쪽 상태 커밋 위험 — 각 커밋 후 `git show --stat` 검증
- `pre_deploy_check.py` +466줄이 여러 lessons에 걸쳐 있어 hunk 분리 난이도 최상 — 필요시 함수 단위 커밋으로 완화
- 참조: 오늘 세션 대화 컨텍스트 (bata-pm agentId a26d8a66d3397667b — 소실 가능)
- 로컬 PC 장애 시 오늘 #36 산출물 유실 → **다음 세션 첫 작업으로 강제**

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [ ] 옵션 A — 별도 세션 (본 plan이 그 별도 세션 자체)
- [x] 옵션 C — 자동 검증: 각 커밋 후 `git show <hash> --stat` + `pre_deploy_check.py` 통과 확인

## 6. 회고 (작업 종료 후 작성)

- **결과**:
- **원인 귀속**:
- **한 줄 회고**:
- **후속 조치**:
