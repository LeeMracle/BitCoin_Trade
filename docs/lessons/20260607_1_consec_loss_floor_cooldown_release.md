# lessons #38 — 연패 cooldown은 closed_trades 재계산이라 cooldown_until 리셋만으론 해제 불가

- 날짜: 2026-06-07 KST
- 분류: 안전장치 / state 회계 / 운영 해제 절차
- 관련 lessons: #3(체결 즉시 체크), #30(cooldown vs alerted 분리), #34/#37(systemd watchdog B9)

## 현상

ADR 20260607-1로 5연패 원인(저유동성 알트, MIN_VOLUME 3억→5억 환원)을 제거한 뒤,
옛 연패(6연패: FLOCK·IN·ALT·HUNT·STEEM·SUN)에 의한 cooldown(`cooldown_until=1780877059`, 6/8 09:04 KST)을 해제하려 함.
state의 `cooldown_until`을 0으로 고치거나 텔레그램 `/cooldown clear`를 써도, 다음 cycle에 cooldown이 72h 재설정되는 함정 존재.

## 근본 원인

연패(consec)는 **별도 카운터 필드가 없음.** 매 cycle `state["closed_trades"]`를 재계산:
- `check_consec_loss`(periodic_analysis.py) / `_get_consec_loss`(realtime_monitor.py)
  → `exit_date >= strategy_start` 거래를 reversed 순회하며 `return_pct<=0` 누적

cooldown 재설정 경로 3개:
- A. 매도 체결 직후 (realtime_monitor.py:2237) `consec>=3 and not cooldown_until` → 3일
- B. 정기보고 cycle (realtime_monitor.py:695) `consec>=5 and cd/alerted 만료` → 72h
- C. 만료 자동 pop (realtime_monitor.py:1850)

→ `cooldown_until`만 리셋하면 현재 consec=6이 그대로 남아 다음 cycle 경로 B가 72h 재설정.
**state 편집 / `/cooldown clear` 정식 명령 둘 다 단독으로는 구조적으로 해제 불가.**

## 핵심 교훈

- 안전장치 카운트가 "원장(closed_trades) 재계산" 방식이면, 결과 플래그(cooldown_until)만 리셋해도
  다음 평가에서 부활한다. **카운트 산정 입력 자체를 끊어야** 한다.
- 단, closed_trades에 가짜 거래/마커를 넣는 것(거래 위조)은 금지 — 통계 오염.
- strategy_start 이동은 누적 통계(n/wins) 전체를 날려 ADR 의도(통계 보존)와 충돌 → 부적합.
- 해법: **연패 산정 전용 floor 필드(`consec_loss_floor_date`)** 도입. 통계는 strategy_start 기준 유지,
  연패만 floor 이후(>) 거래로 한정. floor 위(신규)에서 손실이 나면 안전장치는 정상 재가동.

## 수정 (코드 + 운영 조치)

### 코드
- `services/reporting/periodic_analysis.py::check_consec_loss` — floor 필터 추가 (n/wins는 보존)
- `services/execution/realtime_monitor.py::_get_consec_loss` — 동일 floor 필터 추가 (산정 통일)

### 운영 (state)
- `consec_loss_floor_date = "2026-06-06 00:05"` (마지막 손실 SUN exit_date) → consec 6→0
- `cooldown_until = 0`, `consec_loss_alerted_until = 0`
- 편집 전 백업 `.bak_20260607`, 봇 stop→편집→start로 race 회피

### 불변 (절대 미변경)
- `REGIME_FILTER_ENABLED=True` 유지 — 깊은 BEAR 차단 보존

## 검증규칙 (pre_deploy_check 추가)

`scripts/pre_deploy_check.py::check_consec_loss_floor_consistency()`:
- 연패 산정 두 함수(check_consec_loss / _get_consec_loss)가 **둘 다** `consec_loss_floor_date`를 참조해야 함.
- 한쪽만 floor를 적용하면 산정 불일치로 cooldown 사일런트 부활(경로 B) 가능 → ERROR.

### 단위 검증 (통과 기록)
- floor 없음: consec=6 (기존 동작 회귀 OK)
- floor=SUN: consec=0, n=7/wins=1 보존
- floor + 신규 손실 2건: consec=2 (안전장치 정상 재가동)

## 관련 lessons

- #3 — 안전장치는 주기 체크가 아닌 체결 즉시 체크
- #30 — cooldown_until(매수차단) vs consec_loss_alerted_until(silence) 분리 플래그
- #37 — 5연패 watchdog 회피 (self.running=False 금지, cooldown_until 강제 연장으로 대체)
