# ADR 20260515-1 — ML 신호 필터 운영/개선 로드맵

- **날짜**: 2026-05-15 (KST)
- **결정자**: 자비스 (PM Orchestrator)
- **상태**: 시행 중 — Phase A 조기 종료, SHADOW 전환 (2026-05-16 KST 15:18)
- **참조**: ADR 20260504-2 ML 신호 필터 도입, ADR 20260505-2 ML LIVE 가속, 5/14 세션 로그

## 1. 현재 상태 (2026-05-15)

### 학습/추론 구조
| 단계 | 처리 | 산출물 |
|------|------|--------|
| OHLCV 수집 | ccxt → `data/cache.duckdb` (4h봉, 11종목) | duckdb 테이블 |
| Entry 추출 | DC(15) 상단 돌파 시점만 | 후보 시점 리스트 |
| Feature 계산 | 추세/모멘텀/변동성 + BTC 컨텍스트 + F&G | parquet row |
| 라벨링 | 24봉(=4일) 내 +5%(+0.2% 슬리피지) 도달 여부 | label 0/1 |
| 학습 | XGBoost + Walk-Forward CV (TimeSeriesSplit) | `current.pkl` |
| 추론 | AWS 매수 신호마다 `current.pkl`로 P(상승) 계산 | threshold 게이트 |
| 평가 | shadow JSONL → outcome_matcher cron (4h봉 high 매칭) | PF/차단률/주간 리포트 |

### 운영 지표 (5/14 시점)
- threshold: 0.45 → **0.40** (5/14 완화)
- 직전 1주: 매수 0건, 차단률 99.1%, PF 0.42 — ADR 5-12 롤백 조건 다수 명중
- 다음 평가: 5/19 04:00 KST 자동 ml_weekly_review

### 핵심 문제
1. **학습 데이터와 실거래 분리** — shadow outcome이 학습에 피드백되지 않음 (parquet 미연동)
2. **라벨이 너무 엄격** — DC(15) 돌파 시점에 4일내 +5% 도달은 backtest 평균 보유기간(7~10일)과 불일치 → positive_rate 낮음 → 모델이 과보수
3. **재학습 수동** — 6/4 v3 1회 예정. 데이터 변화 반영 주기 ≥ 1개월
4. **단일 모델 의존** — DC 전략 외 다른 신호(RSI/EMA Trend) 케이스 미커버

## 2. 결정 (3-Phase 로드맵)

### Phase A — 즉시 (5/15~5/26, 2주)
**목표**: threshold 0.40 효과 검증 + SHADOW 모드 polling 옵션 확보

| 항목 | 액션 | 기준 |
|------|------|------|
| A-1 | 5/19 ml_weekly_review 자동 발송 결과 검토 | 매수 ≥ 5건 + PF ≥ 1.0 → 0.40 유지 |
| A-2 | 5/19~5/26 동안 매수 < 3건이면 SHADOW 모드 임시 전환 | `ML_SHADOW=1, ML_ENABLED=0` 1주 운영 |
| A-3 | shadow JSONL 누적 → outcome 매칭 정상 동작 검증 | `ml_outcome_match.py` 매일 cron 결과 카운트 |
| A-4 | 의사결정/outcome JSONL을 학습 소스로 변환할 수 있는 어댑터 스크립트 작성 (실행은 Phase B) | `scripts/ml_shadow_to_features.py` 초안 |

**NO-GO 기준**: 5/26까지 매수 0건이면 threshold 0.35 추가 완화 또는 ML 게이트 비활성(SHADOW only)로 전환.

### Phase B — 1개월 (5/27~6/15, 3주, v3 재학습)

**목표**: 라벨 정의 개선 + 학습 소스 확장 + 자동 재학습 cron

| 항목 | 액션 | 결과물 |
|------|------|--------|
| B-1 | **라벨 재정의** — 현재 "4일 +5% 도달"을 두 가지로 분리:<br>(a) 기존 라벨 유지 (positive_short)<br>(b) **+3% / 7일 도달** (positive_swing — DC 평균 보유기간 반영)<br>두 라벨 OR로 합쳐 positive_rate 회복 | `services/ml/labeler.py` 라벨 함수 추가 |
| B-2 | **학습 소스 확장** — 백테스트 trade_log.csv를 `ml_build_dataset.py`에 추가 옵션으로 통합 | `--include-backtest workspace/runs/...` |
| B-3 | **shadow → feature_store 어댑터 가동** (Phase A-4 어댑터) — 실거래 outcome 매칭 결과를 feature_store에 일 1회 적재 | cron 매일 03:30 KST (outcome_match 직후) |
| B-4 | **v3 모델 재학습** — 확장된 데이터로 walk-forward CV. 기준: mean AUC ≥ 0.62 | `signal_filter_v3.pkl` |
| B-5 | **threshold 동적 결정** — v3 학습 직후 직전 1개월 데이터 OOS로 simulate, PF 최대 threshold 자동 산출 | meta.json에 `optimal_threshold` 필드 |

**Promotion 기준**: v3 mean AUC > v2 + 0.03 AND simulate PF ≥ 1.2. 미달 시 v2 유지 + 데이터만 누적.

### Phase C — 장기 (6/16~7/31)

**목표**: 자동화 완성 + 모델 다양성

