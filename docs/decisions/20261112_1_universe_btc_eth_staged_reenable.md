# ADR 20261112-1 — 모집단 BTC+ETH 축소 + 단계적 매수 재활성 (섀도 → 최소주문 → 정상 비중)

- **날짜**: 2026-11-12
- **상태**: **승인됨 (Master 2026-11-12)** — ADR 채택 + 단계 0 착수. 단계 1 금액 **12,000원** 채택(TP1 부분청산 20% 여유)
- **결정자**: Master (이정수)
- **발의자**: bata-pm (bata-investment-expert 산출물 기반)
- **구현자**: bata-engineer
- **관련 ADR**: 20261107-1 (BUY_DISABLED kill switch — 본 ADR 이 §3 "재활성 별도 ADR") · 20260505-1 (DC12) · 20260930-1 (하드캡 -6%) · 20260824-1/2 (TP) · 20260825-1 (슬롯 15) · 20260516-2 (레짐)
- **관련 plan/research**: [plan 20261110_1](../../workspace/plans/20261110_1_universe_reduction.md) · [research 20261112_1](../../workspace/research/20261112_1_universe_reduction_result.md) (cto 조건부 PASS)
- **관련 lessons**: #1 (봉 마감 레벨) · #6/#26 (매수 경로 전수) · #19 (config import) · #4 (CLAUDE.md↔config 동기화)

---

## 1. 결정

### 1-1. 전략 모집단을 **BTC/KRW + ETH/KRW 2종목**으로 고정한다

사전 등록 백테스트(plan 20261110_1)에서 유일하게 IS Sharpe 최대로 선택되고 OOS 7기준을 전수 통과한 셀 **U2-S1** 을 그대로 채택한다. 전략 규칙은 바꾸지 않는다.

| 항목 | 현행 | 변경 | 근거 |
| --- | --- | --- | --- |
| 종목 | `MIN_VOLUME_KRW` 5억 풀 (~117) | **화이트리스트 `['BTC/KRW','ETH/KRW']`** (신설 상수) | U2-S1 |
| 슬롯 `MAX_POSITIONS` | 15 | **2** | 종목 수 = 슬롯 수 |
| 단일 종목 비중 `MAX_POSITION_WEIGHT` | 0.10 | **0.475** (= `POSITION_RATIO 0.95 / 2`) | plan §9 ① Master 승인 |
| 진입 DC12 · 손절 ATR×3 + 하드캡 -6% · TP1 +5.5%/50% · TP2 +10% · 레짐 BTC>EMA200 · `MAX_ATR_PCT` · 거래량 필터 | 유지 | **유지** | 백테스트와 동일 조건 |
| 진입 경로 | 완성봉 DC12 상단 + 틱 돌파 시 시장가 (`realtime_monitor`) | **유지** | cto M3 — 통과한 경로가 이것. 봉 마감 익일 시가 진입은 IS n=27 미달 |

### 1-2. `BUY_DISABLED` 는 **3단계**로 푼다. 각 단계 진입은 Master 명시 승인

| 단계 | 주문 | 목적 | 통과 기준 (사전 등록, plan 20261112_1) | 기간 상한 |
| --- | --- | --- | --- | --- |
| **0 섀도** | 없음 (`BUY_DISABLED=True` 유지) | 봇이 매일 산출하는 BTC·ETH DC12 상단·레짐·ATR 이 백테스트 코드와 **일치**하는지 | 레벨 불일치 0건 / 14일 | 14일 (신호 불필요) |
| **1 최소주문** | **12,000원/건** (`MICRO_ORDER_KRW` — TP1 50% 부분청산 하한 10,000원 + 20% 여유, Master 결정) | **실제 슬리피지 실측** | 신호 5건 · 평균 진입 슬리피지 **< 0.2%** · 체결 경로 L1 일치 5/5 | 신호 5건 또는 90일 |
| **2 정상 비중** | 종목당 0.475 | 운영 | 별도 Master 승인 | — |

**중단선 (어느 단계든)**: 평균 진입 슬리피지 ≥ 0.2% → 즉시 중단, `BUY_DISABLED=True` 복귀. 0.2% 는 cto 민감도에서 **IS 근거가 사라지는 지점**이다(IS Sharpe 0.82→0.74).

단계 1·2 의 **손익으로 전략을 판정하지 않는다** — 월 1.3건 전략이라 표본이 모이지 않고, 2023년처럼 독립 창 Sharpe 0.46 인 해가 있다. 단계 1 의 질문은 "체결이 백테스트와 같은가" 하나다.

---

## 2. 근거

