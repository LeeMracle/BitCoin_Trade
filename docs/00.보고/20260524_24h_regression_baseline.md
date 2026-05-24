# 24h 회귀 감시 베이스라인 (2026-05-24 21:43 KST)

## 위임 컨텍스트

- **위임자**: bata-pm (액션 D)
- **위임 시각**: 2026-05-24 21:40 KST
- **사유**: engineer가 P1 결함 3건 4단계 close 완료 → 24h 회귀 감시
- **만료 시점**: 2026-05-25 21:40 KST
- **close 판정 보고**: 2026-05-25 22:00 KST 예정
- **중간 보고**: 2026-05-25 09:05 KST 일일 시작 브리핑

## 베이스라인 측정 결과 (5/24 21:43 KST)

### [1] /tmp/bata_heartbeat mtime — PASS

- mtime: **2026-05-24 21:41:59 KST** (경과 83초)
- 정상기준: 2분 이내 → **합격**
- realtime_monitor cycle 정상 (재시작 후 약 3분 경과 시점)

### [2] BitCoin_Trade cron 등록 — PASS

- crontab 라인: **정확히 9개** (정상기준 = 9)
- 재등록 확인된 cron 9건:
  1. `5 0 * * *` daily_live.py (KST 09:05)
  2. `0 9 * * *` daily_report.py (KST 18:00)
  3. `* * * * *` watchdog_check.sh (매분)
  4. `10 0 * * *` log_volume_check.sh (KST 09:10)
  5. `15 0 * * *` vb_recheck_trigger.py (KST 09:15)
  6. `30 0 * * *` regime_check.py (KST 09:30)
  7. `5 * * * *` critical_healthcheck.py (매시 KST 05분)
  8. `0 18 * * *` ml_outcome_match.py (KST 03:00, 결함3 핵심 복구)
  9. `0 19 * * 0` ml_weekly_review.py (일요일 KST 04:00)

### [3] state ↔ balance 미러 (결함2 수정 검증) — PASS

`workspace/multi_trading_state.json` 측정:

| symbol | entry_qty | current_qty | entry_price | entry_amount_krw |
|--------|-----------|-------------|-------------|------------------|
| SUN/KRW | **943.9829587** | None | 28.5 | None |
| MTL/KRW | **46.84087413** | None | 468.0 | 43,843.06 |

- MTL entry_qty: 0 → **46.84087413 복원 완료** (engineer 보고 일치)
- SUN entry_qty: 943.98 정상 양수
- `current_qty=None`은 정상 (런타임에 거래소 잔고로 동적 결정 — invariant 가드는 entry_qty)

### [4] 좀비 daily_live — PASS

- daily_live.py 프로세스: **1개**
- PID 500240, cwd=`/home/ubuntu/BitCoin_Trade`, etime 06:44 (재시작 후 약 7분)
- systemd unit btc-trader.service 단독 가동 (cron 직접 호출 없음, lessons #24 준수)

### [5] ml_outcome cron — 관찰 (베이스라인)

- `/var/log/ml_outcome.log` 존재 (0 byte, 5/24 12:34 UTC = 재시작 시점)
- 다음 실행 예정: **5/25 03:00 KST (18:00 UTC)**
- 24h 회귀 중 1회 실행 흔적 필수 (정상기준)

### [6] 신규 매수 발생 — 관찰

- BEAR 횡보 (F&G 25 Extreme Fear), BTC<EMA200 (115M vs 121M)
- 5연패 cooldown 활성: **24.7h 남음** (consec_loss_alerted_until=1779715251 → 5/25 22:00 KST 부근)
- 신규 매수 **0건이 정상**
- 매수 발생 시: expert 인입 + 즉시 PM 보고

### [7] false alarm 감시 — 관찰

- 최근 closed_trades 5건 모두 -8~-10% 손실 (하드손절 캡 작동, lessons #13 준수)
- 동일 알람 3회+ 반복 시 디바운스 버그 의심 → 즉시 트리아지

### 추가 검증: 봇 가동 상태 — PASS

- btc-trader.service: Active (running) since 2026-05-24 12:40:02 UTC
- Memory: 159.8M / Peak 177.4M (t3.micro 한계 내, lessons #5)
- 174개 종목 웹소켓 구독 완료
- 봇 시작 시 `[5연패] cooldown 활성 (24.7h 남음) — 알람 skip` 로그 = lessons #30 디바운스 정상

## 9분 cycle 모니터링 계획 (5/24 21:43 → 5/25 21:40)

### 측정 주기

- **9분 cycle (자동)**: `monitor` skill 또는 수동 SSH 점검
- **매시 정시 KST**: 7개 회귀 항목 일괄 측정 (24회 측정)
- **결정적 시점**:
  - 5/24 22:00, 23:00 KST — 봇 첫 evening cycle
  - 5/25 03:00 KST — ml_outcome cron 실행 흔적 확인
  - 5/25 09:05 KST — daily_check.py + 일일 시작 브리핑
  - 5/25 09:10 KST — log_volume_check.sh
  - 5/25 18:00 KST — daily_report.py
  - 5/25 22:00 KST — 5연패 cooldown 만료 → 신규 매수 가능 시점
  - 5/25 21:40 KST — 24h 만료, close 판정

### 즉시 알림 트리거 (PM 보고 필수)

| 지표 | 임계 | 액션 |
|------|------|------|
| heartbeat mtime > 2분 | 1회 | engineer 즉시 알림 |
| cron 카운트 ≠ 9 | 즉시 | P1 escalation |
| state entry_qty=0 또는 None 재발 | 1회 | engineer 즉시 알림 (결함2 회귀) |
| 좀비 daily_live ≥ 2개 | 1회 | engineer 알림 + 1차 재시작 시도 |
| 동일 알람 3회 반복 | 3회째 | 디바운스 버그 의심, engineer 트리아지 |
| ml_outcome 5/25 03:00 미실행 | 5/25 04:00까지 미실행 시 | engineer 알림 |

### 정상 운영 시 보고

- 5/25 09:05 KST 일일 시작 브리핑 (24h 회귀 결과 종합)
- 5/25 21:40 KST close 판정 raw data

## 자기평가 금지 준수

- lessons #32 회귀 여부 자체는 별도 cto review 또는 pre_deploy_check 결과 인용으로 판정
- operator는 측정값만 수집·보고, "이상 없음" 판정은 PM이 종합 후 결정
- 이상 발견 시: 사실 수집 → 분류 → 라우팅 권고 (operator 자체 판정 X)

## 참조

- engineer 산출: lessons #32, pre_deploy_check 신규 룰 3개
- 결함2 수정 코드: realtime_monitor.py:1969 4단 fallback + invariant 가드
- 결함3 cron 재등록: deploy_to_aws.sh 9개 일괄 등록
- ADR 20260524_1 (1주 추가 관찰 권고)
