import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tbot import web
from tbot.models import Order, SimState
from tbot.store import Store

TODAY = pd.Timestamp.now().strftime("%d %b")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    st = Store(tmp_path / "sim.sqlite")
    acct = SimState(market="us", initial_capital=5_000, cash=5_000, start_date="2026-10-07",
                    last_date="2026-10-07", peak_equity=5_000, prev_equity=5_000, week_start_equity=5_000)
    acct.orders = [Order(id=1, symbol="ABBV", side="BUY", qty=1, created="2026-10-07", reason="test",
                         stop=260.0, est_price=271.0, planned_risk=11.0)]
    acct.next_order_id = 2
    st.save_state("us", acct)
    monkeypatch.setattr(web, "_store", st)
    monkeypatch.setattr(web.config, "STOP_FILE", tmp_path / "STOP")
    return TestClient(web.api), st


def quote(price, stamp=None):
    return lambda tickers: {t: (price, f"{stamp or TODAY} 10:30") for t in tickers}


def test_market_closed_keeps_order_waiting(setup, monkeypatch):
    client, st = setup
    monkeypatch.setattr(web, "market_open_now", lambda cfg: False)
    r = client.post("/api/market/us/buy-now").json()
    assert "closed" in r["message"]
    assert len(st.load_state("us").orders) == 1


def test_open_market_buys_at_live_price(setup, monkeypatch):
    client, st = setup
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(272.0))
    r = client.post("/api/market/us/buy-now").json()
    assert "Bought live" in r["message"]
    s = st.load_state("us")
    assert s.orders == [] and "ABBV" in s.positions
    p = s.positions["ABBV"]
    assert p.qty == 1 and p.entry_price == pytest.approx(272.0, rel=0.001) and p.stop == 260.0
    assert s.cash < 5_000 - 272


def test_live_buy_message_is_saved_for_stage_five(setup, monkeypatch):
    from tbot import stages
    client, st = setup
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(272.0))
    msg = client.post("/api/market/us/buy-now").json()["message"]
    assert "Bought live" in msg and stages.load_facts("us")["live"]["msg"] == msg


def test_live_price_below_stop_cancels(setup, monkeypatch):
    client, st = setup
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(255.0))
    msg = client.post("/api/market/us/buy-now").json()["message"]
    assert "Cancelled" in msg
    s = st.load_state("us")
    assert s.orders == [] and s.positions == {} and s.cash == 5_000


def test_old_price_is_not_used(setup, monkeypatch):
    client, st = setup
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(272.0, stamp="01 Jan"))
    assert "still waiting" in client.post("/api/market/us/buy-now").json()["message"]
    assert len(st.load_state("us").orders) == 1


def test_auto_live_buy_respects_stop_file(setup, monkeypatch, tmp_path):
    client, st = setup
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(272.0))
    (tmp_path / "STOP_us").write_text("x")
    monkeypatch.setattr(web.config, "STOP_FILE", tmp_path / "STOP")
    assert web.auto_live_buy() == []
    assert len(st.load_state("us").orders) == 1


def test_only_one_share_at_a_time(setup, monkeypatch):
    from tbot import settings
    settings.save("us", {"risk.max_positions": 1})                # this test checks the cap at 1 share
    client, st = setup
    s = st.load_state("us")
    s.orders.append(Order(id=2, symbol="AMZN", side="BUY", qty=1, created="2026-10-07", reason="test",
                          stop=250.0, est_price=260.0, planned_risk=11.0))
    s.next_order_id = 3
    st.save_state("us", s)
    monkeypatch.setattr(web, "market_open_now", lambda cfg: True)
    monkeypatch.setattr(web, "fetch_quotes", quote(272.0))
    msg = client.post("/api/market/us/buy-now").json()["message"]
    s = st.load_state("us")
    assert list(s.positions) == ["ABBV"] and s.orders == []          # first one bought, second cancelled
    assert "Cancelled. AMZN" in msg and "only 1 share" in msg
