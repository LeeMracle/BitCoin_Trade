# ADR 20261107-1 — BUY_DISABLED Kill Switch (전략 재설계 완료 전까지 자동 매수 영구 차단)

- **날짜**: 2026-11-07
- **상태**: 승인됨 (Master 결정)
- **결정자**: Master (이정수)
- **발의자**: bata-pm
- **구현자**: bata-engineer
- **관련 lessons**: #6 매수 경로 전수 적용 · #26 신규 안전장치 진입점 열거 · #57 (본 ADR의 kill switch)
- **관련 ADR**: 20260516-2 (레짐 필터) · 20260930-1 (하드 손절 -6%) · 20260824-1/2 (TP)
- **관련 research**:
  - [workspace/research/20261103_1_live_vs_backtest_universe.md](../../workspace/research/20261103_1_live_vs_backtest_universe.md)
  - [workspace/research/20261104_1_pit_universe_result.md](../../workspace/research/20261104_1_pit_universe_result.md)

---

## 1. 결정

`services/execution/config.py` 에 `BUY_DISABLED = True` 상수를 신설하고,
**전략 재설계 완료 + 새 사전 등록 성공 기준 통과 전까지** True 로 영구 유지한다.

```python
# services/execution/config.py
BUY_DISABLED = True
```

적용 범위:
- 실시간 DC 돌파 매수 (`services/execution/realtime_monitor.py::_execute_buy`)
- scanner 매수 (`services/execution/multi_trader.py::run_daily_cycle` 매수 루프)
- (심층 방어) EMA-TREND 는 이미 `EMA_TREND_ENABLED=False`로 폐기(ADR 20261105-1)이나
  `_check_ema_trend` 내부 매수 라인(1702)에도 **도달 불가 코드이지만** 가드 적용 검토 — 현재는 상위 가드로 충분.

매도 경로(트레일/하드캡/TP/서킷 L2)는 **영향 없음** — 기존 포지션은 정상 청산된다.

---

## 2. 근거

### 2.1 실거래 음의 기대값 확정

- **ROADMAP.md §2** — 손절 -6% 창 52건 승률 **30.8% [CI 19.9~44.3%]**, 건당 평균 **-1.85%**
- L1 실행 검증 52/52 체결과 일치 — 장부 오류 아님 ([research/20261030_1](../../workspace/research/20261030_1_l1_execution_audit.md))
- 신규 진입분(기존 7건 제외) 45건 **35.6% [CI 23.2~50.2%]**, 건당 **-0.90%** — 기존 보유 제거해도 음수

### 2.2 시점 일치 백테스트가 실거래를 재현

- **[research/20261103_1](../../workspace/research/20261103_1_live_vs_backtest_universe.md)** — 실거래 신규 45건 중 **41건이 백테스트 종목군(오래된 대형 알트 40종) 밖의 최근 상장 소형 코인**. 백테스트 기대치가 지금 봇에 적용되지 않는다.
- **[research/20261104_1](../../workspace/research/20261104_1_pit_universe_result.md)** — 시점 일치 백테스트: **IS 승률 33.2% / OOS 39.5%, 건당 -1.12% / -0.23%**. 실거래와 동일한 음의 기대값 재현.

### 2.3 사전 등록 개선 후보 전원 미달

- A (거래대금 순위 상한) · B (변동성 상한) · C (종가 확인) — **0/3 미달**
- 이번 IS 로 다시 고르면 과적합. **새 사전 등록 plan** 이 필요하다.

### 2.4 명시적 kill switch 가 필요한 이유

- 현재 상태: BEAR 지속(5일+), 자동 매수 0건 — 자연 차단 중
- BULL 복귀 신호가 언제 올지 모름 — 그 순간 종목군·전략 기대값 양쪽이 문제인 채로 매수가 재개된다
- 레짐 필터는 BULL 복귀 시 매수를 열지만, **열어야 할 전략이 없는 상태**

---

## 3. 재활성 조건 (전수 충족)

1. **새 사전 등록 plan 완료** (bata-investment-expert)
   - B 변동성 상한 + C 종가 확인 결합, 거래대금 순위 상한, 상폐 종목 보정 포함
   - 수집기 0봉 탈락 버그는 수정 완료 ([lessons #52](../lessons/20261104_1_fetcher_late_listing_dropped.md))
2. **OOS 백테스트 성공 기준 통과**:
   - OOS Sharpe ≥ 0.8
   - OOS MDD ≥ -15%
   - 판정 기준은 [docs/strategy_improvement_criteria.md](../strategy_improvement_criteria.md) 준수
3. **신규 ADR 발의 + Master 재승인**
   - ADR 20261107-1 의 재활성 결정은 **별도 ADR**(예: 20261207-X) 로 명시
   - config 에서 `BUY_DISABLED = False` 전환은 Master 명시 승인 후에만

---

## 4. 영향 범위

| 영역 | 영향 |
| --- | --- |
| 기존 포지션 (XAUT 1건) | **없음** — 트레일/하드캡/TP 정상 작동 |
| 신규 자동 매수 | **전면 차단** (모든 경로) |
| 매도 안전장치 | **없음** — 서킷 L2·연패 쿨다운·일일손실 그대로 |
| 레짐 필터 | 영향 없음 — BULL 복귀해도 kill switch 가 매수 차단 |
| ML shadow | 영향 없음 — shadow 수집은 계속 (현재 shadow mode) |
| 백테스트·리서치 | 영향 없음 — 코드 경로 독립 |

---

## 5. 구현 체크리스트

- [x] `services/execution/config.py` : `BUY_DISABLED = True` 상수 추가
- [x] `services/execution/realtime_monitor.py::_execute_buy` : 가드 추가 (lessons #14 throttle 포함)
- [x] `services/execution/multi_trader.py` scanner 매수 루프 : 가드 추가
- [x] `scripts/pre_deploy_check.py` : `check_buy_disabled_all_entry_paths()` AST 룰 등재
- [x] `docs/lessons/20261107_1_buy_disabled_kill_switch.md` : lessons #57 기록
- [x] `docs/ROADMAP.md` : §3 ①번 상태 교체
- [x] 배포 + 사후 로그 검증

---

## 6. 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| A. 레짐 필터 BEAR 유지만으로 방어 | 레짐 전환 시점 불확실 — BULL 복귀 즉시 매수 재개되어 재발 |
| B. 서비스 전체 정지 (`systemctl stop`) | 매도 안전장치(트레일/하드캡/TP)까지 멈춤 — 기존 포지션 보호 소실 |
| C. `DRY_RUN=True` 전환 | 매수 신호 자체는 발생, 상태 저장·dedupe 동작 — 운영 혼선 |
| D. `MAX_POSITIONS=0` | 기존 포지션 처리 로직에 부작용 가능, 의도 불명확 |
| **E. `BUY_DISABLED` kill switch (채택)** | 의도 명확 · 기존 포지션 미영향 · AST 룰로 우회 차단 · reversible |

---

## 7. Reversible 보증

- `BUY_DISABLED = False` 한 줄만 바꾸면 즉시 복귀 가능
- 그러나 재활성 조건 §3 을 충족하지 않은 변경은 **ADR 위반**
- `pre_deploy_check.check_buy_disabled_all_entry_paths()` 가 상수 존재와 가드 AST 둘 다 검증 — 상수 자체를 지우는 우회도 차단
