import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")

from tradebot.ml import HORIZON, features, walk_forward_probabilities  # noqa: E402
from sklearn.dummy import DummyClassifier  # noqa: E402


def candles(n=1500, seed=2):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-07-20", periods=n, freq="D")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n))), index=idx)
    return pd.DataFrame({"close": close, "high": close * 1.02, "low": close * 0.98, "volume": 1.0 + rng.random(n)})


def test_features_do_not_look_ahead():
    c = candles()
    pd.testing.assert_frame_equal(features(c).iloc[:1000], features(c.iloc[:1000]))


def test_predictions_never_use_the_test_year_or_unknown_outcomes():
    c = candles()
    seen = []

    class Spy(DummyClassifier):
        def fit(self, x, y):
            seen.append(x.index.max())
            return super().fit(x, y)

    p = walk_forward_probabilities(c, lambda: Spy(strategy="prior"))
    years = sorted(set(p.index.year))
    for year, last_train_day in zip(years, seen):
        assert last_train_day < pd.Timestamp(f"{year}-01-01") - pd.Timedelta(days=HORIZON)
