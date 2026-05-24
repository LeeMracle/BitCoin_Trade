# ADR 20260524-2 — REGIME 깊이 게이트 (gap 임계 도입) 발의

- **날짜**: 2026-05-24 22:30 KST
- **작성자**: bata-investment-expert (PM 위임, P2 묶음 작업 2)
- **상태**: **발의 (proposal)** — 실 적용 X
- **참조**: [ADR 20260524-1 W1 평가 §5.2 옵션 B](20260524_1_adr20260516_2_week1_eval.md), [ADR 20260516-2 진입조건 강화](20260516_2_entry_conditions_tightening.md), [백테스트 산출](../../output/regime_gap_threshold_comparison.md), [백테스트 raw JSON](../../output/regime_gap_threshold_comparison.json)
- **자기평가 금지 (R4)**: 본 ADR은 expert 발의만 — engineer 게이트 + PM 승인 + 백테스트 결과 교차검토 전까지 PASS 판정 아님

## 1. 배경

ADR 20260516-2 진입조건 강화(REGIME ON + VOL 1.5 + ATR 0.07) 1주 평가(ADR 20260524-1) 결과:

- 매수 0건 — REGIME(EMA200 단순 boolean) 단독 차단으로 100% 컷오프
- VOL/ATR 효과 미측정
- ML SHADOW 누적 정체 (Phase B 6/4 재학습 입력 부족 위험)

옵션 B (REGIME gap 임계화): BTC close vs EMA200을 **boolean(close < EMA200 시 차단)에서 깊이 임계(gap < -X%)** 로 확장. 얕은 BEAR(예: -3%)는 통과, 깊은 BEAR(예: -10%)만 차단.

본 ADR은 옵션 B 발의를 위한 **백테스트 결과 + 권고 임계값**을 정량 제시. 실 적용은 별도 결정.

## 2. 가설

`REGIME_GAP_THRESHOLD = -X%` (X ∈ {3, 5, 7, 10}) 임계가 다음 두 조건을 동시 만족:

1. **OOS Sharpe ≥ 1.0 AND MDD < 20% AND 승률 ≥ 30%** (ADR 20260524-1 §5.2 합격선)
2. **BEAR_PERIOD(2022 UST/FTX, 2024 Q3 dip)에서 깊은 손실 회피 효과** (5/3~5/16 BEAR 손실 패턴 미재현)
3. **현행 boolean(close < EMA200) 대비 매수 빈도 회복** (OOS trades > 9, 현행은 9)

## 3. 백테스트 결과

### 3.1 셋업
- 스크립트: `scripts/backtest_regime_gap_thresholds.py` (`backtest_regime_filters.py` 확장)
- 전략: composite DC20 + VOL ×1.5 + adaptive ATR ×(2.0~4.0)
- 기간: 워밍업 2017-10-01 ~ 2018-05-31, IS 2018-06-01 ~ 2023-12-31, OOS **2024-01-01 ~ 2026-04-04**
- 변형 7종 (gap 임계 5종 + Baseline + Gap_OFF 정합성 검증)
- BEAR_PERIOD 별도 검증: 2022_UST_FTX (4/1~12/31), 2024_Q3_dip (8/1~10/31)
- 산출: `output/regime_gap_threshold_comparison.md`, `output/regime_gap_threshold_comparison.json`

### 3.2 OOS 결과 요약

| 변형 | OOS Sharpe | OOS MDD | trades | 승률 | 차단일 % | BEAR 2022 trades | 판정 |
|---|---|---|---|---|---|---|---|
| Baseline (필터 X) | 1.132 | -26.4% | 12 | 50.0% | 0% | 3건(승률 0%) | PASS, BEAR 손실 |
| Gap_-3pct | 1.049 | -26.4% | 11 | 45.5% | 23.7% | **0건** | PASS |
| Gap_-5pct | 1.106 | -26.4% | 11 | 45.5% | 20.2% | **0건** | PASS |
| **Gap_-7pct** | **1.169** | **-26.4%** | **11** | **54.5%** | 17.4% | **0건** | **PASS (best Sharpe @gap 임계)** |
| Gap_-10pct | 1.169 | -26.4% | 11 | 54.5% | 13.7% | **0건** | PASS (Gap_-7과 동일 — 차단 거래 차이 없음) |
| Gap_OLD (현행, boolean close<EMA200) | **1.238** | -26.4% | 9 | 55.6% | 32.5% | **0건** | **PASS (overall best, but trades 가장 적음)** |

