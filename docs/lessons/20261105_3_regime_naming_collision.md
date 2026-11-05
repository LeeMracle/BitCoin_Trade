# lessons #55 — 레짐 용어 2중화 (매매 vs 관찰) 혼동

- **발생일**: 2026-11-05 (CP1 베이스라인 직후, 세션 간 핸드오프 중)
- **심각도**: P2 (금전 피해 0, 운영 판단 혼동 — Master 질의 발생)
- **카테고리**: 운영 커뮤니케이션 · 식별자 중복 · 문서-코드 매핑
- **상태**: 수정 완료 (문서 2개 교정 + pre_deploy_check 룰 1개 신설 + 알람 라벨 분리)
- **관련 lessons**: #19 (상수 자체 정의 회피) · #4 (CLAUDE.md ↔ config 동기화) · #6 (모든 경로 전수) · #37 (regime cron 인자)

## 증상

- Master가 CP1 전 베이스라인(`output/regression_watch_20261105_baseline.md`) 확인 중 "슬롯 비어있는데 왜 매수 0건?" 문의
- 사실 조사 결과 포지션 2/15 사용 중 (XAUT, POL) + BEAR 상태에서도 신규 매수가 과거 발생한 흔적 확인
- "레짐 필터 우회 가능성" 의심으로 RCA 진행
- 조사 결과 코드는 **무결**. `regime_state.json`(관찰용) BEAR 상태가 표면에 노출되고 매매 코드는 `scanner.fetch_btc_above_ema()`(BTC>EMA200 단일, BULL)로 동작해 **매수가 열려 있던 상황**이 "레짐 필터 우회"로 오독됨
- 2시스템이 같은 "BEAR/BULL" 용어를 공유 → operator·Master 모두 혼동

## 근본 원인

**같은 명사(BEAR/BULL)를 두 독립 시스템이 공유**. 매트릭스:

