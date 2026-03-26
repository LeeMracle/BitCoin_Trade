"""백테스트 성과 지표 계산."""
import numpy as np
import pandas as pd

from .models import Metrics


def compute_metrics(
    equity_curve: pd.DataFrame,
    trade_log: pd.DataFrame,
    periods_per_year: int = 365,
) -> Metrics:
    """equity_curve: columns [ts, equity], trade_log: columns [..., return_pct]."""
    equity = equity_curve["equity"].values
    returns = pd.Series(equity).pct_change().dropna()

    sharpe = _sharpe(returns, periods_per_year)
    max_dd = _max_drawdown(equity)
    total_ret = (equity[-1] / equity[0]) - 1
    calmar = _calmar(total_ret, max_dd)

    n_trades = len(trade_log)
    win_rate = (trade_log["return_pct"] > 0).mean() if n_trades > 0 else 0.0
    avg_trade = trade_log["return_pct"].mean() if n_trades > 0 else 0.0

    return Metrics(
        sharpe=round(sharpe, 4),
        calmar=round(calmar, 4),
        max_drawdown=round(max_dd, 4),
        total_return=round(total_ret, 4),
        n_trades=n_trades,
        win_rate=round(float(win_rate), 4),
        avg_trade_return=round(float(avg_trade), 4),
    )


def _sharpe(returns: pd.Series, periods_per_year: int) -> float:
    if returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min())


def _calmar(total_return: float, max_dd: float) -> float:
    if max_dd == 0:
        return 0.0
    return float(total_return / abs(max_dd))
