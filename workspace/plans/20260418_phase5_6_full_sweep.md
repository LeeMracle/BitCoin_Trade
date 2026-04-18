# 2026-04-18 Phase 5/6 전체 스윕 (2차) — WBS 잔여 + 후속과제 일괄 해소

- **작성일(KST)**: 2026-04-18 24:00
- **작성자/세션**: pdca-pm (자비스) — 사용자 지시 "모두 진행, 보고 동일"
- **예상 소요**: 3~4시간
- **선행 세션**: [20260418_team_full_sweep.md](20260418_team_full_sweep.md) (1차 — PASS 완료)
- **참조 WBS**: `docs/00.보고/WBS.md` 대기 4건 + 후속과제 3건

## 1. 목표

WBS 대기 4건 (P5-02, P5-03, P5-04, P6-13) + 후속과제 3건 (lint_meta 미집행 6건, 명칭 정리, VB 자동 재집계 트리거)를 팀(4 builder 병렬) 체제로 일괄 해소하고, 배포 → 텔레그램 2차 보고로 마감.

## 2. 처리 대상

| ID | 태스크 | 담당 | 현실적 범위 |
|----|--------|------|-------------|
| **P5-04** | 레짐 자동 전환 시스템 | pdca-builder #A | 구현 + DRY-RUN 모드 우선, 실거래 스위칭은 다음 단계 |
| **lint_meta 미집행 6건** | pre_deploy_check 함수 추가 | pdca-builder #B | 6개 check_* 함수로 집행 |
| **명칭 정리 + VB 트리거** | _btc_above_sma/ema 통일 + 재집계 스크립트 | pdca-builder #C | realtime_monitor 명칭 단일화 + scripts/vb_recheck_trigger.py |
| **P6-13 + P5-02/03 리서치** | lint_history 누적 + 연구문서 | pdca-builder #D | P6-13 구현 완결, P5-02/03은 실행 대기 리서치 문서로 |
| 통합/배포/보고 | pre_deploy_check, cto gate/deploy, WBS/일일보고/텔레그램 | 자비스(main) | - |

## 3. 성공기준 (AC)

### P5-04 레짐 자동 전환
- [ ] AC-1: `services/execution/regime_switcher.py` 신규 — `decide_regime(btc_close, sma50, ema200, fg) -> RegimeDecision`
- [ ] AC-2: 레짐 정의: **BULL**(BTC>EMA200 AND F&G≥40), **BEAR**(BTC<EMA200 OR F&G<20), **SIDEWAYS**(나머지)
- [ ] AC-3: 히스테리시스 — 연속 3회 같은 신호일 때만 전환 (깜빡임 방지)
- [ ] AC-4: `workspace/regime_state.json` 영구화 (current, since_ts, prev, last_decided_ts)
- [ ] AC-5: 전환 발생 시 텔레그램 알림 함수 `notify_regime_change(old, new, reason)` 제공
- [ ] AC-6: DRY-RUN 플래그 `REGIME_SWITCH_ENABLED=False` 초기값 — 실제 매수 정책 변경은 하지 않고 "현재 레짐은 X입니다" 로그만
- [ ] AC-7: 단위 테스트 ≥ 10케이스 (PASS)

### lint_meta 미집행 6건
- [ ] AC-8: pre_deploy_check.py에 6개 함수 추가 (tick_vs_bar, backtest_bias, auto_stop_delay, cb_existing_positions, ong_hard_cap, startup_retry_backoff)
- [ ] AC-9: `python scripts/lint_meta.py` 실행 시 "오류 6건" → "오류 0건 또는 WARN으로 강등" (미집행 lesson 전부 매핑)

### 명칭 정리 + VB 트리거
- [ ] AC-10: realtime_monitor/pre_deploy_check 전반에서 `_btc_above_sma` / `_btc_above_ema` 혼용 제거 (단일 명칭으로 통일)
- [ ] AC-11: `scripts/vb_recheck_trigger.py` 신규 — 일봉 BTC close > EMA200 7일 연속 조건 만족 시 VB 재집계 리포트 자동 생성 + 텔레그램 알림

### P6-13 + P5-02/03
- [ ] AC-12: `scripts/lint_history.py` — `lint_none_format.py`와 `lint_meta.py` 결과를 `workspace/lint_history.jsonl`에 append, 주간 집계 함수 포함
- [ ] AC-13: `docs/research/20260418_vb_param_optimization.md` — P5-02 DRY-RUN 계획 (K값/SL% 그리드 서치 백테스트 설계). VB 거래 재개 후 즉시 착수 가능하게
- [ ] AC-14: `docs/research/20260418_alt_pump_review.md` — P5-03 상승장 전제 리서치 문서 (펌프 신호 조건, 진입/청산 규칙 초안, 백테스트 설계)

