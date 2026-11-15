import numpy as np
import pandas as pd
import pytest

from tradebot.quant import fee_projection, walk_forward
from tradebot.trend import TrendConfig, fee_rate, new_state, run, step, target_weights


def prices(n=1200, seed=3, drift=0.001):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.03, n))), index=idx)


def test_allowlist():
    with pytest.raises(ValueError):
        TrendConfig(product="DOGE-USD")


def test_fee_rate_falls_with_volume():
    cfg = TrendConfig()
    assert fee_rate(0, cfg) == 0.009
    assert fee_rate(10_000, cfg) == 0.0075
    assert fee_rate(60_000, cfg) == 0.003
    assert fee_rate(60_000, TrendConfig(order_side="maker")) == 0.0015


def test_weights_bounded_and_no_look_ahead():
    c, cfg = prices(), TrendConfig()
    w = target_weights(c, cfg)
    assert w.dropna().between(0, 1).all()
    pd.testing.assert_series_equal(w.iloc[:800], target_weights(c.iloc[:800], cfg))


def test_never_borrows_and_accounts_for_fees():
    c, cfg = prices(), TrendConfig()
    s = run(c, cfg)
    assert s["cash"] >= -1e-6 and s["qty"] >= 0
    assert all(d["weight"] <= 1.0001 for d in s["days"])
    assert s["trades"] and all(t["fee"] > 0 for t in s["trades"])


def test_drawdown_breaker_goes_flat_and_pauses():
    cfg = TrendConfig()
    s = new_state(cfg)
    step(s, "2026-01-01", 100.0, 1.0, cfg)
    assert s["qty"] > 0
    step(s, "2026-01-02", 80.0, 1.0, cfg)  # -20% from the peak
    assert s["qty"] == 0 and s["paused_until"] == "2026-02-01" and s["breaker_trips"] == 1
    step(s, "2026-01-15", 120.0, 1.0, cfg)  # still paused
    assert s["qty"] == 0
    step(s, "2026-02-01", 120.0, 1.0, cfg)  # resumes
    assert s["qty"] > 0


def test_walk_forward_uses_only_past_years():
    idx = pd.date_range("2018-01-01", "2022-12-31", freq="D")
    r = pd.DataFrame({"a": 0.001, "b": -0.001}, index=idx)
    r.loc["2022", ["a", "b"]] = [-0.001, 0.001]  # "b" only becomes best in the test year
    oos, picks = walk_forward(r)
    assert list(picks["rules"]) == ["a", "a"]
    assert picks["return_pct"].iloc[-1] < 0


def test_fee_projection_reaches_lower_tiers():
    p = fee_projection(TrendConfig(), turnover=5.0, years=20)
    assert p["fee_rate_pct"].iloc[0] == pytest.approx(0.9) and p["fee_rate_pct"].iloc[-1] < 0.9
