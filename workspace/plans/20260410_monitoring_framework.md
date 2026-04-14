# BATA 모니터링 3계층 프레임워크

- **작성일(KST)**: 2026-04-10 16:00
- **작성자/세션**: Claude (CTO 리뷰 후속)
- **예상 소요**: 3~4시간 (5개 모듈 점진 구현)
- **관련 이슈/결정문서**: CTO 리뷰 04-10 FAIL 판정, docs/lessons/20260410_1 (CB 로그 스팸), L8/L10/L14

## 1. 목표

BATA 봇의 정상 작동을 독립적으로 감시하는 3계층 모니터링 체계를 구축한다.
현재는 "봇이 죽으면 알 방법이 없고, 돌아도 제대로 도는지 확인할 수 없는" 상태.

## 2. 성공기준 (Acceptance Criteria)

- [ ] AC-1: 독립 Watchdog — 봇 heartbeat 10분 미갱신 시 텔레그램 경보 발송
- [ ] AC-2: State ↔ Exchange 교차검증 — 매 1시간 포지션 불일치 탐지 + 경보
- [ ] AC-3: 웹소켓 stale 감지 — 5분간 체결 수신 없으면 강제 재연결 + 경보
- [ ] AC-4: 로그 볼륨 감시 — 일일 cron으로 이상 볼륨(0줄 또는 5000줄+) 경보
- [ ] AC-5: 필터 작동 통계 — 일일 보고에 필터별 차단 건수 포함
- [ ] AC-6: 57/57 테스트 PASS + pre_deploy_check GREEN
- [ ] AC-7: CTO review PASS

## 3. 단계

### Phase A: 즉시 핫픽스 (04-10)

1. ~~WARN-1: get_balance() fetch_tickers 일괄 조회 교체~~ (완료)
2. ~~WARN-5: except pass → 로깅 + 안전 처리~~ (완료)
3. 서버 배포 + 서비스 재시작 + 로그 확인

### Phase B: 1계층 Heartbeat (04-11 목표)

4. realtime_monitor에 heartbeat 파일 갱신 (매 5분 `/tmp/bata_heartbeat` touch)
5. scripts/watchdog_check.sh 작성 — heartbeat 10분 미갱신 시 텔레그램 경보 + systemctl restart
6. cron 등록 (매 1분 실행)
7. systemd WatchdogSec=300 설정 (병행)

### Phase C: 2계층 Sanity Check (04-12~13 목표)

8. State ↔ Exchange 교차검증 — realtime_monitor._hourly_sync() 메서드
   - 매 1시간 get_balance() → state.positions 비교
   - 불일치 시 텔레그램 경보 (자동 보정 안 함, 경보만)
9. 웹소켓 stale 감지 — _last_msg_ts 추적 + 5분 타임아웃 강제 재연결
10. 로그 볼륨 감시 cron — scripts/log_volume_check.sh

### Phase D: 3계층 Performance Audit (04-14 목표)

11. 필터 작동 통계 카운터 — self._filter_stats = {fg: 0, ema200: 0, atr: 0, cb: 0}
12. 일일 보고에 필터 통계 포함 — daily_report.py 확장

## 4. 리스크 & 사전 확인사항

| 리스크 | 완화 |
|--------|------|
| fetch_tickers 일괄 조회가 업비트에서 지원 안 될 수 있음 | ccxt upbit.fetch_tickers 테스트 필수. 실패 시 REST /v1/ticker?markets= 직접 호출 |
| Watchdog 자체가 죽을 수 있음 | cron이므로 별도 프로세스 관리 불필요. cron 자체는 systemd가 관리 |
| State-Exchange 교차검증 시 API 호출 추가 | 1시간 1회이므로 rate limit 영향 미미 |
| 웹소켓 재연결이 무한루프 될 수 있음 | 재연결 최대 5회 → 실패 시 프로세스 종료 (systemd가 재시작) |
| 참조: L10(state/balance 불일치), L14(CB 로그 스팸), L8(알트 합산 누락) |

## 5. 검증 주체 (교차검증)

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [ ] 옵션 B — 서브에이전트(`cto` review)
- [ ] 옵션 C — 자동 검증 스크립트: `scripts/pre_deploy_check.py`

**검증 기록 형식 (필수)**
```
검증 주체: B (CTO review) + C (pre_deploy_check)
확인 항목: 7개 (AC-1~7)
발견 이슈: M개
  - ...
판정: PASS / FAIL / 조건부 PASS
```

> 구현을 수행한 동일 세션은 자기 산출물을 PASS 판정하지 않는다.

## 6. 회고 (작업 종료 후 작성)

- **결과**: (미완)
- **원인 귀속**: 
- **한 줄 회고**:
- **후속 조치**:
