#!/bin/bash
# restore_cron.sh — BitCoin_Trade cron 복구 (lessons #36-08 재발 대응)
#
# 배경:
#   같은 서버에 BitCoin_Trade / Stock_Trade / Blog_Income 3개 프로젝트가
#   crontab을 공유한다. Stock_Trade/scripts/deploy_aws.sh 는
#       crontab config/crontab.txt
#   즉 **파일 원자 교체** 방식으로 crontab을 통째 덮어쓰므로,
#   그 배포가 일어날 때마다 BitCoin_Trade cron 9개가 전면 소실된다.
#   (2026-08-01 lessons #36-08 최초 기록 → 2026-08-03 재발, 19일간 무알람)
#
# 이 스크립트는 deploy_to_aws.sh 의 등록 로직과 **동일한 방식**을 쓴다:
#   기존 crontab을 읽어 BATA 항목만 제거 후 재등록 → 타 프로젝트 항목 보존.
#   (Stock_Trade 처럼 파일로 통째 교체하지 않는다 — 그게 사고의 원인이므로)
#
# 사용:
#   서버에서:  bash scripts/restore_cron.sh          # 미리보기
#              bash scripts/restore_cron.sh --apply  # 실제 등록
set -e

PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
BASELINE=9
APPLY=0
[ "$1" = "--apply" ] && APPLY=1

# ── BATA cron 정의 (deploy_to_aws.sh 와 동기화 필수) ──────────────
# UTC 기준. KST = UTC + 9
CRON_REPORT_18="0 9 * * * cd $PROJECT_DIR && PYTHONUTF8=1 $PROJECT_DIR/.venv/bin/python scripts/daily_report.py >> /var/log/btc_report.log 2>&1"
CRON_WATCHDOG="* * * * * $PROJECT_DIR/scripts/watchdog_check.sh"
CRON_LOGVOL="10 0 * * * $PROJECT_DIR/scripts/log_volume_check.sh"
CRON_VB_RECHECK="15 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 $PROJECT_DIR/.venv/bin/python scripts/vb_recheck_trigger.py --notify >> /var/log/vb_recheck_trigger.log 2>&1"
CRON_REGIME="30 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/regime_check.py --notify >> /var/log/regime_check.log 2>&1"
CRON_DAILY_BRIEFING="32 0 * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/daily_check.py --notify --skip-console >> /var/log/btc_report.log 2>&1"
CRON_CRITICAL="5 * * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/critical_healthcheck.py >> /var/log/critical_healthcheck.log 2>&1"
CRON_ML_OUTCOME="0 18 * * * cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/ml_outcome_match.py --days 3 >> /var/log/ml_outcome.log 2>&1"
CRON_ML_WEEKLY="0 19 * * 0 cd $PROJECT_DIR && PYTHONUTF8=1 PYTHONPATH=$PROJECT_DIR $PROJECT_DIR/.venv/bin/python scripts/ml_weekly_review.py >> /var/log/ml_weekly_review.log 2>&1"

BEFORE=$(crontab -l 2>/dev/null | grep -c "BitCoin_Trade" || true)
OTHERS=$(crontab -l 2>/dev/null | grep -vc "BitCoin_Trade" || true)
echo "현재 crontab — BitCoin_Trade $BEFORE 줄 / 기타(타 프로젝트 포함) $OTHERS 줄"

# 실행 대상 스크립트 존재 확인 — 없는 스크립트를 cron에 등록하면 조용히 실패한다
MISSING=0
for s in daily_report.py watchdog_check.sh log_volume_check.sh vb_recheck_trigger.py \
         regime_check.py daily_check.py critical_healthcheck.py \
         ml_outcome_match.py ml_weekly_review.py; do
    if [ ! -f "$PROJECT_DIR/scripts/$s" ]; then
        echo "  [오류] scripts/$s 없음 — 등록해도 실행 실패"
        MISSING=1
    fi
done
[ "$MISSING" -eq 1 ] && { echo "대상 스크립트 누락 — 중단"; exit 1; }

if [ "$APPLY" -eq 0 ]; then
    echo ""
    echo "[DRY-RUN] 아래 $BASELINE 줄이 등록될 예정 (타 프로젝트 항목은 그대로 보존):"
    for v in "$CRON_REPORT_18" "$CRON_WATCHDOG" "$CRON_LOGVOL" "$CRON_VB_RECHECK" \
             "$CRON_REGIME" "$CRON_DAILY_BRIEFING" "$CRON_CRITICAL" \
             "$CRON_ML_OUTCOME" "$CRON_ML_WEEKLY"; do
        echo "  ${v:0:110}"
    done
    echo ""
    echo "실제 등록: bash scripts/restore_cron.sh --apply"
    exit 0
fi

# 백업 (타임스탬프)
BACKUP="/home/ubuntu/crontab.bak_restore_$(date -u +%Y%m%d_%H%M%S)"
crontab -l > "$BACKUP" 2>/dev/null || true
echo "백업: $BACKUP"

# ── 등록: 기존 읽기 → BATA 항목만 제거 → 재추가 (타 프로젝트 보존) ──
(crontab -l 2>/dev/null \
    | grep -v "daily_live.py" \
    | grep -v "daily_report.py" \
    | grep -v "watchdog_check.sh" \
    | grep -v "log_volume_check.sh" \
    | grep -v "jarvis_executor.py" \
    | grep -v "vb_recheck_trigger.py" \
    | grep -v "regime_check.py" \
    | grep -v "daily_check.py" \
    | grep -v "critical_healthcheck.py" \
    | grep -v "hourly_digest.py" \
    | grep -v "ml_outcome_match.py" \
    | grep -v "ml_weekly_review.py"; \
    echo "$CRON_REPORT_18"; \
    echo "$CRON_WATCHDOG"; \
    echo "$CRON_LOGVOL"; \
    echo "$CRON_VB_RECHECK"; \
    echo "$CRON_REGIME"; \
    echo "$CRON_DAILY_BRIEFING"; \
    echo "$CRON_CRITICAL"; \
    echo "$CRON_ML_OUTCOME"; \
    echo "$CRON_ML_WEEKLY") | crontab -

chmod +x "$PROJECT_DIR/scripts/watchdog_check.sh" "$PROJECT_DIR/scripts/log_volume_check.sh" 2>/dev/null || true

# ── 사후 실측 검증 (lessons #36-08: 등록 성공 = 실측 확인까지) ──
AFTER=$(crontab -l 2>/dev/null | grep -c "BitCoin_Trade" || true)
OTHERS_AFTER=$(crontab -l 2>/dev/null | grep -vc "BitCoin_Trade" || true)
echo ""
echo "등록 후 — BitCoin_Trade $AFTER 줄 / 기타 $OTHERS_AFTER 줄 (등록 전 기타 $OTHERS 줄)"

if [ "$AFTER" -lt "$BASELINE" ]; then
    echo "[FAIL] BitCoin_Trade $AFTER 줄 < baseline $BASELINE — 복구 실패"
    exit 1
fi
if [ "$OTHERS_AFTER" -lt "$OTHERS" ]; then
    echo "[FAIL] 타 프로젝트 cron이 줄었다 ($OTHERS → $OTHERS_AFTER) — 복구 중단, 백업 확인 필요"
    exit 1
fi
echo "[OK] 복구 완료."
