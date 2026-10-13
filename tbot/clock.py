"""The bot's idea of "now".

Normally this is the real time. In the time machine it is a chosen past moment that runs forward at a
chosen speed, and no price after the last finished market day of that moment can be seen (data.read_cache).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import pandas as pd

_sim: dict | None = None                 # {"at": replay time (epoch) at "real", "real": real epoch, "speed", "paused"}
_cfgs: dict[str, dict] = {}
_lock = threading.Lock()                 # start, stop, pause and speed one at a time


def active() -> bool:
    return _sim is not None


def epoch() -> float:
    s = _sim
    if s is None:
        return time.time()
    return s["at"] if s["paused"] else s["at"] + (time.time() - s["real"]) * s["speed"]


def now(tz: str | None = None) -> pd.Timestamp:
    """Like pd.Timestamp.now(tz): aware in tz, or naive local time when tz is None."""
    t = epoch()
    return pd.Timestamp(t, unit="s", tz="UTC").tz_convert(tz) if tz else pd.Timestamp(datetime.fromtimestamp(t))


def start(at: float, speed: float) -> None:
    global _sim
    with _lock:
        _sim = {"at": at, "real": time.time(), "speed": float(speed), "paused": False}


def _change(**what) -> None:
    """Pause or change speed from the moment shown now. Nothing happens if the time machine just stopped."""
    global _sim
    with _lock:
        s = _sim
        if s is None:
            return
        _sim = {**s, "at": epoch(), "real": time.time(), **what}


def set_speed(speed: float) -> None:
    _change(speed=float(speed))


def pause(on: bool) -> None:
    _change(paused=bool(on))


def stop() -> None:
    global _sim
    with _lock:
        _sim = None


def info() -> dict | None:
    return None if _sim is None else {"clock": epoch(), "speed": _sim["speed"], "paused": _sim["paused"]}


def cutoff(market: str) -> pd.Timestamp | None:
    """Last market day whose bar may be seen now. None in real time (no limit)."""
    if _sim is None:
        return None
    if market not in _cfgs:
        from .config import load_market
        _cfgs[market] = load_market(market)
    from .data import session_clock
    return session_clock(_cfgs[market])[0]
