# 20260418_2 — `/var/log/` 로그 파일 사전 생성 누락으로 cron이 silent fail

## 발생 일시

- 발견: 2026-04-18 자율 루프 3단계 (cron 등록 후 약 50분 경과, 첫 실행 예정 시점 이후)
- 실제 영향 시작: 각 cron이 처음 배포된 시점부터 (추정 — daily_live.py도 로그 없었을 가능성)

## 배경

`scripts/deploy_to_aws.sh`가 아래 형태로 cron을 등록한다:

```bash
CRON_REGIME="25 * * * * cd $PROJECT_DIR && ... scripts/regime_check.py >> /var/log/regime_check.log 2>&1"
```

배포 후 30분이 지나도 `workspace/regime_state.json`의 `recent_signals`가 여전히 1건에서 증가하지 않음을 확인. `tail /var/log/regime_check.log` → **파일 자체가 존재하지 않음**. 추가 조사 중 **`/var/log/btc_trader.log`, `/var/log/watchdog_check.log` 등도 전부 미존재**임을 발견. 오직 `/var/log/jarvis_executor.log`만 존재(04-09 생성된 것).

## 영향

- **regime_check(매시), vb_recheck_trigger(매일), watchdog_check(매분), daily_live(매일), daily_report(매일) 전부 로그 파일이 없는 상태에서 cron이 `>> /var/log/*.log 2>&1` 리다이렉트를 시도 → 쉘이 에러로 실패하거나, cron이 최소한 stdout/stderr를 버림 → 결과적으로 스크립트 자체가 silent fail하는 결과**.
- jarvis_executor는 04-09에 한 번 로그가 생성된 상태라 append 가능 → 정상 동작.
- **가장 큰 위험**: daily_live.py가 매일 09:05 KST에 돌아야 하는데 log 파일 없음 → 실제로 실행되었는지 현재로선 journalctl(user cron) 외에는 확인 수단이 없음. 매매 로그가 프로젝트에 남지 않은 채 한동안 운영되었을 가능성.
- watchdog_check도 분당 1회 실행되어야 하는데 로그 없음 → heartbeat 경보가 작동했는지 미검증.

## 원인

1. `/var/log/` 디렉토리는 기본적으로 root:root 소유이며 일반 사용자(ubuntu)에게는 **쓰기 권한이 없음**. 기존 파일이 ubuntu 소유로 존재하면 append는 가능하지만, **파일이 없을 때는 ubuntu가 새 파일을 만들지 못함**.
2. cron은 리다이렉트 실패 시 조용히 처리(shell 종료 코드만 반환, 사용자에게 알림 없음).
3. 최초 배포 시 누군가(사용자 또는 setup_service.sh)가 jarvis_executor.log만 생성했고, 나머지는 자동화되지 않았음. 이후 신규 cron이 추가될 때마다 **같은 실수가 반복됨**.
4. 누적된 lessons 중에도 이 패턴이 기록되지 않았음 → 재발 방지 불가.

## 수정

### 즉시 조치(서버)

```bash
sudo touch /var/log/btc_trader.log /var/log/btc_report.log /var/log/watchdog_check.log /var/log/log_volume.log /var/log/vb_recheck_trigger.log /var/log/regime_check.log
sudo chown ubuntu:ubuntu /var/log/btc_trader.log /var/log/btc_report.log /var/log/watchdog_check.log /var/log/log_volume.log /var/log/vb_recheck_trigger.log /var/log/regime_check.log
```

### 재발 방지(deploy 스크립트)

`scripts/deploy_to_aws.sh`의 crontab 등록 바로 앞에 아래 스니펫을 추가:

```bash
LOG_FILES=(/var/log/btc_trader.log /var/log/btc_report.log /var/log/watchdog_check.log /var/log/log_volume.log /var/log/jarvis_executor.log /var/log/vb_recheck_trigger.log /var/log/regime_check.log)
sudo touch "${LOG_FILES[@]}"
sudo chown ubuntu:ubuntu "${LOG_FILES[@]}"
```

이로써 배포 때마다 필요한 로그 파일이 자동 보장됨. 기존 로그는 보존(touch만 하므로 내용 유지).

## 검증규칙

- **R-log-1**: `scripts/deploy_to_aws.sh`에 `sudo touch /var/log/*.log` + `sudo chown ubuntu:ubuntu` 스니펫이 cron 등록 섹션에 **반드시** 포함되어야 한다.
- **R-log-2**: 새로운 cron을 `/var/log/*.log` 리다이렉트로 등록할 때는, 해당 로그 파일도 동시에 LOG_FILES 배열에 추가한다.
- **R-log-3**: pre_deploy_check는 deploy_to_aws.sh 내 `sudo touch /var/log/` 존재 여부를 검증한다. 부재 시 ERROR.

## 교훈 요약

> cron 등록만으로는 실행을 보장하지 못한다. **리다이렉트 경로의 쓰기 가능성까지 배포 스크립트가 책임**져야 한다. "cron에 넣었으니 돌 것이다"는 가정은 로그 한 번 확인으로 무너진다. "로그가 있는가?"는 "cron이 등록되었는가?" 바로 다음에 오는 필수 검증 항목이다.

## 연관 lesson/문서

- [20260408_1_jarvis_cron_missing.md](20260408_1_jarvis_cron_missing.md) — cron 등록 자체의 재발 방지 (금번 교훈은 그 후속 계층: 등록됐는데 로그 없음)
- [20260417_2_systemd_notify_timeout_start.md](20260417_2_systemd_notify_timeout_start.md) — systemd 쪽 유사 교훈 (설정 하나로는 부족)
- [20260418_1_stale_lint_regex_false_warn.md](20260418_1_stale_lint_regex_false_warn.md) — 오늘 메타 교훈 (검증 자체의 오작동)
