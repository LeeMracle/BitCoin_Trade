# Bitcoin Trading Project Operating Draft

## Goal

This draft defines an initial operating environment for a Bitcoin trading project built around specialized agents, reusable skills, and a small set of MCP-backed tools.

The design principle is simple:

- separate market judgment from implementation
- separate research from execution
- keep live trading behind explicit risk controls
- make every agent produce artifacts that can be reviewed

## Command Model

You communicate only with the PM Orchestrator.

The PM Orchestrator is the single front door for the project. Every other agent works behind it and reports upward through artifacts, summaries, or approval requests. No specialist agent should talk to the user directly unless the PM explicitly exposes that interaction.

## Recommended Team Topology

### 1. PM Orchestrator

Purpose:
Owns task routing, milestone tracking, artifact quality, and handoff order.

Primary outputs:

- project brief
- sprint/task breakdown
- decision log
- release checklist

User interaction model:

- receives all user requests
- decomposes work for specialist agents
- merges outputs into one response for the user

Should not:

- invent strategy rules without research input
- bypass risk signoff

Core tools:

- filesystem
- git
- issue tracker MCP
- docs/search MCP

### 2. Market Analyst

Purpose:
Builds market context from price, macro, ETF flows, funding, and sentiment.

Primary outputs:

- market regime report
- daily/weekly context summary
- feature candidates for strategy research

Should not:

- push code to execution modules
- make live trading decisions alone

Core tools:

- market data MCP
- news/search MCP
- notebook or lightweight analytics scripts

### 3. Strategy Researcher

Purpose:
Converts market hypotheses into testable trading rules.

Primary outputs:

- strategy specification
- entry/exit rules
- parameter ranges
- experiment plan

Should not:

- tune endlessly on the same sample
- move to production without validation criteria

Core tools:

- research references
- backtest runner
- experiment tracker MCP

### 4. Backtest Engineer

Purpose:
Implements datasets, signal logic, simulation rules, metrics, and reproducible backtests.

Primary outputs:

- strategy code
- backtest reports
- performance metrics
- robustness checks

Should not:

- change strategy intent without approval
- ignore slippage, fees, latency, or missing data

Core tools:

- Python environment
- data storage
- experiment tracker MCP
- test runner

### 5. Execution and Risk Guard

Purpose:
Owns order routing, position reconciliation, risk limits, alerts, and kill switches.

Primary outputs:

- execution policy
- risk rules
- exchange integration checks
- incident log

Should not:

- accept ambiguous signals
- allow live trading without state reconciliation

Core tools:

- exchange MCP or broker API wrapper
- secrets manager
- alerting MCP
- logging/observability stack

## Operating Flow

1. PM Orchestrator opens a work item from the user request.
2. Market Analyst publishes current regime and supporting facts.
3. Strategy Researcher writes a testable strategy spec.
4. Backtest Engineer implements and validates the spec.
5. Execution and Risk Guard approves only if operational constraints are met.
6. PM Orchestrator records the decision and responds to the user.

## Skills To Create

Each role should have one focused skill. Keep SKILL.md short and move detailed references into separate files only when needed.

### Skill: `project-orchestrator`

Use when:
Managing task flow, artifact requirements, release readiness, user communication, and inter-agent handoffs.

Must teach:

- how to break strategy work into stages
- required deliverables per stage
- approval gates before live deployment

### Skill: `market-analyst`

Use when:
Assessing Bitcoin market regime, macro conditions, derivatives positioning, or news impact.

Must teach:

- what data to collect first
- how to separate durable signals from noise
- how to summarize a regime without overclaiming

### Skill: `strategy-researcher`

Use when:
Turning a trading idea into explicit rules and experiment hypotheses.

Must teach:

- how to define entry, exit, sizing, and invalidation
- how to avoid leakage and overfitting
- what evidence is required before handoff

### Skill: `backtest-engineer`

