import pytest
from fastapi.testclient import TestClient

from tbot import web
from tbot.models import Position, SimState
from tbot.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    st = Store(tmp_path / "sim.sqlite")
    acct = SimState(market="us", initial_capital=25_000, cash=20_000, start_date="2026-10-01",
                    last_date="2026-10-06", peak_equity=25_000, prev_equity=25_000, week_start_equity=25_000)
    acct.positions["AAPL"] = Position("AAPL", 20, "2026-10-02", 250.0, 240.0, 240.0, 10.0, 280.0, 1.0,
                                      "test reason", highest_close=250.0, last_close=250.0)
    st.save_state("us", acct)
    monkeypatch.setattr(web, "_store", st)
    monkeypatch.setattr(web, "fetch_quotes", lambda tickers: {t: (260.0, "07 Oct 15:30") for t in tickers})
    monkeypatch.setattr(web.config, "STOP_FILE", tmp_path / "STOP")
    return TestClient(web.api)


def us(client):
    return client.get("/api/market/us").json()


def test_overview_shows_live_profit(client):
    m = us(client)
    pos = m["positions"][0]
    assert pos["buy_price"] == 250.0 and pos["price"] == 260.0
    assert pos["pnl"] == pytest.approx(20 * 10 - 1.0)          # price gain minus entry costs
    assert m["in_market"] == pytest.approx(5_200)
    assert m["total"] == pytest.approx(25_200)
    assert m["profit"] == pytest.approx(200)


def test_add_and_withdraw_keep_profit_correct(client):
    assert client.post("/api/market/us/cash", json={"amount": 10_000}).json()["ok"]
    m = us(client)
    assert m["cash"] == 30_000 and m["capital_in"] == 35_000
    assert m["profit"] == pytest.approx(200)                     # adding money is not profit
    r = client.post("/api/market/us/cash", json={"amount": -50_000})
    assert r.status_code == 400 and "free cash" in r.json()["detail"]


def test_manual_sell_closes_position(client):
    r = client.post("/api/market/us/sell/AAPL").json()
    assert r["ok"] and "Sold 20" in r["message"]
    m = us(client)
    assert m["positions"] == [] and m["trades"][0]["exit_reason"] == "Manual sell from the website"
    assert m["performance"]["net_pnl"] > 0 and m["performance"]["trades"] == 1


def test_stop_button_is_per_market(client):
    client.post("/api/market/us/stop", json={"on": True})
    assert us(client)["stop"]
    assert not client.get("/api/market/india").json()["stop"]       # India keeps trading
    client.post("/api/market/us/stop", json={"on": False})
    assert not us(client)["stop"]


def test_start_screen_lists_both_markets(client):
    cards = client.get("/api/markets").json()["markets"]
    assert [c["market"] for c in cards] == ["india", "us"]
    assert cards[1]["started"] and not cards[0]["started"]


def test_results_and_rules_are_json_safe(client):
    m = us(client)
    assert m["performance"]["trades"] == 0 and m["performance"]["profit_factor"] is None
    assert any(r["rule"] == "Daily loss" for r in m["rules"])
    assert m["findings"]


def test_auto_runs_a_market_when_a_session_is_waiting(client, monkeypatch):
    monkeypatch.setattr(web, "AUTO_RUN", True)
    started = []
    monkeypatch.setattr(web, "_start_run", lambda m: started.append(m) or True)
    import pandas as pd
    monkeypatch.setattr(web.data, "session_clock",
                        lambda cfg, now=None: (pd.Timestamp("2026-10-07"), pd.Timestamp("2026-10-08")))
    web._auto["us"].update(expected=None, at=0.0)
    assert web.auto_tick(now=1000.0) == ["us"]            # India has no account yet, so only US
    assert web.auto_tick(now=1100.0) == []                # tried a moment ago, waits
    assert web.auto_tick(now=1000.0 + web.AUTO_RETRY + 1) == ["us"]   # retries later if still behind


def test_no_run_by_itself_unless_switched_on(client, monkeypatch):
    started = []
    monkeypatch.setattr(web, "_start_run", lambda m: started.append(m) or True)
    import pandas as pd
    monkeypatch.setattr(web.data, "session_clock",
                        lambda cfg, now=None: (pd.Timestamp("2026-10-07"), pd.Timestamp("2026-10-08")))
    web._auto["us"].update(expected=None, at=0.0)
    assert not web.AUTO_RUN
    assert web.auto_tick(now=1000.0) == [] and started == []
    assert web.auto_tick(now=1000.0 + web.AUTO_RETRY + 1) == [] and started == []


