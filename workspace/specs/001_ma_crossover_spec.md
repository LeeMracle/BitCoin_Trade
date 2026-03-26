# Strategy Spec: MA Crossover Trend Following

> ID: 001
> 작성일: 2026-03-26
> 담당: Strategy Researcher
> 참조 레짐: workspace/research/2026-03-26_regime.md

---

## 1. Thesis (가설)

> BTC/KRW는 MA20이 MA60 위로 올라서는 정배열 전환 시점에 중기 상승 추세가 시작되며,
> 역배열 전환 시 하락 추세가 확인된다. 이 신호로 long/flat 포지션을 전환한다.

---

## 2. 적용 레짐

- **유효**: 추세 전환 초기 구간 (횡보 → 상승)
- **비유효**: 급격한 변동성 구간 (MA가 교차를 반복하는 횡보 장세)

---

## 3. 신호 입력

| 지표 | 파라미터 | 범위 |
| --- | --- | --- |
| MA_fast | 20일 | 10~30일 |
| MA_slow | 60일 | 40~90일 |
| 타임프레임 | 1d | 고정 |

---

## 4. 진입 규칙 (signal = 1)

```
MA_fast(t) > MA_slow(t)   # 정배열
AND close(t) > MA_slow(t)  # 가격이 MA_slow 위
```

두 조건 동시 충족 시 다음 bar 시가에 매수 (100% long)

---

## 5. 청산 규칙 (signal = 0)

```
MA_fast(t) < MA_slow(t)   # 역배열 전환
OR close(t) < MA_slow(t)   # 가격이 MA_slow 아래로 이탈
```

둘 중 하나 발생 시 다음 bar 시가에 전량 청산

---

## 6. 포지션 크기

- **100% long or flat** — 중간 없음
- 분할 매수/매도 없음 (단순 규칙 우선)

---

## 7. 무효화 조건

- 파라미터 ±10% 변화 시 Sharpe가 50% 이상 하락하면 전략 재설계
- 아웃샘플 n_trades < 10이면 통계적 유의성 없음 → 기각

---

## 8. 평가 기준 (최소 요건)

| 지표 | 최소 기준 |
| --- | --- |
| Sharpe (아웃샘플) | ≥ 0.8 |
| MaxDD | ≤ 25% |
| n_trades | ≥ 10 |
| 수수료 후 총 수익률 | > 0% |

---

## 9. 데이터 분할

- **인샘플**: 2024-09-01 ~ 2025-09-30 (약 13개월, 80%)
- **아웃샘플**: 2025-10-01 ~ 2026-03-26 (약 6개월, 20%)
- 시간 순 분리 — 미래 데이터 사용 금지

---

## 10. signal 함수 인터페이스

```python
def strategy_fn(df: pd.DataFrame) -> pd.Series:
    ma_fast = df['close'].rolling(20).mean()
    ma_slow = df['close'].rolling(60).mean()
    signal = ((ma_fast > ma_slow) & (df['close'] > ma_slow)).astype(int)
    return signal
```
