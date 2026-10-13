"""The day loop. Backtest and daily simulation both call Engine.step, so they behave the same.

Order of one session:
  1. Fill yesterday's SELL orders at today's open.
  2. Check stops and targets of positions held before today.
  3. Fill yesterday's BUY orders at today's open, then check their stops.
  4. After the close: move stops (breakeven, trailing), queue exits for tomorrow.
  5. Mark the account to the close and update the risk counters.
  6. If the account and the market regime allow it: scan, risk-check, queue buys for tomorrow.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import pandas as pd

from . import predict
from . import risk
from .broker import VirtualBroker
from .config import universe_tickers
from .costs import make_cost_model
from .data import clean_bars
from .indicators import sma
from .models import Order, SimState, Trade

log = logging.getLogger("tbot.engine")


@dataclass
class DayResult:
    date: pd.Timestamp
    equity: float = 0.0
    cash: float = 0.0
    positions_value: float = 0.0
    regime: str = ""
    events: list[tuple[str, str, str]] = field(default_factory=list)   # (kind, symbol, message)
    trades: list[Trade] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)

    def add(self, kind: str, symbol: str, message: str) -> None:
        self.events.append((kind, symbol, message))


@dataclass
class MarketData:
    strategy: object
    rows: dict[str, dict[pd.Timestamp, tuple]]
    index: pd.DataFrame
    vix: pd.Series | None
    regime_cfg: dict
    skipped: list[str] = field(default_factory=list)

    def bars_on(self, day: pd.Timestamp) -> dict:
        return {s: r[day] for s, r in self.rows.items() if day in r}

    def calendar(self, start=None, end=None, after=None) -> list[pd.Timestamp]:
        idx = self.index.index
        if start is not None:
            idx = idx[idx >= pd.Timestamp(start)]
        if end is not None:
            idx = idx[idx <= pd.Timestamp(end)]
        if after is not None:
            idx = idx[idx > pd.Timestamp(after)]
        return list(idx)

    def regime(self, day: pd.Timestamp) -> tuple[bool, str]:
        n = self.regime_cfg["index_sma"]
        if day not in self.index.index:
            return False, "No index data today"
        row = self.index.loc[day]
        if math.isnan(row["sma"]):
            return False, f"Index {n}-day average not ready yet"
        if row["Close"] <= row["sma"]:
            return False, f"Index {row['Close']:,.0f} is below its {n}-day average {row['sma']:,.0f}"
        text = f"Index {row['Close']:,.0f} above its {n}-day average {row['sma']:,.0f}"
        if self.vix is not None:
            v = self.fear(day)
            if v is None:                                  # missing data never counts as calm
                return False, "Fear gauge data is missing or older than 5 days"
            vmax = self.regime_cfg["vix_max"]
            if v > vmax:
                return False, f"Fear gauge {v:.1f} is above {vmax:g}"
            text += f", fear gauge {v:.1f}"
        return True, text

    def fear(self, day: pd.Timestamp) -> float | None:
        """The fear gauge on this day, or None when it is missing or more than 5 days old."""
        known = self.vix[self.vix.index <= day] if self.vix is not None else ()
        if not len(known) or (day - known.index[-1]).days > 5:
            return None
        return float(known.iloc[-1])

    def only_below_trend(self, day: pd.Timestamp) -> bool:
        """True when the market check failed ONLY because the index is under its long average
        (no panic in the fear gauge, data present). Used by the extra-safe bad-market mode."""
        if day not in self.index.index:
            return False
        row = self.index.loc[day]
        if math.isnan(row["sma"]) or row["Close"] > row["sma"]:
            return False
        if self.vix is not None:
            v = self.fear(day)
            if v is None or v > self.regime_cfg["vix_max"]:    # panic or missing data: no extra-safe buying either
                return False
        return True

    def index_roc(self, day: pd.Timestamp) -> float:
        if "roc" not in self.index.columns or day not in self.index.index:
            return float("nan")
        return float(self.index.loc[day, "roc"])

    def level(self, day: pd.Timestamp) -> str:
        """Market level for sizing: good (full risk) or normal (reduced). Bad = regime() says no."""
        rc = self.regime_cfg
        if not rc.get("levels"):
            return "good"
        recent = self.index[self.index.index <= day].tail(int(rc.get("confirm_days", 5)))
        steady = len(recent) > 0 and bool((recent["Close"] > recent["sma"]).all())
        row = self.index.loc[day]
        fast_ok = "sma_fast" in self.index.columns and not math.isnan(row["sma_fast"]) and row["Close"] > row["sma_fast"]
        calm = True
        if self.vix is not None:
            v = self.fear(day)
            calm = v is not None and v <= float(rc.get("calm_vix", 20))
        return "good" if steady and fast_ok and calm else "normal"


def prepare_market_data(cfg: dict, frames: dict[str, pd.DataFrame], strategy) -> MarketData:
    max_move = cfg["data"]["max_daily_move"]
    r = cfg["regime"]
    if r["index"] not in frames:
        raise RuntimeError(f"No data for the market index {r['index']}. Cannot run without it.")
    index = clean_bars(frames[r["index"]], True, 1.0)
    if hasattr(strategy, "bind_index"):            # some rules compare a share with the index
        strategy.bind_index(index)
    prepared, skipped = {}, []
    for t in universe_tickers(cfg):
        df = frames.get(t)
        if df is None or len(df) < 30:
            skipped.append(t)
            continue
        prepared[t] = strategy.prepare(clean_bars(df, False, max_move))
    if hasattr(strategy, "cross_section"):         # rules that rank all shares together
        strategy.cross_section(prepared)
    rows = {t: dict(zip(p.index, p.itertuples(index=False, name="Bar"))) for t, p in prepared.items()}
    index["sma"] = sma(index["Close"], r["index_sma"])
    index["sma_fast"] = sma(index["Close"], r.get("fast_sma", 50))
    index["roc"] = index["Close"] / index["Close"].shift(int(getattr(strategy, "p", {}).get("rank_roc_days", 63))) - 1
    vix = None
    if r.get("vix") and r["vix"] in frames:
        vix = clean_bars(frames[r["vix"]], True, 10.0)["Close"]
    elif r.get("vix"):                             # wanted but not downloaded: missing data blocks buys, never "calm"
        log.warning("%s: no data for fear gauge %s, so no new buys", cfg["name"], r["vix"])
        vix = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    if skipped:
        log.warning("%s: skipped %d symbols with no data: %s", cfg["name"], len(skipped), ", ".join(skipped))
    return MarketData(strategy, rows, index, vix, r, skipped)


class Engine:
    def __init__(self, cfg: dict, data: MarketData, events: set | frozenset = frozenset(),
                 mode: str = "backtest", stop_file: bool = False, no_buys: str | None = None):
        self.cfg = cfg
        self.data = data
        self.strategy = data.strategy
        self.events = events
        self.mode = mode
        self.stop_file = stop_file
        self.no_buys = no_buys            # a reason that blocks every new buy (e.g. the past-years test did not pass)
        self.r = cfg["risk"]
        self.costs = make_cost_model(cfg["costs"])
        self.target_r = cfg["strategy"].get("take_profit_r")
        self.broker = VirtualBroker(self.costs, self.target_r, self.r["max_gap_risk_mult"],
                                    self.r.get("fractional_shares", False))

    def new_state(self, start: pd.Timestamp) -> SimState:
        cap = float(self.cfg["capital"])
        return SimState(market=self.cfg["name"], initial_capital=cap, cash=cap,
                        start_date=str(pd.Timestamp(start).date()), peak_equity=cap,
                        prev_equity=cap, week_start_equity=cap)

    def run(self, st: SimState, dates: list[pd.Timestamp]) -> list[DayResult]:
        return [self.step(st, d) for d in dates]

    def step(self, st: SimState, day: pd.Timestamp) -> DayResult:
        res = DayResult(date=day)
        st.session_no += 1
        for note in risk.start_of_session(st, self.mode):
            res.add("note", "", note)
        bars = self.data.bars_on(day)

        # 1. Yesterday's exit orders fill at today's open.
        waiting = []
        for o in [o for o in st.orders if o.side == "SELL"]:
            if o.symbol not in st.positions:
                continue
            bar = bars.get(o.symbol)
            if bar is None:
                waiting.append(o)
                continue
            res.trades.append(self.broker.close(st, o.symbol, float(bar.Open), day, o.reason))

        # 2. Stops and targets of positions held before today.
        for sym in list(st.positions):
            bar = bars.get(sym)
            if bar is None:
                res.add("note", sym, "No price today, position unchanged")
                continue
            trade = self.broker.check_exit(st, st.positions[sym], bar, day, entered_today=False)
            if trade:
                res.trades.append(trade)

        # 3. Yesterday's buy orders fill at today's open.
        blackout = day in self.events
        for o in [o for o in st.orders if o.side == "BUY"]:
            bar = bars.get(o.symbol)
            if bar is None:
                res.add("cancel", o.symbol, "Buy cancelled, no price today")
                continue
            if len(st.positions) >= self.r["max_positions"]:
                res.add("cancel", o.symbol, f"Buy cancelled, already holding {self.r['max_positions']} share(s)")
                continue
            pos, msg = self.broker.fill_entry(st, o, bar, day, blackout)
            res.add("fill" if pos else "cancel", o.symbol, msg)
            if pos:
                trade = self.broker.check_exit(st, pos, bar, day, entered_today=True)
                if trade:
                    res.trades.append(trade)
        st.orders = [o for o in waiting if o.symbol in st.positions]

        for t in res.trades:
            res.add("exit", t.symbol, f"Sold {t.qty:g} at {t.exit_price:.2f}: {t.exit_reason}. "
                                      f"P&L {t.pnl:,.0f} ({t.r_multiple:+.2f}R)")
            for note in risk.record_trade(st, t, self.r):
                res.add("risk", "", note)

        # 4. After the close: manage open positions.
        for sym, pos in list(st.positions.items()):
            bar = bars.get(sym)
            if bar is None:
                continue
            for note in self.strategy.manage(pos, bar):
                res.add("note", sym, note)
            for note in predict.check(self.cfg, pos, bar):     # near-future band: off track + pattern broken = stop up
                res.add("note", sym, note)
            why = self.strategy.exit_reason(pos, bar)
            if why and not any(o.symbol == sym and o.side == "SELL" for o in st.orders):
                order = self.broker.new_order(st, "SELL", sym, pos.qty, day, why)
                st.orders.append(order)
                res.orders.append(order)
                res.add("order", sym, f"Sell queued for next open: {why}")

        # 5. Mark to market and update risk counters.
        res.positions_value = sum(p.qty * p.last_close for p in st.positions.values())
        res.cash = st.cash
        res.equity = st.cash + res.positions_value
        for note in risk.update_counters(st, day, res.equity, self.r, self.mode):
            res.add("risk", "", note)

        # 6. New entries for tomorrow.
        ok, res.regime = self.data.regime(day)
        block = self.no_buys or risk.account_block(st, self.r, self.stop_file)
        if block:
            res.add("blocked", "", f"No new entries: {block}")
        elif not ok:
            rc = self.data.regime_cfg
            bm = rc.get("bad_market") or {}
            if rc.get("levels") and bm.get("allow") and self.data.only_below_trend(day):
                res.regime = f"Bad market. {res.regime}. Extra-safe mode: tiny size, strongest share only"
                self._scan(st, day, res.equity, bars, res, float(bm.get("risk_mult", 0.2)), 0.0, level="bad",
                           strict={"rs_min": float(bm.get("beat_index_by", 0.10)), "max_positions": int(bm.get("max_positions", 1)),
                                   "index_roc": self.data.index_roc(day)})
            else:
                res.regime = "Bad market. " + res.regime if rc.get("levels") else res.regime
                res.add("blocked", "", f"No new entries: {res.regime}")
        else:
            rc, risk_mult, pos_mult, lvl = self.data.regime_cfg, 1.0, 1.0, ""
            if rc.get("levels"):
                lvl = self.data.level(day)
                if lvl == "normal":
                    risk_mult, pos_mult = float(rc.get("normal_risk_mult", 0.5)), float(rc.get("normal_positions_mult", 0.6))
                res.regime = f"{lvl.capitalize()} market. " + res.regime
            self._scan(st, day, res.equity, bars, res, risk_mult, pos_mult, level=lvl)

        st.last_date = str(day.date())
        return res

    def _scan(self, st: SimState, day, equity: float, bars: dict, res: DayResult,
              risk_mult: float = 1.0, pos_mult: float = 1.0, strict: dict | None = None, level: str = "") -> None:
        """Best-ranked candidate first. Market level scales the risk; later picks of the day get less.
        strict (bad market): only the day's best share, only if it beats the index clearly, tiny size."""
        base = dict(self.r, risk_per_trade=self.r["risk_per_trade"] * risk_mult,
                    max_positions=max(1, math.ceil(self.r["max_positions"] * pos_mult)))
        if strict:
            base["max_positions"] = strict["max_positions"]
        busy = set(st.positions) | {o.symbol for o in st.orders}
        many = getattr(self.strategy, "candidates", None)
        if many:
            cands = [c for s, b in bars.items() if b.signal and s not in busy for c in many(s, b)]
        else:
            cands = [self.strategy.candidate(s, b) for s, b in bars.items() if b.signal and s not in busy]
        cands.sort(key=lambda c: (getattr(c, "order", ()), -c.score, c.symbol))   # rule priority first, then momentum
        found = len({c.symbol for c in cands})
        effort = (self.data.regime_cfg.get("scan") or {}).get(level)       # how many of the best shares to compare
        if effort and found:
            keep, seen = [], set()
            for c in cands:                                 # every idea of the best `effort` shares, in rank order
                if c.symbol not in seen:
                    if len(seen) >= int(effort):
                        continue
                    seen.add(c.symbol)
                keep.append(c)
            cands = keep
        if found:
            res.add("note", "", f"Search: {level or 'market'} market, compared the best {len({c.symbol for c in cands})} of "
                                f"{found} share(s) that matched a strategy (ranked by the strategy's past results, then its own score)")
        rank = {s: i + 1 for i, s in enumerate(dict.fromkeys(c.symbol for c in cands))}
        pending_cash = sum(o.qty * (o.est_price or 0) for o in st.orders if o.side == "BUY")
        new_today, placed = 0, set()
        for c in cands:
            if c.symbol in placed:                 # another rule already bought this share today
                continue
            if strict:
                if new_today >= 1:
                    break
                ix = strict["index_roc"]
                if math.isnan(ix) or c.score - ix < strict["rs_min"]:
                    res.add("reject", c.symbol, f"Bad market: needs to beat the index by {strict['rs_min']:.0%} over 3 months "
                                                f"(share {c.score:+.1%}, index {ix:+.1%})")
                    continue
            rr = base if new_today == 0 else dict(base, risk_per_trade=base["risk_per_trade"] * self.r.get("second_choice_mult", 1.0))
            d = risk.evaluate(st, c, equity, rr, self.costs, self.target_r or 2.0,
                              new_today, pending_cash)
            if not d.ok:
                res.add("reject", c.symbol, (_label(c) + ": " if c.strategy else "") + d.reason)
                continue
            order = self.broker.new_order(st, "BUY", c.symbol, d.qty, day, c.reason, stop=c.stop,
                                          est_price=c.price, planned_risk=d.planned_risk, strategy=c.strategy,
                                          target_r=d.target_r if d.target_r is not None else c.target_r,
                                          limit=c.limit, stop_dist=c.stop_dist)
            placed.add(c.symbol)
            st.orders.append(order)
            res.orders.append(order)
            res.add("order", c.symbol, f"Buy {d.qty:g} queued for next open (risk {d.planned_risk:,.0f}, "
                                       f"rank {rank[c.symbol]} of {found}). {c.reason}")
            new_today += 1
            pending_cash += d.qty * c.price


