# 신규매수 게이트 임계값/해제조건 백테스트 검증

- **작성일(KST)**: 2026-04-25 15:30
- **종료일(KST)**: 2026-04-25 17:00 (실 소요 약 1.5시간 — 기존 `backtest_regime_filters.py` 패턴 재사용으로 단축)
- **상태**: ✅ **완료 (PASS)**
- **작성자/세션**: Claude Code (자비스)
- **예상 소요 / 실소요**: 3~4시간 / **약 1.5시간**
- **관련 이슈/결정문서**:
  - 사용자 의문: BEAR 레짐 게이트(BTC>EMA200) 차단 기준의 합리성
  - [docs/decisions/20260329_daytrading_postmortem_and_switch.md](../../docs/decisions/20260329_daytrading_postmortem_and_switch.md)
  - [docs/lessons/20260329_2_backtest_period_bias.md](../../docs/lessons/20260329_2_backtest_period_bias.md)
- **산출물**:
  - 보고서: [workspace/reports/20260425_buy_gate_threshold_validation.md](../reports/20260425_buy_gate_threshold_validation.md)
  - 스크립트: [scripts/backtest_buy_gate_validation.py](../../scripts/backtest_buy_gate_validation.py)
  - 결과: [output/buy_gate_validation_summary.json](../../output/buy_gate_validation_summary.json), [.md](../../output/buy_gate_validation_summary.md)

## 1. 목표

신규 매수 차단 기준의 두 가지 약한 근거를 정량 검증한다.

1. **임계값 검증**: BTC 추세 필터의 EMA200 vs EMA150 vs SMA50 비교 (현 EMA200 선택의 직관적 근거를 백테스트로 보강)
2. **해제 조건 검증**: BEAR→BULL 전환 비대칭(BULL은 AND, BEAR는 OR) 완화 시나리오의 효과 측정

## 2. 성공기준 (Acceptance Criteria)

작업 종료 시 이 체크박스가 모두 채워지면 PASS. → **6/6 충족, PASS**

- [x] composite 전략에 ema_period 파라미터화된 백테스트 코드 (또는 기존 코드 재사용 경로) 확보
  - `make_composite_with_mask(entry_mask, ...)` 신규 작성, entry_mask 주입 방식으로 임계값 자유롭게 변경 가능 (기존 `backtest_regime_filters.py`의 패턴 재사용)
- [x] 6개 시나리오 백테스트 완료 (IS 2018-06~2023-12 / OOS 2024-01~2026-04 / BEAR-OOS 분리)
  - S1 EMA200 / S2 EMA150 / S3 SMA50 / S4 OFF / S5 OR_FG40 / S6 3DAY_CONSEC 모두 실행 완료
  - 단, S5/S6 명명은 보고서상 "BEAR→BULL 해제" 측면이라기보다 "BEAR 게이트 완화/지연 진입" 측면이 더 정확 (cto review 지적, LOW 이슈로 보고서에 주석 반영)
- [x] 각 시나리오의 메트릭(Sharpe, MDD, 승률, 총수익, 거래수, BEAR구간 별도)을 표로 비교
  - OOS / IS / BEAR-OOS 3종 표 작성 (보고서 §2, §3, §2.1)
- [x] 보고서 `workspace/reports/20260425_buy_gate_threshold_validation.md` 작성
- [x] 권장사항 명확히 도출
  - **현행 EMA200 게이트 유지** (OOS Sharpe 1.258·Calmar 3.867 단독 1위)
  - 임계값 변경(EMA150/SMA50) 반려, 완화(OR_FG40)·강화(3일 연속) 모두 반려
- [x] 교차검증 1건 완료
  - cto review 서브에이전트, 조건부 PASS → MEDIUM 2건 보강 → PASS

## 3. 단계

1. ✅ **사전 조사 (실소요 ~10분)**
   - `services/strategies/advanced.py` `make_strategy_composite` — `btc_above_sma` 외부 boolean 주입 구조 확인
   - `services/backtest/engine.py` — bar-close 신호 → 다음 bar 시가 체결, look-ahead 없음
   - 기존 `scripts/backtest_regime_filters.py` 의 entry_mask 패턴 재사용 결정

2. ✅ **백테스트 코드 준비 (실소요 ~30분)**
   - `scripts/backtest_buy_gate_validation.py` 신규 작성
   - 6개 entry_mask 빌더(`mask_close_below_ma`, `mask_close_below_sma`, `mask_or_relaxed`, `mask_consec_above_ema200`)
   - `data/cache.duckdb` BTC/KRW 일봉 3,131봉(2017-10~2026-04-25), F&G 2,984일

3. ✅ **백테스트 실행 (실소요 ~5분)**
   - S1~S6 각각 실행 (각 백테스트 5초 미만, 매우 빠름)
   - 1차 결과에서 메트릭 매핑 버그(필드명 `n_trades` → `trade_count`로 잘못 호출) 발견 → 수정 후 재실행
   - JSON/MD 산출

