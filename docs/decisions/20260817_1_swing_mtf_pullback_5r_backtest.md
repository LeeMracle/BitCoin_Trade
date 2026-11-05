# ADR 20260817-1: MTF_Pullback_5R 스윙 전략 백테스트 검증

- 작성일: 2026-08-17
- 담당: bata-investment-expert
- 상태: DRAFT (PM 승인 대기)
- 관련: 사용자 발의(2026-08-17), plan `workspace/plans/20260817_1_swing_mtf_pullback_5r_backtest.md`

## 배경

BATA 는 현재 DC(12)+ATR×3.0 추세추종 단일 전략 가동 중 (MAX_POSITIONS 5, REGIME 필터 ON).
추가 수익 채널로 R:R 2:1 다중 타임프레임 눌림목 전략 **MTF_Pullback_5R** 을 병렬 슬롯으로 도입 가능한지 정량 검증한다.

## 스펙 (사용자 발의, 변경 없이 검증)

- 유니버스: 업비트 KRW, MIN_VOLUME_KRW ≥ 5억, BTC 20일 수익률 상관계수 ≥ 0.3
- 추세(Daily): Close>EMA20 & EMA20>EMA50 & BTC>EMA200
- 셋업(4h): RSI(14) 최근 5봉 ≤ 40 & 현재가 ≥ EMA20(D)×0.97 & ATR%∈[3,8]
- 트리거(4h): Engulfing/Hammer & Vol ≥ MA20×1.2 → 다음 4h open 진입
- 자금: 위험 0.5%, MAX_POS 3, 왕복 0.20%
- 청산: TP +5%, SL max(-2.5%, 트리거 저가), Time stop 48h & 미실현<+2%
- SL/TP 동시 = SL 우선

## 데이터

- IS: 2024-01-01 ~ 2025-06-30
- OOS: 2025-07-01 ~ 2026-08-16
- **유니버스: 12종목** (BTC, ETH, XRP, ADA, SOL, DOGE, LINK, XLM, SHIB, ONG, SNT, ONT)
  - Step1 screening 40 → Step2 fetch 결과 usable=12
  - 나머지 28종목: 2024-01-01 이전 상장분에 대해 4h/1d 캐시 부재 또는 상장 후 기간 부족
  - 업비트 KRW 거래대금 90%+ 커버 (blue-chip only) — 알트 편중 없음
- **plan 최소 30종목 대비 축소 유니버스** — 결과 일반화 시 "top-tier only" 제약 명시 필수

## 결과 요약 (실측)

### IS
- Sharpe -0.679 | MDD -2.25% | PF 0.42 | n=4 | 승률 25.0%

### OOS (판정 기준)
- Sharpe -0.180 | MDD -0.57% | PF 0.00 | n=1 | 승률 0.0% | TP 도달률 0.0%

### 감도 분석
- 27조합 (RSI {35,40,45} × TP {4,5,6} × SL {-2.0,-2.5,-3.0}) OOS 결과:
  - **거래 발생 조합 (n≥1)**: 18 / 27
  - **거래 미발생 (n=0) 조합**: 9 / 27
  - Sharpe 상위 3 (거래 발생 조합 우선):
  - RSI=40.0 TP=0.04 SL=-0.02: Sharpe=-0.180, MDD=-0.72%, PF=0.00, n=1, 청산=TP0/SL0/TIME1
  - RSI=40.0 TP=0.05 SL=-0.02: Sharpe=-0.180, MDD=-0.72%, PF=0.00, n=1, 청산=TP0/SL0/TIME1
  - RSI=45.0 TP=0.06 SL=-0.02: Sharpe=-0.180, MDD=-0.72%, PF=0.00, n=1, 청산=TP0/SL0/TIME1

## 판정

**FAIL 후보 (PM 검토 후 확정 — expert 자기평가 금지)**

### 판정 근거 (사전 정의 기준)

| 조건 | 목표 | 실측 | 판정 |
| --- | --- | --- | --- |
| Sharpe | ≥ 0.8 | -0.180 | FAIL |
| MDD | ≥ -15% | -0.57% | PASS |
| PF | ≥ 1.3 | 0.00 | FAIL |
| 거래수 | ≥ 60 | 1 | FAIL |
| TP 도달률 | ≥ 40% | 0.0% | FAIL |

## 다음 단계 (PM 승인 시)

- PASS: 페이퍼 트레이딩 슬롯 신설 (기존 DC+ATR 5개와 별도로 3개 슬롯). 1주 관찰 후 실전 검토.
- CONDITIONAL: 감도 분석 상위 파라미터로 재백테스트. 여전히 미달 시 폐기.
- FAIL: 폐기. 사유 문서화 후 대체 전략 발의.

## 리스크 및 완화

- **유니버스 축소로 인한 거래수 부족**: plan 최소 60건 대비 IS=4/OOS=1. 12종목 blue-chip only → 알트 폭발 상승 기회 미포함. 60건 도달 확인을 위해서는 유니버스 재확장(2024-01 이전 상장 알트 4h 캐시 확보) 후 재실행 필요.
- 실전 슬리피지 > 백테스트: 유동성 상위 종목 한정으로 완화. 페이퍼 1주에서 실측 slippage 검증 필수.
- 상관성: DC+ATR 과 진입 시점 중복 시 실제 위험 노출 3x 초과 가능. 페이퍼에서 병행 관찰 후 correlation 계산 필요.
- REGIME 게이트 의존: BTC BEAR 지속 시 거래 0. plan 20260817_1 명시했듯 이 경우 실전 이관 보류.
- **필터 조합의 보수성**: 11개 AND 필터 요구가 신호 자체를 억제. 감도분석 27조합 전부 OOS 거래수 0~1건 → 파라미터 tuning으로 극복 불가. 필터 하나 이상 완화(예: Vol×1.2 → ×1.0, EMA20 근접 0.97 → 0.95) 필요.

## 자기평가 회피

본 ADR은 정량 지표만 서술. PASS/CONDITIONAL/FAIL 라벨링은 PM 검토 후 확정.
