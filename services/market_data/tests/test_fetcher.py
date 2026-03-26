"""market_data fetcher 단위 테스트 — ccxt mock 사용."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from market_data.fetcher import fetch_ohlcv


# 2023-11-15T00:00:00Z = 1_700_006_400_000 ms
# 2023-11-16T00:00:00Z = 1_700_092_800_000 ms
SAMPLE_CANDLES = [
    [1_700_006_400_000, 50_000_000, 51_000_000, 49_500_000, 50_500_000, 1_234_567],
    [1_700_092_800_000, 50_500_000, 52_000_000, 50_000_000, 51_800_000, 987_654],
]


@pytest.fixture
def mock_exchange(monkeypatch):
    ex = MagicMock()
    ex.fetch_ohlcv = AsyncMock(side_effect=[SAMPLE_CANDLES, []])
    ex.close = AsyncMock()
    monkeypatch.setattr("market_data.fetcher._exchange", lambda: ex)
    return ex


@pytest.mark.asyncio
async def test_fetch_ohlcv_returns_records(mock_exchange):
    rows = await fetch_ohlcv(
        symbol="BTC/KRW",
        timeframe="1d",
        start="2023-11-15T00:00:00Z",
        end="2023-11-16T23:59:59Z",
        use_cache=False,
    )
    assert len(rows) == 2
    assert rows[0]["open"] == 50_000_000
    assert rows[1]["close"] == 51_800_000


@pytest.mark.asyncio
async def test_fetch_ohlcv_filters_end(mock_exchange):
    # end_ms보다 큰 ts는 포함되지 않아야 함
    rows = await fetch_ohlcv(
        symbol="BTC/KRW",
        timeframe="1d",
        start="2023-11-15T00:00:00Z",
        end="2023-11-15T23:59:59Z",
        use_cache=False,
    )
    assert len(rows) == 1
    assert rows[0]["ts"] == SAMPLE_CANDLES[0][0]
