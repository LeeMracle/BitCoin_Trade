#!/bin/bash
# install_timers.sh — BATA 스케줄 작업을 crontab → systemd timer로 이전/설치
#
# 배경 (lessons #36-08, #44):
#   같은 서버에서 BitCoin_Trade / Stock_Trade / Blog_Income 이 crontab을 공유한다.
#   Stock_Trade/scripts/deploy_aws.sh 가 `crontab config/crontab.txt` 로 crontab을
#   **통째 교체**하므로, 그 배포가 일어날 때마다 BATA cron 9개가 전면 소실됐다
#   (2026-08-01 최초 기록 → 08-03 재발 → 19일간 무알람).
#
#   감시로 버티는 대신 **구조적으로 분리**한다. systemd timer는 crontab과 무관하므로
#   타 프로젝트가 crontab을 어떻게 다루든 영향을 받지 않는다.
#
# 설계 메모:
#   - 서버 TZ = Etc/UTC 이므로 기존 cron 스케줄을 그대로 OnCalendar에 옮긴다 (KST = UTC+9)
#   - 로그는 기존 /var/log/*.log 경로를 유지 (기존 도구·습관 호환)
#   - Persistent: 알림성 작업은 false(놓친 알림 몰아 발송 방지),
#                 데이터/정비 작업은 true(누락 보정이 유의미)
#   - 멱등: 반복 실행해도 안전. 내용이 같으면 재작성만 하고 결과는 동일
#
# 사용:
#   bash scripts/install_timers.sh            # 미리보기
#   bash scripts/install_timers.sh --apply    # 실제 설치
set -euo pipefail

PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
PY="$PROJECT_DIR/.venv/bin/python"
UNIT_DIR="/etc/systemd/system"
PREFIX="bata"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

# ── 작업 정의 ────────────────────────────────────────────────────────
# 형식: name | OnCalendar | Persistent | 설명 | 실행 커맨드(로그 리다이렉트 포함)
# OnCalendar는 UTC. 주석의 KST는 +9 환산값.
JOBS=(
"watchdog|minutely|false|봇 프로세스 워치독 (매분)|$PROJECT_DIR/scripts/watchdog_check.sh"
"critical-healthcheck|*-*-* *:05:00|false|critical 헬스체크 (매시 05분)|$PY scripts/critical_healthcheck.py >> /var/log/critical_healthcheck.log 2>&1"
"log-volume|*-*-* 00:10:00|true|로그 폭주 감시 (09:10 KST)|$PROJECT_DIR/scripts/log_volume_check.sh"
"vb-recheck|*-*-* 00:15:00|false|VB 재검증 트리거 (09:15 KST)|$PY scripts/vb_recheck_trigger.py --notify >> /var/log/vb_recheck_trigger.log 2>&1"
"regime-check|*-*-* 00:30:00|false|레짐 전환 알림 (09:30 KST)|$PY scripts/regime_check.py --notify >> /var/log/regime_check.log 2>&1"
"daily-briefing|*-*-* 00:32:00|false|아침 브리핑 (09:32 KST)|$PY scripts/daily_check.py --notify --skip-console >> /var/log/btc_report.log 2>&1"
"daily-report|*-*-* 09:00:00|false|일일 리포트 (18:00 KST)|$PY scripts/daily_report.py >> /var/log/btc_report.log 2>&1"
"ml-outcome|*-*-* 18:00:00|true|ML 결과 대조 (03:00 KST)|$PY scripts/ml_outcome_match.py --days 3 >> /var/log/ml_outcome.log 2>&1"
"ml-weekly|Sun *-*-* 19:00:00|true|ML 주간 리뷰 (일 04:00 KST)|$PY scripts/ml_weekly_review.py >> /var/log/ml_weekly_review.log 2>&1"
)

