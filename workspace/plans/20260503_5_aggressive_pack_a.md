# A 패키지 — DC15→DC10 + MIN_VOLUME 5억→3억

- **작성일(KST)**: 2026-05-03 15:50
- **세션**: 자비스 (Auto mode, 사용자 명시 승인)
- **요청**: 매매 활성화 (A 패키지 선택)

## 1. 변경 항목

| config | Before | After | 효과 |
|---|---|---|---|
| `DONCHIAN_PERIOD` | 15 | **10** | DC10 = 10일 신고가 돌파. 신호 빈도 약 1.5배 |
| `STRATEGY_KWARGS.dc_period` | 15 | **10** | composite 전략 동일 적용 |
| `MIN_VOLUME_KRW` | 500_000_000 (5억) | **300_000_000 (3억)** | 거래대금 3억 이상 종목 진입 → 117 → 약 150 종목 |

## 2. 제외 사항 (별도 plan)

- **자비스 BTC BUY 단계 추가** — `jarvis_executor.process_strategy`가 종목당 단일 side만 지원. side를 step 레벨로 분리하는 구조 변경 필요. 별도 plan(`20260504_*` 가칭)에서 진행
- 이번 plan은 composite 적극화만 — composite가 117(→150)종목 전체에 DC10 적용하므로 BTC도 자동 매수 후보 포함

## 3. 성공기준

- [ ] `services/execution/config.py` 3개 값 변경
- [ ] `CLAUDE.md` 메인 전략 설명 업데이트 (DC15 → DC10)
- [ ] `pre_deploy_check.py` 통과 (CLAUDE.md ↔ config.py DC 일치 검증 룰 통과)
- [ ] 서버 배포 + btc-trader.service 재시작 (레벨 갱신 시 종목 수 117 → 약 150 확인)
- [ ] 24h 후 거래 발생 건수 측정 (목표: 직전 26일 0건 → 24h 1~3건)

## 4. 위험

| 리스크 | 완화 |
|---|---|
| DC10이 가짜 돌파 신호 ↑ | 하드 손절 -10% 캡 + 5연패 자동 중단 유지 |
| 거래대금 작은 알트(3~5억) 진입 시 슬리피지 | ATR 필터(8%) + 변동성 큰 종목 자동 제외 |
| 더 많은 종목 감시로 메모리 ↑ | 시스템 헬스체크에서 mem/swap 모니터링 (현재 mem 83%, swap 50% — 임계치 근접) |

## 5. 즉시 롤백 절차

```python
# config.py
DONCHIAN_PERIOD = 15
STRATEGY_KWARGS = {"dc_period": 15}
MIN_VOLUME_KRW = 500_000_000
```
+ deploy + restart. ~2분.

## 6. 회고 (작업 후 작성)
