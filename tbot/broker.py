"""Virtual broker. Fills orders against real daily bars. No real money moves.

Rules that keep the simulation honest:
- Orders made after today's close fill at the NEXT session's open.
- A gap below the stop exits at the (worse) open, not at the stop.
- If the stop and the target are both inside one day's range, the stop wins.
- Every fill pays slippage and the market's real charges.
"""
from __future__ import annotations

import math

import pandas as pd

from .models import Order, Position, SimState, Trade


class VirtualBroker:
    def __init__(self, costs, target_r: float | None = None, max_gap_mult: float = 1.5,
                 fractional: bool = False):
        self.costs = costs
        self.slip = costs.slippage
        self.target_r = target_r
        self.max_gap_mult = max_gap_mult
        self.fractional = fractional

    def new_order(self, st: SimState, side: str, symbol: str, qty: float, day: pd.Timestamp,
                  reason: str, stop: float | None = None, est_price: float | None = None,
                  planned_risk: float | None = None, strategy: str = "", target_r: float | None = None,
                  limit: float | None = None, stop_dist: float | None = None) -> Order:
        order = Order(id=st.next_order_id, symbol=symbol, side=side, qty=qty,
                      created=str(day.date()), reason=reason, stop=stop,
                      est_price=est_price, planned_risk=planned_risk, strategy=strategy,
                      target_r=target_r, limit=limit, stop_dist=stop_dist)
        st.next_order_id += 1
        return order

    def _round(self, qty: float) -> float:
        return round(qty, 4) if self.fractional else math.floor(qty)

    def fill_entry(self, st: SimState, order: Order, bar, day: pd.Timestamp,
                   blackout: bool) -> tuple[Position | None, str]:
        sym = order.symbol
        if blackout:
            return None, f"{sym}: buy cancelled, event blackout day"
        raw = float(bar.Open)
        if order.limit is not None:                      # limit buy: only at the limit price or lower
            if float(getattr(bar, "Low", raw)) > order.limit:
                return None, f"{sym}: limit buy at {order.limit:.2f} not reached (low {getattr(bar, 'Low', raw):.2f}), cancelled"
            raw = min(raw, float(order.limit))
        if order.stop_dist is not None:                  # stop measured from the real fill
            order.stop = raw - order.stop_dist
        if raw <= order.stop:
            return None, f"{sym}: buy cancelled, opened at {bar.Open:.2f}, at or below the stop {order.stop:.2f}"
        price = raw * (1 + self.slip)
        rps = price - order.stop
        if order.planned_risk and rps * order.qty > order.planned_risk * self.max_gap_mult:
            return None, (f"{sym}: buy cancelled, gap up to {bar.Open:.2f} made the risk "
                          f"{rps * order.qty / order.planned_risk:.1f}x the plan")
        qty = order.qty
        cost = self.costs.buy(qty, price)
        if qty * price + cost > st.cash:
            qty = self._round(st.cash * 0.99 / price)
            if qty <= 0:
                return None, f"{sym}: buy cancelled, not enough cash"
            cost = self.costs.buy(qty, price)
        st.cash -= qty * price + cost
        tr = self.target_r if order.target_r is None else order.target_r      # 0: this rule has no fixed target
        target = price + tr * rps if tr else None
        pos = Position(symbol=sym, qty=qty, entry_date=str(day.date()), entry_price=price,
                       stop=float(order.stop), initial_stop=float(order.stop), risk_per_share=rps,
                       target=target, entry_costs=cost, reason=order.reason,
                       highest_close=float(bar.Close), last_close=float(bar.Close), strategy=order.strategy,
                       highest_high=float(getattr(bar, "High", raw)))
        st.positions[sym] = pos
        return pos, f"{sym}: bought {qty:g} at {price:.2f}, stop {pos.stop:.2f}" + (
            f", target {target:.2f}" if target else "")

    def check_exit(self, st: SimState, pos: Position, bar, day: pd.Timestamp,
                   entered_today: bool) -> Trade | None:
        """Stop first, then target. Uses the day's real open, high and low."""
        if not entered_today and bar.Open <= pos.stop:
            return self.close(st, pos.symbol, float(bar.Open), day, "Gapped below the stop")
        if bar.Low <= pos.stop:
            why = "Breakeven or trailing stop hit" if pos.breakeven else "Stop-loss hit"
            return self.close(st, pos.symbol, pos.stop, day, why)
        if pos.target is not None:
            if not entered_today and bar.Open >= pos.target:
                return self.close(st, pos.symbol, float(bar.Open), day, "Gapped above the target")
            if bar.High >= pos.target:
                return self.close(st, pos.symbol, pos.target, day, "Target hit")
        return None

    def close(self, st: SimState, symbol: str, raw_price: float, day: pd.Timestamp,
              reason: str) -> Trade:
        pos = st.positions.pop(symbol)
        price = raw_price * (1 - self.slip)
        cost = self.costs.sell(pos.qty, price)
        st.cash += pos.qty * price - cost
        pnl = (price - pos.entry_price) * pos.qty - pos.entry_costs - cost
        risk = pos.risk_per_share * pos.qty
        st.orders = [o for o in st.orders if not (o.symbol == symbol and o.side == "SELL")]
        st.cooldown[symbol] = st.session_no
        return Trade(symbol=symbol, qty=pos.qty, entry_date=pos.entry_date,
                     entry_price=pos.entry_price, exit_date=str(day.date()), exit_price=price,
                     costs=pos.entry_costs + cost, pnl=pnl,
                     r_multiple=pnl / risk if risk > 0 else 0.0,
                     exit_reason=reason, entry_reason=pos.reason, strategy=pos.strategy)
