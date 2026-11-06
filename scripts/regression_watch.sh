#!/bin/bash
# regression_watch.sh — 24h 회귀 감시 자동 체크포인트 (서버 측 실행)
#
# 배경:
#   - 2026-11-05 커밋 2b6b3ff (ADR 20261105-1 EMA-TREND 폐기 + throttle + ML shadow 위생)
#     + 0dd86ef (lessons #53) 배포 후 24h 회귀 감시
#   - 서버 재시작: 2026-11-05 13:05:02 KST (04:05:02 UTC) MainPID 521961
#   - 베이스라인: output/regression_watch_20261105_baseline.md
#
# 용도: systemd-run 1회성 timer에서 각 CP(체크포인트) 시각에 호출
#   - CP2 (2026-11-06 01:05 KST = 2026-11-05 16:05 UTC)
#   - CP3 (2026-11-06 07:05 KST = 2026-11-05 22:05 UTC)
#   - CP4 (2026-11-06 13:05 KST = 2026-11-06 04:05 UTC, 최종)
#
# 사용:
#   bash scripts/regression_watch.sh <CP라벨>            # 실측 + 텔레그램
#   bash scripts/regression_watch.sh <CP라벨> --dry-run  # stdout만
#   bash scripts/regression_watch.sh --cleanup           # transient timer 전부 정리
#
# 7개 체크 (ERROR/WARN 분리):
#   ERROR (즉시 engineer 발화 필요):
#     1. EMA-TREND journal 발사 수 (재시작 이후) == 0
#     2. ema_trend_state.json history 길이 == 198 (고정)
#   WARN (상태 알림):
#     3. systemd btc-trader active + MainPID 생존
#     4. 좀비 수 (daily_live.py 프로세스) ≤ 1
#     5. btc-trader 메모리 < 400 MiB (lessons #5)
#     6. systemd bata-* timer 개수 == 9
#     7. crontab -l | grep -c BitCoin_Trade == 0 (lessons #44)
#
# 종료 코드:
#   0 = OK (전부 PASS), 1 = ERROR 감지, 2 = WARN 감지, 3 = 사용 오류

set -u

# ── 경로/상수 ─────────────────────────────────────────────────
PROJECT_DIR="/home/ubuntu/BitCoin_Trade"
ENV_FILE="${PROJECT_DIR}/services/.env"
BASELINE_RESTART_UTC="2026-11-05 04:05:00"  # 재시작 시각 (UTC)
BASELINE_MAINPID="521961"
BASELINE_HISTORY_LEN="198"
MEM_LIMIT_MIB="400"
TIMER_UNITS=("bata-regression-cp2" "bata-regression-cp3" "bata-regression-cp4")

# ── 인자 파싱 ─────────────────────────────────────────────────
CP_LABEL=""
DRY_RUN="0"
CLEANUP="0"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN="1" ;;
        --cleanup) CLEANUP="1" ;;
        CP*|late_baseline) CP_LABEL="$arg" ;;
        *) echo "알 수 없는 인자: $arg" >&2; exit 3 ;;
    esac
done

