# Bitcoin Auto-Trading Workflow

## 프로젝트 목표

비트코인 자동매매 워크플로우 — 시장 분석 → 전략 연구 → 백테스트 → 페이퍼 트레이딩 → 실전 거래

## 거래소: 업비트 (Upbit) — 현물 전용

- 기준 통화: **KRW** (심볼: `BTC/KRW`)
- **현물 전용** — 선물/파생상품 없음. 펀딩레이트·미결제약정 데이터 없음
- 포지션: **long/flat만** (숏 불가)
- API 인증: JWT Bearer 토큰 방식 (`UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY`)
- Rate Limit: 기본 29 req/sec, 주문 4 req/sec
- ccxt 사용: `ccxt.upbit({'apiKey': ..., 'secret': ...})`
- 공식 문서: [업비트 개발자 센터](https://docs.upbit.com/kr)

### 환경: 로컬 PC + 유동 IP

- **공개 시세 API는 인증 불필요** — Phase 2 백테스트까지 API 키 없이 진행 가능
- Phase 3 시작 전 아래 중 하나 선택 필요:
  - ISP 고정 IP 신청 (권장, 소액 추가 비용)
  - DDNS + IP 갱신 스크립트
  - 클라우드 서버 이전 (Phase 4 실전 거래 시 필수 수준)
- API 키에 **출금하기 권한 절대 부여 금지**
- 상세 가이드: [workspace/reference/upbit-api-guide.md](workspace/reference/upbit-api-guide.md)

## 현재 단계: Phase 2 완료, Phase 3 준비

- [x] Phase 1: 레포 골격, 스킬 정의, MCP 계약 초안
- [x] Phase 2: 시장 데이터 어댑터, 백테스트 러너, 전략 탐색 완료
  - F&G 역추세 → 실패 (구조적 한계)
  - 추세추종 전환 → **DC(50)+ATR(14)x3.0** OOS Sharpe 1.123, MDD -18.7% (엄격 기준 통과)
  - 보조: RSI(10)>50<45+EMA(150) OOS Sharpe 1.040, MDD -14.9%
- [ ] Phase 3: 페이퍼 트레이딩 진행 중
  - 메인: **DC(12)+ATR(14)x3.0 + ML 게이트(threshold 0.45)** — `services/paper_trading/` + `services/ml/` (DC50→20→15→10→15→12 단계적 공격→보수화→재적극화, [DC15 경위](docs/decisions/20260426_1_dc15_switch.md), [DC10 시도→DC15 복귀](docs/decisions/20260504_1_three_strategy_enhancements.md), [DC12 + ATR 10% + VOL 1.0 동시 튜닝](docs/decisions/20260505_1_strategy_param_tuning.md), [ML LIVE 가속 0.45 보수 시작](docs/decisions/20260505_2_ml_live_acceleration.md))
  - **레짐 필터(EMA200) 재활성화됨 (현재 ON)** (2026-05-16 [ADR 20260516-2](docs/decisions/20260516_2_entry_conditions_tightening.md)) — `REGIME_FILTER_ENABLED = True`, BTC/KRW 종가 < EMA200 시 전 종목 신규 매수 차단(기존 포지션 트레일링스탑은 영향 없음). 경위: 5/3 [plan 20260503_2](workspace/plans/20260503_2_enable_trading_in_bear.md)에서 "거래 빈도 우선, 백테스트 미달 수용"으로 **해제(OFF)** 했으나 5/3~5/16 실거래 악화(승률 17.4%·PF 0.29·누적 -111%·손실 중 63%가 -9.5%↓ 하드손절 직행)로 ADR 20260516-2에서 **재활성화(ON)** 하며 plan 20260503_2를 원복. 동반 강화: VOL 필터 1.0→1.5, ATR 필터 0.10→0.07. 안전장치는 하드 손절 캡(-10%) + 서킷브레이커(-20%/-25%) + ATR 필터 유지
  - **종목 풀**: MIN_VOLUME_KRW 3억 → 5억 환원 (2026-06-07 [ADR 20260607-1](docs/decisions/20260607_1_min_volume_revert_5e.md)) — 저유동성 알트 진입 차단(승률 17.4%·하드손절 직행 48% 주범), 약 150 → 117 종목으로 축소. 이전 확대(5억→3억)는 plan 20260503_5
  - 보조: RSI(10)>50/<45+EMA(150) — 관찰용
  - **검증 기준선 재설정 (2026-08-23, [ADR 20260823-1](docs/decisions/20260823_1_validation_baseline_reset.md))** — `strategy_start` 2026-03-29 → **2026-08-23**. 이전 통계 폐기 사유: (1) 실행 버그 4건(lessons #40~#43)이 매매·통계를 오염, (2) 그 기간 전략 파라미터 반복 변경으로 서로 다른 전략 성적을 합산, (3) 레짐 게이트 통과율이 2026 Q1/Q2 **0%**·최근 180일 **1.1%**로 거래 자체가 거의 불가했음. `closed_trades` 26건은 **보존**(창만 이동)
  - **목표치 재도출**: 승률 35%→**48%** / 평균 +0.5%→**+0.75%** — 현재 config 백테스트 실측(506건/54종목/700일, 수수료 반영: 승률 59.9%·평균 +1.50%·PF 1.51)의 하한. 기존 값은 TP(+5%/+12%) 도입 이전 잔재
  - **판정 기준 전환**: 달력 기반(`7일+15건`) → **표본 수 기반**(최종 30건 / 중간 15건). 레짐 차단 구간에선 시간만 흐르고 표본이 안 쌓여 달력 판정이 무의미. 성과 요약에 `거래가능 M일`(regime_open_days) 병기
  - **단일 종목 비중 상한 (2026-08-23, [ADR 20260823-2](docs/decisions/20260823_2_position_weight_cap.md))** — `MAX_POSITION_WEIGHT = 0.20`. 기존 `available*RATIO/slots_empty`는 빈 슬롯 1개일 때 가용 현금 95% 전액을 한 종목에 투입(실측 OP 47.3%). 상한 기준은 **총자산(total_krw)** — 현금 기준이면 보유분을 무시해 무의미. 백테스트: 수익 +1.4%p / MDD +4.2%p / 최대비중 42.8%→21.2%
  - ⚠ **미해결: 포트폴리오 단위 수익성** — 종목별 백테스트(506신호, 승률 59.9%·평균 +1.50%)와 달리, **5슬롯 제약 포트폴리오는 무작위 선택 20회 전부 손실**(평균 -16.4%, 2025년 -28.3%). 원인: 신호 506개 중 27%만 취할 수 있어 *어떤 27%를 잡느냐*가 결과를 지배(선택 순서만 바꿔도 14%p 차이). 낙관 가정(TP 우선)으로도 -14.5%. 검토 방향: 슬롯 수 / TP 수준 / 손절·ATR 상호작용 / 진입 후보 선택 규칙. **파라미터는 실거래 재검증(ADR 20260823-1) 결과 전까지 변경 보류** — 또 바꾸면 성적을 합산할 수 없게 되는 실수 반복
  - 일일 체크: `python scripts/daily_check.py` (09:05 KST 실행 권장)
  - 텔레그램 알림: `services/.env.example` 참고하여 `.env` 설정 필요
- [ ] Phase 4: 실전 거래 — 모듈 구현 완료, AWS 배포 필요
  - 실행 모듈: `services/execution/` (upbit_client, trader)
  - AWS 서버: `13.124.82.122` (Seoul, t3.micro, Ubuntu 24.04)
  - 배포: `bash scripts/deploy_to_aws.sh`
  - 일일 실행: `scripts/daily_live.py` (cron UTC 00:05 = KST 09:05)

## 에이전트 팀 구조 (v0.5.1, 도메인 축 4 + 보조 1)

사용자는 **bata-pm**에게만 말한다. 나머지 에이전트는 PM이 위임 호출한다.
정의 파일: `.claude/agents/*.md` (Claude Code sub-agents). 상세 책임: [agents/team.yaml](agents/team.yaml)

| 에이전트 | 도메인 | 단일 책임 | 정의 파일 | 사용 스킬 |
| --- | --- | --- | --- | --- |
| **bata-pm** | PROJECT | 사용자 접점·우선순위·승인·주간 audit | `.claude/agents/bata-pm.md` | project-orchestrator |
| **bata-investment-expert** | FINANCE | **P2 무수익** — 시장·전략·진단·도메인 파라미터 발의(독점) | `.claude/agents/bata-investment-expert.md` | market-analyst, strategy-researcher, backtest-engineer, strategy-pipeline |
| **bata-engineer** | SOFTWARE | **P1 오류 반복** — 기획·개발·유지보수·배포·회귀방지 | `.claude/agents/bata-engineer.md` | cto |
| **bata-operator** | OPERATIONS | 모니터링·알람 트리아지·일일/주간 보고 | `.claude/agents/bata-operator.md` | monitor, daily-work |
| btc-market-news-analyst | MARKET | (보조) 시황·뉴스 종합 브리핑 | `.claude/agents/btc-market-news-analyst.md` | — |

## 에이전트 호출 파이프라인 (PM 주도)

PM은 사용자 요청을 받아 **싱글 / 병렬 / 순차** 3가지 패턴으로 다른 에이전트를 호출한다.
실제 호출은 `Agent` 툴에 `subagent_type` 지정 — 병렬 호출은 **단일 메시지에 multiple Agent 블록** (반드시 동시).

### 호출 패턴 3종

| 패턴 | 언제 | 호출 방식 |
| --- | --- | --- |
| **싱글** | 도메인 단일·책임자 명확 | `Agent(subagent_type="bata-engineer", ...)` 1회 |
| **병렬** | 독립 작업 동시 진행 (audit, RCA+영향평가) | 단일 메시지에 `Agent` 블록 N개 동시 호출 |
| **순차** | A 결과가 B 입력 (게이트, 승인 체인) | A 완료 → 결과 검토 → B 호출 |

### 표준 파이프라인 5종

#### 1. 일일 사이클 (daily)
```
09:05 PM → [싱글] bata-operator (start 브리핑)
            ↓ (이상 항목 있으면)
            PM이 분류해서 engineer 또는 expert에게 [싱글] 위임
09:10~23:00 operator 9분 cycle (자동)
23:00 PM → [싱글] bata-operator (end 브리핑)
            → PM이 당일 요약 (직접)
```

#### 2. P1 사이클 — 오류 반복 차단 (incident)
```
알람 발생
  ↓
PM → [싱글] bata-operator (트리아지)
  ↓ (분류 결과: 코드 오류)
PM → [싱글] bata-engineer (RCA → 수정 → 배포 → lessons → 룰)
  ↓
PM → [싱글] bata-operator (24h 회귀 감시 위임)
  ↓ (24h 후)
PM: incident 4단계 close 확인 → close 또는 보강 지시
```

#### 3. P2 사이클 — 주간 수익 개선 (월요일 09:30)
```
PM → [싱글] bata-investment-expert (주간 5Q 진단 + ADR 발의)
  ↓ (ADR 산출)
PM → [싱글] bata-engineer (게이트: 영향 grep + pre_deploy_check)
  ↓ (게이트 PASS)
PM: 승인 결정 (직접)
  ↓ (승인)
PM → [싱글] bata-engineer (배포 — deploy_to_aws.sh 또는 hotfix_deploy.sh)
  ↓
PM → [싱글] bata-operator (1주 drift 추적, 임계 초과 시 롤백 콜)
```

#### 4. 주간 audit (금요일) — **병렬 fan-out**
```
PM → [병렬] {
  bata-engineer:           "이번 주 incident 4단계 close 변환율 + 신규 lessons/룰 카운트"
  bata-investment-expert:  "이번 주 PnL/drift/필터 효과 요약"
  bata-operator:           "이번 주 false alarm rate / 오분류율 / 좀비 lag"
}
  ↓ (3개 결과 동시 회수)
PM: 통합 audit 보고서 작성 (직접) → 사용자 보고
```

#### 5. 신규 사고 패턴 (lessons에 없음) — **병렬**
```
PM → [병렬] {
  bata-engineer:           "RCA + 수정안 + lessons/룰 후보"
  bata-investment-expert:  "이 사고가 전략 수익에 미친 영향 + 도메인 권고"
}
  ↓ (RCA + 영향 평가 동시)
PM: 종합 → 우선순위 결정 → engineer 단독 또는 expert 합의로 다음 단계
```

### 호출 코드 예시

**싱글 호출 (engineer 위임)**
```
Agent(
  subagent_type="bata-engineer",
  description="realtime_monitor KeyError RCA",
  prompt="2026-05-24 22:15 KST 알람: realtime_monitor.py에서 KeyError 'pnl_realized'. RCA + 4단계 close 진행. 영향 범위 grep 결과 포함."
)
```

**병렬 호출 (주간 audit fan-out — 단일 메시지에 3개 Agent 블록)**
```
[같은 메시지에서 동시 호출]
Agent(subagent_type="bata-engineer", description="W## incident audit", prompt="...")
Agent(subagent_type="bata-investment-expert", description="W## PnL audit", prompt="...")
Agent(subagent_type="bata-operator", description="W## ops audit", prompt="...")
```

**순차 호출 (P2 사이클)**
```
1) Agent(subagent_type="bata-investment-expert", ...) → ADR 회수
2) PM 검토 후
3) Agent(subagent_type="bata-engineer", ..., prompt="ADR <링크> 게이트 검토") → 게이트 결과 회수
4) PM 승인
5) Agent(subagent_type="bata-engineer", ..., prompt="배포 실행") → 배포 결과 회수
6) Agent(subagent_type="bata-operator", ..., prompt="1주 drift 추적")
```

### PM 호출 규칙

- **싱글이 기본** — 단일 도메인 작업은 무조건 싱글 (병렬 남용 금지)
- **병렬은 audit·신규 사고만** — 독립 fan-out에만 사용. 의존 작업을 병렬로 돌리면 결과 불일치
- **순차에서 PM 직접 처리 단계 명시** — 게이트 결과 검토, 승인은 PM 본인 (위임 X)
- **하드룰 R4 (자기평가 금지)** — engineer가 만든 코드를 engineer가 PASS 판정하면 안 됨. 게이트는 별도 호출 또는 `cto` review
- **incident close는 PM이 직접 확인** — 4단계(lessons 작성, 룰 등록) 완료 검증 후 PM이 close 판정

## 핵심 파일 위치

- 에이전트 팀 정의: [agents/team.yaml](agents/team.yaml)
- MCP 계약 (업비트): [infra/mcp.upbit.yaml](infra/mcp.upbit.yaml)
- 운영 설계 문서: [docs/agent-team-draft.md](docs/agent-team-draft.md)
- 작업 산출물: [workspace/](workspace/) (research/, reports/, specs/, runs/)
- 레퍼런스 문서: [workspace/reference/](workspace/reference/)

## MCP 서버

업비트 맞춤 계약: [infra/mcp.upbit.yaml](infra/mcp.upbit.yaml)

| 서버 | 상태 | 툴 |
| --- | --- | --- |
| **market_data** | 구현 중 | `get_ohlcv`, `get_ticker`, `get_orderbook`, `get_macro_series` |
| **experiment_tracker** | 구현 중 | `create_experiment`, `log_run`, `compare_runs` |
| exchange_execution | Phase 3 | 업비트 REST 주문 (페이퍼 → 실전) |
| alerting | Phase 3 | Slack/Telegram |
| secrets_config | Phase 3 | 정식 시크릿 관리 |

> 업비트는 현물 전용 — `get_funding`, `get_open_interest` 없음

## 아키텍처 뷰어 앱

[src/App.jsx](src/App.jsx) — React/Vite 기반 프로젝트 구조 시각화 도구 (`npm run dev`)

## 작업 규칙

- 전략 규칙은 반드시 Strategy Researcher 산출물(strategy_spec) 기반
- 라이브 거래는 Execution Risk Guard 승인 + PM Orchestrator 최종 확인 필요
- 인샘플 성과만으로 프로덕션 이동 금지
- 각 단계는 검토 가능한 아티팩트 필수 (보고서, 로그, 메트릭)
- **Execution Plan 강제**: 비자명 작업(30분↑ / 코드·외부시스템·전략·CLAUDE.md 변경 중 1개↑)은 착수 전 `workspace/plans/YYYYMMDD_작업명.md`를 `workspace/plans/_TEMPLATE.md` 기반으로 생성한다. 목표·성공기준이 빈칸인 상태로 착수 금지. 상세 규칙은 [workspace/plans/README.md](workspace/plans/README.md)
- **자기평가 금지 / 교차검증 필수**: 구현을 수행한 동일 세션은 자기 산출물을 PASS 판정하지 않는다. 대상 작업(코드·운영·전략·CLAUDE.md 변경)은 별도 세션 / 서브에이전트(`cto` review) / 자동 검증 스크립트(`pre_deploy_check.py` 등) 중 최소 1개로 검토하고, 결과는 "확인 항목 N개 / 발견 이슈 M개" 형식으로 기록한다. 상세: [docs/cross_review_policy.md](docs/cross_review_policy.md)

## 시행착오 관리

- **시행착오 기록**: `docs/lessons/YYYYMMDD_N_제목.md` — 오류 발생 시 원인·수정·검증규칙·교훈을 기록
- **자동 검증**: `scripts/pre_deploy_check.py` — 배포 전 자동 실행, 기록된 검증규칙을 코드로 검증
- **참조 의무**: 전략 변경, 배포 스크립트 수정, 서버 설정 변경 시 `docs/lessons/`의 관련 기록을 먼저 확인
- **신규 오류 발생 시**: (1) 수정 → (2) lessons 기록 → (3) pre_deploy_check.py에 검증규칙 추가 → (4) 필요 시 CLAUDE.md 업데이트

### 주요 교훈 요약

| # | 교훈 | 참조 |
|---|------|------|
| 1 | 봉 마감 기반 전략을 실시간 틱으로 실행 금지 (가짜 돌파) | [lessons/20260329_1](docs/lessons/20260329_1_tick_vs_bar_entry.md) |
| 2 | 백테스트 상승장 비중 높으면 하락장 성과 과대평가 — 하락장 구간 별도 검증 | [lessons/20260329_2](docs/lessons/20260329_2_backtest_period_bias.md) |
| 3 | 안전장치(연패 중단)는 주기 체크가 아닌 체결 즉시 체크 | [lessons/20260329_3](docs/lessons/20260329_3_auto_stop_delay.md) |
| 4 | CLAUDE.md ↔ config.py ↔ 서버 전략 파라미터 동기화 필수 | [lessons/20260331_1](docs/lessons/20260331_1_dc_strategy_mismatch.md) |
| 5 | t3.micro 스왑 필수, 서비스 추가 전 메모리 예산 확인 | [lessons/20260331_2](docs/lessons/20260331_2_server_memory_pressure.md) |
| 6 | 전략 필터는 모든 매수 경로(scanner+realtime_monitor)에 적용 필수 | [lessons/20260404_1](docs/lessons/20260404_1_v2_filter_missing_path.md) |
| 7 | 1일 1회 작업은 반드시 날짜 체크 + 상태 저장 (재시작 시 중복 방지) | [lessons/20260404_2](docs/lessons/20260404_2_vb_rotation_duplicate.md) |
| 8 | 모니터링 평가금액은 거래소 API 전체 자산 합산 필수 (BTC만 집계하면 알트 누락) | [lessons/20260405_1](docs/lessons/20260405_1_balance_missing_alts.md) |
| 9 | 자동화 전제 스크립트는 cron/systemd 등록 + pre_deploy_check로 검증 필수 | [lessons/20260408_1](docs/lessons/20260408_1_jarvis_cron_missing.md) |
| 10 | 상태 파일은 "거래소 미러"여야 함 — state ↔ balance 불일치 즉시 경보 | [lessons/20260408_2](docs/lessons/20260408_2_state_balance_mismatch.md) |
| 11 | 서킷브레이커는 신규 차단뿐 아니라 기존 포지션 처리 정책도 명시 필요 | [lessons/20260408_3](docs/lessons/20260408_3_cb_existing_positions_policy.md) |
| 12 | dict.get(key, default)는 값이 None이면 default가 무시됨 — 린트 집행 | [lessons/20260408_4](docs/lessons/20260408_4_nonetype_format_lint.md) |
| 13 | ATR*N 스탑은 고변동 종목에서 제어 불능 — 하드 손절 캡 필수 | [lessons/20260408_5](docs/lessons/20260408_5_ong_wide_stop.md) |
| 14 | 이벤트 루프 내 로그는 throttle 필수 — 종목수×빈도 곱 폭발 | [lessons/20260410_1](docs/lessons/20260410_1_cb_log_spam.md) |
| 15 | 외부 API 의존 초기화는 재시도+백오프 필수 — systemd 재시작은 대체 불가 | [lessons/20260413_1](docs/lessons/20260413_1_startup_refresh_crash.md) |
| 16 | 배포 스크립트가 전제하는 로컬 CLI(rsync 등)도 pre_deploy_check로 검증 + 폴백 분기 필수 | [lessons/20260419_1](docs/lessons/20260419_1_rsync_missing_deploy_stall.md) |
| 17 | 다중 프로젝트 공존 서버에서 프로세스 판정 시 `/proc/<pid>/cwd` + 전체 systemd unit 역탐색 필수 — 좀비 오판 방지 | [lessons/20260421_1](docs/lessons/20260421_1_multi_project_process_misdiagnosis.md) |
| 18 | venv 디렉터리 리네임 시 crontab/systemd unit의 인터프리터 경로 동시 갱신 필수 — stderr→로그파일 리디렉션은 silent fail 유발 | [lessons/20260425_1](docs/lessons/20260425_1_crontab_venv_path_drift.md) |
| 19 | 모듈이 config 상수를 import하지 않고 자체 정의하면 동기화 누락 위험 — 운영 변경 권장 시 코드베이스 전체 grep 필수 + import 통일 | [lessons/20260425_2](docs/lessons/20260425_2_config_constant_self_definition.md) |
| 20 | 다중 API 키 운영 시 키↔환경(서버 IP) 매핑 미명시는 silent fail 직결 — critical 경로는 단명(매시 5분) 헬스체크 + 즉시 알람 + 디바운스 세트 필수, daily report만으로는 실시간 감시 불가 | [lessons/20260502_1](docs/lessons/20260502_1_upbit_keyset_ip_mapping.md) |
| 21 | ccxt `enableRateLimit`은 인스턴스 수명에서만 throttle 추적 — `_create_exchange()` 매번 호출 시 무효. 모듈 싱글톤 + 명시 백오프 wrapper 둘 다 필수. 안전장치는 fail-closed 원칙(잔고 조회 실패 → 매수 차단), 헬스체크 판정 기준은 "정상 동작에도 항상 갱신되는 값"이어야 false alarm 회피 | [lessons/20260503_1](docs/lessons/20260503_1_rate_limit_cb_fallback_healthcheck_loop.md) |
| 22 | wrapper(retry/backoff) 일괄 적용 금지 — 조회 경로만 적용, 매수/매도 즉시성 경로는 lessons #3 위배. 알림 등급(level) 도입은 default 호환 유지로 점진 마이그레이션. 신규 통합 cron 추가 시 기존 cron과 동일 거래 회피 로직 사전 설계 필수 (없으면 알림 2~3배 폭주) | [lessons/20260503_2](docs/lessons/20260503_2_p3_wrapper_alert_levels_function_unification.md) |
| 23 | 침묵 모드 cron은 항상 heartbeat 파일과 짝 — 텔레그램 발송 안 해도 cron 죽음 감지 불가 시 더 큰 사고. retry/backoff는 idempotent 호출(조회)만 안전, 주문(매수/매도)에 적용 시 중복 주문 위험. 신규 cron은 pre_deploy_check 등록 검증 룰과 함께 추가 | [lessons/20260503_3](docs/lessons/20260503_3_p4_alert_migration_digest_cron_buy_wrapper_hold.md) |
| 24 | 장시간 가동 스크립트(daily_live.py --realtime)는 systemd 단독 가동, cron 직접 호출 금지 — 매시 새 인스턴스 추가로 좀비 누적·race condition 다중 발화. 다중 프로젝트 환경 crontab 갱신은 grep -v 위험 (다른 프로젝트 라인 우연 매칭 삭제). 백업 디렉터리는 .disabled 등 명시적 격리. ps grep만으로 프로젝트 판단 금지 — `/proc/<PID>/cwd` 확인 필수 | [lessons/20260504_1](docs/lessons/20260504_1_zombie_processes_crontab_overwritten_bak_dirs.md) |
| 25 | 부분 익절 잔량 회계는 "불변 입력(entry_qty/entry_amount_krw) + 가변 추적(tp_sold_levels)" 분리. SL/TP 동시 트리거 시 SL 우선 정책 명문화. 매도 retry 금지(lessons #3) — 실패 시 next-tick 재평가. 정기 reset은 cron보다 기존 함수 진입 시점 호출이 안전 (lessons #18/#24 회피) | [lessons/20260504_2](docs/lessons/20260504_2_strategy_enhancements_partial_tp_volume_daily_loss.md) |
| 26 | "모든 매수 경로"에 적용되는 안전장치(필터·게이트)는 신규 추가 시 `grep buy_market` 등으로 모든 진입점 열거 + task별 분리 필수. fail-open 정책이라도 "일부 경로 누락"은 lessons #6 위배 면책 안 됨. pre_deploy_check에 매수 경로 hook 존재 강제 룰 등록 (자동 사각지대 차단) | [lessons/20260504_3](docs/lessons/20260504_3_ml_filter_realtime_path_missing.md) |
| 27 | systemd 재시작은 cron으로 fork된 별도 PID(좀비)를 죽이지 않음 — 옛 코드 메모리로 알림 발사 지속. `daily_live.py` (no --realtime)도 종료 안 하면 좀비 누적. 알림 메시지에 PID/instance 자동 prefix + 다중 프로젝트 동거 환경에서 crontab 통째 갱신은 다른 프로젝트 라인 보존 책임. pre_deploy_check에 `pgrep -af daily_live.py` 좀비 카운트 룰 등록 | [lessons/20260506_1](docs/lessons/20260506_1_zombie_bot_old_code_alert.md) |
| 28 | state 보정 도구는 모든 state 파일 커버 필수(`fix_state_balance_mismatch.py`는 multi+vb 동시 처리). 봇이 "잔고 0 — 정리 필요"를 인지하면 즉시 자동 정리(N=3회 누적 후 closed_trades+positions.pop) — 수동 의존 시 알람 피로 누적. 알람 디바운스만으로는 false alarm 영구 차단 불가, 근본 정합 + 자동 정리가 짝 | [lessons/20260511_1](docs/lessons/20260511_1_fix_state_vb_orphan.md) |
| 29 | "거래소에만 존재" 차집합 알람은 dust(<5만원, state에 없음) 자동 silence 필수 — 봇이 정리(state)했지만 거래소 잔여 dust는 수동 매도 외 처리 불가 → 디바운스(3회)만으론 영구 알람 루프. 임계 단일 게이트(5천원)는 정책 없음, 컨텍스트(state 비교) 기반 분리 필요. only_state(매도 누락)는 임계 미적용 — 진짜 사고 알람 유지 | [lessons/20260515_1](docs/lessons/20260515_1_dust_only_exchange_alert_loop.md) |
| 30 | 안전장치(5연패 자동 중단) 알람도 발사 후 디바운스 필수 — self.running=False만으로 cycle 중단 가정 금지. cooldown_until(매수 차단)과 alerted_until(알람 silence)은 분리 플래그로 관리. _send_periodic_report 매 cycle(9분) 호출 시 동일 5연패 재인지 → 동일 알람 4회 반복 사고 | [lessons/20260516_1](docs/lessons/20260516_1_consec_loss_alert_loop.md) |
| 31 | 코드 변경(scp)과 인프라 변경(crontab/systemd)은 별도 채널 — scp+restart로는 cron 절대 갱신 안 됨. deploy_to_aws.sh 우회 시 cron 미등록 silent fail (ml_weekly_review/ml_outcome_match 5/15~5/19 누락). silence 플래그(alerted_until)와 매수 차단(cooldown_until)이 독립 관리 시 cooldown 갱신 후 alerted_until 자동 동기화 책임 코드 명시 필수. pre_deploy_check에 "기록된 cron 실제 등록" 검증 룰 추가 필요 (lessons #9 강화) | [lessons/20260520_1](docs/lessons/20260520_1_cron_registration_missing.md) |
| 32 | 업비트 시장가 매수 응답 `order["amount"]`는 None일 수 있음 — `float(None)` → TypeError → entry_qty=0 영구 저장 → 부분 TP 후 state 잔량 회계 영구 drift. 결정 사슬은 `order.filled` → `order.amount` → `fetch_balance` → `order_amount/exec_price` 4단 + `entry_qty<=0` invariant 가드 필수. fix_state_balance_mismatch는 종목 add/remove 외에 잔량(qty) 미러링도 수행해야 함 (lessons #10 "state는 거래소 미러" = 종목+수량 양면). AWS crontab은 다른 프로젝트 deploy로 BitCoin 라인 7개 누락 가능 — pre_deploy_check에 deploy_to_aws.sh 활성 CRON_xxx baseline ≥ 8 검증 추가 | [lessons/20260524_2](docs/lessons/20260524_2_state_qty_zero_and_cron_loss.md) |
| 33 | lessons #33 — 배포 검증 룰만 늘려도 `deploy_to_aws.sh`를 우회한 raw `scp+restart` 채널에서는 호출되지 않아 사문화. 우회 케이스는 **공식 hotfix wrapper**(`scripts/hotfix_deploy.sh`)로 흡수해 pre_deploy_check 강제 호출을 보존하고, deploy 스크립트 자체의 정합성(`CRON_xxx` 변수 정의 ↔ `echo` 등록 1:1)도 자동 lint 필수 (lessons #31 강화) | [lessons/20260524_1](docs/lessons/20260524_1_g6_deploy_guard.md) |
| 34 | lessons #34 — systemd로 가동하는 장시간 스크립트(`daily_live.py --realtime`)는 cron에서 **절대 호출 금지**. non-realtime 매일 호출(`5 0 * * * daily_live.py`)도 매일 새 PID 추가로 좀비 누적(2026-06-02 8개 적발). pre_deploy_check `check_zombie_bot_processes()`의 non-realtime 처리를 warning에서 **ERROR 승격**(non_realtime ≥ 3 또는 합계 ≥ 3) + `deploy_to_aws.sh`가 등록하는 cron에서 `daily_live.py` (no `--realtime`) 라인 존재 금지 룰 추가 (lessons #24/#27 회귀 차단) | [lessons/20260602_1](docs/lessons/20260602_1_cron_zombie_relapse_no_realtime.md) |
| 35 | 운영 자원(SSH 키/서버 정보)의 canonical 경로는 코드뿐 아니라 **별도 1차 문서**(`docs/ssh_access.md`)에 등재 필수. subagent/CI 세션은 `$HOME` 상속·`~/.ssh/config` 자동 매핑 보장 없음 → 경로 추측 시 `Permission denied (publickey)` silent fail. 정량 데이터 파일명도 등재 — `closed_trades.json`/`ml_outcomes.jsonl` 단독 파일은 부재(각각 `multi_trading_state.json["closed_trades"]`, `workspace/ml_shadow/YYYYMMDD.jsonl` 내부). pre_deploy_check에 canonical 키 + 표준 문서 존재 + deploy 일치 검증 룰 추가 | [lessons/20260603_1](docs/lessons/20260603_1_ssh_key_path_subagent_drift.md) |
| 38 | 연패(consec)는 별도 카운터가 아니라 `closed_trades`를 매 cycle 재계산(`check_consec_loss`/`_get_consec_loss`) — 따라서 `cooldown_until`만 리셋하거나 `/cooldown clear`를 써도 다음 cycle `consec>=5`로 72h 재설정되는 함정. 원인 제거 후 cooldown 근본 해제는 `consec_loss_floor_date` 필드 도입(연패 산정만 floor 이후(>) 거래로 한정, 누적통계 n/wins는 strategy_start 기준 보존). 두 산정 함수 모두 floor 적용 필수(한쪽만 적용 시 경로 A/B 산정 불일치로 사일런트 부활). pre_deploy_check `check_consec_loss_floor_consistency()` 룰 추가 | [lessons/20260607_1](docs/lessons/20260607_1_consec_loss_floor_cooldown_release.md) |
| 37 | lessons #37 — `regime_check.py`는 `--notify` 없이는 텔레그램 발송 안 함(:81 `if notify and should_notify(...)`)이나 `deploy_to_aws.sh` `CRON_REGIME`에 `--notify` 미부여 상태로 방치되어 **BULL 전환 알림 침묵 확정 상태**. 로그 100줄 전부 BEAR라 사고 미체감이었지만 다음 BULL 전환 시 관망 종료 인지 불가였음. cron 명령어 인자 누락은 대표적 silent fail — 선택 인자 default False + cron 명시 없음 = 양쪽 다 "누군가 켜주겠지" 구조. pre_deploy_check `check_regime_notify_flag()` 신설: `deploy_to_aws.sh` CRON_REGIME= 라인에서 `regime_check.py`+`--notify` 동시 존재 검증 (lessons #9/#22/#31 계열) | [lessons/20260801_2](docs/lessons/20260801_2_regime_notify_missing.md) |
| 36-08 | BitCoin_Trade crontab 전면 소실 재발 — Stock_Trade `deploy_aws.sh`가 crontab을 파일 원자 갱신 방식(`crontab config/crontab.txt`)으로 통째 덮어써서 BitCoin_Trade cron 8개 소실, `critical_healthcheck` 3일 4시간 무기록. lessons #32/#34의 로컬 정적 검사(스크립트 소스 내 CRON 변수 카운트)는 PASS였음 — 다른 프로젝트의 서버 파괴 행위는 원천 방어 불가. 배포 성공 = 서버 반영 확인까지. `deploy_to_aws.sh` 마지막에 `ssh "crontab -l \| grep -c BitCoin_Trade" ≥ 8` 실측 게이트 + 미만 시 exit 1 추가. pre_deploy_check `check_deploy_post_check_remote_cron()` 신설 — 실측 라인 및 exit 1 가드 존재 정적 lint | [lessons/20260801_1](docs/lessons/20260801_1_cron_baseline_relapse.md) |
| 38-08 | 아침 브리핑 채널 부재 — lessons #33/#34로 `CRON_LIVE`(09:05 KST 아침 트리거)를 좀비 회피 목적으로 제거했으나 **대체 아침 채널을 마련하지 않음** → 사용자 접점이 18:00 daily_report 단독으로 2개월 방치. `CLAUDE.md`엔 "09:05 KST 실행 권장"이라 있으나 crontab 실등록 X (문서·운영 괴리). `regime_check --notify`는 전환 시에만 발송 → BEAR 지속 상태 침묵. 09:32 KST `daily_check.py --notify --skip-console` 신설(regime 완료 2분 후 `regime_state.json` 신선 반영, 레짐/봇/계좌/cron/이상 5섹션 매일 발송). deploy 사후 실측 게이트 baseline 8→9 상향. pre_deploy_check `check_morning_briefing_registered()` 신설 | [lessons/20260801_3](docs/lessons/20260801_3_morning_briefing_missing.md) |
| 39 | 텔레그램 발송 silent fail (Markdown 400 미확인) — 08-02 09:32 KST 아침 브리핑 첫 자동 발화가 서버 로그엔 "발송 성공"으로 남았으나 텔레그램 미도착. RCA: 브리핑 텍스트에 systemd 필드(`MainPID=…`, `ActiveEnterTimestamp=…`, `daily_live.py`) 등 밑줄이 다수 포함되어 legacy Markdown 파서가 짝이 안 맞아 HTTP 400 반환. 기존 `send_message`는 `resp.status` 미확인 + `except Exception: pass`로 400은 예외가 아니므로 조용히 무시되어 상위 호출자는 성공으로 오판. 어제 22:00 KST 수동 실행은 우연히 파싱을 통과한 텍스트 조합 — **간헐적·데이터 의존 silent fail**. 수정: `send_message` `-> bool` 반환 + `resp.status==200` 명시 확인 + 400 시 parse_mode 제거 plain payload로 자동 재시도 + 실패 시 stdout 로그. `daily_check.py::_send_briefing`은 반환값을 신뢰하여 실패 시 exit 1. pre_deploy_check `check_telegram_send_status_verified()` 신설 (lessons #21 fail-closed·#31 silent fail 계열) | [lessons/20260802_1](docs/lessons/20260802_1_telegram_send_silent_fail_markdown_400.md) |
| 40 | 거래량 필터가 **진행 중인 봉**(`df["volume"].iloc[-1]`)을 읽어 24h 캐시 — `refresh_levels()`는 UTC 00:00 하루 1회 실행이므로 그 값은 개장 수십 초 된 오늘 일봉(누적 ≈ 0). `latest_vol < vol_sma*1.5`가 전 종목·하루 종일 참이 되어 **매수 신호 100% 차단**(08-22 차단 70,388건 / 매수 0건, 유휴 현금 111,017 KRW). BTC가 EMA200을 상향 돌파해 레짐 게이트가 열린 첫날 드러남. 백테스트(`advanced.py:348`)는 봉 **마감** 시점 `volume[i] vs vol_sma[i]`를 비교 — 같은 수식이 실시간에선 "자정 0시 30초 거래량"으로 의미가 뒤바뀐 것(교훈 #1 틱 vs 봉의 변종, 이번은 *필터 입력값*). 수정: `vol_sma`/`latest_vol` **둘 다** `iloc[-2]`(완성봉) + 최소 봉 수 가드 `6→7`(부족 시 NaN→0으로 `if vol_sma > 0`에서 필터 통째 무력화). pre_deploy_check `check_vol_filter_completed_bar()` 신설 | [lessons/20260822_1](docs/lessons/20260822_1_vol_filter_stale_snapshot.md) |
| 41 | 트레일링스탑 고점 갱신 블록에 `save_state()` 누락 — 보유 3종목이 +5~7% 상승했음에도 상태 파일은 진입 시각(00:04)에 4시간 38분간 멈춰 `trail_stop`이 진입가 -10% 그대로. 메모리에선 정상 갱신되어 실시간 손절 판정은 옳게 동작하므로 **로그·동작상 무증상**, 그러나 `Restart=always`+`WatchdogSec=300` 서비스에서 재시작 시 확보 이익 보호가 전부 소멸. 같은 파일에 `save_state()`가 13곳 있는데 이 경로에만 누락 — 경로별 누락은 grep으로 세어야 보임(교훈 #6 계열). 수정: throttle(`TRAIL_PERSIST_INTERVAL_SEC=30`, **config.py 정의 후 import** — 교훈 #19 자체정의 금지) 후 `save_state()`. I/O 부담은 "안 쓰기"가 아니라 간격으로 해결. pre_deploy_check `check_trail_stop_persisted()` 신설 | [lessons/20260822_2](docs/lessons/20260822_2_trail_stop_not_persisted.md) |
| 42 | **매수 직후 보유 종목이 웹소켓 구독에서 탈락 — 손절·익절 무방비.** `_execute_buy`가 재매수 방지로 `del self.levels[symbol]`을 하는데 구독 목록이 `self.levels.keys()`에서 생성됨. 웹소켓은 ~10분마다 재연결하며 구독을 재구성하므로 **매수 후 첫 재연결부터 보유 종목 틱이 끊김** → `_on_ticker` 미실행 → 트레일링스탑 이탈 매도·하드손절·부분익절이 전부 미평가. 실측 구독 194→191(보유 3종목만큼 감소), TP1 도달 03:30~04:00 UTC 대비 실제 체결 05:08(재시작 시점) = 1.1~1.6h 무방비. 하락장이었다면 손절 미발동 대형 손실. 근본원인: `self.levels`가 "매수 후보"와 "구독 대상" 두 의미를 겸함 — 전자 관점의 정당한 삭제가 후자를 파괴. 같은 구성부에 VR 포지션·BTC 예외 추가가 이미 2건 있었음(= levels만으론 부족하다는 신호를 두 번 무시). 수정: `self.state["positions"]` 기반 구독 추가. pre_deploy_check `check_positions_subscribed()` 신설 | [lessons/20260822_3](docs/lessons/20260822_3_positions_unsubscribed.md) |
| 43 | **주문 체결가 대신 신호가가 기록 — 손익·손절선·성과통계 오염.** 업비트 `create_market_*_order` 응답에는 체결 정보가 없다(`average`/`price`/`filled`/`cost` 전부 None, status='wait'). `average or price` 방어가 있었으나 **둘 다 None**인 경우를 상정 못해 호출부 `order.get("price") or price` 폴백이 항상 발동, 돌파 감지 시점 신호가가 체결가로 기록됨. 실측 OP 155.0/815.7157 기록 vs 실제 157.0/805.3244. 매수는 `entry_price`·`entry_qty`·**하드손절선**을, 매도는 `exit_price`·`return_pct`·실현손익·`closed_trades`를 오염 → **승률·평균수익률 통계 자체가 부정확**. 발견 지연 원인 2가지: 업비트 시장가 매수는 잔여 KRW 환불로 **`canceled` 상태 종료**(체결 실패 아님 — status로 판정 금지) 이며 그 때문에 `fetch_closed_orders`에 매수가 안 잡힘, `fetchMyTrades`는 업비트 미지원. → `fetch_order(uuid)` 재조회가 유일 경로. 수정: `upbit_client.settle_order()`/`order_exec_price()` 공용 헬퍼(매수·매도 양쪽 적용, **`filled > 0`으로 성공 판정**, fail-open + 폴백 시 경고 로그) + `entry_amount_krw`를 실제 `cost`로 + `trail_stop`을 확정 체결가 기준 재계산. pre_deploy_check `check_exec_price_settled()` 신설 | [lessons/20260822_4](docs/lessons/20260822_4_exec_price_not_settled.md) |
| 44 | **cron 소실 감시기가 cron 안에 있어 함께 죽음 — 19일 무알람.** 2026-08-03 Stock_Trade `deploy_aws.sh:128`의 `crontab config/crontab.txt`(파일 원자 교체)가 BATA cron 9개를 전면 소실시킴(lessons #36-08 재발). 워치독·헬스체크·아침브리핑·레짐알림 전부 정지했으나 **경보 0건** — lessons #36-08 대응으로 넣은 두 방어가 모두 무력: (a) `deploy_to_aws.sh` 배포 후 실측 게이트는 **배포할 때만** 실행되는데 19일간 전체 배포 없었고 오늘 수정도 hotfix(scp) 경로라 우회(교훈 #33 반복), (b) `daily_check.py::_section_cron` 감시는 **자기 자신이 cron**이라 crontab이 지워지면 감시기도 죽어 침묵. **감시기를 감시 대상 안에 둔 설계 결함**. 수정: `scripts/restore_cron.sh` 신설(읽고-덧붙이기 방식, 타 프로젝트 보존 + 사후 실측 검증 — BATA 0→9, 기타 95→95) + `realtime_monitor._check_cron_integrity()` 신설로 감시를 **systemd(crontab 무관)** 로 이전, baseline 미달 시 critical 경보(6h throttle). pre_deploy_check `check_cron_watchdog_outside_cron()` 신설(정의만 하고 미호출이면 ERROR). **근본 해결: 같은 날 9개를 systemd timer로 전면 이전**(`scripts/install_timers.sh`, JOBS 테이블이 스케줄 단일 진실 원천 — `deploy_to_aws.sh`의 CRON_* 변수 9개는 사문화 방지 위해 삭제). crontab을 0줄로 완전히 비운 시뮬레이션에서 timer 9개 생존 실증. 부수 소득: 역방향 테스트로 **검증 룰 2건이 실제로는 아무것도 잡지 못하는 상태**임을 적발(본문 정규식이 함수 경계 초과 캡처 / 안내 문구에 매칭) — 룰은 통과가 아니라 실패를 잡는지로 검증해야 함 | [lessons/20260822_5](docs/lessons/20260822_5_cron_wipe_detector_in_cron.md) |
