# ML 신호 품질 필터 시스템 구축 (Phase 3 보강)

- **작성일(KST)**: 2026-05-04 (시각 업데이트 시 보정)
- **작성자/세션**: 자비스 (사용자 요청)
- **예상 소요**: 5~7일 코드 (S1~S7), + 3개월 Shadow 검증
- **관련 이슈/결정문서**:
  - [output/ml_signal_filter_architecture.html](../../output/ml_signal_filter_architecture.html) (아키텍처)
  - [decisions/20260426_1_dc15_switch.md](../../docs/decisions/20260426_1_dc15_switch.md)
  - [decisions/20260504_1_three_strategy_enhancements.md](../../docs/decisions/20260504_1_three_strategy_enhancements.md)
  - [plans/20260503_2_enable_trading_in_bear.md](20260503_2_enable_trading_in_bear.md) (레짐 필터 해제)

## 1. 목표

수년치 백테스트 데이터로 학습한 **신호 품질 스코어링 모델(XGBoost)**을 도입해, DC(15) 매수 신호 발생 시 "이 매수가 +5% 도달 확률"을 0~1 점수로 산출하고 **fail-open 방식**으로 매수 게이트로 사용한다. 가격 예측이 아닌 **신호 필터링**.

## 2. 성공기준 (Acceptance Criteria)

작업 종료 시 모두 충족해야 PASS.

- [ ] `services/ml/` 디렉터리에 6개 모듈(features, feature_store, labeler, trainer, inference, shadow) 작성 완료, 각 모듈 단독 import 무오류
- [ ] `requirements-ml.txt` 신설, AWS용 `requirements.txt`와 분리(추론 패키지만 AWS)
- [ ] `python scripts/ml_train.py --dry-run` 실행 시 feature → 라벨 → fit → 저장 전 과정 무오류 (실데이터 없어도 dummy로 실행 가능해야 함)
- [ ] `MLFilter.score()`가 dummy 모델로 0~1 float 반환, 모델 파일 부재 시 `passes()`가 항상 `True` 반환 (fail-open)
- [ ] `multi_trader.py` 매수 분기에 ML gate hook 삽입 — 환경변수 `ML_FILTER_ENABLED` 미설정 시 기존 동작 100% 보존
- [ ] `scripts/deploy_model_to_aws.sh` 작성, 모델 .pkl + .meta.json만 rsync (코드 미포함), 심볼릭 링크 원자 갱신
- [ ] `scripts/pre_deploy_check.py`에 ML 검증룰 3개 이상 추가 (모델 메타 존재/threshold 범위/feature 목록 일치)
- [ ] ADR 1건 작성 (`docs/decisions/20260504_2_ml_signal_filter.md`)
- [ ] 교차검증: `cto` 서브에이전트로 코드리뷰, "확인 항목 N개 / 발견 이슈 M개" 기록

## 3. 단계 (S1~S7)

| # | 단계 | 산출물 | 보고 시점 |
|---|---|---|---|
| **S1** | Plan + 디렉터리/requirements 셋업 | 본 파일, `services/ml/__init__.py`, `requirements-ml.txt` | S1 종료 시 |
| **S2** | features.py + feature_store.py | feature 30+개 + parquet IO | S2 종료 시 |
| **S3** | labeler.py | 백테스트→라벨 변환 | S3 종료 시 |
| **S4** | trainer.py + scripts/ml_train.py | 학습 파이프라인 + dummy 학습 OK | S4 종료 시 |
| **S5** | inference.py (MLFilter) + shadow.py | 추론/shadow 모듈 | S5 종료 시 |
| **S6** | multi_trader 통합 + deploy_model_to_aws.sh | gate hook + 배포 | S6 종료 시 |
| **S7** | ADR + lessons + pre_deploy_check + cto review | 문서 + 교차검증 | S7 종료 시 |

## 4. 리스크 & 사전 확인사항

