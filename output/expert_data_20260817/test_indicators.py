"""step3 지표 함수 정합성 검증 — 미래정보 누출 zero 확인.

간단한 시나리오 데이터로 EMA/RSI/ATR/Engulfing/Hammer 를 재현하고 예상값과 대조.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from step3_backtest_mtf_pullback_5r import (
    ema, rsi, atr, bullish_engulfing, hammer,
)


def test_ema_no_lookahead():
    s = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    e = ema(s, 3)
    # min_periods=3 → 인덱스 2까지 NaN 이어야 함
    assert pd.isna(e.iloc[0]) and pd.isna(e.iloc[1]), "EMA min_periods 미준수"
    assert not pd.isna(e.iloc[2])
    # 미래정보 누출 없음: 인덱스 5 값은 인덱스 0~5 만으로 결정
    e_short = ema(s.iloc[:6], 3)
    assert abs(e.iloc[5] - e_short.iloc[5]) < 1e-10, "EMA lookahead 발생"
    print("PASS: EMA lookahead-free")


def test_rsi_no_lookahead():
    # 무작위 계열
    np.random.seed(42)
    s = pd.Series(100 + np.cumsum(np.random.randn(50)))
    r = rsi(s, 14)
    r_short = rsi(s.iloc[:30], 14)
    for i in range(15, 30):
        assert abs(r.iloc[i] - r_short.iloc[i]) < 1e-8, f"RSI lookahead at idx {i}"
    print("PASS: RSI lookahead-free")


def test_atr_no_lookahead():
    np.random.seed(0)
    n = 50
    df = pd.DataFrame({
        "high": 100 + np.cumsum(np.random.randn(n)) + 1,
        "low": 100 + np.cumsum(np.random.randn(n)) - 1,
        "close": 100 + np.cumsum(np.random.randn(n)),
    })
    df["open"] = df["close"].shift(1).fillna(df["close"].iloc[0])
    a = atr(df, 14)
    a_short = atr(df.iloc[:30], 14)
    for i in range(15, 30):
        assert abs(a.iloc[i] - a_short.iloc[i]) < 1e-8, f"ATR lookahead at idx {i}"
    print("PASS: ATR lookahead-free")


def test_bullish_engulfing():
    # 케이스 1: 명확한 강세 감쌈 (현재봉 open < prev_close AND close > prev_open)
    #   직전봉: open=100 close=94 (몸통 6, 하락봉)
    #   현재봉: open=93 close=101 (몸통 8, 상승봉, 감쌈)
    df = pd.DataFrame({
        "open":  [100, 93],
        "high":  [102, 103],
        "low":   [93,  92],
        "close": [94,  101],
    })
    r = bullish_engulfing(df)
    assert r.iloc[1] == True, "case1 fail"

    # 케이스 2: 몸통 작아서 감싸지 못함
    df2 = pd.DataFrame({
        "open":  [100, 95],
        "high":  [102, 97],
        "low":   [93,  94],
        "close": [94,  96],
    })
    r2 = bullish_engulfing(df2)
    assert r2.iloc[1] == False, "case2 fail"
    print("PASS: bullish_engulfing")


def test_hammer():
    # 하꼬리 몸통 2배 + 상꼬리 짧음 + 종가>시가
    df = pd.DataFrame({
        "open":  [100],
        "high":  [101],
        "low":   [90],
        "close": [101],
    })
    # 몸통=|101-100|=1, 하꼬리=min(100,101)-90=10 ≥ 2, 상꼬리=101-max=0 ≤ 0.5
    r = hammer(df)
    assert r.iloc[0] == True, "hammer case1 fail"

    # 상꼬리 큰 케이스
    df2 = pd.DataFrame({
        "open":  [100],
        "high":  [110],
        "low":   [90],
        "close": [101],
    })
    r2 = hammer(df2)
    assert r2.iloc[0] == False, "hammer case2 fail"
    print("PASS: hammer")


if __name__ == "__main__":
    test_ema_no_lookahead()
    test_rsi_no_lookahead()
    test_atr_no_lookahead()
    test_bullish_engulfing()
    test_hammer()
    print("\nALL PASS")
