# 신규 전략 후보 리서치 — BATA v1.5 Sprint C (P5-19)

> 조사일: 2026-04-05 | 업비트 현물 롱온리 환경

## 우선순위 요약

| 순위 | 전략 | 우선순위 | Composite 상관 | 레짐 적합 | 파라미터 |
|:----:|------|:--------:|:--------------:|-----------|:--------:|
| 1 | BB 하단 평균회귀 | ★★★ | 낮음 | SIDEWAYS/BULL | 2~3개 |
| 2 | P/MA200 저점 분할매수 | ★★★ | 낮음 | BEAR/CRISIS | 2개 |
| 3 | 절대 모멘텀 현금화 | ★★★ | 낮음 | BEAR/CRISIS (필터) | 1개 |
| 4 | BB 스퀴즈 돌파 | ★★ | 중간 | SIDEWAYS/BULL | 3개 |
| 5 | 알트 월간 로테이션 | ★★ | 낮음 | BULL/분산 | 2개 |
| 6 | StochRSI+BB 복합 | ★★ | 낮음 | SIDEWAYS/BULL | 2개 |
| 7 | 요일 효과 | ★ | 낮음 | 불안정 | 1개 |

## 전략 1: BB 하단 평균회귀 (Bollinger Band Lower Bounce)

**원리:** BB(20,2.0) 하단 밴드 이탈 후 회복 시 매수. 과도한 하락 후 SMA20 복귀 성질 이용.
- 참고 백테스트(QuantifiedStrategies, 2015~2026): CAGR 49.7%, MDD -66.9%, 시장체류 34%

**진입/청산:**
- 진입: `전날 close < BB_lower` AND `오늘 close > BB_lower`
- 청산: `close >= BB_middle(SMA20)` 또는 손절 -5%
- 레짐 필터(권장): `close > EMA(200)` — 추세 하락장 보호

**레짐 적합:** SIDEWAYS 높음, BULL 중간, BEAR/CRISIS 낮음 (EMA필터 필수)

```python
def _calc_bb(df, period=20, std=2.0):
    sma = df["close"].rolling(window=period, min_periods=period).mean()
    std_dev = df["close"].rolling(window=period, min_periods=period).std()
    return sma + std * std_dev, sma, sma - std * std_dev
```

## 전략 2: P/MA200 저점 분할매수 (Price/MA200 Accumulation)

**원리:** MVRV 무료 대체. `close / EMA(200) < 0.85` 구간에서 분할매수. 역사적 저점 축적.

**진입/청산:**
- 진입: `close / EMA(200) < 0.85` — 분할 3회 (1주 간격)
- 포지션: 전체 자금 30% 한도 (분할당 10%)
- 청산: `close / EMA(200) > 1.10` 또는 손절 -15%

**레짐 적합:** BEAR/CRISIS 높음, SIDEWAYS 중간, BULL 낮음 (신호 없음)

## 전략 3: 절대 모멘텀 현금화 (Absolute Momentum Cash Filter)

**원리:** 30일 수익률 양수면 보유, 음수면 전량 현금. Composite의 레짐 필터로 통합 가능.

**진입/청산:**
- 매월 말 계산: `return_30d = (close / close_30d_ago) - 1`
- 양수 → 투자 유지, 음수 → 현금 보유
- Composite 통합 시: 음수이면 신규 진입 금지

**레짐 적합:** BEAR/CRISIS 높음 (현금화), BULL 높음 (투자 유지)

## 전략 4: BB 스퀴즈 돌파 (Bollinger Squeeze Breakout)

**원리:** BandWidth < 5% 수축 5봉+ 지속 후 상방 돌파. 횡보 종료 포착.

**진입/청산:**
- 스퀴즈: `BandWidth < 5%` 연속 5봉+
- 진입: 스퀴즈 해제 후 `close > BB_upper` + 거래량 > 20일평균 x 1.3
- 청산: ATR(14) x 2.0 트레일링스탑

**레짐 적합:** SIDEWAYS/BULL 높음, BEAR/CRISIS 낮음

## 전략 5: 알트 월간 로테이션 (Alt Monthly Rotation)

**원리:** 업비트 KRW 마켓 알트 30종 중 30일 수익률 상위 3종 균등 투자. 매월 리밸런싱.

**진입/청산:**
- 유니버스: 20일 평균 거래대금 > 1억원
- 랭킹: return_30d 내림차순, 상위 3종 중 return_30d > 0만 편입
- 전부 음수 → 전량 현금

**레짐 적합:** BULL 높음 (알트시즌), BEAR 중간 (현금화)

## 전략 6: StochRSI+BB 복합 과매도 반등

**원리:** BB 하단 터치 + StochRSI(14) < 20 동시 충족. 전략1의 강화 버전, 노이즈 감소.

**진입/청산:**
- 진입: `close < BB_lower` AND `stoch_rsi_k < 20`, 다음봉 회복 확인
- 청산: `close >= BB_middle` 또는 `stoch_rsi_k > 80`

## 전략 7: 요일 효과 (Asia Open Momentum)

**원리:** 일요일 21:00 UTC 매수, 월요일 24:00 UTC 청산. 아시아장 오픈 효과.
- 단독 전략보다 진입 타이밍 필터로 보조 활용 권장.

## 스크리닝 권장 순서

1. **BB 평균회귀** (★★★) — SIDEWAYS 핵심, 구현 단순
2. **P/MA200 저점매수** (★★★) — CRISIS 핵심, 기존 코드 재사용
3. **절대 모멘텀** (★★★) — Composite 필터로 즉시 통합 가능
4. 나머지는 상위 3종 결과 보고 스크리닝

## 참고자료

- QuantifiedStrategies.com BTC BB Backtest 2015~2026
- SSRN 5775962 (BB Regime Study)
- ScienceDirect 2025 Crypto Momentum Risk-Managed
- Concretum Group Asia Open Effect (Calmar 1.79)
- Glassnode MVRV Academy
