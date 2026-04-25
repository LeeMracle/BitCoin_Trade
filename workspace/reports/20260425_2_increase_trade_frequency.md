# 거래 빈도 + 수익 증대 — 진입조건 완화 + 종목풀 확대 검증 보고서

- **작성일**: 2026-04-25 KST
- **plan**: [workspace/plans/20260425_2_increase_trade_frequency.md](../plans/20260425_2_increase_trade_frequency.md)
- **사용자 의도**: (a) 수익 절댓값↑ + (b) 거래 빈도↑

## 1. 핵심 발견 — 우선 알아둘 것

### ❗ BTC 단일 백테스트로는 "진입 조건 완화" 효과 측정 불가

레버 A로 RSI/Vol 임계값을 6 조합 그리드 비교한 결과:

| 시나리오 | RSI | Vol× | 총수익% | Sharpe | 거래수 |
|---|---:|---:|---:|---:|---:|
| G1 baseline | 50 | 1.5 | 101.99 | 1.258 | 9 |
| G2 | 50 | 1.2 | 101.99 | 1.258 | 9 |
| G3 | 50 | 1.0 | 101.99 | 1.258 | 9 |
| G4 | 45 | 1.5 | 101.99 | 1.258 | 9 |
| G5 | 45 | 1.2 | 101.99 | 1.258 | 9 |
| G6 aggressive | 40 | 1.0 | 101.99 | 1.258 | 9 |

**모든 시나리오가 완전히 동일한 결과**.

### 원인
BTC의 DC20 상단 돌파는 변동성이 큰 사건이라, 돌파 시점에 이미:
- RSI(10) ≫ 50 (모멘텀 확실)
- Volume ≫ vol_sma × 1.5 (거래량 급증)

→ RSI/Vol 조건은 BTC 환경에서는 *추가 차단 효과 0*. 임계값을 40으로 낮춰도 동일.

### 함의
- **알트 환경에서는 효과가 있을 수 있다** (변동성 패턴 다름).
- 그러나 멀티 알트 백테스트 인프라가 현재 없음 → BTC 단일 검증으로는 결론 불가.
- → 레버 A는 *알트 멀티 백테스트 후속 작업*으로 분리.

## 2. 거래 빈도 증대의 직접적 경로 — 레버 B (종목 풀)

### 현재 운영 상태
| 항목 | 값 | 비고 |
|---|---|---|
| MAX_POSITIONS | **5** (스윙) | 동시 보유 한도 |
| VB_MAX_POSITIONS | 3 (DRY-RUN) | 04-10 이후 거래 0건 |
| MIN_VOLUME_KRW | **500M** (5억) | "공격적" 주석 (10억→5억 단축됨) |
| MIN_LISTING_DAYS | 60 | DC20 + 여유 |
| DEAD_MARKETS | {SOLO, XCORE} | 2종목만 영구 제외 |
| HARD_STOP_LOSS_PCT | 10% | 단일 포지션 최대 손실 |
| MAX_ATR_PCT | 8% | 변동성 캡 |
| 후보군 규모 | 100+ 종목 | 거래대금 5억↑ 통과 |

### 거래대금 상위 종목 (최근 30일 평균, 캐시 기준)

| 순위 | 종목 | 평균 거래대금 |
|---|---|---:|
| 1 | BTC | 1,494억/일 |
| 2 | XRP | 1,291억/일 |
| 3 | ETH | 957억/일 |
| 4 | ONT | 643억/일 |
| 5~10 | JST, KAT, SOL, PROVE, TAO, SIGN | 240~430억/일 |
| 11~20 | SUPER, KITE, DOGE, SAHARA, CPOOL, NOM, ONDO, BARD, KNC, BLUR | 100~230억/일 |
| 21~30 | ADA, DKA, IP, POLYX, ATH, VIRTUAL, SUI, CFG, ENSO, WLD | 50~120억/일 |

→ 충분한 후보군 존재. 슬롯 한도가 진짜 병목.

