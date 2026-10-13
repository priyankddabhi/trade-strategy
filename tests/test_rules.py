"""The lab strategies run together (tbot/rules.py): each share remembers its rule and is sold by it."""
import ast
import copy
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tbot import risk, rules, settings
from tbot.broker import VirtualBroker
from tbot.config import load_market
from tbot.costs import make_cost_model
from tbot.engine import Engine, prepare_market_data
from tbot.models import Position, SimState
from tbot.rules import Combo, period_ends
from tbot.strategy import Candidate, TrendBreakout, make_strategy

from conftest import make_bars
from test_engine import scenario

ROOT = Path(__file__).resolve().parent.parent


def combo_cfg(*keys):
    cfg = copy.deepcopy(load_market("us"))
    cfg["universe"].update(symbols=["AAA", "BBB"], etfs=[], min_price=1, min_avg_value=0)
    cfg["regime"].update(index="^IDX", vix=None)
    cfg["strategy"]["use"] = {k: True for k in keys}
    return cfg


def state(cash=100_000.0):
    return SimState(market="us", initial_capital=cash, cash=cash, start_date="2024-01-01",
                    peak_equity=cash, prev_equity=cash, week_start_equity=cash)


def test_each_market_has_its_own_list_and_the_bot_uses_all_of_them():
    us, india = load_market("us"), load_market("india")
    assert [k for k, on in us["strategy"]["use"].items() if on] == rules.US + ["trend_breakout"]
    assert [k for k, on in india["strategy"]["use"].items() if on] == rules.INDIA + ["trend_breakout"]
    assert not us["strategy"]["use"]["fear_pullback"] and not india["strategy"]["use"]["sepa"]
    assert isinstance(make_strategy(us), Combo) and len(make_strategy(us).keys) == 20
    assert isinstance(make_strategy(combo_cfg()), TrendBreakout)          # nothing ticked: the trend breakout alone
    assert len(set(rules.ALL)) == 21 and "mean_rev_short" not in rules.ALL    # short selling never


def test_settings_show_one_yes_no_per_strategy_with_the_market_default():
    us = {f["key"]: f for f in settings.view("us")}
    india = {f["key"]: f for f in settings.view("india")}
    assert us["strategy.use.sepa"]["default"] is True and india["strategy.use.sepa"]["default"] is False
    assert us["strategy.use.sepa"]["group"] == "Strategies" and us["strategy.use.sepa"]["value"] is True
    settings.save("india", {"strategy.use.bollinger": False})
    assert load_market("india")["strategy"]["use"]["bollinger"] is False
    with pytest.raises(ValueError):
        settings.save("india", {"strategy.use.bollinger": 1})
    settings.reset("india")
    assert load_market("india")["strategy"]["use"]["bollinger"] is True
    assert load_market("india")["strategy"]["use"]["sepa"] is False


def test_the_rule_that_bought_a_share_also_sells_it():
    cfg = combo_cfg("hundred_day_high")
    md = prepare_market_data(cfg, scenario(), make_strategy(cfg))
    eng = Engine(cfg, md, set(), mode="backtest")
    dates = md.calendar()[250:]
    st = eng.new_state(dates[0])
    results = eng.run(st, dates)
    order = next(o for r in results for o in r.orders if o.side == "BUY")
    assert order.strategy == "hundred_day_high" and order.target_r is not None and order.target_r >= 2.0
    assert order.reason.startswith("100-Day High:")
    trades = [t for r in results for t in r.trades]
    held = list(st.positions.values())
    assert all(t.strategy == "hundred_day_high" for t in trades) and all(p.strategy == "hundred_day_high" for p in held)


def test_a_two_r_target_is_lifted_a_little_so_it_pays_after_costs():
    cfg = load_market("india")
    r, costs = dict(cfg["risk"]), make_cost_model(cfg["costs"])
    c = Candidate("AAA", 1000.0, 980.0, 10, 0.1, "t", strategy="trend200", target_r=2.0, gate_r=2.0, lift=True)
    d = risk.evaluate(state(1_000_000), c, 1_000_000, r, costs, 3.0, 0, 0)
    assert d.ok and 2.0 < d.target_r <= 2.5
    c.lift = False
    assert not risk.evaluate(state(1_000_000), c, 1_000_000, r, costs, 3.0, 0, 0).ok
    far = Candidate("AAA", 1000.0, 999.0, 10, 0.1, "t", strategy="trend200", target_r=2.0, gate_r=2.0, lift=True)
    assert not risk.evaluate(state(1_000_000), far, 1_000_000, r, costs, 3.0, 0, 0).ok   # needs more than +0.5R: no


def test_fear_pullback_buys_only_at_its_limit_and_measures_the_stop_from_the_fill():
    costs = make_cost_model(load_market("us")["costs"])
    br = VirtualBroker(costs, target_r=3.0)
    st, day = state(), pd.Timestamp("2024-01-02")
    o = br.new_order(st, "BUY", "AAA", 10, day, "fp", stop=90.0, est_price=96.0, strategy="fear_pullback",
                     target_r=0.0, limit=96.0, stop_dist=5.0)
    pos, msg = br.fill_entry(st, o, SimpleNamespace(Open=99.0, High=100.0, Low=97.0, Close=98.0), day, False)
    assert pos is None and "not reached" in msg
    pos, _ = br.fill_entry(st, o, SimpleNamespace(Open=99.0, High=100.0, Low=95.0, Close=97.0), day, False)
    assert pos.entry_price == pytest.approx(96.0 * (1 + costs.slippage)) and pos.stop == 91.0
    assert pos.target is None and pos.strategy == "fear_pullback"


