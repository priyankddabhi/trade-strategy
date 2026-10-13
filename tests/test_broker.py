from collections import namedtuple

import pandas as pd
import pytest

from tbot.broker import VirtualBroker
from tbot.costs import make_cost_model
from tbot.models import Order, Position, SimState

Bar = namedtuple("Bar", "Open High Low Close")
DAY = pd.Timestamp("2024-03-05")


@pytest.fixture
def broker(us_cfg):
    return VirtualBroker(make_cost_model(us_cfg["costs"]), target_r=3.0, max_gap_mult=1.5)


def state():
    return SimState(market="us", initial_capital=100_000, cash=100_000, start_date="2024-01-01")


def held(stop=95.0, target=115.0):
    return Position("AAA", 100, "2024-03-01", 100.0, stop, stop, 5.0, target, 1.0, "test",
                    highest_close=100.0, last_close=100.0)


def test_gap_below_stop_exits_at_open(broker):
    st = state()
    st.positions["AAA"] = held()
    t = broker.check_exit(st, st.positions["AAA"], Bar(90, 92, 89, 91), DAY, entered_today=False)
    assert t.exit_reason == "Gapped below the stop"
    assert t.exit_price == pytest.approx(90 * (1 - broker.slip))


def test_stop_wins_when_stop_and_target_same_day(broker):
    st = state()
    st.positions["AAA"] = held()
    t = broker.check_exit(st, st.positions["AAA"], Bar(100, 120, 94, 110), DAY, entered_today=False)
    assert t.exit_reason == "Stop-loss hit"


def test_target_hit(broker):
    st = state()
    st.positions["AAA"] = held()
    t = broker.check_exit(st, st.positions["AAA"], Bar(110, 116, 109, 115), DAY, entered_today=False)
    assert t.exit_reason == "Target hit" and t.r_multiple > 2.5


def order(stop=95.0, qty=100, planned=500.0):
    return Order(1, "AAA", "BUY", qty, "2024-03-04", "test", stop=stop, est_price=100.0, planned_risk=planned)


def test_entry_fills_at_next_open_with_costs(broker):
    st = state()
    pos, msg = broker.fill_entry(st, order(), Bar(101, 103, 100, 102), DAY, blackout=False)
    assert pos.entry_price == pytest.approx(101 * (1 + broker.slip))
    assert st.cash == pytest.approx(100_000 - 100 * pos.entry_price - pos.entry_costs)
    assert pos.target == pytest.approx(pos.entry_price + 3 * (pos.entry_price - 95))


def test_entry_cancelled_when_open_below_stop(broker):
    pos, msg = broker.fill_entry(state(), order(), Bar(94, 96, 93, 95), DAY, blackout=False)
    assert pos is None and "below the stop" in msg


def test_entry_cancelled_on_big_gap_up(broker):
    pos, msg = broker.fill_entry(state(), order(), Bar(104, 106, 103, 105), DAY, blackout=False)
    assert pos is None and "gap up" in msg            # risk 9 per share vs plan 5


def test_entry_cancelled_on_blackout(broker):
    pos, msg = broker.fill_entry(state(), order(), Bar(101, 103, 100, 102), DAY, blackout=True)
    assert pos is None and "blackout" in msg
