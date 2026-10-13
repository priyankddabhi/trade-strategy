"""The run cycle of workflow-v2: open = read only, test gate before buys, search effort, safe switching, safe reset."""
import json
import os
import sqlite3
import threading
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tbot import app as bot
from tbot import clock, config, data, profiles, settings, stages, timemachine, web
from tbot.engine import Engine, prepare_market_data
from tbot.models import Order, Position, SimState, Trade
from tbot.store import Store

from test_engine import build, scenario
from test_stages import passport


@pytest.fixture
def account():
    st = SimState(market="us", initial_capital=25_000, cash=20_000, start_date="2026-10-01", last_date="2026-10-06",
                  peak_equity=25_000, prev_equity=25_000, week_start_equity=25_000)
    st.positions["AAPL"] = Position("AAPL", 20, "2026-10-02", 250.0, 240.0, 240.0, 10.0, 280.0, 1.0, "r",
                                    highest_close=250.0, last_close=250.0)
    web._store = None
    web.store().save_state("us", st)
    yield st
    web._store = None


@pytest.fixture
def real_time_after():
    yield
    if clock.active():
        timemachine.exit_(web._tm_switch)
    clock.stop()


# ---------------------------------------------------------------- 1. opening the website only reads
def test_opening_the_page_starts_no_run_no_test_and_no_order(account, monkeypatch):
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {})
    started = []
    monkeypatch.setattr(web, "_start_run", lambda m: started.append(("run", m)))
    monkeypatch.setattr(web, "_start_test", lambda m: started.append(("test", m)))
    c = TestClient(web.api)                            # no "with": the auto loop is not started
    d = c.get("/api/simple/us").json()
    c.get("/api/simple/india")
    c.get("/api/profiles")
    assert started == [] and d["profile"] == profiles.active()
    assert d["monitor"]["text"] and "Run bot" in d["monitor"]["text"]       # the real monitoring state is reported
    assert len(web.store().load_state("us").orders) == 0


def test_the_time_machine_runs_only_the_market_it_was_started_for(account, monkeypatch, real_time_after):
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {})
    timemachine.start("us", str(cal[300].date()), 1, ["us"], lambda m: True, web._tm_switch)
    TestClient(web.api).get("/api/simple/india")      # only looking at India
    assert timemachine._world["markets"] == {"us"}


def load_us():
    cfg = config.load_market("us")
    cfg["universe"].update(symbols=["AAA", "BBB"], etfs=[], min_price=1, min_avg_value=0)
    cfg["regime"].update(index="^IDX", vix=None)
    cfg["strategy"].pop("use", None)
    return cfg


# ---------------------------------------------------------------- 2. validation gate and market level in the engine
def test_a_failed_or_missing_test_blocks_new_buys_but_sells_still_happen(us_cfg):
    eng, md = build(us_cfg, scenario())
    dates = md.calendar()[250:]
    free = Engine(us_cfg, md, mode="sim")
    st = free.new_state(dates[0])
    assert any(o.side == "BUY" for r in free.run(st, dates) for o in r.orders)
    blocked = Engine(us_cfg, md, mode="sim", no_buys="Strategy not validated: test failed.")
    st = blocked.new_state(dates[0])
    st.positions["AAA"] = Position("AAA", 10, str(dates[0].date()), 60.0, 59.9, 59.9, 0.1, None, 0.0, "held",
                                   highest_close=60.0, last_close=60.0)
    res = blocked.run(st, dates)
    assert not any(o.side == "BUY" for r in res for o in r.orders)
    assert any("Strategy not validated" in m for r in res for k, _, m in r.events if k == "blocked")
    assert any(t.symbol == "AAA" for r in res for t in r.trades)          # the held share was still sold by its rules


def test_missing_or_old_fear_gauge_data_blocks_buys(us_cfg):
    eng, md = build(us_cfg, scenario())
    day = md.calendar()[-1]
    md.vix = pd.Series([15.0], index=[day - pd.Timedelta(days=30)])
    ok, why = md.regime(day)
    assert not ok and "missing" in why
    assert not md.only_below_trend(day) and md.level(day) == "normal"
    md.vix = pd.Series([15.0], index=[day])
    assert md.regime(day)[0]


