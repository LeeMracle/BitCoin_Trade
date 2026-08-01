# lessons #37 — 5연패 self.running=False vs systemd Restart=always 충돌, watchdog SIGABRT로 안전장치 무력화

- 날짜: 2026-06-04
- 분류: 안전장치 / systemd 정합 / 회귀 위험
- 관련 lessons: #3 (안전장치 즉시체크), #15 (외부 API 재시도 vs systemd 재시작), #24 (좀비 프로세스), #30 (consec_loss alert loop), #31 (cron 등록 silent fail)

## 현상

- 2026-06-02 09:04:10 KST: `realtime_monitor._send_periodic_report()`에서 5연패 트리거 → `self.running = False` 실행 + 텔레그램 "🛑 5연패 자동 중단" 발사
- 2026-06-02 09:09:07 KST (5분 후): systemd가 `Watchdog timeout (limit 5min)` 판정 → `SIGABRT (code=6/ABRT)` 발송
- 2026-06-02 09:13:24 KST: `Restart=always` 정책으로 봇 자동 부활 → **5연패 안전장치 무력화**
- 2026-06-02 09:46:24 KST: 동일 시퀀스 재발생 (watchdog timeout → 재시작 → 현 가동분 PID 891532)
- 다행히 `cooldown_until` (3연패 72h)가 별도 트리거로 살아있어 매수 차단은 유지됨 — 그러나 이는 **우연 회피**
- 2026-06-04 03:08 KST 현재 `cooldown_until=0` (3연패 72h 만료) — 9시간 후 매수 재개 가능, `consec_loss_alerted_until=06-05 00:04 KST`까지 silence 살아있음 → **5연패 silence 살아있으면서 매수 차단 풀린 위험 윈도우**

## 근본 원인

1. **systemd 정합 가정 오류**: `self.running = False`는 메인 루프 다음 iteration에서 빠져나가게 하지만, 빠져나가는 데 시간이 걸리는 동안 systemd `WatchdogSec=5min`을 만족하는 `sd_notify("WATCHDOG=1")` 호출이 멈춤. 5분 후 systemd가 timeout 판정.
2. **종료 시그널 누락**: 정상 종료 의도라면 `sd_notify("STOPPING=1")`을 먼저 보내고 systemd가 ExecStop으로 진입하게 해야 함. 그런데 코드는 단순히 `self.running = False`만 함.
3. **Restart=always 무차별 적용**: 정상 종료(exit 0)와 비정상 종료(SIGABRT)를 구분 없이 모두 재시작 — `Restart=on-failure`로도 SIGABRT는 재시작 대상.
4. **silence와 차단 분리 미흡**: `consec_loss_alerted_until`(silence)과 `cooldown_until`(매수 차단)이 동기화되지 않으면, 부활한 봇은 silence 살아있어 재알람 안 함 + cooldown 만료되면 매수 재개 → 사용자 무지 상태로 안전장치 우회 거래 발생.

## 핵심 교훈

- **systemd Restart=always 환경의 봇은 `self.running=False`로 안전장치 의도 표현 금지**. 봇 프로세스 종료가 안전장치의 정의가 되면 안 됨 — 종료를 시도하는 순간 systemd가 부활시킬 가능성이 있고, watchdog 정합 책임도 짊어져야 함.
- **안전장치는 state 플래그(cooldown_until, alerted_until)로 표현하고 process는 살아있게**. 매수 경로는 cooldown_until만 보면 됨(_is_loss_cooldown). state는 재시작에 살아남고 supervisor 의존이 없음.
- **silence ↔ cooldown invariant 명문화**: 둘 중 하나 갱신 시 나머지 자동 동기화. 5분 디바운스로 인한 재알람 방지 + cooldown 종료 시 silence도 만료되도록.
- **봇 자동 종료가 진짜 필요하면** `sd_notify("STOPPING=1")` → graceful exit (0) → `Restart=on-failure` 정책 또는 `Restart=no` + 별도 ExecStop 처리. systemd 의도와 코드 의도가 한 방향이어야 함.

## 수정 (코드 + 운영 조치)

### 코드 (B9 hotfix)
- 파일: `services/execution/realtime_monitor.py` 라인 675-693 (5연패 분기)
- 변경:
  - `self.running = False` 제거
  - `cooldown_until = max(현재값, now+72h)` 강제 갱신
  - `consec_loss_alerted_until = cooldown_until` 동기화 (lessons #30 invariant 강화)
  - 텔레그램 메시지 "🛑 5연패 자동 중단" → "🛑 5연패 cooldown 72h 자동 연장" (process는 정상 가동)

### 보존 경로
- 라인 1599 `self.connectivity_errors >= MAX_CONSECUTIVE_ERRORS` 분기의 `self.running=False`는 **유지** (시스템 오류 누적 시 systemd에 정상 종료 신호 + 재시작 의도가 맞음, 안전장치 의미와는 별개)
- 사용자 정의 stop 명령 경로(이 파일에 직접 없음, 외부 시그널 처리)는 영향 없음

### 운영 조치 (배포 후)
- 봇 재시작 후 `state["cooldown_until"]`이 5연패 트리거 시 정상 연장되는지 1주 모니터링
- 알람 메시지 변경 텍스트 텔레그램 검증

## 검증규칙 (pre_deploy_check 추가)

### Rule 1: `check_consec_loss_no_running_false()`
- 대상: `services/execution/realtime_monitor.py`
- 검출: 5연패 분기(`if consec >= 5:` 블록 내부) 안에 `self.running = False` 패턴 존재 시 ERROR
- 사유: lessons #37 위배 — process 종료로 안전장치 의도 표현 금지

### Rule 2: `check_consec_loss_cooldown_invariant()`
- 대상: `services/execution/realtime_monitor.py`
- 검출: `consec_loss_alerted_until` 설정 라인 인근 30줄 내 `self.state["cooldown_until"]` 또는 `cooldown_until = max(` 동시 설정 없으면 ERROR
- 사유: silence ↔ 매수 차단 invariant 강제 (lessons #30 강화)

## 관련 lessons

- **#3 (안전장치 즉시체크)**: 5연패 즉시 cooldown 갱신 — 본 변경은 `_send_periodic_report` 주기 호출에서 실행되지만 cooldown 강제 갱신 로직은 idempotent하므로 주기 체크라도 안전.
- **#30 (consec_loss alert loop)**: silence 디바운스 — 본 변경에서 `alerted_until = cooldown_until` 동기화로 invariant 강화.
- **#31 (cron 등록 silent fail)**: 별도 채널 변경 위험 — 본 변경은 단일 파일 코드 변경이라 해당 없음. 단, 배포는 deploy_to_aws.sh 또는 hotfix_deploy.sh 강제.
- **#24 (좀비 프로세스)**: systemd 재시작이 별도 PID 좀비 양산 — 본 변경은 단일 봇 process 유지라 좀비 발생 안 함.
