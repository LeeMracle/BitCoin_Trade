# 블록 1 — Heartbeat / Watchdog / WebSocket stale (P7-03/04/05/07)

- **작성일(KST)**: 2026-04-17 09:20
- **작성자/세션**: pdca-pm (자비스)
- **예상 소요**: 2시간
- **관련 이슈/결정문서**: [20260410_monitoring_framework.md](20260410_monitoring_framework.md), docs/lessons/20260410_1 (CB 로그 스팸), docs/lessons/20260413_1 (startup refresh crash)

## 1. 목표

BATA 봇의 "죽어도 알 방법이 없다" 문제를 해결하는 1계층 Heartbeat/Watchdog + 웹소켓 stale 감지를 완성한다. 이미 realtime_monitor.py에 코드상 구현된 heartbeat(P7-03) / last_msg_ts(P7-07) / hourly_sync(P7-06)을 **로컬 검증**하고, 서버 측 **cron·systemd 자동화**를 최종 반영한다.

## 2. 성공기준 (Acceptance Criteria)

- [ ] AC-1 (P7-03): realtime_monitor가 매 5분 `/tmp/bata_heartbeat` touch — 코드 라인 확인 + 기존 pre_deploy_check에 검증 규칙 추가
- [ ] AC-2 (P7-04): `scripts/watchdog_check.sh` 정상 동작 (로컬 dry-run: 파일 존재/AGE 판정/텔레그램 메시지 구성) + `deploy_to_aws.sh`에 crontab 등록 로직이 존재
- [ ] AC-3 (P7-05): `config/btc-trader.service` 에 `WatchdogSec=300` + `NotifyAccess=all`(또는 Type=notify) + realtime_monitor에 `sd_notify("WATCHDOG=1")` 연동 — sd_notify 미설치 환경에서도 안전하게 동작(try/except)
- [ ] AC-4 (P7-07): 웹소켓 5분 무수신 시 `asyncio.TimeoutError` 경로로 강제 재연결 + 경보 — 코드 라인 확인 + pre_deploy_check에 검증 규칙 추가
- [ ] AC-5: `python scripts/pre_deploy_check.py` GREEN
- [ ] AC-6: `python scripts/lint_none_format.py` GREEN
- [ ] AC-7: 교차검증 — builder와 다른 세션(qa)이 "확인 항목 N / 발견 이슈 M" 형식으로 판정

## 3. 단계

1. realtime_monitor.py 내 heartbeat/last_msg_ts/hourly_sync/ws-stale 블록 재확인(코드 그대로 유지)
2. `sd_notify` 연동 추가 (optional import, runtime에 systemd-python 없으면 무시)
3. `config/btc-trader.service`에 `WatchdogSec=300` + `Type=notify` 반영
4. `scripts/watchdog_check.sh` shellcheck (bash -n) + 서버 배포 경로 정합성 확인
5. `scripts/deploy_to_aws.sh`에 watchdog crontab 등록 스니펫이 포함되어 있는지 확인(없으면 추가)
6. `pre_deploy_check.py`에 `heartbeat/ws-stale/WatchdogSec` 검증 규칙 추가
7. 로컬 검증: `python -c "from services.execution.realtime_monitor import RealtimeMonitor"` 임포트 + `pre_deploy_check.py` + `lint_none_format.py`
8. 교차검증 기록

## 4. 리스크 & 사전 확인사항

| 리스크 | 완화 |
|--------|------|
| Windows 로컬에서 `/tmp/bata_heartbeat` 경로 사용 불가 | 코드 상 Path("/tmp/...")는 POSIX 경로이므로 리눅스 서버에서만 유효. 로컬 테스트는 "코드 존재 확인"까지만 수행 |
| systemd-python 의존 추가 시 requirements 폭발 | `sd_notify`는 stdlib 없음 → 소켓 기반 경량 구현 사용 (`os.environ["NOTIFY_SOCKET"]`) |
| WatchdogSec 설정 시 기존 프로세스 재시작 필요 | 배포 시 systemctl daemon-reload + restart 필수 |
| 참조: lessons/20260413_1 (startup refresh crash) — 재시작 루프 방지를 위해 watchdog_check.sh의 30분 쿨다운 유지 |

## 5. 검증 주체 (교차검증)

- [x] 옵션 B — 서브에이전트 pdca-qa (builder와 분리)
- [x] 옵션 C — 자동: `scripts/pre_deploy_check.py`, `scripts/lint_none_format.py`

**검증 기록 형식**
```
검증 주체: B (pdca-qa) + C (pre_deploy_check / lint_none_format)
확인 항목: 7개 (AC-1~7)
발견 이슈: M개
  - ...
판정: PASS / FAIL / 조건부 PASS
```

## 6. 회고

- **결과**: (미완)
- **한 줄 회고**:
- **후속 조치**:
