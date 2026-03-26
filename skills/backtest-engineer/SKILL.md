---
name: backtest-engineer
description: 업비트 BTC/KRW 현물 백테스트 구현 및 검증. 전략 코드 작성, 시뮬레이션 실행, 실험 기록 시 사용. services/backtest/engine.py 기반.
---

# Backtest Engineer

전략 명세를 코드로 구현하고 수치로 검증한다.

## 고정 시뮬레이션 파라미터 (업비트 현물)

| 항목 | 기본값 | 비고 |
| --- | --- | --- |
| `fee_rate` | 0.0005 | 업비트 기본 수수료 0.05%, 진입·청산 각각 적용 |
| `slippage_bps` | 5 | 체결가 불리 방향 5bp |
| `initial_capital` | 10,000,000 | KRW |
| 실행 타이밍 | bar-close 신호 → 다음 bar 시가 체결 | look-ahead bias 방지 |

## 실행 방법

```python
from backtest.engine import BacktestEngine

result = BacktestEngine().run(
    strategy_fn,   # strategy_spec의 signal 함수
    ohlcv_df,      # market_data MCP get_ohlcv 결과
    params={"fee_rate": 0.0005, "slippage_bps": 5}
)
```

## 필수 아티팩트 (`workspace/runs/{run_id}/`)

- `config.json` — 실행 파라미터 전체
- `metrics.json` — sharpe, calmar, max_drawdown, total_return, n_trades, win_rate
- `equity_curve.csv` — ts, equity
- `trade_log.csv` — entry_ts, exit_ts, entry_price, exit_price, return_pct

## 실험 기록

```python
# experiment_tracker MCP
create_experiment(name, strategy_id)
log_run(experiment_id, run_id, params, metrics, artifact_paths)
```

## 검증 기준

- **인샘플/아웃샘플 분리 필수**: 최소 아웃샘플 20% (시간 순 분리)
- 파라미터 ±10% 변화 시 Sharpe 급락 여부 확인
- `n_trades < 10`이면 통계적 유의성 없음 — 재설계 요청

## 제약

- Sharpe 없이 수익률만 보고 금지
- 수수료 가정이 다른 run 간 직접 비교 금지
