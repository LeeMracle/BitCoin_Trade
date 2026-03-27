"""
Phase 2 전략 전환 — 추세추종/모멘텀 전략 종합 백테스트
=====================================================
목적:
  F&G 역추세 실패 후, BTC 모멘텀 특성에 맞는 추세추종 전략 탐색
  4가지 전략 × 파라미터 스윕 → IS/OOS 검증

전략:
  1. Dual MA Crossover (골든크로스/데드크로스)
  2. RSI 모멘텀 (50선 방향)
  3. Donchian Channel Breakout
  4. Multi-Factor (MA추세 + RSI진입 + ATR트레일링스탑)

기간:
  IS: 2020-01-01 ~ 2023-12-31 (강세+하락 모두 포함)
  OOS: 2024-01-01 ~ 2026-03-27
  워밍업: 2019-01-01~ (MA250 계산용)
"""
import sys, asyncio, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
from itertools import product

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv

# ── 기간 설정 ──────────────────────────────────────────
WARMUP_START = "2019-01-01T00:00:00Z"
IS_START     = "2020-01-01T00:00:00Z"
IS_END       = "2023-12-31T00:00:00Z"
OOS_START    = "2024-01-01T00:00:00Z"
OOS_END      = "2026-03-27T00:00:00Z"


# ── 기술적 지표 계산 ──────────────────────────────────────
def calc_sma(series, period):
    return series.rolling(window=period, min_periods=period).mean()

def calc_ema(series, period):
    return series.ewm(span=period, min_periods=period, adjust=False).mean()

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


# ═══════════════════════════════════════════════════════
# 전략 1: Dual MA Crossover
# ═══════════════════════════════════════════════════════
def make_strategy_ma_cross(fast_period, slow_period, ma_type="EMA"):
    def strategy(df):
        close = df["close"]
        calc = calc_ema if ma_type == "EMA" else calc_sma
        fast_ma = calc(close, fast_period)
        slow_ma = calc(close, slow_period)
        signal = pd.Series(0, index=df.index)
        pos = 0
        for i in range(1, len(df)):
            if fast_ma.iloc[i] > slow_ma.iloc[i] and not np.isnan(slow_ma.iloc[i]):
                pos = 1
            elif fast_ma.iloc[i] <= slow_ma.iloc[i] and not np.isnan(slow_ma.iloc[i]):
                pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 전략 2: RSI 모멘텀 (50선 방향)
# ═══════════════════════════════════════════════════════
def make_strategy_rsi_momentum(rsi_period=5, entry_level=50, exit_level=50):
    def strategy(df):
        rsi = calc_rsi(df["close"], rsi_period)
        signal = pd.Series(0, index=df.index)
        pos = 0
        for i in range(1, len(df)):
            if np.isnan(rsi.iloc[i]):
                signal.iloc[i] = pos
                continue
            if pos == 0 and rsi.iloc[i] > entry_level:
                pos = 1
            elif pos == 1 and rsi.iloc[i] < exit_level:
                pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 전략 3: Donchian Channel Breakout
# ═══════════════════════════════════════════════════════
def make_strategy_donchian(entry_period=20, exit_period=10):
    def strategy(df):
        # shift(1): 전일까지의 채널 (look-ahead bias 방지)
        upper = df["high"].shift(1).rolling(window=entry_period, min_periods=entry_period).max()
        lower = df["low"].shift(1).rolling(window=exit_period, min_periods=exit_period).min()
        signal = pd.Series(0, index=df.index)
        pos = 0
        for i in range(1, len(df)):
            if np.isnan(upper.iloc[i]) or np.isnan(lower.iloc[i]):
                signal.iloc[i] = pos
                continue
            if pos == 0 and df["close"].iloc[i] > upper.iloc[i]:
                pos = 1
            elif pos == 1 and df["close"].iloc[i] < lower.iloc[i]:
                pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 전략 4: Multi-Factor (MA추세 + RSI진입 + ATR트레일링스탑)