def test_search_effort_follows_the_market_level(us_cfg):
    us_cfg["regime"]["scan"] = {"good": 20, "normal": 1}
    eng, md = build(us_cfg, scenario())
    dates = md.calendar()[250:]
    st = eng.new_state(dates[0])
    notes = [m for r in eng.run(st, dates) for k, _, m in r.events if k == "note" and m.startswith("Search:")]
    assert notes and all("compared the best" in n for n in notes)
    orders = [m for r in eng.run(eng.new_state(dates[0]), dates) for k, _, m in r.events if k == "order" and "Buy" in m]
    assert orders and "rank 1 of" in orders[0]


# ---------------------------------------------------------------- 3. one run at a time, nothing half done
def test_two_clicks_start_one_run_and_one_test(monkeypatch):
    monkeypatch.setattr(web, "_run_daily", lambda m: time.sleep(0.3))
    monkeypatch.setattr(web, "_run_test", lambda m: time.sleep(0.3))
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=False))
    monkeypatch.setitem(web._tests, "us", dict(web._tests["us"], running=False))
    got = []
    threads = [threading.Thread(target=lambda: got.append(web._start_run("us"))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert got.count(True) == 1
    got.clear()
    threads = [threading.Thread(target=lambda: got.append(web._start_test("us"))) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert got.count(True) == 1


def test_the_website_and_the_command_line_never_run_the_same_profile_at_once(tmp_path):
    with bot.run_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Another run"):
            with bot.run_lock(tmp_path):
                pass
    with bot.run_lock(tmp_path):                       # free again afterwards
        pass
    (tmp_path / ".run.lock").write_text("old")
    os.utime(tmp_path / ".run.lock", (time.time() - 4 * 3600,) * 2)
    with bot.run_lock(tmp_path):                       # a lock left by a crashed run is cleared
        pass


def test_a_price_refresh_reuses_bars_topped_up_minutes_ago(tmp_path, monkeypatch):
    from conftest import make_bars
    df = make_bars([10.0] * 30, start="2026-08-01")
    calls = []
    monkeypatch.setattr(data, "download", lambda tickers, start: calls.append(list(tickers)) or {t: df for t in tickers})
    data.refresh("us", ["AAA"], "2026-08-01", data_dir=tmp_path)            # first time: full download
    data.refresh("us", ["AAA"], "2026-08-01", data_dir=tmp_path)            # minutes later: no second download
    assert calls == [["AAA"]]
    meta = json.loads((tmp_path / "us" / "_meta.json").read_text())
    meta["_fresh"]["AAA"] = time.time() - 3600
    (tmp_path / "us" / "_meta.json").write_text(json.dumps(meta))
    data.refresh("us", ["AAA"], "2026-08-01", data_dir=tmp_path)            # an hour later: topped up again
    assert calls == [["AAA"], ["AAA"]]


def test_a_live_limit_buy_waits_for_its_price(account, monkeypatch):
    st = web.store().load_state("us")
    st.positions.clear()                               # room for one share
    st.orders.append(Order(1, "MSFT", "BUY", 2, "2026-10-06", "fp", stop=90.0, est_price=96.0, planned_risk=12.0,
                           strategy="fear_pullback", target_r=0.0, limit=96.0, stop_dist=5.0))
    web.store().save_state("us", st)
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    stamp = clock.now("America/New_York").strftime("%d %b %H:%M")
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {s: (99.0, stamp, 0.0) for s in syms})
    assert "still waiting" in web.buy_live("us")
    assert any(o.symbol == "MSFT" for o in web.store().load_state("us").orders)
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {s: (95.0, stamp, 0.0) for s in syms})
    assert "Bought live" in web.buy_live("us")
    p = web.store().load_state("us").positions["MSFT"]
    assert p.strategy == "fear_pullback" and p.stop == pytest.approx(95.0 - 5.0)


# ---------------------------------------------------------------- 4. time machine and profiles
def test_the_time_machine_has_its_own_budget_stop_switches_and_a_fresh_past_years_test(account, monkeypatch, real_time_after):
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    old = config.RUNS_DIR / "timemachine" / "passport"
    old.mkdir(parents=True)
    (old / "us.json").write_text("{}")                 # an earlier trip's test, made with later prices
    real_budget = settings.saved("us").get("capital")
    monkeypatch.setattr(web, "_start_run", lambda m: True)        # the trip's own runs are not part of this test
    gen = web._world_gen
    c = TestClient(web.api)
    assert c.post("/api/tm/us/start", json={"date": str(cal[300].date()), "speed": 1}).json()["ok"]
    assert web._world_gen > gen and not old.exists()
    assert config.stop_file_for("us").parent == config.STATE_DIR          # its stop switch lives in the trip
    assert c.post("/api/market/us/budget", json={"amount": 777}).json()["ok"]
    assert config.load_market("us")["capital"] == 777                   # the trip's own settings
    assert c.post("/api/tm/us/control", json={"action": "stop"}).json()["ok"]
    assert not clock.active() and config.load_market("us")["capital"] != 777
    assert settings.saved("us").get("capital") == real_budget              # the real settings never changed
    assert c.post("/api/tm/us/start", json={"date": "1900-01-01", "speed": 1}).status_code == 400
    assert (profiles.recall("tm") or {}).get("date") != "1900-01-01"      # a bad day is not remembered


def test_a_busy_bot_refuses_the_switch_and_keeps_the_time_machine(account, monkeypatch, real_time_after):
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    timemachine.start("us", str(cal[300].date()), 1, [], lambda m: True, web._tm_switch)
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=True))
    with pytest.raises(ValueError, match="still working"):
        web.switch_profile("p2")
    assert clock.active() and clock.info()["paused"]                       # paused, not thrown away
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=False))
    assert web.switch_profile("p2") and not clock.active() and profiles.active() == "p2"


