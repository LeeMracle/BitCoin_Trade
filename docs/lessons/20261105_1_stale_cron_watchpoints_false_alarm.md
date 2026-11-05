# 사문화된 cron 감시 지점이 timer 이전 후에도 살아남아 false alarm — lessons #44 후속

- **발생일**: 2026-11-05 10:40 KST (operator가 "crontab = 0, baseline 9 미달" P1 알람 발화)
- **심각도**: HIGH (실피해 0원 — 봇 매매·알림 전부 정상 / 사용자·on-call 리소스 소모만 발생)
- **카테고리**: 감시 설계 / 사문화 지점 / stale watchpoint
- **상태**: 복구 완료 (잘못된 cron 재등록 롤백) + 룰 보강
- **관련 lessons**: #36-08(20260801_1), #44(20260822_5), #9, #22, #32

## 증상

- operator 알람: AWS 13.124.82.122 서버에서 `crontab -l | grep -c BitCoin_Trade = 0`, baseline 9 미달.
- 현상 유추: 아침 브리핑·레짐 알림·healthcheck 전부 2시간 침묵.
- 실제 상태 확인(SSH):
  - BitCoin_Trade crontab 라인 = 0 — **사실**
  - systemd `bata-*` timer = 9개 전부 active, 최근 실행 `Result=success`
  - `/var/log/btc_report.log` 최신 2줄: `"[telegram] Markdown 400 → plain fallback 성공" / "[아침 브리핑] 텔레그램 발송 성공"`
  - critical_healthcheck 최신: `"[critical] 정상 (FAIL 0건) — 알람 없음"`
- 결론: **진짜 사고 없음. 아침 브리핑도 09:32 KST 정상 발송. 알람 자체가 stale 감시 지점에서 나온 false positive.**

## 근본 원인

### 1차 — 감시 기준이 lessons #44 반영 안 됨

2026-08-22 lessons #44로 스케줄 작업 9개를 crontab에서 **systemd timer로 전면 이전**했다. 그 시점에 `realtime_monitor._check_scheduler_integrity()`·`daily_check._section_cron`·`deploy_to_aws.sh` 사후 게이트는 "timer unit 카운트" 기준으로 재작성됐다.

그러나 운영자/subagent가 참고하는 **외부 감시 지점**은 시프트되지 않았다:

| 위치 | 상태 (2026-11-05 기준) | 내용 |
|---|---|---|
| `scripts/restore_cron.sh` | **사문화** (존재 자체가 함정) | lessons #36-08 당시 수기 복구 도구. 실행 시 timer와 중복 cron 9줄을 재등록한다. |
| `scripts/pre_deploy_check.py::check_deploy_post_check_remote_cron()` | 코드는 존재하나 검증 대상이 틀림 | `crontab -l` + `grep -c BitCoin_Trade` 라인 존재만 보는데, 이 문자열은 `deploy_to_aws.sh:146`의 "잔재 경고" 라인에도 있어 **의도와 무관하게 PASS** → 실질 검증 0 |
| `docs/lessons/20260801_3_morning_briefing_missing.md:23` | 지침 사문화 | "crontab -l \| grep BitCoin_Trade 라인 카운트(baseline 9)" 가 체크리스트로 남음 |
| `docs/lessons/20260524_2_state_qty_zero_and_cron_loss.md:81` | 지침 사문화 | "배포 후 crontab -l \| grep -c BitCoin_Trade ≥ 9 확인" |
| `docs/lessons/20260801_1_cron_baseline_relapse.md` 다수 라인 | 역사적 기록으로는 맞음 | operator/subagent가 현행 지침으로 오해할 가능성 |

operator는 이 사문화된 체크리스트를 따라 `crontab -l | grep -c BitCoin_Trade`를 실행 → 0 반환 → "baseline 9 미달" P1 알람 발사. 실제로는 timer 경로로 모든 스케줄이 정상 동작 중.

### 2차 — 잘못된 복구 (내 실수)

RCA 초기에 operator 보고를 신뢰해 `restore_cron.sh --apply`를 실행했다. 결과: BitCoin_Trade cron 9줄 등록. **이것은 timer 9개와 중복 실행 시작**을 의미했다 — daily_report, regime_check, daily_briefing 등 각 작업이 매일 2회씩 발사되며 텔레그램 중복·레이스 컨디션·로그 뒤섞임 위험.

즉시 `/home/ubuntu/crontab.bak_restore_20261105_014355` 백업으로 롤백했다. 롤백 후 상태: cron 0줄 복귀, timer 9개 그대로 active. 중복 실행 창은 약 2분(01:43 KST ~ 01:45 KST). 이 창 안에 등록된 cron 트리거 시각은 없었으므로 실제 중복 실행은 0건.

**교훈**: operator의 "baseline X 미달"이 실피해 신호인지 사문화 감시 오탐인지 **먼저 실측(ground truth)으로 반증**해야 한다. timer가 살아있다면 cron 0은 정상.

## 수정

### 즉시 (서버)

1. `restore_cron.sh --apply` 원복: `crontab /home/ubuntu/crontab.bak_restore_20261105_014355`
2. 상태 실측: BitCoin_Trade cron 0줄, Stock_Trade 21줄 (불변), bata-* timer 9개 active

### 코드 (이 PR 범위)

