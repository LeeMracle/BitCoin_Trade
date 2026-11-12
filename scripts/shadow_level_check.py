#!/usr/bin/env python
"""섀도 레벨 대조 — 봇이 쓴 DC12 상단·ATR·레짐을 거래소 일봉으로 독립 재계산해 비교.

plan 20261112_1 단계 0 (ADR 20261112-1). 입력은 봇이 매일 `_refresh_levels` 직후 append 하는
`workspace/shadow_levels.jsonl` (한 줄 = 종목·날). 기준값은 **이 스크립트가 ccxt 로 직접 받은
일봉**에서 계산한다 — 봇 코드(`calc_donchian_upper`, `calc_atr`, `fetch_btc_above_ema`)를
import 하지 않는다. 같은 코드로 두 번 계산하는 것은 검증이 아니다(lessons #49).

기준값 정의 (행의 date = D, UTC 일봉 기준):
  upper_ref  = max(high)  of 완성봉 D-12 … D-1               ← 엄격 (상대오차 ≤ 1e-4)
  regime_ref = close(D-1) > EMA200(adjust=False, 최근 250 완성봉) ← 엄격 (불일치 0)
  atr_ref    = Wilder ATR14 (ewm alpha=1/14) of 완성봉 … D-1    ← **보고용** (아래 참조)
  ema_ref    = 위 EMA200 값                                    ← 보고용

ATR 을 엄격히 비교하지 않는 이유 — 봇은 UTC 00:0x 에 `fetch_ohlcv(end=D 00:00)` 로 **방금 열린
D 봉(폭 ≈ 0)까지** 받아 `atr.iloc[-1]` 을 쓴다. 그 값은 완성봉 기준보다 약 1/14 만큼 낮게 나오는
구조이고, 나중에 받은 D 일봉(하루 전체)으로는 그 시점의 부분 봉을 복원할 수 없다. 편차는 그대로
숫자로 보고하고, 수용 여부는 결과 문서에서 판단한다(백테스트 ATR 과의 차이가 단계 0 의 발견이다).

사용:
  py scripts/shadow_level_check.py                       # workspace/shadow_levels.jsonl 전체 대조
  py scripts/shadow_level_check.py --since 2026-11-13    # 날짜 이후만
  py scripts/shadow_level_check.py --selftest            # 네트워크 없이 역방향 테스트 (lessons #44)

종료 코드: 0 = 엄격 항목 불일치 0 · 결측 0,  1 = 불일치/결측 있음,  2 = 입력 없음/오류
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SHADOW_FILE = ROOT / "workspace" / "shadow_levels.jsonl"

DC_PERIOD = 12
ATR_PERIOD = 14
EMA_PERIOD = 200
EMA_WINDOW = 250          # 실거래 scanner._BTC_EMA_FETCH_LIMIT = EMA_PERIOD + 50 (cto M2)
UPPER_TOL = 1e-4          # plan §2 — 가격 레벨 상대오차
ATR_WARN = 0.02           # 보고용 임계 — 넘으면 WARN 표기만
DAY_MS = 86_400_000


# ───────────────────────── 기준값 계산 (순수 함수 — selftest 가 그대로 사용) ─────────────────────────

def reference_levels(candles: list[list], date_utc: str) -> dict:
    """candles = [[ts_ms, o, h, l, c, v], ...] 오름차순. date_utc 의 완성봉(ts < D 00:00)만 쓴다."""
    d0 = int(datetime.strptime(date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    done = [c for c in candles if int(c[0]) < d0]
    if len(done) < max(DC_PERIOD, ATR_PERIOD + 1):
        return {"ok": False, "why": f"완성봉 부족 {len(done)}"}
    df = pd.DataFrame(done, columns=["ts", "open", "high", "low", "close", "volume"]).astype(
        {"open": float, "high": float, "low": float, "close": float})
    upper_ref = float(df["high"].iloc[-DC_PERIOD:].max())
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr_ref = float(tr.ewm(alpha=1 / ATR_PERIOD, min_periods=ATR_PERIOD, adjust=False).mean().iloc[-1])
    out = {"ok": True, "upper_ref": upper_ref, "atr_ref": atr_ref,
           "last_done_ts": int(df["ts"].iloc[-1]), "n_done": int(len(df))}
    return out


def reference_regime(btc_candles: list[list], date_utc: str) -> dict:
    d0 = int(datetime.strptime(date_utc, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    done = [c for c in btc_candles if int(c[0]) < d0][-EMA_WINDOW:]
    if len(done) < EMA_PERIOD:
        return {"ok": False, "why": f"BTC 완성봉 부족 {len(done)}"}
    closes = pd.Series([float(c[4]) for c in done])
    ema = float(closes.ewm(span=EMA_PERIOD, adjust=False).mean().iloc[-1])
    close = float(closes.iloc[-1])
    return {"ok": True, "ema_ref": ema, "close_ref": close, "regime_ref": close > ema, "n": len(done)}


def compare_row(row: dict, candles: list[list], btc_candles: list[list]) -> dict:
    """한 행을 대조. 반환 dict 의 strict_fail 이 True 면 엄격 항목 불일치."""
    res = {"date": row.get("date"), "symbol": row.get("symbol"), "strict_fail": False, "notes": []}
    if row.get("level_missing") or row.get("upper") is None:
        res["strict_fail"] = True
        res["notes"].append("봇 레벨 결측(level_missing)")
        return res
    ref = reference_levels(candles, row["date"])
    if not ref["ok"]:
        res["strict_fail"] = True
        res["notes"].append(f"기준값 불가: {ref['why']}")
        return res
    up_bot = float(row["upper"])
    res["upper_bot"], res["upper_ref"] = up_bot, ref["upper_ref"]
    res["upper_rel"] = abs(up_bot - ref["upper_ref"]) / ref["upper_ref"]
    if res["upper_rel"] > UPPER_TOL:
        res["strict_fail"] = True
        res["notes"].append(f"DC12 상단 불일치 {up_bot:,.4g} vs {ref['upper_ref']:,.4g}")
    if row.get("atr") is not None:
        atr_bot = float(row["atr"])
        res["atr_bot"], res["atr_ref"] = atr_bot, ref["atr_ref"]
        res["atr_rel"] = (atr_bot - ref["atr_ref"]) / ref["atr_ref"]
        if abs(res["atr_rel"]) > ATR_WARN:
            res["notes"].append(f"ATR 편차 {res['atr_rel']:+.1%} (보고용)")
    reg = reference_regime(btc_candles, row["date"])
    if not reg["ok"]:
        res["strict_fail"] = True
        res["notes"].append(f"레짐 기준값 불가: {reg['why']}")
        return res
    res["regime_bot"], res["regime_ref"] = bool(row.get("regime_above")), bool(reg["regime_ref"])
    res["ema_ref"], res["ema_bot"] = reg["ema_ref"], row.get("btc_ema")
    if res["regime_bot"] != res["regime_ref"]:
        res["strict_fail"] = True
        res["notes"].append(f"레짐 불일치 bot={res['regime_bot']} ref={res['regime_ref']} "
                            f"(close {reg['close_ref']:,.0f} / EMA {reg['ema_ref']:,.0f})")
    return res


# ───────────────────────── 데이터 ─────────────────────────

def fetch_daily(symbol: str, n: int = 320) -> list[list]:
    """ccxt 업비트 일봉 최근 n개 (오름차순). 200개 단위로 `to` 역방향 페이징."""
    import ccxt  # 지연 import — selftest 는 네트워크 없이 돈다
    ex = ccxt.upbit({"enableRateLimit": True})
    out: list[list] = []
    to_ms = None
    while len(out) < n:
        params = {}
        if to_ms is not None:
            params["to"] = datetime.fromtimestamp(to_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        page = ex.fetch_ohlcv(symbol, "1d", limit=200, params=params)
        if not page:
            break
        page.sort(key=lambda c: c[0])
        if out and page[-1][0] >= out[0][0]:
            page = [c for c in page if c[0] < out[0][0]]
        if not page:
            break
        out = page + out
        to_ms = page[0][0]
    return out[-n:]


def load_rows(path: Path, since: str | None) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since and (r.get("date") or "") < since:
            continue
        rows.append(r)
    return rows


# ───────────────────────── 역방향 테스트 ─────────────────────────

def _synthetic_candles(n: int = 300, seed: int = 7) -> list[list]:
    import random
    rnd = random.Random(seed)
    ts0 = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    px, out = 100_000_000.0, []
    for i in range(n):
        o = px
        c = o * (1 + rnd.uniform(-0.03, 0.03))
        h = max(o, c) * (1 + rnd.uniform(0, 0.02))
        l = min(o, c) * (1 - rnd.uniform(0, 0.02))
        out.append([ts0 + i * DAY_MS, o, h, l, c, rnd.uniform(1, 10)])
        px = c
    return out


def selftest() -> int:
    """룰이 '통과'가 아니라 '실패를 잡는지' (lessons #44). 네트워크 없음."""
    cands = _synthetic_candles()
    date = datetime.fromtimestamp(cands[-1][0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")  # 마지막 봉 = D(진행 중)
    ref = reference_levels(cands, date)
    reg = reference_regime(cands, date)
    assert ref["ok"] and reg["ok"], "기준값 계산 실패"
    good = {"date": date, "symbol": "BTC/KRW", "upper": ref["upper_ref"], "atr": ref["atr_ref"],
            "regime_above": reg["regime_ref"], "level_missing": False, "btc_ema": reg["ema_ref"]}
    cases = [
        ("정상 행 → PASS", dict(good), False),
        ("상단 +0.1% → FAIL", {**good, "upper": good["upper"] * 1.001}, True),
        ("상단 D 봉 고가 포함(lookahead) → FAIL", {**good, "upper": max(good["upper"], cands[-1][2])}, True),
        ("레짐 반전 → FAIL", {**good, "regime_above": not good["regime_above"]}, True),
        ("레벨 결측 → FAIL", {**good, "upper": None, "level_missing": True}, True),
        ("ATR +10% → PASS(보고용 WARN)", {**good, "atr": good["atr"] * 1.10}, False),
    ]
    bad = 0
    for name, row, expect_fail in cases:
        r = compare_row(row, cands, cands)
        ok = r["strict_fail"] == expect_fail
        if name.startswith("상단 D 봉") and max(good["upper"], cands[-1][2]) == good["upper"]:
            ok = True  # 합성 데이터에서 D 고가가 상단 미만이면 lookahead 케이스가 성립하지 않음 — 스킵
        if name.startswith("ATR"):
            ok = ok and any("ATR 편차" in n for n in r["notes"])
        print(f"  [{'OK' if ok else 'NG'}] {name}  notes={r['notes']}")
        bad += 0 if ok else 1
    print(f"selftest: {len(cases) - bad}/{len(cases)} 통과")
    return 0 if bad == 0 else 1


# ───────────────────────── main ─────────────────────────

def _num(d: dict, key: str) -> float:
    v = d.get(key)
    return float("nan") if v is None else float(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(SHADOW_FILE))
    ap.add_argument("--since", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    path = Path(a.file)
    if not path.exists():
        print(f"입력 없음: {path}")
        return 2
    rows = load_rows(path, a.since)
    if not rows:
        print("대조할 행 없음")
        return 2

    symbols = sorted({r["symbol"] for r in rows if r.get("symbol")})
    data = {s: fetch_daily(s) for s in symbols}
    btc = data.get("BTC/KRW") or fetch_daily("BTC/KRW")

    strict, atr_warn, days = 0, 0, set()
    print(f"{'date':<10} {'symbol':<8} {'upper_bot':>14} {'upper_ref':>14} {'rel':>8} {'atr_rel':>8} {'regime':>12}  notes")
    for r in rows:
        res = compare_row(r, data.get(r.get("symbol"), []), btc)
        days.add(r.get("date"))
        strict += 1 if res["strict_fail"] else 0
        atr_warn += 1 if any("ATR 편차" in n for n in res["notes"]) else 0
        reg = f"{res.get('regime_bot')}/{res.get('regime_ref')}" if "regime_bot" in res else "-"
        # lessons #12: .get(default) 는 값이 None 이면 default 를 주지 않는다 — 숫자 포매팅 전에 정규화
        ub = _num(res, "upper_bot")
        ur = _num(res, "upper_ref")
        rel = _num(res, "upper_rel")
        arel = _num(res, "atr_rel")
        print(f"{res['date']:<10} {res['symbol']:<8} {ub:>14,.0f} {ur:>14,.0f} {rel:>8.1e} {arel:>+8.1%} {reg:>12}  "
              f"{'; '.join(res['notes'])}")
    print(f"\n행 {len(rows)} · 날 {len(days)} · 엄격 불일치(상단/레짐/결측) {strict} · ATR 편차 >{ATR_WARN:.0%} {atr_warn}")
    print("판정:", "PASS" if strict == 0 else "FAIL")
    return 0 if strict == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
