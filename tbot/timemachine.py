"""Time machine: the whole bot lives at a chosen past date and time.

While it runs, the bot's clock (tbot/clock.py) shows that moment and moves forward at the chosen speed,
every price after the last finished market day is hidden, and the bot runs by itself each time a market
day closes, exactly as it does today: market check, share search, buys and sells.
It trades a separate practice account in state/timemachine/ (fresh each start), so the real practice
account is never changed. The trip starts with a copy of your settings; what you change during the trip stays
in the trip (the whole app behaves as on that day). Exit returns everything to today and your real settings.
To remove the feature: delete this file, the "time machine" block in web.py and its panel section in app.html.
"""
from __future__ import annotations

import shutil
import threading
import time
from datetime import datetime

import pandas as pd

from . import clock, config, data
from .config import load_market
from .store import Store

SPEEDS = {1: "Same as now (real time)", 60: "1 minute per second", 3600: "1 hour per second",
          86400: "1 day per second", 604800: "1 week per second", 2592000: "1 month per second"}
WARMUP_DAYS = 260                     # market days of history the 200-day averages need first
_world: dict = {}                     # real folders and the start date while the time machine runs


def date_range(market: str):
    if clock.active():
        return _world["range"]
    idx = data.read_cache(market, load_market(market)["regime"]["index"])
    if idx is None or len(idx) <= WARMUP_DAYS:
        return None, None
    return idx.index[WARMUP_DAYS], idx.index[-1]


def start_time(market: str, day: str, at: str | None) -> float:
    """The trip's first moment: `day` at `at` ("HH:MM", the market's own time), or at the time it is now."""
    if not at:
        return datetime.combine(pd.Timestamp(day).date(), datetime.now().time()).timestamp()
    try:
        hh, mm = (int(x) for x in at.split(":"))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except ValueError:
        raise ValueError("Pick a time like 09:30.") from None
    return pd.Timestamp(day).normalize().replace(hour=hh, minute=mm).tz_localize(load_market(market)["timezone"]).timestamp()


def start(market: str, day: str, speed: int, markets: list[str], start_run, on_switch, tick=None, at: str | None = None,
          busy=lambda: False) -> None:
    """Enter the time machine on `day` at `at` (market time) or at the time it is now, then trade forward."""
    if speed not in SPEEDS:
        raise ValueError("Unknown speed.")
    first_moment = start_time(market, day, at)          # a wrong time is refused before anything changes
    exit_(on_switch)
    first, last = date_range(market)
    if first is None or not (first <= pd.Timestamp(day) < last):
        raise ValueError("Pick a date inside the saved price history.")
    real_state, real_runs, real_stop, real_settings = config.STATE_DIR, config.RUNS_DIR, config.STOP_DIR, config.SETTINGS_DIR
    folder = real_state / "timemachine"
    shutil.rmtree(folder, ignore_errors=True)
    shutil.rmtree(real_runs / "timemachine", ignore_errors=True)   # an old trip's past-years test saw later prices
    folder.mkdir(parents=True, exist_ok=True)
    for f in real_settings.glob("settings_*.json"):                   # the trip starts with your settings, then keeps its own
        shutil.copyfile(f, folder / f.name)
    config.STATE_DIR, config.RUNS_DIR, config.STOP_DIR, config.SETTINGS_DIR = folder, real_runs / "timemachine", folder, folder
    gen = time.time()
    _world.update(state=real_state, runs=real_runs, stop=real_stop, settings=real_settings, day=day, range=(first, last),
                  gen=gen, markets=set(markets))
    clock.start(first_moment, speed)
    on_switch()
    threading.Thread(target=_drive, args=(gen, start_run, tick, busy), daemon=True).start()


def _drive(gen: float, start_run, tick=None, busy=lambda: False) -> None:
    """Like auto mode, but on the time machine's clock: run a market as soon as a new market day has closed,
    and (tick) buy waiting orders while its market is open.
    The trip clock waits while the trip's own Run (and its past-years test) works: no market day is skipped at
    high speed, and every price file of a Run is cut at the same moment."""
    while clock.active() and _world.get("gen") == gen:
        if _world.get("run_pause"):                    # paused by us for the trip's Run: go on when it is done
            if not busy():
                _world["run_pause"] = False
                clock.pause(False)
        elif not clock.info()["paused"]:
            for m in list(_world.get("markets", ())):
                try:
                    st = Store().load_state(m)
                    expected, _ = data.session_clock(load_market(m))
                    if st is None or pd.Timestamp(st.last_date) < expected:
                        clock.pause(True)
                        _world["run_pause"] = True
                        if not start_run(m):           # not started (already running, or you pressed Stop)
                            _world["run_pause"] = False
                            clock.pause(False)
                        break
                    elif tick:
                        tick(m)
                except Exception:                      # a failed run shows on the page itself; keep the clock going
                    if _world.get("run_pause") and not busy():
                        _world["run_pause"] = False
                        clock.pause(False)
        time.sleep(1)


def control(action: str, speed: int | None, on_switch) -> None:
    if not clock.active():
        raise ValueError("The time machine is not running.")
    if action in ("pause", "resume"):
        _world["run_pause"] = False                    # your Pause or Resume wins over the wait for a Run
        clock.pause(action == "pause")
    elif action == "speed" and speed in SPEEDS:
        clock.set_speed(speed)
    elif action == "stop":
        exit_(on_switch)
    else:
        raise ValueError("Unknown action.")


def exit_(on_switch) -> None:
    if not clock.active():
        return
    clock.stop()
    config.STATE_DIR, config.RUNS_DIR, config.STOP_DIR, config.SETTINGS_DIR = (_world["state"], _world["runs"], _world["stop"],
                                                                                _world["settings"])
    _world.clear()
    on_switch()


def drives(market: str) -> bool:
    """True while the trip clock runs and the trip runs this market's days by itself (a Run after each close)."""
    info = clock.info()
    return bool(info and (not info["paused"] or _world.get("run_pause")) and market in _world.get("markets", ()))


def status(market: str) -> dict:
    first, last = date_range(market)
    out = {"first": str(first.date()) if first is not None else None, "last": str(last.date()) if last is not None else None,
           "speeds": SPEEDS, "active": clock.active()}
    if clock.active():
        out.update(clock.info(), day=_world["day"], run_pause=bool(_world.get("run_pause")))
    return out
