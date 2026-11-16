# Owner notes

What the owner has decided or said, kept here so it survives beyond any one conversation.
Every improvement cycle reads this first and adds to it. Record decisions and preferences only;
anything that can be looked up (results, code, state) belongs elsewhere.

## Decisions

- Products: Bitcoin and Ethereum only. No new tokens or "scam coins".
- No margin. Spot, long-only. Futures were noted as much cheaper in fees and left out for this reason.
- Based in Massachusetts, USA. Coinbase Advanced "Intro" fee tier: 0.50% maker, 0.90% taker.
- Intraday track: stop for the day at +1% (net of fees) or -1%; shut down at -5% on the week.
  Trading day is Eastern time. The agent may choose not to trade on a given day.
- Goal for the trend track: 20% a year, judged monthly. The original goal of 1% a day with no
  losing days was dropped as unachievable.
- Paper trading first, $5,000 starting value. Funding is considered only if paper results are
  good; likely first live amount is $1,000, with no approval needed per trade.
- If paper trading is unsuccessful: work out changes and retest before any Coinbase connection.
- Core savings stay in a broad stock fund (VT); this agent is at most a small side bet.
- Reporting: cheapest and easiest option (GitHub issue comments).
- No paid news data until the agent is proven.
- Improvements may be made without asking, must be checked by sub-agents, and must be reported.
- Wants the agent to act on current news and indicators using a model trained on historical
  price patterns (asked 2026-11-16).

## Reported to the owner

- 2026-11-15: intraday rules do not overcome fees (0 of 216 rule sets profitable in both test
  periods); trend following backtests at roughly 15-25% a year with drawdowns near 24%.
- 2026-11-15: buy-and-hold VT is the better default; the trend track needs 6-12 months of paper
  results before a funding decision.
- 2026-11-15: limit-order execution rejected by the gate; a lower-risk setting carried forward.
