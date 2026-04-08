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
    HARD_STOP_LOSS_PCT, MAX_ATR_PCT,
    MAX_POSITIONS, POSITION_RATIO, MIN_VOLUME_KRW,
    MIN_ORDER_KRW, DRY_RUN, EXCLUDE_SYMBOLS, MIN_LISTING_DAYS,
    NOTIFY_ON_BUY, NOTIFY_ON_SELL, NOTIFY_DAILY_REPORT, NOTIFY_NEAR_SIGNAL,
    VB_ENABLED, VB_DRY_RUN, VB_K_BULL, VB_K_NEUTRAL, VB_K_BEAR, VB_K_CRISIS,
    VB_SL_PCT, VB_SMA_PERIOD, VB_MAX_POSITIONS, VB_POSITION_RATIO,
    CIRCUIT_BREAKER_ENABLED, CIRCUIT_BREAKER_INITIAL_CAPITAL,
    EMA_TREND_ENABLED, EMA_TREND_DRY_RUN,
    EMA_TREND_EMA_PERIOD, EMA_TREND_FILTER_PERIOD,
    EMA_TREND_TRAIL_PCT, EMA_TREND_MAX_POSITIONS, EMA_TREND_POSITION_RATIO,
)
from services.execution.circuit_breaker import check_and_trigger, is_triggered
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
VB_STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "vb_state.json"
EMA_TREND_STATE_FILE = Path(__file__).resolve().parents[2] / "workspace" / "ema_trend_state.json"
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
        # VB(변동성 돌파) 상태
        self._vb_positions: dict[str, dict] = {}  # 메모리 캐시
        self._load_vb_state()
        # EMA Trend Follow 상태
        self._ema_trend_level: dict = {}   # {ema50, ema200, prev_close, close}
        self._ema_trend_position: "dict | None" = None  # 보유 중이면 dict
        self._load_ema_trend_state()
        # 안전장치
        self.consecutive_errors = 0
        # v2 필터 캐시 (레벨 갱신 시 1회 조회)
        self._fg_value: "float | None" = None
        self._btc_above_ema: bool = True
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
        if VB_ENABLED:
            mode = "DRY-RUN" if VB_DRY_RUN else "실전"
            print(f"  VB(변동성돌파): {mode}, K={VB_K_BULL}/{VB_K_BEAR}, 슬롯={VB_MAX_POSITIONS}", flush=True)
        if EMA_TREND_ENABLED:
            mode = "DRY-RUN" if EMA_TREND_DRY_RUN else "실전"
            print(f"  EMA Trend: {mode}, EMA{EMA_TREND_EMA_PERIOD}/EMA{EMA_TREND_FILTER_PERIOD}, Trail{EMA_TREND_TRAIL_PCT*100:.0f}%", flush=True)
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

        # v2 필터: F&G + BTC EMA(200) 조회 (레벨 갱신 시 1회)
        if not IS_DAYTRADING:
            try:
                from services.execution.scanner import fetch_fg_value, fetch_btc_above_ema
                from services.execution.config import REGIME_FILTER_ENABLED, REGIME_FILTER_EMA_PERIOD
                self._fg_value = fetch_fg_value()
                self._btc_above_ema = fetch_btc_above_ema()
                fg_disp = f"{self._fg_value:.0f}" if self._fg_value is not None else "N/A"
                fg_blocked = self._fg_value is not None and self._fg_value < 20
                ema_label = f"EMA{REGIME_FILTER_EMA_PERIOD}" if REGIME_FILTER_ENABLED else "EMA(비활성)"
                print(f"  [v2] F&G={fg_disp} {'(진입차단)' if fg_blocked else '(허용)'}"
                      f" | BTC>{ema_label}={self._btc_above_ema}", flush=True)
            except Exception as e:
                print(f"  [v2] 필터 조회 실패: {e} — 기본값 유지", flush=True)

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

                # VB용 데이터: 전일 변동폭, SMA50, 시가
                if VB_ENABLED and not IS_DAYTRADING and len(df) >= VB_SMA_PERIOD + 2:
                    prev_range = float(df["high"].iloc[-2]) - float(df["low"].iloc[-2])
                    prev_open = float(df["open"].iloc[-1])
                    sma50 = float(pd.Series(df["close"]).rolling(VB_SMA_PERIOD).mean().iloc[-1])
                    level["vb_prev_range"] = prev_range
                    level["vb_open"] = prev_open
                    level["vb_sma50"] = sma50

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

        # ── VB(변동성 돌파) 일일 회전 처리 ──
        if VB_ENABLED and not IS_DAYTRADING:
            await self._vb_daily_rotation()

        # ── EMA Trend Follow 레벨 갱신 ──
        if EMA_TREND_ENABLED and not IS_DAYTRADING:
            await self._ema_trend_refresh_level()

        # 보유 종목 트레일링스탑 갱신 (전략 전환 시 기존 스탑 보존)
        # 하드 손절 캡(HARD_STOP_LOSS_PCT)으로 entry*(1-cap) 이하 금지 —
        # ONG 사건(lessons/20260408_5) 재발 방지.
        positions = self.state.get("positions", {})
        for symbol, pos in positions.items():
            old_stop = pos.get("trail_stop", 0)
            hard_floor = pos["entry_price"] * (1 - HARD_STOP_LOSS_PCT)
            if IS_DAYTRADING:
                new_stop = pos["highest"] * (1 - _DT_TRAIL_PCT)
                # 기존 스탑이 더 넓으면(낮으면) 보존 — 전략 전환 보호
                merged = min(old_stop, new_stop) if old_stop > 0 else new_stop
                pos["trail_stop"] = max(merged, hard_floor)
            elif symbol in self.levels:
                atr_val = self.levels[symbol]["atr"]
                new_stop = pos["highest"] - atr_val * ATR_MULTIPLIER
                merged = min(old_stop, new_stop) if old_stop > 0 else new_stop
                pos["trail_stop"] = max(merged, hard_floor)

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

                        # EMA Trend Follow: BTC/KRW 반드시 구독 포함
                        if EMA_TREND_ENABLED and "KRW-BTC" not in upbit_codes:
                            upbit_codes.append("KRW-BTC")

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

            # 고점 갱신 (하드 손절 캡 적용 — lessons/20260408_5)
            if price > pos["highest"]:
                pos["highest"] = price
                hard_floor = pos["entry_price"] * (1 - HARD_STOP_LOSS_PCT)
                if IS_DAYTRADING:
                    new_stop = price * (1 - _DT_TRAIL_PCT)
                elif symbol in self.levels:
                    atr_val = self.levels[symbol]["atr"]
                    new_stop = price - atr_val * ATR_MULTIPLIER
                else:
                    new_stop = pos.get("trail_stop", hard_floor)
                pos["trail_stop"] = max(new_stop, hard_floor)

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

        # ── 보조 전략 감시 (levels 등록 여부와 무관하게 실행) ──

        # vol_reversal DRY-RUN 보유종목: 실시간 청산 감시
        await self._check_vr_exit(symbol, price)

        # VB(변동성 돌파) 보유종목: 실시간 손절 감시
        if VB_ENABLED:
            await self._check_vb_exit(symbol, price)

        # EMA Trend Follow: BTC/KRW 진입/청산 감시 (lessons #6: 모든 진입 경로에 필터 적용)
        if EMA_TREND_ENABLED and symbol == "BTC/KRW":
            await self._check_ema_trend(price)

        # ── 메인 전략 미보유 종목: 진입 확인 ──
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

    # ── EMA Trend Follow 메서드 ─────────────────────────

    def _load_ema_trend_state(self):
        """EMA Trend 상태 파일 로드."""
        try:
            if EMA_TREND_STATE_FILE.exists():
                with open(EMA_TREND_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._ema_trend_position = state.get("position")  # None 또는 dict
                self._ema_trend_level = state.get("level", {})
            else:
                self._ema_trend_position = None
                self._ema_trend_level = {}
        except Exception:
            self._ema_trend_position = None
            self._ema_trend_level = {}

    def _save_ema_trend_state(self):
        """EMA Trend 상태 파일 저장."""
        try:
            EMA_TREND_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if EMA_TREND_STATE_FILE.exists():
                with open(EMA_TREND_STATE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing["position"] = self._ema_trend_position
            existing["level"] = self._ema_trend_level
            if "history" not in existing:
                existing["history"] = []
            with open(EMA_TREND_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [EMA-TREND] 상태 저장 오류: {e}", flush=True)

    async def _ema_trend_refresh_level(self):
        """BTC/KRW 일봉 EMA(50), EMA(200) 재계산 (매일 09:05 KST = UTC 00:05)."""
        try:
            now = datetime.now(tz=timezone.utc)
            start = now - timedelta(days=EMA_TREND_FILTER_PERIOD + 50)
            start_str = start.strftime("%Y-%m-%dT00:00:00Z")
            end_str = now.strftime("%Y-%m-%dT00:00:00Z")

            raw = await fetch_ohlcv("BTC/KRW", "1d", start_str, end_str, use_cache=False)
            df = pd.DataFrame(raw)
            if len(df) < EMA_TREND_FILTER_PERIOD + 5:
                print(f"  [EMA-TREND] 데이터 부족 ({len(df)}봉)", flush=True)
                return

            close_series = df["close"]
            ema50 = float(
                close_series.ewm(span=EMA_TREND_EMA_PERIOD, min_periods=EMA_TREND_EMA_PERIOD, adjust=False)
                .mean().iloc[-1]
            )
            ema50_prev = float(
                close_series.ewm(span=EMA_TREND_EMA_PERIOD, min_periods=EMA_TREND_EMA_PERIOD, adjust=False)
                .mean().iloc[-2]
            )
            ema200 = float(
                close_series.ewm(span=EMA_TREND_FILTER_PERIOD, min_periods=EMA_TREND_FILTER_PERIOD, adjust=False)
                .mean().iloc[-1]
            )
            latest_close = float(close_series.iloc[-1])
            prev_close = float(close_series.iloc[-2])

            self._ema_trend_level = {
                "ema50": ema50,
                "ema50_prev": ema50_prev,
                "ema200": ema200,
                "close": latest_close,       # 어제 종가 (일봉 미마감)
                "prev_close": prev_close,    # 그제 종가
            }
            self._save_ema_trend_state()

            mode = "DRY-RUN" if EMA_TREND_DRY_RUN else "실전"
            above_filter = latest_close > ema200
            print(
                f"  [EMA-TREND-{mode}] EMA{EMA_TREND_EMA_PERIOD}={ema50:,.0f} "
                f"EMA{EMA_TREND_FILTER_PERIOD}={ema200:,.0f} "
                f"close={latest_close:,.0f} "
                f"EMA200_필터={'통과' if above_filter else '차단'}",
                flush=True,
            )
        except Exception as e:
            print(f"  [EMA-TREND] 레벨 갱신 오류: {e}", flush=True)

    async def _check_ema_trend(self, price: float):
        """BTC/KRW 실시간 체결가로 EMA Trend 진입/청산 조건 확인.

        진입: 실시간가 > EMA50  AND  전일 close < EMA50_prev  AND  실시간가 > EMA200
              (봉 마감 기반 돌파를 실시간 틱으로 근사 — lessons #1 주의)
        청산: 실시간가 < trail_stop  OR  실시간가 < EMA50
        """
        if not self._ema_trend_level:
            return  # 레벨 아직 미계산

        level = self._ema_trend_level
        ema50 = level.get("ema50")
        ema50_prev = level.get("ema50_prev")
        ema200 = level.get("ema200")
        prev_close = level.get("prev_close")

        if ema50 is None or ema200 is None or prev_close is None:
            return

        mode = "DRY" if EMA_TREND_DRY_RUN else "LIVE"

        # ── 보유 중: 청산 확인 ──
        if self._ema_trend_position is not None:
            pos = self._ema_trend_position
            entry_price = pos["entry_price"]

            # 고점 갱신
            if price > pos.get("highest", entry_price):
                pos["highest"] = price
                self._save_ema_trend_state()

            highest = pos.get("highest", entry_price)
            trail_stop = highest * (1.0 - EMA_TREND_TRAIL_PCT)
            below_ema = price < ema50

            reason = None
            if price < trail_stop:
                ret_pct = (price / entry_price - 1) * 100
                reason = f"트레일링스탑 {ret_pct:+.1f}%"
            elif below_ema:
                ret_pct = (price / entry_price - 1) * 100
                reason = f"EMA{EMA_TREND_EMA_PERIOD} 하향이탈 {ret_pct:+.1f}%"

            if reason:
                ret_pct_val = (price / entry_price - 1) * 100
                emoji = "🟢" if ret_pct_val > 0 else "🔴"

                # 히스토리 기록
                try:
                    existing = {}
                    if EMA_TREND_STATE_FILE.exists():
                        with open(EMA_TREND_STATE_FILE, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                    existing.setdefault("history", []).append({
                        "symbol": "BTC/KRW",
                        "entry_date": pos["entry_date"],
                        "entry_price": entry_price,
                        "exit_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "exit_price": price,
                        "return_pct": round(ret_pct_val, 2),
                        "reason": reason,
                    })
                    existing["position"] = None
                    existing["level"] = self._ema_trend_level
                    EMA_TREND_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(EMA_TREND_STATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(existing, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

                self._ema_trend_position = None

                print(
                    f"  [EMA-TREND-{mode}] {emoji} BTC/KRW 청산 — {reason} @ {price:,.0f}",
                    flush=True,
                )
                await send(
                    f"[EMA-TREND {mode}] {emoji} *청산* BTC/KRW\n"
                    f"사유: {reason}\n"
                    f"가격: {price:,.0f} (진입 {entry_price:,.0f})\n"
                    f"수익: {ret_pct_val:+.1f}%"
                )
            return

        # ── 미보유: 진입 확인 ──
        # lessons #1: 실시간 틱 돌파는 가짜 돌파 위험 — 봉 마감 기반 EMA와 비교
        # 전일 close < EMA50_prev (전일 EMA) AND 현재가 > EMA50 (오늘 EMA) AND 현재가 > EMA200
        crossed_above = (prev_close < ema50_prev) and (price > ema50)
        above_filter = price > ema200

        if crossed_above and above_filter:
            now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            self._ema_trend_position = {
                "entry_date": now_str,
                "entry_price": price,
                "highest": price,
            }
            self._save_ema_trend_state()

            print(
                f"  [EMA-TREND-{mode}] 📈 BTC/KRW 진입 신호!"
                f" 가격={price:,.0f} EMA50={ema50:,.0f} EMA200={ema200:,.0f}",
                flush=True,
            )
            await send(
                f"[EMA-TREND {mode}] 📈 *진입 신호* BTC/KRW\n"
                f"가격: {price:,.0f}\n"
                f"EMA{EMA_TREND_EMA_PERIOD}: {ema50:,.0f}\n"
                f"EMA{EMA_TREND_FILTER_PERIOD}: {ema200:,.0f}\n"
                f"트레일링스탑: {EMA_TREND_TRAIL_PCT*100:.0f}%\n"
                f"{'[실제 매수 없음 — DRY-RUN]' if EMA_TREND_DRY_RUN else '[실전 매수]'}"
            )

            if not EMA_TREND_DRY_RUN:
                # 실전 매수 (DRY_RUN=False 시에만)
                try:
                    balance = get_balance()
                    avail = balance.get("krw", 0)
                    order_amt = avail * EMA_TREND_POSITION_RATIO
                    if order_amt >= MIN_ORDER_KRW:
                        order = buy_market_coin("BTC/KRW", order_amt)
                        exec_price = order.get("price") or price
                        self._ema_trend_position["entry_price"] = exec_price
                        self._ema_trend_position["highest"] = exec_price
                        self._save_ema_trend_state()
                        print(f"  [EMA-TREND-LIVE] 매수 체결: {exec_price:,.0f}", flush=True)
                except Exception as e:
                    print(f"  [EMA-TREND] 매수 실패: {e}", flush=True)

    # ── VB(변동성 돌파) 메서드 ──────────────────────────

    def _load_vb_state(self):
        """VB 상태 파일 로드."""
        try:
            if VB_STATE_FILE.exists():
                with open(VB_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._vb_positions = state.get("positions", {})
            else:
                self._vb_positions = {}
        except Exception:
            self._vb_positions = {}

    def _save_vb_state(self):
        """VB 상태 파일 저장."""
        try:
            existing = {}
            if VB_STATE_FILE.exists():
                with open(VB_STATE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            existing["positions"] = self._vb_positions
            if "history" not in existing:
                existing["history"] = []
            VB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(VB_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  [VB] 상태 저장 오류: {e}", flush=True)

    async def _vb_daily_rotation(self):
        """VB 일일 회전: 기존 포지션 청산 → 새 신호 매수. 1일 1회만 실행."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        last_rotation = self.state.get("vb_last_rotation_date", "")
        if last_rotation == today:
            print(f"  [VB] 오늘({today}) 이미 회전 완료 — 건너뜀", flush=True)
            return
        self.state["vb_last_rotation_date"] = today
        save_state(self.state)

        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # 1. 기존 VB 포지션 전량 청산
        closed = 0
        for sym in list(self._vb_positions):
            pos = self._vb_positions[sym]
            current_price = self.levels.get(sym, {}).get("close", pos["entry_price"])
            ret = current_price / pos["entry_price"] - 1
            ret_pct = round(ret * 100, 2)

            # 히스토리에 기록
            self._append_vb_history(sym, pos, current_price, "1일 회전")

            if not VB_DRY_RUN:
                try:
                    await self._execute_sell(sym, current_price)
                except Exception as e:
                    print(f"  [VB] {sym} 청산 오류: {e}", flush=True)

            emoji = "🟢" if ret_pct > 0 else "🔴"
            print(f"  [VB] {emoji} {sym} 청산 {ret_pct:+.1f}% @ {current_price:,.0f}", flush=True)
            mode = "DRY" if VB_DRY_RUN else "LIVE"
            await send(
                f"🔬 *VB 청산* [{mode}]\n"
                f"{emoji} {sym}\n"
                f"진입: {pos['entry_price']:,.0f} → 청산: {current_price:,.0f}\n"
                f"수익: {ret_pct:+.1f}%"
            )
            closed += 1

        self._vb_positions.clear()

        # 2. 새 VB 신호 스캔
        signals = []
        for sym, level in self.levels.items():
            if "vb_prev_range" not in level:
                continue
            prev_range = level["vb_prev_range"]
            prev_close = level["close"]
            today_open = level["vb_open"]
            sma50 = level["vb_sma50"]

            if prev_range <= 0:
                continue

            # 레짐별 K값 (F&G 극공포 오버라이드 포함)
            if np.isnan(sma50):
                k = VB_K_NEUTRAL
            elif prev_close > sma50:
                k = VB_K_BULL
            else:
                k = VB_K_BEAR
            # 극공포 구간(F&G 20~30): K를 CRISIS로 상향 (더 보수적)
            if self._fg_value is not None and 20 <= self._fg_value < 30:
                k = max(k, VB_K_CRISIS)

            threshold = today_open + prev_range * k
            if prev_close > threshold:
                score = (prev_close - threshold) / prev_range  # 돌파 강도
                signals.append((sym, level, score, k))

        # 돌파 강도 순 정렬, 최대 슬롯만큼 매수
        signals.sort(key=lambda x: x[2], reverse=True)
        bought = 0

        for sym, level, score, k in signals[:VB_MAX_POSITIONS]:
            entry_price = level["close"]
            self._vb_positions[sym] = {
                "entry_price": entry_price,
                "entry_date": now_str,
                "k_value": k,
            }

            if not VB_DRY_RUN:
                try:
                    balance = get_balance()
                    avail = balance.get("krw", 0)
                    order_amt = avail * VB_POSITION_RATIO / VB_MAX_POSITIONS
                    if order_amt >= MIN_ORDER_KRW:
                        await self._execute_buy(sym, entry_price, level)
                except Exception as e:
                    print(f"  [VB] {sym} 매수 오류: {e}", flush=True)

            mode = "DRY" if VB_DRY_RUN else "LIVE"
            regime = "상승" if k == VB_K_BULL else ("하락" if k == VB_K_BEAR else "중립")
            print(f"  [VB-{mode}] 📈 {sym} 매수 @ {entry_price:,.0f} (K={k}, score={score:.2f})", flush=True)
            await send(
                f"🔬 *VB 매수* [{mode}]\n"
                f"📈 {sym} @ {entry_price:,.0f}\n"
                f"레짐: {regime} (K={k})\n"
                f"돌파강도: {score:.2f}"
            )
            bought += 1

        self._save_vb_state()

        mode = "DRY-RUN" if VB_DRY_RUN else "실전"
        await send(
            f"📊 *VB 일일 회전* ({mode})\n"
            f"청산: {closed}건 / 매수: {bought}건\n"
            f"감시: {len(self._vb_positions)}종목"
        )
        print(f"  [VB] 일일 회전 완료: 청산 {closed}건, 매수 {bought}건", flush=True)

    def _append_vb_history(self, symbol, pos, exit_price, reason):
        """VB 거래 히스토리에 추가."""
        try:
            existing = {}
            if VB_STATE_FILE.exists():
                with open(VB_STATE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            if "history" not in existing:
                existing["history"] = []
            ret = exit_price / pos["entry_price"] - 1
            existing["history"].append({
                "symbol": symbol,
                "entry_date": pos["entry_date"],
                "entry_price": pos["entry_price"],
                "exit_date": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "exit_price": exit_price,
                "return_pct": round(ret * 100, 2),
                "reason": reason,
            })
            with open(VB_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    async def _check_vb_exit(self, symbol: str, price: float):
        """VB 보유종목의 실시간 손절 감시."""
        if symbol not in self._vb_positions:
            return
        pos = self._vb_positions[symbol]
        ret = price / pos["entry_price"] - 1

        if ret <= -VB_SL_PCT:
            ret_pct = round(ret * 100, 2)
            self._append_vb_history(symbol, pos, price, f"손절 {ret_pct:+.1f}%")

            if not VB_DRY_RUN:
                try:
                    await self._execute_sell(symbol, price)
                except Exception as e:
                    print(f"  [VB] {symbol} 손절 매도 오류: {e}", flush=True)

            del self._vb_positions[symbol]
            self._save_vb_state()

            mode = "DRY" if VB_DRY_RUN else "LIVE"
            print(f"  [VB-{mode}] 🛑 {symbol} 손절 {ret_pct:+.1f}% @ {price:,.0f}", flush=True)
            await send(f"🛑 *VB 손절* ({mode})\n{symbol} {ret_pct:+.1f}% @ {price:,.0f}")

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

    def _get_consec_loss(self) -> int:
        """현재 전략 시작일 이후 거래에서 연속 손실 횟수를 반환한다."""
        closed = self.state.get("closed_trades", [])
        strategy_start = self.state.get("strategy_start", "2026-03-29")
        current_trades = [t for t in closed if t.get("exit_date", "") >= strategy_start]
        consec = 0
        for t in reversed(current_trades):
            if t["return_pct"] <= 0:
                consec += 1
            else:
                break
        return consec

    def _is_loss_cooldown(self) -> bool:
        """연패 쿨다운 중이면 True 반환."""
        cooldown_until = self.state.get("cooldown_until")
        if not cooldown_until:
            return False
        now_ts = datetime.now(tz=timezone.utc).timestamp()
        if now_ts < cooldown_until:
            remaining_h = (cooldown_until - now_ts) / 3600
            print(f"  [쿨다운] 연패 쿨다운 중 — {remaining_h:.1f}h 남음 (매수 스킵)", flush=True)
            return True
        # 쿨다운 만료 — 상태에서 제거
        self.state.pop("cooldown_until", None)
        save_state(self.state)
        return False

    async def _execute_buy(self, symbol: str, price: float, level: dict):
        positions = self.state.get("positions", {})
        if symbol in positions:
            return
        if self._is_in_cooldown(symbol):
            return

        # ── 계좌 레벨 서킷브레이커 ─────────────────────────
        if CIRCUIT_BREAKER_ENABLED:
            try:
                balance = get_balance()
                total_krw = balance.get("total_krw", 0)
                newly_triggered = check_and_trigger(total_krw)
                if newly_triggered:
                    loss_pct = (total_krw - CIRCUIT_BREAKER_INITIAL_CAPITAL) / CIRCUIT_BREAKER_INITIAL_CAPITAL * 100
                    msg = (
                        f"서킷브레이커 발동!\n"
                        f"계좌 평가금액: {total_krw:,.0f} KRW\n"
                        f"초기자본 대비: {loss_pct:+.1f}%\n"
                        f"모든 신규 매수 차단 (기존 포지션 유지)\n"
                        f"해제: workspace/circuit_breaker_state.json 삭제 또는 triggered=false 설정"
                    )
                    print(f"\n  [서킷브레이커] {msg}", flush=True)
                    await send(f"🔴 *{msg}")
                    return
                if is_triggered():
                    print(f"  [서킷브레이커] 발동 중 — {symbol} 매수 차단", flush=True)
                    return
            except Exception as e:
                print(f"  [서킷브레이커] 잔고 조회 실패, 매수 진행: {e}", flush=True)

        # 연패 쿨다운 확인 (3연패 시 3일 매수 중단)
        if self._is_loss_cooldown():
            return

        # v2 필터: F&G 게이트 (F&G < 20이면 진입 차단)
        if self._fg_value is not None and self._fg_value < 20:
            return

        # v2 필터: BTC EMA(200) 필터 (BTC < EMA200이면 전 종목 신규 매수 차단)
        if not self._btc_above_ema:
            return

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        # 변동성 필터 — ATR이 가격의 MAX_ATR_PCT 이상이면 진입 차단
        # (lessons/20260408_5 — ONG 같은 고변동 알트 방어)
        if not IS_DAYTRADING and level.get("atr"):
            atr_pct = level["atr"] / price
            if atr_pct > MAX_ATR_PCT:
                print(f"  [{symbol}] ATR 필터 — ATR/price={atr_pct:.1%} > {MAX_ATR_PCT:.0%} 차단", flush=True)
                return
        if IS_DAYTRADING:
            trail_stop = price * (1 - _DT_TRAIL_PCT)
        else:
            # 하드 손절 캡 적용 — entry * (1 - HARD_STOP_LOSS_PCT) 아래 금지
            atr_stop = price - level["atr"] * ATR_MULTIPLIER
            hard_floor = price * (1 - HARD_STOP_LOSS_PCT)
            trail_stop = max(atr_stop, hard_floor)

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
                  f"(vol:{(level.get('latest_vol') or 0):.0f} / sma:{(level.get('vol_sma') or 0):.0f} x{_DT_VOL_THRESHOLD}) ***")
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

        # ── 매도 체결 직후 연패 즉시 체크 (교훈 #3: 주기 체크 아닌 즉시 체크) ──
        consec_loss = self._get_consec_loss()
        if consec_loss >= 3 and not self.state.get("cooldown_until"):
            cooldown_until = (
                datetime.now(tz=timezone.utc).timestamp() + 3 * 24 * 3600
            )
            self.state["cooldown_until"] = cooldown_until
            save_state(self.state)
            cooldown_dt = datetime.fromtimestamp(cooldown_until, tz=timezone.utc)
            cooldown_str = cooldown_dt.strftime("%Y-%m-%d %H:%M UTC")
            print(
                f"  [쿨다운] 3연패 감지 — {cooldown_str}까지 신규 매수 중단",
                flush=True,
            )
            await send(
                f"⏸ *3연패 쿨다운 시작*\n"
                f"연속 {consec_loss}건 손실 → 3일간 신규 매수 중단\n"
                f"재개 예정: {cooldown_str}\n"
                f"기존 보유 종목은 트레일링스탑으로 정상 청산"
            )

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
