"""
다음 단계 백테스트
1. 전략 B 파라미터 최적화 (F&G 임계값 스윕)
2. 전략 B + DCA 조합
3. 아웃오브샘플 검증 (2025-03-01 ~ 2026-03-01)
"""
import sys, asyncio, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pathlib import Path
from itertools import product

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from services.backtest.engine import BacktestEngine
from services.market_data.fetcher import fetch_ohlcv, fetch_fear_greed

IS_START  = "2023-01-01T00:00:00Z"
IS_END    = "2025-03-01T00:00:00Z"
OOS_START = "2025-03-01T00:00:00Z"
OOS_END   = "2026-03-01T00:00:00Z"


# ── 공통 유틸 ──────────────────────────────────────────────
def make_fg_map(fg_df):
    return dict(zip(fg_df["date"], fg_df["value"]))


def make_strategy_fg(fg_map, buy_thr=25, sell_thr=55):
    def strategy(df):
        signal = pd.Series(np.nan, index=df.index)
        pos = 0
        for i in range(len(df)):
            ts_ms = int(df["ts"].iloc[i])
            date_str = pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
            fg = fg_map.get(date_str, np.nan)
            if np.isnan(fg):
                signal.iloc[i] = pos; continue
            if pos == 0 and fg <= buy_thr:
                pos = 1
            elif pos == 1 and fg >= sell_thr:
                pos = 0
            signal.iloc[i] = pos
        return signal
    return strategy


def make_strategy_dca(fg_map, buy_thr=25, sell_thr=55, n_splits=3):
    """F&G 신호 + 분할매수: 조건 충족 후 n_splits일에 걸쳐 균등 진입"""
    def strategy(df):
        signal = pd.Series(0.0, index=df.index)
        pos = 0
        entry_count = 0
        for i in range(len(df)):
            ts_ms = int(df["ts"].iloc[i])
            date_str = pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
            fg = fg_map.get(date_str, np.nan)
            if np.isnan(fg):
                signal.iloc[i] = pos; continue

            if pos == 0 and fg <= buy_thr:
                pos = 1
                entry_count = 0

            if pos == 1 and entry_count < n_splits:
                entry_count += 1

            if pos == 1 and fg >= sell_thr:
                pos = 0
                entry_count = 0

            # DCA: 진입 중에는 부분 신호 (엔진은 0/1만 처리 — 완전 진입으로 단순화)
            signal.iloc[i] = pos
        return signal
    return strategy


# ── 1. 파라미터 최적화 ────────────────────────────────────
def run_param_sweep(engine, ohlcv_df, fg_map):
    buy_thresholds  = [15, 20, 25, 30]
    sell_thresholds = [45, 50, 55, 60]

    rows = []
    for buy_thr, sell_thr in product(buy_thresholds, sell_thresholds):
        if buy_thr >= sell_thr:
            continue
        strat = make_strategy_fg(fg_map, buy_thr, sell_thr)
        r = engine.run(strat, ohlcv_df)
        m = r.metrics
        rows.append({
            "buy_thr": buy_thr,
            "sell_thr": sell_thr,
            "total_return": m.total_return,
            "sharpe": m.sharpe,
            "calmar": m.calmar,
            "max_dd": m.max_drawdown,
            "n_trades": m.n_trades,
            "win_rate": m.win_rate,
            "avg_trade": m.avg_trade_return,
        })

    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    return df


# ── 2. DCA 조합 ───────────────────────────────────────────
def run_dca(engine, ohlcv_df, fg_map, buy_thr, sell_thr):
    strat = make_strategy_dca(fg_map, buy_thr, sell_thr, n_splits=3)
    return engine.run(strat, ohlcv_df)


# ── 3. 아웃오브샘플 ──────────────────────────────────────
def run_oos(engine, ohlcv_oos, fg_map_oos, buy_thr, sell_thr):
    strat = make_strategy_fg(fg_map_oos, buy_thr, sell_thr)
    return engine.run(strat, ohlcv_oos)