# ═══════════════════════════════════════════════════════
def make_strategy_multifactor(fast_ma=20, slow_ma=200, rsi_period=5,
                               rsi_entry=50, atr_period=14, atr_mult=3.0):
    def strategy(df):
        close = df["close"]
        ema_fast = calc_ema(close, fast_ma)
        ema_slow = calc_ema(close, slow_ma)
        rsi = calc_rsi(close, rsi_period)
        atr = calc_atr(df, atr_period)

        signal = pd.Series(0, index=df.index)
        pos = 0
        trailing_stop = 0.0
        highest_since_entry = 0.0

        for i in range(1, len(df)):
            if np.isnan(ema_slow.iloc[i]) or np.isnan(rsi.iloc[i]) or np.isnan(atr.iloc[i]):
                signal.iloc[i] = pos
                continue

            c = close.iloc[i]
            trend_up = ema_fast.iloc[i] > ema_slow.iloc[i]

            if pos == 0:
                # 진입: 추세 상승 + RSI 모멘텀 확인
                if trend_up and rsi.iloc[i] > rsi_entry:
                    pos = 1
                    highest_since_entry = c
                    trailing_stop = c - atr.iloc[i] * atr_mult
            elif pos == 1:
                # 트레일링 스탑 갱신
                if c > highest_since_entry:
                    highest_since_entry = c
                    trailing_stop = c - atr.iloc[i] * atr_mult

                # 청산: 트레일링 스탑 OR 추세 반전 + RSI 하락
                if c < trailing_stop:
                    pos = 0
                elif not trend_up and rsi.iloc[i] < rsi_entry:
                    pos = 0

            signal.iloc[i] = pos
        return signal
    return strategy


# ═══════════════════════════════════════════════════════
# 파라미터 스윕
# ═══════════════════════════════════════════════════════
def sweep_ma_cross(engine, ohlcv):
    params = []
    for ma_type in ["EMA", "SMA"]:
        for fast in [10, 15, 20, 50]:
            for slow in [100, 150, 200, 250]:
                if fast >= slow:
                    continue
                params.append((fast, slow, ma_type))

    rows = []
    for fast, slow, ma_type in params:
        strat = make_strategy_ma_cross(fast, slow, ma_type)
        r = engine.run(strat, ohlcv)
        m = r.metrics
        rows.append({
            "strategy": "MA_Cross",
            "params": f"{ma_type}({fast}/{slow})",
            "fast": fast, "slow": slow, "ma_type": ma_type,
            **_m_to_dict(m), "run_id": r.run_id,
        })
    return pd.DataFrame(rows)


def sweep_rsi_momentum(engine, ohlcv):
    rows = []
    for period in [5, 7, 10, 14]:
        for entry in [45, 50, 55]:
            for exit_l in [35, 40, 45, 50]:
                if exit_l > entry:
                    continue
                strat = make_strategy_rsi_momentum(period, entry, exit_l)
                r = engine.run(strat, ohlcv)
                m = r.metrics
                rows.append({
                    "strategy": "RSI_Mom",
                    "params": f"RSI({period}) >{entry}/<{exit_l}",
                    "rsi_period": period, "entry": entry, "exit": exit_l,
                    **_m_to_dict(m), "run_id": r.run_id,
                })
    return pd.DataFrame(rows)


def sweep_donchian(engine, ohlcv):
    rows = []
    for entry_p in [20, 30, 50, 55]:
        for exit_p in [10, 15, 20]:
            if exit_p > entry_p:
                continue
            strat = make_strategy_donchian(entry_p, exit_p)
            r = engine.run(strat, ohlcv)
            m = r.metrics
            rows.append({
                "strategy": "Donchian",
                "params": f"DC({entry_p}/{exit_p})",
                "entry_period": entry_p, "exit_period": exit_p,
                **_m_to_dict(m), "run_id": r.run_id,
            })
    return pd.DataFrame(rows)


