"""Local website for one market at a time: live account, results, reasons, control buttons.

Runs on http://127.0.0.1:8050 only, so nobody else on the network can open it.
All money is virtual. Buttons change the virtual accounts, never a real broker.
"""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter, deque
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import app as bot
from . import clock, config, data, profiles, report, risk, settings, stages, timemachine
from .broker import VirtualBroker
from .config import MARKETS, load_market
from .costs import make_cost_model
from .store import Store

log = logging.getLogger("tbot.web")
WEB_DIR = Path(__file__).parent / "web"
QUOTE_TTL = 60                      # seconds between live price downloads

INFO = {
    "india": {"name": "India", "exchange": "NSE", "hours": "9:15 to 15:30 IST",
              "universe": "Nifty 50, Nifty Next 50 and index ETFs"},
    "us": {"name": "US", "exchange": "NYSE and Nasdaq", "hours": "9:30 to 16:00 New York (19:00 to 01:30 IST in summer, an hour later in winter)",
           "universe": "S&P 100 stocks plus the SPY and QQQ funds"},
}
COST_NOTE = {
    "india": "Indian charges (STT, stamp duty, exchange fees, GST, DP charge) cost about 0.25% per round trip, "
             "plus slippage. Small moves cannot beat that, so the bot skips trades whose target is too close.",
    "us": "US costs are small: about $1 commission per order plus tiny SEC and FINRA fees on sells. "
          "Slippage matters more than fees here.",
}

api = FastAPI(title="Trading bot (virtual money)")
_lock = threading.Lock()            # one change to the accounts at a time
_world_lock = threading.RLock()     # auto mode works on one profile; switching waits for its check to end
_start_lock = threading.Lock()      # starting a run or a test: check and mark busy in one step
_world_gen = 0                      # +1 each time the profile or the time machine changes the folders in use
_quotes: dict[str, tuple[float, str, float]] = {}
_prev: dict[str, float] = {}      # close of the session before the latest bar
_runs: dict[str, dict] = {m: {"running": False, "started": None, "finished": None, "error": None, "output": "",
                              "summary": None, "stage": stages.FINISHED} for m in MARKETS}
_tests: dict[str, dict] = {m: {"running": False, "started": None, "finished": None, "error": None, "step": 0, "steps": 7}
                          for m in MARKETS}
_store: Store | None = None

AUTO_ON = os.environ.get("TBOT_AUTO", "1") != "0"
AUTO_RUN = os.environ.get("TBOT_AUTO_RUN", "0") == "1"   # off: a run starts only when you press Run bot
PAGE = os.environ.get("TBOT_PAGE", "app.html")      # home page file; a trial copy can run on another port
AUTO_MARKETS = [m for m in os.environ.get("TBOT_AUTO_MARKETS", "india,us").split(",") if m in MARKETS]
LIVE_EVERY = 60                    # seconds between live-buy checks while a market is open
AUTO_EVERY = 300                    # seconds between checks for a finished session
AUTO_RETRY = 1800                   # if a session is still missing, try again after 30 minutes
_auto: dict[str, dict] = {m: {"expected": None, "at": 0.0, "when": None, "result": None} for m in MARKETS}

# Bot timers you can change in the side panel (seconds). Saved per profile in timers.json; limits keep free price
# data from being asked too often.
# What the bot loop is doing right now, for the step rings on the page: job -> [start, end or None, words].
# "market" = step 1 (whole market look-again), "held" = step 2 (held shares), "trade" = step 4 (live buy or sell).
_job: dict[str, dict] = {m: {} for m in MARKETS}


@contextlib.contextmanager
def _doing(market: str, words: str, *jobs: str):
    t = time.time()
    for j in jobs:
        _job[market][j] = [t, None, words]
    try:
        yield
    finally:
        for j in jobs:
            _job[market][j][1] = time.time()


def _jobs_view(market: str) -> dict:
    return {k: list(v) for k, v in _job[market].items()}


TIMER_DEFAULTS = {"loop": 60, "watch_min": 120, "watch_max": 600, "recheck": 300, "day_check": 300}
TIMER_LIMITS = {"loop": (15, 600), "watch_min": (30, 1800), "watch_max": (60, 3600), "recheck": (60, 3600), "day_check": (60, 3600)}
_timer_cache: dict = {}


def _timer_path():
    return config.SETTINGS_DIR / "timers.json"


def bot_timers() -> dict:
    p = _timer_path()
    try:
        stamp = p.stat().st_mtime
    except OSError:
        return dict(TIMER_DEFAULTS)
    if _timer_cache.get("key") != (str(p), stamp):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        _timer_cache.update(key=(str(p), stamp), values={**TIMER_DEFAULTS, **{k: int(v) for k, v in raw.items() if k in TIMER_DEFAULTS}})
    return dict(_timer_cache["values"])


def save_bot_timers(values: dict) -> dict:
    new = {**bot_timers()}
    for k, v in values.items():
        if k not in TIMER_DEFAULTS:
            raise ValueError(f"Unknown timer: {k}")
        lo, hi = TIMER_LIMITS[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"{k}: choose between {lo} and {hi} seconds")
        new[k] = int(v)
    if new["watch_max"] < new["watch_min"]:
        raise ValueError("The slowest held-share check must not be faster than the quickest one.")
    _timer_path().parent.mkdir(parents=True, exist_ok=True)
    _timer_path().write_text(json.dumps(new, indent=1), encoding="utf-8")
    return new

# ---------------------------------------------------------------- live activity: one line per real action
ACTIVITY_KEEP = 300
HEARTBEAT = 300                     # seconds: with nothing else happening, report the held shares this often
_activity: dict[str, deque] = {m: deque(maxlen=ACTIVITY_KEEP) for m in MARKETS}
_activity_id = itertools.count(1)
_last_line: dict[str, float] = {m: 0.0 for m in MARKETS}


def note(market: str, tag: str, text: str, symbol: str = "") -> None:
    _activity[market].append({"id": next(_activity_id), "at": clock.now().strftime("%H:%M:%S"),
                              "tag": tag, "symbol": symbol, "text": text})
    _last_line[market] = time.time()


def _short_sym(market: str, sym: str) -> str:
    suffix = load_market(market)["universe"].get("suffix", "")
    return sym[:-len(suffix)] if suffix and sym.endswith(suffix) else sym


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def clean(x):
    """Make numbers JSON-safe: NaN and infinity become null."""
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if hasattr(x, "item"):          # numpy scalars
        return clean(x.item())
    return x


# ---------------------------------------------------------------- live prices

def fetch_quotes(tickers: list[str]) -> dict[str, tuple[float, str]]:
    """Latest price per ticker from 1-minute bars. Free data, may be delayed up to 15 minutes."""
    if clock.active():                    # the past has no live prices: callers fall back to the last close
        return {}
    now = time.time()
    need = [t for t in tickers if t not in _quotes or now - _quotes[t][2] > QUOTE_TTL]
    if need and data.YF_LOCK.acquire(timeout=3):
        try:
            import yfinance as yf

            raw = yf.download(need, period="5d", interval="1m", group_by="ticker",
                              auto_adjust=False, progress=False, threads=True)
            for t in need:
                if isinstance(raw.columns, pd.MultiIndex):
                    if t not in raw.columns.get_level_values(0):
                        continue
                    closes = raw[t]["Close"].dropna()
                else:
                    closes = raw["Close"].dropna() if len(need) == 1 else pd.Series(dtype=float)
                if len(closes):
                    days = closes.index.date
                    before = closes[days != days[-1]]
                    if len(before):
                        _prev[t] = float(before.iloc[-1])
                    _quotes[t] = (float(closes.iloc[-1]), closes.index[-1].strftime("%d %b %H:%M"), now)
        except Exception as exc:  # network trouble: fall back to the last close
            log.warning("Live prices failed: %s", exc)
        finally:
            data.YF_LOCK.release()
    return {t: (_quotes[t][0], _quotes[t][1]) for t in tickers if t in _quotes}


# ---------------------------------------------------------------- read

def _reason_key(msg: str) -> str:
    msg = re.sub(r"^No new entries: ", "", str(msg))
    msg = re.sub(r"^[A-Z0-9&.\-]+: ", "", msg)
    msg = re.sub(r"[-+]?\d[\d,.]*%?", "#", msg)
    return msg.split(". ")[0][:90]


def _performance(st, trades: pd.DataFrame, equity: pd.DataFrame) -> dict:
    n = len(trades)
    wins = int((trades["pnl"] > 0).sum()) if n else 0
    gross_win = float(trades.loc[trades["pnl"] > 0, "pnl"].sum()) if n else 0.0
    gross_loss = float(-trades.loc[trades["pnl"] <= 0, "pnl"].sum()) if n else 0.0
    costs = (float(trades["costs"].sum()) if n else 0.0) + sum(p.entry_costs for p in st.positions.values())
    curve = equity["equity"] if len(equity) else pd.Series(dtype=float)
    if n < 30:
        sample = f"{n} trades: too few to conclude anything yet"
    elif n < 100:
        sample = f"{n} trades: suggestive, not conclusive"
    else:
        sample = f"{n} trades: enough for a first judgement"
    return {"trades": n, "wins": wins, "losses": n - wins, "sample_note": sample,
            "net_pnl": float(trades["pnl"].sum()) if n else 0.0,
            "win_rate": wins / n if n else None,
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
            "expectancy": float(trades["r_multiple"].mean()) if n else None,
            "costs": costs, "costs_pct": costs / st.capital_in() if st.capital_in() else None,
            "max_drawdown": float((curve / curve.cummax() - 1).min()) if len(curve) else 0.0}


