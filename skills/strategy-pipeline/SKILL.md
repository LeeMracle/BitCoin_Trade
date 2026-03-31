---
name: strategy-pipeline
description: 전략 아이디어 → 구현 → 멀티코인 백테스트 → IS/OOS/하락장 검증 → 등록까지의 전체 파이프라인. 새 전략 발굴 시 사용.
---

# Strategy Pipeline

전략 아이디어를 발굴부터 검증·등록까지 한 번에 처리하는 파이프라인.

## 트리거

- 사용자가 새 전략 아이디어를 제안할 때
- 시장 환경 변화로 기존 전략 개선이 필요할 때
- "전략 발굴", "새 전략", "백테스트 해줘" 등의 요청

## 파이프라인 (7단계)

### Step 1: 아이디어 정의

사용자 아이디어를 strategy-researcher 명세로 변환:

```
- thesis: 한 문장 가설
- 타임프레임: 일봉 / 4시간 / 1시간
- 레짐: 상승장 / 하락장 / 양방향
- 진입 규칙 (결정론적 조건)
- 청산 규칙 (결정론적 조건)
- 파라미터 범위
```

**교훈 참조 의무**: `docs/lessons/` 확인 — 과거 실패 패턴 반복 방지
- 봉 마감 후 진입 (lessons/20260329_1)
- 하락장 별도 검증 (lessons/20260329_2)

### Step 2: 전략 함수 구현

`services/strategies/advanced.py`에 팩토리 함수 추가:

```python
def make_strategy_XXX(
    param1: type = default,
    ...
) -> Callable[[pd.DataFrame], pd.Series]:
    """전략 설명. 진입/청산 규칙 문서화."""
    
    def strategy(df: pd.DataFrame) -> pd.Series:
        # 1. 지표 계산 (_calc_atr, _calc_rsi 등 재사용)
        # 2. bar-by-bar 루프 (트레일링스탑 등 상태 관리)
        # 3. signal 반환 (0 또는 1)
        return pd.Series(signal, index=df.index, dtype=int)
    
    return strategy
```

**재사용 가능한 공통 함수**:
- `_calc_atr(df, period)` — ATR (Wilder)
- `_calc_ema(series, period)` — EMA
- `_calc_rsi(series, period)` — RSI (Wilder)
- `_calc_donchian_upper(df, period)` — DC 상단 (shift(1))
- `_calc_vol_sma(df, period)` — 거래량 SMA

### Step 3: 레지스트리 등록

`services/strategies/__init__.py`에 추가:

```python
from .advanced import make_strategy_XXX
STRATEGY_REGISTRY["xxx"] = make_strategy_XXX
```

### Step 4: 백테스트 스크립트 작성

`workspace/runs/backtest_XXX.py` 생성 — 기존 패턴 기반:

```python
# 멀티코인 (18종목)
COINS = ['BTC/KRW', 'ETH/KRW', 'XRP/KRW', 'SOL/KRW', ...]

# 기간 분할
WARMUP_START = '2022-06-01T00:00:00Z'  # 지표 워밍업
IS:   2023-01-01 ~ 2025-03-31
OOS:  2025-04-01 ~ 2026-03-31
하락장: 2025-09-01 ~ 2026-03-31

# 레짐별 파라미터 변형
상승장 모드 / 하락장 모드 / 자동 레짐

# BacktestEngine 실행
engine = BacktestEngine()
result = engine.run(strategy_fn, df)
```

**타임프레임 선택**:
- 일봉 전략: `fetch_ohlcv(coin, '1d', ...)`
- 4시간봉 전략: `fetch_ohlcv(coin, '4h', ...)`

### Step 5: 실행 및 결과 수집

```bash
PYTHONUTF8=1 python workspace/runs/backtest_XXX.py
```

출력: 전략 × 레짐 × 기간별 비교표
저장: `output/XXX_backtest_results.csv`

### Step 6: 통과 판정

| 지표 | 스윙 전략 | 단타 전략 |
|------|----------|----------|
| OOS Sharpe | >= 0.8 | >= 0.6 |
| OOS MDD | >= -20% | >= -20% |
| 승률 | >= 50% | >= 40% |
| 하락장 승률 | >= 40% | >= 35% |
| 월 거래수 | >= 1 | >= 5 |
| 평균 수익 | > 0.5% | > 0.3% |
| 거래수 합계 | >= 10 | >= 30 |

**추가 검증**:
- 파라미터 ±10% 변화 시 Sharpe 급락 여부 (과적합 체크)
- 특정 코인 편중 여부

### Step 7: 보고서 및 의사결정

**PASS 시**:
1. `workspace/.simulation/YYYYMMDD_제목.md` 보고서 작성
2. `docs/decisions/YYYYMMDD_N_전략명.md` 의사결정 기록
3. config.py에 전략 추가 검토 → 사용자 승인 후 실전 투입

**FAIL 시**:
1. 실패 원인 분석
2. `docs/lessons/YYYYMMDD_N_전략실패원인.md` 교훈 기록
3. 파라미터 수정 → Step 4로 재시도 또는 폐기

## 제약

- 인샘플 성과만으로 통과 선언 금지
- 하락장 구간 검증 없이 실전 투입 금지
- 수수료 0.05% + 슬리피지 5bp 반드시 포함
- 전략 전환 시 CLAUDE.md 동기화 필수 (lessons/20260331_1)

## 파일 위치

| 산출물 | 경로 |
|--------|------|
| 전략 코드 | `services/strategies/advanced.py` |
| 레지스트리 | `services/strategies/__init__.py` |
| 백테스트 스크립트 | `workspace/runs/backtest_*.py` |
| 결과 CSV | `output/*.csv` |
| 시뮬레이션 보고서 | `workspace/.simulation/*.md` |
| 의사결정 기록 | `docs/decisions/*.md` |
| 교훈 기록 | `docs/lessons/*.md` |
