"""수동 청산/부분 정리 — 사용자 지시로 포지션을 닫거나 줄인다.

## 왜 스크립트가 필요한가 (직접 거래소에서 팔면 안 되나)

팔아도 된다. 봇에 `_close_manually_sold()`(1시간 교차검증 3회)가 있어 결국은 정리된다.
문제는 **더 빠른 경로가 먼저 도착한다**는 것이다 — `_check_tp_levels`의 잔고 0 감지
(`_orphan_seen_count` 3회, 틱 주기)가 몇 분 안에 발동해
`_close_position(..., "auto_cleanup_zero_balance")`로 닫아버린다. 그 시점의
`realized_pl_krw`에는 **수동 매도 대금이 없으므로** TP 실현분만으로 수익률이 계산된다.
실측 예: JUP 잔량을 +7.4%에 팔아도 `closed_trades`에는 TP1분만 반영된 +2.5%가 남는다.

즉 "그냥 팔기"는 **표본을 조용히 왜곡**한다(lessons #45 계열). 그래서 매도와 회계를
한 트랜잭션으로 묶는다.

## 회계 원칙 (lessons #25, #45)

- `entry_qty` / `entry_amount_krw`는 **불변 입력** — 부분 정리에도 건드리지 않는다
- 매도 대금은 `position_pnl.record_realized()`로 **가변 추적**에 누적
- 최종 수익률은 money-weighted (`realized_pl_krw / entry_amount_krw`)
- 체결가는 `sell_market_coin`이 `fetch_order` 재조회로 확정한 값 (lessons #43)

## 봇을 반드시 먼저 멈춘다

봇은 `self.state`를 메모리에 들고 주기적으로 저장한다. 가동 중에 state 파일을 고치면
다음 저장에서 덮어써진다. 이 스크립트는 `btc-trader.service`가 active면 거부한다.

## 사용

    # 미리보기
    python scripts/manual_close_position.py JUP/KRW

    # 전량 청산 (봇 정지 후)
    python scripts/manual_close_position.py JUP/KRW --apply --reason manual_tp

    # 부분 정리 — 비중 축소 (포지션은 유지, 실현손익만 누적)
    python scripts/manual_close_position.py OP/KRW --qty 630.1 --apply --reason weight_trim
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATE_PATH = ROOT / "workspace" / "multi_trading_state.json"


def _bot_active() -> bool:
    """btc-trader.service 가동 여부. systemctl이 없는 환경(로컬)에서는 False."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "btc-trader"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="예: JUP/KRW")
    ap.add_argument("--qty", default="all",
                    help="매도 수량. 'all'(기본)이면 전량 청산 후 슬롯 반환")
    ap.add_argument("--reason", default="manual_close",
                    help="closed_trades.exit_reason 에 남길 사유 "
                         "(전략 매도와 구분되어야 성과 집계에서 분리 가능)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="봇 가동 중에도 강행 (state 덮어쓰기 위험 — 권장하지 않음)")
    args = ap.parse_args()

    from services.execution.config import FEE_RATE, MIN_ORDER_KRW, POSITION_DUST_KRW
    from services.execution.position_pnl import record_realized, position_return_pct

    if not STATE_PATH.exists():
        print(f"상태 파일 없음: {STATE_PATH}")
        return 1
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    pos = state.get("positions", {}).get(args.symbol)
    if pos is None:
        print(f"보유 포지션 아님: {args.symbol} (보유: {list(state.get('positions', {}))})")
        return 1

    from services.execution.upbit_client import _create_exchange
    ex = _create_exchange()
    coin = args.symbol.split("/")[0]
    bal = ex.fetch_balance()
    free = float(bal.get(coin, {}).get("free", 0) or 0)
    total = float(bal.get(coin, {}).get("total", 0) or 0)
    px = float(ex.fetch_ticker(args.symbol)["last"])

    entry = float(pos.get("entry_price") or 0)
    basis = float(pos.get("entry_amount_krw") or 0)
    sell_qty = free if args.qty == "all" else round(min(float(args.qty), free), 8)
    full = args.qty == "all" or sell_qty >= total - 1e-9

    print("=" * 66)
    print(f"수동 {'전량 청산' if full else '부분 정리'} — {args.symbol}")
    print("=" * 66)
    print(f"  진입가       : {entry:,.4g}  (진입금 {basis:,.0f}원)")
    print(f"  현재가       : {px:,.4g}  ({(px / entry - 1) * 100:+.2f}%)")
    print(f"  거래소 잔량  : total {total:,.8g} / free {free:,.8g}")
    print(f"  매도 수량    : {sell_qty:,.8g}  ≈ {sell_qty * px:,.0f}원")
    print(f"  이미 실현    : {float(pos.get('realized_pl_krw') or 0):,.0f}원 "
          f"(TP 단계 {pos.get('tp_sold_levels')})")

    if sell_qty <= 0:
        print("\n매도 수량 0 — 중단")
        return 1
    if sell_qty * px < MIN_ORDER_KRW:
        print(f"\n매도액이 최소주문({MIN_ORDER_KRW:,}원) 미만 — 중단")
        return 1

    net_est = px * sell_qty * (1 - FEE_RATE) - entry * sell_qty * (1 + FEE_RATE)
    print(f"  예상 실현손익: {net_est:+,.0f}원 (현재가 기준, 실제는 체결가로 재계산)")
    if not full:
        left = total - sell_qty
        print(f"  정리 후 잔량 : {left:,.8g} ≈ {left * px:,.0f}원 "
              f"(포지션 유지, entry_qty/entry_amount_krw 불변)")
        print(f"  ⚠ 이후 TP는 entry_qty 기준 비율이 잔량을 넘으므로 "
              f"다음 TP에서 전량 매도된다")

    if not args.apply:
        print("\n[DRY-RUN] 실제 실행: --apply")
        return 0

    if _bot_active() and not args.force:
        print("\n btc-trader 가 가동 중이다 — state 수정이 덮어써진다.")
        print("   sudo systemctl stop btc-trader  →  이 스크립트  →  start")
        return 1

    # ── 매도 (lessons #3: 매도 경로에 retry 금지) ──────────────────
    from services.execution.multi_trader import sell_market_coin
    order = sell_market_coin(args.symbol, sell_qty)
    exec_price = order.get("price")
    if not exec_price:
        print("  [경고] 체결가 확정 실패 — 현재가로 폴백 기록 (lessons #43)")
        exec_price = px
    exec_price = float(exec_price)
    filled = float(order.get("amount") or sell_qty)
    print(f"\n  체결: {filled:,.8g} @ {exec_price:,.4g}  (status={order.get('status')})")

    backup = STATE_PATH.with_suffix(
        f".json.bak_manual_{datetime.now(tz=timezone.utc):%Y%m%d_%H%M%S}"
    )
    shutil.copy(STATE_PATH, backup)

    net = record_realized(pos, exec_price, filled)
    print(f"  실현손익 반영: {net:+,.0f}원 "
          f"(누적 {float(pos['realized_pl_krw']):,.0f}원)")

    left_qty = max(total - filled, 0.0)
    if full or left_qty * exec_price < POSITION_DUST_KRW:
        ret = position_return_pct(pos, fallback_price=exec_price)
        state.setdefault("closed_trades", []).append({
            "symbol": args.symbol,
            "entry_date": pos.get("entry_date", ""),
            "entry_price": pos.get("entry_price", 0),
            "exit_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "exit_price": exec_price,
            "return_pct": round(ret, 2),
            "exit_reason": args.reason,
        })
        state["positions"].pop(args.symbol, None)
        print(f"  청산 확정: return {ret:+.2f}% (money-weighted) — 슬롯 반환")
    else:
        pos["remaining_qty"] = left_qty
        print(f"  포지션 유지: 잔량 {left_qty:,.8g}")

    state["last_updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)
    print(f"\n완료. 백업: {backup.name}")
    print("봇을 다시 기동하세요: sudo systemctl start btc-trader")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
