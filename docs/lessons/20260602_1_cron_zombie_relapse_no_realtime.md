# lessons #33 — daily_live.py (no --realtime) cron 재등록으로 좀비 8개 누적 (lessons #24 회귀)

- 날짜: 2026-06-02 22:30 KST
- 분류: ops / cron / zombie
- 관련 lessons: #24 (좀비/crontab 덮어쓰기), #27 (좀비 봇 옛 코드 알림), #17 (다중 프로젝트 PID 오판 방지)

## 현상

- AWS 13.124.82.122 `pgrep -af daily_live.py` 결과 9개 프로세스
  - 정상 1개: PID 891532 `daily_live.py --realtime` (systemd btc-trader, 2026-06-02 00:42 시작)
  - 좀비 8개: 4개 쌍(bash wrapper + python child) — 5/25, 5/26, 5/29, 5/30 00:05:01 시작
- operator가 `pgrep` 결과로 이상 1번 보고: "좀비 daily_live.py 4개"
- 봉 매매 자체 영향은 없으나 옛 코드 메모리로 알림/주문 동작 가능성 (lessons #27 정확한 패턴)

## 근본 원인

crontab에 `5 0 * * * cd /home/ubuntu/BitCoin_Trade && .venv/bin/python scripts/daily_live.py >> /var/log/btc_trader.log 2>&1` 라인 존재.

- systemd `btc-trader.service`가 `daily_live.py --realtime`을 항상 실행 중인 환경에서
- cron은 매일 00:05 UTC (09:05 KST)에 **별도 인스턴스**를 추가로 띄움 (no --realtime, 일회성 의도였을 것)
- 일회성 작업도 매일 새 PID 추가 — 일부는 종료되지 않고 누적 (lessons #24 정확한 시나리오)

왜 lessons #24의 검증을 통과했나:
- pre_deploy_check `check_zombie_bot_processes()`는 non-realtime을 **warning 수준**으로 처리 → 배포 차단 못 함
- deploy_to_aws.sh가 `daily_live.py`를 cron으로 등록한다면 회귀 발생 (확인 필요)

## 핵심 교훈

1. **systemd로 가동하는 장시간 스크립트는 cron에서 절대 호출 금지** (lessons #24 강화). non-realtime 매일 호출도 좀비 누적 위험.
2. 좀비 검증은 **count 임계** 도입 필요. non_realtime ≥ 3 또는 합계 ≥ 3은 ERROR로 승격.
3. operator의 보고("4개")는 실제로 8개(bash + python 쌍). pgrep 결과 해석 시 wrapper/child 분리 인식 필요.
4. SUN/KRW state는 operator가 의심한 "qty=0" 패턴(lessons #32)이 아님. **state는 거래소와 완벽 일치** (660.79 = 660.79). 운영 보고 시 raw state JSON 확인 후 lessons #32 패턴 판단 권장.

## 수정 (코드 + 운영 조치)

### 운영 (2026-06-02 22:39 즉시)
1. `crontab -l > /tmp/crontab.bak.20260602_2239` 백업
2. `crontab -l | grep -v "scripts/daily_live.py >> /var/log/btc_trader.log" | crontab -` — 좀비 유발 cron 라인 제거
3. crontab BitCoin 라인 9개 → 8개 (lessons #32 baseline ≥ 8 충족)
4. 좀비 8개 cwd 재검증 후 (lessons #17) `kill -TERM` 일괄 정리. 모두 SIGTERM으로 정상 종료.
5. systemd `btc-trader.service`는 보호 (active 유지, PID 891532)
6. state 백업: `multi_trading_state.json.bak.20260602_2239`

### 코드 (pre_deploy_check 룰 강화)
- `check_zombie_bot_processes()` 강화: non_realtime ≥ 3 시 ERROR로 승격 (이 lessons에서 8개 발생)
- `check_deploy_no_daily_live_cron()` 신규: deploy_to_aws.sh에 `daily_live.py`(no --realtime) cron 등록 라인이 있으면 ERROR

## 검증규칙 (pre_deploy_check 추가)

```python
def check_zombie_bot_processes() -> None:
    # 기존 로직 + 누적 좀비 임계 ERROR 승격
    if len(non_realtime) >= 3:
        errors.append(f"[좀비] daily_live.py (no --realtime) {len(non_realtime)}개 — 누적 좀비 ERROR (lessons #33)")
    elif non_realtime:
        warnings.append(...)

def check_deploy_no_daily_live_cron() -> None:
    """deploy_to_aws.sh에 daily_live.py (no --realtime) cron 등록 시 ERROR.
    배경: 2026-06-02 lessons #33 — cron 호출이 매일 좀비 1개씩 생성. systemd 단독 가동만 허용.
    """
    deploy = PROJECT_ROOT / "scripts" / "deploy_to_aws.sh"
    if not deploy.exists():
        return
    content = deploy.read_text(encoding="utf-8")
    # daily_live.py를 cron으로 등록하면서 --realtime 미사용 시 차단
    for line in content.splitlines():
        if "daily_live.py" in line and "--realtime" not in line and "crontab" not in line.lower():
            # cron format인 분/시 필드 매칭 (예: "5 0 * * *")
            import re
            if re.search(r"^\s*\d+\s+\d+\s+\*\s+\*\s+\*", line) or "cron" in line.lower():
                errors.append(
                    "[lessons #33] deploy_to_aws.sh에 daily_live.py (no --realtime) cron 등록 잔존 — "
                    "좀비 누적 위험 (systemd 단독 가동만 허용)"
                )
                return
```

추가로 운영에서 **매주 좀비 카운트 헬스체크** 필요 (operator 위임).

## 관련 lessons

- #17 다중 프로젝트 PID 오판 방지 — /proc/cwd 검증 (이번에도 적용, BitCoin_Trade만 정리)
- #24 좀비/crontab 덮어쓰기 — systemd 단독 원칙 (이 lessons에서 cron 재등장 = 회귀)
- #27 좀비 봇 옛 코드 알림 — 같은 위험 표면, 다른 원인(이번은 cron, #27은 자동 종료 누락)
- #32 G6 패치 cron baseline ≥ 8 — 본 정리 후에도 8 충족
