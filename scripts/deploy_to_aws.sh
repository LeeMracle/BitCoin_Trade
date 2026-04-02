#!/bin/bash
# AWS 서버에 프로젝트 배포 스크립트
# 로컬에서 실행: bash scripts/deploy_to_aws.sh
#
# 사전 조건:
#   - upbit-trading-key-seoul.pem 파일 경로 설정
#   - AWS 보안그룹에서 SSH 허용

set -e

# ── 설정 ──────────────────────────────────────────────
AWS_HOST="13.124.82.122"
AWS_USER="ubuntu"
PEM_KEY="$HOME/upbit-trading-key-seoul.pem"  # PEM 파일 경로 수정 필요
PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# PEM 파일 확인
if [ ! -f "$PEM_KEY" ]; then
    echo "PEM 파일을 찾을 수 없습니다: $PEM_KEY"
    echo "PEM_KEY 변수를 올바른 경로로 수정하세요."
    exit 1
fi

SSH_CMD="ssh -i $PEM_KEY -o StrictHostKeyChecking=no $AWS_USER@$AWS_HOST"
SCP_CMD="scp -i $PEM_KEY -o StrictHostKeyChecking=no"

echo "=== AWS 서버 배포 시작 ==="
echo "  서버: $AWS_USER@$AWS_HOST"
echo "  로컬: $LOCAL_DIR"
echo "  원격: $PROJECT_DIR"

# 0. 배포 전 검증 (시행착오 기반 자동 체크)
echo ""
echo "[0/5] 배포 전 검증..."
PYTHONUTF8=1 python "$LOCAL_DIR/scripts/pre_deploy_check.py"
if [ $? -ne 0 ]; then
    echo "배포 전 검증 실패. 배포를 중단합니다."
    exit 1
fi

# 1. 서버에 프로젝트 디렉토리 생성
echo ""
echo "[1/5] 원격 디렉토리 생성..."
$SSH_CMD "mkdir -p $PROJECT_DIR"

# 2. 필요한 파일 동기화 (node_modules, .venv, .git 제외)
echo "[2/5] 파일 동기화..."
rsync -avz --progress \
    -e "ssh -i $PEM_KEY -o StrictHostKeyChecking=no" \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='.venv' \
    --exclude='dist' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='data/cache.duckdb' \
    --exclude='workspace/runs/20*' \
    "$LOCAL_DIR/" "$AWS_USER@$AWS_HOST:$PROJECT_DIR/"

# 3. Python 환경 설정
echo "[3/5] Python 환경 설정..."
$SSH_CMD << 'REMOTE_SCRIPT'
cd /home/ubuntu/BitCoin_Trade

# Python 3.11+ 확인 또는 설치
if ! python3 --version 2>/dev/null | grep -qE "3\.(1[1-9]|[2-9])"; then
    echo "Python 3.11+ 설치 중..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip python3-venv
fi

# venv 생성 및 패키지 설치
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q -e "./services[dev]"

echo "Python 환경 준비 완료: $(.venv/bin/python --version)"
REMOTE_SCRIPT

# 4. 테스트 실행 (dry-run)
echo "[4/5] Dry-run 테스트..."
$SSH_CMD "cd $PROJECT_DIR && .venv/bin/python -m services.execution.trader --dry-run"

# 5. crontab 등록
echo "[5/5] crontab 등록..."
$SSH_CMD << 'CRON_SCRIPT'
PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
CRON_LIVE="5 0 * * * cd $PROJECT_DIR && $PROJECT_DIR/.venv/bin/python scripts/daily_live.py >> /var/log/btc_trader.log 2>&1"
CRON_REPORT="10 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 $PROJECT_DIR/.venv/bin/python scripts/daily_report.py >> /var/log/btc_report.log 2>&1"

# 기존 등록 제거 후 추가
(crontab -l 2>/dev/null | grep -v "daily_live.py" | grep -v "daily_report.py"; echo "$CRON_LIVE"; echo "$CRON_REPORT") | crontab -
echo "crontab 등록 완료:"
crontab -l | grep -E "btc_(trader|report)"
CRON_SCRIPT

echo ""
echo "=== 배포 완료! ==="
echo "  매일 KST 09:05 자동매매 실행"
echo "  매일 KST 09:10 일일 보고 발송"
echo "  로그: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/btc_trader.log'"
echo "  보고: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/btc_report.log'"
echo "  상태: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'cd $PROJECT_DIR && .venv/bin/python -m services.execution.trader --status'"
