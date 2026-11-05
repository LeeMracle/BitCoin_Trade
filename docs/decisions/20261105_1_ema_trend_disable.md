# ADR 20261105-1 — EMA-TREND 전략 영구 비활성화

- **날짜**: 2026-11-05 (KST)
- **결정자**: bata-pm (Master 승인 완료)
- **발의**: bata-investment-expert
- **상태**: 승인 완료 — engineer 반영 대기
- **참조**: ADR 20260516-2 (REGIME ON 유지), lessons #1 (봉 마감 vs 실시간 틱), lessons #14 (알림 throttle)

---

## 1. 결정

`EMA_TREND_ENABLED = False` (`services/execution/config.py`) **영구 비활성화**.

재설계(§6 조건 전수 충족) 완료 전까지 **재활성 금지**.

---

## 2. 근거 — 실측 175건 전수 (2026-11-05 10:40 KST 스냅샷)

### 2.1 성과 지표

| 지표 | 값 | 판정 |
|------|-----|------|
| 총 거래 | 175건 | — |
| 승률 | **0.57%** (1승 174패) | 치명적 |
| 누적 net 복리 (수수료 반영) | **-22.64%** | 치명적 |
| 누적 gross 복리 | -7.83% | 수수료 외 구조 결함 |
| 평균 gross/trade | -0.047% | — |
| 평균 net/trade | -0.147% | 수수료 0.1%가 손익 뒤집음 |
| MDD net | -22.64% (연속 drawdown) | 치명적 |

### 2.2 종료 사유 분포

| 종료 사유 | 건수 | 비율 |
|-----------|------|------|
| EMA50 이탈 즉시청산 | **175** | **100%** |
| Trail stop 5% 작동 | **0** | **0.0%** |

**→ Trail stop 설계는 수학적으로 도달 불능이 증명됨.** 진입과 동시 또는 다음 tick에 EMA50 아래로 가격이 내려가므로 trail 발동 조건(진입가 +5% 후 되돌림)에 도달하기 전 청산이 100% 선행.

### 2.3 레짐별 성과

| 레짐 | 건수 | 승률 | 즉시청산 비율 |
|------|------|------|---------------|
| BEAR | 149 | ~0% | **97.99%** |
| BULL | 26 | ~0% | **100.0%** |
| BULL only 누적 net | — | -3.70% | BULL만 추려도 손실 |

**→ 레짐 필터로 BEAR만 차단해도 수익 불가** (BULL 26건도 100% 즉시청산·누적 -3.70%).

### 2.4 유일 승 trade

- 진입: 2026-11-04 23:35 KST
- 수수료 반영 수익률: **+0.03%**
- 판정: 통계적 생존 불가 (174패 대비 1건의 손익분기 수준 승리)

### 2.5 lessons #1 재현

봉 마감 기반 추세 전략(EMA50)을 실시간 틱으로 실행 → 가짜 돌파 174/175. lessons #1 "봉 마감 기반 전략을 실시간 틱으로 실행 금지" **정면 재현 사례**.

---

## 3. 구조적 결함 3가지

`services/execution/realtime_monitor.py:1445~1556` 분석 결과, 설계상 결함이 서로 상쇄 없이 중첩됨:

### 3.1 진입 조건 하루 종일 고정
- 조건: `prev_close < ema50_prev` (전일 종가 EMA50 아래)
- 이 값은 **하루 내내 상수**. 돌파 후 가격이 EMA50 위에 올라오는 단 1 tick이라도 있으면 매수 트리거
- 결과: chop 구간 EMA50 교차마다 반복 진입

### 3.2 청산 조건이 진입 조건의 대칭 반대
- 청산: `price < ema50` (실시간 가격이 EMA50 아래)
- 진입 즉시 가격이 아주 소폭만 흔들려 EMA50 아래로 가면 바로 청산
- chop 구간에서 매 tick 왕복 → 수수료만 누적

### 3.3 Trail stop 도달 전 즉시청산 100% 선행
- Trail stop 조건: 진입가 +5% 도달 후 피크 대비 -1% 되돌림
- EMA50 이탈 청산이 즉시 발동하므로 trail 활성 구간(진입가 +5% 이상)에 도달할 수학적 경로 없음
- 실측 175건 중 trail 작동 0건으로 증명

**결함 요약**: 진입과 청산 조건이 **거의 동일한 가격대의 반대 부호**라 설계 자체가 chop 자가유발 구조.

