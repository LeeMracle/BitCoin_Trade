# 데일리 단타 전략 리서치 보고서

**날짜**: 2026-03-28
**출처**: 외부 자료 조사 + 백테스트 검증

---

## 조사 대상 5개 전략

| 전략 | 업비트 적합성 | 결론 |
|------|:----------:|------|
| RSI 과매도 반등 | 중 | 추세 자산에서 단독 사용 위험, 필터 필수 |
| 볼린저밴드 반전 | 낮음 | 횡보장만 유효, 추세장 치명적 |
| **거래량 급증 모멘텀** | **높음** | **업비트 알트코인 특성과 최적 부합** |
| 일봉 갭 트레이딩 | 불가 | 24시간 거래로 갭 없음 |
| EMA 크로스 단타 | 중 | 거짓 신호율 57~76%, 추가 필터 필요 |

## 핵심 발견

### 1. 순수 평균회귀는 암호화폐에서 실패
- QuantifiedStrategies: "RSI as a contrarian indicator is basically worthless on Bitcoin"
- RSI 모멘텀(올라갈 때 매수)이 역추세보다 우수: CAGR 122% vs BnH 101%
- 볼린저 바운스는 레짐 필터 없으면 MDD -67% (2015-2026)

### 2. 거래량 급증이 업비트에서 가장 유효
- 업비트 2024년 거래량 $1.1조, 알트코인이 80%
- 리테일 주도 펌프 패턴 빈번 → 거래량 급증이 선행 지표
- 멀티코인 스캔으로 기회 극대화

### 3. 추세 필터 필수
- 하락장 역추세 진입 = "떨어지는 칼날 잡기"
- EMA(200) 또는 SMA(50) 추세 필터가 핵심 약점 보완
- 레짐별 전략 전환이 이상적

## 백테스트 검증 결과 (프로젝트 내부)

### BTC 단일 종목 (6개월, 4시간봉)
- RSI(14)<25: -15.4%, BB반등: -1.2%, 거래량모멘텀: -2.7%
- **전부 마이너스** — 하락장 BTC 단일 종목 단타 불가

### 멀티코인 18종목 (6개월, 4시간봉)
- BB 반등: -94.6% (617회), RSI 과매도: -69.2% (241회)
- **추세 필터 없는 평균회귀는 치명적**

### 거래량 돌파+트레일 (2.2년, 16종목)
- C3: DC15+SMA50+Vol2.5x+Trail2% → **MDD -33.1%, 평균 +0.91%/거래, 월 27회**
- **유일하게 대폭 수익**

## 최종 결론

거래량 돌파 + 추세 필터 + 트레일링스탑 조합이 업비트 현물 단타의 유일한 생존 전략.
구현: `make_strategy_daytrading()` → 서버 배포 완료 (2026-03-28)

## 참고자료
- Bitcoin RSI Strategy Backtest (QuantifiedStrategies)
- Bitcoin Bollinger Bands Strategy Backtest (QuantifiedStrategies)
- Mean Reversion Trading (Stoic.ai)
- altFINS Volume Spike Tracker
- EMA Crossover Crypto Guide 2025
- Upbit Korea Top Exchange 2024 (Bloomberg)