# ── 텔레그램 발송 (lessons #39 plain fallback + lessons #56 안전 파싱) ─
# lessons #56: 공유 임시 파일(/tmp/tg_resp.txt) 재사용 시 소유자 충돌로
#   `curl -o` 가 exit 23 반환 → `$()||echo "000"` 폴백이 작동해
#   status="200000" 조합 → 재시도 폭주 (API는 실제로 2026-11-06 CP2/3/4 성공).
# 수정 3축:
#   (1) 세션 유니크 임시 파일(`mktemp`) + trap cleanup
#   (2) curl exit 와 http_code 분리 추출 (`-w "\nHTTP_STATUS:%{http_code}"` + grep)
#   (3) API body `"ok":true` 를 1차 판정 기준으로 (curl exit 무관하게 성공 판정 가능)
send_telegram() {
    local text="$1"
    if [ ! -f "$ENV_FILE" ]; then
        echo "[telegram] .env 없음 → 발송 스킵: $ENV_FILE" >&2
        return 1
    fi
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
    local token="${TELEGRAM_BOT_TOKEN:-}"
    local chat="${TELEGRAM_CHAT_ID:-}"
    if [ -z "$token" ] || [ -z "$chat" ]; then
        echo "[telegram] 토큰/챗ID 미설정 → 발송 스킵" >&2
        return 1
    fi
    local url="https://api.telegram.org/bot${token}/sendMessage"

    # 세션 유니크 임시 파일 (lessons #56)
    local tmp_resp
    tmp_resp=$(mktemp -t "tg_resp.XXXXXX") || tmp_resp="/tmp/tg_resp.$$"
    # shellcheck disable=SC2064
    trap "rm -f '${tmp_resp}'" RETURN

    # 안전 파싱 헬퍼: curl 결과를 (http_code, body, verdict) 로 분리
    # verdict: 0=ok, 1=api_fail, 2=network_fail
    _send_once() {
        local parse_mode="$1"
        local payload
        if [ -n "$parse_mode" ]; then
            payload=$(python3 -c '
import json, sys
print(json.dumps({"chat_id": sys.argv[1], "text": sys.argv[2], "parse_mode": sys.argv[3]}))
' "$chat" "$text" "$parse_mode")
        else
            payload=$(python3 -c '
import json, sys
print(json.dumps({"chat_id": sys.argv[1], "text": sys.argv[2]}))
' "$chat" "$text")
        fi

        # curl exit 와 http_code 를 섞지 않음:
        #   - `-w` 는 stdout 에, body 는 `-o` 파일에
        #   - `|| true` 로 exit 23 등 write 실패 무시 (어차피 API 성공 판단은 body 로)
        local http_code
        http_code=$(curl -sS -o "$tmp_resp" -w "%{http_code}" \
            --max-time 10 \
            -X POST "$url" \
            -H "Content-Type: application/json" \
            --data-raw "$payload" 2>/dev/null || true)

        # http_code 가 공백·비숫자면 "000" 로 정규화 (exit 23 시 curl 은 http_code 이미 출력)
        if ! [[ "$http_code" =~ ^[0-9]{3}$ ]]; then
            http_code="000"
        fi

        # 1차 판정: body 의 `"ok":true` (API 응답은 curl exit 와 무관)
        local body=""
        if [ -s "$tmp_resp" ]; then
            body=$(head -c 1000 "$tmp_resp")
        fi
        if echo "$body" | grep -q '"ok":true'; then
            echo "$http_code|OK|$body"
            return 0
        fi
        # 2차 판정: http_code 200 이지만 body 못 읽음 (파일 쓰기 실패 등) → 성공 추정
        if [ "$http_code" = "200" ]; then
            echo "$http_code|OK_NO_BODY|$body"
            return 0
        fi
        echo "$http_code|FAIL|$body"
        return 1
    }

    # 1차: Markdown
    local result
    if result=$(_send_once "Markdown"); then
        local code="${result%%|*}"
        local tail="${result#*|}"
        local verdict="${tail%%|*}"
        echo "[telegram] OK (Markdown, status=${code}, verdict=${verdict})"
        return 0
    fi
    local fail_code="${result%%|*}"
    local fail_body="${result##*|}"
    echo "[telegram] Markdown 실패 status=${fail_code} body=$(echo "$fail_body" | head -c 200) → plain 재시도"

    # 2차: plain
    if result=$(_send_once ""); then
        local code="${result%%|*}"
        local tail="${result#*|}"
        local verdict="${tail%%|*}"
        echo "[telegram] OK (plain fallback, status=${code}, verdict=${verdict})"
        return 0
    fi
    fail_code="${result%%|*}"
    fail_body="${result##*|}"
    echo "[telegram] 최종 실패 status=${fail_code} body=$(echo "$fail_body" | head -c 200)" >&2
    return 1
}

# ── cleanup 모드 ─────────────────────────────────────────────
if [ "$CLEANUP" = "1" ]; then
    echo "=== regression_watch cleanup ==="
    for unit in "${TIMER_UNITS[@]}"; do
        if systemctl list-units --all --no-legend | grep -q "${unit}\.timer\|${unit}\.service"; then
            echo "  stop + reset: ${unit}"
            sudo systemctl stop "${unit}.timer" 2>/dev/null || true
            sudo systemctl stop "${unit}.service" 2>/dev/null || true
            sudo systemctl reset-failed "${unit}.timer" 2>/dev/null || true
            sudo systemctl reset-failed "${unit}.service" 2>/dev/null || true
        else
            echo "  (이미 정리됨: ${unit})"
        fi
    done
    rm -f /tmp/tg_resp.txt
    echo "=== cleanup 완료 ==="
    exit 0
fi

# ── CP 라벨 필수 ─────────────────────────────────────────────
if [ -z "$CP_LABEL" ]; then
    echo "사용: bash $0 <CP2|CP3|CP4|late_baseline> [--dry-run]" >&2
    echo "      bash $0 --cleanup" >&2
    exit 3
fi

# ── 7개 체크 실행 ─────────────────────────────────────────────
NOW_KST=$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M KST')
echo "=== regression_watch ${CP_LABEL} @ ${NOW_KST} ==="

ERRORS=()
WARNS=()
SUMMARY=()

# 1. EMA-TREND journal 발사 수 (ERROR)
# 주의: grep -c 는 0 매치 시 exit code 1 → `|| echo` 폴백 금지. awk 로 카운트.
EMA_COUNT=$(sudo journalctl -u btc-trader --since "${BASELINE_RESTART_UTC}" --no-pager 2>/dev/null \
    | awk '/EMA-TREND/{c++} END{print c+0}')
if ! [[ "$EMA_COUNT" =~ ^[0-9]+$ ]]; then EMA_COUNT="ERR"; fi
if [ "$EMA_COUNT" = "0" ]; then
    SUMMARY+=("EMA-TREND journal=0 OK")
    echo "  [1/7] EMA-TREND journal 발사 수: 0 ✓"
else
    ERRORS+=("EMA-TREND journal 발사 ${EMA_COUNT}건 감지 (재시작 이후, 임계=0) — 추정: ADR 20261105-1 폐기 패치 미반영 또는 좀비 프로세스 (lessons #27)")
    SUMMARY+=("EMA-TREND=${EMA_COUNT} FAIL")
    echo "  [1/7] EMA-TREND journal 발사 수: $EMA_COUNT ✗ ERROR"
fi

# 2. ema_trend_state.json history 길이 (ERROR)
HISTORY_LEN=$(cd "$PROJECT_DIR" && python3 -c "
import json
try:
    d = json.load(open('workspace/ema_trend_state.json'))
    print(len(d.get('history', [])))
except Exception as e:
    print(f'ERR:{e}')
" 2>/dev/null || echo "ERR")
if [ "$HISTORY_LEN" = "$BASELINE_HISTORY_LEN" ]; then
    SUMMARY+=("history=${BASELINE_HISTORY_LEN} fixed OK")
    echo "  [2/7] ema_trend_state.json history 길이: $HISTORY_LEN ✓"
else
    ERRORS+=("ema_trend_state.json history 길이 ${HISTORY_LEN} (베이스라인 ${BASELINE_HISTORY_LEN}) — 추정: EMA-TREND 신호 재발화 또는 state 파일 조작")
    SUMMARY+=("history=${HISTORY_LEN} FAIL")
    echo "  [2/7] ema_trend_state.json history 길이: $HISTORY_LEN ✗ ERROR"
fi

# 3. systemd active + MainPID 생존 (WARN)
SYSTEMD_INFO=$(systemctl show btc-trader --property=ActiveState,MainPID 2>/dev/null)
ACTIVE_STATE=$(echo "$SYSTEMD_INFO" | grep '^ActiveState=' | cut -d= -f2)
MAIN_PID=$(echo "$SYSTEMD_INFO" | grep '^MainPID=' | cut -d= -f2)
PID_ALIVE="no"
if [ -n "$MAIN_PID" ] && [ "$MAIN_PID" != "0" ] && [ -d "/proc/$MAIN_PID" ]; then
    PID_ALIVE="yes"
fi
if [ "$ACTIVE_STATE" = "active" ] && [ "$PID_ALIVE" = "yes" ]; then
    SUMMARY+=("systemd active PID=${MAIN_PID}")
    echo "  [3/7] systemd: active PID=$MAIN_PID (alive) ✓"
else
    WARNS+=("systemd 비정상: ActiveState=${ACTIVE_STATE} MainPID=${MAIN_PID} alive=${PID_ALIVE} — 추정: 봇 크래시 또는 재시작 중")
    SUMMARY+=("systemd ${ACTIVE_STATE} WARN")
    echo "  [3/7] systemd: $ACTIVE_STATE PID=$MAIN_PID alive=$PID_ALIVE ✗ WARN"
fi

# 4. 좀비 수 (daily_live.py, bash -c 와 grep 제외) (WARN)
ZOMBIE_COUNT=$(pgrep -af "daily_live.py" 2>/dev/null | grep -v "bash -c" | grep -v grep | wc -l)
if [ "$ZOMBIE_COUNT" -le 1 ]; then
    SUMMARY+=("zombie=${ZOMBIE_COUNT}")
    echo "  [4/7] daily_live.py 프로세스: $ZOMBIE_COUNT (≤1) ✓"
else
    WARNS+=("daily_live.py 프로세스 ${ZOMBIE_COUNT}개 (임계 ≤1) — 추정: cron 호출 좀비 누적 (lessons #24/#27/#34)")
    SUMMARY+=("zombie=${ZOMBIE_COUNT} WARN")
    echo "  [4/7] daily_live.py 프로세스: $ZOMBIE_COUNT ✗ WARN"
fi

# 5. 메모리 < 400 MiB (WARN)
MEM_BYTES=$(systemctl show btc-trader -p MemoryCurrent --value 2>/dev/null)
if [ -z "$MEM_BYTES" ] || [ "$MEM_BYTES" = "[not set]" ] || ! [[ "$MEM_BYTES" =~ ^[0-9]+$ ]]; then
    MEM_MIB="unknown"
    WARNS+=("메모리 측정 실패 (MemoryCurrent='${MEM_BYTES}')")
    SUMMARY+=("mem=unknown WARN")
    echo "  [5/7] 메모리: unknown ✗ WARN"
else
    MEM_MIB=$(( MEM_BYTES / 1024 / 1024 ))
    if [ "$MEM_MIB" -lt "$MEM_LIMIT_MIB" ]; then
        SUMMARY+=("mem=${MEM_MIB}M")
        echo "  [5/7] 메모리: ${MEM_MIB} MiB (< ${MEM_LIMIT_MIB}) ✓"
    else
        WARNS+=("메모리 ${MEM_MIB} MiB (한도 ${MEM_LIMIT_MIB} MiB 초과) — 추정: 메모리 누수 또는 레코드 폭증 (lessons #5)")
        SUMMARY+=("mem=${MEM_MIB}M WARN")
        echo "  [5/7] 메모리: ${MEM_MIB} MiB ✗ WARN"
    fi
fi

# 6. systemd bata-* timer 개수 == 9 (WARN)
# 참고: regression_watch 전용 transient timer(bata-regression-*)는 제외하고 영구 timer만 카운트
TIMER_COUNT=$(systemctl list-timers --all --no-legend 2>/dev/null \
    | grep -E 'bata-[a-z]' \
    | grep -v 'bata-regression' \
    | wc -l)
if [ "$TIMER_COUNT" = "9" ]; then
    SUMMARY+=("timer=9")
    echo "  [6/7] systemd bata-* timer 개수: 9 ✓"
else
    WARNS+=("systemd bata-* timer ${TIMER_COUNT}개 (베이스라인 9) — 추정: timer 소실(lessons #44) 또는 신규 추가")
    SUMMARY+=("timer=${TIMER_COUNT} WARN")
    echo "  [6/7] systemd bata-* timer 개수: $TIMER_COUNT ✗ WARN"
fi

# 7. crontab BitCoin_Trade 라인 == 0 (WARN, lessons #44)
# 주의: grep -c 는 0 매치 시 exit code 1 → awk 로 카운트.
CRON_LINES=$(crontab -l 2>/dev/null | awk '/BitCoin_Trade/{c++} END{print c+0}')
if ! [[ "$CRON_LINES" =~ ^[0-9]+$ ]]; then CRON_LINES="0"; fi
if [ "$CRON_LINES" = "0" ]; then
    SUMMARY+=("cron=0")
    echo "  [7/7] crontab BitCoin_Trade 라인 수: 0 ✓"
else
    WARNS+=("crontab BitCoin_Trade 라인 ${CRON_LINES}개 (임계 0) — 추정: 타 프로젝트 배포가 crontab 덮어씀 또는 수동 추가 (lessons #36)")
    SUMMARY+=("cron=${CRON_LINES} WARN")
    echo "  [7/7] crontab BitCoin_Trade 라인 수: $CRON_LINES ✗ WARN"
fi

# ── 종합 판정 ─────────────────────────────────────────────────
NUM_ERR=${#ERRORS[@]}
NUM_WARN=${#WARNS[@]}

if [ "$NUM_ERR" -gt 0 ]; then
    VERDICT="ERROR"
    EXIT_CODE=1
elif [ "$NUM_WARN" -gt 0 ]; then
    VERDICT="WARN"
    EXIT_CODE=2
else
    VERDICT="OK"
    EXIT_CODE=0
fi

echo ""
echo "판정: ${VERDICT} (ERROR=${NUM_ERR}, WARN=${NUM_WARN})"

# ── 텔레그램 메시지 조립 ─────────────────────────────────────
case "$VERDICT" in
    OK)
        MSG="[regression_watch] ${CP_LABEL} OK @ ${NOW_KST}"$'\n'"$(IFS=' / '; echo "${SUMMARY[*]}")"
        ;;
    WARN)
        MSG="[regression_watch] ${CP_LABEL} WARN @ ${NOW_KST}"
        for w in "${WARNS[@]}"; do MSG+=$'\n'"  - ${w}"; done
        MSG+=$'\n\n'"요약: $(IFS=' / '; echo "${SUMMARY[*]}")"
        ;;
    ERROR)
        MSG="[regression_watch] ${CP_LABEL} ERROR — engineer 즉시 확인 필요 @ ${NOW_KST}"
        for e in "${ERRORS[@]}"; do MSG+=$'\n'"  ERROR: ${e}"; done
        if [ "$NUM_WARN" -gt 0 ]; then
            for w in "${WARNS[@]}"; do MSG+=$'\n'"  WARN: ${w}"; done
        fi
        MSG+=$'\n\n'"요약: $(IFS=' / '; echo "${SUMMARY[*]}")"
        ;;
esac

# CP4 전용 추가 문구 (task #6 close 승인 요청)
if [ "$CP_LABEL" = "CP4" ]; then
    MSG+=$'\n\n'"회귀 감시 24h 완료 — Task #6 close 승인 요청 (operator 최종 확인 후 PM 판정)"
fi

echo ""
echo "── 메시지 미리보기 ──"
echo "$MSG"
echo "────────────────────"

# ── 텔레그램 발송 또는 dry-run ─────────────────────────────────
if [ "$DRY_RUN" = "1" ]; then
    echo "(--dry-run → 텔레그램 발송 스킵)"
else
    send_telegram "$MSG" || echo "텔레그램 발송 실패 — stdout 로그 남김"
fi

exit "$EXIT_CODE"