def test_picking_the_profile_already_in_use_changes_nothing(account):
    web.note("us", "RUN", "kept")
    assert web.switch_profile(profiles.active())
    assert any(a["text"] == "kept" for a in web._activity["us"])


def test_the_command_line_can_pick_a_profile_without_changing_the_website(monkeypatch):
    profiles.use("p2")
    profiles.use("p3", remember=False)
    assert profiles.active() == "p2" and config.STATE_DIR == profiles.folders("p3")["state"]


def test_profiles_file_is_never_seen_half_written():
    profiles.rename("p1", "Main")
    stop = time.time() + 0.6
    seen = []

    def read():
        while time.time() < stop:
            seen.append(profiles.listing()["profiles"][0]["name"])
    t = threading.Thread(target=read)
    t.start()
    while time.time() < stop:
        profiles.remember("tm", {"date": "2020-01-02", "speed": 1})
    t.join()
    assert seen and set(seen) == {"Main"}


# ---------------------------------------------------------------- 5. reset and saved records
def test_reset_deletes_the_account_even_right_after_it_was_read(account):
    web.store().load_state("us")                       # files are closed at once, so Windows can delete them
    assert web.reset_profile(profiles.active())
    assert not (config.STATE_DIR / "sim.sqlite").exists() and web.store().load_state("us") is None


