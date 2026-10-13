"""Portfolio — positions, cash, and the risk counters the gate reads.

This is our belief about the account. Reconciliation compares it to the
broker's belief; when they disagree, the broker wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .orders import OrderTracker
from .risk import PortfolioState


@dataclass
class Position:
    symbol: str
    market: str
    quantity: int
    entry_price: float
    entry_date: datetime
    stop_price: float
    initial_stop_price: float
    target_price: float
    broker_stop_order_id: str | None = None
    breakeven_moved: bool = False
    bars_held: int = 0
    max_favourable: float = 0.0
    max_adverse: float = 0.0
    reason: dict = field(default_factory=dict)
    entry_cost: float = 0.0

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.initial_stop_price

    def unrealised_pnl(self, price: float) -> float:
        return (price - self.entry_price) * self.quantity

    def r_multiple(self, price: float) -> float:
        r = self.risk_per_share
        if r <= 0:
            return 0.0
        return (price - self.entry_price) / r

    def to_row(self) -> dict:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "entry_date": self.entry_date,
            "stop_price": self.stop_price,
            "initial_stop_price": self.initial_stop_price,
            "target_price": self.target_price,
            "breakeven_moved": self.breakeven_moved,
            "bars_held": self.bars_held,
            "entry_cost": self.entry_cost,
        }


@dataclass
class ClosedTrade:
    symbol: str
    market: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float
    entry_date: datetime
    exit_date: datetime
    pnl: float
    r_multiple: float
    exit_reason: str
    costs: float
    reason: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "pnl": self.pnl,
            "r_multiple": self.r_multiple,
            "exit_reason": self.exit_reason,
            "costs": self.costs,
            "reason": str(self.reason),
        }


class Portfolio:
    """One portfolio per market. India and US never share state."""

    def __init__(self, market: str, currency: str, starting_capital: float) -> None:
        self.market = market
        self.currency = currency
        self.starting_capital = float(starting_capital)
        self.cash = float(starting_capital)
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.orders = OrderTracker()

        # --- risk counters the gate reads ---------------------------------
        self.peak_equity = float(starting_capital)
        self.day_pnl = 0.0
        self.week_pnl = 0.0
        self.current_day = None
        self.current_week = None
        self.consecutive_losses = 0
        self.last_loss_time: dict[str, datetime] = {}
        self.cooldown_until: datetime | None = None
        self.halted = False
        self.halt_reason = ""
        self.total_costs = 0.0

    # ------------------------------------------------------------------
    @property
    def equity(self) -> float:
        """Cash plus marked-to-market value of open positions."""
        return self.cash + sum(
            p.quantity * p.entry_price for p in self.positions.values()
        )

    def equity_at(self, prices: dict[str, float]) -> float:
        mv = 0.0
        for sym, pos in self.positions.items():
            px = prices.get(sym, pos.entry_price)
            mv += pos.quantity * px
        return self.cash + mv

    def open_risk_amount(self, prices: dict[str, float] | None = None) -> float:
        total = 0.0
        for pos in self.positions.values():
            ref = pos.entry_price if prices is None else prices.get(pos.symbol, pos.entry_price)
            # risk is measured to the CURRENT stop, not the initial stop
            total += max(0.0, (ref - pos.stop_price)) * pos.quantity
        return total

    def symbol_exposure(self) -> dict[str, float]:
        return {
            sym: pos.quantity * pos.entry_price
            for sym, pos in self.positions.items()
        }

    # ------------------------------------------------------------------
    def roll_day(self, when: datetime) -> None:
        day = when.date()
        if self.current_day != day:
            self.current_day = day
            self.day_pnl = 0.0
        week = when.isocalendar()[:2]
        if self.current_week != week:
            self.current_week = week
            self.week_pnl = 0.0

    def update_peak(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity

    def risk_state(self, prices: dict[str, float] | None = None) -> PortfolioState:
        return PortfolioState(
            equity=self.equity,
            peak_equity=self.peak_equity,
            day_pnl=self.day_pnl,
            week_pnl=self.week_pnl,
            open_positions=len(self.positions),
            open_risk_amount=self.open_risk_amount(prices),
            symbol_exposure=self.symbol_exposure(),
            consecutive_losses=self.consecutive_losses,
            last_loss_time=dict(self.last_loss_time),
            cooldown_until=self.cooldown_until,
            halted=self.halted,
            halt_reason=self.halt_reason,
            market=self.market,
        )

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def resume(self) -> None:
        """Manual restart only. Never called automatically."""
        self.halted = False
        self.halt_reason = ""
        self.consecutive_losses = 0
        self.cooldown_until = None

    # ------------------------------------------------------------------
    def open_position(self, pos: Position) -> None:
        notional = pos.quantity * pos.entry_price
        self.cash -= notional + pos.entry_cost
        self.positions[pos.symbol] = pos

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_date: datetime,
        exit_reason: str,
        exit_cost: float,
    ) -> ClosedTrade | None:
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        gross = (exit_price - pos.entry_price) * pos.quantity
        costs = pos.entry_cost + exit_cost
        net = gross - costs

        r = pos.risk_per_share
        r_mult = (exit_price - pos.entry_price) / r if r > 0 else 0.0

        trade = ClosedTrade(
            symbol=symbol,
            market=pos.market,
            side="long",
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            pnl=net,
            r_multiple=r_mult,
            exit_reason=exit_reason,
            costs=costs,
            reason=pos.reason,
        )

        self.cash += pos.quantity * exit_price - exit_cost
        self.total_costs += costs
        del self.positions[symbol]
        self.closed.append(trade)

        # --- risk counters ------------------------------------------------
        self.day_pnl += net
        self.week_pnl += net
        if net < 0:
            self.consecutive_losses += 1
            self.last_loss_time[symbol] = exit_date
        else:
            self.consecutive_losses = 0

        return trade
