"""모집단 축소 백테스트 — 사전 등록 plan 20261110_1 (U1~U3 × S1~S3 = 9셀 + 벤치마크 B0/B1).

    U1 BTC 단일 · U2 BTC+ETH · U3 메이저10 (매월 1일: 상장 ≥730일 AND 직전 30일 평균 거래대금 상위 10)
    S1 현행 DC12 (config 상수 그대로: DC 돌파·ATR 트레일·하드캡·TP1/TP2 부분익절·레짐/변동성/거래량 필터)
    S2 일봉 채널 추세추종: 종가 > 직전55일 최고종가 → 다음 시가 / 종가 < 직전20일 최저종가 → 다음 시가 / 손절 진입가-2·ATR20
    S3 NR7 (v2 A3 와 동일 규칙·파라미터)

1차 측정 = Guards OFF (plan §5). Guards ON 은 보조 보고. 종목당 비중 0.95/|U| (plan §9 ①, Master 승인).
경로는 시간봉 순서. 한 시간봉 안: (1) 시가 청산(시간·채널) (2) 손절 (3) 익절 — 손절 우선.

    py scripts/backtest_universe_reduction.py --prep          # 일봉(2017~) 로드 + U3 산출 + 시간봉 수집
    py scripts/backtest_universe_reduction.py --run --runs 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:  # noqa: BLE001
    pass

import backtest_architecture_redesign as R  # noqa: E402  (업비트 시간봉 수집·Wilson·KRW 종목 목록 재사용)
import backtest_stop_validation as SV  # noqa: E402  (Guards · STOP_SLIP)
from backtest_param_sweep import P, build_panel  # noqa: E402  (S1 신호 = 운영 로직 그대로)
from services.execution import config as C  # noqa: E402
from services.market_data.fetcher import fetch_ohlcv  # noqa: E402

H1L = ROOT / "output" / "h1_long"          # 2017~ 시간봉 (output/h1 은 다른 연구 재현성 위해 건드리지 않음)
OUT = ROOT / "output" / "universe_reduction_20261110"
FEE = C.FEE_RATE
STOP_SLIP = SV.STOP_SLIP
INIT = float(C.CIRCUIT_BREAKER_INITIAL_CAPITAL)
HOUR_MS, DAY_MS = 3_600_000, 86_400_000

DATA_START = "2017-10-01T00:00:00Z"
DATA_END = "2026-11-08T00:00:00Z"
GRID_START = "2017-10-01"

# ── plan §4-2 창 (사전 고정) ──
WINDOWS = {
    "IS-A": ("2018-07-01", "2020-09-30"),
    "IS-B": ("2020-10-01", "2022-12-31"),
    "IS": ("2018-07-01", "2022-12-31"),
    "OOS": ("2023-01-01", "2026-11-06"),
}

# ── plan §2 모집단 ──
U3_MIN_AGE_DAYS, U3_TOP_N, U3_LOOKBACK = 730, 10, 30
UNIVERSES = ("U1", "U2", "U3")
STRATS = ("S1", "S2", "S3")

# ── plan §3 파라미터 (사전 고정) ──
S2 = dict(entry=55, exit=20, atr=20, stop_mult=2.0)
S3 = dict(nr_period=7, tp=0.08, sl=0.03, max_bars=3)

# ── plan §5 성공 기준 (사전 고정 — 완화 금지) ──
IS_PF, IS_SHARPE, IS_N = 1.3, 0.8, 30
SPLIT_PF, SPLIT_N = 1.1, 10
OOS_SHARPE, OOS_MDD, OOS_PF, OOS_N = 0.8, -15.0, 1.2, 20
SHARPE_DROP_MAX, VS_HOLD = 0.50, 0.8
DELIST_LOSS_PCT, SURVIVAL = -15.0, 0.85


def day_ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


# ═══════════════════════════ 데이터 ═══════════════════════════
async def load_daily_all() -> tuple[dict, pd.DataFrame]:
    btc = None
    raw = {}
    syms = R.all_krw_symbols()
    for i, sym in enumerate(syms, 1):
        df = None
        for attempt in range(4):
            try:
                df = pd.DataFrame(await fetch_ohlcv(sym, "1d", DATA_START, DATA_END, use_cache=True))
                break
            except Exception as ex:  # noqa: BLE001
                print(f"  retry {sym} {attempt + 1}/4: {type(ex).__name__}", flush=True)
                await asyncio.sleep(2 * (attempt + 1))
        if df is None or df.empty:
            print(f"  skip {sym}", flush=True)
            continue
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df[~df.index.duplicated()].sort_index()
        raw[sym] = df
        if sym == "BTC/KRW":
            btc = df
        if i % 50 == 0:
            print(f"  일봉 {i}/{len(syms)}", flush=True)
    return raw, btc


def u3_membership(raw: dict) -> dict[str, list[str]]:
    """{'YYYY-MM': [sym...]} — 매월 1일 00:00 UTC 시점 정보만 사용 (전월까지 완성 일봉)."""
    out = {}
    months = pd.date_range("2018-07-01", WINDOWS["OOS"][1], freq="MS", tz="UTC")
    for m in months:
        cand = []
        for sym, df in raw.items():
            first = df.index[0]
            if (m - first).days < U3_MIN_AGE_DAYS:
                continue
            hist = df[df.index < m].tail(U3_LOOKBACK)
            if len(hist) < U3_LOOKBACK:
                continue
            cand.append((float((hist["close"] * hist["volume"]).mean()), sym))
        cand.sort(reverse=True)
        out[m.strftime("%Y-%m")] = [s for _, s in cand[:U3_TOP_N]]
    return out


def ensure_hourly_long(sym: str, first_ms: int) -> tuple[int, str]:
    market = "KRW-" + sym.split("/")[0]
    path = H1L / f"{market}.json"
    need_lo = max(day_ms(GRID_START), first_ms)
    need_hi = day_ms(DATA_END[:10])
    rows = json.loads(path.read_text()) if path.exists() else []
    have = {r[0]: r for r in rows}
    act = []
    if not have:
        have.update(R._fetch_hourly_range(market, need_lo, need_hi))
        act.append("new")
    else:
        lo, hi = min(have), max(have)
        if hi < need_hi - 2 * HOUR_MS:
            have.update(R._fetch_hourly_range(market, hi, need_hi))
            act.append("tail")
        if lo > need_lo + DAY_MS:
            have.update(R._fetch_hourly_range(market, need_lo, lo))
            act.append("head")
    if act:
        H1L.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([have[k] for k in sorted(have)]))
    return len(have), "+".join(act) or "ok"


# ═══════════════════════════ 격자·지표 ═══════════════════════════
def build_grid(raw: dict, syms: list[str]) -> dict:
    t0 = day_ms(GRID_START) // HOUR_MS
    T = day_ms(DATA_END[:10]) // HOUR_MS - t0
    O = np.full((len(syms), T), np.nan, dtype=np.float32)
    Hh, L, Cc = O.copy(), O.copy(), O.copy()
    for i, sym in enumerate(syms):
        for ts, o, h, lo, c in json.loads((H1L / f"KRW-{sym.split('/')[0]}.json").read_text()):
            k = ts // HOUR_MS - t0
            if 0 <= k < T:
                O[i, k], Hh[i, k], L[i, k], Cc[i, k] = o, h, lo, c
    D = T // 24
    day_index = pd.date_range(GRID_START, periods=D, freq="D", tz="UTC")
    return {"O": O, "H": Hh, "L": L, "C": Cc, "t0": t0, "T": T, "D": D, "syms": syms,
            "day_index": day_index}


def day_of(g: dict, s: str) -> int:
    return (day_ms(s) // HOUR_MS - g["t0"]) // 24


def membership_array(g: dict, uni: str, u3: dict) -> np.ndarray:
    m = np.zeros((len(g["syms"]), g["D"]), dtype=bool)
    idx = {s: i for i, s in enumerate(g["syms"])}
    if uni in ("U1", "U2"):
        for s in (["BTC/KRW"] if uni == "U1" else ["BTC/KRW", "ETH/KRW"]):
            m[idx[s], :] = True
        return m
    for d, ts in enumerate(g["day_index"]):
        for s in u3.get(ts.strftime("%Y-%m"), []):
            if s in idx:
                m[idx[s], d] = True
    return m


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _first_bar(g: dict, si: int, t: int, span: int = 24) -> int | None:
    seg = g["O"][si, t:t + span]
    k = np.where(~np.isnan(seg))[0]
    return t + int(k[0]) if len(k) else None


def _didx(g: dict, ts: pd.Timestamp) -> int:
    return (int(ts.timestamp() * 1000) // HOUR_MS - g["t0"]) // 24


# ═══════════════════════════ 신호 ═══════════════════════════
def signals_s1(raw: dict, g: dict, mem: np.ndarray, regime: dict, p: P) -> list[dict]:
    """운영 DC12 — build_panel 신호(일봉) → 그날 시간봉 고가가 돌파선에 처음 닿는 봉에 진입 (SV.build_ctx 동일)."""
    sub = {s: raw[s] for s in g["syms"]}
    panel = build_panel(sub, regime, p)
    ev = []
    for si, sym in enumerate(g["syms"]):
        df = panel[sym]
        for ts, row in df[df["signal"]].iterrows():
            d = _didx(g, ts)
            if not 0 <= d < g["D"] or not mem[si, d]:
                continue
            px = float(row["entry_px"])
            hit = np.where(g["H"][si, d * 24:d * 24 + 24] >= px)[0]
            if len(hit) == 0:
                continue
            ev.append({"si": si, "t": d * 24 + int(hit[0]), "kind": "S1", "entry_px": px,
                       "atr": float(row["atr"])})
    return ev


def s2_arrays(raw: dict, g: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """entry[si,d] / exit[si,d] = d 일 종가 확정 신호 · atr20[si,d]."""
    n = (len(g["syms"]), g["D"])
    ent, ext, atr = np.zeros(n, bool), np.zeros(n, bool), np.full(n, np.nan)
    for si, sym in enumerate(g["syms"]):
        df = raw[sym].reindex(g["day_index"])
        c = df["close"]
        hi = c.shift(1).rolling(S2["entry"], min_periods=S2["entry"]).max()
        lo = c.shift(1).rolling(S2["exit"], min_periods=S2["exit"]).min()
        ent[si] = (c > hi).fillna(False).to_numpy(dtype=bool)
        ext[si] = (c < lo).fillna(False).to_numpy(dtype=bool)
        atr[si] = _atr(df, S2["atr"]).to_numpy(dtype=float)
    return ent, ext, atr


def signals_s2(g: dict, mem: np.ndarray, ent: np.ndarray, atr: np.ndarray) -> list[dict]:
    ev = []
    for si in range(len(g["syms"])):
        for d in np.where(ent[si])[0]:
            dn = int(d) + 1
            if dn >= g["D"] or not mem[si, dn] or np.isnan(atr[si, d]):
                continue
            t = _first_bar(g, si, dn * 24)
            if t is not None:
                ev.append({"si": si, "t": t, "kind": "S2", "atr": float(atr[si, d])})
    return ev


def signals_s3(raw: dict, g: dict, mem: np.ndarray) -> list[dict]:
    ev = []
    for si, sym in enumerate(g["syms"]):
        df = raw[sym]
        rng = df["high"] - df["low"]
        nr7 = rng <= rng.rolling(S3["nr_period"]).min()
        hit = df[nr7.shift(1, fill_value=False).astype(bool) & (df["close"] > df["high"].shift(1))]
        for ts in hit.index:
            d = _didx(g, ts) + 1
            if not 0 <= d < g["D"] or not mem[si, d]:
                continue
            t = _first_bar(g, si, d * 24)
            if t is not None:
                ev.append({"si": si, "t": t, "kind": "S3", "t_max": (d + S3["max_bars"]) * 24})
    return ev


# ═══════════════════════════ 시뮬레이션 ═══════════════════════════
def simulate(g: dict, events: list[dict], win: tuple[str, str], seed: int, slots: int,
             guards_on: bool, p: P, s2_exit: np.ndarray | None = None) -> dict:
    O, Hh, L, Cc = g["O"], g["H"], g["L"], g["C"]
    t_lo, t_hi = day_of(g, win[0]) * 24, (day_of(g, win[1]) + 1) * 24
    weight = C.POSITION_RATIO / slots      # plan §2: 종목당 0.95/|U|
    by_t: dict[int, list] = {}
    for e in events:
        if t_lo <= e["t"] < t_hi:
            by_t.setdefault(e["t"], []).append(e)
    rng = np.random.default_rng(seed)
    gd = SV.Guards(guards_on)
    cash, pos, last_px = INIT, {}, {}
    curve, trades = [], []
    tid = 0

    def close(si: int, px: float, t: int, why: str) -> None:
        nonlocal cash, tid
        q = pos.pop(si)
        pr = q["qty"] * px * (1 - FEE)
        cash += pr
        q["proceeds"] += pr
        ret = q["proceeds"] / q["cost"] - 1
        tid += 1
        trades.append({"ret": ret * 100, "why": why, "hold_h": t - q["t_in"], "t_in": q["t_in"]})
        gd.on_close(t, ret, q["proceeds"] - q["cost"], tid)

    def exits(si: int, t: int, entry_bar: bool) -> None:
        q = pos[si]
        o, h, lo, c = (float(x) for x in (O[si, t], Hh[si, t], L[si, t], Cc[si, t]))
        k = q["kind"]
        if not entry_bar:
            if q.get("t_max") is not None and t >= q["t_max"]:
                return close(si, o, t, "time")
            if k == "S2" and t // 24 > q["d_chk"]:
                q["d_chk"] = t // 24
                if s2_exit[si, t // 24 - 1]:
                    return close(si, o, t, "channel")
        if k == "S1":
            # SV.simulate 와 동일: 트레일 손절 → TP 부분익절 → 고점·트레일 갱신. 진입 봉은 종가 기준(SV 기본)
            if entry_bar:
                if c <= q["sl"]:
                    return close(si, c * (1 - STOP_SLIP), t, "sl")
                last_px[si] = c
                return None
            if lo <= q["sl"]:
                return close(si, min(o, q["sl"]) * (1 - STOP_SLIP), t, "sl")
            e = q["entry"]
            for j, tp in enumerate(p.tp):
                if j in q["tp_done"] or h < e * (1 + tp["trigger_pct"]):
                    continue
                sq = q["qty"] * tp["sell_ratio"] if j < len(p.tp) - 1 else q["qty"]
                pr = sq * e * (1 + tp["trigger_pct"]) * (1 - FEE)
                cash_add(pr)
                q["proceeds"] += pr
                q["qty"] -= sq
                q["tp_done"].add(j)
            if q["qty"] <= 1e-12:
                q["qty"] = 0.0
                return close(si, 0.0, t, "tp")
            if h > q["highest"]:
                q["highest"] = h
                q["sl"] = max(q["sl"], h - q["atr"] * p.atr_mult, e * (1 - p.hard))
            last_px[si] = c
            return None
        if lo <= q["sl"]:
            px = q["sl"] if entry_bar else min(o, q["sl"])
            return close(si, px * (1 - STOP_SLIP), t, "sl")
        tp = q.get("tp")
        if tp is not None and h >= tp:
            return close(si, tp if entry_bar else max(o, tp), t, "tp")
        last_px[si] = c
        return None

    def cash_add(x: float) -> None:
        nonlocal cash
        cash += x

    for t in range(t_lo, t_hi):
        held = sum(q["qty"] * last_px.get(si, q["entry"]) for si, q in pos.items())
        gd.tick(t, t // 24, cash + held)
        for si in list(pos):
            if not np.isnan(Cc[si, t]):
                exits(si, t, False)
        cands = [e for e in by_t.get(t, []) if e["si"] not in pos]
        rng.shuffle(cands)
        for e in cands:
            if len(pos) >= slots or not gd.can_buy(t):
                break
            si = e["si"]
            held = sum(q["qty"] * last_px.get(k, q["entry"]) for k, q in pos.items())
            amt = min(cash * C.POSITION_RATIO / max(slots - len(pos), 1),
                      (cash + held) * weight, cash * C.POSITION_RATIO)
            if amt < C.MIN_ORDER_KRW:
                continue
            o = float(O[si, t])
            if np.isnan(o):
                continue
            px = max(e["entry_px"], o) if e["kind"] == "S1" else o
            q = {"kind": e["kind"], "entry": px, "qty": amt * (1 - FEE) / px, "cost": amt,
                 "proceeds": 0.0, "t_in": t, "t_max": e.get("t_max"), "tp": None, "d_chk": t // 24}
            if e["kind"] == "S1":
                q.update(atr=e["atr"], highest=px, tp_done=set(),
                         sl=max(px - e["atr"] * p.atr_mult, px * (1 - p.hard)))
            elif e["kind"] == "S2":
                q["sl"] = px - S2["stop_mult"] * e["atr"]
            else:
                q["sl"], q["tp"] = px * (1 - S3["sl"]), px * (1 + S3["tp"])
            cash -= amt
            pos[si] = q
            last_px[si] = float(Cc[si, t])
            exits(si, t, True)
        if t % 24 == 23:
            held = sum(q["qty"] * last_px.get(si, q["entry"]) for si, q in pos.items())
            curve.append(cash + held)
    for si in list(pos):
        close(si, last_px.get(si, pos[si]["entry"]), t_hi - 1, "eow")
    if curve:
        curve[-1] = cash
    eq = pd.Series(curve if curve else [INIT])
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min() * 100)
    dr = eq.pct_change().dropna()
    sh = float(dr.mean() / dr.std(ddof=1) * sqrt(365)) if len(dr) > 1 and dr.std(ddof=1) > 0 else 0.0
    return {"ret": float(eq.iloc[-1] / INIT - 1) * 100, "mdd": mdd, "sharpe": sh,
            "n": len(trades), "trades": trades}


# ═══════════════════════════ 벤치마크 ═══════════════════════════
def benchmark(raw: dict, g: dict, mem: np.ndarray, win: tuple[str, str], regime_only: bool) -> dict:
    """B0 동일가중 보유 / B1 종목별 종가>EMA200 일 때만 보유(신호 다음 날 수익부터). 일간 리밸런싱, 수수료 무시."""
    d_lo, d_hi = day_of(g, win[0]), day_of(g, win[1])
    rets = []
    for si, sym in enumerate(g["syms"]):
        df = raw[sym].reindex(g["day_index"])
        r = df["close"].pct_change().to_numpy(dtype=float)
        if regime_only:
            on = (df["close"] > df["close"].ewm(span=200, adjust=False).mean()).shift(1).fillna(False)
            r = np.where(on.to_numpy(dtype=bool), r, 0.0)
        r = np.where(mem[si], r, np.nan)
        rets.append(r)
    port = np.nanmean(np.vstack(rets), axis=0)[d_lo:d_hi + 1]
    port = np.nan_to_num(port, nan=0.0)
    eq = pd.Series(np.cumprod(1 + port))
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min() * 100)
    sh = float(port.mean() / port.std(ddof=1) * sqrt(365)) if port.std(ddof=1) > 0 else 0.0
    return {"ret": float(eq.iloc[-1] - 1) * 100, "mdd": mdd, "sharpe": sh}


# ═══════════════════════════ 통계 ═══════════════════════════
def summarize(rs: list[dict]) -> dict:
    tr = [x["ret"] for r in rs for x in r["trades"]]
    n = float(np.mean([r["n"] for r in rs]))
    wins = sum(x for x in tr if x > 0)
    loss = -sum(x for x in tr if x <= 0)
    p = float(np.mean([x > 0 for x in tr])) if tr else 0.0
    lo, hi = R.wilson(p, n)
    why = pd.Series([x["why"] for r in rs for x in r["trades"]], dtype=object).value_counts(normalize=True)
    # 연도별 건당 합 (특정 연도 몰빵 확인, seed 평균)
    yr: dict[str, float] = {}
    for r in rs:
        for x in r["trades"]:
            y = str(pd.Timestamp(day_ms(GRID_START) + x["t_in"] * HOUR_MS, unit="ms").year)
            yr[y] = yr.get(y, 0.0) + x["ret"] / len(rs)
    return {"n": n, "wr": p * 100, "wr_ci_lo": lo, "wr_ci_hi": hi,
            "tmean": float(np.mean(tr)) if tr else 0.0,
            "pf": float(wins / loss) if loss > 0 else (float("inf") if wins > 0 else 0.0),
            "ret": float(np.mean([r["ret"] for r in rs])),
            "mdd": float(np.mean([r["mdd"] for r in rs])),
            "sharpe": float(np.mean([r["sharpe"] for r in rs])),
            "exit_mix": {k: round(v * 100, 1) for k, v in why.items()},
            "hold_h_med": float(np.median([x["hold_h"] for r in rs for x in r["trades"]])) if tr else 0.0,
            "year_ret_sum": {k: round(v, 1) for k, v in sorted(yr.items())},
            "trades": tr}


def strip(m: dict | None) -> dict | None:
    return None if m is None else {k: v for k, v in m.items() if k != "trades"}


def delisting_adjust(trades: list[float]) -> dict:
    adj = list(trades)
    k = int(round(len(adj) * (1 - SURVIVAL)))
    for i in sorted(range(len(adj)), key=lambda i: -adj[i])[:k]:
        adj[i] = DELIST_LOSS_PCT
    wins = sum(x for x in adj if x > 0)
    loss = -sum(x for x in adj if x <= 0)
    return {"tmean": float(np.mean(adj)) if adj else 0.0, "pf": float(wins / loss) if loss > 0 else 0.0,
            "wr": float(np.mean([x > 0 for x in adj]) * 100) if adj else 0.0}


# ═══════════════════════════ 메인 ═══════════════════════════
def load_all() -> tuple[dict, pd.DataFrame, dict, list[str]]:
    raw, btc = asyncio.run(load_daily_all())
    u3 = u3_membership(raw)
    need = sorted({"BTC/KRW", "ETH/KRW"} | {s for v in u3.values() for s in v})
    return raw, btc, u3, need


def prep() -> int:
    raw, _, u3, need = load_all()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "u3_membership.json").write_text(json.dumps(u3, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"일봉 {len(raw)}종목 · 시간봉 대상 {len(need)}: {need}", flush=True)
    for i, s in enumerate(need, 1):
        n, act = ensure_hourly_long(s, int(raw[s]["ts"].iloc[0]))
        print(f"  {i:>2}/{len(need)} {s:<12} {n:>6}봉 {act}", flush=True)
    print("prep 완료", flush=True)
    return 0


def run(runs: int) -> int:
    t_start = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    raw, btc, u3, need = load_all()
    miss = [s for s in need if not (H1L / f"KRW-{s.split('/')[0]}.json").exists()]
    if miss:
        print(f"[중단] 시간봉 미수집 {miss} — --prep 먼저", flush=True)
        return 1
    g = build_grid(raw, need)
    print(f"격자 {len(need)}종목 × {g['T']}시간 ({time.time() - t_start:.0f}s)", flush=True)
    empty_m = [m for m, v in u3.items() if not v]
    print(f"U3 빈 달 {len(empty_m)}개 ({empty_m[:3]}…{empty_m[-1:] if empty_m else ''})", flush=True)

    btc_ema = btc["close"].ewm(span=C.REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc_ema))
    ent2, ext2, atr2 = s2_arrays(raw, g)

    cells: dict = {}
    for uni in UNIVERSES:
        mem = membership_array(g, uni, u3)
        slots = 1 if uni == "U1" else 2 if uni == "U2" else U3_TOP_N
        p = P(slots=slots, weight=C.POSITION_RATIO / slots)
        evs = {"S1": signals_s1(raw, g, mem, regime, p), "S2": signals_s2(g, mem, ent2, atr2),
               "S3": signals_s3(raw, g, mem)}
        bm = {w: {"B0": benchmark(raw, g, mem, WINDOWS[w], False),
                  "B1": benchmark(raw, g, mem, WINDOWS[w], True)} for w in WINDOWS}
        for st in STRATS:
            key = f"{uni}-{st}"
            c = {"events": len(evs[st]), "slots": slots, "ev": evs[st], "p": p, "bench": bm}
            for w in ("IS-A", "IS-B", "IS"):
                rs = [simulate(g, evs[st], WINDOWS[w], s, slots, False, p, ext2) for s in range(runs)]
                c[w] = summarize(rs)
                m = c[w]
                print(f"  {key} {w:<4} n={m['n']:6.1f} 건당 {m['tmean']:+.2f}% PF {m['pf']:.2f} "
                      f"Sharpe {m['sharpe']:+.2f} MDD {m['mdd']:.1f}% 승률 {m['wr']:.1f}% "
                      f"청산 {m['exit_mix']}", flush=True)
            a, b, i_ = c["IS-A"], c["IS-B"], c["IS"]
            chk = {
                "IS_tmean>0": i_["tmean"] > 0, "IS_PF>=1.3": i_["pf"] >= IS_PF,
                "IS_Sharpe>=0.8": i_["sharpe"] >= IS_SHARPE, "IS_n>=30": i_["n"] >= IS_N,
                "ISA_tmean>0": a["tmean"] > 0, "ISA_PF>=1.1": a["pf"] >= SPLIT_PF, "ISA_n>=10": a["n"] >= SPLIT_N,
                "ISB_tmean>0": b["tmean"] > 0, "ISB_PF>=1.1": b["pf"] >= SPLIT_PF, "ISB_n>=10": b["n"] >= SPLIT_N,
            }
            c["is_checks"], c["is_pass"] = chk, all(chk.values())
            print(f"  → {key} IS {'PASS' if c['is_pass'] else 'FAIL'} "
                  f"(실패: {[k for k, v in chk.items() if not v] or '-'})", flush=True)
            cells[key] = c

    passing = [k for k, c in cells.items() if c["is_pass"]]
    pick = max(passing, key=lambda k: cells[k]["IS"]["sharpe"]) if passing else None
    oos = checks = aux = None
    if pick:
        c = cells[pick]
        rs = [simulate(g, c["ev"], WINDOWS["OOS"], s, c["slots"], False, c["p"], ext2) for s in range(runs)]
        oos = summarize(rs)
        b0 = c["bench"]["OOS"]["B0"]["sharpe"]
        is_s = c["IS"]["sharpe"]
        drop = (is_s - oos["sharpe"]) / is_s if is_s > 0 else float("nan")
        oos["sharpe_drop"] = drop
        checks = {
            "Sharpe>=0.8": oos["sharpe"] >= OOS_SHARPE, "MDD>=-15%": oos["mdd"] >= OOS_MDD,
            "tmean>0": oos["tmean"] > 0, "PF>=1.2": oos["pf"] >= OOS_PF, "n>=20": oos["n"] >= OOS_N,
            "SharpeDrop<50%": (not np.isnan(drop)) and drop < SHARPE_DROP_MAX,
            "Sharpe>=B0x0.8": oos["sharpe"] >= b0 * VS_HOLD,
        }
        print(f"\n[OOS {pick}] n={oos['n']:.1f} 건당 {oos['tmean']:+.2f}% PF {oos['pf']:.2f} "
              f"Sharpe {oos['sharpe']:+.2f} (B0 {b0:+.2f}) MDD {oos['mdd']:.1f}% 하락 {drop * 100:.0f}%", flush=True)
        print(f"  checks {checks}", flush=True)
        rs_on = [simulate(g, c["ev"], WINDOWS["OOS"], s, c["slots"], True, c["p"], ext2) for s in range(runs)]
        aux = {"guards_on_oos": strip(summarize(rs_on))}
        if pick.startswith("U3"):
            aux["delisting_adjusted_oos"] = delisting_adjust(oos["trades"])
        pd.Series(oos["trades"], name="ret_pct").to_csv(OUT / f"oos_{pick}_trades.csv", index=False)

    # 보조: 전 셀 IS Guards ON (운영 재현, 판정 불포함)
    guards_on_is = {}
    for k, c in cells.items():
        rs = [simulate(g, c["ev"], WINDOWS["IS"], s, c["slots"], True, c["p"], ext2) for s in range(runs)]
        guards_on_is[k] = strip(summarize(rs))

    if not pick:
        verdict = "B: IS 통과 셀 0 — 사전 등록 IS 기준 미달"
    elif not all(checks.values()):
        only_hold = [k for k, v in checks.items() if not v] == ["Sharpe>=B0x0.8"]
        verdict = (f"C: {pick} OOS 통과, 보유 대비만 미달" if only_hold else f"B: {pick} IS 통과 → OOS 미달")
    else:
        verdict = f"A: {pick} OOS 7기준 전수 통과"
    print(f"\n### 판정 — 시나리오 {verdict}", flush=True)

    summary = {
        "plan": "workspace/plans/20261110_1_universe_reduction.md",
        "timestamp": datetime.now(timezone.utc).isoformat(), "runs": runs, "windows": WINDOWS,
        "params": {"S2": S2, "S3": S3, "S1": "services/execution/config.py 현행"},
        "u3_empty_months": empty_m,
        "cells": {k: {"events": c["events"], "slots": c["slots"],
                      **{w: strip(c[w]) for w in ("IS-A", "IS-B", "IS")},
                      "is_checks": c["is_checks"], "is_pass": c["is_pass"]} for k, c in cells.items()},
        "benchmarks": {u: cells[f"{u}-S1"]["bench"] for u in UNIVERSES},
        "picked": pick, "oos": strip(oos), "oos_checks": checks, "aux": aux,
        "guards_on_is": guards_on_is, "verdict": verdict,
        "elapsed_s": round(time.time() - t_start),
    }
    (OUT / "results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                                      encoding="utf-8")
    print(f"결과 저장 {OUT / 'results.json'} ({time.time() - t_start:.0f}s)", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--runs", type=int, default=10)
    a = ap.parse_args()
    if a.prep:
        return prep()
    if a.run:
        return run(a.runs)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
