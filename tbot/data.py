"""Daily price bars: download from yfinance (free), cache as CSV, check for bad data.

yfinance is unofficial. Use it for research and simulation only, never for live orders.
Prices use auto_adjust=False: OHLC are split-adjusted but not dividend-adjusted.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

import pandas as pd

from . import clock
from .config import DATA_DIR

log = logging.getLogger("tbot.data")
FIELDS = ["Open", "High", "Low", "Close", "Volume"]
BATCH = 40
# yfinance keeps shared state between calls, so two downloads at once can mix up results.
YF_LOCK = threading.Lock()
DATA_LOCK = threading.RLock()       # one refresh of the price files at a time (a Run and a past-years test may overlap)
FRESH_SECONDS = 15 * 60             # a ticker topped up this recently is not downloaded again


def _safe(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", ticker)


def cache_file(market: str, ticker: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / market / f"{_safe(ticker)}.csv"


def read_cache(market: str, ticker: str, data_dir: Path = DATA_DIR) -> pd.DataFrame | None:
    path = cache_file(market, ticker, data_dir)
    if not path.exists():
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    cut = clock.cutoff(market)                      # time machine: nothing after the last finished market day
    if cut is not None:
        df = df[df.index <= cut]
    return None if df.empty else df[FIELDS]


def open_price(market: str, ticker: str, day, data_dir: Path = DATA_DIR) -> float | None:
    """The opening price of one day (time machine buys at the open). Only the Open: it is known at the open,
    the rest of that day stays hidden."""
    path = cache_file(market, ticker, data_dir)
    if not path.exists():
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True, usecols=["Date", "Open"])
    day = pd.Timestamp(day).normalize()
    return float(df.loc[day, "Open"]) if day in df.index and pd.notna(df.loc[day, "Open"]) else None


def write_cache(market: str, ticker: str, df: pd.DataFrame, data_dir: Path = DATA_DIR) -> None:
    path = cache_file(market, ticker, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="Date")


def _meta_path(market: str, data_dir: Path) -> Path:
    return data_dir / market / "_meta.json"


def _read_meta(market: str, data_dir: Path) -> dict:
    p = _meta_path(market, data_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _write_meta(market: str, meta: dict, data_dir: Path) -> None:
    p = _meta_path(market, data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=1, sort_keys=True), encoding="utf-8")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[[c for c in FIELDS if c in df.columns]].astype(float)


def _download_batch(tickers: list[str], start: pd.Timestamp) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    raw = None
    for attempt in range(3):
        try:
            with YF_LOCK:
                raw = yf.download(tickers, start=start.strftime("%Y-%m-%d"), auto_adjust=False,
                                  actions=False, group_by="ticker", threads=True, progress=False)
            break
        except Exception as exc:  # network errors, rate limits
            log.warning("Download failed (attempt %d): %s", attempt + 1, exc)
            time.sleep(5 * (attempt + 1))
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in level0:
                sub = raw[t].dropna(how="all")
                if not sub.empty:
                    out[t] = _normalize(sub)
    elif len(tickers) == 1:
        sub = raw.dropna(how="all")
        if not sub.empty:
            out[tickers[0]] = _normalize(sub)
    return out


def download(tickers: list[str], start: pd.Timestamp) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), BATCH):
        out.update(_download_batch(tickers[i:i + BATCH], start))
        if i + BATCH < len(tickers):
            time.sleep(1)
    return out


def refresh(market: str, tickers: list[str], start, data_dir: Path = DATA_DIR,
            offline: bool = False) -> tuple[dict[str, pd.DataFrame], dict[str, float]]:
    """Bars for each ticker from `start`, using the cache and topping it up.

    Returns (frames, splits). `splits` maps a ticker to old/new price ratio when a stock
    split changed its history since the last download (e.g. 2.0 for a 2-for-1 split).
    """
    with DATA_LOCK:
        return _refresh(market, tickers, start, data_dir, offline)


def _refresh(market, tickers, start, data_dir, offline):
    start = pd.Timestamp(start).normalize()
    meta = _read_meta(market, data_dir)
    frames: dict[str, pd.DataFrame] = {}
    splits: dict[str, float] = {}
    full, incremental = [], []
    today = str(clock.now().date())
    offline = offline or clock.active()             # the past cannot download anything new
    missing_today = {t for t, d in (meta.get("_missing") or {}).items() if d == today}
    for t in tickers:
        if t in missing_today:
            continue
        cached = read_cache(market, t, data_dir)
        if cached is not None:
            frames[t] = cached
        covered = t in meta and pd.Timestamp(meta[t]) <= start
        if cached is None or not covered:
            full.append(t)
        elif time.time() - float((meta.get("_fresh") or {}).get(t, 0)) > FRESH_SECONDS:
            incremental.append(t)                   # topped up minutes ago (e.g. by the Run before a test): reuse
    if offline:
        return frames, splits

    if full:
        log.info("%s: downloading full history for %d tickers from %s", market, len(full), start.date())
        got = download(full, start)
        for t, df in got.items():
            frames[t] = df
            write_cache(market, t, df, data_dir)
            meta[t] = str(start.date())
            meta.setdefault("_fresh", {})[t] = time.time()
        for t in full:
            if t not in got:
                log.warning("%s: no data for %s (wrong symbol or delisted?). Retrying tomorrow.", market, t)
                meta.setdefault("_missing", {})[t] = today

    if incremental:
        since = min(frames[t].index[-1] for t in incremental) - pd.Timedelta(days=10)
        log.info("%s: updating %d tickers from %s", market, len(incremental), since.date())
        got = download(incremental, since)
        refetch = []
        for t, new in got.items():
            old = frames[t]
            new = new.dropna(subset=["Close"])             # a day without a close is not a finished bar
            if new.empty:
                continue
            overlap = old.index.intersection(new.index)
            if len(overlap):
                ratio = float((old.loc[overlap, "Close"] / new.loc[overlap, "Close"]).median())
                if abs(ratio - 1) > 0.02:
                    splits[t] = ratio
                    refetch.append(t)
                    continue
            # New bars replace the same days; a saved day the source left out this time stays (Yahoo sometimes drops
            # yesterday's Indian bar while the market is open).
            frames[t] = pd.concat([old[~old.index.isin(new.index)], new]).sort_index()
            write_cache(market, t, frames[t], data_dir)
            meta.setdefault("_fresh", {})[t] = time.time()
        if refetch:
            log.info("%s: history changed (split?) for %s, re-downloading", market, ", ".join(refetch))
            since_full = min(pd.Timestamp(meta.get(t, str(start.date()))) for t in refetch)
            for t, df in download(refetch, since_full).items():
                frames[t] = df
                write_cache(market, t, df, data_dir)
        for t in incremental:
            if t not in got:
                log.warning("%s: update failed for %s, using cached bars", market, t)

    _write_meta(market, meta, data_dir)
    return frames, splits


def clean_bars(df: pd.DataFrame, is_index: bool, max_move: float) -> pd.DataFrame:
    """Drop broken bars and flag suspicious jumps. Flagged bars are never used for new entries."""
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    df = df[(df["Close"] > 0) & (df["High"] >= df["Low"])]
    if not is_index:
        df = df[df["Volume"] > 0]
    df["Open"] = df["Open"].clip(df["Low"], df["High"])
    if is_index:
        df["suspect"] = False
    else:
        df["suspect"] = (df["Close"] / df["Close"].shift(1) - 1).abs() > max_move
    return df


def drop_incomplete(df: pd.DataFrame, cfg: dict, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Remove today's bar if the market has not closed (plus a safety delay) yet."""
    now = now if now is not None else clock.now(cfg["timezone"])
    local = now.tz_convert(cfg["timezone"]).tz_localize(None) if now.tzinfo else now
    today = local.normalize()
    h, m = (int(x) for x in cfg["session"]["close"].split(":"))
    ready = today + pd.Timedelta(hours=h, minutes=m + int(cfg.get("data_ready_after_minutes", 60)))
    cutoff = today + pd.Timedelta(days=1) if local >= ready else today
    return df[df.index < cutoff]


def session_clock(cfg: dict, now: pd.Timestamp | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(latest session that should be finished by now, when the next one will be ready).

    Weekends are skipped. Exchange holidays are not known here, so on a holiday the bot
    simply finds no new bar and waits for the next day.
    """
    tz = cfg["timezone"]
    now = (now if now is not None else clock.now(tz)).tz_convert(tz)
    h, m = (int(x) for x in cfg["session"]["close"].split(":"))
    ready = pd.Timedelta(hours=h, minutes=m + int(cfg.get("data_ready_after_minutes", 60)))
    day = now.normalize()
    if day.weekday() < 5 and now >= day + ready:
        last = day
    else:
        last = day - pd.Timedelta(days=1)
        while last.weekday() >= 5:
            last -= pd.Timedelta(days=1)
    nxt = last + pd.Timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += pd.Timedelta(days=1)
    return last.tz_localize(None), nxt + ready
