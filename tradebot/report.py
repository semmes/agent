"""Markdown reports for the paper run."""
from __future__ import annotations

import pandas as pd

from .config import Config
from .engine import Engine


def _status(eng: Engine, meta: dict) -> str:
    s = eng.s
    if s.shutdown:
        return f"STOPPED: {s.shutdown}. Review, then run with --reset-shutdown."
    if meta.get("finished"):
        return "30-day paper run complete."
    if s.day_halt:
        return f"Done for today ({'hit +target' if s.day_halt == 'target' else 'hit loss cap'})."
    if s.qty:
        return f"Holding {s.qty:.6f} {s.product} from {s.entry_price:,.2f}, stop {s.stop:,.2f}."
    return "Flat, waiting for a setup."


def _headline(eng: Engine, cfg: Config) -> list[str]:
    eq = eng.equity()
    days = eng.s.days
    lines = [
        f"- **Equity:** ${eq:,.2f} ({(eq / cfg.starting_cash - 1) * 100:+.2f}% since start, "
        f"today {eng.day_pnl() * 100:+.2f}%)",
    ]
    if days:
        pnl = pd.Series([d["pnl_pct"] for d in days])
        halts = pd.Series([d["halt"] for d in days])
        lines.append(
            f"- **Completed days:** {len(days)}: {(pnl > 0).sum()} up, {(pnl < 0).sum()} down, "
            f"{(pnl == 0).sum()} flat; hit +target on {(halts == 'target').sum()}, "
            f"hit loss cap on {(halts == 'loss_cap').sum()}; worst day {pnl.min():+.2f}%"
        )
    fees = sum(t["fees"] for t in eng.s.trades)
    lines.append(f"- **Trades:** {len(eng.s.trades)}, fees paid ${fees:,.2f}")
    return lines


def render_daily(eng: Engine, cfg: Config, meta: dict) -> str:
    d = eng.s.days[-1]
    out = [f"### {d['day']}: {d['pnl_pct']:+.2f}% (${d['start']:,.2f} -> ${d['end']:,.2f})", ""]
    out += _headline(eng, cfg) + ["", f"**Status:** {_status(eng, meta)}"]
    return "\n".join(out) + "\n"


def render_report(eng: Engine, cfg: Config, meta: dict) -> str:
    updated = pd.Timestamp(meta.get("updated", 0), unit="s", tz="UTC").tz_convert(cfg.timezone)
    out = ["# Paper trading report", "", f"Updated {updated:%Y-%m-%d %H:%M %Z}. Simulated money only.", ""]
    out += [f"**Status:** {_status(eng, meta)}", ""] + _headline(eng, cfg) + [""]
    if eng.s.days:
        out += ["## Days", "", "| Day | Start | End | P&L % | Trades | Ended by |", "|---|---|---|---|---|---|"]
        out += [
            f"| {d['day']} | {d['start']:,.2f} | {d['end']:,.2f} | {d['pnl_pct']:+.2f} | {d['trades']} | {d['halt'] or '-'} |"
            for d in reversed(eng.s.days)
        ]
        out.append("")
    if eng.s.trades:
        out += ["## Trades", "", "| Product | In | Out | Entry | Exit | P&L $ | Fees $ | Exit reason |", "|---|---|---|---|---|---|---|---|"]
        out += [
            f"| {t['product']} | {t['entry_ts']} | {t['exit_ts']} | {t['entry_price']:,.2f} | {t['exit_price']:,.2f} "
            f"| {t['pnl']:+.2f} | {t['fees']:.2f} | {t['reason']} |"
            for t in reversed(eng.s.trades)
        ]
        out.append("")
    recent = [n for n in meta.get("news", [])][-5:]
    if recent:
        out += ["## Latest news checks", ""]
        out += [
            f"- {pd.Timestamp(n['ts'], unit='s', tz='UTC').tz_convert(cfg.timezone):%m-%d %H:%M}: "
            f"{n['risk']} / {n['direction']}"
            f"{' (entries blocked)' if n['block'] else ' (not confirmed by price; no block)' if n.get('warning') else ''}"
            f": {n['reason']} [{n['scorer']}]"
            for n in reversed(recent)
        ]
    return "\n".join(out) + "\n"
