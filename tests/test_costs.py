import pytest

from tbot.costs import make_cost_model


def test_india_round_trip_about_quarter_percent(india_cfg):
    c = make_cost_model(india_cfg["costs"])
    value = 100_000.0
    buy = c.buy(100, 1000.0)
    sell = c.sell(100, 1000.0)
    assert buy == pytest.approx(value * (0.001 + 0.0000297 + 0.000001 + 0.00015)
                                + value * (0.0000297 + 0.000001) * 0.18)
    assert sell > buy - value * 0.00015                 # sell adds DP charge, no stamp duty
    assert 0.0020 < (buy + sell) / value < 0.0030


def test_us_commission_min_and_cap(us_cfg):
    c = make_cost_model(us_cfg["costs"])
    assert c.buy(10, 100.0) == pytest.approx(1.0)       # minimum 1 dollar
    assert c.buy(1000, 0.5) == pytest.approx(5.0)       # capped at 1% of 500
    assert c.sell(100, 100.0) > c.buy(100, 100.0)       # SEC fee and FINRA TAF on sells