def test_a_reset_that_cannot_delete_a_file_says_so_clearly(monkeypatch):
    path = config.STATE_DIR / "sim.sqlite"
    Store().save_state("us", SimState(market="us", initial_capital=1, cash=1, start_date="2026-01-01"))
    monkeypatch.setattr(profiles.time, "sleep", lambda s: None)
    monkeypatch.setattr(type(path), "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(profiles.ResetError, match="press Reset again"):
        profiles.reset("p1")
    assert path.exists()                               # nothing pretends to be reset


def test_each_trade_saves_the_strategy_that_made_it_also_in_old_account_files(tmp_path):
    db = tmp_path / "sim.sqlite"
    with sqlite3.connect(db) as con:                   # an account file from before the lab strategies
        con.execute("CREATE TABLE trades (market TEXT, symbol TEXT, qty REAL, entry_date TEXT, entry_price REAL, "
                    "exit_date TEXT, exit_price REAL, costs REAL, pnl REAL, r_multiple REAL, exit_reason TEXT, entry_reason TEXT)")
    con.close()
    s = Store(db)
    st = SimState(market="us", initial_capital=1000, cash=1000, start_date="2026-01-01")
    s.save_state("us", st, trades=[Trade("AAA", 1, "2026-01-02", 10, "2026-01-05", 11, 0.1, 0.9, 0.5, "Target hit",
                                         "Bollinger", strategy="bollinger")])
    assert list(s.trades("us")["strategy"]) == ["bollinger"]


def test_stage_four_says_when_the_test_is_old_and_buys_wait(account):
    cfg = config.load_market("us")
    fp = stages.fingerprint(cfg)
    stages.save_passport("us", {**passport(1.5, -0.05, fp=fp), "end": "2025-01-02"})
    assert "ends 2025-01-02" in stages.validation_block(cfg, "us")
    d = TestClient(web.api).get("/api/simple/us").json()
    assert d["stages"][3]["title"].startswith("Test is out of date")
    stages.save_passport("us", passport(1.5, -0.05, fp=fp))
    assert stages.validation_block(cfg, "us") is None


# ---------------------------------------------------------------- 6. prompt 92 fixes
def test_a_live_or_manual_loss_counts_in_the_loss_streak(account, monkeypatch):
    st = web.store().load_state("us")
    st.loss_streak = 3                                 # three losses already
    web.store().save_state("us", st)
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    stamp = clock.now("America/New_York").strftime("%d %b %H:%M")
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {s: (235.0, stamp, 0.0) for s in syms})   # under the stop
    web._watch["us"].clear()
    notes = web.watch_live("us")
    st = web.store().load_state("us")
    assert "AAPL" not in st.positions and st.pause_sessions_left > 0 and any("losing trades" in n for n in notes)
    st.pause_sessions_left, st.loss_streak = 0, 3
    st.positions["MSFT"] = Position("MSFT", 2, "2026-10-02", 100.0, 90.0, 90.0, 10.0, None, 1.0, "r",
                                    highest_close=100.0, last_close=100.0)
    web.store().save_state("us", st)
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {s: (95.0, stamp, 0.0) for s in syms})
    assert "losing trades" in web.manual_sell("us", "MSFT")
    assert web.store().load_state("us").pause_sessions_left > 0


def test_a_fear_gauge_that_was_never_downloaded_blocks_buys(us_cfg):
    us_cfg["regime"]["vix"] = "^VIX"                   # wanted, but not in the downloaded prices
    eng, md = build(us_cfg, scenario())
    day = md.calendar()[-1]
    ok, why = md.regime(day)
    assert not ok and "missing" in why and md.level(day) == "normal"


def test_the_time_machine_keeps_its_own_settings_and_leaves_yours_alone(account, monkeypatch, real_time_after):
    settings.save("us", {"risk.max_new_per_day": 2})
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    monkeypatch.setattr(web, "_start_run", lambda m: True)
    c = TestClient(web.api)
    assert c.post("/api/tm/us/start", json={"date": str(cal[300].date()), "speed": 1}).json()["ok"]
    assert config.load_market("us")["risk"]["max_new_per_day"] == 2           # the trip starts with your settings
    assert c.post("/api/settings/us", json={"values": {"risk.max_new_per_day": 5}}).status_code == 200
    assert config.load_market("us")["risk"]["max_new_per_day"] == 5           # changed inside the trip
    assert c.post("/api/tm/us/control", json={"action": "stop"}).json()["ok"]
    assert config.load_market("us")["risk"]["max_new_per_day"] == 2           # yours are untouched


def test_the_command_line_run_tests_first_when_needed(monkeypatch):
    from tbot import study
    calls = []
    monkeypatch.setattr(study, "passport", lambda m: calls.append(m))
    assert "no past-years test" in bot.test_gate("us") and calls == ["us"]    # missing: tested first, still no buys
    cfg = config.load_market("us")
    stages.save_passport("us", passport(1.5, -0.05, fp=stages.fingerprint(cfg)))
    assert bot.test_gate("us") is None and calls == ["us"]                     # valid: reused, buys allowed


def test_kill_switch_and_halt_restart_work_from_the_page(account, monkeypatch):
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {})
    c = TestClient(web.api)
    assert c.get("/api/simple/us").json()["stopped"] is False
    assert c.post("/api/market/us/stop", json={"on": True}).json()["ok"]
    assert c.get("/api/simple/us").json()["stopped"] is True
    c.post("/api/market/us/stop", json={"on": False})
    st = web.store().load_state("us")
    st.halted, st.halt_reason = True, "Drawdown -5.10% hit the 5% limit"
    web.store().save_state("us", st)
    assert c.get("/api/simple/us").json()["halted"] is True
    assert c.post("/api/market/us/reset-halt").json()["ok"]
    assert c.get("/api/simple/us").json()["halted"] is False


