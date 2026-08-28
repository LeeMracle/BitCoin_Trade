"""틱 지연 계측 결과 집계 — 가설 판정용.

## 판정하려는 것 (research/20260827_1 §5-2)

    가설: async 안의 동기 `get_balance()` + `_retry_on_429`(1s/4s/16s) 백오프가
          이벤트 루프를 멈춰 웹소켓 메시지가 백로그로 쌓이고,
          루프가 재개되면 **오래된 틱부터 처리**한다 → 낡은 가격으로 매수 판단.

    판정 기준: 느린 handler 직후 가격지연이 치솟는 **톱니**가 보이는가.
               - 보이면 → 원인은 우리 루프. `get_balance()` 블로킹 제거가 처방.
               - 안 보이는데 지연만 크면 → 네트워크/업비트 측. 다른 처방이 필요하다.
               - 둘 다 작으면 → KERNEL 은 다른 원인. 가설 폐기.

## 두 개의 입력

    1) 봇 로그의 `[틱지연]` 요약 (15분마다) — 분포와 느린 handler 카운트
    2) ML shadow jsonl 의 `extra.tick_lag_ms` — **매수 신호 시점**의 지연
       이쪽이 결정적이다. 분포가 좋아도 하필 신호 때 낡았다면 문제는 남는다.

## 사용 (서버에서)

    python3 scripts/tick_lag_report.py
    python3 scripts/tick_lag_report.py --hours 48
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = ROOT / "workspace" / "ml_shadow"

RE_SUM = re.compile(
    r"\[틱지연\] n=(?P<n>[\d,]+) 누적=(?P<tot>[\d,]+) \| "
    r"가격지연 p50 (?P<p50>[-\d,]+) / p90 (?P<p90>[-\d,]+) / "
    r"p99 (?P<p99>[-\d,]+) / 최대 (?P<max>[-\d,]+)ms \(최소 (?P<min>[-\d,]+)\)"
)
RE_MSG = re.compile(
    r"전송지연 p50 (?P<m50>[-\d,]+) / p90 (?P<m90>[-\d,]+) / "
    r"p99 (?P<m99>[-\d,]+) / 최대 (?P<mmax>[-\d,]+)ms"
)
RE_H = re.compile(
    r"handler p50 (?P<h50>[\d.,]+) / p99 (?P<h99>[\d.,]+) / "
    r"최대 (?P<hmax>[\d,]+)ms \| [\d,]+ms↑ (?P<slow>\d+)건"
)
RE_BREAK = re.compile(r"\*\*\* (?P<sym>\S+/KRW) 돌파!.*?지연: (?P<lag>[-\d,]+)ms")


def _n(s: str) -> float:
    return float(s.replace(",", ""))


def journal(hours: int) -> list[str]:
    r = subprocess.run(
        ["sudo", "journalctl", "-u", "btc-trader", "--no-pager", "-o", "short-iso",
         "--since", f"-{hours}h"],
        capture_output=True, text=True, timeout=180,
    )
    return r.stdout.splitlines() if r.returncode == 0 else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    lines = journal(args.hours)
    sums, msgs, hs, breaks = [], [], [], []
    for ln in lines:
        if m := RE_SUM.search(ln):
            sums.append({k: _n(v) for k, v in m.groupdict().items()})
        if m := RE_MSG.search(ln):
            msgs.append({k: _n(v) for k, v in m.groupdict().items()})
        if m := RE_H.search(ln):
            hs.append({k: _n(v) for k, v in m.groupdict().items()})
        if m := RE_BREAK.search(ln):
            breaks.append((m.group("sym"), _n(m.group("lag"))))

    print("=" * 74)
    print(f"틱 지연 계측 — 최근 {args.hours}시간")
    print("=" * 74)

    if not sums:
        print("\n[틱지연] 요약 로그 없음 — 15분 주기이므로 기동 직후면 정상.")
        print("  확인: sudo journalctl -u btc-trader | grep 틱지연 | tail")
    else:
        print(f"\n요약 {len(sums)}회 (15분 주기)")
        print(f"  {'p50':>9}{'p90':>10}{'p99':>10}{'최대':>11}{'최소':>10}")
        for s in sums[-8:]:
            print(f"  {s['p50']:>9,.0f}{s['p90']:>10,.0f}{s['p99']:>10,.0f}"
                  f"{s['max']:>11,.0f}{s['min']:>10,.0f}")
        worst = max(sums, key=lambda x: x["max"])
        print(f"  최악 구간 최대 지연: {worst['max']:,.0f}ms")
        if worst["min"] < -1000:
            print(f"  ⚠ 최소 지연 {worst['min']:,.0f}ms — 서버 시계가 앞선다. NTP 확인 필요")

    if msgs:
        print(f"\n전송지연(now - timestamp) — **이쪽이 진짜 지연**")
        print(f"  p50 최대 {max(m['m50'] for m in msgs):,.0f}ms / "
              f"p99 최대 {max(m['m99'] for m in msgs):,.0f}ms / "
              f"최대 {max(m['mmax'] for m in msgs):,.0f}ms")
        print("  가격지연과의 차이 = 거래 희소성(그 종목이 그동안 거래되지 않았을 뿐)")

    if hs:
        # ⚠ slow_n 은 트래커 내부 **누적 게이지**다. 요약마다 같은 값이 다시 찍히므로
        #    합산하면 중복 집계된다 — 2026-08-28 실제로 6건을 196건으로 부풀렸다.
        #    '카운터처럼 보이는 게이지'는 집계 코드에서 흔한 함정이다.
        slow_max = max(h["slow"] for h in hs)
        hmax = max(h["hmax"] for h in hs)
        m99 = max((m["m99"] for m in msgs), default=0.0)
        print(f"\nhandler 소요: 최대 {hmax:,.0f}ms / 임계 초과 {slow_max:,.0f}건(누적 게이지)")
        print("  판정:")
        if hmax >= 5_000:
            print("    → handler 가 수 초를 붙잡는다. 가설(루프 블로킹)에 부합.")
            print("      다음: get_balance() 블로킹 제거 검토 (executor / 잔고 캐시)")
        elif m99 >= 10_000:
            print("    → handler 는 빠른데 **전송지연**이 크다. 원인은 우리 루프 밖")
            print("      (네트워크/업비트). 블로킹 제거는 처방이 아니다.")
        else:
            print("    → 전송지연·handler 모두 작다. 가격지연이 커도 그건 거래 희소성이다.")
            print("      → 루프 블로킹 가설은 **기각 방향**. 신호 시점 지연(아래)만 계속 본다.")
    # ── 결정적 계열: 매수 신호 시점의 지연 ────────────────────
    print("\n" + "-" * 74)
    print("매수 신호 시점의 지연 (결정적 — 분포가 좋아도 신호 때 낡았으면 문제다)")
    print("-" * 74)
    if breaks:
        print("  [로그]")
        for sym, lag in breaks[-10:]:
            flag = "  ⚠ 낡음" if lag > 10_000 else ""
            print(f"    {sym:12s} {lag:>10,.0f}ms{flag}")
    sig = []
    for f in sorted(SHADOW_DIR.glob("*.jsonl"))[-7:] if SHADOW_DIR.exists() else []:
        for ln in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            lag = (r.get("extra") or {}).get("tick_lag_ms")
            if lag is not None:
                sig.append((r.get("ts_utc", "")[:16], r.get("symbol", ""), float(lag)))
    if sig:
        print("  [shadow jsonl]")
        for ts, sym, lag in sig[-12:]:
            flag = "  ⚠ 낡음" if lag > 10_000 else ""
            print(f"    {ts} {sym:12s} {lag:>10,.0f}ms{flag}")
        over = sum(1 for _, _, l in sig if l > 10_000)
        print(f"  10초 초과 {over}/{len(sig)}건")
    if not breaks and not sig:
        print("  아직 매수 신호 없음 — 다음 돌파를 기다린다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
