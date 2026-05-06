# ADR 20260504-2: ML 신호 품질 필터 도입 (XGBoost, fail-open)

- **상태**: 채택 (Phase 3 보강, Shadow 단계 진입)
- **작성일(KST)**: 2026-05-04
- **관련 plan**: [workspace/plans/20260504_3_ml_signal_filter.md](../../workspace/plans/20260504_3_ml_signal_filter.md)
- **선행 문서**: [output/ml_signal_filter_architecture.html](../../output/ml_signal_filter_architecture.html)

## 컨텍스트

DC(15)+ATR×3.0 전략은 OOS에서 검증되었으나 라이브 표본은 작고 승률 변동 큼.
백테스트 trade log 수년치를 활용해 **각 매수 신호의 +5% 도달 확률을 점수화**하고 필터로 사용하면, 동일 전략의 승률 ↑ / MDD ↓ 가능성이 큼.
가격 예측이 아닌 **신호 품질 스코어링**이 핵심.

## 결정

1. **모델**: XGBoost binary classifier (객체화 0.55 threshold).
2. **Feature**: 18개 (RSI/ATR/EMA거리/DC돌파강도/거래량비/시간/BTC컨텍스트/최근성과/메타).
3. **라벨**: entry 후 96봉(15분봉 4일) 내 entry × 1.052 도달 여부 (slippage 0.2% 포함).
4. **검증**: sklearn `TimeSeriesSplit` walk-forward (6 folds).
5. **저장 형식**: `.pkl` + `.meta.json` 페어, `current` 심볼릭 링크 원자 전환.
6. **운영 정책**: **fail-open**
   - 모델 부재/로드 실패/추론 예외 → score=1.0 → passes=True (기존 매수 로직 우회)
   - lessons #21 "fail-closed"의 역케이스: ML이 "추가" 검증이지 "필수" 검증이 아니므로
7. **활성 토글**: 환경변수 `ML_FILTER_ENABLED=1` (기본 OFF).
8. **Shadow Mode**: 모든 의사결정 JSONL 기록, 3개월 누적 후 outcome 매칭으로 효과 평가.
9. **학습 vs 추론 분리**:
   - 학습: 로컬 PC (XGBoost+Optuna+SHAP — `requirements-ml.txt`)
   - 추론: AWS t3.micro (xgboost+sklearn+joblib만)
   - 모델 파일만 `scripts/deploy_model_to_aws.sh`로 전송

## 대안 검토

| 대안 | 결정 | 이유 |
|---|---|---|
| LSTM/Transformer 가격 예측 | 기각 | 노이즈 학습 위험, 라이브 효과 불확실 |
| RL (강화학습) 매매 정책 | 기각 | 자본으로 학습은 사고 직행, 표본 부족 |
| LightGBM | 보류 | XGBoost 검증 우선, 후속 비교 가능 |
| AutoML (auto-sklearn 등) | 기각 | 시계열 non-IID/regime shift 무시 |
| Feast (feature store) | 기각 | 개인 프로젝트 과잉, Parquet으로 충분 |

## 결과 (예상)

- 승률 +5~10%p, MDD -1~3%p (보수적, walk-forward 시뮬 기준)
- AWS 메모리 +50MB (모델 상주), p95 추론 latency < 50ms
- 학습 cron 월 1회 (로컬), 모델 배포는 수동/스크립트

## 리스크 & 완화

| 리스크 | 완화 |
|---|---|
| Lookahead bias | `compute_features` 내 at_ts cutoff + assertion |
| Overfit (상승장) | walk-forward + 하락장 fold 별도 평가 |
| Concept drift | Evidently 주간 PSI 리포트, drift 임계 시 재학습 |
| 모델 버전 충돌 | meta.json에 hash/feature_columns/train_period 기록 |
| 매수 차단 false positive | Shadow 3개월 → A/B → Live 단계적 도입 |

## 후속 작업 (롤아웃)

1. **W1~W4**: 코드 구현 완료 (S1~S6) — 본 ADR 기준
2. **W5~W12**: Shadow mode 운영 (점수만 기록, 차단 X)
3. **W13~W16**: A/B (50% 신호만 ML gate)
4. **W17+**: Live (ML gate 우위 통계 입증 시) + 동적 사이징
5. **상시**: 월 1회 재학습 + 주간 drift 리포트
