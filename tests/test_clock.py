import pandas as pd

from tbot.data import session_clock


def at(cfg, text):
    return pd.Timestamp(text, tz=cfg["timezone"])


def test_india_before_ready_uses_previous_day(india_cfg):
    last, nxt = session_clock(india_cfg, at(india_cfg, "2026-10-07 15:50"))      # Wednesday
    assert last == pd.Timestamp("2026-10-06")
    assert nxt == at(india_cfg, "2026-10-07 16:00")                               # 15:30 + 30 min


def test_india_after_ready_uses_today(india_cfg):
    last, nxt = session_clock(india_cfg, at(india_cfg, "2026-10-07 16:05"))
    assert last == pd.Timestamp("2026-10-07")
    assert nxt == at(india_cfg, "2026-10-08 16:00")


def test_weekend_points_to_friday_and_monday(us_cfg):
    last, nxt = session_clock(us_cfg, at(us_cfg, "2026-10-10 12:00"))            # Saturday
    assert last == pd.Timestamp("2026-10-09")
    assert nxt == at(us_cfg, "2026-10-12 16:30")
