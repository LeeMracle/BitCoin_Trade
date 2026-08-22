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
PEM_KEY="$HOME/Downloads/upbit-trading-key-seoul.pem"  # PEM 파일 경로
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
# lessons/20260419_1 — 로컬에 rsync 미설치(Git Bash 기본환경) 시 배포 중단 방지를 위해
# tar | ssh 폴백 경로 제공. 제외 규칙은 양쪽 공통.
EXCLUDES=(
    --exclude=.git
    --exclude=node_modules
    --exclude=.venv
    --exclude=dist
    --exclude=__pycache__
    --exclude='*.egg-info'
    --exclude=data/cache.duckdb
    --exclude=data/features
    --exclude='workspace/runs/20*'
    --exclude='workspace/ml_shadow'
)

if command -v rsync >/dev/null 2>&1; then
    echo "[2/5] 파일 동기화 (rsync)..."
    rsync -avz --progress \
        -e "ssh -i $PEM_KEY -o StrictHostKeyChecking=no" \
        "${EXCLUDES[@]}" \
        "$LOCAL_DIR/" "$AWS_USER@$AWS_HOST:$PROJECT_DIR/"
else
    echo "[2/5] 파일 동기화 (tar|ssh 폴백 — rsync 미설치)..."
    # --exclude는 -C 앞에 둬서 GNU tar의 옵션 순서 규칙을 만족시킨다.
    tar czf - "${EXCLUDES[@]}" -C "$LOCAL_DIR" . \
        | $SSH_CMD "tar xzf - -C $PROJECT_DIR"
fi

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

# 로그 파일 초기화 (lessons/20260418_2 — cron은 /var/log/에 새 파일을 생성할 권한이 없어 redirect가 silent fail)
# 배포 때마다 touch로 보장. 기존 파일이 있으면 내용 유지.
LOG_FILES=(/var/log/btc_trader.log /var/log/btc_report.log /var/log/watchdog_check.log /var/log/log_volume.log /var/log/jarvis_executor.log /var/log/vb_recheck_trigger.log /var/log/regime_check.log /var/log/critical_healthcheck.log /var/log/hourly_digest.log /var/log/ml_outcome.log /var/log/ml_weekly_review.log)
sudo touch "${LOG_FILES[@]}"
sudo chown ubuntu:ubuntu "${LOG_FILES[@]}"

# ── 스케줄 작업 정의는 scripts/install_timers.sh 로 이관 (2026-08-22, lessons #44) ──
# 이전에는 여기서 CRON_* 변수 9개를 정의하고 crontab에 등록했으나,
# 같은 서버의 Stock_Trade deploy_aws.sh 가 `crontab config/crontab.txt` 로 crontab을
# 통째 교체해 BATA cron이 반복 소실됐다 (08-01 기록 → 08-03 재발 → 19일 무알람).
# 스케줄 정의의 단일 진실 원천은 이제 install_timers.sh 의 JOBS 테이블이다.
# (변수만 여기 남겨두면 "고쳤는데 반영 안 됨" 부류의 사문화 설정이 되므로 제거)
#
# 유지 원칙 (lessons #33): daily_live.py --realtime 은 systemd btc-trader.service 단독 가동.
#                          스케줄러에 절대 등록 금지 (좀비 누적).

# ── 스케줄 작업: systemd timer 설치 (lessons #44) ────────────────────
# 2026-08-22: crontab → systemd timer 전면 이전.
# 사유: 같은 서버의 Stock_Trade deploy_aws.sh 가 `crontab config/crontab.txt` 로
#       crontab을 통째 교체해 BATA cron 9개가 반복 소실됨(08-01 기록 → 08-03 재발,
#       19일 무알람). timer는 crontab과 무관하므로 구조적으로 분리된다.
# 위 CRON_* 변수는 이전 이력 참고용으로만 남겨둔다 (등록에 사용하지 않음).
bash "$PROJECT_DIR/scripts/install_timers.sh" --apply

CRON_SCRIPT

echo ""
echo "[사후 실측 검증] 서버 BATA systemd timer 카운트..."
# lessons #36-08 / #44: 배포 성공 = 서버 반영 실측 확인까지.
# 2026-08-22 crontab → systemd timer 이전으로 검증 대상도 timer로 전환.
# 변수명은 CRON_xxx 패턴 회피 (pre_deploy_check.check_cron_var_echo_consistency 오탐 방지)
BTC_REMOTE_TIMERS=$($SSH_CMD "systemctl list-timers --all --no-legend 2>/dev/null | grep -c 'bata-'" || echo "0")
if [ "$BTC_REMOTE_TIMERS" -lt 9 ]; then
    echo "[FAIL] 서버 BATA systemd timer $BTC_REMOTE_TIMERS개 (< 9 baseline) — lessons #44 회귀"
    echo "       복구: ssh 접속 후 bash scripts/install_timers.sh --apply"
    exit 1
fi
echo "[OK] 서버 BATA systemd timer $BTC_REMOTE_TIMERS개 (>= 9 baseline)"

# crontab 잔재 확인 — timer와 중복 실행되면 알림 2회 발송 등 부작용
BTC_LEFTOVER=$($SSH_CMD "crontab -l 2>/dev/null | grep -c BitCoin_Trade" || echo "0")
if [ "$BTC_LEFTOVER" -ne 0 ]; then
    echo "[WARN] crontab에 BitCoin_Trade 잔재 $BTC_LEFTOVER줄 — timer와 중복 실행 위험"
    echo "       정리: crontab -l | grep -v BitCoin_Trade | crontab -"
fi

echo ""
echo "=== 배포 완료! ==="
echo "  매일 KST 09:05 자동매매 실행"
echo "  매일 KST 18:00 일일 마감 종합 보고 (헬스체크 9개 항목 포함)"
echo "  매시 KST :05  critical 헬스체크 (인증·jarvis cron, 실패 시만 알람)"
echo "  로그: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/btc_trader.log'"
echo "  보고: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/btc_report.log'"
echo "  critical: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/critical_healthcheck.log'"
echo "  상태: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'cd $PROJECT_DIR && .venv/bin/python -m services.execution.trader --status'"
