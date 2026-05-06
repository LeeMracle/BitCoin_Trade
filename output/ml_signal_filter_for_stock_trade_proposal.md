# Stock_Trade(KATA) ML 신호 필터 도입 제안

> **작성일**: 2026-05-05 (KST)
> **작성자**: 자비스 (BATA 운영 사례 기반)
> **대상**: Stock_Trade 운영 담당
> **목적**: BATA(비트코인 자동매매)에 도입한 ML 신호 품질 필터를 Stock_Trade(한국 주식 자동매매)에도 적용 시 효과/타당성 평가

---

## 0. TL;DR (3줄 요약)

1. **BATA에서 ML 게이트 도입 → OOS 89일 시뮬 PF 0.98 → 1.17 입증** (29.7%p 개선)
2. **Stock_Trade에도 동일 패턴 적용 가능** — 단, 한국 주식 특성 반영한 feature 재정의 필요
3. **권고**: 6~8주 PoC (백테스트 → Shadow → LIVE), 표본 200건+ 누적 후 본격 도입

---

## 1. BATA ML 시스템 핵심 (Stock_Trade에 그대로 가져올 수 있는 부분)

### 아키텍처 (코드 그대로 재사용 가능)
```
신호 발생 (DC 돌파 등)
  ↓
사전 필터 (서킷/F&G/거래량/ATR)
  ↓
ML hook (services/ml/inference.py)
  ├─ OHLCV 자동 fetch + 60s LRU 캐시
  ├─ 23 feature 계산 (compute_features)
  ├─ XGBoost 모델 추론 → score 0~1
  ├─ shadow JSONL 로깅
  └─ score >= threshold ? 매수 진행 : 차단
  ↓
매수 실행
```

### 운영 정책 (그대로 적용 가능)
- **fail-open** — 모델 부재/오류 시 항상 통과 (안전망)
- **`ML_FILTER_ENABLED`/`ML_SHADOW_MODE`/`ML_FILTER_THRESHOLD`** 환경변수 토글
- **Shadow Mode** — 점수만 기록, 차단 X (안전한 데이터 누적)
- **OOS 백테스트** — 학습 cutoff 후 기간으로 효과 사전 입증

### 핵심 인프라
- 학습 (로컬): `services/ml/{features,labeler,trainer}.py` + `scripts/ml_train.py`
- 추론 (서버): `services/ml/inference.py`
- 분석: `scripts/ml_outcome_match.py`, `ml_effect_analysis.py`, `ml_weekly_review.py`
- 모델 배포: `scripts/deploy_model_to_aws.sh` (모델 파일만 rsync)

→ **services/ml/ 디렉터리 통째로 Stock_Trade에 복사 후 일부 수정**으로 도입 가능.

---

## 2. 시장 특성 차이 — Stock_Trade에 맞게 조정해야 할 부분

| 항목 | BATA (비트코인) | Stock_Trade (한국 주식) | 영향 |
|---|---|---|---|
| **거래 시간** | 24/7 | 평일 09:00~15:30 KST | feature `hour_of_day`/`is_weekend` 의미 변화 |
| **종목 풀** | Upbit KRW 약 200개 | KOSPI+KOSDAQ 약 2,500개 | feature engineering 부담 ↑, 선별 필요 |
| **신호 주기** | 4h/15m | 일봉/시간봉 (장중) | OHLCV timeframe 재정의 |
| **변동성** | 높음 (일 5~15%) | 낮음 (일 1~5%) | ATR 임계 조정 + label horizon 조정 |
| **시장 컨텍스트** | BTC 추세 / F&G 지수 | KOSPI/KOSDAQ 추세 / VIX 한국판 | feature 대체 |
| **시간 외 갭** | 거의 없음 | 익일 시가 갭 큼 | 라벨링 시 갭 처리 정책 필요 |
| **결제** | 즉시 | T+2 | 거래 빈도 모델링에 영향 |
| **장 시작 동시호가** | — | 09:00 동시호가 변동성 큼 | 9시 첫 봉 노이즈 처리 |

→ **모델은 같은 XGBoost 사용하되, feature 카탈로그(23개)는 한국 주식용으로 재정의 필요**.

---

## 3. Stock_Trade용 Feature 카탈로그 제안 (24개)

### 기술 지표 (BATA 9개 그대로)
- `rsi_14` `atr_14_pct` `ema_dist_50` `ema_dist_200`
- `dc_breakout_strength` `vol_ratio_20`
- `macd_hist` `bb_width_20` `stoch_k_14`