def sweep_multifactor(engine, ohlcv):
    rows = []
    configs = [
        (20, 200, 5, 50, 14, 3.0),
        (20, 200, 5, 50, 14, 2.5),
        (20, 200, 5, 50, 14, 3.5),
        (20, 150, 5, 50, 14, 3.0),
        (10, 200, 5, 50, 14, 3.0),
        (20, 200, 7, 50, 14, 3.0),
        (20, 200, 5, 55, 14, 3.0),
        (20, 200, 5, 45, 14, 3.0),
        (50, 200, 5, 50, 14, 3.0),
        (20, 100, 5, 50, 14, 3.0),
        (10, 150, 5, 50, 14, 3.0),
        (15, 200, 7, 50, 14, 2.5),
        (20, 200, 10, 50, 14, 3.0),
        (20, 200, 5, 50, 21, 3.0),
        (10, 100, 5, 50, 14, 3.0),
    ]
    for fast_ma, slow_ma, rsi_p, rsi_e, atr_p, atr_m in configs:
        strat = make_strategy_multifactor(fast_ma, slow_ma, rsi_p, rsi_e, atr_p, atr_m)
        r = engine.run(strat, ohlcv)
        m = r.metrics
        rows.append({
            "strategy": "MultiFactor",
            "params": f"MA({fast_ma}/{slow_ma})+RSI({rsi_p})>{rsi_e}+ATR({atr_p})x{atr_m}",
            "fast_ma": fast_ma, "slow_ma": slow_ma,
            "rsi_period": rsi_p, "rsi_entry": rsi_e,
            "atr_period": atr_p, "atr_mult": atr_m,
            **_m_to_dict(m), "run_id": r.run_id,
        })
    return pd.DataFrame(rows)


def _m_to_dict(m):
    return {
        "total_return": m.total_return, "sharpe": m.sharpe,
        "calmar": m.calmar, "max_dd": m.max_drawdown,
        "n_trades": m.n_trades, "win_rate": m.win_rate,
        "avg_trade": m.avg_trade_return,
    }


def fmt(label, m, bh):
    return (
        f"  {label}\n"
        f"    수익률: {m.total_return*100:+.1f}%  BnH: {bh*100:+.1f}%  초과: {(m.total_return-bh)*100:+.1f}%p\n"
        f"    Sharpe: {m.sharpe:.3f}  Calmar: {m.calmar:.3f}  MDD: {m.max_drawdown*100:.1f}%\n"
        f"    거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%  평균: {m.avg_trade_return*100:.2f}%"
    )


