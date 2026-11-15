# Improvement log

Candidates tested so far: 2. Earlier research (before this log): 216 intraday rule sets, 36 trend
rule sets, yearly re-tuning, time-of-day effects and intraday momentum; results are in the README.

| Date | Candidate | Result | Decision |
|---|---|---|---|
| 2026-11-15 | Trend track: rest limit orders for a day (`execution: maker_first`) in place of trading at once | With easy fills: 26.2% vs 24.9% a year, Sharpe better in all three periods, fees 2.0% vs 3.6% a year. With hard fills (1% through the order): 27.0% overall but latest-period Sharpe 0.83 vs 0.95, and 15.5% vs 17.3% a year when started in 2023. | Rejected by the gate (fails the latest-period hard-fill check). The benefit depends on a fill rate a backtest cannot establish. Kept as an option, off by default. |
| 2026-11-15 | Trend track: `target_vol` 0.15 and `rebalance_band` 0.30 (lower risk) | 22.9% vs 24.9% a year, drawdown -17.5% vs -24.1%, Sharpe 1.31 vs 1.13; latest-period Sharpe 0.90 vs 0.95. Passes the gate. | Not adopted. It was found by a reviewer scanning a grid of settings, so it is a picked winner; it has had no independent checks. Carry to the next cycle as a candidate. |

## Changes to the gate

- 2026-11-15: The first version compared annual return under a 0.5% hard-fill assumption. A code
  review showed this rejected `maker_first` only because of a bug (an unfilled order chased the
  previous day's target), and that for immediate-execution candidates the check was just "higher
  return". The gate now compares Sharpe ratios, stresses fills at 1%, adds a latest-period
  hard-fill check and a return floor, and validates its inputs. The latest-period hard-fill check
  was added after seeing that `maker_first` passed overall while being worse in 2023-26.