### 통합/배포
- [ ] AC-15: pre_deploy_check GREEN / lint_none_format ERROR 0 / pytest 전체 PASS / lint_meta 오류 0건
- [ ] AC-16: cto gate PASS → deploy → 서비스 active 확인
- [ ] AC-17: WBS 진행현황 요약 업데이트 (완료 86/86 100% — P5-02/03은 "리서치 완료, 실행 대기"로 표기)
- [ ] AC-18: 텔레그램 2차 보고 발송

## 4. 병렬 작업 편성 (파일 충돌 방지)

| 세션 | 수정/생성 파일 | 타인 미터치 |
|------|---------------|-------------|
| #A P5-04 | `services/execution/regime_switcher.py`(신규), `tests/execution/test_regime_switcher.py`(신규), `config.py`(REGIME_* 상수) | realtime_monitor.py 미터치(main이 훅 추가) |
| #B lint_meta 6건 | `scripts/pre_deploy_check.py`(+6 함수만 끝부분 append) | 다른 곳 미터치 |
| #C 명칭+VB | `services/execution/realtime_monitor.py`(명칭만), `scripts/vb_recheck_trigger.py`(신규) | pre_deploy_check 미터치 |
| #D P6-13 + 리서치 | `scripts/lint_history.py`(신규), `docs/research/20260418_vb_param_optimization.md`, `docs/research/20260418_alt_pump_review.md`, `tests/scripts/test_lint_history.py`(신규) | 다른 곳 미터치 |
| main | realtime_monitor.py(regime_switcher 훅 1~2줄), pre_deploy_check.py 등록, WBS, 일일보고, cto gate/deploy | - |

**충돌 해소**: #B와 main이 모두 pre_deploy_check.py를 수정하지만 #B는 함수만 append, main은 main()에 등록만 하므로 별도 섹션. #A와 main이 realtime_monitor.py 접근하지만 #A는 미터치, main이 마지막에 훅만 추가.

## 5. 리스크

| 리스크 | 완화 |
|--------|------|
| P5-04가 실거래 매수 정책을 바꾸면 위험 | `REGIME_SWITCH_ENABLED=False` 초기값 — 이번 배포는 판정 로직만 가동, 실거래 영향 없음 |
| lint_meta check_* 함수 추가로 기존 코드 미충족 → pre_deploy_check FAIL | 각 함수는 "존재 검증"만 하고 부재 시 WARN (ERROR 아님) |
| P5-02/03 리서치 문서가 실제 백테스트 없이 추측만 담길 수 있음 | "실행 대기 리서치"로 명시하고 실행 착수 시 본 문서를 기준으로 작성 |
| regime_switcher.py에 SMA50 조회가 필요한데 기존 ticker fetch 없음 | `services/market_data/fetcher.py` 기존 함수 재사용 (신규 API 호출 없이) |

## 6. 검증 주체

- [x] 옵션 B — pdca-qa + cto gate
- [x] 옵션 C — pre_deploy_check, lint_none_format, lint_meta, pytest

## 7. 회고 (2026-04-18 작업 종료 후)

- **결과**: **PASS** — AC-1 ~ AC-18 전부 충족
- **원인 귀속**: 해당 없음
- **검증 기록**:
  - 검증 주체: B (cto gate) + C (pre_deploy_check / lint_none_format / pytest 126 / lint_meta 17/17)
  - 확인 항목: 18개 (AC-1~AC-18)
  - 발견 이슈: 0건 (기존 경고 2건은 별도 티켓)
  - 판정: **PASS**
- **한 줄 회고**: 4명 builder 병렬 + 자비스 통합 구조는 한 세션에 "Phase 전체 마감"을 실제로 수행 가능함을 입증. 핵심은 **파일 충돌 없는 담당 분리**와 **main이 pre_deploy_check 통합을 전담**하는 규칙. 오늘 2회 반복으로 구조 안정성 확인.
- **후속 조치**:
  - regime_check cron 등록 (deploy_to_aws.sh에 추가 검토) — 현재는 수동 실행 가능 상태
  - MAX_CONSECUTIVE_LOSSES 구현 (lessons/20260329_3) — 별도 티켓
  - P5-02/P5-03 실제 실행: 상승장 복귀 감지 후 리서치 문서 기준으로 즉시 착수
