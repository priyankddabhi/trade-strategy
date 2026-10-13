import numpy as np
import pandas as pd

from tbot import indicators as ind
from tbot.strategy import TrendBreakout

from conftest import make_bars


def test_prior_high_excludes_today():
    s = pd.Series([1, 2, 3, 10], dtype=float)
    assert ind.prior_high(s, 3).iloc[3] == 3.0      # today's 10 is not included


def test_no_look_ahead_in_strategy_columns(us_cfg):
    """Changing the future must not change any indicator or signal in the past."""
    rng = np.random.default_rng(1)
    closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, 400))
    strat = TrendBreakout(us_cfg["strategy"], us_cfg["universe"])
    base = strat.prepare(make_bars(closes))
    changed = closes.copy()
    changed[300:] *= 3                                # wild future
    alt = strat.prepare(make_bars(changed))
    cols = ["sma_fast", "sma_slow", "sma_exit", "high_n", "vol_avg", "atr", "roc", "signal"]
    pd.testing.assert_frame_equal(base[cols].iloc[:300], alt[cols].iloc[:300])


def test_atr_constant_range():
    df = make_bars([100.0] * 30, spread=0.01)
    assert abs(ind.atr(df, 14).iloc[-1] - 2.0) < 1e-9   # high 101, low 99
