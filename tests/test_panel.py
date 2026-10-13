import time

import pandas as pd
import pytest

from tbot import clock, config, data, settings, stages, timemachine, web
from tbot.store import Store

from test_engine import build, scenario
from test_stages import passport


def test_saved_settings_are_what_the_bot_loads():
    assert config.load_market("us")["risk"]["risk_per_trade"] == 0.005
    settings.save("us", {"risk.risk_per_trade": 0.0025, "regime.bad_market.allow": False, "pass.profit_factor": 1.5})
    cfg = config.load_market("us")
    assert cfg["risk"]["risk_per_trade"] == 0.0025 and cfg["regime"]["bad_market"]["allow"] is False
    assert cfg["pass"]["profit_factor"] == 1.5 and config.load_market("india")["risk"]["risk_per_trade"] == 0.005


def test_any_typed_number_is_allowed_and_default_is_bot_instructions():
    for bad in ({"risk.risk_per_trade": -0.01}, {"risk.risk_per_trade": 1.5}, {"strategy.breakout_days": 20.5},
                {"risk.max_positions": "5"}, {"regime.bad_market.allow": 1.0}, {"risk.nope": 1}):
        with pytest.raises(ValueError):
            settings.save("us", bad)
    settings.save("us", {"strategy.breakout_days": 0, "strategy.stop_atr": 2.7, "risk.max_positions": 7.0})
    cfg = config.load_market("us")
    assert cfg["strategy"]["breakout_days"] == 0 and cfg["strategy"]["stop_atr"] == 2.7
    assert cfg["risk"]["max_positions"] == 7 and isinstance(cfg["risk"]["max_positions"], int)
    settings.save("us", {"risk.risk_per_trade": 0.02})
    assert next(f for f in settings.view("us") if f["key"] == "risk.risk_per_trade")["outside"]   # looser than the rules
    settings.reset("us")
    cfg = config.load_market("us")
    assert cfg["risk"]["risk_per_trade"] == 0.005 and cfg["risk"]["max_positions"] == 5 and cfg["pass"]["profit_factor"] == 1.3
    assert not any(f["outside"] for f in settings.view("us"))


def test_pass_mark_change_rejudges_the_saved_test():
    p = passport(1.23, -0.07)
    assert not stages.rejudge(p, {"profit_factor": 1.3, "max_drawdown": 0.10})["passed"]
    assert stages.rejudge(p, {"profit_factor": 1.2, "max_drawdown": 0.10})["passed"]


@pytest.fixture
def real_time_after():
    yield
    clock.stop()


def test_clock_shows_the_chosen_moment_and_runs_at_the_chosen_speed(real_time_after):
    at = pd.Timestamp("2025-03-05 10:00", tz="Asia/Kolkata").timestamp()
    clock.start(at, 3600)                                   # 1 hour per second
    time.sleep(0.5)
    t = clock.now("Asia/Kolkata")
    assert t.date() == pd.Timestamp("2025-03-05").date() and 10 <= t.hour <= 11
    clock.pause(True)
    frozen = clock.epoch()
    time.sleep(0.2)
    assert clock.epoch() == frozen                          # paused: time stands still
    clock.stop()
    assert clock.now().year >= 2026 and not clock.active()


def test_prices_after_the_chosen_moment_are_hidden(tmp_path, real_time_after):
    idx = pd.bdate_range("2025-01-01", "2025-12-31")
    df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=idx)
    data.write_cache("us", "AAA", df, tmp_path)
    assert data.read_cache("us", "AAA", tmp_path).index[-1] == idx[-1]
    clock.start(pd.Timestamp("2025-06-11 12:00", tz="America/New_York").timestamp(), 1)   # Wed, before the close
    seen = data.read_cache("us", "AAA", tmp_path)
    assert seen.index[-1] == pd.Timestamp("2025-06-10")      # that day has not closed yet: only up to the day before
    assert web.fetch_quotes(["AAA"]) == {}                   # no live prices from the future


