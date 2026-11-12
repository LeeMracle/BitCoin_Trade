# lessons #58 — 일봉 백테스트의 "당일 값" lookahead: 레짐·ATR 을 신호 당일 봉으로 계산했다

- **날짜**: 2026-11-12
- **분류**: 백테스트 정합 · 연구 방법
- **관련 lessons**: #1 (봉 마감 레벨) · #2 (백테스트 편향) · #40 (진행 중 봉 읽기) · #49 (복제 시뮬레이션의 한계)
- **관련**: [research 20261112_1](../../workspace/research/20261112_1_universe_reduction_result.md) cto M1 · [ADR 20261112-1](../decisions/20261112_1_universe_btc_eth_staged_reenable.md)
- **발견자**: cto review (서브에이전트), plan 20261110_1 교차검증

---

## 1. 현상

`scripts/backtest_param_sweep.build_panel()` 이 날짜 `t` 의 진입 신호를 만들 때

```python
df["atr"] = _atr(df, C.ATR_PERIOD)                       # t 봉의 고저·종가 포함
df["signal"] = ((df["high"] > upper)
    & (df["atr"] / df["open"] <= p.max_atr)              # ← t 당일 ATR 로 MAX_ATR 필터
    & (df["volume"].shift(1) >= vsma.shift(1) * p.vol_mult)
    & (df["ts"].map(lambda t: bool(regime.get(t, False)))))  # ← t 당일 BTC 종가 vs EMA200
```

- **레짐**: `regime[t]` 는 t 의 BTC **종가**로 판정한다. 실거래(`scanner.fetch_btc_above_ema`)는 UTC 00:0x 에 한 번 판정하므로 사실상 **t-1 종가** 기준이다. t 당일 BTC 가 급등해 EMA 위로 올라선 날의 돌파를 백테스트만 잡는다.
- **ATR**: `atr[t]` 는 t 의 고저를 포함한다. 진입은 장중(t 봉 안)에 일어나는데 그 시점에 t 의 고저는 모른다. MAX_ATR 필터와 초기 손절 거리(`ATR×3`)가 **미래 변동폭**을 쓴다.
- DC 상단(`high.shift(1).rolling`)과 거래량 필터(`shift(1)`)는 올바르다 — 같은 함수 안에서 축마다 기준 시점이 달랐다.

## 2. 영향

| 연구 | 사용 | 결론 영향 |
| --- | --- | --- |
| [20261112_1 모집단 축소](../../workspace/research/20261112_1_universe_reduction_result.md) (S1) | 신호·초기 손절 | 수정 시 IS Sharpe 1.09→0.97, OOS Sharpe 1.31→1.22, n 62→60. **판정 불변(시나리오 A)**. 수정 수치를 결과 문서에 병기 |
| [20261104_1 PIT 모집단](../../workspace/research/20261104_1_pit_universe_result.md) (`backtest_universe_pit.py`) | 동일 | 음의 기대값(IS 33.2% / OOS 39.5%) 판정. lookahead 는 백테스트에 **유리한** 방향이므로 수정하면 더 나빠진다 — 결론 불변 |
| [20260917_1 손절 -5% 검증](../../workspace/research/20260917_1_live53_review_stop5.md) (`backtest_stop_validation.py`) | 동일 | 손절 폭 비교(상대 비교)라 두 팔에 같은 편향 — 순위 결론 불변 가능성이 높으나 **재실행 안 함**, 참고 시 주의 |
| 20260827_1 슬리피지 필터 | 동일 | 음성 결과(죽은 변수) — 불변 |

방향성: 레짐 lookahead 는 "상승 전환 당일"의 진입을 추가로 허용하고, ATR lookahead 는 변동이 커지는 날의 신호를 **더 많이 거른다**(당일 폭이 크면 MAX_ATR 초과). 둘이 상쇄되지 않으며 종목·구간에 따라 부호가 다르다. 이번 S1 에서는 합쳐서 Sharpe 약 -0.1 이었다.

## 3. 근본 원인

- 일봉 패널에서 "이 행의 값"과 "이 행의 **시작 시점**에 알 수 있는 값"을 구분하지 않았다. `shift(1)` 을 DC 상단에는 썼지만 ATR·레짐에는 빠뜨렸다 — 축마다 기준 시점을 따로 정했기 때문.
- 레짐 dict 가 `{날짜: bool}` 이라 "어느 시점의 종가인가"가 타입에 드러나지 않았다.
- 검증(v1·v2 cto review)은 **시간봉↔일봉 정렬**과 **청산 경로 손계산**에 집중했고, 진입 필터의 기준 시점은 묻지 않았다. 세 번째 리뷰(plan 20261110_1)에서야 "실거래는 언제 판정하나"를 코드로 대조해 발견.

## 4. 수정

- **백테스트 코드는 고치지 않았다** — 사전 등록 결과의 사후 변경 금지. 영향 수치는 cto 가 별도 스크립트로 산출해 결과 문서 §3-1 에 병기.
- 다음 백테스트부터 `build_panel` 을 쓰는 plan 은 **"레짐·ATR 전일 기준"** 을 사전 등록 항목에 명시하고, 새 패널 함수(`build_panel_pit`, 미구현)를 쓴다. 기존 함수는 과거 결과 재현용으로 보존.
- 섀도 검증(plan 20261112_1 단계 0)의 기준값은 **전일 완성봉**으로 정의했다(`scripts/shadow_level_check.py`). 봇이 UTC 00:0x 에 쓰는 값과 비교하므로 "실거래가 계산해야 할 값"이 기준이다.

## 5. 검증 규칙

- `scripts/shadow_level_check.py --selftest`: 상단에 당일 고가가 섞이면(lookahead) FAIL 을 내는지 역방향 케이스 포함.
- 신규 백테스트 plan 체크리스트(사전 등록 §"데이터·창")에 **"각 필터의 기준 시점(t-1/t) 표"** 를 필수 항목으로 추가 — `workspace/plans/README.md` 반영은 다음 plan 작성 시.
- pre_deploy_check 룰은 추가하지 않는다 — 운영 코드가 아니라 연구 코드의 결함이며, 사전 등록 문서가 집행 지점이다.

## 6. 교훈

- **일봉 행 하나에도 두 개의 시각이 있다** — 시가 시점에 아는 것(전일까지)과 종가 시점에 아는 것(당일 포함). 장중 진입을 시뮬레이션하는 패널은 모든 필터를 **시가 시점** 값으로 통일해야 한다.
- **같은 함수 안의 축들이 서로 다른 기준 시점을 가질 수 있다.** `shift(1)` 이 하나 보인다고 전체가 PIT 인 것이 아니다 — 축별로 표를 만들어 물어라.
- **정렬 검증과 시점 검증은 다르다.** 봉이 맞게 붙어 있어도(정렬 OK) 어느 봉을 읽는지(시점)는 별개 질문이다. cto 체크리스트에 "실거래는 이 값을 **언제** 계산하는가"를 고정 항목으로 둔다.
