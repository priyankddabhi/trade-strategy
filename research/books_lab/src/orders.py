"""Order state machine.

Concept borrowed from Hummingbot's ClientOrderTracker / InFlightOrder:
ONE authoritative record per order, with a single lifecycle state.

This is what makes duplicate orders impossible and reconciliation trivial.
Every order carries an idempotent client_order_id derived from
(symbol, signal_date) so a network retry cannot create a second position.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class OrderState(str, Enum):
    NEW = "NEW"
    SENT = "SENT"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.REJECTED,
    OrderState.CANCELLED,
    OrderState.TIMEOUT,
}


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TARGET = "TARGET"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    client_order_id: str
    symbol: str
    market: str
    side: Side
    order_type: OrderType
    quantity: int
    limit_price: float | None = None
    stop_price: float | None = None
    state: OrderState = OrderState.NEW
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    broker_order_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""
    attempts: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def notional(self) -> float:
        px = self.average_fill_price or self.limit_price or 0.0
        return self.filled_quantity * px


def make_client_order_id(symbol: str, market: str, signal_date: str, tag: str = "entry") -> str:
    """Deterministic, idempotent. Same inputs -> same id, always."""
    raw = f"{market}|{symbol}|{signal_date}|{tag}"
    digest = hashlib.sha1(raw.encode()).hexdigest()[:12].upper()
    safe_symbol = "".join(c for c in symbol if c.isalnum())[:10]
    return f"{tag[:3].upper()}-{safe_symbol}-{digest}"


class OrderTracker:
    """The single source of truth for in-flight orders."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    def register(self, order: Order) -> Order:
        existing = self._orders.get(order.client_order_id)
        if existing is not None:
            # idempotency: never create a second order with the same id
            return existing
        self._orders[order.client_order_id] = order
        return order

    def get(self, client_order_id: str) -> Order | None:
        return self._orders.get(client_order_id)

    def exists(self, client_order_id: str) -> bool:
        return client_order_id in self._orders

    def transition(
        self,
        client_order_id: str,
        new_state: OrderState,
        *,
        filled_quantity: int | None = None,
        average_fill_price: float | None = None,
        broker_order_id: str | None = None,
        reason: str = "",
    ) -> Order | None:
        order = self._orders.get(client_order_id)
        if order is None:
            return None
        order.state = new_state
        if filled_quantity is not None:
            order.filled_quantity = filled_quantity
        if average_fill_price is not None:
            order.average_fill_price = average_fill_price
        if broker_order_id is not None:
            order.broker_order_id = broker_order_id
        if reason:
            order.reason = reason
        order.updated_at = datetime.utcnow()
        return order

    def open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if not o.is_terminal]

    def all(self) -> list[Order]:
        return list(self._orders.values())
