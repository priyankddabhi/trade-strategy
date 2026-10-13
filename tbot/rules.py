"""The lab strategies, run together with the trend breakout.

Every rule is copied from research/lab/lab.py and research/lab/lab2.py (the lab that tested them on ten
years of real prices), so the bot trades what was tested. Each share remembers the rule that bought it and
is sold by that rule's own exit. All rules share the slots, the risk gate, breakeven at +1R and the stop
that sits at the broker. The order in strategy.use is the priority: when two rules want to buy, the first wins.

Small differences from the lab, written here so nothing is hidden:
- the bot's market check (index under its 200-day average, fear gauge) still applies to every rule,
- one set of slots for all rules (the lab gave every rule its own 5),
- a 2R target is lifted by the costs (at most +0.5R) so the "reward:risk 2 after costs" rule can pass,
- rules with no target are checked by the gate against the bot's profit target (take_profit_r),
- Longboard: highest close of the last 3 years (lab: since the data starts), so live and tests agree,
- Fear Pullback: time exit at the next open after 4 sessions (lab: at the 4th close), stop checked on the buy day,
- weekly and half-year reviews: the last session of the week (or of March and September); for today, the next
  working day decides, so a holiday Friday can skip one weekly review.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .strategy import Candidate, TrendBreakout

LABELS = {
    "trend_breakout": "Trend Breakout",
    "bollinger": "Bollinger Breakout",
    "flipper": "20% Flipper",
    "trendpilot": "TrendPilot",
    "new_yearly_high": "New Yearly High",
    "nifty_mom30": "Momentum 30 (half-year)",
    "hundred_day_high": "100-Day High",
    "longboard": "Longboard (3-year high)",
    "trend200": "Trend 200",
    "sepa": "SEPA (Minervini)",
    "golden_cross": "Golden Cross",
    "clenow_tf": "Clenow Trend Following",
    "mean_reversion": "Mean Reversion",
    "clenow_momentum": "Clenow Momentum",
    "dual_momentum": "Dual Momentum",
    "absolute_momentum": "Absolute Momentum",
    "relative_momentum": "Relative Momentum",
    "turtle_s2": "Turtle System 2",
    "gem": "GEM",
    "weekly_rotation": "Weekly Rotation",
    "fear_pullback": "Fear Pullback",
}
# Lab lists, best 10-year profit factor first (research/lab/table.md and table2.md).
US = ["bollinger", "flipper", "trendpilot", "new_yearly_high", "nifty_mom30", "hundred_day_high", "longboard",
      "trend200", "sepa", "golden_cross", "clenow_tf", "mean_reversion", "clenow_momentum", "dual_momentum",
      "absolute_momentum", "relative_momentum", "turtle_s2", "gem", "weekly_rotation"]
INDIA = ["nifty_mom30", "bollinger", "longboard", "flipper", "trendpilot", "hundred_day_high", "fear_pullback",
         "new_yearly_high", "clenow_momentum", "relative_momentum", "gem", "golden_cross", "trend200",
         "absolute_momentum", "dual_momentum"]
ALL = list(LABELS)

TWO_R = {"sepa", "turtle_s2", "absolute_momentum", "relative_momentum", "gem", "dual_momentum", "new_yearly_high",
         "golden_cross", "trendpilot", "hundred_day_high", "clenow_tf", "clenow_momentum", "trend200"}
NO_TARGET = {"flipper", "bollinger", "longboard", "weekly_rotation", "nifty_mom30"}
WHY = {
    "bollinger": "close above the 100-day average plus 3 deviations",
    "flipper": "close 20% above its lowest close since the last sell signal",
    "trendpilot": "new 100-day high, index above its 200-day average for 5 sessions",
    "new_yearly_high": "new 250-day high",
    "nifty_mom30": "in the top 30 by risk-adjusted 6 and 12-month momentum at the half-year review",
    "hundred_day_high": "new 100-day high",
    "longboard": "highest close of the last 3 years",
    "trend200": "close above the 200-day average",
    "sepa": "trend template, tighter swings, 20-day high on 1.5x volume",
    "golden_cross": "50-day average above the 200-day for 5 sessions",
    "clenow_tf": "new 50-day high, 50-day average above the 100-day, index up",
    "mean_reversion": "2-day RSI 10 or less in an uptrend with a strong 7-day ADX",
    "clenow_momentum": "steady 90-day climb above the 100-day average, index up",
    "dual_momentum": "12-month return above 6.2%",
    "absolute_momentum": "12-month return above 6.2%",
    "relative_momentum": "12-month return beats the index",
    "turtle_s2": "new 55-day high, 50-day average above the 300-day, index up",
    "gem": "index 12-month return above 6.2% and the share beats it",
    "weekly_rotation": "in the week's top 10 by 200-day return",
    "fear_pullback": "fear dip in an uptrend: 3-day RSI under 30, ADX over 45, wide daily range",
}
EXIT = {
    "bollinger": "close fell under the 100-day average minus 1 deviation",
    "flipper": "close fell 20% from its highest high",
    "trendpilot": "close fell below the 100-day low, or the index fell under its 200-day average",
    "new_yearly_high": "close fell below the 250-day low",
    "nifty_mom30": "no longer in the top 30 at the half-year review",
    "hundred_day_high": "close fell below the 100-day low",
    "trend200": "close fell to the 200-day average",
    "golden_cross": "50-day average fell below the 200-day",
    "clenow_momentum": "close fell below the 100-day average",
    "dual_momentum": "12-month return fell to 6.2% or less",
    "absolute_momentum": "12-month return fell to 6.2% or less",
    "relative_momentum": "12-month return no longer beats the index",
    "turtle_s2": "close fell below the 20-day low",
    "gem": "index 12-month return fell to 6.2% or the share stopped beating it",
    "weekly_rotation": "no longer in the week's top 10",
}
HOLD = {"mean_reversion": 3, "fear_pullback": 4}          # sell at the next open after this many sessions held

# The simple funnel: overall view first, then deeper. Every strategy belongs to one of 4 pattern families.
FAMILIES = {
    "breakout": ("Breakout / new high", ["trend_breakout", "bollinger", "new_yearly_high", "hundred_day_high", "turtle_s2",
                                         "trendpilot", "clenow_tf", "longboard", "sepa", "flipper"]),
    "uptrend": ("Steady uptrend", ["trend200", "golden_cross", "clenow_momentum"]),
    "momentum": ("Momentum leader", ["relative_momentum", "absolute_momentum", "dual_momentum", "gem", "nifty_mom30",
                                     "weekly_rotation"]),
    "dip": ("Dip in an uptrend", ["mean_reversion", "fear_pullback"]),
}
FAMILY_OF = {k: fam for fam, (_, keys) in FAMILIES.items() for k in keys}


def family_label(rule: str) -> str:
    return FAMILIES[FAMILY_OF.get(rule, "breakout")][0]


# ---------------------------------------------------------------- lab indicators (books_lab/src/indicators.py)
def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _tr(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    return pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    return _tr(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0.0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-d).clip(lower=0.0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100.0 - 100.0 / (1.0 + gain / loss.replace(0.0, np.nan))
    return out.where(~(loss == 0), 100.0).fillna(50.0)


def _adx(df: pd.DataFrame, n: int) -> pd.Series:
    up, down = df["High"].diff(), -df["Low"].diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    ew = lambda s: s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()  # noqa: E731
    atr_ = ew(_tr(df)).replace(0.0, np.nan)
    pdi, mdi = 100.0 * ew(plus) / atr_, 100.0 * ew(minus) / atr_
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi).replace(0.0, np.nan)
    return ew(dx).fillna(0.0)


def _pmax(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n).max()


def _pmin(s: pd.Series, n: int) -> pd.Series:
    return s.shift(1).rolling(n).min()


def _adj_slope(c: pd.Series, w: int = 90) -> pd.Series:
    """Clenow momentum as in the lab: slope of log price divided by its standard error, 90 sessions."""
    y = np.log(c.astype(float))
    xs = np.arange(w, dtype=float) - (w - 1) / 2
    sxx = (xs ** 2).sum()
    yv = y.to_numpy()
    sxy = np.full(len(yv), np.nan)
    if len(yv) >= w:
        sxy[w - 1:] = np.correlate(yv, xs, mode="valid")
    sy, syy = y.rolling(w).sum().to_numpy(), (y ** 2).rolling(w).sum().to_numpy()
    slope = sxy / sxx
    se = np.sqrt(np.maximum((syy - sy ** 2 / w) - slope * sxy, 0.0) / (w - 2))
    with np.errstate(divide="ignore", invalid="ignore"):
        return pd.Series(slope / se, index=c.index)


def _flipper(c: np.ndarray, h: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """20% Flipper state machine: in at 1.2x the lowest close since the last exit, out at 0.8x the highest high."""
    n = len(c)
    sig, ex, sc = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n)
    state, low, hh = 0, c[0] if n else 0.0, 0.0
    for i in range(n):
        if state == 0:
            low = min(low, c[i])
            if c[i] >= 1.2 * low:
                sig[i], sc[i], state, hh = True, c[i] / low, 1, 0.0
        else:
            hh = max(hh, h[i])
            if c[i] <= 0.8 * hh:
                ex[i], state, low = True, 0, c[i]
    return sig, ex, sc


def _slope(c: pd.Series, w: int = 20) -> pd.Series:
    """Straight-line slope of the close over the last w days, in price per day."""
    xs = np.arange(w, dtype=float) - (w - 1) / 2
    yv = c.to_numpy(dtype=float)
    out = np.full(len(yv), np.nan)
    if len(yv) >= w:
        out[w - 1:] = np.correlate(yv, xs, mode="valid") / (xs ** 2).sum()
    return pd.Series(out, index=c.index)


def period_ends(days: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """Last session of each week, and last session of March and September. The last day looks at the next working day."""
    if not len(days):
        return np.zeros(0, bool), np.zeros(0, bool)
    nxt = pd.DatetimeIndex(list(days[1:]) + [days[-1] + pd.offsets.BDay(1)])
    a, b = days.isocalendar(), nxt.isocalendar()
    week = (a.year.to_numpy() * 100 + a.week.to_numpy()) != (b.year.to_numpy() * 100 + b.week.to_numpy())
    half = np.isin(days.month, (3, 9)) & (nxt.month != days.month)
    return week, half


class Combo:
    """Several rules at once. Same three steps as every strategy: prepare, candidates, manage and exit."""
    name = "combo"

    def __init__(self, params: dict, universe: dict):
        self.p, self.u = params, universe
        self.tb = TrendBreakout(params, universe)
        self.keys = [k for k, on in (params.get("use") or {}).items() if on and k in LABELS]
        self._index = None

    @property
    def warmup_bars(self) -> int:
        return max(self.tb.warmup_bars, 757)          # Longboard looks back 3 years (756 sessions)

    def bind_index(self, index: pd.DataFrame) -> None:
        c = index["Close"]
        above = c > _sma(c, 200)
        self._index = pd.DataFrame({"close": c, "ma200": _sma(c, 200), "above": above,
                                    "above5": above.astype(float).rolling(5).min() == 1.0,
                                    "ret126": c / c.shift(126) - 1.0, "ret252": c / c.shift(252) - 1.0})

    # Step 1: every rule's signal, stop, exit and score, computed once for the whole history.
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = self.tb.prepare(df)
        out["s_trend_breakout"] = out["signal"]
        n, ix = len(out), out.index
        c, h, l, v = out["Close"], out["High"], out["Low"], out["Volume"]
        pos = np.arange(n)
        warm = lambda k: pd.Series(pos >= k - 1, index=ix)  # noqa: E731
        if self._index is not None:
            I = self._index.reindex(ix, method="ffill")
            i_close, i_ma200, ir252 = I["close"], I["ma200"], I["ret252"]
            above, above5 = I["above"].fillna(False).astype(bool), I["above5"].fillna(False).astype(bool)
        else:
            nan = pd.Series(np.nan, index=ix)
            i_close, i_ma200, ir252 = nan, nan, nan
            above = above5 = pd.Series(False, index=ix)
        atr10, atr14, atr20, atr40 = _atr(out, 10), _atr(out, 14), _atr(out, 20), _atr(out, 40)
        s50, s100, s150, s200, s300 = (_sma(c, k) for k in (50, 100, 150, 200, 300))
        own126, own252 = c / c.shift(126) - 1.0, c / c.shift(252) - 1.0
        rsi2, rsi3, adx7 = _rsi(c, 2), _rsi(c, 3), _adx(out, 7)
        cols: dict[str, pd.Series] = {}

        def put(k, sig, stop, score, exit_=None):
            cols[f"s_{k}"] = pd.Series(sig, index=ix).fillna(False).astype(bool)
            cols[f"st_{k}"] = pd.Series(np.asarray(stop, float), index=ix)
            cols[f"sc_{k}"] = pd.Series(np.asarray(score, float), index=ix)
            cols[f"x_{k}"] = (pd.Series(False, index=ix) if exit_ is None
                              else pd.Series(exit_, index=ix).fillna(False).astype(bool))

        # SEPA
        stack = (c > s50) & (s50 > s150) & (s150 > s200)
        rec, pri = atr14.rolling(10).mean(), atr14.shift(10).rolling(10).mean()
        contr = ((pri - rec) / pri).where(pri != 0, 0.0)
        sig = (stack & (s50 > s50.shift(19)) & (own126 * 100.0 > 0) & (contr >= 0.05)
               & (c >= _pmax(h, 20) * (1 - 0.005)) & ((v / _sma(v, 50)) >= 1.5) & warm(200))
        put("sepa", sig, c * 0.93, (0.35 + 0.15 + 0.2 + 0.15 + 0.15 * (contr / 0.05) * 0.5).clip(upper=1))
        # Turtle system 2
        gate = (s50 > s300) & above & warm(305)
        put("turtle_s2", gate & (c > _pmax(h, 55)), c - 2 * atr20, pd.Series(0.9, index=ix), c < _pmin(l, 20))
        # Antonacci family
        exc = own252 * 100.0 - 6.2
        sig_a = (exc > 0) & warm(254)
        put("absolute_momentum", sig_a, c - 3 * atr14, (0.5 + exc / 40.0).clip(upper=1), ~sig_a)
        sig_r = (own252 > ir252) & warm(254)
        put("relative_momentum", sig_r, c - 3 * atr14, (0.5 + (own252 - ir252) * 2.0).clip(upper=1), ~sig_r)
        sig_g = (ir252 > 0.062) & (own252 > ir252) & warm(254)
        put("gem", sig_g, c - 3 * atr14, (0.7 + (own252 - ir252)).clip(upper=1), ~sig_g)
        put("dual_momentum", sig_a, c - 3 * atr14,
            (0.6 + 0.25 * (own252 > ir252) + 0.15 * (exc > 15)).clip(upper=1), ~sig_a)
        # Radge family
        lo250, lo100 = _pmin(l, 250), _pmin(l, 100)
        put("new_yearly_high", (c > _pmax(h, 250)) & warm(252), np.minimum(lo250, c - 3 * atr14),
            pd.Series(0.9, index=ix), c < lo250)
        conf = (s50 > s200).astype(float).rolling(5).min() == 1.0
        put("golden_cross", (s50 > s200) & conf & warm(205), np.minimum(s200, c - 3 * atr14),
            pd.Series(0.8, index=ix), s50 < s200)
        put("trendpilot", above5 & (c > _pmax(h, 100)) & warm(105),
            np.minimum(np.minimum(lo100, h.rolling(60).max() * 0.9), c - 3 * atr14),
            pd.Series(0.9, index=ix), (c < lo100) | ~above)
        put("hundred_day_high", (c > _pmax(h, 100)) & warm(102), np.minimum(lo100, c - 3 * atr14),
            pd.Series(0.85, index=ix), c < lo100)
        # Clenow
        put("clenow_tf", (s50 > s100) & above & (c > _pmax(h, 50)) & warm(105), c - 3 * atr20,
            pd.Series(0.85, index=ix))
        adj = _adj_slope(c, 90)
        put("clenow_momentum", above & (c > s100) & warm(100) & adj.notna(), c - 3 * atr20,
            (0.5 + adj.abs() / 10.0).clip(upper=1), c < s100)
        # Weekly rotation: eligibility here, the week's top 10 in cross_section
        roc200 = c / c.shift(200) - 1.0
        cols["wr_ok"] = ((i_close > i_ma200 * (1 - 0.02)) & (rsi3 < 50) & (roc200 > 0) & warm(400)).fillna(False)
        put("weekly_rotation", pd.Series(False, index=ix), c - 3 * atr14, roc200)
        # Mean reversion (long only): target +4%, out after 2 sessions
        sig = (c > s200) & warm(205) & (rsi2 <= 10) & (adx7 >= 50)
        put("mean_reversion", sig, c - 2.5 * atr10, ((10 - rsi2) / 10 + 0.5).clip(upper=1).clip(lower=0.5))
        cols["tr_mean_reversion"] = (c * 0.04) / (2.5 * atr10)
        # Trend 200
        put("trend200", (c > s200) & warm(200), np.minimum(s200, c - 3 * atr14), pd.Series(0.6, index=ix), c <= s200)
        # Momentum 30: risk-adjusted momentum here, the top 30 in cross_section
        mrs = []
        for lb in (126, 252):
            ret = (c / c.shift(lb) - 1.0) * 100.0
            sd = c.pct_change().rolling(lb).std() * math.sqrt(252) * 100.0
            mrs.append((ret / sd).where(sd > 0, 0.0).where(pd.Series(pos >= lb + 1, index=ix)))
        cols["nms"] = pd.concat(mrs, axis=1).mean(axis=1, skipna=True)
        put("nifty_mom30", pd.Series(False, index=ix), c - 2 * atr14, pd.Series(0.0, index=ix))
        # 20% Flipper
        f_sig, f_ex, f_sc = _flipper(c.to_numpy(float), h.to_numpy(float))
        put("flipper", f_sig & (pos >= 30), 0.8 * c, f_sc, f_ex)
        # Bollinger breakout
        m100, sd100 = _sma(c, 100), c.rolling(100).std(ddof=0)
        lo = m100 - sd100
        put("bollinger", (c > m100 + 3 * sd100) & warm(100), lo, (c - m100) / sd100, c < lo)
        # Longboard
        put("longboard", (c > c.shift(1).rolling(756, min_periods=1).max()) & warm(252), c - 10 * atr40, own126)
        # Fear pullback: limit 4% under the close, stop 2.5 ATR(10) under the fill
        sig = (c > s150) & (adx7 > 45) & (atr10 / c > 0.04) & (rsi3 < 30) & warm(155)
        put("fear_pullback", sig, 0.96 * c - 2.5 * atr10, 100 - rsi3)
        cols["fp_limit"], cols["fp_dist"] = 0.96 * c, 2.5 * atr10
        cols["atr20w"], cols["atr40w"] = atr20, atr40
        # funnel step 2, the quick overall check: above the 200-day average or rising over 3 months
        cols["f_quick"] = ((c > s200) | (out["roc"] > 0)).fillna(False)
        cols["slope20"] = _slope(c, 20)                    # for the held-share band (tbot/predict.py)
        out = pd.concat([out, pd.DataFrame(cols, index=ix)], axis=1)
        return self._signal(out)

    def _signal(self, out: pd.DataFrame) -> pd.DataFrame:
        """A share is flagged when any rule in use wants it. Bad data and too little trading block every rule."""
        suspect = out["suspect"].astype(bool) if "suspect" in out else pd.Series(False, index=out.index)
        ok = out["f_liquid"].astype(bool) & ~suspect
        if "f_quick" in out:
            ok &= out["f_quick"].astype(bool)               # only shares that pass the quick overall check
        any_ = pd.Series(False, index=out.index)
        for k in self.keys:
            any_ |= out[f"s_{k}"]
        out["signal"] = any_ & ok
        self._history(out, ok)
        return out

    def _history(self, out: pd.DataFrame, ok: pd.Series) -> None:
        """The chosen share's own past: every time its strategy said "buy" on this share in the last `years`
        (only days whose `days_after` result is already known, so nothing from the future), how often was the
        price higher `days_after` days later, and by how much on average. h_<rule> = it worked there before."""
        hc = self.p.get("history_check") or {}
        out["hist_n"] = out["hist_up"] = out["hist_avg"] = out["hist_rule"] = np.nan
        if not hc.get("enabled"):
            out["tested"] = out["signal"]
            return
        h, look, least = int(hc.get("days_after", 20)), int(float(hc.get("years", 3)) * 252), int(hc.get("min_signals", 3))
        close = out["Close"]
        known = close / close.shift(h) - 1.0                      # the result of a signal h days ago, known today
        tested = pd.Series(False, index=out.index)
        for i, k in enumerate(self.keys):
            sig = out[f"s_{k}"].astype(bool)
            x = known.where(sig.shift(h, fill_value=False))
            n = x.notna().astype(float).rolling(look - h, min_periods=1).sum()
            up = (x > 0).astype(float).rolling(look - h, min_periods=1).sum()
            avg = x.fillna(0.0).rolling(look - h, min_periods=1).sum() / n.where(n > 0)
            good = (n >= least) & (up >= 0.5 * n) & (avg > 0)
            out[f"h_{k}"] = good
            tested |= sig & good
            first = sig & out["hist_rule"].isna()               # the stats shown are those of the best matching strategy
            out.loc[first, "hist_n"], out.loc[first, "hist_up"] = n[first], up[first]
            out.loc[first, "hist_avg"], out.loc[first, "hist_rule"] = avg[first], float(i)
        out["tested"] = tested & ok

    def cross_section(self, prepared: dict[str, pd.DataFrame]) -> None:
        """Rules that rank all shares together: Weekly Rotation (top 10 each week), Momentum 30 (top 30 twice a year)."""
        if not prepared:
            return
        days = pd.DatetimeIndex(sorted(set().union(*[df.index for df in prepared.values()])))
        week, half = period_ends(days)
        key = pd.DataFrame({s: df["sc_weekly_rotation"].where(df["wr_ok"]) for s, df in prepared.items()}).reindex(days)
        nms = pd.DataFrame({s: df["nms"] for s, df in prepared.items()}).reindex(days)
        wr = pd.DataFrame(False, index=days, columns=key.columns)
        nm = pd.DataFrame(False, index=days, columns=key.columns)
        nz = pd.DataFrame(np.nan, index=days, columns=key.columns)
        for d in days[week]:
            row = key.loc[d].dropna()
            wr.loc[d, row.sort_values(ascending=False, kind="stable").index[:10]] = True
        for d in days[half]:
            row = nms.loc[d].dropna()
            if len(row) < 5 or row.std(ddof=0) <= 0:
                continue
            z = (row - row.mean()) / row.std(ddof=0)
            nz.loc[d, z.index] = z
            nm.loc[d, z.sort_values(ascending=False, kind="stable").index[:30]] = True
        wk, hf = pd.Series(week, index=days), pd.Series(half, index=days)
        for s, df in prepared.items():
            ix = df.index
            on_w, on_h = wk.reindex(ix).fillna(False).astype(bool), hf.reindex(ix).fillna(False).astype(bool)
            pick_w, pick_h = wr[s].reindex(ix).fillna(False).astype(bool), nm[s].reindex(ix).fillna(False).astype(bool)
            df["s_weekly_rotation"], df["x_weekly_rotation"] = on_w & pick_w, on_w & ~pick_w
            df["s_nifty_mom30"], df["x_nifty_mom30"] = on_h & pick_h, on_h & ~pick_h
            df["sc_nifty_mom30"] = nz[s].reindex(ix)
            self._signal(df)

    def matched(self, bar) -> list[str]:
        return [LABELS[k] for k in self.keys if getattr(bar, f"s_{k}", False)]

    def history(self, bar) -> dict | None:
        """The per-share history check of today's best matching strategy, for the page."""
        i = getattr(bar, "hist_rule", float("nan"))
        if i is None or math.isnan(i):
            return None
        k = self.keys[int(i)]
        n = getattr(bar, "hist_n", 0) or 0
        return {"rule": LABELS[k], "n": int(n), "up": int(getattr(bar, "hist_up", 0) or 0),
                "avg": None if math.isnan(getattr(bar, "hist_avg", float("nan"))) else float(bar.hist_avg),
                "ok": bool(getattr(bar, f"h_{k}", False))}

    def families(self, bar) -> list[str]:
        """Pattern families this share shows today, best strategy first."""
        out = []
        for k in self.keys:
            if getattr(bar, f"s_{k}", False) and family_label(k) not in out:
                out.append(family_label(k))
        return out

    # Step 2: one trade idea per rule that wants this share, in priority order.
    def candidates(self, symbol: str, bar) -> list[Candidate]:
        return [c for c in (self._one(i, k, symbol, bar) for i, k in enumerate(self.keys)
                            if getattr(bar, f"s_{k}", False) and getattr(bar, f"h_{k}", True)) if c is not None]

    def candidate(self, symbol: str, bar) -> Candidate | None:
        found = self.candidates(symbol, bar)
        return found[0] if found else None

    def _one(self, i: int, k: str, symbol: str, bar) -> Candidate | None:
        roc = float(bar.roc) if not math.isnan(bar.roc) else 0.0          # 3-month momentum: the bad-market check uses it
        if k == "trend_breakout":
            c = self.tb.candidate(symbol, bar)
            c.reason = f"{LABELS[k]}. {c.reason}"
            c.strategy, c.order = k, (i, -c.score)
            return c
        stop, rule_score = float(getattr(bar, f"st_{k}")), float(getattr(bar, f"sc_{k}"))
        price, limit, dist = float(bar.Close), None, None
        if k == "fear_pullback":
            limit, dist = float(bar.fp_limit), float(bar.fp_dist)
            price, stop = limit, limit - dist
        if any(math.isnan(x) for x in (stop, price)) or stop >= price:
            return None
        tp = float(self.p.get("take_profit_r") or 2.0)
        if k in TWO_R:
            target_r, gate_r, lift, aim = 2.0, 2.0, True, "target 2R"
        elif k == "mean_reversion":
            target_r = gate_r = float(bar.tr_mean_reversion)
            lift, aim = True, f"target +4% ({target_r:.1f}R), out after 2 sessions"
        elif k == "fear_pullback":
            target_r, gate_r, lift = 0.0, 0.03 * price / dist, False
            aim = f"limit {limit:.2f}, sell at +3% ({gate_r:.1f}R) or after 4 sessions"
        else:
            target_r, gate_r, lift, aim = 0.0, tp, False, f"no fixed target, the gate counts {tp:g}R"
        reason = f"{LABELS[k]}: {WHY[k]}. Stop {stop:.2f}, {aim}."
        return Candidate(symbol, price, stop, float(bar.atr), roc, reason, strategy=k, target_r=target_r,
                         gate_r=gate_r, lift=lift, limit=limit, stop_dist=dist,
                         order=(i, -(rule_score if not math.isnan(rule_score) else 0.0)))

    # Step 3: after the close, breakeven at +1R for every rule, then the rule's own trailing stop.
    def manage(self, pos, bar) -> list[str]:
        k = getattr(pos, "strategy", "") or "trend_breakout"
        pos.bars_held = getattr(pos, "bars_held", 0) + 1
        pos.highest_high = max(getattr(pos, "highest_high", 0.0) or 0.0, float(bar.High))
        if k == "trend_breakout":
            return self.tb.manage(pos, bar)
        notes = []
        pos.last_close = float(bar.Close)
        pos.highest_close = max(pos.highest_close, float(bar.Close))
        if not pos.breakeven and bar.High >= pos.entry_price + pos.risk_per_share:
            pos.breakeven = True
            if pos.entry_price > pos.stop:
                pos.stop = pos.entry_price
                notes.append(f"{pos.symbol}: reached +1R, stop moved to breakeven {pos.stop:.2f}")
        trail = float("nan")
        if k == "clenow_tf" and pos.breakeven:
            trail = pos.highest_close - 3 * bar.atr20w
        elif k == "longboard":
            trail = pos.highest_high - 10 * bar.atr40w
        if not math.isnan(trail) and trail > pos.stop:
            pos.stop = float(trail)
        return notes

    def exit_reason(self, pos, bar) -> str | None:
        k = getattr(pos, "strategy", "") or "trend_breakout"
        if k == "trend_breakout" or k not in LABELS:
            return self.tb.exit_reason(pos, bar)
        if getattr(bar, f"x_{k}", False):
            return f"{LABELS[k]}: {EXIT[k]}"
        if k == "fear_pullback" and bar.Close >= pos.entry_price * 1.03:
            return f"{LABELS[k]}: close {bar.Close:.2f} is 3% above the buy price"
        if k in HOLD and getattr(pos, "bars_held", 0) >= HOLD[k]:
            return f"{LABELS[k]}: held {HOLD[k] - 1} sessions, time to sell"
        return None
