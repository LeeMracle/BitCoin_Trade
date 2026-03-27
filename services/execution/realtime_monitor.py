"""실시간 웹소켓 모니터 — 업비트 전체 종목 감시.

업비트 웹소켓으로 실시간 체결가를 수신하여
Donchian(50) 돌파 즉시 매수 신호 발생.

동작:
  1. 시작 시 전체 종목 Donchian 상단 계산 (일봉 기준)
  2. 웹소켓으로 실시간 체결가 수신
  3. 체결가 > Donchian 상단 → 즉시 매수
  4. 보유 종목 체결가 < 트레일링스탑 → 즉시 매도
  5. 매일 09:05 KST에 Donchian 상단 갱신
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import aiohttp
import numpy as np
import pandas as pd

from services.execution.config import (
    DONCHIAN_PERIOD, ATR_PERIOD, ATR_MULTIPLIER,
    MAX_POSITIONS, POSITION_RATIO, MIN_VOLUME_KRW,
    MIN_ORDER_KRW, DRY_RUN, EXCLUDE_SYMBOLS, MIN_LISTING_DAYS,
    NOTIFY_ON_BUY, NOTIFY_ON_SELL, NOTIFY_DAILY_REPORT, NOTIFY_NEAR_SIGNAL,
)
from services.execution.multi_trader import (
    load_state, save_state, append_log,
    buy_market_coin, sell_market_coin,
)
from services.execution.scanner import get_krw_market_coins
from services.market_data.fetcher import fetch_ohlcv
from services.paper_trading.strategy import calc_atr, calc_donchian_upper
from services.execution.upbit_client import get_balance
from services.alerting.notifier import send, notify_error

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
REFRESH_HOUR_UTC = 0  # UTC 00:00 = KST 09:00


class RealtimeMonitor:
    def __init__(self):
        self.levels: dict[str, dict] = {}    # {symbol: {upper, atr, ...}}
        self.state: dict = load_state()
        self.running = True
        self.last_refresh_date = ""

    async def start(self):
        print("=" * 60)
        print("실시간 모니터 시작")
        print(f"  전략: Donchian({DONCHIAN_PERIOD}) + ATR({ATR_PERIOD})x{ATR_MULTIPLIER}")
        print(f"  최대 포지션: {MAX_POSITIONS}")
        print(f"  DRY-RUN: {DRY_RUN}")
        print("=" * 60)

        await self._refresh_levels()
        await self._run_websocket()

    async def _refresh_levels(self):
        """전체 종목 Donchian 상단 + ATR 계산."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if self.last_refresh_date == today:
            return
        self.last_refresh_date = today

        print(f"\n[{today}] Donchian/ATR 레벨 갱신 중...")

        coins = get_krw_market_coins()
        print(f"  거래대금 필터 통과: {len(coins)}개")

        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=MIN_LISTING_DAYS + 10)
        start_str = start.strftime("%Y-%m-%dT00:00:00Z")
        end_str = end.strftime("%Y-%m-%dT00:00:00Z")

        new_levels = {}
        for coin in coins:
            symbol = coin["symbol"]
            try:
                raw = await fetch_ohlcv(symbol, "1d", start_str, end_str, use_cache=False)
                df = pd.DataFrame(raw)
                if len(df) < MIN_LISTING_DAYS:
                    continue

                upper = calc_donchian_upper(df, DONCHIAN_PERIOD)
                atr = calc_atr(df, ATR_PERIOD)

                latest_upper = upper.iloc[-1]
                latest_atr = atr.iloc[-1]

                if np.isnan(latest_upper) or np.isnan(latest_atr):
                    continue

                new_levels[symbol] = {
                    "upper": float(latest_upper),
                    "atr": float(latest_atr),
                    "close": float(df["close"].iloc[-1]),
                    "volume_krw": coin["volume_krw"],
                }

                await asyncio.sleep(0.12)
            except Exception:
                continue

        self.levels = new_levels
        print(f"  레벨 계산 완료: {len(self.levels)}개 종목")

        # 보유 종목 트레일링스탑 갱신
        positions = self.state.get("positions", {})
        for symbol, pos in positions.items():
            if symbol in self.levels:
                atr_val = self.levels[symbol]["atr"]
                pos["trail_stop"] = pos["highest"] - atr_val * ATR_MULTIPLIER

        save_state(self.state)

        # 일일 리포트
        if NOTIFY_DAILY_REPORT:
            await self._send_daily_report()

    async def _send_daily_report(self):
        positions = self.state.get("positions", {})
        closed = self.state.get("closed_trades", [])
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        try:
            balance = get_balance()
            krw = balance["krw"]
        except Exception:
            krw = 0

        near = [
            (sym, lv) for sym, lv in self.levels.items()
            if sym not in positions
            and lv["close"] > 0
            and (lv["upper"] - lv["close"]) / lv["close"] <= 0.03
        ]

        msg = f"📊 *일일 리포트* ({today})\n"
        msg += f"감시: {len(self.levels)}종목\n"
        msg += f"보유: {len(positions)}/{MAX_POSITIONS}\n"

        for sym, pos in positions.items():
            price = self.levels.get(sym, {}).get("close", 0)
            if price > 0 and pos["entry_price"] > 0:
                ret = (price / pos["entry_price"] - 1) * 100
                msg += f"  {sym} {ret:+.1f}%\n"

        if near and NOTIFY_NEAR_SIGNAL:
            msg += f"\n근접({len(near)}개):\n"
            for sym, lv in near[:5]:
                dist = (lv["upper"] - lv["close"]) / lv["close"] * 100
                msg += f"  {sym} ({dist:+.1f}%)\n"

        wins = [t for t in closed if t["return_pct"] > 0]
        msg += f"\n거래: {len(closed)}회"
        if closed:
            msg += f" 승률: {len(wins)/len(closed)*100:.0f}%"
        msg += f"\nKRW: {krw:,.0f}"

        await send(msg)

    async def _run_websocket(self):
        """웹소켓 연결 및 실시간 체결가 수신."""
        while self.running:
            try:
                print("\n웹소켓 연결 중...")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(UPBIT_WS_URL, heartbeat=30) as ws:
                        # 구독 요청
                        market_codes = [
                            sym.replace("/", "-").replace("KRW-", "KRW-")
                            for sym in self.levels.keys()
                        ]
                        # 업비트 형식: KRW-BTC
                        upbit_codes = []
                        for sym in self.levels.keys():
                            coin = sym.split("/")[0]
                            upbit_codes.append(f"KRW-{coin}")

                        subscribe = [
                            {"ticket": str(uuid.uuid4())[:8]},
                            {"type": "ticker", "codes": upbit_codes, "isOnlyRealtime": True},
                        ]
                        await ws.send_json(subscribe)
                        print(f"  구독 완료: {len(upbit_codes)}개 종목")

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                data = json.loads(msg.data.decode("utf-8"))
                                await self._handle_tick(data)

                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                print("웹소켓 연결 종료")
                                break

                            # 매일 갱신 체크
                            now_utc = datetime.now(tz=timezone.utc)
                            if (now_utc.hour == REFRESH_HOUR_UTC and
                                    now_utc.strftime("%Y-%m-%d") != self.last_refresh_date):
                                await self._refresh_levels()
                                # 재구독 필요 → 루프 탈출 후 재연결
                                break

            except Exception as e:
                print(f"웹소켓 오류: {e}")
                await notify_error(f"웹소켓 오류: {e}")

            if self.running:
                print("5초 후 재연결...")
                await asyncio.sleep(5)

    async def _handle_tick(self, data: dict):
        """실시간 체결 처리."""
        code = data.get("code", "")          # "KRW-BTC"
        price = data.get("trade_price", 0)   # 체결가

        if not code or price <= 0:
            return

        # 업비트 코드 → 심볼 변환: KRW-BTC → BTC/KRW
        coin = code.replace("KRW-", "")
        symbol = f"{coin}/KRW"

        positions = self.state.get("positions", {})

        # ── 보유 종목: 청산 확인 ──
        if symbol in positions:
            pos = positions[symbol]

            # 고점 갱신
            if price > pos["highest"]:
                pos["highest"] = price
                if symbol in self.levels:
                    atr_val = self.levels[symbol]["atr"]
                    pos["trail_stop"] = price - atr_val * ATR_MULTIPLIER

            # 트레일링스탑 이탈
            if price < pos.get("trail_stop", 0):
                await self._execute_sell(symbol, price)
            return

        # ── 미보유 종목: 진입 확인 ──
        if len(positions) >= MAX_POSITIONS:
            return

        if symbol not in self.levels:
            return

        level = self.levels[symbol]
        if price > level["upper"]:
            await self._execute_buy(symbol, price, level)

    async def _execute_buy(self, symbol: str, price: float, level: dict):
        positions = self.state.get("positions", {})
        if symbol in positions:
            return

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        trail_stop = price - level["atr"] * ATR_MULTIPLIER

        try:
            balance = get_balance()
            available = balance["krw"]
        except Exception as e:
            await notify_error(f"잔고 조회 실패: {e}")
            return

        slots_empty = MAX_POSITIONS - len(positions)
        order_amount = available * POSITION_RATIO / slots_empty

        if order_amount < MIN_ORDER_KRW:
            return

        print(f"\n  *** {symbol} 돌파! 가격: {price:,.0f}  상단: {level['upper']:,.0f} ***")

        if DRY_RUN:
            print(f"  [DRY-RUN] 매수 생략")
            exec_price = price
        else:
            try:
                order = buy_market_coin(symbol, order_amount)
                exec_price = order.get("price") or price
                print(f"  매수 체결: {exec_price:,.0f}")
            except Exception as e:
                await notify_error(f"{symbol} 매수 실패: {e}")
                return

        positions[symbol] = {
            "entry_date": today,
            "entry_price": exec_price,
            "highest": exec_price,
            "trail_stop": trail_stop,
            "order_amount": order_amount,
        }
        self.state["positions"] = positions
        save_state(self.state)

        append_log({"action": "BUY", "symbol": symbol, "price": exec_price,
                     "amount_krw": order_amount, "trigger": "realtime"})

        if NOTIFY_ON_BUY:
            await send(
                f"🟢 *매수* {symbol}\n"
                f"가격: {exec_price:,.0f}\n"
                f"금액: {order_amount:,.0f} KRW\n"
                f"스탑: {trail_stop:,.0f}"
            )

    async def _execute_sell(self, symbol: str, price: float):
        positions = self.state.get("positions", {})
        if symbol not in positions:
            return

        pos = positions[symbol]
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        print(f"\n  *** {symbol} 스탑 이탈! 가격: {price:,.0f}  스탑: {pos['trail_stop']:,.0f} ***")

        if DRY_RUN:
            print(f"  [DRY-RUN] 매도 생략")
            exec_price = price
        else:
            try:
                coin_id = symbol.split("/")[0]
                from services.execution.upbit_client import _create_exchange
                ex = _create_exchange()
                bal = ex.fetch_balance()
                coin_amount = float(bal.get(coin_id, {}).get("free", 0))

                if coin_amount <= 0:
                    print(f"  {symbol} 잔고 없음")
                    return

                order = sell_market_coin(symbol, coin_amount)
                exec_price = order.get("price") or price
                print(f"  매도 체결: {exec_price:,.0f}")
            except Exception as e:
                await notify_error(f"{symbol} 매도 실패: {e}")
                return

        ret_pct = (exec_price / pos["entry_price"] - 1) * 100

        self.state.setdefault("closed_trades", []).append({
            "symbol": symbol, "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"], "exit_date": today,
            "exit_price": exec_price, "return_pct": round(ret_pct, 2),
        })
        del positions[symbol]
        self.state["positions"] = positions
        save_state(self.state)

        append_log({"action": "SELL", "symbol": symbol, "price": exec_price,
                     "return_pct": ret_pct, "trigger": "realtime"})

        if NOTIFY_ON_SELL:
            emoji = "🟢" if ret_pct > 0 else "🔴"
            await send(
                f"{emoji} *매도* {symbol}\n"
                f"가격: {exec_price:,.0f}\n"
                f"수익: {ret_pct:+.1f}%"
            )


async def main():
    monitor = RealtimeMonitor()
    await monitor.start()


if __name__ == "__main__":
    asyncio.run(main())
