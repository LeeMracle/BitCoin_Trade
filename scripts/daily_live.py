"""실전 매매 일일 체크 스크립트.

매일 09:05 KST 실행 (업비트 일봉 마감 후).
AWS 서버(13.209.165.58)에서 cron으로 실행 권장.

crontab 등록:
  5 0 * * * cd /home/ubuntu/BitCoin_Trade && /home/ubuntu/BitCoin_Trade/.venv/bin/python scripts/daily_live.py >> /var/log/btc_trader.log 2>&1
  (UTC 00:05 = KST 09:05)
"""
import sys, io, asyncio
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution.trader import run


async def main():
    print("=" * 60)
    print("실전 매매 일일 체크")
    print("=" * 60)
    await run(dry_run=False)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
