# cron 9개 → systemd timer 전면 이전 (crontab 공유 구조 탈출)

- **작성일(KST)**: 2026-08-22 23:15
- **작성자/세션**: Claude Code 세션 (사용자 지시)
- **예상 소요**: 1.5시간
- **관련 이슈/결정문서**: [lessons #36-08](../../docs/lessons/20260801_1_cron_baseline_relapse.md), [lessons #44](../../docs/lessons/20260822_5_cron_wipe_detector_in_cron.md)

## 1. 목표

BitCoin_Trade의 스케줄 작업 9개를 crontab에서 systemd timer로 이전해,
타 프로젝트(Stock_Trade) 배포가 crontab을 통째 덮어써도 영향받지 않게 한다.
감시로 버티는 현재 구조를 **구조적 분리**로 대체한다.

## 2. 성공기준 (Acceptance Criteria)

- [x] systemd timer 9개가 `enabled` + `active` 상태 — 실측 enabled 9 / active 9
- [x] 각 timer 다음 실행 시각이 기존 cron 스케줄과 일치 (list-timers 실측)
- [x] 단발 실행 테스트 9개 전부 `Result=success ExitCode=0`
- [x] crontab BATA 9→0, 타 프로젝트 95→95 (diff 무차이 확인)
- [x] crontab 0줄 상태에서 오경보 없음 (감시 기준 timer로 전환 완료)
- [x] `_check_scheduler_integrity()`가 timer 기준 동작 — 정상 시 침묵 / 미달 시 경보 실측
- [x] `pre_deploy_check.py` 서버 실환경 전체 통과 (오류 0)
- [x] **crontab 완전 삭제(0줄) 시뮬레이션 후에도 timer 9개 생존** — 근본 해결 실증

## 3. 단계

1. cron 9개 → timer 매핑표 확정 (UTC 스케줄 그대로 유지)
2. `infra/systemd/` 에 `.service` + `.timer` 9쌍 작성
3. 설치 스크립트 `scripts/install_timers.sh` 작성 (멱등, --dry-run 기본)
4. 서버 설치 → `daemon-reload` → `enable --now`
5. 단발 실행 테스트 (9개 각각)
6. crontab에서 BATA 라인 제거 (타 프로젝트 보존 확인)
7. 감시 기준 전환: `_check_cron_integrity()` → timer 카운트
8. `deploy_to_aws.sh` cron 등록 블록 → timer 설치 호출로 교체
9. `pre_deploy_check.py` 관련 룰 갱신 (cron baseline → timer baseline)
10. Stock_Trade 덮어쓰기 시뮬레이션으로 생존 검증
11. lessons + CLAUDE.md + 커밋/푸시

## 4. 리스크 & 사전 확인사항

| 리스크 | 완화 |
|---|---|
| 이전 중 스케줄 공백 (양쪽 다 없는 순간) | timer 설치·검증 완료 **후에** crontab 제거 (순서 고정) |
| 중복 실행 (cron + timer 동시 가동) | 6단계에서 crontab 제거로 해소. 중복 구간은 짧게 유지하되, 멱등하지 않은 작업(daily_check 알림)은 중복 발송 가능 — 사용자에게 사전 고지 |
| `daemon-reload` 누락으로 unit 미인식 | 설치 스크립트에 포함 + 설치 후 `is-enabled` 실측 |
| `Persistent=true`로 서버 재부팅 시 과거 알림 몰아 발송 | 알림성 작업은 `Persistent=false` (놓친 건 다음 주기에) |
| 환경변수(PYTHONUTF8/PYTHONPATH) 누락 | unit에 `Environment=` 명시, 단발 실행으로 검증 |
| 기존 cron 로그 경로(`/var/log/*.log`) 권한 | timer도 동일 경로 사용, 단발 실행으로 확인 |
| **감시 공백**: 감시 기준을 바꾸는 동안 소실 탐지 불가 | 7단계에서 timer 기준으로 즉시 전환, 전환 후 미달 재현 테스트 |

사전 확인: [lessons #34](../../docs/lessons/20260602_1_cron_zombie_relapse_no_realtime.md)
— systemd로 도는 장시간 스크립트를 cron에서 중복 호출 금지 (좀비 누적).
이번 이전에서 `daily_live.py --realtime`은 **대상 아님** (이미 systemd service).

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [x] 옵션 C — 자동 검증 스크립트: `scripts/pre_deploy_check.py` (신규 룰 포함)
- [x] 추가 — 서버 실측: `systemctl list-timers`, 단발 실행, 덮어쓰기 시뮬레이션

**검증 기록**: 확인 항목 8개 / 발견 이슈 2개

발견 이슈 (둘 다 수정 완료):
1. **검증 룰의 본문 추출 정규식이 함수 경계를 넘어 캡처** — 대상 함수에서 경보를
   지워도 뒤따르는 메서드의 `send_critical`을 보고 통과시켰다. 역방향 테스트로 적발.
   3곳(`check_exec_price_settled` 2곳, `check_cron_watchdog_outside_cron`) 경계 수정.
2. **`install_timers.sh` 호출 검사가 안내 문구에 매칭** — `echo "복구: ... bash
   scripts/install_timers.sh --apply"` 때문에 실제 호출을 지워도 통과했다.
   라인 시작이 `bash`인 경우만 인정하도록 수정.

두 이슈 모두 "룰을 추가했는데 실제로는 아무것도 잡지 못하는" 사문화 사례로,
역방향 테스트(버그 재현본 발화 확인) 없이는 발견 불가능했다.