def test_each_strategy_holds_at_most_its_own_slot(us_cfg):
    from tbot import risk
    from tbot.costs import make_cost_model
    from tbot.strategy import Candidate
    r = dict(us_cfg["risk"], max_positions=5, per_strategy_max=1)
    st = SimState(market="us", initial_capital=100_000, cash=100_000, start_date="2026-01-01",
                  peak_equity=100_000, prev_equity=100_000, week_start_equity=100_000)
    st.positions["AAA"] = Position("AAA", 10, "2026-01-02", 100.0, 90.0, 90.0, 10.0, None, 1.0, "r", strategy="longboard")
    costs = make_cost_model(us_cfg["costs"])
    same = Candidate("BBB", 100.0, 95.0, 2.0, 0.1, "x", strategy="longboard", target_r=0.0, gate_r=3.0)
    other = Candidate("CCC", 100.0, 95.0, 2.0, 0.1, "x", strategy="trend200", target_r=2.0, gate_r=2.0, lift=True)
    assert "at most 1 per strategy" in risk.evaluate(st, same, 100_000, r, costs, 3.0, 0, 0).reason
    assert risk.evaluate(st, other, 100_000, r, costs, 3.0, 0, 0).ok           # another strategy still gets its turn


# ---------------------------------------------------------------- 7. prompt 108: funnel, both sides, near-future band, live re-check
def test_the_near_future_band_keeps_a_share_or_moves_its_stop_up():
    from types import SimpleNamespace as B
    from tbot import predict
    cfg = {"strategy": {"predict": {"enabled": True, "days": 5, "band_atr": 1.0}}}
    pos = Position("AAA", 10, "2026-01-02", 100.0, 90.0, 90.0, 10.0, None, 0.0, "r")
    assert predict.check(cfg, pos, B(Close=100.0, slope20=1.0, atr=2.0, f_quick=True)) == [] and pos.pred_state == "new"
    predict.check(cfg, pos, B(Close=101.5, slope20=1.0, atr=2.0, f_quick=True))
    assert pos.pred_state == "on track"                                     # expected 101, band down to 99
    assert "keep holding" in predict.check(cfg, pos, B(Close=98.0, slope20=1.0, atr=2.0, f_quick=True))[0]
    notes = predict.check(cfg, pos, B(Close=97.0, slope20=1.0, atr=2.0, f_quick=False))
    assert pos.pred_state == "off track, pattern broken" and pos.stop == 95.0 and "stop moved up" in notes[0]
    down = Position("BBB", 1, "2026-01-02", 50.0, 40.0, 40.0, 10.0, None, 0.0, "r")
    predict.check(cfg, down, B(Close=50.0, slope20=-2.0, atr=1.0, f_quick=True))
    assert down.pred_slope == 0.0                                            # assume up or flat, never down


def test_the_funnel_and_the_held_review_run_side_by_side():
    cfg = config.load_market("us")
    cfg["universe"].update(symbols=["AAA", "BBB"], etfs=[], min_price=1, min_avg_value=0)
    cfg["regime"].update(index="^IDX", vix=None)
    md = prepare_market_data(cfg, scenario(), make_strategy_for(cfg))
    day = md.calendar()[-1]
    lk = stages.look(cfg, md, day)
    assert lk["funnel"]["all"] == 2 and lk["funnel"]["quick"] >= lk["funnel"]["pattern"]
    assert all(f in [x for x, _ in rules_families()] for f in lk["funnel"]["families"])
    st = SimState(market="us", initial_capital=1000, cash=1000, start_date="2026-01-01")
    st.positions["AAA"] = Position("AAA", 1, "2026-01-02", 50.0, 45.0, 45.0, 5.0, None, 0.0, "r", strategy="longboard")
    held = stages.review_held(cfg, md, day, st)
    assert held[0]["family"] == "Breakout / new high" and held[0]["pattern_ok"] in (True, False)


def make_strategy_for(cfg):
    from tbot.strategy import make_strategy
    return make_strategy(cfg)


def rules_families():
    from tbot.rules import FAMILIES
    return list(FAMILIES.values())


