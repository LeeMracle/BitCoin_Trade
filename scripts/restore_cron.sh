#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# ⚠ DEPRECATED — 사문화 (lessons #44, 2026-08-22 timer 이전 완료)
# ═══════════════════════════════════════════════════════════════════
# 이 스크립트는 lessons #36-08 (2026-08-01) 당시 Stock_Trade deploy가
# crontab을 파일 원자 교체로 덮어써 BitCoin_Trade cron 8개가 소실된
# 사고의 긴급 복구 도구로 작성됐다.
#
# 2026-08-22 lessons #44로 BATA 스케줄 작업 9개는 전부
# **systemd timer** (`bata-*`) 로 이전됐다. crontab은 더 이상
# BATA 운영 채널이 아니다.
#
# 이 스크립트를 --apply로 실행하면 timer 9개와 **중복되는 cron 9줄**이
# 등록되어 아침 브리핑·레짐 알림·healthcheck가 **각각 2회씩 발사**된다.
# 2026-11-05 lessons #53 (false alarm) RCA 중 실제로 이 사고가 재현됐고
# (01:43 KST 등록 → 01:45 KST 롤백, 중복 발사 0건이었으나 2분 창
# 안에 트리거 시각이 걸렸다면 텔레그램 중복·레이스 컨디션 위험 상당).
#
# 복구가 필요하면 **install_timers.sh**를 사용하라:
#   ssh ubuntu@13.124.82.122
#   cd /home/ubuntu/BitCoin_Trade
#   bash scripts/install_timers.sh --apply
#
# 역사적 가치 때문에 삭제하지 않고 실행만 거부한다.
# ═══════════════════════════════════════════════════════════════════

set -e

cat <<'MSG' >&2
╔═══════════════════════════════════════════════════════════════════╗
║ ⚠ restore_cron.sh는 DEPRECATED (사문화) — lessons #44 timer 이전 ║
║                                                                   ║
║ BATA 스케줄 작업 9개는 systemd timer (bata-*) 로 운영 중.         ║
║ crontab 기반 복구는 timer와 중복 실행을 유발한다 (lessons #53).   ║
║                                                                   ║
║ 복구가 필요하면 아래를 실행하라:                                  ║
║   ssh ubuntu@13.124.82.122                                        ║
║   cd /home/ubuntu/BitCoin_Trade                                   ║
║   bash scripts/install_timers.sh --apply                          ║
║                                                                   ║
║ 서버에서 timer 상태 확인:                                         ║
║   systemctl list-timers --all | grep bata-                        ║
║   (baseline 9+ 이면 정상)                                         ║
╚═══════════════════════════════════════════════════════════════════╝
MSG

# lessons #53 — 복구 경로는 install_timers.sh 로만. 이 스크립트는 즉시 중단.
exit 1