1. **`scripts/restore_cron.sh` 폐기 가드**: 스크립트 상단에 "⚠ 사문화 (lessons #44 timer 이전으로 더 이상 사용 금지)" 경고 + `--apply` 실행 시 "이미 timer로 이전됨. 복구하려면 `install_timers.sh --apply`를 사용하라" 안내 후 exit 1. 완전 삭제하지 않는 이유: lessons #36-08 당시의 복구 기법은 역사적 가치가 있음.
2. **`check_deploy_post_check_remote_cron()` 룰 재정의**:
   - 기존 검증(crontab -l 라인 존재) → **금지 규칙으로 반전**: deploy_to_aws.sh에 `crontab -l` + `grep -c BitCoin_Trade` 라인이 **baseline 게이트 용도**(즉 `-lt 9` + `exit 1` 조합)로 사용되면 lessons #44 회귀 → ERROR.
   - 즉 "잔재 경고용 WARN"은 통과, "baseline 가드로 사용"은 FAIL.
3. **`check_scheduler_watchpoint_not_cron()` 신규 룰**: 운영 체크리스트성 .md 파일에서 "crontab -l | grep -c BitCoin_Trade" + "baseline" 공존 라인을 금지. 사문화된 체크리스트가 subagent·operator에게 다시 cron 기반 판정을 강제하지 않도록. 단, 역사적 기록(lessons 20260801_1, 20260524_2, 20260822_5, 20260801_3)은 명시적 allow-list로 예외 처리.

### 운영 지침 (추가 발생 시)

operator의 BitCoin_Trade 스케줄 건전성 판정 **단일 진실 원천**은:

```
ssh ... "systemctl list-timers --all --no-legend 2>/dev/null | grep -c 'bata-'"
```

baseline ≥ 9 이면 정상, 미만 시에만 alarm. crontab 카운트는 **감시 지점이 아니다** (타 프로젝트 공유 자원이라 BitCoin_Trade 소유권 없음, lessons #44 #170행 교훈 재확인).

## 검증규칙 (pre_deploy_check.py)

1. **`check_deploy_post_check_remote_cron()` 재정의 완료**:
   - baseline 게이트가 cron 기반이면(`BitCoin_Trade` 카운트 + `-lt N` + `exit 1`) ERROR.
   - timer 기반(`BTC_REMOTE_TIMERS` + `-lt 9` + `exit 1`)이 있어야 PASS.
   - 역방향 테스트: deploy_to_aws.sh에서 timer 게이트를 cron 게이트로 바꾼 가짜 패치에 ERROR 발화 확인.

2. **`check_scheduler_watchpoint_not_cron()` 신설**:
   - docs/ 하위 .md 파일(allow-list 제외)에서 `crontab -l | grep` + (`baseline` 또는 `≥ 9` 또는 `>= 9` 또는 `카운트 9`) 공존 라인을 금지.
   - 역방향 테스트: 새 체크리스트에 "crontab -l | grep -c BitCoin_Trade 라인 카운트(baseline 9)" 추가 시 ERROR 발화 확인.

3. **`scripts/restore_cron.sh` 폐기 가드**:
   - pre_deploy_check에서 `restore_cron.sh` 존재 시, 파일 1~5행에 "DEPRECATED" 또는 "사문화" 키워드가 없으면 WARN.

## 역방향 테스트 (룰이 실제로 실패를 잡는지)

| # | 회귀 시나리오 | 기대 결과 |
|---|---|---|
| 1 | `deploy_to_aws.sh`의 timer 게이트(BTC_REMOTE_TIMERS -lt 9)를 cron 게이트로 치환 | `check_deploy_post_check_remote_cron` ERROR |
| 2 | 새 docs/ops/ 파일에 "crontab -l \| grep -c BitCoin_Trade ≥ 9 확인" 체크리스트 추가 | `check_scheduler_watchpoint_not_cron` ERROR |
| 3 | `restore_cron.sh`가 DEPRECATED 가드 없이 존재 | `check_restore_cron_deprecated` WARN |
| 4 | 정상 상태(timer 게이트 + 체크리스트 cron 라인 없음) | 전부 PASS |

4건 모두 실측으로 확인 (이 커밋에 함께 스크린샷/로그).

## 교훈

1. **"감시 대상을 바꾸는 리팩터링"에는 "감시 지점의 사문화 코드·문서 제거"가 반드시 동반되어야 한다.** lessons #44에서 cron → timer 이전은 성공했지만, `restore_cron.sh`·사문화된 check 룰·사문화된 체크리스트가 남아 **false alarm을 2개월 반 뒤에 다시 발사**했다. 리팩터링 체크리스트에 "구 감시 지점 삭제·DEPRECATED 가드 추가" 항목을 넣어야 한다.
2. **operator의 P1 알람은 반드시 실측으로 반증 가능해야 한다.** "crontab -l = 0"은 ground truth이지만 그것이 사고인지는 **timer 생존 확인** 없이 판단 불가. 감시기 자체가 사문화된 기준으로 판정하면 알람은 피해보다 피로를 만든다 (lessons #30 시리즈 계열: 감시기의 디바운스·컨텍스트 분리).
3. **"복구"를 서두르면 사고를 만든다.** 롤백 가능한 2분 안에 수습했지만, `restore_cron.sh`가 timer와 중복 등록을 하는 것은 **스크립트 자체가 사문화되었음을 감시 룰이 차단하지 않은 결과**다. 이 lessons는 "감시 리팩터링 체크리스트"의 공식 등재 항목이 된다.
4. **룰은 "통과"가 아니라 "실패를 잡는지"로 검증한다 (lessons #44 #48 재확인).** `check_deploy_post_check_remote_cron()`는 통과 상태였지만 검증 대상이 틀렸다 — `deploy_to_aws.sh:146`의 잔재 경고 라인에 매칭돼 PASS. 룰의 통과 여부가 아니라 "이 룰이 회귀를 잡을까"를 역방향으로 물어야 한다.
