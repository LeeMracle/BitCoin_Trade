# 가상환경 디렉터리 이름 변경 후 crontab 경로 미동기화 — 7일간 silent 실패

- **발생일**: 2026-04-25 (감지) / 2026-04-18 (실 발생)
- **심각도**: MEDIUM (regime gate `enabled=false`로 거래 영향은 차단되었으나, BULL 트리거 측정 자체가 7일 무효)
- **카테고리**: 운영 / 환경 동기화

## 증상

자비스 일일 운영 감시(04-25 토)에서 `regime_state.json`이 04-18 17:25 이후 7일간 미갱신 발견.

```
last_decided_ts: 1776500703  (2026-04-18 17:25 KST)
since_ts:        1776475503  (2026-04-18 진입)
recent_signals:  ["BEAR","BEAR","BEAR","BEAR","BEAR"]
enabled:         false
```

`/var/log/regime_check.log` 175줄 전부 `No such file or directory` 오류 — 매시 25분 cron은 정상 트리거되었으나 실행 자체가 실패하고 있었음.

## 원인

서버 가상환경 디렉터리가 `venv` → `.venv`로 리네임된 시점이 있었으나(정확한 시점 미확인, 04-18 직전), crontab의 다음 라인이 함께 갱신되지 않음:

```
25 * * * * cd /home/ubuntu/BitCoin_Trade && PYTHONUTF8=1 PYTHONPATH=... \
  /home/ubuntu/BitCoin_Trade/venv/bin/python scripts/regime_check.py --notify \
  >> /var/log/regime_check.log 2>&1
```

- 실제 경로: `/home/ubuntu/BitCoin_Trade/.venv/bin/python` (점 있음)
- crontab 등록값: `/home/ubuntu/BitCoin_Trade/venv/bin/python` (점 없음)

cron은 매시 정시 실행되었으나 `python: command not found` → exit 127로 종료. stderr가 `/var/log/regime_check.log`로 리디렉션되어 외부 알림 없이 누적만 진행. systemd journal이나 텔레그램 알림으로 노출되지 않아 7일간 발견 못 함.

근본 원인은 교훈 #4(전략 파라미터)의 환경 변종 — **인터프리터 경로 자체가 환경 동기화 항목**임을 인지하지 못함. 교훈 #18(이번)로 별도 기록.

## 수정

- [x] crontab `venv/bin/python` → `.venv/bin/python` 1글자 치환 (sed 적용, 다른 라인 무변경)
- [x] crontab 백업 `/home/ubuntu/crontab.bak.20260425`
- [x] regime_check.py 1회 수동 실행 — 종료코드 0, regime_state.json `last_decided_ts`가 실행 시각 근처(2026-04-25 15:21:12 KST)로 갱신됨
- [x] BEAR 판정 정상 출력: `BTC=115,480,000 < EMA200=124,980,753 → BEAR`

## 검증규칙 (`scripts/pre_deploy_check.py` 추가 대상)

다음 검증을 추가한다 — 로컬에서 직접 검증은 어려우나 SSH 가능 시 cto health 흐름에 추가:

1. **crontab 경로 정합성**: `crontab -l`에서 `python` 인터프리터 경로 추출 후 `ssh test -x <경로>` 검증. 실패 시 위험 보고.
2. **regime_state staleness**: `last_decided_ts`가 현재 시각 대비 2시간 초과 시 위험. 매시 25분 cron의 정상 동작 여부를 *결과 데이터*로 확인.
3. **silent cron failure 일반화**: cron 라인 stderr 리디렉션 대상 로그 파일의 마지막 N줄에 `No such file`/`command not found`/`ImportError`가 반복되면 위험.

본 항목은 cto health 스킬 점검 항목으로 추가 권장 (로컬 pre_deploy_check은 SSH 미사용이라 한계).

## 교훈

1. **가상환경 디렉터리 리네임은 광역 동기화 작업이다**. `.venv` 표준화 시 다음을 모두 확인:
   - crontab 라인 (사용자별 `crontab -l`)
   - systemd unit 파일 (`/etc/systemd/system/*.service`의 `ExecStart`)
   - 배포 스크립트, 헬퍼 셸 스크립트
   - CI 설정, 도커파일

2. **stderr 리디렉션은 silent failure를 만든다**. 단발성 cron의 stderr를 로그 파일로만 보내면 외부 모니터링 트리거가 작동하지 않는다. 핵심 cron은 결과 데이터(state json)의 갱신 시각을 모니터링하거나, 실패 시 텔레그램 알림 경로를 확보해야 한다.

3. **`enabled=false` 상태는 실패를 가린다**. 자동 거래 게이트가 꺼져 있어 손실은 없었으나, 신호 자체가 7일 stale인 것을 즉시 알아차리지 못함. 비활성 컴포넌트도 데이터 신선도는 모니터링 대상.

## 관련 교훈

- 교훈 #4 (CLAUDE.md ↔ config.py ↔ 서버 동기화) — 환경 변종의 일종
- 교훈 #9 (자동화 cron 등록 + pre_deploy_check 검증) — cron 등록은 됐으나 실행 결과는 검증 안 됨
- 교훈 #15 (외부 API 의존 초기화는 재시도+백오프 필수) — 본 건은 cron 실패라 재시도 무관

## 참조

- 서버: `/home/ubuntu/crontab.bak.20260425` (변경 전 백업)
- 서버: `/var/log/regime_check.log` (175줄 오류 + 1줄 정상 복구 흔적)
- 서버: `/home/ubuntu/BitCoin_Trade/workspace/regime_state.json`
- 로컬: `D:\20.Personal\Study\BitCoin_Trade\scripts\regime_check.py`
