"""Configuration. Risk limits live here; the product allowlist is hard-coded."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

# Hard allowlist: the agent can never trade anything else, whatever the config says.
ALLOWED_PRODUCTS = frozenset({"BTC-USD", "ETH-USD"})

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Strategy:
    mode: str = "breakout"  # "breakout" | "pullback"
    trend_ema: int = 800  # bars; price must be above this EMA (800 x 15m ~ 8 days)
    fast_ema: int = 96
    slow_ema: int = 384
    breakout_lookback: int = 96  # bars; close above the prior N-bar high
    rsi_len: int = 56
    rsi_dip: float = 35.0
    atr_len: int = 56
    stop_atr: float = 3.0  # initial stop distance in ATRs
    trail_atr: float = 0.0  # trailing stop distance in ATRs; 0 disables
    exit_on_trend_break: bool = True


@dataclass
class Config:
    products: list[str] = field(default_factory=lambda: ["BTC-USD", "ETH-USD"])
    starting_cash: float = 5000.0
    timezone: str = "America/New_York"
    granularity: int = 900  # seconds per bar

    daily_target: float = 0.01  # stop trading for the day at +1%
    daily_loss_cap: float = 0.01  # stop trading for the day at -1%
    weekly_loss_shutdown: float = 0.05  # shut the agent down at -5% on the week
    risk_per_trade: float = 0.005  # share of day-start equity risked per trade
    max_trades_per_day: int = 2
    min_stop_frac: float = 0.003

    maker_fee: float = 0.005
    taker_fee: float = 0.009
    slippage: float = 0.0005  # applied to market orders and stop exits
    entry_order: str = "limit"  # "limit" (maker fee, may not fill) | "market"

    news_enabled: bool = True
    news_block_hours: int = 6
    news_model: str = "claude-opus-5-5"

    strategy: Strategy = field(default_factory=Strategy)

    def __post_init__(self) -> None:
        bad = set(self.products) - ALLOWED_PRODUCTS
        if bad:
            raise ValueError(f"Products not on the allowlist: {sorted(bad)}")
        if self.entry_order not in ("limit", "market"):
            raise ValueError("entry_order must be 'limit' or 'market'")


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = dict(raw or {})
    strat = Strategy(**raw.pop("strategy", {}))
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")
    return Config(strategy=strat, **raw)
