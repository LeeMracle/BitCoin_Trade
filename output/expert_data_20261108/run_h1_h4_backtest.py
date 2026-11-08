"""H1~H4 전략 재설계 백테스트 (사전 등록 plan 20261107_1).

H1: max_atr=0.05 AND confirm_close=True (B+C 복합)
H2: 전일 거래대금 상위 N=30위 이내
H3: 상장 365일 이상 AND 거래대금 상위 50위 이내
H4: 일봉 종가가 EMA20 위 AND EMA50 위

규칙:
- IS/OOS 창은 build_ctx 의 레짐 60/40 자동분할 — plan §3-1 과 같은 창
- IS 선택 규칙: 승률 ≥ 48.6% AND 거래 수 ≥ PIT 기준선 x 30% → 건당 최대 1개
- OOS 는 선택안 1개만 — 4개 성공 기준 전수 통과해야 채택

사용:
    py scripts/backtest_universe_pit.py --fetch   # 선행: 시간봉 캐시 완료 가정
    py output/expert_data_20261108/run_h1_h4_backtest.py --runs 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import backtest_stop_validation as SV  # noqa: E402
import backtest_universe_pit as PIT  # noqa: E402
from backtest_param_sweep import P, build_panel, _atr  # noqa: E402
from services.execution import config as C  # noqa: E402
from services.market_data.fetcher import fetch_ohlcv  # noqa: E402

OUT = ROOT / "output" / "expert_data_20261108"
BREAKEVEN = 43.6
IS_WR_FLOOR = 48.6        # plan §4-1
OOS_WR_FLOOR = 43.6       # plan §4-2
MIN_TRADES_FRACTION = 0.3  # plan §4-1 거래 수 >= PIT 기준선 x 30%
OOS_SHARPE_FLOOR = 0.8
OOS_MDD_FLOOR = -15.0
OOS_N_FLOOR = 60
DELIST_LOSS_PCT = -15.0   # plan §3-3 상폐 보정 가정


# ─────────────────────────── 보조: 거래대금 순위 / EMA ───────────────────────────
def compute_daily_rank(raw: dict[str, pd.DataFrame]) -> dict:
    """날짜별 전일 거래대금 (close × volume) 순위 Series 반환.
    반환: {sym: {ts: rank}} — 1위가 가장 큰 거래대금.
    """
    # 날짜별 전일 거래대금 패널 생성
    value_frame = {}
    for sym, df in raw.items():
        value_frame[sym] = (df["close"] * df["volume"]).shift(1)
    panel = pd.DataFrame(value_frame)
    # 각 행별 rank — ascending=False 로 1위가 큰 값
    ranks = panel.rank(axis=1, ascending=False, method="min")
    out = {}
    for sym in panel.columns:
        out[sym] = ranks[sym].to_dict()
    return out


def add_emas(raw: dict[str, pd.DataFrame]) -> None:
    """각 종목 df 에 ema20, ema50 열 추가 (shift(1) 로 lookahead 방지)."""
    for sym, df in raw.items():
        df["ema20"] = df["close"].shift(1).ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].shift(1).ewm(span=50, adjust=False).mean()


# ─────────────────────────── 커스텀 build_ctx ───────────────────────────
def build_ctx_h(raw: dict, regime: dict, allow, confirm_close: bool, p,
                rank_cap: int | None = None, rank_map: dict | None = None,
                need_ema_above: bool = False) -> dict:
    """SV.build_ctx 의 확장 — H2(rank), H4(ema) 조건을 신호 집합에 반영."""
    panel = build_panel(raw, regime, p)
    syms = sorted(raw)
    t0 = int(min(df.index.min() for df in raw.values()).timestamp() // 3600)
    t1 = int(max(df.index.max() for df in raw.values()).timestamp() // 3600) + 24
    T = t1 - t0
    O = np.full((len(syms), T), np.nan)
    Hh, L, Cc = O.copy(), O.copy(), O.copy()
    for i, sym in enumerate(syms):
        rows = json.loads((SV.H1 / f"KRW-{sym.split('/')[0]}.json").read_text())
        for ts, o, h, lo, c in rows:
            k = ts // 3_600_000 - t0
            if 0 <= k < T:
                O[i, k], Hh[i, k], L[i, k], Cc[i, k] = o, h, lo, c
    sigs: dict[int, list] = {}
    miss = 0
    for i, sym in enumerate(syms):
        df = panel[sym]
        for ts, row in df[df["signal"]].iterrows():
            # 기본 PIT allow (거래대금 5억 + 상장 67일)
            if allow is not None and not allow(sym, ts):
                continue
            # H2/H3: 거래대금 순위 상한
            if rank_cap is not None and rank_map is not None:
                r = rank_map.get(sym, {}).get(ts)
                if r is None or r > rank_cap:
                    continue
            # H4: 추세 게이트 (EMA20 위 AND EMA50 위)
            if need_ema_above:
                ema20 = row.get("ema20")
                ema50 = row.get("ema50")
                close_prev = row.get("close")  # 당일 종가 — 돌파 시점 비교용
                if pd.isna(ema20) or pd.isna(ema50):
                    continue
                if not (close_prev > ema20 and close_prev > ema50):
                    continue
            d = int(ts.timestamp() // 3600) - t0
            px = float(row["entry_px"])
            if confirm_close:
                hit = np.where(Cc[i, d:d + 23] >= px)[0] + 1
            else:
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
    print(f"  종목 {len(syms)} / 신호 {n_sig + miss} (시간봉 돌파 확인 {n_sig}, 미확인 {miss})")
    return {"O": O, "H": Hh, "L": L, "C": Cc, "syms": syms, "sigs": sigs,
            "is": [d for d in all_days if d <= boundary],
            "oos": [d for d in all_days if d > boundary],
            "boundary": datetime.fromtimestamp((boundary + t0) * 3600, tz=timezone.utc).date()}


# ─────────────────────────── 통계 ───────────────────────────
def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - margin) * 100, (centre + margin) * 100


def annualize_sharpe(curve: list[float]) -> float:
    """Sharpe 추정 — 일간 자본곡선 기반, rf=0, 연 365 거래일."""
    if len(curve) < 2:
        return 0.0
    eq = pd.Series(curve)
    daily_ret = eq.pct_change().dropna()
    if daily_ret.std(ddof=1) == 0:
        return 0.0
    return float(daily_ret.mean() / daily_ret.std(ddof=1) * sqrt(365))


def summarize_h(ctx: dict, p, runs: int, part: str, hard: float = C.HARD_STOP_LOSS_PCT) -> dict:
    """SV.simulate 를 runs 회 돌려 성과·안전장치 포함 지표를 집계."""
    rs = []
    for s in range(runs):
        # SV.simulate 는 안전장치 ON 상태를 받는다. plan 은 운영 config 와 동일 환경.
        r = SV.simulate(ctx, ctx[part], p, hard, True, s)
        rs.append(r)
    all_trades = [x["ret"] for r in rs for x in r["trades"]]
    wins = sum(1 for x in all_trades if x > 0)
    n = len(all_trades)
    wr = wins / n * 100 if n > 0 else 0.0
    ci_lo, ci_hi = wilson_ci(wins, n)

    # Sharpe/MDD 는 run 별 평균
    sharpes, mdds, rets, ns, tmeans = [], [], [], [], []
    for r in rs:
        # SV.simulate 의 curve 는 반환되지 않음 → 재계산 필요. 간이: MDD/수익률만 사용
        mdds.append(r["mdd"])
        rets.append(r["ret"])
        ns.append(r["n"])
        tmeans.append(r.get("tmean", 0.0) if r["trades"] else 0.0)

    # Sharpe 는 거래별 수익률 계산 (근사 — 일간 자본곡선 접근 불가)
    # fallback: 거래별 mean/std 로 거래-Sharpe 환산 x sqrt(평균 연간 거래수)
    trades_arr = np.array(all_trades) if all_trades else np.array([0.0])
    trade_sharpe = float(trades_arr.mean() / trades_arr.std(ddof=1)) if len(trades_arr) > 1 and trades_arr.std(ddof=1) > 0 else 0.0
    mean_n_per_run = float(np.mean(ns))
    days = len(ctx[part])
    if days > 0 and mean_n_per_run > 0:
        trades_per_year = mean_n_per_run * (365 / days)
        sharpe = trade_sharpe * sqrt(trades_per_year)
    else:
        sharpe = 0.0

    return {
        "ret": float(np.mean(rets)),
        "mdd": float(np.mean(mdds)),
        "n": float(np.mean(ns)),
        "n_total": n,
        "wr": wr,
        "wr_ci_lo": ci_lo,
        "wr_ci_hi": ci_hi,
        "tmean": float(np.mean(tmeans)) if tmeans else 0.0,
        "sharpe": sharpe,
        "trades": all_trades,
    }


# ─────────────────────────── 상폐 보정 ───────────────────────────
def delisting_adjust(trades: list[float], survival_rate: float = 0.85) -> dict:
    """상폐 보정 — 거래 중 15% 를 -15% 손실로 전환해 재집계 (plan §3-3).

    survival_rate = 1.0 이면 보정 없음.
    """
    if not trades:
        return {"wr": 0.0, "tmean": 0.0, "wr_ci_lo": 0.0, "wr_ci_hi": 0.0, "n": 0}
    adj = trades.copy()
    n = len(adj)
    # 임의 선택이 아니라 "수익 거래의 15% 중 상위를 -15% 로 치환" → 가장 보수적 방향
    # plan 은 "-15% 손실 가정으로 상폐 종목 비율 삽입" — 전체 거래의 (1 - survival_rate) 비율을 -15% 로
    k = int(round(n * (1 - survival_rate)))
    if k > 0:
        # 수익률 내림차순으로 정렬 후 상위 k 개를 -15% 로 치환 (비관 가정)
        sorted_desc = sorted(range(n), key=lambda i: -adj[i])
        for i in sorted_desc[:k]:
            adj[i] = DELIST_LOSS_PCT
    wins = sum(1 for x in adj if x > 0)
    wr = wins / len(adj) * 100
    ci_lo, ci_hi = wilson_ci(wins, len(adj))
    return {"wr": wr, "tmean": float(np.mean(adj)), "wr_ci_lo": ci_lo, "wr_ci_hi": ci_hi,
            "n": len(adj), "trades_adj": adj}


# ─────────────────────────── 메인 ───────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--skip-fetch-check", action="store_true", default=False)
    args = ap.parse_args()

    # 1) 일봉 로드
    print(f"[{datetime.now(timezone.utc).isoformat()}] 일봉 로드 시작", flush=True)
    raw, regime, meta = asyncio.run(PIT.load_all_daily())
    print(f"일봉 로드 {len(raw)}종목", flush=True)

    # 1-1) EMA 사전 계산 (H4)
    add_emas(raw)

    # 2) PIT 신호 종목 결정 + 시간봉 캐시 확인
    sig_syms = PIT.signal_symbols(raw, regime, meta)
    print(f"PIT 종목군 (신호 발생) {len(sig_syms)}종목", flush=True)
    has_h1 = {s for s in raw if (SV.H1 / f"KRW-{s.split('/')[0]}.json").exists()}
    missing = [s for s in sig_syms if s not in has_h1]
    if missing and not args.skip_fetch_check:
        print(f"[중단] 시간봉 미수집 {len(missing)}종목: {missing[:5]}...", flush=True)
        print("먼저 `py scripts/backtest_universe_pit.py --fetch` 를 실행하세요.", flush=True)
        return 1

    # 3) 거래대금 순위 계산 (H2·H3)
    print("거래대금 순위 계산 중...", flush=True)
    rank_map = compute_daily_rank(raw)

    # 4) 기준 — PIT 기준선 (plan §4-1 거래수 기준선)
    pit_raw = {s: raw[s] for s in sig_syms if s in has_h1}
    allow_pit = PIT.make_allow(meta, PIT.MIN_AGE_DAYS)
    allow_h3 = PIT.make_allow(meta, 365)

    print("\n[PIT 기준선 재측정]", flush=True)
    ctx_base = build_ctx_h(pit_raw, regime, allow_pit, False, P())
    base_is = summarize_h(ctx_base, P(), args.runs, "is")
    base_oos = summarize_h(ctx_base, P(), args.runs, "oos")
    boundary_date = ctx_base["boundary"]
    is_days = len(ctx_base["is"])
    oos_days = len(ctx_base["oos"])
    print(f"  경계일 = {boundary_date} (IS {is_days}일 / OOS {oos_days}일)", flush=True)
    print(f"  IS  승률 {base_is['wr']:.1f}% 거래 {base_is['n']:.0f} 건당 {base_is['tmean']:+.2f}%", flush=True)
    print(f"  OOS 승률 {base_oos['wr']:.1f}% 거래 {base_oos['n']:.0f} 건당 {base_oos['tmean']:+.2f}%", flush=True)

    base_n = base_is["n"]
    min_trades = base_n * MIN_TRADES_FRACTION
    print(f"  IS 거래수 하한(30%) = {min_trades:.0f}건", flush=True)

    # 5) H1~H4 IS 실행
    hypotheses = [
        ("H1", "max_atr=0.05 AND confirm_close=True", {
            "raw": pit_raw, "allow": allow_pit, "confirm": True, "p": P(max_atr=0.05),
            "rank_cap": None, "need_ema": False,
        }),
        ("H2", "거래대금 상위 30위", {
            "raw": pit_raw, "allow": allow_pit, "confirm": False, "p": P(),
            "rank_cap": 30, "need_ema": False,
        }),
        ("H3", "상장 365일 이상 AND 거래대금 상위 50위", {
            "raw": pit_raw, "allow": allow_h3, "confirm": False, "p": P(),
            "rank_cap": 50, "need_ema": False,
        }),
        ("H4", "일봉 종가가 EMA20 위 AND EMA50 위", {
            "raw": pit_raw, "allow": allow_pit, "confirm": False, "p": P(),
            "rank_cap": None, "need_ema": True,
        }),
    ]

    is_results = {}
    print("\n[IS 실행 — H1~H4]", flush=True)
    print("| 가설 | 조건 | IS n | IS 승률 | IS CI | IS 건당 | IS Sharpe | IS MDD | 통과 |")
    print("| --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |")
    for hname, desc, cfg in hypotheses:
        print(f"\n> {hname}: {desc}", flush=True)
        ctx = build_ctx_h(cfg["raw"], regime, cfg["allow"], cfg["confirm"], cfg["p"],
                         rank_cap=cfg["rank_cap"], rank_map=rank_map if cfg["rank_cap"] else None,
                         need_ema_above=cfg["need_ema"])
        is_r = summarize_h(ctx, cfg["p"], args.runs, "is")
        # IS 선택 규칙 (plan §4-1): 승률 >= 48.6% AND 거래수 >= 기준선 x 30%
        pass_wr = is_r["wr"] >= IS_WR_FLOOR
        pass_n = is_r["n"] >= min_trades
        passes = pass_wr and pass_n
        is_results[hname] = {
            "desc": desc, "cfg": cfg, "ctx": ctx, "is": is_r,
            "pass_wr": pass_wr, "pass_n": pass_n, "passes": passes,
        }
        print(f"| {hname} | {desc[:28]}… | {is_r['n']:.0f} | {is_r['wr']:.1f}% "
              f"| [{is_r['wr_ci_lo']:.1f}, {is_r['wr_ci_hi']:.1f}] "
              f"| {is_r['tmean']:+.2f}% | {is_r['sharpe']:.2f} | {is_r['mdd']:.1f}% "
              f"| {'PASS' if passes else 'FAIL'}({'wr' if not pass_wr else ''}{'n' if not pass_n else ''}) |")

    # 6) IS 선택 (건당 최대)
    passing = [h for h, r in is_results.items() if r["passes"]]
    print(f"\n[IS 선택] 통과 가설: {passing or '없음'}")
    if not passing:
        print("\n### 시나리오 B: IS 선택 실패 — 어떤 가설도 통과 조건 미달")
        print("판정: 현행 DC12+ATR3 아키텍처는 PIT 모집단에서 작동 불가")
        print("OOS 검증 생략 (plan §5-2)")

        # 결과 JSON 저장
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "boundary_date": str(boundary_date),
            "is_days": is_days, "oos_days": oos_days,
            "base_is": {k: v for k, v in base_is.items() if k != "trades"},
            "base_oos": {k: v for k, v in base_oos.items() if k != "trades"},
            "hypotheses": {h: {
                "desc": r["desc"],
                "is": {k: v for k, v in r["is"].items() if k != "trades"},
                "passes": r["passes"],
                "pass_wr": r["pass_wr"], "pass_n": r["pass_n"],
            } for h, r in is_results.items()},
            "verdict": "IS 선택 실패 (시나리오 B)",
        }
        (OUT / "results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        print(f"\n결과 저장: {OUT / 'results.json'}")
        return 0

    pick = max(passing, key=lambda h: is_results[h]["is"]["tmean"])
    print(f"\n[IS 선택안] {pick} (IS 건당 {is_results[pick]['is']['tmean']:+.2f}% 최대)")

    # 7) OOS 1회 측정
    print(f"\n[OOS 실행 — {pick} 만]")
    pick_cfg = is_results[pick]["cfg"]
    ctx_pick = is_results[pick]["ctx"]
    oos_r = summarize_h(ctx_pick, pick_cfg["p"], args.runs, "oos")

    # 8) 성공 기준 전수 통과?
    oos_checks = {
        "Sharpe>=0.8": oos_r["sharpe"] >= OOS_SHARPE_FLOOR,
        "MDD>=-15%": oos_r["mdd"] >= OOS_MDD_FLOOR,
        "WR_CI_lo>43.6%": oos_r["wr_ci_lo"] > OOS_WR_FLOOR,
        "tmean>0": oos_r["tmean"] > 0,
        "n>=60": oos_r["n_total"] >= OOS_N_FLOOR,
    }
    print(f"| 기준 | 값 | 통과? |")
    print(f"| --- | ---: | --- |")
    print(f"| Sharpe ≥ 0.8 | {oos_r['sharpe']:.2f} | {'PASS' if oos_checks['Sharpe>=0.8'] else 'FAIL'} |")
    print(f"| MDD ≥ -15% | {oos_r['mdd']:.1f}% | {'PASS' if oos_checks['MDD>=-15%'] else 'FAIL'} |")
    print(f"| 승률 CI 하한 > 43.6% | {oos_r['wr_ci_lo']:.1f}% | {'PASS' if oos_checks['WR_CI_lo>43.6%'] else 'FAIL'} |")
    print(f"| 건당 기대값 > 0 | {oos_r['tmean']:+.2f}% | {'PASS' if oos_checks['tmean>0'] else 'FAIL'} |")
    print(f"| 거래 수 ≥ 60 | {oos_r['n_total']} | {'PASS' if oos_checks['n>=60'] else 'FAIL'} |")

    all_pass = all(oos_checks.values())
    print(f"\n[OOS 판정] {'전수 통과' if all_pass else '미달'}")

    # 9) 상폐 보정 재평가 (OOS 통과 시만)
    delist_result = None
    if all_pass:
        print("\n[상폐 보정 재계산] 생존율 85% (상폐 비율 15% 가정, -15% 손실)")
        delist_result = delisting_adjust(oos_r["trades"], survival_rate=0.85)
        print(f"| 기준 | 보정 전 | 보정 후 | 보정 후 통과? |")
        print(f"| --- | ---: | ---: | --- |")
        d_tmean_pass = delist_result["tmean"] > 0
        d_wr_ci_lo_pass = delist_result["wr_ci_lo"] > OOS_WR_FLOOR
        print(f"| 승률 | {oos_r['wr']:.1f}% | {delist_result['wr']:.1f}% | - |")
        print(f"| CI 하한 | {oos_r['wr_ci_lo']:.1f}% | {delist_result['wr_ci_lo']:.1f}% "
              f"| {'PASS' if d_wr_ci_lo_pass else 'FAIL'} |")
        print(f"| 건당 | {oos_r['tmean']:+.2f}% | {delist_result['tmean']:+.2f}% "
              f"| {'PASS' if d_tmean_pass else 'FAIL'} |")

        delist_pass = d_tmean_pass and d_wr_ci_lo_pass
        verdict = "시나리오 A: 전수 통과 + 상폐 보정도 통과" if delist_pass else "시나리오 C: 보정 전 통과했으나 보정 후 미달"
    else:
        verdict = "시나리오 B: OOS 성공 기준 미달"

    print(f"\n### 최종 판정: {verdict}")

    # 10) 결과 저장
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "boundary_date": str(boundary_date),
        "is_days": is_days, "oos_days": oos_days,
        "base_is": {k: v for k, v in base_is.items() if k != "trades"},
        "base_oos": {k: v for k, v in base_oos.items() if k != "trades"},
        "hypotheses": {h: {
            "desc": r["desc"],
            "is": {k: v for k, v in r["is"].items() if k != "trades"},
            "passes": r["passes"],
            "pass_wr": r["pass_wr"], "pass_n": r["pass_n"],
        } for h, r in is_results.items()},
        "picked": pick,
        "oos": {k: v for k, v in oos_r.items() if k != "trades"},
        "oos_checks": oos_checks,
        "oos_pass": all_pass,
        "delisting_adjusted": {k: v for k, v in (delist_result or {}).items() if k != "trades_adj"} if delist_result else None,
        "verdict": verdict,
    }
    (OUT / "results.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    # 거래별 raw 도 별도 CSV (선택안 OOS 만)
    if oos_r["trades"]:
        pd.Series(oos_r["trades"], name="ret_pct").to_csv(OUT / f"oos_{pick}_trades.csv", index=False)
    print(f"\n결과 저장:")
    print(f"  {OUT / 'results.json'}")
    if oos_r["trades"]:
        print(f"  {OUT / f'oos_{pick}_trades.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
