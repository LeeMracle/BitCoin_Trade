"""백테스트 trade log → 학습용 라벨 생성.

핵심 흐름:
    1. trade_log.csv 로드 (entry_ts, exit_ts, side, entry_price, exit_price, return_pct)
    2. 각 entry 시점에 대해 OHLCV에서 N봉 내 (+target%) 도달 여부 → 이진 라벨
    3. 동일 시점 feature 계산 (compute_features)
    4. feature_store에 누적 저장

라벨 정책 (config.py 단일 출처):
    - LABEL_TARGET_PCT (기본 5%)
    - LABEL_HORIZON_BARS (기본 96 = 4일, 15분봉)
    - LABEL_SLIPPAGE_PCT (0.2% 차감하여 보수적 라벨)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from services.ml import feature_store
from services.ml.config import (
    LABEL_HORIZON_BARS,
    LABEL_SLIPPAGE_PCT,
    LABEL_TARGET_PCT,
)
from services.ml.features import MarketContext, compute_features

log = logging.getLogger(__name__)


@dataclass
class TradeRow:
    symbol: str
    entry_ts: pd.Timestamp        # UTC tz-aware
    entry_price: float


def label_one(
    ohlcv_df: pd.DataFrame,
    entry_ts: pd.Timestamp,
    entry_price: float,
    *,
    target_pct: float = LABEL_TARGET_PCT,
    horizon_bars: int = LABEL_HORIZON_BARS,
    slippage_pct: float = LABEL_SLIPPAGE_PCT,
) -> Optional[int]:
    """단일 거래의 라벨 (1=목표 도달, 0=미도달, None=호라이즌 데이터 부족)."""
    target_price = entry_price * (1.0 + target_pct + slippage_pct)
    forward = ohlcv_df[ohlcv_df.index > entry_ts].head(horizon_bars)
    if len(forward) < horizon_bars // 2:
        return None
    return int((forward["high"] >= target_price).any())


def label_trades(
    trades: Iterable[TradeRow],
    ohlcv_provider,
    market_ctx_provider=None,
) -> list[dict]:
    """다수 거래 → feature_store row 리스트.

    Args:
        trades: TradeRow iterable
        ohlcv_provider: callable(symbol) -> OHLCV DataFrame (DatetimeIndex UTC)
        market_ctx_provider: callable(symbol, ts) -> MarketContext (optional)

    Returns:
        feature_store.write_rows에 그대로 넣을 dict 리스트
    """
    rows: list[dict] = []
    for tr in trades:
        try:
            df = ohlcv_provider(tr.symbol)
        except Exception as e:
            log.warning("OHLCV fetch failed %s: %s", tr.symbol, e)
            continue

        label = label_one(df, tr.entry_ts, tr.entry_price)
        if label is None:
            continue

        ctx = market_ctx_provider(tr.symbol, tr.entry_ts) if market_ctx_provider else MarketContext()
        try:
            feat = compute_features(tr.symbol, df, tr.entry_ts, ctx)
        except ValueError as e:
            log.debug("feature skip %s @%s: %s", tr.symbol, tr.entry_ts, e)
            continue

        rows.append({
            "symbol": tr.symbol,
            "signal_ts": tr.entry_ts,
            "label": label,
            "label_horizon_bars": LABEL_HORIZON_BARS,
            **feat,
        })
    return rows


def load_backtest_trades(trade_log_csv: Path, symbol: str) -> list[TradeRow]:
    """워크스페이스 백테스트 trade_log.csv → TradeRow 리스트.

    스키마: entry_ts(ms), exit_ts(ms), side, entry_price, exit_price, return_pct
    """
    df = pd.read_csv(trade_log_csv)
    out: list[TradeRow] = []
    for _, r in df.iterrows():
        if str(r["side"]).lower() != "long":
            continue
        ts = pd.to_datetime(int(r["entry_ts"]), unit="ms", utc=True)
        out.append(TradeRow(symbol=symbol, entry_ts=ts, entry_price=float(r["entry_price"])))
    return out


def persist(rows: list[dict]) -> int:
    """라벨링 결과를 feature_store에 누적 저장."""
    if not rows:
        return 0
    return feature_store.write_rows(rows)


# ── dummy 데이터 생성 (CI/dry-run용) ─────────────────────────
def make_dummy_dataset(n_trades: int = 200, seed: int = 20260504) -> int:
    """학습 파이프라인 검증용 가짜 데이터셋 생성. feature_store에 저장.

    실데이터 없이도 ml_train.py --dry-run을 통과하기 위함.
    """
    rng = np.random.default_rng(seed)
    bars = 96 * 60  # 60일치 15분봉
    idx = pd.date_range("2026-01-01", periods=bars, freq="15min", tz="UTC")
    # 변동성 충분 (학습 가능한 positive 분포 만들기 위해 trend + 큰 noise)
    drift = np.linspace(0, 5_000_000, bars)
    close = 50_000_000 + drift + np.cumsum(rng.normal(0, 400_000, bars))
    close = np.clip(close, 30_000_000, 80_000_000)
    df = pd.DataFrame({
        "open": close + rng.normal(0, 100_000, bars),
        "high": close + rng.uniform(0, 500_000, bars),
        "low":  close - rng.uniform(0, 500_000, bars),
        "close": close,
        "volume": rng.uniform(0.5, 5.0, bars),
    }, index=idx)

    rows: list[dict] = []
    candidate_ts = idx[300:-LABEL_HORIZON_BARS]
    picks = rng.choice(len(candidate_ts), size=min(n_trades, len(candidate_ts)), replace=False)
    for i in picks:
        at = candidate_ts[i]
        entry_price = float(df.loc[at, "close"])
        label = label_one(df, at, entry_price)
        if label is None:
            continue
        try:
            feat = compute_features(
                "DUMMY-COIN", df, at,
                MarketContext(btc_trend_30d=float(rng.normal(0, 0.1)),
                              btc_dominance=float(rng.uniform(40, 60)),
                              fear_greed=int(rng.integers(20, 80)),
                              market_cap_rank=int(rng.integers(1, 50)),
                              days_since_listing=int(rng.integers(100, 3000))),
            )
        except ValueError:
            continue
        rows.append({
            "symbol": "DUMMY-COIN",
            "signal_ts": at,
            "label": label,
            "label_horizon_bars": LABEL_HORIZON_BARS,
            **feat,
        })

    return persist(rows)
