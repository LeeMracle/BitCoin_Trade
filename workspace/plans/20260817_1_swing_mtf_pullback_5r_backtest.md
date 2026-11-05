# 20260817_1 MTF_Pullback_5R 스윙 전략 백테스트

담당: bata-investment-expert (expert 단독 발의, PM 승인 후 페이퍼 이관)
발의일: 2026-08-17
소요 예상: 8~12h (fetch 3~4h + 백테스트 엔진 3~4h + 감도 분석 1~2h + 리포트 1~2h)

## 목표

R:R 2:1 (TP +5%, SL -2.5%) 다중 타임프레임 눌림목 전략 **MTF_Pullback_5R** 의 업비트 KRW 마켓 정량 검증.
현행 DC(12)+ATR 추세추종과 병렬 운용 가능 여부 판정.

## 성공 기준 (사전 정의, ADR에 명시)

PASS: OOS Sharpe ≥ 0.8 ∧ MDD ≥ -15% ∧ PF ≥ 1.3 ∧ 거래수 ≥ 60 ∧ TP도달률 ≥ 40%
CONDITIONAL: Sharpe 0.5~0.8, MDD -15~-20%, PF 1.1~1.3 + 감도 분석에서 대안 파라미터 PASS 근접
FAIL: 그 외

## 데이터

- 기간: 2024-01-01 ~ 2026-08-16
  - IS: 2024-01-01 ~ 2025-06-30 (18개월)
  - OOS: 2025-07-01 ~ 2026-08-16 (13.5개월)
- 타임프레임: Daily + 4h
- BTC/KRW: EMA200(D) 레짐 판단용
- 유니버스: 업비트 KRW 마켓 (2026-08-17 상장 종목 기준). MIN_VOLUME_KRW 5억은 **시점별 재계산**으로 미래정보 누출 회피 (t 시점 직전 24h 누적)

### 데이터 조달 제약 및 대응

- 캐시(data/cache.duckdb) 4h 최신 = 2026-03-31 → 2026-04-01 ~ 2026-08-16 신규 fetch 필요 (5개월)
- 캐시(1d) 최신 확인 필요 → 부족 시 동일 구간 fetch
- 283개 KRW 마켓 전부 fetch는 시간·rate limit 부담 → **2단계 필터링**:
  1. 사전 스크리닝: 최근 90일 24h 거래대금 중앙값 ≥ 5억 & 상장일 ≤ 2024-01-01 인 종목 우선 (약 40~60종목 예상)
  2. 시점별 재계산: 백테스트 루프 내 t 시점의 24h 거래대금이 5억 미만이면 후보 제외
- 캐시에 이미 있는 12종목(BTC, ETH, ADA, XRP, DOGE, SOL, ATOM, NEAR, DOT, LINK, AAVE, AVAX)은 상관계수 baseline 즉시 사용 가능
- 4개 (SEI/SUI/ARB/APT) 는 2024-01-01 시작 데이터 있음 → 편입
- 2개 (OP/TIA) 는 2025-09-01 시작 → 상장 이전 구간 제외 처리

## 전략 spec (사용자 발의 그대로 구현, 변경 금지)

### 유니버스 조건 (시점별)
- MIN_VOLUME_KRW ≥ 5억 (24h 누적)
- ρ(20일 수익률, BTC 20일 수익률) ≥ 0.3

### 추세 필터 (Daily, 09:00 KST = UTC 00:00 마감봉 기준)
- A: Close(D) > EMA(20, D)
- B: EMA(20, D) > EMA(50, D)
- C: BTC/KRW Close(D) > EMA(200, D)
- A ∧ B ∧ C 성립 시 진입 후보

### 셋업 (4h)
- D: 최근 5봉 내 RSI(14, 4h) ≤ 40 터치
- E: 현재가 ≥ EMA(20, D) × 0.97
- F: ATR(14, 4h) / 현재가 ∈ [0.03, 0.08]

### 트리거 (4h 종가 확정 시)
- G: Bullish Engulfing OR Hammer
- H: Vol(현봉) ≥ MA(20, Vol) × 1.2
- **다음 4h 봉 시가**에 시장가 매수 (lessons #1)

### 자금관리
- 초기 자본 1,000만원
- 위험/트레이드 = 자본 × 0.5%
- 포지션 규모 = 위험 / SL 거리(%) × 진입가
- MAX_POSITIONS_SWING = 3
- 왕복 실질 비용 = 0.20% (수수료 0.10% + 슬리피지 0.10%)

### 청산
- TP: entry × 1.05
- SL: max(entry × 0.975, 트리거봉 저가) (하드 캡 -2.5%, lessons #13)
- Time stop: 진입 후 48h 경과 & 미실현 < +2% → 시장가 청산
- SL/TP 동시 = SL 우선 (lessons #25)

## 산출물

1. 백테스트 리포트: `workspace/reports/backtest_mtf_pullback_5r_20260817.md`
2. 거래 로그 CSV: `workspace/runs/mtf_pullback_5r/trades_20260817.csv`
3. ADR: `docs/decisions/20260817_1_swing_mtf_pullback_5r_backtest.md`
4. 자본곡선 PNG: `workspace/runs/mtf_pullback_5r/equity_curve_20260817.png`
5. 월별 히트맵 PNG: `workspace/runs/mtf_pullback_5r/monthly_heatmap_20260817.png`

## 필수 검증

- 미래정보 누출 zero: 지표 계산 t 시점에서 t 이후 데이터 참조 금지
- 진입가 = 트리거 다음 4h 봉 open (트리거 봉의 close 아님)
- REGIME 조건 C를 만족하지 않은 진입 = 0건 확인 (assert)
- lessons #2 회피: 데이터 기간 상승/조정/하락 국면 분포 보고

## 감도 분석 (ADR 필수)

- RSI 임계 (설정: 40) → {35, 40, 45}
- TP → {4%, 5%, 6%}
- SL → {-2.0%, -2.5%, -3.0%}
- 총 27조합. OOS 기준 Sharpe·MDD·PF·거래수 표

## 순차 진행

1. 데이터 조달 스크립트 (`output/expert_data_20260817/fetch_universe.py`)
2. 유니버스 사전 스크리닝 리포트
3. 백테스트 엔진 (`output/expert_data_20260817/backtest_mtf_pullback_5r.py`)
4. IS 실행 & 검증 (미래정보 누출 assert)
5. OOS 실행
6. 감도 분석 (27조합 sweep)
7. 리포트·차트·ADR 작성

## 리스크 및 완화

- 캐시 fetch 중단: 재시작 가능(caсhe.duckdb upsert)
- 종목수 부족: 30개 미만이면 사용자 확인 후 축소 유니버스 진행 명시
- REGIME 조건이 데이터 대부분 기간에서 False → 거래수 60 미달 가능. FAIL 판정 시에도 사유 명시로 재검토 가치 확보

## 자기평가 회피

- expert 스스로 PASS 판정 금지 (자기평가 금지 원칙)
- ADR은 정량 결과만 서술, PASS/CONDITIONAL/FAIL 라벨링은 PM 승인 시 확정
- 코드 검토는 배포 대상 아님(백테스트 산출물 only) → cto review 스킵 가능, 다만 pre_deploy_check 대상은 아님
