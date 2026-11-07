# lessons #57 — 전략이 구조적 음의 기대값으로 확정되면 즉시 매매 끊기 (BUY_DISABLED kill switch)

- **날짜**: 2026-11-07
- **분류**: 전략·안전장치·운영
- **관련 lessons**: #6 전수 적용 · #26 매수 경로 전수 열거 · #22 retry 적용 범위 · #45/#49 AST 검증 · #51 부재 검증
- **관련 ADR**: [20261107-1](../decisions/20261107_1_buy_disabled_until_redesign.md)

---

## 1. 현상

- 2026-11-04 ROADMAP 갱신: 손절 -6% 창 52건 승률 **30.8% [CI 19.9~44.3%]**, 건당 -1.85%
- 2026-11-03 시점 일치 백테스트 ([research/20261103_1](../../workspace/research/20261103_1_live_vs_backtest_universe.md) → [20261104_1](../../workspace/research/20261104_1_pit_universe_result.md)): IS 승률 33.2% / OOS 39.5%, 건당 -1.12% / -0.23%
- 실거래와 백테스트가 **같은 음의 기대값으로 수렴** — 운이 아닌 전략 결함
- 사전 등록 개선 후보 A·B·C **0/3 미달**
- 현재 BEAR 지속으로 자동 매수 0건 — **자연 차단 중**이지만 BULL 복귀 시점은 불확실

---

## 2. 근본 원인

### 2.1 종목군 불일치 (lessons #52 와 짝)

- 백테스트 종목군은 '지금 거래대금 상위 알트 40종' 기반 — 2026년 기준 오래된 대형 알트 중심
- 실거래 신규 45건 중 **41건이 백테스트 종목군 밖의 최근 상장 소형 코인**
- `fetcher._fetch_range` 가 첫 페이지 빈 응답을 '끝'으로 오인해 신규 상장 코인을 0봉으로 탈락시키던 버그가 수정되었으나 ([lessons #52](20261104_1_fetcher_late_listing_dropped.md)), **역사 백테스트가 이들 종목에 대한 평가를 가진 적 없음** 자체가 문제
- 결과: 지금 봇이 실제 사들이는 종목에 대해서는 **검증된 기대값이 없다**

### 2.2 구조적 음의 기대값 확정

- 손익분기 승률 43.6% (하드캡 -6% 기준, `6/(7.75+6)`)
- 실거래 CI 상한 44.3% — 손익분기에 겨우 0.7%p 접근
- 시점 일치 OOS 39.5% — CI 를 보지 않아도 손익분기 이하
- 3가지 개선 후보가 전원 미달 — **이번 IS 를 재사용하면 과적합**

### 2.3 레짐 필터만으로는 부족한 이유

- 레짐 필터는 BTC < EMA200 이면 매수 차단 → 지금 BEAR 라 자연 차단 중
- 그러나 BULL 복귀 시점이 불확실하고, 복귀 즉시 **구조적 결함 상태의 전략이 매수 재개**
- "레짐만 믿고 관망" 상태의 봇은 "BULL 복귀 알람 = 손실 알람" 과 같다

---

## 3. 핵심 교훈

**전략이 구조적 음의 기대값으로 확정되면 즉시 실매매를 끊는다. 재설계 완료 전에 BULL 복귀로 재개되지 않도록 명시적 kill switch 가 필수다.**

하위 교훈:

1. **자연 차단(레짐 BEAR)과 명시적 차단(kill switch)은 다르다** — 자연 차단은 외부 조건이 바뀌면 자동 해제된다
2. **기각된 개선 후보로 재시도하면 안 된다** — "같은 IS 로 다른 파라미터를 또 고르는" 과적합 함정
3. **kill switch 는 매수 경로 전수에 적용되어야 한다** (lessons #6/#26) — 한 경로라도 누락되면 자연 차단과 다를 바 없음
4. **재활성 조건은 ADR 에 사전 등록** — 향후 세션이 "BEAR 길어서 불안하니 풀자" 식으로 되돌리는 것을 차단

---

## 4. 수정

### 코드

| 파일 | 변경 |
| --- | --- |
| `services/execution/config.py` | `BUY_DISABLED = True` 상수 신설 (ADR 20261107-1 설명 포함) |
| `services/execution/realtime_monitor.py::_execute_buy` | 함수 최상단 `if BUY_DISABLED: return` (심볼별 60초 throttle 로 lessons #14 폭주 방지, `record_block("buy_disabled", symbol)` 집계) |
| `services/execution/multi_trader.py` scanner 매수 루프 | 매수 시작점에 `if BUY_DISABLED: continue` (경로 전수 적용 lessons #6/#26) |

### 운영 조치

- 배포: `scripts/hotfix_deploy.sh` 로 3개 파일(config.py, realtime_monitor.py, multi_trader.py) + pre_deploy_check.py 전송
- 사후 로그 검증: `grep "BUY_DISABLED kill switch" /var/log/btc-trader.log` 로 가드 발화 확인
- 포지션 영향 확인: `jq '.positions | length' workspace/multi_trading_state.json` — **1 유지** (XAUT 미영향)

---

## 5. 검증규칙 (pre_deploy_check 추가)

**`check_buy_disabled_all_entry_paths()` (이미 `scripts/pre_deploy_check.py:4331` 등재)**

AST 기반 3단 검증 (메타 교훈 #45/#49/#51 준수):

1. **상수 존재**: `services/execution/config.py` 에 `BUY_DISABLED = ...` `ast.Assign` 노드 존재
2. **실시간 경로 가드**: `services/execution/realtime_monitor.py::_execute_buy` 하위 노드에 `if BUY_DISABLED:` 분기(`ast.Name` 노드) 존재
3. **scanner 경로 가드**: `services/execution/multi_trader.py` 매수 루프 영역에 `if BUY_DISABLED:` 분기 존재

### 역방향 테스트 (실패를 잡는가)

- config.py 에서 `BUY_DISABLED` 라인 삭제 → 규칙 1 ERROR
- `_execute_buy` 에서 `if BUY_DISABLED` 블록 제거 → 규칙 2 ERROR
- multi_trader 매수 루프에서 가드 제거 → 규칙 3 ERROR

정규식이 아닌 AST 를 쓰는 이유:
- 주석의 `BUY_DISABLED` 언급, 정의부, 유사 이름 등에 속지 않음 (lessons #48)
- 가드가 **어느 분기에 놓였는가**를 묻는 질문이라 AST 필수 (lessons #45)
- "옳은 분기가 있는가"뿐 아니라 "틀린 경로가 없는가" — 매수 경로 전수를 함수 단위로 체크 (lessons #51)

---

## 6. 관련 lessons

- [#6 `20260404_1`](20260404_1_v2_filter_missing_path.md) — 전략 필터는 모든 매수 경로에 적용
- [#26 `20260504_3`](20260504_3_ml_filter_realtime_path_missing.md) — 신규 안전장치는 `grep buy_market` 등으로 진입점 전수 열거
- [#45 `20260824_1`](20260824_1_ghost_position_tp_complete.md) — 배치 검증은 AST (정규식 금지)
- [#49 `20260829_2`](20260829_2_indent_bypassed_safety_gate.md) — 들여쓰기 한 칸이 가드 우회
- [#51 `20260917_1`](20260917_1_consec_immediate_path_literal.md) — 부재 검증은 전수
- [#52 `20261104_1`](20261104_1_fetcher_late_listing_dropped.md) — 종목군 불일치의 근본 원인
