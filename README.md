# agent

A rule-based trading agent for Bitcoin and Ethereum on Coinbase, with a backtester and a
30-day paper-trading run on simulated money. Nothing here places real orders.

## Rules the agent cannot break

| Rule | Setting |
|---|---|
| Tradable products | BTC-USD and ETH-USD only (hard-coded allowlist) |
| Position type | Spot, long-only. No margin, no futures, no shorting |
| Daily target | Close everything and stop for the day at +1% |
| Daily loss cap | Close everything and stop for the day at -0.5% |
| Weekly shutdown | Stop entirely at -5% on the week, until reset by hand |
| Trading day | Resets at midnight Eastern; P&L is measured on the day's starting balance |

Each trade is sized so that hitting its stop, including both fees and slippage, costs no more
than what is left of the day's 0.5% loss budget.

## How it decides

1. **Trend filter:** price above a long moving average and the fast average above the slow one.
2. **Entry:** a close above the prior 7-day high, bought with a limit order (lower maker fee).
3. **Exit:** stop-loss 10 ATRs below entry, the daily target or loss cap, or the trend breaking.
4. **News veto:** free RSS headlines (CoinDesk, Cointelegraph, Federal Reserve) are scored for
   risk every hour. A high-risk reading blocks new entries for 6 hours. News never opens a trade.
   With an `ANTHROPIC_API_KEY` secret the scoring is done by Claude; without one, by keywords.

## What the backtest shows

Three years of 15-minute candles (Nov 2023 to Nov 2026), fees of 0.4% maker / 0.6% taker.
108 rule sets were tuned on the first two years and judged on the unseen third year.

| | Tuning period (2 years) | Unseen year |
|---|---|---|
| Rule sets that made money | 6 of 108 | 7 of 108 |
| Rule sets that made money in both | 1 of 108 | |
| Chosen rule set: return | +1.37% | +1.30% |
| Chosen rule set: worst drawdown | -5.75% | -1.66% |
| Chosen rule set: trades | 44 | 12 |
| Chosen rule set: days that hit +1% | 0.5% of days | 0.3% of days |
| Chosen rule set: worst day | -0.51% | -0.51% |
| Buy and hold BTC, for comparison | +163% | -32% |

- **The 1% daily target is hit on about two days a year.** The agent is in a trade on roughly
  5% of days and flat the rest.
- **The loss rules work.** No day lost more than 0.51%, and the account stayed roughly flat in a
  year when BTC fell 32% and ETH 42%.
- **The edge is not proven.** One winner out of 108 is what chance alone would produce, and at
  half the fees (0.2% / 0.3%) no rule set made money in both periods.
- **Fees and the loss cap set the ceiling.** A round trip costs about 1% of the position. To keep
  a losing day inside 0.5%, a position is usually 10-25% of the account, so a 1% account gain
  needs a 5-10% price move.

Reproduce:

```
pip install -r requirements.txt
python -m tradebot.data --days 1095     # download candles
python -m tradebot.backtest             # backtest config.yaml
python -m tradebot.research             # parameter search with the tune/test split
python -m pytest tests
```

## Paper trading

`.github/workflows/paper.yml` runs hourly on GitHub Actions once it is on the default branch.
Each run replays every 15-minute candle since the last run through the same engine the backtest
uses, starting from $5,000, and stops after 30 days.

- State and the full report live on the `paper-state` branch (`report.md`).
- Each completed day is posted to an issue titled "Paper trading daily log".
- Optional: add an `ANTHROPIC_API_KEY` repository secret for LLM news scoring.
- After a weekly shutdown: `python -m tradebot.paper --reset-shutdown`.

Set `maker_fee` and `taker_fee` in `config.yaml` to your own Coinbase Advanced tier first.
