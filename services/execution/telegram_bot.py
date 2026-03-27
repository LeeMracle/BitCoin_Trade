"""텔레그램 봇 명령어 핸들러.

실시간 모니터와 함께 실행되어 텔레그램 명령으로 봇을 제어.

명령어:
  /status   — 보유 종목, 잔고, 수익률
  /scan     — 현재 돌파/근접 종목
  /stop     — 봇 중지
  /start    — 봇 재시작
  /mode     — 거래 주기 변경 (realtime/1h/4h/daily)
  /config   — 현재 설정 확인
  /reset    — 상태 초기화
  /help     — 명령어 목록
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(_env_path)

TELEGRAM_API = "https://api.telegram.org/bot{token}"
CONFIG_FILE = Path(__file__).resolve().parent / "config.py"


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def _chat_id():
    return os.environ.get("TELEGRAM_CHAT_ID", "")


async def send_message(text: str):
    token = _token()
    chat_id = _chat_id()
    if not token or not chat_id:
        return
    url = f"{TELEGRAM_API.format(token=token)}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)):
                pass
    except Exception:
        pass


class TelegramCommandHandler:
    def __init__(self, monitor=None):
        self.monitor = monitor  # RealtimeMonitor 참조
        self.last_update_id = 0
        self.running = True

    async def start_polling(self):
        """텔레그램 명령어 폴링 시작."""
        print("텔레그램 명령어 수신 대기 중...", flush=True)
        while self.running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except Exception as e:
                print(f"텔레그램 폴링 오류: {e}", flush=True)
            await asyncio.sleep(2)

    async def _get_updates(self) -> list:
        token = _token()
        if not token:
            return []
        url = f"{TELEGRAM_API.format(token=token)}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 10}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()
                    results = data.get("result", [])
                    if results:
                        self.last_update_id = results[-1]["update_id"]
                    return results
        except Exception:
            return []

    async def _handle_update(self, update: dict):
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        # 본인 chat_id만 허용
        if chat_id != _chat_id():
            return

        if not text.startswith("/"):
            return

        parts = text.split()
        cmd = parts[0].lower().split("@")[0]  # /command@botname 처리
        args = parts[1:] if len(parts) > 1 else []

        handlers = {
            "/status": self._cmd_status,
            "/scan": self._cmd_scan,
            "/stop": self._cmd_stop,
            "/start": self._cmd_start,
            "/mode": self._cmd_mode,
            "/config": self._cmd_config,
            "/reset": self._cmd_reset,
            "/help": self._cmd_help,
        }

        handler = handlers.get(cmd)
        if handler:
            await handler(args)
        else:
            await send_message(f"알 수 없는 명령: {cmd}\n/help 로 명령어 확인")

    # ── 명령어 구현 ──────────────────────────────────────

    async def _cmd_help(self, args):
        await send_message(
            "📋 *명령어 목록*\n\n"
            "/status — 보유 종목, 잔고, 수익률\n"
            "/scan — 현재 돌파/근접 종목\n"
            "/stop — 봇 중지\n"
            "/start — 봇 재시작\n"
            "/mode — 거래 주기 변경\n"
            "  `/mode realtime` 실시간\n"
            "  `/mode 1h` 1시간\n"
            "  `/mode 4h` 4시간\n"
            "  `/mode daily` 하루 1회\n"
            "/config — 현재 설정 확인\n"
            "/reset — 상태 초기화\n"
            "/help — 이 메시지"
        )

    async def _cmd_status(self, args):
        from services.execution.multi_trader import load_state
        state = load_state()
        positions = state.get("positions", {})
        closed = state.get("closed_trades", [])

        try:
            from services.execution.upbit_client import get_balance
            balance = get_balance()
            krw = balance["krw"]
            total = balance["total_krw"]
        except Exception:
            krw = 0
            total = 0

        msg = f"📊 *현재 상태*\n\n"
        msg += f"KRW 잔고: {krw:,.0f}\n"
        msg += f"총 평가: {total:,.0f}\n"
        msg += f"보유: {len(positions)}/5\n"

        if positions:
            msg += "\n*보유 종목:*\n"
            for sym, pos in positions.items():
                msg += f"  {sym}\n"
                msg += f"    진입: {pos['entry_price']:,.0f}\n"
                msg += f"    스탑: {pos.get('trail_stop', 0):,.0f}\n"

        wins = [t for t in closed if t["return_pct"] > 0]
        msg += f"\n거래: {len(closed)}회"
        if closed:
            msg += f" | 승률: {len(wins)/len(closed)*100:.0f}%"
            total_ret = sum(t["return_pct"] for t in closed)
            msg += f"\n누적: {total_ret:+.1f}%"

        if self.monitor:
            msg += f"\n\n감시: {len(self.monitor.levels)}종목"
            msg += f"\n봇 상태: {'🟢 실행중' if self.monitor.running else '🔴 중지'}"

        await send_message(msg)

    async def _cmd_scan(self, args):
        await send_message("🔍 스캔 중... (약 2분 소요)")

        try:
            from services.execution.scanner import get_krw_market_coins, scan_entry_signals
            coins = get_krw_market_coins()
            signals = await scan_entry_signals(coins)

            buy_signals = [s for s in signals if s["signal"] == "BUY"]
            near_signals = [s for s in signals if s["signal"] == "NEAR"]

            msg = f"📡 *스캔 결과*\n"
            msg += f"검색: {len(coins)}종목\n\n"

            if buy_signals:
                msg += f"*돌파 ({len(buy_signals)}개):*\n"
                for s in buy_signals[:10]:
                    msg += f"  {s['symbol']} {s['price']:,.0f}\n"
            else:
                msg += "돌파: 없음\n"

            if near_signals:
                msg += f"\n*근접 3% ({len(near_signals)}개):*\n"
                for s in near_signals[:10]:
                    msg += f"  {s['symbol']} ({s['distance_pct']:+.1f}%)\n"
            else:
                msg += "\n근접: 없음"

            await send_message(msg)
        except Exception as e:
            await send_message(f"⚠️ 스캔 오류: {e}")

    async def _cmd_stop(self, args):
        if self.monitor:
            self.monitor.running = False
            await send_message("🔴 *봇 중지*\n재시작: /start")
        else:
            await send_message("봇 참조 없음")

    async def _cmd_start(self, args):
        if self.monitor:
            if self.monitor.running:
                await send_message("이미 실행 중입니다")
            else:
                await send_message(
                    "🟢 *봇 재시작 중...*\n"
                    "서버에서 재시작 필요:\n"
                    "`sudo systemctl restart btc-trader`"
                )
        else:
            await send_message("봇 참조 없음")

    async def _cmd_mode(self, args):
        if not args:
            from services.execution.config import MONITOR_MODE
            await send_message(
                f"현재 모드: *{MONITOR_MODE}*\n\n"
                "변경: `/mode [옵션]`\n"
                "  `realtime` — 실시간 웹소켓\n"
                "  `5m` — 5분마다\n"
                "  `10m` — 10분마다\n"
                "  `30m` — 30분마다\n"
                "  `1h` — 1시간마다\n"
                "  `4h` — 4시간마다\n"
                "  `daily` — 하루 1회"
            )
            return

        new_mode = args[0].lower()
        valid_modes = ["realtime", "5m", "10m", "30m", "1h", "4h", "daily"]
        if new_mode not in valid_modes:
            await send_message(f"유효하지 않은 모드: {new_mode}\n옵션: {', '.join(valid_modes)}")
            return

        try:
            _update_config("MONITOR_MODE", f'"{new_mode}"')
            await send_message(
                f"✅ 모드 변경: *{new_mode}*\n\n"
                "적용하려면 재시작 필요:\n"
                "`sudo systemctl restart btc-trader`"
            )
        except Exception as e:
            await send_message(f"⚠️ 설정 변경 실패: {e}")

    async def _cmd_config(self, args):
        try:
            from services.execution import config
            msg = (
                f"⚙️ *현재 설정*\n\n"
                f"모드: `{config.MONITOR_MODE}`\n"
                f"전략: Donchian({config.DONCHIAN_PERIOD})+ATR({config.ATR_PERIOD})x{config.ATR_MULTIPLIER}\n"
                f"최대 포지션: {config.MAX_POSITIONS}\n"
                f"최소 거래대금: {config.MIN_VOLUME_KRW/1e8:.0f}억\n"
                f"DRY-RUN: {config.DRY_RUN}\n"
                f"오류 한도: {config.MAX_CONSECUTIVE_ERRORS}회\n"
            )
            await send_message(msg)
        except Exception as e:
            await send_message(f"설정 로드 실패: {e}")

    async def _cmd_reset(self, args):
        from services.execution.multi_trader import load_state, save_state
        state = load_state()
        positions = state.get("positions", {})

        if positions:
            await send_message(
                f"⚠️ 보유 종목 {len(positions)}개 있습니다.\n"
                f"종목: {', '.join(positions.keys())}\n\n"
                f"정말 초기화하려면:\n`/reset confirm`"
            )
            if args and args[0] == "confirm":
                state = {"positions": {}, "closed_trades": [], "last_updated": ""}
                save_state(state)
                await send_message("✅ 상태 초기화 완료")
        else:
            state = {"positions": {}, "closed_trades": [], "last_updated": ""}
            save_state(state)
            await send_message("✅ 상태 초기화 완료")


def _update_config(key: str, value: str):
    """config.py에서 특정 키의 값을 변경."""
    content = CONFIG_FILE.read_text(encoding="utf-8")
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key} =") or line.strip().startswith(f"{key}="):
            # 기존 주석 유지
            comment = ""
            if "#" in line:
                idx = line.index("#")
                comment = "  " + line[idx:]
            indent = len(line) - len(line.lstrip())
            new_lines.append(f"{' ' * indent}{key} = {value}{comment}")
        else:
            new_lines.append(line)
    CONFIG_FILE.write_text("\n".join(new_lines), encoding="utf-8")