def test_own_exits_time_exits_and_trailing_stops():
    cb = Combo({**load_market("us")["strategy"], "use": {k: True for k in rules.ALL}}, {"min_price": 0, "min_avg_value": 0})
    pos = Position("AAA", 10, "2024-01-02", 100.0, 95.0, 95.0, 5.0, None, 1.0, "r", strategy="mean_reversion",
                   highest_close=100.0, last_close=100.0)
    bar = SimpleNamespace(Open=100, High=101, Low=99, Close=100, atr=2.0, atr20w=2.0, atr40w=1.0,
                          **{f"x_{k}": False for k in rules.ALL})
    for _ in range(2):
        cb.manage(pos, bar)
        assert cb.exit_reason(pos, bar) is None
    cb.manage(pos, bar)
    assert "Mean Reversion" in cb.exit_reason(pos, bar)                 # out at the next open, as the lab did
    pos.strategy, pos.bars_held = "bollinger", 1
    assert cb.exit_reason(pos, SimpleNamespace(**{**vars(bar), "x_bollinger": True})).startswith("Bollinger")
    pos.strategy, pos.highest_high = "longboard", 0.0
    cb.manage(pos, SimpleNamespace(**{**vars(bar), "High": 120.0}))
    assert pos.stop == pytest.approx(120.0 - 10 * 1.0)                  # trails the highest high by 10 ATR(40)
    pos.strategy = "fear_pullback"
    assert "3% above" in cb.exit_reason(pos, SimpleNamespace(**{**vars(bar), "Close": 103.5}))


def test_reviews_fall_on_the_last_session_of_the_week_and_of_march_and_september():
    days = pd.bdate_range("2024-03-25", "2024-04-05")
    week, half = period_ends(days)
    assert [d.day_name() for d in days[week]] == ["Friday", "Friday"]
    assert list(days[half]) == [pd.Timestamp("2024-03-29")]


def lab(path, names):
    from src import indicators as ind     # the lab's own indicators
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    g = dict(np=np, pd=pd, math=math, ind=ind)
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names],
                            type_ignores=[]), str(path), "exec"), g)
    return g


@pytest.mark.parametrize("market,index", [("us", "_GSPC.csv"), ("india", "_NSEI.csv")])
def test_every_rule_matches_the_lab_code_on_real_prices(market, index):
    files = sorted(p for p in (ROOT / "data" / market).glob("*.csv") if not p.name.startswith("_"))[:6]
    if not files or not (ROOT / "research/lab/lab.py").exists():
        pytest.skip("no real price files here")
    sys.path.insert(0, str(ROOT / "research" / "books_lab"))
    L1 = lab(ROOT / "research/lab/lab.py", {"index_info", "pmax", "pmin", "rolling_adj_slope", "make_signals"})
    L2 = lab(ROOT / "research/lab/lab2.py", {"new_signals"})
    load = lambda p: pd.read_csv(p, parse_dates=["Date"], index_col="Date").sort_index().dropna(subset=["Close"])[  # noqa: E731
        ["Open", "High", "Low", "Close", "Volume"]]
    low = lambda d: d.rename(columns=str.lower)  # noqa: E731
    names = {"SEPA": "sepa", "TurtleS2": "turtle_s2", "AbsoluteMom": "absolute_momentum", "RelativeMom": "relative_momentum",
             "GEM": "gem", "DualMomentum": "dual_momentum", "NewYearlyHigh": "new_yearly_high", "GoldenCross": "golden_cross",
             "TrendPilot": "trendpilot", "HundredDayHigh": "hundred_day_high", "ClenowTF": "clenow_tf",
             "Momentum": "clenow_momentum", "MeanReversion": "mean_reversion", "Trend200": "trend200",
             "Flipper": "flipper", "Bollinger": "bollinger", "FearPullback": "fear_pullback"}
    idx = load(ROOT / "data" / market / index)
    cb = Combo({**load_market(market)["strategy"], "use": {k: True for k in rules.ALL}}, {"min_price": 0, "min_avg_value": 0})
    cb.bind_index(idx)
    I = L1["index_info"](low(idx))

    def same(a, b):
        a, b = np.asarray(a, float), np.asarray(b, float)
        return bool(np.all((np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9 * np.maximum(1, np.abs(b)))))

    for f in files:
        df = load(f)
        mine, out = cb.prepare(df), {**L1["make_signals"](low(df), I), **L2["new_signals"](low(df))}
        for ln, k in names.items():
            r, sig = out[ln], out[ln]["sig"]
            assert same(r["sig"], mine[f"s_{k}"]), (f.stem, ln)
            assert same(r["score"][sig], mine[f"sc_{k}"].to_numpy()[sig]), (f.stem, ln)
            if ln == "FearPullback":
                assert same(r["limit"], mine["fp_limit"]) and same(2.5 * r["atr10"], mine["fp_dist"])
            else:
                assert same(r["stop"][sig], mine[f"st_{k}"].to_numpy()[sig]), (f.stem, ln)
            if r.get("exit") is not None:
                assert same(r["exit"], mine[f"x_{k}"]), (f.stem, ln)