- **모집단이 원인이었다**: 같은 DC12 가 PIT 전체 모집단 IS 건당 -1.12%(v1) · 메이저10 -0.12% · **BTC+ETH +2.23%**. 실거래 손실 52건 중 41건이 소형 신규 상장 코인이었다(research 20261103_1).
- **OOS 1회 전수 통과**: Sharpe 1.31 · MDD -12.9% · n=62 · PF 2.31 · 승률 64.5% [52~75]. lookahead(cto M1) 수정 후에도 Sharpe 1.22 · n=60 통과. 보유 대비 수익은 낮으나(+86% vs +233%) MDD 를 -57%→-13% 로 줄인다 — 자본 26만원·서킷 -20% 구조에 맞는 쪽.
- **남은 불확실성은 슬리피지 하나**: IS 여유가 얇아 진입 슬리피지 0.2% 에서 IS 근거가 소진된다. 가상 매매는 슬리피지를 잴 수 없으므로 **최소주문 실전**으로 잰다. 건당 최대 손실 = 10,000 × 6% = **600원**.
- **섀도가 먼저인 이유**: 지금 레짐이 BEAR 라 신호 0건. 그 기간에 신호 없이도 검증 가능한 것(레벨 일치)을 끝낸다.

---

## 3. 영향 범위

| 영역 | 영향 |
| --- | --- |
| 기존 포지션 | 없음 (0/15) |
| 신규 매수 | 단계 0: 없음 · 단계 1: BTC·ETH 각 10,000원 상한 · 단계 2: 0.475 |
| 매도 안전장치 | 변경 없음 (서킷 -20/-25 · 일일 -3% · 5연패 72h) — 백테스트 Guards ON=OFF 로 미발동 확인 |
| ML shadow · `regime_state.json` · EMA-TREND | 영향 없음 |
| 알림 | 단계 1 체결마다 "트리거가 / 체결가 / 슬리피지" 텔레그램 1줄 추가 |

---

## 4. 구현 체크리스트 (bata-engineer, plan 20261112_1)

- [x] `config.py`: `SYMBOL_WHITELIST`(신설) · `MAX_POSITIONS=2` · `MAX_POSITION_WEIGHT=0.475` · `MICRO_ORDER_KRW=12_000` + `MICRO_ORDER_MODE` 플래그
- [x] scanner(`get_krw_market_coins` 1차 관문) · `realtime_monitor._execute_buy` · `multi_trader.run` 화이트리스트 (lessons #6/#26)
- [x] 섀도 로그: `_refresh_levels` 직후 BTC·ETH `upper / atr / close / regime_above / btc_close / btc_ema` → `workspace/shadow_levels.jsonl` (`_write_shadow_levels`) · 대조기 `scripts/shadow_level_check.py` (+`--selftest` 역방향 6/6)
- [x] 체결 계측: 포지션에 `trigger_price · trigger_upper · slip_pct · signal_ts`, 매수 알림에 슬리피지 1줄
- [x] `pre_deploy_check.py`: `check_symbol_whitelist_all_entry_paths` · `check_weight_slots_budget` · `check_micro_order_stage_gate` (AST, 역방향 `tests/scripts/test_stage_gate_rules.py` 14건)
- [x] `docs/lessons/20261112_1` (#58) `build_panel` 당일값 lookahead + 영향 연구 4건
- [x] CLAUDE.md 파라미터 표(슬롯·종목 풀·재활성 단계) 갱신
- [ ] 구현 cto review → `deploy_to_aws.sh` → 서버 `pre_deploy_check` PASS → 섀도 14일

---

## 5. 대안 검토

| 대안 | 기각 사유 |
| --- | --- |
| A. 즉시 `BUY_DISABLED=False`, 정상 비중 | 슬리피지 미실측. IS 여유 0.2% 에 근거를 거는 셈 |
| B. 장기 페이퍼 트레이딩 n=30 | 월 1.3건 → 약 2년. 가상 매매는 슬리피지를 못 잼 — 답할 수 없는 질문에 시간을 씀 |
| C. U3-S2(메이저10 채널) 병행 | 사전 등록 선택 규칙 위반(IS Sharpe 2위). MDD -46% 로 자본 구조 불일치 |
| D. 봉 마감 확인 후 익일 시가 진입으로 변경 | 다른 전략. IS n=27 < 30 미통과 (cto M3) |
| **E. 3단계 재활성 (채택)** | 신호 없이 시작 가능(섀도) · 위험 600원/건으로 실측 · 중단선 사전 고정 |

---

## 6. Reversible 보증

- 각 단계는 `BUY_DISABLED=True` 한 줄로 복귀. 화이트리스트·슬롯 변경은 포지션 0 상태에서 적용하므로 소급 문제 없음
- 단계 전환은 **config 변경 + Master 승인 기록(본 ADR §7 갱신)** 없이는 ADR 위반 — `pre_deploy_check` 가 `MICRO_ORDER_MODE` 와 `BUY_DISABLED` 조합을 검사

## 7. 승인 기록

| 단계 | 승인일 | 비고 |
| --- | --- | --- |
| ADR 채택 + 단계 0 | 2026-11-12 | Master "승인, 12,000원으로". 구현 커밋 + cto review 후 배포 |
| 단계 1 진입 | — | 단계 0 결과 첨부 |
| 단계 2 진입 | — | 단계 1 결과(슬리피지 실측) 첨부 |
