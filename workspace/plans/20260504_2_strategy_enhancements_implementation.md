# 전략 보강 구현 — 부분 익절 + 거래량 필터 + 일일 손실 한도

- **작성일(KST)**: 2026-05-04 07:35
- **세션**: 자비스 (Auto mode)
- **예상 소요**: 4~5시간 (3개 변경 + 검증)
- **연관 결정**: [decision 20260504-1](../../docs/decisions/20260504_1_three_strategy_enhancements.md)
- **선행 plan**: [20260504_1 좀비/crontab 정리](../../docs/lessons/20260504_1_zombie_processes_crontab_overwritten_bak_dirs.md)

## 1. 목표

decision 20260504-1에 명시된 3가지 전략 보강을 구현 → 승률 ↑ + 평균 수익 안정화 + 단일 사건 방어.

## 2. 성공기준 (Acceptance Criteria — 12개)

### A. 부분 익절 (Tiered Take-Profit)
- [ ] **AC1**. `services/execution/config.py`에 `TP_LEVELS` 추가 (5%/10%/15% × 30% 매도)
- [ ] **AC2**. `realtime_monitor.py`의 보유 포지션 가격 체크 — **SL 우선 → 미발동 시 TP** 평가 순서 명문화 (cto M1). `_handle_tick`에서 `if price < trail_stop: SL → return; for tp in TP_LEVELS: ...`
- [ ] **AC3**. position state 잔량 회계 모델 (cto BLOCK-1):
    - `entry_amount_krw`: 불변 (최초 매수 KRW, 비교 기준)
    - `entry_qty`: 불변 (최초 매수 수량)
    - `tp_sold_levels: list[int]`: 이미 매도된 단계 인덱스 (예: [0, 1])
    - `remaining_qty`: 매 단계 매도 후 ccxt `fetch_balance` 재조회로 갱신 (state↔거래소 미러)
