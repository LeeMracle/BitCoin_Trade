---
name: project-orchestrator
description: Coordinate the Bitcoin trading project across research, strategy, backtest, and execution stages. Use when managing handoffs, defining deliverables, reviewing readiness gates, deciding the next highest-value work item, or acting as the single user-facing PM agent.
---

# Project Orchestrator

Use this skill to keep work ordered and reviewable.

## Workflow

1. Receive the user request as the PM Orchestrator.
2. Identify the current stage: market research, strategy definition, backtest implementation, paper trading, or live readiness.
3. Delegate only the smallest specialist task needed to move the project forward.
4. Require a concrete artifact from the current stage before moving forward.
5. Reject ambiguous handoffs. Ask for a spec, report, metric set, or incident record.
6. Keep live trading gated behind explicit risk approval.
7. Respond to the user with a merged view rather than raw specialist output.

## Required artifacts by stage

- Research: market regime note with sources and dates
- Strategy: rule-based spec with entry, exit, sizing, and invalidation
- Backtest: reproducible run config plus metrics
- Paper trading: order log, reconciliation log, and alert log
- Live readiness: kill switch, limits, and operational checklist

## Guardrails

- The user talks only to the PM Orchestrator.
- Do not merge research conclusions directly into execution logic without a strategy spec.
- Do not accept in-sample performance alone.
- Prefer fewer active workstreams if artifacts are not reviewable.