## 3. 거래 빈도 늘리는 옵션 (위험도·효과 비교)

| # | 옵션 | 거래수 영향 | 수익 영향 | 위험 | 즉시성 | 추천 |
|---|---|---|---|---|---|---|
| **B1** | **MAX_POSITIONS 5 → 7** | +40% (이론) | 분산 효과↑ | 낮음 (슬롯당 자본↓ 14%) | 즉시 가능 | ★★★ |
| B2 | MAX_POSITIONS 5 → 10 | +100% (이론) | 분산 효과↑↑ | 중 (슬롯당 자본↓ 50%, 슬리피지↑) | 즉시 | ★★ |
| **B3** | **VB DRY-RUN → LIVE** | +3 슬롯 추가 | 일중 회전 추가 | 중 (P5-04 ADR 부재, 04-10 이후 0건) | 1일 (ADR 작성 후) | ★★ |
| B4 | MIN_VOLUME_KRW 500M → 200M | 후보군 +50% | 변동성↑ | 중 (슬리피지·DEAD↑) | 즉시 | ★ |
| B5 | DC20 → DC15 (이미 20인데 추가 단축) | 돌파 빈도↑ | 가짜 돌파↑ | 고 | 백테스트 후 | ✗ (근거 없음) |
| A | 알트 멀티 RSI/Vol 완화 | 미상 | 미상 | 중 | 후속 plan (1주) | 별도 |

## 4. 권장 단계 — 가장 안전한 거래 빈도 증대

### 1단계 (즉시 적용 가능, 위험 낮음) — **B1: MAX_POSITIONS 5→7**

- `services/execution/config.py:56` 1줄 변경
- 슬롯당 자본 1/5 → 1/7 (14% 감소). 단일 포지션 손실 영향 작아짐.
- HARD_STOP_LOSS_PCT 10% 유지 → 7슬롯 모두 손절해도 자본 영향 -10%×0.95×7/7 = -9.5% (Circuit Breaker -20% 안전선 안)
- 효과: 최근 평균 보유 2/5(=40%) → 같은 진입율이라도 2/7(=29%)로 떨어지지만, 동시 보유 가능 한도가 늘어 *진입 기회*는 +40%

### 2단계 (1단계 1주일 운영 후) — **B3: VB DRY-RUN → LIVE 승격 검토**

- 현재 VB는 04-10 이후 BTC<EMA200으로 거래 0건. **BULL 복귀 전까지는 LIVE 승격해도 효과 없음**.
- BULL 진입 후 DRY-RUN 재집계 → 승률 35%↑ 확인 시 P5-04 ADR 작성 후 LIVE 승격.
- 즉 *지금 당장 효과 없음*. BULL 전환 트리거에 묶여 있음.

### 3단계 (별도 plan, 1주 소요) — **알트 멀티 RSI/Vol 백테스트**

- 캐시 109종목으로 멀티 자산 시뮬 인프라 구축
- composite RSI 50→45, Vol 1.5→1.2 효과 측정
- 현 시점에는 BTC 검증으로는 효과 미입증

## 5. 한계 및 후속 과제

1. **레버 A의 BTC 단일 검증 무효**: 멀티 알트 백테스트 필요 (별도 plan)
2. **MAX_POSITIONS 7 효과의 직접 검증 부재**: 멀티 자산 시뮬 부재로 정량 입증 못 함. 그러나 슬롯 한도가 *직접* 거래수 상한을 결정하므로 정성적으로 명확.
3. **VB LIVE 승격은 BULL 전환 의존**: 현재 BEAR라 즉시 효과 없음.
4. **B4 거래량 임계 완화는 비추천**: SOLO/XCORE 같은 DEAD_MARKETS 추가 발생 위험. 교훈 #13 (ATR*N 스탑 고변동 알트 제어 불능) 재발 가능성.

## 6. 권장 운영 변경 (사용자 승인 대기)

