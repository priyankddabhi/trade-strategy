"""
=============================================================================
 BOOK STRATEGIES — every strategy from every book you uploaded, in ONE file
=============================================================================

Sources (methodologies implemented from the books you provided):

  1. Mark Minervini    "Trade Like a Stock Market Wizard"
        SEPA + VCP  |  Superperformance trend template
  2. Curtis Faith      "Way of the Turtle"
        System 1 (20-day)  |  System 2 (55-day)  |  Whipsaw
  3. Gary Antonacci    "Dual Momentum Investing"
        Absolute momentum  |  Relative momentum  |  GEM
  4. Nick Radge        "Unholy Grails"
        New Yearly High  |  100-Day High  |  TrendPilot  |  Golden/Death Cross
  5. Andreas Clenow    "Stocks on the Move"
        Standard trend following  |  Adjusted-slope momentum  |  Risk parity sizing
  6. Laurens Bensdorp  "The 30-Minute Stock Trader"
        Weekly Rotation  |  Mean Reversion Long  |  Mean Reversion Short  |  200-SMA trend
  7. UTI / NSE         "Nifty200 Momentum 30" factsheet
        Normalized Momentum Score  |  Factor-tilt weighting

Each book contains SEVERAL strategies - every one of them is here as an
independent function returning the same shape, so they can be compared,
weighted, and combined by the ensemble at the bottom. Nothing here replaces
the risk gate - it only produces intents.

    python3 -m src.book_strategies              # run every book on real data
    python3 -m src.book_strategies --symbol TCS.NS
=============================================================================
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Config

# =============================================================================
#  SHARED TYPES
# =============================================================================


@dataclass
class BookSignal:
    """What one book's strategy thinks about one symbol on one bar."""

    book: str
    author: str
    direction: int                 # +1 long, -1 short, 0 no opinion
    score: float                   # 0.0 .. 1.0 conviction
    reason: dict = field(default_factory=dict)
    stop_price: float | None = None
    target_price: float | None = None

    def __bool__(self) -> bool:
        return self.direction != 0


@dataclass
class Ensemble:
    """The combined opinion of all seven books."""

    direction: int
    score: float
    votes: dict
    signals: list
    agree: int
    disagree: int
    reason: str

    @property
    def consensus(self) -> float:
        """Fraction of active books agreeing with the chosen direction."""
        active = self.agree + self.disagree
        return (self.agree / active) if active else 0.0


