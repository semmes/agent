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

- News sources (2026-11-16): add CNBC and Google News headline feeds (which carry Reuters and
  Bloomberg titles). Bloomberg, Reuters and MT Newswires direct feeds are paid and stay out.
- Hosting (2026-11-16): keep GitHub Actions with a free outside trigger for now. Do not choose an
  always-on host until the agent is connected to Coinbase; the owner wants to see first whether
  the agent can run on Coinbase's platform or whether the question resolves itself there.

## Reported to the owner

- 2026-11-15: intraday rules do not overcome fees (0 of 216 rule sets profitable in both test
  periods); trend following backtests at roughly 15-25% a year with drawdowns near 24%.
- 2026-11-15: buy-and-hold VT is the better default; the trend track needs 6-12 months of paper
  results before a funding decision.
- 2026-11-15: limit-order execution rejected by the gate; a lower-risk setting carried forward.
- 2026-11-16: a model trained on historical price patterns showed no skill on unseen years and
  did worse than the trend rules. News could not be included (no free archive); headlines are
  now being logged hourly for a later test.

## How the weekly cycle runs

- It runs inside the owner's original conversation (session "Daily 1% gain trading agent plan"),
  Saturdays 2:17 pm Eastern. A version that starts a fresh session each week was tried on
  2026-11-16 and does not work: a session started by a schedule gets no push access to the
  repository and no pull-request tools. That schedule is disabled.
- GitHub skips many scheduled runs of the hourly paper job (2 of about 18 fired in the first
  day). Trading results are unaffected because every candle is replayed, but reports, alerts and
  news readings arrive late. Two slots an hour are requested to improve the odds.
