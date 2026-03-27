"""멀티코인 자동매매 러너.

매일 1회 실행:
  1. 업비트 KRW 마켓 전체 스캔 (243개)
  2. 거래대금 10억+ 필터 → Donchian(50) 돌파 종목 탐색
  3. 돌파 종목 매수 / 보유 종목 트레일링스탑 확인
  4. 자금 배분: 최대 동시 5포지션, 균등 배분

실행:
  python -m services.execution.multi_trader            (실전)
  python -m services.execution.multi_trader --dry-run  (주문 없이 확인)
  python -m services.execution.multi_trader --status   (상태)
  python -m services.execution.multi_trader --scan     (스캔만)
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.execution.scanner import (
    get_krw_market_coins, scan_entry_signals, check_exit_signal,
)
from services.execution.upbit_client import get_balance, buy_market, sell_market
from services.alerting.notifier import send, notify_error

# 설정
MAX_POSITIONS = 5           # 최대 동시 보유 종목
MIN_ORDER_KRW = 5_000       # 업비트 최소 주문

STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "multi_trading_state.json"
LOG_FILE = Path(__file__).resolve().parents[2] / "workspace" / "multi_trading_log.jsonl"


# ── 상태 관리 ──────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"positions": {}, "closed_trades": [], "last_updated": ""}


def save_state(state: dict):
    state["last_updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def append_log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["logged_at"] = datetime.now(tz=timezone.utc).isoformat()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def run(dry_run: bool = False):
    state = load_state()
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    positions = state.get("positions", {})

    print(f"[{today}] 멀티코인 자동매매 {'(DRY-RUN)' if dry_run else ''}")
    print(f"  전략: Donchian(50) + ATR(14)x3.0")
    print(f"  현재 보유: {len(positions)}종목 / 최대 {MAX_POSITIONS}종목")

    # ── 잔고 확인 ──────────────────────────────────────
    try:
        balance = get_balance()
        print(f"  KRW 잔고: {balance['krw']:,.0f}")
    except Exception as e:
        msg = f"잔고 조회 실패: {e}"
        print(f"  ERROR: {msg}")
        await notify_error(msg)
        return

    # ── 보유 종목 청산 확인 ────────────────────────────
    exit_symbols = []
    for symbol, pos in list(positions.items()):
        print(f"\n  [{symbol}] 청산 확인...")
        try:
            result = await check_exit_signal(
                symbol, pos["entry_price"], pos["highest"]
            )
            pos["highest"] = result["highest"]
            pos["trail_stop"] = result["trail_stop"]

            print(f"    현재: {result['price']:,.0f}  진입: {pos['entry_price']:,.0f}  "
                  f"미실현: {result['unrealized_pct']:+.1f}%")
            print(f"    고점: {result['highest']:,.0f}  스탑: {result['trail_stop']:,.0f}")

            if result["should_exit"]:
                print(f"    *** 매도 신호! ({result['reason']}) ***")
                exit_symbols.append(symbol)
            else:
                print(f"    보유 유지")
        except Exception as e:
            print(f"    오류: {e}")

    # ── 매도 실행 ──────────────────────────────────────
    for symbol in exit_symbols:
        pos = positions[symbol]
        coin_id = symbol.split("/")[0]

        if dry_run:
            print(f"  [DRY-RUN] {symbol} 매도 생략")
            exec_price = pos.get("trail_stop", 0)
        else:
            try:
                # 해당 코인 잔고 조회
                import ccxt
                from services.execution.upbit_client import _create_exchange
                ex = _create_exchange()
                bal = ex.fetch_balance()
                coin_amount = float(bal.get(coin_id, {}).get("free", 0))

                if coin_amount <= 0:
                    print(f"  {symbol} 잔고 없음 — 건너뜀")
                    continue

                order = sell_market_coin(symbol, coin_amount)
                exec_price = order.get("price", 0)
                print(f"  {symbol} 매도 체결: {exec_price:,.0f}")
            except Exception as e:
                msg = f"{symbol} 매도 실패: {e}"
                print(f"  ERROR: {msg}")
                await notify_error(msg)
                continue

        ret_pct = (exec_price / pos["entry_price"] - 1) * 100 if exec_price > 0 else 0
        state["closed_trades"].append({
            "symbol": symbol,
            "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"],
            "exit_date": today,
            "exit_price": exec_price,
            "return_pct": round(ret_pct, 2),
        })
        del positions[symbol]

        emoji = "🟢" if ret_pct > 0 else "🔴"
        await send(f"{emoji} *매도* {symbol}\n가격: {exec_price:,.0f}\n수익: {ret_pct:+.1f}%")
        append_log({"action": "SELL", "symbol": symbol, "price": exec_price, "return_pct": ret_pct})

    # ── 전체 스캔 ──────────────────────────────────────
    available_slots = MAX_POSITIONS - len(positions)
    print(f"\n  매수 가능 슬롯: {available_slots}개")

    if available_slots > 0:
        print("  전체 스캔 중...")
        coins = get_krw_market_coins()
        print(f"  거래대금 10억+ 종목: {len(coins)}개")

        signals = await scan_entry_signals(coins)
        buy_signals = [s for s in signals if s["signal"] == "BUY" and s["symbol"] not in positions]
        near_signals = [s for s in signals if s["signal"] == "NEAR" and s["symbol"] not in positions]

        print(f"  돌파 신호: {len(buy_signals)}개")
        print(f"  근접 종목 (3% 이내): {len(near_signals)}개")

        if near_signals:
            print("\n  [근접 종목]")
            for s in near_signals[:10]:
                print(f"    {s['symbol']:<12} 현재 {s['price']:>12,.0f}  "
                      f"상단 {s['donchian_upper']:>12,.0f}  ({s['distance_pct']:+.1f}%)")

        # ── 매수 실행 ─────────────────────────────────
        if buy_signals:
            # 거래대금 순으로 정렬 (유동성 높은 종목 우선)
            buy_signals.sort(key=lambda x: x["volume_krw"], reverse=True)

            # 가용 자금 배분
            available_krw = balance["krw"]
            if exit_symbols and not dry_run:
                # 매도 후 잔고 재조회
                balance = get_balance()
                available_krw = balance["krw"]

            per_position = available_krw * 0.95 / min(len(buy_signals), available_slots)

            for sig in buy_signals[:available_slots]:
                symbol = sig["symbol"]
                order_amount = min(per_position, available_krw * 0.95)

                if order_amount < MIN_ORDER_KRW:
                    print(f"  {symbol} 잔고 부족 ({order_amount:,.0f} KRW) — 건너뜀")
                    continue

                print(f"\n  *** {symbol} 매수 신호! ***")
                print(f"    가격: {sig['price']:,.0f}  Donchian상단: {sig['donchian_upper']:,.0f}")
                print(f"    트레일링스탑: {sig['trail_stop']:,.0f}  금액: {order_amount:,.0f} KRW")

                if dry_run:
                    print(f"  [DRY-RUN] 매수 생략 (상태 저장 안 함)")
                    continue
                else:
                    try:
                        order = buy_market_coin(symbol, order_amount)
                        exec_price = order.get("price", sig["price"])
                        print(f"  매수 체결: {exec_price:,.0f}")
                    except Exception as e:
                        msg = f"{symbol} 매수 실패: {e}"
                        print(f"  ERROR: {msg}")
                        await notify_error(msg)
                        continue

                positions[symbol] = {
                    "entry_date": today,
                    "entry_price": exec_price,
                    "highest": exec_price,
                    "trail_stop": sig["trail_stop"],
                    "order_amount": order_amount,
                }
                available_krw -= order_amount

                await send(f"🟢 *매수* {symbol}\n가격: {exec_price:,.0f}\n금액: {order_amount:,.0f} KRW")
                append_log({"action": "BUY", "symbol": symbol, "price": exec_price, "amount_krw": order_amount})

    # ── 상태 저장 ──────────────────────────────────────
    state["positions"] = positions
    save_state(state)

    # ── 일일 요약 ──────────────────────────────────────
    closed = state.get("closed_trades", [])
    wins = [t for t in closed if t["return_pct"] > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0

    summary = (
        f"📊 *일일 리포트* ({today})\n"
        f"보유: {len(positions)}종목"
    )
    for sym, pos in positions.items():
        summary += f"\n  {sym} @ {pos['entry_price']:,.0f}"
    summary += f"\n거래: {len(closed)}회 (승률 {win_rate:.0f}%)"
    summary += f"\nKRW: {balance['krw']:,.0f}"

    await send(summary)
    print(f"\n  보유: {len(positions)}종목, 완료거래: {len(closed)}회, 승률: {win_rate:.0f}%")


def sell_market_coin(symbol: str, amount: float) -> dict:
    """특정 코인 시장가 매도."""
    import os
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    exchange = ccxt.upbit({
        "apiKey": os.environ.get("UPBIT_ACCESS_KEY"),
        "secret": os.environ.get("UPBIT_SECRET_KEY"),
        "enableRateLimit": True,
    })
    order = exchange.create_market_sell_order(symbol, amount)
    return {"id": order.get("id"), "price": order.get("average") or order.get("price"),
            "amount": order.get("amount"), "status": order.get("status")}


def buy_market_coin(symbol: str, amount_krw: float) -> dict:
    """특정 코인 시장가 매수."""
    import os
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    exchange = ccxt.upbit({
        "apiKey": os.environ.get("UPBIT_ACCESS_KEY"),
        "secret": os.environ.get("UPBIT_SECRET_KEY"),
        "enableRateLimit": True,
    })
    order = exchange.create_market_buy_order(symbol, None, params={"cost": amount_krw})
    return {"id": order.get("id"), "price": order.get("average") or order.get("price"),
            "amount": order.get("amount"), "status": order.get("status")}


def show_status():
    state = load_state()
    positions = state.get("positions", {})
    closed = state.get("closed_trades", [])

    print("=" * 60)
    print("멀티코인 자동매매 상태")
    print("=" * 60)
    print(f"  전략: Donchian(50) + ATR(14)x3.0 (전체 스캔)")
    print(f"  마지막: {state.get('last_updated', 'N/A')}")
    print(f"  보유: {len(positions)} / {MAX_POSITIONS}")

    if positions:
        print("\n  [보유 종목]")
        for sym, pos in positions.items():
            print(f"    {sym:<12} 진입 {pos['entry_price']:>12,.0f}  "
                  f"스탑 {pos['trail_stop']:>12,.0f}  ({pos['entry_date']})")

    if closed:
        wins = [t for t in closed if t["return_pct"] > 0]
        total_ret = sum(t["return_pct"] for t in closed)
        print(f"\n  [거래 이력] {len(closed)}회, 승률 {len(wins)/len(closed)*100:.0f}%, 누적 {total_ret:+.1f}%")
        for t in closed[-10:]:
            emoji = "+" if t["return_pct"] > 0 else ""
            print(f"    {t['symbol']:<12} {t['entry_date']}→{t['exit_date']}  {emoji}{t['return_pct']:.1f}%")

    print("=" * 60)


async def scan_only():
    """스캔만 실행 (매매 없이 신호 확인)."""
    print("=" * 60)
    print("전체 스캔 — 돌파 및 근접 종목")
    print("=" * 60)

    coins = get_krw_market_coins()
    print(f"거래대금 10억+ 종목: {len(coins)}개\n")

    signals = await scan_entry_signals(coins)
    buy_signals = [s for s in signals if s["signal"] == "BUY"]
    near_signals = [s for s in signals if s["signal"] == "NEAR"]

    if buy_signals:
        print(f"[돌파 신호] {len(buy_signals)}개")
        for s in buy_signals:
            print(f"  {s['symbol']:<12} {s['price']:>12,.0f}  "
                  f"상단 {s['donchian_upper']:>12,.0f}  거래대금 {s['volume_krw']/1e8:,.0f}억")
    else:
        print("[돌파 신호] 없음")

    if near_signals:
        print(f"\n[근접 종목 3% 이내] {len(near_signals)}개")
        for s in near_signals:
            print(f"  {s['symbol']:<12} {s['price']:>12,.0f}  "
                  f"상단 {s['donchian_upper']:>12,.0f}  ({s['distance_pct']:+.1f}%)")
    else:
        print("\n[근접 종목] 없음")

    print("=" * 60)


def reset_state():
    """상태 초기화."""
    state = {"positions": {}, "closed_trades": [], "last_updated": ""}
    save_state(state)
    print("상태 초기화 완료.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        show_status()
    elif "--scan" in args:
        asyncio.run(scan_only())
    elif "--reset" in args:
        reset_state()
    elif "--dry-run" in args:
        asyncio.run(run(dry_run=True))
    else:
        asyncio.run(run(dry_run=False))
