# Harness Step 6 Gate 사용 가이드

하네스 개선안 ③(운영 메트릭 + 훅 자동화) 진입 여부를 자동 판정한다.

## 왜 게이트가 필요한가

개선안 ③은 훅 오작동 시 실거래 자동화(`daily_live.py` 등)에 영향 가능한 복합 리스크를 갖고 있다. "1~2주 안정 운영 후 진입"의 구체 기준(11개 조건)을 사람이 수동 점검하면 자기평가 편향에 빠진다(Step 5 교차검증 정책 위반). 따라서 **파일 실측 기반 자동 게이트**로 강제한다.

근거: `docs/decisions/20260409_1_harness_step6_auto_gate.md`

## 사용법

```bash
# 대화형 실행 (수동 항목 Y/N 입력)
python scripts/harness_step6_gate.py

# 수동 항목 전부 y로 간주 (비대화 환경용)
python scripts/harness_step6_gate.py --yes
```

종료 코드:
- `0` → GO (Step 6 진입 승인)
- `1` → NO-GO (추가 관찰 필요)

## 11개 조건

### A. Execution Plan 안정화 (4)

| # | 조건 | 판정 방식 |
|---|------|-----------|
| A1 | `workspace/plans/` 파일 ≥3건 | 자동 (파일 카운트) |
| A2 | 성공기준 사전 작성 ≥80% | 자동 (§2 체크박스 존재 비율) |
| A3 | 회고 ≥1건 + 원인 귀속 기록 | 자동 (§6 "결과"+"원인 귀속" 채워짐) |
| A4 | 비자명 기준이 판정 가능했는가 | 수동 |

### B. 교차검증 작동 (4)

| # | 조건 | 판정 방식 |
|---|------|-----------|
| B1 | 검증 주체 기록 ≥2건 | 자동 (§5 "검증 주체: A/B/C/D") |
| B2 | 이슈 발견 ≥1건 | 자동 (§5 "발견 이슈: M개" M≥1) |
| B3 | "이슈 0건 PASS" 비율 100% 아님 | 자동 (B2 통계 기반) |
| B4 | `pre_deploy_check.py` 실행 ≥1회 | 자동 (git log + pyc 캐시) |

### C. 운영 안정성 (3)

| # | 조건 | 판정 방식 |
|---|------|-----------|
| C1 | Step 4 이후 신규 사고(하네스 기인) 0건 | 자동 (lessons 파일 grep) |
| C2 | 사문화 없음 | 수동 |
| C3 | CLAUDE.md 규칙 로드 체감 | 수동 |

## 판정 규칙

- **≥9/11**: GO — Step 6 진입 승인
- **<9/11**: NO-GO — 1주 더 관찰 후 재실행

## 리포트

`workspace/gate_reports/YYYYMMDD_HHMM_gate.md`에 자동 생성. 재판정 시 이전 리포트를 덮어쓰지 않고 새 파일로 누적.

## 재실행 주기

- 최초 권장 실행: Step 5 도입 후 **1주 뒤** (2026-04-16 전후)
- NO-GO 시: 매 1주마다 재실행
- GO 판정 받으면 즉시 Step 6 착수 가능

## 한계

- A4·C2·C3은 여전히 주관. 전부 `y`로만 답하는 경향이 관찰되면 정책 재설계 필요
- 자동 판정 8개는 파일 포맷(`_TEMPLATE.md`) 준수가 전제. 템플릿 변경 시 스크립트 정규식 점검 필요

## 관련 문서

- `output/claude_code_harness_guide.md` — 기준 가이드
- `output/diagnosis_bitcoin_trade.md` — 진단
- `output/improvement_risk_benefit.md` — 리스크·효과 분석
- `docs/cross_review_policy.md` — 교차검증 정책
- `docs/decisions/20260409_1_harness_step6_auto_gate.md` — 게이트 결정문