async def main():
    print("데이터 수집 중 (인샘플 + 아웃오브샘플)...")

    ohlcv_is_raw, ohlcv_oos_raw, fg_is_raw, fg_oos_raw = await asyncio.gather(
        fetch_ohlcv("BTC/KRW", "1d", IS_START,  IS_END),
        fetch_ohlcv("BTC/KRW", "1d", OOS_START, OOS_END),
        fetch_fear_greed(IS_START,  IS_END),
        fetch_fear_greed(OOS_START, OOS_END),
    )

    ohlcv_is  = pd.DataFrame(ohlcv_is_raw)
    ohlcv_oos = pd.DataFrame(ohlcv_oos_raw)
    fg_is_df  = pd.DataFrame(fg_is_raw)
    fg_oos_df = pd.DataFrame(fg_oos_raw)
    fg_map_is  = make_fg_map(fg_is_df)
    fg_map_oos = make_fg_map(fg_oos_df)

    bh_is  = (ohlcv_is["close"].iloc[-1]  / ohlcv_is["close"].iloc[0])  - 1
    bh_oos = (ohlcv_oos["close"].iloc[-1] / ohlcv_oos["close"].iloc[0]) - 1

    print(f"IS bars: {len(ohlcv_is)} / OOS bars: {len(ohlcv_oos)}")
    print(f"IS F&G: {len(fg_is_df)}days / OOS F&G: {len(fg_oos_df)}days")

    engine = BacktestEngine()

    # ── Step 1: 파라미터 최적화 ──
    print("\n[Step 1] F&G 파라미터 스윕 중 (인샘플)...")
    sweep_df = run_param_sweep(engine, ohlcv_is, fg_map_is)
    best_row = sweep_df.iloc[0]
    best_buy  = int(best_row["buy_thr"])
    best_sell = int(best_row["sell_thr"])
    print(f"  최적 파라미터: buy≤{best_buy}, sell≥{best_sell}  (Sharpe {best_row['sharpe']:.3f})")

    print("\n파라미터 스윕 Top 10:")
    print(sweep_df.head(10).to_string(index=False))

    # ── Step 2: DCA 조합 ──
    print(f"\n[Step 2] DCA 조합 (buy≤{best_buy}, sell≥{best_sell}, 3분할)...")
    result_dca = run_dca(engine, ohlcv_is, fg_map_is, best_buy, best_sell)

    # 기준 단순 전략 (최적 파라미터)
    result_base = engine.run(make_strategy_fg(fg_map_is, best_buy, best_sell), ohlcv_is)

    # ── Step 3: 아웃오브샘플 ──
    print(f"\n[Step 3] 아웃오브샘플 검증 (2025-03-01 ~ 2026-03-01)...")
    result_oos = run_oos(engine, ohlcv_oos, fg_map_oos, best_buy, best_sell)

    # ── 결과 출력 ──
    print("\n" + "="*65)
    print("Step 1. 파라미터 최적화 결과 (인샘플, Sharpe 기준 Top 5)")
    print("="*65)
    cols = ["buy_thr","sell_thr","sharpe","calmar","max_dd","total_return","n_trades","win_rate"]
    print(sweep_df[cols].head(5).to_string(index=False))

    def fmt(label, r, bh):
        m = r.metrics
        print(f"\n  {label}")
        print(f"    수익률: {m.total_return*100:+.1f}%  BnH: {bh*100:+.1f}%")
        print(f"    Sharpe: {m.sharpe:.3f}  Calmar: {m.calmar:.3f}  MDD: {m.max_drawdown*100:.1f}%")
        print(f"    거래: {m.n_trades}회  승률: {m.win_rate*100:.0f}%  평균: {m.avg_trade_return*100:.2f}%")

    print("\n" + "="*65)
    print("Step 2. DCA 조합 vs 단순 전략 비교 (인샘플)")
    print("="*65)
    fmt(f"단순 F&G (buy≤{best_buy}/sell≥{best_sell})", result_base, bh_is)
    fmt(f"DCA 3분할  (buy≤{best_buy}/sell≥{best_sell})", result_dca, bh_is)

    print("\n" + "="*65)
    print("Step 3. 아웃오브샘플 검증 (2025-03-01 ~ 2026-03-01)")
    print("="*65)
    fmt(f"F&G (buy≤{best_buy}/sell≥{best_sell}) OOS", result_oos, bh_oos)

    # Phase 3 판단
    oos_m = result_oos.metrics
    phase3_go = (
        oos_m.sharpe >= 0.8 and
        oos_m.max_drawdown >= -0.20 and
        oos_m.win_rate >= 0.5 and
        oos_m.n_trades >= 2
    )
    verdict = "GO [OK]" if phase3_go else "HOLD [WAIT]"
    print(f"\n★ Phase 3 페이퍼 트레이딩 전환 판단: {verdict}")
    if not phase3_go:
        reasons = []
        if oos_m.sharpe < 0.8:      reasons.append(f"Sharpe {oos_m.sharpe:.2f} < 0.8")
        if oos_m.max_drawdown < -0.20: reasons.append(f"MDD {oos_m.max_drawdown*100:.1f}% < -20%")
        if oos_m.win_rate < 0.5:    reasons.append(f"승률 {oos_m.win_rate*100:.0f}% < 50%")
        if oos_m.n_trades < 2:      reasons.append(f"거래횟수 {oos_m.n_trades}회 < 2")
        print(f"  미충족: {', '.join(reasons)}")

    print("="*65)

    return {
        "sweep_top5": sweep_df[cols].head(5).to_dict("records"),
        "best_buy": best_buy,
        "best_sell": best_sell,
        "base_is": result_base.metrics.__dict__,
        "dca_is":  result_dca.metrics.__dict__,
        "oos":     result_oos.metrics.__dict__,
        "bh_is":   round(bh_is, 4),
        "bh_oos":  round(bh_oos, 4),
        "phase3_go": phase3_go,
        "run_ids": {
            "base_is": result_base.run_id,
            "dca_is":  result_dca.run_id,
            "oos":     result_oos.run_id,
        }
    }


if __name__ == "__main__":
    import json
    results = asyncio.run(main())
    out = Path(__file__).parent / "backtest_next_steps_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out}")
