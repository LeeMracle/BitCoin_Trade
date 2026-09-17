"""실거래 청산 표본을 1분봉으로 재생해 하드손절 폭별 결과를 비교한다 (2026-09-17).

왜 일봉 백테스트로 부족한가:
    backtest_param_sweep 은 일봉에서 저가·고가가 같은 날 닿으면 **손절 우선**으로 처리한다.
    손절폭이 좁을수록 그런 날이 늘어나므로 좁은 손절에 체계적으로 불리하다.
    1분봉이면 "먼저 닿은 쪽"을 대부분 판별할 수 있고, 한 봉 안에 둘 다 닿는 경우만 모호로 센다.

한계 (결과 해석 시 반드시 병기):
    - 진입 집합은 고정 — 손절이 빨라져 생기는 슬롯 여유·연패 쿨다운 변화·재진입은 반영 못 함
    - 표본 53건, 단일 레짐(BULL 15일) — 파라미터 선택 근거가 아니라 **방향 확인용**
    - ATR 트레일은 무시(실측상 하드캡이 지배) — 현행 10% 재생이 실제 청산과 맞는지로 보정 검증

사용:
    py scripts/replay_live_stop_levels.py --state output/state_20260917.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution import config as C  # noqa: E402
from services.execution.trade_class import split  # noqa: E402

CACHE = ROOT / "output" / "min1"
API = "https://api.upbit.com/v1/candles/minutes/1"
F = "%Y-%m-%d %H:%M"


def _get(market: str, to: datetime) -> list[dict]:
    url = f"{API}?market={market}&count=200&to={to.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except Exception as e:  # 429 등 — 조회 전용이므로 재시도 허용 (lessons #22)
            time.sleep(1.0 * (attempt + 1))
            last = e
    raise RuntimeError(f"{market} {to}: {last}")


def candles(symbol: str, start: datetime, end: datetime) -> list[dict]:
    """[start, end) 1분봉, 시간 오름차순. 거래 없는 분은 업비트가 봉을 만들지 않는다."""
    market = "KRW-" + symbol.split("/")[0]
    key = CACHE / f"{market}_{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.json"
    if key.exists():
        return json.loads(key.read_text())
    out: dict[str, dict] = {}
    to = end
    while to > start:
        rows = _get(market, to)
        time.sleep(0.12)
        if not rows:
            break
        for r in rows:
            out[r["candle_date_time_utc"]] = r
        oldest = min(datetime.fromisoformat(r["candle_date_time_utc"]).replace(tzinfo=timezone.utc)
                     for r in rows)
        if oldest >= to:
            break
        to = oldest
    rows = sorted((r for k, r in out.items()
                   if start <= datetime.fromisoformat(k).replace(tzinfo=timezone.utc) < end),
                  key=lambda r: r["candle_date_time_utc"])
    CACHE.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(rows))
    return rows


def replay(entry: float, rows: list[dict], sl: float, stop_slip: float, fee: float) -> dict:
    """하드손절 sl 로 재생. 반환: ret(%), outcome, ambiguous, mae_before_tp1(%)."""
    stop = entry * (1 - sl)
    rem, proceeds, done = 1.0, 0.0, set()
    ambiguous = False
    mae = 0.0
    for c in rows:
        o, h, l = c["opening_price"], c["high_price"], c["low_price"]
        if 0 not in done:
            mae = min(mae, l / entry - 1)
        hit_stop = l <= stop
        hit_tp = [k for k, tp in enumerate(C.TP_LEVELS)
                  if k not in done and h >= entry * (1 + tp["trigger_pct"])]
        if hit_stop and hit_tp:
            ambiguous = True  # 한 분 안에 둘 다 — 봇의 보수 가정과 같게 손절 우선
        if hit_stop:
            px = min(o, stop) * (1 - stop_slip)  # 갭 하락이면 시가 체결
            proceeds += rem * px * (1 - fee)
            rem = 0.0
            outcome = "stop_after_tp1" if done else "stop"
            break
        for k in hit_tp:
            tp = C.TP_LEVELS[k]
            q = rem if k == len(C.TP_LEVELS) - 1 else tp["sell_ratio"]
            proceeds += q * entry * (1 + tp["trigger_pct"]) * (1 - fee)
            rem -= q
            done.add(k)
        if rem <= 1e-9:
            outcome = "tp2"
            break
    else:
        last = rows[-1]["trade_price"] if rows else entry
        proceeds += rem * last * (1 - fee)
        outcome = "unresolved"
    # proceeds 는 "진입 1단위당 가격" 합이므로 진입가로 정규화해야 수익률이다
    return {"ret": (proceeds / (entry * (1 + fee)) - 1) * 100, "outcome": outcome,
            "ambiguous": ambiguous, "mae": mae * 100}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="output/state_20260917.json")
    ap.add_argument("--start", default=None, help="검증창 시작 (기본: state.strategy_start)")
    ap.add_argument("--levels", default="0.10,0.07,0.05")
    args = ap.parse_args()

    st = json.loads(Path(args.state).read_text(encoding="utf-8"))
    trades = split(st["closed_trades"], args.start or st["strategy_start"])["strategy"]
    levels = [float(x) for x in args.levels.split(",")]
    fee = C.FEE_RATE

    # 실측 손절 슬리피지: TP1 없이 하드손절로 끝난 거래의 (실제 - 이론) 평균
    theo_stop = ((1 - C.HARD_STOP_LOSS_PCT) * (1 - fee) / (1 + fee) - 1) * 100
    full_stops = [t["return_pct"] for t in trades if t["return_pct"] < -8]
    stop_slip = max(0.0, (theo_stop - sum(full_stops) / len(full_stops)) / 100) if full_stops else 0.0

    rows_out = []
    for i, t in enumerate(trades, 1):
        e0 = datetime.strptime(t["entry_date"][:16], F).replace(tzinfo=timezone.utc) + timedelta(minutes=1)
        e1 = datetime.strptime(t["exit_date"][:16], F).replace(tzinfo=timezone.utc) + timedelta(minutes=60)
        rows = candles(t["symbol"], e0, e1)
        r = {"symbol": t["symbol"], "entry_date": t["entry_date"], "actual": t["return_pct"],
             "actual_reason": t["exit_reason"], "n_min": len(rows)}
        for sl in levels:
            r[sl] = replay(float(t["entry_price"]), rows, sl, stop_slip, fee)
        rows_out.append(r)
        print(f"  {i:>2}/{len(trades)} {t['symbol']:<12} {len(rows):>5}분", flush=True)

    out = ROOT / "output" / "replay_stop_levels_20260917.json"
    out.write_text(json.dumps(
        [{k if isinstance(k, str) else f"sl{k}": v for k, v in r.items()} for r in rows_out],
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n표본 {len(trades)}건 / 손절 슬리피지 실측 {stop_slip*100:.2f}%p 반영\n")

    # 보정: 현행 10% 재생이 실제 청산 부호·유형과 맞는가
    base = C.HARD_STOP_LOSS_PCT
    match = sum(1 for r in rows_out
                if (r[base]["ret"] > 0) == (r["actual"] > 0))
    err = [abs(r[base]["ret"] - r["actual"]) for r in rows_out]
    print(f"[보정] 현행 {base:.0%} 재생 vs 실제 — 승패 일치 {match}/{len(rows_out)}, "
          f"건당 오차 중앙값 {sorted(err)[len(err)//2]:.2f}%p")
    for r in rows_out:
        if (r[base]["ret"] > 0) != (r["actual"] > 0):
            print(f"   불일치 {r['symbol']:<12} 실제 {r['actual']:+.2f} ({r['actual_reason']}) "
                  f"재생 {r[base]['ret']:+.2f} ({r[base]['outcome']})")

    print(f"\n{'손절':>6}{'승률':>8}{'건당평균':>9}{'합계':>9}{'평균승':>8}{'평균패':>8}"
          f"{'손익분기':>9}{'TP2':>5}{'TP1후손절':>9}{'손절':>5}{'미결':>5}{'모호':>5}")
    for sl in levels:
        rs = [r[sl] for r in rows_out]
        w = [x["ret"] for x in rs if x["ret"] > 0]
        lo = [x["ret"] for x in rs if x["ret"] <= 0]
        n = len(rs)
        aw = sum(w) / len(w) if w else 0
        al = -sum(lo) / len(lo) if lo else 0
        cnt = {k: sum(1 for x in rs if x["outcome"] == k)
               for k in ("tp2", "stop_after_tp1", "stop", "unresolved")}
        print(f"{sl:>6.0%}{len(w)/n*100:>7.1f}%{sum(x['ret'] for x in rs)/n:>8.2f}%"
              f"{sum(x['ret'] for x in rs):>8.1f}%{aw:>7.2f}%{-al:>7.2f}%"
              f"{(al/(aw+al)*100 if aw+al else 0):>8.1f}%"
              f"{cnt['tp2']:>5}{cnt['stop_after_tp1']:>9}{cnt['stop']:>5}{cnt['unresolved']:>5}"
              f"{sum(1 for x in rs if x['ambiguous']):>5}")
    print(f"{'실제':>6}{sum(1 for r in rows_out if r['actual']>0)/len(rows_out)*100:>7.1f}%"
          f"{sum(r['actual'] for r in rows_out)/len(rows_out):>8.2f}%{sum(r['actual'] for r in rows_out):>8.1f}%")

    # 핵심 질문: 현행에서 이긴 거래 중 TP1 전에 -5% / -7% 를 찍은 건 몇 건인가
    wins = [r for r in rows_out if r[base]["outcome"] == "tp2"]
    print(f"\n[승리 거래의 TP1 전 최대역행(MAE)] 현행 TP2 도달 {len(wins)}건")
    for th in (3, 5, 7):
        k = sum(1 for r in wins if r[base]["mae"] <= -th)
        print(f"   -{th}% 이하를 찍고 이긴 거래: {k}건")
    losers = [r for r in rows_out if r[base]["outcome"] == "stop"]
    print(f"[패배 거래] 현행 TP1 없이 손절 {len(losers)}건 — -5% 손절이면 건당 약 5%p 절약")
    print(f"\n상세: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
