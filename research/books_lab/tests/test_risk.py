"""Risk gate unit tests.

The gate is a pure function, so it can be proven correct here without any
data, any clock and any broker. If these pass, the numbers it enforces are
exactly the numbers in BOT_INSTRUCTIONS §2.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk import PortfolioState, RiskConfig, risk_gate  # noqa: E402

NOW = datetime(2026, 10, 7, 10, 0, 0)


def make_state(**kw) -> PortfolioState:
    base = dict(
        equity=500000.0,
        peak_equity=500000.0,
        day_pnl=0.0,
        week_pnl=0.0,
        open_positions=0,
        open_risk_amount=0.0,
        symbol_exposure={},
        consecutive_losses=0,
        last_loss_time={},
        cooldown_until=None,
        halted=False,
        market="india",
    )
    base.update(kw)
    return PortfolioState(**base)


def call(state=None, **kw):
    """Call the gate with sane defaults, overriding anything."""
    params = dict(
        symbol="RELIANCE",
        market="india",
        proposed_notional=25000.0,
        risk_amount=2500.0,       # 0.5% of 500k
        stop_distance=1.0,
        reward_risk=2.5,
        bar_age_hours=20.0,
        now=NOW,
        portfolio=state or make_state(),
        config=RiskConfig(),
    )
    params.update(kw)
    return risk_gate(**params)


results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f"  -- {detail}" if detail and not condition else ""))


# --------------------------------------------------------------------------
print("\nRISK GATE - BOT_INSTRUCTIONS section 2")
print("-" * 60)

# 0.5% per trade
check("0.5% risk allowed at exactly the limit",
      call(risk_amount=2500.0).allowed)
check("0.5% risk blocked above the limit",
      not call(risk_amount=2501.0).allowed)
check("0.5% risk blocked at 2x the limit",
      not call(risk_amount=5000.0).allowed)
check("risk rejection names the rule",
      call(risk_amount=99999.0).rule == "risk_per_trade",
      call(risk_amount=99999.0).rule)

# 1% daily loss
check("1% daily loss blocks a new trade",
      not call(state=make_state(day_pnl=-5000.0)).allowed)      # exactly -1%
check("daily loss limit names the rule",
      call(state=make_state(day_pnl=-6000.0)).rule == "daily_loss_limit")
check("0.9% daily loss still allows trading",
      call(state=make_state(day_pnl=-4500.0)).allowed)

# 2% weekly loss
check("2% weekly loss blocks a new trade",
      not call(state=make_state(week_pnl=-10000.0)).allowed)
check("weekly loss limit names the rule",
      call(state=make_state(week_pnl=-11000.0)).rule == "weekly_loss_limit")

# 5% drawdown
check("5% drawdown blocks and requires manual restart",
      not call(state=make_state(equity=475000.0, peak_equity=500000.0)).allowed)
check("max drawdown names the rule",
      call(state=make_state(equity=470000.0)).rule == "max_drawdown")
# 4.9% drawdown: equity is lower, so the 0.5% budget shrinks too. Pass the
# correctly-sized risk amount, otherwise the trade is blocked by the per-trade
# rule rather than the drawdown rule.
_dd = make_state(equity=475500.0, peak_equity=500000.0)
check("4.9% drawdown still allows trading",
      call(state=_dd, risk_amount=2377.5).allowed)
check("4.9% drawdown does not trip the drawdown rule",
      call(state=_dd, risk_amount=2377.5).rule != "max_drawdown",
      call(state=_dd, risk_amount=2377.5).rule)

# 10% per stock
check("10% per-stock cap blocks the 11th percent",
      not call(proposed_notional=60000.0,
               state=make_state(symbol_exposure={"RELIANCE": 50000.0})).allowed)
check("per-stock cap names the rule",
      call(proposed_notional=60000.0,
           state=make_state(symbol_exposure={"RELIANCE": 50000.0})).rule
      == "max_per_stock")
check("9% of equity in one stock is allowed",
      call(proposed_notional=45000.0).allowed)
check("9% existing + 2% new = 11% is blocked",
      not call(proposed_notional=10000.0,
               state=make_state(symbol_exposure={"RELIANCE": 45000.0})).allowed)
check("existing exposure in OTHER stocks does not count",
      call(proposed_notional=45000.0,
           state=make_state(symbol_exposure={"TCS": 45000.0, "INFY": 40000.0})).allowed)

# 5 positions per market
check("5 open positions block a 6th",
      not call(state=make_state(open_positions=5)).allowed)
check("position count cap names the rule",
      call(state=make_state(open_positions=9)).rule == "max_positions")
check("4 open positions still allow a 5th",
      call(state=make_state(open_positions=4)).allowed)

# R:R >= 2:1 after costs
check("R:R below 2:1 is rejected",
      not call(reward_risk=1.9).allowed)
check("R:R exactly 2:1 is accepted",
      call(reward_risk=2.0).allowed)
check("R:R rejection names the rule",
      call(reward_risk=1.5).rule == "reward_risk")

# halt
check("a halted system rejects everything",
      not call(state=make_state(halted=True, halt_reason="drawdown")).allowed)
check("halt rejection names the rule",
      call(state=make_state(halted=True)).rule == "halted")

# cooldown
check("cooldown blocks entries",
      not call(state=make_state(cooldown_until=NOW + timedelta(days=2))).allowed)
check("expired cooldown allows entries",
      call(state=make_state(cooldown_until=NOW - timedelta(days=1))).allowed)

# consecutive losses
check("4 consecutive losses trigger the loss streak block",
      not call(state=make_state(consecutive_losses=4)).allowed)
check("loss streak rejection names the rule",
      call(state=make_state(consecutive_losses=6)).rule == "consecutive_losses")

# revenge trade
_rev = make_state(last_loss_time={"RELIANCE": NOW - timedelta(minutes=30)})
check("re-entry on a just-lost symbol is blocked", not call(state=_rev).allowed)

_old = make_state(last_loss_time={"RELIANCE": NOW - timedelta(minutes=90)})
check("re-entry after the lock window is allowed", call(state=_old).allowed)

_rev2 = make_state(last_loss_time={"RELIANCE": NOW - timedelta(minutes=5)})
check("revenge lock names the rule", call(state=_rev2).rule == "revenge_lock")

_other = make_state(last_loss_time={"TCS": NOW - timedelta(minutes=5)})
check("a loss on another symbol does not block this one", call(state=_other).allowed)

# stale data
check("stale data blocks trading",
      not call(bar_age_hours=200.0).allowed)
check("stale data rejection names the rule",
      call(bar_age_hours=200.0).rule == "stale_data")

# zero equity
check("zero equity blocks everything",
      not call(state=make_state(equity=0.0)).allowed)

# --------------------------------------------------------------------------
print("\nRISK GATE - ordering (the first failing rule is the one reported)")
print("-" * 60)
d = call(state=make_state(halted=True, day_pnl=-999999.0, equity=0.0))
check("halt is reported before everything else", d.rule == "halted", d.rule)

d = call(state=make_state(cooldown_until=NOW + timedelta(days=1), day_pnl=-99999.0))
check("cooldown is reported before the daily limit", d.rule == "cooldown", d.rule)

d = call(state=make_state(day_pnl=-99999.0, week_pnl=-999999.0))
check("daily limit is reported before the weekly limit",
      d.rule == "daily_loss_limit", d.rule)

# --------------------------------------------------------------------------
print("\nRISK GATE - purity (no I/O, no clock, no randomness)")
print("-" * 60)
a = call()
b = call()
check("same inputs give the same answer (deterministic)",
      a.allowed == b.allowed and a.rule == b.rule)
check("Decision is a plain value object", hasattr(a, "allowed") and hasattr(a, "rule"))

# --------------------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("\n" + "=" * 60)
print(f"  {passed}/{total} checks passed")
print("=" * 60)
sys.exit(0 if passed == total else 1)
