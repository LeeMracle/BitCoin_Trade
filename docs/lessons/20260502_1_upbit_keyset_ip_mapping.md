# #20 — 업비트 다중 API 키-IP 매핑 + 헬스체크 부재로 8시간 무감지

- **발생일**: 2026-05-01 23:00 KST ~ 2026-05-02 07:00 KST (약 8시간)
- **탐지일**: 2026-05-02 ~07:30 KST (사용자가 텔레그램에서 매시 동일 오류 8건 확인 후 보고)
- **영향 범위**: BATA `jarvis_executor` 매시 정각 실행이 BTC/KRW private API 호출에서 `no_authorization_ip`로 전부 실패. 단, systemd `btc-trader.service`(realtime monitor)는 별도 가동 — 실거래 자체는 진행됨
- **원인 분류**: 운영(키 관리) + 모니터링 부재(헬스체크 없음)
- **관련 plan**: [workspace/plans/20260502_reporting_system_overhaul.md](../../workspace/plans/20260502_reporting_system_overhaul.md)

## 1. 무엇이 일어났나

서버 `services/.env`의 `UPBIT_ACCESS_KEY=bOshvy...`가 업비트에 등록된 허용 IP `13.209.165.58`(불명 — 이전 환경 추정)와 매핑되어 있었으나, 서버 실제 IP는 `13.124.82.122`. 같은 업비트 계정에 별도 키 `6nYe3D...`가 `13.124.82.122`로 등록되어 있었지만 서버 .env에 들어있지 않았다.

오류 발생 시점: 2026-05-01 23:00 KST 정각 jarvis_executor 매시 실행. `workspace/jarvis_log.jsonl`에 이후 8시간 연속 동일 오류 누적. 텔레그램은 매시 동일 오류 메시지 발송했으나 사용자는 06:00, 07:00 알림 확인 후에야 자비스(Claude)에 보고.

## 2. 왜 무감지였나

- jarvis_executor가 매시 `try/except`로 오류를 잡고 텔레그램 발송은 하지만 동일 오류 디바운스가 없어 8시간 동안 8건 발송 → 사용자가 노이즈로 인지
- BATA에 **별도 헬스체크 시스템 부재** — daily_report는 09:10/18:00에만 실행되고 인증 실패 자체를 명시적으로 검증하지 않음
- watchdog_check.sh는 systemd `btc-trader` heartbeat만 감시 (jarvis cron의 인증 실패는 감지 못 함)
- 전체 알림이 매시 30~40건 발송되는 노이즈 환경 → 진짜 critical 알림이 묻힘

## 3. 어떻게 수정했나

### 3.1 즉시 복구 (2026-05-02 ~08:00 KST)
1. SSH로 서버 접속, IP·키·cron 진단
2. 업비트 OpenAPI 화면에서 `bOshvy...` 키의 IP `13.209.165.58` 삭제 → 의도는 `13.124.82.122`로 변경이었으나 키 자체가 삭제됨 (`invalid_access_key` 오류 등장)
3. 사용자가 별도 보관하던 `6nYe3D...` 키쌍을 로컬 `services/.env`에 적용 → scp로 서버 동기화
4. `fetch_balance()` 호출 성공 확인 → `jarvis_executor --dry-run`으로 end-to-end 검증

### 3.2 재발 방지 (plan 20260502 P0+P1)
1. `services/healthcheck/runner.py` 신규 — 9개 체크 함수 (인증·키-IP·jarvis cron·daily_live·regime_check·state 신선도·시스템·state↔balance·로그 볼륨)
2. `scripts/critical_healthcheck.py` 신규 — 매시 5분 cron, 인증·jarvis cron 두 항목만 점검, FAIL 시 즉시 텔레그램 + 30분 디바운스
3. `scripts/daily_report.py` 17시 이후 헬스체크 섹션 통합
4. `services/alerting/notifier.py` `send(message, parse_mode=None)` 옵션 추가 — Markdown 특수문자 escape 안 된 메시지 안전 발송
5. `scripts/jarvis_executor.py` 동일 오류 1시간 디바운스 — 같은 오류 시간당 1회만 발송
6. 09:10 KST `daily_report` cron 제거 — 18:00 KST 단일화
7. `scripts/log_volume_check.sh` 정상 케이스 발송 제거 — 18:00 헬스체크에 흡수, 이상 시만 즉시 발송
8. `scripts/pre_deploy_check.py`에 검증 룰 2종 추가 (`check_healthcheck_module`, `check_critical_healthcheck_cron`)
9. `scripts/deploy_to_aws.sh`에 critical_healthcheck cron + 09:10 제거 반영

## 4. 검증규칙 (pre_deploy_check.py에 코드화)

```python
def check_healthcheck_module():
    """services.healthcheck import 가능성 + 4개 핵심 함수 callable 검증."""
    from services.healthcheck.runner import (
        check_auth, check_jarvis_cron, run_all, build_health_section,
    )

def check_critical_healthcheck_cron():
    """deploy_to_aws.sh에 critical_healthcheck.py cron 등록 + 09:10 cron 제거 검증."""
    if "critical_healthcheck.py" not in content:
        errors.append(...)
    if re.search(r'CRON_REPORT="10\s+0\s+\*', content):
        errors.append(...)
```

## 5. 교훈

1. **다중 API 키 운영 시 키 ↔ 환경(서버 IP) 매핑 표를 코드 또는 문서로 명시**. 한 키에 여러 IP를 등록하지 말고, 환경별 키를 분리 + .env에 어떤 환경인지 주석 필수
2. **인증 같은 critical 경로는 별도 단명 헬스체크(매시 5분 cron) + 즉시 알람 + 디바운스 3가지 세트** — daily report는 사후 보고용이지 실시간 감시 도구 아님
3. **노이즈가 critical을 묻는다** — 정상 cron 동작은 침묵, 이벤트(매매·오류)만 발송. 같은 오류는 1시간 디바운스
4. **자기 검증 금지 정책의 효용** — cto 1차 review에서 plan Minor 3건, 2차 review에서 구현 Critical 3건 발견. 자기 세션이 PASS라 판정한 산출물도 별도 검토에서 사고 직결 이슈 다수 발견됨
5. **텔레그램 Markdown parse_mode는 escape 누락에 약함** — 동적 메시지(헬스체크 등)에는 plain text 또는 V2 escape 필수

## 6. 관련 lessons

- [#9 cron 누락](20260408_1_jarvis_cron_missing.md) — 자동화 전제 스크립트는 cron + pre_deploy_check 검증 필수 (이번에 그대로 적용)
- [#10 state↔balance 불일치](20260408_2_state_balance_mismatch.md) — 헬스체크 8번 항목 상시 감지로 발전
- [#15 외부 API 재시도](20260413_1_startup_refresh_crash.md) — ifconfig.me 폴백(ipify) 패턴 적용
- [#17 다중 프로젝트 동거](20260421_1_multi_project_process_misdiagnosis.md) — t3.micro 메모리 압박 점검에 swap WARN 50%+ 추가
- [#18 venv 경로 드리프트](20260425_1_crontab_venv_path_drift.md) — critical_healthcheck cron 절대경로 + stderr→로그파일 패턴 강제
