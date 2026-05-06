"""state ↔ exchange 불일치 자동 보정 (lessons #10).

흐름:
    1. multi_trading_state.json 백업 (timestamp suffix)
    2. ccxt fetch_balance + fetch_my_trades — 거래소 진실 확보
    3. 거래소만 존재하는 종목 → state에 추가 (평균체결가 + 보수 trail_stop)
    4. state만 존재하는 종목 → state에서 제거
    5. dust(<10,000 KRW) 무시
    6. 새 state 저장 + 변경 요약 출력

봇 재시작 후 효과 발휘. 실행 전 봇 정지 권장.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import ccxt
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "services" / ".env")

from services.execution.config import HARD_STOP_LOSS_PCT  # noqa: E402

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"
DUST_KRW = 10_000  # 1만 원 미만은 dust 처리


def main() -> int:
    if not STATE_PATH.exists():
        print(f"ERROR: {STATE_PATH} 없음")
        return 1

    # 1. 백업
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = STATE_PATH.with_suffix(f".json.backup_{ts}")
    shutil.copy2(STATE_PATH, backup)
    print(f"백업: {backup.name}")

    # 2. 현재 state
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    pos = state.get("positions", {})
    state_coins = {s.split("/")[0]: s for s in pos.keys()}
    print(f"현재 state: {len(pos)}건 — {list(state_coins.keys())}")

    # 3. 거래소
    ex = ccxt.upbit({
        "apiKey": os.getenv("UPBIT_ACCESS_KEY"),
        "secret": os.getenv("UPBIT_SECRET_KEY"),
        "enableRateLimit": True,
    })
    bal = ex.fetch_balance()
    free = bal.get("free", {})

    # 4. 거래소 보유 종목 (의미 있는 가치만)
    exchange_coins = {}
    for sym, qty in free.items():
        if sym == "KRW" or qty <= 0:
            continue
        try:
            tk = ex.fetch_ticker(f"{sym}/KRW")
            value = qty * tk.get("last", 0)
            if value < DUST_KRW:
                continue
            exchange_coins[sym] = {"qty": qty, "value": value, "last": tk["last"]}
        except Exception:
            continue
    print(f"거래소 보유: {len(exchange_coins)}건 — {list(exchange_coins.keys())}")

    # 5. diff
    to_add = set(exchange_coins.keys()) - set(state_coins.keys())
    to_remove = set(state_coins.keys()) - set(exchange_coins.keys())
    print(f"추가 필요: {to_add}")
    print(f"제거 필요: {to_remove}")

    if not to_add and not to_remove:
        print("일치 — 보정 불필요")
        return 0

    # 6. 추가 — 평균체결가 조회
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    for coin in to_add:
        sym = f"{coin}/KRW"
        try:
            trades = ex.fetch_my_trades(sym, limit=200)
        except Exception as e:
            print(f"  {sym} fetch_my_trades 실패: {e} — 현재가로 fallback")
            trades = []

        buys = [t for t in trades if t.get("side") == "buy"]
        if buys:
            total_cost = sum(t["cost"] for t in buys)
            total_qty = sum(t["amount"] for t in buys)
            avg_price = total_cost / total_qty if total_qty > 0 else exchange_coins[coin]["last"]
            first_ts = min(t["timestamp"] for t in buys) / 1000
            entry_date = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            order_amount = total_cost
        else:
            avg_price = exchange_coins[coin]["last"]
            entry_date = today
            order_amount = exchange_coins[coin]["value"]

        # 보수적 trail_stop = entry × (1 - HARD_STOP)
        # 현재가가 entry보다 높으면 highest = 현재가
        last = exchange_coins[coin]["last"]
        highest = max(avg_price, last)
        trail_stop = highest * (1 - HARD_STOP_LOSS_PCT)

        pos[sym] = {
            "entry_date": entry_date,
            "entry_price": avg_price,
            "highest": highest,
            "trail_stop": trail_stop,
            "order_amount": order_amount,
            "tp_sold_levels": [],  # 미발동 가정 (보수적)
        }
        print(f"  + {sym}: entry {avg_price:,.4g} ({entry_date[:10]}), highest {highest:,.4g}, "
              f"trail {trail_stop:,.4g}")

    # 7. 제거
    for coin in to_remove:
        sym = state_coins[coin]
        removed = pos.pop(sym, None)
        ep = (removed or {}).get("entry_price") or 0
        print(f"  - {sym}: 제거 (entry {ep:,.4g})")

    # 8. 저장
    state["positions"] = pos
    state["state_balance_fix_ts"] = ts
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n저장 완료: {len(pos)}건")
    print(f"롤백: cp {backup.name} {STATE_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
