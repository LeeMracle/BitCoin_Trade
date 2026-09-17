"""실거래 표본으로 (1) 보완 필요 판정 (2) 개선 후보 도출 — 2026-09-17 사용자 요청.

replay_live_stop_levels.py 의 1분봉 재생(현행 재생 vs 실제 승패 53/53 일치로 보정됨)을
정책 단위로 일반화한다. 진입 집합은 고정 — 청산 정책 비교만 유효하다.

    py scripts/analyze_live_strategy_20260917.py
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from services.execution import config as C  # noqa: E402
from services.execution.trade_class import split  # noqa: E402
from replay_live_stop_levels import candles, F  # noqa: E402

FEE = C.FEE_RATE
EXTEND_DAYS = 3  # TP2 상향 정책은 원 청산 이후 경로가 필요
BE = ((10 / (7.75 + 10)) * 100)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h) * 100, (c + h) * 100


def beta_cdf(x: float, a: int, b: int, steps: int = 20000) -> float:
    """Beta(a,b) CDF 수치적분 (scipy 의존 회피)."""
    lg = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    s, dx = 0.0, x / steps
    for i in range(steps):
        t = (i + 0.5) * dx
        s += math.exp(lg + (a - 1) * math.log(t) + (b - 1) * math.log(1 - t)) * dx
    return s


def run_policy(entry: float, rows: list[dict], pol: dict, slip: float) -> dict:
    sl, tps, be = pol["sl"], pol["tp"], pol.get("be", False)
    stop = entry * (1 - sl)
    rem, proceeds, done = 1.0, 0.0, set()
    for c in rows:
        o, h, l = c["opening_price"], c["high_price"], c["low_price"]
        if l <= stop:
            px = min(o, stop) * (1 - slip)
            proceeds += rem * px * (1 - FEE)
            return {"ret": (proceeds / (entry * (1 + FEE)) - 1) * 100,
                    "out": "stop_after_tp1" if done else "stop"}
        for k, tp in enumerate(tps):
            if k in done or h < entry * (1 + tp["trigger_pct"]):
                continue
            q = rem if k == len(tps) - 1 else tp["sell_ratio"]
            proceeds += q * entry * (1 + tp["trigger_pct"]) * (1 - FEE)
            rem -= q
            done.add(k)
            if be and k == 0:
                # TP1 후 잔량 손절선을 본전(왕복 수수료 포함)으로 — 다음 분부터 적용
                stop = max(stop, entry * (1 + 2 * FEE))
        if rem <= 1e-9:
            return {"ret": (proceeds / (entry * (1 + FEE)) - 1) * 100, "out": "tp_full"}
    last = rows[-1]["trade_price"] if rows else entry
    proceeds += rem * last * (1 - FEE)
    return {"ret": (proceeds / (entry * (1 + FEE)) - 1) * 100, "out": "unresolved"}


def boot_diff(a: list[float], b: list[float], n: int = 5000) -> tuple[float, float, float]:
    """대응표본 부트스트랩: mean(b - a) 와 95% 구간."""
    rnd = random.Random(7)
    d = [y - x for x, y in zip(a, b)]
    ms = sorted(sum(rnd.choice(d) for _ in d) / len(d) for _ in range(n))
    return sum(d) / len(d), ms[int(n * 0.025)], ms[int(n * 0.975)]


def main() -> int:
    st = json.loads((ROOT / "output" / "state_20260917.json").read_text(encoding="utf-8"))
    trades = split(st["closed_trades"], st["strategy_start"])["strategy"]
    n = len(trades)
    rets = [t["return_pct"] for t in trades]
    wins = sum(1 for r in rets if r > 0)

    print("=" * 78)
    print("[1] 판정 — 사전등록 기준 (docs/strategy_improvement_criteria.md)")
    print("=" * 78)
    for label, sub in (("전체", trades),
                       ("결함창 진입 6건 제외", [t for t in trades
                                            if not ("2026-09-13 00:16" <= t["entry_date"][:16] < "2026-09-13 14:16")])):
        k = sum(1 for t in sub if t["return_pct"] > 0)
        lo, hi = wilson(k, len(sub))
        pbelow = beta_cdf(BE / 100, k + 1, len(sub) - k + 1)
        m = sum(t["return_pct"] for t in sub) / len(sub)
        sd = math.sqrt(sum((t["return_pct"] - m) ** 2 for t in sub) / (len(sub) - 1))
        print(f"  {label:<18} n={len(sub)} 승 {k} = {k/len(sub)*100:.1f}%  95%CI [{lo:.1f}, {hi:.1f}]"
              f"  P(승률<{BE:.1f}%)={pbelow*100:.0f}%")
        print(f"  {'':<18} 건당 {m:+.2f}% ± {1.96*sd/math.sqrt(len(sub)):.2f}%p (95%)  합계 {sum(t['return_pct'] for t in sub):+.1f}%")
    aw = sum(r for r in rets if r > 0) / wins
    al = -sum(r for r in rets if r <= 0) / (n - wins)
    print(f"  실현 평균승 +{aw:.2f}% / 평균패 -{al:.2f}% → **실현 손익분기 승률 {al/(aw+al)*100:.1f}%** "
          f"(구조적 {BE:.1f}%)")

    print("\n" + "=" * 78)
    print("[2] 손실 해부")
    print("=" * 78)
    rows_by = {}
    now = datetime.now(tz=timezone.utc)
    for t in trades:
        e0 = datetime.strptime(t["entry_date"][:16], F).replace(tzinfo=timezone.utc) + timedelta(minutes=1)
        e1 = min(datetime.strptime(t["exit_date"][:16], F).replace(tzinfo=timezone.utc)
                 + timedelta(days=EXTEND_DAYS), now.replace(second=0, microsecond=0))
        rows_by[id(t)] = candles(t["symbol"], e0, e1)

    def hours_to_exit(t):
        a = datetime.strptime(t["entry_date"][:16], F)
        b = datetime.strptime(t["exit_date"][:16], F)
        return (b - a).total_seconds() / 3600

    full_losses = [t for t in trades if t["return_pct"] < -8]
    tp1_then_stop = [t for t in trades if -4 < t["return_pct"] <= 0]
    print(f"  구성: TP2 전량 {wins} / TP1 후 손절 {len(tp1_then_stop)} (건당 {sum(t['return_pct'] for t in tp1_then_stop)/max(1,len(tp1_then_stop)):+.2f}%)"
          f" / 풀 손절 {len(full_losses)} (건당 {sum(t['return_pct'] for t in full_losses)/max(1,len(full_losses)):+.2f}%)")
    hs = sorted(hours_to_exit(t) for t in full_losses)
    print(f"  풀 손절까지 보유시간: 중앙값 {hs[len(hs)//2]:.1f}h, 1h 이내 {sum(1 for h in hs if h<=1)}건, "
          f"6h 이내 {sum(1 for h in hs if h<=6)}건")
    hw = sorted(hours_to_exit(t) for t in trades if t["return_pct"] > 0)
    print(f"  TP2 도달까지 보유시간: 중앙값 {hw[len(hw)//2]:.1f}h, 1h 이내 {sum(1 for h in hw if h<=1)}건")
    # 패배 거래의 MFE: 손절 전 최고 상승폭 — 진입 자체가 틀렸나, 청산이 틀렸나
    mfe = []
    for t in full_losses:
        e, ex = float(t["entry_price"]), datetime.strptime(t["exit_date"][:16], F).strftime("%Y-%m-%dT%H:%M")
        hi = max((c["high_price"] for c in rows_by[id(t)] if c["candle_date_time_utc"][:16] <= ex), default=e)
        mfe.append((hi / e - 1) * 100)
    mfe.sort()
    print(f"  풀 손절 {len(mfe)}건의 손절 전 최고상승(MFE): 중앙값 +{mfe[len(mfe)//2]:.1f}%, "
          f"+2% 미만 {sum(1 for x in mfe if x<2)}건, +4% 이상 {sum(1 for x in mfe if x>=4)}건")
    # 진입 시각: UTC 00시대(일봉 갱신 직후 돌파) vs 그 외
    for lab, cond in (("00:00~00:59 UTC 진입", lambda t: t["entry_date"][11:13] == "00"),
                      ("그 외 시각 진입", lambda t: t["entry_date"][11:13] != "00")):
        s = [t for t in trades if cond(t)]
        if s:
            k = sum(1 for t in s if t["return_pct"] > 0)
            print(f"  {lab:<20} n={len(s):>2} 승률 {k/len(s)*100:5.1f}% 건당 {sum(t['return_pct'] for t in s)/len(s):+.2f}%")
    # 같은 날 동시 진입 수(군집) — 레짐·시장 전체 움직임 노출
    from collections import Counter
    day_cnt = Counter(t["entry_date"][:10] for t in trades)
    for lab, cond in (("당일 동시진입 ≥4", lambda t: day_cnt[t["entry_date"][:10]] >= 4),
                      ("당일 동시진입 ≤3", lambda t: day_cnt[t["entry_date"][:10]] <= 3)):
        s = [t for t in trades if cond(t)]
        if s:
            k = sum(1 for t in s if t["return_pct"] > 0)
            print(f"  {lab:<20} n={len(s):>2} 승률 {k/len(s)*100:5.1f}% 건당 {sum(t['return_pct'] for t in s)/len(s):+.2f}%")

    print("\n" + "=" * 78)
    print("[3] 청산 정책 비교 — 1분봉 재생, 진입 고정, 대응표본 부트스트랩 (현행 대비)")
    print("=" * 78)
    tp_cur = C.TP_LEVELS
    tp_15 = [{"trigger_pct": 0.055, "sell_ratio": 0.5}, {"trigger_pct": 0.15, "sell_ratio": 0.5}]
    policies = [
        ("현행 SL10 TP5.5/10", {"sl": 0.10, "tp": tp_cur}),
        ("SL7", {"sl": 0.07, "tp": tp_cur}),
        ("SL5", {"sl": 0.05, "tp": tp_cur}),
        ("TP1후 본전손절", {"sl": 0.10, "tp": tp_cur, "be": True}),
        ("SL7 + 본전손절", {"sl": 0.07, "tp": tp_cur, "be": True}),
        ("SL5 + 본전손절", {"sl": 0.05, "tp": tp_cur, "be": True}),
        ("TP2 15%", {"sl": 0.10, "tp": tp_15}),
        ("SL7 + 본전 + TP2 15%", {"sl": 0.07, "tp": tp_15, "be": True}),
    ]
    slip = 0.0015  # replay_live_stop_levels 실측
    base = None
    print(f"  {'정책':<22}{'승률':>7}{'건당':>8}{'합계':>8}{'풀손절':>6}{'미결':>5}   현행 대비 건당 차이 [95%]")
    for label, pol in policies:
        rs = [run_policy(float(t["entry_price"]), rows_by[id(t)], pol, slip) for t in trades]
        r = [x["ret"] for x in rs]
        if base is None:
            base = r
            diff = ""
        else:
            m, lo, hi = boot_diff(base, r)
            flag = " *" if lo > 0 else ""
            diff = f"{m:+.2f}%p [{lo:+.2f}, {hi:+.2f}]{flag}"
        print(f"  {label:<22}{sum(1 for x in r if x>0)/n*100:>6.1f}%{sum(r)/n:>7.2f}%{sum(r):>7.1f}%"
              f"{sum(1 for x in rs if x['out']=='stop'):>6}{sum(1 for x in rs if x['out']=='unresolved'):>5}   {diff}")
    print(f"\n  * = 95% 구간이 0 초과. 정책 {len(policies)-1}개 동시비교 — 본페로니 보정 시 구간은 더 넓다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
