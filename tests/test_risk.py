import pandas as pd

from tbot import risk
from tbot.costs import make_cost_model
from tbot.models import Position, SimState, Trade
from tbot.strategy import Candidate


def state(cash=100_000.0):
    return SimState(market="us", initial_capital=cash, cash=cash, start_date="2024-01-01",
                    peak_equity=cash, prev_equity=cash, week_start_equity=cash)


def cand(price=100.0, stop=95.0, symbol="AAA"):
    return Candidate(symbol, price, stop, 2.5, 0.1, "test")


def test_size_risks_half_percent(us_cfg):
    r, costs = us_cfg["risk"], make_cost_model(us_cfg["costs"])
    d = risk.evaluate(state(), cand(100, 95), 100_000, r, costs, 3.0, 0, 0)
    assert d.ok
    assert d.qty == 100                                 # 500 risk / 5 per share
    assert d.planned_risk == 500


def test_size_capped_at_ten_percent(us_cfg):
    r, costs = us_cfg["risk"], make_cost_model(us_cfg["costs"])
    d = risk.evaluate(state(), cand(100, 99.5), 100_000, r, costs, 3.0, 0, 0)
    assert d.ok and d.qty == 100                        # 10% of 100k at 100


def test_max_positions(us_cfg):
    r, costs = us_cfg["risk"], make_cost_model(us_cfg["costs"])
    st = state()
    n = r["max_positions"]
    for i in range(n):
        st.positions[f"S{i}"] = Position(f"S{i}", 1, "2024-01-01", 10, 9, 9, 1, None, 0, "")
    d = risk.evaluate(st, cand(), 100_000, r, costs, 3.0, 0, 0)
    assert not d.ok and f"Max {n} positions" in d.reason


def test_reward_risk_after_costs_rejects_tiny_stop(india_cfg):
    r, costs = india_cfg["risk"], make_cost_model(india_cfg["costs"])
    d = risk.evaluate(state(1_000_000), cand(1000, 999), 1_000_000, r, costs, 3.0, 0, 0)
    assert not d.ok and "Reward:risk" in d.reason


def test_daily_weekly_and_drawdown_limits(us_cfg):
    r = us_cfg["risk"]
    st = state()
    day = pd.Timestamp("2024-03-04")
    risk.update_counters(st, day, 98_900, r, "sim")         # -1.1% in a day
    assert "Daily loss" in risk.account_block(st, r)
    risk.update_counters(st, day + pd.Timedelta(days=1), 97_950, r, "sim")   # -0.96% day, -2.05% week
    assert "Weekly loss" in risk.account_block(st, r)
    risk.update_counters(st, day + pd.Timedelta(days=2), 94_900, r, "sim")   # -5.1% from the peak
    assert st.halted and "Halted" in risk.account_block(st, r)


def test_loss_streak_pause(us_cfg):
    r = us_cfg["risk"]
    st = state()
    loss = Trade("A", 1, "d", 10, "d", 9, 0, -1, -1, "stop", "")
    notes = []
    for _ in range(4):
        notes += risk.record_trade(st, loss, r)
    assert st.pause_sessions_left == 5 and notes


def test_backtest_halt_resumes_after_review_period(us_cfg):
    r = dict(us_cfg["risk"], backtest_halt_resume_days=2)
    st = state()
    risk.update_counters(st, pd.Timestamp("2024-03-04"), 94_000, r, "backtest")
    assert st.halted
    risk.start_of_session(st, "backtest")
    risk.start_of_session(st, "backtest")
    assert not st.halted and st.peak_equity == 94_000
