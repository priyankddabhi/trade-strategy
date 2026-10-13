"""THE ENGINE.

One bar at a time, in order, forever. This is the only place that advances
time. Nothing downstream may look at a bar the engine has not reached.

Order of operations on every bar (this ordering is the anti-look-ahead rule):

  1. Advance the cursor to bar T.
  2. Evaluate resting stop/target orders from bars already reached.
  3. Compute signals from data CLOSED at bar T-1  (never bar T).
  4. Execute those signals at bar T's OPEN.
  5. Mark positions to bar T's close, update risk counters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from . import indicators as ind
from . import strategy as strat
from .broker.paper import PaperBroker
from .events import EventCalendar
from .orders import OrderType, Side, make_client_order_id
from .portfolio import ClosedTrade, Portfolio, Position
from .risk import RiskConfig, evaluate_halts, risk_gate
from .sizing import reward_risk_after_costs, size_position
from .strategy import Rejection, Signal


@dataclass
class EngineResult:
    portfolio: Portfolio
    broker: PaperBroker
    equity_curve: pd.DataFrame
    trades: list[ClosedTrade]
    signals: list[Signal]
    rejections: list[Rejection]
    events: EventCalendar

    @property
    def stats(self) -> dict:
        return self._stats

    _stats: dict = field(default_factory=dict)


class Engine:
    def __init__(
        self,
        *,
        market: str,
        currency: str,
        bars: dict,
        index_bars: pd.DataFrame,
        fear_bars: pd.DataFrame,
        starting_capital: float,
        cfg: dict,
        cost_pct: float,
        warmup_bars: int = 210,
        trade_etfs: bool = True,
        verbose: bool = True,
        start_date: pd.Timestamp | None = None,
        end_date: pd.Timestamp | None = None,
    ) -> None:
        self.market = market
        self.currency = currency
        self.cfg = cfg
        self.cost_pct = cost_pct
        self.verbose = verbose
        self.warmup = warmup_bars
        self.calendar = EventCalendar()

        # --- data ------------------------------------------------------
        self.bars = dict(bars)
        self.index_bars = index_bars
        self.fear_bars = fear_bars

        # --- objects ---------------------------------------------------
        self.portfolio = Portfolio(market, currency, starting_capital)
        self.broker = PaperBroker(bars, starting_capital, cost_pct=cost_pct)
        self.risk_cfg = RiskConfig.from_cfg(cfg)

        # --- precomputed features (pure, closed-bar only) ---------------
        self.features: dict[str, pd.DataFrame] = {}
        for sym, df in bars.items():
            self.features[sym] = strat.build_features(df, cfg)
        self.regime = strat.compute_regime(index_bars, fear_bars, cfg)

        # --- symbol list ------------------------------------------------
        symbols = [s for s in bars.keys()]
        if not trade_etfs:
            etfs = set(cfg.get("_etfs", []))
            symbols = [s for s in symbols if s not in etfs]
        self.symbols = sorted(symbols)

        # --- run state --------------------------------------------------
        self.equity_curve: list[dict] = []
        self.signals: list[Signal] = []
        self.rejections: list[Rejection] = []
        self.trades: list[ClosedTrade] = []
        self._pending: list[tuple[pd.Timestamp, Signal, int, float]] = []
        # The trading window. Indicators are always computed on the FULL
        # history; only the bars the engine is allowed to act on are limited.
        # Slicing the bars instead would starve the warmup and silently
        # produce zero trades in a walk-forward leg.
        self._dates = [d for d in self.broker.dates if d >= self._first_tradable_date()]
        if start_date is not None:
            self._dates = [d for d in self._dates if d >= start_date]
        if end_date is not None:
            self._dates = [d for d in self._dates if d <= end_date]

    # ------------------------------------------------------------------
    def _first_tradable_date(self) -> pd.Timestamp:
        """Earliest date with enough history for SMA200 + signal warmup."""
        best: pd.Timestamp | None = None
        for df in self.bars.values():
            if len(df) > self.warmup:
                d = df.index[self.warmup]
                best = d if best is None else min(best, d)
        return best if best is not None else pd.Timestamp("1900-01-01")

    # ------------------------------------------------------------------
    #  MAIN LOOP
    # ------------------------------------------------------------------
    def run(self) -> EngineResult:
        dates = self._dates
        if self.verbose:
            print(f"  [{self.market}] {len(self.symbols)} symbols, "
                  f"{len(dates)} trading days "
                  f"({dates[0].date() if dates else 'n/a'} -> "
                  f"{dates[-1].date() if dates else 'n/a'})")

        for i, bar_date in enumerate(dates):
            self.broker.set_cursor(i)

            # --- 0. new day: reset counters -----------------------------
            now = bar_date.to_pydatetime()
            self.portfolio.roll_day(now)

            # --- 1. execute pending signals at THIS bar's open ----------
            self._execute_pending(bar_date)

            # --- 2. fire resting stops / targets ------------------------
            self._fire_resting(bar_date)

            # --- 3. manage open positions (trail, breakeven, time) ------
            self._manage_positions(bar_date)

            # --- 4. look for new signals from data closed at bar T-1 ----
            self._generate_signals(bar_date)

            # --- 5. mark to market, record equity -----------------------
            prices = self._close_prices(bar_date)
            eq = self.portfolio.equity_at(prices)
            self.portfolio.update_peak(eq)
            self.equity_curve.append(
                {
                    "date": bar_date,
                    "equity": eq,
                    "cash": self.portfolio.cash,
                    "positions": len(self.portfolio.positions),
                    "drawdown": (eq - self.portfolio.peak_equity) / self.portfolio.peak_equity
                    if self.portfolio.peak_equity
                    else 0.0,
                }
            )

            # --- 6. halt escalation -------------------------------------
            self._check_halts(now)

        # --- close out at the end so the numbers are complete ------------
        self._liquidate(dates[-1] if dates else None)

        curve = pd.DataFrame(self.equity_curve)
        if not curve.empty:
            curve = curve.set_index("date")

        return EngineResult(
            portfolio=self.portfolio,
            broker=self.broker,
            equity_curve=curve,
            trades=list(self.portfolio.closed),
            signals=list(self.signals),
            rejections=list(self.rejections),
            events=self.calendar,
        )

    # ------------------------------------------------------------------
    def _bar_age_hours(self, symbol: str, bar_date: pd.Timestamp) -> float:
        """How stale is this symbol's data relative to the simulation clock?

        In BACKTEST mode the answer must be measured against the bar we are
        simulating, never against the wall clock - a 2025 bar is not stale in
        2026, it is history. In LIVE mode the wall clock is the truth.

        Semantics: hours since this symbol last printed a bar.
        """
        df = self.bars.get(symbol)
        if df is None or df.empty:
            return float("inf")
        sub = df.loc[:bar_date]
        if sub.empty:
            return float("inf")

        last = sub.index[-1]
        if last.tzinfo is not None:
            last = last.tz_localize(None)
        ref = bar_date
        if ref.tzinfo is not None:
            ref = ref.tz_localize(None)
        delta = ref.to_pydatetime() - last.to_pydatetime()
        return max(0.0, delta.total_seconds() / 3600.0)

    def _close_prices(self, bar_date: pd.Timestamp) -> dict:
        out: dict = {}
        for sym in self.portfolio.positions:
            df = self.bars.get(sym)
            if df is None:
                continue
            sub = df.loc[:bar_date]
            if not sub.empty:
                out[sym] = float(sub["close"].iloc[-1])
        return out

    # ------------------------------------------------------------------
    #  1. PENDING SIGNALS -> NEXT BAR OPEN
    # ------------------------------------------------------------------
    def _execute_pending(self, bar_date: pd.Timestamp) -> None:
        if not self._pending:
            return
        still_pending = []
        for (signal_date, sig, qty, risk_amt) in self._pending:
            bar = self.broker.bar_at(sig.symbol, bar_date)
            if bar is None:
                still_pending.append((signal_date, sig, qty, risk_amt))
                continue
            self._open_trade(sig, bar_date, float(bar["open"]), qty)
        self._pending = still_pending

    # ------------------------------------------------------------------
    def _open_trade(self, sig: Signal, bar_date: pd.Timestamp, fill_price: float, qty: int) -> None:
        entry_cost = qty * fill_price * self.cost_pct / 200.0  # half round trip
        pos = Position(
            symbol=sig.symbol,
            market=self.market,
            quantity=qty,
            entry_price=fill_price,
            entry_date=bar_date.to_pydatetime(),
            stop_price=sig.stop_price,
            initial_stop_price=sig.stop_price,
            target_price=sig.target_price,
            reason=dict(sig.reason, signal_date=str(sig.date.date()),
                        signal_reason=sig.reason_text()),
            entry_cost=entry_cost,
        )
        self.portfolio.open_position(pos)

        # Broker-side stop-loss: the hard floor required by BOT_INSTRUCTIONS §2.
        coid = make_client_order_id(sig.symbol, self.market, str(bar_date.date()), "sl")
        order = self.broker.place_order(
            symbol=sig.symbol,
            side=Side.SELL,
            order_type=OrderType.STOP_LOSS,
            quantity=qty,
            stop_price=sig.stop_price,
            client_order_id=coid,
        )
        pos.broker_stop_order_id = order.client_order_id

    # ------------------------------------------------------------------
    #  2. RESTING ORDERS
    # ------------------------------------------------------------------
    def _fire_resting(self, bar_date: pd.Timestamp) -> None:
        fired = self.broker.evaluate_resting_orders(bar_date)
        for f in fired:
            sym = f["symbol"]
            pos = self.portfolio.positions.get(sym)
            if pos is None:
                continue
            exit_cost = pos.quantity * f["fill"] * self.cost_pct / 200.0
            reason = "stop_loss" if f["type"] == "STOP_LOSS" else "target"
            trade = self.portfolio.close_position(
                sym, f["fill"], bar_date.to_pydatetime(), reason, exit_cost
            )
            if trade is not None:
                self.trades.append(trade)
                if self.verbose:
                    print(f"    {bar_date.date()} {sym} {reason} @ {f['fill']:.2f} "
                          f"pnl {trade.pnl:+,.2f} ({trade.r_multiple:+.2f}R)")

    # ------------------------------------------------------------------
    #  3. POSITION MANAGEMENT
    # ------------------------------------------------------------------
    def _manage_positions(self, bar_date: pd.Timestamp) -> None:
        s = self.cfg["strategy"]
        for sym in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[sym]
            bar = self.broker.bar_at(sym, bar_date)
            if bar is None:
                continue

            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
            pos.max_favourable = max(pos.max_favourable, high - pos.entry_price)
            pos.max_adverse = min(pos.max_adverse, low - pos.entry_price)
            pos.bars_held += 1

            # --- breakeven at +1R ---------------------------------------
            if not pos.breakeven_moved and close >= pos.entry_price + s["breakeven_at_r"] * pos.risk_per_share:
                pos.breakeven_moved = True
                pos.stop_price = pos.entry_price
                if pos.broker_stop_order_id:
                    self.broker.cancel_order(pos.broker_stop_order_id)
                    coid = make_client_order_id(sym, self.market, str(bar_date.date()), "be")
                    order = self.broker.place_order(
                        symbol=sym,
                        side=Side.SELL,
                        order_type=OrderType.STOP_LOSS,
                        quantity=pos.quantity,
                        stop_price=pos.entry_price,
                        client_order_id=coid,
                    )
                    pos.broker_stop_order_id = order.client_order_id

            # --- trend break exit ---------------------------------------
            if s.get("exit_on_trend_break", True):
                feats = self.features.get(sym)
                if feats is not None:
                    sub = feats.loc[:bar_date]
                    if not sub.empty and not pd.isna(sub["sma_fast"].iloc[-1]):
                        if close < float(sub["sma_fast"].iloc[-1]):
                            if pos.broker_stop_order_id:
                                self.broker.cancel_order(pos.broker_stop_order_id)
                            exit_cost = pos.quantity * close * self.cost_pct / 200.0
                            trade = self.portfolio.close_position(
                                sym, close, bar_date.to_pydatetime(),
                                "trend_break", exit_cost,
                            )
                            if trade is not None:
                                self.trades.append(trade)
                                if self.verbose:
                                    print(f"    {bar_date.date()} {sym} trend_break "
                                          f"@ {close:.2f} pnl {trade.pnl:+,.2f} "
                                          f"({trade.r_multiple:+.2f}R)")
                            continue

            # --- max hold -------------------------------------------------
            if pos.bars_held >= s["max_hold_bars"]:
                if pos.broker_stop_order_id:
                    self.broker.cancel_order(pos.broker_stop_order_id)
                exit_cost = pos.quantity * close * self.cost_pct / 200.0
                trade = self.portfolio.close_position(
                    sym, close, bar_date.to_pydatetime(), "max_hold", exit_cost
                )
                if trade is not None:
                    self.trades.append(trade)
                    if self.verbose:
                        print(f"    {bar_date.date()} {sym} max_hold @ {close:.2f} "
                              f"pnl {trade.pnl:+,.2f} ({trade.r_multiple:+.2f}R)")

    # ------------------------------------------------------------------
    #  4. SIGNAL GENERATION
    # ------------------------------------------------------------------
    def _generate_signals(self, bar_date: pd.Timestamp) -> None:
        # Signals come from the bar BEFORE this one. The engine knows bar_date
        # is on the calendar, so the previous calendar date is the signal date.
        idx = self.broker.dates.index(bar_date)
        if idx == 0:
            return
        signal_date = self.broker.dates[idx - 1]

        # event blackout - no new entries on macro event days
        if self.calendar.is_blocked(bar_date):
            return

        if self.portfolio.halted:
            return

        for sym in self.symbols:
            if sym in self.portfolio.positions:
                continue

            feats = self.features.get(sym)
            if feats is None or feats.empty:
                continue
            hist = feats.loc[:signal_date]
            if len(hist) < self.warmup:
                continue

            bar_now = self.broker.bar_at(sym, bar_date)
            if bar_now is None:
                continue
            exec_price = float(bar_now["open"])
            if exec_price <= 0:
                continue

            # --- stale data, measured against the simulation clock --------
            age = self._bar_age_hours(sym, bar_date)
            if age > self.risk_cfg.max_bar_age_hours:
                self.rejections.append(
                    Rejection(sym, signal_date, "stale_data",
                              f"last bar {age:.1f}h before this bar")
                )
                continue

            result = strat.signal(
                features=feats,
                regime_features=self.regime,
                symbol=sym,
                market=self.market,
                cfg=self.cfg,
                as_of=signal_date,
                execution_price=exec_price,
                cost_pct=self.cost_pct,
            )
            if isinstance(result, Rejection):
                self.rejections.append(result)
                continue

            sig = result
            # --- sizing -------------------------------------------------
            size = size_position(
                equity=self.portfolio.equity,
                entry_price=exec_price,
                stop_price=sig.stop_price,
                risk_pct=self.risk_cfg.risk_per_trade_pct,
            )
            if size.skipped:
                self.rejections.append(
                    Rejection(sym, signal_date, "sizing", size.reason)
                )
                continue

            notional = size.quantity * exec_price
            if notional > self.portfolio.cash:
                self.rejections.append(
                    Rejection(sym, signal_date, "cash",
                              f"need {notional:.0f}, have {self.portfolio.cash:.0f}")
                )
                continue

            # --- R:R after costs ---------------------------------------
            net_rr = reward_risk_after_costs(
                entry_price=exec_price,
                stop_price=sig.stop_price,
                target_price=sig.target_price,
                notional=notional,
                round_trip_pct=self.cost_pct,
            )
            if net_rr < self.risk_cfg.min_reward_risk - 1e-9:
                self.rejections.append(
                    Rejection(sym, signal_date, "reward_risk_after_costs",
                              f"net R:R {net_rr:.2f} < {self.risk_cfg.min_reward_risk}")
                )
                continue

            # --- RISK GATE ---------------------------------------------
            state = self.portfolio.risk_state()
            decision = risk_gate(
                symbol=sym,
                market=self.market,
                proposed_notional=notional,
                risk_amount=size.risk_amount,
                stop_distance=size.risk_amount / size.quantity,
                reward_risk=net_rr,
                bar_age_hours=age,
                now=bar_date.to_pydatetime(),
                portfolio=state,
                config=self.risk_cfg,
            )
            if not decision.allowed:
                self.rejections.append(
                    Rejection(sym, signal_date, decision.rule, decision.detail)
                )
                continue

            self.signals.append(sig)
            self._pending.append((signal_date, sig, size.quantity, size.risk_amount))

    # ------------------------------------------------------------------
    def _check_halts(self, now: datetime) -> None:
        if self.portfolio.halted:
            return
        decision = evaluate_halts(self.portfolio.risk_state(), self.risk_cfg)
        if decision is not None:
            if decision.rule in ("max_drawdown",):
                self.portfolio.halt(decision.detail)
                if self.verbose:
                    print(f"    !! {now.date()} HALTED: {decision.detail}")
            elif decision.rule in ("daily_loss_limit",):
                # day-level stop: resume next day automatically
                self.portfolio.day_pnl = min(self.portfolio.day_pnl, 0.0)
            elif decision.rule == "consecutive_losses":
                self.portfolio.cooldown_until = now + timedelta(
                    days=self.risk_cfg.cooldown_days
                )

    def _liquidate(self, last_date: pd.Timestamp | None) -> None:
        """Mark open positions to market at the last bar. Paper accounting."""
        if last_date is None:
            return
        for sym in list(self.portfolio.positions.keys()):
            bar = self.broker.bar_at(sym, last_date)
            if bar is None:
                continue
            px = float(bar["close"])
            if self.portfolio.positions[sym].broker_stop_order_id:
                self.broker.cancel_order(self.portfolio.positions[sym].broker_stop_order_id)
            exit_cost = self.portfolio.positions[sym].quantity * px * self.cost_pct / 200.0
            trade = self.portfolio.close_position(
                sym, px, last_date.to_pydatetime(), "end_of_run", exit_cost
            )
            if trade is not None:
                self.trades.append(trade)
