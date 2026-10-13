"""Plain data objects for the virtual account. Saved as JSON between daily runs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Position:
    symbol: str
    qty: float
    entry_date: str
    entry_price: float
    stop: float
    initial_stop: float
    risk_per_share: float
    target: float | None
    entry_costs: float
    reason: str
    highest_close: float = 0.0
    last_close: float = 0.0
    breakeven: bool = False
    strategy: str = ""            # the rule that bought it; it also decides the sell
    highest_high: float = 0.0
    bars_held: int = 0
    pred_base: float = 0.0        # the near-future band (tbot/predict.py): start price, rise per day, width, day
    pred_slope: float = 0.0
    pred_atr: float = 0.0
    pred_n: int = 0
    pred_state: str = ""


@dataclass
class Order:
    id: int
    symbol: str
    side: str                     # BUY or SELL, filled at the next session's open
    qty: float
    created: str
    reason: str
    stop: float | None = None
    est_price: float | None = None
    planned_risk: float | None = None
    strategy: str = ""
    target_r: float | None = None
    limit: float | None = None
    stop_dist: float | None = None


@dataclass
class Trade:
    symbol: str
    qty: float
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    costs: float
    pnl: float
    r_multiple: float
    exit_reason: str
    entry_reason: str
    strategy: str = ""


@dataclass
class SimState:
    market: str
    initial_capital: float
    cash: float
    start_date: str
    last_date: str | None = None
    session_no: int = 0
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    next_order_id: int = 1
    peak_equity: float = 0.0
    prev_equity: float = 0.0
    week_key: str = ""
    week_start_equity: float = 0.0
    day_return: float = 0.0
    week_return: float = 0.0
    drawdown: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    halt_sessions_left: int = 0
    halts: int = 0
    pause_sessions_left: int = 0
    loss_streak: int = 0
    cooldown: dict[str, int] = field(default_factory=dict)   # symbol -> session of last exit
    deposits: float = 0.0                                    # money added minus money withdrawn

    def capital_in(self) -> float:
        """Starting money plus net deposits. Profit is measured against this."""
        return self.initial_capital + self.deposits

    def equity(self) -> float:
        return self.cash + sum(p.qty * p.last_close for p in self.positions.values())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SimState":
        d = dict(d)
        d["positions"] = {k: Position(**v) for k, v in (d.get("positions") or {}).items()}
        d["orders"] = [Order(**o) for o in (d.get("orders") or [])]
        return cls(**d)
