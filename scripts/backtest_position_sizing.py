"""포지션 사이징 규칙 비교 — 포트폴리오 단위 백테스트 (P2).

문제:
    order_amount = available * POSITION_RATIO / slots_empty

    빈 슬롯이 1개면 가용 현금의 95% 전액이 한 종목에 들어간다.
    실측(2026-08-22): OP 진입액 126,436원 = 총자산의 47.3%.
    다른 4종목은 각 10% 내외 — 5분산의 의미가 사라진 상태.

    발생 경로: XRP 손절 직전(아직 보유 중) OP 매수 → slots_empty=1 →
    available*0.95/1. TP 부분익절과 손절로 현금이 쌓인 직후 슬롯이 하나만
    비어 있으면 항상 이 일이 생긴다.

비교 규칙:
    A_current : available * RATIO / slots_empty            (현행)
    B_capped  : min(A, equity / MAX_POSITIONS)             (지분 상한만 추가)
    C_target  : min(available * RATIO, equity / MAX_POSITIONS)  (항상 균등 목표)

    equity = 현금 + 보유 평가액. 상한을 equity 기준으로 잡아야 현금이 쌓여도
    한 종목 비중이 커지지 않는다.

측정:
    최종 수익률 / MDD / 최대 단일종목 비중 / 거래수 / 승률
    수수료 0.05% x2 반영.

사용:
    python scripts/backtest_position_sizing.py [--coins N] [--days N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.market_data.fetcher import fetch_ohlcv  # noqa: E402
from services.execution.scanner import get_krw_market_coins  # noqa: E402
from services.execution.config import (  # noqa: E402
    DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, HARD_STOP_LOSS_PCT,
    MAX_ATR_PCT, TP_LEVELS, VOL_FILTER_MULTIPLIER, MAX_POSITIONS,
    POSITION_RATIO, MIN_ORDER_KRW, REGIME_FILTER_EMA_PERIOD,
    CIRCUIT_BREAKER_INITIAL_CAPITAL,
)

NL = chr(10)
FEE = 0.0005


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def size_order(rule: str, available: float, equity: float, slots_empty: int) -> float:
    base = available * POSITION_RATIO / max(slots_empty, 1)
    cap = equity / MAX_POSITIONS
    if rule == "A_current":
        return base
    if rule == "B_capped":
        return min(base, cap)
    if rule == "C_target":
        return min(available * POSITION_RATIO, cap)
    raise ValueError(rule)


def run_sim(rule: str, panel: dict[str, pd.DataFrame], dates: list,
            select: str = "vol_desc", seed: int = 0,
            tp_first: bool = False) -> dict:
    rng = np.random.default_rng(seed)
    cash = float(CIRCUIT_BREAKER_INITIAL_CAPITAL)
    pos: dict[str, dict] = {}
    equity_curve: list[float] = []
    trades: list[float] = []
    entry_dates: list = []
    max_weight = 0.0

    for d in dates:
        # ── 1) 청산 (손절 → TP 순, 보수적) ──
        for sym in list(pos.keys()):
            df = panel[sym]
            if d not in df.index:
                continue
            row = df.loc[d]
            p = pos[sym]
            hi, lo = float(row["high"]), float(row["low"])
            entry, atr0 = p["entry"], p["atr"]
            hard = entry * (1 - HARD_STOP_LOSS_PCT)

            # 같은 날 손절선과 TP가 모두 닿았을 때의 순서 가정.
            # 일봉만으로는 장중 순서를 알 수 없다. 보수(손절 우선)와
            # 낙관(TP 우선)의 두 극단으로 결과를 브래킷한다.
            if not tp_first and lo <= p["trail"]:
                proceeds = p["qty"] * p["trail"] * (1 - FEE)
                cash += proceeds
                p["proceeds"] += proceeds
                trades.append(p["proceeds"] / p["cost"] - 1)
                del pos[sym]
                continue
            for k, tp in enumerate(TP_LEVELS):
                if k in p["tp_done"]:
                    continue
                trig = entry * (1 + tp["trigger_pct"])
                if hi >= trig:
                    sell_qty = (p["qty"] * tp["sell_ratio"]
                                if k < len(TP_LEVELS) - 1 else p["qty"])
                    proceeds = sell_qty * trig * (1 - FEE)
                    cash += proceeds
                    p["proceeds"] += proceeds
                    p["qty"] -= sell_qty
                    p["tp_done"].add(k)
            if p["qty"] <= 1e-12:
                trades.append(p["proceeds"] / p["cost"] - 1)
                del pos[sym]
                continue
            if tp_first and lo <= p["trail"]:
                proceeds = p["qty"] * p["trail"] * (1 - FEE)
                cash += proceeds
                p["proceeds"] += proceeds
                trades.append(p["proceeds"] / p["cost"] - 1)
                del pos[sym]
                continue
            if hi > p["highest"]:
                p["highest"] = hi
                p["trail"] = max(p["trail"], hi - atr0 * ATR_MULTIPLIER, hard)

        # ── 2) 평가액 산출 ──
        held_val = 0.0
        for sym, p in pos.items():
            df = panel[sym]
            px = float(df.loc[d, "close"]) if d in df.index else p["entry"]
            held_val += p["qty"] * px
        equity = cash + held_val
        equity_curve.append(equity)
        if held_val > 0:
            for sym, p in pos.items():
                df = panel[sym]
                px = float(df.loc[d, "close"]) if d in df.index else p["entry"]
                max_weight = max(max_weight, p["qty"] * px / equity if equity > 0 else 0)

        # ── 3) 진입 ──
        if len(pos) >= MAX_POSITIONS:
            continue
        cands = []
        for sym, df in panel.items():
            if sym in pos or d not in df.index:
                continue
            row = df.loc[d]
            if not bool(row["signal"]):
                continue
            cands.append((float(row["volume"]) * float(row["close"]), sym, row))
        # 후보 선택 순서 — 실거래는 웹소켓 틱 도착순(사실상 무작위)이라
        # 백테스트의 선택 규칙이 결과를 좌우하면 그 자체가 편향이다. 민감도 확인용.
        if select == "vol_desc":
            cands.sort(reverse=True, key=lambda x: x[0])
        elif select == "vol_asc":
            cands.sort(key=lambda x: x[0])
        elif select == "symbol":
            cands.sort(key=lambda x: x[1])
        elif select == "random":
            # 실거래는 웹소켓 틱 도착순으로 선착순 매수 — 사실상 무작위.
            # 결정적 정렬(거래대금 등)은 백테스트에만 존재하는 정보 우위다.
            rng.shuffle(cands)

        for _, sym, row in cands:
            if len(pos) >= MAX_POSITIONS:
                break
            slots_empty = MAX_POSITIONS - len(pos)
            amt = size_order(rule, cash, equity, slots_empty)
            amt = min(amt, cash * POSITION_RATIO)
            if amt < MIN_ORDER_KRW:
                continue
            entry = float(row["entry_px"])
            atr0 = float(row["atr"])
            qty = amt * (1 - FEE) / entry
            cash -= amt
            pos[sym] = {
                "entry": entry, "qty": qty, "atr": atr0, "highest": entry,
                "trail": max(entry - atr0 * ATR_MULTIPLIER, entry * (1 - HARD_STOP_LOSS_PCT)),
                "tp_done": set(),
                # 거래 수익률은 실제 원가/회수로 계산해야 정확하다.
                # TP 분할매도가 있으면 "마지막 청산가/진입가"는 틀린 값이 된다.
                "cost": amt, "proceeds": 0.0,
            }
            entry_dates.append(d)

    eq_series = pd.Series(equity_curve, index=pd.DatetimeIndex(dates[:len(equity_curve)]))
    final = equity_curve[-1] if equity_curve else float(CIRCUIT_BREAKER_INITIAL_CAPITAL)
    eq = pd.Series(equity_curve)
    mdd = ((eq - eq.cummax()) / eq.cummax()).min() * 100 if len(eq) else 0.0
    wins = [t for t in trades if t > 0]
    avg_trade = (sum(trades) / len(trades)) if trades else 0.0
    return {
        "rule": rule,
        "final": final,
        "ret": final / CIRCUIT_BREAKER_INITIAL_CAPITAL - 1,
        "mdd": mdd,
        "trades": len(trades),
        "winrate": len(wins) / len(trades) * 100 if trades else 0.0,
        "max_weight": max_weight * 100,
        "avg_trade": avg_trade * 100,
        "equity": eq_series,
        "entry_dates": entry_dates,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=120)
    ap.add_argument("--days", type=int, default=700)
    ap.add_argument("--select", default="vol_desc",
                    choices=["vol_desc", "vol_asc", "symbol"])
    ap.add_argument("--sweep-select", action="store_true")
    ap.add_argument("--bracket", action="store_true",
                    help="손절우선/TP우선 두 극단으로 결과 브래킷")
    ap.add_argument("--random-runs", type=int, default=0,
                    help="무작위 선택 N회 반복 — 선택편향 제거한 기대값 추정")
    args = ap.parse_args()

    now = datetime.now(tz=timezone.utc)
    start = (now - timedelta(days=args.days + 260)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT00:00:00Z")

    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", start, end, use_cache=True))
    btc["ema"] = btc["close"].ewm(span=REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc["ema"]))

    coins = get_krw_market_coins()[: args.coins]
    panel: dict[str, pd.DataFrame] = {}
    for idx, c in enumerate(coins, 1):
        sym = c["symbol"]
        try:
            df = pd.DataFrame(await fetch_ohlcv(sym, "1d", start, end, use_cache=True))
            if len(df) < DONCHIAN_PERIOD + ATR_PERIOD + 30:
                continue
            df = df.tail(args.days).reset_index(drop=True)
            upper = df["high"].shift(1).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).max()
            df["atr"] = _atr(df)
            vsma = df["volume"].rolling(5).mean()
            df["entry_px"] = np.maximum(upper, df["open"])
            df["signal"] = (
                (df["high"] > upper)
                & (df["atr"] / df["open"] <= MAX_ATR_PCT)
                & (df["volume"].shift(1) >= vsma.shift(1) * VOL_FILTER_MULTIPLIER)
                & (df["ts"].map(lambda t: bool(regime.get(t, False))))
            ).fillna(False)
            df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
            panel[sym] = df
        except Exception:
            continue
        if idx % 30 == 0:
            print(f"  로딩 {idx}/{len(coins)}", flush=True)

    if not panel:
        print("데이터 없음")
        return 1

    dates = sorted({d for df in panel.values() for d in df.index})
    print(f"\n포트폴리오 시뮬레이션 — {len(panel)}종목 / {len(dates)}일 / "
          f"초기자본 {CIRCUIT_BREAKER_INITIAL_CAPITAL:,}원 / 슬롯 {MAX_POSITIONS}")
    print("=" * 78)
    print(f"{'규칙':<12}{'최종자산':>13}{'수익률':>10}{'MDD':>9}{'거래수':>8}{'승률':>8}{'평균/건':>9}{'최대비중':>10}")

    if args.sweep_select:
        print("\n[선택순서 민감도] 규칙 B_capped 고정")
        for sel in ("vol_desc", "vol_asc", "symbol"):
            r = run_sim("B_capped", panel, dates, sel)
            print(f"  {sel:<10} 수익률 {r['ret']*100:>7.1f}% | MDD {r['mdd']:>6.1f}% | "
                  f"거래 {r['trades']:>4} | 승률 {r['winrate']:>5.1f}%")
        print()

    results = []
    for rule in ("A_current", "B_capped", "C_target"):
        r = run_sim(rule, panel, dates, args.select)
        results.append(r)
        print(f"{r['rule']:<12}{r['final']:>13,.0f}{r['ret']*100:>9.1f}%{r['mdd']:>8.1f}%"
              f"{r['trades']:>8}{r['winrate']:>7.1f}%{r['avg_trade']:>8.2f}%{r['max_weight']:>9.1f}%")

    # 연도별 진단 — 손익이 특정 구간에 몰렸는지 확인
    ref = next(r for r in results if r["rule"] == "B_capped")
    eq = ref["equity"]
    print("\n" + "-" * 78)
    print("연도별 (B_capped 기준) — 손익이 어디서 났는가")
    print("-" * 78)
    print(f"{'연도':<8}{'시작':>12}{'종료':>12}{'수익률':>10}{'진입건수':>10}")
    ed = pd.Series(1, index=pd.DatetimeIndex(ref["entry_dates"])) if ref["entry_dates"] else pd.Series(dtype=int)
    for y, g in eq.groupby(eq.index.year):
        n = int(ed[ed.index.year == y].sum()) if len(ed) else 0
        print(f"{y:<8}{g.iloc[0]:>12,.0f}{g.iloc[-1]:>12,.0f}"
              f"{(g.iloc[-1]/g.iloc[0]-1)*100:>9.1f}%{n:>10}")

    if args.random_runs:
        print("\n" + "-" * 78)
        print(f"무작위 선택 {args.random_runs}회 — 선택편향 제거 (실거래 틱 도착순 근사)")
        print("-" * 78)
        print(f"{'규칙':<12}{'수익률 평균':>13}{'표준편차':>11}{'최악':>10}{'최선':>10}{'MDD 평균':>11}")
        for rule in ("A_current", "B_capped"):
            rr = [run_sim(rule, panel, dates, "random", s) for s in range(args.random_runs)]
            rets = np.array([x["ret"] for x in rr]) * 100
            mdds = np.array([x["mdd"] for x in rr])
            print(f"{rule:<12}{rets.mean():>12.1f}%{rets.std():>10.1f}%"
                  f"{rets.min():>9.1f}%{rets.max():>9.1f}%{mdds.mean():>10.1f}%")

    if args.bracket:
        print(NL + "-" * 78)
        print("장중 순서 가정 민감도 (B_capped, 무작위 선택 20회)")
        print("-" * 78)
        for label, tf in (("보수: 손절 우선", False), ("낙관: TP 우선", True)):
            rr = [run_sim("B_capped", panel, dates, "random", s, tf) for s in range(20)]
            rets = np.array([x["ret"] for x in rr]) * 100
            wins = np.array([x["winrate"] for x in rr])
            print(f"  {label:<16} 수익률 {rets.mean():>7.1f}% (최악 {rets.min():>6.1f}% / "
                  f"최선 {rets.max():>6.1f}%) | 승률 {wins.mean():>5.1f}%")

    a = next(r for r in results if r["rule"] == "A_current")
    print("\n" + "-" * 78)
    print("현행(A) 대비")
    print("-" * 78)
    for r in results[1:]:
        print(f"  {r['rule']:<10} 수익률 {(r['ret']-a['ret'])*100:+.1f}%p | "
              f"MDD {r['mdd']-a['mdd']:+.1f}%p | "
              f"최대비중 {r['max_weight']-a['max_weight']:+.1f}%p | "
              f"거래 {r['trades']-a['trades']:+d}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
