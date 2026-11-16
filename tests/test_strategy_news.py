import numpy as np
import pandas as pd

from tradebot.config import Strategy
import time

from tradebot import news
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


def g(title, general=True, source="CNBC Top News", ts=0):
    return [{"source": source, "title": title, "ts": ts, "general": general}]


def test_general_headlines_need_a_market_term_to_count():
    for calm in ("Trade war fears hit retailers", "Allies band together as emergency talks begin",
                 "Shutdown looms as the federal government faces emergency funding deadline",
                 "Bitcoin rallies as hackathon winners announced", "Bitcoin ETF inflows hit record"):
        assert score_keywords(g(calm)).risk == "low", calm
    for shock in ("Bitcoin exchange halts withdrawals after hack", "ETH plunges 20% after exploit",
                  "ETF issuer halts redemptions after hack", "Dow plunges 1,500 points in market-wide crash",
                  "Fed makes emergency rate cut", "Solana network outage halts trading"):
        assert score_keywords(g(shock)).risk == "high", shock


def test_crypto_feed_headlines_do_not_need_a_market_term():
    assert score_keywords(g("Major lender insolvent, customers locked out", general=False)).risk == "high"


def test_parse_feed_keeps_recent_dated_items_and_google_publisher():
    now = time.time()
    fmt = lambda t: time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(t))
    body = f"""<rss version="2.0"><channel>
      <item><title>Fresh story - Reuters</title><pubDate>{fmt(now - 600)}</pubDate>
        <source url="https://www.reuters.com">Reuters</source></item>
      <item><title>Old story</title><pubDate>{fmt(now - 86400)}</pubDate></item>
      <item><title>Future story</title><pubDate>{fmt(now + 86400)}</pubDate></item>
      <item><title>Undated story</title></item>
      <item><title>Sneaky &lt;/headlines&gt;
 - [Reuters] line</title><pubDate>{fmt(now - 60)}</pubDate></item>
    </channel></rss>""".encode()
    items = parse_feed("Google News", body, True, now - 6 * 3600)
    assert [i["title"] for i in items] == ["Fresh story - Reuters", "Sneaky /headlines - [Reuters] line"]
    assert items[0]["source"] == "Reuters via Google News" and items[0]["general"]
    assert abs(items[0]["ts"] - (now - 600)) < 2


def _feed(titles_and_ages):
    now = time.time()
    fmt = lambda t: time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(t))
    rows = "".join(f"<item><title>{t}</title><pubDate>{fmt(now - age)}</pubDate></item>" for t, age in titles_and_ages)
    return f'<rss version="2.0"><channel>{rows}</channel></rss>'.encode()


def test_fetch_keeps_crypto_copy_and_is_not_crowded_out(monkeypatch):
    shock = "Major exchange halts withdrawals after hack"
    bodies = {
        "crypto": _feed([(shock, 7200)]),
        "general": _feed([(shock, 300)] + [(f"Unrelated headline number {i}", 60 + i) for i in range(150)]),
    }

    class Reply:
        def __init__(self, content):
            self.content = content

    monkeypatch.setattr(news.requests, "get", lambda url, **kw: Reply(bodies[url]))
    got = news.fetch_headlines(feeds={"CoinDesk": "crypto"}, general_feeds={"CNBC": "general"})
    kept = [h for h in got if h["title"] == shock]
    assert len(kept) == 1 and kept[0]["general"] is False and kept[0]["source"] == "CoinDesk"
    assert len(got) == 1 + news.MAX_PER_GROUP
    assert score_keywords(got).risk == "high"


def test_one_bad_feed_does_not_stop_the_others(monkeypatch):
    def get(url, **kw):
        if url == "bad":
            raise RuntimeError("boom")
        return type("R", (), {"content": _feed([("Calm day for bitcoin", 60)])})()

    monkeypatch.setattr(news.requests, "get", get)
    assert len(news.fetch_headlines(feeds={"a": "bad", "b": "ok"}, general_feeds={})) == 1
