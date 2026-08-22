# cron 소실 감시기가 cron 안에 있어 함께 죽음 — 19일간 무알람

- **발생일**: 2026-08-03 소실 → 2026-08-22 발견 (19일 경과)
- **심각도**: CRITICAL (자동 보고·알람·워치독 전면 정지)
- **카테고리**: 감시 설계 / 단일 실패 지점
- **상태**: 복구 완료 + 근본 해결(systemd timer 이전) 완료 — 2026-08-22

## 증상

봇 상태 점검 중 `crontab -l | grep -c BitCoin_Trade` = **0**.
등록돼 있어야 할 9개가 전부 없었다.

| cron | 역할 | 정지 영향 |
|---|---|---|
| `watchdog_check.sh` (매분) | 봇 멈춤 감지 → 자동 재시작 | 봇이 죽어도 방치 |
| `critical_healthcheck.py` (매시) | 위험 상태 → 텔레그램 | 손실·이상 무알람 |
| `daily_check.py --notify` (09:32 KST) | 아침 브리핑 | 사용자 접점 소멸 |
| `regime_check.py --notify` (09:30) | 레짐 전환 알림 | 관망 종료 인지 불가 |
| `daily_report.py` (18:00) | 일일 리포트 | — |
| `log_volume_check.sh` / `vb_recheck_trigger.py` / `ml_outcome_match.py` / `ml_weekly_review.py` | 정비·재검증 | — |

산출물 최종 갱신 시각으로 역산: `logs/log_volume.log` 08-03 00:10,
`workspace/regime_state.json` 08-03 00:30 → **2026-08-03부터 정지**.

## 원인

### 1차 원인 — 타 프로젝트의 crontab 전체 덮어쓰기 (lessons #36-08 재발)

```bash
# Stock_Trade/scripts/deploy_aws.sh:128
crontab $REMOTE_DIR/config/crontab.txt
```

`crontab <파일>`은 기존 crontab을 **전부 버리고** 그 파일로 교체한다.
같은 서버에서 3개 프로젝트(BitCoin_Trade / Stock_Trade / Blog_Income)가
crontab을 공유하므로, Stock_Trade가 배포할 때마다 BATA 9줄이 증발한다.

이미 2026-08-01에 lessons #36-08로 기록된 사고인데 **이틀 뒤 그대로 재발**했다.

### 2차 원인(진짜 문제) — 감시기가 감시 대상과 같은 실패 지점을 공유

lessons #36-08 대응으로 두 가지 방어를 넣었었다:

1. `deploy_to_aws.sh` 배포 후 실측 게이트 (`crontab -l | grep -c` ≥ 9 → 미달 시 exit 1)
2. `daily_check.py::_section_cron` — 아침 브리핑에 cron 라인 카운트 포함

**둘 다 이번에 작동하지 않았다.**

- (1)은 `deploy_to_aws.sh`를 **실행할 때만** 돈다. 8/3 이후 전체 배포가 없었고,
  오늘 수정은 hotfix 경로(scp)로 나가 이 게이트를 타지 않았다.
  애초에 타 프로젝트가 지우는 것을 막을 수도 없다.
- (2)는 **자기 자신이 cron 작업**이다. crontab이 지워지면 브리핑이 안 돌고,
  안 도니 cron 검사도 안 되고, 그래서 "cron이 없다"는 사실을 알릴 주체가 사라진다.

즉 **감시기를 감시 대상 안에 넣었다.** 감시 대상이 죽으면 감시기도 죽는
구조라 실패가 자기 자신을 은폐한다.

## 수정

### 즉시 복구

`scripts/restore_cron.sh` 신설 — `deploy_to_aws.sh`와 동일한
**읽고-덧붙이는** 방식(타 프로젝트 항목 보존). Stock_Trade처럼 파일로
통째 교체하지 않는다.

```bash
(crontab -l | grep -v "daily_check.py" | grep -v ... ;  # 기존 읽고 BATA만 제거
 echo "$CRON_DAILY_BRIEFING"; ...) | crontab -          # BATA 9줄 재등록
```

사후 실측 검증 내장: BATA ≥ 9 **그리고** 타 프로젝트 라인 수가 줄지 않았는지
확인, 위반 시 exit 1. 결과: BATA 0 → 9, 기타 95 → 95 (보존).

### 재발 감시 — cron 밖으로 이전

`realtime_monitor._check_cron_integrity()` 신설.
봇 본체는 **systemd로 상시 가동**되므로 crontab이 통째로 날아가도 살아남는다.

```python
async def _check_cron_integrity(self):
    ...
    if n >= CRON_BASELINE_LINES: return
    await send_critical(f"[BATA] cron 소실 감지 — {n}/{CRON_BASELINE_LINES}개 ...")
```

