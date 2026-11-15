"""Parameter search with an honest split: tune on the early window, judge on the later one.

Usage: python -m tradebot.research [--split 2025-11-15] [--maker-fee X --taker-fee Y]
"""
from __future__ import annotations

import argparse
import itertools
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pandas as pd

from .backtest import backtest
from .config import Config, Strategy, load_config
from .data import load_cached

GRID = {
    "mode": ["breakout", "pullback"],
    "breakout_lookback": [96, 288, 672],
    "ema": [(96, 384, 800), (384, 1344, 2000)],  # fast, slow, trend
    "stop_atr": [3.0, 6.0, 10.0],
    "trail_atr": [0.0, 6.0, 10.0],
    "entry_order": ["limit"],
}

_candles: dict[str, pd.DataFrame] = {}


def _init(products: list[str], granularity: int) -> None:
    for p in products:
        _candles[p] = load_cached(p, granularity)


def _evaluate(args: tuple[Config, dict, str]) -> dict:
    base, combo, split = args
    fast, slow, trend = combo["ema"]
    strat = replace(
        base.strategy,
        mode=combo["mode"],
        breakout_lookback=combo["breakout_lookback"],
        fast_ema=fast,
        slow_ema=slow,
        trend_ema=trend,
        stop_atr=combo["stop_atr"],
        trail_atr=combo["trail_atr"],
    )
    cfg = replace(base, strategy=strat, entry_order=combo["entry_order"])
    tune = backtest(_candles, cfg, end=split)
    test = backtest(_candles, cfg, start=split)
    row = {k: (v if k != "ema" else "/".join(map(str, v))) for k, v in combo.items()}
    for name, m in (("tune", tune), ("test", test)):
        row[f"{name}_ret"] = m["total_return_pct"]
        row[f"{name}_dd"] = m["max_drawdown_pct"]
        row[f"{name}_trades"] = m["trades"]
        row[f"{name}_target_days"] = m["days_hit_target_pct"]
    return row


def search(base: Config, split: str) -> pd.DataFrame:
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    with ProcessPoolExecutor(initializer=_init, initargs=(base.products, base.granularity)) as ex:
        rows = list(ex.map(_evaluate, [(base, c, split) for c in combos], chunksize=4))
    return pd.DataFrame(rows).sort_values("tune_ret", ascending=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="2025-11-15")
    ap.add_argument("--maker-fee", type=float)
    ap.add_argument("--taker-fee", type=float)
    ap.add_argument("--out", default="research_results.csv")
    a = ap.parse_args()
    cfg = load_config()
    if a.maker_fee is not None:
        cfg = replace(cfg, maker_fee=a.maker_fee)
    if a.taker_fee is not None:
        cfg = replace(cfg, taker_fee=a.taker_fee)
    res = search(cfg, a.split)
    res.to_csv(a.out, index=False)
    pd.set_option("display.width", 250)
    print(res.head(15).to_string(index=False))
    print(f"\n{len(res)} combinations; profitable while tuning: {(res.tune_ret > 0).sum()}, "
          f"profitable on unseen data: {(res.test_ret > 0).sum()}, both: "
          f"{((res.tune_ret > 0) & (res.test_ret > 0)).sum()}")
