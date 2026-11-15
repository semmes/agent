import pytest

from tradebot.config import Config
from tradebot.engine import Bar, Engine


def cfg(**kw):
    base = dict(products=["BTC-USD"], entry_order="market", slippage=0.0, maker_fee=0.004, taker_fee=0.006)
    base.update(kw)
    return Config(**base)


def bar(o, h, l, c, entry=False, exit=False, atr=1.0):
    return Bar(o, h, l, c, entry, exit, atr)


def step(eng, n, b, day="2026-01-05", week="2026-W02"):
    eng.step(f"t{n}", day, week, {"BTC-USD": b})


def test_allowlist_rejects_other_products():
    with pytest.raises(ValueError):
        Config(products=["DOGE-USD"])
    eng = Engine(cfg())
    with pytest.raises(ValueError):
        eng._buy("t", "SHIB-USD", 1.0, 100.0, 0.0, 0.5)


def test_no_margin():
    eng = Engine(cfg())
    with pytest.raises(ValueError):
        eng._buy("t", "BTC-USD", 100.0, 6000.0, 0.0, 90.0)


def test_stop_loss_stays_within_daily_cap_and_halts():
    eng = Engine(cfg())
    step(eng, 0, bar(100, 100, 100, 100, entry=True))  # buy at 100, stop 3 ATR below at 97
    assert eng.s.qty > 0 and eng.s.stop == pytest.approx(97)
    step(eng, 1, bar(100, 100, 96, 96))
    assert eng.s.qty == 0
    assert eng.s.trades[-1]["reason"] == "stop"
    assert eng.day_pnl() == pytest.approx(-0.005, abs=2e-4)
    assert eng.s.day_halt == "loss_cap"
    step(eng, 2, bar(96, 96, 96, 96, entry=True))  # no re-entry after the halt
    assert eng.s.qty == 0 and len(eng.s.trades) == 1


def test_daily_target_closes_position_and_stops_trading():
    eng = Engine(cfg())
    step(eng, 0, bar(100, 100, 100, 100, entry=True))
    step(eng, 1, bar(100, 200, 100, 200))
    assert eng.s.trades[-1]["reason"] == "daily_target"
    assert eng.day_pnl() == pytest.approx(0.01, abs=1e-6)
    assert eng.s.day_halt == "target"
    step(eng, 2, bar(200, 200, 200, 200, entry=True))
    assert eng.s.qty == 0
    # A new day resets the halt.
    step(eng, 3, bar(200, 200, 200, 200, entry=True), day="2026-01-06")
    assert eng.s.qty > 0 and eng.s.days[0]["halt"] == "target"


def test_stop_wins_when_stop_and_target_hit_in_same_bar():
    eng = Engine(cfg())
    step(eng, 0, bar(100, 100, 100, 100, entry=True))
    step(eng, 1, bar(100, 200, 90, 150))
    assert eng.s.trades[-1]["reason"] == "stop"


def test_gap_through_stop_fills_at_open():
    eng = Engine(cfg())
    step(eng, 0, bar(100, 100, 100, 100, entry=True))
    step(eng, 1, bar(90, 91, 89, 90))
    assert eng.s.trades[-1]["exit_price"] == 90


def test_limit_entry_fills_only_if_price_trades_through():
    eng = Engine(cfg(entry_order="limit"))
    step(eng, 0, bar(100, 100, 100, 100, entry=True))
    assert eng.s.pending and eng.s.qty == 0
    step(eng, 1, bar(101, 102, 100.5, 101))  # never trades below 100: cancelled
    assert eng.s.pending is None and eng.s.qty == 0
    step(eng, 2, bar(101, 101, 101, 101, entry=True))
    step(eng, 3, bar(101, 101, 100.5, 100.8))
    assert eng.s.qty > 0 and eng.s.entry_price == 101


def test_weekly_shutdown_blocks_trading_until_reset():
    eng = Engine(cfg())
    step(eng, 0, bar(100, 100, 100, 100))
    eng.s.cash *= 0.94  # simulate a 6% loss on the week
    step(eng, 1, bar(100, 100, 100, 100, entry=True))
    assert eng.s.shutdown
    step(eng, 2, bar(100, 100, 100, 100, entry=True), day="2026-01-12", week="2026-W03")
    assert eng.s.qty == 0 and eng.s.shutdown  # still stopped the following week
