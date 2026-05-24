"""state ↔ exchange 불일치 자동 보정 (lessons #10, #28).

흐름:
    1. multi_trading_state.json + vb_state.json 백업 (timestamp suffix)
    2. ccxt fetch_balance + fetch_my_trades — 거래소 진실 확보
    3. multi: 거래소만 존재 → state에 추가 (평균체결가 + 보수 trail_stop)
             state만 존재 → state에서 제거
    4. vb:    state만 존재 → state에서 제거 (vb는 자체 진입 로직 사용 → add 미실시)
    5. dust(<5,000 KRW) 무시
    6. 새 state 저장 + 변경 요약 출력

봇 재시작 후 효과 발휘. 실행 전 봇 정지 권장.

lessons #28: vb_state 누락 보정으로 영구 false alarm 방지.
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
VB_STATE_PATH = ROOT / "workspace" / "vb_state.json"
DUST_KRW = 5_000  # _hourly_sync(realtime_monitor.py:237) 임계와 일치 — 불일치 시 영구 false alarm 발생 (lessons #28 적용 완료)


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
    # lessons #28: free만 보면 매도 미체결 주문에 잠긴 잔량을 누락 → false negative
    # total = free + used 기준으로 평가해야 거래소 진실 반영
    total = bal.get("total", {})

    # 4. 거래소 보유 종목 (의미 있는 가치만)
    # lessons #28: ticker 조회 실패 시 continue로 빠지면 to_remove 후보가 되어 잘못 제거됨
    #              → 시세 없으면 보수적으로 exchange_coins에 등록(remove 회피, add 후보엔 미포함 처리)
    exchange_coins = {}
    for sym, qty in total.items():
        if sym in ("KRW", "info") or not isinstance(qty, (int, float)) or qty <= 0:
            continue
        try:
            tk = ex.fetch_ticker(f"{sym}/KRW")
            last = tk.get("last") or 0
            value = qty * last
            if last > 0 and value < DUST_KRW:
                continue  # dust = 무시 (시세 확인된 경우만)
            exchange_coins[sym] = {"qty": qty, "value": value, "last": last}
        except Exception as _e:
            # 시세 조회 실패 — 보수적으로 등록(remove 회피). value=None 마커로 add 후보에서도 제외
            print(f"  {sym} ticker 실패 ({_e}) — 보수 등록(remove 회피)")
            exchange_coins[sym] = {"qty": qty, "value": None, "last": None}
    print(f"거래소 보유: {len(exchange_coins)}건 — {list(exchange_coins.keys())}")

    # 5. diff
    # add 후보에서는 value=None(시세실패) 종목 제외 — entry/order_amount 계산 불가
    addable_exchange = {c for c, v in exchange_coins.items() if v.get("value") is not None}
    to_add = addable_exchange - set(state_coins.keys())
    to_remove = set(state_coins.keys()) - set(exchange_coins.keys())
    print(f"추가 필요: {to_add}")
    print(f"제거 필요: {to_remove}")

    # 5b. 잔량 미러링 (lessons #10/#25/#28 강화 — 2026-05-24)
    # 두 state 모두에 존재하는 종목이라도 entry_qty/remaining_qty가 거래소 실잔량과
    # 어긋날 수 있음 (부분 TP 발동 시 entry_qty=0 저장 버그 등). 잔량 차이가
    # 1% 또는 0.0001 BTC 이상이면 거래소 진실로 미러링 — 단, entry_qty 불변
    # 원칙(lessons #25)은 유지하기 위해 "최초 매수시" 누락된 경우만 보정한다.
    qty_fixes: list[tuple[str, dict[str, float]]] = []
    QTY_EPS_RATIO = 0.01  # 1%
    QTY_EPS_ABS = 1e-4    # 절대 임계 (BTC 등 소수점 종목)
    for coin, sym in state_coins.items():
        if coin not in exchange_coins:
            continue
        ex_info = exchange_coins[coin]
        if ex_info.get("value") is None:
            continue  # 시세 실패 — 보수 skip
        ex_qty = float(ex_info["qty"])
        st_pos = pos.get(sym, {})
        st_entry_qty = float(st_pos.get("entry_qty") or 0)
        st_remaining = float(st_pos.get("remaining_qty") or 0)
        # entry_qty=0이면 무조건 보정 (매수 시점 누락 버그 — lessons #25)
        if st_entry_qty <= 0:
            qty_fixes.append((sym, {
                "entry_qty": ex_qty,           # 거래소 잔량으로 복원 (TP 미발동 가정)
                "remaining_qty": ex_qty,
                "reason": "entry_qty=0 (매수 시점 누락 버그)",
            }))
            continue
        # remaining_qty 와 ex_qty 차이가 큰 경우 (TP 후 미갱신)
        gap = abs(st_remaining - ex_qty)
        if st_remaining > 0:
            ratio = gap / st_remaining
        else:
            ratio = 1.0 if ex_qty > 0 else 0.0
        if gap > QTY_EPS_ABS and ratio > QTY_EPS_RATIO:
            qty_fixes.append((sym, {
                "remaining_qty": ex_qty,
                "reason": f"remaining_qty drift ({st_remaining:.6g} → {ex_qty:.6g})",
            }))
    print(f"잔량 보정 필요: {len(qty_fixes)}건")
    for sym, fix in qty_fixes:
        print(f"  ~ {sym}: {fix['reason']}")

    multi_changed = bool(to_add or to_remove or qty_fixes)
    if not (to_add or to_remove):
        if qty_fixes:
            print("종목 일치 — 잔량만 보정")
        else:
            print("multi 일치 — multi 보정 스킵 (vb 처리는 계속)")

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

    # 7b. 잔량 미러링 적용 (lessons #10/#25 강화 — 2026-05-24)
    for sym, fix in qty_fixes:
        if sym not in pos:
            continue
        for k, v in fix.items():
            if k == "reason":
                continue
            pos[sym][k] = v
        print(f"  ~ {sym}: 잔량 보정 적용")

    # 8. 저장 (변경 있을 때만 — 백업 누적 방지)
    if multi_changed:
        state["positions"] = pos
        state["state_balance_fix_ts"] = ts
        STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n저장 완료(multi): {len(pos)}건")
        print(f"롤백(multi): cp {backup.name} {STATE_PATH.name}")
    else:
        backup.unlink(missing_ok=True)

    # 9. vb_state 보정 (lessons #28) — 거래소에 없는 vb 종목만 제거 (add 미실시)
    if VB_STATE_PATH.exists():
        vb_backup = VB_STATE_PATH.with_suffix(f".json.backup_{ts}")
        shutil.copy2(VB_STATE_PATH, vb_backup)
        vb_state = json.loads(VB_STATE_PATH.read_text(encoding="utf-8"))
        vb_pos = vb_state.get("positions", {})
        vb_state_coins = {s.split("/")[0]: s for s in vb_pos.keys()}
        vb_to_remove = set(vb_state_coins.keys()) - set(exchange_coins.keys())
        print(f"\nvb_state: {len(vb_pos)}건 — {list(vb_state_coins.keys())}")
        print(f"vb 제거 필요: {vb_to_remove}")
        if vb_to_remove:
            for coin in vb_to_remove:
                sym = vb_state_coins[coin]
                removed = vb_pos.pop(sym, None)
                ep = (removed or {}).get("entry_price") or 0
                print(f"  - vb {sym}: 제거 (entry {ep:,.4g})")
            vb_state["positions"] = vb_pos
            vb_state["state_balance_fix_ts"] = ts
            VB_STATE_PATH.write_text(
                json.dumps(vb_state, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"저장 완료(vb): {len(vb_pos)}건")
            print(f"롤백(vb): cp {vb_backup.name} {VB_STATE_PATH.name}")
        else:
            # 변경 없으면 백업 제거 (불필요한 누적 방지)
            vb_backup.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
