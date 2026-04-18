# 블록 3 — VB 개선 DRY-RUN 7일치 재검증 (P5-28b)

- **작성일(KST)**: 2026-04-17 09:30
- **작성자/세션**: pdca-pm (자비스)
- **예상 소요**: 1시간
- **관련 이슈/결정문서**: docs/decisions/20260405_1_bata_strategy_upgrade.md, docs/lessons/20260404_2 (VB rotation duplicate)

## 1. 목표

04-10~04-16(7일) VB DRY-RUN 로그를 누적 재집계하여 승률·평균수익·MDD·거래 수를 리포트. 이전 04-06 DRY-RUN(04-06 결정문서의 NO-GO 판단) 대비 개선 효과가 유의미한지 판정.

## 2. 성공기준 (Acceptance Criteria)

- [ ] AC-1: `workspace/reports/20260417_vb_drymake_7day_recheck.md` 생성
- [ ] AC-2: 리포트에 최소 포함 — 기간, 전체 거래 수, 승률, 평균 수익률, 누적 수익률, MDD, 거래별 표
- [ ] AC-3: 데이터 소스 경로 명시 (vb_state.json, logs/vb_rotation.log 또는 journalctl 필요 시)
- [ ] AC-4: 의사결정(Recommend GO / CONDITIONAL / NO-GO) 포함
- [ ] AC-5: 교차검증 — qa가 숫자 재검산 혹은 "데이터 누락 여부" 확인

## 3. 단계

1. VB 상태/로그 파일 식별 — `workspace/vb_state.json`, `workspace/reports/`, `logs/` 탐색
2. 04-10~04-16 기간 거래 레코드 추출
3. 집계: 승률, 평균 수익, MDD
4. 리포트 작성
5. 결정 권고 (GO/CONDITIONAL/NO-GO)
6. 교차검증

## 4. 리스크

| 리스크 | 완화 |
|--------|------|
| 로컬에 서버 journalctl 로그 없음 | workspace/vb_state.json 및 기존 리포트 기반으로 재집계. 데이터 부족 시 리포트에 한계 명시 |
| DRY-RUN이므로 실제 체결 없음 — 슬리피지 미반영 | 리포트에 가정 명시 |

## 5. 검증 주체

- [x] 옵션 B — pdca-qa (재집계 확인)
- [ ] 옵션 C — 자동 스크립트 없음 (수치 검증은 qa가 샘플링)

## 6. 회고

- (미완)
