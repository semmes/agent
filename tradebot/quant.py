"""Quant research tools for the trend track.

  walk-forward    re-tune each year on the trailing three years, judge on the next year
  deflated Sharpe discount the result for the number of rule sets tried
  fee projection  how Coinbase's volume tiers lower fees as the account grows
  intraday test   does the first half hour of the day predict the last (Shen et al. 2022)
  gate            does a candidate trend.yaml beat the current one robustly enough to adopt

Usage: python -m tradebot.quant [walkforward|fees|intraday|gate --candidate FILE]
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import replace

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

from .trend import FEE_TIERS, TrendConfig, equity_curve, load_daily, load_trend_config, metrics, run

GRID = {
    "ma_windows": [[50], [100, 200], [50, 100, 200], [20, 50, 100, 200]],
    "target_vol": [0.15, 0.25, 0.40],
    "rebalance_band": [0.10, 0.20, 0.30],
}


def grid_returns(close: pd.DataFrame, base: TrendConfig, start: str) -> pd.DataFrame:
    """Daily returns of every rule set in the grid, one column each."""
    cols = {}
    for values in itertools.product(*GRID.values()):
        cfg = replace(base, **dict(zip(GRID, values)))
        cols[str(values)] = equity_curve(run(close, cfg, start=start)).pct_change().fillna(0)
    return pd.DataFrame(cols)


def sharpe(r: pd.Series) -> float:
    return float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0


def walk_forward(returns: pd.DataFrame, train_years: int = 3) -> tuple[pd.Series, pd.DataFrame]:
    """Each year, use the rule set with the best Sharpe over the previous `train_years`."""
    years = sorted(set(returns.index.year))
    parts, picks = [], []
    for y in years[train_years:]:
        train = returns[(returns.index.year >= y - train_years) & (returns.index.year < y)]
        best = train.apply(sharpe).idxmax()
        test = returns.loc[returns.index.year == y, best]
        parts.append(test)
        picks.append({"year": y, "rules": best, "return_pct": round(((1 + test).prod() - 1) * 100, 1),
                      "max_dd_pct": round(((1 + test).cumprod() / (1 + test).cumprod().cummax() - 1).min() * 100, 1)})
    return pd.concat(parts), pd.DataFrame(picks)


def deflated_sharpe(r: pd.Series, trial_sharpes: pd.Series) -> float:
    """Probability the true Sharpe is above zero after allowing for the best-of-N selection
    (Bailey and Lopez de Prado). Sharpes here are per-day, not annualised."""
    n, trials = len(r), len(trial_sharpes)
    sr = r.mean() / r.std()
    var_trials = (trial_sharpes / np.sqrt(365)).var()
    gamma = 0.5772156649
    expected_max = np.sqrt(var_trials) * (
        (1 - gamma) * norm.ppf(1 - 1 / trials) + gamma * norm.ppf(1 - 1 / (trials * np.e))
    )
    denom = np.sqrt(1 - skew(r) * sr + (kurtosis(r, fisher=False) - 1) / 4 * sr**2)
    return float(norm.cdf((sr - expected_max) * np.sqrt(n - 1) / denom))


def summarize(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    years = len(r) / 365
    return {"annual_return_pct": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round((eq / eq.cummax() - 1).min() * 100, 1), "sharpe": round(sharpe(r), 2)}


def fee_projection(cfg: TrendConfig, turnover: float, maker_share: float = 0.0, years: int = 20) -> pd.DataFrame:
    """Fee tier by year if the account compounds at the annual target. `maker_share` is the
    share of traded value filled at the maker fee."""
    rows = []
    for y in range(0, years + 1):
        equity = cfg.starting_cash * (1 + cfg.annual_target) ** y
        monthly = equity * turnover / 12
        tier = [t for t in cfg.fee_tiers if monthly >= t[0]][-1]
        blended = maker_share * tier[1] + (1 - maker_share) * tier[2]
        rows.append({"year": y, "account": round(equity), "volume_per_month": round(monthly),
                     "maker_fee_pct": tier[1] * 100, "taker_fee_pct": tier[2] * 100,
                     "fee_drag_pct_per_year": round(blended * turnover * 100, 2)})
    return pd.DataFrame(rows)


def intraday_momentum(candles: pd.DataFrame, split: str = "2025-11-15") -> pd.DataFrame:
    """First 30 minutes of the UTC day against the last 30 minutes, from 15-minute bars."""
    c = candles.copy()
    c["day"] = c.index.floor("D")
    c["slot"] = (c.index - c["day"]) // pd.Timedelta(minutes=15)
    opens = c.pivot(index="day", columns="slot", values="open")
    closes = c.pivot(index="day", columns="slot", values="close")
    full = closes.notna().all(axis=1)
    first = (closes[1] / opens[0] - 1)[full]
    last = (closes[95] / opens[94] - 1)[full]
    rows = []
    for name, sl in (("first two years", slice(None, split)), ("last year", slice(split, None))):
        f, l = first[sl], last[sl]
        long_days = l[f > 0]
        rows.append({"period": name, "days": len(f), "correlation": round(f.corr(l), 3),
                     "avg_last_30min_after_up_open_pct": round(long_days.mean() * 100, 4),
                     "avg_last_30min_all_days_pct": round(l.mean() * 100, 4)})
    return pd.DataFrame(rows)


GATE_PERIODS = [("2017", "2019"), ("2020", "2022"), ("2023", None)]
GATE_START = "2017-01-01"
HARD_FILL_BUFFER = 0.01  # stress: a resting order fills only if price trades 1% through it


def gate(candles: pd.DataFrame, baseline: TrendConfig, candidate: TrendConfig) -> dict:
    """Promotion gate: a candidate replaces the baseline only if its risk-adjusted return is
    better across periods (not just overall), it does not add drawdown, it still holds up when
    resting orders are assumed hard to fill, and it does not give up the return target."""
    if candidate.product != baseline.product:
        raise ValueError("Candidate and baseline must trade the same product")
    if candles.index[0] > pd.Timestamp("2016-06-01"):
        raise ValueError("The gate needs daily history from mid-2016 so all three periods are covered")

    def profile(cfg: TrendConfig) -> dict:
        eq = equity_curve(run(candles, cfg, start=GATE_START))
        if eq.index[0] != pd.Timestamp(GATE_START):
            raise ValueError(f"Config cannot trade from {GATE_START} (first day {eq.index[0].date()}): "
                             "its indicators need more history than the data holds")
        r = eq.pct_change().fillna(0)
        hard_cfg = replace(cfg, fill_buffer=max(HARD_FILL_BUFFER, cfg.fill_buffer))
        hard = equity_curve(run(candles, hard_cfg, start=GATE_START)).pct_change().fillna(0)
        return {"period_sharpes": [sharpe(r.loc[a:b]) for a, b in GATE_PERIODS],
                "sharpe": sharpe(r), "hard_fill_sharpe": sharpe(hard),
                "hard_fill_latest_sharpe": sharpe(hard.loc[GATE_PERIODS[-1][0]:]),
                "annual_return_pct": ((eq.iloc[-1] / cfg.starting_cash) ** (365 / len(eq)) - 1) * 100,
                "max_drawdown_pct": (eq / eq.cummax() - 1).min() * 100}

    base, cand = profile(baseline), profile(candidate)
    wins = sum(c > b for c, b in zip(cand["period_sharpes"], base["period_sharpes"]))
    target = min(base["annual_return_pct"], baseline.annual_target * 100)
    checks = {
        "better_sharpe_in_2_of_3_periods": bool(wins >= 2),
        "latest_period_sharpe_within_0.05": bool(cand["period_sharpes"][-1] >= base["period_sharpes"][-1] - 0.05),
        "drawdown_not_worse_by_2pts": bool(cand["max_drawdown_pct"] >= base["max_drawdown_pct"] - 2),
        "hard_fill_sharpe_not_worse": bool(cand["hard_fill_sharpe"] >= base["hard_fill_sharpe"]),
        "hard_fill_latest_sharpe_within_0.05": bool(
            cand["hard_fill_latest_sharpe"] >= base["hard_fill_latest_sharpe"] - 0.05),
        "return_at_least_target_or_baseline": bool(cand["annual_return_pct"] >= target),
    }
    rounded = lambda p: {k: [round(x, 2) for x in v] if isinstance(v, list) else round(float(v), 2) for k, v in p.items()}
    b, c = vars(baseline), vars(candidate)
    changed = {k: (b[k], c[k]) for k in b if b[k] != c[k]}  # shown so an unintended difference is visible
    return {"changed_settings": changed, "baseline": rounded(base), "candidate": rounded(cand),
            "checks": checks, "passed": all(checks.values())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["walkforward", "fees", "intraday", "gate"])
    ap.add_argument("--candidate", help="candidate trend config (yaml) for the gate")
    ap.add_argument("--start", default="2017-01-01")
    a = ap.parse_args()
    cfg = load_trend_config()
    pd.set_option("display.width", 200)
    if a.command == "walkforward":
        rets = grid_returns(load_daily(cfg.product), cfg, a.start)
        oos, picks = walk_forward(rets)
        print(picks.to_string(index=False))
        print("walk-forward:", summarize(oos))
        print("deflated Sharpe probability:", round(deflated_sharpe(oos, rets.apply(sharpe)), 3))
    elif a.command == "gate":
        if not a.candidate:
            ap.error("gate needs --candidate FILE")
        if a.start != GATE_START:
            ap.error(f"gate always runs from {GATE_START}; --start is not accepted")
        result = gate(load_daily(cfg.product), cfg, load_trend_config(a.candidate))
        for key in ("changed_settings", "baseline", "candidate", "checks"):
            print(f"{key}: {result[key]}")
        print("PASSED" if result["passed"] else "REJECTED")
    elif a.command == "fees":
        state = run(load_daily(cfg.product), cfg, start=a.start)
        t = pd.DataFrame(state["trades"])
        if len(state["days"]) < 365 or t.empty or t["notional"].sum() == 0:
            raise SystemExit("Need at least a year of history with trades to project turnover")
        m = metrics(state, cfg)
        share = t.loc[t["type"] == "maker", "notional"].sum() / t["notional"].sum()
        print(f"turnover {m['turnover_per_year']}x per year, {share:.0%} of traded value at the maker fee")
        print(fee_projection(cfg, m["turnover_per_year"], share).to_string(index=False))
    else:
        from .data import load_cached
        print(intraday_momentum(load_cached(cfg.product)).to_string(index=False))