async def main():
    print("=" * 70)
    print("Phase 2 전략 전환 — 추세추종/모멘텀 종합 백테스트")
    print("=" * 70)

    # ── 데이터 수집 ─────────────────────────────────────
    print("\n데이터 수집 중...")
    ohlcv_raw = await fetch_ohlcv("BTC/KRW", "1d", WARMUP_START, OOS_END)
    ohlcv_full = pd.DataFrame(ohlcv_raw)

    from datetime import datetime, timezone
    def iso_ms(iso):
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)

    # 워밍업 포함 IS (MA250 계산용)
    ohlcv_is = ohlcv_full[ohlcv_full["ts"] <= iso_ms(IS_END)].copy().reset_index(drop=True)
    ohlcv_oos_full = ohlcv_full.copy().reset_index(drop=True)  # 워밍업~OOS 전체 (MA 계산용)

    # OOS만 추출 (성과 계산용)
    ohlcv_oos = ohlcv_full[ohlcv_full["ts"] >= iso_ms(OOS_START)].copy().reset_index(drop=True)

    # BnH 기준
    is_start_idx = ohlcv_is[ohlcv_is["ts"] >= iso_ms(IS_START)].index[0]
    bh_is = ohlcv_is["close"].iloc[-1] / ohlcv_is["close"].iloc[is_start_idx] - 1
    bh_oos = ohlcv_oos["close"].iloc[-1] / ohlcv_oos["close"].iloc[0] - 1

    print(f"  OHLCV IS (워밍업 포함): {len(ohlcv_is)}일")
    print(f"  OHLCV OOS: {len(ohlcv_oos)}일")
    print(f"  BnH IS: {bh_is*100:+.1f}%  BnH OOS: {bh_oos*100:+.1f}%")

    engine = BacktestEngine()

    # ── 전략별 IS 스윕 ─────────────────────────────────
    print("\n[1/4] Dual MA Crossover 스윕 중...")
    df_ma = sweep_ma_cross(engine, ohlcv_is)
    print(f"  {len(df_ma)}개 조합 완료")

    print("[2/4] RSI 모멘텀 스윕 중...")
    df_rsi = sweep_rsi_momentum(engine, ohlcv_is)
    print(f"  {len(df_rsi)}개 조합 완료")

    print("[3/4] Donchian Breakout 스윕 중...")
    df_dc = sweep_donchian(engine, ohlcv_is)
    print(f"  {len(df_dc)}개 조합 완료")

    print("[4/4] Multi-Factor 스윕 중...")
    df_mf = sweep_multifactor(engine, ohlcv_is)
    print(f"  {len(df_mf)}개 조합 완료")

    # ── IS Top 5 per strategy ──────────────────────────
    all_results = pd.concat([df_ma, df_rsi, df_dc, df_mf], ignore_index=True)
    all_results = all_results.sort_values("sharpe", ascending=False)

    print("\n" + "=" * 70)
    print("IS 결과 — 전략별 Top 3 (Sharpe 기준)")
    print("=" * 70)

    show_cols = ["strategy", "params", "sharpe", "total_return", "max_dd", "n_trades", "win_rate"]
    for strat_name in ["MA_Cross", "RSI_Mom", "Donchian", "MultiFactor"]:
        subset = all_results[all_results["strategy"] == strat_name]
        print(f"\n  [{strat_name}]")
        print(subset[show_cols].head(3).to_string(index=False))

    # ── 전체 Top 10 ───────────────────────────────────
    print("\n" + "=" * 70)
    print("IS 전체 Top 10 (Sharpe 기준)")
    print("=" * 70)
    print(all_results[show_cols].head(10).to_string(index=False))

    # ── OOS 검증: 각 전략 IS Top 1 ────────────────────
    print("\n" + "=" * 70)
    print("OOS 검증 — 각 전략 IS 최적 파라미터")
    print("=" * 70)

    oos_results = []

    # MA Cross 최적
    best_ma = df_ma.sort_values("sharpe", ascending=False).iloc[0]
    strat = make_strategy_ma_cross(int(best_ma["fast"]), int(best_ma["slow"]), best_ma["ma_type"])
    r_ma_oos = engine.run(strat, ohlcv_oos)
    print(fmt(f"MA Cross {best_ma['params']} (OOS)", r_ma_oos.metrics, bh_oos))
    oos_results.append(("MA_Cross", best_ma["params"], r_ma_oos))

    # RSI 최적
    best_rsi = df_rsi.sort_values("sharpe", ascending=False).iloc[0]
    strat = make_strategy_rsi_momentum(int(best_rsi["rsi_period"]), int(best_rsi["entry"]), int(best_rsi["exit"]))
    r_rsi_oos = engine.run(strat, ohlcv_oos)
    print(fmt(f"RSI Mom {best_rsi['params']} (OOS)", r_rsi_oos.metrics, bh_oos))
    oos_results.append(("RSI_Mom", best_rsi["params"], r_rsi_oos))

    # Donchian 최적
    best_dc = df_dc.sort_values("sharpe", ascending=False).iloc[0]
    strat = make_strategy_donchian(int(best_dc["entry_period"]), int(best_dc["exit_period"]))
    r_dc_oos = engine.run(strat, ohlcv_oos)
    print(fmt(f"Donchian {best_dc['params']} (OOS)", r_dc_oos.metrics, bh_oos))
    oos_results.append(("Donchian", best_dc["params"], r_dc_oos))

    # MultiFactor 최적
    best_mf = df_mf.sort_values("sharpe", ascending=False).iloc[0]
    strat = make_strategy_multifactor(
        int(best_mf["fast_ma"]), int(best_mf["slow_ma"]),
        int(best_mf["rsi_period"]), int(best_mf["rsi_entry"]),
        int(best_mf["atr_period"]), float(best_mf["atr_mult"]),
    )
    r_mf_oos = engine.run(strat, ohlcv_oos)
    print(fmt(f"MultiFactor {best_mf['params']} (OOS)", r_mf_oos.metrics, bh_oos))
    oos_results.append(("MultiFactor", best_mf["params"], r_mf_oos))

    # ── 최종 비교 ─────────────────────────────────────
    print("\n" + "=" * 70)
    print("최종 OOS 비교표")
    print("=" * 70)

    comp = [{"전략": "Buy & Hold", "수익률": f"{bh_oos*100:+.1f}%",
             "Sharpe": "—", "MDD": "—", "거래": "—", "승률": "—"}]
    for name, params, r in oos_results:
        m = r.metrics
        comp.append({
            "전략": f"{name} ({params})",
            "수익률": f"{m.total_return*100:+.1f}%",
            "Sharpe": f"{m.sharpe:.3f}",
            "MDD": f"{m.max_drawdown*100:.1f}%",
            "거래": f"{m.n_trades}",
            "승률": f"{m.win_rate*100:.0f}%",
        })
    print(pd.DataFrame(comp).to_string(index=False))

    # ── Phase 3 판단 ──────────────────────────────────
    print("\n" + "=" * 70)
    print("Phase 3 전환 판단")
    print("=" * 70)

    best_oos_name = ""
    best_oos_sharpe = -999
    best_oos_r = None
    for name, params, r in oos_results:
        if r.metrics.sharpe > best_oos_sharpe:
            best_oos_sharpe = r.metrics.sharpe
            best_oos_name = f"{name} ({params})"
            best_oos_r = r

    m = best_oos_r.metrics
    print(f"\n  최고 OOS 전략: {best_oos_name}")
    print(f"  Sharpe: {m.sharpe:.3f}  MDD: {m.max_drawdown*100:.1f}%  "
          f"거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%")

    strict = (m.sharpe >= 0.8 and m.max_drawdown >= -0.20 and m.win_rate >= 0.5 and m.n_trades >= 2)
    relaxed = (m.sharpe >= 0.5 and m.max_drawdown >= -0.25 and m.win_rate >= 0.4 and m.n_trades >= 2)

    print(f"  엄격 기준 (Sharpe≥0.8, MDD≥-20%): {'PASS' if strict else 'FAIL'}")
    print(f"  완화 기준 (Sharpe≥0.5, MDD≥-25%): {'PASS' if relaxed else 'FAIL'}")

    if strict:
        print("\n  ★ GO — Phase 3 페이퍼 트레이딩 전환 권장")
    elif relaxed:
        print("\n  ★ CONDITIONAL GO — 소액 페이퍼 트레이딩 권장")
    else:
        print("\n  ★ HOLD — 추가 최적화 필요")

    print("=" * 70)

    # ── 결과 저장 ─────────────────────────────────────
    results = {
        "description": "Phase 2 전략 전환 — 추세추종/모멘텀 종합 백테스트",
        "periods": {"is": f"{IS_START[:10]}~{IS_END[:10]}", "oos": f"{OOS_START[:10]}~{OOS_END[:10]}"},
        "bh_is": round(bh_is, 4), "bh_oos": round(bh_oos, 4),
        "is_top10": all_results[show_cols].head(10).to_dict("records"),
        "oos_comparison": comp,
        "best_oos": {"name": best_oos_name, "metrics": _m_to_dict(m)},
        "phase3_strict": strict, "phase3_relaxed": relaxed,
    }
    return results


if __name__ == "__main__":
    results = asyncio.run(main())
    out = Path(__file__).parent / "backtest_trend_strategies_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out}")
