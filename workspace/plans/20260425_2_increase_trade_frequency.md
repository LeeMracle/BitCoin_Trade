# 거래 빈도 + 수익 증대 — 진입조건 완화 + 종목풀 확대 검증

- **작성일(KST)**: 2026-04-25 17:30
- **종료일(KST)**: 2026-04-25 18:30 (실 소요 약 1시간)
- **상태**: ✅ 검증 완료 / **운영 적용 사용자 승인 대기**
- **작성자/세션**: Claude Code (자비스)
- **예상 소요 / 실소요**: 2~3시간 / **약 1시간**
- **산출물**: 보고서 [workspace/reports/20260425_2_increase_trade_frequency.md](../reports/20260425_2_increase_trade_frequency.md), 백테스트 [scripts/backtest_entry_relaxation.py](../../scripts/backtest_entry_relaxation.py)
- **관련 이슈/결정문서**:
  - 사용자 의도: (a) 수익 절댓값↑ + (b) 거래 빈도↑
  - 직전 검증: [workspace/plans/20260425_buy_gate_threshold_validation.md](20260425_buy_gate_threshold_validation.md) (현행 EMA200 게이트는 OOS 단독 1위 → 게이트 풀기 반려)
  - [docs/decisions/20260329_daytrading_postmortem_and_switch.md](../../docs/decisions/20260329_daytrading_postmortem_and_switch.md)

## 1. 목표

EMA200 게이트는 그대로 유지하면서, 거래 빈도와 수익 절댓값을 늘리는 두 레버의 효과를 정량 검증한다.

1. **레버 A — composite 진입 조건 완화**: RSI 임계 50→45/40, Vol×1.5→1.2/1.0 그리드 백테스트
2. **레버 B — 종목 풀 확대**: 현재 스윙 5/5 한도, 거래량·DEAD_MARKETS 기준 후보 도출

## 2. 성공기준 (Acceptance Criteria) — 7/7 충족, **PASS**

- [x] 현재 종목 풀·진입 조건·스윙 한도 파악 (scanner/config/multi_trader)
  - MAX_POSITIONS=5 (스윙), VB_MAX=3 (DRY-RUN), MIN_VOLUME=500M, DEAD={SOLO,XCORE}
  - **HIGH 발견**: multi_trader.py:33이 config.py 참조 없이 자체 상수
- [x] 레버 A: 6 조합(RSI×Vol) 백테스트 결과표
  - G1~G6 모두 동일 결과 → 레버 A는 BTC 단일 환경에서 **무효**
  - cto가 데이터로 입증: BTC DC20 돌파 180회 중 RSI>50 = 180/180 (100%)
- [x] 레버 B: 캐시 109종목 중 후보군(거래량 상위, DEAD 제외) 식별
  - 거래대금 100억+/일 종목 20+개, DEAD={SOLO,XCORE} 2종만
- [x] 두 레버 각각의 거래수·총수익·Sharpe 변화 정량 비교 (보고서 §1, §2)
- [x] 권장 운영 변경안 도출 (단계적 적용 순서 포함)
  - 1순위: MAX_POSITIONS 5→7 (config.py + multi_trader.py 동시)
  - 2순위: 5→10 (선택)
  - 보류: VB LIVE 승격 (BULL 의존)
  - 후속: 멀티 알트 백테스트 (1차 1~2일, 2차 1주)
- [x] 교차검증 1건 (cto review) — 4개 이슈, HIGH 1 + MEDIUM 2 + LOW 1, 모두 보고서 반영
- [x] 운영 적용은 본 plan 범위 외 — **사용자 승인 후 별도 진행** (현 상태)

## 3. 단계

1. 현황 파악 (15분)
2. 레버 A 백테스트 코드 + 실행 (1시간)
3. 레버 B 후보 분석 (30분)
4. 보고서 작성 (30~45분)
5. 교차검증 (15분)
6. 마무리·텔레그램 (15분)

## 4. 리스크

- 진입 조건 완화 시 가짜 돌파 비율↑ → 승률·Sharpe 하락 가능
- 종목 풀 확대 시 유동성 낮은 종목의 슬리피지 증가
- 백테스트는 BTC 단일 자산이라 멀티 알트 외삽 한계 (직전 검증과 동일)

## 5. 검증 주체

- [x] 옵션 B — 서브에이전트(`cto` review)

## 6. 회고

- **결과**: PASS (검증 완료, 운영 적용은 사용자 승인 대기)
- **원인 귀속**: 해당 없음 (계획대로 진행, cto review HIGH 이슈 반영)
- **한 줄 회고**: 사용자의 "조건 완화" 요청을 정량 검증한 결과 *BTC 단일 환경에선 임계값 변경이 무효*임이 데이터로 입증됨 — 거래 빈도 증대의 본질은 **슬롯 한도(MAX_POSITIONS)** 와 **종목 다변화**에 있다는 결론 도출. cto가 multi_trader.py 하드코딩(HIGH) 발견하여 운영 적용 시 양쪽 동시 수정 필수 명시.
- **후속 조치**:
  - 사용자 승인 시 두 파일 동시 수정 (config.py:56 + multi_trader.py:33)
  - lessons 신규 등재 후보: "config.py 상수의 자체 import 누락 패턴" — 교훈 #4 변종이나 import 통일이 근본 해결
  - 후속 plan: 멀티 알트 백테스트 (1차 1~2일 단순 합산, 2차 1주 풀 엔진)
  - VB LIVE 승격 ADR (P5-04)는 BULL 복귀 후 별도 처리

### 검증 기록
```
검증 주체: B (cto review 서브에이전트)
확인 항목: 5개 (레버A 해석·B 정량근거·누락위험·VB결론·후속plan식별)
발견 이슈: 4개 (HIGH 1 + MEDIUM 2 + LOW 1)
  - [HIGH]   MAX_POSITIONS 하드코딩 (multi_trader.py:33) → §6 양쪽 수정 명시 반영
  - [MEDIUM] 5→7 효과 기대치 정확화 (평균 보유율 2/5 → 효과는 신호 5초과 일자만) → §6 보정 추가
  - [MEDIUM] 빠른 대안(top3~5 알트 단일 합산 1~2일) 미제시 → §6 표 보강
  - [LOW]    슬리피지/체결 부하·교훈 #5/#13 명시 → §6 후속 작업에 보강
판정: 조건부 PASS → HIGH 반영 후 PASS
```
