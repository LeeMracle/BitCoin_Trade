"""실전 매매 일일 체크 스크립트.

매일 09:05 KST 실행 (업비트 일봉 마감 후).
업비트 KRW 마켓 전체 스캔 → 돌파 종목 매수 / 보유 종목 청산 확인.

crontab:
  5 0 * * * cd /home/ubuntu/BitCoin_Trade && /home/ubuntu/BitCoin_Trade/.venv/bin/python scripts/daily_live.py >> /var/log/btc_trader.log 2>&1
"""
import sys, io, asyncio
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.execution.multi_trader import run


async def main():
    print("=" * 60)
    print("멀티코인 자동매매 일일 실행")
    print("=" * 60)
    await run(dry_run=False)
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
