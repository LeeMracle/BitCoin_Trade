# WBS — Bitcoin Auto-Trading

> 갱신: 2026-05-05 (W19, ML LIVE 가속 도입 P8-27 — threshold 0.45 보수 시작)

## 진행현황 요약

| 구분                    | 대기 | 진행 | 완료 | 합계 |
| ----------------------- | ---- | ---- | ---- | ---- |
| Phase 0 환경설정        | 0    | 0    | 2    | 2    |
| Phase 1 구조+데이터     | 0    | 0    | 3    | 3    |
| Phase 2 백테스트+전략   | 0    | 0    | 4    | 4    |
| Phase 3 페이퍼트레이딩  | 0    | 0    | 3    | 3    |
| Phase 4 실전거래        | 0    | 0    | 21   | 21   |
| Phase 5 전략고도화      | 2    | 0    | 30   | 32   |
| Phase 6 코드품질/린트   | 0    | 0    | 12   | 12   |
| Phase 7 운영모니터링    | 0    | 0    | 11   | 11   |
| **Phase 8 ML 신호 필터**| **3**| **0**| **25**| **28**|
| **합계**                | **5**| **0**| **111**| **116**|

> P5-28b는 "조건부 완료"(거래 0건 샘플 부족, 상승장 복귀 시 재집계). P5-02/P5-03은 "리서치 완료, 실행 대기"(BTC 상승장 복귀 + regime BULL 지속 시 착수). Phase 6 전체 완료.
> Phase 8: 학습/추론/배포까지 완료 (Shadow Mode 활성), 4건 대기 = OHLCV 주입 / v3 모델 개선 / outcome idempotent fix / 3개월 누적 후 A/B 결정.

## 주간 마일스톤

> 갱신: 2026-05-04 (W19 월요일)

### 이번 주 (05-04 ~ 05-10, W19)

| 목표 | WBS ID | 상태 | 비고 |
|------|--------|------|------|
| **ML 신호 필터 시스템 신설 + Shadow Mode 활성** | **P8-01~22** | **완료** | 23 features, v2_real (CV mean AUC 0.553), AWS 배포, fail-open 정책, shadow JSONL 누적 시작 |
| pdca-qa 교차검증 + lessons #26 신설 | P8-08, P8-09 | **완료** | realtime_monitor ML hook 누락 발견·수정, MAX_POSITIONS 자체정의 제거 |
| outcome 매칭 cron 등록 (KST 03:00) | P8-21 | **완료** | shadow 의사결정의 24h 후 도달 여부 자동 기록 (3개월 평가 데이터 수집 시작) |
| daily_report ML 섹션 + pre_deploy_check 메모리 룰 | P8-18, P8-19 | **완료** | t3.micro RAM 압박 (free 92MB) 모니터링 가시화 |
| OHLCV 주입 + ML_SHADOW_MODE 가드 (P8-23) | P8-23 | **완료** | 05-05 inference.py에 ccxt 자동 fetch + 60s LRU 캐시. BTC 실 score 0.17 검증 |
| **ML LIVE 가속 도입 (threshold 0.45)** | **P8-27** | **완료** | 05-05 ADR 20260505-2. OOS 89일 시뮬 PF 1.04 / 1.17 입증 → 0.45 보수 시작, 1주 후 0.50 강화 |
| v3 모델 개선 (timeframe 정합/도미넌스/cap rank) | P8-24 | 대기 | 1개월+ 후 |
| **DC15→12 + ATR 12→10% + VOL 1.2→1.0 동시 튜닝** | **P5-30** | **완료** | 05-05 ADR 20260505-1, 매매 0건 30h 후 적극화 + ATR 안전 복귀, 1주 후 평가 |
| **ML LIVE 가속 도입 (threshold 0.45)** | **P8-27** | **완료** | 05-05 ADR 20260505-2. OOS 89일 시뮬 PF 1.04 (0.45) / 1.17 (0.55) 입증 → 0.45 보수 시작, ML_SHADOW_MODE=0 활성, 5-12 평가 |
| **신호 발화 dedupe (봉 ID + 60s)** | C-FIX | **완료** | 05-05 lessons #1 부분 준수, ORDER/KRW 16,484회 폭주 차단 |
| **시작 시 레벨 갱신 알림 정책 fix** | P-FIX2 | **완료** | 05-05 첫 1~2회 실패 알림 X (3회 시 알림), 메시지 truncate, 100회 시 critical |
| **[쿨다운] 매수 스킵 throttle 60s** | P-FIX | **완료** | 05-05 lessons #14 정신, 봇 만성 재시작 완화 시도 (실효는 24h 후 검증) |
| ticker `markets=` 배치 분할 (근본 fix) | P-FIX3 | 후속 | URL 너무 김 + 429 — 50개 × 4 batch 분할, 별도 PR로 분리 |

### 지난 주 (04-27 ~ 05-03, W18)

| 목표 | WBS ID | 상태 | 비고 |
|------|--------|------|------|
| 좀비 프로세스 + crontab 회귀 + .bak 디렉터리 격리 | (운영변경) | **완료** | lessons #24, daily_live.py --realtime systemd 단독 가동, /proc/<PID>/cwd 검증 |
| Rate limit 백오프 + CB fallback + 헬스체크 루프 | (P3-운영) | **완료** | lessons #21, ccxt 싱글톤 + fail-closed |
| Wrapper retry 일괄 적용 금지 + 알림 등급 + 함수 통합 | (P3-운영) | **완료** | lessons #22, 매수/매도 즉시 경로 제외 |
| Cron heartbeat 짝 + retry 주문 적용 금지 + cron 등록 검증 | (P3-운영) | **완료** | lessons #23 |
| 멀티 API 키↔환경 매핑 + 매시 critical 헬스체크 | (운영변경) | **완료** | lessons #20, plan 20260502 P0 |
| 부분 익절/거래량 필터/일일 손실 한도 | (P3-전략) | **완료** | lessons #25 |

### 04-20 ~ 04-26 (W17)

