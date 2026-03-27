#!/bin/bash
# 실시간 모니터를 systemd 서비스로 등록
# 서버 재부팅 시 자동 시작됨
set -e

SERVICE_FILE="/etc/systemd/system/btc-trader.service"

sudo tee $SERVICE_FILE > /dev/null << 'EOF'
[Unit]
Description=BTC Auto Trader - Realtime Monitor
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/BitCoin_Trade
ExecStart=/home/ubuntu/BitCoin_Trade/.venv/bin/python scripts/daily_live.py --realtime
Restart=always
RestartSec=10
Environment=PYTHONUTF8=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable btc-trader
sudo systemctl start btc-trader

echo "=== 서비스 등록 완료 ==="
echo "상태: sudo systemctl status btc-trader"
echo "로그: sudo journalctl -u btc-trader -f"
echo "중지: sudo systemctl stop btc-trader"
echo "재시작: sudo systemctl restart btc-trader"
