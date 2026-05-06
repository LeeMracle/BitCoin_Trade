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
LOG_FILES=(/var/log/btc_trader.log /var/log/btc_report.log /var/log/watchdog_check.log /var/log/log_volume.log /var/log/jarvis_executor.log /var/log/vb_recheck_trigger.log /var/log/regime_check.log /var/log/critical_healthcheck.log /var/log/hourly_digest.log /var/log/ml_outcome.log)
sudo touch "${LOG_FILES[@]}"
sudo chown ubuntu:ubuntu "${LOG_FILES[@]}"

CRON_LIVE="5 0 * * * cd $PROJECT_DIR && $PROJECT_DIR/.venv/bin/python scripts/daily_live.py >> /var/log/btc_trader.log 2>&1"
# plan 20260502: 09:10 KST CRON_REPORT 제거 — 18:00 KST 마감 종합 단일화
# 18:00 KST (= 09:00 UTC) 일일 마감 종합 보고 (헬스체크 9개 항목 포함)
CRON_REPORT_18="0 9 * * * cd $PROJECT_DIR && PYTHONUTF8=1 $PROJECT_DIR/.venv/bin/python scripts/daily_report.py >> /var/log/btc_report.log 2>&1"
# P7-04: 매 1분 watchdog 체크 (heartbeat 10분 미갱신 시 경보 + systemctl restart)
CRON_WATCHDOG="* * * * * /home/ubuntu/BitCoin_Trade/scripts/watchdog_check.sh"
# P7-08: 매일 00:10 UTC (09:10 KST) 로그 볼륨 감시 (정상 시 침묵, 이상 시만 즉시 발송 — plan 20260502)
CRON_LOGVOL="10 0 * * * /home/ubuntu/BitCoin_Trade/scripts/log_volume_check.sh"
# P4-14c: jarvis_executor — 2026-05-05 STOP (BTC 분할매도 전략 5-4 사용자 수동 매도 완료, 비활성)
# 활성화 시 아래 라인의 # 제거 + echo 라인의 # 제거로 복귀 (jarvis_strategies.json 활성 전략 등록 후)
# CRON_JARVIS="0 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 $PROJECT_DIR/.venv/bin/python scripts/jarvis_executor.py >> /var/log/jarvis_executor.log 2>&1"
# VB 재검증 트리거: 매일 09:15 KST (= UTC 00:15) — BTC EMA200 7일 연속 충족 시 재집계 보고서 생성
CRON_VB_RECHECK="15 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 $PROJECT_DIR/.venv/bin/python scripts/vb_recheck_trigger.py --notify >> /var/log/vb_recheck_trigger.log 2>&1"
# P5-04: 일 1회 KST 09:30 레짐 자동 판정 (2026-05-05 매시→일1회 축소, 알림 제거)
# 사유: BTC EMA200은 일봉 지표 — 시간단위 갱신 불필요. 자원 24배 절감.
# regime_state.json은 healthcheck/hourly_digest에서 참조하므로 cron 자체는 유지.
# healthcheck 임계도 2h → 26h 동시 조정 (services/healthcheck/runner.py)
CRON_REGIME="30 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/regime_check.py >> /var/log/regime_check.log 2>&1"
# plan 20260502 P0: 매시 5분 critical 헬스체크 (인증·jarvis cron) — FAIL 시만 즉시 알람, 30분 디바운스
# 배경: 2026-05-01 23:00 KST 인증실패 8h 무감지 사고 재발 방지 (lessons #20)
CRON_CRITICAL="5 * * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/critical_healthcheck.py >> /var/log/critical_healthcheck.log 2>&1"
# plan 20260503_4 P4-2: 매시 30분 hourly_digest — 2026-05-04 사용자 요청으로 비활성화 (cron 등록 안 함)
# CRON_DIGEST="30 * * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/hourly_digest.py >> /var/log/hourly_digest.log 2>&1"
# plan 20260504_3 P1: ML outcome 매칭 — 매일 KST 03:00 (UTC 18:00) 어제 결정의 24h 도달 여부 기록
CRON_ML_OUTCOME="0 18 * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/ml_outcome_match.py --days 3 >> /var/log/ml_outcome.log 2>&1"

# 기존 등록 제거 후 추가
(crontab -l 2>/dev/null \
    | grep -v "daily_live.py" \
    | grep -v "daily_report.py" \
    | grep -v "watchdog_check.sh" \
    | grep -v "log_volume_check.sh" \
    | grep -v "jarvis_executor.py" \
    | grep -v "vb_recheck_trigger.py" \
    | grep -v "regime_check.py" \
    | grep -v "critical_healthcheck.py" \
    | grep -v "hourly_digest.py" \
    | grep -v "ml_outcome_match.py"; \
    echo "$CRON_LIVE"; \
    echo "$CRON_REPORT_18"; \
    echo "$CRON_WATCHDOG"; \
    echo "$CRON_LOGVOL"; \
    : "echo $CRON_JARVIS (STOP — 2026-05-05, 활성화 시 :까지 제거)"; \
    echo "$CRON_VB_RECHECK"; \
    echo "$CRON_REGIME"; \
    echo "$CRON_CRITICAL"; \
    echo "$CRON_ML_OUTCOME") | crontab -

# watchdog/log_volume 스크립트 실행권한 부여
chmod +x "$PROJECT_DIR/scripts/watchdog_check.sh" "$PROJECT_DIR/scripts/log_volume_check.sh" 2>/dev/null

echo "crontab 등록 완료:"
crontab -l | grep -E "(btc_(trader|report)|watchdog_check|log_volume_check|jarvis_executor|vb_recheck_trigger|regime_check|critical_healthcheck)"
CRON_SCRIPT

echo ""
echo "=== 배포 완료! ==="
echo "  매일 KST 09:05 자동매매 실행"
echo "  매일 KST 18:00 일일 마감 종합 보고 (헬스체크 9개 항목 포함)"
echo "  매시 KST :05  critical 헬스체크 (인증·jarvis cron, 실패 시만 알람)"
echo "  로그: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/btc_trader.log'"
echo "  보고: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/btc_report.log'"
echo "  critical: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'tail -f /var/log/critical_healthcheck.log'"
echo "  상태: ssh -i $PEM_KEY $AWS_USER@$AWS_HOST 'cd $PROJECT_DIR && .venv/bin/python -m services.execution.trader --status'"
