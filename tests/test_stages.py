import json

import numpy as np
import pandas as pd
import pytest

from tbot import app as bot
from tbot import stages
from tbot.store import Store

from conftest import make_bars
from test_engine import build, scenario

LOOK = {"session": "2026-10-07", "level": "good", "extra_safe": False, "risk": 0.005, "scanned": 2, "missing": 0,
        "text": "Index 6,700 above its 200-day average 6,200, fear gauge 15.2", "breakout_days": 20,
        "trend": 2, "breakout": 1, "volume": 1, "qualified": ["ABBV"],
        "rows": [{"symbol": "ABBV", "trend": True, "breakout": True, "volume": True, "liquid": True, "signal": True, "score": 0.12},
                 {"symbol": "AMZN", "trend": True, "breakout": False, "volume": False, "liquid": True, "signal": False, "score": 0.03}]}
NONE = {"session": "2026-10-07", "sessions": 1, "buys": [], "sells": [], "rejects": [], "blocked": None}


def ctx(**over):
    c = {"name": "US", "hours": "9:30 to 16:00 New York (19:00 IST)", "open": True, "currency": "USD", "suffix": "",
         "started": True, "account": {"new": False, "budget": 25000.0, "cash": 24000.0, "holdings": 0, "orders": 0,
                                      "last_session": "2026-10-07"},
         "facts": {"look": LOOK, "decision": NONE, "exits": []}, "positions": [], "orders": [], "block": None,
         "passport": None, "fingerprint": "now", "test": {}, "running": False, "cur": stages.FINISHED, "error": None}
    c.update(over)
    return c


def passport(pf, dd, trades=300, fp="now"):
    def row(p, d):
        return {"trades": trades, "win": 0.4, "pf": p, "ret": 0.06, "dd": d, "halts": 0}
    return {"market": "us", "made": "2026-10-08 16:00", "fingerprint": fp, "shares": 102, "start": "2016-10-08",
            "train_end": "2021-10-07", "split": "2021-10-08", "end": "2026-10-07", "years_test": 5,
            "train": row(1.2, -0.11), "test": row(pf, dd), "parts": [{"window": "a", **row(1.1, -0.05)}],
            **stages.judge({"trades": trades, "profit_factor": pf, "max_drawdown": dd})}


def stage(c, n):
    return stages.build(c)[n - 1]


def held(symbol="AAPL", price=260.0, stop=240.0, target=280.0, buy=250.0):
    return {"symbol": symbol, "qty": 20.0, "buy_price": buy, "price": price, "pnl": (price - buy) * 20 - 1, "stop": stop,
            "target": target}


def test_always_five_stages_in_the_website_order():
    out = stages.build(ctx())
    assert [s["name"] for s in out] == ["Pick market", "Search whole market", "Search my shares", "Test past years", "Buy or sell"]
    assert [s["n"] for s in out] == [1, 2, 3, 4, 5]


# ---- stage 4: honest pass marks
def test_profit_factor_under_the_mark_is_never_green():
    s = stage(ctx(passport=passport(1.23, -0.07)), 4)
    assert s["status"] == stages.ATTENTION and "NOT passed" in s["title"]
    assert any(l.startswith("Fail") and "1.23" in l for l in s["lines"])
    assert any("Real money stays locked" in l for l in s["lines"])


def test_a_real_pass_is_green():
    s = stage(ctx(passport=passport(1.45, -0.08)), 4)
    assert s["status"] == stages.DONE and "passed" in s["title"] and "NOT" not in s["title"]


@pytest.mark.parametrize("pf, dd, trades", [(1.5, -0.12, 300), (3.0, -0.02, 10), (1.3, -0.05, 300), (float("nan"), -0.02, 300)])
def test_other_ways_to_fail(pf, dd, trades):
    assert stage(ctx(passport=passport(pf, dd, trades)), 4)["status"] == stages.ATTENTION


def test_too_few_trades_are_called_too_few():
    s = stage(ctx(passport=passport(2.0, -0.02, trades=10)), 4)
    assert any("too few to judge" in l for l in s["lines"])


def test_a_test_of_old_settings_does_not_count():
    s = stage(ctx(passport=passport(1.5, -0.05, fp="old")), 4)
    assert s["status"] == stages.IDLE and "Settings changed" in s["title"]          # not an error: the next Run tests again


def test_no_saved_test_and_a_running_test():
    assert stage(ctx(), 4)["title"] == "Tested at the next Run" and stage(ctx(), 4)["status"] == stages.IDLE
    assert stage(ctx(test={"running": True}), 4)["status"] == stages.RUNNING


def test_failed_retest_is_reported_not_hidden():
    s = stage(ctx(test={"error": "no internet"}), 4)
    assert s["status"] == stages.ATTENTION and any("no internet" in l for l in s["lines"])


