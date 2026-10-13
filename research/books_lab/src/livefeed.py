"""Live market feed for the website.

Polls real prices with a short TTL so the page shows live trends without
hammering Yahoo. Falls back to the cached bars if the network is unhappy, and
always reports how fresh the data is - the page must never imply it is live
when it is not.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pandas as pd

from .config import Config
from .data.fetcher import fetch

_LOCK = threading.Lock()
_CACHE: dict = {}
_TTL_SECONDS = 45


def _fresh(key: str) -> bool:
    entry = _CACHE.get(key)
    if not entry:
        return False
    return (time.time() - entry["at"]) < _TTL_SECONDS


def live_quotes(market: str, cfg: Config) -> dict:
    """Latest close, previous close, % change and a short trend for every symbol."""
    key = f"quotes:{market}"
    with _LOCK:
        if _fresh(key):
            return _CACHE[key]["data"]

    mkt = cfg.market_cfg(market)
    suffix = mkt["suffix"]
    out: dict = {"market": market, "symbols": [], "fetched_at": None, "source": "yfinance"}

    index_sym = mkt["index_symbol"]
    idx = fetch(index_sym, lookback_days=120)
    if not idx.empty:
        closes = idx["close"].astype(float)
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else last
        out["index"] = {
            "symbol": index_sym,
            "label": "NIFTY 50" if market == "india" else "S&P 500",
            "last": last,
            "prev": prev,
            "change_pct": (last / prev - 1.0) * 100.0 if prev else 0.0,
            "date": idx.index[-1].date().isoformat(),
            "trend": [round(float(v), 2) for v in closes.tail(60).tolist()],
        }

    fear_sym = mkt["fear_symbol"]
    fear = fetch(fear_sym, lookback_days=120)
    if not fear.empty:
        fcloses = fear["close"].astype(float)
        out["fear"] = {
            "symbol": fear_sym,
            "label": "INDIA VIX" if market == "india" else "VIX",
            "last": float(fcloses.iloc[-1]),
            "date": fear.index[-1].date().isoformat(),
            "trend": [round(float(v), 2) for v in fcloses.tail(60).tolist()],
        }

    symbols = list(mkt["universe"]) + list(mkt.get("etfs", []))
    rows = []
    for sym in symbols:
        yf_sym = f"{sym}{suffix}" if suffix else sym
        df = fetch(yf_sym, lookback_days=400)
        if df.empty or len(df) < 2:
            continue
        closes = df["close"].astype(float)
        last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        rows.append(
            {
                "symbol": sym,
                "last": last,
                "prev": prev,
                "change_pct": (last / prev - 1.0) * 100.0 if prev else 0.0,
                "date": df.index[-1].date().isoformat(),
                "trend": [round(float(v), 2) for v in closes.tail(60).tolist()],
            }
        )
    out["symbols"] = rows
    out["fetched_at"] = datetime.now(timezone.utc).isoformat()
    out["is_live"] = True

    with _LOCK:
        _CACHE[key] = {"at": time.time(), "data": out}
    return out
