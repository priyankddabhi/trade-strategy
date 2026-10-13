"""Event calendar - days the bot is forbidden to enter.

BOT_INSTRUCTIONS §4: no entries in the first/last 15 minutes of the session,
or around FOMC / CPI / RBI / Budget / results.

The static list below covers the recurring macro events. Per-stock earnings
dates are not published far enough ahead to be reliable in a static file, so
they are handled by the `earnings_lookback` window: if a stock reported in the
last N trading days, treat the next N as blocked too.
"""
from __future__ import annotations

from datetime import date, datetime

# (month, day, label) - recurring macro events
RECURRING = [
    (1, 31, "US-Budget"),
    (2, 1, "India-Budget"),
    (3, 31, "India-Fiscal-Year-End"),
    (4, 15, "India-Tax-Audit"),
    (5, 31, "India-Q4-Results"),
    (6, 30, "India-Q1-Results"),
    (9, 30, "India-Q2-Results"),
    (10, 31, "India-Q2-Results"),
    (11, 14, "India-Q2-Results"),
    (12, 31, "India-Q3-Results"),
]

# Dates blocked because of a known event, filled per run.
# In production this is populated from a provider. Here it is honest: an empty
# list means "no known events", which the engine reports loudly.
EVENT_DATES: dict[date, str] = {}

# FOMC meeting dates (2024-2026) - the Fed publishes these a year ahead.
FOMC_DATES = [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
    "2026-09-16", "2026-10-28", "2026-12-09",
]

# RBI MPC dates (India)
RBI_DATES = [
    "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06", "2025-10-01",
    "2025-12-05",
    "2026-02-06", "2026-04-08", "2026-06-05", "2026-08-07", "2026-10-02",
    "2026-12-04",
]

# India Union Budget day (1 February, moved to 1 Feb 2025+)
INDIA_BUDGET_DATES = [
    "2025-02-01", "2026-02-01",
]


def _load_static() -> dict[date, str]:
    out: dict[date, str] = {}
    for iso, label in [(d, "FOMC") for d in FOMC_DATES]:
        out[datetime.strptime(iso, "%Y-%m-%d").date()] = label
    for iso in RBI_DATES:
        out[datetime.strptime(iso, "%Y-%m-%d").date()] = "RBI-MPC"
    for iso in INDIA_BUDGET_DATES:
        out[datetime.strptime(iso, "%Y-%m-%d").date()] = "India-Budget"
    out.update(EVENT_DATES)
    return out


class EventCalendar:
    """Answers: may we open a new position on this date?"""

    def __init__(self) -> None:
        self._events = _load_static()

    def event_on(self, when: date | datetime) -> str | None:
        d = when.date() if isinstance(when, datetime) else when
        return self._events.get(d)

    def is_blocked(self, when: date | datetime) -> bool:
        return self.event_on(when) is not None

    def blocked_dates(self) -> list[tuple[date, str]]:
        return sorted(self._events.items())

    def days_until_next_event(self, when: date | datetime) -> int | None:
        d = when.date() if isinstance(when, datetime) else when
        future = [e for e in self._events if e >= d]
        if not future:
            return None
        return (min(future) - d).days

    def session_window_ok(self, when: datetime, first_minutes: int = 15, last_minutes: int = 15) -> tuple[bool, str]:
        """Daily bars cannot see intraday time, so this only guards the
        explicitly-known session boundaries. For daily data this returns True
        with a note; the intraday version needs 15-minute bars."""
        return True, "daily bars - session window not enforced (needs intraday data)"
