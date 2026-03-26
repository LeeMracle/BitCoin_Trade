from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class Metrics:
    sharpe: float
    calmar: float
    max_drawdown: float      # 음수 (e.g. -0.25 = -25%)
    total_return: float      # (e.g. 0.42 = +42%)
    n_trades: int
    win_rate: float          # 0~1
    avg_trade_return: float


@dataclass
class RunResult:
    run_id: str
    config: dict
    metrics: Metrics
    equity_curve: pd.DataFrame   # columns: ts, equity
    trade_log: pd.DataFrame      # columns: entry_ts, exit_ts, side, entry_price, exit_price, return_pct
    artifact_dir: Path
