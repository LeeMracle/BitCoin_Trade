---
name: backtest-engineer
description: Implement and validate Bitcoin trading backtests with realistic assumptions. Use when building simulation logic, loading datasets, logging experiment runs, or evaluating robustness and reproducibility.
---

# Backtest Engineer

Use this skill when strategy logic needs to be implemented and measured.

## Minimum simulation assumptions

- exchange fees
- slippage
- bar-close or event timing assumptions
- missing data handling
- position sizing rules

## Required outputs

- run configuration
- summary metrics
- equity curve artifact
- trade log artifact
- note on robustness checks

## Validation checks

- in-sample vs out-of-sample split
- walk-forward or rolling validation when possible
- sensitivity to small parameter changes

## Guardrails

- Do not report headline returns without drawdown and trade count.
- Do not compare runs unless assumptions match.
