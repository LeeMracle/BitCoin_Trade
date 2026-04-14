#!/bin/bash
# BATA 로그 볼륨 감시
# cron: 매일 00:10 UTC (09:10 KST)에 전일 로그 볼륨 체크

SERVICE="btc-trader"
YESTERDAY=$(date -d "yesterday" +%Y-%m-%d)
LOG_DIR="/home/ubuntu/BitCoin_Trade/logs"
mkdir -p "$LOG_DIR"

# 전일 로그 라인 수
TOTAL=$(journalctl -u "$SERVICE" --since "$YESTERDAY 00:00:00" --until "$YESTERDAY 23:59:59" --no-pager 2>/dev/null | wc -l)

# 오류 관련 라인 수
ERRORS=$(journalctl -u "$SERVICE" --since "$YESTERDAY 00:00:00" --until "$YESTERDAY 23:59:59" --no-pager 2>/dev/null | grep -ciE '오류|에러|실패|error|timeout|traceback')

# 마켓없음 스팸 라인 수
MARKET_SPAM=$(journalctl -u "$SERVICE" --since "$YESTERDAY 00:00:00" --until "$YESTERDAY 23:59:59" --no-pager 2>/dev/null | grep -c '마켓 없음')

# 기록
echo "$YESTERDAY total=$TOTAL errors=$ERRORS spam=$MARKET_SPAM" >> "$LOG_DIR/log_volume.log"

# 이상 판단
ALERT=""
if [ "$TOTAL" -eq 0 ]; then
    ALERT="로그 0줄 — 봇 미작동 의심"
elif [ "$TOTAL" -gt 50000 ]; then
    ALERT="로그 ${TOTAL}줄 — 비정상 과다 (스팸 의심)"
elif [ "$ERRORS" -gt 100 ]; then
    ALERT="오류 로그 ${ERRORS}줄 — 반복 오류 의심"
fi

# 텔레그램 보고 (항상 발송 — 일일 요약)
cd /home/ubuntu/BitCoin_Trade
source .venv/bin/activate

if [ -n "$ALERT" ]; then
    PYTHONUTF8=1 python3 -c "
import asyncio
from services.alerting.notifier import send
msg = '''📊 *일일 로그 볼륨 보고* ($YESTERDAY)

총 로그: ${TOTAL}줄
오류 로그: ${ERRORS}줄
마켓없음 스팸: ${MARKET_SPAM}줄

🔴 이상 감지: $ALERT'''
asyncio.run(send(msg))
"
else
    PYTHONUTF8=1 python3 -c "
import asyncio
from services.alerting.notifier import send
msg = '''📊 *일일 로그 볼륨 보고* ($YESTERDAY)

총 로그: ${TOTAL}줄
오류 로그: ${ERRORS}줄
마켓없음 스팸: ${MARKET_SPAM}줄

✅ 정상 범위'''
asyncio.run(send(msg))
"
fi