4. ✅ **분석 및 보고서 (실소요 ~30분)**
   - `workspace/reports/20260425_buy_gate_threshold_validation.md` 작성
   - OOS / IS / BEAR-OOS 3종 표 + 시나리오 정의 + 한계 + 권장 액션

5. ✅ **교차검증 (실소요 ~10분)**
   - cto review 서브에이전트, 조건부 PASS, 4개 이슈 (LOW 2 + MEDIUM 2)
   - MEDIUM 2건은 보고서 §3·§4에 톤 다운 + BEAR=0 해석 보강

6. ✅ **마무리 (실소요 ~10분)**
   - `docs/00.보고/20260425_일일작업.md` "작업 결과" 표 갱신
   - 텔레그램 결과 보고 1건 송출 (`output/telegram_gate_validation_20260425.py`)
   - lessons 신규 기록 미작성 (오류·실패가 아닌 검증 성공 사례)

## 4. 리스크 & 사전 확인사항 (회고 반영)

- ✅ **리스크 1** (해결): composite 코드가 `btc_above_sma` boolean 외부 주입 구조라 entry_mask 어댑터 패턴 그대로 적용 가능. 기존 `backtest_regime_filters.py`의 `_make_composite_dc20_with_filter` 패턴을 신규 함수로 분리.
- ✅ **리스크 2** (해결): EMA/SMA는 런타임 pandas 계산 (캐시 컬럼 불필요).
- ⚠️ **리스크 3** (실현): BEAR-OOS 거래수 0~3건으로 통계 유의성 약함 — 보고서 §5에 한계 명시, 결론은 *방향성* 해석. cto review에서도 동일 지적 (MEDIUM 이슈 #2·#3 → 보강 반영).
- ✅ **리스크 4** (해결): cto review 서브에이전트로 5개 항목 검토, 4개 이슈 발견·반영.
- **신규 발생 리스크**: 1차 실행 시 metrics_to_dict 필드명 매핑 버그 (n_trades vs trade_count) — 거래수=0으로 표시되는 현상 즉시 발견·수정. lessons 등재할 수준은 아니나 메트릭 모델 필드명 일관 점검 필요성 시사.

## 5. 검증 주체 (교차검증) — 수행 완료

정책: [docs/cross_review_policy.md](../../docs/cross_review_policy.md)

- [ ] 옵션 A — 별도 세션
- [x] **옵션 B — 서브에이전트(`cto` review)** ✅ 수행
- [ ] 옵션 C — 자동 검증 스크립트
- [ ] 옵션 D — 다른 모델 (선택)

**검증 기록**
```
검증 주체: B (cto review 서브에이전트)
확인 항목: 5개 (방법론·메트릭·강건성·누락·산출물 무결성)
발견 이슈: 4개
  - [LOW]    S6 명명 "강화"→"지연 진입"이 정확
  - [MEDIUM] §4 단정 톤: Sharpe 차이 0.088로 "그렇다" 단정은 §5 한계와 상충 → 보고서 §4 보강
  - [MEDIUM] BEAR=0 해석 모호: 차단력 vs 진입기회 감소 분리 미명시 → 보고서 §4 우회 입증 단락 추가
  - [LOW]    IS Calmar 25~40대: OOS와 단위 직접 비교 불가 → §3 주석 추가
판정: 조건부 PASS → 보강 후 PASS
```

## 6. 회고

- **결과**: PASS (조건부 PASS → MEDIUM 이슈 2건 반영 후 무조건부 PASS)
- **원인 귀속**: 해당 없음 (계획대로 진행, cto review에서 톤·해석 보강 권고 수렴)
- **한 줄 회고**: 사용자 의문에 대해 6 시나리오 동일조건 비교로 "현행 EMA200 유지가 OOS 단독 1위" 방향성 확인 — 표본 9~12 한계 명시 + cto review 보강 반영으로 결론 신뢰도 확보.
- **후속 조치**:
  - lessons 신규 기록은 미작성 (오류·실패가 아니라 검증 성공 사례) — `docs/decisions/` 에 합산하거나 W18 ADR P5-04에 인용
  - 후속 plan: 멀티 알트 환경에서 BTC 게이트 효과, F&G 게이트 결합 효과는 별도 검증 (Phase 6/7 또는 W18 후보)
  - 운영 변경 없음 — 게이트 코드·파라미터 그대로 유지
  - 텔레그램 결과 보고 1건 송출
  - 일일작업(`docs/00.보고/20260425_일일작업.md`) "작업 결과" 표 갱신

### 검증 기록
```
검증 주체: B (cto review 서브에이전트)
확인 항목: 5개
발견 이슈: 4개 (LOW 2 + MEDIUM 2)
  - [LOW]    S6 명명 "강화"→"지연 진입"
  - [MEDIUM] §4 단정 톤 다운 → 반영
  - [MEDIUM] BEAR=0 해석 명시화 → 반영
  - [LOW]    IS Calmar 단위 주석 → 반영
판정: 조건부 PASS → 보강 후 PASS
```
