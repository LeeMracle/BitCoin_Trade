# #22 — P3 통합 처리: wrapper 통일 + 알림 3등급 + 함수 통일 + 다이제스트 skeleton

- **작업일**: 2026-05-03 15:00~15:20 KST
- **관련 plan**: [20260503_3_p3_remaining_works.md](../../workspace/plans/20260503_3_p3_remaining_works.md)
- **선행 lessons**: [#19 config 자체정의](20260425_2_config_constant_self_definition.md), [#20 키-IP](20260502_1_upbit_keyset_ip_mapping.md), [#21 Rate Limit](20260503_1_rate_limit_cb_fallback_healthcheck_loop.md)

## 1. 처리한 4건

### P3-1 — `_send_periodic_report` 함수 통일 (lessons #19 완전 해소)
- realtime_monitor.py의 인라인 산식(체크포인트/연속손실/시장조회) 모두 제거
- `services/reporting/periodic_analysis.py` 함수 호출로 통일
- 5연패 자동 중단은 `check_consec_loss(state)` 결과로 보존 (회귀 단위 테스트 통과)

### P3-2 — `with_retry` wrapper 노출
- `upbit_client.py`에 `with_retry(callable, ...)` 공용 함수 추가 (`_retry_on_429` alias)
- realtime_monitor `_hourly_sync` 2곳에 적용 (fetch_balance, fetch_tickers)
- **매수/매도 즉시성 경로(multi_trader, _execute_sell)는 적용 제외** — lessons #3 (안전장치 즉시 체크) 보호

### P3-3 — 알림 3등급 (`level=critical/report/silent`)
- `notifier.send(message, parse_mode=None, level="report")` 통합
- `send_critical / send_report / send_silent` 헬퍼 추가
- 등급 prefix: 🚨 / 📋 / 무
- **critical 실패 시 stderr/journalctl 강제 기록** — 텔레그램 실패해도 사후 추적 가능
- 기존 `send(msg)` 호출 호환 100% (default level="report")

### P3-4 — `hourly_digest.py` skeleton (cron 미등록)
- 매시 jarvis 매매 + regime + critical 상태 통합 메시지 빌더
- cron 미등록 — jarvis(매시 정각)/regime(매시 25분)와 중복 발송 위험 (cto 우려)
- 향후 통합 마이그레이션은 별도 plan에서 동일 거래 회피 로직 + 단계적 비활성화 설계

## 2. 교훈

1. **함수 추출은 코드 한 번에 다 옮기는 게 아니라 "산식 함수"부터 분리** — daily_report에 먼저 적용 후 realtime_monitor에 확장. 두 단계로 나누면 회귀 위험 ↓
2. **wrapper 일괄 적용 금지** — 매수/매도 즉시성 경로엔 retry/backoff 추가 시 lessons #3 위배. "조회 vs 주문" 경로 분리 정책 명시
3. **알림 등급은 default 호환 유지로 점진 마이그레이션** — 시그니처 추가 인자는 기본값 처리. 한꺼번에 모든 호출처 변경하면 회귀 위험
4. **신규 cron 추가 시 기존 cron과 중복 발송 위험 사전 점검** — hourly_digest 같은 통합 알림은 기존 알림 비활성화 + 동일 거래 회피 로직 없이 추가하면 노이즈 2~3배 증가
5. **cto 1차에서 위험 식별 → plan 즉시 축소가 가장 효과적** — 본 작업은 cto 1차에서 "P3-4 hourly_digest 중복 발송 HIGH"를 잡았고 즉시 plan 축소(skeleton만, cron 미등록)로 사고 회피

## 3. 미해결 / 후속 (P4 plan)

- multi_trader.py 4곳은 wrapper 미적용 — 매수/매도 경로 분리 정책 + 부분 적용 검토
- hourly_digest cron 등록 + 기존 jarvis/regime cron 비활성화 (동일 거래 회피 로직 추가 후)
- `send_critical` 마이그레이션 확대 — watchdog_check.sh, log_volume_check.sh 등 다른 알림 경로
