# P4 — 알림 등급 마이그레이션 + hourly_digest cron + 매수 wrapper 보류 결정

- **작성일(KST)**: 2026-05-03 15:30
- **세션**: 자비스 (Auto mode)
- **예상 소요**: 1.5~2시간
- **선행**: [20260503_3 P3](20260503_3_p3_remaining_works.md)

## 1. 목표

P3에서 이월된 3건을 안전 우선으로 처리. 위험 큰 작업은 보류 결정 + 명시.

## 2. 성공기준 (Acceptance)

### P4-1 — watchdog + log_volume 등급 분리
- [ ] `watchdog_check.sh`의 텔레그램 알림 → `send_critical` 사용 (heartbeat 미갱신은 critical)
- [ ] `log_volume_check.sh` 이상 감지 → `send_critical` 사용 (정상 시 침묵은 P1에서 이미 적용됨)
- [ ] bash에서 send_critical 호출 패턴 통일 (인라인 python -c 또는 helper)

### P4-2 — hourly_digest 침묵 모드 + cron 등록 (cto 1차 우려 회피)
- [ ] `hourly_digest.py`에 침묵 모드 추가: 매매 없음 + critical 정상 + regime 변화 없음 → 텔레그램 발송 안 함
- [ ] 발송 조건: critical FAIL 1건 이상 OR 직전 1h 매매 5건 이상 (요약 가치) OR regime 전환 발생
- [ ] **heartbeat 파일 touch (침묵 여부 무관) — `/tmp/bata_hourly_digest_heartbeat` (cto 1차 #1)** — digest 자체 죽음 감지용
- [ ] 매시 30분 cron 등록 (`30 * * * *`)
- [ ] `deploy_to_aws.sh` LOG_FILES + cron 라인 추가
- [ ] **`pre_deploy_check.py`에 `check_hourly_digest_cron` 룰 추가 (cto 1차 #2)** — lessons #9 위반 방지
- [ ] **기존 jarvis 매시 정각 + regime 매시 25분 cron 유지** (매매·전환 즉시 알림 보장)
- [ ] **헬스체크 신규 항목: `check_digest_heartbeat`** — heartbeat mtime 1.5h 이상이면 WARN, 2h 이상이면 FAIL (digest 죽음 자동 감지)

### P4-3 — multi_trader 매수 wrapper 보류 결정 (안전 우선)
- [x] 결정: **적용 안 함**. 매수 주문에 retry/backoff 추가 시 **중복 주문 위험** (429 응답 후 실제 주문은 처리되었는데 retry로 또 주문 가능). plan 회고 + lessons #23에 결정 명시

## 3. 단계

1. plan + cto 1차 (이 단계)
2. P4-1 watchdog/log_volume 수정
3. P4-2 hourly_digest 침묵 모드 + cron 등록
4. cto 2차 + pre_deploy_check
5. 서버 배포 + crontab 갱신 + 1h 후 첫 hourly_digest 실측
6. lessons #23 + 텔레그램 보고

## 4. 리스크

| 리스크 | 완화 |
|---|---|
| hourly_digest 침묵 조건이 너무 엄격 → 진짜 critical도 묻힘 | critical FAIL 1건만 있어도 발송, 첫 1주 모니터링 |
| watchdog 알림 형식 변경 → 기존 알림 누락 | bash 스크립트만 수정, python notifier 호출은 P3에서 검증된 send_critical |
| cron 등록 실패 (`deploy_to_aws.sh` 미반영) | crontab 수동 등록 + pre_deploy_check `check_deploy_log_files` |

## 5. 검증

- [ ] B (cto) 1·2차
- [ ] C (pre_deploy_check)
- [ ] A (24h 후 회고)

## 6. 회고 (작업 후 작성)