def latest_backtest(market: str) -> dict | None:
    """Summary of the newest 10-year backtest for this market, if one exists."""
    def span(folder: Path) -> tuple[int, float]:
        parts = folder.name.split("_")          # market_start_end_timestamp
        try:
            days = (pd.Timestamp(parts[2]) - pd.Timestamp(parts[1])).days
        except (IndexError, ValueError):
            days = 0
        return days, folder.stat().st_mtime

    runs = sorted((profiles.folders("p1")["runs"] / "backtest").glob(f"{market}_*"), key=span)   # research: the same for every profile
    if not runs:
        return None
    folder = runs[-1]                           # the longest test, newest if several are equally long
    js = folder / "stats.json"
    if js.exists():
        d = json.loads(js.read_text(encoding="utf-8"))
    else:
        eq = pd.read_csv(folder / "equity.csv", index_col=0, parse_dates=True)
        tr = pd.read_csv(folder / "trades.csv")
        stats = report.summarize(eq["equity"], tr, float(load_market(market)["capital"]))
        d = {"stats": stats, "benchmark": None,
             "checks": [{"text": t, "ok": bool(ok)} for t, ok in report.pass_checks(stats)]}
    if d.get("benchmark") is None and d["stats"].get("start"):
        idx = data.read_cache(market, load_market(market)["regime"]["index"])
        if idx is not None:
            c = idx["Close"][(idx.index >= d["stats"]["start"]) & (idx.index <= d["stats"]["end"])]
            d["benchmark"] = float(c.iloc[-1] / c.iloc[0] - 1) if len(c) > 1 else None
    d["folder"] = folder.name
    d["passed"] = all(c["ok"] for c in d["checks"])
    return d


def _findings(market, st, perf, why_no_buys, bt) -> list[dict]:
    out = []
    if bt:
        s = bt["stats"]
        if bt["passed"]:
            out.append({"tone": "good", "title": "The strategy passed the 10-year test.",
                        "text": f"Profit factor {s.get('profit_factor') or 0:.2f}, max drawdown "
                                f"{(s.get('max_drawdown') or 0):.1%} from {s.get('start')} to {s.get('end')}."})
        else:
            out.append({"tone": "bad", "title": "The current strategy failed the 10-year test.",
                        "text": f"Profit factor {s.get('profit_factor') or 0:.2f} (needs over 1.3) and max "
                                f"drawdown {(s.get('max_drawdown') or 0):.1%} (must stay under 10%). "
                                "This live run tests the machine, not a proven strategy."})
    out.append({"tone": "warn", "title": "Sample size.",
                "text": perf["sample_note"] + ". BOT_INSTRUCTIONS asks for 100+ trades before judging."})
    out.append({"tone": "warn", "title": "Costs.", "text": COST_NOTE[market]})
    if why_no_buys:
        out.append({"tone": "good" if "Safety rule" in why_no_buys else "warn",
                    "title": "No new buys right now.",
                    "text": why_no_buys + (" A no-trade day is a good day." if "Safety rule" in why_no_buys else "")})
    return out


def _next_text(cfg: dict, last_date: str | None) -> str:
    expected, next_ready = data.session_clock(cfg)
    if last_date is None or pd.Timestamp(last_date) < expected:
        return f"A finished session ({expected.date()}) is waiting. The bot processes it on the next run."
    ist = next_ready.tz_convert("Asia/Kolkata").strftime("%a %d %b, %H:%M IST")
    return (f"Up to date: the latest finished session ({last_date}) is already processed. "
            f"The next session is counted after {ist}.")


def _series(market: str, ticker: str, quotes: dict, label: str) -> dict | None:
    """Last 60 daily closes plus the live price, for the picture cards."""
    df = data.read_cache(market, ticker)
    if df is None or len(df) < 2:
        return None
    closes = [float(v) for v in df["Close"].dropna().tail(61)]
    last_day = df.index[-1].strftime("%d %b")
    q = quotes.get(ticker)
    if q and not q[1].startswith(last_day):          # live price is from a newer day than the saved bars
        prev, last, closes = closes[-1], q[0], closes + [q[0]]
        when = q[1]
    else:
        prev, last, when = closes[-2], closes[-1], f"close {df.index[-1].date()}"
        if q:
            last, when = q[0], q[1]
    return {"symbol": ticker, "label": label, "last": last, "prev": prev,
            "change": last / prev - 1 if prev else None, "trend": [round(v, 2) for v in closes[-60:]], "time": when}


def market_card(market: str, cfg: dict, quotes: dict, syms: list[str]) -> dict:
    idx, vix = cfg["regime"]["index"], cfg["regime"].get("vix")
    return {"index": _series(market, idx, quotes, "S&P 500" if market == "us" else "NIFTY 50"),
            "fear": _series(market, vix, quotes, "VIX" if market == "us" else "INDIA VIX") if vix else None,
            "stocks": [s for s in (_series(market, x, quotes, x) for x in syms) if s]}


def overview(market: str, live: bool = True) -> dict:
    cfg = load_market(market)
    st = store().load_state(market)
    base = {"market": market, "currency": cfg["currency"], "started": st is not None, **INFO[market],
            "stop": config.stopped(market), "run": _runs[market],
            "auto": {"on": AUTO_ON, "last": _auto[market]["when"]}}
    if st is None:
        return clean(base)
    index = cfg["regime"]["index"]
    watch_syms = config.universe_tickers(cfg) if len(config.universe_tickers(cfg)) <= 10 else []
    vix = cfg["regime"].get("vix")
    quotes = fetch_quotes(list(dict.fromkeys(list(st.positions) + watch_syms + [index] + ([vix] if vix else [])))) if live else {}

    positions, in_market, open_pnl = [], 0.0, 0.0
    for p in st.positions.values():
        price, at = quotes.get(p.symbol, (p.last_close, f"close {st.last_date}"))
        value, cost = p.qty * price, p.qty * p.entry_price
        pnl = value - cost - p.entry_costs
        in_market += value
        open_pnl += pnl
        positions.append({"symbol": p.symbol, "qty": p.qty, "buy_date": p.entry_date, "buy_price": p.entry_price,
                          "price": price, "price_time": at, "value": value, "pnl": pnl,
                          "pnl_pct": pnl / cost if cost else 0.0, "stop": p.stop, "target": p.target,
                          "r_now": (price - p.entry_price) / p.risk_per_share if p.risk_per_share else 0.0,
                          "reason": p.reason})

    trades = store().trades(market)
    equity = store().equity(market)
    events = store().events(market, limit=5000)
    total, capital_in = st.cash + in_market, st.capital_in()
    expected, next_ready = data.session_clock(cfg)
    behind = st.last_date is None or pd.Timestamp(st.last_date) < expected
    next_update = _next_text(cfg, st.last_date)
    regime = equity["regime"].iloc[-1] if len(equity) else ""
    regime_ok = " above its " in regime and not regime.startswith("Bad")      # "Good market. Index ... above its ..."
    validation = stages.load_facts(market).get("validation") or {}
    if st.halted:
        why_no_buys = "The bot is halted after a 5% drawdown."
    elif config.stopped(market):
        why_no_buys = "New buys are stopped by the Stop button."
    elif validation.get("why"):
        why_no_buys = validation["why"]
    elif regime and "Extra-safe mode" in regime:
        why_no_buys = "The market is falling: extra-safe mode only (tiny size, the day's strongest share). " + regime
    elif regime and not regime_ok:
        why_no_buys = "Safety rule: the market is falling, too nervous or its data is missing, so the bot stays in cash. " + regime
    elif not st.positions and not st.orders:
        why_no_buys = "No stock met all the buy rules on the last session."
    else:
        why_no_buys = ""

    perf = _performance(st, trades, equity)
    said_no = Counter(_reason_key(m) for m in
                      events.loc[events["kind"].isin(["reject", "blocked", "cancel"]), "message"])
    bt = latest_backtest(market)
    r = cfg["risk"]
    biggest = max((x["value"] for x in positions), default=0.0)
    rules = [
        {"rule": "Risk per trade", "limit": f"{r['risk_per_trade']:.1%} of equity", "now": "Applied to every new order", "ok": True},
        {"rule": "Daily loss", "limit": f"{r['max_daily_loss']:.0%}", "now": f"{st.day_return:+.2%} on the last session",
         "ok": st.day_return > -r["max_daily_loss"]},
        {"rule": "Weekly loss", "limit": f"{r['max_weekly_loss']:.0%}", "now": f"{st.week_return:+.2%} this week",
         "ok": st.week_return > -r["max_weekly_loss"]},
        {"rule": "Max drawdown, manual restart", "limit": f"{r['max_drawdown']:.0%}", "now": f"{st.drawdown:.2%}",
         "ok": not st.halted},
        {"rule": "Open positions", "limit": str(r["max_positions"]), "now": f"{len(st.positions)} held, "
         f"{sum(o.side == 'BUY' for o in st.orders)} queued", "ok": len(st.positions) <= r["max_positions"]},
        {"rule": "Money in one stock", "limit": f"{r['max_position_pct']:.0%}",
         "now": f"{biggest / total:.1%} largest" if total else "0%", "ok": biggest <= total * r["max_position_pct"] * 1.5},
        {"rule": "Losing streak pause", "limit": f"{r['loss_streak']} losses in a row",
         "now": f"{st.loss_streak} now" + (f", paused {st.pause_sessions_left} sessions" if st.pause_sessions_left else ""),
         "ok": st.pause_sessions_left == 0},
        {"rule": "Market trend filter", "limit": "Index above its 200-day average",
         "now": "Passing" if regime_ok else "Blocking new buys", "ok": regime_ok},
        {"rule": "Stop-loss on every trade", "limit": "Always", "now": "Every position has a stop", "ok": True},
        {"rule": "No leverage, no shorting, no options", "limit": "Never", "now": "Cash buys only", "ok": True},
    ]
    watch = []
    held = {p["symbol"]: p for p in positions}
    queued = {o.symbol: o for o in st.orders}
    for s in watch_syms:
        q = quotes.get(s)
        last = None
        if s in held:
            last = held[s]["price"]
        elif q:
            last = q[0]
        elif s in queued:
            last = queued[s].est_price
        prev = _prev.get(s)
        if s in held:
            what, stop, target = "HOLDING: sells at the stop or target", held[s]["stop"], held[s]["target"]
        elif s in queued:
            what, stop, target = "BUY queued: buys 1 order at the next open", queued[s].stop, None
        else:
            what, stop, target = "Waiting: no buy signal on the last scan", None, None
        watch.append({"symbol": s, "price": last, "time": q[1] if q else "last close",
                      "change": (last / prev - 1) if (last and prev) else None,
                      "what": what, "qty": held[s]["qty"] if s in held else (queued[s].qty if s in queued else 0),
                      "stop": stop, "target": target})
    idx = quotes.get(index)
    out = {**base, "watch": watch, "card": market_card(market, cfg, quotes, watch_syms),
           "status": "HALTED" if st.halted else ("Paused" if st.pause_sessions_left > 0 else "Trading"),
           "halt_reason": st.halt_reason, "start_date": st.start_date, "last_session": st.last_date,
           "next_update": next_update, "why_no_buys": why_no_buys, "regime": regime,
           "initial": st.initial_capital, "deposits": st.deposits, "capital_in": capital_in,
           "cash": st.cash, "in_market": in_market, "total": total,
           "profit": total - capital_in, "profit_pct": total / capital_in - 1 if capital_in else 0.0,
           "open_profit": open_pnl, "day_return": st.day_return, "drawdown": st.drawdown,
           "index": {"symbol": index, "price": idx[0] if idx else None, "time": idx[1] if idx else None},
           "positions": positions,
           "orders": [{"side": o.side, "symbol": o.symbol, "qty": o.qty, "reason": o.reason,
                       "est_price": o.est_price, "stop": o.stop} for o in st.orders],
           "performance": perf,
           "trades": trades.iloc[::-1][["symbol", "qty", "entry_date", "entry_price", "exit_date", "exit_price",
                                        "pnl", "r_multiple", "exit_reason"]].head(200).to_dict("records"),
           "said_no": [{"reason": k, "times": v} for k, v in said_no.most_common(12)],
           "events": events.head(40)[["date", "kind", "symbol", "message"]].to_dict("records"),
           "equity_curve": [[str(d.date()), float(v)] for d, v in equity["equity"].items()],
           "backtest": bt, "rules": rules,
           "findings": _findings(market, st, perf, why_no_buys, bt)}
    return clean(out)


