# lessons #36 — BitCoin_Trade crontab 전면 소실 재발 (Stock_Trade 원자 덮어쓰기)

- 날짜: 2026-08-01 18:05 KST
- 분류: ops / cron / 다중 프로젝트 인프라 충돌
- 관련 lessons: #24(다중 프로젝트 crontab 덮어쓰기), #31(deploy 우회 cron silent fail), #32(cron baseline ≥8), #33(daily_live cron 좀비)

## 현상

- 2026-08-01 18:05 KST PM 정기 점검 시 발견:
  - `ssh ... "crontab -l | grep -c BitCoin_Trade"` = **0** (기대 baseline ≥ 8, lessons #32 룰 위배)
  - `/var/log/critical_healthcheck.log` mtime = **2026-07-29 14:05 KST**에서 정지 (3일 4시간 무기록)
  - 봇 프로세스 alive (systemd `btc-trader.service` active, PID 2195995)
  - 좀비 0개, state 정합 (positions=[], cooldown_until=0)
  - REGIME 필터 ON으로 신규 진입 억제 중이라 손실 확대는 없었으나, **사고 알람 채널 자체가 죽어 있던 상태**
- 즉 `/var/log/critical_healthcheck.log`가 침묵한 3일 동안 인증 실패·잔고 이상이 발생했다면 감지 불가.

## 근본 원인 (확정)

### Stock_Trade `deploy_aws.sh`가 crontab을 파일 통째로 덮어씀

- 서버 `/home/ubuntu/Stock_Trade/scripts/deploy_aws.sh:128` — `crontab $REMOTE_DIR/config/crontab.txt`
- 이 방식은 **원자 갱신**이라 기존 crontab을 통째로 파일 내용으로 교체 → BitCoin_Trade 라인이 보존될 여지가 전혀 없음
- BitCoin_Trade `deploy_to_aws.sh`는 `crontab -l | grep -v ... | crontab -` 방식으로 다른 프로젝트 라인 보존을 시도하지만, Stock_Trade는 그 반대 방향

### 왜 lessons #24 룰이 회귀 방지에 실패했나

- lessons #24/#32의 `check_btc_cron_count_baseline()`은 **`deploy_to_aws.sh` 파일 내부의 `CRON_xxx` 변수 카운트**만 검증 (로컬 정적 검사)
- 실제 서버 crontab이 어떻게 되어 있는지는 검증 안 함 → Stock_Trade가 서버에서 덮어쓰면 감지 불가
- pre_deploy_check는 로컬 PASS, 서버 상태는 소실 상태 → 로컬 정적 검사만으로는 다른 프로젝트가 서버에서 벌이는 파괴 행위를 원천 방어 불가

### 정확한 소실 시점

- `critical_healthcheck.log` 최종 mtime = 2026-07-29 14:05 KST (05:05 UTC)
- syslog(/var/log/syslog)는 3일치만 보존 → 07-29 시점 crontab 재설치 이력 확인 불가
- 정황상 07-29 05:05 UTC 이후 Stock_Trade `deploy_aws.sh` 실행이 원인. cron 라인 자체가 사라졌으므로 07-29 05:05 실행분이 마지막

## 핵심 교훈

1. **로컬 pre_deploy_check 만으로는 다중 프로젝트 crontab 충돌 방어 불가**. 스크립트 소스는 정상이어도 서버에서 다른 프로젝트가 덮어쓰면 무력. 배포 후 **서버 실측 검증**이 필수.
2. **배포 성공 = 서버 반영 확인까지**. `deploy_to_aws.sh` 마지막 단계에 `ssh "crontab -l | grep -c BitCoin_Trade"` 자동 체크 + baseline 미만이면 exit 1.
3. **critical_healthcheck 자체의 침묵도 감지 필요**. 지금 구조는 healthcheck가 "정상 발견"할 때만 로그 기록 → cron 자체가 죽으면 감지 완전 불가. 별도 heartbeat 파일(`/tmp/critical_hc_heartbeat`) mtime N시간 초과 감시 룰이 있어야 self-monitoring 성립.
4. **다중 프로젝트 공존 서버는 crontab을 파일 원자 갱신 금지 표준**. Stock_Trade `deploy_aws.sh`도 `grep -v Stock_Trade | cat - crontab.txt | crontab -` 형태로 리팩터링 필요 (본 프로젝트 범위 밖이지만 해당 프로젝트 CLAUDE에 issue 등록 권고).

## 수정 (코드 + 운영 조치)

### 운영 (2026-08-01 18:14 KST 즉시)
1. 로컬 `pre_deploy_check.py` 실행 → PASS
2. `bash scripts/deploy_to_aws.sh` 실행 → BitCoin_Trade cron 8개 복원
3. 서버 검증: `crontab -l | grep -c BitCoin_Trade` = **8** (baseline 충족)
4. Stock_Trade cron 보존 확인: 15개 (덮어쓰기 후 재추가 방식 정상 동작)
5. critical_healthcheck 수동 1회 실행 → 로그 신규 append 확인 (KRW 262,486 / total 262,751)

### 코드 (pre_deploy_check 신규 룰)

`scripts/pre_deploy_check.py`에 `check_deploy_post_check_remote_cron()` 추가:

- `deploy_to_aws.sh`의 마지막(crontab 등록 이후) 섹션에 `ssh ... crontab -l | grep -c` 실측 검증 라인이 있는지 정적 검사
- 없으면 ERROR — 로컬 정적 검사만으로는 lessons #36 회귀 방지 불가

또한 `deploy_to_aws.sh` 자체에 사후 실측 게이트 추가:

```bash
# 사후 실측 검증 (lessons #36 신설)
REMOTE_CRON_COUNT=$($SSH_CMD "crontab -l 2>/dev/null | grep -c BitCoin_Trade")
if [ "$REMOTE_CRON_COUNT" -lt 8 ]; then
    echo "❌ 배포 후 서버 crontab BitCoin_Trade 라인 $REMOTE_CRON_COUNT (< 8 baseline) — lessons #36 회귀"
    exit 1
fi
echo "[OK] 사후 실측: BitCoin_Trade cron $REMOTE_CRON_COUNT 라인 등록 확인"
```

## 검증규칙 (pre_deploy_check 추가)

```python
def check_deploy_post_check_remote_cron() -> None:
    """deploy_to_aws.sh에 사후 SSH 실측 검증(BitCoin_Trade cron ≥ 8) 라인이 있는지.

    배경 (lessons #36, 2026-08-01):
        Stock_Trade deploy_aws.sh가 crontab을 파일 원자 갱신 방식(`crontab config/crontab.txt`)
        으로 통째 덮어써서 BitCoin_Trade cron 8개 전면 소실.
        로컬 정적 검사(check_btc_cron_count_baseline)는 PASS였음 — 스크립트 소스는 정상.
        배포 성공 = 서버 반영 확인까지. deploy_to_aws.sh 마지막에 ssh 실측 게이트 필수.
    """
    d_path = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not d_path.exists():
        return
    txt = d_path.read_text(encoding="utf-8")
    # SSH_CMD (ssh -i ...) 로 crontab -l | grep -c BitCoin_Trade 실측 라인이 있는지
    has_remote_check = ("crontab -l" in txt and "grep -c BitCoin_Trade" in txt) or \
                       ("crontab -l" in txt and "grep -c \"BitCoin_Trade\"" in txt)
    if not has_remote_check:
        errors.append(
            "[lessons #36] deploy_to_aws.sh에 사후 SSH 실측 검증 라인 부재 — "
            "'crontab -l | grep -c BitCoin_Trade' 게이트 필수 "
            "(로컬 정적 검사만으로는 다중 프로젝트 crontab 덮어쓰기 방어 불가)"
        )
```

## 관련 lessons

- #24 다중 프로젝트 crontab 덮어쓰기 (본 사고의 반복)
- #31 deploy 우회 cron silent fail (본 사고는 우회가 아닌 다른 프로젝트 원자 덮어쓰기)
- #32 cron baseline ≥ 8 (로컬 검사 통과에도 실측 소실 = 정적 검사 한계)
- #33 daily_live cron 좀비 (다중 프로젝트 공존 환경의 다른 형태 사고)

## 후속 권고

- Stock_Trade 프로젝트에 issue 등록: `deploy_aws.sh` crontab 원자 갱신 → 병합 방식(다른 프로젝트 라인 grep -v 후 자기 라인 append) 리팩터링 요구
- 매시 실행되는 `critical_healthcheck.py` 자체 heartbeat 파일 신설 검토 — self-monitoring 부재로 3일 침묵 무감지 재발 위험
- daily-work 스킬 세션 시작 시 `crontab -l | grep -c BitCoin_Trade ≥ 8` 자동 점검 (operator 위임)
