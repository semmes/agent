"""Trading engine: position handling and the hard risk rules.

The same engine drives the backtest and the paper trader, one bar at a time,
so what was tested is what runs. It is long-only spot: no margin, no shorting.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import NamedTuple

from .config import ALLOWED_PRODUCTS, Config


class Bar(NamedTuple):
    open: float
    high: float
    low: float
    close: float
    entry: bool
    exit: bool
    atr: float


@dataclass
class State:
    cash: float
    product: str | None = None
    qty: float = 0.0
    entry_price: float = 0.0
    entry_ts: str = ""
    entry_fee: float = 0.0
    stop: float = 0.0
    high_water: float = 0.0
    pending: dict | None = None  # resting limit buy, valid for one bar
    marks: dict = field(default_factory=dict)
    day: str = ""
    day_start_equity: float = 0.0
    day_halt: str = ""  # "", "target", "loss_cap"
    trades_today: int = 0
    week: str = ""
    week_start_equity: float = 0.0
    shutdown: str = ""  # non-empty: agent is stopped until manually reset
    last_ts: str = ""
    trades: list = field(default_factory=list)
    days: list = field(default_factory=list)


class Engine:
    def __init__(self, cfg: Config, state: State | None = None, resume_after_shutdown: bool = False):
        self.cfg = cfg
        self.s = state or State(cash=cfg.starting_cash)
        # Backtests restart the agent the following week so the whole history is covered.
        self.resume_after_shutdown = resume_after_shutdown

    # ---- accounting -----------------------------------------------------
    def equity(self) -> float:
        s = self.s
        return s.cash + (s.qty * s.marks.get(s.product, s.entry_price) if s.qty else 0.0)

    def day_pnl(self) -> float:
        return self.equity() / self.s.day_start_equity - 1 if self.s.day_start_equity else 0.0

    def _buy(self, ts: str, product: str, price: float, notional: float, fee_rate: float, stop: float) -> None:
        if product not in ALLOWED_PRODUCTS:
            raise ValueError(f"{product} is not on the allowlist")
        s = self.s
        fee = notional * fee_rate
        if notional + fee > s.cash + 1e-9:
            raise ValueError("Order exceeds cash: margin is not allowed")
        s.cash -= notional + fee
        s.product, s.qty = product, notional / price
        s.entry_price, s.entry_ts, s.entry_fee = price, ts, fee
        s.stop, s.high_water = stop, price
        s.marks[product] = price
        s.trades_today += 1

    def _sell(self, ts: str, price: float, fee_rate: float, reason: str) -> None:
        s = self.s
        gross = s.qty * price
        fee = gross * fee_rate
        s.cash += gross - fee
        s.trades.append(
            {
                "product": s.product,
                "entry_ts": s.entry_ts,
                "exit_ts": ts,
                "entry_price": round(s.entry_price, 2),
                "exit_price": round(price, 2),
                "qty": s.qty,
                "fees": round(s.entry_fee + fee, 4),
                "pnl": round(gross - fee - s.qty * s.entry_price - s.entry_fee, 4),
                "reason": reason,
            }
        )
        s.marks[s.product] = price
        s.product, s.qty, s.stop = None, 0.0, 0.0

    # ---- calendar -------------------------------------------------------
    def _roll(self, day: str, week: str) -> None:
        s = self.s
        eq = self.equity()
        if s.day:
            s.days.append(
                {
                    "day": s.day,
                    "start": round(s.day_start_equity, 2),
                    "end": round(eq, 2),
                    "pnl_pct": round((eq / s.day_start_equity - 1) * 100, 4),
                    "halt": s.day_halt,
                    "trades": s.trades_today,
                }
            )
        s.day, s.day_start_equity, s.day_halt, s.trades_today = day, eq, "", 0
        if week != s.week:
            s.week, s.week_start_equity = week, eq
            if self.resume_after_shutdown:
                s.shutdown = ""

    # ---- per-bar logic --------------------------------------------------
    def step(self, ts: str, day: str, week: str, bars: dict[str, Bar], allow_entries: bool = True) -> None:
        """Process one closed bar per product. `day`/`week` are in the config timezone."""
        s, c = self.s, self.cfg
        if day != s.day:
            self._roll(day, week)

        if s.pending:
            self._fill_pending(ts, bars)
        elif s.qty and s.product in bars:
            self._manage(ts, bars[s.product])

        for p, b in bars.items():
            s.marks[p] = b.close
        self._enforce_limits(ts)

        can_trade = not (s.qty or s.pending or s.day_halt or s.shutdown)
        if can_trade and allow_entries and s.trades_today < c.max_trades_per_day:
            self._maybe_enter(ts, bars)
        s.last_ts = ts

    def _manage(self, ts: str, b: Bar, check_target: bool = True) -> None:
        s, c = self.s, self.cfg
        # Price at which today's loss would reach the cap, after the exit fee.
        cap_px = (s.day_start_equity * (1 - c.daily_loss_cap) - s.cash) / (s.qty * (1 - c.taker_fee))
        stop = max(s.stop, cap_px)
        if b.low <= stop:  # checked before the target: assume the worst order of events
            reason = "stop" if s.stop >= cap_px else "daily_loss_cap"
            self._sell(ts, min(b.open, stop) * (1 - c.slippage), c.taker_fee, reason)
            return
        if check_target:
            tgt_px = (s.day_start_equity * (1 + c.daily_target) - s.cash) / (s.qty * (1 - c.maker_fee))
            if b.high >= tgt_px:  # resting limit sell
                self._sell(ts, max(b.open, tgt_px), c.maker_fee, "daily_target")
                return
        if b.exit:
            self._sell(ts, b.close * (1 - c.slippage), c.taker_fee, "trend_exit")
            return
        if c.strategy.trail_atr > 0 and b.atr > 0:
            s.high_water = max(s.high_water, b.close)
            s.stop = max(s.stop, s.high_water - c.strategy.trail_atr * b.atr)

    def _fill_pending(self, ts: str, bars: dict[str, Bar]) -> None:
        s, c = self.s, self.cfg
        o, s.pending = s.pending, None
        b = bars.get(o["product"])
        if b is None or b.low >= o["price"]:
            return  # not filled; the order is cancelled
        self._buy(ts, o["product"], o["price"], o["notional"], c.maker_fee, o["stop"])
        self._manage(ts, b, check_target=False)  # the stop may be hit on the fill bar

    def _enforce_limits(self, ts: str) -> None:
        s, c = self.s, self.cfg
        pnl = self.day_pnl()
        if not s.day_halt:
            if pnl >= c.daily_target:
                s.day_halt = "target"
            elif pnl <= -c.daily_loss_cap:
                s.day_halt = "loss_cap"
            elif not s.qty and c.daily_loss_cap + pnl < 0.2 * c.daily_loss_cap:
                s.day_halt = "loss_cap"  # too little risk budget left for another trade
        if s.day_halt and s.qty:
            self._sell(ts, s.marks[s.product] * (1 - c.slippage), c.taker_fee, "day_halt")
        if s.day_halt:
            s.pending = None
        if not s.shutdown and self.equity() <= s.week_start_equity * (1 - c.weekly_loss_shutdown):
            if s.qty:
                self._sell(ts, s.marks[s.product] * (1 - c.slippage), c.taker_fee, "weekly_shutdown")
            s.pending = None
            s.shutdown = f"weekly loss limit hit on {s.day}"

    def _maybe_enter(self, ts: str, bars: dict[str, Bar]) -> None:
        s, c = self.s, self.cfg
        for product in c.products:  # config order is the priority order
            b = bars.get(product)
            if b is None or not b.entry or not b.atr > 0:
                continue
            limit = c.entry_order == "limit"
            price = b.close if limit else b.close * (1 + c.slippage)
            stop = price - c.strategy.stop_atr * b.atr
            stop_frac = (price - stop) / price
            if stop_frac < c.min_stop_frac:
                continue
            entry_fee = c.maker_fee if limit else c.taker_fee
            budget = min(c.risk_per_trade, c.daily_loss_cap + self.day_pnl()) * s.day_start_equity
            # Size so that stop distance + both fees + slippage costs at most the budget.
            notional = budget / (stop_frac + entry_fee + c.taker_fee + c.slippage)
            notional = min(notional, s.cash / (1 + entry_fee))  # never more than cash
            if notional < 10:
                continue
            if limit:
                s.pending = {"product": product, "price": price, "notional": notional, "stop": stop}
            else:
                self._buy(ts, product, price, notional, entry_fee, stop)
            return

    def to_dict(self) -> dict:
        return asdict(self.s)
