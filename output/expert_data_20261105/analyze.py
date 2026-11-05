"""EMA-TREND DRY 전략 166건 전수 정량 분석.

Q1: 수익성 평가 (승률/평균/수수료반영/MDD/trail 작동 여부)
Q2: BEAR vs BULL 분리, 보유 시간 분포, 즉시청산 비율
Q3: 레짐 필터 적용 시 가상 효과
"""
from __future__ import annotations
import json
import statistics as st
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent
EMA_FILE = HERE / "ema_trend_state.json"
REGIME_FILE = HERE / "regime_state.json"

FEE_PCT = 0.0005  # 업비트 수수료 0.05% 편측. 라운드 트립 = 2*0.05% = 0.10%
TRAIL_PCT = 0.05  # EMA_TREND_TRAIL_PCT

# BEAR 전환 시점 (regime_state.since_ts)
BEAR_SINCE = datetime.fromtimestamp(1793493019, tz=timezone.utc)
print(f"BEAR_SINCE (UTC): {BEAR_SINCE}", flush=True)
print(f"BEAR_SINCE (KST): {BEAR_SINCE + timedelta(hours=9)}", flush=True)

def parse_dt(s: str) -> datetime:
    # "2026-10-28 08:36" (UTC, naive)
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)

def main():
    data = json.loads(EMA_FILE.read_text(encoding="utf-8"))
    history = data.get("history", [])
    print(f"총 history: {len(history)}건", flush=True)

    # ── 기본 집계 ───────────────────────────────────────────
    rets = [h["return_pct"] / 100.0 for h in history]  # fraction
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    mean_ret = st.mean(rets)
    gross_cum = sum(rets)  # 단순 합 (복리 아님)
    # 복리 누적
    equity = 1.0
    equities = []
    for r in rets:
        equity *= (1 + r)
        equities.append(equity)
    cum_compound = equity - 1.0

    # 수수료 반영: 트레이드당 -0.10% (라운드 트립)
    net_rets = [r - 2 * FEE_PCT for r in rets]
    net_equity = 1.0
    net_equities = []
    for r in net_rets:
        net_equity *= (1 + r)
        net_equities.append(net_equity)
    net_cum_compound = net_equity - 1.0

    # MDD (복리 기준, net)
    peak = 1.0
    mdd = 0.0
    for e in net_equities:
        if e > peak:
            peak = e
        dd = (e - peak) / peak
        if dd < mdd:
            mdd = dd

    print("\n=== Q1. 전수 수익성 ===", flush=True)
    print(f"  거래 수:              {len(rets)}건", flush=True)
    print(f"  승률:                 {len(wins)/len(rets)*100:.2f}% ({len(wins)}승 {len(losses)}패)", flush=True)
    print(f"  평균 손익 (gross):    {mean_ret*100:+.4f}% / trade", flush=True)
    print(f"  평균 손익 (net):      {st.mean(net_rets)*100:+.4f}% / trade  (수수료 -0.10%p/trade)", flush=True)
    print(f"  누적 합산 (gross):    {gross_cum*100:+.2f}%", flush=True)
    print(f"  누적 복리 (gross):    {cum_compound*100:+.2f}%", flush=True)
    print(f"  누적 복리 (net):      {net_cum_compound*100:+.2f}%", flush=True)
    print(f"  MDD (net 복리):       {mdd*100:.2f}%", flush=True)
    print(f"  최대 단일 손실:       {min(rets)*100:+.2f}%", flush=True)
    print(f"  최대 단일 이익:       {max(rets)*100:+.2f}%", flush=True)
    if wins:
        print(f"  평균 승:             {st.mean(wins)*100:+.3f}%", flush=True)
    if losses:
        print(f"  평균 패:             {st.mean(losses)*100:+.3f}%", flush=True)

    # Trail stop 작동 여부
    trail_triggered = sum(1 for h in history if "트레일링스탑" in h["reason"])
    ema_exit = sum(1 for h in history if "EMA50 하향이탈" in h["reason"])
    print(f"\n  청산 사유:", flush=True)
    print(f"    트레일링스탑(5%):   {trail_triggered}건 ({trail_triggered/len(rets)*100:.1f}%)", flush=True)
    print(f"    EMA50 하향이탈:     {ema_exit}건 ({ema_exit/len(rets)*100:.1f}%)", flush=True)

    # 이익 trade에서 return이 5% 넘은 것 (trail stop 범위 밖)
    big_wins = [r for r in rets if r >= 0.05]
    small_wins = [r for r in wins if r < 0.05]
    print(f"  수익 +5% 이상 trade: {len(big_wins)}건", flush=True)
    print(f"  수익 0~5% trade:     {len(small_wins)}건", flush=True)

    # ── Q2. BEAR vs BULL 분리 ───────────────────────────────
    bear_rets = []
    bull_rets = []
    bear_holds_min = []
    bull_holds_min = []
    insta_exit_bear = 0
    insta_exit_bull = 0
    for h in history:
        entry_dt = parse_dt(h["entry_date"])
        exit_dt = parse_dt(h["exit_date"])
        hold_min = (exit_dt - entry_dt).total_seconds() / 60.0
        r = h["return_pct"] / 100.0
        if entry_dt >= BEAR_SINCE:
            bear_rets.append(r)
            bear_holds_min.append(hold_min)
            if hold_min <= 5:
                insta_exit_bear += 1
        else:
            bull_rets.append(r)
            bull_holds_min.append(hold_min)
            if hold_min <= 5:
                insta_exit_bull += 1

    print("\n=== Q2. BEAR vs BULL 분리 ===", flush=True)
    print(f"  BEAR 구간 (since {BEAR_SINCE.strftime('%Y-%m-%d %H:%M')} UTC):", flush=True)
    print(f"    거래 수:            {len(bear_rets)}건 ({len(bear_rets)/len(rets)*100:.1f}%)", flush=True)
    if bear_rets:
        print(f"    승률:               {sum(1 for r in bear_rets if r>0)/len(bear_rets)*100:.2f}%", flush=True)
        print(f"    평균 손익 (net):    {(st.mean(bear_rets) - 2*FEE_PCT)*100:+.3f}% / trade", flush=True)
        print(f"    평균 보유:          {st.mean(bear_holds_min):.2f}분", flush=True)
        print(f"    중앙 보유:          {st.median(bear_holds_min):.2f}분", flush=True)
        print(f"    최대 보유:          {max(bear_holds_min):.1f}분", flush=True)
        print(f"    즉시청산(≤5분):     {insta_exit_bear}건 ({insta_exit_bear/len(bear_rets)*100:.1f}%)", flush=True)

    print(f"\n  BULL 구간 (before {BEAR_SINCE.strftime('%Y-%m-%d %H:%M')} UTC):", flush=True)
    print(f"    거래 수:            {len(bull_rets)}건 ({len(bull_rets)/len(rets)*100:.1f}%)", flush=True)
    if bull_rets:
        print(f"    승률:               {sum(1 for r in bull_rets if r>0)/len(bull_rets)*100:.2f}%", flush=True)
        print(f"    평균 손익 (net):    {(st.mean(bull_rets) - 2*FEE_PCT)*100:+.3f}% / trade", flush=True)
        print(f"    평균 보유:          {st.mean(bull_holds_min):.2f}분", flush=True)
        print(f"    중앙 보유:          {st.median(bull_holds_min):.2f}분", flush=True)
        print(f"    최대 보유:          {max(bull_holds_min):.1f}분", flush=True)
        print(f"    즉시청산(≤5분):     {insta_exit_bull}건 ({insta_exit_bull/len(bull_rets)*100:.1f}%)", flush=True)
        # BULL에서 홀딩 후 수익 청산 사례
        bull_pos_rets = [r for r in bull_rets if r > 0.005]  # 0.5% 넘는 수익
        print(f"    홀딩 후 +0.5% 이상 수익 trade: {len(bull_pos_rets)}건", flush=True)
        if bull_pos_rets:
            print(f"      예시 수익: {[f'{r*100:+.2f}%' for r in bull_pos_rets[:10]]}", flush=True)

    # ── Q3. 레짐 필터 적용 가상 효과 ──────────────────────
    # BEAR 구간은 전부 차단. BEAR 구간 trade 전부 삭제한 가정.
    net_bull_only = [r - 2*FEE_PCT for r in bull_rets]
    eq = 1.0
    peak = 1.0
    mdd_bull = 0.0
    for r in net_bull_only:
        eq *= (1 + r)
        if eq > peak:
            peak = eq
        if (eq - peak) / peak < mdd_bull:
            mdd_bull = (eq - peak) / peak

    print("\n=== Q3. 레짐 필터 BEAR 차단 가상 효과 ===", flush=True)
    print(f"  필터 ON 시 trade 수:  {len(bull_rets)}건 (차단 {len(bear_rets)}건)", flush=True)
    if bull_rets:
        print(f"  BULL only 누적 복리(net): {(eq-1)*100:+.3f}%", flush=True)
        print(f"  BULL only MDD:          {mdd_bull*100:.2f}%", flush=True)

    # 시간당 알림 빈도
    print("\n=== 알림 빈도 분석 ===", flush=True)
    # 24h 분당 거래
    all_entries = sorted(parse_dt(h["entry_date"]) for h in history)
    if len(all_entries) >= 2:
        span_min = (all_entries[-1] - all_entries[0]).total_seconds() / 60.0
        print(f"  전체 기간:          {all_entries[0]} ~ {all_entries[-1]}", flush=True)
        print(f"  기간 분량:          {span_min/60/24:.1f}일 ({span_min:.0f}분)", flush=True)
        print(f"  평균 분당 알림:     {len(history)/span_min:.3f}건/분", flush=True)
        # 진입+청산 2배 → 알림 수
        print(f"  평균 분당 알림(진+청): {len(history)*2/span_min:.3f}건/분", flush=True)

    # 1분내 연속 trade
    minute_buckets = {}
    for h in history:
        key = h["entry_date"]  # "2026-10-28 08:36"
        minute_buckets[key] = minute_buckets.get(key, 0) + 1
    top_minutes = sorted(minute_buckets.items(), key=lambda x: -x[1])[:10]
    print(f"\n  동일 분(minute) 내 연속 거래 TOP10:", flush=True)
    for k, v in top_minutes:
        print(f"    {k}: {v}건", flush=True)

    # 집계 결과 저장
    summary = {
        "n_trades": len(rets),
        "win_rate_pct": len(wins)/len(rets)*100,
        "mean_gross_pct": mean_ret*100,
        "mean_net_pct": st.mean(net_rets)*100,
        "cum_compound_gross_pct": cum_compound*100,
        "cum_compound_net_pct": net_cum_compound*100,
        "mdd_net_pct": mdd*100,
        "trail_triggered": trail_triggered,
        "ema_exit": ema_exit,
        "bear_trades": len(bear_rets),
        "bull_trades": len(bull_rets),
        "insta_exit_bear_ratio_pct": insta_exit_bear/len(bear_rets)*100 if bear_rets else 0,
        "insta_exit_bull_ratio_pct": insta_exit_bull/len(bull_rets)*100 if bull_rets else 0,
        "bull_only_cum_net_pct": (eq-1)*100 if bull_rets else None,
        "bull_only_mdd_pct": mdd_bull*100 if bull_rets else None,
    }
    (HERE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[저장] {HERE / 'summary.json'}", flush=True)

if __name__ == "__main__":
    main()