| 구분 | 매매 레짐 (actual gate) | 관찰 레짐 (observatory) |
|---|---|---|
| 판정 함수 | `services/execution/scanner.py::fetch_btc_above_ema()` | `services/execution/regime_switcher.py::decide_regime()` |
| 입력 | **BTC close vs EMA200** (단일) | SMA50 + EMA200 + F&G (복합) |
| 호출 시점 | `_refresh_levels()` + `_execute_buy()` fail-closed | `scripts/regime_check.py` cron 매일 00:30 UTC |
| 매매 영향 | **직접** — False 시 전 종목 신규 매수 차단 (ADR 20260516-2) | **없음** — `REGIME_SWITCH_ENABLED=False` DRY-RUN |
| 상태 저장 | `realtime_monitor._btc_above_ema` (메모리) | `workspace/regime_state.json` (디스크) |
| 알림 | (현재 없음, 로그만) | `[BATA] 레짐 전환` 텔레그램 (lessons #37로 켜짐) |
| ADR | 20260516-2 | (P5-04 DRY-RUN 훅, 미ADR) |

2026-11-05 실측 스냅샷:
- 매매 레짐 = **BULL** (BTC > EMA200)
- 관찰 레짐 = **BEAR** (regime_switcher 복합 판정)
- → 매수 활성(매매 레짐 열림) + 관찰 레짐 BEAR 표시 → 모순처럼 보임

관찰 레짐이 BEAR인 날 매수가 발생하는 것은 **정상**. 매매 레짐이 BULL이고 다른 필터(ATR·VOL·연패)를 통과하면 매수가 열린다. 두 시스템이 섞인 멘탈 모델에서는 이게 "필터 우회"로 보인다.

## 핵심 교훈

**매매 기준이 되는 레짐과 관찰용 레짐은 식별자 자체를 분리하라.**

- 같은 용어를 두 시스템이 공유하면 코드가 무결해도 운영 판단이 흔들린다 (#19 계열 — 상수 자체 정의가 silent drift를 낳듯, 용어 자체 정의가 silent 혼동을 낳는다)
- 관찰용 시스템은 **용어 접두사(`[관찰레짐]` · `[WATCH_REGIME]`)**로 매매 경로와 분리
- 문서(CLAUDE.md·베이스라인·레거시 리포트) 어디서든 "BEAR/BULL" 단독 사용 금지 — 어느 시스템 기준인지 표기

## 수정

### 1. 문서 교정

- **CLAUDE.md `현재 전략 파라미터` 표 "레짐 필터" 행 직후 각주 추가** — 2시스템 매트릭스·독립성·매매 판단 시 매매 레짐만 참조 명시 (lessons #55 링크)
- **`output/regression_watch_20261105_baseline.md`** 섹션 4 REGIME:
  - `BEAR 지속 → 신규 매수 0건` 문구 삭제 → **매매 레짐(BULL) + 관찰 레짐(BEAR, DRY-RUN)** 분리 서술
  - L76 "BEAR 지속 중 정상" → "매매 레짐 BULL이나 13분 창 샘플 특성상 미발화 (관찰 레짐 BEAR 무관)"

### 2. pre_deploy_check 룰 신설 (`check_regime_filter_all_entry_paths()`)

매수 경로에 레짐 필터가 사문화되지 않도록 AST로 전수 검증 — 룰 세부는 "검증 규칙" 섹션 참조.

### 3. 알람 라벨 분리

- `scripts/regime_check.py::run()` 알림 발송부에 `[관찰레짐]` prefix (format_notification의 매매 혼동 prefix 추가 — 접두사만 추가해 메시지 본문 호환)
- `scripts/daily_check.py::_section_regime()` 헤더를 `🌤 레짐 상태` → `🌤 관찰 레짐 상태 (regime_switcher DRY-RUN)`로 변경
- `regime_switcher.format_notification`에 `[관찰레짐]` prefix 추가 (텔레그램 메시지 본문)
- **매매 레짐은 현재 알림을 쏘지 않음** — 로그에만 기록 (`[composite v2] F&G=... | BTC>EMA200=...`). 미래 매매 레짐 알림을 추가할 경우 `[매매레짐]` prefix 의무

### 4. 코드 수정 범위

- **매매 로직 코드 변경 없음** (ADR 20260516-2 사후변경 회피, RCA 결과 무결)
- 알람 포맷만 변경 (로직 변경 금지)
- 현재 보유 포지션(XAUT, POL) 강제 청산 없음

## 검증 규칙

### `check_regime_filter_all_entry_paths()` — AST 전수 검증

메타 교훈 #45/#49 준수: 정규식 금지, AST로 "어디에 놓였는가"를 묻는다.

1. **`services/execution/realtime_monitor.py::_execute_buy` 함수** 내부에 `self._btc_above_ema` 참조(Attribute Load) 또는 `fetch_btc_above_ema` 호출(Call) 존재 — 없으면 ERROR
2. **`services/execution/scanner.py`** 모듈에 `fetch_btc_above_ema()` 함수 정의 존재 + `composite_signals()` 또는 유사 신호 생성 경로에서 호출 존재 — 없으면 ERROR
3. 매수 경로 파일(`realtime_monitor.py`, `multi_trader.py`)에 신규 매수 함수가 추가될 때(`buy_market`, `place_order` 등 호출) — (별도 룰 `check_all_buy_paths_ml_gate` 과 중복 회피를 위해 본 룰은 레짐 체크 경유만 담당)

### 역방향 테스트

- 시나리오 A: `_execute_buy`에서 `self._btc_above_ema` Attribute Load 제거 → ERROR 발화 PASS
- 시나리오 B: `scanner.fetch_btc_above_ema` 정의 자체 제거 → ERROR 발화 PASS
- 시나리오 C: 원상복구 → 정상 PASS

메타 교훈 #44 준수 — "룰이 통과하는가"가 아니라 "룰이 **실패를 잡는가**"로 검증.

### 보완 테스트

`tests/execution/test_regime_fail_closed.py` 이미 존재 (런타임 검증). 본 룰은 **정적 AST 검증**으로 상호 보완:
- 런타임 테스트: 실행 중 레짐 False → 매수 차단 확인
- 정적 룰: 코드 변경 시 레짐 체크 경로가 제거되지 않는지 CI/pre-deploy에서 선차단

## 관련 lessons

- **#4**: CLAUDE.md ↔ config.py 동기화 — 본 lessons는 "용어 매트릭스" 동기화의 확장
- **#6**: 매수 경로 전수 — 본 룰은 매수 경로의 레짐 필터 전수 검증 추가
- **#19**: 상수 자체 정의 silent drift — 용어 자체 정의도 같은 유형
- **#37**: regime_check.py `--notify` 누락 (cron silent fail) — 본 lessons의 전임
- **#45 / #49**: AST vs 정규식 — 본 룰 AST 강제 근거
