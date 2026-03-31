# -*- coding: utf-8 -*-
"""고도화 전략 모듈 — services/strategies/advanced.py

각 make_strategy_XXX 함수는 파라미터를 받아 strategy_fn을 반환한다.
strategy_fn: pd.DataFrame -> pd.Series (0 또는 1)

백테스트 엔진(BacktestEngine) 호환:
  - signal Series 인덱스는 df.index 와 동일해야 함
  - int(signal) == 1 이면 진입, 0 이면 대기/청산
  - 트레일링스탑이 있는 전략은 bar-by-bar loop 로 계산
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────
# 공통 지표 계산 함수
# ──────────────────────────────────────────────

def _calc_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder 평활화 방식 ATR."""
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    """지수이동평균."""
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def _calc_rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI (Wilder 평활화)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _calc_donchian_upper(df: pd.DataFrame, period: int) -> pd.Series:
    """Donchian 상단 채널 (전일까지의 최고가 — look-ahead 방지)."""
    return df["high"].shift(1).rolling(window=period, min_periods=period).max()


def _calc_vol_sma(df: pd.DataFrame, period: int) -> pd.Series:
    """거래량 단순이동평균."""
    return df["volume"].rolling(window=period, min_periods=period).mean()


# ──────────────────────────────────────────────
# 전략 1: DC(50) + ATR(14)x3.0 — 기존 메인 전략 (기준선)
# ──────────────────────────────────────────────

def make_strategy_dc_atr(
    dc_period: int = 50,
    atr_period: int = 14,
    atr_mult: float = 3.0,
) -> Callable[[pd.DataFrame], pd.Series]:
    """Donchian 채널 돌파 + ATR 트레일링스탑.

    진입: close > Donchian(dc_period) 상단 (shift(1) 적용으로 look-ahead 방지)
    청산: close < trailing_stop (rolling_highest - ATR * atr_mult)

    Phase 2 OOS 실적: Sharpe 1.123, MDD -18.7%
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        atr = _calc_atr(df, atr_period).values
        dc_upper = _calc_donchian_upper(df, dc_period).values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            a = atr[i]
            u = dc_upper[i]

            if not in_position:
                # 진입: DC 상단 돌파 (nan 체크 포함)
                if not np.isnan(u) and c > u:
                    in_position = True
                    highest = c
                    signal[i] = 1
            else:
                # 보유 중 — 고점 갱신 후 트레일링스탑 계산
                highest = max(highest, c)
                if not np.isnan(a):
                    trailing_stop = highest - a * atr_mult
                    if c < trailing_stop:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1  # ATR nan이면 보유 유지

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 2: RSI(10) + EMA(150) — 기존 보조 전략 (기준선)
# ──────────────────────────────────────────────

def make_strategy_rsi_ema(
    rsi_period: int = 10,
    rsi_entry: float = 50.0,
    rsi_exit: float = 45.0,
    ema_period: int = 150,
) -> Callable[[pd.DataFrame], pd.Series]:
    """RSI 모멘텀 + EMA 추세 필터.

    진입: close > EMA(ema_period) AND RSI(rsi_period) > rsi_entry
    청산: RSI(rsi_period) < rsi_exit

    Phase 2 OOS 실적: Sharpe 1.040, MDD -14.9%
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        rsi = _calc_rsi(close, rsi_period).values
        ema = _calc_ema(close, ema_period).values
        close_vals = close.values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False

        for i in range(n):
            c = close_vals[i]
            r = rsi[i]
            e = ema[i]

            if np.isnan(r) or np.isnan(e):
                signal[i] = 1 if in_position else 0
                continue

            if not in_position:
                if c > e and r > rsi_entry:
                    in_position = True
                    signal[i] = 1
            else:
                if r < rsi_exit:
                    in_position = False
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 3: 앙상블 투표
# ──────────────────────────────────────────────

