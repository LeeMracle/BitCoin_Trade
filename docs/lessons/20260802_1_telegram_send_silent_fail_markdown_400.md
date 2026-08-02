# lessons #39 — 텔레그램 발송 silent fail (Markdown 400 미확인)

- 날짜: 2026-08-02 (KST)
- 분류: 알림 파이프라인 / silent fail / API 응답 미검증
- 관련 lessons: #12(None 포매팅), #20(silent fail), #21(fail-closed), #31(silent fail), #38(아침 브리핑)

## 현상

- 어제(2026-08-01) 09:32 KST 아침 브리핑 cron(`daily_check.py --notify`) 신설 배포 완료 (lessons #38, commit 9c08290)
- 어제 밤 22:00 KST 수동 실행 → 텔레그램 발송 성공 (사용자 실측 확인)
- **오늘 2026-08-02 09:32 KST 자동 발화 텔레그램 미도착** (사용자 실측)
- 서버 로그(`/var/log/btc_report.log`)에는 정상적으로 브리핑 텍스트 + `[아침 브리핑] 텔레그램 발송 성공`이 남아 있음
- cron 자체는 `/var/log/syslog`에서 UTC 00:32 정상 발화 확인, 코드는 정상 실행됨

## 근본 원인

**한 줄**: `services/execution/telegram_bot.py::send_message`가 텔레그램 API의 HTTP status를 확인하지 않아 Markdown parse 실패(400 Bad Request)를 예외로 잡지 못했고, 상위 호출자는 무조건 "성공"으로 오판했다.

상세:

1. 오늘 아침 브리핑 텍스트에는 systemd 필드가 포함됨:
   - `MainPID=2206594`
   - `ActiveEnterTimestamp=Sat 2026-08-01 13:11:52 UTC`
   - `daily_live.py 프로세스: 총 1`
2. legacy Markdown 파서는 `_` 밑줄을 이탤릭 시작 문자로 해석. `MainPID`, `ActiveEnterTimestamp`, `daily_live` 등 짝이 안 맞는 밑줄이 다수 존재 → **HTTP 400 Bad Request** (`Can't find end of the entity starting at byte offset 903`)
3. 기존 `send_message` 구현:
   ```python
   async with session.post(url, json=payload, timeout=...):
       pass                          # <-- status 확인 없음
   except Exception:
       pass                          # <-- 400은 예외 아님, 무시됨
   ```
4. 반환값도 없음 → 상위 호출자(`_send_briefing`)는 예외만 안 잡히면 성공으로 간주하여 "발송 성공" 로그 남김
5. 어제 22:00 수동 실행이 성공한 이유는 우연: 그 시점 브리핑 텍스트가 Markdown 파싱을 통과하는 조합이었음. 오늘 아침에는 systemd 필드가 다르게 조합되어 파싱 실패.

## 핵심 교훈

- **모든 외부 API 호출은 응답 status를 확인해야 한다.** HTTP 4xx/5xx는 예외가 아니라 정상 응답이므로 `try/except`만으로는 실패를 감지할 수 없다 (lessons #21 fail-closed 원리와 동일)
- **알림 파이프라인의 "발송 성공" 로그는 실제 도착과 동치가 아니다.** 로컬 로그 성공 = 도착 보장 → 위험한 착각. 응답 status + `ok: true` 필드까지 확인해야 한다
- **Markdown parse_mode는 legacy 파서로 이스케이핑이 불완전하다.** systemd/PID/파일명 등 밑줄이 포함된 텍스트는 4xx 유발 가능 → parse_mode 없는 fallback 필수
- 텍스트가 데이터 의존적으로 파싱을 통과할 때도 있고 실패할 때도 있어 **간헐적·재현 어려운 silent fail**이 된다 (어제 성공, 오늘 실패의 원인)

## 수정

### 코드 (silent fail 근절 + fallback)

`services/execution/telegram_bot.py::send_message`:
- 반환 타입 `-> bool` 추가 (성공 True / 실패 False)
- HTTP `resp.status == 200` 명시 확인
- 400 시 parse_mode 제거된 plain payload로 자동 재시도 (한 번)
- 그 외 status(401/403/429/5xx) 또는 예외 시 즉시 중단하고 stdout에 `[telegram] 발송 실패 — <details>` 로그
- 토큰/chat_id 미설정 시에도 stdout 로그 남기고 False 반환

`scripts/daily_check.py::_send_briefing`:
- `send_message`의 반환값을 신뢰
- 실패 시 명확한 로그 + `sys.exit(1)` 반영 → cron 실행 자체는 성공했다는 착각 차단

### 배포

- hotfix 2 파일 (`services/execution/telegram_bot.py`, `scripts/daily_check.py`)
- 코드만 변경 (인프라 미변경) → `scripts/hotfix_deploy.sh` 사용

### 검증 (실측)

- 서버에서 09:32 브리핑 원문 그대로 Markdown parse_mode로 재발송 시 status 400 재현 성공 (message id 미할당)
- 동일 원문을 plain payload로 재발송 시 status 200 + `ok: true` + `message_id=1598` 성공
- 봇/채팅 매핑 정상: `claude_code_remot_bot (id=8737963559)` → `Jungsu Lee (chat_id=8200493718)`

## 검증규칙 (pre_deploy_check 추가)

`scripts/pre_deploy_check.py::check_telegram_send_status_verified()`:

1. `services/execution/telegram_bot.py`의 `send_message` 함수 시그니처에 `-> bool` 반환 타입 명시 여부
2. 함수 본문에 `resp.status` 참조 존재 여부 (없으면 ERROR)
3. parse_mode 제거된 plain payload fallback 리터럴 존재 여부 (없으면 WARN)

## 관련 lessons

- lessons #12 — dict.get 결과 None format crash → 유사한 "상위가 조용히 잘못됨" 패턴, 린트로 방지
- lessons #20 — 헬스체크 판정 기준은 정상 동작 시 항상 갱신되는 값이어야 false alarm 회피 → 이번엔 반대로 "실패해도 성공으로 기록"되는 silent fail
- lessons #21 — 안전장치는 fail-closed 원칙 → status 미확인은 fail-open과 동치
- lessons #31 — cron 등록 silent fail → 이번엔 API 응답 silent fail (같은 유형: "실행됐다 = 성공"의 착각)
- lessons #38 — 아침 브리핑 채널 부재 → 신설 후 하루 만에 발생한 첫 데이터 의존 실패