| 목표 | WBS ID | 상태 | 비고 |
|------|--------|------|------|
| 주말(04-19~20) 서버 상태 점검 | (운영) | **완료** | 04-21~25 매일 cto health PASS, 다중 프로젝트 동거 정상 |
| 필터 통계 3일치 확인 (filter_stats.json) | (운영) | **완료** | 04-25 누적 W17 평균 약 76k/일, ema200_filter 단일 차단 패턴 확인 |
| 레짐 상태 확인 (regime_state.json) | (운영) | **완료** | 04-25 stale 7일 → crontab `.venv` 경로 회귀 복구, BEAR 유지(lessons #18) |
| P5-02/03 트리거 조건 점검 | P5-02/03 | 대기 | BEAR 지속, BULL 레짐 7일 유지 시 자동 착수 |
| lint_history --weekly 주간 집계 | P6-13 후속 | 진행 | 04-26(일) 본실행 예정 |
| VB 샘플 수집 모니터링 | P5-28b 후속 | 조건부 | 수동 운영 유지, BULL 미감지 시 no-op 정상 |
| P5-04 LIVE 승격 ADR 초안 | P5-04 후속 | 스트레치 | 현재 DRY-RUN(enabled=false), 04-26~W18 이월 |
| 잔고 로그 throttle 회귀 점검 | - | **완료** | 04-23/24/25 폭발 패턴 없음 |
| 신규매수 게이트 합리성 백테스트 | (신규) | **완료** | 04-25 6 시나리오 비교, 현행 S1 EMA200 1위(OOS Sharpe 1.258), 유지 권장 |
| crontab venv 경로 회귀 복구 + lessons #18 | (운영변경) | **완료** | 04-25 `.venv` 1글자 회귀 → lessons/20260425_1, CLAUDE.md 교훈 #18 추가 |
| **DC 기간 단축 (DC20→15) 백테스트 + ADR + 배포** | **P5-29** | **완료** | 04-26 단일 BTC OOS Sharpe 1.140→1.375, 멀티 6코인 평균 0.624→0.803, 200EMA 유지, ADR 20260426-1, cto gate PASS, AWS 배포 완료 |

### 지난 주 (04-13 ~ 04-19, W16)

| 목표 | WBS ID | 상태 | 비고 |
|------|--------|------|------|
| **모니터링 1계층 — Heartbeat watchdog** | P7-03/04/05 | **완료** | 04-17 heartbeat 120s + Type=notify + WatchdogUSec=5min + cron 1분 |
| **모니터링 2계층 — Sanity check** | P7-06/07/08 | **완료** | 04-17 `_hourly_sync` + WS 5분 무수신 재연결 + log_volume cron |
| **jarvis cron 재발 방지** | P4-14c | **완료** | 04-17 deploy_to_aws.sh에 CRON_JARVIS + pre_deploy WARN→ERROR |
| **btc-trader 재시작 루프 해결** | P4-14d | **완료** | 04-17 heartbeat 120s + _refresh_levels 내부 ping, SIGABRT 재발 없음 |
| **린트 R6~R8 확장** | P6-09/10/11 | **완료** | 04-17 async await/fetch_balance/fetch_order 규칙 추가 |
| **모니터링 3계층 — Performance audit** | P7-09/10/11 | **완료** | 04-18 filter_stats + daily_report 필터 섹션 + CTO 재검증 |
| **VB 개선 재검증 (샘플 부족 판정)** | P5-28b | **완료(조건부)** | 04-18 거래 0건, A 필터 7일 연속 차단(의도대로) → CONDITIONAL, 상승장 복귀 후 재집계 |
| **잔고 로그 스팸 throttle** | (신규) | **완료** | 04-18 upbit_client 60초 throttle, log_volume 임계 보호 |
| **메타 린트 (lessons ↔ 규칙 매핑)** | P6-12 | **완료** | 04-18 scripts/lint_meta.py, 미연결 lesson 자동 경고 |
| **P5-04 레짐 자동 전환 (DRY-RUN)** | P5-04 | **완료** | 04-18 regime_switcher.py + regime_check.py, BULL/BEAR/SIDEWAYS 판정 + 히스테리시스 3회 |
| **lint_meta 미집행 6건 해소** | - | **완료** | 04-18 pre_deploy_check +6 함수 (검증 22~27), 17/17 매핑 |
| **P6-13 lint_history 누적** | P6-13 | **완료** | 04-18 주간 집계 + --weekly (원 목표 04-27 스트레치 선행 달성) |
| **VB 재집계 자동 트리거** | - | **완료** | 04-18 vb_recheck_trigger.py + cron 09:15 KST |
| **VB 최적화 / 알트 펌프 리서치** | P5-02/03 | **리서치 완료** | 04-18 실행 대기(상승장 복귀 후 자동 착수) |
| **rsync→tar 폴백 + 배포툴 의존성 체크** | - | **완료** | 04-19 lessons/20260419_1, pre_deploy_check에 rsync 감지 |

| 목표 | WBS ID | 상태 | 비고 |
|------|--------|------|------|
| 매매 LOG 분석 + CB 발동 사후분석 | - | **완료** | 04-08 (BATA -29% 회복 중, 285,510원) |
| BTC 분할매도 cron 누락 복구 | P4-14b | **완료** | 04-08 수동 실행 + cron 등록, TP1 체결 |
| jarvis NoneType 버그 수정 | P6-01 | **완료** | 04-08 _fmt_num/_resolve_fill 도입 |
| 린트 층 Phase 1 기반 구축 | P6-01 | **완료** | 04-08 lint_none_format.py + pre_deploy_check 통합 |
| 린트 층 Phase 2 공용 헬퍼 | P6-02 | **완료** | 04-08 services/common/ccxt_utils.py, WARN 28→1 |
| 린트 층 Phase 3 pre-commit hook | P6-04 | **완료** | 04-08 .githooks/pre-commit |
| 린트 층 문서화 | P6-03 | **완료** | 04-08 docs/lint_layer.md |
| **ONG -29% 원인 분석 + 하드 캡** | P4-16 | **완료** | 04-08 `HARD_STOP_LOSS_PCT=10%`, `MAX_ATR_PCT=8%` |
| **하락장 레짐 3중 방어** | P5-27 | **완료** | 04-08 BTC EMA200 + ATR% + 하드 캡 |
| **CB 기존 포지션 정책 ADR** | P4-17 | **완료** | 04-08 Option A 유지 + L2(-25%) 2차 안전장치 |
| **공용 헬퍼 단위 테스트** | P6-06 | **완료** | 04-08 16/16 PASS |
| **GitHub Actions CI** | P6-05 | **완료** | 04-08 lint/test/pre-deploy 3 job |
| **AWS 배포 + 서버 검증** | P4-20 | **완료** | 04-08 08:10 UTC 재시작, 3중 방어 작동 확인 |
| **GitHub origin master push** | - | **완료** | 04-08 c032a3c..59bde68 |
| lessons L9~L13 추가 | - | **완료** | 04-08 cron, state, CB, 린트, ATR 고변동 |
| **VB DRY-RUN 7일 검증 마감** | P4-05 | **완료** | 04-09 24건 집계, 승률 36.8%, 누적 +13.66%, 4 PASS/2 FAIL |
| **VB GO/NO-GO 판단** | P4-06 | **완료** | 04-09 **NO-GO (개선 후 재검증)** — 폐기 아님 |
| **CB L2 구현 (ADR 후속)** | P4-18 | **완료** | 04-09 L2/L1자동해제 + 전량청산 + 단위테스트 16/16 |
| CB L2 로컬 검증 | P4-19 | **완료** | 04-09 lint/test/pre_deploy GREEN (AWS scp는 이월) |
| **VB 개선 A~E 구현 + 배포** | P5-28 | **완료** | 04-09 16:50 KST, 25/25 테스트 PASS, 서버 반영 |
| VB 개선 재검증 (04-15 목표) | P5-28b | 대기 | DRY-RUN 로그 재집계 |
| **CB L2 AWS 배포** | P4-19b | **완료** | 04-09 16:45 KST 재시작 |
| **R4: subscript 린트 규칙** | P6-07 | **완료** | 04-10 WARN 99건 탐지, 57/57 테스트 PASS |
| **R5: strptime None 체크** | P6-08 | **완료** | 04-10 WARN 4건 탐지 |
| CB 로그 스팸 수정 (긴급) | - | **완료** | 04-10 6,420건/일 → 60초 throttle, L14 기록 |
| L8 알트 합산 수정 + CB L1 해제 | - | **완료** | 04-10 upbit_client 수정, 배포 완료 |
| get_balance 성능 핫픽스 (CTO) | P7-01 | **완료** | 04-10 fetch_tickers 일괄 + load_markets 필터 |
| 알트 조회 실패 로깅 (CTO) | P7-02 | **완료** | 04-10 except pass → 로깅 |
| 모니터링 Execution Plan | - | **완료** | 04-10 workspace/plans/ 계획서 |
| Heartbeat watchdog | P7-03~05 | 대기 | 04-11 목표 |
| State-Exchange 교차검증 + 웹소켓 | P7-06~08 | 대기 | 04-12~13 목표 |

### 다음 주 (05-11 ~ 05-17, W20)

| 목표 | WBS ID | 상태 | 조건/비고 |
|------|--------|------|----------|
| **ML LIVE 1주 평가 (PF/승률/차단률)** | **P8-27 후속** | **필수** | 5-12 평가 — PF≥1.0 → 0.50 강화, PF<0.95 → 0.40 완화 또는 SHADOW 복귀 |
| **P5-30 (DC12+ATR10%+VOL1.0) 평가** | P5-30 후속 | 필수 | 동일 5-12 평가 — 매매 빈도 + 손절 비율 |
| ML threshold 강화 (0.45→0.50) 또는 롤백 | P8-27 | 조건부 | 5-12 평가 결과 따라 |
| outcome JSONL idempotent fix | P8-25 | 대기 | 1주 운영 후 중복 라인 발생 패턴 확인 |
| P5-04 LIVE 승격 ADR | P5-04 후속 | 스트레치 | DRY-RUN 로그 1주 이상 누적 후 결정 |
| P5-02 VB 파라미터 최적화 착수 | P5-02 | 대기 | BULL 레짐 7일 유지 시 자동 착수 |
| P5-03 알트 펌프 서핑 재검토 | P5-03 | 대기 | BULL 레짐 7일 유지 시 자동 착수 |
| lint_meta 신규 lesson 즉시 매핑 운영 | - | 상시 | 신규 lesson 발생 시 즉시 규칙 매핑 |

## 상태 범례

- 대기: 선행 미완료 또는 미착수
- 진행: 작업 중
- **완료**: 완료됨
- ~~보류~~: 일시 중단

## Phase 0: 환경 설정

| ID    | 태스크                         | 담당   | 선행  | 상태     | 목표일 | 비고                          |
| ----- | ------------------------------ | ------ | ----- | -------- | ------ | ----------------------------- |
| P0-01 | 업비트 API 키 발급             | 사용자 | -     | **완료** | -      | ACCESS_KEY + SECRET_KEY       |
| P0-02 | AWS 서버 구축 + systemd 설정   | Claude | P0-01 | **완료** | -      | EC2 t3.micro, Ubuntu 24.04    |

## Phase 1: 프로젝트 구조 + 시장 데이터

| ID    | 태스크                              | 담당   | 선행  | 상태     | 목표일 | 비고                       |
| ----- | ----------------------------------- | ------ | ----- | -------- | ------ | -------------------------- |
| P1-01 | 프로젝트 구조 설계                  | Claude | P0-02 | **완료** | -      | services/, skills/ 구조    |
| P1-02 | 시장 데이터 어댑터 (ccxt + DuckDB)  | Claude | P1-01 | **완료** | -      | market_data/fetcher.py     |
| P1-03 | 텔레그램 봇 + 알림 시스템           | Claude | P1-01 | **완료** | -      | alerting/notifier.py       |

## Phase 2: 백테스트 + 전략 탐색

| ID    | 태스크                        | 담당   | 선행  | 상태     | 목표일 | 비고                                |
| ----- | ----------------------------- | ------ | ----- | -------- | ------ | ----------------------------------- |
| P2-01 | 백테스트 엔진                 | Claude | P1-02 | **완료** | -      | backtest/engine.py                  |
| P2-02 | F&G 역추세 전략 탐색          | Claude | P2-01 | **완료** | -      | 실패 — 구조적 한계                  |
| P2-03 | 추세추종 전략 탐색 (DC+ATR)   | Claude | P2-01 | **완료** | -      | OOS Sharpe 1.123, MDD -18.7%       |
| P2-04 | 일봉 전략 8종 구현            | Claude | P2-03 | **완료** | -      | strategies/advanced.py              |

## Phase 3: 페이퍼 트레이딩

| ID    | 태스크                             | 담당   | 선행  | 상태     | 목표일 | 비고                                     |
| ----- | ---------------------------------- | ------ | ----- | -------- | ------ | ---------------------------------------- |
| P3-01 | 페이퍼 트레이딩 러너               | Claude | P2-03 | **완료** | -      | paper_trading/runner.py                  |
| P3-02 | daytrading 실전 투입 + 중단        | Claude | P3-01 | **완료** | ~03-29 | 8건 1승7패 → 교훈 기록, composite 전환   |
| P3-03 | composite DC(20) 실전 전환         | Claude | P3-02 | **완료** | ~03-29 | 03-29 실전 전환, DRY_RUN=False           |

## Phase 4: 실전 거래 운영

| ID    | 태스크                                 | 담당   | 선행  | 상태     | 목표일      | 비고                                     |
| ----- | -------------------------------------- | ------ | ----- | -------- | ----------- | ---------------------------------------- |
| P4-01 | AWS 배포 + systemd 서비스              | Claude | P3-03 | **완료** | ~03-30      | btc-trader.service, deploy_to_aws.sh     |
| P4-02 | 검증 루프 (lessons + pre_deploy_check) | Claude | P4-01 | **완료** | ~03-31      | docs/lessons/ 5건, 자동 검증 스크립트    |
| P4-03 | 서버 다이어트 (디스크/메모리 최적화)   | Claude | P4-01 | **완료** | ~03-31      | 62%→57%, 스왑 512MB 추가                 |
| P4-04 | 단타 전략 3종 리서치 + 백테스트        | Claude | P4-02 | **완료** | ~03-31      | VB 유망, Pump 보류, Div 폐기             |
| P4-05 | VB(변동성돌파) DRY-RUN 검증            | Claude | P4-04 | **완료** | ~04-09      | 24건 집계, 승률 36.8%, 누적 +13.66%     |
| P4-06 | VB 실전 전환 판단                      | Claude | P4-05 | **완료** | ~04-09      | NO-GO (개선 후 재검증), 폐기 아님        |
| P4-07 | 일일 보고 자동화 (텔레그램)            | Claude | -     | **완료** | ~04-02      | scripts/daily_report.py + cron 등록      |
| P4-08 | WBS + 일일작업 워크플로우              | Claude | -     | **완료** | ~04-02      | daily-work 스킬 확정                      |
| P4-09 | 1시간 모니터링 보고 자동화             | Claude | P4-07 | **완료** | ~04-02      | hourly_monitor.py + /loop 1h 설정         |
| P4-10 | /monitor 스킬 + 스케줄러 구현          | Claude | P4-09 | **완료** | ~04-04      | monitor SKILL.md + scheduled_report.py    |
| P4-11 | 시장 분석 + 매매전략 점검              | Claude | -     | **완료** | ~04-04      | F&G, BTC 기술분석, 보유종목 점검, BCH 손절 |
| P4-12 | 전략 카탈로그 + 용어집 정리            | Claude | -     | **완료** | ~04-04      | 13개 전략 정리, 용어집 7개 추가           |
| P4-13 | 모니터링 보고 개선 (전체 자산 합산)    | Claude | P4-10 | **완료** | ~04-05      | 알트 평가액 포함, 현재가 기준 수익률, 교훈#8 |
| P4-14 | 자비스 실행기 구현 + AWS 배포          | Claude | -     | **완료** | ~04-05      | jarvis_executor.py, BTC 분할매도 LIVE 가동  |
| P4-15 | VB 극공포장 대응 (K값+SL 개선)         | Claude | -     | **완료** | ~04-05      | VB_K_CRISIS=0.85, SL 1.5%→2.0%             |
| P4-16 | ONG -29% 원인 분석 + 하드 캡            | Claude | -     | **완료** | ~04-08      | HARD_STOP_LOSS_PCT=10%, MAX_ATR_PCT=8%    |
| P4-17 | CB 기존 포지션 정책 ADR                 | Claude | -     | **완료** | ~04-08      | Option A 유지 + L2(-25%) 2차 안전장치      |
| P4-18 | CB L2 구현 (ADR 후속)                   | Claude | P4-17 | **완료** | ~04-09      | L2/L1자동해제 + 단위테스트 16/16           |
| P4-19 | CB L2 로컬 검증                         | Claude | P4-18 | **완료** | ~04-09      | lint/test/pre_deploy GREEN (`cedb6ef`)    |
| P4-19b| CB L2 AWS 서버 배포                     | Claude | P4-19 | **완료** | ~04-09      | 04-09 16:45 KST 재시작, 서버 32/32 PASS   |
| P4-20 | 3중 방어 AWS 배포 + 서버 검증            | Claude | P4-16 | **완료** | ~04-08      | 08:10 UTC 재시작, 3중 방어 작동 확인       |
| P4-14c| jarvis cron 재발 방지 + deploy 스크립트 반영 | Claude | P4-14b | **완료** | ~04-17   | deploy_to_aws.sh에 CRON_JARVIS 추가 + pre_deploy WARN→ERROR 승급 + 서버 cron 재등록(배포로) |
| P4-14d| btc-trader 재시작 루프 조사·해결         | Claude | -      | **완료** | ~04-17   | 원인: heartbeat 주기 300s==WatchdogSec + _refresh_levels 4분 블로킹 ping 공백. 조치: 120s + refresh 내부 ping, lessons/20260417_2 기록 |

## Phase 5: 전략 고도화

| ID    | 태스크                            | 담당   | 선행        | 상태     | 목표일      | 비고                            |
| ----- | --------------------------------- | ------ | ----------- | -------- | ----------- | ------------------------------- |
| P5-01 | strategy-pipeline skill 정의      | Claude | P4-04       | **완료** | ~03-31      | skills/strategy-pipeline/       |
| P5-02 | VB 파라미터 최적화 (K값, 필터)    | Claude | P4-06       | 리서치 완료 | 04-06~04-10 | `docs/research/20260418_vb_param_optimization.md` — 탐색 그리드 60조합, 실행 대기 |
| P5-03 | 알트 펌프 서핑 재검토             | Claude | P5-02       | 리서치 완료 | 04-13~04-17 | `docs/research/20260418_alt_pump_review.md` — BULL 레짐 7일 유지 시 착수 |
| P5-04 | 레짐 자동 전환 시스템             | Claude | P5-02       | **완료** | 04-18 | `regime_switcher.py` + `scripts/regime_check.py` (DRY-RUN), 26/26 tests |
| P5-05 | VB 손절 dead code 버그 수정       | Claude | -           | **완료** | ~04-04      | advanced.py dead code 정리 + sl_count    |
| P5-06 | 전략 코드 품질 개선               | Claude | P5-05       | **완료** | ~04-04      | ATR 중복 제거, __all__ 4개 추가          |
| P5-07 | BTC 필터 + F&G 레짐 스위치 리서치 | Claude | -           | **완료** | ~04-04      | 3가지 모두 가능, 클로저 패턴, 난이도 낮  |
| P5-08 | composite v2 구현 + 백테스트      | Claude | P5-07       | **완료** | ~04-04      | F&G게이트+BTC필터+연패쿨다운, QA PASS    |

### 5-2. 전략 업그레이드 (04-05 일괄)

> ADR: [20260405_1_bata_strategy_upgrade.md](../decisions/20260405_1_bata_strategy_upgrade.md)
> 목표: 데이터 확장 → 기존 전략 재검증 → 신규 발굴 → 포트폴리오 통합

**Sprint A: 데이터 확장 + 긴급 방어**

| ID    | 태스크                                | 담당   | 선행        | 상태 | 목표일 | 비고                                   |
| ----- | ------------------------------------- | ------ | ----------- | ---- | ------ | -------------------------------------- |
| P5-09 | 서킷브레이커 구현 (계좌 -20% 중단)   | Claude | P5-08       | **완료** | 04-05  | circuit_breaker.py, 모든 매수경로 차단 |
| P5-10 | BTC/KRW 2017-10~2018-12 데이터 수집  | Claude | -           | **완료** | 04-05  | +457일봉, 총 3,110개 (9년)             |
| P5-11 | F&G 지수 2018~ 전체 수집·저장        | Claude | -           | **완료** | 04-05  | 2,982건 (2018-02-01~2026-04-05)        |
| P5-12 | 알트 Top-10 데이터 갭 메우기          | Claude | -           | **완료** | 04-05  | +1,534일봉 (LINK,DOGE,SOL,NEAR,ETC)   |
| P5-13 | 레짐 태깅 스크립트                    | Claude | P5-10,P5-11 | **완료** | 04-05  | BULL 44.5%, BEAR 24.9%, SIDEWAYS 14.7% |

**Sprint B: 기존 전략 재검증 (백테스트)**

| ID    | 태스크                                | 담당   | 선행        | 상태 | 목표일 | 비고                                   |
| ----- | ------------------------------------- | ------ | ----------- | ---- | ------ | -------------------------------------- |
| P5-14 | Composite DC20 — 9년 전체 백테스트    | Claude | P5-10,P5-13 | **완료** | 04-05  | IS 1.52 / OOS 1.11 (견고)              |
| P5-15 | 레짐별 성과 분리표                    | Claude | P5-14,P5-13 | **완료** | 04-05  | CRISIS 유일 손실, F&G<20 차단 +380pp   |
| P5-16 | 레짐 필터 변형 3종 비교               | Claude | P5-14       | **완료** | 04-05  | 200EMA 필터 최선 (OOS 1.274, +14%)     |
| P5-17 | 서킷브레이커 시뮬레이션               | Claude | P5-14       | **완료** | 04-05  | -20% 수동해제 안전, 자동해제 역효과    |
| P5-18 | VB 전략 9년 재검증                    | Claude | P5-10       | **완료** | 04-05  | OOS 0.30 FAIL                          |

**Sprint C: 신규 전략 발굴**

| ID    | 태스크                                | 담당   | 선행        | 상태 | 목표일 | 비고                                   |
| ----- | ------------------------------------- | ------ | ----------- | ---- | ------ | -------------------------------------- |
| P5-19 | 횡보/하락장 특화 후보 7종 리서치       | Claude | P5-15       | **완료** | 04-05  | BB평균회귀, P/MA200, 절대모멘텀 등     |
| P5-20 | IS 스크리닝 (후보별 9년 데이터)       | Claude | P5-19,P5-10 | **완료** | 04-05  | BB(-0.28),P/MA200(0.28) 탈락, 절대모멘텀 IS1.33 |
| P5-21 | OOS 검증 → 필터 통합 전환            | Claude | P5-20       | **완료** | 04-05  | OOS 기준 미달 → Sprint D 필터 통합     |

**Sprint D: 포트폴리오 통합**

| ID    | 태스크                                | 담당   | 선행             | 상태 | 목표일 | 비고                                   |
| ----- | ------------------------------------- | ------ | ---------------- | ---- | ------ | -------------------------------------- |
| P5-22 | 종합분석 + 카탈로그 갱신 + config 권장안 | Claude | P5-16,P5-21   | **완료** | 04-05  | bata_v15_final_report.md, catalog 갱신 |
| P5-23 | 텔레그램 최종 보고                    | Claude | P5-22            | **완료** | 04-05  | Sprint A~D 전체 완료 보고              |

**추가 리서치 (04-06)**

| ID    | 태스크                                | 담당   | 선행   | 상태     | 목표일 | 비고                                   |
| ----- | ------------------------------------- | ------ | ------ | -------- | ------ | -------------------------------------- |
| P5-24 | 지수(EMA) 기반 투자 전략 3종 백테스트 | Claude | P5-10  | **완료** | 04-06  | A:EMA50 OOS 1.52 PASS, B:골든크로스 거래부족, C:EMA200 FAIL |
| P5-24b| EMA50 심화 검증 (9종 변형)            | Claude | P5-24  | **완료** | 04-06  | EMA50+200필터 최선: OOS 1.83, MDD -12.6% |
| P5-25 | EMA50+200필터 전략 DRY-RUN 배포       | Claude | P5-24b | **완료** | 04-06  | advanced.py + config + realtime_monitor, AWS 배포 |
| P5-26 | Composite EMA200 필터 코드 적용       | Claude | P5-16  | **완료** | 04-06  | SMA20→EMA200, 전종목 차단, CTO PASS |

**하락장 레짐 + VB 개선 (04-08~04-09)**

| ID     | 태스크                                | 담당   | 선행   | 상태     | 목표일 | 비고                                   |
| ------ | ------------------------------------- | ------ | ------ | -------- | ------ | -------------------------------------- |
| P5-27  | 하락장 레짐 3중 방어                   | Claude | P5-26  | **완료** | 04-08  | BTC EMA200 + ATR% + 하드 캡            |
| P5-28  | VB 개선 A~E 구현 + 배포                | Claude | P4-06  | **완료** | 04-09  | 하락장필터/데드블랙/주3회/연패쿨다운/임계완화, 25/25 PASS |
| P5-28b | VB 개선 재검증 (DRY-RUN 재집계)        | Claude | P5-28  | **완료(조건부)** | 04-18  | 거래 0건, A필터 7일 연속 차단(의도대로) — CONDITIONAL |

**Composite DC 기간 단축 (04-26)**

| ID     | 태스크                                | 담당   | 선행   | 상태     | 목표일 | 비고                                   |
| ------ | ------------------------------------- | ------ | ------ | -------- | ------ | -------------------------------------- |
| P5-29  | Composite DC(20)→DC(15) 단축 + 배포   | Claude | P5-26  | **완료** | 04-26  | 단일 BTC OOS Sharpe 1.140→1.375, 멀티 6코인 평균 0.624→0.803, 200EMA 유지, ADR 20260426-1, cto gate PASS, AWS 반영 |
| P5-30  | DC15→DC12 + ATR 12→10% + VOL 1.2→1.0 동시 튜닝 | Claude | P5-29 | **완료** | 05-05 | ADR 20260505-1. 5-3~5-5 매매 0건 30h + ATR 차단 0건 실측 → ATR 안전 복귀(lessons #13) + DC/VOL 적극화. 1주 후 평가 |

## Phase 6: 코드 품질 / 린트 층 (Lint Layer)

> 설계 문서: [docs/lint_layer.md](../lint_layer.md)
> 배경 lesson: [20260408_4_nonetype_format_lint](../lessons/20260408_4_nonetype_format_lint.md)
> 목표: "같은 실수를 두 번 하지 않는다"를 사람 기억이 아닌 코드로 강제 집행

### 6-1. Phase 1 — 기반 구축 ✅

| ID    | 태스크                                   | 담당   | 선행 | 상태     | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ---- | -------- | ------ | -------------------------------------------- |
| P6-01 | lint_none_format.py (R1/R2/R3) + pre_deploy_check 통합 + R1 10건 수정 | Claude | -    | **완료** | 04-08  | AST 기반, ERROR 10→0, WARN 28건 탐지         |

### 6-2. Phase 2 — 공용 헬퍼 분리 ✅

| ID    | 태스크                                   | 담당   | 선행  | 상태     | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ----- | -------- | ------ | -------------------------------------------- |
| P6-02 | services/common/ccxt_utils.py (fmt_num, resolve_fill) + jarvis_executor 리팩토링 + R3 지능화 + 실거래 3건 버그 수정 | Claude | P6-01 | **완료** | 04-08  | WARN 28→1, BoolOp(Or)/SAFE_WRAPPERS 억제    |
| P6-03 | docs/lint_layer.md 설계 문서 작성        | Claude | P6-02 | **완료** | 04-08  | 제목/무엇/동작/기대효과/로드맵 포함 단독 문서 |
| P6-06 | tests/common/test_ccxt_utils.py 단위 테스트 | Claude | P6-02 | **완료** | 04-08  | 16건 전부 PASS (fmt_num 9, resolve_fill 7)   |

### 6-3. Phase 3 — 자동화 강화

| ID    | 태스크                                   | 담당   | 선행  | 상태     | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ----- | -------- | ------ | -------------------------------------------- |
| P6-04 | git pre-commit hook (.githooks/pre-commit) | Claude | P6-01 | **완료** | 04-08  | README 포함, 수동 활성화 방식                 |
| P6-05 | GitHub Actions / CI 통합                 | Claude | P6-04 | **완료** | 04-08  | .github/workflows/lint.yml (3 job)            |

### 6-4. Phase 4 — 규칙 확장 (경험 기반 점증)

> lessons 누적에 따라 점진적 추가. 현재 후보 5종.

| ID    | 태스크                                    | 담당   | 선행  | 상태 | 목표일 | 비고                                         |
| ----- | ----------------------------------------- | ------ | ----- | ---- | ------ | -------------------------------------------- |
| P6-07 | R4: dict[key] subscript 포매팅 KeyError 방어 | Claude | P6-01 | **완료** | ~04-10 | WARN 99건 탐지, lint_none_format.py R4 추가  |
| P6-08 | R5: datetime.strptime None 체크 린트      | Claude | P6-01 | **완료** | ~04-10 | WARN 4건 탐지, lint_none_format.py R5 추가   |
| P6-09 | R6: async ccxt fetch_* 누락 await 탐지    | Claude | P6-01 | **완료** | 04-17  | R6 5건 탐지, lessons/20260417_1 등록          |
| P6-10 | R7: 상태 파일 진입 시 fetch_balance 교차검증 누락 | Claude | P6-01 | **완료** | 04-17  | lessons/20260408_2 연계                      |
| P6-11 | R8: 시장가 주문 직후 sleep 없는 fetch_order 방지 | Claude | P6-01 | **완료** | 04-17  | 업비트 반영 지연 대응                         |

### 6-5. Phase 5 — 메타 린트

| ID    | 태스크                                    | 담당   | 선행        | 상태 | 목표일 | 비고                                         |
| ----- | ----------------------------------------- | ------ | ----------- | ---- | ------ | -------------------------------------------- |
| P6-12 | lessons ↔ 린트 규칙 매핑 자동 생성 + 미연결 경고 | Claude | P6-07~11    | **완료** | 04-18  | scripts/lint_meta.py, 11/17 매핑, 미집행 6건 경고 |

### 6-6. Phase 6 — 패턴 데이터베이스 (스트레치)

| ID    | 태스크                                    | 담당   | 선행  | 상태 | 목표일 | 비고                                         |
| ----- | ----------------------------------------- | ------ | ----- | ---- | ------ | -------------------------------------------- |
| P6-13 | workspace/lint_history.jsonl 누적 + 주간 통계 | Claude | P6-12 | **완료** | 04-18 | scripts/lint_history.py, --summary/--weekly 옵션, 10 tests |

## Phase 7: 운영 모니터링 (Monitoring Framework)

> 설계: [workspace/plans/20260410_monitoring_framework.md](../../workspace/plans/20260410_monitoring_framework.md)
> 배경: CTO 리뷰 04-10 FAIL — "봇이 죽으면 알 방법이 없고, 돌아도 제대로 도는지 확인할 수 없음"
> 목표: 3계층 감시 체계 (Heartbeat → Sanity Check → Performance Audit)

### 7-A. 즉시 핫픽스 ✅

| ID    | 태스크                                   | 담당   | 선행 | 상태     | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ---- | -------- | ------ | -------------------------------------------- |
| P7-01 | get_balance() 성능 핫픽스 (CTO WARN-1)   | Claude | -    | **완료** | 04-10  | fetch_tickers 일괄 + load_markets 필터        |
| P7-02 | 알트 시세 조회 실패 로깅 (CTO WARN-5)    | Claude | -    | **완료** | 04-10  | except pass → 마켓 없음/실패 로깅             |

### 7-B. 1계층 — Heartbeat (프로세스 생존 감시)

| ID    | 태스크                                   | 담당   | 선행  | 상태 | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ----- | ---- | ------ | -------------------------------------------- |
| P7-03 | heartbeat 파일 갱신 (realtime_monitor)    | Claude | P7-01 | **완료** | 04-17  | 매 2분 /tmp/bata_heartbeat touch (경계 회피)   |
| P7-04 | watchdog_check.sh + cron 등록             | Claude | P7-03 | **완료** | 04-17  | cron 1분, 서버 자동 재시작 + 텔레그램 경보      |
| P7-05 | systemd WatchdogSec 설정                  | Claude | P7-03 | **완료** | 04-17  | Type=notify, WatchdogSec=300, TimeoutStart=600|

### 7-C. 2계층 — Sanity Check (정합성 감시)

| ID    | 태스크                                   | 담당   | 선행  | 상태 | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ----- | ---- | ------ | -------------------------------------------- |
| P7-06 | State ↔ Exchange 교차검증 (매 1시간)      | Claude | P7-01 | **완료** | 04-17  | _hourly_sync — 먼지 5000원 필터, 경보만        |
| P7-07 | 웹소켓 stale connection 감지              | Claude | P7-03 | **완료** | 04-17  | 5분 timeout → asyncio.TimeoutError 재연결      |
| P7-08 | 로그 볼륨 감시 cron                       | Claude | -     | **완료** | 04-17  | cron 09:10 KST, 임계 5000줄                     |

### 7-D. 3계층 — Performance Audit (성과 감시)

| ID    | 태스크                                   | 담당   | 선행  | 상태 | 목표일 | 비고                                         |
| ----- | ---------------------------------------- | ------ | ----- | ---- | ------ | -------------------------------------------- |
| P7-09 | 필터 작동 통계 카운터                     | Claude | P7-06 | **완료** | 04-18  | filter_stats.py, realtime_monitor 훅 6곳, JSON 영구화 |
| P7-10 | 일일 보고에 필터 통계 포함                | Claude | P7-09 | **완료** | 04-18  | daily_report.py "필터 차단 통계" 섹션           |
| P7-11 | CTO 재검증 (모니터링 전체)                | Claude | P7-10 | **완료** | 04-18  | gate PASS → deploy 성공, XRP 차단 실측 확인     |

## Phase 8: ML 신호 품질 필터 (Signal Quality Scoring)

> 설계: [output/ml_signal_filter_architecture.html](../../output/ml_signal_filter_architecture.html)
> Plan: [workspace/plans/20260504_3_ml_signal_filter.md](../../workspace/plans/20260504_3_ml_signal_filter.md)
> ADR: [docs/decisions/20260504_2_ml_signal_filter.md](../decisions/20260504_2_ml_signal_filter.md)
> 목표: DC15 매수 신호의 +5% 도달 확률을 XGBoost로 스코어링 → fail-open 게이트로 사용. 가격 예측 X, 신호 품질 스코어링.

### 8-A. 학습 인프라 (S1~S4) ✅

| ID    | 태스크                                       | 담당   | 선행  | 상태     | 목표일 | 비고                                                        |
| ----- | -------------------------------------------- | ------ | ----- | -------- | ------ | ----------------------------------------------------------- |
| P8-01 | Plan + 디렉터리/requirements 셋업            | Claude | -     | **완료** | 05-04  | services/ml/, requirements-ml.txt, config.py 단일 출처      |
| P8-02 | features.py + feature_store.py (18 feature)  | Claude | P8-01 | **완료** | 05-04  | lookahead 차단(at_ts cutoff), Parquet IO, 일자별 dedup       |
| P8-03 | labeler.py + dummy 데이터셋                  | Claude | P8-02 | **완료** | 05-04  | +5%/horizon/slippage 0.2%, make_dummy_dataset (CI용)         |
| P8-04 | trainer.py + scripts/ml_train.py             | Claude | P8-03 | **완료** | 05-04  | XGBoost + walk-forward 6 folds, model registry, dry-run 통과 |

### 8-B. 추론/통합 + QA (S5~S7) ✅

| ID    | 태스크                                       | 담당   | 선행  | 상태     | 목표일 | 비고                                                        |
| ----- | -------------------------------------------- | ------ | ----- | -------- | ------ | ----------------------------------------------------------- |
| P8-05 | inference.py (MLFilter) + shadow.py          | Claude | P8-04 | **완료** | 05-04  | fail-open 정책, 싱글톤, JSONL 로거 (log_decision/log_outcome) |
| P8-06 | multi_trader 매수 hook + deploy_model_to_aws.sh | Claude | P8-05 | **완료** | 05-04  | ML_FILTER_ENABLED=0 시 zero-cost, scp 폴백, 원자 심볼릭 전환  |
| P8-07 | ADR + pre_deploy_check ML 검증룰 5개         | Claude | P8-06 | **완료** | 05-04  | meta 존재/threshold 범위/feature 카탈로그 일치/AUC sanity     |
| P8-08 | [QA-FIX] realtime_monitor ML hook 누락 수정   | Claude | P8-07 | **완료** | 05-04  | pdca-qa MAJOR 발견 → 모든 매수 경로에 hook + lessons #26     |
| P8-09 | [QA-FIX] MAX_POSITIONS 자체정의 제거         | Claude | P8-08 | **완료** | 05-04  | multi_trader → config.py import 통일 (lessons #19 해소)      |

### 8-C. 모델 v2 (실데이터 학습 + 비교 실험) ✅

| ID    | 태스크                                       | 담당   | 선행  | 상태     | 목표일 | 비고                                                        |
| ----- | -------------------------------------------- | ------ | ----- | -------- | ------ | ----------------------------------------------------------- |
| P8-10 | scripts/ml_build_dataset.py (실데이터)        | Claude | P8-04 | **완료** | 05-04  | DuckDB 4h × 11종 코인 × 3.8년 → DC15 돌파 3,430 샘플          |
| P8-11 | v1_real 학습 (18 feature)                    | Claude | P8-10 | **완료** | 05-04  | walk-forward AUC 0.543 / Precision 0.518                    |
| P8-12 | Feature 18→23 보강 (F&G/MACD/BB/Stoch/btc_corr) | Claude | P8-11 | **완료** | 05-04  | macro 테이블 F&G 실데이터 활용 (2018~2026, 2982일)            |
| P8-13 | v2_real 학습 (23 feature)                    | Claude | P8-12 | **완료** | 05-04  | AUC 0.553 / Precision 0.530, F&G #2 importance               |
| P8-14 | XGBoost vs LightGBM 비교                     | Claude | P8-13 | **완료** | 05-04  | LGB 0.5556 vs XGB 0.5532 동등, **XGBoost 유지** 결정          |
| P8-15 | BTC 단독 vs 11종 통합 비교                   | Claude | P8-13 | **완료** | 05-04  | BTC 단독 0.477 (표본 320 부족), **통합 v2_real 채택**         |

### 8-D. AWS 배포 + Shadow Mode 활성 ✅

| ID    | 태스크                                       | 담당   | 선행  | 상태     | 목표일 | 비고                                                        |
| ----- | -------------------------------------------- | ------ | ----- | -------- | ------ | ----------------------------------------------------------- |
| P8-16 | AWS venv ML 패키지 설치 (xgb/sklearn/joblib/pyarrow) | Claude | -     | **완료** | 05-04  | services/pyproject.toml에 [ml] optional + 직접 설치, nvidia-nccl 제거 |
| P8-17 | data/models/ + 모델 파일 배포                | Claude | P8-16 | **완료** | 05-04  | scp 폴백, current.pkl 심볼릭 → signal_filter_v2_real.pkl     |
| P8-18 | systemd drop-in ML_FILTER_ENABLED=1 + restart | Claude | P8-17 | **완료** | 05-04  | btc-trader PID 144899 → 150922, 무중단 재시작 (TimeoutStartSec=600) |

### 8-E. 모니터링 + Outcome 매칭 ✅

| ID    | 태스크                                       | 담당   | 선행  | 상태     | 목표일 | 비고                                                        |
| ----- | -------------------------------------------- | ------ | ----- | -------- | ------ | ----------------------------------------------------------- |
| P8-19 | pre_deploy_check 메모리 임계 룰              | Claude | P8-18 | **완료** | 05-04  | free <100MB ERROR / <200MB WARN / swap >80% WARN (lessons #5) |
| P8-20 | daily_report ML 섹션                         | Claude | P8-18 | **완료** | 05-04  | 활성 여부/모델/오늘 의사결정 통계 (buy/block, mean score)     |
| P8-21 | outcome_matcher 모듈 + ml_outcome_match.py   | Claude | P8-18 | **완료** | 05-04  | ccxt 4h 6봉 high vs entry×1.052 → reached_target            |
| P8-22 | outcome cron 등록 (KST 03:00) + ml_effect_analysis.py | Claude | P8-21 | **완료** | 05-04  | 매일 어제 결정의 24h 후 도달 자동 기록, confusion+가상 PnL 분석 |

### 8-F. 후속 작업 (대기)

| ID    | 태스크                                       | 담당   | 선행  | 상태 | 목표일 | 비고                                                        |
| ----- | -------------------------------------------- | ------ | ----- | ---- | ------ | ----------------------------------------------------------- |
| P8-23 | OHLCV 주입 (실 score 분포 만들기)            | Claude | P8-22 | **완료** | 05-05  | MLFilter.score() 내부 ccxt 자동 fetch + 60s LRU 캐시. ML_SHADOW_MODE=1 추가 (차단 없이 score 누적). BTC 실 score 0.17 검증 |
| P8-24 | v3 모델 개선 (timeframe 정합/도미넌스/cap rank) | Claude | P8-23 | 대기 | W22+   | 4h vs 15m feature 가정 통일, BTC dominance CoinGecko, market_cap_rank Upbit 실측 |
| P8-25 | outcome JSONL idempotent fix                  | Claude | P8-22 | 대기 | W20    | 현재 outcome이 signal_ts 날짜 파일에 저장 → 재실행 시 중복 가능 |
| P8-26 | 3개월 누적 후 A/B 검증 + Live 승격 결정       | 사용자 | P8-23 | **건너뜀** | -      | OOS 백테스트로 압축 (P8-27) — 사용자 "최대 효과 신속히" 요구 |
| P8-27 | **ML LIVE 가속 (threshold 0.45 보수 시작)**   | Claude | P8-23 | **완료** | 05-05  | OOS 89일 시뮬 PF 1.04 (0.45) / 1.17 (0.55) 입증, ADR 20260505-2, ML_SHADOW_MODE=0 활성, 1주 후 0.50 강화 또는 롤백 |
| P8-28 | 1주 자동 평가 cron (5-12 KST 18:00)          | Claude | P8-27 | **완료** | 05-05  | scripts/ml_weekly_review.py + crontab `0 9 12 5 *` — closed_trades + shadow JSONL outcome 통합 → 텔레그램 자동 발송 |

## 지금 할 수 있는 일 (2026-05-05 갱신, W19 — ML LIVE 활성)

| ID       | 태스크                               | 비고                                             |
| -------- | ------------------------------------ | ------------------------------------------------ |
| 🔴 P8-27  | **ML LIVE 1주 모니터링 (5-12 평가)**   | threshold 0.45 활성, 차단/허용 outcome 추적 (매일 KST 03:00 cron) |
| 🔴 P5-30  | **DC12 + ATR 10% + VOL 1.0 평가**     | 매매 빈도 / 손절률 / PF — 5-12 통합 평가         |
| 🟡 운영 감시 | ML 차단 비율 (`record_block(ml_filter)`) | 30~50% 권장. 95%+ 또는 5%- 시 임계 재조정     |
| 🟡 운영 감시 | t3.micro 메모리 (ML LIVE 후 RSS 209MB) | free 150MB, swap 약 65% — OOM 임박 감시         |
| 🟡 운영 감시 | btc-trader 가동 안정성                 | PID 194328, watchdog/heartbeat 정상 여부        |
| P8-25    | outcome JSONL idempotent fix          | 1주 운영 후 중복 라인 패턴 확인 시 진행          |
| P5-02    | VB 파라미터 최적화 (대기)            | BULL 레짐 7일 유지 시 자동 착수 — 현재 BEAR 지속 |
| P5-03    | 알트 펌프 서핑 재검토 (대기)         | BULL 레짐 7일 유지 시 자동 착수 — 동일 조건      |
| 스트레치 | 시드머니 증액 검토 (PF≥1.2 안정 시)   | 6월 이후, 강제 금지선: 누적 -10% 시 재검토       |
| 스트레치 | P5-04 레짐 자동 전환 LIVE 승격 ADR   | 현재 DRY-RUN, enabled=false                       |

