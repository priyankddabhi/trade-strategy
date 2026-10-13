import pandas as pd
import pytest

from tbot import web
from tbot.models import Position, SimState
from tbot.store import Store

TODAY = pd.Timestamp.now(tz="America/New_York").strftime("%d %b")


@pytest.fixture
def acct(tmp_path, monkeypatch):
    st = Store(tmp_path / "sim.sqlite")
    a = SimState(market="us", initial_capital=5_000, cash=4_700, start_date="2026-10-07", last_date="2026-10-07",
                 peak_equity=5_000, prev_equity=5_000, week_start_equity=5_000)
    a.positions["ABBV"] = Position("ABBV", 1, "2026-10-08", 272.0, 260.0, 260.0, 12.0, 308.0, 1.0, "test")
    st.save_state("us", a)
    monkeypatch.setattr(web, "_store", st)
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    web._watch["us"].clear()
    return st, monkeypatch


def price(mp, p):
    mp.setattr(web, "fetch_quotes", lambda t: {s: (p, f"{TODAY} 11:00") for s in t})


def test_sells_at_once_when_stop_is_hit(acct):
    st, mp = acct; price(mp, 259.0)
    notes = web.watch_live("us", now=1000)
    s = st.load_state("us")
    assert "ABBV" not in s.positions and "stop-loss" in notes[0]
    assert st.trades("us").iloc[0]["exit_reason"] == "Stop-loss hit (live check)"


def test_sells_at_target(acct):
    st, mp = acct; price(mp, 309.0)
    web.watch_live("us", now=1000)
    assert "ABBV" not in st.load_state("us").positions


def test_gap_grows_while_rising_safely_then_snaps_back_on_risk(acct):
    st, mp = acct
    price(mp, 286.0); web.watch_live("us", now=0)            # +1.2R: stop moves to 272 (breakeven), cushion 1.17R
    assert st.load_state("us").positions["ABBV"].stop == 272.0
    w = web._watch["us"]["ABBV"]; assert w["every"] == 120
    price(mp, 290.0); web.watch_live("us", now=120); assert w["every"] == 180 and w["level"] == "safe"
    price(mp, 295.0); web.watch_live("us", now=300); assert w["every"] == 270
    price(mp, 296.0); assert web.watch_live("us", now=400) == [] and w["every"] == 270   # not due yet: no check
    price(mp, 280.0); web.watch_live("us", now=570)          # sharp drop, cushion 0.67R: risk
    assert w["every"] == 120 and w["level"] == "risk"


def test_never_waits_more_than_ten_minutes(acct):
    st, mp = acct; t = 0
    for p in range(290, 320, 2):
        if p >= 308: break
        price(mp, float(p)); web.watch_live("us", now=t); t += web._watch["us"]["ABBV"]["every"]
    assert web._watch["us"]["ABBV"]["every"] <= 600


def test_market_closed_does_nothing(acct):
    st, mp = acct; mp.setattr(web, "market_open_now", lambda cfg: False); price(mp, 200.0)
    assert web.watch_live("us", now=0) == [] and "ABBV" in st.load_state("us").positions
