"""하드손절 폭 검증 — 일봉 신호 + 시간봉 경로 + 안전장치 모델링 (plan 20260917_2).

backtest_param_sweep 의 한계를 제거한다:
  (1) 일봉 안 순서 불명 → 손절 우선 가정(좁은 손절에 불리)   → 60분봉 시간순 처리
  (2) 5연패 72h 쿨다운 · 일일손실 · 서킷 L1 미모델링          → 라이브 로직대로 재현
  (3) SL 3점(10/7/5)                                          → 4/5/6/7/10 곡선
  (4) 종목 변동성 무시                                        → 진입 ATR% 구간별 분해

    py scripts/backtest_stop_validation.py --fetch      # 시간봉 수집(캐시)
    py scripts/backtest_stop_validation.py --runs 10    # 시뮬레이션
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_param_sweep import P, build_panel  # noqa: E402
from services.execution import config as C  # noqa: E402
from services.execution.scanner import get_krw_market_coins  # noqa: E402
from services.market_data.fetcher import fetch_ohlcv  # noqa: E402

H1 = ROOT / "output" / "h1"
FEE = C.FEE_RATE
INIT = float(C.CIRCUIT_BREAKER_INITIAL_CAPITAL)
STOP_SLIP = 0.0015  # 실거래 하드손절 실측 (replay_live_stop_levels)
DAYS = 700


# ─────────────────────────── 데이터 ───────────────────────────
async def load_daily() -> tuple[dict, dict]:
    now = datetime.now(tz=timezone.utc)
    s = (now - timedelta(days=DAYS + 260)).strftime("%Y-%m-%dT00:00:00Z")
    e = now.strftime("%Y-%m-%dT00:00:00Z")
    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", s, e, use_cache=True))
    btc["ema"] = btc["close"].ewm(span=C.REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc["ema"]))
    raw = {}
    for c in get_krw_market_coins()[:120]:
        try:
            df = pd.DataFrame(await fetch_ohlcv(c["symbol"], "1d", s, e, use_cache=True))
        except Exception:
            continue
        if len(df) < 60:
            continue
        df = df.tail(DAYS).reset_index(drop=True)
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        raw[c["symbol"]] = df
    return raw, regime


def fetch_hourly(symbol: str, start: datetime, end: datetime) -> list[list[float]]:
    """[ts_ms, o, h, l, c] 오름차순. 파일 캐시."""
    market = "KRW-" + symbol.split("/")[0]
    path = H1 / f"{market}.json"
    if path.exists():
        return json.loads(path.read_text())
    out: dict[int, list[float]] = {}
    to = end
    while to > start:
        url = (f"https://api.upbit.com/v1/candles/minutes/60?market={market}&count=200"
               f"&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        rows = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(urllib.request.Request(
                        url, headers={"accept": "application/json"}), timeout=15) as r:
                    rows = json.loads(r.read())
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.11)
        if not rows:
            break
        for r in rows:
            ts = int(datetime.fromisoformat(r["candle_date_time_utc"]).replace(
                tzinfo=timezone.utc).timestamp() * 1000)
            out[ts] = [ts, r["opening_price"], r["high_price"], r["low_price"], r["trade_price"]]
        oldest = min(out) / 1000
        nxt = datetime.fromtimestamp(oldest, tz=timezone.utc)
        if nxt >= to:
            break
        to = nxt
    rows = [out[k] for k in sorted(out)]
    H1.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


# ─────────────────────────── 시뮬레이션 ───────────────────────────
class Guards:
    """라이브 안전장치 재현 (realtime_monitor 09-17 수정판 기준)."""

    def __init__(self, on: bool):
        self.on = on
        self.streak = 0
        self.last_loss_id = None
        self.cd_until = -1
        self.cd_for = None
        self.day = None
        self.day_pl = 0.0
        self.day_blocked = False
        self.cb = False
        self.n_cd = 0
        self.cd_hours = 0

    def on_close(self, t: int, ret: float, pl_krw: float, trade_id: int) -> None:
        if ret <= 0:
            self.streak += 1
            self.last_loss_id = trade_id
        else:
            self.streak = 0
        self.day_pl += pl_krw
        if not self.on:
            return
        if C.DAILY_LOSS_LIMIT_ENABLED and self.day_pl <= -C.DAILY_LOSS_LIMIT_PCT * C.DAILY_LOSS_BASE_KRW:
            self.day_blocked = True
        # 매도 즉시 경로
        if self.streak >= C.CONSEC_LOSS_LIMIT and t >= self.cd_until:
            self._impose(t)

    def _impose(self, t: int) -> None:
        self.cd_until = t + C.CONSEC_LOSS_COOLDOWN_HOURS
        self.cd_for = self.last_loss_id
        self.n_cd += 1

    def tick(self, t: int, day: int, equity: float) -> None:
        if day != self.day:
            self.day, self.day_pl, self.day_blocked = day, 0.0, False
        if not self.on:
            return
        # 주기 경로: 만료 시 같은 연패면 해제(floor), 새 손실이 얹혔으면 재부과 (ADR 20260829-1)
        if self.cd_for is not None and t >= self.cd_until:
            if self.streak >= C.CONSEC_LOSS_LIMIT and self.last_loss_id != self.cd_for:
                self._impose(t)
            else:
                self.streak, self.cd_for = 0, None
        if t < self.cd_until:
            self.cd_hours += 1
        if C.CIRCUIT_BREAKER_ENABLED:
            if not self.cb and equity <= INIT * (1 + C.CIRCUIT_BREAKER_THRESHOLD):
                self.cb = True
            elif self.cb and equity >= INIT * C.CIRCUIT_BREAKER_L1_AUTO_RESUME_PCT:
                self.cb = False

    def can_buy(self, t: int) -> bool:
        return not self.on or (t >= self.cd_until and not self.day_blocked and not self.cb)


def simulate(ctx: dict, days: list[int], p: P, hard: float, guards_on: bool, seed: int) -> dict:
    O, Hh, L, Cc = ctx["O"], ctx["H"], ctx["L"], ctx["C"]
    syms, sigs = ctx["syms"], ctx["sigs"]
    rng = np.random.default_rng(seed)
    g = Guards(guards_on)
    cash, pos = INIT, {}
    last_px = {}
    curve, trades = [], []
    tid = 0

    def close(si: int, q: dict, px: float, t: int) -> None:
        nonlocal cash, tid
        pr = q["qty"] * px * (1 - FEE)
        cash += pr
        q["proceeds"] += pr
        ret = q["proceeds"] / q["cost"] - 1
        tid += 1
        trades.append({"ret": ret * 100, "atrp": q["atrp"]})
        g.on_close(t, ret, q["proceeds"] - q["cost"], tid)
        del pos[si]

    for d in days:
        todays = sigs.get(d, [])
        by_hour: dict[int, list] = {}
        for s in todays:
            by_hour.setdefault(s["t"], []).append(s)
        for t in range(d, d + 24):
            held = sum(q["qty"] * last_px.get(si, q["entry"]) for si, q in pos.items())
            g.tick(t, d, cash + held)
            # 1) 보유 포지션: 손절 우선 → 익절 → 고점·트레일 갱신
            for si in list(pos):
                q = pos[si]
                o, h, lo, c = O[si, t], Hh[si, t], L[si, t], Cc[si, t]
                if np.isnan(c):
                    continue
                last_px[si] = c
                e = q["entry"]
                if lo <= q["trail"]:
                    close(si, q, min(o, q["trail"]) * (1 - STOP_SLIP), t)
                    continue
                for k, tp in enumerate(p.tp):
                    if k in q["tp_done"] or h < e * (1 + tp["trigger_pct"]):
                        continue
                    sq = q["qty"] * tp["sell_ratio"] if k < len(p.tp) - 1 else q["qty"]
                    pr = sq * e * (1 + tp["trigger_pct"]) * (1 - FEE)
                    cash += pr
                    q["proceeds"] += pr
                    q["qty"] -= sq
                    q["tp_done"].add(k)
                if q["qty"] <= 1e-12:
                    q["qty"] = 0.0
                    close(si, q, 0.0, t)
                    continue
                if h > q["highest"]:
                    q["highest"] = h
                    q["trail"] = max(q["trail"], h - q["atr"] * p.atr_mult, e * (1 - hard))
            # 2) 이 시간에 돌파한 신호 진입
            cands = [s for s in by_hour.get(t, []) if s["si"] not in pos]
            rng.shuffle(cands)
            for s in cands:
                if len(pos) >= p.slots or not g.can_buy(t):
                    break
                si = s["si"]
                held = sum(q["qty"] * last_px.get(k, q["entry"]) for k, q in pos.items())
                equity = cash + held
                amt = cash * C.POSITION_RATIO / max(p.slots - len(pos), 1)
                amt = min(amt, equity * p.weight, cash * C.POSITION_RATIO)
                if amt < C.MIN_ORDER_KRW:
                    continue
                e = max(s["entry_px"], O[si, t])
                a = s["atr"]
                cash -= amt
                q = {"entry": e, "qty": amt * (1 - FEE) / e, "atr": a, "highest": e,
                     "trail": max(e - a * p.atr_mult, e * (1 - hard)), "tp_done": set(),
                     "cost": amt, "proceeds": 0.0, "atrp": a / e}
                pos[si] = q
                last_px[si] = Cc[si, t]
                # 진입 봉: 내부 순서 불명 — 종가가 선 너머일 때만 확정 처리 (정책 공통)
                if Cc[si, t] <= q["trail"]:
                    close(si, q, Cc[si, t] * (1 - STOP_SLIP), t)
        held = sum(q["qty"] * last_px.get(si, q["entry"]) for si, q in pos.items())
        curve.append(cash + held)

    eq = pd.Series(curve) if curve else pd.Series([INIT])
    mdd = float(((eq - eq.cummax()) / eq.cummax()).min() * 100)
    r = np.array([x["ret"] for x in trades]) if trades else np.array([0.0])
    return {"ret": (eq.iloc[-1] / INIT - 1) * 100, "mdd": mdd, "n": len(trades),
            "wr": float((r > 0).mean() * 100) if trades else 0.0, "tmean": float(r.mean()),
            "n_cd": g.n_cd, "cd_h": g.cd_hours, "trades": trades}


def build_ctx(raw: dict, regime: dict) -> dict:
    p = P()
    panel = build_panel(raw, regime, p)
    syms = sorted(raw)
    t0 = int(min(df.index.min() for df in raw.values()).timestamp() // 3600)
    t1 = int(max(df.index.max() for df in raw.values()).timestamp() // 3600) + 24
    T = t1 - t0
    O = np.full((len(syms), T), np.nan)
    Hh, L, Cc = O.copy(), O.copy(), O.copy()
    for i, sym in enumerate(syms):
        rows = json.loads((H1 / f"KRW-{sym.split('/')[0]}.json").read_text())
        for ts, o, h, lo, c in rows:
            k = ts // 3_600_000 - t0
            if 0 <= k < T:
                O[i, k], Hh[i, k], L[i, k], Cc[i, k] = o, h, lo, c
    sigs: dict[int, list] = {}
    miss = 0
    for i, sym in enumerate(syms):
        df = panel[sym]
        for ts, row in df[df["signal"]].iterrows():
            d = int(ts.timestamp() // 3600) - t0
            px = float(row["entry_px"])
            hit = np.where(Hh[i, d:d + 24] >= px)[0]
            if len(hit) == 0:
                miss += 1
                continue
            sigs.setdefault(d, []).append({"si": i, "t": d + int(hit[0]), "entry_px": px,
                                           "atr": float(row["atr"])})
    all_days = sorted({int(ts.timestamp() // 3600) - t0 for df in raw.values() for ts in df.index})
    open_days = sorted(d for d in all_days
                       if regime.get((d + t0) * 3_600_000, False))
    boundary = open_days[int(len(open_days) * 0.6)]
    n_sig = sum(len(v) for v in sigs.values())
    print(f"종목 {len(syms)} / 신호 {n_sig + miss} (시간봉 돌파 확인 {n_sig}, 미확인 {miss})")
    return {"O": O, "H": Hh, "L": L, "C": Cc, "syms": syms, "sigs": sigs,
            "is": [d for d in all_days if d <= boundary],
            "oos": [d for d in all_days if d > boundary],
            "boundary": datetime.fromtimestamp((boundary + t0) * 3600, tz=timezone.utc).date()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()

    raw, regime = asyncio.run(load_daily())
    if len(raw) < 40:
        print(f"[중단] 일봉 로드 {len(raw)}종목 < 40 — DuckDB 캐시 잠금 확인")
        return 1
    if args.fetch:
        start = min(df.index.min() for df in raw.values()).to_pydatetime()
        end = datetime.now(tz=timezone.utc)
        for i, sym in enumerate(sorted(raw), 1):
            rows = fetch_hourly(sym, start, end)
            print(f"  {i:>2}/{len(raw)} {sym:<14} {len(rows):>6}봉", flush=True)
        return 0

    ctx = build_ctx(raw, regime)
    print(f"IS ~{ctx['boundary']} {len(ctx['is'])}일 / OOS {len(ctx['oos'])}일 · 무작위 {args.runs}회\n")
    p = P()
    policies = [("SL10 현행", 0.10, True), ("SL7", 0.07, True), ("SL6", 0.06, True),
                ("SL5", 0.05, True), ("SL4", 0.04, True),
                ("SL10 안전장치OFF(보정)", 0.10, False), ("SL5 안전장치OFF", 0.05, False)]
    hdr = (f"{'정책':<22}{'IS':>8}{'OOS':>8}{'OOS MDD':>9}{'거래':>6}{'승률':>7}{'건당':>8}"
           f"{'쿨다운':>7}{'차단h':>7}")
    print(hdr)
    detail = {}
    for label, hard, gon in policies:
        res = {}
        for part in ("is", "oos"):
            rs = [simulate(ctx, ctx[part], p, hard, gon, s) for s in range(args.runs)]
            res[part] = rs
        m = lambda part, k: float(np.mean([x[k] for x in res[part]]))  # noqa: E731
        print(f"{label:<22}{m('is','ret'):>7.1f}%{m('oos','ret'):>7.1f}%{m('oos','mdd'):>8.1f}%"
              f"{m('oos','n'):>6.0f}{m('oos','wr'):>6.1f}%{m('oos','tmean'):>7.2f}%"
              f"{m('oos','n_cd'):>7.1f}{m('oos','cd_h'):>7.0f}", flush=True)
        detail[label] = res

    print("\n[변동성 구간별 건당 수익 — IS+OOS 전체 거래, 안전장치 ON]")
    print(f"{'정책':<12}" + "".join(f"{b:>18}" for b in ("ATR% <3", "3~5", "≥5")))
    for label in ("SL10 현행", "SL7", "SL5"):
        tr = [x for part in ("is", "oos") for r in detail[label][part] for x in r["trades"]]
        cells = []
        for lo, hi in ((0, 0.03), (0.03, 0.05), (0.05, 1)):
            b = [x["ret"] for x in tr if lo <= x["atrp"] < hi]
            cells.append(f"{np.mean(b):+.2f}% (n={len(b)//args.runs})" if b else "-")
        print(f"{label:<12}" + "".join(f"{c:>18}" for c in cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
