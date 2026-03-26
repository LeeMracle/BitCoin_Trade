---
name: market-analyst
description: Analyze Bitcoin market regime using price action, macro context, derivatives, ETF flows, and trusted news. Use when building market context, identifying drivers, or summarizing the current trading environment.
---

# Market Analyst

Use this skill when the task is to describe the market, not to code a strategy immediately.

## First-pass checklist

- Spot price structure across daily and 4h frames
- Realized and implied volatility if available
- Funding rate and open interest trend
- ETF flow or institutional flow proxy
- Macro catalysts, central bank events, and major news

## Output format

Produce:

- current regime
- bullish and bearish drivers
- invalidation conditions
- what data is still missing

## Guardrails

- Separate confirmed facts from inference.
- Include exact dates for time-sensitive claims.
- Do not recommend live trades from one headline or one indicator.
