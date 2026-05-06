# #21 — Rate Limit 429 폭주 + 서킷브레이커 fallback 우회 + 헬스체크 false alarm 사이클

- **발생일**: 다중 — 2026-05-02 종일 (Rate Limit), 2026-05-03 09:30 (false alarm)
- **탐지일**: 2026-05-03 09:10 (log_volume 알림으로 사용자 인지)
- **영향 범위**:
  - btc-trader.service 어제 일일 오류 로그 3484줄 (90%가 429)
  - 서킷브레이커 잔고 조회 실패 시 "매수 진행" silent fallback → 안전장치 무력화 위험
  - 어제(2026-05-02) plan 적용 직후 critical_healthcheck false alarm 1건 (jarvis_log.jsonl 판정 결함)
- **원인 분류**: 코드 결함(Rate Limit 미처리) + 정책 결함(silent fallback) + 로직 결함(헬스체크 판정 기준)
- **관련 plan**: [workspace/plans/20260503_1_reporting_v2_and_rate_limit.md](../../workspace/plans/20260503_1_reporting_v2_and_rate_limit.md)
- **연관 lessons**: [#15 외부 API 재시도](20260413_1_startup_refresh_crash.md), [#11 CB 기존 포지션 정책](20260408_3_cb_existing_positions_policy.md), [#18 venv 경로 silent fail](20260425_1_crontab_venv_path_drift.md), [#20 키-IP 매핑 + 헬스체크 도입](20260502_1_upbit_keyset_ip_mapping.md)

## 1. 무엇이 일어났나

### 1.1 Rate Limit 429 폭주 (2026-05-02)
btc-trader.service가 realtime monitor로 117개 종목 감시 중 ccxt 호출이 업비트 Rate Limit(조회 29 req/sec)을 초과. 어제 하루 3484줄 오류 발생:
- 2484건: `[서킷브레이커] 잔고 조회 실패, 매수 진행: ... 429 Too Many Requests`
- 1000건: `[잔고] 알트 시세 일괄조회 실패: ... 429 — KRW+BTC만으로 산출`

근본 원인:
- `_create_exchange()`가 매번 새 ccxt 인스턴스 생성 → `enableRateLimit=True` 효과 매번 리셋
- 모든 ccxt 호출에 백오프 미적용 → 429 발생 시 즉시 fallback 분기로 전환
- realtime_monitor에서 `get_balance()`가 1회 매수 평가당 2회(서킷브레이커 + 매수금 산정), 주기 CB 체크 1회, 정기 분석 1회 등 종목·시각당 다발 호출

### 1.2 서킷브레이커 fallback 우회 위험
`realtime_monitor.py:1488`에 `except Exception as e: print(...) "매수 진행"` 코드 — 잔고 조회 실패 시 CB 평가 없이 매수가 그대로 진행되는 구조. 안전장치(L1/L2 발동 = 매수 차단/전량 청산)가 잔고 조회에 의존하는데, 그 조회가 실패하면 안전장치 자체가 무력화.

이전 lessons #20(2026-05-01 인증 실패 8h 무감지) 사고 동안에도 이 fallback이 silent로 매수를 허용했을 가능성. 다행히 EMA200 필터(BEAR 레짐 차단)가 별도로 작동해 실제 매수는 차단되었으나, 정책 결함은 그대로.

### 1.3 헬스체크 false alarm 사이클 (2026-05-03)
어제 plan(20260502)으로 도입한 `critical_healthcheck.py`가 오늘 09:30 텔레그램 critical 알람 발송. 원인:
- `check_jarvis_cron`이 `workspace/jarvis_log.jsonl` 마지막 entry로 판정
- 이 파일은 jarvis_executor가 **매매 발생 또는 오류 발생 시만** 기록 — 정상 동작 + 매매 신호 없는 시간엔 갱신 안 됨
- 매매 신호 없는 시간이 1h 넘으면 false FAIL 발화

추가로 발견:
- `check_daily_live`가 09:05 KST 이전 시간엔 "오늘 라인 없음" false WARN
- `check_state_balance_consistency`가 dust 종목(BTC, PYTH, SOLO, XCORE) false WARN

### 1.4 critical 로그 silent fail
어제 plan에서 직접 crontab 수정으로 critical_healthcheck cron을 등록했으나, `/var/log/critical_healthcheck.log` 파일을 별도로 생성하지 않음. cron 실행 시 `>> /var/log/critical_healthcheck.log 2>&1` 리디렉션이 silent fail (lessons #18 패턴 재현). 응급 조치로 `sudo touch + chown`.

## 2. 어떻게 수정했나 (plan 20260503 P0)

### 2.1 Rate Limit 안정성 (`upbit_client.py`)
- 모듈 레벨 싱글톤 `_EXCHANGE_INSTANCE` 도입 → ccxt 내부 throttle 누적 보존
- `_retry_on_429()` wrapper: 1s → 4s → 16s 지수 백오프, Retry-After 헤더 우선, 30s 한도, max_retries=3
- 모든 ccxt 호출(`fetch_balance`, `fetch_ticker`, `fetch_tickers`, `load_markets`, 매수/매도 주문)을 wrapper로 통과
- `RateLimitExhausted` 예외 정의 + `get_balance()` 내 raise (silent KRW+BTC만 산출 금지)
- `_save_last_known_balance()` / `load_last_known_balance(max_age_hours=24)` — CB fallback용 캐시

### 2.2 CB fallback 정책 변경 (`realtime_monitor.py`)
- "[서킷브레이커] 잔고 조회 실패, 매수 진행" 로직 제거
- 새 정책: 잔고 조회 실패 시 **매수 차단** + WARN 텔레그램 (1h 디바운스, `/tmp/bata_balance_fail_alert_flag`)
- `RateLimitExhausted`와 일반 `Exception` 분리 처리
- 첫 번째 잔고 조회(`_execute_buy:1460`) + 두 번째(`_execute_buy:1574`) 모두 동일 정책
- `_check_circuit_breaker_periodic`도 같은 알람 정책 적용
- 결정론적 평가식(`max(last_known, 0.7 * CB_INITIAL)`)은 안전 측 정책으로 단순화 — 항상 매수 차단

### 2.3 헬스체크 신뢰성 (`services/healthcheck/runner.py`)
- `check_jarvis_cron`: `workspace/jarvis_log.jsonl`(매매 시만 기록) → `/var/log/jarvis_executor.log` mtime(cron 실행 흔적) 1순위
- `check_daily_live`: 09:05 KST 이전엔 어제 날짜로 체크
- `check_balance_fetch` 신규 추가 — 인증 OK인데 잔고만 429 케이스 별도 탐지 (영구 차단 트랩 조기 발견)
- `critical_healthcheck.py`가 `check_balance_fetch`를 인증·jarvis와 함께 매시 5분 점검

### 2.4 정기 분석 18시 통합 (`realtime_monitor.py` + `daily_report.py`)
- `_send_periodic_report`의 `await send(msg)` 비활성화 (4h 6회 → 0회)
- `daily_report.py`에 백테스트 대비 + 체크포인트 인라인 추가
- 함수 추출(`services/reporting/periodic_analysis.py`)은 P2 plan으로 이관 (현재 분산 상태 인지 + 동기화 의무 plan 회고에 명시)

### 2.5 silent fail 자동 검증 (`pre_deploy_check.py`)
- `check_deploy_log_files` 룰: deploy_to_aws.sh의 LOG_FILES 배열과 모든 cron의 `>> /var/log/X.log` redirect 경로를 차집합으로 검증
- 다음 plan에서 신규 cron 추가 시 LOG_FILES 누락이 자동 차단됨

## 3. 검증규칙 (pre_deploy_check.py 코드)

```python
def check_deploy_log_files():
    """LOG_FILES 배열에 cron redirect 경로 모두 포함되었는지 검증."""
    log_files = set(re.findall(r"/var/log/[\w.]+\.log", LOG_FILES_section))
    cron_logs = set(re.findall(r">>\s+(/var/log/[\w.]+\.log)", content))
    missing = cron_logs - log_files
    if missing:
        errors.append(f"[배포로그] 누락: {sorted(missing)} (lessons #18)")
```

## 4. 교훈

1. **`enableRateLimit=True`만으로는 부족** — ccxt의 내장 throttle은 **인스턴스 수명 동안만** 호출 빈도를 추적한다. `_create_exchange()`를 매번 호출하면 매번 리셋되어 의미 없음. 모듈 레벨 싱글톤 + 명시적 `_retry_on_429` 백오프 둘 다 필요.

2. **안전장치를 우회하는 fallback은 안전장치가 아니다** — "잔고 조회 실패 → 매수 진행"은 코드 작성 시점엔 "관대한 정책"으로 보이지만, 실제론 안전장치 자체를 무력화. 항상 **fail-closed**(차단) + 즉시 알람으로 사용자가 인지하도록.

3. **헬스체크의 판정 기준은 "정상 동작 시에도 항상 갱신되는 것"이어야 함** — `jarvis_log.jsonl`은 매매·오류 시만 기록되므로 "최근 무로그"가 정상일 수 있다. cron 실행 흔적은 `/var/log/X.log` mtime이 더 신뢰할 만함.

4. **헬스체크는 사고를 잡으려고 만들었지만 그 자체가 사고를 만들 수 있다** — false alarm은 단순 노이즈가 아니라 사용자가 진짜 critical을 무시하게 만드는 신뢰 손실 사고. 첫 알람부터 정확해야 함.

5. **plan-구현-검증 사이클에 "직전 plan의 잔여 결함" 주기 점검 필요** — 직전 plan 적용 직후 24시간 이내가 가장 취약. 이번에도 직전 plan(20260502) 적용 24h 이내 5개 이슈 통합 발견.

6. **함수 추출을 미루면 lessons #19(config 자체정의) 패턴이 재발** — 정기 분석 메시지 빌더가 realtime_monitor와 daily_report 두 곳에 분산. P2에서 반드시 추출.

## 5. 미해결 / 후속 (P2 plan으로 이관)

- AC10 결정론적 평가식: 현재 안전 측 단순화(항상 차단). "캐시 신뢰 → 매수 진행" 정책 도입 시 산식 코드화 필요
- AC16 `services/reporting/periodic_analysis.py` 함수 추출
- multi_trader.py 4곳 + `_hourly_sync` + `_execute_sell`의 `_create_exchange` 직접 호출 → `_retry_on_429` 미경유. wrapper 함수로 경로 통일
- `balance_fetch_fail` 카운터 daily_report 필터 통계 섹션에 표시
- `_retry_on_429` NetworkError 첫 실패 throttled_print 추가
- realtime_monitor 내부 다른 텔레그램 호출(매수/매도/CB 발동/VB 회전 등) 전수 정리
