import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tbot.config import load_market  # noqa: E402


def make_bars(closes, start="2020-01-01", volume=1_000_000, spread=0.01):
    """Synthetic daily bars: open at the previous close, high/low 1% around the close."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    high = np.maximum(opens, closes) * (1 + spread)
    low = np.minimum(opens, closes) * (1 - spread)
    vol = np.full(len(closes), float(volume)) if np.isscalar(volume) else np.asarray(volume, float)
    return pd.DataFrame({"Open": opens, "High": high, "Low": low, "Close": closes, "Volume": vol}, index=idx)


@pytest.fixture(autouse=True)
def private_files(tmp_path, monkeypatch):
    """Tests never read or write the real saved run facts or the real past-years test."""
    from tbot import config
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "SETTINGS_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "STOP_DIR", None)
    from tbot import profiles
    monkeypatch.setattr(profiles, "_home", {})


@pytest.fixture(autouse=True)
def no_stray_threads(private_files):
    """After each test: leave the time machine, cancel tests, and wait for Run, test and trip threads to end,
    so a thread from one test never wakes up inside the next one (runs before the folders are put back)."""
    yield
    import threading
    from tbot import clock, timemachine, web
    if clock.active():
        timemachine.exit_(web._tm_switch)
    clock.stop()
    timemachine._world.clear()
    for m in web.MARKETS:
        if web._tests[m]["running"]:
            web._tests[m]["cancel"] = True
    for t in threading.enumerate():
        if t is not threading.current_thread() and any(k in t.name for k in ("_run_daily", "_run_test", "_drive")):
            t.join(5)


@pytest.fixture
def us_cfg():
    cfg = copy.deepcopy(load_market("us"))
    cfg["universe"].update(symbols=["AAA", "BBB"], etfs=[], min_price=1, min_avg_value=0)
    cfg["regime"].update(index="^IDX", vix=None)
    cfg["strategy"].pop("use", None)              # these tests check the trend breakout alone (test_rules.py covers the rest)
    return cfg


@pytest.fixture
def india_cfg():
    return copy.deepcopy(load_market("india"))
