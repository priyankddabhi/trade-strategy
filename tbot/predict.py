"""A held share's near future: assume it keeps rising (or at least stays flat) and draw a band for the next days.

Each finished day the bot checks the share against its band:
- inside or above the band: on track, nothing to do;
- under the band: off track. Then its pattern is checked again (still above its 200-day average or still
  rising over 3 months?). Pattern still fine: keep holding. Pattern broken: move the stop up to 1 ATR under
  the close, so the share is protected. The strategy's own exit rule still decides the sale.
After `days` days (or with no band yet) a fresh band starts from today's close.
Only stops are moved up, never down; nothing here buys or sells by itself.
"""
from __future__ import annotations

import math


def check(cfg: dict, pos, bar) -> list[str]:
    p = (cfg.get("strategy") or {}).get("predict") or {}
    if not p.get("enabled"):
        return []
    days, band = int(p.get("days", 5)), float(p.get("band_atr", 1.0))
    close = float(bar.Close)
    slope, atr = float(getattr(bar, "slope20", float("nan"))), float(getattr(bar, "atr", float("nan")))
    if math.isnan(slope) or math.isnan(atr) or atr <= 0:
        return []
    if pos.pred_base <= 0 or pos.pred_n >= days:          # a fresh band: assume up (or flat), never down
        pos.pred_base, pos.pred_slope, pos.pred_atr, pos.pred_n, pos.pred_state = close, max(slope, 0.0), atr, 0, "new"
        return []
    pos.pred_n += 1
    expected = pos.pred_base + pos.pred_slope * pos.pred_n
    low = expected - band * pos.pred_atr
    if close >= low:
        pos.pred_state = "on track"
        return []
    if bool(getattr(bar, "f_quick", True)):
        pos.pred_state = "off track, pattern fine"
        return [f"{pos.symbol}: under its expected band ({close:.2f} < {low:.2f}), pattern still fine: keep holding"]
    pos.pred_state = "off track, pattern broken"
    new_stop = close - atr
    if pos.stop < new_stop < close:
        pos.stop = float(new_stop)
        return [f"{pos.symbol}: under its expected band and its pattern broke: stop moved up to {pos.stop:.2f}"]
    return [f"{pos.symbol}: under its expected band and its pattern broke (stop already close)"]


def status(pos, price: float, band: float = 1.0) -> str:
    """Live view of the band with a live price (shown only; stops move only at the daily check)."""
    if pos.pred_base <= 0 or pos.pred_atr <= 0:
        return "no band yet"
    expected = pos.pred_base + pos.pred_slope * max(pos.pred_n, 0)
    return "on track" if price >= expected - band * pos.pred_atr else "under its band"
