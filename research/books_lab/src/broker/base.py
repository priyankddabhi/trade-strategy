"""Broker adapter contract.

Every venue - OpenAlgo (India), IBKR, Alpaca, and the paper broker -
implements exactly this. That is what makes "dry-run == live" true: the
strategy never knows which one it is talking to.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..orders import Order, OrderType, Side


class BrokerAdapter(Protocol):
    name: str
    market: str

    def connect(self) -> bool: ...

    def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        order_type: OrderType,
        quantity: int,
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
    ) -> Order: ...

    def cancel_order(self, client_order_id: str) -> bool: ...

    def get_order(self, client_order_id: str) -> Order | None: ...

    def positions(self) -> dict: ...

    def cash(self) -> float: ...

    def last_price(self, symbol: str) -> float | None: ...

    def is_market_open(self, when: datetime) -> bool: ...
