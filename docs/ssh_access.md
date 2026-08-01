# BATA AWS 서버 SSH 접속 표준

> subagent / operator / expert가 "키 경로 추측" 없이 즉시 사용할 수 있도록 canonical 경로를 등재.
> 변경 시 `scripts/deploy_to_aws.sh`, `scripts/hotfix_deploy.sh`, `scripts/hourly_monitor.py`도 함께 갱신.

## 1. 서버 정보 (canonical)

| 항목 | 값 |
|---|---|
| Host (IP) | `13.124.82.122` |
| Region | ap-northeast-2 (Seoul) |
| Instance | t3.micro (Ubuntu 24.04) |
| User | `ubuntu` |
| Port | `22` (기본) |
| Project Dir | `/home/ubuntu/BitCoin_Trade/` |
| Python venv | `/home/ubuntu/BitCoin_Trade/.venv/bin/python` |

## 2. SSH 키 표준 경로

| 환경 | PEM 파일 절대 경로 |
|---|---|
| 로컬 PC (Master, Windows) | `C:\Users\Administrator\Downloads\upbit-trading-key-seoul.pem` |
| Git Bash 표기 | `/c/Users/Administrator/Downloads/upbit-trading-key-seoul.pem` |
| `$HOME` 기반 표기 | `$HOME/Downloads/upbit-trading-key-seoul.pem` |

> deploy/hotfix 스크립트는 `$HOME/Downloads/upbit-trading-key-seoul.pem`를 사용. `$HOME` 미설정 환경에서는 절대 경로 사용.

## 3. 표준 접속 명령 (그대로 복사 사용)

```bash
PEM="$HOME/Downloads/upbit-trading-key-seoul.pem"
ssh -i "$PEM" -o StrictHostKeyChecking=no ubuntu@13.124.82.122
```

또는 절대 경로:
```bash
ssh -i /c/Users/Administrator/Downloads/upbit-trading-key-seoul.pem -o StrictHostKeyChecking=no ubuntu@13.124.82.122
```

## 4. 자주 쓰는 명령

```bash
PEM="$HOME/Downloads/upbit-trading-key-seoul.pem"
HOST="ubuntu@13.124.82.122"
REMOTE_DIR="/home/ubuntu/BitCoin_Trade"

# 상태 조회
ssh -i "$PEM" "$HOST" "cd $REMOTE_DIR && .venv/bin/python -m services.execution.trader --status"

# 로그 tail
ssh -i "$PEM" "$HOST" "tail -50 /var/log/btc_trader.log"
ssh -i "$PEM" "$HOST" "tail -50 /var/log/critical_healthcheck.log"

# crontab 확인
ssh -i "$PEM" "$HOST" "crontab -l | grep -i bitcoin"

# state 파일 회수 (정량 분석용)
scp -i "$PEM" "$HOST:$REMOTE_DIR/workspace/multi_trading_state.json" ./output/
scp -i "$PEM" "$HOST:$REMOTE_DIR/workspace/vb_state.json"            ./output/
scp -i "$PEM" -r "$HOST:$REMOTE_DIR/workspace/ml_shadow"              ./output/
```

## 5. 정량 데이터 위치 (서버 기준)

| 항목 | 경로 | 비고 |
|---|---|---|
| 메인 state (closed_trades 포함) | `workspace/multi_trading_state.json` | `state["closed_trades"]` 배열 |
| VB state | `workspace/vb_state.json` | volume breakout 봇 상태 |
| ML shadow 의사결정 | `workspace/ml_shadow/YYYYMMDD.jsonl` | 일자별. outcome 매칭 결과 포함 |
| trader 로그 | `/var/log/btc_trader.log` | 거래 실행 로그 |
| report 로그 | `/var/log/btc_report.log` | 9분 cycle 리포트 |
| critical healthcheck | `/var/log/critical_healthcheck.log` | 매시 5분 cron |

> 참고: 별도 `closed_trades.json` 파일은 **없음** — `multi_trading_state.json["closed_trades"]` 키 안에 들어 있음.
> 별도 `ml_outcomes.jsonl` 파일도 **없음** — `workspace/ml_shadow/YYYYMMDD.jsonl`에 outcome 라인이 포함됨 (`services/ml/outcome_matcher.py` 참조).

## 6. 거부 시 (`Permission denied (publickey)`) 체크리스트

1. **PEM 파일 절대경로 명시했는가?** (`-i $PEM`) — subagent 환경은 `~/.ssh/config` 자동 매핑 안 됨
2. **PEM 파일 존재 확인**: `ls -la "$PEM"`
3. **사용자명 `ubuntu` 맞는가?** (`ec2-user` 아님, AL2가 아니라 Ubuntu)
4. **권한 (Linux/Mac만)**: `chmod 400 "$PEM"` (Windows ACL은 OS가 관리)
5. **로컬 메인 세션에서는 OK인데 subagent에서 실패** → 환경변수 `$HOME` 미상속 가능, 절대경로 사용

## 7. 금지 사항 (안전 가드)

- 새 SSH 키 생성/배포는 Master 승인 필수 (임의 금지)
- `authorized_keys` 임의 변경 금지
- 키 파일 내용/사본을 텔레그램/로그/git에 기록 금지
- `output/` 등 git untracked 위치 외에 키 보관 금지

## 8. 관련 lessons

- lessons #18 — venv 경로 drift (인터프리터 절대경로 동시 갱신 의무)
- lessons #20 — 다중 API 키 환경 매핑 미명시는 silent fail (이 문서는 동일 원리를 SSH 키에 적용)
- lessons #35 (`20260603_1`) — subagent SSH 키 경로 추측 → Permission denied 차단 (본 문서 신설 계기)

## 9. pre_deploy_check 검증 룰

`scripts/pre_deploy_check.py::check_ssh_canonical_key()`:
- canonical PEM 경로(`$HOME/Downloads/upbit-trading-key-seoul.pem`) 존재 여부
- 본 문서(`docs/ssh_access.md`) 존재 여부 (없으면 ERROR — subagent 표준 경로 부재 = lessons #35 위배)
- `scripts/deploy_to_aws.sh`가 canonical PEM 파일명 참조 여부

본 문서 폐기/이동 금지 — pre_deploy_check가 의존.