def test_the_live_recheck_looks_at_both_sides_without_trading(account, monkeypatch, tmp_path):
    from conftest import make_bars
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    cfg = config.load_market("us")
    df = make_bars([100.0 + i * 0.1 for i in range(260)])
    monkeypatch.setattr(web.data, "read_cache", lambda m, t, *a: df)
    stamp = clock.now("America/New_York").strftime("%d %b %H:%M")
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {s: (130.0, stamp, 0.0) for s in syms})
    orders_before = len(web.store().load_state("us").orders)
    out = web.recheck("us")
    assert out["quick"] == out["all"] > 0 and "above" in out["market"] and "AAPL" in out["held"]
    assert len(web.store().load_state("us").orders) == orders_before          # only looks, never trades
    assert web._activity["us"][-1]["tag"] == "LIVE"


# ---------------------------------------------------------------- 8. a time-machine Start stops the old work first
def test_a_world_change_cancels_the_old_test_without_saving(monkeypatch):
    from tbot import study
    calls, saved = [], []

    def slow_passport(market, offline=False, progress=None):
        for k in range(1, 8):
            progress(k, 7)
            calls.append(k)
            time.sleep(0.15)
        saved.append(market)
    monkeypatch.setattr(study, "passport", slow_passport)
    monkeypatch.setitem(web._tests, "us", dict(web._tests["us"], running=False))
    assert web._start_test("us")
    time.sleep(0.35)
    web._tm_switch()                                   # what Start / Leave / a profile switch does
    for _ in range(40):
        if not web._tests["us"]["running"]:
            break
        time.sleep(0.05)
    assert not web._tests["us"]["running"] and saved == [] and len(calls) < 7 and web._tests["us"]["error"] is None


def test_an_old_run_stopped_by_a_switch_leaves_no_error(monkeypatch):
    def stopped(*a, **k):
        raise web.WorldChanged("changed")
    monkeypatch.setattr(web.bot, "daily", stopped)
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=True))
    web._run_daily("us")
    assert web._runs["us"]["error"] is None and not web._runs["us"]["running"]


def test_a_run_does_not_trust_a_test_from_the_old_world(account, monkeypatch):
    started = []
    monkeypatch.setitem(web._tests, "us", dict(web._tests["us"], running=True, gen=web._world_gen - 1))   # left from before Start

    def wait(market, limit=1800, gen=None):
        web._tests["us"]["running"] = False            # the old test stops (cancelled)
    monkeypatch.setattr(web, "_wait_test", wait)
    monkeypatch.setattr(web, "_start_test", lambda m: started.append(m) or True)
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=True))
    with web._lock:
        block = web._gate("us", web._world_gen)
    assert started == ["us", "us"] and "no past-years test" in block   # waited for the old one, then tested in this world


def test_reset_stops_the_test_and_the_time_machine_first(account, monkeypatch, real_time_after):
    from tbot import study
    saved = []

    def slow_passport(market, offline=False, progress=None):
        for k in range(1, 8):
            progress(k, 7)
            time.sleep(0.2)
        saved.append(market)
    monkeypatch.setattr(study, "passport", slow_passport)
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    timemachine.start("us", str(cal[300].date()), 1, [], lambda m: True, web._tm_switch)
    monkeypatch.setitem(web._tests, "us", dict(web._tests["us"], running=False))
    assert web._start_test("us")
    time.sleep(0.3)
    assert web.reset_profile(profiles.active())        # no "wait": it stops everything, then resets
    assert not clock.active() and not web._tests["us"]["running"] and saved == []
    assert web.store().load_state("us") is None


def test_the_time_machine_buys_waiting_orders_at_the_days_open(account, monkeypatch):
    from conftest import make_bars
    st = web.store().load_state("us")
    st.positions.clear()
    st.orders.append(Order(1, "MSFT", "BUY", 2, "2026-10-06", "x", stop=90.0, est_price=100.0, planned_risk=20.0))
    web.store().save_state("us", st)
    clock.start(pd.Timestamp("2026-10-07 10:30", tz="America/New_York").timestamp(), 1)
    monkeypatch.setattr(data, "open_price", lambda m, t, day: 101.0 if str(day.date()) == "2026-10-07" else None)
    web._tm_tick_at["us"] = 0.0
    try:
        web._tm_tick("us")
    finally:
        clock.stop()
    pos = web.store().load_state("us").positions["MSFT"]
    assert pos.entry_date == "2026-10-07" and abs(pos.entry_price - 101.0) < 0.2       # the day's open (plus slippage)


