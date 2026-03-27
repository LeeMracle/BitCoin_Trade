"""자동매매 메인 실행 스크립트.

config.py의 MONITOR_MODE에 따라 동작:
  "realtime" — 웹소켓 실시간 감시 (상시 실행)
  "1h"       — 1시간마다 스캔 (cron: 매시 5분)
  "4h"       — 4시간마다 스캔 (cron: 0,4,8,12,16,20시 5분)
  "daily"    — 하루 1회 (cron: 09:05 KST)

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

    else:
        # 1회 스캔 (cron에서 호출)
        from services.execution.multi_trader import run
        print("=" * 60)
        print(f"자동매매 스캔 실행 (모드: {mode})")
        print("=" * 60)
        await run(dry_run=False)
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