# ---- stage 2: the whole market
def test_market_states_in_stage_two():
    assert stage(ctx(), 2)["status"] == stages.DONE and "1 of 2 shares passed" in stage(ctx(), 2)["title"]
    falling = dict(LOOK, level="bad", extra_safe=False, risk=0.0, text="Index 5,100 is below its 200-day average 5,300")
    assert stage(ctx(facts={"look": falling, "decision": NONE}), 2)["status"] == stages.BLOCKED
    careful = dict(falling, extra_safe=True, risk=0.001)
    assert stage(ctx(facts={"look": careful, "decision": NONE}), 2)["status"] == stages.ATTENTION
    lines = stage(ctx(), 2)["lines"]
    assert any(l.startswith("ABBV") and l.endswith("Passed.") for l in lines) and any(l.startswith("AMZN") for l in lines)


def test_a_new_account_is_described_as_opening_then_opened():
    new = {"new": True, "budget": 5000.0, "cash": 5000.0, "holdings": 0, "orders": 0, "last_session": None}
    opening = stage(ctx(started=False, account=new, facts={"account": new}, running=True, cur=2), 1)
    assert "Opening a new practice account" in "\n".join(opening["lines"])
    opened = stage(ctx(facts={"account": new}), 1)
    assert "Opened a new practice account" in "\n".join(opened["lines"])
    assert "loaded from saved memory" in "\n".join(stage(ctx(), 1)["lines"])


def test_nothing_is_claimed_before_the_first_run():
    c = ctx(facts={}, account=None, started=False)
    out = stages.build(c)
    assert [s["status"] for s in out] == [stages.IDLE] * 5                         # no warning before the first Run


# ---- stage 3: my shares
def test_no_holdings_means_nothing_to_check_but_still_done():
    s = stage(ctx(), 3)
    assert s["status"] == stages.DONE and s["title"] == "Nothing held, nothing to check"


def test_hold_and_sell_verdicts_use_the_real_account():
    orders = [{"side": "SELL", "symbol": "MSFT", "qty": 5, "reason": "Close fell below the 50-day average", "est_price": None, "stop": None}]
    positions = [held("AAPL"), held("MSFT"), held("NVDA", price=239.0)]
    s = stage(ctx(positions=positions, orders=orders), 3)
    text = "\n".join(s["lines"])
    assert "HOLD AAPL" in text and "SELL MSFT" in text and "next open" in text
    assert "SELL NVDA" in text and "at the stop" in text
    assert s["title"] == "3 shares held · 2 to sell"


def test_stop_moved_up_is_visible():
    s = stage(ctx(positions=[held("AAPL", price=270.0, stop=250.0)]), 3)
    assert "profit protected" in s["lines"][0]


# ---- stage 5: buy or sell
def test_buy_status_follows_the_real_account():
    d = dict(NONE, buys=[{"symbol": "ABBV", "qty": 12.0, "est_price": 170.0, "stop": 160.0, "risk": 100.0}])
    facts = {"look": LOOK, "decision": d, "exits": []}
    bought = stage(ctx(facts=facts, positions=[held("ABBV", price=171.0, stop=160.0, buy=170.0)]), 5)
    assert bought["title"] == "Bought ABBV" and bought["status"] == stages.DONE
    order = {"side": "BUY", "symbol": "ABBV", "qty": 12.0, "reason": "x", "est_price": 170.0, "stop": 160.0}
    waiting = stage(ctx(facts=facts, orders=[order]), 5)
    assert waiting["title"] == "Buy order waiting: ABBV"
    gone = stage(ctx(facts=facts), 5)                           # no position and no order: do not guess why
    assert gone["lines"][0].startswith("BUY ABBV: the order is no longer waiting") and "cancelled" not in gone["lines"][0]
    trade = {"symbol": "ABBV", "entry_date": "2026-10-08", "exit_price": 158.0, "pnl": -25.0, "exit_reason": "Stop-loss hit (live check)"}
    sold = stage(ctx(facts=facts, trades=[trade]), 5)           # bought after the decision and already sold again
    assert sold["title"] == "Bought and sold ABBV" and "sold again at $158.00" in sold["lines"][0]


def test_nothing_to_do_is_said_plainly():
    quiet = {"look": dict(LOOK, qualified=[]), "decision": NONE, "exits": []}
    s = stage(ctx(facts=quiet), 5)
    assert s["title"] == "No trade today" and s["lines"][0] == "No buy and no sell today. Your money is kept."