def test_auto_skips_when_up_to_date(client, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(web.data, "session_clock",
                        lambda cfg, now=None: (pd.Timestamp("2026-10-06"), pd.Timestamp("2026-10-07")))
    web._auto["us"].update(expected=None, at=0.0)
    assert web.auto_tick(now=5000.0) == []


def test_simple_view_is_small_and_plain(client):
    d = client.get("/api/simple/us").json()
    assert d["started"] and d["limit"] == 5 and d["step"] == 5 and d["state"] == "holding"
    assert d["target"]["symbol"] == "AAPL" and d["spent"] == 20 * 250.0
    assert d["chart"]["kind"] in ("share", "market")
    assert "watching the price" in d["headline"]


def test_simple_page_is_home_and_full_page_is_kept(client):
    assert "Semi Final Bot" in client.get("/").text and "/vendor/chart.umd.min.js" in client.get("/").text
    assert client.get("/vendor/anime.min.js").status_code == 200 and client.get("/simple").status_code == 200
    assert "Paper Trading Bot" in client.get("/full").text


def test_simple_view_before_first_run(client):
    d = client.get("/api/simple/india").json()
    assert d["started"] is False and d["step"] == 0 and "Run bot" in d["headline"]
    assert [s["status"] for s in d["stages"]] == ["idle"] * 5


# ---- the five stages show what the bot really did
def fresh_run(monkeypatch, **over):
    run = {"running": True, "started": None, "finished": None, "error": None, "output": "", "summary": None, "stage": 1, **over}
    monkeypatch.setitem(web._runs, "us", run)
    return run


def test_simple_view_lists_the_five_stages(client):
    s = client.get("/api/simple/us").json()["stages"]
    assert [x["name"] for x in s] == ["Pick market", "Search whole market", "Search my shares", "Test past years", "Buy or sell"]
    assert s[0]["status"] == "done" and "US market chosen" == s[0]["title"]
    assert s[1]["status"] == "idle"                                    # no run yet, so nothing is claimed
    assert s[3]["status"] == "idle" and s[3]["title"] == "Tested at the next Run"


def test_stage_four_shows_the_saved_test_and_reading_the_page_never_starts_one(client, monkeypatch):
    from test_stages import passport
    started = []
    monkeypatch.setattr(web, "_start_test", lambda m: started.append(m) or True)
    web.stages.save_passport("us", passport(1.23, -0.07, fp=web.stages.fingerprint(web.load_market("us"))))
    s4 = client.get("/api/simple/us").json()["stages"][3]
    assert s4["status"] == "attention" and "NOT passed" in s4["title"]
    assert any("1.23" in line for line in s4["lines"]) and started == []


def gate(market="us"):
    with web._lock:                                   # a Run holds the account lock when it reaches the gate
        return web._gate(market, web._world_gen)


def test_the_gate_tests_only_when_missing_changed_or_old_and_blocks_buys_until_passed(client, monkeypatch):
    from test_stages import passport
    started = []
    monkeypatch.setattr(web, "_start_test", lambda m: started.append(m) or True)
    fresh_run(monkeypatch)
    block = gate()
    assert started == ["us"] and "no past-years test" in block            # nothing saved yet: test first, no buys
    fp = web.stages.fingerprint(web.load_market("us"))
    web.stages.save_passport("us", passport(1.5, -0.05, fp=fp))
    assert gate() is None and started == ["us"]                            # passed and fresh: reused, buys allowed
    web.stages.save_passport("us", passport(1.1, -0.05, fp=fp))
    block = gate()
    assert started == ["us"] and "did not pass" in block                   # failed: no new test, no new buys
    web.stages.save_passport("us", passport(1.5, -0.05, fp="old"))
    assert "settings changed" in gate() and started == ["us", "us"]        # settings changed since the test
    web.stages.save_passport("us", {**passport(1.5, -0.05, fp=fp), "end": "2025-01-02"})
    assert "ends 2025-01-02" in gate() and started == ["us", "us", "us"]   # out of date: tested again


def test_a_run_waits_for_the_test_without_holding_the_account_lock(client, monkeypatch):
    import threading
    import time
    from test_stages import passport
    fresh_run(monkeypatch)
    fp = web.stages.fingerprint(web.load_market("us"))
    t = web._tests["us"]
    t.update(running=True, step=3, steps=7)
    free = []

    def finish():
        time.sleep(0.6)
        free.append(web._lock.acquire(blocking=False))                    # the live watch could work meanwhile
        if free[-1]:
            web._lock.release()
        web.stages.save_passport("us", passport(1.5, -0.05, fp=fp))
        t.update(running=False, step=7)
    threading.Thread(target=finish).start()
    t0 = time.monotonic()
    assert gate() is None
    assert time.monotonic() - t0 >= 0.5 and free == [True]                 # held until the test was done, lock let go
    assert "profit factor 1.50" in web._activity["us"][-1]["text"]         # then it reads the fresh result


def test_a_waiting_run_stops_without_saving_when_the_profile_or_time_machine_changes(client, monkeypatch):
    import threading
    import time
    fresh_run(monkeypatch)
    t = web._tests["us"]
    t.update(running=True, step=2, steps=7)

    def change():
        time.sleep(0.3)
        web._tm_switch()                                                   # what leaving the time machine does
    threading.Thread(target=change).start()
    try:
        with pytest.raises(web.WorldChanged):
            gate()
    finally:
        t.update(running=False)


def test_stage_four_shows_which_of_the_seven_tests_is_running(client):
    web._tests["us"].update(running=True, step=3, steps=7)
    try:
        d = client.get("/api/simple/us").json()
        s4 = d["stages"][3]
        assert d["test"]["running"] and d["test"]["step"] == 3           # the page polls this until the test is done
        assert s4["status"] == "running" and s4["title"].endswith("test 3 of 7")
    finally:
        web._tests["us"].update(running=False, step=0)


def test_a_failed_run_is_shown_at_its_own_stage(client, monkeypatch):
    def boom(markets, telegram=True, progress=None, **kw):
        progress("us", 1)
        progress("us", 2)
        raise RuntimeError("no internet")
    monkeypatch.setattr(web.bot, "daily", boom)
    fresh_run(monkeypatch)
    web._run_daily("us")
    s = client.get("/api/simple/us").json()["stages"]
    assert [x["status"] for x in s][:3] == ["done", "blocked", "idle"] and s[1]["lines"] == ["no internet"]


def test_a_run_fills_waiting_buys_when_the_market_is_open(client, monkeypatch):
    from tbot.models import Order
    a = web.store().load_state("us")
    a.orders.append(Order(1, "AMZN", "BUY", 1, "2026-10-06", "x", stop=190.0, est_price=200.0, planned_risk=10.0))
    web.store().save_state("us", a)
    monkeypatch.setattr(web.bot, "daily", lambda markets, telegram=True, progress=None, **kw: None)
    bought = []
    monkeypatch.setattr(web, "buy_live", lambda m: bought.append(m) or "Bought live.")
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    fresh_run(monkeypatch)
    web._run_daily("us")
    assert bought == ["us"] and web._runs["us"]["stage"] == web.stages.FINISHED and web._runs["us"]["error"] is None
    monkeypatch.setattr(web, "market_open_now", lambda cfg: False)
    fresh_run(monkeypatch)
    web._run_daily("us")
    assert bought == ["us"]                                            # market closed: the orders wait for the open


def test_a_run_that_found_no_index_data_is_not_shown_as_success(client, monkeypatch):
    def nodata(markets, telegram=True, progress=None, **kw):
        web.stages.save_facts("us", {"problem": "No price data for the US market index, so nothing could be checked."})
    monkeypatch.setattr(web.bot, "daily", nodata)
    fresh_run(monkeypatch, stage=2)
    web._run_daily("us")
    assert "No price data" in web._runs["us"]["error"] and web._runs["us"]["stage"] != web.stages.FINISHED


def test_kill_feed_shows_real_events_newest_first(client):
    st = web.store()
    a = st.load_state("us")
    st.save_state("us", a, "Bought live. ABBV 1 at 272.00", symbol="ABBV", date="2026-10-07")
    st.save_state("us", a, "AAPL: sold at 239.00, stop-loss hit (live check)", symbol="AAPL", date="2026-10-08")
    feed = client.get("/api/simple/us").json()["feed"]
    assert [(f["tag"], f["symbol"]) for f in feed[:2]] == [("SELL", "AAPL"), ("BUY", "ABBV")]
    assert "stop-loss hit" in feed[0]["text"]
    stats = client.get("/api/simple/us").json()["feed_stats"]
    assert stats["bought"] == 1 and stats["sold"] == 0 and stats["checked"] is None   # no run yet: nothing claimed


def test_test_endpoint_starts_one_background_test(client, monkeypatch):
    monkeypatch.setattr(web, "_start_test", lambda m: True)
    assert client.post("/api/market/us/test").json()["ok"]
    monkeypatch.setattr(web, "_start_test", lambda m: False)
    assert not client.post("/api/market/us/test").json()["ok"]


def test_live_feed_reports_each_share_sorted_and_each_holding(client, monkeypatch):
    for m in web.MARKETS:
        web._activity[m].clear()
        web._last_line[m] = 0.0
    look = {"text": "S&P 500 above its 200-day average", "scanned": 3, "breakout_days": 20, "qualified": ["ABBV"],
            "misses": {"MSFT": ["breakout"], "KO": ["trend", "volume"]}}
    monkeypatch.setattr(web.stages, "load_facts", lambda m, folder=None: {"look": look})
    web._note_sorting("us")
    first = client.get("/api/live/us").json()
    lines = [(x["tag"], x["symbol"], x["text"]) for x in first["items"]]
    assert lines[0][0] == "MARKET"
    assert ("SKIP", "", "no new 20-day high: 1; trend not up, volume too low: 1") in lines    # one line for all skipped shares
    assert lines[-2][:2] == ("PASS", "ABBV") and lines[-1] == ("SCAN", "", "3 shares sorted: 1 passed every check")
    assert client.get(f"/api/live/us?after={first['last']}").json()["items"] == []
    web._last_line["us"] = 0.0
    web.heartbeat("us")
    hold = client.get(f"/api/live/us?after={first['last']}").json()["items"]
    assert [(x["tag"], x["symbol"]) for x in hold] == [("HOLD", "AAPL")] and "stop 240.00" in hold[0]["text"]
    web.heartbeat("us")                                   # quiet for less than 5 minutes: no repeat
    assert client.get(f"/api/live/us?after={hold[-1]['id']}").json()["items"] == []
