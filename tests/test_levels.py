import numpy as np
import pandas as pd

from tbot.engine import MarketData


def md(closes, vix=None, levels=True):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    df = pd.DataFrame({"Close": closes}, index=idx)
    df["sma"] = df["Close"].rolling(20, min_periods=1).mean()
    df["sma_fast"] = df["Close"].rolling(5, min_periods=1).mean()
    v = pd.Series(vix, index=idx) if vix is not None else None
    rc = {"index_sma": 20, "vix_max": 25, "levels": levels, "confirm_days": 5, "calm_vix": 20}
    return MarketData(None, {}, df, v, rc), idx


def test_rising_calm_market_is_good():
    m, idx = md(list(np.linspace(100, 130, 60)), vix=[14] * 60)
    assert m.regime(idx[-1])[0] and m.level(idx[-1]) == "good"


def test_nervous_market_is_normal():
    m, idx = md(list(np.linspace(100, 130, 60)), vix=[22] * 60)          # above calm 20, below max 25
    assert m.regime(idx[-1])[0] and m.level(idx[-1]) == "normal"


def test_fresh_recovery_is_normal_not_good():
    m, idx = md([100.0] * 30, vix=[15] * 30)
    m.index["sma"] = 105.0                       # price below its average ...
    m.index.loc[idx[-2:], "sma"] = 95.0          # ... except the last 2 days
    m.index["sma_fast"] = 90.0
    assert m.regime(idx[-1])[0]                  # today the market check passes
    assert m.level(idx[-1]) == "normal"          # but only 2 of 5 days above: not "good" yet
    m.index["sma"] = 95.0
    assert m.level(idx[-1]) == "good"            # all 5 days above: good


def test_falling_market_is_blocked():
    m, idx = md(list(np.linspace(130, 100, 60)))
    assert not m.regime(idx[-1])[0]


def test_levels_off_means_always_good():
    m, idx = md(list(np.linspace(100, 130, 60)), vix=[22] * 60, levels=False)
    assert m.level(idx[-1]) == "good"
