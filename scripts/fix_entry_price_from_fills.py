"""보유 포지션의 entry_price/entry_qty를 거래소 실제 체결값으로 보정 (lessons/20260822_4).

배경:
    업비트 create_market_*_order 응답에 체결 정보가 없어(average/filled/cost = None)
    호출부가 신호가로 폴백, 돌파 감지 시점 가격이 entry_price로 기록돼 왔다.
    코드는 lessons #43에서 수정됐으나 **이미 열려 있는 포지션의 기록은 그대로**이므로
    이 스크립트로 1회 보정한다.

보정 대상:
    entry_price       → fetch_order 재조회 평균 체결가
    entry_qty         → 실제 체결 수량(filled)
    entry_amount_krw  → 실제 체결 대금(cost)
    trail_stop        → 확정 체결가 기준 재계산 (기존값보다 낮추지 않음)

안전장치:
    - 상태 파일 백업 (timestamp suffix)
    - --dry-run 기본값. --apply 를 줘야 실제 기록
    - 체결 정보를 못 찾은 종목은 건너뜀 (임의 추정 금지)
    - trail_stop 은 max(기존, 재계산)으로만 이동 — 보정이 손절선을 느슨하게
      만들어 이미 확보한 보호를 되돌리는 일이 없도록 한다

사용:
    python scripts/fix_entry_price_from_fills.py            # 미리보기
    python scripts/fix_entry_price_from_fills.py --apply    # 실제 보정
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution.config import HARD_STOP_LOSS_PCT  # noqa: E402
from services.execution.upbit_client import _create_exchange, order_exec_price  # noqa: E402

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"


def _latest_buy_fill(exchange, symbol: str) -> dict | None:
    """가장 최근 매수 주문의 체결 정보.

    업비트 시장가 매수는 잔여 KRW 환불로 status='canceled' 종료되어
    fetch_closed_orders 에 잡히지 않는다 (lessons #43).
    """
    orders = []
    for fetch in (exchange.fetch_canceled_orders, exchange.fetch_closed_orders):
        try:
            orders.extend(fetch(symbol, limit=20) or [])
        except Exception:
            continue
    buys = [o for o in orders if o.get("side") == "buy" and float(o.get("filled") or 0) > 0]
    if not buys:
        return None
    buys.sort(key=lambda o: o.get("timestamp") or 0)
    return buys[-1]


def main() -> int:
    apply = "--apply" in sys.argv

    if not STATE_PATH.exists():
        print(f"상태 파일 없음: {STATE_PATH}")
        return 1

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    positions = state.get("positions", {})
    if not positions:
        print("보유 포지션 없음 — 보정할 대상이 없습니다.")
        return 0

    exchange = _create_exchange()
    changes: list[tuple[str, dict]] = []

    for symbol, pos in positions.items():
        order = _latest_buy_fill(exchange, symbol)
        if not order:
            print(f"  [건너뜀] {symbol} — 체결 주문을 찾지 못함 (추정 금지)")
            continue

        real_price = order_exec_price(order)
        real_qty = float(order.get("filled") or 0)
        real_cost = float(order.get("cost") or 0)
        if not real_price or real_qty <= 0:
            print(f"  [건너뜀] {symbol} — 체결가/수량 확정 실패")
            continue

        old_price = float(pos.get("entry_price") or 0)
        old_qty = float(pos.get("entry_qty") or 0)

        # 하드 손절 캡을 확정 체결가 기준으로 재계산.
        # 기존 trail_stop 이 이미 더 높다면(고점 갱신으로 상승) 그대로 둔다 —
        # 보정이 손절선을 낮춰 확보한 보호를 되돌리는 일이 없도록 max 사용.
        hard_floor = real_price * (1 - HARD_STOP_LOSS_PCT)
        new_trail = max(float(pos.get("trail_stop") or 0), hard_floor)

        if abs(old_price - real_price) < 1e-9 and abs(old_qty - real_qty) < 1e-9:
            print(f"  [일치] {symbol} — 보정 불필요")
            continue

        changes.append((symbol, {
            "entry_price": (old_price, real_price),
            "entry_qty": (old_qty, real_qty),
            "entry_amount_krw": (float(pos.get("entry_amount_krw") or 0), real_cost or None),
            "trail_stop": (float(pos.get("trail_stop") or 0), new_trail),
        }))

        if apply:
            pos["entry_price"] = real_price
            pos["entry_qty"] = real_qty
            if real_cost > 0:
                pos["entry_amount_krw"] = real_cost
            pos["trail_stop"] = new_trail
            if float(pos.get("highest") or 0) < real_price:
                pos["highest"] = real_price

    if not changes:
        print("\n보정 대상 없음 — 모든 포지션이 거래소와 일치합니다.")
        return 0

    print(f"\n{'종목':<12}{'항목':<18}{'기존':>16}{'보정':>16}")
    print("-" * 64)
    for symbol, diff in changes:
        for field, (old, new) in diff.items():
            if new is None:
                continue
            print(f"{symbol:<12}{field:<18}{old:>16,.4f}{new:>16,.4f}")

    if not apply:
        print(f"\n[DRY-RUN] {len(changes)}종목 보정 예정. 실제 적용: --apply")
        return 0

    backup = STATE_PATH.with_suffix(
        f".json.bak_entryfix_{datetime.now(tz=timezone.utc):%Y%m%d_%H%M%S}"
    )
    shutil.copy(STATE_PATH, backup)
    state["positions"] = positions
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n보정 완료 {len(changes)}종목. 백업: {backup.name}")
    print("봇 재시작 후 반영됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
