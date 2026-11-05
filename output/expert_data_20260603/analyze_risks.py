"""
2026-06-03 신규 위험 2건 평가용 데이터 회수
- 위험 1: EMA-TREND BTC/KRW 경로 ML 게이트 미적용 — closed_trades에서 EMA-TREND BTC 진입 추출
- 위험 2: 부팅 직후 fail-open (ml_active=false) 진입 카운트 + 평균 PnL
"""
import json
import os
from pathlib import Path
from collections import defaultdict

BASE = Path(r"D:\20.Personal\Study\BitCoin_Trade\output\expert_data_20260603")

# ---------- closed_trades 로드 ----------
state = json.loads((BASE / "multi_trading_state.json").read_text(encoding="utf-8"))
closed = state.get("closed_trades", [])
print(f"[closed_trades] total={len(closed)}")

# 키 구조 확인
if closed:
    print(f"sample keys: {list(closed[0].keys())}")
    print(f"sample: {json.dumps(closed[0], ensure_ascii=False, indent=2)[:500]}")

# 전략별 / 심볼별 PnL 분포
by_strategy = defaultdict(list)
by_symbol = defaultdict(list)
for t in closed:
    strat = t.get("strategy") or t.get("entry_strategy") or t.get("source") or "UNKNOWN"
    sym = t.get("symbol") or t.get("ticker") or "UNKNOWN"
    pnl_pct = t.get("pnl_pct") or t.get("return_pct") or t.get("profit_pct") or 0
    pnl_krw = t.get("pnl_krw") or t.get("profit_krw") or 0
    by_strategy[strat].append((sym, pnl_pct, pnl_krw))
    by_symbol[sym].append((strat, pnl_pct, pnl_krw))

print("\n[전략별 분포]")
for strat, trades in sorted(by_strategy.items()):
    pnls = [p[1] for p in trades]
    avg = sum(pnls) / len(pnls) if pnls else 0
    print(f"  {strat}: n={len(trades)} avg_pnl_pct={avg:.2f}% syms={set(t[0] for t in trades)}")

print("\n[BTC/KRW 진입 추출]")
btc_trades = [t for t in closed if (t.get("symbol") or t.get("ticker") or "").upper() in ("BTC/KRW", "KRW-BTC", "BTC")]
print(f"  BTC trades n={len(btc_trades)}")
for t in btc_trades:
    print(f"  {json.dumps(t, ensure_ascii=False)[:300]}")

# ---------- ml_shadow 분석: ml_active=false 진입 ----------
print("\n\n[ml_shadow ml_active=false 분석]")
shadow_dir = BASE / "ml_shadow"
fail_open_records = []
total_records = 0
by_date = defaultdict(lambda: {"total": 0, "fail_open": 0, "will_buy": 0, "fail_open_will_buy": 0})

for jf in sorted(shadow_dir.glob("*.jsonl*")):
    date = jf.stem.split(".")[0]
    try:
        for line in jf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total_records += 1
            by_date[date]["total"] += 1
            ml_active = rec.get("ml_active", True)
            will_buy = rec.get("will_buy") or rec.get("decision") == "BUY"
            if will_buy:
                by_date[date]["will_buy"] += 1
            if ml_active is False:
                by_date[date]["fail_open"] += 1
                if will_buy:
                    by_date[date]["fail_open_will_buy"] += 1
                    fail_open_records.append((date, rec))
    except Exception as e:
        print(f"  ! {jf.name}: {e}")

print(f"total records: {total_records}")
print(f"fail_open (ml_active=false) + will_buy: {len(fail_open_records)}")
print("\n[날짜별]")
for d, s in sorted(by_date.items()):
    print(f"  {d}: total={s['total']} will_buy={s['will_buy']} fail_open={s['fail_open']} fail_open_buy={s['fail_open_will_buy']}")

print("\n[fail-open 진입 상세]")
for d, rec in fail_open_records:
    sym = rec.get("symbol") or rec.get("ticker") or "?"
    score = rec.get("score") or rec.get("ml_score") or "?"
    strat = rec.get("strategy") or rec.get("source") or "?"
    print(f"  {d} sym={sym} score={score} strat={strat}")
    print(f"    full: {json.dumps(rec, ensure_ascii=False)[:400]}")
