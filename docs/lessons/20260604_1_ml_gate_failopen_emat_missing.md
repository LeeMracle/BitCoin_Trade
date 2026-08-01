# lessons #36 — ML 게이트 EMA-TREND 누락 + fail-open 정책 안전망 부재

- 날짜: 2026-06-03 (작성), 2026-06-04 (1차 배포: B9 + B8 scanner / 재작성 + cto re-review 패키지)
- 분류: P1 안전장치 (전략 게이트 누락 + 정책)
- 관련 lessons: #6 (모든 매수 경로 필터 적용), #21 (fail-closed 원칙), #26 (ML 필터 realtime 경로 누락), #37 (5연패 watchdog 회피, B9)

## 배포 이력 (2026-06-04)

- **1차 배포 (B9 + B8 scanner 단독 분리, 2026-06-04 KST)**: lessons #37(B9 5연패 watchdog 회피)이 cooldown_until 만료(6/4 12:04 KST) 전 시급 사안으로 B9 + B8 scanner DC(`multi_trader.py:210~`)만 배포 (PID 1015239). B7(EMA-TREND ML hook), B8 realtime DC(`realtime_monitor.py:1882~`), B8 realtime fail-closed 분기는 **cto P3 review에서 FAIL(#1~#4)** → 재작성 후 후속 배포 예정.
- **최종 배포 명세 (재작성, 2026-06-04, cto re-review 패키지)**: B7(EMA-TREND ML 게이트, `realtime_monitor.py:1224~` 매수 직전 hook) + B8 realtime(`realtime_monitor.py:2005~` fail-CLOSED 분기) + 부팅 readiness probe(`start()` 메서드, ML 상태 1회 로그) 추가. 다른 매수 경로(multi_trader scanner DC)와 동형 패턴. shadow 로그에 `EMA_TREND_breakout` signal_type 신규.
- **재작성 발생 사유**: 1차 재작성 시 engineer가 "B7/B8 적용 완료" 보고했으나 cto 검증 결과 실제 코드에는 0줄 적용(B9 hunk만 존재) — 자기 보고 신뢰 위반. 본 재작성은 변경 후 라인별 grep 5건으로 자기검증(`_get_ml_filter`/`_ml_pass` hook, `fail-open` 잔존, `EMA_TREND_breakout` shadow, `ML-readiness` probe, `pre_deploy_check B7B8_REVIEWED=1 PASS`) 도입 — 보고와 코드 1:1 일치를 grep으로 증명.
- pre_deploy_check 룰 `check_all_buy_paths_ml_gate`, `check_ml_failopen_policy`는 `B7B8_REVIEWED=0` 환경변수 미설정 시 ERROR→WARNING 격하(임시 가드). 본 재작성으로 `B7B8_REVIEWED=1` 실행 시 ERROR 0건 확인 → cto PASS 후 가드 제거 + 영구 ERROR 룰 복구.
- 본 1차 배포에 포함된 `multi_trader.py` B8 scanner DC fail-closed는 lessons #36 B8 정책의 일부이며, 단독 적용도 다른 매수 경로(realtime DC/EMA-TREND)에 영향 없음 — cto 독립 PASS 확인.

## 현상

1. **B7 — EMA-TREND BTC/KRW 매수 경로(`realtime_monitor.py:1223`)에 ML 신호 필터 게이트 미적용.**
   - `multi_trader.py:236` (scanner DC 경로)와 `realtime_monitor.py:1956` (realtime DC 경로)에는 ML 게이트 적용됨
   - EMA-TREND만 누락 — `ML_FILTER_ENABLED=1` 운영 환경에서도 EMA-TREND 매수는 ML 점수 무관하게 통과
   - shadow 로그에도 `signal_type="EMA_TREND_breakout"` 항목 부재 → 사후 분석/매칭 불가

2. **B8 — 모델 로드 실패 시 fail-open 정책(`inference.py:118`, `score=1.0` / `passes()=True`).**
   - `current.pkl` 누락, joblib 호환성 오류, feature 카탈로그 mismatch 중 어느 케이스든 게이트가 "통과"로 fallback
   - 실제 LIVE 운영에서 ML이 안전망으로 의도되는 시점(threshold=0.45 보수 시작)에 안전망 자체가 무효
   - 부팅 시 readiness probe 없음 → 모델 로드 실패가 운영자에게 가시화되지 않음

## 근본 원인

