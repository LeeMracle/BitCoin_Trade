"""
멀티코인 Donchian(50)+ATR(14)x3.0 백테스트
==========================================
상위 거래량 코인에 동일 전략 적용, 종목별 IS/OOS 검증
"""
import sys, asyncio, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv

WARMUP_START = "2019-01-01T00:00:00Z"
IS_START     = "2020-01-01T00:00:00Z"
IS_END       = "2023-12-31T00:00:00Z"
OOS_START    = "2024-01-01T00:00:00Z"
OOS_END      = "2026-03-27T00:00:00Z"

COINS = [
    "BTC/KRW", "ETH/KRW", "XRP/KRW", "SOL/KRW", "DOGE/KRW",
    "ADA/KRW", "SUI/KRW", "AVAX/KRW", "DOT/KRW", "LINK/KRW",
    "NEAR/KRW", "ETC/KRW", "HBAR/KRW", "APT/KRW",
]


def calc_ema(s, p):
    return s.ewm(span=p, min_periods=p, adjust=False).mean()

def calc_atr(df, period=14):
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

def make_donchian_atr(entry_period=50, atr_period=14, atr_mult=3.0):
    def strategy(df):
        upper = df["high"].shift(1).rolling(window=entry_period, min_periods=entry_period).max()
        atr = calc_atr(df, atr_period)
        signal = pd.Series(0, index=df.index)
        pos = 0
        highest = 0.0
        trail_stop = 0.0
        for i in range(1, len(df)):
            if np.isnan(upper.iloc[i]) or np.isnan(atr.iloc[i]):
                signal.iloc[i] = pos; continue
            c = df["close"].iloc[i]
            if pos == 0:
                if c > upper.iloc[i]:
                    pos = 1
                    highest = c
                    trail_stop = c - atr.iloc[i] * atr_mult
            elif pos == 1:
                if c > highest:
                    highest = c
                    trail_stop = max(trail_stop, c - atr.iloc[i] * atr_mult)
                if c < trail_stop:
                    pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


async def main():
    from datetime import datetime
    def iso_ms(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)

    print("=" * 75)
    print("멀티코인 Donchian(50)+ATR(14)x3.0 백테스트")
    print("=" * 75)

    engine = BacktestEngine()
    results = []

    for coin in COINS:
        print(f"\n[{coin}] 데이터 수집 중...")
        try:
            raw = await fetch_ohlcv(coin, "1d", WARMUP_START, OOS_END)
            df_full = pd.DataFrame(raw)
            if len(df_full) < 100:
                print(f"  데이터 부족 ({len(df_full)}일) — 건너뜀")
                continue
        except Exception as e:
            print(f"  수집 실패: {e} — 건너뜀")
            continue

        df_is = df_full[df_full["ts"] <= iso_ms(IS_END)].copy().reset_index(drop=True)
        df_oos = df_full[df_full["ts"] >= iso_ms(OOS_START)].copy().reset_index(drop=True)

        if len(df_is) < 80 or len(df_oos) < 30:
            print(f"  IS {len(df_is)}일 / OOS {len(df_oos)}일 — 부족, 건너뜀")
            continue

        is_start_idx = df_is[df_is["ts"] >= iso_ms(IS_START)].index
        if len(is_start_idx) == 0:
            print(f"  IS 시작점 없음 — 건너뜀")
            continue

        bh_is = df_is["close"].iloc[-1] / df_is["close"].iloc[is_start_idx[0]] - 1
        bh_oos = df_oos["close"].iloc[-1] / df_oos["close"].iloc[0] - 1

        strat = make_donchian_atr(50, 14, 3.0)

        r_is = engine.run(strat, df_is)
        r_oos = engine.run(strat, df_oos)

        mi = r_is.metrics
        mo = r_oos.metrics

        results.append({
            "coin": coin,
            "is_bars": len(df_is), "oos_bars": len(df_oos),
            "bh_is": round(bh_is, 4), "bh_oos": round(bh_oos, 4),
            "is_sharpe": mi.sharpe, "is_return": mi.total_return,
            "is_mdd": mi.max_drawdown, "is_trades": mi.n_trades, "is_winrate": mi.win_rate,
            "oos_sharpe": mo.sharpe, "oos_return": mo.total_return,
            "oos_mdd": mo.max_drawdown, "oos_trades": mo.n_trades, "oos_winrate": mo.win_rate,
            "oos_avg_trade": mo.avg_trade_return,
        })

        print(f"  IS: Sharpe {mi.sharpe:.3f}, 수익 {mi.total_return*100:+.1f}%, "
              f"MDD {mi.max_drawdown*100:.1f}%, 거래 {mi.n_trades}회")
        print(f"  OOS: Sharpe {mo.sharpe:.3f}, 수익 {mo.total_return*100:+.1f}%, "
              f"MDD {mo.max_drawdown*100:.1f}%, 거래 {mo.n_trades}회, 승률 {mo.win_rate*100:.0f}%")

    # 종합 결과
    df = pd.DataFrame(results).sort_values("oos_sharpe", ascending=False)

    print("\n" + "=" * 75)
    print("OOS 결과 종합 (Sharpe 기준)")
    print("=" * 75)
    cols = ["coin", "oos_sharpe", "oos_return", "oos_mdd", "oos_trades", "oos_winrate", "bh_oos"]
    print(df[cols].to_string(index=False))

    # 추천 종목 (OOS Sharpe > 0, 거래 2회 이상)
    good = df[(df["oos_sharpe"] > 0) & (df["oos_trades"] >= 2)]
    print(f"\n추천 종목 (OOS Sharpe > 0, 거래 2+): {len(good)}개")
    if len(good) > 0:
        print(good[cols].to_string(index=False))

    print("=" * 75)

    return {"all": df.to_dict("records"), "recommended": good["coin"].tolist() if len(good) > 0 else []}


if __name__ == "__main__":
    results = asyncio.run(main())
    out = Path(__file__).parent / "backtest_multi_coin_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out}")
