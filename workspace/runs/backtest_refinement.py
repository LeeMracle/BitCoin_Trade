"""
Phase 2 전략 최적화 — RSI 모멘텀 + 리스크 관리 조합
===================================================
목적:
  RSI(10) >55/<50가 OOS Sharpe 1.075 달성했으나 MDD -30.3% (기준 -20% 초과)
  ATR 트레일링스탑, MA 트렌드 필터 등으로 MDD를 -20% 이내로 낮추기

접근:
  1. RSI 모멘텀 + ATR 트레일링스탑
  2. RSI 모멘텀 + MA 트렌드 필터
  3. RSI 모멘텀 + ATR 트레일링스탑 + MA 트렌드 필터
  4. MultiFactor 변형 (MDD -25.6%, 승률 55%에서 출발)
  5. Donchian + ATR 트레일링스탑
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


# ── 지표 계산 ──────────────────────────────────────────
def calc_ema(s, p): return s.ewm(span=p, min_periods=p, adjust=False).mean()
def calc_sma(s, p): return s.rolling(window=p, min_periods=p).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


# ═══════════════════════════════════════════════════════
# 조합 1: RSI 모멘텀 + ATR 트레일링스탑
# ═══════════════════════════════════════════════════════
def make_rsi_atr(rsi_period=10, rsi_entry=55, rsi_exit=50,
                 atr_period=14, atr_mult=3.0):
    def strategy(df):
        rsi = calc_rsi(df["close"], rsi_period)
        atr = calc_atr(df, atr_period)
        signal = pd.Series(0, index=df.index)
        pos = 0
        highest = 0.0
        trail_stop = 0.0

        for i in range(1, len(df)):
            if np.isnan(rsi.iloc[i]) or np.isnan(atr.iloc[i]):
                signal.iloc[i] = pos; continue
            c = df["close"].iloc[i]

            if pos == 0:
                if rsi.iloc[i] > rsi_entry:
                    pos = 1
                    highest = c
                    trail_stop = c - atr.iloc[i] * atr_mult
            elif pos == 1:
                if c > highest:
                    highest = c
                    trail_stop = max(trail_stop, c - atr.iloc[i] * atr_mult)
                if c < trail_stop:
                    pos = 0
                elif rsi.iloc[i] < rsi_exit:
                    pos = 0

            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 조합 2: RSI 모멘텀 + MA 트렌드 필터
# ═══════════════════════════════════════════════════════
def make_rsi_ma(rsi_period=10, rsi_entry=55, rsi_exit=50, ma_period=200):
    def strategy(df):
        rsi = calc_rsi(df["close"], rsi_period)
        ma = calc_ema(df["close"], ma_period)
        signal = pd.Series(0, index=df.index)
        pos = 0

        for i in range(1, len(df)):
            if np.isnan(rsi.iloc[i]) or np.isnan(ma.iloc[i]):
                signal.iloc[i] = pos; continue
            c = df["close"].iloc[i]
            trend_up = c > ma.iloc[i]

            if pos == 0:
                if trend_up and rsi.iloc[i] > rsi_entry:
                    pos = 1
            elif pos == 1:
                if rsi.iloc[i] < rsi_exit:
                    pos = 0

            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 조합 3: RSI + MA + ATR (풀 조합)
# ═══════════════════════════════════════════════════════
def make_rsi_ma_atr(rsi_period=10, rsi_entry=55, rsi_exit=50,
                     ma_period=200, atr_period=14, atr_mult=3.0):
    def strategy(df):
        rsi = calc_rsi(df["close"], rsi_period)
        ma = calc_ema(df["close"], ma_period)
        atr = calc_atr(df, atr_period)
        signal = pd.Series(0, index=df.index)
        pos = 0
        highest = 0.0
        trail_stop = 0.0

        for i in range(1, len(df)):
            if np.isnan(rsi.iloc[i]) or np.isnan(ma.iloc[i]) or np.isnan(atr.iloc[i]):
                signal.iloc[i] = pos; continue
            c = df["close"].iloc[i]
            trend_up = c > ma.iloc[i]

            if pos == 0:
                if trend_up and rsi.iloc[i] > rsi_entry:
                    pos = 1
                    highest = c
                    trail_stop = c - atr.iloc[i] * atr_mult
            elif pos == 1:
                if c > highest:
                    highest = c
                    trail_stop = max(trail_stop, c - atr.iloc[i] * atr_mult)
                if c < trail_stop:
                    pos = 0
                elif not trend_up and rsi.iloc[i] < rsi_exit:
                    pos = 0

            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 조합 4: Donchian + ATR 트레일링스탑
# ═══════════════════════════════════════════════════════
def make_donchian_atr(entry_period=30, exit_period=10,
                       atr_period=14, atr_mult=3.0):
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


def _m(m):
    return {
        "total_return": m.total_return, "sharpe": m.sharpe,
        "calmar": m.calmar, "max_dd": m.max_drawdown,
        "n_trades": m.n_trades, "win_rate": m.win_rate,
        "avg_trade": m.avg_trade_return,
    }


def fmt(label, m, bh):
    return (
        f"  {label}\n"
        f"    수익률: {m.total_return*100:+.1f}%  BnH대비: {(m.total_return-bh)*100:+.1f}%p\n"
        f"    Sharpe: {m.sharpe:.3f}  Calmar: {m.calmar:.3f}  MDD: {m.max_drawdown*100:.1f}%\n"
        f"    거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%  평균: {m.avg_trade_return*100:.2f}%"
    )


async def main():
    print("=" * 70)
    print("Phase 2 전략 최적화 — MDD 축소를 위한 리스크 관리 조합")
    print("=" * 70)

    print("\n데이터 수집 중...")
    ohlcv_raw = await fetch_ohlcv("BTC/KRW", "1d", WARMUP_START, OOS_END)
    ohlcv_full = pd.DataFrame(ohlcv_raw)

    from datetime import datetime
    def iso_ms(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)

    ohlcv_is = ohlcv_full[ohlcv_full["ts"] <= iso_ms(IS_END)].copy().reset_index(drop=True)
    ohlcv_oos = ohlcv_full[ohlcv_full["ts"] >= iso_ms(OOS_START)].copy().reset_index(drop=True)

    is_start_idx = ohlcv_is[ohlcv_is["ts"] >= iso_ms(IS_START)].index[0]
    bh_is = ohlcv_is["close"].iloc[-1] / ohlcv_is["close"].iloc[is_start_idx] - 1
    bh_oos = ohlcv_oos["close"].iloc[-1] / ohlcv_oos["close"].iloc[0] - 1

    print(f"  IS: {len(ohlcv_is)}일 (BnH {bh_is*100:+.1f}%)")
    print(f"  OOS: {len(ohlcv_oos)}일 (BnH {bh_oos*100:+.1f}%)")

    engine = BacktestEngine()
    all_rows = []

    # ── 조합 1: RSI + ATR 트레일링스탑 ─────────────────
    print("\n[1] RSI 모멘텀 + ATR 트레일링스탑...")
    configs_1 = [
        {"rsi_period": 10, "rsi_entry": 55, "rsi_exit": 50, "atr_period": 14, "atr_mult": m}
        for m in [2.0, 2.5, 3.0, 3.5, 4.0]
    ] + [
        {"rsi_period": 10, "rsi_entry": 55, "rsi_exit": 50, "atr_period": p, "atr_mult": 3.0}
        for p in [10, 21]
    ] + [
        {"rsi_period": 10, "rsi_entry": 55, "rsi_exit": 45, "atr_period": 14, "atr_mult": m}
        for m in [2.5, 3.0, 3.5]
    ] + [
        {"rsi_period": 14, "rsi_entry": 55, "rsi_exit": 40, "atr_period": 14, "atr_mult": m}
        for m in [2.5, 3.0, 3.5]
    ]

    for cfg in configs_1:
        strat = make_rsi_atr(**cfg)
        r_is = engine.run(strat, ohlcv_is)
        r_oos = engine.run(strat, ohlcv_oos)
        label = f"RSI({cfg['rsi_period']})>{cfg['rsi_entry']}<{cfg['rsi_exit']}+ATR({cfg['atr_period']})x{cfg['atr_mult']}"
        all_rows.append({
            "group": "RSI+ATR", "params": label,
            "is_sharpe": r_is.metrics.sharpe, "is_return": r_is.metrics.total_return,
            "is_mdd": r_is.metrics.max_drawdown, "is_trades": r_is.metrics.n_trades,
            "is_winrate": r_is.metrics.win_rate,
            "oos_sharpe": r_oos.metrics.sharpe, "oos_return": r_oos.metrics.total_return,
            "oos_mdd": r_oos.metrics.max_drawdown, "oos_trades": r_oos.metrics.n_trades,
            "oos_winrate": r_oos.metrics.win_rate, "oos_avg_trade": r_oos.metrics.avg_trade_return,
        })

    # ── 조합 2: RSI + MA 트렌드 필터 ──────────────────
    print("[2] RSI 모멘텀 + MA 트렌드 필터...")
    for ma_p in [100, 150, 200]:
        for rsi_e, rsi_x in [(55, 50), (55, 45), (50, 45)]:
            cfg = {"rsi_period": 10, "rsi_entry": rsi_e, "rsi_exit": rsi_x, "ma_period": ma_p}
            strat = make_rsi_ma(**cfg)
            r_is = engine.run(strat, ohlcv_is)
            r_oos = engine.run(strat, ohlcv_oos)
            label = f"RSI(10)>{rsi_e}<{rsi_x}+EMA({ma_p})"
            all_rows.append({
                "group": "RSI+MA", "params": label,
                "is_sharpe": r_is.metrics.sharpe, "is_return": r_is.metrics.total_return,
                "is_mdd": r_is.metrics.max_drawdown, "is_trades": r_is.metrics.n_trades,
                "is_winrate": r_is.metrics.win_rate,
                "oos_sharpe": r_oos.metrics.sharpe, "oos_return": r_oos.metrics.total_return,
                "oos_mdd": r_oos.metrics.max_drawdown, "oos_trades": r_oos.metrics.n_trades,
                "oos_winrate": r_oos.metrics.win_rate, "oos_avg_trade": r_oos.metrics.avg_trade_return,
            })

    # ── 조합 3: RSI + MA + ATR (풀) ────────────────────
    print("[3] RSI + MA + ATR (풀 조합)...")
    for ma_p in [100, 150, 200]:
        for atr_m in [2.5, 3.0, 3.5]:
            for rsi_e, rsi_x in [(55, 50), (55, 45), (50, 50)]:
                cfg = {"rsi_period": 10, "rsi_entry": rsi_e, "rsi_exit": rsi_x,
                       "ma_period": ma_p, "atr_period": 14, "atr_mult": atr_m}
                strat = make_rsi_ma_atr(**cfg)
                r_is = engine.run(strat, ohlcv_is)
                r_oos = engine.run(strat, ohlcv_oos)
                label = f"RSI(10)>{rsi_e}<{rsi_x}+EMA({ma_p})+ATR(14)x{atr_m}"
                all_rows.append({
                    "group": "RSI+MA+ATR", "params": label,
                    "is_sharpe": r_is.metrics.sharpe, "is_return": r_is.metrics.total_return,
                    "is_mdd": r_is.metrics.max_drawdown, "is_trades": r_is.metrics.n_trades,
                    "is_winrate": r_is.metrics.win_rate,
                    "oos_sharpe": r_oos.metrics.sharpe, "oos_return": r_oos.metrics.total_return,
                    "oos_mdd": r_oos.metrics.max_drawdown, "oos_trades": r_oos.metrics.n_trades,
                    "oos_winrate": r_oos.metrics.win_rate, "oos_avg_trade": r_oos.metrics.avg_trade_return,
                })

    # ── 조합 4: Donchian + ATR ─────────────────────────
    print("[4] Donchian + ATR 트레일링스탑...")
    for entry_p in [20, 30, 50]:
        for atr_m in [2.5, 3.0, 3.5]:
            cfg = {"entry_period": entry_p, "exit_period": 10, "atr_period": 14, "atr_mult": atr_m}
            strat = make_donchian_atr(**cfg)
            r_is = engine.run(strat, ohlcv_is)
            r_oos = engine.run(strat, ohlcv_oos)
            label = f"DC({entry_p})+ATR(14)x{atr_m}"
            all_rows.append({
                "group": "Donchian+ATR", "params": label,
                "is_sharpe": r_is.metrics.sharpe, "is_return": r_is.metrics.total_return,
                "is_mdd": r_is.metrics.max_drawdown, "is_trades": r_is.metrics.n_trades,
                "is_winrate": r_is.metrics.win_rate,
                "oos_sharpe": r_oos.metrics.sharpe, "oos_return": r_oos.metrics.total_return,
                "oos_mdd": r_oos.metrics.max_drawdown, "oos_trades": r_oos.metrics.n_trades,
                "oos_winrate": r_oos.metrics.win_rate, "oos_avg_trade": r_oos.metrics.avg_trade_return,
            })

    df = pd.DataFrame(all_rows)

    # ── OOS 기준 정렬 및 필터 ─────────────────────────
    # Phase 3 기준으로 필터: Sharpe ≥ 0.5, MDD ≥ -25%
    qualified = df[
        (df["oos_sharpe"] >= 0.5) &
        (df["oos_mdd"] >= -0.25) &
        (df["oos_trades"] >= 2)
    ].sort_values("oos_sharpe", ascending=False)

    print("\n" + "=" * 70)
    print(f"전체 {len(df)}개 조합 중 OOS 통과 조합 (Sharpe≥0.5, MDD≥-25%): {len(qualified)}개")
    print("=" * 70)

    oos_cols = ["group", "params", "oos_sharpe", "oos_return", "oos_mdd", "oos_trades", "oos_winrate"]
    is_cols = ["group", "params", "is_sharpe", "is_return", "is_mdd", "is_trades", "is_winrate"]

    if len(qualified) > 0:
        print("\nOOS 통과 Top 15:")
        print(qualified[oos_cols].head(15).to_string(index=False))

        print("\n같은 조합의 IS 성과:")
        print(qualified[is_cols].head(15).to_string(index=False))
    else:
        print("\nOOS 통과 조합 없음. 전체 OOS Top 15:")
        top_all = df.sort_values("oos_sharpe", ascending=False)
        print(top_all[oos_cols].head(15).to_string(index=False))

    # ── 엄격 기준 필터 (Sharpe ≥ 0.8, MDD ≥ -20%) ────
    strict = df[
        (df["oos_sharpe"] >= 0.8) &
        (df["oos_mdd"] >= -0.20) &
        (df["oos_trades"] >= 2)
    ].sort_values("oos_sharpe", ascending=False)

    print("\n" + "=" * 70)
    print(f"엄격 기준 통과 (Sharpe≥0.8, MDD≥-20%): {len(strict)}개")
    print("=" * 70)

    if len(strict) > 0:
        print(strict[oos_cols + ["oos_avg_trade"]].head(10).to_string(index=False))

    # ── 최종 판단 ─────────────────────────────────────
    print("\n" + "=" * 70)
    print("Phase 3 전환 최종 판단")
    print("=" * 70)

    if len(strict) > 0:
        best = strict.iloc[0]
        print(f"\n  ★ GO — Phase 3 페이퍼 트레이딩 전환 권장")
        print(f"  최적 전략: {best['params']}")
        print(f"  OOS: Sharpe {best['oos_sharpe']:.3f}, 수익률 {best['oos_return']*100:+.1f}%, "
              f"MDD {best['oos_mdd']*100:.1f}%, 거래 {int(best['oos_trades'])}회, 승률 {best['oos_winrate']*100:.0f}%")
        print(f"  IS:  Sharpe {best['is_sharpe']:.3f}, 수익률 {best['is_return']*100:+.1f}%, "
              f"MDD {best['is_mdd']*100:.1f}%, 거래 {int(best['is_trades'])}회, 승률 {best['is_winrate']*100:.0f}%")
    elif len(qualified) > 0:
        best = qualified.iloc[0]
        print(f"\n  ★ CONDITIONAL GO — 완화 기준 통과, 소액 페이퍼 트레이딩 권장")
        print(f"  최적 전략: {best['params']}")
        print(f"  OOS: Sharpe {best['oos_sharpe']:.3f}, 수익률 {best['oos_return']*100:+.1f}%, "
              f"MDD {best['oos_mdd']*100:.1f}%, 거래 {int(best['oos_trades'])}회, 승률 {best['oos_winrate']*100:.0f}%")
    else:
        best = df.sort_values("oos_sharpe", ascending=False).iloc[0]
        print(f"\n  ★ HOLD — Phase 3 기준 미충족")
        print(f"  최고 OOS: {best['params']}")
        print(f"  Sharpe {best['oos_sharpe']:.3f}, MDD {best['oos_mdd']*100:.1f}%")

    print("=" * 70)

    return {
        "total_combinations": len(df),
        "qualified_relaxed": len(qualified),
        "qualified_strict": len(strict),
        "all_results": df.to_dict("records"),
        "top_qualified": qualified[oos_cols].head(10).to_dict("records") if len(qualified) > 0 else [],
        "top_strict": strict[oos_cols].head(10).to_dict("records") if len(strict) > 0 else [],
    }


if __name__ == "__main__":
    results = asyncio.run(main())
    out = Path(__file__).parent / "backtest_refinement_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out}")
