# lessons #31 — deploy_to_aws.sh 우회 시 cron 미등록 + silence 패치 동기화 부재

- **날짜**: 2026-05-20 (KST)
- **참조**: lessons #9 (자동화 cron 등록 검증), #16 (배포 스크립트 폴백), #18/#24 (crontab 갱신), #30 (5연패 silence 패치)

## 현상

### 사고 A — cron 미등록
1. 5/14 세션에서 `ml_weekly_review.py` cron 등록 + `deploy_to_aws.sh`에 cron 정의 추가
2. 5/15~5/20 운영 변경(MAX_POSITIONS, TP, REGIME 등) 시 `scp + restart`만 사용 (deploy_to_aws.sh 전체 실행 X)
3. 결과: AWS crontab 갱신 안 됨 → ml_weekly_review/ml_outcome_match cron 모두 미등록
4. 5/15부터 outcome_matcher 실행 0 → shadow JSONL (5/14 1711건 / 5/16 GLM·META 2건 등) outcome 매칭 누락
5. 5/19 04:00 KST ml_weekly_review 자동 발송 누락
6. ml_weekly_review 수동 재실행 결과 Accuracy 42.1% / FN 11 등 5/14 값과 동일 — 신규 outcome 미반영의 직접 증거

### 사고 B — silence 패치 동기화 부재 (lessons #30 보강)
- 5/16 lessons #30 패치: `consec_loss_alerted_until` 도입 → 5연패 알람 디바운스
- 5/18 HUNT 손절 → 6연패 → `cooldown_until` 자동 갱신 (5/19 → 5/21)
- 그러나 `consec_loss_alerted_until`은 자동 동기화 안 됨 → 만료 후 reset → 다음 트리거 시 알람 재발사 위험
- 즉 lessons #30 패치는 "최초 1회 디바운스"만 보장, cooldown 갱신 시 재발사 가능

## 근본 원인

### A
- 코드 변경(scp) 경로와 운영 인프라(crontab/systemd unit) 변경 경로가 분리됨
- `deploy_to_aws.sh`만이 crontab을 갱신하는 단일 출처 → scp 우회 시 인프라 변경 누락
- pre_deploy_check.py에 "기록된 cron이 실제 crontab에 있는지" 검증 룰 부재

### B
- 알람 silence 플래그(`consec_loss_alerted_until`)와 매수 차단 플래그(`cooldown_until`)가 **독립 관리**
- 한쪽이 갱신될 때 다른 쪽 동기화 책임 없음 → state 형식 변환/save/load 시 reset 위험

## 핵심 교훈

- **인프라 변경은 인프라 채널로** — 코드(scp)와 인프라(deploy_to_aws.sh, crontab, systemd) 변경은 절대 한쪽으로 우회 금지
- **cron 등록은 deploy_to_aws.sh 전체 실행 또는 별도 인프라 패치 ssh로만** — scp + restart 콤보로는 절대 cron 갱신 안 됨
- pre_deploy_check.py에 "기록된 cron job 실제 등록 여부" 검증 룰 필수 (lessons #9 강화)
- 안전장치 플래그는 의미가 겹치면 동기화 규칙을 코드로 강제 — 가장 긴 만료 시점으로 자동 일치

## 수정

### 코드 변경
- `services/execution/realtime_monitor.py:_send_periodic_report()` 5연패 분기:
  - skip 분기 진입 시 `cd_until > alerted_until`이면 `alerted_until = cd_until` 자동 동기화
  - cooldown 갱신 후에도 alerted_until 자동 추적 → silence 영구 유지

### 운영 조치 (즉시)
- AWS crontab에 `ml_weekly_review` + `ml_outcome_match` 두 cron 직접 추가 (`crontab -l + echo` 패턴)
- 누락된 outcome 매칭 수동 실행 (`--days 7` → 5/14~5/19 보강)
- AWS state.consec_loss_alerted_until = cooldown_until로 즉시 동기화 (다음 cycle 전에 silence 확정)
- `realtime_monitor.py` 패치본 scp + restart

## 검증규칙 (pre_deploy_check 추가 후보)

```python
def check_cron_registration():
    """deploy_to_aws.sh에 기록된 cron이 실제 AWS crontab에 있는지 검증."""
    required = ['ml_outcome_match.py', 'ml_weekly_review.py',
                'daily_live.py', 'daily_report.py', 'critical_healthcheck.py']
    actual = ssh_run('crontab -l')
    missing = [c for c in required if c not in actual]
    if missing:
        raise CheckFailed(f"cron 미등록: {missing}")
```

## 관련 lessons

- #9  자동화 전제 cron/systemd 등록 + pre_deploy_check 검증 필수
- #16 배포 스크립트가 전제하는 로컬 CLI도 검증 + 폴백
- #18 venv path drift — 인프라 변경 누락의 다른 형태
- #24 crontab 통째 갱신 시 grep -v 위험
- #28 안전장치는 알람 디바운스 + 자동 정리 짝
- #30 5연패 알람 디바운스 도입 (본 lessons에서 보강)
