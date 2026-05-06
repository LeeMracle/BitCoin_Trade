# P3 — 백오프 wrapper 통일 + 알림 3등급 + 매시 30분 다이제스트 + 함수 호출 통일

- **작성일(KST)**: 2026-05-03 15:05
- **세션**: 자비스 (Auto mode)
- **예상 소요**: 4~6시간
- **선행 plan**: [20260503_1](20260503_1_reporting_v2_and_rate_limit.md), [20260503_2](20260503_2_enable_trading_in_bear.md)

## 1. 목표

직전 plan들에서 P2/P3로 이관된 4건을 점진적·안전하게 처리.

## 2. 성공기준 (Acceptance Criteria)

### P3-1 (작음 — lessons #19 완전 해소)
- [ ] `realtime_monitor._send_periodic_report`가 `build_strategy_summary`/`check_consec_loss`/`build_market_snapshot` 호출
- [ ] 인라인 산식(체크포인트/연속손실/시장조회) 모두 제거
- [ ] **5연패 자동 중단 회귀 검증** — `check_consec_loss`로 분기 보존, dummy state로 5연패 시뮬 단위 테스트 (cto #3)
- [ ] 기존 출력과 동일 (메시지 길이 ±10% 이내)

### P3-2 (중간 — Rate Limit 백오프 확대)
- [ ] `services/execution/upbit_client.py`에 `with_retry(callable, max_retries=N)` 공용 wrapper 노출
- [ ] `multi_trader.py` 4곳 + `realtime_monitor._hourly_sync`가 wrapper 경유 (max_retries=3 = 기본)
- [ ] **`_execute_sell`은 적용 제외** — 손절 지연 위험 (lessons #3 위배 가능, cto #2). 별도 wrapper 호출 안 함, 기존 동작 유지
- [ ] py_compile + dry-run import 검증

### P3-3 (중간-큼 — 알림 3등급, 점진)
- [ ] `notifier.py`에 `send(message, parse_mode=None, level="report")` 통합 + `send_critical/send_report/send_silent` 헬퍼
- [ ] 기존 `send(msg)` 호출 호환 유지 (default level="report")
- [ ] critical_healthcheck.py + balance_fail 알람을 `send_critical`로 마이그레이션
- [ ] 텔레그램에 등급 prefix(🚨 critical / 📋 report / 무 silent) 추가
- [ ] **send_critical 실패 시 journalctl 강제 기록** (cto #4) — 텔레그램 실패해도 사후 추적 가능

### P3-4 (축소 — skeleton만, cron 미등록)
- [ ] `scripts/hourly_digest.py` skeleton 신규 (실행 가능하나 cron 등록 안 함)
- [ ] **기존 jarvis/regime cron 유지, hourly_digest cron 등록 안 함** (cto #1 — 중복 발송 위험)
- [ ] 향후 jarvis 통합 마이그레이션은 별도 plan(`20260504_2` 가칭)에서 동일 거래 회피 로직 + 단계적 비활성화 설계 후 진행

## 3. 단계

1. 본 plan 작성 + cto 1차 검증
2. P3-1: 함수 통일 (작음, 안전)
3. P3-2: wrapper 통일 (중간)
4. P3-3: 알림 등급 — 헬퍼만 추가, 호환 유지
5. P3-4: hourly_digest 신규
6. cto 2차 검증 + pre_deploy_check
7. 서버 배포 + btc-trader 재시작 + cron 등록
8. 운영 모니터링 (1h 후 hourly_digest 첫 실행 확인)
9. lessons #22 작성 + 텔레그램 보고

## 4. 리스크 & 사전 확인

| 리스크 | 완화 |
|---|---|
| _send_periodic_report 동작 변경 → 5연패 자동 중단 누락 | check_consec_loss 결과로 별도 자동 중단 분기 보존 |
| with_retry 적용 후 ccxt 인스턴스 호환성 | 같은 _create_exchange 싱글톤 사용 → 영향 없음 |
| notifier level 변경이 기존 호출 영향 | default="report" 유지 → 기존 호출 동작 100% 동일 |
| hourly_digest가 기존 jarvis와 중복 발송 | 이번 plan은 hourly_digest 추가만, jarvis 비활성화는 별도 |

## 5. 검증 주체

- [ ] B (cto) — 1차 plan, 2차 구현
- [ ] C (pre_deploy_check)
- [ ] A (별도 세션, 24h 후 회고)

## 6. 회고 (작성 예정)