- `_refresh_levels()` 앞에서 주기 호출 (CB 주기 체크와 같은 훅)
- 경보 6시간 throttle (`CRON_ALERT_INTERVAL_SEC`)
- 상수는 `config.py` 정의 후 import (교훈 #19)
- 실측 검증: baseline 정상 시 침묵, 미달 시 경보 발송 확인

## 검증규칙 (pre_deploy_check.py)

`check_cron_watchdog_outside_cron()` 신설:

1. `realtime_monitor`에 `_check_cron_integrity()` 존재
2. 본문에 `crontab` 조회 + `send_critical` 경보 경로 존재
3. **정의만 하고 미호출이면 ERROR** (사문화 방지)
4. 상수가 `config.py`에 정의됐는지

버그 재현본에서 발화 확인.

## 근본 해결 — systemd timer 전면 이전 (2026-08-22 완료)

감시로 버티는 대신 **구조를 분리**했다. cron 9개를 systemd timer로 전량 이전
(`scripts/install_timers.sh`). timer는 crontab과 무관하므로 타 프로젝트가
crontab을 어떻게 다루든 영향받지 않는다.

| 기존 cron (UTC) | systemd timer |
|---|---|
| `* * * * *` watchdog_check.sh | `bata-watchdog.timer` (minutely) |
| `5 * * * *` critical_healthcheck.py | `bata-critical-healthcheck.timer` |
| `10 0 * * *` log_volume_check.sh | `bata-log-volume.timer` |
| `15 0 * * *` vb_recheck_trigger.py | `bata-vb-recheck.timer` |
| `30 0 * * *` regime_check.py --notify | `bata-regime-check.timer` |
| `32 0 * * *` daily_check.py --notify | `bata-daily-briefing.timer` |
| `0 9 * * *` daily_report.py | `bata-daily-report.timer` |
| `0 18 * * *` ml_outcome_match.py | `bata-ml-outcome.timer` |
| `0 19 * * 0` ml_weekly_review.py | `bata-ml-weekly.timer` |

설계 결정:
- 스케줄 정의의 **단일 진실 원천**은 `install_timers.sh`의 `JOBS` 테이블.
  `deploy_to_aws.sh`의 `CRON_*` 변수 9개는 **삭제**했다 — 남겨두면
  "고쳤는데 반영 안 됨" 부류의 사문화 설정이 된다.
- `Persistent`: 알림성 작업은 `false`(재부팅 시 과거 알림 몰아 발송 방지),
  데이터/정비 작업은 `true`(누락 보정이 유의미).
- 감시 기준도 crontab 라인 → timer 유닛으로 전환
  (`_check_scheduler_integrity`, `daily_check._section_cron`, 배포 사후 게이트).

### 실증

**crontab을 0줄로 완전히 비운 뒤에도 timer 9개가 그대로 생존**했다.
(Stock_Trade 배포 시뮬레이션 → `crontab -l` 0줄 / `bata-*` timer 9개 active·enabled)

부수 확인:
- crontab BATA 9→0, 타 프로젝트 95→95 (diff 무차이)
- 단발 실행 9개 전부 `Result=success ExitCode=0`
- 아침 브리핑 텔레그램 발송 성공 (lessons #39 plain fallback 정상 작동)

### 이전 과정에서 적발한 검증 룰 결함 2건

역방향 테스트(버그 재현본에 룰이 발화하는지)로만 드러난 것들이다.

1. **본문 추출 정규식이 함수 경계를 넘어 캡처** — 대상 함수에서 경보 코드를
   지워도 뒤따르는 메서드의 `send_critical`을 보고 통과시켰다. 3곳 수정.
2. **`install_timers.sh` 호출 검사가 안내 문구에 매칭** —
   `echo "복구: ... bash scripts/install_timers.sh --apply"` 때문에
   실제 호출을 제거해도 통과했다. 라인 시작이 `bash`인 경우만 인정하도록 수정.

둘 다 "룰을 추가했지만 아무것도 잡지 못하는" 상태였다. **통과 확인만으로는
룰의 유효성을 알 수 없다**는 것이 이번 작업의 가장 큰 소득이다.

## 남은 한계

봇 프로세스가 죽으면 `_check_scheduler_integrity`도 함께 멈춘다. 다만 봇이 죽으면
매매 자체가 멈추므로 증상이 즉시 드러나고, `bata-watchdog.timer`가 매분 감지해
재시작한다 — 워치독은 이제 봇과 독립된 systemd 경로에 있으므로 순환 의존이 없다.

## 교훈

1. **감시기는 감시 대상과 실패 지점을 공유하면 안 된다.** cron을 감시하는 코드를
   cron에 넣으면, 정확히 감시가 필요한 순간에 감시기가 없다. 화재경보기를
   불타는 방 안에만 두는 것과 같다.
2. **"배포 시 검사"는 배포하지 않는 동안 무방비다.** `deploy_to_aws.sh`의 실측
   게이트는 옳은 방어였지만 19일간 한 번도 실행되지 않았다. 외부 요인으로 깨질
   수 있는 상태는 **배포 시점이 아니라 상시** 감시해야 한다.
3. **hotfix 경로가 게이트를 우회한다.** 오늘 수정을 scp로 내보내 배포 후 검증이
   생략됐다 — lessons #33에서 이미 지적된 패턴인데 또 반복했다.
   `hotfix_deploy.sh` 같은 wrapper를 실제로 쓰는 습관이 필요하다.
4. **공유 자원(crontab)은 소유권이 없으면 반드시 깨진다.** 3개 프로젝트가 같은
   crontab을 쓰는 한, 한 프로젝트의 "전체 교체"가 나머지를 지운다.
   감시로 버티는 것은 임시방편이고, **소유권이 분명한 자원으로 옮기는 것**이
   해결이다 (systemd unit은 프로젝트별 파일 단위라 충돌하지 않는다).
5. **검증 룰은 "통과"가 아니라 "실패를 잡는지"로 검증해야 한다.** 이번에 추가한
   룰 중 2건이 버그 재현본에서 발화하지 않았다 — 정규식이 함수 경계를 넘어
   캡처하거나, 안내 문구에 매칭돼 실제 회귀를 놓쳤다. 룰을 넣고 게이트가
   초록불이면 안심하기 쉬운데, 그 초록불이 "검사가 아무것도 안 하고 있음"을
   뜻할 수 있다. 역방향 테스트 없는 룰 추가는 안전감만 준다.
