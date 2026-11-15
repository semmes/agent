import numpy as np
import pandas as pd

from tradebot.config import Strategy
from tradebot.news import score_keywords
from tradebot.strategy import compute_signals


def candles(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n)))
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {"open": close, "high": close * 1.002, "low": close * 0.998, "close": close, "volume": 1.0}, index=idx
    )


def test_signals_do_not_look_ahead():
    df = candles()
    p = Strategy(trend_ema=400, fast_ema=50, slow_ema=200)
    full = compute_signals(df, p)
    part = compute_signals(df.iloc[:2000], p)
    cols = ["entry", "exit", "atr"]
    pd.testing.assert_frame_equal(full[cols].iloc[:2000], part[cols])
    assert full["entry"].any()


def test_keyword_scorer_flags_shocks_only():
    calm = [{"source": "x", "title": "Bitcoin ETF sees steady inflows", "ts": 0}]
    shock = [{"source": "x", "title": "Major exchange halts withdrawals after hack", "ts": 0}]
    assert score_keywords(calm).risk == "low"
    assert score_keywords(shock).risk == "high"
