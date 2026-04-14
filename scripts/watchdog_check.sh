#!/bin/bash
# BATA Heartbeat Watchdog
# cron으로 매 1분 실행, heartbeat 10분 미갱신 시 텔레그램 경보 + 서비스 재시작
#
# crontab 등록 예시:
#   * * * * * /home/ubuntu/BitCoin_Trade/scripts/watchdog_check.sh

HEARTBEAT_FILE="/tmp/bata_heartbeat"
MAX_AGE_SEC=600  # 10분
SERVICE="btc-trader"
ALERT_FLAG="/tmp/bata_watchdog_alerted"
LOG_DIR="/home/ubuntu/BitCoin_Trade/logs"

# logs 디렉토리 보장
mkdir -p "$LOG_DIR"

# heartbeat 파일 없으면 경보
if [ ! -f "$HEARTBEAT_FILE" ]; then
    # 서비스 활성 여부 확인
    if systemctl is-active --quiet "$SERVICE"; then
        # 서비스 활성인데 heartbeat 없음 → 봇이 아직 초기화 중일 수 있음
        UPTIME=$(systemctl show "$SERVICE" --property=ActiveEnterTimestamp --value)
        UPTIME_SEC=$(( $(date +%s) - $(date -d "$UPTIME" +%s) ))
        if [ "$UPTIME_SEC" -lt "$MAX_AGE_SEC" ]; then
            exit 0  # 시작 직후, 아직 정상
        fi
    else
        exit 0  # 서비스 비활성 → watchdog 할 일 없음
    fi
    AGE=$MAX_AGE_SEC  # heartbeat 없으면 max 취급
else
    AGE=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT_FILE") ))
fi

if [ "$AGE" -ge "$MAX_AGE_SEC" ]; then
    # 중복 경보 방지: 30분 내 이미 경보 발송했으면 건너뜀
    if [ -f "$ALERT_FLAG" ]; then
        FLAG_AGE=$(( $(date +%s) - $(stat -c %Y "$ALERT_FLAG") ))
        if [ "$FLAG_AGE" -lt 1800 ]; then
            exit 0
        fi
    fi

    # 텔레그램 경보
    cd /home/ubuntu/BitCoin_Trade
    source .venv/bin/activate
    PYTHONUTF8=1 python3 -c "
import asyncio
from services.alerting.notifier import send
asyncio.run(send('🚨 *Heartbeat Watchdog 경보*\nBata 봇 heartbeat ${AGE}초 미갱신\n자동 재시작 시도...'))
"

    # 서비스 재시작
    sudo systemctl restart "$SERVICE"

    # 경보 플래그 갱신
    touch "$ALERT_FLAG"

    echo "$(date): Watchdog triggered - age=${AGE}s, service restarted" >> "$LOG_DIR/watchdog.log"
fi
