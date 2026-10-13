"""The step animation rules of CLAUDE.md ("Step animations"). If one of these fails, a change broke a rule the user
set (Prompt 168): fix the change, not the test.

Step 1 = page step "Search whole market" = stage 2. Step 2 = "Search my shares" = stage 3.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from tbot import clock, stages, timemachine, web
from tbot.models import Order, Position, SimState

APP = Path(web.__file__).parent / "web" / "app.html"


@pytest.fixture
def normal_mode(monkeypatch):
    """Normal mode with the auto loop on for both markets, a clean Run state, the market open."""
    monkeypatch.setattr(web, "AUTO_ON", True)
    monkeypatch.setattr(web, "AUTO_MARKETS", ["india", "us"])
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=False, stage=stages.FINISHED, cancel=False, held=False))
    web._store = None
    yield
    web._store = None


def account(held=True, waiting=False):
    st = SimState(market="us", initial_capital=25_000, cash=20_000, start_date="2026-10-01", last_date="2026-10-06",
                  peak_equity=25_000, prev_equity=25_000, week_start_equity=25_000)
    if held:
        st.positions["AAPL"] = Position("AAPL", 20, "2026-10-02", 250.0, 240.0, 240.0, 10.0, 280.0, 1.0, "r",
                                        highest_close=250.0, last_close=250.0)
    if waiting:
        st.orders.append(Order(1, "MSFT", "BUY", 2, "2026-10-06", "fp", stop=90.0, est_price=96.0, planned_risk=12.0))
    web.store().save_state("us", st)
    return st


def modes():
    m = web.step_modes("us")
    return m[2]["mode"], m[3]["mode"]


@pytest.fixture
def trip(monkeypatch):
    """Time machine on, driving the US market, on a Tuesday 11:00 New York time (market open on the trip clock)."""
    monkeypatch.setattr(web, "AUTO_ON", False)                  # the real-time loop plays no part in a trip
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=False, stage=stages.FINISHED, cancel=False, held=False))
    web._store = None
    clock.start(pd.Timestamp("2025-10-14 11:00", tz="America/New_York").timestamp(), 1)
    timemachine._world["markets"] = {"us"}
    yield
    timemachine._world.clear()
    clock.stop()
    web._store = None


# 1 ------------------------------------------------------------------------------------------------------------------
def test_1_step1_shows_the_ring_on_the_first_startup(normal_mode):
    web._runs["us"].update(running=True, stage=2)               # first Run: no account yet, loading prices
    assert modes() == (None, None)                              # no reactor: the step's own state (the ring) shows
    s = {x["n"]: x["status"] for x in web.simple_view("us")["stages"]}
    assert s[2] == "running"                                    # the ring
    account()
    web._runs["us"].update(running=True, stage=3)               # a later Run's first look: still the ring, not the reactor
    assert modes() == (None, None)


# 2 ------------------------------------------------------------------------------------------------------------------
def test_2_step1_shows_the_reactor_throughout_ongoing_monitoring(normal_mode):
    account()
    web._runs["us"].update(running=True, stage=4)               # first look done, the Run goes on (test, trading)
    assert modes()[0] == "reactor"
    web._runs["us"].update(running=False, stage=stages.FINISHED)
    assert modes()[0] == "reactor"
    assert web.step_modes("us")[2]["pace"] == "live"


# 3 ------------------------------------------------------------------------------------------------------------------
def test_3_step1_has_no_reactor_when_genuinely_stopped(normal_mode, monkeypatch):
    assert modes()[0] is None                                   # no account: nothing is scheduled
    account()
    monkeypatch.setattr(web, "AUTO_ON", False)                  # auto checks off
    assert modes()[0] is None
    monkeypatch.setattr(web, "AUTO_ON", True)
    monkeypatch.setattr(web, "AUTO_MARKETS", ["india"])         # this market is not watched
    assert modes()[0] is None


# 4 ------------------------------------------------------------------------------------------------------------------
def test_4_step2_shows_the_reactor_when_shares_are_held(normal_mode):
    account(held=True)
    assert modes()[1] == "reactor"


# 5 ------------------------------------------------------------------------------------------------------------------
def test_5_step2_shows_the_reactor_for_shares_step1_chose_that_wait_to_be_bought(normal_mode):
    account(held=False, waiting=True)
    assert modes()[1] == "reactor"


# 6 ------------------------------------------------------------------------------------------------------------------
def test_6_step2_has_no_reactor_without_shares_to_watch(normal_mode):
    account(held=False, waiting=False)
    assert modes() == ("reactor", None)                         # step 1 still repeats; step 2 has nothing to watch


# 7 ------------------------------------------------------------------------------------------------------------------
def test_7_the_rules_work_in_the_time_machine_on_its_own_clock(trip, monkeypatch):
    account(held=True)
    assert modes() == ("reactor", "reactor")                    # the trip runs this market after each close
    assert web.step_modes("us")[2]["pace"] == "live"            # Tuesday 11:00 New York on the trip clock
    clock.pause(True)
    assert modes() == (None, None)                              # paused trip: nothing scheduled
    clock.pause(False)
    timemachine._world["markets"] = {"india"}                   # the trip drives another market
    assert modes() == (None, None)
    timemachine._world["markets"] = {"us"}
    clock.start(pd.Timestamp("2025-10-12 11:00", tz="America/New_York").timestamp(), 1)   # a Sunday on the trip clock
    assert web.step_modes("us")[2]["pace"] == "standby"


# 8 ------------------------------------------------------------------------------------------------------------------
def test_8_the_reactor_does_not_disappear_when_the_run_completes(normal_mode):
    account(held=True)
    web._runs["us"].update(running=True, stage=5)
    during = modes()
    web._runs["us"].update(running=False, stage=stages.FINISHED, finished="12:00:00")
    assert during == modes() == ("reactor", "reactor")


def test_8b_the_page_only_draws_what_the_server_says():
    """One source of truth: looping() in app.html reads d.modes and nothing else (no guessing on the page)."""
    page = APP.read_text(encoding="utf-8")
    body = re.search(r"function looping\(n, d\) \{(.*?)\n\}", page, re.S).group(1)
    code = "\n".join(line.split("//")[0] for line in body.splitlines())
    assert "d.modes" in code
    assert not re.search(r"d\.(started|positions|run|sim|auto_on|monitor|open|orders)\b", code)


# 9 ------------------------------------------------------------------------------------------------------------------
def test_9_a_stopped_run_does_not_bring_the_reactor_back(normal_mode, monkeypatch):
    account(held=True)
    web._runs["us"].update(running=True, stage=4)
    web.stop_run("us")                                          # Stop pressed
    web._runs["us"].update(running=False, stage=stages.FINISHED, cancel=False)
    assert modes() == (None, "reactor")                         # step 1 stops; held shares are still watched live
    assert not web._auto_start_run("us")                        # the trip or auto run does not start it again
    monkeypatch.setattr(web, "_run_daily", lambda m: None)
    assert web._start_run("us")                                 # Run bot clears the stop
    web._runs["us"].update(running=False, stage=stages.FINISHED)
    assert modes() == ("reactor", "reactor")


def test_9b_after_stop_in_the_time_machine_nothing_is_scheduled(trip):
    account(held=True, waiting=True)
    web._runs["us"].update(held=True)                           # Stop pressed inside the trip
    assert modes() == (None, None)                              # the trip starts no Run, so nothing checks the shares


# 10 -----------------------------------------------------------------------------------------------------------------
def test_10_the_page_still_has_the_ring_reactor_and_tick_and_stop():
    page = APP.read_text(encoding="utf-8")
    for part in ('const REACTOR', '.step.running .node::after', 'ICON = {done: "✓"', 'id="stoprun"', '.step.loop.kick .reactor'):
        assert part in page
