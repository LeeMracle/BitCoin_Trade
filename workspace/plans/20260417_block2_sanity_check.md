# 블록 2 — Sanity Check (P7-06 / P7-08)

- **작성일(KST)**: 2026-04-17 09:25
- **작성자/세션**: pdca-pm (자비스)
- **예상 소요**: 1시간
- **관련 이슈/결정문서**: docs/lessons/20260408_2 (state/balance 불일치), docs/lessons/20260405_1 (알트 누락)

## 1. 목표

2계층 Sanity Check — (1) state ↔ exchange 포지션 교차검증이 운영 중 실제로 작동하는지 확인, (2) 로그 볼륨 감시 cron이 올바른 임계값으로 일일 경보를 보내는지 점검.

## 2. 성공기준 (Acceptance Criteria)

- [ ] AC-1 (P7-06): realtime_monitor `_hourly_sync()` 존재, KRW/BTC 제외·먼지 필터(5000원) 포함, 불일치 시 텔레그램 경보(notify_error) 호출 — 자동 보정 없음
- [ ] AC-2 (P7-06): pre_deploy_check에 `_hourly_sync` 존재 검증 규칙 추가
- [ ] AC-3 (P7-08): `scripts/log_volume_check.sh` 정상 (임계: 0줄/50000줄/오류 100줄) + bash -n 통과
- [ ] AC-4 (P7-08): cron 등록 스크립트(deploy_to_aws.sh 또는 별도 systemd timer) 확인 — 현재 설치 상태는 서버 대상이므로 "deploy_to_aws.sh 내 crontab 스니펫이 있는지"로 대리 판정
- [ ] AC-5: 요건서 P7-08의 기준 "일일 0줄 또는 5000줄+"와 현재 스크립트 임계값 검토. 사용자 요청이 더 엄격하면 조정 (현 스크립트: 50000줄+) → **보수적으로 5000줄 기준**으로 조정
- [ ] AC-6: 교차검증 기록

## 3. 단계

1. realtime_monitor._hourly_sync 로직 재확인
2. pre_deploy_check에 _hourly_sync 존재 검증 추가
3. log_volume_check.sh 임계값을 5000줄로 조정 (사용자 요청 준수)
4. bash -n 구문 검증
5. deploy_to_aws.sh cron 섹션 점검
6. 교차검증 기록

## 4. 리스크

| 리스크 | 완화 |
|--------|------|
| log_volume 임계 5000줄은 정상 운영에서도 초과할 수 있음 | "경보만" 발송하고 서비스 중단은 없음. 임계 근거는 daily_check.py + realtime_monitor 평균 로그 수로 향후 튜닝 |
| _hourly_sync에서 fetch_balance rate limit | 1시간 1회 호출 → 무시 가능 |

## 5. 검증 주체

- [x] 옵션 B — pdca-qa
- [x] 옵션 C — pre_deploy_check + bash -n

## 6. 회고

- (미완)
