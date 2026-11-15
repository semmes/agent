# agent

A rule-based trading agent for Bitcoin and Ethereum on Coinbase, with a backtester and a
30-day paper-trading run on simulated money. Nothing here places real orders.

## Rules the agent cannot break

| Rule | Setting |
|---|---|
| Tradable products | BTC-USD and ETH-USD only (hard-coded allowlist) |
| Position type | Spot, long-only. No margin, no futures, no shorting |
| Daily target | Close everything and stop for the day at +1% |
| Daily loss cap | Close everything and stop for the day at -1% |
| Weekly shutdown | Stop entirely at -5% on the week, until reset by hand |
| Trading day | Resets at midnight Eastern; P&L is measured on the day's starting balance |

Each trade is sized so that hitting its stop, including both fees and slippage, costs no more
than 0.5% of the account, so one losing day allows two attempts inside the 1% cap.
The +1% target is net of fees: the entry fee is already paid and the exit fee is deducted
before a day counts as +1%.

## How it decides

1. **Trend filter:** price above a long moving average and the fast average above the slow one.
2. **Entry:** a close above the prior 7-day high, bought with a limit order (lower maker fee).
3. **Exit:** stop-loss 10 ATRs below entry, the daily target or loss cap, or the trend breaking.
4. **News veto:** free RSS headlines (CoinDesk, Cointelegraph, Federal Reserve) are scored for
   risk every hour. A high-risk reading blocks new entries for 6 hours. News never opens a trade.
   With an `ANTHROPIC_API_KEY` secret the scoring is done by Claude; without one, by keywords.

## What the backtest shows

Three years of 15-minute candles (Nov 2023 to Nov 2026), fees of 0.5% maker / 0.9% taker.
216 rule sets were tuned on the first two years and judged on the unseen third year.

| | Tuning period (2 years) | Unseen year |
|---|---|---|
| Rule sets that made money | 5 of 216 | 13 of 216 |
| Rule sets that made money in both | 0 of 216 | |
| Chosen rule set: return | -1.33% | +1.27% |
| Chosen rule set: worst drawdown | -5.69% | -1.38% |
| Chosen rule set: trades | 35 | 11 |
| Chosen rule set: days that hit +1% | 0.4% of days | 0.5% of days |
| Buy and hold BTC, for comparison | +163% | -32% |

- **No rule set tested is profitable at these fees.** The chosen one is the closest to
  break-even across both periods; the typical rule set lost 7-12%.
- **The 1% daily target is hit on about two days a year.** The agent is flat on most days.
- **The loss rules work.** Losses stay inside the daily cap, and the account stayed roughly flat
  in a year when BTC fell 32% and ETH 42%.
- **Fees set the ceiling.** A round trip costs 1.0-1.4% of the position. Risking the full 1%
  daily cap on one trade made results worse (-8.9% and -5.1%), not better.

## Paper-run success criteria

The 30-day run counts as a pass only if all of these hold:

1. Ending balance above $5,000 after fees.
2. No day worse than about -1.1% (the cap plus slippage).
3. No weekly shutdown.
4. At least 5 completed trades. Fewer than that is too small a sample to judge either way.

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

Fees in `config.yaml` are set to 0.50% maker / 0.90% taker; update them if the tier changes.