Use when:
Implementing simulation logic, metrics, and reproducible tests.

Must teach:

- fee/slippage/latency assumptions
- walk-forward and out-of-sample checks
- artifact format for reports and logs

### Skill: `execution-risk-guard`

Use when:
Connecting a strategy to paper/live execution and checking operational safety.

Must teach:

- pre-trade risk checks
- exchange state reconciliation
- stop conditions and incident handling

## MCP And Tooling Draft

These are the MCP categories worth defining first.

### 1. Market Data MCP

Responsibilities:

- OHLCV retrieval
- funding/open interest
- ETF flow or macro series retrieval
- symbol metadata and calendar normalization

Suggested interface:

- `get_ohlcv(symbol, timeframe, start, end)`
- `get_funding(symbol, start, end)`
- `get_open_interest(symbol, start, end)`
- `get_macro_series(series_id, start, end)`

Priority:
Highest

### 2. News and Research MCP

Responsibilities:

- trusted news search
- filing or official source fetch
- article metadata and date normalization

Suggested interface:

- `search_news(query, from_date, to_date, trusted_only)`
- `fetch_article(url)`
- `extract_key_events(text)`

Priority:
High

### 3. Experiment Tracker MCP

Responsibilities:

- save strategy specs
- record run parameters
- store metrics and artifact paths

Suggested interface:

- `create_experiment(name, strategy_id)`
- `log_run(experiment_id, params, metrics, artifact_paths)`
- `compare_runs(experiment_id, run_ids)`

Priority:
High

### 4. Exchange Execution MCP

Responsibilities:

- paper/live order routing
- balance and position sync
- order status polling

Suggested interface:

- `place_order(exchange, symbol, side, type, size, price)`
- `get_positions(exchange)`
- `cancel_open_orders(exchange, symbol)`
- `reconcile_state(exchange, symbol)`

Priority:
High for paper trading, gated for live.

### 5. Alerts and Incident MCP

Responsibilities:

- Slack/Telegram/email alerts
- failed order notifications
- drawdown and heartbeat alerts

Suggested interface:

- `send_alert(channel, severity, message)`
- `trigger_incident(name, payload)`
- `healthcheck(service_name)`

Priority:
Medium

### 6. Secrets and Config MCP

Responsibilities:

- API key resolution
- environment separation
- config versioning checks

Suggested interface:

- `get_secret(key, env)`
- `list_required_secrets(service)`
- `validate_runtime_config(path)`

Priority:
High before any exchange integration.

## Repo Layout Draft

```text
.
|-- agents/
|   `-- team.yaml
|-- docs/
|   `-- agent-team-draft.md
|-- infra/
|   `-- mcp.example.yaml
|-- skills/
|   |-- project-orchestrator/
|   |   `-- SKILL.md
|   |-- market-analyst/
|   |   `-- SKILL.md
|   |-- strategy-researcher/
|   |   `-- SKILL.md
|   |-- backtest-engineer/
|   |   `-- SKILL.md
|   `-- execution-risk-guard/
|       `-- SKILL.md
`-- workspace/
    |-- research/
    |-- reports/
    |-- specs/
    `-- runs/
```

## Recommended Phase Order

### Phase 1

- create repo skeleton
- define skills
- define MCP contracts
- write strategy spec template

### Phase 2

- implement market data adapter
- implement backtest runner
- implement experiment logging

### Phase 3

- paper trading loop
- alerts
- reconciliation

### Phase 4

- live execution with hard risk gates

## Immediate Recommendation

Start with five agents only. The PM Orchestrator is the only user-facing agent. Do not create separate agents for every indicator, exchange, or strategy idea. That fragmentation adds coordination cost before the system has enough leverage to justify it.

The first production-worthy path is:

- research and reporting workflow
- reproducible backtest workflow
- paper execution workflow

Live execution should remain disabled until those three produce stable artifacts for multiple cycles.
