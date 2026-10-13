"""RISK GATE — the only module allowed to say no.

Design rules (from the architecture report):
  * It is a PURE function: (intent, portfolio, state) -> Decision.
    No I/O, no clock, no randomness. Fully unit-testable.
  * It runs BEFORE position sizing and BEFORE the order manager.
  * It cannot be overridden by a strategy. Ever.
  * Every rejection carries the rule name, for the website.

BOT_INSTRUCTIONS §2 rules, in order:
  0.5% per trade · 1% day · 2% week · 5% drawdown · 10%/stock ·
  5 positions/market · R:R >= 2:1 after costs · cooldown · no stale data ·
  no revenge trades · no averaging down
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


# --------------------------------------------------------------------------
@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 1.0
    max_weekly_loss_pct: float = 2.0
    max_drawdown_pct: float = 5.0
    max_capital_per_stock_pct: float = 10.0
    max_positions_per_market: int = 5
    min_reward_risk: float = 2.0
    max_consecutive_losses: int = 4
    cooldown_days: int = 3
    revenge_lock_minutes: int = 60
    max_open_risk_pct: float = 2.0          # aggregate open risk cap
    max_bar_age_hours: int = 72

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RiskConfig":
        r = cfg["risk"]
        return cls(
            risk_per_trade_pct=float(r["risk_per_trade_pct"]),
            max_daily_loss_pct=float(r["max_daily_loss_pct"]),
            max_weekly_loss_pct=float(r["max_weekly_loss_pct"]),
            max_drawdown_pct=float(r["max_drawdown_pct"]),
            max_capital_per_stock_pct=float(r["max_capital_per_stock_pct"]),
            max_positions_per_market=int(r["max_positions_per_market"]),
            min_reward_risk=float(r["min_reward_risk"]),
            max_consecutive_losses=int(r["max_consecutive_losses"]),
            cooldown_days=int(r["cooldown_days_after_losses"]),
            revenge_lock_minutes=int(r["revenge_trade_lock_minutes"]),
        )


@dataclass
class Decision:
    allowed: bool
    rule: str = "ok"
    detail: str = ""

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class PortfolioState:
    """Everything the gate is allowed to know about the account."""

    equity: float
    peak_equity: float
    day_pnl: float
    week_pnl: float
    open_positions: int
    open_risk_amount: float
    symbol_exposure: dict = field(default_factory=dict)   # symbol -> notional
    consecutive_losses: int = 0
    last_loss_time: dict = field(default_factory=dict)    # symbol -> datetime
    cooldown_until: datetime | None = None
    halted: bool = False
    halt_reason: str = ""
    market: str = "india"


ALLOW = Decision(True, "ok", "all checks passed")


def _pct(x: float) -> float:
    return x * 100.0


# --------------------------------------------------------------------------
#  THE GATE
# --------------------------------------------------------------------------
def risk_gate(
    *,
    symbol: str,
    market: str,
    proposed_notional: float,
    risk_amount: float,
    stop_distance: float,
    reward_risk: float,
    bar_age_hours: float,
    now: datetime,
    portfolio: PortfolioState,
    config: RiskConfig,
) -> Decision:
    """Evaluate one proposed trade. Returns Decision(allowed, rule, detail).

    `risk_amount` is the rupee/dollar loss if the stop is hit:
        quantity * stop_distance
    It is computed by the sizer, not by the gate, so the gate stays a pure
    function of numbers it is given.
    """

    equity = portfolio.equity

    # ---- 1. system halt ------------------------------------------------
    # Checked FIRST: it is the most severe state and needs a manual restart.
    if portfolio.halted:
        return Decision(False, "halted", f"system halted: {portfolio.halt_reason}")

    if equity <= 0:
        return Decision(False, "no_equity", "equity is zero or negative")

    # ---- 2. cooldown after loss streak ---------------------------------
    if portfolio.cooldown_until and now < portfolio.cooldown_until:
        return Decision(
            False,
            "cooldown",
            f"in cooldown until {portfolio.cooldown_until.isoformat()}",
        )

    # ---- 3. daily loss -------------------------------------------------
    if portfolio.day_pnl <= -(config.max_daily_loss_pct / 100.0) * equity:
        return Decision(
            False,
            "daily_loss_limit",
            f"day P&L {_pct(portfolio.day_pnl / equity):.2f}% <= "
            f"-{config.max_daily_loss_pct}%",
        )

    # ---- 4. weekly loss ------------------------------------------------
    if portfolio.week_pnl <= -(config.max_weekly_loss_pct / 100.0) * equity:
        return Decision(
            False,
            "weekly_loss_limit",
            f"week P&L {_pct(portfolio.week_pnl / equity):.2f}% <= "
            f"-{config.max_weekly_loss_pct}%",
        )

    # ---- 5. max drawdown -----------------------------------------------
    if portfolio.peak_equity > 0:
        dd = (equity - portfolio.peak_equity) / portfolio.peak_equity
        if dd <= -(config.max_drawdown_pct / 100.0):
            return Decision(
                False,
                "max_drawdown",
                f"drawdown {_pct(dd):.2f}% <= -{config.max_drawdown_pct}% "
                "(manual restart required)",
            )

    # ---- 6. consecutive losses -----------------------------------------
    if portfolio.consecutive_losses >= config.max_consecutive_losses:
        return Decision(
            False,
            "consecutive_losses",
            f"{portfolio.consecutive_losses} losses in a row "
            f"(limit {config.max_consecutive_losses})",
        )

    # ---- 7. revenge trade lock -----------------------------------------
    last_loss = portfolio.last_loss_time.get(symbol)
    if last_loss is not None:
        if now - last_loss < timedelta(minutes=config.revenge_lock_minutes):
            return Decision(
                False,
                "revenge_lock",
                f"{symbol} lost within "
                f"{config.revenge_lock_minutes} min — no re-entry",
            )

    # ---- 8. stale data -------------------------------------------------
    if bar_age_hours > config.max_bar_age_hours:
        return Decision(
            False,
            "stale_data",
            f"bar is {bar_age_hours:.1f}h old (limit {config.max_bar_age_hours}h)",
        )

    # ---- 9. position count ---------------------------------------------
    if portfolio.open_positions >= config.max_positions_per_market:
        return Decision(
            False,
            "max_positions",
            f"{portfolio.open_positions} open "
            f"(limit {config.max_positions_per_market})",
        )

    # ---- 10. per-stock concentration -----------------------------------
    existing = portfolio.symbol_exposure.get(symbol, 0.0)
    total_symbol = existing + proposed_notional
    if total_symbol > (config.max_capital_per_stock_pct / 100.0) * equity:
        return Decision(
            False,
            "max_per_stock",
            f"{symbol} would be {_pct(total_symbol / equity):.1f}% of equity "
            f"(limit {config.max_capital_per_stock_pct}%)",
        )

    # ---- 11. per-trade risk --------------------------------------------
    if risk_amount > (config.risk_per_trade_pct / 100.0) * equity + 1e-9:
        return Decision(
            False,
            "risk_per_trade",
            f"risk {risk_amount:.2f} = {_pct(risk_amount / equity):.3f}% of equity "
            f"> {config.risk_per_trade_pct}%",
        )

    # ---- 12. aggregate open risk ---------------------------------------
    if portfolio.open_risk_amount > (config.max_open_risk_pct / 100.0) * equity:
        return Decision(
            False,
            "max_open_risk",
            f"open risk {_pct(portfolio.open_risk_amount / equity):.2f}% > "
            f"{config.max_open_risk_pct}%",
        )

    # ---- 13. reward:risk after costs -----------------------------------
    if reward_risk < config.min_reward_risk - 1e-9:
        return Decision(
            False,
            "reward_risk",
            f"R:R {reward_risk:.2f} < {config.min_reward_risk}",
        )

    return ALLOW


# --------------------------------------------------------------------------
#  Halt escalation — called by the engine after every closed trade
# --------------------------------------------------------------------------
def evaluate_halts(portfolio: PortfolioState, config: RiskConfig) -> Decision | None:
    """Check whether an already-realised loss must escalate to a halt.
    Returns a Decision to apply, or None to continue."""
    equity = portfolio.equity

    if portfolio.peak_equity > 0:
        dd = (equity - portfolio.peak_equity) / portfolio.peak_equity
        if dd <= -(config.max_drawdown_pct / 100.0):
            return Decision(
                False, "max_drawdown",
                f"drawdown {_pct(dd):.2f}% — FULL STOP, manual restart",
            )

    if portfolio.week_pnl <= -(config.max_weekly_loss_pct / 100.0) * equity:
        return Decision(False, "weekly_loss_limit", "week loss limit hit")

    if portfolio.day_pnl <= -(config.max_daily_loss_pct / 100.0) * equity:
        return Decision(False, "daily_loss_limit", "day loss limit hit")

    if portfolio.consecutive_losses >= config.max_consecutive_losses:
        return Decision(
            False, "consecutive_losses", "loss streak — cooldown engaged"
        )

    return None
