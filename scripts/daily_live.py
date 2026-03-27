"""자동매매 메인 실행 스크립트.

config.py의 MONITOR_MODE에 따라 동작:
  "realtime" — 웹소켓 실시간 감시 (상시 실행)
  "5m"       — 5분마다 스캔
  "10m"      — 10분마다 스캔
  "30m"      — 30분마다 스캔
  "1h"       — 1시간마다 스캔
  "4h"       — 4시간마다 스캔
  "daily"    — 하루 1회

실행:
  python scripts/daily_live.py             (config 설정대로)
  python scripts/daily_live.py --realtime  (강제 실시간)
  python scripts/daily_live.py --scan      (1회 스캔)
"""
import sys, io, asyncio
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution.config import MONITOR_MODE

INTERVAL_MAP = {
    "5m": 300,
    "10m": 600,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "daily": 86400,
}


async def run_interval(seconds: int, mode: str):
    """주기적 스캔 + 텔레그램 명령어 동시 실행."""
    from services.execution.multi_trader import run
    from services.execution.telegram_bot import TelegramCommandHandler

    cmd_handler = TelegramCommandHandler()
    polling_task = asyncio.create_task(cmd_handler.start_polling())

    print(f"주기 모드: {mode} ({seconds}초 간격)", flush=True)

    while True:
        try:
            print(f"\n{'='*60}", flush=True)
            print(f"스캔 실행 ({mode})", flush=True)
            print(f"{'='*60}", flush=True)
            await run(dry_run=False)
        except Exception as e:
            print(f"스캔 오류: {e}", flush=True)

        print(f"\n다음 스캔까지 {seconds}초 대기...", flush=True)
        await asyncio.sleep(seconds)


async def main():
    args = sys.argv[1:]

    if "--realtime" in args:
        mode = "realtime"
    elif "--scan" in args:
        mode = "scan"
    else:
        mode = MONITOR_MODE

    if mode == "realtime":
        from services.execution.realtime_monitor import main as rt_main
        await rt_main()

    elif mode == "scan":
        from services.execution.multi_trader import run
        print("=" * 60, flush=True)
        print("1회 스캔", flush=True)
        print("=" * 60, flush=True)
        await run(dry_run=False)

    elif mode in INTERVAL_MAP:
        await run_interval(INTERVAL_MAP[mode], mode)

    else:
        print(f"알 수 없는 모드: {mode}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