| 리스크 | 완화 |
|---|---|
| **Lookahead bias** (학습 시점에 미래 데이터 누설) | feature 계산 함수 내 `bar_close_ts ≤ now` assertion + pandera 스키마 강제 |
| **상승장 overfit** (lessons #2) | walk-forward CV + 하락장 fold 별도 평가 메트릭 출력 |
| **config 동기화 누락** (lessons #4, #19) | threshold, 모델 경로 → `services/ml/config.py` 단일 출처 + grep 검증 |
| **모델 부재로 매수 차단** (lessons #21 fail-closed 역케이스) | **fail-open**: 모델 없으면 ML 우회, 기존 로직만 (안전망) |
| **추론 latency 과대** | 모델 프로세스 시작 시 1회 로드 + 메모리 상주, 50ms SLO |
| **cron 학습 silent fail** (lessons #23) | (Phase 후반) 학습 cron + heartbeat 파일 |
| **AWS 메모리 폭증** (lessons #5) | XGBoost 모델 ~5MB, predict ~50MB RAM 사용 — 사전 측정 필수 |
| **운영 매수 경로 누락** (lessons #6) | scanner + realtime_monitor 두 경로 모두 hook 검토 (S6) |
| **자체정의 상수 분산** (lessons #19) | `ML_FILTER_ENABLED`, threshold 등 `services/ml/config.py`에 단일 정의, 모듈은 import만 |

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [ ] 옵션 A — 별도 세션
- [x] 옵션 B — 서브에이전트(`cto` review) — S7에서 실행
- [x] 옵션 C — 자동 검증 스크립트: `scripts/pre_deploy_check.py` (ML 검증룰 추가)
- [ ] 옵션 D — 다른 모델 (선택)

**검증 기록 형식 (필수, S7 종료 시 채움)**
```
검증 주체: B (pdca-qa 서브에이전트, 별도 세션) + C (pre_deploy_check)
확인 항목: 14개 (성공기준 9 + fail-open 경로 3 + lookahead + lessons #4/#6/#19)
발견 이슈: 3개 (1차 QA)
  - [MAJOR] realtime_monitor._execute_buy ML hook 누락 (lessons #6 위배) → 수정
  - [MINOR] MAX_POSITIONS multi_trader=7 vs config=10 불일치 → 수정 (config import 통일)
  - [MINOR] pre_deploy_check가 realtime_monitor hook 누락을 감지 못함 → 검증룰 추가
판정: 1차 FAIL → 수정 후 PASS (회귀: pre_deploy_check 전체 통과, syntax/import OK)
```

## 6. 회고 (작업 종료 후 작성)

- **결과**: PASS (수정 후)
- **원인 귀속**: 실행 결함 (Plan §4에 "scanner+realtime_monitor 두 경로" 명시됐으나 S6에서 한 곳만 구현)
- **한 줄 회고**: "모든 매수 경로" 안전장치는 신규 모듈 추가 전 `grep buy_market`로 경로 열거 + task별 분리 필수
- **후속 조치**:
  - lessons #26 신설: [20260504_3_ml_filter_realtime_path_missing.md](../../docs/lessons/20260504_3_ml_filter_realtime_path_missing.md)
  - pre_deploy_check 자동 감지 룰 강화 완료
  - CLAUDE.md 주요 교훈 표에 #25 추가 (별도 작업)
  - W5~W12 Shadow mode 운영 데이터 수집 (별도 cron 설정 필요)

---

## 부록: 디렉터리 구조 (목표)

```
services/ml/
  __init__.py
  config.py            # ML_FILTER_ENABLED, THRESHOLD, MODEL_PATH 단일 출처
  features.py          # 학습/추론 공용 feature 계산
  feature_store.py     # parquet read/write
  labeler.py           # 백테스트 trade → 라벨
  trainer.py           # XGBoost + walk-forward
  inference.py         # MLFilter 클래스 (운영 진입점)
  shadow.py            # shadow mode 로거

data/
  features/{YYYYMMDD}.parquet
  models/
    signal_filter_v{N}.pkl
    signal_filter_v{N}.meta.json
    current.pkl  → 심볼릭 링크 (윈도우는 복사)

scripts/
  ml_train.py            # 로컬 학습 진입점
  ml_evaluate.py         # 검증 리포트
  deploy_model_to_aws.sh # 모델 파일만 rsync
  pre_deploy_check.py    # (수정) ML 검증룰 추가

docs/decisions/
  20260504_2_ml_signal_filter.md  # ADR
```
