"""이벤트 필터 연구 A(업비트 투자유의·거래지원 종료) · B(FOMC·CPI) — plan 20261005_6.

사전 등록: 정의·차단 구간·채택 기준은 plan 20261005_6 §2~§3 에 **평가 실행 전에 고정**됐다.
이 스크립트는 그 기준을 그대로 코드로 옮긴 것이며 결과를 보고 바꾸지 않는다.

  PA: flagged(투자유의 지정 중 / 거래지원 종료 공지 후) 종목의 신규 진입을 건너뜀  — 방어형(무해 기준)
  PB: [이벤트 −12h, 이벤트 +12h) 에 돌파한 신호의 진입을 건너뜀                  — 수익 기준

모든 상태는 **공지가 올라온 시각 이후에만** 유효하다(인과). 시뮬레이터는 `simulate` 무수정(rank_fn 훅 사용).

재현:  py scripts/study_event_filters.py [--state PATH] | tee output/study_event_filters_20261005.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from study_candidate_ranking import (  # noqa: E402
    HARD, MDD_TOL_PP, MIN_GAIN_PP, SE_MULT, SEEDS_MAIN, run_variant,
)

NOTICES = ROOT / "workspace" / "research" / "data" / "20261005_upbit_trade_notices.json"
EVENTS = ROOT / "workspace" / "research" / "data" / "20261005_macro_events.json"
HOUR_MS = 3_600_000
INF = float("inf")
NY = ZoneInfo("America/New_York")
WIN_BEFORE_H, WIN_AFTER_H = 12, 12            # 사전 고정 (plan §2 B)
POWER_MIN = {"PA": 15, "PB": 30}              # 사전 고정 (plan §3 검정력 규칙)
HARMLESS_MIN_GAIN = -2.0                      # 방어형 '무해' 기준: 수익 변화 ≥ −2.0%p
_SYM = re.compile(r"\(([A-Z0-9]{2,12})\)")


# ─────────────────────────── A: 업비트 공지 → flagged 타임라인 ───────────────────────────
def _ms(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp() * 1000


def classify_title(title: str) -> str | None:
    if "거래지원 종료" in title:
        return "DELIST"
    if "유의 종목 지정 해제" in title:
        return "UNFLAG"
    if "유의 종목 지정" in title:
        return "FLAG"
    return None                                           # '투자유의 촉구' 등은 flagged 아님


def parse_notices(notices: list[dict]) -> dict[str, list[tuple[float, str]]]:
    """심볼 → [(공지 시각 ms, 종류)] 시간순. 제목의 첫 영문 대문자 괄호를 심볼로 본다."""
    ev: dict[str, list[tuple[float, str]]] = {}
    for n in notices:
        kind = classify_title(n["title"])
        m = _SYM.search(n["title"])
        if kind and m:
            ev.setdefault(m.group(1), []).append((_ms(n["listed_at"]), kind))
    for v in ev.values():
        v.sort()
    return ev


def build_intervals(events: list[tuple[float, str]]) -> list[tuple[float, float]]:
    """공지 시각부터 flagged. FLAG→UNFLAG 사이, DELIST 이후 영구."""
    out: list[tuple[float, float]] = []
    start = None
    for t, kind in events:
        if kind == "FLAG" and start is None:
            start = t
        elif kind == "UNFLAG" and start is not None:
            out.append((start, t))
            start = None
        elif kind == "DELIST":
            out.append((start if start is not None else t, INF))
            return out
    if start is not None:
        out.append((start, INF))
    return out


def is_flagged(intervals: list[tuple[float, float]], t_ms: float) -> bool:
    return any(s <= t_ms < e for s, e in intervals)


# ─────────────────────────── B: 매크로 이벤트 → 차단 구간 ───────────────────────────
def event_instants(dates: list[str], hhmm: tuple[int, int]) -> list[float]:
    """미국 동부시간 현지 시각 → UTC ms (서머타임 반영)."""
    out = []
    for d in dates:
        y, m, dd = map(int, d.split("-"))
        local = datetime.combine(datetime(y, m, dd).date(), time(*hhmm), tzinfo=NY)
        out.append(local.astimezone(timezone.utc).timestamp() * 1000)
    return out


def build_windows(instants: list[float], before_h: int = WIN_BEFORE_H, after_h: int = WIN_AFTER_H):
    return sorted((t - before_h * HOUR_MS, t + after_h * HOUR_MS) for t in instants)


def in_windows(windows: list[tuple[float, float]], t_ms: float) -> bool:
    """[시작, 끝) — 시작 포함, 끝 제외."""
    return any(s <= t_ms < e for s, e in windows)


# ─────────────────────────── 정책 훅 ───────────────────────────
def make_block(blocker):
    """blocker(cand) 가 True 인 후보를 건너뛰고 나머지는 무작위 순서(기존과 같은 방식)."""
    def fn(cands: list, rng) -> list:
        keep = [c for c in cands if not blocker(c)]
        rng.shuffle(keep)
        return keep
    return fn


def thr_for(se: float) -> float:
    return max(MIN_GAIN_PP, SE_MULT * se)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None, help="multi_trading_state.json 경로(A-2 라이브 노출, 선택)")
    args = ap.parse_args()

    import backtest_stop_validation as SV
    import benchmark_vs_btc_hold as BM

    notices = json.loads(NOTICES.read_text(encoding="utf-8"))
    macro = json.loads(EVENTS.read_text(encoding="utf-8"))
    ev = parse_notices(notices)
    iv = {s: build_intervals(v) for s, v in ev.items()}

    raw, regime, _btc = asyncio.run(BM.load_inputs())
    if len(raw) < 40:
        print(f"[중단] 종목 {len(raw)} < 40")
        return 1
    t0 = int(min(df.index.min() for df in raw.values()).timestamp() // 3600)
    ctx = SV.build_ctx(raw, regime)
    syms = ctx["syms"]
    is_days = set(ctx["is"])

    def t_ms(c) -> float:
        return (c["t"] + t0) * HOUR_MS

    def blk_a(c) -> bool:
        return is_flagged(iv.get(syms[c["si"]].split("/")[0], []), t_ms(c))

    wins = build_windows(event_instants(macro["fomc_decision_dates"], (14, 0))
                         + event_instants(macro["cpi_release_dates"], (8, 30)))

    def blk_b(c) -> bool:
        return in_windows(wins, t_ms(c))

    # ── 데이터 요약 + 검정력 규칙 (평가 전)
    since = datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000
    n_flag = sum(1 for v in ev.values() for t, k in v if k == "FLAG" and t >= since)
    n_delist = sum(1 for v in ev.values() for t, k in v if k == "DELIST" and t >= since)
    uni = {s.split("/")[0] for s in syms}
    print(f"[데이터] 공지 {len(notices)}건 · 2024-10 이후 유의 지정 {n_flag} / 거래지원 종료 {n_delist} · "
          f"유니버스 52종목 중 공지 이력 있는 종목 {len(uni & set(ev))}개")
    print(f"[데이터] 이벤트 FOMC {len(macro['fomc_decision_dates'])} + CPI {len(macro['cpi_release_dates'])} → 차단 구간 {len(wins)}개 "
          f"(각 {WIN_BEFORE_H}h 전 ~ {WIN_AFTER_H}h 후)\n")

    allsig = [(d, s) for d, lst in ctx["sigs"].items() for s in lst]
    counts = {}
    for pid, blk in (("PA", blk_a), ("PB", blk_b)):
        tot = sum(1 for _d, s in allsig if blk(s))
        in_is = sum(1 for d, s in allsig if blk(s) and d in is_days)
        counts[pid] = (tot, in_is, tot - in_is)
        print(f"[{pid}] 건너뛰는 신호 {tot}/{len(allsig)} (IS {in_is} / OOS {tot - in_is}) — 검정력 하한 {POWER_MIN[pid]} "
              f"→ {'검정 가능' if tot >= POWER_MIN[pid] else '검정 불가'}")

    # ── A-2: 라이브 노출 (기술통계)
    if args.state and Path(args.state).exists():
        st = json.loads(Path(args.state).read_text(encoding="utf-8"))
        rows = []
        for t in st.get("closed_trades", []):
            try:
                e = datetime.strptime(t["entry_date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000
            except (KeyError, ValueError):
                continue
            if is_flagged(iv.get(t["symbol"].split("/")[0], []), e):
                rows.append((t["symbol"], t["entry_date"], t.get("return_pct"), t.get("exit_reason")))
        print(f"\n[A-2 라이브 노출] 전체 청산 {len(st.get('closed_trades', []))}건 중 flagged 상태에서 진입 {len(rows)}건")
        for r in rows[:10]:
            print("   ", r)

    # ── 시뮬레이션
    c0 = {p: run_variant(SV, ctx, p, None, SEEDS_MAIN) for p in ("is", "oos")}
    thr = {p: thr_for(c0[p]["se"]) for p in c0}
    print(f"\n[기준 C0] IS {c0['is']['ret']:.1f}% / MDD {c0['is']['mdd']:.1f}% · OOS {c0['oos']['ret']:.1f}% / MDD {c0['oos']['mdd']:.1f}%"
          f" · 개선 하한 IS {thr['is']:.2f} / OOS {thr['oos']:.2f}%p")

    verdict = {}
    for pid, blk, label in (("PA", blk_a, "PA 투자유의·거래지원 종료 종목 건너뜀"), ("PB", blk_b, "PB FOMC·CPI ±12h 진입 건너뜀")):
        tot = counts[pid][0]
        r = {p: run_variant(SV, ctx, p, make_block(blk), SEEDS_MAIN) for p in ("is", "oos")}
        g = {p: r[p]["ret"] - c0[p]["ret"] for p in r}
        dm = {p: c0[p]["mdd"] - r[p]["mdd"] for p in r}            # 양수 = MDD 악화
        print(f"\n[{pid}] {label}")
        print(f"   IS  수익 {r['is']['ret']:.1f}% ({g['is']:+.2f}%p)  MDD {r['is']['mdd']:.1f}% (악화 {dm['is']:+.2f}%p)  거래 {r['is']['n']:.0f} (C0 {c0['is']['n']:.0f})")
        print(f"   OOS 수익 {r['oos']['ret']:.1f}% ({g['oos']:+.2f}%p)  MDD {r['oos']['mdd']:.1f}% (악화 {dm['oos']:+.2f}%p)  거래 {r['oos']['n']:.0f} (C0 {c0['oos']['n']:.0f})")
        if tot < POWER_MIN[pid]:
            verdict[pid] = "검정 불가 (건너뛰는 신호 수가 사전 하한 미만) — 기술통계로만 보고"
        elif pid == "PA":
            ok = all(g[p] >= HARMLESS_MIN_GAIN for p in g) and all(dm[p] <= MDD_TOL_PP for p in dm)
            verdict[pid] = "무해 → 채택 권고(방어: 상폐 위험 비모델링이 근거)" if ok else "무해 기준 위반 → 채택 권고 안 함"
        else:
            ok = all(g[p] >= thr[p] for p in g) and all(dm[p] <= MDD_TOL_PP for p in dm)
            verdict[pid] = "수익 기준 통과 → 채택 권고" if ok else "수익 기준 미달 → 탈락"
    print("\n[판정 — plan 20261005_6 §3]")
    for pid, v in verdict.items():
        print(f"   {pid}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
