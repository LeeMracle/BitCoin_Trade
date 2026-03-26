---
name: strategy-researcher
description: Turn Bitcoin trading ideas into explicit, testable strategy specifications. Use when defining hypotheses, entry and exit logic, sizing rules, parameter ranges, and validation criteria before backtesting.
---

# Strategy Researcher

Use this skill to convert intuition into rules.

## Required spec sections

- thesis
- market regime where the strategy applies
- signal inputs
- entry rules
- exit rules
- sizing and risk limits
- invalidation
- evaluation metrics

## Research method

1. Write the hypothesis in plain language.
2. Translate it into deterministic rules.
3. Define what would falsify the thesis.
4. Limit parameter search space before backtesting.

## Guardrails

- Avoid data leakage.
- Avoid strategy definitions that depend on discretionary interpretation.
- Prefer simple rules that survive transaction costs.