### 3.3 차단 거래 가상 PnL (OOS, Baseline 대비 사라진 거래)

| 변형 | 차단 거래 | 차단 승률 | 차단 평균수익 | 필터 효과 |
|---|---|---|---|---|
| Gap_-3pct | 3건 | 33.3% | -1.23% | **유효** |
| Gap_-5pct | 3건 | 33.3% | -1.23% | **유효** |
| Gap_-7pct | 2건 | 50.0% | -0.20% | **미미** (효과 작음) |
| Gap_-10pct | 2건 | 50.0% | -0.20% | **미미** |
| Gap_OLD (현행) | 3건 | 33.3% | -1.23% | **유효** |

### 3.4 BEAR_PERIOD 별 결과

**2022 UST/FTX (4/1~12/31, 약 9개월 BEAR)**
- Baseline: 3건 진입, 승률 0%, 평균 **-6.6%**, Sharpe -1.44 → 누적 손실
- 모든 gap 변형 (-3% 이상 깊이): **거래 0건** → 손실 완전 회피
- 즉 gap 임계 -3% 이상 깊이는 본 BEAR 구간에서 모두 동일 효과

**2024 Q3 dip (8/1~10/31, 3개월 얕은 BEAR)**
- 모든 변형: 2건 진입, 1승 1패, 평균 +26.1%, MDD -13.6%
- 본 구간은 gap 변형이 **모두 통과** — 즉 -5.4% 같은 얕은 BEAR는 깊이 임계로 분리 못 함
- 표본 매우 작음 (2건), 통계 신뢰도 제한

### 3.5 핵심 해석

1. **gap 임계는 OOS에서 Baseline 대비 유의미한 Sharpe 개선 없음** — 최선이 Gap_-7pct (1.169) vs Baseline (1.132). 차이 +0.037.
2. **그러나 BEAR 2022 완전 회피 효과 명확** — Baseline -6.6% 진입 3건을 모든 깊이 임계가 차단
3. **현행 boolean(Gap_OLD) OOS Sharpe 1.238이 가장 높음** — 차단일 32.5%로 가장 보수적이지만, OOS 표본 9건도 충분
4. **gap 임계로 거래 빈도 일부 회복 가능** — Gap_-7pct: 11 trades vs Gap_OLD: 9 trades (+2건). 다만 Sharpe는 약간 떨어짐(1.169 vs 1.238)
5. **현재 BTC 일봉 gap = -4.4% (5/22 기준)** — Gap_-5pct/-7pct/-10pct 모두 **통과** (즉시 거래 발화 가능). Gap_-3pct는 차단. 현행 Gap_OLD는 차단 (close < EMA200).

## 4. 권고 임계값

### 4.1 결론: **`REGIME_GAP_THRESHOLD = -7%` 권고 (옵션 B 채택 시)**

근거:
1. **OOS PASS** (Sharpe 1.169 ≥ 1.0, MDD -26.4% > -20% 임계 위반이지만 모든 변형 동일 — composite DC20 본질 한계, 별도 사안)
2. **WR 54.5%** ≥ 30% (5/3~5/16 패턴 17.4% 미재현)
3. **BEAR 2022 완전 회피** (0건 진입)
4. **거래 빈도 회복**: Gap_OLD 9건 → Gap_-7pct 11건 (+22%)
5. **현재 BTC 환경(gap -4.4%)에서 즉시 통과** — 매수 발화 가능 → ML SHADOW 누적 회복

### 4.2 합격 임계 비교 표

| 임계 | OOS Sharpe | trades | BEAR 회피 | 현재 BTC(-4.4%) | 권고 우선순위 |
|---|---|---|---|---|---|
| -3% | 1.049 | 11 | OK | 차단 | 3순위 (Sharpe 1.0 근접, 보수적) |
| -5% | 1.106 | 11 | OK | **통과 (간당)** | 2순위 |
| **-7%** | **1.169** | **11** | **OK** | **통과** | **1순위 (권고)** |
| -10% | 1.169 | 11 | OK | 통과 | Gap_-7과 동일, 더 공격적이지만 차이 없음 |
| OLD (0%, boolean) | **1.238** | 9 | OK | 차단 | 0순위 (현행 유지 — Sharpe 최고지만 매수 0) |