def test_time_machine_start_stops_the_old_test_first(account, monkeypatch, real_time_after):
    from tbot import study
    saved = []

    def slow_passport(market, offline=False, progress=None):
        for k in range(1, 8):
            progress(k, 7)
            time.sleep(0.2)
        saved.append(market)
    monkeypatch.setattr(study, "passport", slow_passport)
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    monkeypatch.setattr(web, "_start_run", lambda m: True)
    monkeypatch.setitem(web._tests, "us", dict(web._tests["us"], running=False))
    assert web._start_test("us")
    time.sleep(0.3)
    c = TestClient(web.api)
    assert c.post("/api/tm/us/start", json={"date": str(cal[300].date()), "speed": 1}).json()["ok"]
    assert clock.active() and not web._tests["us"]["running"] and saved == []       # old test stopped, trip started
    assert c.post("/api/tm/us/start", json={"date": "1900-01-01", "speed": 1}).status_code == 400
    assert clock.active()                                                              # a wrong day stops nothing


def test_bot_timers_can_be_edited_within_safe_limits(account, monkeypatch):
    monkeypatch.setattr(web, "AUTO_ON", True)
    c = TestClient(web.api)
    t = c.get("/api/timers").json()
    assert t["values"]["loop"] == 60 and t["values"]["recheck"] == 300
    assert c.post("/api/timers", json={"values": {"loop": 30, "recheck": 120}}).json()["ok"]
    assert web.bot_timers()["loop"] == 30 and web.bot_timers()["recheck"] == 120
    assert "every 2 minutes" in web._monitor()["text"] and "waiting buys every 30 s" in web._monitor()["text"]
    assert c.post("/api/timers", json={"values": {"loop": 1}}).status_code == 400                # too fast for free data
    assert c.post("/api/timers", json={"values": {"watch_min": 600, "watch_max": 120}}).status_code == 400
    assert c.get("/api/simple/us").json()["timers"]["every"] == 30


def test_each_live_job_rings_its_own_step_while_it_runs(account, monkeypatch):
    from conftest import make_bars
    web._job["us"].clear()
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web.data, "read_cache", lambda m, t, *a: make_bars([100.0 + i * 0.1 for i in range(260)]))
    stamp = clock.now("America/New_York").strftime("%d %b %H:%M")
    seen = []

    def quotes(syms):                                   # what the page would see while the price is fetched
        seen.append({k: v[1] is None for k, v in web._job["us"].items()})
        return {s: (260.0, stamp, 0.0) for s in syms}
    monkeypatch.setattr(web, "fetch_quotes", quotes)
    web.watch_live("us")                                 # a held-share check: step 2 only
    assert seen[-1] == {"held": True}
    web.recheck("us")                                    # the look-again: steps 1 and 2
    assert seen[-1]["market"] and seen[-1]["held"]
    jobs = TestClient(web.api).get("/api/live/us").json()["jobs"]
    assert jobs["market"][1] >= jobs["market"][0] and "trade" not in jobs  # finished, nothing bought or sold
    monkeypatch.setattr(web, "fetch_quotes", lambda syms: {s: (235.0, stamp, 0.0) for s in syms})   # under the stop
    web.watch_live("us", now=time.time() + 10_000)       # due again: a live sell rings step 4
    assert web._job["us"]["trade"][2] == "Sold at the live price"
    assert "AAPL" not in web.store().load_state("us").positions


def test_a_top_up_never_deletes_a_saved_day(tmp_path, monkeypatch):
    from conftest import make_bars
    old = make_bars([100.0 + i for i in range(30)])
    web.data.write_cache("india", "X.NS", old, tmp_path)
    web.data._write_meta("india", {"X.NS": str(old.index[0].date())}, tmp_path)
    gone = old.index[-2]                                   # the source leaves out yesterday, adds a day without a close
    new = old.iloc[-6:].drop(index=gone).copy()
    extra = new.iloc[[-1]].copy(); extra.index = [old.index[-1] + pd.Timedelta(days=1)]; extra["Close"] = float("nan")
    monkeypatch.setattr(web.data, "download", lambda tickers, start: {"X.NS": pd.concat([new, extra])})
    frames, _ = web.data.refresh("india", ["X.NS"], old.index[0], tmp_path)
    got = frames["X.NS"]
    assert gone in got.index and len(got) == len(old) and got.index.is_monotonic_increasing
    assert gone in web.data.read_cache("india", "X.NS", tmp_path).index


