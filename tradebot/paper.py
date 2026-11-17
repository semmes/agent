"""Paper trader: live Coinbase prices, simulated fills, real fee model.

Each run ("tick") downloads the candles that closed since the last run and feeds
them through the same engine the backtest uses. Stops and targets are evaluated
on every 15-minute bar, so it does not matter if a tick runs late.

Usage: python -m tradebot.paper [--dir paper] [--reset-shutdown]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from . import news
from .backtest import prepare, run
from .config import Config, load_config
from .data import fetch_candles
from .engine import Engine, State
from .report import render_daily, render_report

WARMUP_DAYS = 45  # history needed before the slowest indicator is valid
KEEP_DAYS = 60
RUN_DAYS = 30


def update_candles(directory: Path, product: str, cfg: Config, now: int) -> pd.DataFrame:
    path = directory / f"candles_{product}.csv"
    end = now // cfg.granularity * cfg.granularity  # only fully closed bars
    if path.exists():
        old = pd.read_csv(path, index_col="time", parse_dates=True)
        old.index = pd.to_datetime(old.index, utc=True)
        start = int(old.index[-1].timestamp()) + cfg.granularity
    else:
        old, start = None, end - WARMUP_DAYS * 86400
    new = fetch_candles(product, start, end, cfg.granularity) if start < end else None
    df = pd.concat([d for d in (old, new) if d is not None and len(d)])
    df = df[~df.index.duplicated()].sort_index()
    df = df[df.index >= df.index[-1] - pd.Timedelta(days=KEEP_DAYS)]
    df.to_csv(path)
    return df


def apply_news_block(meta: dict, verdict: dict, now: int, hours: float) -> bool:
    """Start a block when none is active. During a block, extend it only on fresh evidence: a
    price reaction since the previous run. Re-reading the same story does not extend it, but a
    new shock arriving mid-block still gets its full `hours`. Records what it did in
    verdict["action"]. Returns whether new entries are allowed."""
    action = None
    if verdict["block"]:
        if now >= meta.get("news_block_until", 0):
            action = "started"
        elif (verdict.get("market") or {}).get("confirmed"):
            action = "extended"
        if action:
            meta["news_block_until"] = max(meta.get("news_block_until", 0), now + int(hours * 3600))
    verdict["action"] = action
    return now >= meta.get("news_block_until", 0)


def tick(directory: Path, cfg: Config, now: int | None = None, check_news: bool = True) -> dict:
    now = now or int(time.time())
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"started": now, "news": []}
    state = State(**json.loads(state_path.read_text())) if state_path.exists() else State(cash=cfg.starting_cash)
    eng = Engine(cfg, state)

    candles = {p: update_candles(directory, p, cfg, now) for p in cfg.products}
    signals = prepare(candles, cfg)
    start = pd.Timestamp(state.last_ts) if state.last_ts else pd.Timestamp(meta["started"], unit="s", tz="UTC")
    stop = pd.Timestamp(meta["started"] + RUN_DAYS * 86400, unit="s", tz="UTC")
    fresh = {p: df[(df.index > start) & (df.index < stop)] for p, df in signals.items()}

    allow_entries = True
    if check_news and cfg.news_enabled:
        # Read every run, even during a block, so the research log has no gaps. The price check
        # covers every hour since the previous run (runs can be hours apart), but at least the
        # last 2 hours (headlines lag price) and at most the last 12 (after the 30-day run ends
        # the replay stops, so "since the previous run" would otherwise keep growing).
        latest = max(d.index[-1] for d in candles.values())
        since = pd.Timestamp(state.last_ts) if state.last_ts else latest - pd.Timedelta(hours=2)
        since = min(max(since, latest - pd.Timedelta(hours=12)), latest - pd.Timedelta(hours=2))
        reaction = news.market_reaction(candles, cfg.reaction_drop, cfg.reaction_range_ratio, since)
        verdict = news.assess(cfg.news_model, reaction=reaction)
        verdict["ts"] = now
        allow_entries = apply_news_block(meta, verdict, now, cfg.news_block_hours)
        with open(directory / "news_log.jsonl", "a") as log:  # full history with headlines, for research
            log.write(json.dumps(verdict) + "\n")
        verdict.pop("titles", None)
        verdict.pop("by_source", None)
        meta["news"] = (meta["news"] + [verdict])[-200:]

    days_before, was_shutdown = len(state.days), bool(state.shutdown)
    run(fresh, cfg, eng, allow_entries=allow_entries)
    meta["finished"] = now >= meta["started"] + RUN_DAYS * 86400
    meta["updated"] = now

    state_path.write_text(json.dumps(eng.to_dict(), indent=1))
    meta_path.write_text(json.dumps(meta, indent=1))
    (directory / "report.md").write_text(render_report(eng, cfg, meta))
    daily = directory / "daily.md"  # present only when a trading day has just closed
    if len(state.days) > days_before:
        daily.write_text(render_daily(eng, cfg, meta))
    elif daily.exists():
        daily.unlink()
    # Written when the shutdown trips. The workflow deletes it only after posting it, so a failed
    # post is retried on the next run instead of being lost.
    if state.shutdown and not was_shutdown:
        (directory / "alert.md").write_text(
            f"### ALERT: intraday track stopped\n\n{state.shutdown}. Equity ${eng.equity():,.2f}. "
            "It stays stopped until reset by hand (`python -m tradebot.paper --reset-shutdown`).\n")
    return {"equity": round(eng.equity(), 2), "bars": sum(len(d) for d in fresh.values()), "allow_entries": allow_entries}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run one paper-trading tick")
    ap.add_argument("--dir", default="paper")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--reset-shutdown", action="store_true", help="clear a weekly-loss shutdown after review")
    a = ap.parse_args()
    d = Path(a.dir)
    if a.reset_shutdown and (d / "state.json").exists():
        st = json.loads((d / "state.json").read_text())
        st["shutdown"] = ""
        (d / "state.json").write_text(json.dumps(st, indent=1))
    print(tick(d, load_config(), check_news=not a.no_news))
