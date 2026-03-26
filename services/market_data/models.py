"""업비트 현물 전용 — 펀딩레이트/미결제약정 없음."""
from pydantic import BaseModel
from typing import Optional


class OHLCVRecord(BaseModel):
    ts: int        # Unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float  # KRW 거래량


class TickerRecord(BaseModel):
    ts: int
    last: float
    bid: float
    ask: float
    volume_24h: float     # KRW 기준
    change_pct_24h: float


class OrderBookRecord(BaseModel):
    ts: int
    bids: list[list[float]]  # [[price, size], ...]
    asks: list[list[float]]


class MacroRecord(BaseModel):
    date: str              # YYYY-MM-DD
    value: float
    label: Optional[str] = None