def make_strategy_ensemble(
    dc_period: int = 50,
    atr_period: int = 14,
    atr_mult: float = 3.0,
    rsi_period: int = 10,
    rsi_entry: float = 50.0,
    rsi_exit: float = 45.0,
    ema_period: int = 150,
) -> Callable[[pd.DataFrame], pd.Series]:
    """DC+ATR 신호와 RSI+EMA 신호를 결합하는 앙상블 투표.

    진입: 둘 중 하나라도 매수 신호 → 1 (진입)
    청산: 두 전략 모두 대기/청산 신호 → 0

    0.5(절반 포지션) 개념을 반환하지만, BacktestEngine 호환을 위해
    "하나라도 1이면 1, 둘다 0이면 0"으로 단순화.
    포지션 사이징은 상위 레이어에서 활용 가능.
    """
    # 내부적으로 각 전략 팩토리를 재사용
    _dc = make_strategy_dc_atr(dc_period, atr_period, atr_mult)
    _rsi = make_strategy_rsi_ema(rsi_period, rsi_entry, rsi_exit, ema_period)

    def strategy(df: pd.DataFrame) -> pd.Series:
        sig_dc = _dc(df)
        sig_rsi = _rsi(df)

        # 두 신호의 합 (0, 1, 2)
        combined = sig_dc + sig_rsi

        # 하나라도 1이면 진입(1), 둘다 0이면 청산(0)
        signal = (combined > 0).astype(int)
        return pd.Series(signal.values, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 4: 변동성 레짐 스위칭
# ──────────────────────────────────────────────

def make_strategy_regime(
    dc_period: int = 50,
    atr_period: int = 14,
    vol_lookback: int = 60,
    atr_mult_high: float = 4.0,
    atr_mult_low: float = 2.0,
) -> Callable[[pd.DataFrame], pd.Series]:
    """ATR/close 비율로 변동성 레짐을 감지하여 ATR 배수를 동적으로 조정.

    변동성 측정:
      normalized_vol = ATR(14) / close
      vol_ma = normalized_vol 의 20일 이동평균
      레짐 임계값 = vol_lookback 기간의 중앙값

    고변동 레짐 (vol_ma > 중앙값): atr_mult_high (보수적, 넓은 스탑)
    저변동 레짐 (vol_ma <= 중앙값): atr_mult_low (공격적, 좁은 스탑)

    진입: DC(dc_period) 상단 돌파
    청산: 레짐별 트레일링스탑
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        atr = _calc_atr(df, atr_period).values
        dc_upper = _calc_donchian_upper(df, dc_period).values

        # 정규화 변동성 (ATR / close)
        close_series = df["close"]
        atr_series = _calc_atr(df, atr_period)
        norm_vol = atr_series / close_series
        # 20일 이동평균으로 스무딩
        vol_ma = norm_vol.rolling(window=20, min_periods=20).mean().values
        # vol_lookback 기간 중앙값으로 레짐 구분
        vol_median = (
            pd.Series(vol_ma)
            .rolling(window=vol_lookback, min_periods=vol_lookback)
            .median()
            .values
        )

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            a = atr[i]
            u = dc_upper[i]
            vm = vol_ma[i]
            vmed = vol_median[i]

            # 레짐 결정 (nan이면 보수적으로 고변동 처리)
            if np.isnan(vm) or np.isnan(vmed):
                mult = atr_mult_high
            else:
                mult = atr_mult_high if vm > vmed else atr_mult_low

            if not in_position:
                if not np.isnan(u) and c > u:
                    in_position = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                if not np.isnan(a):
                    trailing_stop = highest - a * mult
                    if c < trailing_stop:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 8: 데일리 단타 (DayTrading)
# ──────────────────────────────────────────────

def make_strategy_daytrading(
    dc_period: int = 15,
    vol_ma_period: int = 20,
    vol_threshold: float = 2.5,
    trail_pct: float = 0.02,
    sl_pct: float = 0.015,
    max_bars: int = 12,
    trend_period: int = 50,
) -> Callable[[pd.DataFrame], pd.Series]:
    """4시간봉 기반 데일리 단타 전략.

    단기 돌파 + 거래량 급증 + 추세 필터 + 빠른 트레일링.
    기존 추세추종(일봉)과 병행하여 매매 빈도를 높임.

    진입:
      - close > DC(15) 상단 (단기 돌파)
      - close > SMA(50) (추세 필터 — 상승추세에서만 진입)
      - volume > vol_sma(20) * 2.5 (거래량 급증 확인)
      - 양봉 (close > open)

    청산:
      - 2% 트레일링스탑 (고점 대비)
      - -1.5% 고정 손절
      - 최대 12봉(48시간) 보유

    백테스트 결과 (2024-2026, 18코인):
      726회 거래, 38% 승률, +0.91% 평균, MDD -33.1%, 월 27회
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        high = df["high"].values
        open_ = df["open"].values if "open" in df.columns else close
        volume = df["volume"].values

        dc_upper = pd.Series(high).shift(1).rolling(dc_period, min_periods=dc_period).max().values
        sma_trend = pd.Series(close).rolling(trend_period, min_periods=trend_period).mean().values
        vol_sma = pd.Series(volume).rolling(vol_ma_period, min_periods=vol_ma_period).mean().values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0
        entry_price = 0.0
        entry_idx = 0

        for i in range(n):
            c = close[i]
            v = volume[i]
            u = dc_upper[i]
            sma = sma_trend[i]
            vsma = vol_sma[i]

            if not in_position:
                dc_ok = (not np.isnan(u)) and (c > u)
                trend_ok = (not np.isnan(sma)) and (c > sma)
                vol_ok = (not np.isnan(vsma)) and (v > vsma * vol_threshold)
                candle_ok = c > open_[i]

                if dc_ok and trend_ok and vol_ok and candle_ok:
                    in_position = True
                    highest = c
                    entry_price = c
                    entry_idx = i
                    signal[i] = 1
            else:
                highest = max(highest, c)
                ret = c / entry_price - 1
                bars_held = i - entry_idx

                # 청산 조건: 트레일링 or 손절 or 시간초과
                trail_hit = c < highest * (1 - trail_pct)
                sl_hit = ret <= -sl_pct
                time_exit = bars_held > max_bars

                if trail_hit or sl_hit or time_exit:
                    in_position = False
                    highest = 0.0
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 5: 다중 타임프레임 (MTF)
# ──────────────────────────────────────────────

def make_strategy_mtf(
    trend_ema: int = 200,
    entry_dc: int = 30,
    atr_period: int = 14,
    atr_mult: float = 3.0,
) -> Callable[[pd.DataFrame], pd.Series]:
    """다중 타임프레임 추세추종.

    대세 방향 확인 (주봉 대용):
      - close > EMA(200): 대세 상승 필터
      (200일 EMA는 약 40주 EMA이며, 장기 추세 방향을 대리)

    일봉 진입:
      - close > DC(30) 상단 (더 빠른 진입)

    진입 조건: close > EMA(trend_ema) AND close > DC(entry_dc) 상단
    청산: ATR(atr_period) * atr_mult 트레일링스탑
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close_series = df["close"]
        close = close_series.values
        atr = _calc_atr(df, atr_period).values
        ema_trend = _calc_ema(close_series, trend_ema).values
        dc_entry_upper = _calc_donchian_upper(df, entry_dc).values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            a = atr[i]
            e = ema_trend[i]
            u = dc_entry_upper[i]

            if not in_position:
                # 대세 상승 AND 단기 DC 돌파
                if (
                    not np.isnan(e)
                    and not np.isnan(u)
                    and c > e
                    and c > u
                ):
                    in_position = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                if not np.isnan(a):
                    trailing_stop = highest - a * atr_mult
                    if c < trailing_stop:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 6: 거래량 확인 (Volume Confirmation)
# ──────────────────────────────────────────────

def make_strategy_volume(
    dc_period: int = 50,
    vol_ma_period: int = 20,
    vol_threshold: float = 1.5,
    atr_period: int = 14,
    atr_mult: float = 3.0,
) -> Callable[[pd.DataFrame], pd.Series]:
    """DC 돌파 + 거래량 급증 확인.

    진입: close > DC(dc_period) 상단
          AND volume > vol_sma(vol_ma_period) * vol_threshold

    거래량 조건이 추가되어 돌파의 신뢰도를 높임.
    청산: ATR 트레일링스탑 (기존 메인 전략과 동일)
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        volume = df["volume"].values
        atr = _calc_atr(df, atr_period).values
        dc_upper = _calc_donchian_upper(df, dc_period).values
        vol_sma = _calc_vol_sma(df, vol_ma_period).values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            v = volume[i]
            a = atr[i]
            u = dc_upper[i]
            vsma = vol_sma[i]

            if not in_position:
                # DC 돌파 + 거래량 급증 동시 충족
                vol_ok = (not np.isnan(vsma)) and (v > vsma * vol_threshold)
                dc_ok = (not np.isnan(u)) and (c > u)
                if dc_ok and vol_ok:
                    in_position = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                if not np.isnan(a):
                    trailing_stop = highest - a * atr_mult
                    if c < trailing_stop:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 7: 복합 최종 전략 (Composite)
# ──────────────────────────────────────────────

def make_strategy_composite(
    dc_period: int = 50,
    rsi_period: int = 10,
    rsi_threshold: float = 50.0,
    vol_ma: int = 20,
    vol_mult: float = 1.5,
    atr_period: int = 14,
    vol_lookback: int = 60,
) -> Callable[[pd.DataFrame], pd.Series]:
    """앙상블 + 변동성 적응형 트레일링스탑 복합 전략 (추천 조합).

    진입 조건 (AND/OR 구조):
      필수: close > DC(dc_period) 상단
      선택: RSI(rsi_period) > rsi_threshold
            OR volume > vol_sma(vol_ma) * vol_mult
      → DC 돌파 + (모멘텀 또는 거래량) 중 하나 이상 충족

    청산 — 변동성 적응형 트레일링스탑:
      normalized_vol = ATR(atr_period) / close 의 vol_lookback일 percentile rank (0~1)
      adaptive_mult = 2.0 + 2.0 * normalized_vol  → 범위 [2.0, 4.0]
      trailing_stop = rolling_highest - ATR * adaptive_mult

    변동성이 높을수록 스탑이 넓어져 노이즈 청산을 방지.
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close_series = df["close"]
        close = close_series.values
        volume = df["volume"].values
        atr_series = _calc_atr(df, atr_period)
        atr = atr_series.values
        dc_upper = _calc_donchian_upper(df, dc_period).values
        rsi_vals = _calc_rsi(close_series, rsi_period).values
        vsma = _calc_vol_sma(df, vol_ma).values

        # 정규화 변동성 percentile rank 계산
        norm_vol = (atr_series / close_series).values  # 각 봉의 ATR/close

        def _percentile_rank(arr: np.ndarray, lookback: int) -> np.ndarray:
            """rolling percentile rank: 현재 값이 과거 lookback봉 중 몇 번째 백분위인지."""
            n = len(arr)
            ranks = np.full(n, np.nan)
            for i in range(lookback - 1, n):
                window = arr[i - lookback + 1: i + 1]
                valid = window[~np.isnan(window)]
                if len(valid) < lookback // 2:
                    continue
                current = arr[i]
                if np.isnan(current):
                    continue
                ranks[i] = np.sum(valid <= current) / len(valid)
            return ranks

        pct_rank = _percentile_rank(norm_vol, vol_lookback)

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        highest = 0.0

        for i in range(n):
            c = close[i]
            v = volume[i]
            a = atr[i]
            u = dc_upper[i]
            r = rsi_vals[i]
            vs = vsma[i]
            pr = pct_rank[i]

            # 적응형 배수: percentile rank nan이면 기본값 3.0 사용
            if np.isnan(pr):
                adaptive_mult = 3.0
            else:
                adaptive_mult = 2.0 + 2.0 * pr  # [2.0, 4.0] 범위

            if not in_position:
                dc_ok = (not np.isnan(u)) and (c > u)
                rsi_ok = (not np.isnan(r)) and (r > rsi_threshold)
                vol_ok = (not np.isnan(vs)) and (v > vs * vol_mult)

                # DC 필수 + (RSI 또는 거래량) 하나 이상
                if dc_ok and (rsi_ok or vol_ok):
                    in_position = True
                    highest = c
                    signal[i] = 1
            else:
                highest = max(highest, c)
                if not np.isnan(a):
                    trailing_stop = highest - a * adaptive_mult
                    if c < trailing_stop:
                        in_position = False
                        highest = 0.0
                        signal[i] = 0
                    else:
                        signal[i] = 1
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 10: 변동성 돌파 일중회전 (Volatility Breakout)
# ──────────────────────────────────────────────

def make_strategy_volatility_breakout(
    k_bull: float = 0.4,
    k_neutral: float = 0.5,
    k_bear: float = 0.7,
    sl_pct: float = 0.015,
    sma_period: int = 50,
) -> Callable[[pd.DataFrame], pd.Series]:
    """Larry Williams 변동성 돌파 — 레짐 적응형 일중회전.

    진입: close > open + 전일(high-low) × K
      K는 SMA(50) 기준 레짐에 따라 자동 조절
      상승장(close>SMA): 공격적(K=0.4), 하락장: 보수적(K=0.7)

    청산: 다음 봉 자동 청산 (1봉 보유) 또는 -1.5% 손절

    특징: 일 1회 거래, 수수료 0.1%를 평균 변동 3~5%로 충분히 커버.
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        open_ = df["open"].values
        high = df["high"].values
        low = df["low"].values

        sma = _calc_ema(pd.Series(close), sma_period).values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_position = False
        entry_price = 0.0

        for i in range(1, n):
            c = close[i]
            o = open_[i]

            if in_position:
                # 1봉 보유 후 무조건 청산 또는 손절
                ret = c / entry_price - 1
                if ret <= -sl_pct:
                    signal[i] = 0
                else:
                    signal[i] = 0  # 1봉 보유 후 청산
                in_position = False
                continue

            # 레짐 판별
            s = sma[i]
            if np.isnan(s):
                k = k_neutral
            elif c > s:
                k = k_bull
            else:
                k = k_bear

            # 전일 변동폭
            prev_range = high[i - 1] - low[i - 1]
            threshold = o + prev_range * k

            if c > threshold and prev_range > 0:
                in_position = True
                entry_price = c
                signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 11: 알트 펌프 서핑 (Alt Pump Surf)
# ──────────────────────────────────────────────

def make_strategy_alt_pump_surf(
    vol_mult_bull: float = 4.0,
    vol_mult_bear: float = 6.0,
    vol_ma_period: int = 20,
    pct_threshold: float = 0.02,
    tp_pct: float = 0.03,
    trail_pct: float = 0.015,
    sl_pct: float = 0.01,
    max_bars: int = 8,
    sma_period: int = 50,
) -> Callable[[pd.DataFrame], pd.Series]:
    """알트코인 펌프 초기 감지 → 서핑 → 빠른 청산.

    업비트 리테일 시장 특화: 펌프/덤프 패턴이 예측 가능하고,
    숏이 없어서 펌프 지속시간이 길다 (매도 압력 약함).

    진입:
      - 거래량 > SMA(20) × vol_mult (상승장 4x, 하락장 6x)
      - 가격 변동 > +2% (봉 내)
      - 양봉 (close > open)

    청산:
      - +3% 익절 또는 1.5% 트레일링
      - -1% 손절
      - 최대 8봉(32h on 4h) 보유
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        open_ = df["open"].values
        volume = df["volume"].values

        sma = _calc_ema(pd.Series(close), sma_period).values
        vol_sma = pd.Series(volume).rolling(
            vol_ma_period, min_periods=vol_ma_period
        ).mean().values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_pos = False
        ep = 0.0
        hi = 0.0
        ei = 0

        for i in range(n):
            c = close[i]
            o = open_[i]
            v = volume[i]
            vsma = vol_sma[i]
            s = sma[i]

            if not in_pos:
                # 레짐별 거래량 배수
                if np.isnan(s):
                    vm = vol_mult_bull
                elif c > s:
                    vm = vol_mult_bull
                else:
                    vm = vol_mult_bear

                vol_ok = (not np.isnan(vsma)) and (v > vsma * vm)
                pct_chg = (c - o) / o if o > 0 else 0
                pct_ok = pct_chg > pct_threshold
                bull_candle = c > o

                if vol_ok and pct_ok and bull_candle:
                    in_pos = True
                    ep = c
                    hi = c
                    ei = i
                    signal[i] = 1
            else:
                hi = max(hi, c)
                ret = c / ep - 1
                bars = i - ei

                tp_hit = ret >= tp_pct
                trail_hit = c < hi * (1 - trail_pct)
                sl_hit = ret <= -sl_pct
                time_exit = bars > max_bars

                if tp_hit or trail_hit or sl_hit or time_exit:
                    in_pos = False
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 12: RSI 다이버전스 반전 (RSI Divergence)
# ──────────────────────────────────────────────

def make_strategy_rsi_divergence(
    rsi_period: int = 14,
    lookback: int = 20,
    atr_period: int = 14,
    atr_mult: float = 2.0,
    tp_pct: float = 0.05,
    sl_pct: float = 0.02,
    max_bars: int = 12,
    sma_period: int = 50,
) -> Callable[[pd.DataFrame], pd.Series]:
    """RSI 다이버전스 기반 반전 매매 — 레짐 자동 전환.

    상승 다이버전스 (하락장용):
      가격: lower low, RSI: higher low → 하락 모멘텀 약화 → 매수

    히든 다이버전스 (상승장용):
      가격: higher low, RSI: lower low → 추세 지속 눌림목 → 매수

    레짐 판별: SMA(50) 위 = 히든만, 아래 = 일반만
    청산: ATR(14)x2.0 트레일링 / +5% 익절 / -2% 손절 / 12봉 시간제한
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        open_ = df["open"].values
        low = df["low"].values

        rsi = _calc_rsi(pd.Series(close), rsi_period).values
        atr = _calc_atr(df, atr_period).values
        sma = _calc_ema(pd.Series(close), sma_period).values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_pos = False
        ep = 0.0
        hi = 0.0
        ei = 0

        half = lookback // 2

        for i in range(lookback, n):
            c = close[i]

            if not in_pos:
                # 직전 두 구간의 최저가 & 최저 RSI
                recent_low = np.min(low[i - half: i])
                prev_low = np.min(low[i - lookback: i - half])
                recent_rsi_low = np.nanmin(rsi[i - half: i])
                prev_rsi_low = np.nanmin(rsi[i - lookback: i - half])

                if np.isnan(recent_rsi_low) or np.isnan(prev_rsi_low):
                    continue

                s = sma[i]
                bull_candle = c > open_[i]

                if np.isnan(s):
                    continue

                entered = False

                if c <= s:
                    # 하락장 → 일반 다이버전스 (price LL, RSI HL)
                    if recent_low < prev_low and recent_rsi_low > prev_rsi_low and bull_candle:
                        entered = True
                else:
                    # 상승장 → 히든 다이버전스 (price HL, RSI LL)
                    if recent_low > prev_low and recent_rsi_low < prev_rsi_low and bull_candle:
                        entered = True

                if entered:
                    in_pos = True
                    ep = c
                    hi = c
                    ei = i
                    signal[i] = 1

            else:
                hi = max(hi, c)
                a = atr[i]
                ret = c / ep - 1
                bars = i - ei

                # 청산 조건
                tp_hit = ret >= tp_pct
                sl_hit = ret <= -sl_pct
                time_exit = bars > max_bars
                trail_hit = (not np.isnan(a)) and (c < hi - a * atr_mult)

                if tp_hit or sl_hit or time_exit or trail_hit:
                    in_pos = False
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy


# ──────────────────────────────────────────────
# 전략 9: 거래량 반전 단타 (Volume Reversal)
# ──────────────────────────────────────────────

def make_strategy_vol_reversal(
    rsi_period: int = 14,
    rsi_threshold: float = 35.0,
    vol_ma_period: int = 20,
    vol_threshold: float = 3.0,
    tp_pct: float = 0.03,
    trail_pct: float = 0.015,
    sl_pct: float = 0.02,
    max_bars: int = 8,
) -> Callable[[pd.DataFrame], pd.Series]:
    """하락장 전용 거래량 반전 단타.

    핵심: 하락 중 거래량 3배 급증 + 양봉 전환 = 바닥 신호.
    추세 필터 없음 — 하락장에서도 작동하도록 설계.

    진입:
      - 거래량 > 20봉 평균 x 3.0 (급증)
      - RSI(14) < 35 (과매도 구간)
      - 현재봉 양봉 (close > open)
      - 이전봉 음봉 (반전 확인)

    청산:
      - +3% 익절, 1.5% 트레일링, -2% 손절, 8봉(32h) 시간제한

    하락장 6개월 백테스트: 31건, 71% 승률, +1.78% 평균, MDD -5.2%
    """

    def strategy(df: pd.DataFrame) -> pd.Series:
        close = df["close"].values
        open_ = df["open"].values if "open" in df.columns else close
        volume = df["volume"].values

        rsi = _calc_rsi(pd.Series(close), rsi_period).values
        vol_sma = pd.Series(volume).rolling(
            vol_ma_period, min_periods=vol_ma_period
        ).mean().values

        n = len(df)
        signal = np.zeros(n, dtype=np.int8)
        in_pos = False
        ep = 0.0
        hi = 0.0
        ei = 0

        for i in range(1, n):
            c = close[i]
            v = volume[i]
            r = rsi[i]
            vsma = vol_sma[i]

            if not in_pos:
                vol_ok = (not np.isnan(vsma)) and (v > vsma * vol_threshold)
                rsi_ok = (not np.isnan(r)) and (r < rsi_threshold)
                bull = c > open_[i]
                prev_bear = close[i - 1] < open_[i - 1]

                if vol_ok and rsi_ok and bull and prev_bear:
                    in_pos = True
                    ep = c
                    hi = c
                    ei = i
                    signal[i] = 1
            else:
                hi = max(hi, c)
                ret = c / ep - 1
                bars = i - ei

                if ret >= tp_pct or c < hi * (1 - trail_pct) or ret <= -sl_pct or bars > max_bars:
                    in_pos = False
                    signal[i] = 0
                else:
                    signal[i] = 1

        return pd.Series(signal, index=df.index, dtype=int)

    return strategy
