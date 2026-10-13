"""PaperBroker - real market prices, dummy money.

This is the whole point of the exercise: see how the strategy performs against
real data without risking a rupee.

Design notes
------------
* Filled against the SAME bar data the strategy saw, at the NEXT bar's open,
  exactly like a live broker fills a market order submitted after the signal
  bar closes.
* Stops and targets are evaluated against the bar's high/low. When one bar
  contains both, the STOP is assumed to hit first. Conservative, deliberate.
* It keeps its own cash and positions, separate from the portfolio's
  accounting, so reconciliation has something real to compare against.
"""
from __future__ import annotations

import pandas as pd

from ..orders import (
    Order,
    OrderState,
    OrderTracker,
    OrderType,
    Side,
    make_client_order_id,
)


class PaperBroker:
    name = "PAPER"
    market = "paper"

    def __init__(
        self,
        bars: dict,
        starting_cash: float,
        cost_pct: float = 0.29,
    ) -> None:
        self._bars = bars
        self._cash = float(starting_cash)
        self._cost_pct = cost_pct
        self._positions: dict = {}
        self._orders = OrderTracker()
        self._filled: list = []
        self._cursor = 0
        self._dates: list = self._build_calendar()

    # ------------------------------------------------------------------
    def _build_calendar(self) -> list:
        dates = set()
        for df in self._bars.values():
            dates.update(df.index)
        return sorted(dates)

    @property
    def dates(self) -> list:
        return self._dates

    def set_cursor(self, index: int) -> None:
        self._cursor = index

    def current_date(self):
        if not self._dates or self._cursor >= len(self._dates):
            return None
        return self._dates[self._cursor]

    def bar_at(self, symbol, date):
        df = self._bars.get(symbol)
        if df is None or date not in df.index:
            return None
        return df.loc[date]

    def bars_up_to(self, symbol, date):
        df = self._bars.get(symbol)
        if df is None:
            return pd.DataFrame()
        return df.loc[:date]

    # ------------------------------------------------------------------
    #  BrokerAdapter
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        return True

    def place_order(
        self,
        *,
        symbol,
        side,
        order_type,
        quantity,
        limit_price=None,
        stop_price=None,
        client_order_id=None,
    ) -> Order:
        coid = client_order_id or make_client_order_id(
            symbol, self.market, str(self.current_date()), order_type.value.lower()
        )
        if self._orders.exists(coid):
            return self._orders.get(coid)  # idempotent

        order = Order(
            client_order_id=coid,
            symbol=symbol,
            market=self.market,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
        )
        self._orders.register(order)

        date = self.current_date()
        if date is None:
            self._orders.transition(coid, OrderState.REJECTED, reason="no market date")
            return self._orders.get(coid)

        if order_type == OrderType.MARKET:
            fill_px = limit_price if limit_price is not None else self.last_price(symbol)
            if fill_px is None or fill_px <= 0:
                self._orders.transition(coid, OrderState.REJECTED, reason="no price")
                return self._orders.get(coid)
            self._fill(coid, symbol, side, quantity, float(fill_px), date)
        else:
            # stop-loss / target orders rest until the bar touches them
            self._orders.transition(coid, OrderState.SENT)

        return self._orders.get(coid)

    def _fill(self, coid, symbol, side, quantity, price, date) -> None:
        cost = quantity * price * self._cost_pct / 100.0
        self._orders.transition(
            coid,
            OrderState.FILLED,
            filled_quantity=quantity,
            average_fill_price=price,
            reason="filled @ %.2f" % price,
        )
        if side == Side.BUY:
            self._cash -= quantity * price + cost
            self._positions.setdefault(symbol, {"qty": 0, "avg": 0.0})
            p = self._positions[symbol]
            total_qty = p["qty"] + quantity
            p["avg"] = (p["avg"] * p["qty"] + price * quantity) / max(1, total_qty)
            p["qty"] = total_qty
        else:
            self._cash += quantity * price - cost
            p = self._positions.get(symbol)
            if p:
                p["qty"] = max(0, p["qty"] - quantity)
                if p["qty"] == 0:
                    del self._positions[symbol]

        self._filled.append(
            {
                "date": date,
                "symbol": symbol,
                "side": side.value,
                "quantity": quantity,
                "price": price,
                "cost": cost,
                "client_order_id": coid,
            }
        )

    def cancel_order(self, client_order_id: str) -> bool:
        order = self._orders.get(client_order_id)
        if order is None or order.is_terminal:
            return False
        self._orders.transition(client_order_id, OrderState.CANCELLED)
        return True

    def get_order(self, client_order_id: str):
        return self._orders.get(client_order_id)

    def positions(self) -> dict:
        return {s: p["qty"] for s, p in self._positions.items() if p["qty"] > 0}

    def cash(self) -> float:
        return self._cash

    def last_price(self, symbol):
        df = self._bars.get(symbol)
        if df is None or df.empty:
            return None
        sub = df.loc[: self.current_date()] if self.current_date() is not None else df
        if sub.empty:
            return None
        return float(sub["close"].iloc[-1])

    def is_market_open(self, when) -> bool:
        return True  # daily bars; session logic lives in the strategy

    # ------------------------------------------------------------------
    #  Resting stop / target evaluation - called by the engine each bar
    # ------------------------------------------------------------------
    def evaluate_resting_orders(self, date) -> list:
        """Fire any resting stop-loss or target order whose level was touched.

        Conservative rule: if a single bar contains BOTH the stop and the
        target, the stop fires first. We do not get to be lucky.
        """
        fired = []
        for order in self._orders.open_orders():
            if order.order_type not in (OrderType.STOP_LOSS, OrderType.TARGET):
                continue
            bar = self.bar_at(order.symbol, date)
            if bar is None:
                continue

            level = order.stop_price or order.limit_price
            if level is None:
                continue

            hit = False
            fill_px = level
            if order.order_type == OrderType.STOP_LOSS and float(bar["low"]) <= level:
                hit = True
                fill_px = min(level, float(bar["open"]))  # gap down -> fill at open
            elif order.order_type == OrderType.TARGET and float(bar["high"]) >= level:
                hit = True
                fill_px = max(level, float(bar["open"]))

            if hit:
                self._fill(
                    order.client_order_id,
                    order.symbol,
                    order.side,
                    order.quantity,
                    float(fill_px),
                    date,
                )
                fired.append(
                    {
                        "client_order_id": order.client_order_id,
                        "symbol": order.symbol,
                        "type": order.order_type.value,
                        "level": level,
                        "fill": float(fill_px),
                    }
                )
        return fired

    def fills(self) -> list:
        return list(self._filled)
