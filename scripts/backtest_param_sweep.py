"""전략 파라미터 단일요인 탐색 — 포트폴리오 단위 (plan 20260823_1).

배경:
    ADR 20260823-2에서 현재 config가 포트폴리오 단위로 손실(무작위 20회 전부,
    평균 -16.4%)임을 확인했다. 무엇을 바꾸면 달라지는지 축별로 본다.

방법론 (과최적화 방어):
    - **단일요인만** 변경한다. 격자 탐색은 조합 폭발로 우연한 승자를 만든다.
    - 진입 후보 선택 순서가 결과를 14%p 좌우하므로 **무작위 선택 N회 평균**으로만 판단.
    - **IS/OOS 분리** — 전반부에서 좋았던 값이 후반부에도 유지되는지 본다.
    - 시도한 조합 수를 출력한다 (다중비교 문제를 숨기지 않기 위해).
    - 절대 수익률은 생존편향(상장폐지 코인 누락)으로 낙관 쪽이다.
      **상대 비교에만** 쓴다.

사용:
    python scripts/backtest_param_sweep.py --runs 12
    python scripts/backtest_param_sweep.py --runs 12 --axis slots
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
from services.execution import config as C  # noqa: E402

FEE = 0.0005
INIT = float(C.CIRCUIT_BREAKER_INITIAL_CAPITAL)


class P:
    """탐색 대상 파라미터 묶음. 기본값 = 현재 운영 config."""

    def __init__(self, **kw):
        self.dc = kw.get("dc", C.DONCHIAN_PERIOD)
        self.slots = kw.get("slots", C.MAX_POSITIONS)
        self.tp = kw.get("tp", C.TP_LEVELS)
        self.hard = kw.get("hard", C.HARD_STOP_LOSS_PCT)
        self.atr_mult = kw.get("atr_mult", C.ATR_MULTIPLIER)
        self.max_atr = kw.get("max_atr", C.MAX_ATR_PCT)
        self.vol_mult = kw.get("vol_mult", C.VOL_FILTER_MULTIPLIER)
        self.weight = kw.get("weight", C.MAX_POSITION_WEIGHT)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def build_panel(raw: dict[str, pd.DataFrame], regime: dict, p: P) -> dict[str, pd.DataFrame]:
    """파라미터에 맞춰 신호를 재계산. DC/ATR/VOL 필터가 바뀌면 신호 집합이 달라진다."""
    panel = {}
    for sym, src in raw.items():
        df = src.copy()
        upper = df["high"].shift(1).rolling(p.dc, min_periods=p.dc).max()
        df["atr"] = _atr(df, C.ATR_PERIOD)
        vsma = df["volume"].rolling(5).mean()
        df["entry_px"] = np.maximum(upper, df["open"])
        df["signal"] = (
            (df["high"] > upper)
            & (df["atr"] / df["open"] <= p.max_atr)
            & (df["volume"].shift(1) >= vsma.shift(1) * p.vol_mult)
            & (df["ts"].map(lambda t: bool(regime.get(t, False))))
        ).fillna(False)
        panel[sym] = df
    return panel


def run_sim(panel: dict[str, pd.DataFrame], dates: list, p: P, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    cash, pos = INIT, {}
    curve, trades = [], []

    for d in dates:
        for sym in list(pos.keys()):
            df = panel[sym]
            if d not in df.index:
                continue
            row = df.loc[d]
            q = pos[sym]
            hi, lo = float(row["high"]), float(row["low"])
            entry = q["entry"]
            hard = entry * (1 - p.hard)
            if lo <= q["trail"]:                       # 손절 우선 (보수)
                q["proceeds"] += q["qty"] * q["trail"] * (1 - FEE)
                cash += q["qty"] * q["trail"] * (1 - FEE)
                trades.append(q["proceeds"] / q["cost"] - 1)
                del pos[sym]
                continue
            for k, tp in enumerate(p.tp):
                if k in q["tp_done"]:
                    continue
                trig = entry * (1 + tp["trigger_pct"])
                if hi >= trig:
                    sq = q["qty"] * tp["sell_ratio"] if k < len(p.tp) - 1 else q["qty"]
                    pr = sq * trig * (1 - FEE)
                    cash += pr
                    q["proceeds"] += pr
                    q["qty"] -= sq
                    q["tp_done"].add(k)
            if q["qty"] <= 1e-12:
                trades.append(q["proceeds"] / q["cost"] - 1)
                del pos[sym]
                continue
            if hi > q["highest"]:
                q["highest"] = hi
                q["trail"] = max(q["trail"], hi - q["atr"] * p.atr_mult, hard)

        held = 0.0
        for sym, q in pos.items():
            df = panel[sym]
            held += q["qty"] * (float(df.loc[d, "close"]) if d in df.index else q["entry"])
        equity = cash + held
        curve.append(equity)

        if len(pos) >= p.slots:
            continue
        cands = [(sym, panel[sym].loc[d]) for sym in panel
                 if sym not in pos and d in panel[sym].index
                 and bool(panel[sym].loc[d, "signal"])]
        rng.shuffle(cands)
        for sym, row in cands:
            if len(pos) >= p.slots:
                break
            amt = cash * C.POSITION_RATIO / max(p.slots - len(pos), 1)
            amt = min(amt, equity * p.weight, cash * C.POSITION_RATIO)
            if amt < C.MIN_ORDER_KRW:
                continue
            e = float(row["entry_px"])
            a = float(row["atr"])
            cash -= amt
            pos[sym] = {"entry": e, "qty": amt * (1 - FEE) / e, "atr": a, "highest": e,
                        "trail": max(e - a * p.atr_mult, e * (1 - p.hard)),
                        "tp_done": set(), "cost": amt, "proceeds": 0.0}

    eq = pd.Series(curve)
    final = curve[-1] if curve else INIT
    mdd = ((eq - eq.cummax()) / eq.cummax()).min() * 100 if len(eq) else 0.0
    return {"ret": final / INIT - 1, "mdd": mdd, "n": len(trades),
            "wr": (sum(1 for t in trades if t > 0) / len(trades) * 100) if trades else 0.0}


def evaluate(panel: dict, dates: list, p: P, runs: int) -> dict:
    rs = [run_sim(panel, dates, p, s) for s in range(runs)]
    r = np.array([x["ret"] for x in rs]) * 100
    return {"mean": r.mean(), "std": r.std(), "min": r.min(), "max": r.max(),
            "mdd": np.mean([x["mdd"] for x in rs]),
            "n": np.mean([x["n"] for x in rs]),
            "wr": np.mean([x["wr"] for x in rs])}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=120)
    ap.add_argument("--days", type=int, default=700)
    ap.add_argument("--runs", type=int, default=12)
    ap.add_argument("--axis", default="all",
                    choices=["all", "slots", "tp", "stop", "dc", "combo"])
    args = ap.parse_args()

    now = datetime.now(tz=timezone.utc)
    s = (now - timedelta(days=args.days + 260)).strftime("%Y-%m-%dT00:00:00Z")
    e = now.strftime("%Y-%m-%dT00:00:00Z")

    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", s, e, use_cache=True))
    btc["ema"] = btc["close"].ewm(span=C.REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc["ema"]))

    raw = {}
    for i, c in enumerate(get_krw_market_coins()[: args.coins], 1):
        sym = c["symbol"]
        try:
            df = pd.DataFrame(await fetch_ohlcv(sym, "1d", s, e, use_cache=True))
            if len(df) < 60:
                continue
            df = df.tail(args.days).reset_index(drop=True)
            df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
            raw[sym] = df
        except Exception:
            continue
        if i % 40 == 0:
            print(f"  로딩 {i}/{args.coins}", flush=True)

    all_dates = sorted({d for df in raw.values() for d in df.index})

    # ── IS/OOS 분리는 "거래 가능일" 기준 ──────────────────────────────
    # 달력 60/40으로 나누면 OOS가 통째로 레짐 폐쇄 구간(2026 Q1/Q2 통과율 0%)에
    # 들어가 거래 3~16건뿐인 무의미한 표본이 된다 (2026-08-23 1차 시도 실측).
    # 레짐이 열려 있던 날만 세어 그 60% 지점을 경계로 삼아야 양쪽에 표본이 생긴다.
    open_set = {d for d in all_dates
                if bool(regime.get(int(d.timestamp() * 1000), False))}
    open_dates = sorted(open_set)
    if len(open_dates) < 20:
        print("거래 가능일이 너무 적어 IS/OOS 분리 불가")
        return 1
    boundary = open_dates[int(len(open_dates) * 0.6)]
    is_dates = [d for d in all_dates if d <= boundary]
    oos_dates = [d for d in all_dates if d > boundary]
    is_open = sum(1 for d in is_dates if d in open_set)
    print(f"\n종목 {len(raw)} / 전체 {len(all_dates)}일 (거래가능 {len(open_dates)}일)")
    print(f"  IS  {len(is_dates):>4}일 (거래가능 {is_open:>3}일) ~ {boundary.date()}")
    print(f"  OOS {len(oos_dates):>4}일 (거래가능 {len(open_dates)-is_open:>3}일)")
    print(f"무작위 선택 {args.runs}회 평균. 절대수익은 생존편향으로 낙관 — 상대비교용.\n")

    axes: dict[str, list[tuple[str, dict]]] = {
        "slots": [(f"슬롯 {n}", {"slots": n}) for n in (3, 5, 7, 10, 15)],
        "tp": [
            ("TP 현행 5/12%", {}),
            ("TP 없음(트레일만)", {"tp": [{"trigger_pct": 9.99, "sell_ratio": 1.0}]}),
            ("TP1 10%/50%", {"tp": [{"trigger_pct": 0.10, "sell_ratio": 0.5},
                                    {"trigger_pct": 0.25, "sell_ratio": 0.5}]}),
            ("TP1 15%/50%", {"tp": [{"trigger_pct": 0.15, "sell_ratio": 0.5},
                                    {"trigger_pct": 0.30, "sell_ratio": 0.5}]}),
            ("TP1 5%/30%", {"tp": [{"trigger_pct": 0.05, "sell_ratio": 0.3},
                                   {"trigger_pct": 0.12, "sell_ratio": 0.5}]}),
        ],
        "stop": [(f"손절 {int(h*100)}% / ATRx{m}", {"hard": h, "atr_mult": m})
                 for h, m in ((0.10, 3.0), (0.07, 3.0), (0.15, 3.0),
                              (0.20, 3.0), (0.10, 2.0), (0.10, 4.0))],
        "dc": [(f"DC {n}", {"dc": n}) for n in (8, 12, 20, 30, 50)],
        # 결합 검증은 **IS·OOS 방향이 일관된 축만** 묶는다.
        # 단일요인 결과: 슬롯↑ 일관 개선 / TP 늦추기 일관 개선 /
        #               손절·ATR 신호 없음 / DC는 IS와 OOS가 반대(과최적화 함정) →
        # 뒤 두 축은 결합에서 제외한다. 격자 탐색이 아니라 근거 있는 2축 조합이다.
        "combo": [
            ("현행 (슬롯5 TP5/12)", {}),
            ("슬롯10 + TP없음", {"slots": 10, "tp": [{"trigger_pct": 9.99, "sell_ratio": 1.0}]}),
            ("슬롯10 + TP15%", {"slots": 10, "tp": [{"trigger_pct": 0.15, "sell_ratio": 0.5},
                                                     {"trigger_pct": 0.30, "sell_ratio": 0.5}]}),
            ("슬롯15 + TP없음", {"slots": 15, "tp": [{"trigger_pct": 9.99, "sell_ratio": 1.0}]}),
            ("슬롯15 + TP15%", {"slots": 15, "tp": [{"trigger_pct": 0.15, "sell_ratio": 0.5},
                                                     {"trigger_pct": 0.30, "sell_ratio": 0.5}]}),
        ],
    }
    todo = list(axes) if args.axis == "all" else [args.axis]

    tried = 0
    survivors: list[tuple[str, dict]] = []
    base = P()
    base_panel = build_panel(raw, regime, base)

    for axis in todo:
        print("=" * 84)
        print(f"[축] {axis}")
        print("=" * 84)
        print(f"{'설정':<22}{'IS 평균':>10}{'OOS 평균':>10}{'OOS 표준편차':>13}"
              f"{'OOS MDD':>10}{'거래':>7}{'승률':>7}")
        for label, kw in axes[axis]:
            tried += 1
            p = P(**kw)
            panel = base_panel if (p.dc == base.dc and p.max_atr == base.max_atr
                                   and p.vol_mult == base.vol_mult) else build_panel(raw, regime, p)
            r_is = evaluate(panel, is_dates, p, args.runs)
            r_oos = evaluate(panel, oos_dates, p, args.runs)
            print(f"{label:<22}{r_is['mean']:>9.1f}%{r_oos['mean']:>9.1f}%"
                  f"{r_oos['std']:>12.1f}%{r_oos['mdd']:>9.1f}%"
                  f"{r_oos['n']:>7.0f}{r_oos['wr']:>6.1f}%")
            # IS·OOS 양쪽 모두 기준선보다 나은 것만 후보로 (한쪽만 좋으면 과최적화 의심)
            if r_is["mean"] > 0 and r_oos["mean"] > 0:
                survivors.append((label, {"is": r_is["mean"], "oos": r_oos["mean"]}))
        print()

    print("=" * 84)
    print(f"시도한 설정 수: {tried}  (다중비교 — 우연히 좋은 값이 나올 확률을 감안할 것)")
    if survivors:
        print("IS·OOS 모두 양(+)인 후보:")
        for label, v in survivors:
            print(f"  {label:<22} IS {v['is']:+.1f}% / OOS {v['oos']:+.1f}%")
    else:
        print("IS·OOS 모두 양(+)인 설정 없음 — 단일요인 조정으로는 해결되지 않는다는 뜻.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
