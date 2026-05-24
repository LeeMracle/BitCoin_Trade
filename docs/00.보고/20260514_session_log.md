# 세션 로그 — 2026-05-14 (BATA 운영 + ML 정책 조정)

> W19 목요일 — state 정합, 무한 재시작 복구, ML threshold 완화, 주간 평가 자동화

## 세션 요약

| 항목 | 결과 |
| ---- | ---- |
| 진행 시간 | 약 1.5h (오전~오후 점검) |
| 주요 변경 | 텔레그램 /cooldown, heartbeat 패치, state 정리, ML threshold 0.45→0.40, 주간 cron |
| 봇 상태 | active (정상 가동, DC 4포지션, ML LIVE 0.40) |
| 배포 | AWS 2회 (rsync 코드 + 운영 핫픽스) |

## 작업 항목

| # | 구분 | 항목 | 결과 |
|---|------|------|------|
| 1 | 신규기능 | 텔레그램 `/cooldown` 명령 (상태/해제) | `telegram_bot.py` 추가 — `/cooldown clear confirm` 흐름 |
| 2 | 운영조치 | DC 쿨다운 (3연패, 05-16까지) 수동 해제 | `state.cooldown_until` 제거 |
| 3 | state 정합 | TAO/RVN dust 포지션(>5천원이지만 의미 없음) 정리 | positions 6→4, closed_trades에 manual_cleanup 기록 |
| 4 | 봇 장애 복구 | 60초마다 무한 재시작 발견 → heartbeat 패치 | `realtime_monitor.py` 시작 즉시 + 레벨갱신 중 `/tmp/bata_heartbeat` touch |
| 5 | state 정합 | 5연패 자동 중단 트리거 → manual_cleanup 항목 제거 | closed_trades 23→21 (TAO/RVN 0% 손실 카운트 제거) |
| 6 | ML 정책 | threshold 0.45 → 0.40 완화 | ADR 20260505-2 롤백 조건 명중 (1주 매수 0건) |
| 7 | ML 자동화 | `ml_weekly_review.py` cron 등록 | 일 19:00 UTC (월 04:00 KST) — `/var/log/ml_weekly_review.log` |
| 8 | 영구화 | `deploy_to_aws.sh`에 ml_weekly cron 보존 패치 | 다음 배포 시 cron 유실 방지 |

## 발견 이슈 / 근본 원인

### 1. 봇 무한 재시작 (60초 주기)
- **현상**: `Deactivated successfully` → restart 무한 반복, 레벨 갱신 50/228에서 항상 중단
- **근본 원인**: 봇 시작 후 레벨 갱신 ~5분 소요. 그동안 `/tmp/bata_heartbeat` 파일 미갱신 (메모리 `self._last_heartbeat`만 갱신) → cron `watchdog_check.sh`가 10분 임계 초과로 매분 `systemctl restart` 발동 → 봇이 메인 루프(WS) 도달 전에 또 죽음 → 영구 악순환
- **해결**: 2곳 패치
  - `realtime_monitor.py:166` 시작 즉시 `Path("/tmp/bata_heartbeat").touch()`
  - `realtime_monitor.py:541` 레벨 갱신 10개마다 file touch (메모리 갱신과 동시)

### 2. 5연패 자동 중단 오발동
- **현상**: state 정리 후 봇 시작 시 "!!! 5연패 자동 중단 !!!" 즉시 발동
- **근본 원인**: state 정리 작업에서 TAO/RVN을 `closed_trades`에 `return_pct=0.0, reason=manual_cleanup_dust`로 추가 → `check_consec_loss`가 0%를 손실로 카운트 → SOL +7.2% 이후 (FLOCK -10.7%, IN -10.1%, ALT -10.4%, TAO 0%, RVN 0%) **5연패** 트리거
- **해결**: `reason=manual_cleanup_dust` 항목 closed_trades에서 제거 (23 → 21건)
- **교훈**: 정합 보정 시 state.positions만 변경, closed_trades에 의사 거래 기록 금지 (안전장치 오발동 위험)

### 3. ML LIVE 1주 평가 시점 누락
- **현상**: ADR 5-12 평가일이 5-14까지 미수행
- **결과**: 매수 0건/9일, 차단률 99.1%, PF 0.42 — ADR 롤백 조건 다수 명중
- **조치**: threshold 0.45 → 0.40 완화 + 주간 자동 평가 cron 등록 (재발 방지)

## 산출물

| 파일 | 변경 내용 |
|------|----------|
| `services/execution/telegram_bot.py` | `/cooldown` 명령 추가 (상태/clear confirm) |
| `services/execution/realtime_monitor.py` | heartbeat 파일 touch 2곳 추가 |
| `scripts/deploy_to_aws.sh` | ml_weekly cron 보존 + 로그 파일 touch 추가 |
| `docs/decisions/20260505_2_ml_live_acceleration.md` | 5-14 운영 업데이트 섹션 추가 |
| AWS `/etc/systemd/system/btc-trader.service.d/ml.conf` | THRESHOLD 0.45 → 0.40 |
| AWS crontab | `0 19 * * 0 ml_weekly_review.py` 추가 |
| `workspace/multi_trading_state.json` | positions 6→4, closed_trades 23→21 (백업 2회) |
| `output/_status_check.py` | AWS 진단용 (현황 + BTC 시세) |
| `output/_cleanup_state_4pos.py` | TAO/RVN positions 제거 |
| `output/_remove_manual_cleanup_trades.py` | closed_trades에서 manual_cleanup 항목 제거 |

## 현재 상태 (세션 종료 시점)

| 항목 | 값 |
|------|-----|
| 봇 상태 | active (heartbeat 정상 갱신) |
| DC 포지션 | 4/7 — SUN, STEEM, HUNT, MTL |
| KRW 잔고 | 139,101원 |
| 총 평가 | 286,404원 |
| 쿨다운 | DC/VB 모두 해제 |
| ML | ENABLED=1, SHADOW=0, THRESHOLD=0.40 |
| 시장 (BTC) | 117,630,000원, 24h ±0% (횡보) |

## 다음 단계 / 이월

| 일자 | 작업 | 비고 |
|------|------|------|
| 2026-05-19 | ml_weekly_review 자동 실행 (월 04:00 KST) | 0.40 1주 효과 평가, 텔레그램 자동 발송 |
| 2026-05-19~ | 조건부 분기 | 매수↑/PF↑ → 0.45 복귀 / 무반응 → SHADOW 검토 |
| 2026-06-04 | v3 모델 재학습 (1개월 데이터) | 로컬 PC `ml_train.py` 수동 → `deploy_model_to_aws.sh` |
| 6월 이후 | 학습 자동화 cron 검토 | 월 1회 (로컬 PC 또는 AWS 임시 인스턴스) |
| 상시 | 거래소 dust 정리 | TAO 9,164원 / RVN 7,472원 — 업비트 앱에서 수동 매도 가능 |

## 참고 ADR / 문서

- ADR 20260504-2 ML 신호 필터 도입 (학습/추론 분리, fail-open)
- ADR 20260505-2 ML LIVE 가속 (0.45 → 본 세션에서 0.40 완화)
- plan 20260504_3 ML 신호 필터 시스템 구축
- lessons #10 state ↔ balance 미러 원칙
- lessons #14 이벤트 루프 내 로그 throttle
- lessons #28 fix_state 자동정리 + 알람 디바운스
