"""Strategy A: trend breakout on daily bars.

The strategy only reads prices. It never touches the account or the broker.
Same three steps as Freqtrade: prepare indicators, find entries, manage exits.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from . import indicators as ind


@dataclass
class Candidate:
    symbol: str
    price: float
    stop: float
    atr: float
    score: float
    reason: str
    strategy: str = ""                # the rule that found it (empty: the trend breakout)
    target_r: float | None = None     # None: the bot's profit target, 0: no fixed target
    gate_r: float | None = None       # reward the risk gate counts, in R (None: the bot's profit target)
    lift: bool = False                # the gate may lift the target a little so it pays after costs
    limit: float | None = None        # buy only at this price or lower
    stop_dist: float | None = None    # stop this far under the fill (instead of a fixed stop price)
    order: tuple = ()                 # rank among ideas: rule priority, then the rule's own score


class TrendBreakout:
    name = "trend_breakout"

    def __init__(self, params: dict, universe: dict):
        self.p = params
        self.u = universe

    @property
    def warmup_bars(self) -> int:
        p = self.p
        return max(p["trend_sma_slow"], p["breakout_days"], p["volume_days"], p["atr_days"],
                   p["exit_sma"], p["rank_roc_days"]) + 1

    # Step 1: indicators and the entry flag, computed once for the whole history.
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        p, u = self.p, self.u
        out = df.copy()
        c = out["Close"]
        out["sma_fast"] = ind.sma(c, p["trend_sma_fast"])
        out["sma_slow"] = ind.sma(c, p["trend_sma_slow"])
        out["sma_exit"] = ind.sma(c, p["exit_sma"])
        out["high_n"] = ind.prior_high(out["High"], p["breakout_days"])
        out["vol_avg"] = ind.prior_mean(out["Volume"], p["volume_days"])
        out["atr"] = ind.atr(out, p["atr_days"])
        out["roc"] = ind.roc(c, p["rank_roc_days"])
        out["value_avg"] = ind.sma(c * out["Volume"], p["volume_days"])

        trend = (c > out["sma_fast"]) & (c > out["sma_slow"])
        breakout = c > out["high_n"]
        volume = out["Volume"] > out["vol_avg"] * p["volume_mult"]
        liquid = (c >= u["min_price"]) & (out["value_avg"] >= u["min_avg_value"])
        ready = out[["sma_slow", "atr", "high_n", "vol_avg"]].notna().all(axis=1)
        suspect = out["suspect"].astype(bool) if "suspect" in out else pd.Series(False, index=out.index)
        out["signal"] = trend & breakout & volume & liquid & ready & ~suspect
        out["f_trend"], out["f_breakout"], out["f_volume"], out["f_liquid"] = trend, breakout, volume, liquid   # kept so the website can report each check
        return out

    # Step 2: turn a flagged bar into a trade idea with a stop and a written reason.
    def candidate(self, symbol: str, bar) -> Candidate:
        p = self.p
        stop = bar.Close - p["stop_atr"] * bar.atr
        vol_x = bar.Volume / bar.vol_avg if bar.vol_avg else float("nan")
        reason = (f"New {p['breakout_days']}-day high: close {bar.Close:.2f} above {bar.high_n:.2f}. "
                  f"Trend: above {p['trend_sma_fast']}-day avg {bar.sma_fast:.2f} and "
                  f"{p['trend_sma_slow']}-day avg {bar.sma_slow:.2f}. "
                  f"Volume {vol_x:.1f}x its {p['volume_days']}-day average. "
                  f"Stop {p['stop_atr']:g} ATR below at {stop:.2f}.")
        score = float(bar.roc) if not math.isnan(bar.roc) else 0.0
        return Candidate(symbol, float(bar.Close), float(stop), float(bar.atr), score, reason)

    # Step 3: after the close, move stops up and decide exits for the next open.
    def manage(self, pos, bar) -> list[str]:
        notes = []
        pos.last_close = float(bar.Close)
        pos.highest_close = max(pos.highest_close, float(bar.Close))
        if not pos.breakeven and bar.High >= pos.entry_price + pos.risk_per_share:
            pos.breakeven = True
            if pos.entry_price > pos.stop:
                pos.stop = pos.entry_price
                notes.append(f"{pos.symbol}: reached +1R, stop moved to breakeven {pos.stop:.2f}")
        if pos.breakeven and not math.isnan(bar.atr):
            trail = pos.highest_close - self.p["trail_atr"] * bar.atr
            if trail > pos.stop:
                pos.stop = float(trail)
        return notes

    def exit_reason(self, pos, bar) -> str | None:
        if not math.isnan(bar.sma_exit) and bar.Close < bar.sma_exit:
            return (f"Close {bar.Close:.2f} fell below the {self.p['exit_sma']}-day average "
                    f"{bar.sma_exit:.2f}")
        return None


STRATEGIES = {TrendBreakout.name: TrendBreakout}


def make_strategy(cfg: dict):
    if any((cfg["strategy"].get("use") or {}).values()):
        from .rules import Combo
        return Combo(cfg["strategy"], cfg["universe"])
    name = cfg["strategy"]["name"]
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Use one of: {', '.join(STRATEGIES)}")
    return STRATEGIES[name](cfg["strategy"], cfg["universe"])