# ---------------------------------------------------------------- actions

def change_cash(market: str, amount: float) -> str:
    """Add (positive) or withdraw (negative) virtual money. Risk counters move with it."""
    if amount == 0:
        raise ValueError("Amount must not be zero.")
    with _lock:
        st = store().load_state(market)
        if st is None:
            raise ValueError("Market not started yet. Run the bot once first.")
        if amount < 0 and -amount > st.cash:
            raise ValueError(f"Only {st.cash:,.0f} free cash can be withdrawn. Money in shares stays invested.")
        st.cash += amount
        st.deposits += amount
        st.prev_equity += amount
        st.peak_equity += amount
        st.week_start_equity += amount
        word = "Added" if amount > 0 else "Withdrew"
        note = f"{word} {abs(amount):,.0f} virtual money from the website"
        store().save_state(market, st, note, date=str(clock.now().date()))
        settings.remember_budget(market, st.capital_in())         # this profile (or this trip) remembers its last budget
        return note


def manual_sell(market: str, symbol: str) -> str:
    """Sell a virtual position now at the latest price, with slippage and real charges."""
    with _lock:
        cfg = load_market(market)
        st = store().load_state(market)
        if st is None or symbol not in st.positions:
            raise ValueError(f"No open position in {symbol}.")
        quote = fetch_quotes([symbol]).get(symbol)
        price = quote[0] if quote else st.positions[symbol].last_close
        broker = VirtualBroker(make_cost_model(cfg["costs"]))
        today = clock.now(cfg["timezone"]).tz_localize(None).normalize()
        trade = broker.close(st, symbol, price, today, "Manual sell from the website")
        note = " ".join([f"Sold {trade.qty:g} at {trade.exit_price:.2f}. P&L {trade.pnl:,.2f}"]
                        + risk.record_trade(st, trade, cfg["risk"]))       # counts in the loss streak too
        store().save_state(market, st, note, trades=[trade], symbol=symbol, date=str(today.date()))
        return note


def next_open(cfg) -> pd.Timestamp | None:
    """When the market opens next, in market time (weekends skipped; holidays are not known)."""
    now = clock.now(cfg["timezone"])
    oh, om = (int(x) for x in cfg["session"]["open"].split(":"))
    for i in range(8):
        d = now.normalize() + pd.Timedelta(days=i)
        t = d + pd.Timedelta(hours=oh, minutes=om)
        if d.weekday() < 5 and t > now:
            return t
    return None


def market_open_now(cfg) -> bool:
    now = clock.now(cfg["timezone"])
    if now.weekday() >= 5:
        return False
    oh, om = (int(x) for x in cfg["session"]["open"].split(":"))
    ch, cm = (int(x) for x in cfg["session"]["close"].split(":"))
    mins = now.hour * 60 + now.minute
    return oh * 60 + om <= mins < ch * 60 + cm


def _tm_open_quotes(market: str, cfg: dict, symbols: list[str]) -> dict:
    """Time machine: the price at today's open plays the live price (nothing later in the day is used)."""
    today = clock.now(cfg["timezone"])
    out = {}
    for s in symbols:
        p = data.open_price(market, s, today.tz_localize(None).normalize())
        if p:
            out[s] = (p, today.strftime("%d %b %H:%M"), 0.0)
    return out


_tm_tick_at: dict[str, float] = {m: 0.0 for m in MARKETS}


def _tm_tick(market: str) -> None:
    """Called by the time machine every second: while its market is open, waiting buys fill at the day's open."""
    if time.time() - _tm_tick_at[market] < 5 or _runs[market]["running"]:
        return
    _tm_tick_at[market] = time.time()
    st = store().load_state(market)
    if st is not None and any(o.side == "BUY" for o in st.orders) and market_open_now(load_market(market)):
        with _doing(market, "Buying at today's opening price", "trade"):
            buy_live(market)


def buy_live(market: str) -> str:
    """Fill the waiting BUY orders now, at the live price, while the market is open.
    Same safety checks as the broker: price above the stop, risk not blown by a gap, enough cash."""
    from types import SimpleNamespace
    with _lock:
        cfg = load_market(market)
        st = store().load_state(market)
        if st is None:
            raise ValueError("The account is not started yet.")
        waiting = [o for o in st.orders if o.side == "BUY"]
        if not waiting:
            return "No buy orders are waiting."
        block = risk.account_block(st, cfg["risk"], stop_file=config.stopped(market))   # same as the trading part
        if block:
            note(market, "BLOCK", f"Waiting buys not filled: {block}")
            return f"New buys are blocked ({block}), so the waiting orders were not filled."
        if not market_open_now(cfg):
            return f"{INFO[market]['name']} market is closed. Waiting orders buy as soon as it opens."
        quotes = _tm_open_quotes(market, cfg, [o.symbol for o in waiting]) if clock.active() else fetch_quotes([o.symbol for o in waiting])
        limit = cfg["risk"]["max_positions"]
        today = clock.now(cfg["timezone"])
        stamp = today.strftime("%d %b")
        broker = VirtualBroker(make_cost_model(cfg["costs"]), target_r=cfg["strategy"].get("take_profit_r"),
                               max_gap_mult=cfg["risk"].get("max_gap_risk_mult", 1.5),
                               fractional=cfg["risk"].get("fractional_shares", False))
        day = today.tz_localize(None).normalize()
        blackout = day in config.load_events()[market]          # a news day cancels the buy, like at the next open
        notes = []
        for o in waiting:
            if len(st.positions) >= limit:
                st.orders = [x for x in st.orders if x.id != o.id]
                notes.append(f"Cancelled. {o.symbol}: only {limit} share(s) at a time")
                store().save_state(market, st, notes[-1], symbol=o.symbol, date=str(day.date()))
                continue
            q = quotes.get(o.symbol)
            if not q or not q[1].startswith(stamp):
                notes.append(f"{o.symbol}: no live price from today yet, still waiting")
                continue
            if o.limit is not None and q[0] > o.limit:         # a limit buy waits for its price (or the next open)
                notes.append(f"{o.symbol}: live price {q[0]:.2f} is above the limit {o.limit:.2f}, still waiting")
                continue
            bar = SimpleNamespace(Open=q[0], Close=q[0])
            pos, msg = broker.fill_entry(st, o, bar, day, blackout=blackout)
            st.orders = [x for x in st.orders if x.id != o.id]
            notes.append(("Bought live. " if pos else "Cancelled. ") + msg)
            note(market, "BUY" if pos else "CANCEL", msg, _short_sym(market, o.symbol))
            store().save_state(market, st, notes[-1], symbol=o.symbol, date=str(day.date()))
        msg = " | ".join(notes)
        if notes:                                     # shown in stage 5 of the page
            facts = stages.load_facts(market)
            facts["live"] = {"msg": msg, "at": time.strftime("%H:%M:%S")}
            stages.save_facts(market, facts)
        return msg


# ---------------------------------------------------------------- live watch of held shares
WATCH_MIN = 120          # seconds: check a held share at least every 2 minutes after buying
WATCH_MAX = 600          # seconds: never wait more than 10 minutes, even when it rises safely
WATCH_GROW = 1.5         # each safe, rising check stretches the gap: 2 -> 3 -> 4.5 -> 6.75 -> 10 minutes
_watch: dict[str, dict] = {m: {} for m in MARKETS}


def watch_live(market: str, now: float | None = None) -> list[str]:
    """While the market is open, check each held share on its own timer.
    Sell at once at the stop or the target. Move the stop to the buy price at +1R.
    Rising safely: check less often. Showing risk: back to every 2 minutes."""
    now = time.time() if now is None else now
    tm = bot_timers()
    with _lock:
        cfg = load_market(market)
        st = store().load_state(market)
        w = _watch[market]
        if st is None or not st.positions:
            w.clear()
            return []
        for s in [s for s in w if s not in st.positions]:
            del w[s]
        if not market_open_now(cfg):
            return []
        due = [s for s in st.positions if s not in w or now >= w[s]["next"]]
        if not due:
            return []
        with _doing(market, "Checking " + ", ".join(_short_sym(market, s) for s in due) + " on the live price", "held"):
            return _watch_due(market, cfg, st, w, due, now, tm)


