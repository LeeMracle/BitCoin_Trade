# BEAR 레짐에서 거래 활성화 — REGIME_FILTER + VB_BEAR_MARKET_FILTER 해제

- **작성일(KST)**: 2026-05-03 11:05
- **작성자/세션**: 자비스(Claude Opus 4.7)
- **요청자 의도**: "승률이 낮아도 되니, 거래 가능하도록 조치"
- **예상 소요**: 30분
- **관련 plan**: 보고체계 v2 [20260503_1](20260503_1_reporting_v2_and_rate_limit.md)

## 1. 목표

BEAR 레짐에서 신규 매수가 차단된 상태(현재 BTC < EMA200 -5.1%)를 해제하여 **거래 발화 가능 상태**로 전환. 백테스트 목표치 미달은 사용자 명시 수용.

## 2. 현재 차단 구조 분석

| 게이트 | 현재 상태 | 차단 여부 | 해제 안 |
|---|---|---|---|
| EMA200 필터 (composite) | `REGIME_FILTER_ENABLED=True` | **차단** (BEAR 시 전종목 매수 차단) | **False로 변경** |
| F&G 게이트 (composite) | `_fg_value < 20` 차단 | 통과 (현재 47) | 변경 안 함 |
| DC15 돌파 신호 | 매수 신호 자체 (전략) | 신호 발화 시 매수 | 변경 안 함 |
| ATR 필터 (composite) | `MAX_ATR_PCT=0.08` | 정상 종목 통과 | 변경 안 함 |
| VB BEAR 필터 | `VB_BEAR_MARKET_FILTER=True` | **차단** (BEAR 시 VB 진입 차단) | **False로 변경** |
| VB DRY-RUN | `VB_DRY_RUN=True` | 가상매매 | **유지** (실거래 미전환) |

유지되는 안전장치:
- 서킷브레이커 L1 (-20% 신규 차단) / L2 (-25% 전량 청산)
- 하드 손절 캡 `HARD_STOP_LOSS_PCT=0.10` (단일 포지션 -10% 한도)
- ATR 필터 `MAX_ATR_PCT=0.08` (고변동 종목 차단)
- 슬롯 한도 `MAX_POSITIONS=7`
- VB 손절 `VB_SL_PCT=0.020`
- 연패 쿨다운, dead symbol 영구 제외 등

## 3. 성공기준

- [ ] `services/execution/config.py` 2개 파라미터 변경 (`REGIME_FILTER_ENABLED=False`, `VB_BEAR_MARKET_FILTER=False`)
- [ ] CLAUDE.md "현재 단계: Phase 3" 섹션에 변경 명시 + lessons에 "거래 활성화 결정" 기록
- [ ] 서버 배포 + btc-trader.service 재시작
- [ ] 재시작 후 로그에서 "EMA200 필터" 차단 메시지 없음 확인
- [ ] 사용자에게 텔레그램 보고 (변경 내용 + 위험 안내 + 즉시 회복 절차)

## 4. 위험 평가 (사용자 인지 사항)

| 위험 | 영향 | 완화 |
|---|---|---|
| BEAR 매수로 추가 손실 | 누적 -28% → 더 깊어질 가능성 | 하드 손절 -10% 캡 + 서킷브레이커 -20%/-25% 자동 발동 |
| 가용 KRW 112k 전부 슬롯 분배 | 슬롯당 ~21k, 손실 가능 한도 ~12k | 슬롯 7개 한도, 분산 효과 |
| 백테스트 미달 (승률 25% < 목표 35%) | 이미 미달 상태, 사용자 명시 수용 | 운영 1주 후 재평가 (자동 5연패 중단) |
| VB BEAR 진입 → DRY-RUN 누적 손실 표시 증가 | 가상매매라 실금융 영향 0 | DRY-RUN 유지로 격리 |

**즉시 롤백 절차** (필요 시):
```python
# config.py
REGIME_FILTER_ENABLED = True
VB_BEAR_MARKET_FILTER = True
```
+ deploy + restart. 약 2분 소요.

## 5. 단계

1. plan 작성 (이 단계)
2. `services/execution/config.py` 수정
3. CLAUDE.md 메인 전략 설명 업데이트
4. `pre_deploy_check.py` 통과 확인
5. 서버 scp + `sudo systemctl restart btc-trader`
6. 5분 후 로그 확인 — EMA200 차단 메시지 없음 + 정상 거래 시도 흔적
7. 텔레그램 보고 (변경 내용 + 위험 + 롤백 절차)

## 6. 검증 주체

- [x] 옵션 D — 사용자 명시 승인 (즉시 요청)
- [ ] 옵션 C — `pre_deploy_check.py` 자동 검증
- [ ] 옵션 A — 24h 운영 후 별도 회고

> 본 plan은 단순 config 토글 + 사용자 명시 요청. cto review는 plan 단계 생략, 운영 1주 후 회고 시 평가.

## 7. 회고 (작성 예정)

- 결과: (운영 후)
- 거래 발화 건수: (24h 후 집계)
- 누적 P/L 변화: (1주 후)
