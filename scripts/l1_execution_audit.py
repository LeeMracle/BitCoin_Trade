#!/usr/bin/env python3
"""L1 실행 검증 — 검증 창의 전략 표본이 설계대로 집행됐는가 (plan 20261030_1).

## 왜 필요한가

판정 기준 §3-1: **실행 결함이 남아 있으면 L2(성과)를 읽지 않는다.**
`closed_trades` 의 `return_pct` 는 봇이 스스로 계산한 값이다. 그것이 거래소 체결과
맞는지, 손절·익절이 설계 가격에서 일어났는지를 거래소 이력으로 대조해야 승률을
"전략의 성적"으로 읽을 수 있다.

## 검사 항목 (plan §3 에 사전 고정 — 결과를 본 뒤 허용오차를 바꾸지 말 것)

    C1 진입가      entry_price vs 실제 매수 평균 체결가          괴리 <= 0.5%
    C2 수익률      return_pct vs 체결 이력 money-weighted 재계산   차이 <= 0.3%p
    C3 손절 캡     손실 매도 체결가 / 매수가 - 1 >= -(캡 + 1.0%p)   신규 6% / 기존 10%
    C4 TP 집행     TP 매도 체결가 >= 트리거 - 1.0%p, 수량 45~55%
    C5 진입 조건   돌파 로그 존재 + BTC>EMA200 + 같은 종목 중복 보유 없음
    C6 슬롯/비중   매수금액 / 총평가 <= 0.105, 동시 보유 <= 15

## 업비트 특성 (lessons #43)

- 시장가 매수는 잔여 KRW 환불로 `canceled` 종료 → canceled + closed 둘 다 조회
- 매도 목록은 체결가가 전부 None → `fetch_order(uuid)` 재조회로만 확정

## 사용 (서버에서, 읽기 전용 — state·주문을 바꾸지 않는다)

    .venv/bin/python scripts/l1_execution_audit.py --out output/l1_audit_20261030.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution.config import (  # noqa: E402
    FEE_RATE,
    HARD_STOP_LOSS_PCT,
    LEGACY_HARD_STOP_LOSS_PCT,
    MAX_POSITION_WEIGHT,
    MAX_POSITIONS,
    REGIME_FILTER_EMA_PERIOD,
    TP_LEVELS,
)

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"

# ── 허용오차 (plan §3 고정값) ──
TOL_ENTRY = 0.005          # C1
TOL_RETURN_PP = 0.3        # C2 (%p)
TOL_STOP_PP = 0.010        # C3
TOL_TP_PP = 0.010          # C4 체결가
TP_RATIO_RANGE = (0.45, 0.55)
TOL_WEIGHT = 0.005         # C6
QTY_RESIDUAL = 0.02        # 매도 수량 합 / 매수 수량 — 이 이상 남으면 미청산 잔량

# 주문 매칭 창 — entry_date/exit_date 는 분 단위 절삭 + 로그 버퍼링이 있다
BUY_MATCH = timedelta(minutes=10)
EXIT_GRACE = timedelta(minutes=5)
# journald 시각은 stdout 버퍼링으로 수 분 늦게 찍힌다(PEPE 10-29 실측 +130s)
SIGNAL_BEFORE = timedelta(minutes=3)
SIGNAL_AFTER = timedelta(minutes=30)

PASS, FAIL, NA = "PASS", "FAIL", "N/A"


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def ms_to_dt(ms: float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ═══════════════════════════════════════════════════════
# 순수 판정 함수 — tests/scripts/test_l1_execution_audit.py 가 결함을 주입해 검증
# ═══════════════════════════════════════════════════════

def match_orders(trade: dict, orders: list[dict]) -> dict:
    """한 거래에 속하는 매수 1건과 매도 N건을 고른다.

    orders: {"ts": datetime, "side", "qty", "price"(확정 체결가 또는 None)}
    매도 구간은 [매수 시각, min(청산+유예, 다음 매수 주문)) — 재진입 거래의 매도가 섞이지 않게.
    경계는 기록(분 절삭)이 아니라 **주문 시각**으로 자른다: API3 10-06 은 청산과 재진입이
    같은 07:17 이라 기록 기준으로 자르면 첫 거래의 TP2 매도가 빠진다.
    """
    entry_dt = parse_utc(trade["entry_date"])
    exit_dt = parse_utc(trade["exit_date"])
    all_buys = [o for o in orders if o["side"] == "buy"]
    buys = [o for o in all_buys if abs(o["ts"] - entry_dt) <= BUY_MATCH]
    if not buys:
        return {"buy": None, "sells": []}
    buy = min(buys, key=lambda o: abs(o["ts"] - entry_dt))
    hi = exit_dt + EXIT_GRACE
    later_buys = [o["ts"] for o in all_buys if o["ts"] > buy["ts"]]
    if later_buys and min(later_buys) <= hi:
        hi = min(later_buys) - timedelta(microseconds=1)
    sells = sorted(
        (o for o in orders if o["side"] == "sell" and buy["ts"] <= o["ts"] <= hi),
        key=lambda o: o["ts"],
    )
    return {"buy": buy, "sells": sells}


def realized_return_pct(buy: dict, sells: list[dict]) -> float | None:
    """position_pnl.record_realized 와 같은 산식을 **실체결가**로 적용한다."""
    if not buy or not sells or any(s["price"] is None for s in sells):
        return None
    basis = buy["price"] * buy["qty"]
    net = sum(s["price"] * s["qty"] * (1 - FEE_RATE) - buy["price"] * s["qty"] * (1 + FEE_RATE)
              for s in sells)
    return net / basis * 100.0


def check_c1(trade: dict, buy: dict | None) -> tuple[str, str]:
    if buy is None or not buy.get("price"):
        return NA, "매수 체결 매칭 실패"
    gap = float(trade["entry_price"]) / buy["price"] - 1
    return (PASS if abs(gap) <= TOL_ENTRY else FAIL), f"entry/fill-1={gap * 100:+.2f}%"


def check_c2(trade: dict, buy: dict | None, sells: list[dict]) -> tuple[str, str]:
    r = realized_return_pct(buy, sells)
    if r is None:
        return NA, "체결 이력 재구성 불가"
    sold = sum(s["qty"] for s in sells) / buy["qty"]
    if sold < 1 - QTY_RESIDUAL:
        return FAIL, f"매도수량 {sold * 100:.1f}% — 미청산 잔량"
    diff = float(trade["return_pct"]) - r
    return (PASS if abs(diff) <= TOL_RETURN_PP else FAIL), \
        f"기록 {trade['return_pct']:+.2f}% / 실측 {r:+.2f}% (Δ{diff:+.2f}p)"


def check_c3(buy: dict | None, sells: list[dict], cap: float) -> tuple[str, str]:
    if buy is None or not sells or any(s["price"] is None for s in sells):
        return NA, "체결 이력 없음"
    worst = min(s["price"] / buy["price"] - 1 for s in sells)
    if worst >= 0:
        return PASS, "손실 매도 없음"
    ok = worst >= -(cap + TOL_STOP_PP)
    return (PASS if ok else FAIL), f"최저 매도 {worst * 100:+.2f}% (캡 -{cap * 100:.0f}%)"


def check_c4(trade: dict, buy: dict | None, sells: list[dict]) -> tuple[str, str]:
    """TP 매도로 보이는 체결(트리거 근처 이상)이 설계 비율·가격인지.

    매도 순서상 앞의 레그부터 TP_LEVELS 에 대응시킨다. 마지막 레그가 트레일 이탈이면
    그 레그는 검사하지 않는다(tp2_full_exit 이면 마지막 레그도 TP2).
    """
    if buy is None or not sells or any(s["price"] is None for s in sells):
        return NA, "체결 이력 없음"
    n_tp = len(sells) if trade.get("exit_reason") == "tp2_full_exit" else len(sells) - 1
    if n_tp <= 0:
        return PASS, "TP 체결 없음"
    if n_tp > len(TP_LEVELS):
        return FAIL, f"TP 레그 {n_tp}개 > 설계 {len(TP_LEVELS)}개"
    notes, ok = [], True
    for lvl, s in zip(TP_LEVELS, sells[:n_tp]):
        r = s["price"] / buy["price"] - 1
        ratio = s["qty"] / buy["qty"]
        good = r >= lvl["trigger_pct"] - TOL_TP_PP and TP_RATIO_RANGE[0] <= ratio <= TP_RATIO_RANGE[1]
        ok &= good
        notes.append(f"TP{lvl['trigger_pct'] * 100:g}:{r * 100:+.2f}%×{ratio * 100:.0f}%")
    return (PASS if ok else FAIL), " ".join(notes)


def check_c5(trade: dict, buy: dict | None, signal_times: list[datetime],
             log_start: datetime | None, regime_bull: bool | None,
             overlapping_same_symbol: bool) -> tuple[str, str]:
    notes = []
    if overlapping_same_symbol:
        return FAIL, "같은 종목 중복 보유"
    if regime_bull is False:
        return FAIL, "진입일 BTC<EMA200 (레짐 BEAR)"
    notes.append("레짐 OK" if regime_bull else "레짐 판정불가")
    t = buy["ts"] if buy else parse_utc(trade["entry_date"])
    if log_start is None or t < log_start + SIGNAL_BEFORE:
        return NA, "; ".join(notes + ["로그 보존 구간 밖"])
    hit = any(t - SIGNAL_BEFORE <= s <= t + SIGNAL_AFTER for s in signal_times)
    if not hit:
        return FAIL, "; ".join(notes + ["돌파 로그 없음"])
    return (PASS if regime_bull else NA), "; ".join(notes + ["돌파 로그 확인"])


def check_c6(buy: dict | None, total_krw: float | None, concurrent: int) -> tuple[str, str]:
    if concurrent > MAX_POSITIONS:
        return FAIL, f"동시 보유 {concurrent} > {MAX_POSITIONS}"
    if buy is None or not buy.get("price"):
        return NA, "매수 체결 없음"
    if not total_krw:
        return NA, f"총평가 미상 (동시 {concurrent})"
    w = buy["price"] * buy["qty"] / total_krw
    return (PASS if w <= MAX_POSITION_WEIGHT + TOL_WEIGHT else FAIL), \
        f"비중 {w * 100:.1f}% (동시 {concurrent})"


def regime_on(day: datetime, closes: list[tuple[datetime, float]]) -> bool | None:
    """진입일 레짐 — 봇은 일 1회 갱신 시 iloc[-1](막 열린 봉)을 쓰므로 실질적으로
    전일 종가 기준이다. 전일·당일 기준 **둘 다** BEAR 일 때만 False(보수적으로 FAIL 축소)."""
    import pandas as pd

    if len(closes) < REGIME_FILTER_EMA_PERIOD:
        return None
    s = pd.Series([c for _, c in closes], index=[d for d, _ in closes])
    ema = s.ewm(span=REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    d0 = day.replace(hour=0, minute=0)
    verdicts = []
    for d in (d0 - timedelta(days=1), d0):
        if d in s.index:
            verdicts.append(bool(s[d] > ema[d]))
    if not verdicts:
        return None
    return any(verdicts)


def concurrency_at(t: datetime, intervals: list[tuple[datetime, datetime]]) -> int:
    return sum(1 for a, b in intervals if a <= t < b)


# ═══════════════════════════════════════════════════════
# I/O
# ═══════════════════════════════════════════════════════

# 업비트 /orders/closed 는 start_time 이 없으면 **최근 7일만** 반환하고, 범위도 7일이 상한이다.
ORDER_WINDOW = timedelta(days=6, hours=12)


def load_orders(ex, symbol: str, lo: datetime, hi: datetime, seen: dict[str, dict]) -> None:
    """[lo, hi] 구간의 체결 주문을 7일 이하 조각으로 나눠 조회해 `seen`(id→주문)에 누적한다."""
    from services.execution.upbit_client import order_exec_price

    raw = []
    a = lo
    while a < hi:
        b = min(a + ORDER_WINDOW, hi)
        for fetch in (ex.fetch_canceled_orders, ex.fetch_closed_orders):
            try:
                raw.extend(fetch(symbol, since=int(a.timestamp() * 1000), limit=100,
                                 params={"until": int(b.timestamp() * 1000)}) or [])
            except Exception as e:  # noqa: BLE001
                print(f"  ! {symbol} {fetch.__name__}: {e}", file=sys.stderr)
            time.sleep(0.15)
        a = b
    for o in raw:
        qty = float(o.get("filled") or 0)
        if qty <= 0 or o["id"] in seen:
            continue
        px = order_exec_price(o)
        if px is None or o.get("side") == "sell":
            try:
                px = order_exec_price(ex.fetch_order(o["id"], symbol))
            except Exception:  # noqa: BLE001
                px = None
            time.sleep(0.12)
        seen[o["id"]] = {"ts": ms_to_dt(o["timestamp"]), "side": o["side"], "qty": qty,
                         "price": px, "id": o["id"]}


def journal_lines(since: str) -> list[str]:
    r = subprocess.run(
        ["journalctl", "-u", "btc-trader", "--since", since, "--no-pager", "-o", "short-iso"],
        capture_output=True, text=True, check=False,
    )
    return r.stdout.splitlines()


_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def _line_ts(line: str) -> datetime | None:
    m = _TS.match(line)
    if not m:
        return None
    # short-iso 는 서버 로컬(UTC) 오프셋을 붙인다 — 서버는 UTC
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def parse_journal(lines: list[str]) -> tuple[datetime | None, dict, list]:
    start = next((t for t in map(_line_ts, lines) if t), None)
    signals: dict[str, list[datetime]] = {}
    totals: list[tuple[datetime, float]] = []
    for ln in lines:
        t = _line_ts(ln)
        if t is None:
            continue
        m = re.search(r"\*\*\* (\S+/KRW) 돌파!", ln)
        if m:
            signals.setdefault(m.group(1), []).append(t)
        m = re.search(r"평가: ([\d,]+)", ln)
        if m:
            totals.append((t, float(m.group(1).replace(",", ""))))
    return start, signals, totals


def nearest_total(t: datetime, totals: list[tuple[datetime, float]]) -> float | None:
    before = [v for ts, v in totals if ts <= t and t - ts <= timedelta(days=1)]
    return before[-1] if before else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "output" / "l1_audit.json"))
    args = ap.parse_args()

    from services.execution import trade_class as tc
    from services.execution.upbit_client import _create_exchange

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    start = state["strategy_start"]
    parts = tc.split(state["closed_trades"], start)
    trades = parts["strategy"]
    all_window = [t for k in parts for t in parts[k]]

    ex = _create_exchange()
    btc = ex.fetch_ohlcv("BTC/KRW", "1d", limit=400)
    closes = [(ms_to_dt(c[0]), float(c[4])) for c in btc]

    log_start, signals, totals = parse_journal(journal_lines(start[:10]))

    intervals = [(parse_utc(t["entry_date"]), parse_utc(t["exit_date"]))
                 for t in all_window if t.get("entry_date")]

    cache: dict[str, dict[str, dict]] = {}
    rows = []
    for i, t in enumerate(trades):
        sym = t["symbol"]
        entry_dt = parse_utc(t["entry_date"])
        seen = cache.setdefault(sym, {})
        load_orders(ex, sym, entry_dt - BUY_MATCH,
                    parse_utc(t["exit_date"]) + EXIT_GRACE + BUY_MATCH, seen)
        m = match_orders(t, list(seen.values()))
        buy, sells = m["buy"], m["sells"]
        legacy = t["entry_date"] < start
        cap = LEGACY_HARD_STOP_LOSS_PCT if legacy else HARD_STOP_LOSS_PCT
        exit_dt = parse_utc(t["exit_date"])
        overlap = any(
            x is not t and x["symbol"] == sym
            and parse_utc(x["entry_date"]) < exit_dt and entry_dt < parse_utc(x["exit_date"])
            for x in all_window
        )
        t_buy = buy["ts"] if buy else entry_dt
        res = {
            "C1": check_c1(t, buy),
            "C2": check_c2(t, buy, sells),
            "C3": check_c3(buy, sells, cap),
            "C4": check_c4(t, buy, sells),
            "C5": check_c5(t, buy, signals.get(sym, []), log_start,
                           regime_on(entry_dt, closes), overlap),
            "C6": check_c6(buy, nearest_total(t_buy, totals), concurrency_at(t_buy, intervals)),
        }
        rows.append({"i": i + 1, "symbol": sym, "entry_date": t["entry_date"],
                     "exit_date": t["exit_date"], "exit_reason": t["exit_reason"],
                     "return_pct": t["return_pct"], "legacy": legacy,
                     "n_sells": len(sells),
                     "checks": {k: {"verdict": v[0], "note": v[1]} for k, v in res.items()}})

    summary = {k: {PASS: 0, FAIL: 0, NA: 0} for k in ("C1", "C2", "C3", "C4", "C5", "C6")}
    for r in rows:
        for k, c in r["checks"].items():
            summary[k][c["verdict"]] += 1
    clean = sum(1 for r in rows if all(c["verdict"] != FAIL for c in r["checks"].values()))

    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
           "strategy_start": start, "n": len(rows), "log_start": str(log_start),
           "summary": summary, "rows_without_fail": clean, "rows": rows}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"L1 audit n={len(rows)}  log_start={log_start}")
    for k, v in summary.items():
        print(f"  {k}: PASS {v[PASS]:>2}  FAIL {v[FAIL]:>2}  N/A {v[NA]:>2}")
    print(f"  FAIL 0건 표본: {clean}/{len(rows)}")
    for r in rows:
        bad = {k: c["note"] for k, c in r["checks"].items() if c["verdict"] == FAIL}
        if bad:
            print(f"  #{r['i']:>2} {r['symbol']:<11} {r['entry_date']} {r['exit_reason']:<14} "
                  f"{r['return_pct']:+.2f}%  {bad}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
