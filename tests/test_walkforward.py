import math

import pandas as pd

from tbot import walkforward as wf

from conftest import make_bars
from test_engine import scenario


def test_year_windows_cover_part_years():
    w = wf.year_windows("2015-01-01", "2017-06-30")
    assert w == [("2015-01-01", "2015-12-31"), ("2016-01-01", "2016-12-31"), ("2017-01-01", "2017-06-30")]
    assert wf.year_windows("2015-03-10", "2015-09-01") == [("2015-03-10", "2015-09-01")]


def rows(values, pfs, trades=50):
    return [{"value": v, "pf": p, "trades": trades} for v, p in zip(values, pfs)]


def test_choose_best_keeps_live_value_unless_clearly_better():
    assert wf.choose_best(rows([10, 20, 40], [1.00, 1.02, 0.90]), 20) == 20       # 0.02 is noise
    assert wf.choose_best(rows([10, 20, 40], [1.00, 1.02, 1.40]), 20) == 40       # clear win
    assert wf.choose_best(rows([10, 20, 40], [1.9, 1.02, 0.9], trades=5), 20) == 20   # too few trades
    assert wf.choose_best(rows([10, 20], [math.nan, math.nan]), 20) == 20


def test_sensitivity_verdict_words():
    fragile, text = wf.sensitivity_verdict(1.1, [0.6, 1.1, 1.4])
    assert fragile and "FRAGILE" in text
    fragile, text = wf.sensitivity_verdict(1.2, [1.1, 1.2, 1.3, 1.25])
    assert not fragile and "STABLE" in text
    assert wf.sensitivity_verdict(math.nan, [])[0] is False


def test_run_once_and_override_change_nothing_in_the_live_config(us_cfg):
    frames = scenario()
    before = us_cfg["strategy"]["stop_atr"]
    a = wf.run_once(us_cfg, frames, set(), "2021-01-04")
    b = wf.run_once(us_cfg, frames, set(), "2021-01-04", overrides={("strategy", "stop_atr"): 3.0})
    assert a is not None and b is not None and a["trades"] >= 1
    assert us_cfg["strategy"]["stop_atr"] == before                     # test never edits the settings
    assert wf.run_once(us_cfg, frames, set(), "2021-01-04", "2021-01-08") is None   # window too short


def test_yearly_and_sensitivity_run_on_synthetic_data(us_cfg):
    frames = scenario()
    end = str(frames["AAA"].index[-1].date())
    years = wf.yearly(us_cfg, frames, set(), "2021-01-04", end)
    assert years and all("window" in y for y in years)
    rows_, base = wf.sensitivity(us_cfg, frames, set(), "2021-01-04", end)
    assert {r["setting"] for r in rows_} == {k[1] for k in wf.GRID}
    assert sum(r["live"] for r in rows_) == len(wf.GRID)               # live value marked once per setting