### 시장 컨텍스트 (한국 시장 맞춤)
| BATA | → | Stock_Trade |
|---|---|---|
| `btc_trend_30d` | → | `kospi_trend_30d` (KOSPI 30일 수익률) |
| `btc_dominance` | → | `kospi_kosdaq_ratio` (코스피/코스닥 시총비) |
| `fear_greed` | → | `vkospi` (VKOSPI 변동성지수, 한국 VIX) |
| `btc_corr_30d` | → | `kospi_corr_30d` (개별종목과 KOSPI 상관) |
| `daily_ema200_dist` | (동일) | `daily_ema200_dist` |

### 종목 메타 (한국 시장 특화)
- `market_cap_bucket` (시가총액 구간: 대/중/소형주) — Upbit rank 대체
- `volume_krw_5d` (5일 평균 거래대금) — 일별 거래대금
- `days_since_listing` (그대로)

### 시간/캘린더 (한국 시장)
- `hour_of_day` (09~15시만) — 의미 다름
- `day_of_week` (0~4 평일만)
- `is_post_open` (장 개시 30분 내 여부) — 동시호가 변동성
- `is_pre_close` (장 마감 30분 내 여부) — 종가 베팅

### 최근 성과 (3개 그대로)
- `last_7d_return` `max_drawdown_30d` `consecutive_up_days`

### 한국 주식 추가 feature (3~4개)
- `foreign_net_buy_5d` (외국인 5일 순매수, ka10059) — **한국 시장 핵심 지표**
- `institution_net_buy_5d` (기관 5일 순매수)
- `program_buy_ratio` (프로그램 매매 비율, 시장 전체)
- `theme_strength` (해당 종목 테마 강도, 선택)

→ 외국인/기관 수급은 한국 시장에서 **단일 종목 강도의 가장 강력한 신호** 중 하나. BATA에는 없는 강점.

---

## 4. 도입 시 기대 효과

### BATA OOS 백테스트 결과 (참고)
| 시나리오 | PF | Expectancy | 누적 (89일) |
|---|---|---|---|
| 무필터 | 0.98 | -0.05% | -8.6% |
| ML 0.45 | 1.04 | +0.09% | **+8.9%** |
| ML 0.55 | 1.17 | +0.34% | **+21.1%** |

### Stock_Trade 추정 (한국 주식 특성 반영)
- 한국 주식은 BATA보다 **변동성 낮음** → 절대 수익률 작지만 안정성 ↑
- 외국인/기관 수급 feature 추가 효과 → **AUC 0.6+ 가능 추정** (BATA 0.553)
- **PF 1.0 → 1.2~1.3 개선 가능** (보수적 추정)

### 정량 가설
- 100거래 기준: 무필터 누적 -5% → ML 적용 +10~15%
- 거래 빈도는 30~50% 감소 (가짜 돌파 차단)
- 평균 수익/손실 비율 (R-multiple) 0.8 → 1.3 개선

---

## 5. 도입 단계 (6~8주 PoC)

### Phase 1: 데이터 인프라 (1~2주)
- [ ] Stock_Trade `services/ml/` 디렉터리 신설 (BATA 복사 후 수정)
- [ ] KRX 일봉 OHLCV 수집 (키움 ka10081~83)
- [ ] 외국인/기관 수급 데이터 수집 (ka10059, ka10063, ka10066)
- [ ] DuckDB cache 스키마 정의 (`ohlcv`, `investor_trading`, `index_macro`)

### Phase 2: Feature + 라벨 (1주)
- [ ] `services/ml/features.py` — 24 feature 함수 (한국 주식용)
- [ ] `services/ml/labeler.py` — 한국 주식 horizon (T+2 결제 고려, 5거래일 = +5% 도달)
- [ ] dummy 데이터셋 + 첫 학습 검증

### Phase 3: 학습 + OOS 검증 (1~2주)
- [ ] 백테스트 데이터로 학습 (KOSPI 200 + KOSDAQ 100 = 300종목 × 3년)
- [ ] OOS 60일 시뮬 (학습 cutoff 후 기간)
- [ ] PF/AUC 검증 — PF ≥ 1.1 입증 시 다음 단계

### Phase 4: Shadow Mode (2주)
- [ ] Stock_Trade 봇에 ML hook 통합 (multi_trader 패턴)
- [ ] `ML_SHADOW_MODE=1` — 점수만 기록, 차단 X
- [ ] shadow JSONL 누적 + outcome 매칭 cron

### Phase 5: LIVE 가속 (1주)
- [ ] OOS + Shadow 입증 시 → `ML_SHADOW_MODE=0` + `THRESHOLD=0.45` 보수 시작
- [ ] 1주 평가 → 강화 또는 롤백

### Phase 6: 운영 통합 (상시)
- [ ] daily_report에 ML 섹션 추가
- [ ] `ml_weekly_review.py` 텔레그램 자동 보고
- [ ] 월 1회 재학습 (drift 대응)

---

## 6. 위험 / 한계

