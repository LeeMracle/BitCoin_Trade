# #24 — 좀비 daily_live 3중 가동 + crontab 덮어쓰기 + services.bak 잔존

- **사고일**: 2026-05-04 06:18 KST 알림 폭주로 발견 (이미 어제부터 진행)
- **탐지일**: 2026-05-04 06:18 KST (사용자 텔레그램 알림 4건 후 보고)
- **plan**: 2개 plan 거쳐 진단 → 직접 정리

## 1. 무엇이 일어났나

### 1.1 좀비 프로세스 3중 가동
```
PID 106185  May02부터 가동 (1일 21시간 누적, 6:57:02 CPU)
PID 119250  00:05부터 가동 (21시간 누적, 3:41:31 CPU)
PID 139559  21:11부터 가동 (정상 systemd btc-trader.service Main PID)
```
3개가 모두 `daily_live.py --realtime`을 실행 → 각각 별도 `_hourly_sync` 돌리며 차집합 알림 발송 → **race condition 다중 발화 + 텔레그램 폭주**.

PID 106185, 119250은 PPID(부모 bash)가 cron으로 시작된 흔적 (`bash -c "cd ... && python scripts/daily_live.py >> /var/log/btc_trader.log"`). 정확한 등록 시점/원인은 추적 불가(crontab 이미 사라짐), but daily_live.py를 cron으로 호출하는 부적절한 패턴.

### 1.2 crontab BitCoin 라인 9건 → 0건 사라짐
```
/tmp/crontab_v2_20260501_232614.bak: BitCoin 9건
현재 crontab: BitCoin 0건 (Stock_Trade + KATA-M만 잔존)
```
plan 20260502/20260503에서 추가한 모든 cron(critical_healthcheck, hourly_digest, jarvis_executor, regime_check 등) 모두 사라짐. 추정 원인: Stock_Trade/KATA-M cron 추가 작업 중 BitCoin 라인 보존 미흡 (`crontab -l > tmp; echo new >> tmp; crontab tmp` 패턴이지만 BitCoin 라인이 어떤 시점에 빠짐).

### 1.3 services.bak.20260408_1708 디렉터리 잔존
4월 8일 백업 디렉터리가 그대로 남음. PYTHONPATH 직접 import는 0건이지만:
- 외부 호출 또는 잘못된 sys.path 설정 시 import 가능
- 코드 grep 시 검색 결과 노이즈 (이전 plan 검토 시 무시했음)

### 1.4 다중 프로젝트 동거 환경의 모듈명 충돌
`services.execution.runner` (PID 132865)는 Stock_Trade의 정상 가동 프로세스 (cwd=/home/ubuntu/Stock_Trade). 모듈명이 BitCoin_Trade와 동일 (`services.execution.X`)하여 ps/grep 결과만 보고 BitCoin 좀비로 오해할 수 있음 (lessons #17 패턴 강화).

## 2. 어떻게 수정했나

1. **좀비 PID 106185, 119250 SIGTERM** — 둘 다 3초 내 정상 종료
2. **PID 132865는 Stock_Trade 정상 → 보존** (cwd 확인 후 결정)
3. **crontab BitCoin 9건 복구** — `/tmp/crontab_v2_20260501_232614.bak`에서 추출 + 신규 cron(critical, hourly_digest) 합쳐 적용
4. **중복 cron 제거** — critical 2회 등록 → 1회로 정리
5. **plan 20260502 P1 준수** — 09:10 daily_report cron 제외 (백업에서 가져왔지만 이번 plan에서 제거하기로 결정한 항목)
6. **services.bak.20260408_1708 → .disabled로 이름 변경** — 향후 오용 방지

## 3. 교훈

1. **장시간 가동 스크립트는 systemd로만, cron 직접 호출 금지** — `daily_live.py --realtime`처럼 무한 루프 스크립트를 cron으로 호출하면 매시 새 인스턴스가 추가되어 좀비 누적. systemd는 단일 인스턴스 보장 (`Type=notify`)
2. **다중 프로젝트 환경 crontab 갱신은 grep -v + echo 패턴 위험** — 다른 프로젝트 라인이 우연히 매칭되어 사라질 수 있음. 갱신 전후 라인 수 비교 (`crontab -l | wc -l`) 또는 백업 비교 필수
3. **백업 디렉터리는 명시적 격리** — `services.bak.YYYYMMDD` → `.bak.YYYYMMDD.disabled`로 이름 변경. 또는 프로젝트 외부(`/tmp` 또는 `~/backup/`)로 이동
4. **lessons #17 다중 프로젝트 동거 강화** — `ps -ef | grep services.execution`만으로 판단 금지. **반드시 `/proc/<PID>/cwd` 확인**해서 어느 프로젝트인지 식별. 모듈명만 같다고 같은 프로젝트 아님
5. **헬스체크 신규 항목 필요**: `check_zombie_processes` — `ps -ef`로 daily_live.py 인스턴스 수 확인, 1개 초과 시 FAIL. critical_healthcheck에 추가 권장
6. **cron 등록 검증 헬스체크**: `check_cron_lines` — crontab의 BitCoin 라인 수가 expected(9개)와 일치하는지. 차이 발생 시 즉시 알람

## 4. pre_deploy_check 신규 룰 후보

```python
def check_zombie_daily_live():
    """daily_live.py 다중 인스턴스 (좀비) 검증."""
    # 로컬 검증 어려움 — 서버 SSH 호출 또는 별도 스크립트
    pass

def check_crontab_bitcoin_lines(expected_count: int = 9):
    """crontab의 BitCoin_Trade 라인 수 검증."""
    pass
```

## 5. 미해결 / 후속

- **알림 4건 정확한 발송 source 미확인** — journalctl 12h엔 매칭 0건. 텔레그램 큐 지연 또는 좀비 프로세스 발송 후 종료 케이스 추정. 좀비 종료로 향후 재발 차단
- **crontab BitCoin 라인이 사라진 정확한 시점/원인** — Stock_Trade 작업 로그 추적 필요
- **`check_zombie_processes` + `check_crontab_lines` 헬스체크** 신규 추가 (별도 plan)
