"""학습/추론 공용 Feature 계산 (lookahead 방지 강제).

핵심 원칙:
  1. 동일 함수가 학습/추론 양쪽에서 호출 — drift 원천 차단
  2. 모든 feature는 "신호 발생 시점(at_ts)"의 봉 마감 기준으로만 계산
  3. at_ts 이후 데이터 접근 금지 (assertion으로 강제, lookahead bias 차단 — lessons #1)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from services.ml.config import FEATURE_COLUMNS


@dataclass
class MarketContext:
    """신호 발생 시점의 시장 전반 컨텍스트 (BTC 추세 등)."""
    btc_trend_30d: float = 0.0       # BTC 30일 수익률 (-1 ~ +N)
    btc_dominance: float = 0.0        # 0~100
    fear_greed: int = 50              # 0~100
    market_cap_rank: int = 999        # Upbit 거래대금 랭크
    days_since_listing: int = 365
    btc_corr_30d: float = 0.0         # v2: 해당 종목 close와 BTC close의 30일 상관 (-1~1)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return float(a) / float(b) if b not in (0, 0.0) and not pd.isna(b) else default


def _rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _ema(series: pd.Series, period: int) -> float:
    val = series.ewm(span=period, adjust=False).mean().iloc[-1]
    return float(val) if pd.notna(val) else float(series.iloc[-1])


def _max_drawdown(close: pd.Series) -> float:
    cummax = close.cummax()
    dd = (close - cummax) / cummax
    return float(dd.min()) if not dd.empty else 0.0


def _macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD 히스토그램 (signal 대비 MACD 차이) 마지막 값."""
    if len(close) < slow + signal:
        return 0.0
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    val = (macd - sig).iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _bb_width(close: pd.Series, period: int = 20, k: float = 2.0) -> float:
    """Bollinger Band width 정규화 (band폭 / 종가)."""
    if len(close) < period:
        return 0.0
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    upper = ma + k * sd
    lower = ma - k * sd
    width = (upper - lower) / close
    val = width.iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _stoch_k(df: pd.DataFrame, period: int = 14) -> float:
    """Stochastic %K (0~100)."""
    if len(df) < period:
        return 50.0
    low_n = df["low"].rolling(period).min()
    high_n = df["high"].rolling(period).max()
    rng = (high_n - low_n).replace(0, np.nan)
    k = 100 * (df["close"] - low_n) / rng
    val = k.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def _consecutive_up_days(close: pd.Series) -> int:
    """가장 최근 봉 기준 연속 상승 봉 수."""
    if len(close) < 2:
        return 0
    diffs = close.diff().fillna(0)
    cnt = 0
    for v in reversed(diffs.tolist()):
        if v > 0:
            cnt += 1
        else:
            break
    return cnt