| 위험 | 영향 | 완화 |
|---|---|---|
| **Stock_Trade 표본 부족** | 학습 데이터 부족 → AUC 낮음 | 백테스트 시뮬레이션으로 표본 인위 증식 (BATA 3,430건 사례) |
| **외국인/기관 수급 데이터 신뢰성** | 키움 API 지연/누락 | T+1 데이터로 학습 (실시간 X) |
| **장 시작 동시호가 노이즈** | 09:00 첫 봉 거짓 신호 | `is_post_open` feature + threshold 별도 |
| **테마주 폭등/폭락** | 모델이 학습 못 한 패턴 | 시가총액 하위 30% 종목 풀 제외 |
| **공시/실적 시즌 효과** | feature에 미반영 | Phase 6에서 추가 검토 |
| **상장폐지 종목 데이터 누락** | 생존자 편향 | 학습 데이터에 상폐 종목 포함 |
| **모델 drift** | 시장 환경 변화 시 성능 저하 | 월 1회 재학습 + drift 모니터링 (Evidently) |

---

## 7. 도입 비용 추정

| 항목 | 시간 | 비고 |
|---|---|---|
| 코드 작성 (services/ml + scripts) | 30~40h | BATA 코드 70% 재사용 |
| 데이터 수집/정제 | 20~30h | 키움 API + 외국인/기관 |
| 학습/검증/튜닝 | 15~20h | XGBoost + Optuna |
| 통합/배포 | 10~15h | multi_trader hook + AWS deploy |
| **총** | **75~105h** | 6~8주 분산 가능 |

### 인프라 비용
- 학습: 로컬 PC (X86, 16GB+ RAM 권장)
- 추론: 기존 AWS 서버 (메모리 +50MB, 미미)
- 디스크: ~500MB (모델 + cache.duckdb 일부)

---

## 8. BATA vs Stock_Trade 비교 — 도입 우선순위

| 평가 항목 | BATA | Stock_Trade |
|---|---|---|
| 신호 발생 빈도 | 높음 (24/7) | 중간 (장중만) |
| 가짜 돌파 비율 | 높음 (변동성) | 중간 |
| feature 풍부도 | 보통 | **높음** (외국인/기관 수급) |
| 표본 데이터 | 4년 | **5년+ (KRX 풍부)** |
| ML 효과 기대 | 입증됨 (PF +0.19) | **더 높을 가능성** (수급 신호 강력) |
| 운영 위험도 | 중간 | **낮음** (변동성 ↓) |

→ **결론: Stock_Trade가 ML 도입에 더 유리한 환경**. BATA보다 효과 클 가능성 큼.

---

## 9. 권고

### 즉시 가능 (PoC 시작)
1. `services/ml/` 디렉터리 BATA → Stock_Trade로 복사
2. KRX 일봉 + 외국인/기관 수급 백테스트 데이터 수집 시작
3. Feature 카탈로그 한국 주식용 재정의 (Section 3 참고)

### 6주 후 결정 시점
- OOS 시뮬 PF ≥ 1.1 입증 시 → Shadow → LIVE 가속 (BATA 패턴)
- 입증 실패 시 → 한국 주식 특성 재분석, feature 추가/모델 변경

### 안전선
- Stock_Trade는 **실거래 자금이 BATA보다 클 가능성** → 더 보수적 임계 시작 (0.50 또는 0.55)
- 첫 LIVE 시 1주 손실 -5% 도달 즉시 SHADOW 복귀

---

## 10. 결론

**ML 신호 필터를 Stock_Trade에 도입하는 것은 효과적일 가능성이 큼**. 이유:

1. **이미 검증된 시스템** — BATA OOS PF 0.98→1.17 입증
2. **코드 70% 재사용 가능** — 신규 개발 부담 적음
3. **한국 주식 특성이 ML에 유리** — 외국인/기관 수급은 강력한 leading 지표
4. **위험 통제 가능** — fail-open + Shadow Mode + 점진 강화 패턴

다만 **6~8주 PoC를 거쳐 Stock_Trade 환경에서 효과 검증 후** 본격 도입 권장. BATA 결과를 그대로 가정하지 말 것.

---

## 부록: 참고 산출물

- BATA ML 아키텍처: [output/ml_signal_filter_architecture.html](ml_signal_filter_architecture.html)
- BATA ADR: `docs/decisions/20260504_2_ml_signal_filter.md`
- BATA LIVE 가속 ADR: `docs/decisions/20260505_2_ml_live_acceleration.md`
- BATA WBS Phase 8: `docs/00.보고/WBS.md` Phase 8 (28개 항목)
- BATA OOS 백테스트 시뮬: `scripts/ml_backtest_sim.py`

---

*본 문서는 BATA 운영 1개월 + ML 도입 2일 시점의 데이터 기반 추정. Stock_Trade에 적용 시 실제 결과는 다를 수 있으며, 6~8주 PoC를 통한 검증이 필수.*
