"""실전 자동매매 러너 — Donchian(50) + ATR(14)x3.0.

매일 1회 실행:
  1. 업비트 잔고 조회
  2. OHLCV 수집 + 전략 신호 확인
  3. 신호 발생 시 실제 주문 실행
  4. 상태 저장 + 텔레그램 알림

실행:
  python -m services.execution.trader            (일일 실행)
  python -m services.execution.trader --status   (상태 조회)
  python -m services.execution.trader --dry-run  (주문 없이 신호만 확인)
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.market_data.fetcher import fetch_ohlcv
from services.paper_trading.strategy import (
    check_entry, check_exit, calc_donchian_upper, calc_atr,
    DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, get_strategy_info,
)
from services.execution.upbit_client import get_balance, buy_market, sell_market
from services.alerting.notifier import send, notify_trade, notify_error

# 상태 파일
STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "live_trading_state.json"
LOG_FILE = Path(__file__).resolve().parents[2] / "workspace" / "live_trading_log.jsonl"

MIN_BARS = max(DONCHIAN_PERIOD, ATR_PERIOD) + 10
MIN_ORDER_KRW = 5_000  # 업비트 최소 주문


# ── 상태 관리 ──────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "is_holding": False,
        "entry_price": 0.0,
        "entry_date": "",
        "highest_since_entry": 0.0,
        "trailing_stop": 0.0,
        "position_btc": 0.0,
        "trades": [],
        "last_updated": "",
        "last_close": 0.0,
    }


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


# ── 데이터 수집 ───────────────────────────────────────
async def fetch_recent() -> pd.DataFrame:
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=MIN_BARS + 10)
    raw = await fetch_ohlcv(
        "BTC/KRW", "1d",
        start.strftime("%Y-%m-%dT00:00:00Z"),
        end.strftime("%Y-%m-%dT00:00:00Z"),
        use_cache=False,
    )
    return pd.DataFrame(raw)


# ── 메인 로직 ─────────────────────────────────────────
async def run(dry_run: bool = False):
    state = load_state()
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    info = get_strategy_info()

    print(f"[{today}] 실전 매매 {'(DRY-RUN)' if dry_run else ''}")
    print(f"  전략: {info['name']}")

    # 잔고 확인
    try:
        balance = get_balance()
        print(f"  잔고: KRW {balance['krw']:,.0f} | BTC {balance['btc']:.8f} "
              f"({balance['btc_krw_value']:,.0f} KRW) | 합계 {balance['total_krw']:,.0f} KRW")
    except Exception as e:
        msg = f"잔고 조회 실패: {e}"
        print(f"  ERROR: {msg}")
        await notify_error(msg)
        return

    # OHLCV 수집
    try:
        df = await fetch_recent()
    except Exception as e:
        msg = f"데이터 수집 실패: {e}"
        print(f"  ERROR: {msg}")
        await notify_error(msg)
        return

    if len(df) < MIN_BARS:
        msg = f"데이터 부족: {len(df)}일 < {MIN_BARS}일"
        print(f"  ERROR: {msg}")
        await notify_error(msg)
        return

    latest_close = float(df["close"].iloc[-1])
    latest_date = pd.Timestamp(int(df["ts"].iloc[-1]), unit="ms", tz="UTC").strftime("%Y-%m-%d")
    state["last_close"] = latest_close

    print(f"  BTC/KRW: {latest_close:,.0f} ({latest_date})")
    print(f"  포지션: {'보유중' if state['is_holding'] else '대기중'}")

    action = None

    if not state["is_holding"]:
        # ── 진입 확인 ──
        if check_entry(df):
            available_krw = balance["krw"]
            if available_krw < MIN_ORDER_KRW:
                print(f"  매수 신호 발생! 그러나 잔고 부족: {available_krw:,.0f} KRW")
                await send(f"⚠️ 매수 신호 발생했으나 KRW 잔고 부족\n잔고: {available_krw:,.0f} KRW")
            else:
                # 전체 KRW로 매수 (수수료 고려하여 99.9%)
                order_amount = available_krw * 0.999
                print(f"  *** 매수 신호! 금액: {order_amount:,.0f} KRW ***")

                if dry_run:
                    print("  [DRY-RUN] 실제 주문 생략")
                    order_result = {"id": "dry-run", "price": latest_close,
                                    "amount": order_amount / latest_close, "status": "dry-run"}
                else:
                    try:
                        order_result = buy_market(order_amount)
                        print(f"  주문 체결: {order_result}")
                    except Exception as e:
                        msg = f"매수 주문 실패: {e}"
                        print(f"  ERROR: {msg}")
                        await notify_error(msg)
                        return

                exec_price = order_result.get("price") or latest_close
                state["is_holding"] = True
                state["entry_price"] = exec_price
                state["entry_date"] = today
                state["highest_since_entry"] = exec_price
                state["position_btc"] = order_result.get("amount", 0)
                action = "BUY"

                append_log({"action": "BUY", "price": exec_price,
                            "amount_krw": order_amount, "order": order_result})
                await notify_trade("BUY", exec_price, balance["total_krw"], f"live_{today}")

        else:
            upper = calc_donchian_upper(df)
            if not pd.isna(upper.iloc[-1]):
                dist = (upper.iloc[-1] - latest_close) / latest_close * 100
                print(f"  Donchian 상단: {upper.iloc[-1]:,.0f} ({dist:+.1f}%)")
            print("  신호 없음 — 대기")

    else:
        # ── 청산 확인 ──
        should_exit, new_stop = check_exit(df, state["highest_since_entry"])

        if latest_close > state["highest_since_entry"]:
            state["highest_since_entry"] = latest_close
        state["trailing_stop"] = new_stop

        unrealized = (latest_close / state["entry_price"] - 1) * 100
        print(f"  진입가: {state['entry_price']:,.0f}  미실현: {unrealized:+.1f}%")
        print(f"  고점: {state['highest_since_entry']:,.0f}  스탑: {new_stop:,.0f}")

        if should_exit:
            btc_amount = balance["btc"]
            if btc_amount <= 0:
                print("  매도 신호! 그러나 BTC 잔고 없음")
                await send("⚠️ 매도 신호 발생했으나 BTC 잔고 없음")
            else:
                print(f"  *** 매도 신호! 수량: {btc_amount:.8f} BTC ***")

                if dry_run:
                    print("  [DRY-RUN] 실제 주문 생략")
                    order_result = {"id": "dry-run", "price": latest_close,
                                    "amount": btc_amount, "status": "dry-run"}
                else:
                    try:
                        order_result = sell_market(btc_amount)
                        print(f"  주문 체결: {order_result}")
                    except Exception as e:
                        msg = f"매도 주문 실패: {e}"
                        print(f"  ERROR: {msg}")
                        await notify_error(msg)
                        return

                exec_price = order_result.get("price") or latest_close
                ret_pct = (exec_price / state["entry_price"] - 1) * 100

                state["trades"].append({
                    "entry_date": state["entry_date"],
                    "entry_price": state["entry_price"],
                    "exit_date": today,
                    "exit_price": exec_price,
                    "return_pct": round(ret_pct, 2),
                })
                state["is_holding"] = False
                state["entry_price"] = 0.0
                state["highest_since_entry"] = 0.0
                state["trailing_stop"] = 0.0
                state["position_btc"] = 0.0
                action = "SELL"

                append_log({"action": "SELL", "price": exec_price,
                            "return_pct": ret_pct, "order": order_result})
                await notify_trade("SELL", exec_price, balance["total_krw"], f"live_{today}")

        else:
            print("  보유 유지")

    save_state(state)

    # 일일 요약
    new_balance = get_balance() if not dry_run else balance
    total = new_balance["total_krw"]
    print(f"\n  총 평가: {total:,.0f} KRW")

    summary = (
        f"📊 *일일 리포트* ({today})\n"
        f"BTC: {latest_close:,.0f} KRW\n"
        f"포지션: {'보유중' if state['is_holding'] else '대기중'}\n"
        f"평가금액: {total:,.0f} KRW\n"
        f"거래: {len(state['trades'])}회"
    )
    if state["is_holding"]:
        unrealized = (latest_close / state["entry_price"] - 1) * 100
        summary += f"\n미실현: {unrealized:+.1f}%"
    await send(summary)

    return action


def show_status():
    state = load_state()
    info = get_strategy_info()

    print("=" * 60)
    print("실전 매매 상태")
    print("=" * 60)
    print(f"  전략: {info['name']}")
    print(f"  마지막 업데이트: {state.get('last_updated', 'N/A')}")
    print(f"  포지션: {'보유중' if state['is_holding'] else '대기중'}")

    if state["is_holding"]:
        print(f"  진입가: {state['entry_price']:,.0f} KRW ({state['entry_date']})")
        print(f"  고점: {state['highest_since_entry']:,.0f}")
        print(f"  트레일링스탑: {state['trailing_stop']:,.0f}")

    trades = state.get("trades", [])
    print(f"  완료 거래: {len(trades)}회")
    for i, t in enumerate(trades, 1):
        print(f"    #{i} {t['entry_date']} @ {t['entry_price']:,.0f}"
              f" → {t['exit_date']} @ {t['exit_price']:,.0f} ({t['return_pct']:+.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        show_status()
    elif "--dry-run" in args:
        asyncio.run(run(dry_run=True))
    else:
        asyncio.run(run(dry_run=False))