BASELINE=${#JOBS[@]}
echo "BATA systemd timer 설치 — 대상 $BASELINE개"
echo ""

# ── 사전 확인: 실행 대상 존재 ──────────────────────────────────────
MISSING=0
for s in watchdog_check.sh log_volume_check.sh vb_recheck_trigger.py regime_check.py \
         daily_check.py daily_report.py critical_healthcheck.py \
         ml_outcome_match.py ml_weekly_review.py; do
    [ -f "$PROJECT_DIR/scripts/$s" ] || { echo "  [오류] scripts/$s 없음"; MISSING=1; }
done
[ ! -x "$PY" ] && { echo "  [오류] venv python 없음: $PY"; MISSING=1; }
[ "$MISSING" -eq 1 ] && { echo "사전 확인 실패 — 중단"; exit 1; }

if [ "$APPLY" -eq 0 ]; then
    printf "%-24s %-22s %-11s %s\n" "TIMER" "OnCalendar(UTC)" "Persistent" "설명"
    for j in "${JOBS[@]}"; do
        IFS='|' read -r name sched persist desc _cmd <<< "$j"
        printf "%-24s %-22s %-11s %s\n" "$PREFIX-$name.timer" "$sched" "$persist" "$desc"
    done
    echo ""
    echo "[DRY-RUN] 실제 설치: bash scripts/install_timers.sh --apply"
    exit 0
fi

# ── unit 생성 ────────────────────────────────────────────────────────
for j in "${JOBS[@]}"; do
    IFS='|' read -r name sched persist desc cmd <<< "$j"
    svc="$PREFIX-$name.service"
    tmr="$PREFIX-$name.timer"

    sudo tee "$UNIT_DIR/$svc" > /dev/null <<EOF
# 자동 생성: scripts/install_timers.sh (직접 편집 금지 — 스크립트를 고칠 것)
# ref: docs/lessons/20260822_5_cron_wipe_detector_in_cron.md
[Unit]
Description=BATA - $desc
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$PROJECT_DIR
ExecStart=/bin/bash -c '$cmd'
# 스케줄 작업이 무한정 붙잡히지 않도록 상한 (봇 본체와 무관)
TimeoutStartSec=600
EOF

    sudo tee "$UNIT_DIR/$tmr" > /dev/null <<EOF
# 자동 생성: scripts/install_timers.sh (직접 편집 금지 — 스크립트를 고칠 것)
[Unit]
Description=BATA timer - $desc

[Timer]
OnCalendar=$sched
Persistent=$persist
AccuracySec=30s
Unit=$svc

[Install]
WantedBy=timers.target
EOF
    echo "  생성: $tmr"
done

sudo systemctl daemon-reload

for j in "${JOBS[@]}"; do
    IFS='|' read -r name _s _p _d _c <<< "$j"
    sudo systemctl enable --now "$PREFIX-$name.timer" > /dev/null 2>&1
done

# ── 사후 실측 검증 (lessons #36-08: 설치 성공 = 실측 확인까지) ──────
echo ""
ENABLED=$(systemctl list-unit-files --type=timer --no-legend 2>/dev/null | grep -c "^$PREFIX-.*enabled" || true)
ACTIVE=$(systemctl list-timers --all --no-legend 2>/dev/null | grep -c "$PREFIX-" || true)
echo "설치 결과 — enabled $ENABLED개 / active $ACTIVE개 (baseline $BASELINE)"

if [ "$ENABLED" -lt "$BASELINE" ]; then
    echo "[FAIL] enabled $ENABLED < baseline $BASELINE"
    exit 1
fi
if [ "$ACTIVE" -lt "$BASELINE" ]; then
    echo "[FAIL] active $ACTIVE < baseline $BASELINE"
    exit 1
fi
echo "[OK] timer $BASELINE개 설치 완료."
echo ""
echo "다음 실행 예정:"
systemctl list-timers --no-legend "$PREFIX-*" | awk '{print "  ", $1, $2, $3, $4, "->", $NF}'