def _watch_due(market, cfg, st, w, due, now, tm) -> list[str]:
    """Check the held shares that are due (called with the lock held)."""
    quotes = fetch_quotes(due)
    today = clock.now(cfg["timezone"])
    stamp, day = today.strftime("%d %b"), today.tz_localize(None).normalize()
    broker = VirtualBroker(make_cost_model(cfg["costs"]))
    notes, trades = [], []
    for s in due:
        p = st.positions[s]
        info = w.setdefault(s, {"every": tm["watch_min"], "last": None})
        q = quotes.get(s)
        if not q or not q[1].startswith(stamp):
            info.update(next=now + tm["watch_min"], state="Waiting for a live price", level="wait")
            note(market, "WAIT", f"no live price from today yet, checking again in {tm['watch_min'] / 60:g} min", _short_sym(market, s))
            continue
        price = float(q[0])
        r = p.risk_per_share if p.risk_per_share > 0 else max(p.entry_price - p.initial_stop, 1e-9)
        if price <= p.stop:
            why = "Breakeven stop hit (live check)" if p.breakeven else "Stop-loss hit (live check)"
            trades.append(broker.close(st, s, price, day, why))
            notes.append(f"{s}: sold at {price:.2f}, {why.lower()}")
            note(market, "SELL", f"sold at {price:.2f}, {why.lower()}", _short_sym(market, s))
            del w[s]
            continue
        if p.target is not None and price >= p.target:
            trades.append(broker.close(st, s, price, day, "Target hit (live check)"))
            notes.append(f"{s}: sold at {price:.2f}, profit target reached")
            note(market, "SELL", f"sold at {price:.2f}, profit target reached", _short_sym(market, s))
            del w[s]
            continue
        if not p.breakeven and price >= p.entry_price + r:
            p.breakeven, p.stop = True, max(p.stop, p.entry_price)
            notes.append(f"{s}: up 1R, stop moved to the buy price {p.stop:.2f}")
            note(market, "STOP", f"up 1R, stop moved to the buy price {p.stop:.2f}", _short_sym(market, s))
        cushion = (price - p.stop) / r
        last = info.get("last")
        risky = cushion < 1.0 or price < p.entry_price or (last is not None and price < last - 0.25 * r)
        rising = last is not None and price > last
        if risky:
            info.update(every=tm["watch_min"], state=f"Showing risk: checking every {tm['watch_min'] / 60:g} min", level="risk")
        elif rising:
            info["every"] = min(tm["watch_max"], info["every"] * WATCH_GROW)
            info.update(state=f"Rising safely: checking every {info['every'] / 60:.1f} min", level="safe")
        else:
            info.update(state=f"Steady: checking every {info['every'] / 60:.1f} min", level="steady")
        info.update(last=price, next=now + info["every"], cushion=round(cushion, 2), price=price,
                    checked=time.strftime("%H:%M:%S", time.localtime(now)),
                    next_at=time.strftime("%H:%M", time.localtime(now + info["every"])))
        note(market, "WATCH", f"{price:.2f}, {cushion:.1f}R above the stop {p.stop:.2f}. {info['state']}", _short_sym(market, s))
    if trades:
        _job[market]["trade"] = [time.time(), time.time(), "Sold at the live price"]
    for t in trades:                               # a live sell counts in the loss streak, like the engine's
        for msg in risk.record_trade(st, t, cfg["risk"]):
            notes.append(msg)
            note(market, "RISK", msg)
    if notes or trades:
        store().save_state(market, st, " | ".join(notes) or None, trades=trades, date=str(day.date()))
    return notes


def auto_live_buy() -> list[str]:
    done = []
    for m in AUTO_MARKETS:
        st = store().load_state(m)
        if st is None or not any(o.side == "BUY" for o in st.orders):
            continue
        if market_open_now(load_market(m)) and not _runs[m]["running"]:
            with _doing(m, "Buying the waiting orders at the live price", "trade"):
                msg = buy_live(m)
            if msg.startswith(("Bought", "Cancelled")):
                done.append(f"{m}: {msg}")
    return done


def set_stop(market: str, on: bool) -> str:
    f = config.stop_file_for(market)
    if on:
        f.write_text("Created from the website. Delete to allow new buys.\n", encoding="utf-8")
        return f"{INFO[market]['name']}: new buys stopped. Open positions keep their stops."
    f.unlink(missing_ok=True)
    if config.STOP_FILE.exists():
        return "This market is allowed again, but the global STOP file still blocks both markets."
    return f"{INFO[market]['name']}: new buys allowed again."


class RunStopped(RuntimeError):
    """You pressed Stop: the Run ends at its next safe point, before the account is changed."""


def _check_stop(market: str) -> None:
    if _runs[market].get("cancel"):
        raise RunStopped("Stopped by you. Nothing was saved: the account is as it was before this Run. "
                         "Press Run bot to run it again.")


def stop_run(market: str) -> str:
    """Stop this market's Run (and its past-years test). Waiting buys, held shares and the live checks carry on."""
    run = _runs[market]
    if not run["running"]:
        return f"No {INFO[market]['name']} Run is working."
    run.update(cancel=True, held=True)                  # held: the time machine or auto run does not start it again
    if _tests[market]["running"]:
        _tests[market]["cancel"] = True
    note(market, "CANCEL", "Stop pressed: the Run stops at its next safe point, nothing is saved")
    return f"Stopping the {INFO[market]['name']} Run. It stops at its next safe point (within about a minute), then the other market can run."


def _progress(market: str, stage: int) -> None:
    """Told by the daily run each time one of the five stages starts."""
    if stage < 5:                                       # stage 5 comes after the trading part: too late to stop cleanly
        _check_stop(market)
    _runs[market]["stage"] = stage
    note(market, "RUN", "Loading the practice account" if stage == 1           # the page shows 4 steps: stage 1 is part of step 1
         else f"Step {stage - 1} of 4: {stages.NAMES[stage - 1]}")
    if stage >= 4 and not _runs[market].get("noted"):  # both sides are done: tell what they found
        _runs[market]["noted"] = True
        _note_sorting(market)
        _note_held(market)
    if stage == 5:
        _note_decision(market)


FEED_BEST = 5                       # the live feed names this many matching shares, then gives a count
CHECK_WORDS = {"trend": "trend not up", "breakout": "no new {n}-day high", "volume": "volume too low",
               "liquid": "too little trading"}


def _note_sorting(market: str) -> None:
    """Stage 2 and 3 as single lines: the market check, then every share with the check it failed."""
    look = stages.load_facts(market).get("look") or {}
    if not look:
        return
    note(market, "MARKET", look.get("text", ""))
    n = look.get("breakout_days", 20)
    many = look.get("rules")
    skipped: dict[str, int] = {}
    for sym, miss in (look.get("misses") or {}).items():             # one line for all skipped shares, by reason
        why = ", ".join(CHECK_WORDS[k].format(n=n) for k in miss) or "price data not ready"
        if many:
            why = "too little trading" if "liquid" in miss else "no strategy matched"
        skipped[why] = skipped.get(why, 0) + 1
    if skipped:
        note(market, "SKIP", "; ".join(f"{k}: {v}" for k, v in sorted(skipped.items(), key=lambda x: -x[1])))
    qualified = look.get("qualified", [])
    for sym in qualified[:FEED_BEST]:                                 # the best few by name, the rest as a count
        if many:
            note(market, "PASS", "matched: " + ", ".join((look.get("matched") or {}).get(sym, [])), sym)
        else:
            note(market, "PASS", f"passed every check: trend up, new {n}-day high, strong volume", sym)
    if len(qualified) > FEED_BEST:
        note(market, "PASS", f"and {len(qualified) - FEED_BEST} more shares matched")
    note(market, "SCAN", f"{look.get('scanned', 0)} shares sorted: {len(look.get('qualified', []))} "
                         + (f"matched at least one of {many} strategies" if many else "passed every check"))


def _note_held(market: str) -> None:
    for h in stages.load_facts(market).get("held") or []:
        ok = {True: "pattern still fine", False: "pattern broken", None: "no price today"}[h["pattern_ok"]]
        note(market, "HOLD", f"{h['rule']} ({h['family']}): {ok}, near future {h['band']}", h["symbol"])


RECHECK_EVERY = 300                 # seconds: every 5 minutes while the market is open both sides look again on live prices
_recheck: dict[str, dict] = {}
_recheck_at: dict[str, float] = {m: 0.0 for m in MARKETS}
_recheck_base: dict[tuple, dict] = {}


def recheck(market: str) -> dict | None:
    """Every 5 minutes while the market is open: the whole market (index vs its 200-day average, how many shares
    pass the quick check on live prices) and each held share against its band. Only looks: buying stays once a day."""
    from . import predict
    cfg = load_market(market)
    st = store().load_state(market)
    if st is None or clock.active() or not market_open_now(cfg):
        return None
    idx, syms = cfg["regime"]["index"], config.universe_tickers(cfg)
    key = (market, str(clock.now().date()))
    if key not in _recheck_base:                          # 200-day average and price 63 days ago, once per day
        base = {}
        for t in syms + [idx]:
            df = data.read_cache(market, t)
            if df is not None and len(df) >= 200:
                base[t] = (float(df["Close"].tail(200).mean()), float(df["Close"].iloc[-63]))
        _recheck_base.clear()
        _recheck_base[key] = base
    base = _recheck_base[key]
    with _doing(market, "Looking again on live prices", *(("market", "held") if st.positions else ("market",))):
        quotes = fetch_quotes(syms + [idx])
    live = {t: q[0] for t, q in quotes.items() if q}
    quick = sum(1 for t in syms if t in base and t in live and (live[t] > base[t][0] or live[t] > base[t][1]))
    mood = ("index above its 200-day average" if live.get(idx, 0) > base.get(idx, (float("inf"), 0))[0]
            else "index under its 200-day average") if idx in live and idx in base else "index price not available"
    band = float(((cfg.get("strategy") or {}).get("predict") or {}).get("band_atr", 1.0))
    held = {_short_sym(market, s): predict.status(p, live[s], band) for s, p in st.positions.items() if s in live}
    out = {"at": time.strftime("%H:%M"), "epoch": time.time(), "market": mood, "quick": quick,
           "all": sum(1 for t in syms if t in base), "held": held}
    _recheck[market] = out
    text = f"Re-check: {mood}; {quick} of {out['all']} shares pass the quick check"
    if held:
        text += "; " + ", ".join(f"{s} {v}" for s, v in held.items())
    note(market, "LIVE", text)
    return out


