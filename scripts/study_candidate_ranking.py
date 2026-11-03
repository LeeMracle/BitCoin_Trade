"""진입 후보 선택 규칙 연구 (plan 20261005_5, ADR 20260823-2 미해결 항목).

사전 등록: 가설 R1~R4 · 대조군 · 채택 기준은 plan 20261005_5 §2~§3 에 **실행 전에 고정**됐다.
이 스크립트는 그 기준을 그대로 코드로 옮긴 것이며, 결과를 보고 기준을 바꾸지 않는다.

  단계 A (oracle 상한): 같은 hour 후보를 규칙으로 정렬해 진입 — 실거래에선 불가능한 상한
  단계 B (인과 환산)  : A 승자만. 특성이 IS 중앙값 이상인 신호만 받는 고정 임계 필터, OOS 평가

시뮬레이터는 `backtest_stop_validation.simulate` 를 호출한다(rank_fn 훅 외에는 무수정, 로직 복제 금지).
특성은 전부 전일(d-1) 이하 데이터만 쓴다 — look-ahead 없음은 tests/scripts/test_study_candidate_ranking.py.

재현:  py scripts/study_candidate_ranking.py | tee output/study_candidate_ranking_20261005.txt
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_param_sweep import P, _atr  # noqa: E402

# ─────────────────────────── 사전 고정 상수 (plan §2~§3) ───────────────────────────
RUNS = 10
SEEDS_MAIN = list(range(0, RUNS))
SEEDS_NOISE = list(range(100, 100 + RUNS))
MIN_GAIN_PP = 2.0          # 개선 하한 (%p)
SE_MULT = 2 * math.sqrt(2)  # 두 평균의 차이 SE 의 2배
MDD_TOL_PP = 1.0           # MDD 악화 허용 (%p)
HARD = 0.06                # 현행 하드 손절
# (id, 특성 이름, 방향) — 방향 +1: 큰 순이 먼저
RULES = [("R1", "vol_ratio", +1), ("R2", "atrp", -1), ("R3", "ret30", +1), ("R4", "liq", +1)]


# ─────────────────────────── 특성 (전일 이하만) ───────────────────────────
def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """index 는 일 단위. 반환 행 d 의 값은 **d-1 이하** 데이터로만 계산된다 (shift(1) 이후 사용)."""
    atr = _atr(df, 14)
    out = pd.DataFrame(index=df.index)
    vsma5 = df["volume"].rolling(5).mean()
    out["vol_ratio"] = (df["volume"] / vsma5).shift(1)
    out["atrp"] = (atr / df["close"]).shift(1)
    out["ret30"] = (df["close"] / df["close"].shift(30) - 1).shift(1)
    out["liq"] = (df["volume"] * df["close"]).rolling(5).mean().shift(1)
    return out


def attach_features(ctx: dict, raw: dict, t0: int) -> int:
    """ctx['sigs'] 의 각 신호에 s['f'] = {특성: 값} 부착. 반환: 특성 결측 신호 수."""
    feats = {}
    for sym, df in raw.items():
        f = compute_features(df.reset_index(drop=True))
        f.index = df["ts"].to_numpy()
        feats[sym] = f
    missing = 0
    for d, lst in ctx["sigs"].items():
        ts = (d + t0) * 3_600_000
        for s in lst:
            sym = ctx["syms"][s["si"]]
            try:
                row = feats[sym].loc[ts]
                s["f"] = {k: float(row[k]) for k in ("vol_ratio", "atrp", "ret30", "liq")}
            except KeyError:
                s["f"] = {k: float("nan") for k in ("vol_ratio", "atrp", "ret30", "liq")}
            if any(math.isnan(v) for v in s["f"].values()):
                missing += 1
    return missing


# ─────────────────────────── 순위/필터 함수 ───────────────────────────
def _key(c: dict, name: str, direction: int) -> float:
    v = c["f"][name]
    return -math.inf if math.isnan(v) else direction * v      # 결측은 맨 뒤


def make_rank(name: str, direction: int):
    def fn(cands: list, rng) -> list:
        tie = rng.random(len(cands))                          # 동률 무작위
        order = sorted(range(len(cands)), key=lambda i: (_key(cands[i], name, direction), tie[i]), reverse=True)
        return [cands[i] for i in order]
    return fn


def make_filter(name: str, direction: int, thr: float):
    """인과 환산: key ≥ thr 인 후보만 받는다(도착 시점에 알 수 있는 값). 순서는 무작위."""
    def fn(cands: list, rng) -> list:
        keep = [c for c in cands if _key(c, name, direction) >= thr]
        rng.shuffle(keep)
        return keep
    return fn


# ─────────────────────────── 실행 ───────────────────────────
def run_variant(SV, ctx, part: str, rank_fn, seeds: list[int]) -> dict:
    p = P()
    rs = [SV.simulate(ctx, ctx[part], p, HARD, True, s, rank_fn=rank_fn) for s in seeds]
    rets = np.array([r["ret"] for r in rs])
    return {"ret": float(rets.mean()), "sd": float(rets.std(ddof=1)), "se": float(rets.std(ddof=1) / math.sqrt(len(rets))),
            "mdd": float(np.mean([r["mdd"] for r in rs])), "n": float(np.mean([r["n"] for r in rs])),
            "wr": float(np.mean([r["wr"] for r in rs]))}


def threshold(gain_ok_floor_se: float) -> float:
    return max(MIN_GAIN_PP, SE_MULT * gain_ok_floor_se)


def main() -> int:
    argparse.ArgumentParser().parse_args()
    import backtest_stop_validation as SV
    import benchmark_vs_btc_hold as B

    raw, regime, _btc = asyncio.run(B.load_inputs())
    if len(raw) < 40:
        print(f"[중단] 종목 {len(raw)} < 40")
        return 1
    t0 = int(min(df.index.min() for df in raw.values()).timestamp() // 3600)
    ctx = SV.build_ctx(raw, regime)
    miss = attach_features(ctx, raw, t0)
    n_sig = sum(len(v) for v in ctx["sigs"].values())
    print(f"신호 {n_sig} 중 특성 결측 {miss} (결측은 순위 맨 뒤)\n")

    res: dict[str, dict[str, dict]] = {}
    for part in ("is", "oos"):
        res.setdefault("C0", {})[part] = run_variant(SV, ctx, part, None, SEEDS_MAIN)
        res.setdefault("C1", {})[part] = run_variant(SV, ctx, part, None, SEEDS_NOISE)
        for rid, name, d in RULES:
            res.setdefault(rid, {})[part] = run_variant(SV, ctx, part, make_rank(name, d), SEEDS_MAIN)
            res.setdefault(rid + "r", {})[part] = run_variant(SV, ctx, part, make_rank(name, -d), SEEDS_MAIN)

    def line(label, r):
        return (f"{label:<26}{r['is']['ret']:>8.1f}%{r['is']['mdd']:>8.1f}%{r['oos']['ret']:>9.1f}%{r['oos']['mdd']:>8.1f}%"
                f"{r['oos']['n']:>8.0f}{r['oos']['wr']:>7.1f}%")

    print("[단계 A — oracle 상한] 무작위 10회 평균, SL6(현행), 안전장치 ON")
    print(f"{'변형':<26}{'IS 수익':>9}{'IS MDD':>9}{'OOS 수익':>10}{'OOS MDD':>9}{'OOS거래':>8}{'OOS승률':>8}")
    print(line("C0 무작위 (기준, seed0-9)", res["C0"]))
    print(line("C1 무작위 (잡음, seed100-109)", res["C1"]))
    names = {"R1": "R1 거래량 급증 큰 순", "R2": "R2 변동성 낮은 순", "R3": "R3 30일 상대강도 큰 순", "R4": "R4 유동성 큰 순"}
    for rid, _n, _d in RULES:
        print(line(names[rid], res[rid]))
        print(line("   └ 역순 (대조)", res[rid + "r"]))

    c0 = res["C0"]
    thr = {part: threshold(c0[part]["se"]) for part in ("is", "oos")}
    noise = {part: res["C1"][part]["ret"] - c0[part]["ret"] for part in ("is", "oos")}
    print(f"\n잡음 바닥: C1-C0 = IS {noise['is']:+.2f}%p / OOS {noise['oos']:+.2f}%p")
    print(f"개선 하한 max({MIN_GAIN_PP}%p, 2√2·SE): IS {thr['is']:.2f}%p / OOS {thr['oos']:.2f}%p (C0 SE IS {c0['is']['se']:.2f} / OOS {c0['oos']['se']:.2f})\n")

    passers = []
    print("[판정 — plan 20261005_5 §3 단계 A]")
    for rid, name, d in RULES:
        r, rr = res[rid], res[rid + "r"]
        g = {p: r[p]["ret"] - c0[p]["ret"] for p in ("is", "oos")}
        dm = {p: c0[p]["mdd"] - r[p]["mdd"] for p in ("is", "oos")}     # 양수 = MDD 악화
        c1 = all(g[p] >= thr[p] for p in g)
        c2 = all(dm[p] <= MDD_TOL_PP for p in dm)
        c3 = all(rr[p]["ret"] < c0[p]["ret"] for p in ("is", "oos"))
        ok = c1 and c2 and c3
        print(f"  {rid}: 개선 IS {g['is']:+.1f}/OOS {g['oos']:+.1f}%p [{'O' if c1 else 'X'} 하한] | "
              f"MDD 악화 IS {dm['is']:+.1f}/OOS {dm['oos']:+.1f}%p [{'O' if c2 else 'X'}] | "
              f"역순 IS {rr['is']['ret']-c0['is']['ret']:+.1f}/OOS {rr['oos']['ret']-c0['oos']['ret']:+.1f}%p [{'O' if c3 else 'X'} 둘 다 악화] "
              f"=> {'통과' if ok else '탈락'}")
        if ok:
            passers.append((min(g.values()), rid, name, d, g))

    if not passers:
        print("\n결론: 단계 A 통과 0개 — 선택 규칙은 (이 데이터에서) 개선 레버가 아니다. 단계 B 생략, 종료.")
        return 0

    passers.sort(reverse=True)
    _m, rid, name, d, gA = passers[0]
    print(f"\n단계 A 통과: {[p[1] for p in passers]} → 승자 {rid} (min 개선 {_m:+.1f}%p)")
    is_days = set(ctx["is"])
    keys = [_key(s, name, d) for dd, lst in ctx["sigs"].items() if dd in is_days for s in lst]
    keys = [k for k in keys if k != -math.inf]
    th = float(np.median(keys))
    out_b = run_variant(SV, ctx, "oos", make_filter(name, d, th), SEEDS_MAIN)
    gB = out_b["ret"] - c0["oos"]["ret"]
    okB = (gB >= thr["oos"]) and (c0["oos"]["mdd"] - out_b["mdd"] <= MDD_TOL_PP) and (gB >= 0.5 * gA["oos"])
    print(f"[단계 B — 인과 환산] {rid} 임계 = IS 신호 {name} 중앙값(키 {th:.4g}), OOS 만 평가")
    print(f"  OOS 수익 {out_b['ret']:.1f}% (C0 {c0['oos']['ret']:.1f}%, 개선 {gB:+.1f}%p / 하한 {thr['oos']:.2f}) "
          f"MDD {out_b['mdd']:.1f}% (C0 {c0['oos']['mdd']:.1f}%) 거래 {out_b['n']:.0f} (C0 {c0['oos']['n']:.0f})")
    print(f"  oracle 개선(OOS) {gA['oos']:+.1f}%p 의 50% = {0.5*gA['oos']:.1f}%p 이상 보존? {'예' if gB >= 0.5*gA['oos'] else '아니오'}")
    print(f"  => 단계 B {'통과' if okB else '탈락'} (실거래 반영은 사용자 결정, strategy_start 리셋 필요)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
