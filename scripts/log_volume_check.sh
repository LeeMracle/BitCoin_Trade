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

# 이상 판단 (P7-08 기준: 0줄 또는 5000줄+ 경보)
ALERT=""
if [ "$TOTAL" -eq 0 ]; then
    ALERT="로그 0줄 — 봇 미작동 의심"
elif [ "$TOTAL" -gt 5000 ]; then
    ALERT="로그 ${TOTAL}줄 — 비정상 과다 (스팸 의심, 임계=5000)"
elif [ "$ERRORS" -gt 100 ]; then
    ALERT="오류 로그 ${ERRORS}줄 — 반복 오류 의심"
fi

# P1 (plan 20260502): 정상 케이스 발송 제거 → 18:00 KST daily_report 헬스체크에 흡수.
# 이상 감지 시만 즉시 텔레그램 발송 (필요 시 critical 알람으로도 잡힘).
cd /home/ubuntu/BitCoin_Trade
source .venv/bin/activate

if [ -n "$ALERT" ]; then
    # plan 20260503_4 P4-1: send_critical 등급 마이그레이션 (parse_mode=None 안전)
    PYTHONUTF8=1 python3 -c "
import asyncio
from services.alerting.notifier import send_critical
msg = '''일일 로그 볼륨 이상 ($YESTERDAY)

총 로그: ${TOTAL}줄
오류 로그: ${ERRORS}줄
마켓없음 스팸: ${MARKET_SPAM}줄

이상 감지: $ALERT'''
asyncio.run(send_critical(msg, parse_mode=None))
"
else
    echo "$(date): 정상 범위 — 텔레그램 발송 생략 (18:00 daily_report에 흡수)" >> "$LOG_DIR/log_volume.log"
fi
