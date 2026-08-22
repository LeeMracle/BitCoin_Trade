"""교차검사(crossing check) 도입의 기대가치 측정.

질문:
    현재 진입은 `price > level["upper"]` — 밴드 "위"인지만 본다.
    아래→위로 뚫는 교차인지, 상승 중인지는 확인하지 않는다.
    레벨 갱신(UTC 00:00)이나 봇 재시작 직후, 이미 밴드 위에 있는 종목을
    방향과 무관하게 즉시 매수한다 (2026-08-22 XRP/SUI 사례: 급락 중 매수 → 2분 만에 -10%).

    교차검사를 넣으면 무엇이 달라지는가?

측정 설계:
    일봉 기준으로 진입일을 두 그룹으로 분해한다.
      A. gap_open  : open > upper       → 시가부터 이미 밴드 위 (현재 로직만 매수)
      B. cross     : open <= upper < high → 장중 아래→위 교차 (양쪽 다 매수)
    교차검사 도입 = **그룹 A를 버리는 것**. 따라서 A의 성과가 곧 기대가치다.

    두 그룹에 동일한 청산 로직을 적용해 비교한다:
      - 하드 손절 캡 entry*(1-0.10)
      - ATR(14)*3.0 트레일링 (고점 갱신 시 상향, 하드캡 아래로는 안 감)
      - TP1 +5% 50% / TP2 +12% 잔량 전부
      - 보수적 순서: 같은 날이면 손절 우선 판정

    진입가:
      A: open (00:00 갱신 직후 첫 틱 ≈ 시가)
      B: upper (교차 순간)

동일 조건 유지:
    ATR 필터(MAX_ATR_PCT), 거래량 필터(완성봉 기준, lessons #40 수정본),
    BTC EMA200 레짐 필터를 양쪽에 동일 적용해 진입 트리거 차이만 남긴다.

사용:
    python scripts/backtest_cross_check_ev.py [--coins N] [--days N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.market_data.fetcher import fetch_ohlcv  # noqa: E402
from services.execution.scanner import get_krw_market_coins  # noqa: E402
from services.execution.config import (  # noqa: E402
    DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, HARD_STOP_LOSS_PCT,
    MAX_ATR_PCT, TP_LEVELS, VOL_FILTER_MULTIPLIER,
)

NL = chr(10)
MAX_HOLD_DAYS = 60  # 무한 보유 방지 (composite는 시간 청산 없음 — 집계 목적 상한)


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def simulate_exit(df: pd.DataFrame, i: int, entry: float, atr_at_entry: float) -> tuple[float, int]:
    """진입 후 청산까지 시뮬레이션. (가중평균 수익률, 보유일) 반환.

    TP 분할을 반영해 실현 수익률을 비중 가중으로 합산한다.
    """
    hard_floor = entry * (1 - HARD_STOP_LOSS_PCT)
    trail = max(entry - atr_at_entry * ATR_MULTIPLIER, hard_floor)
    highest = entry
    remaining = 1.0
    realized = 0.0
    tp_done = [False] * len(TP_LEVELS)

    for d in range(1, MAX_HOLD_DAYS + 1):
        j = i + d
        if j >= len(df):
            break
        hi, lo = float(df["high"].iloc[j]), float(df["low"].iloc[j])

        # 1) 손절 우선 (보수적 — 같은 날 TP와 스탑이 겹치면 스탑으로 본다)
        if lo <= trail:
            realized += remaining * (trail / entry - 1)
            return realized, d

        # 2) 부분 익절
        for k, tp in enumerate(TP_LEVELS):
            if tp_done[k]:
                continue
            trig = entry * (1 + tp["trigger_pct"])
            if hi >= trig:
                sell = remaining * tp["sell_ratio"] if k < len(TP_LEVELS) - 1 else remaining
                realized += sell * (trig / entry - 1)
                remaining -= sell
                tp_done[k] = True
        if remaining <= 1e-9:
            return realized, d

        # 3) 고점 갱신 → 트레일 상향 (하드캡 아래로는 내리지 않음)
        if hi > highest:
            highest = hi
            trail = max(trail, highest - atr_at_entry * ATR_MULTIPLIER, hard_floor)

    # 상한 도달 — 잔량은 마지막 종가로 청산
    j = min(i + MAX_HOLD_DAYS, len(df) - 1)
    realized += remaining * (float(df["close"].iloc[j]) / entry - 1)
    return realized, MAX_HOLD_DAYS


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=80)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--no-filters", action="store_true",
                    help="ATR/거래량/레짐 필터 해제 — 표본 확대용")
    ap.add_argument("--paired", action="store_true",
                    help="동일 날짜에서 밴드진입 vs 종가진입을 짝지어 비교 (선택편향 제거)")
    ap.add_argument("--late", action="store_true",
                    help="늦은 진입 모사: 밴드가 아닌 당일 종가에 체결 (재시작 시나리오)")
    args = ap.parse_args()

    _now = datetime.now(tz=timezone.utc)
    # EMA200 워밍업 + 요청 기간 확보
    _start = (_now - timedelta(days=args.days + 260)).strftime("%Y-%m-%dT00:00:00Z")
    _end = _now.strftime("%Y-%m-%dT00:00:00Z")

    # BTC 레짐 필터용 EMA200
    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", _start, _end, use_cache=True))
    if btc.empty:
        print("BTC 데이터 조회 실패")
        return 1
    btc["ema200"] = btc["close"].ewm(span=200, adjust=False).mean()
    btc["regime_ok"] = btc["close"] > btc["ema200"]
    btc_regime = dict(zip(btc["ts"], btc["regime_ok"]))

    coins = get_krw_market_coins()[: args.coins]
    rows: list[dict] = []

    for idx, c in enumerate(coins, 1):
        sym = c["symbol"]
        try:
            df = pd.DataFrame(await fetch_ohlcv(sym, "1d", _start, _end, use_cache=True))
            if len(df) < DONCHIAN_PERIOD + ATR_PERIOD + 30:
                continue
            df = df.tail(args.days).reset_index(drop=True)

            upper = df["high"].shift(1).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).max()
            atr = _atr(df)
            vol_sma = df["volume"].rolling(5).mean()

            for i in range(DONCHIAN_PERIOD + ATR_PERIOD, len(df) - 1):
                u, a = upper.iloc[i], atr.iloc[i]
                if np.isnan(u) or np.isnan(a) or u <= 0:
                    continue
                o, hi = float(df["open"].iloc[i]), float(df["high"].iloc[i])
                if hi <= u:            # 밴드 미돌파 — 어느 규칙으로도 진입 없음
                    continue

                # ── 공통 필터 (양쪽 arm 동일 적용) ──
                if not args.no_filters:
                    if a / o > MAX_ATR_PCT:                  # ATR 변동성 필터
                        continue
                    vs, lv = vol_sma.iloc[i - 1], df["volume"].iloc[i - 1]  # 완성봉 (lessons #40)
                    if np.isnan(vs) or vs <= 0 or lv < vs * VOL_FILTER_MULTIPLIER:
                        continue
                    if not btc_regime.get(df["ts"].iloc[i], False):         # BTC EMA200 레짐
                        continue

                if o > u:
                    group, entry = "A_gap_open", o     # 시가부터 밴드 위 → 즉시 매수 (현재만)
                else:
                    group, entry = "B_cross", float(u)  # 장중 교차 → 양쪽 매수
                    # 재시작/지연 진입 모사: 밴드보다 premium% 위에서 체결됐다고 가정.
                    # 그 가격이 당일 고가를 넘으면 실현 불가능한 진입이므로 스킵.
                    if args.paired:
                        # 동일 날짜에 대해 두 진입가를 모두 기록 — 짝지은 비교(paired).
                        # 날짜 집합이 같으므로 선택편향 없이 "진입가 차이"만 분리된다.
                        cl = float(df["close"].iloc[i])
                        if cl <= float(u):
                            continue   # 종가가 밴드 아래 → 늦은 진입 자체가 성립 안 함
                        r1, h1 = simulate_exit(df, i, float(u), float(a))
                        r2, h2 = simulate_exit(df, i, cl, float(a))
                        rows.append({"symbol": sym, "group": "P1_band", "ret": r1, "held": h1, "premium": 0.0})
                        rows.append({"symbol": sym, "group": "P2_late_close", "ret": r2, "held": h2,
                                     "premium": cl / float(u) - 1})
                        continue
                    if args.late:
                        # 편향 없는 "늦은 진입" 대리: 당일 종가.
                        # premium 방식(밴드*(1+x) <= high)은 "그만큼 오른 날"만 남겨
                        # 결과를 조건부로 만드는 생존편향이 있어 사용하지 않는다.
                        entry = float(df["close"].iloc[i])
                        if entry <= float(u):
                            continue   # 종가가 밴드 아래로 되돌아온 날 — 진입 자체가 성립 안 함
                        group = "B_cross_late"

                ret, held = simulate_exit(df, i, entry, float(a))
                rows.append({"symbol": sym, "group": group, "ret": ret,
                             "held": held, "premium": entry / float(u) - 1})
        except Exception:
            continue

        if idx % 20 == 0:
            print(f"  진행 {idx}/{len(coins)} — 누적 진입 {len(rows)}건", flush=True)

    if not rows:
        print("진입 사례 없음")
        return 1

    res = pd.DataFrame(rows)
    print("\n" + "=" * 72)
    print(f"교차검사 기대가치 — 표본 {len(res)}건 / {res['symbol'].nunique()}종목 / 최근 {args.days}일")
    print("=" * 72)
    print(f"{'그룹':<14}{'건수':>7}{'승률':>8}{'평균':>9}{'중앙값':>9}{'합계':>10}{'PF':>7}{'평균보유':>8}")

    def stats(g: pd.DataFrame) -> tuple:
        wins, losses = g[g.ret > 0], g[g.ret <= 0]
        gp, gl = wins.ret.sum(), -losses.ret.sum()
        pf = gp / gl if gl > 0 else float("inf")
        return len(g), len(wins) / len(g) * 100, g.ret.mean() * 100, g.ret.median() * 100, g.ret.sum() * 100, pf, g.held.mean()

    for grp in ("A_gap_open", "B_cross", "B_cross_late", "P1_band", "P2_late_close"):
        g = res[res.group == grp]
        if g.empty:
            continue
        n, wr, mu, md, tot, pf, hd = stats(g)
        print(f"{grp:<14}{n:>7}{wr:>7.1f}%{mu:>8.2f}%{md:>8.2f}%{tot:>9.1f}%{pf:>7.2f}{hd:>8.1f}")

    n, wr, mu, md, tot, pf, hd = stats(res)
    print(f"{'전체(현재)':<14}{n:>7}{wr:>7.1f}%{mu:>8.2f}%{md:>8.2f}%{tot:>9.1f}%{pf:>7.2f}{hd:>8.1f}")

    # ── 진입 프리미엄(밴드 대비 초과율)별 성과 ── 진입 상한 임계값 결정용
    late = res[res.group == "P2_late_close"]
    if not late.empty:
        print(NL + "-" * 72)
        print("진입 프리미엄(밴드 대비 초과율)별 성과 — 진입 상한 임계값 근거")
        print("-" * 72)
        print(f"{'프리미엄 구간':<16}{'건수':>7}{'승률':>8}{'평균':>9}{'합계':>10}{'PF':>7}")
        bins = [(0, .01), (.01, .02), (.02, .03), (.03, .05), (.05, .08), (.08, 10)]
        for lo, hi in bins:
            g = late[(late.premium >= lo) & (late.premium < hi)]
            if g.empty:
                continue
            wins, losses = g[g.ret > 0], g[g.ret <= 0]
            gl = -losses.ret.sum()
            pf = wins.ret.sum() / gl if gl > 0 else float("inf")
            label = f"{lo*100:.0f}~{hi*100:.0f}%" if hi < 10 else f"{lo*100:.0f}%+"
            print(f"{label:<16}{len(g):>7}{g.ret.gt(0).mean()*100:>7.1f}%{g.ret.mean()*100:>8.2f}%{g.ret.sum()*100:>9.1f}%{pf:>7.2f}")
        print(f"{NL}  프리미엄 중앙값 {late.premium.median()*100:.2f}% / 평균 {late.premium.mean()*100:.2f}%")
        for cap in (.01, .02, .03, .05):
            kept = late[late.premium <= cap]
            if kept.empty:
                continue
            print(f"  상한 {cap*100:.0f}% 적용 시: {len(kept)}/{len(late)}건 유지 "
                  f"({len(kept)/len(late)*100:.0f}%), 평균 {kept.ret.mean()*100:+.2f}% "
                  f"(전체 {late.ret.mean()*100:+.2f}%)")

    b = res[res.group == "B_cross"]
    a = res[res.group == "A_gap_open"]
    print("\n" + "-" * 72)
    print("교차검사 도입 효과 (= 그룹 A 제거)")
    print("-" * 72)
    if not a.empty:
        print(f"  버리는 거래       : {len(a)}건 ({len(a)/len(res)*100:.1f}%)")
        print(f"  버리는 손익 합계   : {a.ret.sum()*100:+.1f}%p  (평균 {a.ret.mean()*100:+.2f}%/건)")
    if not b.empty and not a.empty:
        print(f"  승률   {res.ret.gt(0).mean()*100:.1f}% → {b.ret.gt(0).mean()*100:.1f}%  "
              f"({b.ret.gt(0).mean()*100 - res.ret.gt(0).mean()*100:+.1f}%p)")
        print(f"  평균   {res.ret.mean()*100:+.2f}% → {b.ret.mean()*100:+.2f}%  "
              f"({(b.ret.mean()-res.ret.mean())*100:+.2f}%p)")
        print(f"  합계   {res.ret.sum()*100:+.1f}% → {b.ret.sum()*100:+.1f}%  "
              f"({(b.ret.sum()-res.ret.sum())*100:+.1f}%p)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
