"""진입 슬리피지 실측 — 신호 감지 시점과 실제 체결가의 괴리를 센다.

## 왜 필요한가 (2026-08-27 KERNEL)

    09:55:16  *** KERNEL/KRW 돌파! 가격: 57  상단: 57 ***
    09:55:16  매수 체결: 68            <- 신호가 대비 +19%
    09:55:16  *** KERNEL/KRW 스탑 이탈! 가격: 58  스탑: 61 ***

부풀려진 체결가(67.8)를 기준으로 하드손절선(-10% = 61.0)이 잡히자, 실제 시세(58)가
그 아래라 **같은 초에 손절**됐다. 손실은 -178원으로 끝났지만 구조는 위험하다 —
체결가가 신호가보다 크게 높으면 손절선이 "코인이 다시 오지 않을 가격"에 걸린다.

## 두 지표를 분리해서 센다 (섞으면 진단이 틀린다)

    집행 슬리피지  = 체결가 / 신호가 - 1     주문이 호가창에 닿는 사이의 이동
    밴드 이격      = 신호가 / DC상단 - 1     감지 시점에 이미 밴드를 얼마나 넘었나

KERNEL 은 이격 0%(밴드에서 정확히 감지) + 슬리피지 +19%(집행 중 급등) 이고,
SPK 는 이격 +14%(감지 시 이미 급등 상태) + 슬리피지 0% 다. **원인도 대책도 다르다.**

## 정밀도 한계 — 반올림을 없앨 수 없으면 경계를 명시한다

로그 포맷이 `{price:,.0f}` 라 신호가가 정수로 뭉개진다(VTHO 0.56 -> "1").
체결가는 로그 대신 **state 의 `entry_price`**(lessons #43 정산 완료, 전정밀도)를 쓴다.
그러면 오차원이 신호가 하나로 줄고, 남은 불확실도는 `+-0.5/신호가` 로 계산 가능하다.
집계는 그 불확실도가 `PRECISION_LIMIT` 이하인 표본으로만 낸다.

## 사용 (서버에서)

    python3 scripts/slippage_audit.py                 # journalctl 전체 보존 구간
    python3 scripts/slippage_audit.py --since "2026-08-25"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"

# 신호가 반올림 불확실도(+-0.5/신호가)가 이 값을 넘으면 집계에서 뺀다.
# 1% 를 넘으면 "슬리피지 1%" 같은 결론을 낼 수 없다 — 측정치가 오차에 묻힌다.
PRECISION_LIMIT = 0.01
NL = chr(10)

# `  *** KITE/KRW 돌파! 가격: 183  상단: 166 ***`
RE_BREAK = re.compile(
    r"\*\*\* (?P<sym>[A-Z0-9]+/KRW) 돌파! 가격: (?P<px>[\d,]+)\s+상단: (?P<up>[\d,]+)"
)
RE_TS = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def read_journal(since: str | None) -> list[str]:
    cmd = ["sudo", "journalctl", "-u", "btc-trader", "--no-pager", "-o", "short-iso"]
    if since:
        cmd += ["--since", since]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"journalctl 실패: {r.stderr.strip()[:200]}")
        return []
    return r.stdout.splitlines()


def collect_signals(lines: list[str]) -> list[dict]:
    """돌파 로그에서 (시각, 종목, 신호가, 상단) 추출."""
    out = []
    for ln in lines:
        m = RE_BREAK.search(ln)
        if not m:
            continue
        t = RE_TS.search(ln)
        out.append({
            "ts": t.group("ts") if t else "",
            "symbol": m.group("sym"),
            "signal_px": _num(m.group("px")),
            "upper": _num(m.group("up")),
        })
    return out


def load_fills() -> dict[str, list[dict]]:
    """state 에서 종목별 실체결 진입 기록 (보유 + 청산 양쪽)."""
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    fills: dict[str, list[dict]] = {}
    for sym, p in state.get("positions", {}).items():
        fills.setdefault(sym, []).append(
            {"entry_date": str(p.get("entry_date", "")),
             "entry_price": float(p.get("entry_price") or 0),
             "ret": None, "reason": "보유중"}
        )
    for t in state.get("closed_trades", []):
        sym = t.get("symbol")
        if not sym:
            continue
        fills.setdefault(sym, []).append(
            {"entry_date": str(t.get("entry_date", "")),
             "entry_price": float(t.get("entry_price") or 0),
             "ret": t.get("return_pct"), "reason": str(t.get("exit_reason") or "")}
        )
    return fills


# journald 기록 시각과 앱이 기록한 entry_date 사이의 허용 오차(분).
MATCH_WINDOW_MIN = 60


def _to_min(s: str) -> int | None:
    """ "YYYY-MM-DD HH:MM..." / "YYYY-MM-DDTHH:MM..." -> epoch 분. 실패 시 None."""
    try:
        d = datetime.strptime(s[:16].replace("T", " "), "%Y-%m-%d %H:%M")
        return int(d.replace(tzinfo=timezone.utc).timestamp() // 60)
    except Exception:
        return None


def match(sig: dict, fills: dict[str, list[dict]]) -> dict | None:
    """신호에 대응하는 진입 체결가를 찾는다 (시간상 가장 가까운 것).

    ## 왜 정확한 분 일치로 맞출 수 없는가 (2026-08-27 적발)

    journald 의 타임스탬프는 **로그가 수집된 시각**이지 이벤트 발생 시각이 아니다.
    실측 지연: KERNEL 2.4초, KITE 61초, **POL 38분**. 분 일치로 맞추면 21건 중
    8건이 통째로 날아간다. 권위 시각은 앱이 직접 기록한 `entry_date` 이므로,
    로그 시각은 "근처" 로만 쓴다.

    동일 종목이 같은 윈도우에 두 번 돌파하면 오매칭 위험이 있으나, 돌파 후
    재진입에는 TP/손절 종료가 끼어 실측 간격이 시간 단위다 (KITE 01:28 / 06:42).
    """
    st = _to_min(sig["ts"])
    if st is None:
        return None
    best, best_d = None, None
    for f in fills.get(sig["symbol"], []):
        ft = _to_min(f["entry_date"])
        if ft is None or f["entry_price"] <= 0:
            continue
        d = abs(ft - st)
        if d <= MATCH_WINDOW_MIN and (best_d is None or d < best_d):
            best, best_d = f, d
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help='예: "2026-08-25"')
    args = ap.parse_args()

    sigs = collect_signals(read_journal(args.since))
    if not sigs:
        print("돌파 로그 없음")
        return 1
    fills = load_fills()

    rows, unmatched, unmeasurable = [], [], []
    for s in sigs:
        rec = match(s, fills)
        if rec is None:
            unmatched.append(s)
            continue
        fill = rec["entry_price"]
        # 저가 코인은 `{:,.0f}` 포멃에 삼켜져 0 으로 찍힌다(BLAST 0.42, VTHO 0.56).
        # 0 으로 나누면 죽고, 1 로 나눠도 무의미한 숫자다 — 정밀도 판정 앞에서 배제한다.
        if s["signal_px"] <= 0 or s["upper"] <= 0:
            unmeasurable.append(s)
            continue
        # 신호가 반올림 불확실도. 로그가 정수이므로 실제값은 +-0.5 안에 있다.
        unc = 0.5 / s["signal_px"]
        rows.append({
            **s, "fill": fill,
            "slip": fill / s["signal_px"] - 1,
            "ext": s["signal_px"] / s["upper"] - 1,
            "unc": unc, "ret": rec["ret"], "reason": rec["reason"],
        })

    print("=" * 78)
    print("진입 슬리피지 실측 — 집행 슬리피지(체결/신호) vs 밴드 이격(신호/상단)")
    print("=" * 78)
    print(f"{'시각(UTC)':17s} {'종목':11s} {'신호가':>9s} {'체결가':>10s} "
          f"{'슬리피지':>9s} {'이격':>8s} {'결과':>9s}  정밀도")
    print("-" * 88)
    for r in sorted(rows, key=lambda x: x["ts"]):
        ok = "OK" if r["unc"] <= PRECISION_LIMIT else f"±{r['unc']:.1%} 제외"
        out = "보유중" if r["ret"] is None else f"{float(r['ret']):+.2f}%"
        print(f"{r['ts'][:16]:17s} {r['symbol']:11s} {r['signal_px']:>9,.0f} "
              f"{r['fill']:>10,.4g} {r['slip']:>+8.1%} {r['ext']:>+7.1%} {out:>9s}  {ok}")

    good = [r for r in rows if r["unc"] <= PRECISION_LIMIT]
    print("-" * 78)
    print(f"돌파 신호 {len(sigs)}건 / 체결 매칭 {len(rows)}건 / "
          f"정밀도 충족 {len(good)}건 (불확실도 ≤ {PRECISION_LIMIT:.0%})")
    if unmeasurable:
        print(f"  측정불가 {len(unmeasurable)}건(로그가 0 으로 반올림): "
              + ", ".join(f"{u['symbol']}" for u in unmeasurable))
    if unmatched:
        print(f"  미매칭 {len(unmatched)}건: "
              + ", ".join(f"{u['symbol']}@{u['ts'][11:16]}" for u in unmatched[:8]))
        print("    (필터 차단·중복신호·state 롤백 등으로 진입 기록이 없는 신호)")

    if not good:
        print("\n정밀도 충족 표본 없음 — 집계 불가")
        return 0

    def stat(key: str, label: str) -> None:
        v = sorted(r[key] for r in good if r[key] == r[key])
        if not v:
            return
        n = len(v)
        med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
        print(f"  {label:14s} 중앙값 {med:+6.2%} | 평균 {sum(v)/n:+6.2%} | "
              f"최대 {v[-1]:+6.2%} | 1%↑ {sum(1 for x in v if x > 0.01)}/{n}건")

    print("\n[분포]")
    stat("slip", "집행 슬리피지")
    stat("ext", "밴드 이격")

    # 필터 도입의 전제: 이격이 큰 진입이 실제로 더 나쁘가?
    # 방향이 반대거나 차이가 없으면 상한 필터는 근거가 없다.
    done = [r for r in good if r["ret"] is not None]
    if done:
        hi = [r for r in done if r["ext"] > 0.03]
        lo = [r for r in done if r["ext"] <= 0.03]
        print(f"{NL}[이격 ↔ 성과] 청산 완료 {len(done)}건")
        for grp, label in ((lo, "이격 <= 3%"), (hi, "이격  > 3%")):
            if not grp:
                continue
            rs = [float(r["ret"]) for r in grp]
            w = sum(1 for x in rs if x > 0)
            print(f"  {label}  n={len(rs):>2}  승률 {w/len(rs):>5.0%}  "
                  f"평균 {sum(rs)/len(rs):+6.2f}%")
        print("  ⚠ n 이 작아 판정불가 — 방향만 본다.")

    worst = max(good, key=lambda r: r["slip"])
    print(f"\n[최악 집행] {worst['symbol']} {worst['ts'][:16]} "
          f"신호 {worst['signal_px']:,.0f} → 체결 {worst['fill']:,.4g} "
          f"({worst['slip']:+.1%})")
    print(f"  이 체결가 기준 하드손절선 = {worst['fill'] * 0.9:,.4g} "
          f"(신호가 기준이었다면 {worst['signal_px'] * 0.9:,.4g})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
