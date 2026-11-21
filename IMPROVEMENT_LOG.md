# Improvement log

Candidates tested so far: 4 (the lower-risk setting was re-tested on 2026-11-21, not counted again). Earlier research (before this log): 216 intraday rule sets, 36 trend
rule sets, yearly re-tuning, time-of-day effects and intraday momentum; results are in the README.

| Date | Candidate | Result | Decision |
|---|---|---|---|
| 2026-11-15 | Trend track: rest limit orders for a day (`execution: maker_first`) in place of trading at once | With easy fills: 26.2% vs 24.9% a year, Sharpe better in all three periods, fees 2.0% vs 3.6% a year. With hard fills (1% through the order): 27.0% overall but latest-period Sharpe 0.83 vs 0.95, and 15.5% vs 17.3% a year when started in 2023. | Rejected by the gate (fails the latest-period hard-fill check). The benefit depends on a fill rate a backtest cannot establish. Kept as an option, off by default. |
| 2026-11-15 | Trend track: `target_vol` 0.15 and `rebalance_band` 0.30 (lower risk) | 22.9% vs 24.9% a year, drawdown -17.5% vs -24.1%, Sharpe 1.31 vs 1.13; latest-period Sharpe 0.90 vs 0.95. Passes the gate. | Not adopted. It was found by a reviewer scanning a grid of settings, so it is a picked winner; it has had no independent checks. Carry to the next cycle as a candidate. |
| 2026-11-16 | Model trained on historical price patterns (18 indicators, predicting whether price is higher 5 days later; refitted each year on all earlier data, tested 2018-2026). Two models: logistic regression and gradient boosting (`python -m tradebot.ml`) | No predictive skill on unseen years: hit rate 51.0% and 49.9% against 51.9% for always guessing "up"; AUC 0.49 for both. Trading on the model alone: 2.2% and -9.7% a year, against 18.3% for the current trend rules over the same years. Using it as a filter on the trend rules cut the return to 14.4% and 4.2% and deepened the drawdown. A reviewer noted that much of the return gap is fees (the models trade 25 times the account a year) and that a slower-moving use of the same predictions was not tried. | Rejected before the gate. The code stays for re-testing when there is new input data (for example the news log). |
| 2026-11-21 | Re-test of the carried-over `target_vol` 0.15 / `rebalance_band` 0.30, with an independent sceptical check | Reproduced exactly: from 2017 22.9% vs 24.8% a year, drawdown -17.5% vs -24.1%, Sharpe 1.31 vs 1.13, fees 1.3% vs 3.6% a year. The checker's own scripts (not in the repo) found the Sharpe gain is about what the best of the 36-setting grid it came from would show by chance, that grid rankings do not persist between periods, and that the gain depends on the start date (from 26 weekly starts in early 2017 the average gain is 0.07, not 0.18). The lower drawdown comes from the lower `target_vol` and is consistent; it costs about 5 points of return a year on average across start dates. Neither change alone passes the gate. The revised gate (paired across 13 start dates) rejects it: it wins only one of three periods. | Not adopted. It is a lower-risk, lower-return choice rather than an improvement, and it moves further below the 20%/yr goal, so it goes to the owner as a decision. Dropped as an improvement candidate. |

## Changes to the gate

- 2026-11-15: The first version compared annual return under a 0.5% hard-fill assumption. A code
  review showed this rejected `maker_first` only because of a bug (an unfilled order chased the
  previous day's target), and that for immediate-execution candidates the check was just "higher
  return". The gate now compares Sharpe ratios, stresses fills at 1%, adds a latest-period
  hard-fill check and a return floor, and validates its inputs. The latest-period hard-fill check
  was added after seeing that `maker_first` passed overall while being worse in 2023-26.
  A second review noted that the 1% stress level decides the outcome (at 0.5% `maker_first` is
  ahead since 2023), so this rejection reflects caution about an unknowable fill rate, not
  evidence that the change is harmful. Revisit if real fill data ever becomes available.
- 2026-11-21: The gate compared runs from a single start date (2017-01-01), which flattered the
  lower-risk candidate (its 2017-19 win disappears from a start two weeks later). The gate now
  repeats the comparison from 13 start dates two weeks apart and compares the two settings start
  by start (median of the paired differences); it also requires a better Sharpe from at least
  two thirds of the start dates. A first version compared each setting's own median instead, and
  a sub-agent review showed that still let the candidate pass (its 2017-19 median win came from
  different start dates for each setting; paired, it loses that period from 8 of 13 starts).
  The two hard-fill checks now run only when either config uses resting orders: for two configs
  that trade at once, one duplicated the latest-period check and the other was the only overall
  Sharpe check, which the start-date check now covers.
