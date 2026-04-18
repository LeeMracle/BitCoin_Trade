#!/bin/bash
# 실시간 모니터를 systemd 서비스로 등록
# 서버 재부팅 시 자동 시작됨
set -e

SERVICE_FILE="/etc/systemd/system/btc-trader.service"
SOURCE_FILE="/home/ubuntu/BitCoin_Trade/config/btc-trader.service"

# 단일 진실 원천: config/btc-trader.service (WatchdogSec=300, Type=notify 포함)
if [ -f "$SOURCE_FILE" ]; then
    sudo cp "$SOURCE_FILE" "$SERVICE_FILE"
    echo "서비스 파일 복사: $SOURCE_FILE → $SERVICE_FILE"
else
    echo "ERROR: $SOURCE_FILE 없음. 저장소 최신화 후 재시도하세요." >&2
    exit 1
fi

sudo systemctl daemon-reload
sudo systemctl enable btc-trader
sudo systemctl start btc-trader

echo "=== 서비스 등록 완료 ==="
echo "상태: sudo systemctl status btc-trader"
echo "로그: sudo journalctl -u btc-trader -f"
echo "중지: sudo systemctl stop btc-trader"
echo "재시작: sudo systemctl restart btc-trader"
