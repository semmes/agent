# Improvement log

Candidates tested so far: 1. Earlier research (before this log): 216 intraday rule sets, 36 trend
rule sets, yearly re-tuning, time-of-day effects and intraday momentum; results are in the README.

| Date | Candidate | Result | Decision |
|---|---|---|---|
| 2026-11-15 | Trend track: rest limit orders for a day (`execution: maker_first`) in place of trading at once | Return 26.5% vs 25.0% a year and better Sharpe in all three periods, but 23.1% vs 25.0% when price must trade 0.5% through the order. An independent re-implementation reproduced the numbers and judged the advantage fragile. | Rejected by the gate (fails "better with hard fills"). Kept as an option, off by default. |
