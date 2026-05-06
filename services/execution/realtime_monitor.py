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
import time as _time
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
    TP_LEVELS, TP_ENABLED,
    VOL_FILTER_ENABLED, VOL_FILTER_MULTIPLIER,
    DAILY_LOSS_LIMIT_ENABLED, DAILY_LOSS_LIMIT_PCT, DAILY_LOSS_BASE_KRW,
    MAX_POSITIONS, POSITION_RATIO, MIN_VOLUME_KRW,
    MIN_ORDER_KRW, DRY_RUN, EXCLUDE_SYMBOLS, MIN_LISTING_DAYS,
    NOTIFY_ON_BUY, NOTIFY_ON_SELL, NOTIFY_DAILY_REPORT, NOTIFY_NEAR_SIGNAL,
    VB_ENABLED, VB_DRY_RUN, VB_K_BULL, VB_K_NEUTRAL, VB_K_BEAR, VB_K_CRISIS,
    VB_SL_PCT, VB_SMA_PERIOD, VB_MAX_POSITIONS, VB_POSITION_RATIO,
    VB_BEAR_MARKET_FILTER, VB_DEAD_SYMBOL_THRESHOLD, VB_MAX_WEEKLY_PER_SYMBOL,
    VB_LOSS_COOLDOWN_N, VB_LOSS_COOLDOWN_HOURS,
    CIRCUIT_BREAKER_ENABLED, CIRCUIT_BREAKER_INITIAL_CAPITAL,
    EMA_TREND_ENABLED, EMA_TREND_DRY_RUN,
    EMA_TREND_EMA_PERIOD, EMA_TREND_FILTER_PERIOD,
    EMA_TREND_TRAIL_PCT, EMA_TREND_MAX_POSITIONS, EMA_TREND_POSITION_RATIO,
)
from services.execution.vb_filters import (
    compute_dead_symbols,
    iso_week,
    weekly_count_exceeded,
    bump_weekly_count,
    recent_consecutive_losses,
    is_in_loss_cooldown,
    set_loss_cooldown,
)
from services.execution.circuit_breaker import (
    check_and_trigger,
    check_and_trigger_l2,
    check_l1_auto_resume,
    is_triggered,
    is_l2_triggered,
)
from services.execution.filter_stats import record_block
from services.execution.multi_trader import (
    load_state, save_state, append_log,
    buy_market_coin, sell_market_coin,
)
from services.execution.scanner import get_krw_market_coins
from services.market_data.fetcher import fetch_ohlcv
from services.paper_trading.strategy import calc_atr, calc_donchian_upper
from services.execution.upbit_client import (
    get_balance, _create_exchange,
    load_last_known_balance, RateLimitExhausted,
)
from services.alerting.notifier import send, notify_error

