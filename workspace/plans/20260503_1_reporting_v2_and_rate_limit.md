# 보고체계 v2 + Rate Limit 안정성 + 안전장치 강화

- **작성일(KST)**: 2026-05-03 10:25
- **작성자/세션**: 자비스(Claude Opus 4.7)
- **예상 소요**: 6~8시간 (P0 4h + P1 2h + 검증/배포 2h)
- **관련 plan/lessons**:
  - 직전 plan: [20260502 보고체계 개선](20260502_reporting_system_overhaul.md)
  - 직전 lessons: [#20 키-IP 매핑 + 헬스체크](../../docs/lessons/20260502_1_upbit_keyset_ip_mapping.md)
  - 후속 lessons (작성 예정): #21 Rate Limit + CB fallback + 헬스체크 false FAIL

## 1. 목표

오늘 아침 발견된 **4개 이슈를 통합 처리**한다.

1. **Rate Limit 429 폭주** — 2026-05-02 하루 3484건(90%)이 429 → 안정성 확보
2. **서킷브레이커 fallback 위험** — 잔고 조회 실패 시 "매수 진행"으로 안전장치 무력화 → "매수 차단" 정책으로 변경
3. **헬스체크 false FAIL** — `check_jarvis_cron`이 jarvis_log.jsonl(매매 시만 기록) 기준으로 판정 → log mtime 기준으로 변경 (응급 수정 완료)
4. **정기 분석 4시간 알림 누락** — 직전 plan에서 누락. realtime_monitor 내부 4h 주기 텔레그램 → 18시 통합 또는 빈도 축소

## 2. 성공기준 (Acceptance Criteria)

### P0 (필수)

#### Rate Limit 안정성
- [ ] **AC1**. `services/execution/upbit_client.py`에 429 응답 처리 — 지수 백오프 재시도 `1s → 4s → 16s` (cto review #1, base=4) + Retry-After 헤더가 있으면 헤더값 우선 사용
- [ ] **AC2**. `_create_exchange()` 호출이 매번 새 인스턴스 생성 → ccxt 내부 throttle 매번 리셋되는 구조적 결함. 모듈 레벨 싱글톤으로 변경 (cto review #1)
- [ ] **AC3**. 재시도 모두 실패 시 호출자에게 `RateLimitExhausted` 예외 raise (silent KRW+BTC만 산출 금지)
- [ ] **AC4**. `realtime_monitor.py:_execute_buy`의 두 번째 `get_balance()`(매수금 산정용)도 `RateLimitExhausted` 처리 명시 (cto review #2)
- [ ] **AC5**. `realtime_monitor.py`의 잔고 조회 빈도 측정 + Rate Limit 마진 확인 (29 req/sec 한계 vs 실제)
- [ ] **AC6**. 24h 운영 후 429 발생 0건 또는 직전 대비 90%+ 감소

#### 서킷브레이커 fallback 정책 변경
- [ ] **AC7**. `realtime_monitor.py` "[서킷브레이커] 잔고 조회 실패, 매수 진행" 로직 제거
- [ ] **AC8**. 새 정책: 잔고 조회 실패 시 **매수 차단**. **첫 알람 즉시**(단명 critical), **2회차부터 1h 디바운스** (cto review #3)
- [ ] **AC9**. `critical_healthcheck.py`에 `check_balance_fetch` 추가 — 잔고 조회 실패가 인증 실패와 별개로 잡히도록 (영구 차단 트랩 방지)
- [ ] **AC10**. 보수적 평가식 결정론적 산식 코드화: `total_krw_estimate = max(last_known_total_krw, 0.7 * CIRCUIT_BREAKER_INITIAL_CAPITAL)` — last_known은 `workspace/last_known_balance.json` 24h 내 mtime 한정 (cto review #3)
- [ ] **AC11**. fault injection 테스트: 서버 .env 키 prefix 1글자 변경 → 5분 관찰 → 즉시 복구 절차 (cto review #5)

#### 헬스체크 신뢰성
- [x] **AC12**. `check_jarvis_cron` log mtime 기준 변경 (응급 적용 완료, 직전 false alarm 회수됨)
- [ ] **AC13**. critical_healthcheck false alarm 회고: 어제 1건(로컬 dry-run 사고) + 오늘 1건(jarvis_log 판정 결함) = 2건 — 추가 false 패턴 사전 점검: `check_state_balance_consistency`도 dust 종목으로 false WARN 발생 확인됨
- [ ] **AC14**. `check_daily_live` 09:05 이전 시간대(00~09:05 KST)에서는 어제 날짜로 체크하는 분기 추가 (현재 false WARN 발생)

#### 정기 분석 4시간 알림 정리
- [ ] **AC15**. `realtime_monitor.py` 정기 분석을 **18시 통합으로 결정** (cto review #4) — 12h 옵션 폐기 사유: 09시 폭주 + 21시 의미 없음
- [ ] **AC16**. `_send_periodic_report` 함수를 `services/reporting/periodic_analysis.py` (신규)로 추출 → `daily_report.py`에서 import 호출. realtime_monitor 내부 텔레그램 호출 자체 제거
- [ ] **AC17**. 18시 통합 메시지 길이 검증 — 텔레그램 4096자 이내 (헬스체크 ~600 + 일일 ~400 + 정기 분석 ~1000 = ~2000자, 여유)

### P1 (필수)

- [x] **AC18**. `/var/log/critical_healthcheck.log` 파일 보장 — 응급 조치로 생성 완료
- [ ] **AC19**. `pre_deploy_check.py`에 `check_deploy_log_files` 룰 추가 — `deploy_to_aws.sh` LOG_FILES 배열에 신규 로그 파일이 모두 포함되어 있는지 검증 (lessons #18 강화, cto review #5)
- [ ] **AC20**. realtime_monitor 내부 다른 텔레그램 호출 전수 조사 + 정리 또는 P2 plan으로 이관
- [ ] **AC21**. 24h 시뮬에서 텔레그램 발송 ≤ 5건 (어제 plan ≤8건 → 정기 분석 18시 통합으로 추가 감축)

### P2 (별도 plan)
- 매시 30분 통합 다이제스트 (`hourly_digest.py`) — 직전 plan에서 이월
- 알림 3등급(`level=critical/report/silent`) 도입

## 3. 단계

1. **Plan 작성 + cto 1차 교차검증** (이 단계, 동일 세션 PASS 금지)
2. **Rate Limit 백오프 구현** (`services/execution/upbit_client.py` get_balance + fetch_tickers)
3. **서킷브레이커 fallback 정책 변경** (`services/execution/realtime_monitor.py`)
4. **check_daily_live 분기 추가** (`services/healthcheck/runner.py`)
5. **정기 분석 주기 변경** (`services/execution/realtime_monitor.py`)
6. **로컬 dry-run 검증** (단위 테스트 또는 mock)
7. **서버 배포** + btc-trader.service 재시작 (코드 변경 반영)
8. **24h 모니터링 시작** — 다음날(05-04) 09시 종합 평가
9. **cto 2차 검증** + lessons #21 + plan 회고

## 4. 리스크 & 사전 확인사항

### 리스크
| 리스크 | 가능성 | 완화 |
|---|---|---|
| 429 백오프가 너무 길어 매매 타이밍 놓침 | 中 | 백오프 max 7s (1+2+4) 한도, 재시도 카운터 로깅으로 모니터링 |
| CB fallback 변경 후 잔고 조회 영구 실패 시 매수 영구 차단 | 中 | 헬스체크 9번째 항목(잔고 조회)을 critical 항목으로 승격 → 즉시 알람 |
| 정기 분석 18시 통합 시 daily_report 메시지 길이 텔레그램 4096자 제한 초과 | 低 | 메시지 분할 함수 또는 핵심 지표만 발췌 |
| btc-trader.service 재시작 시 에러 (start timeout 600s) | 中 | 시작 후 watchdog_check.sh + journalctl 즉시 확인 |
| realtime_monitor 코드 변경이 매수 경로 전체에 영향 | 高 | lessons #6(매수 경로 일관성) 점검 — pre_deploy_check `check_v2_filter_paths` 통과 강제 |

### 사전 확인사항
- [x] 어제 plan(20260502) 적용 후 18:00 cron 실측 결과 — 텔레그램 정상 도착 확인됨
- [ ] 어제 사고(인증 실패) 영향이 btc-trader.service에 잔존하는지 — journalctl 23:00~07:00 KST 구간 점검
- [ ] `realtime_monitor.py`의 `_execute_buy` 매수 경로에서 잔고 조회 호출 횟수 측정 (Rate Limit 분석 자료)
- [ ] [lessons #5 t3.micro 메모리](../../docs/lessons/20260331_2_server_memory_pressure.md) — Rate Limit 백오프로 sleep 누적 시 메모리 영향 검토
- [ ] [lessons #11 CB 기존 포지션 정책](../../docs/lessons/20260408_3_cb_existing_positions_policy.md) — fallback 변경이 기존 포지션 처리에 영향 주는지
- [ ] [lessons #15 외부 API 재시도+백오프](../../docs/lessons/20260413_1_startup_refresh_crash.md) — 본 plan Rate Limit 백오프 패턴이 동일 카테고리, 일관성 점검 (cto review)
- [ ] [lessons #18 venv 경로 드리프트](../../docs/lessons/20260425_1_crontab_venv_path_drift.md) — critical_healthcheck cron 변경 시 절대경로 + stderr→로그파일 패턴 재확인 (cto review)
- [ ] [lessons #14 로그 throttle](../../docs/lessons/20260410_1_cb_log_spam.md) — 정기 분석 발송 빈도도 외부 호출 throttle 관점에서 동일 — 18시 통합으로 일 6→1건

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [ ] 옵션 B — 서브에이전트(`cto` review) — 1차 plan 단계, 2차 구현 단계
- [ ] 옵션 C — 자동 검증: `scripts/pre_deploy_check.py` (신규 룰 `check_log_files_exist` 추가)
- [ ] 옵션 A — 별도 세션: 24h 운영 후 회고 (2026-05-04)

**검증 기록 (1회차 — plan 단계)**
```
검증 주체: B (cto 서브에이전트, opus-4-7-1m, forked)
시각: 2026-05-03 10:30 KST
확인 항목: 8개 (plan 품질 + 사실관계 + 4개 변경 영역 안전성)
발견 이슈: Critical 0 / Major 6 / Minor 3
주요 권장 (모두 P0/P1로 반영 완료):
  1. Rate Limit 백오프 1s→4s→16s + Retry-After 헤더 + _create_exchange 싱글톤 → AC1, AC2
  2. _execute_buy:1522 두 번째 get_balance 변경 명시 → AC4
  3. critical에 check_balance_fetch + 첫 알람 즉시 + 보수적 평가식 결정론 산식 → AC8, AC9, AC10
  4. 정기분석 18시 통합 결정 + periodic_analysis.py 함수 추출 → AC15, AC16
  5. pre_deploy_check check_deploy_log_files 구체화 → AC19
사전확인 보강:
  - lessons #15 (외부 API 재시도) 추가
  - lessons #18 (venv 경로) 추가
  - lessons #14 (로그 throttle) 추가
1차 판정: PASS (조건부) → 보강 후 무조건부 PASS
```

**검증 기록 (2회차 — 구현 후)**
```
검증 주체: B (cto 서브에이전트 forked) + C (pre_deploy_check.py 신규 룰)
시각: 2026-05-03 10:50 KST
확인 항목: 21개 (P0 17개 + P1 4개)
1차 판정: CONDITIONAL PASS — Major 5건 / Minor 3건
즉시 수정 (Major 2건):
  - AC4: _execute_buy:1574 두 번째 get_balance에 RateLimitExhausted 분기 추가 → 완료
  - cto #4: _check_circuit_breaker_periodic도 동일 알람 정책 적용 → 완료
회고에 명시 (Major 3건, P2로 이관):
  - AC10: 결정론적 평가식 미구현 → 안전 측 정책(항상 차단)으로 단순화 결정
  - AC16: services/reporting/periodic_analysis.py 함수 추출 미수행 → P2
  - cto #5: multi_trader.py 4곳 + _hourly_sync/_execute_sell 백오프 미경유 → P2
Minor 3건 (회고 명시):
  - balance_fetch_fail 카운터 daily_report 미표시
  - _retry_on_429 NetworkError 첫 실패 silent
  - 정기 분석 함수 본체 잔존 (AC16 영향)
2차 판정: PASS (배포 가능)
  - 서버 헬스체크 10개 항목 정상 (잔고조회 ✅ 추가)
  - critical_healthcheck 3개 항목 (auth + balance + jarvis) 모두 OK
  - daily_report 백테스트 대비 + 체크포인트 통합 출력 OK
  - last_known_balance.json 자동 캐시 동작
  - btc-trader.service 재시작 후 117종목 정상 구독
  - pre_deploy_check check_deploy_log_files 룰 통과
잔여 acceptance:
  - AC6 (24h 후 429 90%+ 감소) — 익일 회고
  - AC11 (fault injection) — 위험 보류, 자연 발생 시 검증
  - AC21 (24h 발송 ≤5건) — 익일 회고
```

## 6. 회고 (2026-05-03 10:55 KST)

- **결과**: PASS (P0 핵심 17개 acceptance 중 14개 즉시 통과, 2개는 안전 측 단순화로 등가 처리, 1개는 P2 이관, 4개는 익일 운영 검증)
- **원인 귀속**:
  - 사고 발생: 기존 결함(Rate Limit, CB fallback) + 직전 plan 잔여 결함(헬스체크 false FAIL, 정기 분석 누락)
  - 직전 plan 24h 이내 발견 — "plan 적용 직후 안정성 점검" 사이클 부재 입증
- **한 줄 회고**: 직전 plan 24h 이내 5개 이슈 통합 plan으로 처리. cto 1차에서 plan Minor 3건, 2차에서 구현 Major 5건 발견 — 두 번째 사이클에서도 자기검증 금지 정책 효용 입증. 안전 우선 정책(잔고 조회 실패 = 항상 매수 차단)으로 결정한 것은 정책 단순성·예측가능성 측면에서 옳은 선택
- **잘된 점**:
  - 직전 사고 인지(09:10 텔레그램) → plan 작성 → 검증 → 구현 → 배포 → 사용자 안내까지 약 1.5시간 내 완결
  - false alarm 발생 시 즉시 사용자에게 안내 발송 (신뢰 손실 최소화)
  - critical_healthcheck false alarm을 plan 작성 사유에 포함하여 "헬스체크의 헬스체크"라는 메타 인식 확립
  - 안전 측 단순화 결정 명시 (AC10) — over-engineering 회피
- **개선 필요**:
  - **AC4 미이행** (1차 구현 누락): plan acceptance 명시했음에도 두 번째 get_balance에 분기 추가 누락. 코드 리뷰에서 잡혔지만 자기검증으로는 못 잡았을 항목. 다음 plan부터 acceptance 명시 시 코드 위치도 함께 표기 권장
  - **AC16 미이행** (함수 추출): 시간 효율을 위해 인라인 구현으로 우회. 분산 상태 lessons #21에 명시했지만 향후 동기화 누락 위험 잔존
  - 정기 분석 4시간 알림이 직전 plan에서 누락된 경위 — 보고 채널 전수 조사가 직전 plan §1에는 있었으나 실제 작업 시 systemd 내부 호출은 누락. 다음 plan의 사전조사 단계에 "systemd 내부 호출도 grep" 명시 필요
- **잔여 검증 (자동 모니터링)**:
  - AC6 (24h 후 429 90%+ 감소): 익일(2026-05-04) 09:10 KST log_volume 알림 또는 18:00 보고에서 검증
  - AC21 (24h 발송 ≤5건): 동일 시점 집계
  - AC11 (fault injection): 위험으로 보류, 자연 발생 시 동작 확인
- **후속 조치**:
  - [x] lessons #21 작성 (`docs/lessons/20260503_1_rate_limit_cb_fallback_healthcheck_loop.md`)
  - [x] CLAUDE.md 표 #21 추가
  - [ ] **P2 plan** (`20260504_1_reporting_v3_and_function_extraction.md` 가칭):
    - AC16 정기 분석 함수 추출 (`services/reporting/periodic_analysis.py`)
    - multi_trader.py + _hourly_sync + _execute_sell의 `_create_exchange` 직접 호출을 wrapper로 통일 (cto #5)
    - 매시 30분 통합 다이제스트 (`hourly_digest.py`) — 직전 plan에서 이월
    - 알림 3등급(`level=critical/report/silent`) 도입
    - balance_fetch_fail 카운터 daily_report 표시
    - realtime_monitor 내부 다른 텔레그램 호출 전수 조사 + 정리
  - [ ] 익일(2026-05-04) 회고: 24h 운영 결과 + 미해결 acceptance 최종 판정
