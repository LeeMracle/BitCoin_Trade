# Type=notify 전환 시 TimeoutStartSec 상향 필수

- **발생일(KST)**: 2026-04-17 14:30
- **관련 WBS**: P7-05 (systemd WatchdogSec + sd_notify 연동)
- **증상 심각도**: MEDIUM (배포 1회 실패 + 수동 재배포 필요)

## 발생한 오류

P7-05로 `config/btc-trader.service`를 `Type=simple` → `Type=notify` + `WatchdogSec=300`으로 전환하고 AWS 배포. 초기 배포 직후 서비스가 바로 kill되고 restart 루프 발생.

원인: systemd 기본값 `TimeoutStartSec=90s`는 `Type=notify` 모드에서 **프로세스가 `sd_notify("READY=1")`을 보낼 때까지의 허용 시간**으로도 쓰인다. 반면 realtime_monitor 부팅 시 202개 종목의 레벨 갱신 루프에 **약 4분**이 소요되며 `_sd_ready()`는 해당 루프 완료 이후에 호출됨. 결과적으로 90초 안에 READY 신호가 도달하지 못해 systemd가 프로세스를 kill하고 재시작 루프로 진입.

## 원인

1. `Type=simple` 이었을 때는 `TimeoutStartSec=90s`가 거의 무관 (프로세스 exec 직후 started 간주).
2. `Type=notify`로 바뀌면 부팅 완료 판정이 **애플리케이션 측 READY 신호 송신 시점**으로 이동.
3. 서비스 파일에 `TimeoutStartSec`을 명시적으로 올리지 않으면 systemd 기본값 90s가 적용되어, 초기화 시간이 긴 애플리케이션은 전부 kill.

## 수정

- `config/btc-trader.service`에 `TimeoutStartSec=600` 추가 (10분 여유, 향후 종목수 증가 대응).
- `scripts/setup_service.sh` 재실행으로 서비스 교체 + `systemctl daemon-reload`.
- 배포 직후 `journalctl -u btc-trader -n 50`에서 `READY=1` 수신 및 `active (running)` 확인.

## 검증규칙 (pre_deploy_check.py에 추가할 것)

```python
def check_systemd_notify_timeout():
    """Type=notify일 때 TimeoutStartSec이 충분히 큰지 확인."""
    svc = PROJECT_ROOT / "config" / "btc-trader.service"
    if not svc.exists():
        return
    text = svc.read_text(encoding="utf-8")
    if "Type=notify" not in text:
        return
    m = re.search(r"TimeoutStartSec\s*=\s*(\d+)", text)
    if not m or int(m.group(1)) < 300:
        errors.append(
            "[systemd] Type=notify 사용 중이나 TimeoutStartSec이 300초 미만 — "
            "초기화 지연 시 kill/restart 루프 위험. 최소 300s 권장 "
            "(ref: docs/lessons/20260417_2_systemd_notify_timeout_start.md)"
        )
```

## 교훈

systemd `Type` 변경은 타임아웃 의미 자체를 바꾼다. 단순한 설정 변경처럼 보여도 초기화 시간이 긴 서비스는 반드시 `TimeoutStartSec`을 **초기화 소요 시간 × 2 이상**으로 올려야 한다. 프로세스 kill 후 루프로 들어가면 자동 복구가 어렵고, 시장가 주문 중이면 포지션 관리에도 영향을 준다.

관련 교훈: #5(서버 메모리 압박), #9(cron 미등록), #14(CB 로그 스팸) — 모두 "설정 한 줄 누락이 서비스 전체 중단"으로 이어진 유형.

## 후속 조치 (04-17 15:30 — P4-14d 조사 결과)

`TimeoutStartSec=600` 반영 후에도 운영 중 1시간 내 **2회 연속 Watchdog SIGABRT 재발**(05:57, 06:06 UTC). 원인은 **운영 중 WATCHDOG ping 주기가 `WatchdogSec`과 동일**이어서 경계 조건에 걸린 것과, **`_refresh_levels()` 4분 블로킹 동안 ping 공백**이었음. 2가지 추가 조치:

1. **heartbeat 주기 300s → 120s**로 단축 (`realtime_monitor.py:668`) — systemd 권장 "타임아웃의 절반 미만".
2. **`_refresh_levels()` 진행 로그 블록(10건마다)에서 `_sd_watchdog()` 호출 추가** — 4분 블로킹 중에도 ping 유지.

### 확장된 검증규칙 (pre_deploy_check.py)

`check_service_watchdog_sec()`에 `TimeoutStartSec < 300` ERROR 승급 완료. 추가로 다음 정적 검증 권장 (후속 P6-xx로 분리):

```python
def check_refresh_levels_watchdog():
    """_refresh_levels() 내부에 _sd_watchdog 호출이 있는지."""
    rt = (PROJECT_ROOT / "services/execution/realtime_monitor.py").read_text(encoding="utf-8")
    m = re.search(r"async def _refresh_levels.*?(?=\n    async def |\n    def |\nclass |\Z)",
                  rt, re.DOTALL)
    if m and "_sd_watchdog" not in m.group(0):
        errors.append(
            "[P7-05] _refresh_levels() 내부에 _sd_watchdog() 호출 누락 — "
            "4분 블로킹 중 Watchdog timeout 위험 (lessons/20260417_2)"
        )
```