def _fresh_recheck(market: str) -> dict | None:
    r = _recheck.get(market)
    return r if r and time.time() - r["epoch"] < 2 * bot_timers()["recheck"] and not clock.active() else None


def _note_decision(market: str) -> None:
    facts = stages.load_facts(market)
    if facts.get("carried"):
        note(market, "RUN", "No new finished session, so nothing new to buy or sell")
        return
    d = facts.get("decision") or {}
    for e in facts.get("exits", []):
        note(market, "SELL", f"sold {e['qty']:g} at {e['price']:.2f} ({_plain_exit(e['why'])})", _short_sym(market, e["symbol"]))
    for b in d.get("buys", []):
        note(market, "ORDER", f"buy {b['qty']:g} queued for the next open, stop {b['stop']:.2f}", _short_sym(market, b["symbol"]))
    for r in d.get("rejects", []):
        note(market, "REFUSE", r["why"], _short_sym(market, r["symbol"]))
    if d.get("blocked"):
        note(market, "BLOCK", d["blocked"])
    if not (d.get("buys") or facts.get("exits") or d.get("rejects") or d.get("blocked")):
        note(market, "RUN", "No buy and no sell today")


class WorldChanged(RuntimeError):
    """The profile or the time machine changed while a run waited: the run stops without saving."""


def _gate(market: str, gen: int) -> str | None:
    """Step 3 of a Run: is the strategy validated by a past-years test? Reuse a saved test while it is up to date;
    otherwise start one and wait for it. The account lock is let go while waiting, so the live stop checks,
    a manual sell and cash changes keep working. Returns why new buys are blocked, or None."""
    _check_stop(market)
    cfg = load_market(market)
    marks = cfg.get("pass") or {}
    state = stages.passport_state(stages.rejudge(stages.load_passport(market), marks), stages.fingerprint(cfg),
                                  max_days=int(marks.get("max_age_days", stages.PASSPORT_MAX_DAYS)))
    old_world_before = False
    for round_ in range(2):                           # a second round only when an old world's test was in the way
        if state not in ("missing", "changed", "old") and not _tests[market]["running"]:
            break
        old_world = _tests[market]["running"] and _tests[market].get("gen", gen) != gen
        if round_ and not old_world_before:
            break
        why = {"missing": "No past-years test yet", "changed": "Settings changed since the last past-years test",
               "old": "The past-years test is out of date"}.get(state, "A past-years test is already running")
        note(market, "TEST", f"{why}: testing first (7 tests, a minute or two), then the run goes on")
        old_world_before = old_world
        _start_test(market)
        _lock.release()                       # held by _run_daily: let the live watch work while the test runs
        try:
            _wait_test(market, gen=gen)
        finally:
            _lock.acquire()
        _check_stop(market)
        if gen != _world_gen:
            raise WorldChanged("The profile or the time machine changed during the run, so this run stopped without saving.")
        state = stages.passport_state(stages.rejudge(stages.load_passport(market), marks), stages.fingerprint(cfg),
                                      max_days=int(marks.get("max_age_days", stages.PASSPORT_MAX_DAYS)))
    block = stages.validation_block(cfg, market)
    if block is None:
        t = (stages.load_passport(market) or {}).get("test") or {}
        note(market, "TEST", f"Past-years test passed (profit factor {t.get('pf', float('nan')):.2f}, biggest drop "
                             f"{abs(t.get('dd') or 0):.1%}): new buys allowed, each still checked by the risk rules")
    else:
        if _tests[market]["error"]:
            block += f" The last test failed: {_tests[market]['error']}"
        note(market, "TEST", block + " Shares you hold are still watched and sold by their rules.")
    return block


def _start_test(market: str) -> bool:
    with _start_lock:                        # two clicks at once start one test
        t = _tests[market]
        if t["running"]:
            return False
        t.update(running=True, started=time.strftime("%H:%M:%S"), finished=None, error=None, step=0, gen=_world_gen, cancel=False)
    threading.Thread(target=_run_test, args=(market,), daemon=True).start()
    return True


class TestCancelled(RuntimeError):
    """The profile or the time machine changed: this test belongs to the old world and stops without saving."""


def _run_test(market: str) -> None:
    from . import study
    t = _tests[market]

    def tell(k: int, n: int) -> None:                   # between the 7 tests: stop if the world changed
        if t.get("cancel"):
            raise TestCancelled("stopped: the time machine or the profile changed")
        t.update(step=k, steps=n)
    try:
        study.passport(market, progress=tell)
    except TestCancelled:
        log.info("%s: past-years test of the old world stopped", market)
    except Exception as exc:
        t["error"] = str(exc)
        log.exception("Past-years test failed")
    finally:
        t.update(running=False, finished=time.strftime("%H:%M:%S"), cancel=False)


def _wait_test(market: str, limit: float = 1800, gen: int | None = None) -> bool:
    """Hold the daily run at step 3 while the past-years test thread works. True if a test was waited for.
    Stops waiting at once when the profile or the time machine changes."""
    waited, end = False, time.monotonic() + limit
    while (_tests[market]["running"] and time.monotonic() < end and (gen is None or gen == _world_gen)
           and not _runs[market].get("cancel")):
        waited = True
        time.sleep(0.5)
    return waited


def _run_daily(market: str) -> None:
    buf, run, gen = io.StringIO(), _runs[market], _world_gen
    try:
        with _lock, contextlib.redirect_stdout(buf):
            s = store()                                  # this run's account file, even if the world changes later
            before = s.load_state(market)
            before_date = before.last_date if before else None
            bot.daily([market], telegram=False, progress=_progress, gate=lambda m: _gate(m, gen))
            after = s.load_state(market)
        problem = stages.load_facts(market).get("problem")
        if problem:
            raise RuntimeError(problem)
        cfg = load_market(market)
        if gen == _world_gen and after is not None and market_open_now(cfg) and any(o.side == "BUY" for o in after.orders):
            buy_live(market)                              # stage 5 ends by filling the waiting buys at the live price
            after = s.load_state(market)                  # the summary tells what is true after that buy
        if after is None:
            run["summary"] = "Could not open the account. See the details below."
        elif after.last_date != before_date:
            first = "opened the account and processed" if before is None else "processed"
            run["summary"] = (f"{INFO[market]['name']}: {first} the session of {after.last_date}. "
                              f"{len(after.positions)} shares held, {sum(o.side == 'BUY' for o in after.orders)} "
                              f"buy orders waiting for the next open.")
        else:
            run["summary"] = f"{INFO[market]['name']}: nothing new to process. " + _next_text(cfg, after.last_date)
        run["error"] = None
        run["stage"] = stages.FINISHED
        note(market, "DONE", run["summary"] or "Run finished")
    except RunStopped as exc:
        run["error"] = str(exc)
        note(market, "CANCEL", "Run stopped by you, nothing saved")
        log.info("%s: run stopped by the user", market)
    except WorldChanged:
        run.update(error=None, stage=stages.FINISHED)    # the old world's run stopped quietly; the new session starts clean
        log.info("%s: run of the old world stopped", market)
    except Exception as exc:
        run["error"] = str(exc)
        note(market, "ERROR", f"Run stopped: {exc}")
        log.exception("Daily run failed")
    finally:
        run.update(running=False, finished=time.strftime("%H:%M:%S"), output=buf.getvalue()[-4000:], cancel=False)
        _quotes.clear()


# ---------------------------------------------------------------- auto mode

PLAIN_EXIT = [("Target", "Profit target reached"), ("Gapped above", "Profit target reached"),
              ("Breakeven or trailing", "Safety stop moved up and was hit"), ("Stop-loss", "Safety stop hit, loss limited"),
              ("Gapped below", "Price opened below the safety stop"), ("fell below", "Trend turned down"),
              ("Manual", "Sold by you")]


def _plain_exit(reason: str) -> str:
    return next((text for key, text in PLAIN_EXIT if key in (reason or "")), reason or "")


FEED_TAGS = {"fill": "BUY", "exit": "SELL", "order": "ORDER", "cancel": "CANCEL", "blocked": "BLOCK", "risk": "RISK"}


def kill_feed(market: str, n: int = 12) -> list[dict]:
    """Kill feed: the latest real events of this market, newest first. Read from the saved event log only."""
    suffix = load_market(market)["universe"].get("suffix", "")
    out = []
    for e in store().events(market, limit=400).itertuples():
        msg, kind = str(e.message), e.kind
        if kind == "manual":                                  # live buys, live sells, website actions
            low = msg.lower()
            tag = "SELL" if ("sold" in low or "sell" in low) else "BUY" if "bought" in low else \
                  "STOP" if "stop moved" in low else "CANCEL" if low.startswith("cancelled") else "LIVE"
        elif kind == "note" and e.symbol and "stop moved" in msg:
            tag = "STOP"
        elif kind in FEED_TAGS:
            tag = FEED_TAGS[kind]
        else:
            continue                                          # daily refusals already have their own list
        sym = str(e.symbol or "")
        out.append({"date": e.date, "tag": tag, "symbol": sym[:-len(suffix)] if suffix and sym.endswith(suffix) else sym,
                    "text": msg})
        if len(out) >= n:
            break
    return out