def test_a_run_stops_when_most_prices_of_its_day_are_missing(monkeypatch, tmp_path):
    from tbot import app, stages
    monkeypatch.setattr(config, "STATE_DIR", tmp_path); monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(stages, "look", lambda cfg, md, day: {"missing": 100, "scanned": 2, "funnel": {}})
    folder = tmp_path / "runs"
    text = []
    app._daily_market("us", web.store(), folder, True, None, lambda m: None, [], text)
    f = stages.load_facts("us", folder)
    assert "missing for 100 of 102" in f["problem"] and web.store().load_state("us") is None   # no account, no day used


def test_the_time_machine_starts_at_the_chosen_hour_in_market_time(account, monkeypatch, real_time_after):
    eng, md = build(load_us(), scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    monkeypatch.setattr(web, "_start_run", lambda m: True)
    day = str(cal[300].date())
    c = TestClient(web.api)
    assert c.post("/api/tm/us/start", json={"date": day, "speed": 1, "time": "25:00"}).status_code == 400   # refused, nothing stopped
    assert not clock.active()
    assert c.post("/api/tm/us/start", json={"date": day, "speed": 1, "time": "09:30"}).json()["ok"]
    now = clock.now("America/New_York")
    assert str(now.date()) == day and (now.hour, now.minute) == (9, 30)
    assert (profiles.recall("tm") or {}).get("time") == "09:30"
    assert c.post("/api/tm/us/control", json={"action": "stop"}).json()["ok"]


def test_stop_ends_one_markets_run_before_saving_and_frees_the_way(account, monkeypatch, tmp_path):
    from tbot import app
    for t in threading.enumerate():                        # Run threads left by other tests finish first
        if "_run_daily" in t.name:
            t.join(30)
    reached, mine, done = [], [], threading.Event()

    def daily(markets, telegram=True, progress=None, gate=None, **kw):
        if threading.current_thread() not in mine:         # a Run thread left waiting by another test: keep it out
            done.wait(10)
            return
        with app.run_lock(tmp_path / "lock"):              # the one-run-per-profile lock
            progress("us", 1); progress("us", 2); progress("us", 3)
            reached.append("gate")
            gate("us")                                     # waits for a past-years test that never ends by itself
            reached.append("trading")                      # must never be reached
    monkeypatch.setattr(web.bot, "daily", daily)
    monkeypatch.setattr(web, "_start_test", lambda m: True)
    monkeypatch.setitem(web._runs, "us", dict(web._runs["us"], running=False, cancel=False, held=False))   # left by other tests
    real_thread = threading.Thread

    def keep(*a, **k):                                     # remember the Run thread this test starts
        t = real_thread(*a, **k)
        if getattr(k.get("target"), "__name__", "") == "_run_daily":
            mine.append(t)
        return t
    monkeypatch.setattr(web.threading, "Thread", keep)
    monkeypatch.setitem(web._tests, "us", dict(web._tests["us"], running=True, gen=web._world_gen, cancel=False))
    before = web.store().load_state("us").cash
    assert web._start_run("us")
    for _ in range(100):
        if reached:
            break
        time.sleep(0.05)
    assert "Stopping" in TestClient(web.api).post("/api/market/us/run/stop").json()["message"]
    assert web._tests["us"]["cancel"]                      # its test is told to stop too
    for _ in range(100):
        if not web._runs["us"]["running"]:
            break
        time.sleep(0.05)
    run = web._runs["us"]
    assert not run["running"] and run["error"].startswith("Stopped by you") and reached == ["gate"]
    assert not (tmp_path / "lock" / ".run.lock").exists()           # the other market can run now
    assert web.store().load_state("us").cash == before      # nothing saved
    assert not web._auto_start_run("us")                   # the time machine does not start it again
    monkeypatch.setattr(web, "_run_daily", lambda m: None)
    assert web._start_run("us") and not web._runs["us"]["held"]   # Run bot works and clears the hold
    web._runs["us"]["running"] = False
    web._tests["us"].update(running=False, cancel=False)
    done.set()