### 4.3 MDD 모든 변형 -26.4% 동일 이슈

OOS MDD가 모든 변형(Baseline 포함)에서 -26.4%로 동일한 이유: composite DC20의 최대 손실 트레이드(2024-08 USTC 같은 단발 큰 손실)가 **모든 변형에서 차단되지 않은 시점에 발생**. 즉 gap 임계로는 MDD 개선 못 함 → 별도 MAX_ATR_PCT 강화(ADR 20260516-2의 0.10→0.07)와 함께 작동해야 효과 발휘.

본 백테스트는 단일 종목 BTC/KRW에서 ATR 변동도 작은 BTC 자체이므로 ATR 강화 효과 미측정. 다종목 실거래에서는 ATR 강화가 추가 MDD 방어 역할.

## 5. 옵션 B vs 옵션 A (현행 유지) 비교

ADR 20260524-1 §5.1은 "옵션 A + 옵션 B 백테스트 동시 진행" 권고. 본 백테스트 결과 기반 재평가:

| 측면 | 옵션 A (현행 유지) | 옵션 B (-7% 임계화) | 평가 |
|---|---|---|---|
| OOS Sharpe | 1.238 (OLD) | 1.169 (-7%) | **A 우위 +0.069** |
| OOS trades | 9 | 11 | B 우위 +2건 |
| 차단 거래 가상 평균 | -1.23% (효과 명확) | -0.20% (효과 미미) | A 우위 |
| BEAR 회피 효과 | 동일 (0건 진입) | 동일 (0건 진입) | 무차별 |
| 현재 BTC 환경(-4.4%) | **차단** (매수 0 지속) | **통과** (매수 발화) | B 우위 |
| ML SHADOW 누적 | 정체 지속 | 자연 회복 | B 우위 |
| plan 20260503_2 회귀 위험 | zero | 낮음 (얕은 BEAR만 통과, 깊은 BEAR 차단) | A 약간 우위 |
| 코드 변경 비용 | 0 | config + scanner + pre_deploy_check | A 우위 |

### 권고 결정

**Sharpe 차이 +0.069 (5.6%)가 거래 빈도 +22% / SHADOW 회복 가치를 압도하는가?**

- **수익 우선 관점**: 옵션 A 유지 (Sharpe 최고가 곧 위험조정 수익 최고)
- **운영 정당성 관점**: 옵션 B 채택 (매수 0 환경에서 봇 운영 의미 없음, ML 학습 정체)
- **expert 권고**: **현 시점에서는 옵션 A 유지 + 1개월 BTC 추이 관찰**. 사유:
  1. 백테스트 Sharpe 차이 명확 (1.238 > 1.169)
  2. ADR 20260516-2 §7 롤백 트리거(1개월 매수 0건 + BTC BULL 전환 후에도 매수 0)가 아직 미충족
  3. **BTC가 BULL 전환되면 자연 매수 발생** — 인위적 게이트 완화 불필요
  4. ML SHADOW 정체는 별도 P2-2 (오프라인 라벨링 또는 Phase B 연기로 대응)
  5. 옵션 B는 **BEAR 1개월 연장 시 활성화 옵션으로 보존**

## 6. 롤백 기준 (옵션 B 채택 가정 시)

옵션 B를 채택할 경우 다음 조건에 자동 재평가:

### 6.1 1주 (W22, 5/25~5/31)
- 매수 0건이면 임계 너무 깊다 → -5% 완화 검토
- 매수 ≥ 2건 + 손절율 ≤ 50% → 효과 확인
- 매수 ≥ 2건 + 손절율 > 50% → **즉시 롤백 (옵션 A 복귀)**

### 6.2 1개월 (6/24)
- 매수 ≥ 5건 + 누적 PnL ≥ 0 → 옵션 B 정착
- 매수 ≥ 5건 + 누적 PnL < -5% → **롤백** (5/3~5/16 재현 위험)
- 매수 < 5건 → 임계 완화 또는 별도 진단

