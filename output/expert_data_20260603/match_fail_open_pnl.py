"""fail-open 진입 4건의 closed_trades 매칭 → 실 PnL 확정"""
import json
from pathlib import Path

BASE = Path(r"D:\20.Personal\Study\BitCoin_Trade\output\expert_data_20260603")
state = json.loads((BASE / "multi_trading_state.json").read_text(encoding="utf-8"))
closed = state.get("closed_trades", [])

# fail-open 진입 (symbol, ts_utc 근처)
fail_opens = [
    ("KRW-BTC", "2026-05-04"),  # 진입 시도, BTC라 entry 안 됐을 수 있음
    ("ALT/KRW", "2026-05-09"),
    ("GLM/KRW", "2026-05-16"),
    ("META/KRW", "2026-05-16"),
]

for sym, date in fail_opens:
    sym_norm = sym.replace("KRW-", "").replace("/KRW", "")
    matches = [t for t in closed if sym_norm in (t.get("symbol") or "") and date in (t.get("entry_date") or "")]
    print(f"\n{sym} {date}: matches={len(matches)}")
    for m in matches:
        print(f"  {json.dumps(m, ensure_ascii=False)}")

# 전체 closed_trades 의 ALT/KRW 직접 확인
print("\n[ALT/KRW closed_trades 전체]")
for t in closed:
    if "ALT" in (t.get("symbol") or ""):
        print(f"  {t}")

# 전체 closed_trades 중 5/9, 5/16 진입
print("\n[5/9 진입]")
for t in closed:
    if "2026-05-09" in (t.get("entry_date") or ""):
        print(f"  {t}")
print("\n[5/16 진입]")
for t in closed:
    if "2026-05-16" in (t.get("entry_date") or ""):
        print(f"  {t}")

# closed_trades 전체 통계: 평균 PnL, n
pnls = [t.get("return_pct", 0) for t in closed]
print(f"\n[전체 통계] n={len(pnls)} avg={sum(pnls)/len(pnls):.2f}% min={min(pnls):.2f}% max={max(pnls):.2f}%")
neg = [p for p in pnls if p < 0]
pos = [p for p in pnls if p >= 0]
print(f"  losses n={len(neg)} avg={sum(neg)/len(neg) if neg else 0:.2f}%")
print(f"  wins   n={len(pos)} avg={sum(pos)/len(pos) if pos else 0:.2f}%")
