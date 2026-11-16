"""Track 2: multi-day trend following with volatility-based sizing.

Daily bars, long-only spot, no margin. The goal is measured monthly against an
annual target, not daily. Exposure is the share of moving averages the price is
above, scaled down when the market is volatile. Fees follow Coinbase's volume
tiers, so they fall as the account and its trading volume grow.

Usage:
  python -m tradebot.trend backtest [--cash 5000]
  python -m tradebot.trend paper --dir paper/trend
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .config import ALLOWED_PRODUCTS, ROOT
from .data import cache_path, fetch_candles

DAY = 86400

# Coinbase Advanced US spot tiers from 16 Sep 2026: (30-day volume, maker, taker).
FEE_TIERS = [
    (0, 0.0050, 0.0090),
    (10_000, 0.0035, 0.0075),
    (50_000, 0.0015, 0.0030),
    (250_000, 0.0010, 0.0015),
    (1_000_000, 0.0006, 0.0010),
    (5_000_000, 0.0004, 0.0006),
]


@dataclass
class TrendConfig:
    product: str = "BTC-USD"
    starting_cash: float = 5000.0
    annual_target: float = 0.20
    ma_windows: list[int] = field(default_factory=lambda: [20, 50, 100, 200])
    vol_window: int = 30
    target_vol: float = 0.25  # annualised volatility the position is sized to
    rebalance_band: float = 0.10  # trade only when exposure is off by this share of equity
    drawdown_limit: float = 0.15  # go flat and pause when equity falls this far from its peak
    pause_days: int = 30
    # "maker_first": rest a limit order at the closing price for one day (maker fee). If it does
    # not fill it is cancelled, and the next close's own decision trades at once (taker fee).
    # "taker": always trade at once at the taker fee.
    execution: str = "taker"
    fill_buffer: float = 0.001  # a resting order only counts as filled if price trades this far through it
    slippage: float = 0.0005
    fee_tiers: list = field(default_factory=lambda: [list(t) for t in FEE_TIERS])

    def __post_init__(self) -> None:
        if self.product not in ALLOWED_PRODUCTS:
            raise ValueError(f"{self.product} is not on the allowlist")
        if self.execution not in ("maker_first", "taker"):
            raise ValueError("execution must be 'maker_first' or 'taker'")


def load_trend_config(path: str | Path | None = None) -> TrendConfig:
    if path and not Path(path).exists():
        raise FileNotFoundError(f"No trend config at {path}")
    path = Path(path) if path else ROOT / "trend.yaml"
    return TrendConfig(**(yaml.safe_load(path.read_text()) or {})) if path.exists() else TrendConfig()


def fee_rate(volume_30d: float, cfg: TrendConfig, maker: bool = False) -> float:
    col = 1 if maker else 2
    rate = cfg.fee_tiers[0][col]
    for tier in cfg.fee_tiers:
        if volume_30d >= tier[0]:
            rate = tier[col]
    return rate


def target_weights(close: pd.Series, cfg: TrendConfig) -> pd.Series:
    """Desired share of equity in the asset at each daily close (no look-ahead)."""
    above = [(close > close.rolling(w).mean()).astype(float) for w in cfg.ma_windows]
    strength = sum(above) / len(above)
    vol = np.log(close).diff().rolling(cfg.vol_window).std() * np.sqrt(365)
    weight = strength * (cfg.target_vol / vol).clip(upper=1.0)
    weight[close.rolling(max(cfg.ma_windows)).mean().isna() | vol.isna()] = np.nan
    return weight


def new_state(cfg: TrendConfig) -> dict:
    return {"cash": cfg.starting_cash, "qty": 0.0, "peak": cfg.starting_cash, "paused_until": "",
            "last_date": "", "volume": [], "days": [], "trades": [], "breaker_trips": 0, "pending": None}


def _trade(s: dict, date: str, price: float, target: float, maker: bool, cfg: TrendConfig) -> None:
    """Move exposure to `target` at `price`. Maker fills pay the maker fee and no slippage."""
    rate = fee_rate(sum(v[1] for v in s["volume"]), cfg, maker)
    slip = 0.0 if maker else cfg.slippage
    equity = s["cash"] + s["qty"] * price
    gap = target - (s["qty"] * price / equity if equity > 0 else 0.0)
    if gap > 0:
        notional = min(gap * equity, s["cash"] / (1 + rate))  # never more than cash: no margin
        s["qty"] += notional / (price * (1 + slip))
        s["cash"] -= notional * (1 + rate)
        side = "buy"
    else:
        qty = s["qty"] if target == 0 else min(s["qty"], -gap * equity / price)
        notional = qty * price * (1 - slip)
        s["qty"] -= qty
        s["cash"] += notional * (1 - rate)
        side = "sell"
    if notional > 0:
        s["volume"].append([date, round(notional, 2)])
        s["trades"].append({"date": date, "side": side, "price": round(price, 2),
                            "notional": round(notional, 2), "fee": round(notional * rate, 2),
                            "fee_rate": rate, "type": "maker" if maker else "taker"})


def step(s: dict, date: str, bar: dict, target: float, cfg: TrendConfig) -> None:
    """Process one closed daily bar (`close`, `high`, `low`): settle any resting order,
    apply the drawdown breaker, then rebalance if needed."""
    price = bar["close"]
    cutoff = (pd.Timestamp(date) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    s["volume"] = [v for v in s["volume"] if v[0] > cutoff]

    # A resting order was placed at the previous close. It fills only if today's range traded
    # through it. If it did not, it is cancelled and today's decision below is made afresh, on
    # today's target, and executed at once: an old target is never chased at a new price.
    order, s["pending"] = s.get("pending"), None
    missed = False
    if order and cfg.execution == "maker_first":
        if order["side"] == "buy":
            filled = bar["low"] < order["limit"] * (1 - cfg.fill_buffer)
        else:
            filled = bar["high"] > order["limit"] * (1 + cfg.fill_buffer)
        if filled:
            _trade(s, date, order["limit"], order["target"], True, cfg)
        missed = not filled

    equity = s["cash"] + s["qty"] * price
    if s["paused_until"]:
        if date < s["paused_until"]:
            target = 0.0
        else:
            s["paused_until"], s["peak"] = "", equity  # resume with a fresh high-water mark
    s["peak"] = max(s["peak"], equity)
    tripped = not s["paused_until"] and equity <= s["peak"] * (1 - cfg.drawdown_limit)
    if tripped:
        target = 0.0
        s["paused_until"] = (pd.Timestamp(date) + pd.Timedelta(days=cfg.pause_days)).strftime("%Y-%m-%d")
        s["breaker_trips"] += 1

    current = s["qty"] * price / equity if equity > 0 else 0.0
    gap = target - current
    if abs(gap) >= cfg.rebalance_band or (target == 0 and current > 0):
        if tripped or s["paused_until"] or missed or cfg.execution == "taker":
            _trade(s, date, price, target, False, cfg)  # safety exits never wait for a fill
        else:
            s["pending"] = {"side": "buy" if gap > 0 else "sell", "limit": price, "target": target}
    equity = s["cash"] + s["qty"] * price
    s["days"].append({"date": date, "equity": round(equity, 2),
                      "weight": round(s["qty"] * price / equity, 3) if equity > 0 else 0.0})
    s["last_date"] = date


def run(candles: pd.DataFrame, cfg: TrendConfig, state: dict | None = None, start=None, end=None,
        weights: pd.Series | None = None) -> dict:
    """Feed daily candles (columns close, high, low) through the strategy. `weights` replaces
    the built-in trend signal with another target-exposure series (research use)."""
    s = state or new_state(cfg)
    w = (target_weights(candles["close"], cfg) if weights is None else weights).loc[start:end].dropna()
    bars = candles.loc[w.index, ["close", "high", "low"]].to_dict("index")
    for ts, target in w.items():
        date = ts.strftime("%Y-%m-%d")
        if date > s["last_date"]:
            step(s, date, bars[ts], float(target), cfg)
    return s


def equity_curve(s: dict) -> pd.Series:
    d = pd.DataFrame(s["days"])
    return pd.Series(d["equity"].to_numpy(), index=pd.to_datetime(d["date"]))


def metrics(s: dict, cfg: TrendConfig) -> dict:
    eq = equity_curve(s)
    r = eq.pct_change().dropna()
    years = len(eq) / 365
    monthly = eq.resample("ME").last().pct_change().dropna()
    trades = pd.DataFrame(s["trades"])
    volume = trades["notional"].sum() if len(trades) else 0.0
    return {
        "years": round(years, 1),
        "annual_return_pct": round(((eq.iloc[-1] / cfg.starting_cash) ** (1 / years) - 1) * 100, 1),
        "max_drawdown_pct": round((eq / eq.cummax() - 1).min() * 100, 1),
        "sharpe": round(r.mean() / r.std() * np.sqrt(365), 2) if r.std() > 0 else 0.0,
        "months_up_pct": round((monthly > 0).mean() * 100, 0),
        "worst_month_pct": round(monthly.min() * 100, 1),
        "time_in_market_pct": round(np.mean([d["weight"] > 0.05 for d in s["days"]]) * 100, 0),
        "trades_per_year": round(len(trades) / years, 1),
        "turnover_per_year": round(volume / eq.mean() / years, 1),  # traded value / average equity
        "fees_pct_per_year": round(trades["fee"].sum() / eq.mean() / years * 100, 2) if len(trades) else 0.0,
        "breaker_trips": s["breaker_trips"],
        "end_equity": round(float(eq.iloc[-1]), 0),
    }


def load_daily(product: str) -> pd.DataFrame:
    df = pd.read_csv(cache_path(product, DAY), index_col="time", parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    return df


# ---- paper trading -------------------------------------------------------
def render(s: dict, cfg: TrendConfig) -> str:
    eq = equity_curve(s)
    now = eq.iloc[-1]
    since = (now / cfg.starting_cash - 1) * 100
    days = len(eq)
    pace = ((1 + cfg.annual_target) ** (days / 365) - 1) * 100
    month = eq[eq.index >= eq.index[-1].replace(day=1)]
    mtd = (now / (eq[eq.index < month.index[0]].iloc[-1] if len(eq) > len(month) else cfg.starting_cash) - 1) * 100
    status = f"paused until {s['paused_until']} (drawdown breaker)" if s["paused_until"] else \
        f"{s['days'][-1]['weight'] * 100:.0f}% in {cfg.product}"
    if s.get("pending"):
        o = s["pending"]
        status += f"; resting limit {o['side']} at {o['limit']:,.2f} toward {o['target'] * 100:.0f}%"
    fees = sum(t["fee"] for t in s["trades"])
    out = [
        "# Trend track (paper)", "",
        f"As of the {s['last_date']} daily close. Simulated money only.", "",
        f"- **Equity:** ${now:,.2f} ({since:+.2f}% since start; the {cfg.annual_target * 100:.0f}%/yr pace would be {pace:+.2f}%)",
        f"- **This month:** {mtd:+.2f}% (pace needs about {((1 + cfg.annual_target) ** (1 / 12) - 1) * 100:.2f}% a month)",
        f"- **Position:** {status}",
        f"- **Drawdown from peak:** {(now / max(s['peak'], now) - 1) * 100:.2f}% (breaker at -{cfg.drawdown_limit * 100:.0f}%)",
        f"- **Trades:** {len(s['trades'])}, fees paid ${fees:,.2f}", "",
    ]
    if s["trades"]:
        out += ["| Date | Side | Price | Value $ | Fee $ | Fee rate |", "|---|---|---|---|---|---|"]
        out += [f"| {t['date']} | {t['side']} | {t['price']:,.2f} | {t['notional']:,.2f} | {t['fee']:.2f} | {t['fee_rate'] * 100:.2f}% {t.get('type', '')} |"
                for t in reversed(s["trades"])]
    return "\n".join(out) + "\n"


def paper_tick(directory: Path, cfg: TrendConfig, now: int | None = None) -> dict:
    now = now or int(time.time())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "state.json"
    end = now // DAY * DAY  # only fully closed daily candles
    warmup = (max(cfg.ma_windows) + cfg.vol_window + 10) * DAY
    close = fetch_candles(cfg.product, end - warmup, end, DAY)
    close.index = close.index.tz_localize(None)
    if path.exists():
        s = json.loads(path.read_text())
    else:  # first run: start flat at the latest close, trade from the next one
        s = new_state(cfg)
        s["last_date"] = close.index[-1].strftime("%Y-%m-%d")
        s["days"].append({"date": s["last_date"], "equity": cfg.starting_cash, "weight": 0.0})
    before, trips = s["last_date"], s["breaker_trips"]
    run(close, cfg, s)
    path.write_text(json.dumps(s, indent=1))
    (directory / "report.md").write_text(render(s, cfg))
    # Written when the breaker trips. The workflow deletes it only after posting it, so a failed
    # post is retried on the next run instead of being lost.
    if s["breaker_trips"] > trips:
        until = f"is paused until {s['paused_until']}" if s["paused_until"] else f"paused for {cfg.pause_days} days"
        (directory / "alert.md").write_text(
            f"### ALERT: trend track drawdown breaker tripped\n\nEquity fell {cfg.drawdown_limit * 100:.0f}% or more "
            f"below its peak (now ${s['days'][-1]['equity']:,.2f}). The track sold everything and {until}. "
            f"Breaker trips so far: {s['breaker_trips']}.\n")
    return {"equity": s["days"][-1]["equity"], "new_day": s["last_date"] != before}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["backtest", "paper"])
    ap.add_argument("--dir", default="paper/trend")
    ap.add_argument("--cash", type=float)
    ap.add_argument("--start")
    a = ap.parse_args()
    cfg = load_trend_config()
    if a.cash:
        cfg.starting_cash = a.cash
    if a.command == "paper":
        print(paper_tick(Path(a.dir), cfg))
    else:
        for k, v in metrics(run(load_daily(cfg.product), cfg, start=a.start), cfg).items():
            print(f"{k:22} {v}")