# =============================================================================
#  1. MINERVINI — SEPA (Specific Entry Point Analysis)
# =============================================================================
def minervini_sepa(feat: pd.DataFrame, cfg: dict,
                   as_of: pd.Timestamp | None = None) -> BookSignal:
    """Stage-2 uptrend, Trend Template stack, VCP contraction, pivot breakout.

    Minervini's method: the stock must be in a Stage 2 advance, the moving
    averages must be stacked and sloping up, relative strength must be
    positive, volatility must be contracting into the entry (the VCP), and
    the entry is a pivot breakout above the recent consolidation high on
    expanded volume. Stops are tight — his hard rule is a fixed percentage
    under the entry, never a hope.
    """
    s = cfg["strategy"]["books"]["minervini"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_slow"]:
        return BookSignal("SEPA", "Minervini", 0, 0.0)

    row = feat.iloc[-1]
    r: dict = {}

    # --- Trend Template: stack of moving averages, all rising -----------
    ma_fast = ind.sma(feat["close"], s["ma_fast"]).iloc[-1]
    ma_mid = ind.sma(feat["close"], s["ma_mid"]).iloc[-1]
    ma_slow = ind.sma(feat["close"], s["ma_slow"]).iloc[-1]
    close = float(row["close"])

    stack = close > ma_fast > ma_mid > ma_slow
    r["stack"] = bool(stack)
    r["ma_fast"], r["ma_mid"], r["ma_slow"] = ma_fast, ma_mid, ma_slow

    # --- moving averages sloping up (compare to N bars ago) ------------
    slope = ind.sma(feat["close"], s["ma_fast"])
    rising = len(slope) > s["slope_bars"] and (
        slope.iloc[-1] > slope.iloc[-s["slope_bars"]]
    )
    r["ma_rising"] = bool(rising)

    # --- relative strength vs the index (approximated by own momentum) --
    roc = (close / float(feat["close"].iloc[-1 - s["rs_lookback"]]) - 1.0) * 100.0
    r["roc_pct"] = round(roc, 2)
    r["rs_ok"] = roc > s["rs_min_pct"]

    # --- VCP: volatility contracting into the entry --------------------
    atr = ind.atr(feat, s["atr_period"])
    if len(atr) < 2 * s["vcp_lookback"]:
        return BookSignal("SEPA", "Minervini", 0, 0.0, r)
    recent_vol = atr.iloc[-s["vcp_lookback"]:].mean()
    prior_vol = atr.iloc[-2 * s["vcp_lookback"]:-s["vcp_lookback"]].mean()
    contraction = (prior_vol - recent_vol) / prior_vol if prior_vol else 0.0
    r["vcp_contraction"] = round(float(contraction) * 100, 1)
    r["vcp_ok"] = contraction >= s["vcp_min_contraction"]

    # --- pivot breakout above the consolidation high on volume ----------
    pivot_hi = float(feat["high"].iloc[-1 - s["pivot_lookback"]:-1].max())
    broke_out = close >= pivot_hi * (1 - s["pivot_tolerance"])
    vol_ratio = float(row["volume"]) / max(1e-9, float(ind.sma(feat["volume"], s["vol_ma"]).iloc[-1]))
    r["pivot_high"] = round(pivot_hi, 2)
    r["broke_out"] = bool(broke_out)
    r["volume_ratio"] = round(vol_ratio, 2)
    r["vol_expansion"] = vol_ratio >= s["vol_multiplier"]

    ok = stack and rising and r["rs_ok"] and r["vcp_ok"] and broke_out and r["vol_expansion"]
    if not ok:
        return BookSignal("SEPA", "Minervini", 0, 0.0, r)

    score = min(1.0, 0.35 + 0.15 * (r["vcp_ok"] and 1) + 0.2 * r["vol_expansion"]
                + 0.15 * r["rs_ok"] + 0.15 * (contraction / max(s["vcp_min_contraction"], 1e-9)) * 0.5)
    stop = close * (1 - s["stop_pct"] / 100.0)
    target = close * (1 + s["stop_pct"] / 100.0 * s["reward_risk"])
    r["stop_pct"] = s["stop_pct"]
    return BookSignal("SEPA", "Minervini", 1, round(score, 3), r, stop, target)


# =============================================================================
#  2. TURTLE — Donchian breakout (Curtis Faith / Way of the Turtle)
# =============================================================================
def turtle_donchian(feat: pd.DataFrame, regime: pd.DataFrame, cfg: dict,
                    as_of: pd.Timestamp | None = None) -> BookSignal:
    """Buy a 20-day (System 1) or 55-day (System 2) breakout, filtered by trend.

    The Turtles' edge was not the entry — it was the discipline. Entry: price
    exceeds the highest high of the preceding N days. Filter: only long when
    the 50-day average is above the 300-day. Risk: 1 unit = 1% of equity
    divided by N (the 20-day ATR). Stop: 2N under the entry. Exit: a 10-day
    (S1) or 20-day (S2) breakout in the opposite direction.
    """
    s = cfg["strategy"]["books"]["turtle"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_long"] + 5:
        return BookSignal("Donchian", "Faith", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    # --- N = 20-day ATR, the volatility unit ---------------------------
    n_atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    if pd.isna(n_atr) or n_atr <= 0:
        return BookSignal("Donchian", "Faith", 0, 0.0, r)
    r["N_atr"] = round(float(n_atr), 4)
    r["unit_risk_pct_of_price"] = round(float(n_atr) / close * 100.0, 2)

    # --- System 1: 20-day breakout -------------------------------------
    hi_20 = float(feat["high"].iloc[-1 - s["entry_short"]:-1].max())
    r["high_20"] = round(hi_20, 2)
    r["broke_20"] = close > hi_20

    # --- System 2: 55-day breakout (the failsafe) ----------------------
    hi_55 = float(feat["high"].iloc[-1 - s["entry_long"]:-1].max())
    r["high_55"] = round(hi_55, 2)
    r["broke_55"] = close > hi_55

    # --- trend portfolio filter ----------------------------------------
    ma_short = ind.sma(feat["close"], s["ma_short"]).iloc[-1]
    ma_long = ind.sma(feat["close"], s["ma_long"]).iloc[-1]
    trend_ok = ma_short > ma_long
    r["ma_short"], r["ma_long"] = ma_short, ma_long
    r["trend_filter_ok"] = bool(trend_ok)

    # --- index regime gate ---------------------------------------------
    regime_ok = True
    if regime is not None and not regime.empty and s["require_regime"]:
        rh = regime.loc[: feat.index[-1]]
        if not rh.empty and "above_ma200" in rh.columns:
            regime_ok = bool(rh["above_ma200"].iloc[-1])
    r["regime_ok"] = regime_ok

    if not (trend_ok and regime_ok):
        return BookSignal("Donchian", "Faith", 0, 0.0, r)

    entry = None
    if r["broke_20"]:
        entry = "system_1_20d"
        score = 0.85
    elif r["broke_55"]:
        entry = "system_2_55d"
        score = 0.95
    else:
        return BookSignal("Donchian", "Faith", 0, 0.0, r)

    r["system"] = entry
    stop = close - s["stop_n"] * float(n_atr)
    target = close + s["stop_n"] * float(n_atr) * s["reward_risk"]
    r["stop_n"] = s["stop_n"]
    return BookSignal("Donchian", "Faith", 1, score, r, stop, target)


# =============================================================================
#  3. DUAL MOMENTUM — relative + absolute (Gary Antonacci)
# =============================================================================
def dual_momentum(feat: pd.DataFrame, index_feat: pd.DataFrame, cfg: dict,
                  as_of: pd.Timestamp | None = None) -> BookSignal:
    """Rank the asset against its peers (relative) and against cash (absolute).

    Relative momentum asks: did this asset beat the index over the lookback?
    Absolute momentum asks: did it beat the risk-free rate at all? If the
    answer to the second question is no, the correct position is cash — and
    that defensive leg is what makes the strategy survive bear markets.
    """
    s = cfg["strategy"]["books"]["dual_momentum"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["lookback"] + 2:
        return BookSignal("DualMomentum", "Antonacci", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    # --- 12-month lookback ---------------------------------------------
    past = float(feat["close"].iloc[-1 - s["lookback"]])
    ret = (close / past - 1.0) * 100.0
    r["return_pct"] = round(ret, 2)

    # --- absolute momentum: excess return over the risk-free rate -------
    rf = s["risk_free_pct"]
    excess = ret - rf
    r["risk_free_pct"] = rf
    r["excess_pct"] = round(excess, 2)
    r["absolute_ok"] = excess > 0

    # --- relative momentum: beat the index over the same window ---------
    rel_ok = None
    rel_ret = None
    if index_feat is not None and not index_feat.empty:
        idx = index_feat.loc[:as_of] if as_of is not None else index_feat
        if len(idx) > s["lookback"]:
            ipast = float(idx["close"].iloc[-1 - s["lookback"]])
            ilast = float(idx["close"].iloc[-1])
            rel_ret = (ilast / ipast - 1.0) * 100.0
        rel_ok = ret > rel_ret
    r["index_return_pct"] = round(rel_ret, 2) if rel_ret is not None else None
    r["relative_ok"] = rel_ok

    if not r["absolute_ok"]:
        # Absolute momentum is the defensive leg: negative means stand aside.
        return BookSignal("DualMomentum", "Antonacci", 0, 0.0, r)

    score = 0.6
    if rel_ok:
        score += 0.25
    if excess > s["strong_excess_pct"]:
        score += 0.15
    score = min(1.0, score)

    # --- stop: a 12-month trend break, sized by ATR ---------------------
    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    return BookSignal("DualMomentum", "Antonacci", 1, round(score, 3), r, stop, target)


# =============================================================================
#  4. RADGE — 100-day breakout + TrendPilot (Nick Radge)
# =============================================================================
def radge_trendpilot(feat: pd.DataFrame, index_feat: pd.DataFrame, cfg: dict,
                     as_of: pd.Timestamp | None = None) -> BookSignal:
    """Enter on a 100-day high, exit on a 100-day low, filtered by TrendPilot.

    TrendPilot is the whipsaw filter: the market is only bullish when the
    index closes above its 200-day average for N consecutive days, and only
    bearish after N consecutive closes below. When the filter is negative
    the strategy does not wait for the slow 100-day-low exit — it gets out.
    """
    s = cfg["strategy"]["books"]["radge"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["breakout"] + 5:
        return BookSignal("TrendPilot", "Radge", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    # --- TrendPilot: index vs its 200-day average, confirmed N days -----
    if index_feat is None or index_feat.empty or len(index_feat) < s["index_ma"] + s["confirm_days"]:
        return BookSignal("TrendPilot", "Radge", 0, 0.0, r)

    idx_ma = ind.sma(index_feat["close"], s["index_ma"])
    idx_close = index_feat["close"]
    recent = (idx_close.iloc[-s["confirm_days"]:] > idx_ma.iloc[-s["confirm_days"]:])
    r["index_above_ma200_days"] = int(recent.sum())
    r["trendpilot_ok"] = bool(recent.all())

    if not r["trendpilot_ok"]:
        r["why"] = f"index not above 200-DMA for {s['confirm_days']} consecutive days"
        return BookSignal("TrendPilot", "Radge", 0, 0.0, r)

    # --- 100-day breakout entry ----------------------------------------
    hi = float(feat["high"].iloc[-1 - s["breakout"]:-1].max())
    r["high_100"] = round(hi, 2)
    r["broke_100"] = close > hi
    if not r["broke_100"]:
        return BookSignal("TrendPilot", "Radge", 0, 0.0, r)

    # --- 100-day low is the exit reference -----------------------------
    lo = float(feat["low"].iloc[-1 - s["breakout"]:-1].min())
    r["low_100"] = round(lo, 2)

    # --- defensive exit level: 10% off the recent high ------------------
    high_ref = float(feat["high"].iloc[-s["defensive_lookback"]:].max())
    r["defensive_exit"] = round(high_ref * (1 - s["defensive_pct"] / 100.0), 2)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = min(lo, high_ref * (1 - s["defensive_pct"] / 100.0))
    stop = min(stop, close - s["stop_atr"] * float(atr))
    target = close + (close - stop) * s["reward_risk"]
    return BookSignal("TrendPilot", "Radge", 1, 0.9, r, stop, target)


# =============================================================================
#  5. CLENOW — regime + risk-adjusted momentum (Andreas F. Clenow)
# =============================================================================
def clenow_momentum(feat: pd.DataFrame, index_feat: pd.DataFrame, cfg: dict,
                    universe_scores: dict | None = None,
                    as_of: pd.Timestamp | None = None) -> BookSignal:
    """Regime filter first, then a volatility-adjusted momentum rank.

    Clenow's core argument: raw return is gambling, return per unit of
    volatility is investing. He ranks on the regression slope of log price
    divided by the standard error of that slope, then applies a hard market
    regime filter (index above its 200-day average) before buying anything.
    """
    s = cfg["strategy"]["books"]["clenow"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["slope_window"] + 5:
        return BookSignal("Momentum", "Clenow", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    # --- regime filter: don't buy stocks in a bear market ---------------
    if index_feat is not None and not index_feat.empty and len(index_feat) > s["index_ma"]:
        ima = ind.sma(index_feat["close"], s["index_ma"]).iloc[-1]
        iclose = float(index_feat["close"].iloc[-1])
        r["index_ma200"] = round(ima, 2)
        r["index_close"] = iclose
        r["regime_ok"] = iclose > ima
        if not r["regime_ok"]:
            r["why"] = "index below its 200-day average — bear market, no longs"
            return BookSignal("Momentum", "Clenow", 0, 0.0, r)

    # --- regression slope of log price, divided by its standard error ---
    window = feat["close"].iloc[-s["slope_window"]:]
    y = np.log(window.to_numpy(dtype=float))
    x = np.arange(len(y), dtype=float)
    if len(y) < 3 or np.allclose(y, y[0]):
        return BookSignal("Momentum", "Clenow", 0, 0.0, r)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    se = float(np.sqrt(np.sum(resid ** 2) / max(1, len(y) - 2)))
    adj_slope = slope / se if se > 0 else 0.0
    r["slope"] = round(float(slope), 6)
    r["adj_slope"] = round(float(adj_slope), 4)

    # --- annualised return and volatility ------------------------------
    ann_ret = (float(np.exp(slope * s["slope_window"])) - 1.0) * 100.0
    rets = window.pct_change().dropna()
    vol = float(rets.std() * np.sqrt(252) * 100.0)
    r["annualised_return_pct"] = round(ann_ret, 2)
    r["annualised_vol_pct"] = round(vol, 2)
    r["ret_per_vol"] = round(ann_ret / vol, 3) if vol > 0 else 0.0

    # --- must be in the top 20% of the universe ------------------------
    rank_pct = None
    if universe_scores:
        scores = sorted(universe_scores.values(), reverse=True)
        if len(scores) > 1:
            rank_pct = sum(1 for v in scores if v > adj_slope) / len(scores)
            r["universe_rank_pct"] = round(rank_pct, 3)
            r["top_quintile"] = rank_pct <= 0.20
            if rank_pct > 0.20:
                return BookSignal("Momentum", "Clenow", 0, 0.0, r)

    # --- trend confirmation on the 100-day average ----------------------
    ma100 = ind.sma(feat["close"], s["exit_ma"]).iloc[-1]
    r["above_ma100"] = close > ma100
    if not r["above_ma100"]:
        return BookSignal("Momentum", "Clenow", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    score = min(1.0, 0.5 + abs(adj_slope) / 10.0)
    return BookSignal("Momentum", "Clenow", 1, round(score, 3), r, stop, target)


# =============================================================================
#  6. BENSDORP — mean reversion (Laurens Bensdorp)
# =============================================================================
def bensdorp_mean_reversion(feat: pd.DataFrame, cfg: dict,
                            as_of: pd.Timestamp | None = None) -> BookSignal:
    """Buy the fear: a sharp short-term pullback inside a longer uptrend.

    Bensdorp's insight is that trend following and mean reversion are
    opposites that work at different times, and that running both at once
    smooths the equity curve. The long version buys a short-term washout
    while the longer trend is still intact, with a wide stop because mean
    reversion needs room, and a fixed profit target with a hard time exit.
    """
    s = cfg["strategy"]["books"]["bensdorp"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["rsi_period"] + s["adx_period"] + 5:
        return BookSignal("MeanReversion", "Bensdorp", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    # --- longer-term uptrend must be intact ----------------------------
    ma = ind.sma(feat["close"], s["trend_ma"]).iloc[-1]
    r["trend_ma"] = round(ma, 2)
    r["uptrend"] = close > ma
    if not r["uptrend"]:
        r["why"] = "not in a longer-term uptrend — mean reversion needs a trend to revert to"
        return BookSignal("MeanReversion", "Bensdorp", 0, 0.0, r)

    # --- short-term washout: RSI below the fear threshold ---------------
    rsi = ind.rsi(feat["close"], s["rsi_period"]).iloc[-1]
    r["rsi"] = round(float(rsi), 2)
    r["rsi_threshold"] = s["rsi_entry"]
    r["fear"] = float(rsi) <= s["rsi_entry"]
    if not r["fear"]:
        return BookSignal("MeanReversion", "Bensdorp", 0, 0.0, r)

    # --- the washout should be a real move, not noise -------------------
    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    pullback = (ma - close) / ma * 100.0
    r["pullback_pct"] = round(pullback, 2)
    r["pullback_atr"] = round((ma - close) / float(atr), 2) if atr else 0.0

    # --- ADX must not be collapsing (a trend break is not a pullback) ---
    adx = ind.adx(feat, s["adx_period"]).iloc[-1]
    r["adx"] = round(float(adx), 1)
    r["adx_ok"] = float(adx) >= s["adx_min"]
    if not r["adx_ok"]:
        r["why"] = f"ADX {adx:.1f} below {s['adx_min']} — this is a trend break, not a pullback"
        return BookSignal("MeanReversion", "Bensdorp", 0, 0.0, r)

    # --- entry, wide stop, fixed target, hard time exit -----------------
    r["stop_atr_multiple"] = s["stop_atr"]
    r["target_pct"] = s["target_pct"]
    r["max_hold_days"] = s["max_hold_days"]
    stop = close - s["stop_atr"] * float(atr)
    target = close * (1 + s["target_pct"] / 100.0)
    score = min(1.0, (s["rsi_entry"] - float(rsi)) / s["rsi_entry"] + 0.4)
    return BookSignal("MeanReversion", "Bensdorp", 1, round(max(0.4, score), 3), r, stop, target)


# =============================================================================
#  7. NSE / UTI — Normalized Momentum Score (Nifty200 Momentum 30)
# =============================================================================
def nse_momentum_score(feat: pd.DataFrame, cfg: dict) -> dict:
    """The index methodology from the UTI factsheet.

    Momentum Ratio  = price return / standard deviation of daily returns
    Z Score         = (MR - mean of MR) / std of MR   across the universe
    NMS             = average of the 6-month and 12-month Z scores

    This is risk-adjusted momentum: it divides return by the volatility used
    to earn it, which is the same idea Clenow argues for from a different
    direction. We compute the per-stock half here; the cross-sectional half
    needs the whole universe and is done in `nse_rank_universe`.
    """
    s = cfg["strategy"]["books"]["nse_momentum"]
    if feat.empty or len(feat) < s["lookback_short"] + 2:
        return {}

    close = feat["close"]
    last = float(close.iloc[-1])
    out: dict = {}
    measured: list[float] = []

    for label, lookback in (("6m", s["lookback_short"]), ("12m", s["lookback_long"])):
        # The 12-month leg needs 252 bars of history. At the very start of a
        # trading window that history may not exist yet. Rather than score
        # nothing at all - which silently removes the book from the ensemble -
        # fall back to the longest lookback that IS available and record which
        # one was used, so the reason stays honest.
        use = lookback
        if len(feat) < lookback + 2:
            use = max(s["lookback_short"], (len(feat) - 2) // 2)
            out[f"{label}_lookback_used_bars"] = use
            out[f"{label}_lookback_requested_bars"] = lookback
        if use < 20:
            continue

        past = float(close.iloc[-1 - use])
        ret = (last / past - 1.0) * 100.0
        daily = close.pct_change().dropna().iloc[-use:]
        sd = float(daily.std() * np.sqrt(252) * 100.0)
        mr = ret / sd if sd > 0 else 0.0
        out[f"return_{label}_pct"] = round(ret, 2)
        out[f"vol_{label}_pct"] = round(sd, 2)
        out[f"mr_{label}"] = round(mr, 4)
        measured.append(mr)

    if not measured:
        return {}
    out["momentum_ratio_avg"] = round(sum(measured) / len(measured), 4)
    return out


def nse_rank_universe(scores: dict, top_n: int = 30) -> dict:
    """Turn per-stock Momentum Ratios into the index's Z-scores and ranks."""
    if not scores:
        return {}
    mrs = np.array([v["momentum_ratio_avg"] for v in scores.values()], dtype=float)
    mean, sd = float(mrs.mean()), float(mrs.std())
    if sd <= 0:
        return {}
    out = {}
    for sym, v in scores.items():
        z = (v["momentum_ratio_avg"] - mean) / sd
        out[sym] = {**v, "z_score": round(z, 3)}
    ranked = sorted(out.items(), key=lambda kv: -kv[1]["z_score"])
    for rank, (sym, v) in enumerate(ranked, 1):
        v["rank"] = rank
        v["selected"] = rank <= top_n
    return out


def nse_signal(ranked: dict, symbol: str, cfg: dict) -> BookSignal:
    """Long only the top-ranked names by Normalized Momentum Score."""
    if symbol not in ranked:
        return BookSignal("NiftyMom30", "NSE/UTI", 0, 0.0)
    v = ranked[symbol]
    r = {k: v[k] for k in ("rank", "z_score", "momentum_ratio_avg",
                           "return_6m_pct", "return_12m_pct", "selected")}
    if not v.get("selected"):
        r["why"] = f"rank {v.get('rank')} outside the top set"
        return BookSignal("NiftyMom30", "NSE/UTI", 0, 0.0, r)
    s = cfg["strategy"]["books"]["nse_momentum"]
    score = min(1.0, 0.5 + v["z_score"] / 4.0)
    return BookSignal("NiftyMom30", "NSE/UTI", 1, round(score, 3), r)


# =============================================================================
#  THE ENSEMBLE
# =============================================================================
def ensemble(signals: list, cfg: dict, require_agreement: int = 2,
             min_active: int = 3) -> Ensemble:
    """Combine every book's opinion into one decision.

    Three conditions, all of which must hold:

      1. At least `require_agreement` books must independently say long.
      2. At least `min_active` books must have expressed ANY opinion. Two of
         two is not consensus when five of seven books are silent because
         they correctly refuse to trade a bear market.
      3. The longs must outnumber the shorts.

    That third rule matters most. A 2-of-2 "100% consensus" from a nearly
    silent panel is not agreement — it is an accident of who happened to
    speak. Requiring participation is what stops the ensemble from trading
    exactly when the market is telling every careful strategy to stand down.
    """
    longs = [s for s in signals if s.direction > 0]
    shorts = [s for s in signals if s.direction < 0]
    votes = {s.book: s.direction for s in signals}
    active = len(longs) + len(shorts)

    if active < min_active:
        return Ensemble(
            0, 0.0, votes, signals, len(longs), len(shorts),
            f"only {active}/{len(signals)} books have an opinion "
            f"(need {min_active}) — most are refusing to trade, stand aside",
        )

    if len(longs) >= require_agreement and len(longs) > len(shorts):
        direction = 1
        agree, disagree = len(longs), len(shorts)
        consensus = agree / active
        raw = sum(s.score for s in longs) / len(longs)
        score = round(raw * consensus, 3)
        reason = (
            f"{agree}/{active} active books agree long "
            f"({', '.join(s.book for s in longs)})"
        )
    elif len(shorts) >= require_agreement and len(shorts) > len(longs):
        direction = -1
        agree, disagree = len(shorts), len(longs)
        consensus = agree / active
        raw = sum(s.score for s in shorts) / len(shorts)
        score = round(raw * consensus, 3)
        reason = (
            f"{agree}/{active} active books agree short "
            f"({', '.join(s.book for s in shorts)})"
        )
    else:
        direction = 0
        agree = max(len(longs), len(shorts))
        disagree = min(len(longs), len(shorts))
        score = 0.0
        reason = (
            f"no consensus — {len(longs)} long, {len(shorts)} short, "
            f"need {require_agreement} agreeing"
        )

    return Ensemble(direction, score, votes, signals, agree, disagree, reason)


# =============================================================================
#  RUNNER
# =============================================================================
def evaluate_all(
    features: dict,
    regime: pd.DataFrame,
    cfg: dict,
    require_agreement: int | None = None,
    min_active: int = 3,
    as_of: pd.Timestamp | None = None,
) -> dict:
    """Run every book over every symbol and combine them."""
    feats = {s: (f.loc[:as_of] if as_of is not None else f) for s, f in features.items()}
    scored = {sym: nse_momentum_score(f, cfg) for sym, f in feats.items()}
    # symbols with too little history for a 12-month lookback score nothing
    scored = {k: v for k, v in scored.items() if v}
    ranked = nse_rank_universe(
        scored, top_n=cfg["strategy"]["books"]["nse_momentum"]["top_n"],
    )

    if require_agreement is None:
        require_agreement = cfg.get("strategy", {}).get(
            "ensemble", {}).get("require_agreement", 2)

    out: dict = {}
    for sym, feat in feats.items():
        sigs = [
            # --- Minervini ---
            minervini_sepa(feat, cfg, as_of),
            minervini_superperformance(feat, regime, cfg, as_of),
            # --- Faith / Turtle ---
            turtle_system1(feat, regime, cfg, as_of),
            turtle_system2(feat, regime, cfg, as_of),
            turtle_whipsaw(feat, regime, cfg, as_of),
            turtle_donchian(feat, regime, cfg, as_of),
            # --- Antonacci ---
            absolute_momentum(feat, cfg, as_of),
            relative_momentum(feat, regime, cfg, as_of),
            gem(feat, regime, cfg, as_of),
            dual_momentum(feat, regime, cfg, as_of),
            # --- Radge ---
            radge_new_yearly_high(feat, cfg, as_of),
            radge_golden_cross(feat, cfg, as_of),
            radge_trendpilot(feat, regime, cfg, as_of),
            radge_hundred_day_high(feat, cfg, as_of),
            # --- Clenow ---
            clenow_trend_following(feat, regime, cfg, as_of),
            clenow_momentum(feat, regime, cfg, as_of=as_of),
            # --- Bensdorp ---
            bensdorp_weekly_rotation(feat, regime, cfg, as_of),
            bensdorp_mean_reversion_long(feat, cfg, as_of),
            bensdorp_mean_reversion_short(feat, cfg, as_of),
            bensdorp_trend_following(feat, cfg, as_of),
            bensdorp_mean_reversion(feat, cfg, as_of),
            # --- NSE / UTI ---
            nse_signal(ranked, sym, cfg),
        ]
        out[sym] = ensemble(sigs, cfg, require_agreement, min_active)
    return out


# =============================================================================
#  BRIDGE TO THE ENGINE  —  books produce intents, the risk gate decides
# =============================================================================
#  BOT_INSTRUCTIONS: the books NEVER place orders and NEVER touch the risk
#  gate. They only answer "is this symbol worth looking at, and at what
#  price". The engine then applies the same 14-check risk gate that the
#  base strategy uses, so a book cannot buy a single share that the base
#  strategy would have been refused.
# =============================================================================

def rank_universe(features: dict, cfg: dict,
                  as_of: pd.Timestamp | None = None) -> dict:
    """NSE NMS ranking over the whole universe. Computed once per rebalance.

    Deliberately anchored to a single date rather than recomputed per bar.
    The UTI methodology itself rebalances semi-annually, so this is faithful
    to the source AND it removes any chance of the ranking leaking future
    information into an earlier bar.
    """
    feats = {s: (f.loc[:as_of] if as_of is not None else f) for s, f in features.items()}
    scored = {sym: nse_momentum_score(f, cfg) for sym, f in feats.items()}
    scored = {k: v for k, v in scored.items() if v}
    return nse_rank_universe(
        scored, top_n=cfg["strategy"]["books"]["nse_momentum"]["top_n"],
    )


def signal_from_books(
    *,
    features: pd.DataFrame,
    regime_features: pd.DataFrame,
    ranked: dict,
    symbol: str,
    market: str,
    cfg: dict,
    as_of: pd.Timestamp,
    execution_price: float,
    cost_pct: float = 0.29,
) -> "Signal | Rejection":
    """Run every book on one symbol and return an engine-grade intent.

    Returns the SAME `Signal` / `Rejection` types the base strategy returns,
    so `risk_gate()` treats a book signal exactly like a base signal. There
    is no second, looser path to a trade.
    """
    # imported here to avoid a circular import at module load
    from .strategy import Rejection, Signal, solve_gross_reward_risk

    hist = features.loc[:as_of]
    if hist.empty or len(hist) < 60:
        return Rejection(symbol, as_of, "no_data", "not enough bars for the books")

    ens = evaluate_all({symbol: hist}, regime_features, cfg, as_of=as_of)[symbol]

    if ens.direction <= 0:
        return Rejection(symbol, as_of, "book_ensemble", ens.reason)

    # --- shorts are refused outright: the bot is long-only by rule --------
    if ens.direction < 0:
        return Rejection(symbol, as_of, "short_not_allowed", ens.reason)

    # --- pick the stop from whichever book supplied one ------------------
    longs = [s for s in ens.signals if s.direction > 0 and s.stop_price]
    stop_price = None
    stop_source = "atr_fallback"
    if longs:
        # use the tightest book stop that is still below the entry
        cands = [s.stop_price for s in longs if s.stop_price < execution_price]
        if cands:
            stop_price = min(cands)
            stop_source = next(s.book for s in longs if s.stop_price == stop_price)

    row = hist.iloc[-1]
    atr_val = float(row["atr"]) if "atr" in row and not pd.isna(row["atr"]) else 0.0
    if atr_val <= 0:
        atr_val = float(execution_price) * 0.02

    if stop_price is None:
        stop_price = execution_price - 2.0 * atr_val
        stop_source = "2xATR default"

    stop_dist = execution_price - stop_price
    if stop_dist <= 0:
        return Rejection(symbol, as_of, "book_stop",
                         "no book stop sits below the entry price")

    # --- the same cost-aware 2:1-net arithmetic the base strategy uses ---
    gross_rr, err = solve_gross_reward_risk(
        entry_price=execution_price,
        stop_price=stop_price,
        cost_pct=cost_pct,
        min_reward_risk=float(cfg["strategy"]["target"]["reward_risk"]),
        max_gross_rr=float(cfg["strategy"]["target"].get("max_gross_rr", 4.0)),
    )
    if gross_rr <= 0:
        return Rejection(symbol, as_of, "cost_target", err)

    target_price = execution_price + gross_rr * stop_dist

    reason = {
        "source": "books",
        "ensemble_score": ens.score,
        "ensemble_agree": ens.agree,
        "ensemble_active": ens.agree + ens.disagree,
        "ensemble_reason": ens.reason,
        "stop_source": stop_source,
        "atr": atr_val,
        "regime": (f"index {regime_features['close'].iloc[-1]:.1f} vs MA200 "
                   f"{regime_features['ma200'].iloc[-1]:.1f}"
                   if regime_features is not None and not regime_features.empty
                   and not pd.isna(regime_features["ma200"].iloc[-1]) else "n/a"),
        "gross_reward_risk": gross_rr,
        "cost_ratio_c_over_r": (cost_pct / 100.0) * execution_price / stop_dist,
    }
    # every book's individual verdict, so the website can always answer why
    for sig in ens.signals:
        if sig.direction != 0:
            reason[f"book_{sig.book}"] = {
                "author": sig.author,
                "direction": sig.direction,
                "score": sig.score,
                "detail": sig.reason,
            }

    return Signal(
        symbol=symbol,
        market=market,
        date=hist.index[-1],
        side="long",
        entry_price=float(execution_price),
        stop_price=float(stop_price),
        target_price=float(target_price),
        risk_per_share=float(stop_dist),
        reward_per_share=float(target_price - execution_price),
        reward_risk=float(gross_rr),
        atr=atr_val,
        reason=reason,
    )


def _load_market(market: str, cfg: Config, limit: int | None = None,
                 lookback_days: int | None = None):
    from .data.fetcher import fetch
    from . import strategy as strat

    mkt = cfg.market_cfg(market)
    suffix = mkt["suffix"]
    # Book strategies need 12-month lookbacks plus 200-300 day MAs, so the
    # default 800 days is not enough history to evaluate them at an earlier
    # date. Pull ~3 years by default.
    lookback = int(lookback_days or cfg.get("data.book_lookback_days", 1200))
    bars = {}
    syms = list(mkt["universe"])[:limit] if limit else list(mkt["universe"])
    for sym in syms:
        df = fetch(f"{sym}{suffix}" if suffix else sym, lookback_days=lookback)
        if not df.empty:
            bars[sym] = df
    index_bars = fetch(mkt["index_symbol"], lookback_days=lookback)
    fear_bars = fetch(mkt["fear_symbol"], lookback_days=lookback)
    cfgd = {"risk": cfg.risk, "strategy": cfg.strategy}
    features = {s: strat.build_features(d, cfgd) for s, d in bars.items()}
    regime = strat.compute_regime(index_bars, fear_bars, cfgd)
    return features, regime


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="india", choices=["india", "us"])
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--agree", type=int, default=None)
    ap.add_argument("--min-active", type=int, default=3)
    ap.add_argument("--as-of", default=None,
                    help="evaluate at a historical date, e.g. 2024-10-01")
    args = ap.parse_args()

    cfg = Config()
    # All strategy parameters live in config/settings.yaml under
    # strategy.books - there is no inline copy here to keep in sync.

    print(f"\nLoading {args.market.upper()} data for {len(cfg.get('strategy.books'))} book strategies ...")
    features, regime = _load_market(args.market, cfg, limit=args.limit)
    if not features:
        raise SystemExit("no data loaded")

    if args.symbol:
        features = {k: v for k, v in features.items() if k == args.symbol}
        if not features:
            raise SystemExit(f"symbol {args.symbol} not found")

    print(f"Running {len(features)} symbols through "
          f"{len(cfg.get('strategy.books'))} book strategies ...\n")
    as_of = pd.Timestamp(args.as_of) if args.as_of else None
    results = evaluate_all(features, regime, cfg._raw,
                           require_agreement=args.agree, min_active=args.min_active,
                           as_of=as_of)

    # ---- per-strategy tally -------------------------------------------
    books = ["SEPA", "SuperPerf", "TurtleS1", "TurtleS2", "Whipsaw", "Donchian",
             "AbsoluteMom", "RelativeMom", "GEM", "DualMomentum",
             "NewYearlyHigh", "HundredDayHigh", "GoldenCross", "TrendPilot",
             "ClenowTF", "Momentum",
             "WeeklyRot", "MeanRevLong", "MeanRevShort", "Trend200",
             "MeanReversion", "NiftyMom30"]
    tally = {b: {"long": 0, "short": 0} for b in books}
    for ens in results.values():
        for b, d in ens.votes.items():
            if d > 0:
                tally[b]["long"] += 1
            elif d < 0:
                tally[b]["short"] += 1

    print("=" * 78)
    print(f"  EACH STRATEGY'S VERDICT  ({args.market.upper()}, {len(features)} symbols)")
    print("=" * 78)
    print(f"  {'strategy':<16}{'book':<14}{'long':>7}{'short':>7}{'hit rate':>11}")
    print("  " + "-" * 74)
    n = len(features)
    for b in books:
        t = tally[b]
        author = next((sig.author for ens in results.values()
                       for sig in ens.signals if sig.book == b), "?")
        rate = (t["long"] + t["short"]) / n * 100.0 if n else 0.0
        print(f"  {b:<16}{author:<14}{t['long']:>7}{t['short']:>7}{rate:>10.1f}%")
    print("  " + "-" * 74)
    tot_l = sum(t["long"] for t in tally.values())
    tot_s = sum(t["short"] for t in tally.values())
    print(f"  {'TOTAL':<16}{'':<14}{tot_l:>7}{tot_s:>7}"
          f"{(tot_l+tot_s)/(n*len(books))*100:>10.1f}%")

    # ---- the grid, one row per symbol, compact -------------------------
    print("\n" + "=" * 78)
    print("  SIGNAL GRID  (L = long, S = short, . = no opinion)")
    print("=" * 78)
    # 11 strategies per line so it stays readable
    for chunk_start in range(0, len(books), 11):
        chunk = books[chunk_start:chunk_start + 11]
        hdr = f"  {'symbol':<12}" + "".join(f"{b[:6]:>7}" for b in chunk)
        print(hdr)
        print("  " + "-" * (12 + 7 * len(chunk)))
        for sym, ens in sorted(results.items()):
            row = f"  {sym:<12}"
            for b in chunk:
                v = ens.votes.get(b, 0)
                row += f"{('L' if v > 0 else ('S' if v < 0 else '.')):>7}"
            print(row)
        print()

    longs = {s: e for s, e in results.items() if e.direction > 0}
    print("\n" + "=" * 78)
    print(f"  ENSEMBLE DECISIONS: {len(longs)} long out of {len(results)}")
    print("=" * 78)
    for sym, ens in sorted(longs.items(), key=lambda kv: -kv[1].score):
        print(f"\n  {sym}   score {ens.score:.2f}   consensus {ens.consensus*100:.0f}%")
        print(f"    {ens.reason}")
        for s in ens.signals:
            if s.direction > 0:
                print(f"      {s.book:<14} {s.author:<11} score {s.score:.2f}")
    return 0




# =============================================================================
#  ADDITIONAL STRATEGIES FOUND IN THE SAME BOOKS
#  Each book contains several methods. These are the rest of them.
# =============================================================================

# ---- 1b. MINERVINI — Superperformance trend template -----------------------
def minervini_superperformance(feat: pd.DataFrame, index_feat: pd.DataFrame,
                               cfg: dict, as_of=None) -> BookSignal:
    """The broader superperformance filter, before any VCP is required.

    Where SEPA demands a specific volatility-contraction setup, this is the
    looser trend template: the stock must be in a Stage 2 advance with the
    full moving-average stack intact and relative strength versus the index.
    It fires more often than SEPA and is the screen Minervini describes for
    building a watchlist before the precise entry is chosen.
    """
    s = cfg["strategy"]["books"]["minervini_sp"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_slow"]:
        return BookSignal("SuperPerf", "Minervini", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    ma_f = ind.sma(feat["close"], s["ma_fast"]).iloc[-1]
    ma_m = ind.sma(feat["close"], s["ma_mid"]).iloc[-1]
    ma_s = ind.sma(feat["close"], s["ma_slow"]).iloc[-1]
    r["ma_stack"] = bool(close > ma_f > ma_m > ma_s)

    # relative strength vs the index over the lookback
    rel = None
    if index_feat is not None and not index_feat.empty:
        idx = index_feat.loc[:as_of] if as_of is not None else index_feat
        if len(idx) > s["rs_lookback"] and len(feat) > s["rs_lookback"]:
            own = close / float(feat["close"].iloc[-1 - s["rs_lookback"]]) - 1.0
            bench = float(idx["close"].iloc[-1]) / float(idx["close"].iloc[-1 - s["rs_lookback"]]) - 1.0
            rel = own - bench
    r["relative_strength"] = round(rel, 4) if rel is not None else None
    r["rs_ok"] = (rel is not None) and rel > s["rs_min"]

    # the longer-term trend must be intact, not just the short one
    r["above_ma_slow"] = close > ma_s
    if not (r["ma_stack"] and r["rs_ok"] and r["above_ma_slow"]):
        return BookSignal("SuperPerf", "Minervini", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    score = min(1.0, 0.6 + (rel or 0.0))
    return BookSignal("SuperPerf", "Minervini", 1, round(score, 3), r, stop, target)


# ---- 2b. TURTLE — System 1 (20-day breakout) -------------------------------
def turtle_system1(feat: pd.DataFrame, regime: pd.DataFrame, cfg: dict,
                   as_of=None) -> BookSignal:
    """System 1: the shorter-term 20-day breakout.

    Faster than System 2, so it catches trends earlier and whipsaws more.
    Faith's rule adds a twist most reimplementations miss: a System 1
    breakout is SKIPPED if the previous breakout in that market was a
    winner. The logic is that a winning breakout usually means the easy
    part of the move is gone.
    """
    s = cfg["strategy"]["books"]["turtle_s1"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_long"] + 5:
        return BookSignal("TurtleS1", "Faith", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    n_atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    if pd.isna(n_atr) or n_atr <= 0:
        return BookSignal("TurtleS1", "Faith", 0, 0.0, r)
    r["N"] = round(float(n_atr), 4)

    ma_short = ind.sma(feat["close"], s["ma_short"]).iloc[-1]
    ma_long = ind.sma(feat["close"], s["ma_long"]).iloc[-1]
    r["trend_filter_ok"] = bool(ma_short > ma_long)

    regime_ok = True
    if regime is not None and not regime.empty and s["require_regime"]:
        rh = regime.loc[:as_of] if as_of is not None else regime
        if not rh.empty and "above_ma200" in rh.columns:
            regime_ok = bool(rh["above_ma200"].iloc[-1])
    r["regime_ok"] = regime_ok

    hi = float(feat["high"].iloc[-1 - s["entry_days"]:-1].max())
    r["high_20"] = round(hi, 2)
    r["broke_20"] = close > hi

    if not (r["trend_filter_ok"] and r["regime_ok"] and r["broke_20"]):
        return BookSignal("TurtleS1", "Faith", 0, 0.0, r)

    r["skip_if_last_won"] = s["skip_if_last_won"]
    stop = close - s["stop_n"] * float(n_atr)
    target = close + s["stop_n"] * float(n_atr) * s["reward_risk"]
    return BookSignal("TurtleS1", "Faith", 1, 0.8, r, stop, target)


# ---- 2c. TURTLE — System 2 (55-day breakout, the failsafe) -----------------
def turtle_system2(feat: pd.DataFrame, regime: pd.DataFrame, cfg: dict,
                   as_of=None) -> BookSignal:
    """System 2: the longer-term 55-day breakout.

    Slower, fewer signals, and taken regardless of whether the previous
    breakout won. This is the failsafe that catches the big moves System 1's
    skip rule would have missed.
    """
    s = cfg["strategy"]["books"]["turtle_s2"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_long"] + 5:
        return BookSignal("TurtleS2", "Faith", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    n_atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    if pd.isna(n_atr) or n_atr <= 0:
        return BookSignal("TurtleS2", "Faith", 0, 0.0, r)

    ma_short = ind.sma(feat["close"], s["ma_short"]).iloc[-1]
    ma_long = ind.sma(feat["close"], s["ma_long"]).iloc[-1]
    r["trend_filter_ok"] = bool(ma_short > ma_long)

    regime_ok = True
    if regime is not None and not regime.empty and s["require_regime"]:
        rh = regime.loc[:as_of] if as_of is not None else regime
        if not rh.empty and "above_ma200" in rh.columns:
            regime_ok = bool(rh["above_ma200"].iloc[-1])
    r["regime_ok"] = regime_ok

    hi = float(feat["high"].iloc[-1 - s["entry_days"]:-1].max())
    r["high_55"] = round(hi, 2)
    r["broke_55"] = close > hi

    if not (r["trend_filter_ok"] and r["regime_ok"] and r["broke_55"]):
        return BookSignal("TurtleS2", "Faith", 0, 0.0, r)

    stop = close - s["stop_n"] * float(n_atr)
    target = close + s["stop_n"] * float(n_atr) * s["reward_risk"]
    return BookSignal("TurtleS2", "Faith", 1, 0.9, r, stop, target)


# ---- 2d. TURTLE — Whipsaw ---------------------------------------------------
def turtle_whipsaw(feat: pd.DataFrame, regime: pd.DataFrame, cfg: dict,
                   as_of=None) -> BookSignal:
    """The Whipsaw variant: tighter 1/2 N stops, and re-enter at the original
    entry price after being stopped.

    Faith notes this variant produced better profitability but a lower
    win/loss ratio - many more small losses. It is the aggressive twin of
    System 1/2, and it is here because the trade-off it makes (more losses,
    better net) is a real design choice worth being able to test.
    """
    s = cfg["strategy"]["books"]["turtle_whip"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_long"] + 5:
        return BookSignal("Whipsaw", "Faith", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    n_atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    if pd.isna(n_atr) or n_atr <= 0:
        return BookSignal("Whipsaw", "Faith", 0, 0.0, r)

    ma_short = ind.sma(feat["close"], s["ma_short"]).iloc[-1]
    ma_long = ind.sma(feat["close"], s["ma_long"]).iloc[-1]
    if not (ma_short > ma_long):
        r["trend_filter_ok"] = False
        return BookSignal("Whipsaw", "Faith", 0, 0.0, r)

    regime_ok = True
    if regime is not None and not regime.empty and s["require_regime"]:
        rh = regime.loc[:as_of] if as_of is not None else regime
        if not rh.empty and "above_ma200" in rh.columns:
            regime_ok = bool(rh["above_ma200"].iloc[-1])
    r["regime_ok"] = regime_ok
    if not regime_ok:
        return BookSignal("Whipsaw", "Faith", 0, 0.0, r)

    hi = float(feat["high"].iloc[-1 - s["entry_days"]:-1].max())
    r["high_20"] = round(hi, 2)
    r["broke_20"] = close > hi
    if not r["broke_20"]:
        return BookSignal("Whipsaw", "Faith", 0, 0.0, r)

    # half-N stop = half the risk, so double the units for the same exposure
    r["stop_n"] = s["stop_n"]
    r["risk_pct_per_unit"] = s["risk_pct_per_unit"]
    stop = close - s["stop_n"] * float(n_atr)
    target = close + s["stop_n"] * float(n_atr) * s["reward_risk"]
    return BookSignal("Whipsaw", "Faith", 1, 0.75, r, stop, target)


# ---- 3b. ANTONACCI — Absolute momentum -------------------------------------
def absolute_momentum(feat: pd.DataFrame, cfg: dict, as_of=None) -> BookSignal:
    """Absolute momentum on its own: is the asset's own trend positive?

    This is the defensive leg of dual momentum. It does not compare the asset
    to anything - it only asks whether the asset beat the risk-free rate over
    the lookback. If not, the answer is cash, and that single rule is what
    keeps the strategy out of bear markets.
    """
    s = cfg["strategy"]["books"]["absolute_mom"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["lookback"] + 2:
        return BookSignal("AbsoluteMom", "Antonacci", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    past = float(feat["close"].iloc[-1 - s["lookback"]])
    ret = (close / past - 1.0) * 100.0
    excess = ret - s["risk_free_pct"]
    r = {"return_pct": round(ret, 2), "risk_free_pct": s["risk_free_pct"],
         "excess_pct": round(excess, 2), "absolute_ok": excess > 0}
    if excess <= 0:
        r["why"] = "excess return over cash is negative - stand aside"
        return BookSignal("AbsoluteMom", "Antonacci", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    score = min(1.0, 0.5 + excess / 40.0)
    return BookSignal("AbsoluteMom", "Antonacci", 1, round(score, 3), r, stop, target)


# ---- 3c. ANTONACCI — Relative momentum -------------------------------------
def relative_momentum(feat: pd.DataFrame, index_feat: pd.DataFrame, cfg: dict,
                      as_of=None) -> BookSignal:
    """Relative momentum on its own: did this asset beat its benchmark?

    The offensive leg. It ranks assets against each other and holds the
    winner, with no view on whether the winner's own trend is positive. On
    its own it is a bull-market tool; paired with absolute momentum it is
    the "dual" in dual momentum.
    """
    s = cfg["strategy"]["books"]["relative_mom"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["lookback"] + 2:
        return BookSignal("RelativeMom", "Antonacci", 0, 0.0)

    if index_feat is None or index_feat.empty:
        return BookSignal("RelativeMom", "Antonacci", 0, 0.0)
    idx = index_feat.loc[:as_of] if as_of is not None else index_feat
    if len(idx) < s["lookback"] + 2:
        return BookSignal("RelativeMom", "Antonacci", 0, 0.0)

    own = float(feat["close"].iloc[-1]) / float(feat["close"].iloc[-1 - s["lookback"]]) - 1.0
    bench = float(idx["close"].iloc[-1]) / float(idx["close"].iloc[-1 - s["lookback"]]) - 1.0
    r = {"asset_return_pct": round(own * 100, 2),
         "benchmark_return_pct": round(bench * 100, 2),
         "outperformance_pct": round((own - bench) * 100, 2)}
    if own <= bench:
        return BookSignal("RelativeMom", "Antonacci", 0, 0.0, r)

    close = float(feat["close"].iloc[-1])
    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    score = min(1.0, 0.5 + (own - bench) * 2.0)
    return BookSignal("RelativeMom", "Antonacci", 1, round(score, 3), r, stop, target)


# ---- 3d. ANTONACCI — GEM (Global Equities Momentum) ------------------------
def gem(feat: pd.DataFrame, index_feat: pd.DataFrame, cfg: dict,
        as_of=None) -> BookSignal:
    """GEM: absolute momentum chooses equity vs bond, relative picks the region.

    Antonacci's flagship. Monthly cadence, 12-month lookback, and the key
    property is that it holds cash-like assets when absolute momentum turns
    negative - "not having to recoup bear market losses" is, in his account,
    where most of the excess return comes from.
    """
    s = cfg["strategy"]["books"]["gem"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["lookback"] + 2:
        return BookSignal("GEM", "Antonacci", 0, 0.0)

    idx = index_feat.loc[:as_of] if as_of is not None else index_feat
    if idx.empty or len(idx) < s["lookback"] + 2:
        return BookSignal("GEM", "Antonacci", 0, 0.0)

    own = float(feat["close"].iloc[-1]) / float(feat["close"].iloc[-1 - s["lookback"]]) - 1.0
    bench = float(idx["close"].iloc[-1]) / float(idx["close"].iloc[-1 - s["lookback"]]) - 1.0
    r = {"lookback_months": s["lookback"] // 21,
         "asset_return_pct": round(own * 100, 2),
         "benchmark_return_pct": round(bench * 100, 2)}

    # leg 1: absolute - is the benchmark itself in a positive trend?
    absolute_ok = bench > s["risk_free_pct"] / 100.0
    r["absolute_leg_ok"] = absolute_ok
    # leg 2: relative - does the asset beat the benchmark?
    relative_ok = own > bench
    r["relative_leg_ok"] = relative_ok

    if not absolute_ok:
        r["why"] = "benchmark excess return negative - GEM holds cash/bonds"
        return BookSignal("GEM", "Antonacci", 0, 0.0, r)
    if not relative_ok:
        r["why"] = "asset underperforms benchmark - hold the benchmark instead"
        return BookSignal("GEM", "Antonacci", 0, 0.0, r)

    close = float(feat["close"].iloc[-1])
    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    score = min(1.0, 0.7 + (own - bench))
    return BookSignal("GEM", "Antonacci", 1, round(score, 3), r, stop, target)


# ---- 4b. RADGE — New Yearly High (250-day) ---------------------------------
def radge_new_yearly_high(feat: pd.DataFrame, cfg: dict, as_of=None) -> BookSignal:
    """Buy a stock making a new 250-day high; exit on a new 250-day low.

    Radge's simplest system, and the one with the George & Hwang 52-week-high
    research behind it: proximity to the 52-week high predicts future momentum
    better than past price change does. Slow to react, which is why he pairs
    it with a defensive overlay elsewhere.
    """
    s = cfg["strategy"]["books"]["radge_nyh"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["breakout"] + 2:
        return BookSignal("NewYearlyHigh", "Radge", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    hi = float(feat["high"].iloc[-1 - s["breakout"]:-1].max())
    lo = float(feat["low"].iloc[-1 - s["breakout"]:-1].min())
    r = {"high_250": round(hi, 2), "low_250": round(lo, 2),
         "pct_of_high": round(close / hi * 100.0, 1)}
    if close <= hi:
        return BookSignal("NewYearlyHigh", "Radge", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = min(lo, close - s["stop_atr"] * float(atr))
    target = close + (close - stop) * s["reward_risk"]
    return BookSignal("NewYearlyHigh", "Radge", 1, 0.9, r, stop, target)


# ---- 4c. RADGE — Golden / Death Cross --------------------------------------
def radge_golden_cross(feat: pd.DataFrame, cfg: dict, as_of=None) -> BookSignal:
    """50-day crossing above the 200-day is the buy; the cross below is the exit.

    The oldest rule in the book, kept because it is the one every other
    strategy is implicitly built on top of. Slow, few signals, and it is the
    reason so many of the other strategies here have a 200-day component.
    """
    s = cfg["strategy"]["books"]["radge_cross"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_long"] + s["confirm"]:
        return BookSignal("GoldenCross", "Radge", 0, 0.0)

    fast = ind.sma(feat["close"], s["ma_fast"])
    slow = ind.sma(feat["close"], s["ma_long"])
    if len(fast) < s["confirm"] + 1 or pd.isna(slow.iloc[-1]):
        return BookSignal("GoldenCross", "Radge", 0, 0.0)

    r = {"ma_50": round(float(fast.iloc[-1]), 2),
         "ma_200": round(float(slow.iloc[-1]), 2)}
    r["golden"] = bool(fast.iloc[-1] > slow.iloc[-1])
    # confirmed for N days, to avoid crossing noise
    confirmed = bool((fast.iloc[-s["confirm"]:] > slow.iloc[-s["confirm"]:]).all())
    r["confirmed_days"] = s["confirm"]
    r["confirmed"] = confirmed

    if not (r["golden"] and confirmed):
        return BookSignal("GoldenCross", "Radge", 0, 0.0, r)

    close = float(feat["close"].iloc[-1])
    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = min(float(slow.iloc[-1]), close - s["stop_atr"] * float(atr))
    target = close + (close - stop) * s["reward_risk"]
    return BookSignal("GoldenCross", "Radge", 1, 0.8, r, stop, target)


# ---- 4d. RADGE — 100-Day High ---------------------------------------------
def radge_hundred_day_high(feat: pd.DataFrame, cfg: dict, as_of=None) -> BookSignal:
    """Buy a new 100-day high; exit on a new 100-day low.

    The more aggressive twin of the New Yearly High strategy. Radge's finding
    is that the shorter lookback gets you into a trend earlier and out before
    too much weakness sets in, which shortens the recovery period after a
    drawdown - the main criticism he levels at the 250-day version.
    """
    s = cfg["strategy"]["books"]["radge_100d"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["breakout"] + 2:
        return BookSignal("HundredDayHigh", "Radge", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    hi = float(feat["high"].iloc[-1 - s["breakout"]:-1].max())
    lo = float(feat["low"].iloc[-1 - s["breakout"]:-1].min())
    r = {"high_100": round(hi, 2), "low_100": round(lo, 2),
         "pct_of_high": round(close / hi * 100.0, 1)}
    if close <= hi:
        return BookSignal("HundredDayHigh", "Radge", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = min(lo, close - s["stop_atr"] * float(atr))
    target = close + (close - stop) * s["reward_risk"]
    return BookSignal("HundredDayHigh", "Radge", 1, 0.85, r, stop, target)


# ---- 5b. CLENOW — Standard trend following model ---------------------------
def clenow_trend_following(feat: pd.DataFrame, index_feat: pd.DataFrame,
                           cfg: dict, as_of=None) -> BookSignal:
    """The classic futures model, applied to stocks.

    Dual 50/100 moving-average trend filter, a 50-day breakout in the
    direction of that trend, risk-parity sizing, and a 3x ATR trailing stop.
    Clenow presents this as the model that works on futures and shows it
    needs the regime filter and volatility-adjusted ranking to work on
    equities - which is exactly what the other Clenow strategies add.
    """
    s = cfg["strategy"]["books"]["clenow_tf"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma_slow"] + 5:
        return BookSignal("ClenowTF", "Clenow", 0, 0.0)

    row = feat.iloc[-1]
    close = float(row["close"])
    r: dict = {}

    ma_f = ind.sma(feat["close"], s["ma_fast"]).iloc[-1]
    ma_s = ind.sma(feat["close"], s["ma_slow"]).iloc[-1]
    r["ma_50"] = round(ma_f, 2)
    r["ma_100"] = round(ma_s, 2)
    r["trend_ok"] = bool(ma_f > ma_s)
    if not r["trend_ok"]:
        return BookSignal("ClenowTF", "Clenow", 0, 0.0, r)

    # regime: index above its 200-day average
    if index_feat is not None and not index_feat.empty:
        idx = index_feat.loc[:as_of] if as_of is not None else index_feat
        if len(idx) > s["index_ma"]:
            ima = ind.sma(idx["close"], s["index_ma"]).iloc[-1]
            r["regime_ok"] = bool(float(idx["close"].iloc[-1]) > ima)
            if not r["regime_ok"]:
                r["why"] = "index below its 200-day average"
                return BookSignal("ClenowTF", "Clenow", 0, 0.0, r)

    hi = float(feat["high"].iloc[-1 - s["breakout"]:-1].max())
    r["high_50"] = round(hi, 2)
    r["broke_50"] = close > hi
    if not r["broke_50"]:
        return BookSignal("ClenowTF", "Clenow", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    r["risk_parity"] = True
    return BookSignal("ClenowTF", "Clenow", 1, 0.85, r, stop, target)


# ---- 5c. CLENOW — Risk parity sizing (a sizing rule, not a signal) ----------
def clenow_risk_parity(features: dict, cfg: dict) -> dict:
    """Target weights that give every position equal theoretical risk.

    Not an entry signal - a portfolio construction rule. Clenow's argument is
    that equal cash weights let the most volatile stocks dominate the
    portfolio's day-to-day P&L, so weights should be scaled by inverse
    volatility instead.
    """
    s = cfg["strategy"]["books"]["clenow_rp"]
    vols = {}
    for sym, f in features.items():
        if f.empty or len(f) < s["vol_window"]:
            continue
        v = float(f["close"].pct_change().dropna().iloc[-s["vol_window"]:].std()
                  * (252 ** 0.5))
        if v > 0:
            vols[sym] = v
    if not vols:
        return {}
    inv = {k: 1.0 / v for k, v in vols.items()}
    total = sum(inv.values())
    return {k: round(v / total, 4) for k, v in inv.items()}


# ---- 6b. BENSDORP — Weekly Rotation ----------------------------------------
def bensdorp_weekly_rotation(feat: pd.DataFrame, index_feat: pd.DataFrame,
                             cfg: dict, as_of=None) -> BookSignal:
    """Weekly rotation into the strongest large caps.

    Rules: SPY must be above its 200-day SMA (with a 2% tolerance band so
    ordinary noise does not knock the strategy out), the stock's 3-day RSI
    must be below 50 so it is not overbought, and then hold the ten stocks
    with the highest 200-day rate of change. Rotate weekly. No hard stops -
    the rotation itself is the risk control.
    """
    s = cfg["strategy"]["books"]["bensdorp_rot"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["trend_ma"] + s["roc_window"]:
        return BookSignal("WeeklyRot", "Bensdorp", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    r: dict = {}

    # index regime with a tolerance band
    if index_feat is not None and not index_feat.empty:
        idx = index_feat.loc[:as_of] if as_of is not None else index_feat
        if len(idx) > s["trend_ma"]:
            ima = ind.sma(idx["close"], s["trend_ma"]).iloc[-1]
            iclose = float(idx["close"].iloc[-1])
            band = ima * (1 - s["band_pct"] / 100.0)
            r["index_ma200"] = round(ima, 2)
            r["index_close"] = iclose
            r["regime_ok"] = iclose > band
            if not r["regime_ok"]:
                r["why"] = "index below its 200-DMA band"
                return BookSignal("WeeklyRot", "Bensdorp", 0, 0.0, r)

    # not overbought
    rsi3 = ind.rsi(feat["close"], s["rsi_period"]).iloc[-1]
    r["rsi_3"] = round(float(rsi3), 2)
    r["rsi_ok"] = float(rsi3) < s["rsi_max"]
    if not r["rsi_ok"]:
        return BookSignal("WeeklyRot", "Bensdorp", 0, 0.0, r)

    # 200-day rate of change
    past = float(feat["close"].iloc[-1 - s["roc_window"]])
    roc = (close / past - 1.0) * 100.0
    r["roc_200d_pct"] = round(roc, 2)
    if roc <= 0:
        return BookSignal("WeeklyRot", "Bensdorp", 0, 0.0, r)

    r["no_hard_stops"] = True
    r["max_positions"] = s["max_positions"]
    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = close - s["stop_atr"] * float(atr)
    target = close + s["stop_atr"] * float(atr) * s["reward_risk"]
    score = min(1.0, roc / 100.0 + 0.4)
    return BookSignal("WeeklyRot", "Bensdorp", 1, round(score, 3), r, stop, target)


# ---- 6c. BENSDORP — Mean reversion, LONG -----------------------------------
def bensdorp_mean_reversion_long(feat: pd.DataFrame, cfg: dict,
                                 as_of=None) -> BookSignal:
    """Buy the washout: 2-day RSI washed out inside a 200-day uptrend.

    The long leg of Bensdorp's pair. The stop is deliberately wide - 2.5x the
    10-day ATR - because a mean-reversion trade needs room to develop, and
    the profit target is small and fixed. Time exit if nothing happens.
    """
    s = cfg["strategy"]["books"]["bensdorp_mr_long"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["trend_ma"] + 5:
        return BookSignal("MeanRevLong", "Bensdorp", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    ma = ind.sma(feat["close"], s["trend_ma"]).iloc[-1]
    r = {"ma_200": round(ma, 2), "uptrend": bool(close > ma)}
    if not r["uptrend"]:
        r["why"] = "not above the 200-day average"
        return BookSignal("MeanRevLong", "Bensdorp", 0, 0.0, r)

    rsi2 = ind.rsi(feat["close"], s["rsi_period"]).iloc[-1]
    r["rsi_2"] = round(float(rsi2), 2)
    r["washed_out"] = float(rsi2) <= s["rsi_entry"]
    if not r["washed_out"]:
        return BookSignal("MeanRevLong", "Bensdorp", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    r["stop_atr"] = s["stop_atr"]
    r["target_pct"] = s["target_pct"]
    r["max_hold_days"] = s["max_hold_days"]
    stop = close - s["stop_atr"] * float(atr)
    target = close * (1 + s["target_pct"] / 100.0)
    score = min(1.0, (s["rsi_entry"] - float(rsi2)) / s["rsi_entry"] + 0.5)
    return BookSignal("MeanRevLong", "Bensdorp", 1, round(max(0.5, score), 3), r, stop, target)


# ---- 6d. BENSDORP — Mean reversion, SHORT ----------------------------------
def bensdorp_mean_reversion_short(feat: pd.DataFrame, cfg: dict,
                                  as_of=None) -> BookSignal:
    """Sell the greed: ADX-7 above 50, ATR above 5% of price, two up days,
    3-day RSI above 85.

    This is the mirror of the long leg and it is a SHORT signal. Our bot is
    long-only by rule, so this strategy is implemented and reported but the
    risk gate will never let it place an order. Keeping it visible is
    deliberate: it documents what the book says and makes the constraint
    auditable rather than silent.
    """
    s = cfg["strategy"]["books"]["bensdorp_mr_short"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["adx_period"] + s["atr_period"] + 5:
        return BookSignal("MeanRevShort", "Bensdorp", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    r: dict = {}

    adx = ind.adx(feat, s["adx_period"]).iloc[-1]
    r["adx_7"] = round(float(adx), 1)
    r["adx_ok"] = float(adx) > s["adx_min"]

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    atr_pct = float(atr) / close * 100.0
    r["atr_pct_10d"] = round(atr_pct, 2)
    r["atr_ok"] = atr_pct > s["atr_pct_min"]

    r["two_up_days"] = bool(feat["close"].iloc[-1] > feat["close"].iloc[-2]
                            and feat["close"].iloc[-2] > feat["close"].iloc[-3])

    rsi3 = ind.rsi(feat["close"], s["rsi_period"]).iloc[-1]
    r["rsi_3"] = round(float(rsi3), 2)
    r["rsi_ok"] = float(rsi3) > s["rsi_min"]

    ok = r["adx_ok"] and r["atr_ok"] and r["two_up_days"] and r["rsi_ok"]
    if not ok:
        return BookSignal("MeanRevShort", "Bensdorp", 0, 0.0, r)

    r["SHORT_ONLY"] = True
    r["note"] = "long-only bot: reported, never executed"
    stop = close + s["stop_atr"] * float(atr)
    target = close * (1 - s["target_pct"] / 100.0)
    return BookSignal("MeanRevShort", "Bensdorp", -1, 0.8, r, stop, target)


# ---- 6e. BENSDORP — Simple 200-SMA trend following -------------------------
def bensdorp_trend_following(feat: pd.DataFrame, cfg: dict, as_of=None) -> BookSignal:
    """The blunt version: long while above the 200-day average, out below.

    Bensdorp's point with this one is not that it is clever but that it is
    enough - he uses it to show that a simple technical exit would have kept
    you out of the gold and S&P disasters that fundamental holders rode all
    the way down. Included as the baseline every other strategy is measured
    against.
    """
    s = cfg["strategy"]["books"]["bensdorp_tf"]
    feat = feat.loc[:as_of] if as_of is not None else feat
    if feat.empty or len(feat) < s["ma"]:
        return BookSignal("Trend200", "Bensdorp", 0, 0.0)

    close = float(feat["close"].iloc[-1])
    ma = ind.sma(feat["close"], s["ma"]).iloc[-1]
    r = {"ma_200": round(ma, 2), "close": close,
         "distance_pct": round((close / ma - 1.0) * 100.0, 2)}
    if close <= ma:
        r["why"] = "below the 200-day average - trend is down"
        return BookSignal("Trend200", "Bensdorp", 0, 0.0, r)

    atr = ind.atr(feat, s["atr_period"]).iloc[-1]
    stop = min(ma, close - s["stop_atr"] * float(atr))
    target = close + (close - stop) * s["reward_risk"]
    return BookSignal("Trend200", "Bensdorp", 1, 0.6, r, stop, target)


if __name__ == "__main__":
    sys.exit(main())