def test_a_share_that_passed_but_was_not_bought_is_explained():
    s = stage(ctx(limit=1), 5)                               # ABBV passed the checks but nothing was ordered
    assert any("Also passed the checks but not bought: ABBV" in l and "at most 1 share at a time" in l for l in s["lines"])
    ordered = dict(NONE, buys=[{"symbol": "ABBV", "qty": 1.0, "est_price": 170.0, "stop": 160.0, "risk": 10.0}])
    order = {"side": "BUY", "symbol": "ABBV", "qty": 1.0, "reason": "x", "est_price": 170.0, "stop": 160.0}
    s = stage(ctx(facts={"look": LOOK, "decision": ordered, "exits": []}, orders=[order]), 5)
    assert not any("Also passed" in l for l in s["lines"])


def test_safety_stop_blocks_stage_five():
    s = stage(ctx(block="Weekly loss -2.10% reached the 2% limit."), 5)
    assert s["status"] == stages.BLOCKED and "will not buy" in "\n".join(s["lines"])


def test_real_money_note_only_until_the_test_passes():
    assert any("Real money stays locked" in l for l in stage(ctx(), 5)["lines"])
    assert not any("Real money" in l for l in stage(ctx(passport=passport(1.5, -0.05)), 5)["lines"])


# ---- while a run is in progress, and when it fails
def test_stages_follow_a_run_in_progress():
    c = ctx(running=True, cur=3, facts={"look": LOOK, "decision": NONE, "exits": []})
    out = {s["n"]: s["status"] for s in stages.build(c)}
    assert out == {1: stages.DONE, 2: stages.RUNNING, 3: stages.RUNNING, 4: stages.IDLE, 5: stages.IDLE}   # side by side
    c4 = ctx(running=True, cur=4, facts={"look": LOOK, "decision": NONE, "exits": []})
    assert {s["n"]: s["status"] for s in stages.build(c4)}[2] != stages.RUNNING                # then the test step
    assert stage(c, 5)["title"] == "Waiting"
    assert stage(ctx(running=True, cur=2, test={"running": True}), 4)["status"] == stages.IDLE      # the Run is not there yet
    assert stage(ctx(running=False, test={"running": True}), 4)["status"] == stages.RUNNING          # "Test again" on its own


def test_an_error_stops_at_the_stage_where_it_happened():
    c = ctx(error="no internet", cur=2)
    out = stages.build(c)
    assert [s["status"] for s in out][:3] == [stages.DONE, stages.BLOCKED, stages.IDLE]
    assert out[1]["lines"] == ["no internet"] and out[4]["status"] == stages.IDLE


def test_no_new_market_day_is_labelled():
    facts = {"look": LOOK, "decision": NONE, "exits": [], "carried": True, "processed_at": "2026-10-07 20:10"}
    assert any("No new market day since 2026-10-07" in l for l in stage(ctx(facts=facts), 5)["lines"])


# ---- plain helpers
def test_money_and_signs():
    assert stages.money("USD", 25000) == "$25,000" and stages.money("INR", 1000000) == "₹10,00,000"
    assert stages.money("INR", 123456.5, 2) == "₹1,23,456.50" and stages.money("USD", -3.5, 2) == "-$3.50"
    assert stages.signed("USD", 7.1) == "+$7.10" and stages.signed("USD", -7.1) == "-$7.10"


def test_fingerprint_sees_strategy_changes_but_not_the_share_limit(us_cfg):
    base = stages.fingerprint(us_cfg)
    us_cfg["risk"]["max_positions"] = 3
    assert stages.fingerprint(us_cfg) == base
    us_cfg["strategy"]["stop_atr"] = us_cfg["strategy"]["stop_atr"] + 1
    assert stages.fingerprint(us_cfg) != base


def test_a_busy_file_does_not_abort_the_save(monkeypatch):
    """On Windows the page reading the facts file made the swap fail with 'Access is denied' and ended the run."""
    real, calls = stages.os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(stages.os, "replace", flaky)
    stages.save_facts("us", {"a": 1})
    assert len(calls) == 3 and stages.load_facts("us") == {"a": 1}


def test_reading_while_writing_never_fails_or_returns_half_a_file():
    import threading
    stages.save_facts("us", {"n": 0})
    stop, bad = threading.Event(), []

    def writer():
        for i in range(300):
            stages.save_facts("us", {"n": i, "pad": "x" * 2000})
        stop.set()

    t = threading.Thread(target=writer)
    t.start()
    while not stop.is_set():
        f = stages.load_facts("us")
        if "n" not in f:
            bad.append(f)
    t.join()
    assert bad == []


def test_saved_facts_are_json_safe_and_survive_a_restart():
    stages.save_facts("us", {"a": float("nan"), "b": np.float64(1.5), "c": [np.int64(3)]})
    assert stages.load_facts("us") == {"a": None, "b": 1.5, "c": [3]}
    json.loads(stages._facts_path("us").read_text(encoding="utf-8"))


