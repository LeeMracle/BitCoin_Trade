"""backtest engine 단위 테스트 — 결정론적 데이터 사용."""
import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestEngine


def _make_ohlcv(prices: list[float]) -> pd.DataFrame:
    """단순 테스트용 OHLCV — 종가 = 시가 = 고가 = 저가."""
    rows = []
    for i, p in enumerate(prices):
        rows.append({
            "ts": 1_700_000_000_000 + i * 86_400_000,
            "open": p, "high": p, "low": p, "close": p, "volume": 1_000_000,
        })
    return pd.DataFrame(rows)


def _always_long(df: pd.DataFrame) -> pd.Series:
    return pd.Series(1, index=df.index)


def _always_flat(df: pd.DataFrame) -> pd.Series:
    return pd.Series(0, index=df.index)


def _buy_hold_sell(df: pd.DataFrame) -> pd.Series:
    """bar 0에 매수, 마지막 bar에 청산."""
    sig = pd.Series(0, index=df.index)
    sig.iloc[0] = 1
    sig.iloc[-1] = 0
    return sig


@pytest.fixture
def engine():
    return BacktestEngine()


def test_always_flat_no_trades(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("backtest.report.RUNS_DIR", tmp_path)
    prices = [50_000_000] * 10
    ohlcv = _make_ohlcv(prices)
    result = engine.run(_always_flat, ohlcv)
    assert result.metrics.n_trades == 0
    assert result.metrics.total_return == 0.0


def test_rising_market_positive_return(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("backtest.report.RUNS_DIR", tmp_path)
    prices = [50_000_000 + i * 1_000_000 for i in range(10)]
    ohlcv = _make_ohlcv(prices)
    result = engine.run(_buy_hold_sell, ohlcv, params={"fee_rate": 0, "slippage_bps": 0})
    assert result.metrics.total_return > 0
    assert result.metrics.n_trades == 1


def test_fee_reduces_return(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("backtest.report.RUNS_DIR", tmp_path)
    prices = [50_000_000 + i * 1_000_000 for i in range(10)]
    ohlcv = _make_ohlcv(prices)

    r_no_fee = engine.run(_buy_hold_sell, ohlcv, params={"fee_rate": 0, "slippage_bps": 0})
    r_with_fee = engine.run(_buy_hold_sell, ohlcv, params={"fee_rate": 0.001, "slippage_bps": 5})
    assert r_no_fee.metrics.total_return > r_with_fee.metrics.total_return


def test_artifacts_created(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("backtest.report.RUNS_DIR", tmp_path)
    prices = [50_000_000] * 5
    ohlcv = _make_ohlcv(prices)
    result = engine.run(_always_flat, ohlcv)
    assert (result.artifact_dir / "config.json").exists()
    assert (result.artifact_dir / "metrics.json").exists()
    assert (result.artifact_dir / "equity_curve.csv").exists()
    assert (result.artifact_dir / "trade_log.csv").exists()
