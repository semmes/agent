"""Backtest the engine over cached candle history."""
from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

from .config import Config, load_config
from .data import load_cached
from .engine import Bar, Engine
from .strategy import compute_signals

COLS = ["open", "high", "low", "close", "entry", "exit", "atr"]


def prepare(candles: dict[str, pd.DataFrame], cfg: Config) -> dict[str, pd.DataFrame]:
    return {p: compute_signals(df, cfg.strategy)[COLS] for p, df in candles.items()}


def calendar_keys(index: pd.DatetimeIndex, tz: str) -> tuple[np.ndarray, np.ndarray]:
    local = index.tz_convert(tz)
    iso = local.isocalendar()
    week = (iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)).to_numpy()
    return local.strftime("%Y-%m-%d").to_numpy(), week


def run(
    signals: dict[str, pd.DataFrame], cfg: Config, engine: Engine | None = None, allow_entries: bool = True
) -> Engine:
    """Feed every bar in `signals` through the engine, in time order."""
    eng = engine or Engine(cfg, resume_after_shutdown=True)
    index = pd.DatetimeIndex(sorted(set().union(*[df.index for df in signals.values()])))
    if index.empty:
        return eng
    days, weeks = calendar_keys(index, cfg.timezone)
    stamps = index.strftime("%Y-%m-%dT%H:%M:%SZ").to_numpy()
    rows = {p: df.reindex(index).to_numpy(dtype=object) for p, df in signals.items()}
    for i in range(len(index)):
        bars = {}
        for p, arr in rows.items():
            r = arr[i]
            if r[3] == r[3]:  # skip gaps (NaN close)
                bars[p] = Bar(r[0], r[1], r[2], r[3], bool(r[4]), bool(r[5]), r[6] if r[6] == r[6] else 0.0)
        if bars:
            eng.step(stamps[i], days[i], weeks[i], bars, allow_entries)
    return eng


def metrics(eng: Engine, cfg: Config) -> dict:
    days = pd.DataFrame(eng.s.days)
    trades = pd.DataFrame(eng.s.trades)
    if days.empty:
        return {}
    eq = days["end"]
    years = len(days) / 365
    total = eq.iloc[-1] / cfg.starting_cash - 1
    dd = (eq / eq.cummax() - 1).min()
    traded = days[days["trades"] > 0]
    wins = trades[trades["pnl"] > 0] if len(trades) else trades
    return {
        "days": len(days),
        "total_return_pct": round(total * 100, 2),
        "annual_return_pct": round(((1 + total) ** (1 / years) - 1) * 100, 2) if years > 0 else 0.0,
        "max_drawdown_pct": round(dd * 100, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if len(trades) else 0.0,
        "fees_paid": round(trades["fees"].sum(), 2) if len(trades) else 0.0,
        "days_traded_pct": round(len(traded) / len(days) * 100, 1),
        "days_hit_target_pct": round((days["halt"] == "target").mean() * 100, 1),
        "days_hit_loss_cap_pct": round((days["halt"] == "loss_cap").mean() * 100, 1),
        "days_up_pct": round((days["pnl_pct"] > 0).mean() * 100, 1),
        "days_down_pct": round((days["pnl_pct"] < 0).mean() * 100, 1),
        "worst_day_pct": round(days["pnl_pct"].min(), 2),
        "best_day_pct": round(days["pnl_pct"].max(), 2),
    }


def backtest(candles: dict[str, pd.DataFrame], cfg: Config, start=None, end=None) -> dict:
    """Signals are computed on full history, then the window is cut, so indicators are warmed up."""
    sig = prepare(candles, cfg)
    sig = {p: df.loc[start:end] for p, df in sig.items()}
    return metrics(run(sig, cfg), cfg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backtest the configured strategy")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--maker-fee", type=float)
    ap.add_argument("--taker-fee", type=float)
    a = ap.parse_args()
    cfg = load_config()
    if a.maker_fee is not None:
        cfg = replace(cfg, maker_fee=a.maker_fee)
    if a.taker_fee is not None:
        cfg = replace(cfg, taker_fee=a.taker_fee)
    candles = {p: load_cached(p, cfg.granularity) for p in cfg.products}
    for k, v in backtest(candles, cfg, a.start, a.end).items():
        print(f"{k:24} {v}")