def feed_stats(market: str) -> dict:
    """Kill feed numbers: what the last run checked, and what the bot did since the account opened."""
    ev = store().events(market, limit=100000)
    kinds = Counter(ev["kind"]) if len(ev) else Counter()
    live_buys = int(ev["message"].astype(str).str.startswith("Bought live").sum()) if len(ev) else 0
    look = stages.load_facts(market).get("look") or {}
    return {"checked": look.get("scanned"), "passed": len(look["qualified"]) if look else None,
            "bought": int(kinds["fill"]) + live_buys, "sold": int(len(store().trades(market))),
            "refused": int(kinds["reject"]), "blocked": int(kinds["blocked"])}


def _stages_for(market: str, cfg: dict, o: dict) -> list[dict]:
    """The five stage reports for the page. Every line comes from the saved facts of the last run,
    the live account or the saved past-years test. Nothing is invented here."""
    run = dict(_runs[market])                 # stage first, facts second: facts are saved before a stage starts
    facts, started = stages.load_facts(market), bool(o["started"])
    st = store().load_state(market) if started else None
    return stages.build({
        "name": INFO[market]["name"], "hours": INFO[market]["hours"], "open": market_open_now(cfg),
        "currency": cfg["currency"], "suffix": cfg["universe"].get("suffix", ""), "started": started,
        "limit": cfg["risk"]["max_positions"],
        "account": stages.account_facts(cfg, st) if started else (facts.get("account") if run["running"] else None),
        "facts": facts, "positions": o.get("positions", []), "orders": o.get("orders", []), "trades": o.get("trades", []),
        "block": risk.account_block(st, cfg["risk"], config.stopped(market)) if st else None,
        "passport": stages.rejudge(stages.load_passport(market), cfg.get("pass")), "fingerprint": stages.fingerprint(cfg),
        "test": dict(_tests[market]), "today": clock.now(), "monitor": _monitor()["text"], "recheck": _fresh_recheck(market),
        "running": run["running"], "cur": run["stage"], "error": run["error"]})


def simple_view(market: str) -> dict:
    """Very small picture of the account for the simple page: budget, spent, price now, sold price, one graph."""
    cfg = load_market(market)
    o = overview(market, live=True)
    suffix = cfg["universe"].get("suffix", "")
    plain = lambda s: s[:-len(suffix)] if suffix and s.endswith(suffix) else s
    names = list(cfg["universe"].get("symbols") or []) + list(cfg["universe"].get("etfs") or [])
    now = clock.now(cfg["timezone"])
    out = {"market": market, "name": INFO[market]["name"], "currency": cfg["currency"], "universe": names,
           "open": market_open_now(cfg), "clock": now.strftime("%a %H:%M"), "zone": cfg["timezone"],
           "hours": INFO[market]["hours"], "run": o["run"], "started": o["started"], "test": dict(_tests[market]),
           "limit": cfg["risk"]["max_positions"], "budget": cfg["capital"],
           "page": int((WEB_DIR / PAGE).stat().st_mtime), "auto_on": AUTO_ON,         # an open tab reloads itself when the page changes
           "sim": clock.info(),                                                               # time machine clock, None in real time
           "profile": profiles.active(), "monitor": _monitor(), "recheck": _fresh_recheck(market),
           "timers": _timers(market, cfg), "jobs": _jobs_view(market), "modes": step_modes(market),
           "stopped": config.stopped(market), "global_stop": config.STOP_FILE.exists(),
           "halted": o.get("status") == "HALTED",
           "opens": _opens_text(cfg),
           "test_pf": ((stages.load_passport(market) or {}).get("test") or {}).get("pf")}           # opening a page only reads: it starts nothing
    if not o["started"]:
        out.update(step=0, state="new", headline="Press Run bot. It opens a practice account with the budget below.",
                   stages=_stages_for(market, cfg, o), feed=[], feed_stats=feed_stats(market), passed=[])
        return clean(out)
    held = o["positions"][0] if o["positions"] else None
    queued = next((x for x in o["orders"] if x["side"] == "BUY"), None)
    sold = o["trades"][0] if o["trades"] else None
    trend_ok = next((r["ok"] for r in o["rules"] if r["rule"] == "Market trend filter"), True)
    target = held or queued
    ticker = target["symbol"] if target else None
    chart = None
    if ticker:
        s = _series(market, ticker, fetch_quotes([ticker]), plain(ticker))
        if s:
            chart = {"kind": "share", "label": plain(ticker), "points": s["trend"], "time": s["time"], "price": s["last"],
                     "buy": held["buy_price"] if held else None, "stop": (held or queued)["stop"],
                     "target": held["target"] if held else None}
    if chart is None and o.get("card") and o["card"].get("index"):
        k = o["card"]["index"]
        chart = {"kind": "market", "label": k["label"], "points": k["trend"], "time": k["time"], "price": k["last"],
                 "buy": None, "stop": None, "target": None}
    spent = held["qty"] * held["buy_price"] if held else 0.0
    tgt = None
    if target:
        tgt = {"symbol": plain(ticker), "qty": target["qty"],
               "price": held["price"] if held else (chart["price"] if chart else None),
               "approx_cost": None if held else target["qty"] * (queued["est_price"] or 0),
               "stop": (held or queued)["stop"], "profit_target": held["target"] if held else None,
               "buy_price": held["buy_price"] if held else None, "pnl": held["pnl"] if held else None,
               "price_time": held["price_time"] if held else (chart["time"] if chart else None)}
    sold_out = None
    if sold:
        sold_out = {"symbol": plain(sold["symbol"]), "qty": sold["qty"], "price": sold["exit_price"], "pnl": sold["pnl"],
                    "date": sold["exit_date"], "why": _plain_exit(sold["exit_reason"])}
    out.update(budget=o["capital_in"], spent=spent, cash=o["cash"], chart=chart, target=tgt, sold=sold_out,
               card=o.get("card"), value_now=o["in_market"], floating=o["open_profit"], total=o["total"],
               booked=o["performance"]["net_pnl"], positions=o["positions"], orders=o["orders"],
               trades=o["trades"][:20], said_no=o["said_no"][:6],
               watch={s: {k: v for k, v in i.items() if k not in ('next',)} for s, i in _watch[market].items()},
               level=next((w for w in ('Good', 'Normal', 'Bad') if (o.get('regime') or '').startswith(w)), None),
               level_text=_level_text(cfg, o.get("regime") or ""))
    n = len(names)
    if o["status"] == "HALTED" or o["stop"]:
        step, state, line = 1, "paused", "The bot is paused by a safety stop. It will not buy."
    elif not trend_ok and not held:
        bm = (cfg["regime"].get("bad_market") or {}).get("allow")
        step, state, line = 1, "blocked", ("The whole market is falling. Extra-safe mode: the bot only buys a very strong share, at a tiny size."
                                           if bm else "The whole market is falling, so the bot keeps your money today.")
    elif held:
        step, state, line = 5, "holding", f"Bought {plain(ticker)}. The bot is watching the price and will sell by itself."
    elif queued and out["open"]:
        step, state, line = 4, "buying", f"Locked on {plain(ticker)}. Buying now at the live price."
    elif queued:
        step, state, line = 4, "waiting", f"Locked on {plain(ticker)}. It buys as soon as the market opens."
    elif sold:
        step, state, line = 2, "searching", f"Sold {plain(sold['symbol'])}. Looking for the next share."
    else:
        step, state, line = 2, "nothing", f"Searched {n} shares. None is strong enough today, so nothing is bought."
    out.update(step=step, state=state, headline=line, stages=_stages_for(market, cfg, o), feed=kill_feed(market),
               feed_stats=feed_stats(market), passed=(stages.load_facts(market).get("look") or {}).get("qualified", []))
    return clean(out)


