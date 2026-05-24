#!/bin/bash
# hotfix_deploy.sh — 우회 배포(scp+restart) 안전 wrapper
#
# 배경:
#   - lessons #31: deploy_to_aws.sh 우회(직접 scp+restart) 시 pre_deploy_check
#     미실행 + cron 갱신 누락 → 5일 silent fail
#   - 정식 배포가 너무 무겁다고 직접 scp 하는 경우라도, 최소 의무 강제
#
# 사용:
#   bash scripts/hotfix_deploy.sh <파일1> [파일2 ...]
#   예: bash scripts/hotfix_deploy.sh services/execution/realtime_monitor.py
#
# 최소 의무 (자동 강제):
#   1. pre_deploy_check.py 실행 (실패 시 abort)
#   2. 변경 파일 scp
#   3. AWS systemctl restart btc-trader
#   4. 30초 대기 후 헬스: 서비스 status + cron 등록 상태 출력
#   5. 24h 내 lessons 보강 의무 경고 출력
#
# 한계:
#   - 인프라(cron/systemd unit) 변경은 처리 안 함 — 그 경우 deploy_to_aws.sh 전체 실행 필수

set -e

# ── 설정 (deploy_to_aws.sh와 동일) ─────────────────────────────────
AWS_HOST="13.124.82.122"
AWS_USER="ubuntu"
PEM_KEY="$HOME/Downloads/upbit-trading-key-seoul.pem"
PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ $# -eq 0 ]; then
    echo "사용: bash scripts/hotfix_deploy.sh <파일1> [파일2 ...]"
    echo ""
    echo "예: bash scripts/hotfix_deploy.sh services/execution/realtime_monitor.py"
    echo ""
    echo "주의: 인프라(cron/systemd) 변경 시에는 deploy_to_aws.sh 전체 실행 필수"
    exit 1
fi

if [ ! -f "$PEM_KEY" ]; then
    echo "PEM 파일 없음: $PEM_KEY"
    exit 1
fi

SSH_CMD="ssh -i $PEM_KEY -o StrictHostKeyChecking=no $AWS_USER@$AWS_HOST"
SCP_CMD="scp -i $PEM_KEY -o StrictHostKeyChecking=no"

echo "=== HOTFIX 배포 시작 ==="
echo "  대상 파일: $@"
echo "  서버: $AWS_USER@$AWS_HOST"

# ─── 단계 1: pre_deploy_check 강제 실행 (G6 핵심) ─────────────────
echo ""
echo "[1/5] 배포 전 검증 (pre_deploy_check.py)..."
PYTHONUTF8=1 python "$LOCAL_DIR/scripts/pre_deploy_check.py"
if [ $? -ne 0 ]; then
    echo ""
    echo "검증 실패 — hotfix 배포 중단"
    echo "오류를 먼저 수정하세요. 우회 배포 금지."
    exit 1
fi

# ─── 단계 2: 변경 파일 scp ─────────────────────────────────────
echo ""
echo "[2/5] 파일 전송..."
for file in "$@"; do
    if [ ! -f "$LOCAL_DIR/$file" ]; then
        echo "  파일 없음: $file"
        exit 1
    fi
    # 원격 경로의 디렉터리 보장
    remote_dir="$PROJECT_DIR/$(dirname "$file")"
    $SSH_CMD "mkdir -p $remote_dir"
    $SCP_CMD "$LOCAL_DIR/$file" "$AWS_USER@$AWS_HOST:$PROJECT_DIR/$file"
    echo "  전송 완료: $file"
done

# ─── 단계 3: 서비스 재시작 ─────────────────────────────────────
echo ""
echo "[3/5] btc-trader 재시작..."
$SSH_CMD "sudo systemctl restart btc-trader"

# ─── 단계 4: 30초 대기 후 헬스 ─────────────────────────────────
echo ""
echo "[4/5] 30초 대기 후 헬스 확인..."
sleep 30

echo ""
echo "  ── 서비스 상태 ──"
$SSH_CMD "sudo systemctl status btc-trader --no-pager | head -15" || true

echo ""
echo "  ── cron 등록 상태 (lessons #31 검증) ──"
REQUIRED_CRONS="daily_live.py daily_report.py watchdog_check.sh critical_healthcheck.py ml_outcome_match.py ml_weekly_review.py"
MISSING_CRONS=""
ACTUAL_CRONTAB=$($SSH_CMD "crontab -l 2>/dev/null" || echo "")
for cron in $REQUIRED_CRONS; do
    if echo "$ACTUAL_CRONTAB" | grep -q "$cron"; then
        echo "    OK: $cron"
    else
        echo "    MISSING: $cron"
        MISSING_CRONS="$MISSING_CRONS $cron"
    fi
done

# ─── 단계 5: 종료 메시지 (24h 의무 경고) ───────────────────────
echo ""
echo "[5/5] 배포 후 의무 사항:"
echo ""
if [ -n "$MISSING_CRONS" ]; then
    echo "  ❗ cron 미등록 감지:$MISSING_CRONS"
    echo "     → deploy_to_aws.sh 전체 실행으로 cron 복원 필요 (lessons #31)"
fi
echo "  1. 24h 내 변경 사유 + 영향 범위를 lessons 또는 daily 보고에 기록"
echo "  2. 회귀 위험 있는 변경이면 docs/lessons/YYYYMMDD_N_*.md 작성"
echo "  3. 회귀 방지 룰이 있으면 pre_deploy_check.py에 추가"
echo "  4. 24시간 내 봇 안정성(좀비/state-balance/PnL) 운영자 모니터"
echo ""
echo "=== HOTFIX 배포 완료 ==="
