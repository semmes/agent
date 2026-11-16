import numpy as np
import pandas as pd

from tradebot.config import Strategy
import time

from tradebot.news import parse_feed, score_keywords
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


def test_general_headlines_need_a_market_term_to_count():
    war = [{"source": "CNBC Top News", "title": "Trade war fears hit retailers", "ts": 0, "general": True}]
    crypto = [{"source": "Reuters", "title": "Bitcoin exchange halts withdrawals after hack", "ts": 0, "general": True}]
    assert score_keywords(war).risk == "low"
    assert score_keywords(crypto).risk == "high"


def test_parse_feed_keeps_recent_dated_items_and_google_publisher():
    now = time.time()
    fmt = lambda t: time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(t))
    body = f"""<rss version="2.0"><channel>
      <item><title>Fresh story - Reuters</title><pubDate>{fmt(now - 600)}</pubDate>
        <source url="https://www.reuters.com">Reuters</source></item>
      <item><title>Old story</title><pubDate>{fmt(now - 86400)}</pubDate></item>
      <item><title>Undated story</title></item>
    </channel></rss>""".encode()
    items = parse_feed("Google News", body, True, now - 6 * 3600)
    assert [i["title"] for i in items] == ["Fresh story - Reuters"]
    assert items[0]["source"] == "Reuters" and items[0]["general"] and abs(items[0]["ts"] - (now - 600)) < 2
