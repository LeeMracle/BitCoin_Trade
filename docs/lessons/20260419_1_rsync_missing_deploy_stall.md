# deploy_to_aws.sh — 로컬 rsync 미설치로 배포 중단

- **발생일**: 2026-04-19
- **심각도**: MEDIUM
- **카테고리**: 배포 / 도구 의존성

## 증상

`bash scripts/deploy_to_aws.sh` 실행 시 `[2/5] 파일 동기화…` 단계에서 즉시 실패:

```
scripts/deploy_to_aws.sh: line 49: rsync: command not found
```

`set -e`로 전체 파이프라인이 중단되어 Python 환경 재설정·crontab 등록·dry-run 테스트가 모두 스킵됨. fg_gate 버그 픽스(6bd00a8) 배포가 지연될 뻔함.

## 원인

로컬 환경이 Windows + Git Bash이고, Git for Windows 기본 배포판에는 `rsync`가 포함되지 않음. 배포 스크립트는 rsync 존재를 암묵적 전제로 작성되어 있었고 `pre_deploy_check.py`도 배포 시 쓰이는 로컬 도구(rsync/ssh/scp/tar)의 가용성을 검증하지 않음.

이전 배포(04-18)는 별도 환경에서 수행되었거나 수동 복구 경로가 있었으나 기록되지 않아 같은 환경에서 재시도 시 재발.

## 수정

- [x] `scripts/deploy_to_aws.sh`에 tar|ssh 폴백 경로 추가:
  ```
  if command -v rsync >/dev/null 2>&1; then rsync ...
  else tar czf - --exclude=... -C "$LOCAL_DIR" . | ssh ... "tar xzf - -C $PROJECT_DIR"
  fi
  ```
  exclude 목록은 배열로 공통화하여 rsync/tar 양쪽에 그대로 전달.
- [x] fg_gate fix 배포는 우선 scp 3파일로 수동 완료(132d9ee/6bd00a8 시점).
- [x] 본 lesson 기록 + `pre_deploy_check.check_deploy_tooling` 추가.

## 검증 규칙

1. `scripts/deploy_to_aws.sh`에 `command -v rsync` 분기가 존재해야 한다 (rsync 없어도 배포 가능 경로 유지).
2. `scripts/deploy_to_aws.sh`에 tar 폴백이 `-C "$LOCAL_DIR" .` 형태로 작성되어 있어야 한다 (GNU tar 옵션 순서).
3. `pre_deploy_check.py`의 `check_deploy_tooling`은 로컬 환경에 `ssh` 와 (`rsync` 또는 `tar`) 중 하나가 존재하는지 WARN/ERROR로 보고해야 한다.

## 교훈

자동화 스크립트가 전제하는 **로컬 측 도구**도 `pre_deploy_check`로 검증해야 한다 (원격 쪽 cron/systemd 검증과 동등한 수준). Windows/Git Bash처럼 "기본 쉘인데 rsync는 없는" 환경은 실제로 존재하므로 `command not found`가 났을 때 즉시 대체 경로로 떨어지도록 스크립트를 방어적으로 작성한다. 관련 선례: lessons/20260408_1 (jarvis cron 미등록), 20260418_2 (log 파일 누락 사일런트 cron 실패).
