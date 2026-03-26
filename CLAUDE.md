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

## 현재 단계: Phase 1 완료, Phase 2 진행 중

- [x] Phase 1: 레포 골격, 스킬 정의, MCP 계약 초안
- [ ] Phase 2: 시장 데이터 어댑터, 백테스트 러너, 실험 로깅 구현
- [ ] Phase 3: 페이퍼 트레이딩, 알림, 조정
- [ ] Phase 4: 실전 거래 (하드 리스크 게이트 이후)

## 에이전트 팀 구조

사용자는 **PM Orchestrator**에게만 말한다. 나머지 에이전트는 내부 전용.

| 에이전트 | 역할 | 스킬 파일 |
| --- | --- | --- |
| PM Orchestrator | 단일 사용자 접점, 작업 라우팅, 마일스톤 추적 | [skills/project-orchestrator/SKILL.md](skills/project-orchestrator/SKILL.md) |
| Market Analyst | 시장 레짐 분석, 매크로 데이터 | [skills/market-analyst/SKILL.md](skills/market-analyst/SKILL.md) |
| Strategy Researcher | 가설 → 테스트 가능한 트레이딩 규칙 | [skills/strategy-researcher/SKILL.md](skills/strategy-researcher/SKILL.md) |
| Backtest Engineer | 시뮬레이션, 메트릭, 재현 가능한 백테스트 | [skills/backtest-engineer/SKILL.md](skills/backtest-engineer/SKILL.md) |
| Execution Risk Guard | 주문 라우팅, 포지션 조정, 리스크 한도 | [skills/execution-risk-guard/SKILL.md](skills/execution-risk-guard/SKILL.md) |

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
