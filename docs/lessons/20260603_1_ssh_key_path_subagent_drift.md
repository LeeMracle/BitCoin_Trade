# lessons #35 — subagent SSH 키 경로 추측 → `Permission denied (publickey)` → 정량 진단 차단

- 날짜: 2026-06-03 KST
- 분류: 운영 / 에이전트 환경 / SSH 표준
- 관련 lessons: #18 (venv 경로 drift), #20 (다중 키 환경 매핑), #24 (다중 프로젝트 SSH 분리)

## 현상

2026-06-03 18:00 KST, expert(`bata-investment-expert`) subagent가 P2 무수익 진단(5연패 cooldown 원인 분해)에서
AWS BATA 서버(`13.124.82.122`)의 closed_trades / ml_shadow 정량 데이터 회수를 시도했으나
`Permission denied (publickey)`로 거부됨. 정량 분석이 막혀 PM에게 escalation.

- 메인 세션(engineer/operator)은 동일 키로 SSH OK
- 동일 서버, 동일 사용자(ubuntu), 동일 IP
- 키 파일 자체는 `C:\Users\Administrator\Downloads\upbit-trading-key-seoul.pem`에 정상 존재
- authorized_keys 정상 (서버 측 변경 없음)

## 재현 절차

1. subagent 세션 시작 (별도 ssh-agent / 별도 `$HOME` 환경)
2. `ssh ubuntu@13.124.82.122` (PEM `-i` 미명시) 실행
3. → `Permission denied (publickey)` (subagent에 등록된 기본 키가 매칭 안 되거나 키 자체가 없음)
4. 또는 추측 경로 (`~/.ssh/aws_bata_key`, `~/.ssh/id_rsa` 등) 시도 → 동일 거부

## 근본 원인

**1줄**: SSH 접속 표준(키 절대경로 / 사용자명 / 호스트)이 코드(`deploy_to_aws.sh`)에만 박혀 있고
별도 문서로 등재되어 있지 않아, subagent / 신규 세션이 키 경로를 **추측**하다 거부당함.

상세:
- canonical 키는 `$HOME/Downloads/upbit-trading-key-seoul.pem` (deploy_to_aws.sh 14행)
- 메인 세션은 `$HOME = /c/Users/Administrator`로 정상 매핑
- subagent / CI 세션은 (a) `$HOME` 미상속 또는 (b) 사용자 홈 다름 또는 (c) 표준 경로 모르고 추측
- `~/.ssh/config`도 미작성 → 자동 매핑 실패
- `~/.ssh/`에는 PEM 키 없음 (`known_hosts`만)
- SSH 표준이 "코드에만" 존재 = `agents/team.yaml`, `docs/`에 등재되지 않음 → subagent가 발견 불가

## 핵심 교훈

- 운영 자원(SSH 키 / API 키 / DB 접속)의 **canonical 경로는 코드 외 1차 문서에 등재 필수**
  — 코드에만 박히면 새 세션/신규 에이전트가 추측하다 silent fail
- subagent는 환경변수(`$HOME` 등) 상속 보장 없음 → 절대 경로 권장, `$HOME` 사용 시 표준 명시
- lessons #20(키↔환경 매핑)과 동일 원리 — SSH 키도 "환경↔경로" 매핑 표준 없으면 silent fail
- "메인 세션 OK"라고 환경 표준 미문서화 방치 시, expert / operator subagent에서 반드시 막힘

## 수정 (코드 + 운영 조치)

| 영역 | 조치 | 파일 |
|---|---|---|
| 문서 | SSH 접속 표준(키/유저/포트/명령 예시) 신설 | `docs/ssh_access.md` |
| 검증 | pre_deploy_check에 canonical 키 + 표준 문서 + deploy 일치 검증 룰 추가 | `scripts/pre_deploy_check.py::check_ssh_canonical_key()` |
| 정량 데이터 회수 | 메인 세션에서 회수 완료(`output/expert_data_20260603/`) — expert 진단 unblock | — |

복구된 데이터:
- `output/expert_data_20260603/multi_trading_state.json` (closed_trades 23건, 마지막 STEEM 5/22 -8.33%)
- `output/expert_data_20260603/vb_state.json`
- `output/expert_data_20260603/ml_shadow/` (5/4~5/16, 5/14 71만 byte 포함)

> 참고: 사용자가 잘못 알고 있던 파일명(`closed_trades.json`, `ml_outcomes.jsonl`)은 실제 존재하지 않음.
> closed_trades는 `multi_trading_state.json["closed_trades"]` 배열, ml outcomes는 `workspace/ml_shadow/YYYYMMDD.jsonl` 안. `docs/ssh_access.md` §5 등재 완료.

## 검증규칙 (pre_deploy_check 추가)

`scripts/pre_deploy_check.py::check_ssh_canonical_key()`:
1. canonical PEM (`$HOME/Downloads/upbit-trading-key-seoul.pem`) 존재 확인 (없으면 WARNING — subagent/CI 환경 고려)
2. Linux/Mac 권한 0400/0600 검증 (Windows는 ACL 위임)
3. `docs/ssh_access.md` 존재 ERROR 가드 — 표준 문서 폐기 시 즉시 알람
4. `scripts/deploy_to_aws.sh`가 canonical 파일명(`upbit-trading-key-seoul.pem`) 참조 — 키명 임의 변경 차단

## 회귀 방지 체크

- [x] 표준 문서 등재: `docs/ssh_access.md`
- [x] pre_deploy_check 룰 1개 추가: `check_ssh_canonical_key()`
- [x] 정량 데이터 회수 완료 → expert 진단 unblock
- [x] CLAUDE.md 주요 교훈 표 갱신

## 관련 lessons

- #18 (`20260425_1_crontab_venv_path_drift.md`) — 경로 drift 동일 패턴 (venv ↔ crontab/systemd)
- #20 (`20260502_1_upbit_keyset_ip_mapping.md`) — 키↔환경 매핑 미명시 silent fail
- #24 (`20260504_1_zombie_processes_crontab_overwritten_bak_dirs.md`) — 다중 프로젝트 SSH 동거 환경 주의
