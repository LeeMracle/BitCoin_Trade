"""아키텍처 재설계 v2 백테스트 — 사전 등록 plan 20261108_1 (A1~A4).

DC 돌파 축을 버리고 신호 생성 메커니즘 자체가 다른 4개 가설을 독립 검증한다.
    A1 Mean Reversion   : 일봉 종가 < BB(20,2σ) 하단 AND RSI(2) < 10 → 다음 일봉 시가
    A2 MTF Momentum     : 일봉 종가>EMA50 & EMA20>EMA50 + 시간봉 EMA20 아래 2봉↑ 후 재돌파 → 다음 시간봉 시가
    A3 NR7 Compression  : D=NR7, D+1 종가 > D 고가 → D+2 일봉 시가
    A4 RS Rotation      : 20봉 수익률 상위 10 ∩ BTC 20봉 수익률 상회 ∩ BTC>EMA200 → 다음 일봉 시가

청산 규칙은 가설별 전용 (plan §2 — 현행 TP/ATR 트레일 복사 금지). 경로는 시간봉 순서대로 처리한다.
한 시간봉 안의 순서: (1) 시간 청산/교체 청산은 그 봉 시가 (2) 손절 (3) 익절 — 손절 우선(보수적).

    py scripts/backtest_architecture_redesign.py --prep          # 일봉 로드 + 시간봉 캐시 보충
    py scripts/backtest_architecture_redesign.py --run --runs 10 # IS-A/IS-B/IS → 선택 → OOS 1회

사전 등록 규칙(plan §4·§5)은 코드 상수로 고정. 결과를 본 뒤 상수 변경 금지.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
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

import backtest_stop_validation as SV  # noqa: E402  (Guards 재사용, 시간봉 캐시 경로)
from services.execution import config as C  # noqa: E402
from services.market_data.fetcher import fetch_ohlcv  # noqa: E402

H1 = SV.H1
OUT = ROOT / "output" / "arch_redesign_20261109"
FEE = C.FEE_RATE
STOP_SLIP = SV.STOP_SLIP
INIT = float(C.CIRCUIT_BREAKER_INITIAL_CAPITAL)
SLOTS = C.MAX_POSITIONS
WEIGHT = C.MAX_POSITION_WEIGHT
EXCLUDE = {"USDT", "USDC", "DAI", "BUSD"}
SKIPPED: list[str] = []   # 일봉 로드 실패 종목 — results.json 에 기록 (종목군 누락 추적)
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# ── 데이터 범위 ──
DAILY_START = "2024-03-24T00:00:00Z"   # 지표 워밍업(EMA50·SMA20) 확보 — IS 시작 219일 전
BTC_START = "2023-06-01T00:00:00Z"     # EMA200 워밍업
DATA_END = "2026-11-08T00:00:00Z"
GRID_START = "2024-10-01"              # 시간봉 격자 시작 (A2 시간봉 EMA20 워밍업 포함)

# ── plan §3-1 창 (사전 고정) ──
WINDOWS = {
    "IS-A": ("2024-10-29", "2025-03-19"),
    "IS-B": ("2025-03-20", "2025-07-11"),
    "IS": ("2024-10-29", "2025-07-11"),
    "OOS": ("2025-07-12", "2026-11-06"),
}

# ── plan §3-2 종목군 (PIT) ──
MIN_AGE_DAYS = 67
MIN_VALUE_KRW = C.MIN_VOLUME_KRW   # 5억

# ── plan §4 성공 기준 (사전 고정 — 완화 금지) ──
BREAKEVEN = 43.6
IS_WR_FLOOR, IS_N_FLOOR = 48.6, 60
SPLIT_WR_FLOOR, SPLIT_N_FLOOR = 45.0, 25
OOS_SHARPE_FLOOR, OOS_MDD_FLOOR, OOS_N_FLOOR = 0.8, -15.0, 60
SHARPE_DROP_MAX = 0.50
DELIST_LOSS_PCT, SURVIVAL = -15.0, 0.85

# ── plan §2 가설 파라미터 (사전 고정) ──
A1 = dict(bb_period=20, bb_sigma=2.0, rsi_period=2, rsi_threshold=10, sl=0.04, max_bars=5)
A2 = dict(daily_ema_fast=20, daily_ema_slow=50, hourly_ema=20, pullback_bars_min=2,
          tp=0.06, max_hours=48)
A3 = dict(nr_period=7, tp=0.08, sl=0.03, max_bars=3)
A4 = dict(lookback=20, top_n=10, sl=0.05, regime_ema=200)


def day_ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


# ═══════════════════════════ 데이터 ═══════════════════════════
def all_krw_symbols() -> list[str]:
    req = urllib.request.Request("https://api.upbit.com/v1/market/all",
                                 headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read())
    return sorted(f"{m['market'][4:]}/KRW" for m in rows
                  if m["market"].startswith("KRW-") and m["market"][4:] not in EXCLUDE)


async def load_daily() -> tuple[dict, pd.DataFrame]:
    """raw{sym: 일봉 df(index=UTC 일자)} · btc df(ema200 포함)."""
    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", BTC_START, DATA_END, use_cache=True))
    btc.index = pd.to_datetime(btc["ts"], unit="ms", utc=True)
    btc["ema200"] = btc["close"].ewm(span=A4["regime_ema"], adjust=False).mean()
    raw = {}
    syms = all_krw_symbols()
    for i, sym in enumerate(syms, 1):
        df = None
        for attempt in range(4):   # 일시 타임아웃으로 종목이 조용히 빠지면 종목군이 바뀐다
            try:
                df = pd.DataFrame(await fetch_ohlcv(sym, "1d", DAILY_START, DATA_END, use_cache=True))
                break
            except Exception as ex:  # noqa: BLE001
                print(f"  retry {sym} {attempt + 1}/4: {type(ex).__name__}", flush=True)
                await asyncio.sleep(2 * (attempt + 1))
        if df is None:
            SKIPPED.append(sym)
            print(f"  skip {sym}: 4회 실패", flush=True)
            continue
        if len(df) < MIN_AGE_DAYS:
            continue
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        df = df[~df.index.duplicated()].sort_index()
        raw[sym] = df
        if i % 50 == 0:
            print(f"  일봉 {i}/{len(syms)}", flush=True)
    return raw, btc


def add_pit(raw: dict) -> None:
    """eligible[d] = 그날 시가 시점에 거래 대상이었는가 — 전일 거래대금 ≥ 5억 AND 상장 ≥ 67일.

    상장일은 데이터 내 첫 일봉(DAILY_START 이전 상장은 DAILY_START 로 잘림 → IS 시작 시 219일 ≥ 67).
    """
    for df in raw.values():
        first = int(df["ts"].iloc[0])
        age = (df["ts"] - first) // DAY_MS
        prev_val = (df["close"] * df["volume"]).shift(1)
        df["eligible"] = (age >= MIN_AGE_DAYS) & (prev_val >= MIN_VALUE_KRW)


def eligible_symbols(raw: dict) -> list[str]:
    lo, hi = day_ms(WINDOWS["IS"][0]), day_ms(WINDOWS["OOS"][1])
    return sorted(s for s, df in raw.items()
                  if df.loc[(df["ts"] >= lo) & (df["ts"] <= hi), "eligible"].any())


def _fetch_hourly_range(market: str, start_ms: int, end_ms: int) -> dict[int, list]:
    """업비트 60분봉 [start_ms, end_ms) 역방향 페이지 수집."""
    out: dict[int, list] = {}
    to = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
    while int(to.timestamp() * 1000) > start_ms:
        url = (f"https://api.upbit.com/v1/candles/minutes/60?market={market}&count=200"
               f"&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        rows = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(
                        url, headers={"accept": "application/json"}), timeout=15) as r:
                    rows = json.loads(r.read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.11)
        if not rows:
            break
        for r in rows:
            ts = int(datetime.fromisoformat(r["candle_date_time_utc"]).replace(
                tzinfo=timezone.utc).timestamp() * 1000)
            out[ts] = [ts, r["opening_price"], r["high_price"], r["low_price"], r["trade_price"]]
        nxt = datetime.fromtimestamp(min(out) / 1000, tz=timezone.utc)
        if nxt >= to:
            break
        to = nxt
    return {k: v for k, v in out.items() if start_ms <= k < end_ms}


def ensure_hourly(sym: str, first_daily_ms: int) -> tuple[int, str]:
    """시간봉 캐시를 [max(GRID_START, 상장일), DATA_END) 까지 보충. 반환 (봉 수, 동작)."""
    market = "KRW-" + sym.split("/")[0]
    path = H1 / f"{market}.json"
    need_lo = max(day_ms(GRID_START), first_daily_ms)
    need_hi = day_ms(DATA_END[:10])
    rows = json.loads(path.read_text()) if path.exists() else []
    have = {r[0]: r for r in rows}
    act = []
    if not have:
        have.update(_fetch_hourly_range(market, need_lo, need_hi))
        act.append("new")
    else:
        lo, hi = min(have), max(have)
        if hi < need_hi - 2 * HOUR_MS:
            have.update(_fetch_hourly_range(market, hi, need_hi))
            act.append("tail")
        if lo > need_lo + DAY_MS:
            have.update(_fetch_hourly_range(market, need_lo, lo))
            act.append("head")
    if act:
        H1.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([have[k] for k in sorted(have)]))
    return len(have), "+".join(act) or "ok"


# ═══════════════════════════ 지표 ═══════════════════════════
def rsi_wilder(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(dn > 0, 100.0).where(up > 0, 0.0)


def add_indicators(raw: dict, btc: pd.DataFrame) -> None:
    btc_ret20 = btc["close"] / btc["close"].shift(A4["lookback"]) - 1
    btc_on = btc["close"] > btc["ema200"]
    for df in raw.values():
        c = df["close"]
        # A1 — 전부 해당 일 종가까지 확정된 값 (신호는 종가 마감 후, 진입은 다음 봉 시가)
        sma = c.rolling(A1["bb_period"]).mean()
        sd = c.rolling(A1["bb_period"]).std(ddof=0)
        df["sma20"] = sma
        df["bb_lo"] = sma - A1["bb_sigma"] * sd
        df["rsi2"] = rsi_wilder(c, A1["rsi_period"])
        # A2 — 일봉 추세
        df["d_ema20"] = c.ewm(span=A2["daily_ema_fast"], adjust=False).mean()
        df["d_ema50"] = c.ewm(span=A2["daily_ema_slow"], adjust=False).mean()
        df["trend_up"] = (c > df["d_ema50"]) & (df["d_ema20"] > df["d_ema50"])
        # A3 — NR7
        rng = df["high"] - df["low"]
        df["nr7"] = rng <= rng.rolling(A3["nr_period"]).min()
        # A4 — 상대강도
        df["ret20"] = c / c.shift(A4["lookback"]) - 1
        df["btc_ret20"] = btc_ret20.reindex(df.index)
        df["btc_on"] = btc_on.reindex(df.index).fillna(False).astype(bool)


# ═══════════════════════════ 격자 ═══════════════════════════
def build_grid(raw: dict, syms: list[str]) -> dict:
    t0 = day_ms(GRID_START) // HOUR_MS
    T = day_ms(DATA_END[:10]) // HOUR_MS - t0
    O = np.full((len(syms), T), np.nan)
    Hh, L, Cc = O.copy(), O.copy(), O.copy()
    for i, sym in enumerate(syms):
        for ts, o, h, lo, c in json.loads((H1 / f"KRW-{sym.split('/')[0]}.json").read_text()):
            k = ts // HOUR_MS - t0
            if 0 <= k < T:
                O[i, k], Hh[i, k], L[i, k], Cc[i, k] = o, h, lo, c
    D = T // 24
    # 일봉 파생 배열 [sym, day] — day k 는 GRID_START + k 일 (UTC 00:00, 업비트 일봉 경계와 동일)
    day_index = pd.date_range(GRID_START, periods=D, freq="D", tz="UTC")
    def arr(col, fill=np.nan, dtype=float):
        out = np.full((len(syms), D), fill, dtype=dtype)
        for i, s in enumerate(syms):
            out[i] = raw[s][col].reindex(day_index).to_numpy(dtype=dtype, na_value=fill)
        return out
    return {"O": O, "H": Hh, "L": L, "C": Cc, "t0": t0, "T": T, "D": D, "syms": syms,
            "day_index": day_index,
            "elig": arr("eligible", False, bool), "sma20": arr("sma20")}


def day_of(g: dict, s: str) -> int:
    return (day_ms(s) // HOUR_MS - g["t0"]) // 24


# ═══════════════════════════ 신호 (진입 이벤트) ═══════════════════════════
# 이벤트: {"si", "t" (진입 시간봉 index, 시가 체결), "kind", 가설별 청산 파라미터}
def _first_bar(g: dict, si: int, t: int, span: int = 24) -> int | None:
    """t 부터 span 시간 안 첫 거래 시간봉 (거래 없는 시간은 업비트 캔들이 없다)."""
    seg = g["O"][si, t:t + span]
    k = np.where(~np.isnan(seg))[0]
    return t + int(k[0]) if len(k) else None


def signals_a1(raw: dict, g: dict) -> list[dict]:
    ev = []
    for si, sym in enumerate(g["syms"]):
        df = raw[sym]
        hit = df[(df["close"] < df["bb_lo"]) & (df["rsi2"] < A1["rsi_threshold"]) & df["eligible"]]
        for ts in hit.index:
            d = (int(ts.timestamp() * 1000) // HOUR_MS - g["t0"]) // 24 + 1   # 다음 일봉
            if not 0 <= d < g["D"]:
                continue
            t = _first_bar(g, si, d * 24)
            if t is None:
                continue
            ev.append({"si": si, "t": t, "kind": "A1", "d_entry": d,
                       "t_max": (d + A1["max_bars"]) * 24})
    return ev


def signals_a2(raw: dict, g: dict) -> list[dict]:
    ev = []
    n_need = A2["pullback_bars_min"]
    for si, sym in enumerate(g["syms"]):
        df = raw[sym]
        trend = df["trend_up"] & True
        elig = df["eligible"]
        c = pd.Series(g["C"][si])
        valid = c.notna()
        cv = c[valid]
        if len(cv) < 50:
            continue
        ema = cv.ewm(span=A2["hourly_ema"], adjust=False).mean()
        below = (cv < ema).to_numpy()
        above = (cv > ema).to_numpy()
        idx = cv.index.to_numpy()
        lows = g["L"][si]
        for j in range(n_need, len(idx) - 1):
            if not above[j] or not below[j - n_need:j].all():
                continue
            k = idx[j]                 # 재돌파 시간봉 (종가 확정)
            t = int(idx[j + 1])        # 다음 거래 시간봉 시가 진입
            if t - k > 24:
                continue
            day_k = k // 24
            # 일봉 추세 = 직전 완성 일봉 기준 (진행 중 일봉 금지 — lessons #40)
            prev_day = g["day_index"][0] + pd.Timedelta(days=int(day_k) - 1)
            cur_day = g["day_index"][0] + pd.Timedelta(days=int(t // 24))
            if not bool(trend.get(prev_day, False)) or not bool(elig.get(cur_day, False)):
                continue
            ev.append({"si": si, "t": t, "kind": "A2", "sl_px": float(lows[k]),
                       "t_max": t + A2["max_hours"]})
    return ev


def signals_a3(raw: dict, g: dict) -> list[dict]:
    ev = []
    for si, sym in enumerate(g["syms"]):
        df = raw[sym]
        nr = df["nr7"].shift(1, fill_value=False).astype(bool)      # 전일이 NR7
        brk = df["close"] > df["high"].shift(1)                      # 금일 종가 > 전일(NR7) 고가
        hit = df[nr & brk & df["eligible"]]
        for ts in hit.index:
            d = (int(ts.timestamp() * 1000) // HOUR_MS - g["t0"]) // 24 + 1
            if not 0 <= d < g["D"]:
                continue
            t = _first_bar(g, si, d * 24)
            if t is None:
                continue
            ev.append({"si": si, "t": t, "kind": "A3", "t_max": (d + A3["max_bars"]) * 24})
    return ev


def rs_list(raw: dict, g: dict) -> np.ndarray:
    """in_list[si, d] = d 일 종가 기준 선별 리스트 포함 여부 (다음 일봉 시가에 행동)."""
    syms, D = g["syms"], g["D"]
    ret = np.full((len(syms), D), np.nan)
    btc_r = np.full(D, np.nan)
    on = np.zeros(D, dtype=bool)
    for i, s in enumerate(syms):
        df = raw[s].reindex(g["day_index"])
        r = df["ret20"].to_numpy(dtype=float)
        ret[i] = np.where(df["eligible"].fillna(False).to_numpy(dtype=bool), r, np.nan)
        b = df["btc_ret20"].to_numpy(dtype=float)
        btc_r = np.where(np.isnan(btc_r), b, btc_r)
        o = df["btc_on"].fillna(False).to_numpy(dtype=bool)
        on |= o
    out = np.zeros_like(ret, dtype=bool)
    for d in range(D):
        if not on[d] or np.isnan(btc_r[d]):
            continue
        col = ret[:, d]
        ok = np.where(~np.isnan(col))[0]
        if len(ok) == 0:
            continue
        top = ok[np.argsort(-col[ok], kind="stable")[:A4["top_n"]]]
        out[top[col[top] > btc_r[d]], d] = True
    return out


def signals_a4(raw: dict, g: dict) -> tuple[list[dict], np.ndarray]:
    in_list = rs_list(raw, g)
    ev = []
    for si in range(len(g["syms"])):
        for d in np.where(in_list[si])[0]:
            dn = int(d) + 1
            if dn >= g["D"]:
                continue
            t = _first_bar(g, si, dn * 24)
            if t is None:
                continue
            ev.append({"si": si, "t": t, "kind": "A4"})
    return ev, in_list


# ═══════════════════════════ 시뮬레이션 ═══════════════════════════
def simulate(g: dict, events: list[dict], win: tuple[str, str], seed: int,
             in_list: np.ndarray | None = None) -> dict:
    O, Hh, L, Cc = g["O"], g["H"], g["L"], g["C"]
    d_lo, d_hi = day_of(g, win[0]), day_of(g, win[1])
    t_lo, t_hi = d_lo * 24, (d_hi + 1) * 24
    by_t: dict[int, list] = {}
    for e in events:
        if t_lo <= e["t"] < t_hi:
            by_t.setdefault(e["t"], []).append(e)
    rng = np.random.default_rng(seed)
    gd = SV.Guards(True)
    cash, pos, last_px = INIT, {}, {}
    curve, trades = [], []
    tid = 0

    def close(si: int, px: float, t: int, why: str) -> None:
        nonlocal cash, tid
        q = pos.pop(si)
        pr = q["qty"] * px * (1 - FEE)
        cash += pr
        ret = pr / q["cost"] - 1
        tid += 1
        trades.append({"ret": ret * 100, "why": why, "hold_h": t - q["t_in"]})
        gd.on_close(t, ret, pr - q["cost"], tid)

    def exits(si: int, t: int, entry_bar: bool) -> None:
        q = pos[si]
        o, h, lo, c = O[si, t], Hh[si, t], L[si, t], Cc[si, t]
        k = q["kind"]
        if not entry_bar:
            # (1) 시가 청산: 시간 청산 / A4 교체·레짐 이탈
            if q.get("t_max") is not None and t >= q["t_max"]:
                return close(si, o, t, "time")
            # A4: 새 일봉의 첫 거래 시간봉 = "익일 시가". 전일 종가 기준 리스트 이탈(레짐 OFF 포함) 시 청산
            if k == "A4" and t // 24 > q["d_chk"]:
                q["d_chk"] = t // 24
                if not in_list[si, t // 24 - 1]:
                    return close(si, o, t, "rotate")
        # (2) 손절
        if lo <= q["sl"]:
            px = q["sl"] if entry_bar else min(o, q["sl"])
            return close(si, px * (1 - STOP_SLIP), t, "sl")
        # (3) 익절
        tp = q.get("tp")
        if k == "A1":
            tp = g["sma20"][si, t // 24 - 1]   # 직전 완성 일봉의 SMA20 = 중심선
        if tp is not None and not np.isnan(tp) and h >= tp:
            px = tp if entry_bar else max(o, tp)
            return close(si, px, t, "tp")
        last_px[si] = c

    for t in range(t_lo, t_hi):
        d = t // 24
        held = sum(q["qty"] * last_px.get(si, q["entry"]) for si, q in pos.items())
        gd.tick(t, d, cash + held)
        for si in list(pos):
            if not np.isnan(Cc[si, t]):
                exits(si, t, False)
        cands = [e for e in by_t.get(t, []) if e["si"] not in pos]
        rng.shuffle(cands)
        for e in cands:
            if len(pos) >= SLOTS or not gd.can_buy(t):
                break
            si = e["si"]
            held = sum(q["qty"] * last_px.get(k, q["entry"]) for k, q in pos.items())
            equity = cash + held
            amt = min(cash * C.POSITION_RATIO / max(SLOTS - len(pos), 1),
                      equity * WEIGHT, cash * C.POSITION_RATIO)
            if amt < C.MIN_ORDER_KRW:
                continue
            px = O[si, t]
            q = {"kind": e["kind"], "entry": px, "qty": amt * (1 - FEE) / px, "cost": amt,
                 "t_in": t, "t_max": e.get("t_max"), "tp": None, "d_chk": d}
            if e["kind"] == "A1":
                q["sl"] = px * (1 - A1["sl"])
            elif e["kind"] == "A2":
                q["sl"], q["tp"] = e["sl_px"], px * (1 + A2["tp"])
            elif e["kind"] == "A3":
                q["sl"], q["tp"] = px * (1 - A3["sl"]), px * (1 + A3["tp"])
            else:
                q["sl"] = px * (1 - A4["sl"])
            cash -= amt
            pos[si] = q
            last_px[si] = Cc[si, t]
            exits(si, t, True)      # 시가 체결 후 같은 봉 경로 — 손절 우선
        if t % 24 == 23:
            held = sum(q["qty"] * last_px.get(si, q["entry"]) for si, q in pos.items())
            curve.append(cash + held)
    # 창 종료: 미청산 포지션은 마지막 종가로 청산 (평가 일관성)
    for si in list(pos):
        close(si, last_px.get(si, pos[si]["entry"]), t_hi - 1, "eow")
    if curve:
        curve[-1] = cash
    eq = pd.Series(curve if curve else [INIT])
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min() * 100)
    dr = eq.pct_change().dropna()
    sharpe = float(dr.mean() / dr.std(ddof=1) * sqrt(365)) if len(dr) > 1 and dr.std(ddof=1) > 0 else 0.0
    return {"ret": float(eq.iloc[-1] / INIT - 1) * 100, "mdd": mdd, "sharpe": sharpe,
            "n": len(trades), "trades": trades}


# ═══════════════════════════ 통계 ═══════════════════════════
def wilson(p: float, n: float, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    m = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (ctr - m) * 100, (ctr + m) * 100


def summarize(rs: list[dict]) -> dict:
    """seed 별 결과 집계. CI 는 seed 평균 거래 수(n)로 계산 — seed 반복은 독립 표본이 아니다."""
    tr = [x["ret"] for r in rs for x in r["trades"]]
    n = float(np.mean([r["n"] for r in rs]))
    p = float(np.mean([x > 0 for x in tr])) if tr else 0.0
    lo, hi = wilson(p, n)
    why = pd.Series([x["why"] for r in rs for x in r["trades"]]).value_counts(normalize=True)
    return {"n": n, "wr": p * 100, "wr_ci_lo": lo, "wr_ci_hi": hi,
            "tmean": float(np.mean(tr)) if tr else 0.0,
            "ret": float(np.mean([r["ret"] for r in rs])),
            "mdd": float(np.mean([r["mdd"] for r in rs])),
            "sharpe": float(np.mean([r["sharpe"] for r in rs])),
            "exit_mix": {k: round(v * 100, 1) for k, v in why.items()},
            "hold_h_med": float(np.median([x["hold_h"] for r in rs for x in r["trades"]])) if tr else 0.0,
            "trades": tr}


def delisting_adjust(trades: list[float], n_per_run: float) -> dict:
    """plan §3-4 / §4-3 — 상위 수익 거래 15% 를 -15% 로 치환 (비관). v1 과 동일 규칙."""
    adj = list(trades)
    k = int(round(len(adj) * (1 - SURVIVAL)))
    for i in sorted(range(len(adj)), key=lambda i: -adj[i])[:k]:
        adj[i] = DELIST_LOSS_PCT
    p = float(np.mean([x > 0 for x in adj])) if adj else 0.0
    lo, hi = wilson(p, n_per_run)
    return {"wr": p * 100, "tmean": float(np.mean(adj)) if adj else 0.0, "wr_ci_lo": lo, "wr_ci_hi": hi}


def strip(m: dict) -> dict:
    return {k: v for k, v in m.items() if k != "trades"}


# ═══════════════════════════ 메인 ═══════════════════════════
def prep() -> int:
    raw, _ = asyncio.run(load_daily())
    add_pit(raw)
    syms = eligible_symbols(raw)
    print(f"일봉 {len(raw)}종목 · PIT 적격(창 내 1일 이상) {len(syms)}종목", flush=True)
    for i, s in enumerate(syms, 1):
        n, act = ensure_hourly(s, int(raw[s]["ts"].iloc[0]))
        if act != "ok":
            print(f"  {i:>3}/{len(syms)} {s:<14} {n:>6}봉 {act}", flush=True)
    print("prep 완료", flush=True)
    return 0


def run(runs: int) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    raw, btc = asyncio.run(load_daily())
    add_pit(raw)
    add_indicators(raw, btc)
    syms = [s for s in eligible_symbols(raw) if (H1 / f"KRW-{s.split('/')[0]}.json").exists()]
    miss = [s for s in eligible_symbols(raw) if s not in syms]
    print(f"PIT 적격 {len(syms) + len(miss)} · 시간봉 보유 {len(syms)} · 누락 {len(miss)} {miss[:8]}", flush=True)
    if miss:
        print("[중단] --prep 먼저", flush=True)
        return 1
    g = build_grid(raw, syms)
    print(f"격자 {len(syms)}종목 × {g['T']}시간 ({time.time() - t_start:.0f}s)", flush=True)

    ev = {"A1": signals_a1(raw, g), "A2": signals_a2(raw, g), "A3": signals_a3(raw, g)}
    ev["A4"], in_list = signals_a4(raw, g)
    for k, v in ev.items():
        print(f"  {k} 진입 이벤트 {len(v)}", flush=True)

    res: dict = {}
    for h in ("A1", "A2", "A3", "A4"):
        res[h] = {}
        for w in ("IS-A", "IS-B", "IS"):
            rs = [simulate(g, ev[h], WINDOWS[w], s, in_list) for s in range(runs)]
            res[h][w] = summarize(rs)
            m = res[h][w]
            print(f"  {h} {w:<4} n={m['n']:6.1f} 승률 {m['wr']:5.1f}% 건당 {m['tmean']:+.2f}% "
                  f"Sharpe {m['sharpe']:+.2f} MDD {m['mdd']:.1f}% 청산 {m['exit_mix']}", flush=True)
        a, b, i_ = res[h]["IS-A"], res[h]["IS-B"], res[h]["IS"]
        chk = {
            "IS_wr>=48.6": i_["wr"] >= IS_WR_FLOOR,
            "IS_tmean>0": i_["tmean"] > 0,
            "IS_n>=60": i_["n"] >= IS_N_FLOOR,
            "ISA_wr>=45": a["wr"] >= SPLIT_WR_FLOOR, "ISB_wr>=45": b["wr"] >= SPLIT_WR_FLOOR,
            "ISA_tmean>0": a["tmean"] > 0, "ISB_tmean>0": b["tmean"] > 0,
            "ISA_n>=25": a["n"] >= SPLIT_N_FLOOR, "ISB_n>=25": b["n"] >= SPLIT_N_FLOOR,
        }
        res[h]["is_checks"] = chk
        res[h]["is_pass"] = all(chk.values())
        print(f"  → {h} IS {'PASS' if res[h]['is_pass'] else 'FAIL'} "
              f"(실패: {[k for k, v in chk.items() if not v] or '-'})", flush=True)

    passing = [h for h in res if res[h]["is_pass"]]
    pick = max(passing, key=lambda h: res[h]["IS"]["tmean"]) if passing else None
    oos = checks = delist = None
    if pick:
        rs = [simulate(g, ev[pick], WINDOWS["OOS"], s, in_list) for s in range(runs)]
        oos = summarize(rs)
        is_s = res[pick]["IS"]["sharpe"]
        drop = (is_s - oos["sharpe"]) / is_s if is_s > 0 else float("nan")
        oos["sharpe_drop"] = drop
        checks = {
            "Sharpe>=0.8": oos["sharpe"] >= OOS_SHARPE_FLOOR,
            "MDD>=-15%": oos["mdd"] >= OOS_MDD_FLOOR,
            "WR_CI_lo>43.6%": oos["wr_ci_lo"] > BREAKEVEN,
            "tmean>0": oos["tmean"] > 0,
            "n>=60": oos["n"] >= OOS_N_FLOOR,
            "SharpeDrop<50%": (not np.isnan(drop)) and drop < SHARPE_DROP_MAX,
        }
        print(f"\n[OOS {pick}] n={oos['n']:.1f} 승률 {oos['wr']:.1f}% CI[{oos['wr_ci_lo']:.1f},"
              f"{oos['wr_ci_hi']:.1f}] 건당 {oos['tmean']:+.2f}% Sharpe {oos['sharpe']:+.2f} "
              f"MDD {oos['mdd']:.1f}% 하락 {drop * 100:.0f}%", flush=True)
        print(f"  checks {checks}", flush=True)
        if all(checks.values()):
            delist = delisting_adjust(oos["trades"], oos["n"])
            delist["pass"] = delist["tmean"] > 0 and delist["wr_ci_lo"] > BREAKEVEN
            print(f"  상폐 보정 {delist}", flush=True)
            pd.Series(oos["trades"], name="ret_pct").to_csv(OUT / f"oos_{pick}_trades.csv", index=False)

    if not pick:
        # cto review Major-2: 탈락 사유는 사전 등록 기준(승률·분할 안정성) 미달이지 "기대값 없음"이 아니다
        verdict = "B: IS 통과 가설 0 — 사전 등록 IS 기준(승률·분할 안정성) 미달"
    elif not all(checks.values()):
        verdict = f"B: {pick} IS 통과 → OOS 미달"
    elif delist and delist["pass"]:
        verdict = f"A: {pick} OOS 전수 통과 + 상폐 보정 통과"
    else:
        verdict = f"C: {pick} OOS 통과, 상폐 보정 후 미달"
    print(f"\n### 판정 — 시나리오 {verdict}", flush=True)

    summary = {
        "plan": "workspace/plans/20261108_1_architecture_redesign.md",
        "timestamp": datetime.now(timezone.utc).isoformat(), "runs": runs,
        "universe": {"pit_symbols": len(syms), "daily_load_skipped": SKIPPED}, "windows": WINDOWS,
        "events": {k: len(v) for k, v in ev.items()},
        "params": {"A1": A1, "A2": A2, "A3": A3, "A4": A4},
        "is": {h: {w: strip(res[h][w]) for w in ("IS-A", "IS-B", "IS")} |
               {"is_checks": res[h]["is_checks"], "is_pass": res[h]["is_pass"]} for h in res},
        "picked": pick, "oos": strip(oos) if oos else None, "oos_checks": checks,
        "delisting_adjusted": delist, "verdict": verdict,
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
