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
import ccxt
import numpy as np
import pandas as pd

from services.execution.config import (
    STRATEGY, STRATEGY_KWARGS,
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
from services.execution.upbit_client import get_balance, _create_exchange
from services.alerting.notifier import send, notify_error

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
REFRESH_HOUR_UTC = 0  # UTC 00:00 = KST 09:00

# daytrading 전략: 4시간봉 사용, 4시간마다 갱신
IS_DAYTRADING = STRATEGY == "daytrading"
_DT_TIMEFRAME = "4h" if IS_DAYTRADING else "1d"
_DT_LOOKBACK_DAYS = 120 if IS_DAYTRADING else max(MIN_LISTING_DAYS + 10, DONCHIAN_PERIOD + 80)
_DT_DC_PERIOD = STRATEGY_KWARGS.get("dc_period", 15 if IS_DAYTRADING else DONCHIAN_PERIOD)
_DT_VOL_THRESHOLD = STRATEGY_KWARGS.get("vol_threshold", 2.5 if IS_DAYTRADING else 1.5)
_DT_TRAIL_PCT = STRATEGY_KWARGS.get("trail_pct", 0.02)  # daytrading: 2% 고정
_DT_SL_PCT = STRATEGY_KWARGS.get("sl_pct", 0.015)       # daytrading: 1.5% 손절
_DT_MAX_BARS = STRATEGY_KWARGS.get("max_bars", 12)       # daytrading: 12봉(48h)
_DT_TREND_PERIOD = STRATEGY_KWARGS.get("trend_period", 50)

# ── 안전장치 설정 ─────────────────────────────────────
MAX_CONSECUTIVE_ERRORS = 5     # 연속 오류 N회 시 봇 중지
ERROR_COOLDOWN_SEC = 60        # 오류 발생 후 동일 종목 재시도 대기 (초)
ALERT_COOLDOWN_SEC = 300       # 동일 오류 알림 간격 (5분)


VR_STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "vol_reversal_dryrun_state.json"
VR_TP = 0.03     # vol_reversal 익절 +3%
VR_TRAIL = 0.015  # vol_reversal 트레일링 1.5%
VR_SL = 0.02      # vol_reversal 손절 -2%
VR_MAX_HOURS = 32  # vol_reversal 시간제한 32시간


class RealtimeMonitor:
    def __init__(self):
        self.levels: dict[str, dict] = {}    # {symbol: {upper, atr, ...}}
        self.state: dict = load_state()
        self.running = True
        self.last_refresh_date = ""
        # 안전장치
        self.consecutive_errors = 0
        self.error_cooldown: dict[str, float] = {}   # {symbol: timestamp}
        self.last_alert_time: dict[str, float] = {}   # {error_key: timestamp}

    async def start(self):
        print("=" * 60, flush=True)
        print("실시간 모니터 시작", flush=True)
        if IS_DAYTRADING:
            print(f"  전략: daytrading DC({_DT_DC_PERIOD})+Vol{_DT_VOL_THRESHOLD}x+Trail{_DT_TRAIL_PCT*100}% (4h)", flush=True)
        else:
            print(f"  전략: {STRATEGY} Donchian({DONCHIAN_PERIOD}) + ATR({ATR_PERIOD})x{ATR_MULTIPLIER}", flush=True)
        print(f"  최대 포지션: {MAX_POSITIONS}", flush=True)
        print(f"  DRY-RUN: {DRY_RUN}", flush=True)
        print("=" * 60, flush=True)

        await self._refresh_levels()
        await self._run_websocket()

    async def _refresh_levels(self):
        """전체 종목 레벨 갱신 (전략에 따라 일봉 또는 4시간봉)."""
        now = datetime.now(tz=timezone.utc)
        refresh_key = now.strftime("%Y-%m-%d") if not IS_DAYTRADING else now.strftime("%Y-%m-%d-%H")

        # daytrading: 4시간마다 갱신, 기타: 일 1회
        if IS_DAYTRADING:
            if (self.last_refresh_date == refresh_key[:13]):  # 같은 시간대면 스킵
                return
        else:
            if self.last_refresh_date == refresh_key:
                return
        self.last_refresh_date = refresh_key[:13] if IS_DAYTRADING else refresh_key

        tf_label = _DT_TIMEFRAME
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M')}] 레벨 갱신 ({tf_label}, DC{_DT_DC_PERIOD})...")

        coins = get_krw_market_coins()
        print(f"  거래대금 필터 통과: {len(coins)}개")

        end = now
        start = end - timedelta(days=_DT_LOOKBACK_DAYS)
        start_str = start.strftime("%Y-%m-%dT00:00:00Z")
        end_str = end.strftime("%Y-%m-%dT00:00:00Z")

        new_levels = {}
        total = len(coins)
        for idx, coin in enumerate(coins, 1):
            symbol = coin["symbol"]
            try:
                raw = await fetch_ohlcv(symbol, _DT_TIMEFRAME, start_str, end_str, use_cache=False)
                df = pd.DataFrame(raw)
                min_bars = _DT_DC_PERIOD + _DT_TREND_PERIOD + 5
                if len(df) < min_bars:
                    continue

                upper = calc_donchian_upper(df, _DT_DC_PERIOD)
                atr = calc_atr(df, ATR_PERIOD)

                latest_upper = upper.iloc[-1]
                latest_atr = atr.iloc[-1]
                latest_close = float(df["close"].iloc[-1])

                if np.isnan(latest_upper) or np.isnan(latest_atr):
                    continue

                level = {
                    "upper": float(latest_upper),
                    "atr": float(latest_atr),
                    "close": latest_close,
                    "volume_krw": coin["volume_krw"],
                }

                # daytrading: 추세 필터 + 거래량 조건 사전 계산
                if IS_DAYTRADING:
                    sma_trend = float(pd.Series(df["close"]).rolling(_DT_TREND_PERIOD).mean().iloc[-1])
                    vol_sma = float(pd.Series(df["volume"]).rolling(20).mean().iloc[-1])
                    latest_vol = float(df["volume"].iloc[-1])
                    level["sma_trend"] = sma_trend
                    level["vol_sma"] = vol_sma
                    level["latest_vol"] = latest_vol
                    level["trend_ok"] = latest_close > sma_trend
                    level["vol_ok"] = latest_vol > vol_sma * _DT_VOL_THRESHOLD

                new_levels[symbol] = level
                await asyncio.sleep(0.12)
            except Exception:
                continue

            if idx % 10 == 0 or idx == total:
                print(f"  진행: {idx}/{total} ({len(new_levels)}개 등록)", flush=True)

        self.levels = new_levels
        print(f"  레벨 계산 완료: {len(self.levels)}개 종목", flush=True)

        # 보유 종목 트레일링스탑 갱신 (전략 전환 시 기존 스탑 보존)
        positions = self.state.get("positions", {})
        for symbol, pos in positions.items():
            old_stop = pos.get("trail_stop", 0)
            if IS_DAYTRADING:
                new_stop = pos["highest"] * (1 - _DT_TRAIL_PCT)
                # 기존 스탑이 더 넓으면(낮으면) 보존 — 전략 전환 보호
                pos["trail_stop"] = min(old_stop, new_stop) if old_stop > 0 else new_stop
            elif symbol in self.levels:
                atr_val = self.levels[symbol]["atr"]
                new_stop = pos["highest"] - atr_val * ATR_MULTIPLIER
                pos["trail_stop"] = min(old_stop, new_stop) if old_stop > 0 else new_stop

        # daytrading: 시간 초과 포지션 청산 체크
        if IS_DAYTRADING:
            now = datetime.now(tz=timezone.utc)
            expired = []
            for symbol, pos in positions.items():
                try:
                    entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    hours_held = (now - entry_dt).total_seconds() / 3600
                    max_hours = _DT_MAX_BARS * 4  # 12봉 × 4시간 = 48시간
                    if hours_held >= max_hours:
                        expired.append(symbol)
                        print(f"  ⏰ {symbol} 시간초과 ({hours_held:.0f}h >= {max_hours}h)")
                except (ValueError, KeyError):
                    pass
            for symbol in expired:
                price = self.levels.get(symbol, {}).get("close", pos.get("entry_price", 0))
                await self._execute_sell(symbol, price)

        save_state(self.state)

        # 정기 분석 보고 (4시간마다)
        try:
            await self._send_periodic_report()
        except Exception as e:
            print(f"  보고 전송 오류: {e}", flush=True)

    async def _send_periodic_report(self):
        """4시간 정기 분석 보고 — 검증 플랜 누적 성적 포함."""
        positions = self.state.get("positions", {})
        closed = self.state.get("closed_trades", [])
        now = datetime.now(tz=timezone.utc)
        now_str = now.strftime("%m/%d %H:%M UTC")

        try:
            balance = get_balance()
            krw = balance["krw"]
            total = balance["total_krw"]
        except Exception:
            krw = 0
            total = 0

        # ── 누적 성적표 (현재 전략 기간만 집계) ──
        # 전략 전환 시점 이후 거래만 카운트
        strategy_start = self.state.get("strategy_start", "2026-03-29")
        current_trades = [t for t in closed if t.get("exit_date", "") >= strategy_start]

        n_trades = len(current_trades)
        wins = [t for t in current_trades if t["return_pct"] > 0]
        losses = [t for t in current_trades if t["return_pct"] <= 0]
        win_rate = len(wins) / n_trades * 100 if n_trades > 0 else 0
        avg_ret = sum(t["return_pct"] for t in current_trades) / n_trades if n_trades > 0 else 0
        total_ret = sum(t["return_pct"] for t in current_trades)

        # 연속 손실 카운트 (현재 전략 거래만)
        consec_loss = 0
        for t in reversed(current_trades):
            if t["return_pct"] <= 0:
                consec_loss += 1
            else:
                break

        # ── 검증 플랜 기준일 ──
        start_date_str = self.state.get("strategy_start", "2026-03-29")
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_elapsed = (now - start_date).days

        # 체크포인트 판정
        checkpoint = ""
        if days_elapsed >= 7:
            if n_trades >= 15:
                verdict = "PASS" if win_rate >= 35 else "FAIL"
                checkpoint = f"\n🏁 *7일 최종판정*: {verdict} ({n_trades}건, {win_rate:.0f}%)"
                if win_rate >= 35:
                    checkpoint += "\n→ 증액 검토 가능"
                else:
                    checkpoint += "\n→ 전략 변경 권장"
            else:
                checkpoint = f"\n📅 7일차 — 거래 {n_trades}건 (15건 미달, 계속 관찰)"
        elif days_elapsed >= 5:
            if n_trades >= 10:
                verdict = "OK" if win_rate >= 30 else "경고"
                checkpoint = f"\n📅 5일 중간점검: {verdict} ({n_trades}건, {win_rate:.0f}%)"
                if win_rate < 30:
                    checkpoint += "\n→ 전략 수정 검토 필요"
            else:
                checkpoint = f"\n📅 5일차 — 거래 {n_trades}건 (10건 미달)"
        elif days_elapsed >= 3:
            if n_trades >= 5 and win_rate == 0:
                checkpoint = f"\n🚨 3일 긴급: 전패 ({n_trades}건 0승) → 중단 권장"
            elif n_trades >= 5:
                checkpoint = f"\n📅 3일 방향확인: {n_trades}건, {win_rate:.0f}% — 계속 진행"
            else:
                checkpoint = f"\n📅 {days_elapsed}일차 — 거래 {n_trades}건"

        # ── 5연패 자동 중단 ──
        if consec_loss >= 5:
            self.running = False
            await send(
                f"🛑 *5연패 자동 중단*\n"
                f"연속 {consec_loss}건 손실 — 검증 플랜 조기 탈출\n"
                f"승률: {win_rate:.0f}% ({len(wins)}/{n_trades})\n"
                f"원인 분석 후 전략 수정 필요\n"
                f"재시작: `sudo systemctl restart btc-trader`"
            )
            print(f"\n!!! 5연패 자동 중단 !!!", flush=True)
            return

        # ── 시장 분석 ──
        market_msg = ""
        try:
            import urllib.request, json as _json
            # BTC 시세
            _ex = ccxt.upbit({"enableRateLimit": True})
            _btc = _ex.fetch_ticker("BTC/KRW")
            btc_price = _btc["last"]
            btc_chg = _btc.get("percentage", 0) or 0
            # Fear & Greed
            _fg = _json.loads(urllib.request.urlopen(
                "https://api.alternative.me/fng/?limit=1", timeout=5
            ).read())
            fg_val = _fg["data"][0]["value"]
            fg_label = _fg["data"][0]["value_classification"]
            market_msg = (
                f"\n*시장*\n"
                f"  BTC: {btc_price:,.0f} ({btc_chg:+.1f}%)\n"
                f"  F&G: {fg_val} ({fg_label})\n"
            )
        except Exception:
            market_msg = "\n*시장* 조회 실패\n"

        # ── 메시지 조합 ──
        msg = f"📋 *정기 분석* ({now_str})\n"
        msg += market_msg

        # 포지션
        msg += f"\n*보유 {len(positions)}/5*\n"
        if positions:
            for sym, pos in positions.items():
                price = self.levels.get(sym, {}).get("close", 0)
                if price > 0 and pos["entry_price"] > 0:
                    ret = (price / pos["entry_price"] - 1) * 100
                    try:
                        entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                        hours = (now - entry_dt).total_seconds() / 3600
                        msg += f"  {sym} {ret:+.1f}% ({hours:.0f}h/48h)\n"
                    except (ValueError, KeyError):
                        msg += f"  {sym} {ret:+.1f}%\n"
                else:
                    msg += f"  {sym}\n"
        else:
            msg += "  없음\n"

        # 누적 성적
        msg += f"\n*누적 성적* ({days_elapsed}일차)\n"
        msg += f"  거래: {n_trades}건\n"
        msg += f"  승률: {win_rate:.0f}% ({len(wins)}승 {len(losses)}패)\n"
        msg += f"  평균: {avg_ret:+.1f}% | 합계: {total_ret:+.1f}%\n"
        if consec_loss > 0:
            msg += f"  연속손실: {consec_loss}건 ({'⚠️' if consec_loss >= 3 else ''})\n"

        # 백테스트 대비
        msg += f"\n*백테스트 대비*\n"
        msg += f"  승률: {win_rate:.0f}% (목표 35%+)\n"
        msg += f"  평균: {avg_ret:+.1f}% (목표 +0.5%+)\n"

        # 체크포인트
        if checkpoint:
            msg += checkpoint

        # 잔고
        msg += f"\n\nKRW: {krw:,.0f} | 평가: {total:,.0f}"

        # 다음 보고 시각
        next_h = ((now.hour // 4) + 1) * 4
        if next_h >= 24:
            next_h = 0
        msg += f"\n⏰ 다음: {next_h:02d}:05 UTC ({next_h+9:02d}:05 KST)"

        await send(msg)

    async def _run_websocket(self):
        """웹소켓 연결 및 실시간 체결가 수신."""
        while self.running:
            try:
                print("\n웹소켓 연결 중...", flush=True)
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(UPBIT_WS_URL, heartbeat=30, timeout=30) as ws:
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

                        # vol_reversal DRY-RUN 보유종목도 구독에 추가
                        try:
                            if VR_STATE_FILE.exists():
                                with open(VR_STATE_FILE, "r", encoding="utf-8") as _vrf:
                                    _vr = json.load(_vrf)
                                for sym in _vr.get("positions", {}).keys():
                                    code = f"KRW-{sym.split('/')[0]}"
                                    if code not in upbit_codes:
                                        upbit_codes.append(code)
                        except Exception:
                            pass

                        subscribe = [
                            {"ticket": str(uuid.uuid4())[:8]},
                            {"type": "ticker", "codes": upbit_codes, "isOnlyRealtime": True},
                        ]
                        await ws.send_json(subscribe)
                        print(f"  구독 완료: {len(upbit_codes)}개 종목", flush=True)

                        async for msg in ws:
                            if not self.running:
                                print("봇 중지 요청 — 웹소켓 종료")
                                return

                            if msg.type == aiohttp.WSMsgType.BINARY:
                                data = json.loads(msg.data.decode("utf-8"))
                                await self._handle_tick(data)

                            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                print("웹소켓 연결 종료")
                                break

                            # 갱신 체크: daytrading은 4시간마다, 기타는 일 1회
                            now_utc = datetime.now(tz=timezone.utc)
                            if IS_DAYTRADING:
                                # 4시간봉 마감 시점(0,4,8,12,16,20시) + 5분에 갱신
                                if (now_utc.hour % 4 == 0 and now_utc.minute >= 5 and
                                        now_utc.strftime("%Y-%m-%d-%H") != self.last_refresh_date):
                                    await self._refresh_levels()
                                    break
                            else:
                                if (now_utc.hour == REFRESH_HOUR_UTC and
                                        now_utc.strftime("%Y-%m-%d") != self.last_refresh_date):
                                    await self._refresh_levels()
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
                if IS_DAYTRADING:
                    pos["trail_stop"] = price * (1 - _DT_TRAIL_PCT)
                elif symbol in self.levels:
                    atr_val = self.levels[symbol]["atr"]
                    pos["trail_stop"] = price - atr_val * ATR_MULTIPLIER

            # daytrading: 고정 손절 + 시간 제한 확인
            if IS_DAYTRADING:
                ret = price / pos["entry_price"] - 1
                if ret <= -_DT_SL_PCT:
                    print(f"  🛑 {symbol} 손절! {ret*100:+.1f}%")
                    await self._execute_sell(symbol, price)
                    return

                # 시간 초과 체크 (실시간)
                try:
                    entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    hours_held = (datetime.now(tz=timezone.utc) - entry_dt).total_seconds() / 3600
                    if hours_held >= _DT_MAX_BARS * 4:
                        print(f"  ⏰ {symbol} 시간초과 {hours_held:.0f}h → 청산")
                        await self._execute_sell(symbol, price)
                        return
                except (ValueError, KeyError):
                    pass

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

        if IS_DAYTRADING:
            # daytrading: DC돌파 + 추세(SMA50) + 거래량(사전계산)
            if (price > level["upper"] and
                    level.get("trend_ok", False) and
                    level.get("vol_ok", False)):
                await self._execute_buy(symbol, price, level)
        else:
            if price > level["upper"]:
                await self._execute_buy(symbol, price, level)

        # ── vol_reversal DRY-RUN 보유종목: 실시간 청산 감시 ──
        await self._check_vr_exit(symbol, price)

    async def _check_vr_exit(self, symbol: str, price: float):
        """vol_reversal DRY-RUN 보유종목의 실시간 청산 확인."""
        try:
            if not VR_STATE_FILE.exists():
                return
            with open(VR_STATE_FILE, "r", encoding="utf-8") as f:
                vr_state = json.load(f)

            vr_positions = vr_state.get("positions", {})
            if symbol not in vr_positions:
                return

            pos = vr_positions[symbol]
            entry_price = pos["entry_price"]
            highest = max(pos.get("highest", entry_price), price)
            pos["highest"] = highest

            ret = price / entry_price - 1
            trail_stop = highest * (1 - VR_TRAIL)

            # 청산 조건
            reason = None
            if ret >= VR_TP:
                reason = f"익절 {ret*100:+.1f}%"
            elif price < trail_stop:
                reason = f"트레일 {ret*100:+.1f}%"
            elif ret <= -VR_SL:
                reason = f"손절 {ret*100:+.1f}%"
            else:
                # 시간 제한
                try:
                    entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                    hours = (datetime.now(tz=timezone.utc) - entry_dt).total_seconds() / 3600
                    if hours >= VR_MAX_HOURS:
                        reason = f"시간초과 {hours:.0f}h"
                except (ValueError, KeyError):
                    pass

            if reason:
                ret_pct = round(ret * 100, 2)
                vr_state["closed_trades"].append({
                    "symbol": symbol,
                    "entry_date": pos["entry_date"],
                    "entry_price": entry_price,
                    "exit_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "exit_price": price,
                    "return_pct": ret_pct,
                })
                invested = pos.get("amount_krw", 0)
                vr_state["capital"] = vr_state.get("capital", 0) + invested * (1 + ret / 100)
                del vr_positions[symbol]
                vr_state["positions"] = vr_positions

                with open(VR_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(vr_state, f, ensure_ascii=False, indent=2)

                emoji = "🟢" if ret_pct > 0 else "🔴"
                print(f"  [VR-DRY] {emoji} {symbol} {reason} @ {price:,.0f}", flush=True)
                await send(
                    f"🔬 *vol\\_reversal DRY*\n"
                    f"{emoji} {symbol} {reason}\n"
                    f"가격: {price:,.0f} (진입 {entry_price:,.0f})\n"
                    f"수익: {ret_pct:+.1f}%"
                )
            else:
                # 고점만 갱신하여 저장
                with open(VR_STATE_FILE, "w", encoding="utf-8") as f:
                    json.dump(vr_state, f, ensure_ascii=False, indent=2)

        except Exception:
            pass  # DRY-RUN 감시 오류는 무시 (메인 매매에 영향 없도록)

    # ── 안전장치 메서드 ─────────────────────────────────
    def _is_in_cooldown(self, symbol: str) -> bool:
        """해당 종목이 오류 쿨다운 중인지 확인."""
        import time
        if symbol in self.error_cooldown:
            if time.time() < self.error_cooldown[symbol]:
                return True
            del self.error_cooldown[symbol]
        return False

    def _set_cooldown(self, symbol: str):
        import time
        self.error_cooldown[symbol] = time.time() + ERROR_COOLDOWN_SEC

    async def _handle_error(self, error_key: str, msg: str):
        """오류 처리: 연속 오류 카운트 + 알림 쿨다운."""
        import time
        self.consecutive_errors += 1

        # 동일 오류 알림 쿨다운 (5분 이내 중복 알림 방지)
        now = time.time()
        last = self.last_alert_time.get(error_key, 0)
        if now - last >= ALERT_COOLDOWN_SEC:
            self.last_alert_time[error_key] = now
            await notify_error(f"{msg}\n(연속 오류: {self.consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")

        # 연속 오류 초과 시 봇 중지
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            self.running = False
            await send(
                f"🛑 *봇 자동 중지*\n"
                f"연속 오류 {self.consecutive_errors}회 발생\n"
                f"마지막: {msg}\n"
                f"서버에서 원인 확인 후 재시작 필요:\n"
                f"`sudo systemctl restart btc-trader`"
            )
            print(f"\n!!! 봇 자동 중지: 연속 오류 {self.consecutive_errors}회 !!!")

    def _reset_errors(self):
        """성공 시 연속 오류 카운트 초기화."""
        self.consecutive_errors = 0

    async def _execute_buy(self, symbol: str, price: float, level: dict):
        positions = self.state.get("positions", {})
        if symbol in positions:
            return
        if self._is_in_cooldown(symbol):
            return

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        if IS_DAYTRADING:
            trail_stop = price * (1 - _DT_TRAIL_PCT)
        else:
            trail_stop = price - level["atr"] * ATR_MULTIPLIER

        try:
            balance = get_balance()
            available = balance["krw"]
        except Exception as e:
            self._set_cooldown(symbol)
            await self._handle_error(f"balance_{symbol}", f"잔고 조회 실패: {e}")
            return

        slots_empty = MAX_POSITIONS - len(positions)
        order_amount = available * POSITION_RATIO / slots_empty

        if order_amount < MIN_ORDER_KRW:
            return

        # 상세 조건 로깅
        if IS_DAYTRADING:
            print(f"\n  *** {symbol} 돌파! 가격: {price:,.0f}  상단: {level['upper']:,.0f} "
                  f"추세:{level.get('trend_ok','?')} 거래량:{level.get('vol_ok','?')} "
                  f"(vol:{level.get('latest_vol',0):.0f} / sma:{level.get('vol_sma',0):.0f} x{_DT_VOL_THRESHOLD}) ***")
        else:
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
                self._set_cooldown(symbol)
                await self._handle_error(f"buy_{symbol}", f"{symbol} 매수 실패: {e}")
                return

        self._reset_errors()

        positions[symbol] = {
            "entry_date": today,
            "entry_price": exec_price,
            "highest": exec_price,
            "trail_stop": trail_stop,
            "order_amount": order_amount,
        }
        self.state["positions"] = positions
        save_state(self.state)

        # 돌파 후 동일 종목 재매수 방지 (레벨에서 제거)
        if symbol in self.levels:
            del self.levels[symbol]

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
        if self._is_in_cooldown(symbol):
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
                ex = _create_exchange()
                bal = ex.fetch_balance()
                coin_amount = float(bal.get(coin_id, {}).get("free", 0))

                if coin_amount <= 0:
                    print(f"  {symbol} 잔고 없음")
                    del positions[symbol]
                    self.state["positions"] = positions
                    save_state(self.state)
                    return

                order = sell_market_coin(symbol, coin_amount)
                exec_price = order.get("price") or price
                print(f"  매도 체결: {exec_price:,.0f}")
            except Exception as e:
                self._set_cooldown(symbol)
                await self._handle_error(f"sell_{symbol}", f"{symbol} 매도 실패: {e}")
                return

        self._reset_errors()
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

    # 텔레그램 명령어 핸들러 동시 실행
    from services.execution.telegram_bot import TelegramCommandHandler
    cmd_handler = TelegramCommandHandler(monitor=monitor)

    await asyncio.gather(
        monitor.start(),
        cmd_handler.start_polling(),
    )


if __name__ == "__main__":
    asyncio.run(main())
