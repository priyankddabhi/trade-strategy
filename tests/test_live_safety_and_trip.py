"""Prompt 173: a live buy passes the same safety checks as the trading part; the Run summary is written after the
live buy; the time machine clock waits while the trip's own Run works."""
import time

import pandas as pd
import pytest

from tbot import clock, timemachine, web
from tbot.config import load_market

from test_engine import build, scenario
from test_live_buy import quote, setup  # noqa: F401  (fixture)


@pytest.fixture
def open_market(monkeypatch):
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(272.0))


def test_a_live_buy_waits_while_the_daily_loss_stop_is_hit(setup, open_market):
    client, st = setup
    s = st.load_state("us"); s.day_return = -0.02; st.save_state("us", s)
    msg = client.post("/api/market/us/buy-now").json()["message"]
    assert "blocked" in msg and "Daily loss" in msg
    assert len(st.load_state("us").orders) == 1 and not st.load_state("us").positions   # still waiting, nothing bought


def test_a_live_buy_waits_during_the_losing_streak_pause(setup, open_market):
    client, st = setup
    s = st.load_state("us"); s.pause_sessions_left = 2; st.save_state("us", s)
    assert "Loss-streak pause" in client.post("/api/market/us/buy-now").json()["message"]
    assert not st.load_state("us").positions


def test_a_live_buy_is_cancelled_on_a_news_day(setup, open_market, monkeypatch):
    client, st = setup
    today = clock.now(load_market("us")["timezone"]).tz_localize(None).normalize()
    monkeypatch.setattr(web.config, "load_events", lambda *a: {"us": {today}, "india": set()})
    msg = client.post("/api/market/us/buy-now").json()["message"]
    assert "event blackout" in msg and not st.load_state("us").positions and not st.load_state("us").orders


def test_a_live_buy_still_works_on_a_normal_day(setup, open_market):
    client, st = setup
    assert "Bought live" in client.post("/api/market/us/buy-now").json()["message"]


def test_the_run_summary_is_written_after_the_live_buy(setup, open_market, monkeypatch):
    client, st = setup

    def daily(markets, telegram=True, progress=None, gate=None, **kw):   # the trading part processed a new day
        s = st.load_state("us"); s.last_date = "2026-10-08"; st.save_state("us", s)
    monkeypatch.setattr(web.bot, "daily", daily)
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=True, cancel=False, held=False))
    web._run_daily("us")
    assert "1 shares held, 0 buy orders" in web._runs["us"]["summary"]


@pytest.fixture
def trip_cleanup():
    yield
    if clock.active():
        timemachine.exit_(lambda: None)
    clock.stop()


def test_the_trip_clock_waits_while_its_own_run_works(us_cfg, monkeypatch, trip_cleanup):
    eng, md = build(us_cfg, scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    busy_until, runs = {"t": 0.0}, []

    def start_run(m):
        runs.append(m); busy_until["t"] = time.time() + 2.0
        return True
    timemachine.start("us", str(cal[300].date()), 86400, ["us"], start_run, lambda: None,
                      busy=lambda: time.time() < busy_until["t"])
    for _ in range(50):
        if runs:
            break
        time.sleep(0.1)
    assert runs
    a = clock.epoch(); time.sleep(1.0); b = clock.epoch()
    assert a == b                                                    # no trip day passes while its Run works
    assert timemachine.status("us")["run_pause"] and timemachine.drives("us")   # its work is still scheduled
    time.sleep(2.5)
    assert clock.epoch() > b                                         # then the trip goes on by itself
    timemachine.control("pause", None, lambda: None)                 # your Pause wins: it stays paused
    busy_until["t"] = 0
    c = clock.epoch(); time.sleep(1.5)
    assert clock.epoch() == c and not timemachine.status("us")["run_pause"]
