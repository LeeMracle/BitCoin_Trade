# Bitcoin Auto-Trading Workflow

비트코인 자동매매 워크플로우 — 시장 분석 → 전략 연구 → 백테스트 → 페이퍼 트레이딩 → 실전 거래

## 🔴 세션 시작 시 읽는 순서

1. **[docs/ROADMAP.md](docs/ROADMAP.md)** — **지금 무엇을 해야 하는가** (현황판, 1페이지)
2. [workspace/reports/20260825_1_session_handoff.md](workspace/reports/20260825_1_session_handoff.md) — 직전 세션 인수인계
3. 이 문서 — 프로젝트 레퍼런스 (규칙·제약·파라미터·교훈 인덱스)

> ROADMAP.md 가 "다음 행동", 이 문서가 "변하지 않는 규칙"이다. **진척 상태를 이 문서에 적지 말 것.**

---

## 거래소 제약: 업비트 (Upbit) — 현물 전용

전략 설계를 규정하는 **구조적 제약**이다. 여기서 벗어나려면 거래소를 바꿔야 한다.

- 기준 통화 **KRW** (`BTC/KRW`) · **현물 전용** — 선물/파생 없음, 펀딩레이트·미결제약정 데이터 없음
- 포지션 **long/flat 만** — **숏 불가 = 하락장 수익 수단이 원천적으로 없다**
- 인증: JWT Bearer (`UPBIT_ACCESS_KEY`, `UPBIT_SECRET_KEY`) · ccxt `ccxt.upbit({...})`
- Rate Limit: 기본 29 req/s, 주문 4 req/s
- API 키에 **출금 권한 절대 부여 금지**
- 상세: [workspace/reference/upbit-api-guide.md](workspace/reference/upbit-api-guide.md) · [업비트 개발자 센터](https://docs.upbit.com/kr)

---

## 현재 전략 파라미터

> **단일 진실 원천은 `services/execution/config.py` 다.** 아래 표는 사람이 읽는 사본이며,
> `scripts/pre_deploy_check.py` 가 이 문서와 config 의 일치를 검사한다(lessons #4).
> 상수를 모듈에서 자체 정의하지 말고 config 에서 import 할 것(lessons #19).

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 진입 | **DC(12)** 돌파 | [ADR 20260505-1](docs/decisions/20260505_1_strategy_param_tuning.md) (50→20→15→10→15→12) |
| 손절 | ATR(14) × 3.0 트레일링 + **하드 캡 -10%** | lessons #13 |
| 익절 | **TP1 +5.5% 50%** / **TP2 +10% 50%** | [ADR 20260824-1](docs/decisions/20260824_1_tp1_55_partial.md) · [20260824-2](docs/decisions/20260824_2_tp2_10pct.md) |
| 슬롯 | **15** · 단일 종목 비중 상한 **0.10** | [ADR 20260825-1](docs/decisions/20260825_1_slot_expansion.md) |
| 레짐 필터 | **ON** — BTC 종가 < EMA200 시 전 종목 신규 매수 차단 | [ADR 20260516-2](docs/decisions/20260516_2_entry_conditions_tightening.md) |
| 종목 풀 | `MIN_VOLUME_KRW` **5억** (~117 종목) | [ADR 20260607-1](docs/decisions/20260607_1_min_volume_revert_5e.md) |
| 변동성 필터 | `MAX_ATR_PCT` 0.07 · `VOL_FILTER` ×1.5 | ADR 20260516-2 |
| 안전장치 | 서킷 -20% / -25% · 일일손실 -3% · 5연패 72h 중단 | lessons #11 · #30 · #38 |
| 수수료 | 0.05% × 2 (`FEE_RATE = 0.0005`) | — |

**보조 전략(관찰용)**: RSI(10) >50 / <45 + EMA(150)

### ⚠ ML 게이트 — 현재 shadow 수집 중, 차단 안 함

`ML_FILTER_ENABLED=1` + `ML_SHADOW_MODE=1` (서버 drop-in `/etc/systemd/system/btc-trader.service.d/ml.conf`).
점수 기록 O / 매수 차단 X.

🔴 **지금 LIVE 로 켜지 말 것** — 학습 positive_rate 48.6% vs 라이브 점수 중앙값 0.147 로
**prior shift** 가 있어 임계 0.40 이면 **99.3% 차단**된다. 슬롯 확대가 무의미해진다.
전환 조건: 독립표본 800건 수집 → **차단기가 아니라 랭커로 재설계**.
분석: [research/20260825_2](workspace/research/20260825_2_ml_gate_assessment.md)

---

## 판정 기준 — "지금 전략을 고쳐야 하는가"

**[docs/strategy_improvement_criteria.md](docs/strategy_improvement_criteria.md)** 가 사전 등록된 규칙이다.

- **수익률로 판정 금지** — 필요 표본 68 ~ 1,064건
- **승률이 훨씬 예민하다** — n=20 에서 수익률은 판정불가인데 승률은 유의(p=0.034)
- **구조적 손익분기 승률 56.3%** = `10/(7.75+10)` — 표본 0건으로 계산 가능. 백테스트 62.6~65% 대비 여유 6~9%p뿐
- **판정 시점 사전 고정**: n=30 실행검증만 / **n=50 CI상한 < 56.3% → 개선필요 확정** / n=150 CI하한 > 56.3% → 정상확인
- ⚠ **L1(실행) 통과 전에는 L2(성과)를 읽지 않는다** — 결함이 섞인 표본은 전략 성적이 아니다
- ⚠ **실거래 30건은 "실행 검증"용이지 파라미터 선택용이 아니다** — 파라미터 선택은 백테스트로만 가능하다. 두 질문을 섞지 말 것

### 미해결로 남은 것

- **포트폴리오 수익성** — 종목별 백테스트는 양호하나 슬롯 제약 포트폴리오에서 손실 ([ADR 20260823-2](docs/decisions/20260823_2_position_weight_cap.md)). 슬롯 15 확대로 개선됐으나 실거래 미검증
- **TP2 축** — 슬롯과 **대체재**라 독립 축으로 최적화하면 안 된다. 재측정 결과 변경 없음 ([research/20260831_1](workspace/research/20260831_1_tp2_slot_combo.md))

---

## 작업 규칙

- 전략 규칙은 반드시 Strategy Researcher 산출물(strategy_spec) 기반
- 라이브 거래는 Execution Risk Guard 승인 + PM Orchestrator 최종 확인 필요
- **인샘플 성과만으로 프로덕션 이동 금지**
- 각 단계는 검토 가능한 아티팩트 필수 (보고서, 로그, 메트릭)
- **Execution Plan 강제** — 비자명 작업(30분↑ / 코드·외부시스템·전략·CLAUDE.md 변경 중 1개↑)은
  착수 전 `workspace/plans/YYYYMMDD_작업명.md` 생성. 목표·성공기준이 빈칸이면 착수 금지.
  [workspace/plans/README.md](workspace/plans/README.md)
- **자기평가 금지 / 교차검증 필수** — 구현한 세션은 자기 산출물을 PASS 판정하지 않는다.
  별도 세션 / 서브에이전트(`cto` review) / 자동 검증 스크립트 중 최소 1개.
  결과는 "확인 항목 N개 / 발견 이슈 M개" 형식. [docs/cross_review_policy.md](docs/cross_review_policy.md)

---

## 시행착오 관리

- **기록**: `docs/lessons/YYYYMMDD_N_제목.md` — 원인·수정·검증규칙·교훈
- **자동 검증**: `scripts/pre_deploy_check.py` — 배포 전 실행, 기록된 검증규칙을 코드로 집행
- **참조 의무**: 전략 변경·배포 스크립트 수정·서버 설정 변경 시 관련 lessons 먼저 확인
- **신규 오류 시**: (1) 수정 → (2) lessons 기록 → (3) pre_deploy_check 룰 추가 → (4) 필요 시 이 문서 갱신

> 📖 **[docs/lessons/INDEX.md](docs/lessons/INDEX.md) — 49개 교훈 상세 (원인·수정·검증규칙 전문)**
> 아래는 한 줄 인덱스다. **관련 영역을 건드리기 전에 INDEX 에서 해당 항목 전문을 읽을 것.**

### 메타 교훈 — 검증 룰 자체가 반복해서 속았다 (동일 유형 7회)

| 무엇이 | 어떻게 실패했나 |
| --- | --- |
| 정규식이 **이름**을 물으면 | 정의부·주석·유사이름이 전부 답이 된다 → **호출 형태**(`self.foo(`)로 물어라 (#48) |
| 정규식은 "무엇이 있는가"만 | "**어디에 놓였는가**"는 **AST 로만** 물을 수 있다 (#45, #49) |
| 시뮬레이션은 로직을 **복제** | 실제 파일 들여쓰기와 무관 — 4/4 통과하고도 버그 생존 (#49) |
| 룰은 통과가 아니라 | **실패를 잡는지**(역방향 테스트)로 검증해야 한다 (#44) |

### 영역별 한 줄 인덱스

#### 전략 실행 정합 — 백테스트와 실시간이 같은 것을 계산하는가

| # | 한 줄 |
| --- | --- |
| 1 | 봉 마감 기반 전략을 실시간 틱으로 실행 금지 (가짜 돌파) |
| 2 | 백테스트 상승장 비중 높으면 하락장 성과 과대평가 — 하락장 구간 별도 검증 |
| 4 | CLAUDE.md ↔ config.py ↔ 서버 전략 파라미터 동기화 필수 |
| 40 | 거래량 필터가 **진행 중인 봉**을 읽어 전 종목 100% 차단 — `iloc[-2]` 완성봉 필수 |
| 43 | 업비트 시장가 응답엔 체결정보 없음(전부 None) — `fetch_order(uuid)` 재조회가 유일 경로 |
| 48 | 연결은 죽기 전에 **먼저 썩는다** — liveness(침묵) 감시로 freshness(지연)를 지킬 수 없다 |

#### 매수/매도 경로 누락 — "모든 경로"에 적용했는가

| # | 한 줄 |
| --- | --- |
| 6 | 전략 필터는 모든 매수 경로(scanner + realtime_monitor)에 적용 필수 |
| 26 | 안전장치 신규 추가 시 `grep buy_market` 등으로 진입점 전수 열거 + task별 분리 |
| 42 | 매수 직후 보유 종목이 웹소켓 구독에서 탈락 — 손절·익절 무방비 (`positions` 기반 구독) |
| 45 | 포지션 종료가 TP 루프 안에 있어 **도달 불가능** — 이익 청산에서만 발생 = 통계 하방 편향 |

#### 상태·회계 정합 — state 는 거래소의 미러인가

| # | 한 줄 |
| --- | --- |
| 8 | 평가금액은 거래소 API 전체 자산 합산 필수 (BTC만 집계하면 알트 누락) |
| 10 | 상태 파일은 "거래소 미러" — state ↔ balance 불일치 즉시 경보 |
| 25 | 부분 익절 회계 = 불변 입력(`entry_qty`) + 가변 추적(`tp_sold_levels`) 분리. SL 우선 |
| 28 | state 보정 도구는 모든 state 파일 커버 + 잔고 0 인지 시 즉시 자동 정리 |
| 32 | `order["amount"]` 는 None 일 수 있음 — 4단 결정 사슬 + `entry_qty<=0` 가드 |
| 41 | 트레일링 고점 갱신에 `save_state()` 누락 — 무증상이나 재시작 시 확보 이익 소멸 |
| 46 | 수동 매도 회계: 틱 경로가 1시간 교차검증을 앞질러 손익 확정 — `trade_class` allowlist 로 분리 |

#### 안전장치 설계 — 카운터의 리셋 조건까지가 설계다

| # | 한 줄 |
| --- | --- |
| 3 | 안전장치(연패 중단)는 주기 체크가 아닌 **체결 즉시** 체크 |
| 11 | 서킷브레이커는 신규 차단뿐 아니라 **기존 포지션 처리 정책**도 명시 필요 |
| 13 | ATR×N 스탑은 고변동 종목에서 제어 불능 — **하드 손절 캡 필수** |
| 21 | 안전장치는 **fail-closed**(잔고 조회 실패 → 매수 차단). ccxt 싱글톤 + 명시 백오프 |
| 30 | 안전장치 알람도 발사 후 디바운스 필수 — `cooldown_until` 과 `alerted_until` 은 분리 |
| 38 | 연패는 `closed_trades` 재계산이라 cooldown 만 리셋하면 다음 cycle 에 부활 — `consec_loss_floor_date` |
| 47 | "연속 오류"가 실은 **누적 오류** — 리셋 경로가 거래 성공뿐이라 고장 없이 봇이 멈춘다 |
| 49 | 들여쓰기 한 칸이 해제 분기를 `else` 밖으로 빼내 안전장치 우회 — 배치는 AST 로만 검증 |

#### 알림 — 폭주와 침묵은 같은 뿌리

| # | 한 줄 |
| --- | --- |
| 12 | `dict.get(key, default)` 는 값이 None 이면 default 가 무시됨 — 린트 집행 |
| 14 | 이벤트 루프 내 로그는 throttle 필수 — 종목수 × 빈도 곱 폭발 |
| 22 | wrapper(retry/backoff) 일괄 적용 금지 — 조회만. 매수/매도 즉시성 경로는 lessons #3 위배 |
| 29 | "거래소에만 존재" 알람은 dust 자동 silence 필수 — 디바운스만으론 영구 루프 |
| 37 | `regime_check.py` 에 `--notify` 미부여로 BULL 전환 알림 침묵 — cron 인자 누락은 대표적 silent fail |
| 39 | 텔레그램 Markdown 400 을 `except: pass` 가 삼킴 — `resp.status` 확인 + plain 재시도 |

#### 배포·인프라 — 코드 채널과 인프라 채널은 다르다

| # | 한 줄 |
| --- | --- |
| 5 | t3.micro 스왑 필수, 서비스 추가 전 메모리 예산 확인 |
| 7 | **1일 1회 작업은 날짜 체크 + 상태 저장 필수** — 재시작 시 중복 실행 방지 |
| 9 | 자동화 전제 스크립트는 cron/systemd 등록 + pre_deploy_check 검증 필수 |
| 15 | 외부 API 의존 초기화는 재시도+백오프 필수 — systemd 재시작은 대체 불가 |
| 16 | 배포 스크립트가 전제하는 로컬 CLI(rsync 등)도 검증 + 폴백 분기 필수 |
| 17 | 다중 프로젝트 서버에서 프로세스 판정은 `/proc/<pid>/cwd` + systemd unit 역탐색 |
| 18 | venv 리네임 시 crontab/systemd 인터프리터 경로 동시 갱신 — stderr 리디렉션은 silent fail |
| 19 | 모듈이 config 상수를 **자체 정의**하면 동기화 누락 — import 통일 + 전체 grep |
| 20 | 다중 API 키의 키↔환경(서버 IP) 매핑 미명시는 silent fail — 단명 헬스체크 + 디바운스 |
| 23 | 침묵 모드 cron 은 반드시 heartbeat 파일과 짝. 주문에 retry 적용 금지(중복 주문) |
| 24 | 장시간 스크립트는 systemd 단독 — cron 직접 호출 금지(좀비 누적·race) |
| 27 | systemd 재시작은 cron 이 fork 한 좀비를 못 죽인다 — 옛 코드로 알림 발사 지속 |
| 31 | 코드(scp)와 인프라(crontab/systemd)는 **별도 채널** — scp+restart 로 cron 은 갱신 안 됨 |
| 33 | 검증 룰만 늘려도 `scp+restart` 우회 채널에선 사문화 — 공식 `hotfix_deploy.sh` 로 흡수 |
| 34 | systemd 가동 스크립트의 cron 호출은 ERROR 승격 — non-realtime 매일 호출도 좀비 누적 |
| 35 | 운영 자원(SSH 키/서버 정보)의 canonical 경로는 별도 1차 문서 필수 — [docs/ssh_access.md](docs/ssh_access.md) |
| 36 | 타 프로젝트 `deploy` 가 crontab 을 통째 덮어써 BATA cron 소실 — **배포 성공 = 서버 반영 확인까지** |
| 44 | **cron 소실 감시기가 cron 안에 있어 함께 죽음(19일 무알람)** — 감시를 systemd 로 이전 |

---

## 에이전트 팀 (v0.5.1)

사용자는 **bata-pm** 에게만 말한다. 나머지는 PM 이 위임 호출한다.
정의: `.claude/agents/*.md` · 책임: [agents/team.yaml](agents/team.yaml) · **호출 패턴 상세: [docs/agent_pipeline.md](docs/agent_pipeline.md)**

| 에이전트 | 단일 책임 |
| --- | --- |
| **bata-pm** | 사용자 접점·우선순위·승인·주간 audit |
| **bata-investment-expert** | 시장·전략·진단·도메인 파라미터 발의(독점) |
| **bata-engineer** | 기획·개발·유지보수·배포·회귀방지 |
| **bata-operator** | 모니터링·알람 트리아지·일일/주간 보고 |
| btc-market-news-analyst | (보조) 시황·뉴스 브리핑 |

**호출 규칙**: 싱글이 기본 · 병렬은 audit·신규사고 fan-out 에만 · 순차에서 게이트/승인은 PM 직접 ·
**하드룰 R4** 엔지니어 산출물을 엔지니어가 PASS 판정 금지 · incident close 는 PM 직접 확인

---

## 핵심 파일 위치

| | |
| --- | --- |
| **현황판 (지금 할 일)** | [docs/ROADMAP.md](docs/ROADMAP.md) |
| **전략 파라미터 (단일 진실)** | `services/execution/config.py` |
| 배포 | `bash scripts/deploy_to_aws.sh` · 핫픽스 `scripts/hotfix_deploy.sh` |
| 배포 전 검증 | `python scripts/pre_deploy_check.py` |
| 일일 체크 | `python scripts/daily_check.py` (09:32 KST 자동 발송) |
| 서버 접속 | [docs/ssh_access.md](docs/ssh_access.md) — AWS `13.124.82.122` (Seoul, t3.micro, Ubuntu 24.04) |
| 상태 파일 | 서버 `workspace/multi_trading_state.json` |
| MCP 계약 | [infra/mcp.upbit.yaml](infra/mcp.upbit.yaml) |
| 작업 산출물 | [workspace/](workspace/) — research/ reports/ specs/ plans/ runs/ |
| 아키텍처 뷰어 | [src/App.jsx](src/App.jsx) (`npm run dev`) |
| 아카이브 | [docs/00.보고/WBS.md](docs/00.보고/WBS.md) (2026-05-06 동결) |

### MCP 서버

| 서버 | 상태 | 툴 |
| --- | --- | --- |
| market_data | 구현 중 | `get_ohlcv`, `get_ticker`, `get_orderbook`, `get_macro_series` |
| experiment_tracker | 구현 중 | `create_experiment`, `log_run`, `compare_runs` |
| exchange_execution | Phase 3 | 업비트 REST 주문 (페이퍼 → 실전) |
| alerting | Phase 3 | Slack/Telegram |
| secrets_config | Phase 3 | 정식 시크릿 관리 |

> 업비트는 현물 전용 — `get_funding`, `get_open_interest` 없음