def compute_features(
    symbol: str,
    ohlcv_df: pd.DataFrame,
    at_ts: pd.Timestamp,
    market_ctx: Optional[MarketContext] = None,
    *,
    dc_period: int = 15,
) -> dict:
    """신호 발생 시점(at_ts)의 feature dict를 반환.

    Args:
        symbol: "KRW-BTC" 등
        ohlcv_df: timestamp index, columns=[open,high,low,close,volume]
                  반드시 봉 마감 시각 기준이어야 함
        at_ts: 신호 발생 시각 (이후 데이터 접근 금지)
        market_ctx: BTC 추세 등 외부 컨텍스트
        dc_period: 도네찬 채널 기간 (DC15 기본)

    Returns:
        FEATURE_COLUMNS 순서대로 정렬된 dict
    """
    ctx = market_ctx or MarketContext()

    # ── lookahead 방지: at_ts 이후 데이터 컷오프 (lessons #1) ──
    if not isinstance(ohlcv_df.index, pd.DatetimeIndex):
        raise ValueError("ohlcv_df must have DatetimeIndex (UTC).")
    df = ohlcv_df[ohlcv_df.index <= at_ts].copy()
    if len(df) < 50:
        raise ValueError(f"insufficient bars before at_ts ({len(df)} < 50)")

    close = df["close"]
    high = df["high"]
    last_close = float(close.iloc[-1])

    # 기술 지표
    rsi_14 = _rsi(close, 14)
    atr_14 = _atr(df, 14)
    atr_14_pct = _safe_div(atr_14, last_close)
    ema_50 = _ema(close, 50)
    ema_200 = _ema(close, 200) if len(close) >= 200 else _ema(close, len(close))
    ema_dist_50 = _safe_div(last_close - ema_50, ema_50)
    ema_dist_200 = _safe_div(last_close - ema_200, ema_200)

    dc_high = high.iloc[-(dc_period + 1):-1].max() if len(high) > dc_period else high.max()
    dc_breakout_strength = _safe_div(last_close - dc_high, atr_14) if atr_14 else 0.0

    vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
    vol_ratio_20 = _safe_div(df["volume"].iloc[-1], vol_ma20, default=1.0)

    # 시간/캘린더
    ts_kst = at_ts.tz_convert("Asia/Seoul") if at_ts.tzinfo else at_ts.tz_localize(timezone.utc).tz_convert("Asia/Seoul")
    hour_of_day = ts_kst.hour
    day_of_week = ts_kst.dayofweek
    is_weekend = int(day_of_week >= 5)

    # 최근 성과 (일봉 환산: 15분봉 기준 최근 7일 = 672 봉, 30일 = 2880 봉)
    bars_per_day = 96
    last_7d_window = min(7 * bars_per_day, len(close))
    last_30d_window = min(30 * bars_per_day, len(close))
    last_7d_close = close.iloc[-last_7d_window:]
    last_30d_close = close.iloc[-last_30d_window:]
    last_7d_return = _safe_div(last_close - float(last_7d_close.iloc[0]), float(last_7d_close.iloc[0]))
    max_drawdown_30d = _max_drawdown(last_30d_close)

    # 일봉 기준 연속 상승일 (15분봉 → resample)
    daily_close = close.resample("1D").last().dropna()
    consecutive_up_days = _consecutive_up_days(daily_close)

    # v2 추가 지표
    macd_hist = _macd_hist(close)
    bb_width_20 = _bb_width(close, 20)
    stoch_k_14 = _stoch_k(df, 14)
    # 1d EMA200 이격
    if len(daily_close) >= 30:
        d_ema200 = _ema(daily_close, min(200, len(daily_close)))
        daily_ema200_dist = _safe_div(last_close - d_ema200, d_ema200)
    else:
        daily_ema200_dist = 0.0

    feat = {
        "rsi_14": rsi_14,
        "atr_14_pct": atr_14_pct,
        "ema_dist_50": ema_dist_50,
        "ema_dist_200": ema_dist_200,
        "dc_breakout_strength": dc_breakout_strength,
        "vol_ratio_20": float(vol_ratio_20),
        "macd_hist": macd_hist,
        "bb_width_20": bb_width_20,
        "stoch_k_14": stoch_k_14,
        "btc_trend_30d": ctx.btc_trend_30d,
        "btc_dominance": ctx.btc_dominance,
        "fear_greed": int(ctx.fear_greed),
        "btc_corr_30d": ctx.btc_corr_30d,
        "daily_ema200_dist": daily_ema200_dist,
        "volume_krw_24h": float(df["volume"].iloc[-bars_per_day:].sum() * last_close),
        "market_cap_rank": int(ctx.market_cap_rank),
        "days_since_listing": int(ctx.days_since_listing),
        "hour_of_day": int(hour_of_day),
        "day_of_week": int(day_of_week),
        "is_weekend": is_weekend,
        "last_7d_return": last_7d_return,
        "max_drawdown_30d": max_drawdown_30d,
        "consecutive_up_days": int(consecutive_up_days),
    }

    # 카탈로그 순서/누락 검증 (학습/추론 일치 보장)
    missing = [c for c in FEATURE_COLUMNS if c not in feat]
    if missing:
        raise RuntimeError(f"feature missing: {missing}")
    return {c: feat[c] for c in FEATURE_COLUMNS}


def features_to_vector(feat: dict) -> np.ndarray:
    """dict → FEATURE_COLUMNS 순서의 1D ndarray (모델 입력용)."""
    return np.array([feat[c] for c in FEATURE_COLUMNS], dtype=np.float64)
