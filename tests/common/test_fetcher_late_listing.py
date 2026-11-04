"""fetcher._fetch_range — 조회 시작일 이후 상장한 코인도 받아야 한다 (plan 20261103_1).

과거 구현은 첫 페이지가 비면(상장 전 구간) 즉시 멈춰 0봉을 반환했다.
"""
from __future__ import annotations

import asyncio

from services.market_data import fetcher

DAY = 86_400_000


class _FakeEx:
    """listing_ms 이후 일봉만 있는 거래소. since 부터 limit 개를 돌려준다."""

    def __init__(self, listing_ms: int, end_ms: int):
        self.listing_ms, self.end_ms, self.calls = listing_ms, end_ms, 0

    def parse_timeframe(self, tf: str) -> int:
        return 86_400

    async def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls += 1
        window_end = since + limit * DAY
        ts = max(since, self.listing_ms)
        ts += (-ts) % DAY
        out = []
        while ts < window_end and ts <= self.end_ms:
            out.append([ts, 1.0, 1.0, 1.0, 1.0, 1.0])
            ts += DAY
        return out

    async def close(self):
        pass


def _run(listing_day: int, n_days: int = 900):
    end = n_days * DAY
    ex = _FakeEx(listing_day * DAY, end)
    orig = fetcher._exchange
    fetcher._exchange = lambda: ex
    orig_iv = fetcher._REQUEST_INTERVAL
    fetcher._REQUEST_INTERVAL = 0
    try:
        rows = asyncio.run(fetcher._fetch_range("upbit", "X/KRW", "1d", 0, end))
    finally:
        fetcher._exchange = orig
        fetcher._REQUEST_INTERVAL = orig_iv
    return rows, ex


def test_late_listing_is_fetched():
    rows, _ = _run(listing_day=500)
    assert len(rows) == 900 - 500 + 1
    assert rows[0]["ts"] == 500 * DAY


def test_listed_before_start_unchanged():
    rows, _ = _run(listing_day=0)
    assert len(rows) == 901


def test_never_listed_terminates():
    rows, ex = _run(listing_day=10_000)
    assert rows == []
    assert ex.calls <= 900 // 200 + 2