# ---- facts taken from the real engine
def test_look_reports_the_real_strategy_checks(us_cfg):
    eng, md = build(us_cfg, scenario())
    day = next(d for d in md.calendar() if md.rows["AAA"][d].signal)
    lk = stages.look(us_cfg, md, day)
    rows = {r["symbol"]: r for r in lk["rows"]}
    assert lk["level"] == "good" and lk["scanned"] == 2 and lk["qualified"] == ["AAA"]
    assert rows["AAA"]["trend"] and rows["AAA"]["breakout"] and rows["AAA"]["volume"] and rows["AAA"]["signal"]
    assert not rows["BBB"]["breakout"] and not rows["BBB"]["signal"]


def test_look_in_a_falling_market(us_cfg):
    frames = scenario()
    frames["^IDX"] = make_bars(100 * np.cumprod(np.full(420, 0.999)))
    eng, md = build(us_cfg, frames)
    lk = stages.look(us_cfg, md, md.calendar()[-1])
    assert lk["level"] == "bad" and lk["extra_safe"] and lk["risk"] < us_cfg["risk"]["risk_per_trade"]
    us_cfg["regime"]["bad_market"]["allow"] = False
    assert stages.look(us_cfg, md, md.calendar()[-1])["extra_safe"] is False


def test_the_daily_run_marks_the_five_stages_in_order(tmp_path, monkeypatch, us_cfg):
    eng, md = build(us_cfg, scenario())
    last = md.calendar()[-1]
    monkeypatch.setattr(bot, "load_data", lambda cfg, start, offline=False: (md, {}))
    monkeypatch.setattr(bot, "load_market", lambda m: us_cfg)
    monkeypatch.setattr(bot, "load_events", lambda: {"us": set()})
    monkeypatch.setattr(bot, "Store", lambda: Store(tmp_path / "sim.sqlite"))
    monkeypatch.setattr(bot, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(bot, "stopped", lambda m: False)
    monkeypatch.setattr(bot.data, "session_clock", lambda cfg, now=None: (last, pd.Timestamp("2030-01-01", tz="UTC")))
    seen = []
    bot.daily(["us"], telegram=False, progress=lambda m, s: seen.append(s))
    assert seen == [1, 2, 3, 4, 5]
    f = stages.load_facts("us")
    assert f["account"]["new"] and f["look"]["scanned"] == 2 and f["carried"] is False
    assert f["decision"]["session"] == str(last.date()) and "exits" in f and f["processed_at"]
    seen.clear()
    bot.daily(["us"], telegram=False, progress=lambda m, s: seen.append(s))      # nothing new: the old decision is kept
    assert seen == [1, 2, 3, 5] and stages.load_facts("us")["carried"] is True
    assert stages.load_facts("us")["account"]["new"] is False


def test_facts_are_written_even_without_the_website(tmp_path, monkeypatch, us_cfg):
    eng, md = build(us_cfg, scenario())
    monkeypatch.setattr(bot, "load_data", lambda cfg, start, offline=False: (md, {}))
    monkeypatch.setattr(bot, "load_market", lambda m: us_cfg)
    monkeypatch.setattr(bot, "load_events", lambda: {"us": set()})
    monkeypatch.setattr(bot, "Store", lambda: Store(tmp_path / "sim.sqlite"))
    monkeypatch.setattr(bot, "stopped", lambda m: False)
    monkeypatch.setattr(bot.data, "session_clock", lambda cfg, now=None: (md.calendar()[-1], pd.Timestamp("2030-01-01", tz="UTC")))
    bot.daily(["us"], telegram=False)
    assert "look" in stages.load_facts("us")


def test_my_shares_is_done_as_soon_as_its_side_has_looked_even_on_the_first_run():
    c = ctx(running=True, cur=4, started=False, account=None, facts={"look": LOOK, "held": []})
    out = {s["n"]: (s["status"], s["title"]) for s in stages.build(c)}
    assert out[2][0] == stages.DONE and out[3] == (stages.DONE, "Nothing held, nothing to check")   # no "Waiting" gap
    assert out[4][0] == stages.RUNNING


def test_a_run_goes_step_by_step_even_with_a_test_already_running():
    t = {"running": True, "step": 2, "steps": 7}
    for cur in (2, 3):                                 # download and both searches: steps 1 and 2 together, test not yet
        out = {s["n"]: s for s in stages.build(ctx(running=True, cur=cur, test=t, facts={}))}
        assert out[2]["status"] == out[3]["status"] == stages.RUNNING
        assert out[4]["status"] == stages.IDLE and out[4]["title"].startswith("Next: a past-years test is already running")
    out = {s["n"]: s for s in stages.build(ctx(running=True, cur=4, test=t, facts={"look": LOOK, "held": []}))}
    assert out[4]["status"] == stages.RUNNING and out[2]["status"] != stages.RUNNING   # then the test step itself