def test_time_machine_switches_the_whole_bot_and_comes_back(us_cfg, monkeypatch, real_time_after):
    eng, md = build(us_cfg, scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    real = config.STATE_DIR
    Store().save_state("us", eng.new_state(cal[0]))           # the real practice account
    runs, switches = [], []
    timemachine.start("us", str(cal[300].date()), 86400, ["us"], lambda m: runs.append(m) or True, lambda: switches.append(1))
    assert clock.active() and clock.now().date() == cal[300].date()
    assert config.STATE_DIR == real / "timemachine" and Store().load_state("us") is None   # its own, fresh account
    for _ in range(40):
        if runs:
            break
        time.sleep(0.1)
    assert runs and runs[0] == "us"                           # it runs the market by itself
    timemachine.control("stop", None, lambda: switches.append(1))
    assert not clock.active() and config.STATE_DIR == real and len(switches) == 2
    assert Store().load_state("us") is not None               # the real account is still there, untouched
    assert 1 in timemachine.SPEEDS and min(timemachine.SPEEDS) == 1 and 86400 in timemachine.SPEEDS


def test_time_machine_refuses_dates_outside_the_saved_prices(us_cfg, monkeypatch, real_time_after):
    eng, md = build(us_cfg, scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    with pytest.raises(ValueError, match="inside the saved price history"):
        timemachine.start("us", "1990-01-01", 1, ["us"], lambda m: True, lambda: None)
    assert not clock.active()


def test_three_profiles_keep_their_own_account_settings_and_memory(us_cfg):
    from tbot import profiles
    home = config.STATE_DIR
    eng, md = build(us_cfg, scenario())
    Store().save_state("us", eng.new_state(md.calendar()[0]))            # profile 1 has an account
    settings.save("us", {"pass.profit_factor": 1.1})
    assert web.switch_profile("p2") == "Profile 2"
    assert config.STATE_DIR == home / "profiles" / "p2" and Store().load_state("us") is None    # a fresh, empty profile
    assert config.load_market("us")["pass"]["profit_factor"] == 1.3                             # its own settings
    settings.remember_budget("us", 25000)
    assert config.load_market("us")["capital"] == 25000                                          # a new account opens with it
    profiles.remember("tm", {"date": "2024-05-06", "speed": 3600})
    profiles.rename("p2", "  Safe   one ")
    web.switch_profile("p1")
    assert config.STATE_DIR == home and Store().load_state("us") is not None                    # profile 1 is untouched
    assert config.load_market("us")["pass"]["profit_factor"] == 1.1 and profiles.recall("tm") is None
    lst = profiles.listing()
    assert lst["active"] == "p1" and [p["name"] for p in lst["profiles"]] == ["Profile 1", "Safe one", "Profile 3"]
    assert lst["profiles"][0]["started"] == ["us"] and lst["profiles"][1]["started"] == []
    web.switch_profile("p2")
    assert profiles.recall("tm") == {"date": "2024-05-06", "speed": 3600} and config.stop_file_for("us").parent == config.STATE_DIR
    web._runs["us"]["running"] = True
    try:
        with pytest.raises(ValueError, match="still working"):
            web.switch_profile("p3")
    finally:
        web._runs["us"]["running"] = False
        web.switch_profile("p1")


def test_profile_picture_is_checked_and_kept_per_profile():
    from tbot import profiles
    with pytest.raises(ValueError, match="PNG, JPG or WEBP"):
        profiles.save_picture("p2", b"<svg onload=alert(1)>")
    with pytest.raises(ValueError, match="under 3 MB"):
        profiles.save_picture("p2", b"\xff\xd8\xff" + b"0" * profiles.PICTURE_MAX)
    profiles.save_picture("p2", b"\xff\xd8\xff\xe0 jpeg")
    profiles.save_picture("p2", b"\x89PNG\r\n png")                        # a new picture replaces the old one
    assert profiles.picture("p2").name == "p2.png" and profiles.picture("p1") is None
    assert [p["picture"] is not None for p in profiles.listing()["profiles"]] == [False, True, False]


def test_reset_wipes_one_profile_back_to_a_fresh_start(us_cfg):
    from tbot import profiles
    home = config.STATE_DIR
    eng, md = build(us_cfg, scenario())
    Store().save_state("us", eng.new_state(md.calendar()[0]))            # profile 1 keeps its account
    web.switch_profile("p2")
    Store().save_state("us", eng.new_state(md.calendar()[0]))
    settings.remember_budget("us", 25000)
    profiles.remember("tm", {"date": "2024-05-06", "speed": 3600})
    profiles.save_picture("p2", b"\xff\xd8\xff\xe0 jpeg")
    (config.RUNS_DIR / "passport").mkdir(parents=True, exist_ok=True)
    (config.RUNS_DIR / "passport" / "us.json").write_text("{}")
    (config.RUNS_DIR / "backtest").mkdir(parents=True, exist_ok=True)
    config.stop_file_for("us").write_text("")                         # a safety switch is never removed by a reset
    web._runs["us"]["running"] = True                 # a Run that does not stop in time: the reset says so
    old_wait, web.STOP_WAIT = web.STOP_WAIT, 0.3
    try:
        with pytest.raises(ValueError, match="still working"):
            web.reset_profile("p2")
    finally:
        web._runs["us"]["running"] = False
        web.STOP_WAIT = old_wait
    assert web.reset_profile("p2") == "Profile 2"
    assert Store().load_state("us") is None and config.load_market("us")["capital"] != 25000
    assert profiles.recall("tm") is None and not (config.RUNS_DIR / "passport").exists()
    assert (config.RUNS_DIR / "backtest").exists() and config.stop_file_for("us").exists()
    assert profiles.picture("p2") is not None and profiles.listing()["profiles"][1]["started"] == []
    web.switch_profile("p1")
    assert config.STATE_DIR == home and Store().load_state("us") is not None   # the other profile is untouched
    assert (home / "profiles.json").exists()


def test_switch_and_reset_work_while_the_time_machine_is_on(us_cfg, monkeypatch, real_time_after):
    from tbot import profiles
    home = config.STATE_DIR
    eng, md = build(us_cfg, scenario())
    cal = md.calendar()
    monkeypatch.setattr(timemachine, "date_range", lambda m: (cal[250], cal[-1]))
    Store().save_state("us", eng.new_state(cal[0]))                     # profile 1's real account
    timemachine.start("us", str(cal[300].date()), 1, [], lambda m: True, lambda: None)
    assert web.switch_profile("p2") == "Profile 2"                       # no "exit first": it goes back to today
    assert not clock.active() and config.STATE_DIR == home / "profiles" / "p2"
    Store().save_state("us", eng.new_state(cal[0]))
    timemachine.start("us", str(cal[300].date()), 1, [], lambda m: True, lambda: None)
    web.reset_profile("p1")                                              # another profile: this one stays in the time machine
    assert clock.active() and config.STATE_DIR == home / "profiles" / "p2" / "timemachine"
    web.reset_profile("p2")                                              # the profile in use: back to today, then wiped
    assert not clock.active() and config.STATE_DIR == home / "profiles" / "p2" and Store().load_state("us") is None
    web.switch_profile("p1")
    assert config.STATE_DIR == home and Store().load_state("us") is None and profiles.active() == "p1"
