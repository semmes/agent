"""Research: a model trained on historical price patterns, tested walk-forward.

Each year the model is refitted on all earlier days and used, unchanged, for that year, so
every prediction is for a day the model has never seen. The prediction is the probability
that the price is higher five days later.

Usage: python -m tradebot.ml
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .quant import sharpe
from .strategy import rsi
from .trend import equity_curve, load_daily, load_trend_config, run, target_weights

HORIZON = 5  # days ahead the model predicts
FIRST_TEST_YEAR = 2018
PERIODS = [("2018", "2019"), ("2020", "2022"), ("2023", None)]


def features(c: pd.DataFrame) -> pd.DataFrame:
    """Indicators known at each daily close. Nothing here looks forward."""
    close, logp = c["close"], np.log(c["close"])
    f = pd.DataFrame(index=c.index)
    for n in (1, 3, 7, 14, 30, 60, 90):
        f[f"ret_{n}"] = logp.diff(n)
    for n in (20, 50, 100, 200):
        f[f"ma_gap_{n}"] = close / close.rolling(n).mean() - 1
    for n in (10, 30, 90):
        f[f"vol_{n}"] = logp.diff().rolling(n).std()
    f["rsi_14"] = rsi(close, 14)
    f["drawdown_90"] = close / close.rolling(90).max() - 1
    f["range"] = (c["high"] - c["low"]) / close
    f["volume_ratio"] = c["volume"] / c["volume"].rolling(30).mean()
    return f


def walk_forward_probabilities(c: pd.DataFrame, make_model) -> pd.Series:
    x = features(c)
    forward = np.log(c["close"]).shift(-HORIZON) - np.log(c["close"])
    y = (forward > 0).astype(int)
    known = x.notna().all(axis=1)
    out = []
    for year in range(FIRST_TEST_YEAR, c.index[-1].year + 1):
        cutoff = pd.Timestamp(f"{year}-01-01")
        # Train only on days whose five-day outcome was already known before the test year.
        train = known & (x.index < cutoff - pd.Timedelta(days=HORIZON))
        test = known & (x.index.year == year)
        if not test.any():
            continue
        model = make_model().fit(x[train], y[train])
        out.append(pd.Series(model.predict_proba(x[test])[:, 1], index=x.index[test]))
    return pd.concat(out)


MODELS = {
    "logistic regression": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000)),
    "gradient boosting": lambda: GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.03, subsample=0.8, random_state=0),
}


def evaluate(c: pd.DataFrame) -> pd.DataFrame:
    cfg = load_trend_config()
    start = f"{FIRST_TEST_YEAR}-01-01"
    trend = target_weights(c["close"], cfg)
    annual_vol = np.log(c["close"]).diff().rolling(cfg.vol_window).std() * np.sqrt(365)
    vol_scale = (cfg.target_vol / annual_vol).clip(upper=1.0)  # same sizing rule as the trend track
    forward_up = (np.log(c["close"]).shift(-HORIZON) > np.log(c["close"]))
    variants = {"trend rules (current)": (trend, None)}
    for name, make in MODELS.items():
        p = walk_forward_probabilities(c, make)
        ok = p.index[p.index <= c.index[-1 - HORIZON]]
        skill = {"auc": round(roc_auc_score(forward_up[ok], p[ok]), 3),
                 "hit_rate_pct": round(((p[ok] > 0.5) == forward_up[ok]).mean() * 100, 1)}
        variants[f"{name}: model alone"] = (((p - 0.5) / 0.1).clip(0, 1) * vol_scale.reindex(p.index), skill)
        variants[f"{name}: trend rules, only when model agrees"] = (trend.reindex(p.index) * (p >= 0.5), skill)
    rows = []
    for name, (w, skill) in variants.items():
        state = run(c, cfg, start=start, weights=w)
        eq = equity_curve(state)
        r = eq.pct_change().fillna(0)
        years = len(eq) / 365
        rows.append({
            "variant": name,
            "annual_return_pct": round(((eq.iloc[-1] / cfg.starting_cash) ** (1 / years) - 1) * 100, 1),
            "max_drawdown_pct": round((eq / eq.cummax() - 1).min() * 100, 1),
            "sharpe": round(sharpe(r), 2),
            **{f"sharpe_{a}-{b or 'now'}": round(sharpe(r.loc[a:b]), 2) for a, b in PERIODS},
            "trades_per_year": round(len(state["trades"]) / years, 1),
            **(skill or {"auc": None, "hit_rate_pct": None}),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    data = load_daily("BTC-USD")
    scored = data["close"].iloc[:-HORIZON].loc["2018":]  # days whose outcome is already known
    base = (data["close"].shift(-HORIZON).loc[scored.index] > scored).mean() * 100
    print(f"data {data.index[0].date()} to {data.index[-1].date()}; "
          f"price was higher {HORIZON} days later on {base:.1f}% of days since 2018")
    print(evaluate(data).to_string(index=False))