| 항목 | 액션 |
|------|------|
| C-1 | 월 1회 자동 재학습 cron (로컬 PC, 1일 새벽 03:00 KST) — `ml_train.py + deploy_model_to_aws.sh` |
| C-2 | ML 게이트 적용 신호 확장 — DC 외 RSI/EMA Trend 신호도 게이트 적용 (lessons #6, plan 20260504_3) |
| C-3 | 모델 ensemble 검토 — XGBoost + LightGBM 평균 (분산 감소) |
| C-4 | regime-aware threshold — F&G 구간별 threshold 분기 (BULL=0.35, NEUTRAL=0.40, BEAR=0.50) |

## 3. 결정의 근거

### 왜 라벨 재정의를 우선하는가
- 현재 +5%/4일 라벨은 DC backtest 평균 trade(보유 7~10일, 평균 +6~12%)와 시간축 불일치
- positive_rate 낮음 → 모델이 "안전하게 0 예측"하는 학습 편향
- +3%/7일 OR 추가는 **현실 매매 결과와 정렬** → threshold가 낮아도 정확도 ↑ 기대

### 왜 shadow→학습 피드백이 Phase B인가
- Phase A는 정상 데이터 누적 검증 단계 — 잘못된 매칭이 학습에 들어가면 v3 오염 위험
- 어댑터 스크립트 먼저 작성(A-4) → 1주 dry-run 후(B-3) 가동
- shadow 표본은 매우 작으므로(주 0~5건) 백테스트 데이터의 보조 역할

### 왜 자동 재학습이 Phase C인가
- v3 결과 확인 후 안정성 입증 필요 — Phase B에서 1회 수동 검증
- 자동 재학습은 모델 회귀 시 silent fail 위험 → 회귀 감지 룰(AUC 임계) 필수, 이건 Phase C로 분리

## 4. 즉시 실행 액션 (5/15)

| # | 액션 | 담당 |
|---|------|------|
| 1 | 본 ADR 사용자 confirm | 사용자 |
| 2 | confirm 후 Phase A-1, A-3, A-4 진행 (코드 변경 없음, 모니터링 + 어댑터 초안만) | 자비스 |
| 3 | dust 알람 픽스 배포(`scripts/deploy_to_aws.sh`) — 별건 lessons #29 | 자비스 |

## 5. 측정 지표

| 지표 | Phase A 목표 | Phase B 목표 | Phase C 목표 |
|------|-------------|-------------|-------------|
| 주간 매수 건수 | ≥ 3건 | ≥ 5건 | ≥ 5건 |
| 주간 PF | ≥ 0.8 | ≥ 1.2 | ≥ 1.5 |
| 모델 AUC (CV) | 0.58 (v2) | 0.62 | 0.65 |
| 재학습 주기 | 수동 | 수동 1회 | 자동 월 1회 |
| 데이터 소스 | OHLCV+DC | +backtest+shadow | 동일 + ensemble |

## 5.1. 시행 기록 (2026-05-16 KST 15:18)

### Phase A 조기 종료 — SHADOW 전환 결정

5/19 ml_weekly_review cron 자동 트리거를 기다리지 않고 사전 평가 수행 결과 NO-GO 다수 명중.

**평가 (5/9~5/16, 7일):**
| 지표 | 값 | 판정 |
|------|-----|------|
| 실거래 closed | 4건 (승 1 / 패 3) | 표본 부족이지만 추세 명확 |
| 승률 | 25.0% | — |
| PF | **0.23** | 🔴 NO-GO (<0.95) |
| ML 차단률 | 100.0% (2,803건 중 1건만 buy) | 과보수 |
| Accuracy | 42.1% (TP 0/FP 0/TN 8/FN 11) | 동전 던지기 이하 |
| FN | **11건** — ML이 차단한 11건이 실제로 +5% 도달 | 좋은 기회 놓침 |
| 자동 권고 | 🔴 **SHADOW 복귀 권장** | — |

### 적용
- AWS `/etc/systemd/system/btc-trader.service.d/ml.conf`:
  - `ML_FILTER_ENABLED=1 → 0`
  - `ML_SHADOW_MODE=0 → 1`
  - `ML_FILTER_THRESHOLD=0.40` 유지 (참고용)
- 백업: `ml.conf.bak_20260516`
- `sudo systemctl daemon-reload && sudo systemctl restart btc-trader`

### 효과
- 매수 차단 0 (filter 비활성) — DC/RSI/EMA Trend 신호 그대로 실행
- shadow JSONL은 계속 누적 (`ML_SHADOW_MODE=1`) → Phase B v3 학습 데이터 확보 지속

### Phase 진행 영향
- **Phase A 종료** — A-1/A-2 평가 완료, A-3/A-4 검증 완료 (어댑터 25행 변환 가능 확인)
- **Phase B 가속** — 학습 데이터 부족 명확, 6/4 v3 재학습이 더욱 critical
- **다음 자동 트리거**: 5/19 04:00 KST ml_weekly_review — SHADOW 모드로 차단률 0 확인이 정상

### 복귀 조건
- v3 모델이 walk-forward CV mean AUC ≥ 0.62 AND OOS 1개월 simulate PF ≥ 1.2 만족 시
- 또는 사용자 명시 지시

## 6. 리스크 / 폐기 조건

- **Phase A 폐기**: 5/26까지 매수 0건 + SHADOW 전환 후에도 outcome 표본 부족 시 → ML 게이트 자체 무기한 보류, DC 전략 단독 운영 검토
- **Phase B 폐기**: v3 promotion 기준 미달 + 다음 회차에도 미달 → 라벨/feature 재설계 (Phase B 재시작)
- **Phase C 폐기**: 자동 재학습이 모델 품질 회귀 유발 → 수동 재학습으로 영구 회귀