- [ ] **AC4**. 부분 매도 절차 (atomic):
    1. ccxt `create_market_sell_order(sym, qty * sell_ratio)` 호출
    2. 성공 시: `fetch_balance` 재조회 → `remaining_qty` 갱신 + `tp_sold_levels.append(idx)` + atomic write
    3. 실패 시: state 변경 안 함, 다음 가격 틱에서 재평가 (lessons #3: retry 아닌 next-tick)
- [ ] **AC5**. 단위 테스트: dummy state로 +5%/+10%/+15% 시뮬 → 단계별 매도 트리거 + 잔량 10% 보유 + tp_sold_levels 정확성 확인

### B. 거래량 필터 (Volume Confirmation)
- [ ] **AC6**. `config.py`에 `VOL_FILTER_ENABLED=True`, `VOL_FILTER_MULTIPLIER=1.5` 추가
- [ ] **AC7**. `_execute_buy`의 진입 게이트에 거래량 체크 — `level.get("latest_vol", 0) >= level.get("vol_sma", 0) * VOL_FILTER_MULTIPLIER` (level 누락 시 게이트 통과 = silent fail 회피)
- [ ] **AC8**. `_refresh_levels`의 **composite 분기에도 vol_sma 계산 추가** (cto M4 — 현재 daytrading IS_DAYTRADING 분기 내부에만 존재). composite의 `level` dict에 `latest_vol`, `vol_sma` 키 추가. KeyError/silent fail 방지
- [ ] **AC9**. `record_block("vol_filter", symbol)` 카운터 추가 → daily_report 필터 통계 표시 (lessons #6 매수 경로 일관성: scanner/realtime 양쪽 점검)

### C. 일일 손실 한도 (Daily Loss Circuit)
- [ ] **AC10**. `config.py`에 `DAILY_LOSS_LIMIT_PCT=0.03` (3%) + `DAILY_LOSS_BASE_KRW=CIRCUIT_BREAKER_INITIAL_CAPITAL` (300,000) 추가 (cto M2 — 분모 명시)
- [ ] **AC11**. `workspace/daily_pl_state.json` 신규:
    ```json
    {"date": "YYYY-MM-DD", "realized_pl_krw": -9000, "blocked": false, "events": [...]}
    ```
- [ ] **AC12**. `_execute_buy` 진입 게이트:
    - 손실률 = `abs(realized_pl_krw) / DAILY_LOSS_BASE_KRW`
    - 손실률 ≥ 0.03 → **매수 차단** + `record_block("daily_loss_limit", symbol)` + 첫 발동 시 `send_critical` (디바운스 24h)
- [ ] **AC13**. 매도(`_execute_sell`) 성공 시 daily_pl_state 갱신:
    - `realized_pl_krw += (exec_price - entry_price) * sold_qty` (KRW 손익)
    - atomic write (lessons #24)
- [ ] **AC14**. 매일 09:00 KST 자동 reset:
    - **cron 추가 금지** (lessons #18, #24 위험) — `daily_live.py` 시작 시 + `realtime_monitor` `_refresh_levels`(매일 09:00 KST 호출) 시점에 reset 함수 호출
    - 함수: `reset_daily_pl(today)` — 오늘 날짜와 다르면 reset, 같으면 유지

### D. 헬스체크 + pre_deploy_check
- [ ] **AC15**. `services/healthcheck/runner.py`에 `check_daily_loss_state` 항목 추가 (12개 → 13개) — `daily_pl_state.json` mtime 24h 초과 시 WARN (reset 누락 감지, cto M3 보강)
- [ ] **AC16**. `pre_deploy_check.py`에 신규 config 키(TP_LEVELS, VOL_FILTER_ENABLED, VOL_FILTER_MULTIPLIER, DAILY_LOSS_LIMIT_PCT, DAILY_LOSS_BASE_KRW) 존재 검증
- [ ] **AC17**. cto M3 사전확인 명시: 09:00 reset이 cron 추가 아닌 daily_live.py 내부 호출이라 lessons #18/#24 위험 회피 — pre_deploy_check `check_critical_healthcheck_cron` 패턴 따라 `check_daily_pl_reset_path` 검증 룰 추가 권장 (선택)

## 3. 단계

1. plan 작성 + cto 1차 검증 (이 단계, 동일 세션 PASS 금지)
2. **A 부분 익절 구현** — config + realtime_monitor 매도 분기 + atomic state 업데이트
3. **B 거래량 필터 구현** — config + level 계산 + 진입 게이트 체크
4. **C 일일 손실 한도 구현** — config + daily_pl_state + 진입 게이트 + 매도 시 갱신 + 09:00 reset
5. **D 헬스체크 + pre_deploy_check 보강**
6. cto 2차 검증 (구현)
7. 단위 테스트 (가능한 부분)
8. 서버 배포 + btc-trader 재시작
9. lessons #25 작성 + plan 회고 + 텔레그램 보고
10. 1주 운영 후 회고 (별도)

## 4. 리스크 & 사전 확인

| 리스크 | 완화 |
|---|---|
| 부분 익절 매도 시 ccxt 호출 실패 → 잔량 추적 오류 | atomic state write + 매도 실패 시 state 변경 안 함 (rollback) |
| 거래량 필터 너무 엄격 → 진입 빈도 0건 | VOL_FILTER_MULTIPLIER 1.5 시작, 1주 후 조정 권한 명시 |
| 일일 손실 한도 false trigger (실현 vs 미실현 혼동) | 실현 손익만 카운트 (closed_trades), 미실현은 무관 |
| 09:00 reset 실패 → 영구 매수 차단 | daily_live.py에 reset 로직 추가 + 헬스체크에서 daily_pl_state mtime 24h 초과 시 WARN |
| ccxt 부분 매도 시 거래소 최소 주문량 미달 (잔량 < 5000원) | 단계별 매도 시 최소 주문 검증 → 잔량 부족 시 마지막 단계는 전량 |
| TP 매도 후 trail_stop 갱신 누락 | 부분 매도 후 highest_price 그대로 유지 (정상) |
| 매도 경로에 lessons #3 위배 (즉시성) | 부분 익절은 즉시 체결 시장가 (retry 없음, lessons #3 준수) |

### 사전 확인사항
- [ ] [lessons #3 즉시 체크](../../docs/lessons/20260329_3_auto_stop_delay.md) — 매도 경로 retry 금지 재확인
- [ ] [lessons #11 CB 기존 포지션 정책](../../docs/lessons/20260408_3_cb_existing_positions_policy.md) — 부분 매도 시 CB 발동 정책 점검
- [ ] [lessons #13 하드캡](../../docs/lessons/20260408_5_ong_wide_stop.md) — TP 매도 후 잔량은 여전히 -10% 캡 적용
- [ ] [lessons #18 cron silent fail](../../docs/lessons/20260425_1_crontab_venv_path_drift.md) — 09:00 reset cron 등록 시 검증
- [ ] [lessons #24 다중 프로젝트 동거](../../docs/lessons/20260504_1_zombie_processes_crontab_overwritten_bak_dirs.md) — 신규 cron 등록 시 BitCoin 라인 보존 확인

## 5. 검증

- [x] B (cto) 1차 — CONDITIONAL FAIL → BLOCK-1 + M1~M4 모두 plan 보강 완료 (잔량 회계 모델 / TP/SL 우선순위 / 손실 분모 / composite vol_sma / 09:00 reset 경로)
- [ ] B (cto) 2차 — 구현 후
- [ ] C (pre_deploy_check + 단위 테스트)
- [ ] A (1주 운영 후 회고, 별도 세션)

## 6. 회고 (작업 후)

- 결과: (작성 예정)
- 12개 AC 통과 여부
- 부분 익절 첫 발화 시점
- 거래량 필터 차단 비율
- 일일 손실 한도 발동 여부
