"""Safety rules tried against hundreds of random cases (Hypothesis). If one case breaks a rule, the test shows it."""
from types import SimpleNamespace

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as s

from tbot import risk
from tbot.broker import VirtualBroker
from tbot.config import load_market
from tbot.costs import make_cost_model
from tbot.models import Order, Position, SimState
from tbot.strategy import Candidate

CFG = {m: load_market(m) for m in ("us", "india")}
FAST = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def account(cash: float, equity: float) -> SimState:
    return SimState(market="us", initial_capital=equity, cash=cash, start_date="2026-01-01",
                    peak_equity=equity, prev_equity=equity, week_start_equity=equity)


@FAST
@given(market=s.sampled_from(["us", "india"]), price=s.floats(1, 5000), stop_frac=s.floats(0.5, 0.999),
       equity=s.floats(1_000, 10_000_000), cash_frac=s.floats(0, 1), lift=s.booleans(), gate=s.sampled_from([None, 2.0, 3.0]))
def test_a_buy_never_breaks_the_size_rules(market, price, stop_frac, equity, cash_frac, lift, gate):
    cfg = CFG[market]
    r, costs = cfg["risk"], make_cost_model(cfg["costs"])
    st = account(equity * cash_frac, equity)
    c = Candidate("AAA", price, price * stop_frac, 1.0, 0.1, "x", strategy="trend200", gate_r=gate, lift=lift)
    d = risk.evaluate(st, c, equity, r, costs, 3.0, 0, 0)
    if not d.ok:
        return
    assert d.qty > 0 and d.qty == int(d.qty)                                         # whole shares
    assert d.planned_risk <= equity * r["risk_per_trade"] * (1 + 1e-9)                # at most 0.5% risked
    assert d.qty * price <= equity * r["max_position_pct"] * (1 + 1e-9)              # at most 10% in one share
    assert d.qty * price * (1 + costs.slippage) <= st.cash * 0.98 * (1 + 1e-9)        # never more than the free cash
    assert d.target_r is None or d.target_r - (gate or 3.0) <= 0.5 + 1e-6            # a target is lifted by 0.5R at most


@FAST
@given(price=s.floats(1, 5000), above=s.floats(0, 0.5))
def test_a_stop_at_or_above_the_price_is_never_bought(price, above):
    cfg = CFG["us"]
    c = Candidate("AAA", price, price * (1 + above), 1.0, 0.1, "x")
    assert not risk.evaluate(account(1e6, 1e6), c, 1e6, cfg["risk"], make_cost_model(cfg["costs"]), 3.0, 0, 0).ok


@FAST
@given(held=s.lists(s.sampled_from(["longboard", "trend200", "bollinger", "flipper", "sepa"]), max_size=6),
       new=s.sampled_from(["longboard", "trend200", "bollinger", "flipper", "sepa", "gem"]))
def test_slots_are_never_exceeded(held, new):
    cfg = CFG["us"]
    r = dict(cfg["risk"], max_positions=5, per_strategy_max=1, symbol_cooldown_days=0)
    st = account(1e7, 1e7)
    for i, k in enumerate(held):
        st.positions[f"H{i}"] = Position(f"H{i}", 1, "2026-01-02", 100.0, 90.0, 90.0, 10.0, None, 0.0, "r", strategy=k)
    d = risk.evaluate(st, Candidate("NEW", 100.0, 95.0, 1.0, 0.1, "x", strategy=new, gate_r=3.0), 1e7, r,
                      make_cost_model(cfg["costs"]), 3.0, 0, 0)
    if d.ok:
        assert len(held) < 5 and new not in held                                     # a free seat and a strategy without one


@FAST
@given(open_=s.floats(1, 5000), move=s.floats(-0.3, 0.3), stop_frac=s.floats(0.5, 0.99), cash=s.floats(0, 1e6),
       qty=s.integers(1, 10_000), limit=s.one_of(s.none(), s.floats(0.8, 1.2)))
def test_a_fill_never_leaves_negative_cash_or_a_stop_above_the_buy(open_, move, stop_frac, cash, qty, limit):
    cfg = CFG["india"]
    br = VirtualBroker(make_cost_model(cfg["costs"]), target_r=3.0)
    st = account(cash, cash or 1)
    lim = open_ * limit if limit else None
    o = Order(1, "AAA", "BUY", qty, "2026-01-01", "x", stop=open_ * stop_frac, est_price=open_, planned_risk=None,
              limit=lim, stop_dist=open_ * (1 - stop_frac) if lim else None)
    low = min(open_, open_ * (1 + move))
    pos, _ = br.fill_entry(st, o, SimpleNamespace(Open=open_, High=max(open_, open_ * (1 + move)), Low=low,
                                                  Close=open_ * (1 + move)), pd.Timestamp("2026-01-02"), False)
    assert st.cash >= -1e-6                                                          # never spends money it does not have
    if pos is not None:
        assert pos.stop < pos.entry_price and pos.risk_per_share > 0 and pos.qty >= 1
        if lim is not None:
            assert pos.entry_price <= lim * (1 + 0.01)                                  # a limit buy never pays above its limit (plus slippage)


@FAST
@given(path=s.lists(s.floats(-0.04, 0.04), min_size=1, max_size=120))
def test_a_five_percent_drop_always_halts(path):
    r = CFG["us"]["risk"]
    st, eq, peak, worst = account(100_000, 100_000), 100_000.0, 100_000.0, 0.0
    for i, ch in enumerate(path):
        eq *= 1 + ch
        peak, worst = max(peak, eq), min(worst, eq / max(peak, eq) - 1)
        risk.update_counters(st, pd.Timestamp("2026-01-05") + pd.Timedelta(days=i), eq, r, "sim")
    if worst <= -r["max_drawdown"]:
        assert st.halted                                                             # the full stop is never skipped
