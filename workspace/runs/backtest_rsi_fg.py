"""
백테스트: RSI 과매도 vs 공포탐욕지수 역추세 전략 비교
기간: 2023-01-01 ~ 2025-03-01 (일봉, BTC/KRW)
"""
import sys, asyncio, json
from pathlib import Path

# 프로젝트 루트를 path에 추가
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv, fetch_fear_greed

START = "2023-01-01T00:00:00Z"
END   = "2025-03-01T00:00:00Z"

# ─────────────────────────────────────────
# 전략 A: RSI 과매도 반등
# 매수: RSI ≤ 30 / 청산: RSI ≥ 55
# ─────────────────────────────────────────
def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def strategy_rsi(df: pd.DataFrame) -> pd.Series:
    rsi = _rsi(df["close"], 14)
    signal = pd.Series(np.nan, index=df.index)
    position = 0
    for i in range(len(df)):
        r = rsi.iloc[i]
        if np.isnan(r):
            signal.iloc[i] = position
            continue
        if position == 0 and r <= 30:
            position = 1
        elif position == 1 and r >= 55:
            position = 0
        signal.iloc[i] = position
    return signal


# ─────────────────────────────────────────
# 전략 B: 공포탐욕지수 역추세
# 매수: F&G ≤ 25 (Extreme Fear) / 청산: F&G ≥ 55
# ─────────────────────────────────────────
def make_strategy_fg(fg_df: pd.DataFrame):
    """fg_df: date(str), value(float) 컬럼"""
    fg_map = dict(zip(fg_df["date"], fg_df["value"]))

    def strategy_fg(df: pd.DataFrame) -> pd.Series:
        signal = pd.Series(np.nan, index=df.index)
        position = 0
        for i in range(len(df)):
            ts_ms = int(df["ts"].iloc[i])
            date_str = pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
            fg = fg_map.get(date_str, np.nan)
            if np.isnan(fg):
                signal.iloc[i] = position
                continue
            if position == 0 and fg <= 25:
                position = 1
            elif position == 1 and fg >= 55:
                position = 0
            signal.iloc[i] = position
        return signal

    return strategy_fg


# ─────────────────────────────────────────
# 전략 C: RSI + F&G 복합 (둘 다 조건 충족 시 매수)
# ─────────────────────────────────────────
def make_strategy_combined(fg_df: pd.DataFrame):
    fg_map = dict(zip(fg_df["date"], fg_df["value"]))

    def strategy_combined(df: pd.DataFrame) -> pd.Series:
        rsi = _rsi(df["close"], 14)
        signal = pd.Series(np.nan, index=df.index)
        position = 0
        for i in range(len(df)):
            ts_ms = int(df["ts"].iloc[i])
            date_str = pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
            fg = fg_map.get(date_str, np.nan)
            r = rsi.iloc[i]
            if np.isnan(fg) or np.isnan(r):
                signal.iloc[i] = position
                continue
            # 매수: RSI ≤ 32 AND F&G ≤ 30
            if position == 0 and r <= 32 and fg <= 30:
                position = 1
            # 청산: RSI ≥ 55 OR F&G ≥ 60
            elif position == 1 and (r >= 55 or fg >= 60):
                position = 0
            signal.iloc[i] = position
        return signal

    return strategy_combined


async def main():
    print("데이터 수집 중...")
    ohlcv_raw = await fetch_ohlcv("BTC/KRW", "1d", START, END)
    fg_raw    = await fetch_fear_greed(START, END)

    ohlcv_df = pd.DataFrame(ohlcv_raw)
    fg_df    = pd.DataFrame(fg_raw)

    print(f"OHLCV: {len(ohlcv_df)}bars  /  F&G: {len(fg_df)}days")

    engine = BacktestEngine()

    print("\n[전략 A] RSI 과매도 반등 실행 중...")
    result_a = engine.run(strategy_rsi, ohlcv_df)

    print("[전략 B] 공포탐욕지수 역추세 실행 중...")
    result_b = engine.run(make_strategy_fg(fg_df), ohlcv_df)

    print("[전략 C] RSI + F&G 복합 신호 실행 중...")
    result_c = engine.run(make_strategy_combined(fg_df), ohlcv_df)

    # Buy & Hold 비교 기준
    bh_return = (ohlcv_df["close"].iloc[-1] / ohlcv_df["close"].iloc[0]) - 1

    print("\n" + "="*60)
    print("백테스트 결과 요약 (2023-01-01 ~ 2025-03-01, BTC/KRW 일봉)")
    print("="*60)

    def fmt(label, r):
        m = r.metrics
        print(f"\n【{label}】  run_id: {r.run_id}")
        print(f"  총수익률   : {m.total_return*100:+.1f}%  (BuyHold: {bh_return*100:+.1f}%)")
        print(f"  샤프비율   : {m.sharpe:.3f}")
        print(f"  칼마비율   : {m.calmar:.3f}")
        print(f"  최대낙폭   : {m.max_drawdown*100:.1f}%")
        print(f"  거래횟수   : {m.n_trades}회")
        print(f"  승률       : {m.win_rate*100:.1f}%")
        print(f"  평균거래수익: {m.avg_trade_return*100:.2f}%")

    fmt("전략 A: RSI ≤30 반등", result_a)
    fmt("전략 B: F&G ≤25 역추세", result_b)
    fmt("전략 C: RSI+F&G 복합", result_c)

    print("\n" + "="*60)

    # 최고 샤프 전략 선택
    best = max(
        [("A(RSI)", result_a), ("B(F&G)", result_b), ("C(복합)", result_c)],
        key=lambda x: x[1].metrics.sharpe
    )
    print(f"★ 최고 샤프비율 전략: {best[0]}  (Sharpe {best[1].metrics.sharpe:.3f})")
    print("="*60)

    return {
        "A": result_a.metrics.__dict__,
        "B": result_b.metrics.__dict__,
        "C": result_c.metrics.__dict__,
        "buy_hold_return": round(bh_return, 4),
        "winner": best[0],
    }


if __name__ == "__main__":
    results = asyncio.run(main())
