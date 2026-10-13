import json

import numpy as np
import pandas as pd

from tbot.engine import Engine, prepare_market_data, results_to_frames
from tbot.models import SimState
from tbot.strategy import TrendBreakout

from conftest import make_bars


def scenario(n=420, crash_at=None):
    """Index in a steady uptrend. AAA drifts up, then breaks out. BBB is flat."""
    idx = 100 * np.cumprod(np.full(n, 1.001))
    aaa = 50 * np.cumprod(np.full(n, 1.0005))
    aaa[300:] = aaa[299] * 1.03 * np.cumprod(np.full(n - 300, 1.006))   # breakout leg
    vol = np.full(n, 1_000_000.0)
    vol[300:] = 2_000_000.0
    if crash_at:
        aaa[crash_at:] = aaa[crash_at - 1] * 0.85                     # one-day crash, then flat
    bbb = np.full(n, 30.0)
    return {"^IDX": make_bars(idx), "AAA": make_bars(aaa, volume=vol, spread=0.002),
            "BBB": make_bars(bbb, spread=0.002)}


def build(cfg, frames, mode="backtest"):
    md = prepare_market_data(cfg, frames, TrendBreakout(cfg["strategy"], cfg["universe"]))
    return Engine(cfg, md, mode=mode), md


def test_breakout_buys_at_next_open(us_cfg):
    eng, md = build(us_cfg, scenario())
    dates = md.calendar()[250:]
    st = eng.new_state(dates[0])
    results = eng.run(st, dates)
    first_order = next(r for r in results if any(o.side == "BUY" for o in r.orders))
    fill_day = next(r for r in results if any(k == "fill" for k, _, _ in r.events))
    assert fill_day.date > first_order.date                    # never filled on the signal day
    bar = md.rows["AAA"][fill_day.date]
    pos_entry = [m for k, s, m in fill_day.events if k == "fill"][0]
    assert f"{bar.Open * 1.0002:.2f}" in pos_entry


def test_crash_exits_with_bounded_loss(us_cfg):
    eng, md = build(us_cfg, scenario(crash_at=303))           # AAA is held on day 303
    dates = md.calendar()[250:]
    st = eng.new_state(dates[0])
    _, trades, _ = results_to_frames(eng.run(st, dates))
    crash = trades[trades["exit_reason"].str.contains("stop", case=False)]
    assert len(crash) >= 1
    assert crash["r_multiple"].min() > -1.5                     # loss limited to about 1R
    assert st.equity() > st.initial_capital * 0.97             # 0.5% risk per trade keeps it small


def test_no_entries_when_index_below_average_and_bad_market_mode_off(us_cfg):
    frames = scenario()
    frames["^IDX"] = make_bars(100 * np.cumprod(np.full(420, 0.999)))
    us_cfg["regime"]["bad_market"]["allow"] = False                  # the old rule
    eng, md = build(us_cfg, frames)
    dates = md.calendar()[250:]
    results = eng.run(eng.new_state(dates[0]), dates)
    assert not any(r.orders for r in results)
    assert any("below its 200-day average" in m for r in results for _, _, m in r.events)


def test_bad_market_mode_buys_only_tiny_and_only_a_clear_leader(us_cfg):
    frames = scenario()                                               # AAA breaks out strongly from bar 300
    frames["^IDX"] = make_bars(100 * np.cumprod(np.full(420, 0.999)))   # index falling all the time
    us_cfg["capital"] = 25_000          # at $5,000 a 0.1% risk ($5) is eaten by the $1 commissions (2:1 rule says no)
    eng, md = build(us_cfg, frames)
    dates = md.calendar()[250:]
    results = eng.run(eng.new_state(dates[0]), dates)
    orders = [o for r in results for o in r.orders if o.side == "BUY"]
    assert orders and {o.symbol for o in orders} == {"AAA"}           # only the clear leader
    full_risk = 25_000 * us_cfg["risk"]["risk_per_trade"]
    assert all(o.planned_risk <= full_risk * 0.2 + 1 for o in orders)   # tiny: at most 0.1% risk
    assert any("Extra-safe mode" in r.regime for r in results)


def test_split_runs_equal_one_run(us_cfg):
    """Daily simulation (save state, reload, continue) must equal one backtest of the same days."""
    eng, md = build(us_cfg, scenario(crash_at=360))
    dates = md.calendar()[250:]
    st_a = eng.new_state(dates[0])
    _, trades_a, _ = results_to_frames(eng.run(st_a, dates))

    eng_b, _ = build(us_cfg, scenario(crash_at=360), mode="backtest")
    st_b = eng_b.new_state(dates[0])
    chunks = [dates[:60], dates[60:61], dates[61:120], dates[120:]]
    trades_b = []
    for chunk in chunks:
        st_b = SimState.from_dict(json.loads(json.dumps(st_b.to_dict())))   # like saving to SQLite
        part = results_to_frames(eng_b.run(st_b, chunk))[1]
        if len(part):
            trades_b.append(part)
    trades_b = pd.concat(trades_b, ignore_index=True)
    assert len(trades_a) >= 2
    pd.testing.assert_frame_equal(trades_a.reset_index(drop=True), trades_b, check_dtype=False)
    assert abs(st_a.equity() - st_b.equity()) < 1e-6
