---
name: execution-risk-guard
description: Govern paper or live Bitcoin trade execution with strict operational and risk controls. Use when validating order flow, reconciling exchange state, enforcing limits, or handling incidents and stop conditions.
---

# Execution Risk Guard

Use this skill when a strategy is close to paper trading or live routing.

## Pre-trade checks

- runtime config is valid
- required secrets exist
- exchange connectivity is healthy
- position limits and notional limits are within policy
- no unreconciled open orders remain

## Runtime checks

- order acknowledgement received
- fills reconcile against intended position
- repeated failures trigger alerts
- drawdown and daily loss limits are enforced

## Stop conditions

- stale market data
- repeated order rejection
- position mismatch
- missing heartbeat

## Guardrails

- Prefer cancel-and-freeze over uncertain state.
- Escalate incidents with exact timestamps and affected symbols.
