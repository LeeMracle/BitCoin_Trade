# lessons #27: systemd 재시작은 좀비 cron 인스턴스를 죽이지 않는다 — 옛 코드 알림 발사 + crontab 통째 갱실

- **발생일(KST)**: 2026-05-06
- **분류**: 운영 회귀 / 다중 프로젝트 동거 (lessons #17 #24 연관)
- **선행 lessons**: #17, #18, #24, #20

## 사건 요약

코드 변경 + systemd `restart btc-trader` 한 후에도 **State ↔ Exchange 불일치 알림이 옛 메시지(2회 연속) 형식으로 계속 발생**.

진단 결과:
1. 정상 봇(PID 212753) 외 **좀비 3개**(147121, 173929, 206053)가 BitCoin_Trade cwd로 가동 중
2. 좀비는 매일 00:05 UTC ubuntu cron으로 fork된 `daily_live.py` (no `--realtime`) 인스턴스로, **종료되지 않고 누적**
3. 좀비는 옛 코드(REQUIRED_CONSEC=2) 메모리 보유 → 새 코드(=3) 변경 무시
4. 알림 메시지에 PID 식별자 부재 → 어느 프로세스가 발신했는지 즉시 판별 불가
5. **추가 발견**: ubuntu crontab의 BitCoin_Trade cron 9개가 어느 시점 통째 사라짐 (Stock_Trade만 남음). watchdog/critical_healthcheck 모두 비활성 = 매우 위험 (lessons #20 8h 무감지 사고 재현 위험)

## 원인

1. **`daily_live.py` (no `--realtime`)가 종료되지 않고 무한 루프**:
   - cron이 매일 00:05에 `daily_live.py` 호출 → 종료 가정
   - 그러나 실제는 종료 안 함 → systemd btc-trader.service의 `--realtime` 인스턴스와 별도 프로세스 누적
   - lessons #24 (`--realtime` 케이스)와 유사하지만 **non-realtime 케이스도 동일 문제**

2. **알림 메시지 식별자 부재**:
   - `notify_error()` 메시지에 발신 PID/instance 표시 X
   - 좀비/정상 구분에 30분+ 진단 시간 소요

3. **crontab 통째 갱신으로 BitCoin_Trade 라인 소실**:
   - Stock_Trade 또는 다른 자동화가 `crontab -l | ... | crontab -` 패턴으로 갱신할 때 BitCoin_Trade 라인 미보존
   - lessons #24 (grep -v 위험) 변형 — Stock_Trade가 자체 cron만 보존하고 다른 라인 모두 삭제

## 수정 (2026-05-06)

1. **좀비 3개 + wrapper bash 6개 종료** (kill, PID 212753 보호)
2. **`/tmp/bata_state_mismatch_pending` 클리어**
3. **`deploy_to_aws.sh` 재실행** → BitCoin_Trade cron 9개 복구
4. **lessons 본 문서 작성**
5. **pre_deploy_check.py 룰 추가** (아래 §검증규칙)

## 검증규칙 (자동화)

`scripts/pre_deploy_check.py`에 추가:

```python
def check_zombie_bot_processes() -> None:
    """BitCoin_Trade cwd로 가동 중인 daily_live.py 인스턴스가 1개 (--realtime)뿐인지 검증.
    좀비 누적 = lessons #27 회귀.
    """
    import subprocess
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", "daily_live.py"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except subprocess.CalledProcessError:
        return  # 매치 없음 = 정상 (로컬 검증 시)
    except Exception:
        return

    lines = [l for l in out.strip().split("\n") if "BitCoin_Trade" in l]
    if not lines:
        return  # 로컬 (BitCoin_Trade 프로세스 없음)
    realtime = [l for l in lines if "--realtime" in l]
    non_realtime = [l for l in lines if "--realtime" not in l]
    if len(realtime) > 1:
        errors.append(f"[좀비] daily_live.py --realtime {len(realtime)}개 (1개여야 함, lessons #27)")
    if non_realtime:
        warnings.append(f"[좀비] daily_live.py (no --realtime) {len(non_realtime)}개 — 누적 좀비 의심")
```

## 교훈

1. **`daily_live.py` (no `--realtime`)도 좀비 누적 가능** — lessons #24 (`--realtime`)에 추가로 non-realtime 케이스 검증 필요
2. **알림 메시지에 PID/식별자 자동 prefix 권장** — `notify_error(f"[PID={os.getpid()}] {msg}")` 또는 데코레이터
3. **crontab 통째 갱신 시 다른 프로젝트 라인 보존 책임** — Stock_Trade가 ubuntu crontab 갱신할 때 BitCoin_Trade 라인 보존하도록 협업 필요 (별도 user 분리도 옵션)
4. **systemd ExecStartPre로 좀비 학살** 권장 (옵션):
   ```
   ExecStartPre=/usr/bin/pkill -f "daily_live.py" || true
   ExecStartPre=/bin/sleep 2
   ```
   단, `--realtime` 자기 자신도 죽일 수 있어 신중. PID 검증 필요.
5. **다중 프로젝트 동거 환경**(lessons #17)에서 별 사용자 격리 검토 (BitCoin_Trade는 `bata` user, Stock_Trade는 `stock` user 분리)

## 참조
- 위배된 lessons: [#24 좀비 프로세스](20260504_1_zombie_processes_crontab_overwritten_bak_dirs.md)
- 다중 프로젝트: [#17 다중 프로젝트 프로세스 오진](20260421_1_multi_project_process_misdiagnosis.md)
- crontab 갱신: [#18 venv 경로 회귀](20260425_1_crontab_venv_path_drift.md)
- 헬스체크 단명: [#20 키↔IP 매핑](20260502_1_upbit_keyset_ip_mapping.md)
