"""시점 일치 종목군 백테스트 — 실거래와 같은 규칙으로 날짜별 종목을 고른다 (plan 20261103_1).

backtest_stop_validation 은 "현재 거래대금 상위 120 중 시간봉 캐시가 있는 40종목"으로 700일을 재생했다.
실거래 봇은 매일 거래대금 5억 이상 · 일봉 67개 이상인 종목을 새로 고른다. 그 결과 실거래 신규 45건 중
41건이 백테스트 종목군 밖이었다 (research/20261103_1). 이 스크립트는 그 날의 종목군으로 신호를 거른다.

    py scripts/backtest_universe_pit.py --fetch     # 전 KRW 종목 일봉 + 신호 종목 시간봉 수집(캐시)
    py scripts/backtest_universe_pit.py --runs 10   # 기준선 + 후보 A·B·C (plan §3 사전 고정 규칙)

한계: 상장폐지 종목은 업비트 API 로 받을 수 없다 → 생존편향(낙관). 결과는 상한으로 읽는다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import backtest_stop_validation as SV  # noqa: E402
from backtest_param_sweep import P, build_panel  # noqa: E402
from services.execution import config as C  # noqa: E402
from services.market_data.fetcher import fetch_ohlcv  # noqa: E402

# 실거래 편입 기준 = realtime_monitor 레벨 갱신의 min_bars (DC + 추세 50 + 5 = 67봉).
# scanner.MIN_LISTING_DAYS(22)는 실시간 경로에서 쓰이지 않는다 — plan 20261103_1 §5 정정
MIN_AGE_DAYS = C.STRATEGY_KWARGS.get("dc_period", C.DONCHIAN_PERIOD) + 50 + 5
AGE_A = 180                                # 후보 A — plan §3 사전 고정
BREAKEVEN = 43.6
MARGIN = 5.0
EXCLUDE = {"USDT", "USDC", "DAI", "BUSD"}
LEGACY = ROOT / "output" / "h1_legacy_universe.json"   # 기존 40종목 목록 스냅샷


def all_krw_symbols() -> list[str]:
    req = urllib.request.Request("https://api.upbit.com/v1/market/all",
                                 headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        rows = json.loads(r.read())
    return sorted(f"{m['market'][4:]}/KRW" for m in rows
                  if m["market"].startswith("KRW-") and m["market"][4:] not in EXCLUDE)


async def load_all_daily() -> tuple[dict, dict, dict]:
    """raw(최근 DAYS 일) · regime · meta{sym: (첫 일봉 ts, 전일 거래대금 Series)}."""
    now = datetime.now(tz=timezone.utc)
    s = (now - timedelta(days=SV.DAYS + 260)).strftime("%Y-%m-%dT00:00:00Z")
    e = now.strftime("%Y-%m-%dT00:00:00Z")
    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", s, e, use_cache=True))
    btc["ema"] = btc["close"].ewm(span=C.REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc["ema"]))
    raw, meta = {}, {}
    syms = all_krw_symbols()
    for i, sym in enumerate(syms, 1):
        try:
            df = pd.DataFrame(await fetch_ohlcv(sym, "1d", s, e, use_cache=True))
        except Exception as ex:  # noqa: BLE001 — 개별 종목 실패는 건너뛴다
            print(f"  skip {sym}: {type(ex).__name__}", flush=True)
            continue
        if len(df) < MIN_AGE_DAYS:
            continue
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        first_ts = int(df["ts"].iloc[0])
        prev_value = (df["close"] * df["volume"]).shift(1)
        df = df.tail(SV.DAYS)
        raw[sym] = df
        meta[sym] = (first_ts, prev_value)
        if i % 25 == 0:
            print(f"  일봉 {i}/{len(syms)}", flush=True)
    return raw, regime, meta


def make_allow(meta: dict, min_age: int):
    day_ms = 86_400_000

    def allow(sym: str, ts: pd.Timestamp) -> bool:
        first_ts, prev_value = meta[sym]
        t_ms = int(ts.timestamp() * 1000)
        if (t_ms - first_ts) // day_ms < min_age:
            return False
        v = prev_value.get(ts)
        return v is not None and not np.isnan(v) and v >= C.MIN_VOLUME_KRW
    return allow


def signal_symbols(raw: dict, regime: dict, meta: dict) -> list[str]:
    panel = build_panel(raw, regime, P())
    allow = make_allow(meta, MIN_AGE_DAYS)
    out = []
    for sym, df in panel.items():
        if any(allow(sym, ts) for ts in df.index[df["signal"]]):
            out.append(sym)
    return sorted(out)


def summarize(ctx: dict, p: P, runs: int, part: str) -> dict:
    rs = [SV.simulate(ctx, ctx[part], p, C.HARD_STOP_LOSS_PCT, True, s) for s in range(runs)]
    tr = [x["ret"] for r in rs for x in r["trades"]]
    return {"ret": float(np.mean([r["ret"] for r in rs])),
            "mdd": float(np.mean([r["mdd"] for r in rs])),
            "n": float(np.mean([r["n"] for r in rs])),
            "wr": float(np.mean([x > 0 for x in tr]) * 100) if tr else 0.0,
            "tmean": float(np.mean(tr)) if tr else 0.0}


def row(label: str, part: str, m: dict) -> str:
    return (f"| {label} | {part.upper()} | {m['ret']:+.1f}% | {m['mdd']:.1f}% | {m['n']:.0f} "
            f"| {m['wr']:.1f}% | {m['tmean']:+.2f}% |")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()

    if not LEGACY.exists():
        legacy = sorted(f"{f.stem[4:]}/KRW" for f in SV.H1.glob("KRW-*.json"))
        LEGACY.write_text(json.dumps(legacy))
    legacy = set(json.loads(LEGACY.read_text()))

    raw, regime, meta = asyncio.run(load_all_daily())
    print(f"일봉 로드 {len(raw)}종목", flush=True)
    sig_syms = signal_symbols(raw, regime, meta)
    print(f"PIT 종목군에서 신호가 난 종목 {len(sig_syms)}", flush=True)

    if args.fetch:
        start = min(df.index.min() for df in raw.values()).to_pydatetime()
        end = datetime.now(tz=timezone.utc)
        todo = [s for s in sig_syms if not (SV.H1 / f"KRW-{s.split('/')[0]}.json").exists()]
        print(f"시간봉 수집 대상 {len(todo)} (캐시 {len(sig_syms) - len(todo)})", flush=True)
        for i, sym in enumerate(todo, 1):
            rows = SV.fetch_hourly(sym, start, end)
            print(f"  {i:>3}/{len(todo)} {sym:<14} {len(rows):>6}봉", flush=True)
        return 0

    has_h1 = {s for s in raw if (SV.H1 / f"KRW-{s.split('/')[0]}.json").exists()}
    missing = [s for s in sig_syms if s not in has_h1]
    if missing:
        print(f"[중단] 시간봉 미수집 {len(missing)}종목 — --fetch 먼저", flush=True)
        return 1

    old_raw = {s: raw[s] for s in raw if s in legacy and s in has_h1}
    pit_raw = {s: raw[s] for s in sig_syms}
    allow_pit = make_allow(meta, MIN_AGE_DAYS)
    allow_a = make_allow(meta, AGE_A)

    print("\n| 종목군·정책 | 구간 | 수익 | MDD | 거래 | 승률 | 건당 |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    res = {}
    configs = [
        ("OLD40 기존", old_raw, None, False, P()),
        ("PIT 기준선", pit_raw, allow_pit, False, P()),
        ("A 상장180일+", pit_raw, allow_a, False, P()),
        ("B ATR%≤5", pit_raw, allow_pit, False, P(max_atr=0.05)),
        ("C 종가확인", pit_raw, allow_pit, True, P()),
    ]
    for label, rw, allow, conf, p in configs:
        ctx = SV.build_ctx(rw, regime, allow=allow, confirm_close=conf, p=p)
        res[label] = {}
        for part in ("is", "oos"):
            # OOS 는 기준선 두 줄과 plan §3 의 선택안에만 쓴다 — 후보는 IS 만 먼저 출력
            if part == "oos" and label[0] in "ABC":
                continue
            m = summarize(ctx, p, args.runs, part)
            res[label][part] = m
            print(row(label, part, m), flush=True)
        res[label]["ctx"] = (ctx, p)

    base = res["PIT 기준선"]["is"]
    ok = [k for k in ("A 상장180일+", "B ATR%≤5", "C 종가확인")
          if res[k]["is"]["wr"] >= BREAKEVEN + MARGIN and res[k]["is"]["n"] >= base["n"] * 0.3]
    print(f"\nIS 선택 규칙 통과: {ok or '없음'}")
    if not ok:
        print("판정: 개선안 없음 (plan §3)")
        return 0
    pick = max(ok, key=lambda k: res[k]["is"]["tmean"])
    ctx, p = res[pick]["ctx"]
    m = summarize(ctx, p, args.runs, "oos")
    print(row(pick, "oos", m))
    b_oos = res["PIT 기준선"]["oos"]
    adopt = m["tmean"] > b_oos["tmean"] and m["wr"] >= BREAKEVEN
    print(f"선택: {pick} → OOS 채택 {'예' if adopt else '아니오'} "
          f"(건당 {m['tmean']:+.2f}% vs 기준선 {b_oos['tmean']:+.2f}%, 승률 {m['wr']:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
