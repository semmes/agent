import numpy as np
import pandas as pd
import pytest

from dataclasses import replace

from tradebot.quant import fee_projection, gate, walk_forward
from tradebot.trend import TrendConfig, fee_rate, new_state, run, step, target_weights


def prices(n=1200, seed=3, drift=0.001):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.03, n))), index=idx)
    return pd.DataFrame({"close": close, "high": close * 1.02, "low": close * 0.98})


def bar(close, high=None, low=None):
    return {"close": close, "high": high or close, "low": low or close}


def test_allowlist():
    with pytest.raises(ValueError):
        TrendConfig(product="DOGE-USD")


def test_fee_rate_falls_with_volume():
    cfg = TrendConfig()
    assert fee_rate(0, cfg) == 0.009
    assert fee_rate(10_000, cfg) == 0.0075
    assert fee_rate(60_000, cfg) == 0.003
    assert fee_rate(60_000, cfg, maker=True) == 0.0015


def test_weights_bounded_and_no_look_ahead():
    c, cfg = prices()["close"], TrendConfig()
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
    step(s, "2026-01-01", bar(100.0), 1.0, cfg)
    assert s["qty"] > 0
    step(s, "2026-01-02", bar(80.0), 1.0, cfg)  # -20% from the peak
    assert s["qty"] == 0 and s["paused_until"] == "2026-02-01" and s["breaker_trips"] == 1
    step(s, "2026-01-15", bar(120.0), 1.0, cfg)  # still paused
    assert s["qty"] == 0
    step(s, "2026-02-01", bar(120.0), 1.0, cfg)  # resumes
    assert s["qty"] > 0


def test_maker_first_fills_at_limit_or_falls_back_to_taker():
    cfg = TrendConfig(execution="maker_first")
    s = new_state(cfg)
    step(s, "2026-01-01", bar(100.0), 1.0, cfg)
    assert s["qty"] == 0 and s["pending"]["limit"] == 100.0  # order rests, nothing bought yet
    step(s, "2026-01-02", bar(103.0, high=104.0, low=99.0), 1.0, cfg)  # traded through 100
    t = s["trades"][-1]
    assert t["type"] == "maker" and t["price"] == 100.0 and t["fee_rate"] == 0.005

    s = new_state(cfg)
    step(s, "2026-01-01", bar(100.0), 1.0, cfg)
    step(s, "2026-01-02", bar(105.0, high=106.0, low=101.0), 1.0, cfg)  # never came back to 100
    t = s["trades"][-1]
    assert t["type"] == "taker" and t["price"] == 105.0 and t["fee_rate"] == 0.009


def test_breaker_exit_does_not_wait_for_a_fill():
    cfg = TrendConfig(execution="maker_first")
    s = new_state(cfg)
    step(s, "2026-01-01", bar(100.0), 1.0, cfg)
    step(s, "2026-01-02", bar(100.0, low=99.0), 1.0, cfg)
    assert s["qty"] > 0
    step(s, "2026-01-03", bar(80.0), 1.0, cfg)
    assert s["qty"] == 0 and s["pending"] is None and s["trades"][-1]["type"] == "taker"


def test_fill_buffer_requires_price_to_trade_through_the_limit():
    cfg = TrendConfig(execution="maker_first", fill_buffer=0.01)
    s = new_state(cfg)
    step(s, "2026-01-01", bar(100.0), 1.0, cfg)
    step(s, "2026-01-02", bar(102.0, high=103.0, low=99.5), 1.0, cfg)  # dipped 0.5%, needs 1%
    assert s["trades"][-1]["type"] == "taker"


def test_missed_order_is_cancelled_and_todays_target_is_used():
    cfg = TrendConfig(execution="maker_first")
    s = new_state(cfg)
    step(s, "2026-01-01", bar(100.0), 1.0, cfg)  # buy rests at 100
    step(s, "2026-01-02", bar(105.0, high=106.0, low=101.0), 0.0, cfg)  # missed; target is now 0
    assert s["qty"] == 0 and s["trades"] == [] and s["pending"] is None  # nothing chased


def test_switching_to_taker_cancels_a_resting_order():
    s = new_state(TrendConfig())
    step(s, "2026-01-01", bar(100.0), 1.0, TrendConfig(execution="maker_first"))
    assert s["pending"]
    step(s, "2026-01-02", bar(100.0, low=90.0), 1.0, TrendConfig(execution="taker"))
    assert s["pending"] is None and len(s["trades"]) == 1 and s["trades"][0]["type"] == "taker"