def _opens_text(cfg: dict) -> str | None:
    """'opens Tue 09:15, in 16 h 4 min' while the market is closed; the bot's own clock (also in the time machine)."""
    if market_open_now(cfg):
        return None
    t = next_open(cfg)
    if t is None:
        return None
    mins = int((t - clock.now(cfg["timezone"])).total_seconds() // 60)
    wait = f"{mins // 60} h {mins % 60} min" if mins >= 60 else f"{mins} min"
    return f"opens {t.strftime('%a %H:%M')}, in {wait}"


def _level_text(cfg: dict, regime: str) -> str:
    """What the market level really means for buying, from the last run's own words."""
    r, rc = float(cfg["risk"]["risk_per_trade"]), cfg["regime"]
    if regime.startswith("Good"):
        return f"full risk {r:.2%}"
    if regime.startswith("Normal"):
        return f"reduced risk {r * float(rc.get('normal_risk_mult', 0.5)):.2%}"
    if "Extra-safe mode" in regime:
        return f"extra-safe, tiny size {r * float((rc.get('bad_market') or {}).get('risk_mult', 0.2)):.2%}, strongest share only"
    return "no new buys"


def step_modes(market: str) -> dict:
    """THE one source of truth for the reactor on page steps 1 and 2 (rules: CLAUDE.md, "Step animations").
    Keys are the stage numbers of the page steps: 2 = step 1 (search whole market), 3 = step 2 (search my shares).
    mode "reactor" = the step's work repeats by itself (scheduled); None = the page shows the step's own state:
    the ring while a Run works on it, a tick when done, grey while waiting. pace: "live" while its market is open
    (on the trip clock in the time machine), "standby" while it is closed."""
    cfg, st, run, tm = load_market(market), store().load_state(market), _runs[market], bot_timers()
    trip = clock.active()
    duty = timemachine.drives(market) if trip else AUTO_ON and market in AUTO_MARKETS
    first_look = run["running"] and run.get("stage", 0) <= 3      # the Run's first look at both sides: the ring shows
    pace = "live" if market_open_now(cfg) else "standby"
    held = bool(st and st.positions)
    pending = bool(st and any(o.side == "BUY" for o in st.orders))   # shares step 1 chose, waiting to be bought
    stopped = bool(run.get("held"))                                # you pressed Stop: no Run starts by itself
    one = duty and st is not None and not stopped and not first_look
    # step 2: live checks of held shares and waiting buys keep going after Stop; in a trip they happen only at its Runs
    two = duty and (held or pending) and not first_look and not (trip and stopped)
    why1 = ("The trip searches the whole market after each close" if trip else
            f"Looked at again every {tm['recheck'] / 60:g} min while the market is open")
    why2 = ("Held and waiting shares are checked at each trip Run" if trip else
            f"Held shares checked every {tm['watch_min'] / 60:g}–{tm['watch_max'] / 60:g} min, waiting buys every {tm['loop']} s")
    return {2: {"mode": "reactor" if one else None, "pace": pace, "why": why1 if one else None},
            3: {"mode": "reactor" if two else None, "pace": pace, "why": why2 if two else None}}


def _timers(market: str, cfg: dict) -> dict:
    """Epoch times of the next jobs of the auto loop, for the side panel countdowns (the page counts down itself)."""
    st, tm = store().load_state(market), bot_timers()
    at = _loop["at"] or _loop["started"]
    return {"on": AUTO_ON and not clock.active(), "tm": clock.active(), "now": time.time(), "open": market_open_now(cfg),
            "every": tm["loop"], "recheck_every": tm["recheck"], "day_every": tm["day_check"], "auto_run": AUTO_RUN,
            "watch_min": tm["watch_min"], "watch_max": tm["watch_max"], "values": tm, "defaults": TIMER_DEFAULTS,
            "loop_next": (at + tm["loop"]) if at else None,
            "recheck_next": (_recheck_at[market] + tm["recheck"]) if _recheck_at[market] else None,
            "day_next": (_loop["day_check"] + tm["day_check"]) if _loop["day_check"] else None,
            "held": {_short_sym(market, s): (_watch[market].get(s) or {}).get("next") for s in (st.positions if st else {})},
            "buys": sum(1 for o in (st.orders if st else []) if o.side == "BUY")}


def _monitor() -> dict:
    """The real monitoring state, for the page: what checks itself, how often, and what needs Run bot."""
    if clock.active():
        text = "Time machine: it runs each market day by itself on its own clock. Live checks are off."
    elif not AUTO_ON:
        text = "Auto checks are off (TBOT_AUTO=0): nothing is watched until you press Run bot."
    else:
        text = (f"While this server runs (the page may be closed) and the market is open: the whole market and your shares are "
                f"looked at again every {bot_timers()['recheck'] / 60:g} minutes, held shares are checked against their stop and "
                f"target every {bot_timers()['watch_min'] / 60:g} to {bot_timers()['watch_max'] / 60:g} minutes, waiting buys every "
                f"{bot_timers()['loop']} s. "
                + ("A new market day starts a Run by itself." if AUTO_RUN else "A new market day needs Run bot (auto run is off)."))
    return {"auto": AUTO_ON and not clock.active(), "auto_run": AUTO_RUN, "every": bot_timers()["loop"], "day_check": bot_timers()["day_check"],
            "markets": AUTO_MARKETS, "text": text}


def _start_run(market: str) -> bool:
    with _start_lock:                         # two clicks at once start one run
        run = _runs[market]
        if run["running"]:
            return False
        run.update(running=True, cancel=False, held=False, started=time.strftime("%H:%M:%S"), finished=None, error=None, output="", summary=None, stage=1,
                   noted=False)
    threading.Thread(target=_run_daily, args=(market,), daemon=True).start()
    return True


def auto_tick(now: float | None = None) -> list[str]:
    """Run the bot for every started market that has a finished session waiting. Returns markets started.
    With AUTO_RUN off it only says once per session that a new day is ready, and starts nothing."""
    now = time.time() if now is None else now
    started = []
    for m in AUTO_MARKETS:
        st = store().load_state(m)
        if st is None or _runs[m]["running"]:
            continue                                   # a market starts when you first open it
        expected, _ = data.session_clock(load_market(m))
        if st.last_date and pd.Timestamp(st.last_date) >= expected:
            continue
        a = _auto[m]
        if a["expected"] == expected and now - a["at"] < AUTO_RETRY:
            continue                                   # already tried for this session, wait a while
        if not AUTO_RUN:
            if a["expected"] != expected:
                a.update(expected=expected, at=now)
                note(m, "IDLE", "A new day of prices is ready. Press Run bot to check it.")
            continue
        a.update(expected=expected, at=now, when=time.strftime("%d %b %H:%M"))
        if _auto_start_run(m):
            started.append(m)
    return started


def heartbeat(market: str, now: float | None = None) -> None:
    """With nothing else happening for a while, one line per held share or waiting order, so the feed shows the bot is awake."""
    now = time.time() if now is None else now
    if now - _last_line[market] < HEARTBEAT:
        return
    cfg = load_market(market)
    st = store().load_state(market)
    if st is None:
        return
    where = "Market open" if market_open_now(cfg) else "Market closed"
    buys = [o for o in st.orders if o.side == "BUY"]
    if not st.positions and not buys:
        note(market, "IDLE", f"{where}. No shares held. Waiting for the next finished session.")
        return
    for s, p in st.positions.items():
        q = _quotes.get(s)
        price = q[0] if q else p.last_close
        r = p.risk_per_share if p.risk_per_share > 0 else max(p.entry_price - p.initial_stop, 1e-9)
        target = f", target {p.target:.2f}" if p.target is not None else ""
        note(market, "HOLD", f"{price:.2f}, stop {p.stop:.2f}{target}, {(price - p.stop) / r:.1f}R above the stop. {where}.",
             _short_sym(market, s))
    for o in buys:
        note(market, "ORDER", f"buy {o.qty:g} waiting for the {'live price' if where == 'Market open' else 'next open'}",
             _short_sym(market, o.symbol))


_loop = {"at": 0.0, "day_check": 0.0, "started": 0.0}   # when the auto loop last woke / last looked for a new day


def _auto_loop() -> None:
    _loop["started"] = time.time() + 20
    time.sleep(20)
    while True:
        _loop["at"] = time.time()
        for m in AUTO_MARKETS:                # the page flares the reactor at each wake
            _job[m]["wake"] = [_loop["at"], None, "Bot loop checked"]
        if clock.active():                # the time machine drives its own runs on its own clock
            _nap()
            continue
        try:
            with _world_lock:
                for m in AUTO_MARKETS:
                    watch_live(m)
                auto_live_buy()
                for m in AUTO_MARKETS:
                    heartbeat(m)
                    if (time.time() - _recheck_at[m] >= bot_timers()["recheck"] and not _runs[m]["running"]
                            and not _runs[m].get("held")):
                        _recheck_at[m] = time.time()
                        recheck(m)
                if time.time() - _loop["day_check"] >= bot_timers()["day_check"]:
                    _loop["day_check"] = time.time()
                    auto_tick()
        except Exception:
            log.exception("Auto check failed")
        for m in AUTO_MARKETS:
            _job[m]["wake"][1] = time.time()
        _nap()


def _nap() -> None:
    """Wait until the next loop is due; a timer made shorter in the side panel counts at once."""
    start = _loop["at"] or time.time()
    while time.time() < start + bot_timers()["loop"]:
        time.sleep(1)


@api.on_event("startup")
def _start_auto() -> None:
    profiles.use(profiles.active())                    # the profile picked last time
    if AUTO_ON:
        threading.Thread(target=_auto_loop, daemon=True).start()


# ---------------------------------------------------------------- HTTP

class CashBody(BaseModel):
    amount: float


class StopBody(BaseModel):
    on: bool


def _ok(fn, *args) -> dict:
    try:
        return {"ok": True, "message": fn(*args)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _check(market: str) -> None:
    if market not in MARKETS:
        raise HTTPException(status_code=404, detail=f"Unknown market {market}")


@api.get("/")
def index_page():
    return FileResponse(WEB_DIR / PAGE, headers={"Cache-Control": "no-store"})   # never show an old copy             # main page: Bootstrap + Chart.js + anime.js + Lucide


@api.get("/simple")
def simple_page():
    return FileResponse(WEB_DIR / "simple.html")          # the earlier simple view, kept


from fastapi.staticfiles import StaticFiles  # noqa: E402
api.mount("/vendor", StaticFiles(directory=WEB_DIR / "vendor"), name="vendor")


@api.get("/full")
def full_page():
    return FileResponse(WEB_DIR / "index.html")           # the detailed view, hidden for now


@api.get("/api/simple/{market}")
def api_simple(market: str):
    _check(market)
    return simple_view(market)


@api.get("/api/live/{market}")
def api_live(market: str, after: int = 0):
    """New live feed lines after the given id, oldest first."""
    _check(market)
    if not _activity[market] and not clock.active():
        try:
            heartbeat(market)
        except Exception:
            log.exception("Heartbeat failed")
    items = list(_activity[market])
    return {"items": [x for x in items if x["id"] > after], "last": items[-1]["id"] if items else 0,
            "jobs": _jobs_view(market), "now": time.time()}


@api.get("/api/markets")
def api_markets():
    """Short cards for the start screen."""
    cards = []
    for m in MARKETS:
        o = overview(m, live=False)
        cards.append({k: o.get(k) for k in ("market", "name", "exchange", "hours", "universe", "currency",
                                            "started", "status", "total", "profit", "profit_pct",
                                            "last_session", "regime", "stop")} |
                     {"positions": len(o.get("positions") or [])})
    return clean({"markets": cards})


@api.get("/api/market/{market}")
def api_market(market: str, live: bool = True):
    _check(market)
    return overview(market, live)


@api.post("/api/market/{market}/run")
def api_run(market: str):
    _check(market)
    if not _start_run(market):
        return {"ok": False, "message": "Already running."}
    return {"ok": True, "message": f"{INFO[market]['name']} bot run started."}


@api.post("/api/market/{market}/run/stop")
def api_stop_run(market: str):
    _check(market)
    return {"ok": True, "message": stop_run(market)}


def _working() -> bool:
    """A Run or a past-years test works now (the time machine waits for it)."""
    return any(_runs[m]["running"] or _tests[m]["running"] for m in MARKETS)


def _auto_start_run(market: str) -> bool:
    """Started by the time machine or auto run: not while you have stopped this market (Run bot clears that)."""
    return not _runs[market].get("held") and _start_run(market)


@api.post("/api/market/{market}/test")
def api_test(market: str):
    _check(market)
    if not _start_test(market):
        return {"ok": False, "message": "The past-years test is already running."}
    return {"ok": True, "message": f"{INFO[market]['name']} past-years test started. It takes a minute or two."}


# ---------------------------------------------------------------- settings panel
class SettingsBody(BaseModel):
    values: dict


@api.get("/api/settings/{market}")
def api_settings(market: str):
    _check(market)
    return {"fields": settings.view(market)}


@api.post("/api/settings/{market}")
def api_settings_save(market: str, body: SettingsBody):
    _check(market)
    try:
        settings.save(market, body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": "Saved. The bot uses these settings from the next run.", "fields": settings.view(market)}


@api.post("/api/settings/{market}/default")
def api_settings_default(market: str):
    _check(market)
    settings.reset(market)
    return {"ok": True, "message": "Back to the BOT_INSTRUCTIONS numbers.", "fields": settings.view(market)}


# ---------------------------------------------------------------- time machine (removable, see timemachine.py)
class TmStart(BaseModel):
    date: str
    speed: int = 1
    time: str | None = None                            # "HH:MM" in the market's own time; empty = the time it is now


class TmControl(BaseModel):
    action: str
    speed: int | None = None


@api.get("/api/tm/{market}")
def api_tm(market: str):
    _check(market)
    sessions = {m: {"tz": (c := load_market(m))["timezone"], **c["session"]} for m in MARKETS}   # both markets' hours
    return clean({**timemachine.status(market), "pick": profiles.recall("tm"), "sessions": sessions})


def _tm_switch() -> None:
    """Entering or leaving the time machine (or another profile): forget everything cached from the other world.
    A run still waiting in the old world sees the new number and stops without saving."""
    global _store, _world_gen
    _store = None
    _world_gen += 1
    _quotes.clear()
    _prev.clear()
    for m in MARKETS:
        _watch[m].clear()
        _runs[m].update(stage=stages.FINISHED, error=None, finished=None, summary=None, output="", held=False)
        if _tests[m]["running"]:
            _tests[m]["cancel"] = True                   # the old world's test stops at its next part, without saving
        else:
            _tests[m].update(error=None, step=0)
        _activity[m].clear()
        _last_line[m] = 0.0
        _recheck.pop(m, None)
        _recheck_at[m] = 0.0


@api.post("/api/tm/{market}/start")
def api_tm_start(market: str, body: TmStart):
    _check(market)
    first, last = timemachine.date_range(market)       # a wrong day is refused before anything is stopped
    if first is None or not (first <= pd.Timestamp(body.date) < last):
        raise HTTPException(400, "Pick a date inside the saved price history.")
    try:
        timemachine.start_time(market, body.date, body.time)   # a wrong time is refused before anything is stopped
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    try:
        _stop_everything("Start")                      # like Reset: stop the Run and the test of the old world first
        with _world_lock, _lock:              # no account work may be half done while the folders change
            timemachine.start(market, body.date, body.speed, [market], _auto_start_run, _tm_switch, tick=_tm_tick, at=body.time,
                              busy=_working)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    profiles.remember("tm", {"date": body.date, "speed": body.speed, "time": body.time})   # only a day that worked is remembered
    return {"ok": True}


@api.post("/api/tm/{market}/control")
def api_tm_control(market: str, body: TmControl):
    _check(market)
    try:
        if body.action == "stop":
            _stop_everything("Leave")                  # stop the trip's Run and test first, then go back to today
            with _world_lock, _lock:          # leaving: account work in the time machine finishes first
                timemachine.control(body.action, body.speed, _tm_switch)
        else:
            timemachine.control(body.action, body.speed, _tm_switch)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


class TimersBody(BaseModel):
    values: dict


@api.get("/api/timers")
def api_timers():
    return {"values": bot_timers(), "defaults": TIMER_DEFAULTS, "limits": TIMER_LIMITS}


@api.post("/api/timers")
def api_timers_save(body: TimersBody):
    try:
        values = save_bot_timers(body.values)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "values": values, "message": "Saved. The bot uses the new timers from its next loop."}


class BudgetBody(BaseModel):
    amount: float


@api.post("/api/market/{market}/budget")
def api_budget(market: str, body: BudgetBody):
    """Before the account is opened: remember the budget it should open with."""
    _check(market)
    try:
        settings.remember_budget(market, body.amount)   # in the time machine: the trip's own settings
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": f"Saved. The {INFO[market]['name']} account opens with {body.amount:,.0f}."}


# ---------------------------------------------------------------- profiles (see profiles.py)
class NameBody(BaseModel):
    name: str


def switch_profile(pid: str) -> str:
    if pid not in profiles.IDS:
        raise ValueError("Unknown profile.")
    if pid == profiles.active() and not clock.active():
        return _profile_name(pid)                      # already in use: nothing to forget
    _profile_ready("switch profile")
    with _world_lock, _lock:
        _refuse_busy("switch profile")                 # a run may have started in between
        timemachine.exit_(_tm_switch)
        profiles.use(pid)
        _forget_all()
    return _profile_name(pid)


def _profile_ready(what: str) -> None:
    """The profile in use changes. First stop the time machine's clock from starting new runs, then make sure
    nothing is working. Leaving the time machine itself happens under the locks (the caller); the profile keeps
    its remembered day and speed."""
    if clock.active():
        clock.pause(True)
    _refuse_busy(what)


def _refuse_busy(what: str) -> None:
    busy = [INFO[m]["name"] for m in MARKETS if _runs[m]["running"] or _tests[m]["running"]]
    if busy:
        raise ValueError(f"Wait: the bot is still working on {', '.join(busy)}, then {what}.")


def _forget_all() -> None:
    """Drop everything cached from the profile that was in use before."""
    _tm_switch()
    for m in MARKETS:
        _auto[m].update(expected=None, at=0.0, when=None, result=None)
        _runs[m].update(summary=None, output="")
        _tests[m].update(started=None, finished=None, error=None)


def _profile_name(pid: str) -> str:
    return next(p["name"] for p in profiles.listing()["profiles"] if p["id"] == pid)


STOP_WAIT = 90.0                    # seconds a reset waits for a Run or a past-years test to stop


def _stop_everything(what: str) -> None:
    """Before a reset: pause the time machine, tell a waiting Run and a running past-years test to stop
    (they stop at their next safe point, without saving), then wait until nothing is working."""
    if clock.active():
        clock.pause(True)
    _tm_switch()                                       # new world number: a waiting Run stops, old tests are cancelled
    for m in MARKETS:
        _auto[m].update(expected=None, at=time.time())  # the auto loop starts nothing new for a while
    end = time.monotonic() + STOP_WAIT
    while any(_runs[m]["running"] or _tests[m]["running"] for m in MARKETS) and time.monotonic() < end:
        time.sleep(0.2)
    busy = [INFO[m]["name"] for m in MARKETS if _runs[m]["running"] or _tests[m]["running"]]
    if busy:
        raise ValueError(f"Could not stop everything in time: still working on {', '.join(busy)}. Press {what} again.")


def reset_profile(pid: str) -> str:
    """Fresh start for one profile: first everything of it is stopped (a Run, a past-years test, the time machine,
    the live checks), then its accounts, bought shares, trades, history and settings are deleted."""
    global _store
    if pid not in profiles.IDS:
        raise ValueError("Unknown profile.")
    if pid == profiles.active():                       # another profile can be reset while this one keeps working
        _stop_everything("Reset")
    with _world_lock, _lock:
        if pid == profiles.active():
            _refuse_busy("reset a profile")            # nothing may have started in between
            timemachine.exit_(_tm_switch)
        _store = None                                  # let go of the open account file before it is deleted
        gone = profiles.reset(pid)
        if pid == profiles.active():
            _forget_all()
            config.STATE_DIR.mkdir(parents=True, exist_ok=True)
            config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Profile %s reset, removed: %s", pid, ", ".join(gone) or "nothing")
    return _profile_name(pid)


@api.get("/api/profiles")
def api_profiles():
    return profiles.listing()


@api.post("/api/profiles/{pid}/use")
def api_profile_use(pid: str):
    try:
        name = switch_profile(pid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": f"Now trading as {name}.", **profiles.listing()}


@api.post("/api/profiles/{pid}/reset")
def api_profile_reset(pid: str):
    try:
        name = reset_profile(pid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": f"{name} is a fresh start now.", **profiles.listing()}


@api.get("/api/profiles/{pid}/picture")
def api_profile_picture(pid: str):
    pic = profiles.picture(pid)
    if pic is None:
        raise HTTPException(404, "No picture.")
    return FileResponse(pic, headers={"Cache-Control": "no-cache"})


@api.post("/api/profiles/{pid}/picture")
async def api_profile_picture_save(pid: str, request: Request):
    try:
        profiles.save_picture(pid, await request.body())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **profiles.listing()}


@api.post("/api/profiles/{pid}/name")
def api_profile_name(pid: str, body: NameBody):
    try:
        profiles.rename(pid, body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **profiles.listing()}


@api.post("/api/market/{market}/stop")
def api_stop(market: str, body: StopBody):
    _check(market)
    return _ok(set_stop, market, body.on)


@api.post("/api/market/{market}/cash")
def api_cash(market: str, body: CashBody):
    _check(market)
    return _ok(change_cash, market, body.amount)


@api.post("/api/market/{market}/buy-now")
def api_buy_now(market: str):
    _check(market)
    return _ok(buy_live, market)


@api.post("/api/market/{market}/sell/{symbol}")
def api_sell(market: str, symbol: str):
    _check(market)
    return _ok(manual_sell, market, symbol)


@api.post("/api/market/{market}/reset-halt")
def api_reset_halt(market: str):
    _check(market)
    buf = io.StringIO()
    with _lock, contextlib.redirect_stdout(buf):
        bot.reset_halt(market)
    note(market, "RISK", "Restarted after the halt by you")
    return {"ok": True, "message": buf.getvalue().strip()}