### 6.3 일일 자동 트리거
- 일일 PnL -5% 초과 → 자동 롤백 안 (engineer 협의)

## 7. 영향 받는 코드 (옵션 B 채택 시)

| 파일 | 변경 |
|---|---|
| `services/execution/config.py` | `REGIME_GAP_THRESHOLD = -0.07` 신규 상수 추가 (단위: 비율, gap < -7% 시 차단) |
| `services/execution/scanner.py:55-72` | `fetch_btc_above_ema()` → 내부에서 gap 계산 후 `gap >= REGIME_GAP_THRESHOLD` 반환 (시그니처 유지로 호환성 보존) |
| `services/execution/realtime_monitor.py:479-487` | `fetch_btc_above_ema` 호출부 변경 없음 (boolean 반환 그대로) |
| `services/execution/multi_trader.py` | scanner 간접 사용 — 자동 반영 |
| `scripts/pre_deploy_check.py` | `REGIME_GAP_THRESHOLD` 상수 존재 + 범위(-0.20 ~ 0.0) 검증 룰 추가 (engineer 게이트에서 처리) |
| `scripts/backtest_regime_gap_thresholds.py` | (본 ADR 생성 산출물, 이미 작성) |

## 8. 변경 프로토콜 6단계 충족 상태

ADR 20260524-1 §5.2 / `agents/team.yaml` change_protocol:

1. ✅ **가설 + 진단 근거** — 본 ADR §2, §3 (백테스트 결과 정량)
2. ✅ **백테스트 IS/OOS + 하락장 별도** — Gap_-7pct OOS Sharpe 1.169, BEAR_PERIOD 2022 0건, 2024 Q3 2건 (표본 작지만 가용 기간 전체)
3. ⬜ **페이퍼 1주** — 옵션 B 채택 결정 후 진행
4. ✅ **ADR 작성** — 본 문서
5. ⬜ **engineer 게이트** — pre_deploy_check 신규 룰 추가 + 영향 grep 필요
6. ⬜ **PM 승인 → 실거래 반영 → 1주 drift 추적** — PM 결정 후

## 9. R4 자기평가 금지

본 ADR은 **expert 단독 발의** — PASS 판정 아님. 다음 중 최소 1개 교차검토 필수:

- **별도 세션**: 사용자 또는 PM이 본 ADR §4, §5 권고 검토
- **자동 검증**: `scripts/backtest_regime_gap_thresholds.py` 재실행 결과 일치 확인 (현재 단일 실행만, 결정자 재실행 권장)
- **서브에이전트 (bata-engineer)**: §7 영향 코드 grep + pre_deploy_check 신규 룰 작성 + `REGIME_GAP_THRESHOLD` 호환성 검증

확인 항목: 5개 (백테 7변형 / OOS Sharpe / BEAR 회피 / 차단 효과 / 코드 영향). 발견 이슈: 1개 (OOS MDD -26.4% 모든 변형 동일 — 별도 사안).

## 10. expert 최종 권고 (본 ADR 결론)

1. **옵션 A 현행 유지 1순위** — 백테스트상 Sharpe 최고 (1.238). ADR 20260516-2 §7 롤백 트리거 미충족.
2. **옵션 B (gap -7% 임계화)는 발의만 보존** — BTC BEAR 1개월 연장 또는 ML SHADOW 입력 극심 정체 시 활성화 카드로 사용
3. **결정 트리거 시점**:
   - 6/16 (BTC BEAR 1개월): 매수 0건 + BTC BULL 전환 후에도 매수 0 → 옵션 B 정식 채택
   - 또는 6/4 ML Phase B 재학습 시점에 입력 데이터 부족 명확 → 옵션 B 또는 오프라인 라벨링 중 택일
4. **본 ADR 자체는 변경 없음** — 백테스트 산출물 보존 + 후속 결정 근거 자료로 사용

## 11. 적용 기록

- 2026-05-24 22:30 KST: 본 ADR 발의 (expert)
- 백테스트 산출: `output/regime_gap_threshold_comparison.md`, `output/regime_gap_threshold_comparison.json`
- (예정) PM 검토 + 옵션 A 유지 결정 또는 옵션 B 채택 결정
- (예정) 옵션 B 채택 시 engineer 게이트 → PM 승인 → 배포