def gate_prices():
    return prices(n=3900, seed=5).set_axis(pd.date_range("2016-03-12", periods=3900, freq="D"))


def test_gate_checks_and_input_validation():
    c, cfg = gate_prices(), TrendConfig()
    same = gate(c, cfg, replace(cfg))
    assert not same["passed"] and not same["checks"]["better_sharpe_in_2_of_3_periods"]
    assert same["checks"]["drawdown_not_worse_by_2pts"]
    assert not same["checks"]["better_sharpe_from_most_start_dates"]
    assert "hard_fill_sharpe_not_worse" not in same["checks"]  # both trade at once: no fill risk to test
    resting = gate(c, cfg, replace(cfg, execution="maker_first"))
    assert "hard_fill_sharpe_not_worse" in resting["checks"]
    assert all(type(v) is bool for v in same["checks"].values())
    timid = gate(c, cfg, replace(cfg, target_vol=0.01))  # barely invested: fails the return check
    assert not timid["checks"]["return_at_least_target_or_baseline"]
    with pytest.raises(ValueError):
        gate(c, cfg, replace(cfg, product="ETH-USD"))
    with pytest.raises(ValueError):
        gate(c.loc["2020":], cfg, replace(cfg))
    with pytest.raises(ValueError):  # needs 400 days of history, so cannot start on time
        gate(c, cfg, replace(cfg, ma_windows=[50, 100, 200, 400]))
    assert gate(c, cfg, replace(cfg, target_vol=0.2))["changed_settings"] == {"target_vol": (0.25, 0.2)}


def test_missing_config_file_is_an_error():
    from tradebot.trend import load_trend_config
    with pytest.raises(FileNotFoundError):
        load_trend_config("/nonexistent/trend.yaml")


def test_walk_forward_uses_only_past_years():
    idx = pd.date_range("2018-01-01", "2022-12-31", freq="D")
    r = pd.DataFrame({"a": 0.001, "b": -0.001}, index=idx)
    r.loc["2022", ["a", "b"]] = [-0.001, 0.001]  # "b" only becomes best in the test year
    oos, picks = walk_forward(r)
    assert list(picks["rules"]) == ["a", "a"]
    assert picks["return_pct"].iloc[-1] < 0


def test_fee_projection_reaches_lower_tiers():
    p = fee_projection(TrendConfig(), turnover=5.0, years=20)
    assert p["taker_fee_pct"].iloc[0] == pytest.approx(0.9) and p["taker_fee_pct"].iloc[-1] < 0.9
    half = fee_projection(TrendConfig(), turnover=5.0, maker_share=1.0, years=20)
    assert half["fee_drag_pct_per_year"].iloc[0] < p["fee_drag_pct_per_year"].iloc[0]



def test_gate_compares_start_by_start():
    """The baseline's own median (1.2) is below the candidate's (1.9), but the candidate loses the
    period from 6 of 13 starts by more than it wins from the other 7, so paired it is not a win."""
    from tradebot.quant import compare_runs
    run = lambda p, s: {"period_sharpes": [p, 1.0, 1.0], "sharpe": s, "annual_return_pct": 25.0,
                        "max_drawdown_pct": -20.0}
    base = [run(1.2, 1.0)] * 7 + [run(3.0, 1.0)] * 6
    cand = [run(1.25, 1.1)] * 7 + [run(1.9, 1.1)] * 6
    b, c, gain, start_wins, checks = compare_runs(base, cand, 0.20, resting=False)
    assert c["period_sharpes"][0] > b["period_sharpes"][0]  # separate medians would call it a win
    assert gain[0] > 0  # paired median: 7 of 13 starts are +0.05, so the median is still positive
    cand2 = [run(1.25, 1.1)] * 6 + [run(1.9, 1.1)] * 7
    base2 = [run(1.2, 1.0)] * 6 + [run(3.0, 1.0)] * 7
    _, c2, gain2, _, checks2 = compare_runs(base2, cand2, 0.20, resting=False)
    assert gain2[0] < 0 and not checks2["better_sharpe_in_2_of_3_periods"]
    assert start_wins == 13 and checks["better_sharpe_from_most_start_dates"]
    assert "hard_fill_sharpe_not_worse" not in checks