> **⚠️ cto review HIGH 이슈 반영**: MAX_POSITIONS 변경 시 **두 파일 동시 수정 필수** — `multi_trader.py:33`이 config.py를 참조하지 않고 자체 상수로 하드코딩되어 있음. 한 곳만 바꾸면 daily_check 경로(realtime_monitor 외 일일 스캔)는 여전히 5슬롯. 교훈 #4 패턴.

| 우선순위 | 변경 | 파일 | 변경 내용 |
|---|---|---|---|
| **1순위 (즉시) — 두 파일 동시** | MAX_POSITIONS 5→7 | `services/execution/config.py:56` **AND** `services/execution/multi_trader.py:33` | 양쪽 모두 `MAX_POSITIONS = 7` |
| 2순위 (선택) | MAX_POSITIONS 5→10 | 동일 두 파일 | 양쪽 `MAX_POSITIONS = 10` |
| 보류 | VB LIVE 승격 | `config.py:83` `VB_DRY_RUN = False` | BULL 복귀 후 검토 |
| 후속 plan | 알트 멀티 백테스트 | 신규 인프라 | 1차 1~2일 (top 3-5 알트 단일 합산), 2차 1주 (풀 멀티 엔진) |

운영 변경 시 후속 작업:
- multi_trader.py와 config.py 동기화는 별도 lessons 등재 권장 (교훈 #4 재발 방지를 위해 import 통일이 더 근본 해결)
- 변경 후 1주일간 거래 빈도·평균 슬리피지·승률 모니터링
- Circuit Breaker -20% 임계는 그대로 (자본 1/7 분할이라 더 안전, HARD_STOP 7슬롯 동시 손절 시 -9.5%)
- HARD_STOP_LOSS_PCT 10% 캡이 적용된 상태라 ONG 사례(28.7% 손실, 교훈 #13)는 재발 차단됨
- pre_deploy_check 통과 확인 후 배포

### 효과 기대치 보정 (cto MEDIUM 이슈 반영)

- 현재 평균 보유율 **2/5 (40%)** — 슬롯이 60% 비어 있음
- 5→7 확대 효과는 *모든 날 +40%*가 아니라 **"동시 매수 신호가 5개를 초과하는 날에만 +N건"**
- BEAR 레짐 중에는 매수 자체가 적어 효과 미미할 수 있음 → BULL 복귀 후 진가 발현
- 메모리 영향 없음 (감시 풀은 동일, 슬롯 한도만 변경)

## 7. 산출물

- 스크립트: [scripts/backtest_entry_relaxation.py](../../scripts/backtest_entry_relaxation.py)
- 결과: [output/entry_relaxation_summary.json](../../output/entry_relaxation_summary.json), [.md](../../output/entry_relaxation_summary.md)
- plan: [workspace/plans/20260425_2_increase_trade_frequency.md](../plans/20260425_2_increase_trade_frequency.md)

## 8. 교차검증 (cto review 수행)

```
검증 주체: B (cto review 서브에이전트)
확인 항목: 5개
발견 이슈: 4개
  - [HIGH]   MAX_POSITIONS 하드코딩 — multi_trader.py:33 config 미참조 → §6 보강 반영
  - [MEDIUM] 5→7 효과 기대치 정확화 → §6 후속 보정 추가
  - [MEDIUM] 멀티 백테스트 빠른 대안(top3~5 알트 단일 합산 1~2일) → §6 표 보강
  - [LOW]    슬리피지/체결 부하·교훈 #5(메모리)·교훈 #13(HARD_STOP) 명시 보강
판정: 조건부 PASS → HIGH 반영 후 PASS

cto가 데이터로 직접 입증한 점:
- BTC DC20 상단 돌파 180회 중 RSI(10)>50: 180/180 (100%) → 레버 A 무효의 본질 확인
- VB_BEAR_MARKET_FILTER 코드(realtime_monitor.py:1204)로 LIVE 승격 보류 결론 검증
- services/backtest/engine.py 단일 자산 전용 — 멀티 인프라 부재 진단 정확
```
