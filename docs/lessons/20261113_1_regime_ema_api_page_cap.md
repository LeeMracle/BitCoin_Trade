# lessons #59 — 거래소 API 페이지 상한이 요청 limit 을 조용히 자른다: 레짐 EMA200 이 200봉 seed 편향으로 6개월 판정

- **날짜**: 2026-11-13
- **분류**: 전략 실행 정합 · 외부 API · silent fail
- **관련 lessons**: #21 (fail-closed) · #40 (진행 중 봉) · #52 (수집기 빈 페이지 오인) · #55 (레짐 2중화) · #58 (당일값 lookahead)
- **관련 ADR**: 20260516-2 (레짐 필터) · 20261112-1 (단계적 재활성 — 섀도 단계 0)
- **발견 경로**: plan 20261112_1 단계 0 **첫 섀도 행** (`btc_n = 200`) → `scripts/shadow_level_check.py` "EMA 창 길이 200 ≠ 250 · EMA200 불일치 1.59%"

---

## 1. 현상

`scanner.fetch_btc_above_ema()` 는 `exchange.fetch_ohlcv("BTC/KRW", "1d", limit=250)` 으로 받은 종가에 `ewm(span=200, adjust=False)` 를 적용해 BTC > EMA200 을 판정한다. **업비트 캔들 API 는 요청당 최대 200개**라 ccxt 는 오류 없이 200개만 돌려주고, `len(candles) < 200` 가드는 정확히 200 에서 통과한다.

`adjust=False` EMA 는 첫 종가를 seed 로 쓴다. 200봉이면 seed 가중치 (1 − 2/201)^200 ≈ **13.7%** — 200일 전 종가 하나가 EMA 의 1/7 이다. 2026-11-12 실측: 봇 EMA **106,288,664** vs 전체 이력 기준 **104,621,931** (+1.59%). 방향은 그날 seed(약 200일 전 종가)가 지금보다 높았기 때문이며, 시기에 따라 부호가 바뀐다.

ADR 20260516-2(2026-05-16) 이후 약 6개월간 매매 레짐이 이 편향된 선으로 판정됐다. 이번 사례는 둘 다 BEAR(96.96M < 104.6M)라 판정이 갈리지 않았지만, EMA 교차 ±1.6% 구간에서는 BULL/BEAR 가 뒤바뀐다.

## 2. 왜 못 봤나

- API 가 **오류를 내지 않았다** — limit 초과는 절단일 뿐 예외가 아니다 (lessons #52 와 같은 "빈/짧은 응답을 정상으로 오인" 계열).
- 길이 가드가 `>= EMA_PERIOD`(200) 였다. "계산이 가능한가"만 물었고 "**요청한 창이 다 왔는가**"는 묻지 않았다.
- 관찰 레짐(`regime_switcher`)·백테스트(2017~ 전체 이력)·cto M2 분석(250 가정)까지 모두 "250" 을 전제로 했다 — 아무도 응답 길이를 찍어 보지 않았다.
- 섀도 기록기가 **판정에 쓰인 숫자(btc_n, btc_ema)** 를 그대로 남기도록 설계한 덕에 첫 행에서 드러났다. bool 만 기록했으면 못 봤다.

## 3. 수정

- `scanner._fetch_btc_daily(exchange, n)` — 200개 단위 `params["to"]` 역방향 페이징 (`scripts/shadow_level_check.fetch_daily` 와 동형, 실측 600봉 단조·중복 0).
- `_BTC_EMA_FETCH_LIMIT = REGIME_FILTER_EMA_PERIOD * 3` (600, seed 가중치 ≈ 0.25%).
- 길이 가드를 `len(candles) < _BTC_EMA_FETCH_LIMIT` 로 — 창이 덜 차면 **fail-closed** (lessons #21).
- `fetch_btc_ema_snapshot()`(섀도 기록) 동일 적용. 매매 판정 함수의 구조·이름·fail-closed 분기는 유지(기존 AST 룰 보존).
- 섀도 대조기 `EMA_WINDOW = 600`, `btc_n == 600` 검사.

## 4. 검증 규칙

- `pre_deploy_check.check_btc_ema_history_depth()` (AST): (1) `_fetch_btc_daily` 에 `while` + `"to"` 페이징, (2) `fetch_btc_above_ema`/`fetch_btc_ema_snapshot` 이 `fetch_ohlcv(` 직접 호출 금지·`_fetch_btc_daily(` 호출 필수, (3) `_BTC_EMA_FETCH_LIMIT ≥ 250`, (4) `len(candles) < _BTC_EMA_FETCH_LIMIT` 가드. 역방향 `tests/scripts/test_stage_gate_rules.py` 4건.
- 섀도 단계 0 는 매일 `btc_n`·`btc_ema` 를 기준값과 대조한다 — 재발 시 다음 날 FAIL.

## 5. 교훈

- **외부 API 의 `limit` 은 요청이지 보장이 아니다.** 응답 길이를 요청과 비교해 모자라면 실패로 다뤄라. "계산 가능 최소치"가 아니라 "**요청한 만큼 왔는가**"를 가드하라.
- **판정 함수는 bool 과 함께 판정에 쓴 숫자를 남겨라.** 섀도 기록에 `btc_n` 하나를 넣은 것이 6개월 된 결함을 첫날 드러냈다.
- 백테스트·리뷰·문서가 같은 전제(250)를 공유하면 셋이 일치해도 틀릴 수 있다 — **실행 경로의 실제 응답**만이 진실이다 (lessons #49 의 외부 API 판).
