"""Position sizing.

BOT_INSTRUCTIONS: max 0.5% of capital per trade.

    quantity = floor( (risk_pct x equity) / stop_distance )

If the answer is zero, the trade is too big for the account and is skipped.
That is a feature, not a bug.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SizeResult:
    quantity: int
    notional: float
    risk_amount: float
    skipped: bool
    reason: str = ""


def size_position(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_pct: float = 0.5,
    min_quantity: int = 1,
) -> SizeResult:
    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        return SizeResult(0, 0.0, 0.0, True, "stop distance is not positive")
    if entry_price <= 0:
        return SizeResult(0, 0.0, 0.0, True, "entry price is not positive")

    risk_budget = (risk_pct / 100.0) * equity
    raw_qty = risk_budget / stop_distance
    qty = int(math.floor(raw_qty))

    if qty < min_quantity:
        return SizeResult(
            0,
            0.0,
            0.0,
            True,
            f"sized quantity {qty} < {min_quantity} "
            f"(need {raw_qty:.2f}; stop too wide for this account)",
        )

    notional = qty * entry_price
    return SizeResult(qty, notional, qty * stop_distance, False, "")


def cost_of_round_trip(notional: float, round_trip_pct: float, minimum: float = 0.0) -> float:
    """Round-trip cost in currency units."""
    return max(notional * round_trip_pct / 100.0, minimum)


def reward_risk_after_costs(
    *,
    entry_price: float,
    stop_price: float,
    target_price: float,
    notional: float,
    round_trip_pct: float,
) -> float:
    """R:R measured in rupees, not in price points.

    A 2:1 price ratio is NOT a 2:1 money ratio once costs are paid on both
    legs. This is the number BOT_INSTRUCTIONS §2 actually asks for.
    """
    qty = notional / entry_price if entry_price else 0.0
    if qty <= 0:
        return 0.0
    gross_risk = (entry_price - stop_price) * qty
    gross_reward = (target_price - entry_price) * qty
    cost = cost_of_round_trip(notional, round_trip_pct)
    net_risk = gross_risk + cost
    net_reward = gross_reward - cost
    if net_risk <= 0:
        return 0.0
    return net_reward / net_risk
