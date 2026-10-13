"""SQLite storage for the daily simulation: account state, trades, equity and every event."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import config
from .models import SimState

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (market TEXT PRIMARY KEY, json TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades (
    market TEXT, symbol TEXT, qty REAL, entry_date TEXT, entry_price REAL, exit_date TEXT,
    exit_price REAL, costs REAL, pnl REAL, r_multiple REAL, exit_reason TEXT, entry_reason TEXT, strategy TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS equity (
    market TEXT, date TEXT, equity REAL, cash REAL, positions_value REAL, regime TEXT,
    PRIMARY KEY (market, date));
CREATE TABLE IF NOT EXISTS events (market TEXT, date TEXT, kind TEXT, symbol TEXT, message TEXT);
"""
TRADE_SQL = ("INSERT INTO trades (market, symbol, qty, entry_date, entry_price, exit_date, exit_price, costs, pnl, "
             "r_multiple, exit_reason, entry_reason, strategy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def _trade_row(market: str, t) -> tuple:
    return (market, t.symbol, t.qty, t.entry_date, t.entry_price, t.exit_date, t.exit_price, t.costs, t.pnl,
            t.r_multiple, t.exit_reason, t.entry_reason, getattr(t, "strategy", "") or "")


class Store:
    def __init__(self, path: Path | None = None):
        path = path or config.STATE_DIR / "sim.sqlite"      # the time machine has its own folder
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with contextlib.closing(self._conn()) as con, con:     # closed at once: Windows can then delete the file
            con.executescript(SCHEMA)
            if "strategy" not in [r[1] for r in con.execute("PRAGMA table_info(trades)")]:
                con.execute("ALTER TABLE trades ADD COLUMN strategy TEXT DEFAULT ''")   # accounts made before the lab rules

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def load_state(self, market: str) -> SimState | None:
        with contextlib.closing(self._conn()) as con:
            row = con.execute("SELECT json FROM state WHERE market = ?", (market,)).fetchone()
        return SimState.from_dict(json.loads(row[0])) if row else None

    def save_run(self, market: str, st: SimState, results: list) -> None:
        """State, trades, equity and events of one run, written in a single transaction."""
        con = self._conn()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)",
                            (market, json.dumps(st.to_dict()), datetime.now().isoformat(timespec="seconds")))
                for r in results:
                    d = str(r.date.date())
                    con.execute("INSERT OR REPLACE INTO equity VALUES (?, ?, ?, ?, ?, ?)",
                                (market, d, r.equity, r.cash, r.positions_value, r.regime))
                    con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                                    [(market, d, k, s, m) for k, s, m in r.events])
                    con.executemany(TRADE_SQL, [_trade_row(market, t) for t in r.trades])
        finally:
            con.close()

    def save_state(self, market: str, st: SimState, note: str | None = None, trades: list = (),
                   symbol: str = "", date: str | None = None) -> None:
        """Save the account after a manual action, with an optional note and closed trades."""
        con = self._conn()
        try:
            with con:
                con.execute("INSERT OR REPLACE INTO state VALUES (?, ?, ?)",
                            (market, json.dumps(st.to_dict()), datetime.now().isoformat(timespec="seconds")))
                if note:
                    con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                                (market, date or st.last_date or "", "manual", symbol, note))
                con.executemany(TRADE_SQL, [_trade_row(market, t) for t in trades])
        finally:
            con.close()

    def _frame(self, sql: str, params: tuple) -> pd.DataFrame:
        con = self._conn()
        try:
            return pd.read_sql_query(sql, con, params=params)
        finally:
            con.close()

    def trades(self, market: str) -> pd.DataFrame:
        return self._frame("SELECT * FROM trades WHERE market = ? ORDER BY exit_date", (market,))

    def equity(self, market: str) -> pd.DataFrame:
        df = self._frame("SELECT * FROM equity WHERE market = ? ORDER BY date", (market,))
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    def events(self, market: str, since: str | None = None, limit: int = 200) -> pd.DataFrame:
        if since:
            return self._frame("SELECT * FROM events WHERE market = ? AND date >= ? ORDER BY rowid DESC LIMIT ?",
                               (market, since, limit))
        return self._frame("SELECT * FROM events WHERE market = ? ORDER BY rowid DESC LIMIT ?", (market, limit))
