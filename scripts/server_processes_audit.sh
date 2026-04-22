#!/usr/bin/env bash
# 서버 프로세스 감사 — 다중 프로젝트 공존 환경에서 소유권 가시화
#
# 목적:
#   - 교훈 #17(docs/lessons/20260421_1_*) 재발 방지
#   - cto health 보조 — 어떤 systemd 서비스/프로세스가 어느 프로젝트 소유인지
#     /proc/<pid>/cwd 기반으로 분류하여 출력
#
# 사용:
#   bash scripts/server_processes_audit.sh
#   PEM=~/Downloads/upbit-trading-key-seoul.pem HOST=ubuntu@13.124.82.122 \
#     bash scripts/server_processes_audit.sh
#
# 출력 섹션:
#   1) Active systemd services (전체)
#   2) RSS top 10 프로세스 + cwd
#   3) 프로젝트별 소유권 분류 (BitCoin_Trade / Stock_Trade / Blog_Income / 기타)

set -euo pipefail

PEM="${PEM:-$HOME/Downloads/upbit-trading-key-seoul.pem}"
HOST="${HOST:-ubuntu@13.124.82.122}"

if [[ ! -f "$PEM" ]]; then
    echo "ERROR: PEM 파일 없음 — $PEM" >&2
    echo "       PEM=경로 환경변수로 지정하거나 Downloads/upbit-trading-key-seoul.pem 배치" >&2
    exit 2
fi

ssh -i "$PEM" -o StrictHostKeyChecking=no "$HOST" 'bash -s' <<'REMOTE_SCRIPT'
set -u

echo "=========================================="
echo " 서버 프로세스 감사 (교훈 #17)"
echo " 시각: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=========================================="

echo
echo "[1/3] Active systemd services"
echo "------------------------------------------"
systemctl list-units --type=service --state=running --no-pager --no-legend \
    | awk '{print "  " $1}'

echo
echo "[2/3] RSS top 10 프로세스 + 소유 cwd"
echo "------------------------------------------"
printf "  %-8s %-10s %8s  %s\n" "PID" "USER" "RSS_KB" "CWD → CMD"
printf "  %-8s %-10s %8s  %s\n" "---" "----" "------" "---------"
ps -eo pid,user,rss,cmd --sort=-rss --no-headers \
    | head -10 \
    | while read -r pid user rss cmd; do
        cwd=$(sudo readlink "/proc/${pid}/cwd" 2>/dev/null || echo "(접근불가)")
        printf "  %-8s %-10s %8s  %s\n" "$pid" "$user" "$rss" "$cwd"
        printf "  %-8s %-10s %8s    └ %s\n" "" "" "" "${cmd:0:100}"
    done

echo
echo "[3/3] 프로젝트별 소유권 분류 (top 10 기준)"
echo "------------------------------------------"
declare -A project_pids
ps -eo pid,rss --sort=-rss --no-headers | head -10 | while read -r pid rss; do
    cwd=$(sudo readlink "/proc/${pid}/cwd" 2>/dev/null || echo "")
    case "$cwd" in
        /home/ubuntu/BitCoin_Trade*) bucket="BitCoin_Trade" ;;
        /home/ubuntu/Stock_Trade*)   bucket="Stock_Trade" ;;
        /home/ubuntu/Blog_Income*)   bucket="Blog_Income" ;;
        /home/ubuntu)                bucket="ubuntu_home" ;;
        "")                          bucket="(접근불가)" ;;
        *)                           bucket="기타(${cwd})" ;;
    esac
    printf "  PID=%-8s RSS=%8s KB → %s\n" "$pid" "$rss" "$bucket"
done

echo
echo "[참고] 의심 프로세스 발견 시 다음 명령으로 추가 검증:"
echo "  sudo readlink /proc/<PID>/exe        # 실행 바이너리"
echo "  sudo cat /proc/<PID>/cmdline | tr '\\0' ' '   # 전체 명령어"
echo "  grep -rE '<module>' /etc/systemd/system/ /lib/systemd/system/  # systemd 역탐색"
echo "  sudo journalctl _PID=<PID> --no-pager | tail -30   # PID별 로그"
echo "=========================================="
REMOTE_SCRIPT