---

## 4. ML shadow 학습 데이터 오염 리스크

### 4.1 현상
- `services/execution/realtime_monitor.py:1601` 에서 `_ml_shadow.log_decision(signal_type="EMA_TREND_breakout", ...)` 호출 중
- 174건의 **음수 라벨**(즉시청산→손실)이 shadow 로그에 유입됨
- 향후 ML 재학습 시 `EMA_TREND_breakout` 유사 패턴(EMA50 돌파 직후 되돌림)을 **과적합 음수 패턴**으로 학습할 위험

### 4.2 동반 조치 (engineer 작업)
`ml_shadow` 학습 파이프라인에서 다음 필터 추가 — **본 ADR에 명기**:

```
# workspace/ml_shadow 집계 또는 학습 전 전처리에서
filter: signal_type != "EMA_TREND_breakout"
```

- 적용 대상: 과거 174건 전수 + 금일(2026-11-05) 이후 추가 발생분
- 책임: bata-engineer
- 검증: pre_deploy_check 또는 학습 스크립트에서 raw 로그 vs 필터링 후 건수 diff 로깅

---

## 5. 메인 전략 영향 (없음 — 안전 확인)

| 항목 | 상태 | 근거 |
|------|------|------|
| DC12+ATR 메인 전략 | **영향 없음** | EMA-TREND와 분리된 진입 경로 |
| REGIME_FILTER_ENABLED | `True` 유지 | `services/execution/config.py` — ADR 20260516-2 체계 그대로 유효 |
| regime_state.enabled | `false` (DRY-RUN) | 자동 전환 **DRY-RUN 플래그**로 매매 영향 無 — 수동 ON/OFF 유지 |
| 서킷브레이커 (-20%/-25%) | 유지 | — |
| 하드 손절 (-10%) | 유지 | — |

**→ 본 ADR은 EMA-TREND 보조 전략 단독 폐기이며, 메인 DC12 체계 및 ADR 20260516-2 재활성 체계는 완전히 보존.**

---

## 6. 재설계 조건 (향후 재활성 전제)

재활성화는 아래 4개 조건 **전수 충족** + 별도 ADR 발의 + Master 승인을 요한다.

| # | 조건 | 검증 방법 |
|---|------|----------|
| 1 | 진입을 **일봉 마감 1회성**으로 전환 (실시간 틱 근사 금지) | 코드 리뷰 — 진입 로직이 OHLCV 일봉 close에서만 트리거하는지 |
| 2 | **재진입 쿨다운 최소 4h** 도입 | 종목별 last_exit_ts + 4h 가드 코드 존재 |
| 3 | 청산 로직을 **trail stop 전용**으로 전환 (EMA50 이탈 즉시청산 제거) | 진입/청산이 대칭 반대 조건이 되지 않도록 설계 |
| 4 | OOS 백테스트 **Sharpe ≥ 0.8** + **MDD ≥ -15%** 통과 | `services/backtest/` 러너 — IS/OOS + 하락장 별도 검증 |

4개 조건 중 1개라도 미달 시 재활성 금지.

---

## 7. PM 승인 상태

- **발의자**: bata-investment-expert (2026-11-05 KST)
- **승인**: Master 승인 완료 (2026-11-05 KST)
- **반영 상태**: engineer 반영 대기
- **배포 경로**: `services/execution/config.py` → `deploy_to_aws.sh` 또는 `hotfix_deploy.sh`

---

## 8. 참조

### 분석 산출물
- `output/expert_data_20261105/analyze.py` — 전수 분석 스크립트
- `output/expert_data_20261105/summary.json` — 정량 요약 (본 ADR §2 근거)
- `output/expert_data_20261105/ema_trend_state.json` — 종료 시점 state 스냅샷
- `output/expert_data_20261105/regime_state.json` — 레짐 상태 스냅샷

### 관련 lessons
- **#1** (`docs/lessons/20260329_1_tick_vs_bar_entry.md`) — 봉 마감 vs 실시간 틱 (정면 재현 사례)
- **#14** (`docs/lessons/20260410_1_cb_log_spam.md`) — 알림 throttle
- **#46** (금번 신설 예정 — EMA-TREND throttle / engineer 작성)

### 관련 ADR
- **ADR 20260516-2** (`docs/decisions/20260516_2_entry_conditions_tightening.md`) — 메인 REGIME 필터 ON 유지 (본 ADR과 독립)
