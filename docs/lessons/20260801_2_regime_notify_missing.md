# lessons #37 — regime_check.py 알림 인자 누락 silent fail

- 날짜: 2026-08-01 (KST)
- 분류: cron 명령어 인자 누락 / silent fail / 관망 종료 시점 인지 지연
- 관련 lessons: #9 (cron 등록 검증), #22 (알림 등급/디폴트), #31 (cron 갱신 채널 우회), #34 (cron silent fail), #35 (SSH 표준)

## 현상

- BTC 관망(BEAR) 모드 지속 중이었으나 사용자는 **BULL 전환 발생 시 즉시 알림을 받지 못하는 상태**로 방치되어 있었음.
- `scripts/regime_check.py:81`
  ```python
  if notify and should_notify(prev, new_state):
  ```
  — `notify` 인자가 False면 전환이 감지되어도 텔레그램 발송을 안 함.
- `scripts/deploy_to_aws.sh:131` `CRON_REGIME` 라인:
  ```bash
  CRON_REGIME="30 0 * * * ... scripts/regime_check.py >> /var/log/regime_check.log 2>&1"
  ```
  — `--notify` 플래그가 **없음**. 즉 매일 09:30 KST 실행되지만 어떤 전환이 있어도 침묵.
- AWS 서버 crontab 실측(2026-08-01):
  ```
  30 0 * * * cd /home/ubuntu/BitCoin_Trade && PYTHONUTF8=1 ... /.venv/bin/python scripts/regime_check.py >> /var/log/regime_check.log 2>&1
  ```
  → 서버 반영 상태도 `--notify` 없음 (deploy 스크립트가 원인).
- 로그(`/var/log/regime_check.log`) 확인 결과: 최근 100줄 전부 **BEAR 판정만 존재**. 실제 BULL/SIDEWAYS 전환 이력 없어서 사용자 체감 사고는 아직 없었지만, **다음 BULL 전환 시 알림 미발송 확정 상태**였음.
- `workspace/regime_state.json` 실측: `current: "UNKNOWN"`, `recent_signals: ["BEAR"]` 1개 — 히스테리시스(3회 동일) 미충족으로 아직 상태 정착 전. 상태 정착 후 BULL 전환이 오면 사용자는 관망 종료를 인지 못 함.

## 근본 원인

1. `regime_check.py`는 `--notify`를 **선택 인자(store_true)**로 설계했고 기본값 False. cron 등록 시 명시 부여가 없으면 자동으로 침묵.
2. `deploy_to_aws.sh`의 `CRON_REGIME` 라인 작성 시 `--notify` 부여 누락. 같은 파일의 `CRON_VB_RECHECK`는 `--notify`가 있어 대조군이 됐어야 했으나 리뷰 누락.
3. 알림 인자가 스크립트 side 기본 False + cron side 명시 없음 = **두 side 모두 "누군가 켜주겠지"** 구조. lessons #22 (알림 등급 도입 시 default 호환 유지)의 이면 — 안전한 default(off)가 오히려 사고를 침묵으로 감춤.
4. `should_notify()` 로직 자체는 정상. 판정 로직도 정상. 유일한 문제는 **알림 전송 경로가 cron 인자 하나 때문에 통째로 죽어있었음**. 코드 리뷰만으로는 발견 어려움 — cron 명령어 라인 lint가 없으면 영구 사각지대.

## 핵심 교훈

- **cron 명령어 인자는 static lint 필수**. 스크립트가 알림/트리거 기능을 옵션 인자로 노출하면, 해당 인자가 cron 라인에 실제 부여됐는지 자동 검사해야 함.
- **선택 인자의 안전 default가 곧 silent fail 유발**. `--notify`, `--alert`, `--send` 등 사용자 인지 경로가 옵션이면 default False가 안전한 게 아니라 **사후 검증이 없으면 사고 감춤**.
- **cron 등록 확인은 "라인 존재"뿐 아니라 "필수 인자 존재"까지 검증**. lessons #9/#31의 "cron 등록 검증"을 인자 단위로 확장.
- **대조 grep 활용**: 동일 파일 내 유사 라인(`CRON_VB_RECHECK`이 `--notify` 있음)이 있으면 신규 cron 추가 시 스타일 통일 검토 의무.

## 수정

### 코드
1. `scripts/deploy_to_aws.sh:131` `CRON_REGIME` 라인:
   ```diff
   -CRON_REGIME="30 0 * * * ... scripts/regime_check.py >> /var/log/regime_check.log 2>&1"
   +CRON_REGIME="30 0 * * * ... scripts/regime_check.py --notify >> /var/log/regime_check.log 2>&1"
   ```
   같은 라인 상단 주석에서 "2026-05-05 매시→일1회 축소, 알림 제거"의 "알림 제거" 문구 제거 (알림은 제거된 적 없이 옵션만 미부여 상태였음).

### 운영 조치
2. `scripts/hotfix_deploy.sh scripts/deploy_to_aws.sh` 실행으로 AWS 반영.
   - deploy_to_aws.sh는 인프라 변경(crontab) 포함하므로 원칙적으로 `deploy_to_aws.sh` 전체 실행 대상. 이번 건은 CRON_REGIME 문자열 1개만 바뀌므로 hotfix로 전송 + 원격에서 재실행 대신 **`deploy_to_aws.sh` 전체 실행**을 정석 채널로 사용.
3. 배포 후 서버 crontab에서 `crontab -l | grep regime_check` 실측 → `--notify` 반영 확인.

## 검증규칙 (pre_deploy_check 추가)

`scripts/pre_deploy_check.py::check_regime_notify_flag()` 신설:

- `deploy_to_aws.sh`의 `CRON_REGIME=` 라인 (주석 제외) 파싱
- 해당 라인에 `regime_check.py`와 `--notify`가 **둘 다** 존재해야 PASS
- 미충족 시 ERROR로 배포 차단

로컬 실증:
- 정상 상태: PASS (경고 1건은 무관한 rsync 없음)
- `--notify` 제거 시나리오: ERROR 1건 정확히 검출, 배포 차단 확인

## 향후 확장 검토

- 신규 스크립트가 `--notify`/`--alert` 등 알림 인자를 추가할 때 cron 등록 라인에 자동 반영되도록 하는 방안:
  - 옵션 A: 스크립트 side default를 True로 뒤집기 (BC 파괴 위험)
  - 옵션 B: cron 등록 라인에 인자 부여 강제하는 lint 룰을 스크립트별로 개별 등록 (현재 채택)
  - 옵션 C: `regime_check --dry-run`만 옵션으로 하고 알림은 기본 활성 (재설계 필요)
- 현재는 옵션 B — 스크립트가 신규 추가되면 pre_deploy_check에 대응 lint 룰도 함께 추가하는 규범을 유지.

## 관련 lessons

- lessons #9 — cron 등록 검증 (본 룰의 원형)
- lessons #22 — 알림 등급 도입 default 호환 유지 (본 사고의 이면 원인)
- lessons #31 — deploy 우회 cron silent fail (본 사고는 우회는 아니지만 인자 누락으로 동일 silent 결과)
- lessons #33 — G6 배포 가드 (pre_deploy_check 사문화 방지 원칙, 본 룰도 hotfix 경로에서 강제 통과)