def _label(c) -> str:
    from .rules import LABELS
    return LABELS.get(c.strategy, c.strategy)


def apply_splits(st: SimState, splits: dict[str, float]) -> list[str]:
    """Keep open positions consistent when a stock split rescales its price history."""
    notes = []
    for sym, ratio in splits.items():
        pos = st.positions.get(sym)
        if pos:
            pos.qty *= ratio
            for f in ("entry_price", "stop", "initial_stop", "risk_per_share", "highest_close", "last_close", "highest_high",
                      "pred_base", "pred_slope", "pred_atr"):
                setattr(pos, f, getattr(pos, f) / ratio)
            if pos.target is not None:
                pos.target /= ratio
            notes.append(f"{sym}: split detected (ratio {ratio:.2f}), position adjusted")
        for o in st.orders:
            if o.symbol == sym:
                o.qty *= ratio
                o.stop = o.stop / ratio if o.stop else o.stop
                o.est_price = o.est_price / ratio if o.est_price else o.est_price
                o.limit = o.limit / ratio if o.limit else o.limit
                o.stop_dist = o.stop_dist / ratio if o.stop_dist else o.stop_dist
    return notes


def results_to_frames(results: list[DayResult]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    equity = pd.DataFrame([{"date": r.date, "equity": r.equity, "cash": r.cash,
                            "positions_value": r.positions_value, "regime": r.regime} for r in results])
    if not equity.empty:
        equity = equity.set_index("date")
    trades = pd.DataFrame([t.__dict__ for r in results for t in r.trades],
                          columns=list(Trade.__dataclass_fields__))
    events = pd.DataFrame([{"date": r.date, "kind": k, "symbol": s, "message": m}
                           for r in results for k, s, m in r.events],
                          columns=["date", "kind", "symbol", "message"])
    return equity, trades, events
