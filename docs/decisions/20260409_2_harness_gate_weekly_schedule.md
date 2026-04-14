# 결정: Harness Step 6 Gate 주간 자동 실행 스케줄 등록

- **일자(KST)**: 2026-04-09
- **관련 결정**:
  - `docs/decisions/20260409_1_harness_step6_auto_gate.md` (게이트 스크립트 도입)
- **관련 문서**:
  - `docs/harness_step6_gate_guide.md`
  - `output/improvement_risk_benefit.md`

## 배경

`scripts/harness_step6_gate.py`를 도입했으나 사용자가 매주 수동 실행해야 하는 구조였다. 이는 다음 문제를 가진다.

- 사람이 실행을 잊으면 게이트가 사문화 → Step 6 진입 판단이 감에 의존
- 하네스 가이드 원칙 "진짜 지켜야 할 규칙은 코드로 강제" 위배
- 사용자 피드백: "내가 실행해야 하나요? 자동으로 실행해서 체크해야지요?"

## 결정

1. `harness_step6_gate.py`에 **`--auto` 모드**를 추가한다.
   - 수동 항목(A4/C2/C3) skip
   - 자동 8개 중 **7/8 이상** → READY (텔레그램 "수동 확인 요청")
   - 7/8 미만 → NOT READY (텔레그램 FAIL 항목 요약)
   - 리포트: `workspace/gate_reports/YYYYMMDD_HHMM_gate_auto.md`
2. `scripts/harness_gate_weekly.bat` 배치 래퍼 작성.
3. `scripts/register_harness_gate_task.ps1` 등록기 작성 — 관리자 1회 실행.
4. **Windows 작업 스케줄러에 주간 등록** (`HarnessStep6Gate`, 매주 목요일 09:05 KST).
5. READY 도달 시에만 사용자가 대화형 실행으로 A4/C2/C3 최종 확인 → 9/11 이상이면 Step 6 진입.

## 등록 확인

```
schtasks /Query /TN HarnessStep6Gate /FO LIST
→ 호스트: INTEGERII
  작업 이름: \HarnessStep6Gate
  다음 실행 시간: 2026-04-16 오전 9:05:00
  상태: 준비
  로그온 모드: 대화형만
```

등록일: 2026-04-09 (사용자 관리자 PowerShell로 1회 등록)

## 근거

- 주간 자동 실행으로 사람이 잊어도 게이트가 계속 돌아감
- READY 도달 시에만 사람 개입 → 편향 차단(Step 5 교차검증 정책) 유지
- 훅 방식보다 안전: 기존 자동화(`daily_live.py` 등)에 간섭 0 (읽기+텔레그램만)
- 기존 Windows Task Scheduler 사용 → 신규 인프라 0

## 대안과 기각 사유

| 대안 | 기각 사유 |
|------|-----------|
| Claude Code 셸에서 schtasks/Register-ScheduledTask 직접 등록 | 권한 거부 (샌드박스) — 불가 |
| PreToolUse 훅으로 세션마다 자동 실행 | 세션 시작 지연, 여전히 사람이 Claude Code를 열어야 함 |
| jarvis_executor/daily_report에 piggyback | 기존 트레이딩 자동화와 결합도 증가, 실패 시 상호 영향 |
| 사용자 수동 실행 유지 | 사문화 가능성 — 사용자 명시 거부 |

## 영향 범위

- 신규: `scripts/harness_gate_weekly.bat`, `scripts/register_harness_gate_task.ps1`, Windows 작업 `HarnessStep6Gate`
- 수정: `scripts/harness_step6_gate.py` (`--auto` 모드 추가, 기존 동작 보존)
- 실거래·트레이딩 모듈 영향: 없음 (게이트는 읽기+텔레그램 전용)

## 후속 조치

- 2026-04-16 09:05 첫 자동 실행 예정 — 텔레그램 수신 여부로 등록 검증
- READY 도달 시 사용자는 `python scripts/harness_step6_gate.py` 대화형 실행으로 최종 판정
- 1달 이상 NOT READY 지속 시 ①/② 정책 자체 재설계 검토
- Step 6 진입(GO) 후에는 본 스케줄을 삭제 또는 Step 6 완료 검증용으로 재활용
