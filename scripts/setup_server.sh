#!/bin/bash
set -e

cd /home/ubuntu/BitCoin_Trade

echo "=== 1. .env 생성 ==="
cat > services/.env << 'ENVEOF'
UPBIT_ACCESS_KEY=bOshvyFH5MX5O6VpkU0PasW1wxRQUwx1nCGrlSMJ
UPBIT_SECRET_KEY=mSscKjzUHiSXhx4YKpmtD4uF0MrBRZpQh3wcoPG8
TELEGRAM_BOT_TOKEN=8737963559:AAEmfj8BBIcdgSf31p1_TwkGu1JGf891k_M
TELEGRAM_CHAT_ID=8200493718
ENVEOF

echo "=== 2. pip 설치 ==="
.venv/bin/pip install -q -e "./services[dev]"

echo "=== 3. dry-run 테스트 ==="
.venv/bin/python -m services.execution.trader --dry-run

echo "=== 4. crontab 등록 (매일 KST 09:05) ==="
crontab -l 2>/dev/null | grep -v daily_live > /tmp/cron_tmp || true
echo "5 0 * * * cd /home/ubuntu/BitCoin_Trade && /home/ubuntu/BitCoin_Trade/.venv/bin/python scripts/daily_live.py >> /var/log/btc_trader.log 2>&1" >> /tmp/cron_tmp
crontab /tmp/cron_tmp
rm /tmp/cron_tmp

echo "=== 완료! ==="
echo "crontab 확인:"
crontab -l
