# #25 — 전략 보강 3종: 부분 익절 + 거래량 필터 + 일일 손실 한도

- **작업일**: 2026-05-04 07:30~08:30 KST
- **decision**: [20260504-1](../decisions/20260504_1_three_strategy_enhancements.md)
- **plan**: [20260504_2](../../workspace/plans/20260504_2_strategy_enhancements_implementation.md)

## 1. 처리 내역

### A. 부분 익절 (Tiered Take-Profit)
- TP_LEVELS = [+5%×30%, +10%×30%, +15%×30%, 잔량 10% 트레일]
- realtime_monitor `_check_tp_levels` 신규 — SL 우선 → TP 평가
- position state에 entry_qty/entry_amount_krw/tp_sold_levels 불변 회계 모델
- 매도 후 fetch_balance 재조회로 remaining_qty 갱신

### B. 거래량 필터 (Volume Confirmation)
- VOL_FILTER_ENABLED + VOL_FILTER_MULTIPLIER (1.5×)
- composite의 `_refresh_levels`에 vol_sma 5일 평균 계산 추가 (cto M4 — 기존 daytrading 전용이었음)
- `_execute_buy` 게이트: latest_vol >= vol_sma × 1.5
- record_block("vol_filter", symbol) 차단 카운터 추가

### C. 일일 손실 한도 (Daily Loss Circuit)
- `services/execution/daily_pl.py` 신규 모듈 (record_sell, is_daily_loss_blocked, reset_if_new_day)
- DAILY_LOSS_LIMIT_PCT 3% × DAILY_LOSS_BASE_KRW 300,000 = -9,000원 도달 시 매수 차단
- 매도 시 KRW 손익 누적 (atomic write)
- 매수 직전 게이트 + 첫 발동 시 send_critical 알람
- 09:00 KST 자동 reset — `_refresh_levels` 진입 시 호출 (cron 추가 회피, lessons #18/#24)

### D. 헬스체크 + pre_deploy_check
- `check_daily_loss_state` 추가 (12개 → 13개 항목)
- `check_strategy_enhancement_config` 룰 — 신규 7개 키 존재 검증

## 2. cto 검증 흐름 (2회)

| 회차 | 판정 | 발견 | 조치 |
|---|---|---|---|
| 1차 (plan) | CONDITIONAL FAIL | BLOCK-1 (잔량 회계 모델), M1 (TP/SL 우선순위), M2 (손실 분모), M3 (09:00 reset 경로), M4 (composite vol_sma) | plan 5개 모두 보강 |
| 2차 (구현) | FAIL | BLOCK-1 잔존: `_execute_buy`에 entry_qty/entry_amount_krw 저장 누락 | 즉시 수정 (1줄) |
| 재배포 | PASS | — | active OK, 일일손익 +333 KRW 즉시 기록 (record_sell 동작 입증) |

## 3. 교훈

1. **잔량 회계는 "불변 입력 + 가변 추적" 분리** — entry_qty/entry_amount_krw는 매수 시 한 번만 저장, tp_sold_levels는 단계 진행 시 갱신. 둘을 섞으면 부분 매도 후 잔량 계산 부정확
2. **TP/SL 우선순위 명문화** — 같은 가격틱에서 SL 트리거 + TP 트리거 동시 가능. **SL 우선** 정책으로 자산 보호 (cto M1)
3. **매도 retry 금지 (lessons #3 재확인)** — TP 매도 실패 시 next-tick 재평가, asyncio.sleep + retry 구조 금지
4. **09:00 reset은 cron보다 _refresh_levels 진입 시점 호출이 안전** — cron 등록 시 lessons #18(venv 경로) + lessons #24(다른 프로젝트 라인 덮어쓰기) 위험
5. **단위 테스트로 정책 차이 발견** — daily_pl.is_daily_loss_blocked가 한 번 발동 후 익절로 복구되면 즉시 unblocked. 정책상 "당일 reset 전까지 유지"가 더 안전하지만 현재 코드는 즉시 복구 — 1주 운영 후 결정

## 4. 미해결 / 후속

- 표본 15+ 거래 누적 후 부분 익절 실효성 백테스트 비교
- 거래량 필터 1.5× → 1.2× or 2.0× 미세조정 (1주 운영 후)
- daily_loss state.blocked가 익절로 복구 시 즉시 해제 vs 당일 유지 정책 결정
- DRY_RUN 모드 entry_qty 추정 정확도 검증 (현재 order_amount/exec_price 사용)
