# lessons #56 — regression_watch.sh 텔레그램 발송 "성공인데 실패 판정" (curl exit 23 + fallback 혼합)

- 날짜: 2026-11-06
- 분류: 알림/배포 운영 (lessons #39 후속)
- 관련 lessons: #39(Markdown 400 삼킴), #22(retry 범위), #21(fail-closed)
- 트리거 금액: 0원 (알람 최종 실패로만 보임, 실제 텔레그램에는 메시지 2766/2772/2765 도달)

## 현상

2026-11-05 ~ 11-06 회귀 감시 CP2/CP3/CP4 (lessons #53/#54 서버 배포 후 24h 감시) 3회 전부 journal에
다음 패턴 반복:

```
Nov 06 04:05:02 bash[541452]: curl: (23) Failure writing output to destination
Nov 06 04:05:02 bash[541394]: [telegram] Markdown status=200000 body={"ok":true,"result":{"message_id":2766,...}} → plain 재시도
Nov 06 04:05:03 bash[541462]: curl: (23) Failure writing output to destination
Nov 06 04:05:03 bash[541394]: [telegram] 최종 실패 status=200000 ...
Nov 06 04:05:03 bash[541394]: 텔레그램 발송 실패 — stdout 로그 남김
```

- API 응답 body에 `"ok":true,"result":{"message_id":2766,...}` — **텔레그램에 메시지 실제 도달**
- 그러나 status 코드 `"200000"` (6자리) — 정상 200이 아니라 bash 변수로 들어온 결합문자열
- 재시도 로직이 Markdown → plain 자동 재발사 → **메시지 2건씩 이중 도달 가능** (CP2/CP3/CP4 각 2개)

## 근본 원인

**curl exit 23 + `$()||echo "000"` 폴백이 stdout 문자열을 결합한 것**

문제 코드 (수정 전 `send_telegram()`):
```bash
resp=$(curl -sS -o /tmp/tg_resp.txt -w "%{http_code}" \
    --max-time 10 -X POST "$url" \
    -H "Content-Type: application/json" \
    --data-raw "$(python3 -c '...')" || echo "000")
```

실행 흐름:
1. curl이 요청 전송 → 서버 200 응답 → stdout 에 `"200"` 출력 (–w 지시자)
2. curl이 body를 `/tmp/tg_resp.txt` 에 쓰려 함 → **write 실패 → exit 23 반환**
3. `||` 분기 발동 → `echo "000"` 추가 출력
4. command substitution `$(...)` 가 stdout 전체를 캡처 → `resp="200000"` (3+3자리)
5. `if [ "$resp" = "200" ]` 비교 실패 → plain 재시도 → 같은 패턴 반복

**curl: (23) 자체의 원인** (서버 실측 재현):
- `/tmp/tg_resp.txt` 가 **ubuntu:ubuntu `-rw-rw-r--`** 로 이미 존재
- systemd service는 root 로 실행 (bata-regression-cp4.service)
- root가 ubuntu 소유 파일을 open 자체는 성공(CAP_DAC_OVERRIDE)하나, curl 내부 **write/close 단계에서 실패** → exit 23
- 파일은 0바이트로 truncate 되고, 다음 실행부터는 root:root 소유가 되어 자가 복구됨 (즉 **간헐적 발생 + 자동 자가해소**로 오랫동안 미발견)
- 재현 명령: `sudo touch /tmp/tg_resp.txt && sudo chown ubuntu:ubuntu /tmp/tg_resp.txt && sudo bash regression_watch.sh CP4`

## 핵심 교훈

> **curl exit code 와 HTTP status 를 섞어 파싱하는 패턴은 fragile하다. API 응답 body(`"ok":true`)
> 가 1차 판정 기준이어야 하고, 임시 파일은 세션별 유니크(`mktemp`)여야 한다.**

- 공유 임시 파일 경로(`/tmp/tg_resp.txt`)는 **UID 가 다른 실행 주체가 섞이면** 소유자 충돌 발생.
  systemd service(root)와 수동 실행(ubuntu)이 같은 파일 재사용 시 반드시 터진다.
- `$(curl ...) || echo "000"` 패턴은 curl이 **일부 출력 후 실패**할 때 stdout 결합으로 비정상 문자열 생성.
  `-w` 는 stdout 쓰기이므로 http_code는 이미 출력됨 → 폴백 echo가 **뒤에 덧붙음**.
- lessons #39 ("`except: pass`가 400을 삼킴")의 bash 변종 — **"API 성공을 bash가 실패로 오판한다"**.
  방향은 반대지만 뿌리는 같다: **응답 파싱이 fragile해서 침묵/폭주**.

## 수정 (코드)

`scripts/regression_watch.sh` `send_telegram()` 재설계 — 3축:

1. **세션 유니크 임시 파일**: `mktemp -t "tg_resp.XXXXXX"` + RETURN trap 으로 cleanup
2. **curl exit ≠ API 성공**: `curl ... 2>/dev/null || true` 로 exit 23 흡수 + http_code 는 `[0-9]{3}` 검증 후 정규화
3. **1차 판정 = body `"ok":true`** (2차 = http_code 200 + 빈 body도 OK_NO_BODY 로 허용)

```bash
send_telegram() {
    ...
    local tmp_resp
    tmp_resp=$(mktemp -t "tg_resp.XXXXXX") || tmp_resp="/tmp/tg_resp.$$"
    trap "rm -f '${tmp_resp}'" RETURN

    _send_once() {
        local parse_mode="$1"
        local payload=...
        local http_code
        http_code=$(curl -sS -o "$tmp_resp" -w "%{http_code}" \
            --max-time 10 -X POST "$url" \
            -H "Content-Type: application/json" \
            --data-raw "$payload" 2>/dev/null || true)
        if ! [[ "$http_code" =~ ^[0-9]{3}$ ]]; then http_code="000"; fi
        local body=""
        [ -s "$tmp_resp" ] && body=$(head -c 1000 "$tmp_resp")
        if echo "$body" | grep -q '"ok":true'; then
            echo "$http_code|OK|$body"; return 0
        fi
        if [ "$http_code" = "200" ]; then
            echo "$http_code|OK_NO_BODY|$body"; return 0
        fi
        echo "$http_code|FAIL|$body"; return 1
    }
    ...
}
```

### 검증 (서버 실측)

1. 재현 조건 복원: `sudo touch /tmp/tg_resp.txt && sudo chown ubuntu:ubuntu /tmp/tg_resp.txt`
2. 수정판 실행: `sudo bash scripts/regression_watch.sh CP4`
3. 결과: `[telegram] OK (Markdown, status=200, verdict=OK)` — 첫 시도 성공, 재시도 없음, mktemp 생성 유니크 파일로 소유자 충돌 회피

### 수정 범위

- **단일 파일**: `scripts/regression_watch.sh` (regression_watch 는 1회성 24h 감시 전용 — 다른 경로 영향 없음)
- 다른 bash 텔레그램 발송 스크립트 전수 조사:

```
scripts/send_alert.sh       — 미존재
scripts/telegram_send.sh    — 미존재
scripts/daily_check.py      — Python httpx 경유 (별도, lessons #39 수정됨)
scripts/regime_check.py     — Python 경유 (lessons #39 수정됨)
```

→ bash 텔레그램 발송 스크립트는 regression_watch.sh **단독**. 확산 조치 불필요.

## 검증규칙 (pre_deploy_check 추가)

**결정: lessons 등재만으로 충분, pre_deploy_check 룰은 추가하지 않음.**

사유:
- regression_watch.sh 는 **1회성 24h 감시** 스크립트 (CP2/CP3/CP4 배포 후 한 번만 실행 후 cleanup)
- 동일 패턴의 bash 텔레그램 발송 스크립트 추가 가능성 낮음 (BATA의 알림 주력은 Python 경로)
- 범용 룰 (bash에서 `-o /tmp/고정파일` + curl + `|| echo "000"` 감시) 은 false positive 대비 가치 낮음
- 재발 방지는 **lessons 열람 의무 + 코드 리뷰**로 흡수 (CLAUDE.md 알림 영역 인덱스에 등재)

단, **룰 추가 조건**이 발생하면 복귀:
- bash 텔레그램 발송 스크립트 2개 이상 추가되는 순간
- 또는 `/tmp/<고정이름>` 공유 임시 파일 패턴이 다른 스크립트에 반복 적용되는 순간

## 관련 lessons

- **#39** — 텔레그램 Markdown 400 삼킴 (Python `except: pass` → `send_message` 반환값 + status 검증).
  이 lessons는 bash 변종 — API 응답이 성공인데 **bash 파싱 실패로 재시도 폭주**.
- **#22** — retry 적용 범위. regression_watch 는 **1회성 알림**이라 Markdown→plain 자동 재시도 자체는 안전.
  단, "실패로 오판하고 재시도" 하는 경우 **메시지 중복 발송** 유발 (실측 2766 + 2772 두 번 도착).
- **#21** — fail-closed 원칙. 이 수정은 그 반대 **fail-open-on-api-success**: curl exit code보다
  API body `"ok":true` 를 신뢰. 안전장치가 아닌 **알림**이라 fail-open 허용.
- **#44** — cron 감시기가 감시 대상 안에 있어 함께 죽음. regression_watch 는 systemd timer 라 그 문제 없음.