1. **안전장치 정책의 비명문화** — ML 게이트는 도입 당시(2026-05) "차단보다 통과 우선"의 shadow 운영을 위해 의도적으로 fail-open으로 설계됐다. shadow→LIVE 전환(threshold 0.45, [20260505_2_ml_live_acceleration](../decisions/20260505_2_ml_live_acceleration.md))은 진행됐으나 정책 자체(fail-open→fail-closed 전환 기준)는 문서로 명시되지 않아 코드 그대로 잔존.
2. **lessons #26 도메인 해석 불일치** — "모든 매수 경로"에 ML 게이트를 hook해야 한다는 룰은 `multi_trader`/`realtime_monitor` DC 경로 두 곳만 검증하고 EMA-TREND 진입점은 누락. lessons #6/#26은 "DC 돌파"에 한정된 것으로 좁게 해석된 정황.
3. **`pre_deploy_check.check_ml_filter_integrity`는 hook 문자열(`_get_ml_filter`/`get_filter`) 모듈 단위 존재만 검사** — 함수 단위(매수 직전 호출) 검증은 없어 EMA-TREND처럼 같은 파일 내 별도 매수 분기는 빠짐.

## 핵심 교훈

1. **lessons #6/#26은 진입 함수 단위로 강제**되어야 함 — 파일 단위 grep만으론 "한 파일에 N개 매수 분기" 케이스 누락. `buy_market_coin(...)` 호출 부근에 ML 게이트 호출이 있는지 라인 범위 매칭 필요.
2. **안전장치는 기본 fail-closed, 의도적 fail-open만 환경변수(`ML_SHADOW_MODE=1`/`ML_FILTER_ENABLED=0`) 명시** — 정책 우선순위는 (1) shadow > (2) 비활성 > (3) LIVE 모델 부재 시 차단.
3. **모델 로드 readiness probe**: `MLFilter.status` 문자열은 운영 가시성 부족 — 부팅 직후 로그에 ML 게이트 상태 1회 출력 의무.
4. **검증규칙은 hook 존재(파일 단위)와 호출 인접성(라인 단위) 둘 다 검사** — pre_deploy_check 신규 룰 2건으로 영구화.

## 수정 (코드 + 운영 조치)

### 코드 변경 (3개 경로 동시)

| 경로 | 파일 | 변경 |
|---|---|---|
| EMA-TREND | `services/execution/realtime_monitor.py:1216~` | B7 신규 ML 게이트 호출 + B8 fail-closed |
| scanner DC | `services/execution/multi_trader.py:210~` | B8 fail-closed 분기 (모델 로드 실패 + LIVE → 차단) |
| realtime DC | `services/execution/realtime_monitor.py:1882~` | B8 fail-closed 분기 + 추론 예외 시 score=0.0 |

정책 우선순위:
1. `ML_FILTER_ENABLED=0` → 통과 (ML 미도입 환경)
2. `ML_SHADOW_MODE=1` → 통과 (검증 운영, 차단 안 함)
3. `is_active=False` 그 외 → **차단** (fail-closed)
4. `is_active=True` + 추론 예외 → **차단** (score=0.0 강제)
5. `is_active=True` + 정상 → `score ≥ threshold` 평가

### 운영 조치

- 본 변경은 작성·점검까지 — 배포는 PM 승인 후 `hotfix_deploy.sh` 별도 실행
- cooldown 만료(6/5 09:03 KST) 전 배포 윈도우 확보
- SUN/KRW 포지션 관여 금지 (CB L1까지 +11.28% 여유)

## 검증규칙 (pre_deploy_check 신규 룰 2건)

### 룰 1 — `check_all_buy_paths_ml_gate()`

`buy_market_coin(` 호출 전 100라인 이내에 `_get_ml_filter()` 또는 `get_filter()` 호출이 있어야 함. 모든 매수 경로를 라인 단위로 매칭하여 진입 함수 단위 hook 누락 차단. lessons #6/#26 강화.

### 룰 2 — `check_ml_failopen_policy()`

`inference.py`와 매수 경로에서 `score = 1.0` + `will_buy = True` 조합이 무조건 통과 패턴인지 검증. fail-closed 분기(`is_active=False` + `ML_FILTER_ENABLED=1` + `ML_SHADOW_MODE=0` → 차단)가 코드에 존재해야 함.

## 관련 lessons

- #6 (전략 필터는 모든 매수 경로에 적용) — 본 lessons로 진입 함수 단위 강제
- #21 (fail-closed 원칙: 잔고 조회 실패 → 매수 차단) — ML 게이트도 동일 원칙 적용
- #26 (ML 필터 realtime 경로 누락) — 라인 단위 매칭 룰로 확장 강제