# ── ML 신호 필터 (Phase 3 보강, fail-open) ──────────────────
# multi_trader.py와 동일 정책. ML_FILTER_ENABLED=0(기본) 시 zero-cost.
# 실시간 매수 경로에도 동일 게이트 적용 — lessons #6 (모든 매수 경로 필터)
from services.ml.inference import get_filter as _get_ml_filter  # noqa: E402
from services.ml import shadow as _ml_shadow  # noqa: E402
from services.common.sd_notify import ready as _sd_ready, watchdog_ping as _sd_watchdog

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
WS_RECONNECT_BASE = 5          # 웹소켓 재연결 초기 대기 (초)
WS_RECONNECT_MAX = 60          # 웹소켓 재연결 최대 대기 (초)


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
        self._cb_log_ts: float = 0.0  # 서킷브레이커 로그 throttle (초)
        self._last_heartbeat: float = 0.0  # heartbeat touch 마지막 시각 (monotonic)
        self._last_msg_ts: float = 0.0    # 웹소켓 마지막 메시지 수신 시각 (monotonic, P7-07)
        # 신호 발화 dedupe (lessons #1) — {symbol: {"bar_id": int, "ts": float}}
        # _execute_buy 진입 시 같은 15분봉 ID 또는 60s 내 재시도면 skip.
        # 폭주 차단: ORDER/KRW 한 종목 30h에 16,484회 → 봉당 1회 + 60s = 30h/15m = 120회 내외.
        self._signal_dedupe: dict[str, dict] = {}
        self._dedupe_log_ts: float = 0.0  # 차단 로그 60s throttle

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

        # 시작 시 레벨 갱신 — API 장애(점검 등) 대비 재시도 (lessons #15)
        # 알림 정책: 첫 1~2회 실패는 일시적 429 가능성 높아 알림 X.
        # 3회 연속 실패 시 1회 알림 (메시지 truncate), 100회 모두 실패 시 critical.
        _refresh_ok = False
        for _attempt in range(1, 100):
            try:
                await self._refresh_levels()
                _refresh_ok = True
                break
            except Exception as e:
                delay = min(WS_RECONNECT_BASE * (2 ** (_attempt - 1)), WS_RECONNECT_MAX)
                err_short = str(e)[:200] + ("..." if len(str(e)) > 200 else "")
                print(f"  레벨 갱신 실패 (시도 {_attempt}): {err_short}", flush=True)
                print(f"  {delay}초 후 재시도...", flush=True)
                if _attempt == 3:
                    await notify_error(
                        f"시작 시 레벨 갱신 3회 연속 실패\n"
                        f"오류: {err_short}\n"
                        f"계속 재시도 중 (최대 100회)..."
                    )
                await asyncio.sleep(delay)
        if not _refresh_ok:
            # 100회 모두 실패 — critical
            from services.alerting.notifier import send_critical
            await send_critical("시작 시 레벨 갱신 100회 모두 실패 — 봇 시작 불가, 즉시 점검 필요")

        # systemd Type=notify 환경에서 "READY=1" — 없으면 no-op
        try:
            _sd_ready()
        except Exception:
            pass

        await asyncio.gather(
            self._run_websocket(),
            self._hourly_sync(),
        )

    async def _hourly_sync(self):
        """P7-06: 매 1시간 state ↔ exchange 포지션 교차검증."""
        _SKIP = {"KRW", "BTC", "info", "free", "used", "total",
                 "timestamp", "datetime"}
        while self.running:
            await asyncio.sleep(3600)
            if not self.running:
                break
            try:
                # plan 20260503 P3-2: with_retry 적용 (조회 전용, 1h 주기라 즉시성 무관)
                from services.execution.upbit_client import _create_exchange, with_retry
                exchange = _create_exchange()
                raw_balance = with_retry(exchange.fetch_balance)

                # 거래소 보유 코인 (KRW·BTC·메타키·먼지 제외)
                # 먼지 판별: 유효 마켓이 있고 평가액 > 5000원
                exchange_coins = set()
                alt_coins = {}
                for coin, amounts in raw_balance.items():
                    if coin in _SKIP or not isinstance(amounts, dict):
                        continue
                    total_amt = float(amounts.get("total", 0) or 0)
                    if total_amt > 0:
                        alt_coins[coin] = total_amt

                if alt_coins:
                    try:
                        markets = exchange.load_markets()
                    except Exception:
                        markets = {}
                    valid = [c for c in alt_coins if f"{c}/KRW" in markets]
                    if valid:
                        try:
                            tickers = with_retry(exchange.fetch_tickers, [f"{c}/KRW" for c in valid])
                            for c in valid:
                                sym = f"{c}/KRW"
                                price = float(tickers.get(sym, {}).get("last", 0) or 0)
                                if alt_coins[c] * price > 5000:
                                    exchange_coins.add(sym)
                        except Exception:
                            # 시세 조회 실패 시 보유량만으로 판단
                            for c in valid:
                                exchange_coins.add(f"{c}/KRW")

                # state 보유 코인 — 3개 전략 state 합집합 (lessons #10, worktree festive-thompson)
                #   composite: self.state["positions"]      (multi_trading_state.json)
                #   VB:        self._vb_positions            (vb_state.json) — DRY_RUN 시 가상 포지션 제외
                #   EMA Trend: self._ema_trend_position      (ema_trend_state.json, BTC/KRW 단일)
                composite_coins = set(self.state.get("positions", {}).keys())
                # 2026-05-06: VB_DRY_RUN=True면 가상 포지션이라 거래소 실잔고와 불일치 정상 → 검증 제외
                vb_coins = set()
                if hasattr(self, "_vb_positions") and not VB_DRY_RUN:
                    vb_coins = set(self._vb_positions.keys())
                ema_coins = {"BTC/KRW"} if getattr(self, "_ema_trend_position", None) else set()
                state_coins = composite_coins | vb_coins | ema_coins

                only_exchange = exchange_coins - state_coins
                only_state = state_coins - exchange_coins

                if only_exchange or only_state:
                    # plan 20260503 (race condition 보호 — AKT 12:15 false alarm 사고):
                    # ① 메모리 state는 매수 직후 timing race로 누락 가능 → 파일에서 재로드 (AC P0+)
                    # ② state 파일 mtime 60초 이내면 알림 보류, 다음 사이클(1h 후) 재검증.
                    import time as _t
                    _ws = Path(__file__).resolve().parents[2] / "workspace"
                    state_files = [
                        _ws / "multi_trading_state.json",
                        _ws / "vb_state.json",
                    ]
                    # state 파일 재로드 — 메모리 self.state 누락 보강
                    try:
                        reloaded_composite = set()
                        if state_files[0].exists():
                            with open(state_files[0], "r", encoding="utf-8") as f:
                                reloaded_composite = set(json.load(f).get("positions", {}).keys())
                        reloaded_vb = set()
                        if state_files[1].exists():
                            with open(state_files[1], "r", encoding="utf-8") as f:
                                reloaded_vb = set(json.load(f).get("positions", {}).keys())
                        reloaded_state = reloaded_composite | reloaded_vb | ema_coins
                        only_exchange = exchange_coins - reloaded_state
                        only_state = reloaded_state - exchange_coins
                        if not only_exchange and not only_state:
                            print(f"  [교차검증] 재로드 후 일치 OK — false alarm 회피", flush=True)
                            continue
                    except Exception as _e:
                        print(f"  [교차검증] state 재로드 실패, 메모리 기준 진행: {_e}", flush=True)

                    youngest = max(
                        (f.stat().st_mtime for f in state_files if f.exists()),
                        default=0,
                    )
                    state_age = _t.time() - youngest
                    if state_age < 60:
                        print(f"  [교차검증] 차집합 발견했지만 state 최근 갱신({state_age:.0f}s) — "
                              f"알림 보류, 다음 사이클 재검증 "
                              f"(exchange_only={only_exchange}, state_only={only_state})", flush=True)
                    else:
                        # plan 20260504: 2회 연속 동일 차집합 시만 알림 (false alarm 추가 차단)
                        # 2026-05-06 디바운스 2→3회 강화 (ML LIVE 활성화로 매매 빈도 ↑, 시차 알림 노이즈 ↓)
                        # signature 형식: "count|sigs"
                        REQUIRED_CONSEC = 3
                        pending_flag = Path("/tmp/bata_state_mismatch_pending")
                        sig = "|".join(sorted(only_exchange) + sorted(only_state))
                        if pending_flag.exists():
                            try:
                                last_raw = pending_flag.read_text(encoding="utf-8").strip()
                                if "::" in last_raw:
                                    last_count_str, last_sig = last_raw.split("::", 1)
                                    last_count = int(last_count_str) if last_count_str.isdigit() else 1
                                else:
                                    last_count = 1
                                    last_sig = last_raw
                            except Exception:
                                last_count, last_sig = 1, ""
                            if last_sig == sig:
                                new_count = last_count + 1
                                if new_count >= REQUIRED_CONSEC:
                                    msg = f"⚠️ *State ↔ Exchange 불일치 ({new_count}회 연속 확인)*\n"
                                    if only_exchange:
                                        msg += f"거래소에만 존재: {', '.join(only_exchange)}\n"
                                    if only_state:
                                        msg += f"State에만 존재: {', '.join(only_state)}\n"
                                    msg += f"자동 보정 없음 — 수동 확인 필요 (state {state_age:.0f}s 전 갱신, {new_count}회 연속)"
                                    await notify_error(msg)
                                    print(f"  [교차검증] {new_count}회 연속 불일치 — 알림 발송 + pending 클리어", flush=True)
                                    pending_flag.unlink(missing_ok=True)
                                else:
                                    pending_flag.write_text(f"{new_count}::{sig}", encoding="utf-8")
                                    print(f"  [교차검증] 차집합 {new_count}회 누적 (필요 {REQUIRED_CONSEC}회), 다음 사이클 재확인", flush=True)
                            else:
                                pending_flag.write_text(f"1::{sig}", encoding="utf-8")
                                print(f"  [교차검증] signature 변경 ({last_sig[:60]} → {sig[:60]}), 카운터 리셋", flush=True)
                        else:
                            pending_flag.write_text(f"1::{sig}", encoding="utf-8")
                            print(f"  [교차검증] 차집합 1회 발견 — 다음 사이클(1h 후) 재확인 대기 "
                                  f"(exchange_only={only_exchange}, state_only={only_state})", flush=True)
                else:
                    # 일치 OK 시 pending 클리어 (1회차 차집합 후 회복된 케이스)
                    pending_flag = Path("/tmp/bata_state_mismatch_pending")
                    if pending_flag.exists():
                        pending_flag.unlink(missing_ok=True)
                        print(f"  [교차검증] 일치 OK — pending 클리어 (false alarm 회피)", flush=True)
                    else:
                        print(f"  [교차검증] 일치 OK — {len(exchange_coins)}종목", flush=True)
            except Exception as e:
                print(f"  [교차검증] 오류: {e}", flush=True)

    async def _check_circuit_breaker_periodic(self):
        """레벨 갱신 주기마다 CB L2 발동/L1 자동해제 체크 (ADR 20260408_1).

        L2: 총자산 -25% 이하 → 전량 시장가 청산 + 경보
        L1 auto-resume: 총자산 95% 이상 회복 → L1 자동 해제 + 경보
        """
        if not CIRCUIT_BREAKER_ENABLED:
            return
        try:
            balance = get_balance()
            total_krw = balance.get("total_krw", 0)
        except RateLimitExhausted as e:
            # plan 20260503 P0 (cto 2차 #4): 주기 CB도 잔고 실패 시 동일 알람 정책
            print(f"  [CB-주기체크] 잔고 조회 429: {e}", flush=True)
            await self._notify_balance_fetch_fail("__periodic__", "rate_limit_periodic", str(e))
            return
        except Exception as e:
            print(f"  [CB-주기체크] 잔고 조회 실패: {e}", flush=True)
            return

        # L2: -25% 이하 → 전량 청산
        if check_and_trigger_l2(total_krw):
            loss_pct = (total_krw - CIRCUIT_BREAKER_INITIAL_CAPITAL) / CIRCUIT_BREAKER_INITIAL_CAPITAL * 100
            msg = (
                f"🚨 *CB L2 발동 — 전량 청산*\n"
                f"총자산: {total_krw:,.0f} KRW ({loss_pct:+.1f}%)\n"
                f"임계: -25% (ADR 20260408_1)\n"
                f"해제: 수동만 (workspace/circuit_breaker_state.json l2_triggered=false)"
            )
            print(f"\n  [CB-L2] {msg}", flush=True)
            try:
                await send(msg)
            except Exception:
                pass
            await self._liquidate_all_positions(reason="cb_l2")
            return

        # L1 자동 해제: 95% 회복
        if check_l1_auto_resume(total_krw):
            resume_pct = total_krw / CIRCUIT_BREAKER_INITIAL_CAPITAL * 100
            msg = (
                f"🟢 *CB L1 자동 해제*\n"
                f"총자산: {total_krw:,.0f} KRW ({resume_pct:.1f}%)\n"
                f"95% 회복 → 신규 매수 재개"
            )
            print(f"\n  [CB-L1-resume] {msg}", flush=True)
            try:
                await send(msg)
            except Exception:
                pass

    async def _liquidate_all_positions(self, reason: str = "cb_l2"):
        """보유 중인 전 포지션을 시장가로 즉시 청산.

        L2 발동 시 호출. _execute_sell을 재사용하여 기록/쿨다운/알림 일관성 유지.
        """
        positions = dict(self.state.get("positions", {}))
        if not positions:
            print(f"  [청산] 보유 포지션 없음 (reason={reason})", flush=True)
            return
        print(f"  [청산] {len(positions)}개 포지션 전량 청산 시작 (reason={reason})", flush=True)
        for symbol, pos in positions.items():
            price = self.levels.get(symbol, {}).get("close", pos.get("entry_price", 0))
            try:
                await self._execute_sell(symbol, price)
            except Exception as e:
                print(f"  [청산] {symbol} 실패: {e}", flush=True)

    async def _refresh_levels(self):
        # plan 20260504_2 AC14: 09:00 KST 자동 reset (cron 추가 없이 _refresh_levels 시점)
        try:
            from services.execution import daily_pl as _dpl
            if _dpl.reset_if_new_day():
                print(f"  [일일손익] 새 날 reset 완료", flush=True)
        except Exception as _e:
            print(f"  [일일손익] reset 실패 (무시): {_e}", flush=True)

        """전체 종목 레벨 갱신 (전략에 따라 일봉 또는 4시간봉)."""
        # ── CB 주기 체크 (L2 발동 / L1 자동 해제) ──
        await self._check_circuit_breaker_periodic()

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
                if fg_blocked:
                    record_block("fg_gate_daily", None)
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
                else:
                    # composite/그 외 — 거래량 필터용 vol_sma 5일 평균 (cto M4, plan AC8)
                    if len(df) >= 6:
                        try:
                            vol_sma = float(pd.Series(df["volume"]).rolling(5).mean().iloc[-1])
                            latest_vol = float(df["volume"].iloc[-1])
                            level["vol_sma"] = vol_sma
                            level["latest_vol"] = latest_vol
                        except Exception:
                            level["vol_sma"] = 0
                            level["latest_vol"] = 0

                new_levels[symbol] = level
                await asyncio.sleep(0.12)
            except Exception:
                continue

            if idx % 10 == 0 or idx == total:
                print(f"  진행: {idx}/{total} ({len(new_levels)}개 등록)", flush=True)
                # 레벨 갱신 중 systemd watchdog ping — 4분 블로킹 루프에서 timeout 방지
                # ref: lessons/20260417_2 (refresh_levels 중 watchdog ping 누락 → SIGABRT)
                try:
                    _sd_watchdog()
                    self._last_heartbeat = _time.monotonic()
                except Exception:
                    pass

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
        """정기 분석 보고 — plan 20260503 P3-1 (lessons #19 완전 해소).

        services.reporting.periodic_analysis 함수 호출로 산식 통일.
        텔레그램 발송은 plan P0(20260503_1)에서 비활성화 — 콘솔만.
        5연패 자동 중단은 보존 (회귀 검증 필수).
        """
        from services.reporting.periodic_analysis import (
            build_strategy_summary, check_consec_loss, build_market_snapshot,
        )

        positions = self.state.get("positions", {})
        now = datetime.now(tz=timezone.utc)
        now_str = now.strftime("%m/%d %H:%M UTC")

        try:
            balance = get_balance()
            krw = balance["krw"]
            total = balance["total_krw"]
        except Exception:
            krw = 0
            total = 0

        # ── 5연패 자동 중단 (회귀 보존 — cto P3 review #3) ──
        consec, n_trades, wins_n = check_consec_loss(self.state)
        if consec >= 5:
            win_rate = wins_n / n_trades * 100 if n_trades > 0 else 0
            self.running = False
            await send(
                f"🛑 *5연패 자동 중단*\n"
                f"연속 {consec}건 손실 — 검증 플랜 조기 탈출\n"
                f"승률: {win_rate:.0f}% ({wins_n}/{n_trades})\n"
                f"원인 분석 후 전략 수정 필요\n"
                f"재시작: `sudo systemctl restart btc-trader`"
            )
            print(f"\n!!! 5연패 자동 중단 !!!", flush=True)
            return

        # ── 시장 + 누적 성적 (함수 호출로 통일) ──
        market = build_market_snapshot()
        market_msg = ""
        if market.get("btc_price") is not None:
            _btc_chg = market.get("btc_chg") or 0  # lessons #12: None 대비
            market_msg = (
                f"\n*시장*\n"
                f"  BTC: {market['btc_price']:,.0f} ({_btc_chg:+.1f}%)\n"
                f"  F&G: {market.get('fg_value', '?')} ({market.get('fg_label', '?')})\n"
            )
        else:
            market_msg = "\n*시장* 조회 실패\n"

        summary = build_strategy_summary(self.state)

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

        # ── 누적 성적 + 백테스트 대비 + 체크포인트 (build_strategy_summary 통일) ──
        msg += f"\n*전략 성과*\n"
        for ln in summary.split("\n"):
            msg += f"  {ln}\n"

        # 잔고
        msg += f"\n\nKRW: {krw:,.0f} | 평가: {total:,.0f}"

        # plan 20260503 P0 (AC15, AC16): 정기 분석 텔레그램 발송 비활성화 → 18:00 daily_report 통합
        # 4h 주기 일 6회 → 18:00 KST 일 1회 (노이즈 -83%)
        # 함수 추출은 P2 plan에서 진행. 현재는 send(msg) 호출만 차단.
        # 메시지 자체는 콘솔/journalctl에 남아 사후 분석 가능
        next_h = ((now.hour // 4) + 1) * 4
        if next_h >= 24:
            next_h = 0
        msg += f"\n⏰ 다음: {next_h:02d}:05 UTC ({next_h+9:02d}:05 KST) — 18:00 통합으로 대체"
        print(f"\n[정기분석-텔레그램비활성화]\n{msg}\n", flush=True)
        # await send(msg)  # plan 20260503 P0: 18:00 통합으로 이전

    async def _run_websocket(self):
        """웹소켓 연결 및 실시간 체결가 수신."""
        reconnect_delay = WS_RECONNECT_BASE
        while self.running:
            try:
                print("\n웹소켓 연결 중...", flush=True)
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(UPBIT_WS_URL, heartbeat=30, timeout=30) as ws:
                        # 연결 성공 → 백오프 리셋
                        reconnect_delay = WS_RECONNECT_BASE

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

                        # P7-07: async for 대신 wait_for(timeout=300)으로 stale 감지
                        while True:
                            try:
                                msg = await asyncio.wait_for(ws.receive(), timeout=300)
                            except asyncio.TimeoutError:
                                print("  [WS-STALE] 5분간 메시지 없음 — 강제 재연결", flush=True)
                                await notify_error("웹소켓 5분간 메시지 미수신 — 강제 재연결")
                                break

                            if not self.running:
                                print("봇 중지 요청 — 웹소켓 종료")
                                return

                            if msg.type == aiohttp.WSMsgType.BINARY:
                                data = json.loads(msg.data.decode("utf-8"))
                                self._last_msg_ts = _time.monotonic()  # P7-07
                                await self._handle_tick(data)

                                # heartbeat: 2분마다 /tmp/bata_heartbeat touch + systemd watchdog
                                # WatchdogSec=300의 절반(150s 미만) 주기로 핑해야 경계조건 timeout 회피
                                # ref: lessons/20260417_2 (systemd notify watchdog 규칙)
                                _now_mono = _time.monotonic()
                                if _now_mono - self._last_heartbeat >= 120:
                                    self._last_heartbeat = _now_mono
                                    try:
                                        Path("/tmp/bata_heartbeat").touch()
                                    except Exception:
                                        # 로컬(Windows) / 권한 문제 시 조용히 통과
                                        pass
                                    # systemd WatchdogSec=300 핑 (P7-05)
                                    try:
                                        _sd_watchdog()
                                    except Exception:
                                        pass

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
                await self._handle_error("ws_connect", f"웹소켓 오류: {e}\n{UPBIT_WS_URL}")

            if self.running:
                print(f"{reconnect_delay}초 후 재연결...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, WS_RECONNECT_MAX)

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

            # 트레일링스탑 이탈 (SL 우선 — cto M1)
            if price < pos.get("trail_stop", 0):
                await self._execute_sell(symbol, price)
                return

            # 부분 익절 단계 평가 (SL 미발동 시) — plan 20260504_2 AC2
            if TP_ENABLED and not IS_DAYTRADING:
                await self._check_tp_levels(symbol, price, pos)
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
            # plan 20260503 P0+: atomic write
            import os as _os
            tmp = VB_STATE_FILE.with_suffix(VB_STATE_FILE.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            _os.replace(tmp, VB_STATE_FILE)
        except Exception as e:
            print(f"  [VB] 상태 저장 오류: {e}", flush=True)

    def _load_vb_full_state(self) -> dict:
        """P5-28 필터를 위해 vb_state 전체 로드 (positions + history + 필터 필드)."""
        if VB_STATE_FILE.exists():
            try:
                with open(VB_STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_vb_full_state(self, state: dict) -> None:
        """vb_state 전체 저장 (필터 필드 포함). plan 20260503 P0+: atomic write."""
        try:
            VB_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            import os as _os
            tmp = VB_STATE_FILE.with_suffix(VB_STATE_FILE.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            _os.replace(tmp, VB_STATE_FILE)
        except Exception as e:
            print(f"  [VB] 전체 상태 저장 오류: {e}", flush=True)

    async def _vb_daily_rotation(self):
        """VB 일일 회전: 기존 포지션 청산 → 새 신호 매수. 1일 1회만 실행.

        P5-28 필터 적용:
          A. 하락장 필터 (BTC<EMA200)     — 신규 매수 차단
          B. 데드 종목 블랙리스트          — 연속 0% 종목 제외
          C. 종목 집중도 캡 (주 3회)       — 과편중 방지
          D. 연패 쿨다운 (3연손 24h)       — 악화 추세 차단
        """
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

        # ── P5-28 진입 게이트 ──────────────────────────────
        vb_full = self._load_vb_full_state()
        dead_symbols = set(compute_dead_symbols(
            vb_full.get("history", []), VB_DEAD_SYMBOL_THRESHOLD))
        # 기존 dead_symbols + 새로 계산된 목록 병합 (한 번 들어가면 영구)
        prev_dead = set(vb_full.get("dead_symbols", []))
        dead_symbols |= prev_dead
        vb_full["dead_symbols"] = sorted(dead_symbols)
        weekly_count = vb_full.setdefault("weekly_count", {})
        cooldown_until = vb_full.get("loss_cooldown_until")

        gate_skip_reason = None
        # A. 하락장 필터
        if VB_BEAR_MARKET_FILTER and not self._btc_above_ema:
            gate_skip_reason = "A: 하락장(BTC<EMA200)"
            record_block("vb_gate_a_bearish", None)
        # D. 연패 쿨다운
        elif is_in_loss_cooldown(cooldown_until):
            gate_skip_reason = f"D: 연패 쿨다운 중 (until {cooldown_until})"

        if gate_skip_reason:
            print(f"  [VB] 신규 진입 차단 — {gate_skip_reason}", flush=True)
            self._save_vb_full_state(vb_full)
            await send(
                f"🚫 *VB 회전 스킵*\n사유: {gate_skip_reason}\n"
                f"청산 {closed}건, 매수 0건"
            )
            return

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

        # 돌파 강도 순 정렬
        signals.sort(key=lambda x: x[2], reverse=True)

        # P5-28 B/C 필터: 데드 종목 + 주간 집중도 캡
        filtered_signals = []
        skipped_dead = 0
        skipped_weekly = 0
        for sig in signals:
            sym = sig[0]
            if sym in dead_symbols:
                skipped_dead += 1
                continue
            if weekly_count_exceeded(weekly_count, sym, VB_MAX_WEEKLY_PER_SYMBOL):
                skipped_weekly += 1
                continue
            filtered_signals.append(sig)

        if skipped_dead or skipped_weekly:
            print(f"  [VB] 필터 스킵 — 데드 {skipped_dead}, 주간캡 {skipped_weekly}", flush=True)

        bought = 0
        for sym, level, score, k in filtered_signals[:VB_MAX_POSITIONS]:
            entry_price = level["close"]
            self._vb_positions[sym] = {
                "entry_price": entry_price,
                "entry_date": now_str,
                "k_value": k,
            }
            bump_weekly_count(weekly_count, sym)

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

        # P5-28 필터 필드 persistent 저장
        vb_full = self._load_vb_full_state()
        vb_full["dead_symbols"] = sorted(dead_symbols)
        vb_full["weekly_count"] = weekly_count
        self._save_vb_full_state(vb_full)

        mode = "DRY-RUN" if VB_DRY_RUN else "실전"
        await send(
            f"📊 *VB 일일 회전* ({mode})\n"
            f"청산: {closed}건 / 매수: {bought}건\n"
            f"감시: {len(self._vb_positions)}종목\n"
            f"데드: {len(dead_symbols)}개 / 주간스킵: {skipped_weekly}"
        )
        print(f"  [VB] 일일 회전 완료: 청산 {closed}건, 매수 {bought}건, "
              f"데드 {len(dead_symbols)}, 주간스킵 {skipped_weekly}", flush=True)

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

            # P5-28 D. 연패 쿨다운 체크 & 발동
            vb_full = self._load_vb_full_state()
            consec = recent_consecutive_losses(vb_full.get("history", []))
            if consec >= VB_LOSS_COOLDOWN_N:
                until_iso = set_loss_cooldown(VB_LOSS_COOLDOWN_HOURS)
                vb_full["loss_cooldown_until"] = until_iso
                self._save_vb_full_state(vb_full)
                print(f"  [VB] 🧊 연패 쿨다운 발동 {consec}연손 → {VB_LOSS_COOLDOWN_HOURS}h OFF (until {until_iso})", flush=True)
                await send(
                    f"🧊 *VB 연패 쿨다운 발동*\n"
                    f"{consec}연속 손절 → {VB_LOSS_COOLDOWN_HOURS}h 신규 진입 중단\n"
                    f"해제: {until_iso}"
                )

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

    async def _check_tp_levels(self, symbol: str, price: float, pos: dict):
        """plan 20260504_2 AC2-AC4: 부분 익절 단계별 매도.

        잔량 회계 모델 (cto BLOCK-1):
          - entry_amount_krw: 불변 (최초 매수 KRW)
          - tp_sold_levels: 이미 매도된 단계 인덱스 list
          - 매도 후 fetch_balance 재조회로 remaining_qty 갱신
        실패 시 state 변경 안 함, 다음 가격 틱에서 재평가 (lessons #3 즉시성).
        """
        entry = pos.get("entry_price", 0)
        if entry <= 0:
            return
        ret_pct = (price / entry - 1.0)
        sold_levels = set(pos.get("tp_sold_levels", []))

        for idx, tp in enumerate(TP_LEVELS):
            if idx in sold_levels:
                continue
            if ret_pct < tp["trigger_pct"]:
                continue
            # TP 트리거 — 부분 매도
            try:
                coin = symbol.split("/")[0]
                ex = _create_exchange()
                bal = ex.fetch_balance()
                cur_total = float(bal.get(coin, {}).get("total", 0) or 0)
                cur_free = float(bal.get(coin, {}).get("free", 0) or 0)
                if cur_total <= 0:
                    print(f"  [TP] {symbol} 잔고 없음 (total=0) — TP 스킵, position 정리 필요", flush=True)
                    return
                # 매도 수량: 매수 시점 entry_qty 기준 비율 (잔량 변동 무관 일관성)
                # 단, 최초 진입 후 첫 TP에선 entry_qty 미저장 가능 → cur_total 기준
                entry_qty = pos.get("entry_qty") or cur_total
                sell_qty = round(entry_qty * tp["sell_ratio"], 8)
                if sell_qty > cur_free:
                    sell_qty = round(cur_free, 8)
                if sell_qty <= 0:
                    print(f"  [TP] {symbol} sell_qty {sell_qty} <= 0 — 스킵", flush=True)
                    return
                # 최소 주문 검증
                if sell_qty * price < MIN_ORDER_KRW:
                    print(f"  [TP] {symbol} 매도금 {sell_qty*price:.0f} < 최소 {MIN_ORDER_KRW}, 잔량 마지막 단계로 통합", flush=True)
                    sell_qty = round(cur_free, 8)
                # 시장가 매도 (lessons #3: retry 없음)
                order = sell_market_coin(symbol, sell_qty)
                exec_price = float(order.get("price") or price)
                pl_krw = (exec_price - entry) * sell_qty
                # state 갱신
                sold_levels.add(idx)
                pos["tp_sold_levels"] = sorted(sold_levels)
                # entry_qty 최초 기록
                if "entry_qty" not in pos:
                    pos["entry_qty"] = entry_qty
                # remaining_qty 재조회
                bal_after = ex.fetch_balance()
                pos["remaining_qty"] = float(bal_after.get(coin, {}).get("total", 0) or 0)
                save_state(self.state)
                # 일일 손익 기록
                from services.execution import daily_pl as _dpl
                _dpl.record_sell(symbol, pl_krw, exec_price, sell_qty)
                # 알림
                msg = (
                    f"[TP{idx+1}] {symbol} 부분 익절\n"
                    f"  단계: +{tp['trigger_pct']*100:.0f}% / 매도 비율 {tp['sell_ratio']*100:.0f}%\n"
                    f"  체결가: {exec_price:,.4f} | 수량: {sell_qty}\n"
                    f"  실현손익: {pl_krw:+,.0f} KRW\n"
                    f"  잔량: {pos['remaining_qty']}"
                )
                print(f"  [TP{idx+1}] {symbol} 부분 익절 체결 — pl={pl_krw:+.0f}", flush=True)
                try:
                    from services.alerting.notifier import send_report
                    await send_report(msg, parse_mode=None)
                except Exception:
                    pass
                # 같은 틱에서 다음 단계도 트리거 가능 — break 하지 않음
            except Exception as e:
                print(f"  [TP] {symbol} 부분 매도 실패: {e} — state 미변경, 다음 틱 재평가", flush=True)
                return

    async def _notify_balance_fetch_fail(self, symbol: str, kind: str, detail: str):
        """plan 20260503 P0 (AC8): 잔고 조회 실패 시 알람 — 첫 알람 즉시, 2회차부터 1h 디바운스.

        영구 매수 차단 트랩 방지: 사용자가 즉시 인지하여 수동 복구할 수 있도록.
        """
        import time as _t
        flag_path = Path("/tmp/bata_balance_fail_alert_flag")
        now = _t.time()
        debounce_sec = 3600  # 1h
        send_alarm = True
        if flag_path.exists():
            age = now - flag_path.stat().st_mtime
            if age < debounce_sec:
                send_alarm = False
                print(f"  [잔고실패] 디바운스 ({int(age)}s < {debounce_sec}s) — 알람 스킵", flush=True)
        if send_alarm:
            try:
                msg = (
                    f"[BATA] 잔고 조회 실패 — 매수 차단\n"
                    f"종목: {symbol}\n"
                    f"종류: {kind}\n"
                    f"상세: {detail[:200]}\n"
                    f"조치: 서버 healthcheck + critical_healthcheck.log 확인\n"
                    f"디바운스 1h (다음 알람은 복구 안 되면 1시간 후)"
                )
                # plan 20260503 P3-3: send_critical 등급 마이그레이션
                from services.alerting.notifier import send_critical
                await send_critical(msg)
                flag_path.touch()
            except Exception as e:
                print(f"  [잔고실패] 알람 발송 실패: {e}", flush=True)

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
            # lessons #14 — 이벤트 루프 내 로그 throttle 필수.
            # 종목수×신호빈도 곱으로 폭발 → systemd WatchdogSec 미충족 → 만성 재시작 (5-3 13회).
            from services.common.log_throttle import throttled_print
            throttled_print(
                "loss_cooldown_skip",
                f"  [쿨다운] 연패 쿨다운 중 — {remaining_h:.1f}h 남음 (매수 스킵)",
                interval_sec=60,
            )
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

        # ── 신호 발화 dedupe (lessons #1, C-FIX 20260505 → 20260506 완화) ───
        # 60초 내 재시도만 차단. ORDER/KRW 1.6건/초 폭주 → 분당 1회로 차단.
        # 같은 봉 차단은 제거 — 진짜 신호까지 막혀 매수 0건 발생 (1.5h 257건 차단 / 매수 0건).
        _now_ts = _time.time()
        _last = self._signal_dedupe.get(symbol)
        if _last is not None and (_now_ts - _last["ts"]) < 60:
            if _now_ts - self._dedupe_log_ts > 60:
                print(f"  [신호 dedupe] {symbol} 60s 내 재시도 차단", flush=True)
                self._dedupe_log_ts = _now_ts
            return
        self._signal_dedupe[symbol] = {"ts": _now_ts}

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
                _now = _time.monotonic()
                if is_l2_triggered():
                    if _now - self._cb_log_ts > 60:
                        print(f"  [서킷브레이커-L2] 발동 중 — {symbol} 매수 차단", flush=True)
                        self._cb_log_ts = _now
                    record_block("cb_l2", symbol)
                    return
                if is_triggered():
                    if _now - self._cb_log_ts > 60:
                        print(f"  [서킷브레이커] 발동 중 — {symbol} 매수 차단", flush=True)
                        self._cb_log_ts = _now
                    record_block("cb_l1", symbol)
                    return
            except RateLimitExhausted as e:
                # plan 20260503 P0 (AC7-AC10): 잔고 조회 429 → 매수 차단 (silent fallback 금지)
                print(f"  [서킷브레이커] 잔고 조회 429 실패, 매수 차단: {e}", flush=True)
                last = load_last_known_balance(max_age_hours=24)
                if last:
                    _last_total = last.get("total_krw") or 0
                    print(f"  [서킷브레이커] last_known 캐시 ({_last_total:,.0f}) 사용", flush=True)
                else:
                    print(f"  [서킷브레이커] 캐시 만료/없음, 보수적 평가로 매수 차단", flush=True)
                await self._notify_balance_fetch_fail(symbol, "rate_limit", str(e))
                record_block("balance_fetch_fail", symbol)
                return
            except Exception as e:
                # plan 20260503 P0: 일반 오류도 매수 차단 (이전엔 silent 매수 진행 → 안전장치 우회)
                print(f"  [서킷브레이커] 잔고 조회 실패, 매수 차단: {e}", flush=True)
                await self._notify_balance_fetch_fail(symbol, "general", str(e))
                record_block("balance_fetch_fail", symbol)
                return

        # 연패 쿨다운 확인 (3연패 시 3일 매수 중단)
        if self._is_loss_cooldown():
            return

        # v2 필터: F&G 게이트 (F&G < 20이면 진입 차단)
        if self._fg_value is not None and self._fg_value < 20:
            record_block("fg_gate", symbol)
            return

        # v2 필터: BTC EMA(200) 필터 (BTC < EMA200이면 전 종목 신규 매수 차단)
        if not self._btc_above_ema:
            record_block("ema200_filter", symbol)
            return

        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # 거래량 필터 — plan 20260504_2 AC7 (가짜 돌파 차단)
        if VOL_FILTER_ENABLED and not IS_DAYTRADING:
            vol_sma = level.get("vol_sma", 0) or 0
            latest_vol = level.get("latest_vol", 0) or 0
            if vol_sma > 0 and latest_vol < vol_sma * VOL_FILTER_MULTIPLIER:
                from services.common.log_throttle import throttled_print
                throttled_print(
                    f"vol_filter_{symbol}",
                    f"  [{symbol}] 거래량 필터 — {latest_vol:.0f} < {vol_sma:.0f}×{VOL_FILTER_MULTIPLIER} 차단",
                    interval_sec=60,
                )
                record_block("vol_filter", symbol)
                return

        # 일일 손실 한도 — plan 20260504_2 AC12 (단일 사건 후 추가 손실 방어)
        if DAILY_LOSS_LIMIT_ENABLED:
            from services.execution import daily_pl as _dpl
            blocked, loss_pct, limit_pct = _dpl.is_daily_loss_blocked(
                DAILY_LOSS_LIMIT_PCT, DAILY_LOSS_BASE_KRW
            )
            if blocked:
                # 첫 발동 시 critical 알람
                state_d = _dpl.get_state()
                if not state_d.get("alarm_sent_today"):
                    try:
                        from services.alerting.notifier import send_critical
                        await send_critical(
                            f"[BATA] 일일 손실 한도 발동 — 매수 24h 차단\n"
                            f"실현손익: {(state_d.get('realized_pl_krw') or 0):,.0f} KRW\n"
                            f"손실률: {loss_pct*100:.2f}% / 한도 {limit_pct*100:.0f}%\n"
                            f"기존 포지션 트레일링은 정상, 09:00 KST 자동 reset",
                            parse_mode=None,
                        )
                        state_d["alarm_sent_today"] = True
                        _dpl._save(state_d)
                    except Exception as _e:
                        print(f"  [일일손실] 알람 실패: {_e}", flush=True)
                record_block("daily_loss_limit", symbol)
                return

        # 변동성 필터 — ATR이 가격의 MAX_ATR_PCT 이상이면 진입 차단
        # (lessons/20260408_5 — ONG 같은 고변동 알트 방어)
        if not IS_DAYTRADING and level.get("atr"):
            atr_pct = level["atr"] / price
            if atr_pct > MAX_ATR_PCT:
                # plan 20260503 (lessons #14 강화): ATR 차단 로그 throttle (종목당 60s 1회)
                from services.common.log_throttle import throttled_print
                throttled_print(
                    f"atr_filter_{symbol}",
                    f"  [{symbol}] ATR 필터 — ATR/price={atr_pct:.1%} > {MAX_ATR_PCT:.0%} 차단",
                    interval_sec=60,
                )
                record_block("atr_filter", symbol)
                return
        # ── ML 신호 필터 게이트 (fail-open, lessons #6) ─────
        # 모든 사전 필터(서킷브레이커/F&G/EMA200/거래량/ATR) 통과 후 마지막 게이트.
        # ML 비활성/모델부재/추론실패 시 score=1.0 → 항상 통과.
        _ml_flt = _get_ml_filter()
        _ml_score = 1.0
        if _ml_flt.is_active:
            _ohlcv = level.get("ohlcv")  # 주입 시 사용, 없으면 fail-open
            if _ohlcv is not None:
                try:
                    _ml_score = _ml_flt.score(symbol, _ohlcv, pd.Timestamp.now(tz="UTC"))
                except Exception as _e:
                    print(f"  [{symbol}] ML 점수 실패: {_e} — fail-open", flush=True)
        _ml_pass = _ml_flt.passes(_ml_score)
        _ml_shadow.log_decision(
            symbol=symbol, signal_ts=pd.Timestamp.now(tz="UTC"),
            signal_type="DC_breakout_realtime", score=_ml_score,
            threshold=_ml_flt.threshold, will_buy=_ml_pass,
            ml_active=_ml_flt.is_active,
        )
        if not _ml_pass:
            from services.common.log_throttle import throttled_print
            throttled_print(
                f"ml_filter_{symbol}",
                f"  [{symbol}] ML 차단 score={_ml_score:.3f} < {_ml_flt.threshold}",
                interval_sec=60,
            )
            record_block("ml_filter", symbol)
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
        except RateLimitExhausted as e:
            # plan 20260503 P0 (AC4 — cto 2차 review): 두 번째 잔고 조회도 critical 알람 + 매수 차단
            self._set_cooldown(symbol)
            await self._notify_balance_fetch_fail(symbol, "rate_limit_buy", str(e))
            return
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

        # plan 20260504_2 AC3 (cto BLOCK-1): entry_qty/entry_amount_krw 불변 저장 — 부분 익절 잔량 회계 기준
        # DRY_RUN인 경우 order는 None이므로 추정값 사용
        try:
            entry_qty = float((order or {}).get("amount") if not DRY_RUN else 0) or (order_amount / exec_price if exec_price > 0 else 0)
        except Exception:
            entry_qty = 0
        positions[symbol] = {
            "entry_date": today,
            "entry_price": exec_price,
            "highest": exec_price,
            "trail_stop": trail_stop,
            "order_amount": order_amount,
            "entry_amount_krw": order_amount,    # 불변 (TP 잔량 회계 기준)
            "entry_qty": entry_qty,              # 불변 (부분 매도 수량 산정 기준)
            "tp_sold_levels": [],                # 단계별 매도 추적
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
                # worktree festive-thompson: locked(used) 잔고 보존 — 거래소 잔고가 진짜 0일 때만 state 정리
                amounts = bal.get(coin_id, {}) if isinstance(bal.get(coin_id), dict) else {}
                total_amount = float(amounts.get("total", 0) or 0)
                free_amount = float(amounts.get("free", 0) or 0)

                if total_amount <= 0:
                    print(f"  {symbol} 잔고 없음 (total=0)")
                    del positions[symbol]
                    self.state["positions"] = positions
                    save_state(self.state)
                    return

                # 매도 가능 수량: free 우선 (locked 제외), free=0이면 매도 불가 → 보존
                coin_amount = free_amount if free_amount > 0 else 0
                if coin_amount <= 0:
                    print(f"  {symbol} 매도 가능 수량 없음 (free=0, total={total_amount}) — 포지션 보존")
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

        # 일일 손실 한도 — 실현 KRW 손익 누적 (plan 20260504_2 AC13)
        try:
            from services.execution import daily_pl as _dpl
            sold_qty_full = float(coin_amount) if not DRY_RUN else float(pos.get("entry_qty") or 0)
            if sold_qty_full > 0:
                pl_krw = (float(exec_price) - float(pos["entry_price"])) * sold_qty_full
                _dpl.record_sell(symbol, pl_krw, float(exec_price), sold_qty_full)
        except Exception as _e:
            print(f"  [일일손익] 기록 실패 (무시): {_e}", flush=True)

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
