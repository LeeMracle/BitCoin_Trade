# #23 — P4 알림 등급 마이그레이션 + hourly_digest 침묵 cron + 매수 wrapper 보류

- **작업일**: 2026-05-03 15:30~15:35 KST
- **plan**: [20260503_4](../../workspace/plans/20260503_4_p4_alert_migration_and_digest_cron.md)
- **선행**: [#22 P3](20260503_2_p3_wrapper_alert_levels_function_unification.md)

## 1. 처리 내역

### P4-1 — watchdog/log_volume 알림 등급 분리
- `watchdog_check.sh`: `send` → `send_critical` (heartbeat 미갱신은 critical)
- `log_volume_check.sh`: `send` → `send_critical` (이상 감지 시만, 정상 침묵은 P1에서 적용됨)

### P4-2 — hourly_digest 침묵 모드 + cron 등록
- 발송 조건 OR: critical FAIL 1건 / 매매 5건 이상 / regime 전환
- 그 외 침묵 — 텔레그램 노이즈 0
- **heartbeat 파일** `/tmp/bata_hourly_digest_heartbeat` (침묵 여부 무관) — digest 죽음 자동 감지
- 매시 30분 cron 등록 (deploy_to_aws.sh + 직접 crontab)
- pre_deploy_check `check_hourly_digest_cron` 룰 신설 (lessons #9 자동 검증)
- 헬스체크 신규 항목 `check_digest_heartbeat` (>90분 WARN, >2h FAIL)

### P4-3 — multi_trader 매수 wrapper 보류 결정
- **적용 안 함**. 매수 주문에 retry/backoff 추가 시 **중복 주문 위험** (429 응답이 와도 실제 주문은 처리되었을 수 있고, retry로 또 주문되면 두 번 매수)
- lessons #11 "안전장치 우회 금지" 원칙과 동일 맥락
- 매수 경로는 ccxt 내부 throttle + 단발 호출로 유지

## 2. 교훈

1. **침묵 모드는 heartbeat와 짝**. 텔레그램 발송 안 한다고 해서 cron 자체가 죽었는지 알 수 없으면 결국 더 큰 사고. heartbeat는 침묵·발송 무관 항상 갱신
2. **Retry/backoff 적용은 idempotent 호출만 안전**. 잔고 조회·시세 조회는 OK, 주문은 절대 NO. 같은 retry라도 호출 종류별 정책 분리 필수
3. **신규 cron 등록은 항상 pre_deploy_check 검증 추가와 짝** (lessons #9). 이번에도 cto 1차에서 잡혀 즉시 보강

## 3. 미해결 / 후속

- jarvis_executor 매시 매매 알림과 hourly_digest 매매 N건 요약 — 사용자 입장에서 즉시 알림 + 30분 후 다이제스트가 중복 인식될 수 있으나, 1주 운영 후 재평가
- 매수 경로 Rate Limit 보호는 ccxt 내부 throttle에 의존 (싱글톤 + enableRateLimit으로 간접 보호). 직접 제어 필요시 별도 plan으로 idempotency 검증 후 도입
