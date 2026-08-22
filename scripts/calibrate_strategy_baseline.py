"""현재 config 기준 전략 기대성과 재측정 (P1 — 검증 기준선 재설정).

배경:
    `periodic_analysis._BACKTEST_TARGET_WINRATE/AVG_RET` = 35% / +0.5% 는
    TP(+5%/+12%) 도입 **이전** 설정에서 나온 값으로 보인다. 현재는 TP1이 +5%에서
    절반을 실현하므로 승률은 구조적으로 높고 평균수익은 낮아진다 — 목표치가
    현실과 어긋나면 PASS/FAIL 판정이 무의미해진다.

    또한 기존 `strategy_start = 2026-03-29` 창은 그 사이 전략 파라미터가
    반복 변경(DC 50→20→15→10→15→12, MIN_VOLUME 5억→3억→5억, 레짐필터 OFF→ON,
    VOL 1.0→1.5, ATR 0.10→0.07)되어 **서로 다른 전략의 성적을 합산**하고 있었다.

목적:
    현재 config 그대로 백테스트해 (1) 기대 승률/평균수익, (2) 거래 빈도,
    (3) 레짐 게이트가 빈도에 미치는 영향을 측정한다.
    (3)은 재검증 기간을 며칠로 잡아야 표본이 쌓이는지 판단하는 근거다.

수수료:
    업비트 현물 0.05% (매수·매도 각각). TP 분할매도마다 부과.

사용:
    python scripts/calibrate_strategy_baseline.py [--coins N] [--days N]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.market_data.fetcher import fetch_ohlcv  # noqa: E402
from services.execution.scanner import get_krw_market_coins  # noqa: E402
from services.execution.config import (  # noqa: E402
    DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, HARD_STOP_LOSS_PCT,
    MAX_ATR_PCT, TP_LEVELS, VOL_FILTER_MULTIPLIER, MAX_POSITIONS,
    REGIME_FILTER_ENABLED, REGIME_FILTER_EMA_PERIOD,
)

FEE = 0.0005          # 업비트 현물 수수료 (편도)
MAX_HOLD_DAYS = 60


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def simulate_exit(df: pd.DataFrame, i: int, entry: float, atr0: float) -> tuple[float, int]:
    """수수료 반영 실현 수익률과 보유일 반환."""
    hard_floor = entry * (1 - HARD_STOP_LOSS_PCT)
    trail = max(entry - atr0 * ATR_MULTIPLIER, hard_floor)
    highest, remaining, realized = entry, 1.0, 0.0
    tp_done = [False] * len(TP_LEVELS)

    def _net(exit_px: float, weight: float) -> float:
        """매수·매도 수수료 반영 순수익률 기여분."""
        return weight * ((exit_px * (1 - FEE)) / (entry * (1 + FEE)) - 1)

    for d in range(1, MAX_HOLD_DAYS + 1):
        j = i + d
        if j >= len(df):
            break
        hi, lo = float(df["high"].iloc[j]), float(df["low"].iloc[j])
        if lo <= trail:                      # 손절 우선 (보수적)
            return realized + _net(trail, remaining), d
        for k, tp in enumerate(TP_LEVELS):
            if tp_done[k]:
                continue
            trig = entry * (1 + tp["trigger_pct"])
            if hi >= trig:
                sell = remaining * tp["sell_ratio"] if k < len(TP_LEVELS) - 1 else remaining
                realized += _net(trig, sell)
                remaining -= sell
                tp_done[k] = True
        if remaining <= 1e-9:
            return realized, d
        if hi > highest:
            highest = hi
            trail = max(trail, highest - atr0 * ATR_MULTIPLIER, hard_floor)

    j = min(i + MAX_HOLD_DAYS, len(df) - 1)
    return realized + _net(float(df["close"].iloc[j]), remaining), MAX_HOLD_DAYS


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", type=int, default=120)
    ap.add_argument("--days", type=int, default=700)
    args = ap.parse_args()

    now = datetime.now(tz=timezone.utc)
    start = (now - timedelta(days=args.days + 260)).strftime("%Y-%m-%dT00:00:00Z")
    end = now.strftime("%Y-%m-%dT00:00:00Z")

    btc = pd.DataFrame(await fetch_ohlcv("BTC/KRW", "1d", start, end, use_cache=True))
    btc["ema"] = btc["close"].ewm(span=REGIME_FILTER_EMA_PERIOD, adjust=False).mean()
    regime = dict(zip(btc["ts"], btc["close"] > btc["ema"]))

    coins = get_krw_market_coins()[: args.coins]
    rows: list[dict] = []

    for idx, c in enumerate(coins, 1):
        sym = c["symbol"]
        try:
            df = pd.DataFrame(await fetch_ohlcv(sym, "1d", start, end, use_cache=True))
            if len(df) < DONCHIAN_PERIOD + ATR_PERIOD + 30:
                continue
            df = df.tail(args.days).reset_index(drop=True)
            upper = df["high"].shift(1).rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).max()
            atr = _atr(df)
            vsma = df["volume"].rolling(5).mean()

            for i in range(DONCHIAN_PERIOD + ATR_PERIOD, len(df) - 1):
                u, a = upper.iloc[i], atr.iloc[i]
                if np.isnan(u) or np.isnan(a) or u <= 0:
                    continue
                o, hi = float(df["open"].iloc[i]), float(df["high"].iloc[i])
                if hi <= u:
                    continue
                if a / o > MAX_ATR_PCT:
                    continue
                vs, lv = vsma.iloc[i - 1], df["volume"].iloc[i - 1]
                if np.isnan(vs) or vs <= 0 or lv < vs * VOL_FILTER_MULTIPLIER:
                    continue
                reg_ok = bool(regime.get(df["ts"].iloc[i], False))
                if REGIME_FILTER_ENABLED and not reg_ok:
                    # 레짐 차단분도 빈도 분석용으로 기록 (거래는 미실행)
                    rows.append({"sym": sym, "ts": df["ts"].iloc[i], "blocked": True,
                                 "ret": np.nan, "held": np.nan})
                    continue
                entry = max(float(u), o)      # 시가가 밴드 위면 시가 체결
                ret, held = simulate_exit(df, i, entry, float(a))
                rows.append({"sym": sym, "ts": df["ts"].iloc[i], "blocked": False,
                             "ret": ret, "held": held})
        except Exception:
            continue
        if idx % 30 == 0:
            print(f"  진행 {idx}/{len(coins)}", flush=True)

    if not rows:
        print("표본 없음")
        return 1

    res = pd.DataFrame(rows)
    res["date"] = pd.to_datetime(res["ts"], unit="ms", utc=True)
    ex = res[~res.blocked].dropna(subset=["ret"])

    print("\n" + "=" * 70)
    print(f"현재 config 기준선 — DC{DONCHIAN_PERIOD} / ATR{ATR_PERIOD}x{ATR_MULTIPLIER} / "
          f"TP{[t['trigger_pct'] for t in TP_LEVELS]} / 수수료 {FEE*100:.2f}%x2")
    print(f"표본 {len(ex)}건 체결 / {ex.sym.nunique()}종목 / 최근 {args.days}일")
    print("=" * 70)

    wins = ex[ex.ret > 0]
    gl = -ex[ex.ret <= 0].ret.sum()
    print(f"  승률       : {len(wins)/len(ex)*100:.1f}%")
    print(f"  평균수익   : {ex.ret.mean()*100:+.2f}%")
    print(f"  중앙값     : {ex.ret.median()*100:+.2f}%")
    print(f"  PF         : {wins.ret.sum()/gl:.2f}" if gl > 0 else "  PF: inf")
    print(f"  평균보유   : {ex.held.mean():.1f}일")
    print(f"  최대손실   : {ex.ret.min()*100:.2f}%")

    # ── 거래 빈도 (재검증 기간 산정 근거) ──
    print("\n" + "-" * 70)
    print("거래 빈도 — 재검증에 며칠이 필요한가")
    print("-" * 70)
    days_span = (res.date.max() - res.date.min()).days or 1
    sig_per_day = len(ex) / days_span
    # 슬롯 제약: 동시 5개 × 평균보유일 → 하루 처리 가능 건수
    capacity = MAX_POSITIONS / max(ex.held.mean(), 1e-9)
    eff = min(sig_per_day, capacity)
    print(f"  신호 발생  : {sig_per_day:.2f}건/일 (전 종목)")
    print(f"  슬롯 처리량: {capacity:.2f}건/일 ({MAX_POSITIONS}슬롯 / 평균보유 {ex.held.mean():.1f}일)")
    print(f"  실효 빈도  : {eff:.2f}건/일 → 월 {eff*30:.0f}건")
    for n in (15, 30, 50):
        print(f"    {n}건 표본까지 {n/eff:.0f}일 ({n/eff/7:.1f}주)")

    # ── 레짐 게이트 영향 ──
    blocked = res[res.blocked]
    print("\n" + "-" * 70)
    print("레짐 게이트(BTC>EMA200) 영향")
    print("-" * 70)
    tot = len(ex) + len(blocked)
    print(f"  통과 {len(ex)}건 / 차단 {len(blocked)}건 (차단률 {len(blocked)/tot*100:.0f}%)")
    by_month = res.groupby([res.date.dt.to_period("M"), res.blocked]).size().unstack(fill_value=0)
    if False in by_month.columns:
        recent = by_month.tail(8)
        print(f"\n  {'월':<10}{'체결':>7}{'레짐차단':>10}")
        for m, r in recent.iterrows():
            print(f"  {str(m):<10}{r.get(False, 0):>7}{r.get(True, 0):>10}")

    # ── 목표치 제안 ──
    print("\n" + "-" * 70)
    print("검증 목표치 제안 (현재 config 기준)")
    print("-" * 70)
    wr, avg = len(wins) / len(ex) * 100, ex.ret.mean() * 100
    print(f"  기대 승률 {wr:.0f}% / 평균 {avg:+.2f}%")
    print(f"  → 목표치는 기대의 하한으로: 승률 {wr*0.8:.0f}%+ / 평균 {avg*0.5:+.2f}%+")
    print(f"     (기대 그대로 목표로 잡으면 절반은 구조적으로 FAIL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
