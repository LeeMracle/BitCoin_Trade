# Execution Plan — 연패 cooldown 근본 해제 (consec_loss_floor 도입)

- 작성: bata-engineer
- 일자: 2026-06-07 KST
- 승인: 마스터 (ADR 20260607-1 MIN_VOLUME 5억 환원 연계)
- 관련 lessons: #3(체결 즉시 체크), #30(cooldown vs alerted 분리), #34/#37(systemd watchdog)

## 1. 배경 / 문제

ADR 20260607-1로 5연패 원인(저유동성 알트, MIN_VOLUME 3억→5억)을 제거. 옛 알트 손실로 누적된 연패(현재 6연패: FLOCK·IN·ALT·HUNT·STEEM·SUN)에 의한 cooldown을 해제해야 함.

### RCA — 재설정 함정 (이 작업의 핵심)
연패는 별도 카운터 필드가 없음. `closed_trades`를 매 cycle 재계산:
- `check_consec_loss`(periodic_analysis.py:21) / `_get_consec_loss`(realtime_monitor.py:1820)
  → `exit_date >= strategy_start` 거래를 reversed로 순회, `return_pct<=0` 누적
- 재설정 경로 3개:
  - A. 매도 체결 직후 (realtime_monitor.py:2237) `consec>=3 and not cooldown_until` → 3일
  - B. 정기보고 cycle (realtime_monitor.py:695) `consec>=5 and cd/alerted 만료` → 72h
  - C. 만료 자동 pop (realtime_monitor.py:1850)

→ **state의 cooldown_until만 0으로 고치거나 `/cooldown clear`를 써도 다음 cycle 경로 B가 consec=6으로 72h 재설정.** state 편집/정식 명령 단독으로는 구조적으로 불가능.

## 2. 목표 / 성공기준

- [ ] cooldown_until / consec_loss_alerted_until 만료(과거값)
- [ ] **1 cycle(9분) 경과 후에도 cooldown 재설정 안 됨** (로그에 "5연패 cooldown" 미발생) ← 최우선
- [ ] REGIME_FILTER_ENABLED=True 불변 (절대 미변경)
- [ ] 누적 통계(n/wins/win_rate)는 보존 (strategy_start 기준 유지)
- [ ] pre_deploy_check PASS

## 3. 설계 — consec_loss_floor_date 필드 도입

연패 산정 함수에 floor 필드 추가. floor가 있으면 연패 카운트는 `exit_date > floor`인 거래만 집계 (통계 n/wins는 strategy_start 기준 그대로).

- floor 값 = 마지막 손실 SUN exit_date = `"2026-06-06 00:05"` → SUN 포함 이전 모두 연패 제외 → consec=0
- 신규 거래(6/7 이후)가 손실이면 floor 위라서 정상적으로 다시 카운트 시작 (안전장치 보존)

### 변경 파일 (2개 함수 + state)
1. `services/reporting/periodic_analysis.py::check_consec_loss` — floor 필터 추가
2. `services/execution/realtime_monitor.py::_get_consec_loss` — floor 필터 추가 (동일 로직 통일)
3. 서버 `workspace/multi_trading_state.json`:
   - `consec_loss_floor_date = "2026-06-06 00:05"`
   - `cooldown_until = 0`, `consec_loss_alerted_until = 0`

### 영향 범위 grep (모든 연패/cooldown 경로)
- 연패 산정: check_consec_loss, _get_consec_loss (2곳, 위 수정 대상)
- cooldown 소비: _is_loss_cooldown(realtime:1833) → cooldown_until만 봄 (변경 불필요)
- multi_trader.py: 연패/cooldown 로직 없음 (grep 0건)
- scanner/매수경로: cooldown_until은 realtime 전담, scanner 별도 없음
- VB 전략: 별도 loss_cooldown_until (vb_state.json) — 본 작업 무관, 미변경

## 4. 배포 방식

- 코드 2파일 hotfix → `bash scripts/hotfix_deploy.sh` (pre_deploy_check 강제)
- state 편집은 봇 race 회피: 봇 stop → 백업(.bak_20260607) → state 편집 → start
  (또는 hotfix 후 restart 시점에 state 동시 반영)

## 5. 검증 계획

- pre_deploy_check PASS
- 단위: floor 적용 시 consec=0, floor 없으면 기존 동작 (회귀)
- 배포 후 1 cycle 로그 확인 — "5연패 cooldown" 미발생
- REGIME ON 확인 / cooldown_until 만료 확인

## 6. lessons / 룰 후보

- lessons #38(예정) — 연패는 closed_trades 재계산이므로 cooldown_until 리셋만으로 해제 불가, floor 필드 필요
- pre_deploy_check 룰(예정) — check_consec_loss/_get_consec_loss의 floor 필터 일관성 검증
