# lessons #32 — 배포 검증 우회 차단 가드 (G6 패치)

- **날짜**: 2026-05-24 (KST)
- **분류**: 운영 안정화 / 프로세스 가드
- **관련 lessons**: #9, #16, #18, #23, #24, #27, #31
- **관련 plan**: `workspace/plans/20260524_g6_deploy_guard.md`
- **관련 분석**: `output/team_v0.5_gap_review.md` (G6)

## 현상

`deploy_to_aws.sh:36-40`은 이미 `pre_deploy_check.py`를 강제 실행한다. 그러나 운영 중 빈번한 작은 변경(파라미터 튜닝, 알람 임계 조정 등)은 **편의상 `scp + restart`로 정식 배포 스크립트를 우회**하는 패턴이 반복됨. 결과:

1. lessons #31 사고: 5/15~5/20 6회 운영 변경 모두 scp+restart → `ml_weekly_review` / `ml_outcome_match` cron 5일 미등록 silent fail
2. 검증 룰(`pre_deploy_check`)을 아무리 늘려도 deploy 우회 시 호출 안 됨 → 사문화
3. `deploy_to_aws.sh` 내부 정합성(예: `CRON_xxx` 변수 정의와 `echo "$CRON_xxx"` 등록 1:1 매칭)이 자동 검증 안 됨 — 변수 추가하고 echo 등록 누락해도 silent fail

## 근본 원인

- **변경 채널 단일화 부재**: 코드(scp)와 인프라(cron/systemd)가 분리됐는데, 우회 채널이 강제 검증 의무 없이 허용됨
- **deploy 스크립트 자체의 내적 정합성 검증 부재**: CRON_xxx 변수와 echo 등록은 사람이 짝맞춰야 함 (휴먼 에러 노출)
- **우회 케이스를 위한 공식 도구 부재**: hotfix용 안전 wrapper 없으니 자연스럽게 raw scp 사용

## 핵심 교훈

- **검증 자동화는 강제 호출 경로와 짝이어야 효과** — 룰만 늘려도 호출 안 되면 무용
- **우회 케이스도 공식 도구로 흡수** — "정식 절차가 무겁다"는 이유로 raw scp 쓰지 않게 안전 wrapper 제공
- **deploy 스크립트 자체의 정합성도 자동 검증** — CRON_xxx 정의↔echo 매칭 같은 휴먼 페어링은 lint로 잡기

## 수정

### 코드 변경

1. **`scripts/pre_deploy_check.py` 신규 함수 `check_cron_var_echo_consistency()`**:
   - `deploy_to_aws.sh`의 활성 `CRON_xxx` 변수 정의(주석 제외) 추출
   - `echo "$CRON_xxx"` 등록 라인(주석 제외) 추출
   - 차집합 양방향(미등록·고스트) 모두 errors
   - main()에 등록 — 모든 배포 검증 사이클에서 실행

2. **`scripts/hotfix_deploy.sh` 신규**:
   - 인자: 변경 파일 1개 이상
   - 단계 강제:
     - [1/5] `pre_deploy_check.py` 실행 (PASS 없이 abort)
     - [2/5] scp 변경 파일
     - [3/5] AWS `systemctl restart btc-trader`
     - [4/5] 30초 대기 후 서비스 상태 + cron 등록 상태 출력
     - [5/5] 24h 내 lessons 보강 의무 경고 출력
   - 한계 명시: 인프라 변경은 처리 안 함 → `deploy_to_aws.sh` 전체 실행 권장

### 운영 조치

- 운영자는 코드 1~2파일 변경 시 `bash scripts/hotfix_deploy.sh <파일>` 사용
- 인프라 변경(cron/systemd unit 추가, log 파일 신규 등)은 `bash scripts/deploy_to_aws.sh` 전체 실행
- raw scp + restart는 금지 (예외 시 PM 사전 승인)

## 검증규칙 (이번 패치로 추가)

```python
def check_cron_var_echo_consistency() -> None:
    """deploy_to_aws.sh의 활성 CRON_xxx 변수와 echo 등록 1:1 일관성.

    1. ^CRON_(\\w+)= 매칭 → 활성 변수 (주석 라인 제외)
    2. echo "$CRON_\\w+" 매칭 → 등록 변수 (주석 라인 제외)
    3. 차집합 양방향 errors
    """
```

- 정상 케이스: 현재 `deploy_to_aws.sh` 통과 (CRON_LIVE, CRON_REPORT_18, CRON_WATCHDOG, CRON_LOGVOL, CRON_VB_RECHECK, CRON_REGIME, CRON_CRITICAL, CRON_ML_OUTCOME, CRON_ML_WEEKLY 9개 모두 echo 등록)
- false case 시뮬레이션: `CRON_MISSING` 정의 + `CRON_GHOST` echo → errors 2건 정확히 탐지

## 후속 갭 (G1, G3 등)

이 패치는 G6(우회 차단)만 처리. team_v0.5_gap_review.md에 정리된 G1(hotfix 워크플로우 정식화), G3(알람 카탈로그) 등은 후속.

## 관련 lessons

- #9  자동화 cron 등록 + pre_deploy_check 검증
- #16 배포 스크립트 폴백 (rsync 없을 때 tar)
- #18 venv 경로 drift (인프라 변경 누락의 다른 형태)
- #23 침묵 cron은 heartbeat 짝
- #24 crontab 통째 갱신 시 grep -v 위험
- #27 좀비 봇 옛 코드 알림 (재시작 ≠ 좀비 종료)
- #31 deploy_to_aws.sh 우회 시 cron 미등록 (본 lessons의 직접 트리거)
