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
  - 메인: DC(20)+ATR(14)x3.0 — `services/paper_trading/` (DC50→20 공격적 전환, [경위](docs/decisions/20260329_daytrading_postmortem_and_switch.md))
  - 보조: RSI(10)>50/<45+EMA(150) — 관찰용
  - 일일 체크: `python scripts/daily_check.py` (09:05 KST 실행 권장)
  - 텔레그램 알림: `services/.env.example` 참고하여 `.env` 설정 필요
- [ ] Phase 4: 실전 거래 — 모듈 구현 완료, AWS 배포 필요
  - 실행 모듈: `services/execution/` (upbit_client, trader)
  - AWS 서버: `13.124.82.122` (Seoul, t3.micro, Ubuntu 24.04)
  - 배포: `bash scripts/deploy_to_aws.sh`
  - 일일 실행: `scripts/daily_live.py` (cron UTC 00:05 = KST 09:05)

## 에이전트 팀 구조

사용자는 **PM Orchestrator**에게만 말한다. 나머지 에이전트는 내부 전용.

| 에이전트 | 역할 | 스킬 파일 |
| --- | --- | --- |
| PM Orchestrator | 단일 사용자 접점, 작업 라우팅, 마일스톤 추적 | [skills/project-orchestrator/SKILL.md](skills/project-orchestrator/SKILL.md) |
| Market Analyst | 시장 레짐 분석, 매크로 데이터 | [skills/market-analyst/SKILL.md](skills/market-analyst/SKILL.md) |
| Strategy Researcher | 가설 → 테스트 가능한 트레이딩 규칙 | [skills/strategy-researcher/SKILL.md](skills/strategy-researcher/SKILL.md) |
| Backtest Engineer | 시뮬레이션, 메트릭, 재현 가능한 백테스트 | [skills/backtest-engineer/SKILL.md](skills/backtest-engineer/SKILL.md) |
| Execution Risk Guard | 주문 라우팅, 포지션 조정, 리스크 한도 | [skills/execution-risk-guard/SKILL.md](skills/execution-risk-guard/SKILL.md) |
| **Strategy Pipeline** | 전략 발굴 → 구현 → 백테스트 → 검증 → 등록 파이프라인 | [skills/strategy-pipeline/SKILL.md](skills/strategy-pipeline/SKILL.md) |

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
