"""Chart-reading rules: trend filter, entry trigger, volatility-based stop.

Every column at row i uses only bars up to and including i, so a signal on a
bar's close can be acted on at that close without look-ahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Strategy


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def compute_signals(df: pd.DataFrame, p: Strategy) -> pd.DataFrame:
    """Add `uptrend`, `entry`, `exit` and `atr` columns to a candle frame."""
    out = df.copy()
    c = out["close"]
    fast, slow, trend = ema(c, p.fast_ema), ema(c, p.slow_ema), ema(c, p.trend_ema)
    out["atr"] = atr(out, p.atr_len)
    out["uptrend"] = (c > trend) & (fast > slow)

    if p.mode == "breakout":
        prior_high = out["high"].shift(1).rolling(p.breakout_lookback).max()
        trigger = (c > prior_high) & (c.shift(1) <= prior_high.shift(1))
    elif p.mode == "pullback":
        r = rsi(c, p.rsi_len)
        dipped = r.rolling(p.breakout_lookback).min() < p.rsi_dip
        trigger = dipped & (r > 50) & (r.shift(1) <= 50)
    else:
        raise ValueError(f"Unknown strategy mode {p.mode!r}")

    out["entry"] = (out["uptrend"] & trigger & out["atr"].notna()).fillna(False)
    out["exit"] = ((fast < slow) if p.exit_on_trend_break else False) & slow.notna()
    return out
