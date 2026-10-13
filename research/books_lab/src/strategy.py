"""Strategy v1 — trend + momentum + volume + regime, cash equity swing.

BOT_INSTRUCTIONS §3. The signal function is PURE: same input -> same output,
no I/O, no clock, no randomness. That is what lets the identical function run
in backtest, paper and live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from . import indicators as ind


# --------------------------------------------------------------------------
#  Data containers
# --------------------------------------------------------------------------
@dataclass
class Regime:
    """Market-level regime. Computed once per bar, shared by every symbol."""

    date: datetime
    index_close: float
    index_ma200: float
    above_ma200: bool
    fear_gauge: float | None
    fear_ok: bool
    reason: str


@dataclass
class Signal:
    """A trading intent. `reason` is a first-class field because
    BOT_INSTRUCTIONS §3 requires every trade to store its reason."""

    symbol: str
    market: str
    date: datetime
    side: str                      # long | short (short is unused in v1)
    entry_price: float
    stop_price: float
    target_price: float
    risk_per_share: float
    reward_per_share: float
    reward_risk: float
    atr: float
    reason: dict = field(default_factory=dict)

    def reason_text(self) -> str:
        """Human-readable one-liner for the website."""
        r = self.reason
        return (
            f"{self.symbol} {self.side} @ {self.entry_price:.2f} | "
            f"regime: {r.get('regime', '')} | "
            f"trend: close>{r.get('trend_ma_fast', 0):.0f}>{r.get('trend_ma_slow', 0):.0f} | "
            f"RSI {r.get('rsi', 0):.1f} | "
            f"MACD>{r.get('macd_signal', 0):.2f} | "
            f"vol {r.get('volume_ratio', 0):.2f}x | "
            f"stop {self.stop_price:.2f} | target {self.target_price:.2f} | "
            f"R:R {self.reward_risk:.2f}"
        )


@dataclass
class Rejection:
    """Why a symbol did NOT produce a signal. Logged for the website."""

    symbol: str
    date: datetime
    failed_rule: str
    detail: str


# --------------------------------------------------------------------------
#  Feature building
# --------------------------------------------------------------------------
def build_features(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add every indicator column. Pure. Never drops the original OHLCV."""
    s = cfg["strategy"]
    out = df.copy()

    out["sma_fast"] = ind.sma(out["close"], s["trend"]["ma_fast"])
    out["sma_slow"] = ind.sma(out["close"], s["trend"]["ma_slow"])
    out["rsi"] = ind.rsi(out["close"], s["momentum"]["rsi_period"])
    macd_line, sig_line, hist = ind.macd(
        out["close"],
        s["momentum"]["macd_fast"],
        s["momentum"]["macd_slow"],
        s["momentum"]["macd_signal"],
    )
    out["macd"] = macd_line
    out["macd_signal"] = sig_line
    out["macd_hist"] = hist
    out["atr"] = ind.atr(out, s["stop"]["atr_period"])
    out["adx"] = ind.adx(out)
    out["vol_ratio"] = ind.volume_ratio(out["volume"], s["volume"]["ma_period"])
    out["vol_sma"] = ind.sma(out["volume"], s["volume"]["ma_period"])
    out["obv"] = ind.obv(out)
    out["obv_sma"] = ind.sma(out["obv"], 20)
    return out


def compute_regime(index_df: pd.DataFrame, fear_df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Attach regime columns to the index frame. Pure."""
    s = cfg["strategy"]
    period = s["regime"]["index_ma_period"]
    out = index_df.copy()
    out["ma200"] = ind.sma(out["close"], period)
    out["above_ma200"] = out["close"] > out["ma200"]

    fear_max = None
    if not fear_df.empty and "close" in fear_df.columns:
        joined = out[["close"]].join(fear_df[["close"]].rename(columns={"close": "fear"}), how="left")
        out["fear"] = joined["fear"].ffill()
    else:
        out["fear"] = pd.NA
    return out


# --------------------------------------------------------------------------
#  The four required conditions
# --------------------------------------------------------------------------
def _check_trend(row: pd.Series, cfg: dict) -> tuple[bool, dict]:
    s = cfg["strategy"]["trend"]
    if pd.isna(row["sma_fast"]) or pd.isna(row["sma_slow"]):
        return False, {"why": "indicators not warmed up"}
    fast, slow, close = row["sma_fast"], row["sma_slow"], row["close"]
    if s.get("require_stack", True):
        ok = close > fast > slow
        detail = {"trend_close": close, "trend_ma_fast": fast, "trend_ma_slow": slow}
    else:
        ok = close > slow
        detail = {"trend_close": close, "trend_ma_fast": fast, "trend_ma_slow": slow}
    return ok, detail


def _check_momentum(row: pd.Series, cfg: dict) -> tuple[bool, dict]:
    m = cfg["strategy"]["momentum"]
    if pd.isna(row["rsi"]) or pd.isna(row["macd"]) or pd.isna(row["macd_signal"]):
        return False, {"why": "momentum indicators not warmed up"}
    rsi_ok = m["rsi_min"] <= row["rsi"] <= m["rsi_max"]
    macd_ok = (row["macd"] > row["macd_signal"]) if m.get("require_macd_above", True) else True
    ok = rsi_ok and macd_ok
    detail = {
        "rsi": float(row["rsi"]),
        "rsi_ok": bool(rsi_ok),
        "macd": float(row["macd"]),
        "macd_signal": float(row["macd_signal"]),
        "macd_ok": bool(macd_ok),
    }
    return ok, detail


def _check_volume(row: pd.Series, cfg: dict) -> tuple[bool, dict]:
    v = cfg["strategy"]["volume"]
    if pd.isna(row["vol_ratio"]):
        return False, {"why": "volume indicator not warmed up"}
    ok = row["vol_ratio"] >= v["multiplier"]
    detail = {
        "volume": float(row["volume"]),
        "volume_sma": float(row["vol_sma"]),
        "volume_ratio": float(row["vol_ratio"]),
        "volume_required": float(v["multiplier"]),
    }
    return ok, detail


def _check_regime(regime_row: pd.Series | None, cfg: dict) -> tuple[bool, dict]:
    s = cfg["strategy"]
    if not s["regime"]["enabled"]:
        return True, {"regime": "disabled"}
    if regime_row is None or pd.isna(regime_row.get("ma200")):
        return False, {"why": "regime not computable"}
    above = bool(regime_row["above_ma200"])
    fear_ok = True
    fear_val = None
    if regime_row.get("fear") is not None and not pd.isna(regime_row.get("fear")):
        fear_val = float(regime_row["fear"])
    detail = {
        "regime": f"index {regime_row['close']:.1f} vs MA200 {regime_row['ma200']:.1f}",
        "regime_above_ma200": above,
        "fear_gauge": fear_val,
    }
    return above, detail


# --------------------------------------------------------------------------
#  Cost-aware target
# --------------------------------------------------------------------------
def solve_gross_reward_risk(
    *,
    entry_price: float,
    stop_price: float,
    cost_pct: float,
    min_reward_risk: float,
    max_gross_rr: float = 4.0,
) -> tuple[float, str]:
    """Find the price target that nets `min_reward_risk` AFTER costs.

    This is the most important piece of arithmetic in the whole system.

    Define R = the money lost if the stop is hit, and C = the round-trip cost.
    For a target at g x the stop distance:

        net R:R = (g*R - C) / (R + C)

    Solving for g when the net ratio must reach m:

        g = m + (m + 1) * C/R

    The C/R term is the killer. With India's 0.29% round-trip cost and a stop
    that sits ~3% below entry, C/R is roughly 0.10, so:

        g = 2 + 3 x 0.10 = 2.30

    A 2:1 *price* target nets only about 1.8:1 in rupees. BOT_INSTRUCTIONS
    asks for 2:1 AFTER costs, so the target has to be wider than 2:1 in price.
    Any system that does not do this is quietly trading at ~1.8:1 while
    believing it is trading at 2:1.
    """
    stop_dist = entry_price - stop_price
    if stop_dist <= 0 or entry_price <= 0:
        return 0.0, "stop distance is not positive"

    # C/R = (cost_pct% of notional) / (qty x stop_dist)
    #     = (cost_pct/100 x qty x E) / (qty x stop_dist)
    #     = (cost_pct/100) x E / stop_dist
    cost_ratio = (cost_pct / 100.0) * entry_price / stop_dist
    g = min_reward_risk + (min_reward_risk + 1.0) * cost_ratio

    if g > max_gross_rr:
        return 0.0, (
            f"costs force a {g:.2f}:1 gross target for a "
            f"{min_reward_risk}:1 net edge; that exceeds the {max_gross_rr}:1 cap"
        )
    return g, ""


# --------------------------------------------------------------------------
#  The signal function  (the only entry point)
# --------------------------------------------------------------------------
def signal(
    features: pd.DataFrame,
    regime_features: pd.DataFrame,
    symbol: str,
    market: str,
    cfg: dict,
    as_of: pd.Timestamp,
    execution_price: float,
    cost_pct: float = 0.29,
) -> Signal | Rejection:
    """Evaluate one symbol at one bar.

    `as_of`           - the bar the signal is computed on (closed bar)
    `execution_price` - the NEXT bar's open. Entry happens there, never here.
    `cost_pct`        - round-trip cost, used to set a target that nets 2:1.
    """
    s = cfg["strategy"]

    hist = features.loc[:as_of]
    if hist.empty:
        return Rejection(symbol, as_of, "no_data", "no bars at or before as_of")

    row = hist.iloc[-1]
    bar_date = hist.index[-1]

    regime_row = None
    if regime_features is not None and not regime_features.empty:
        rh = regime_features.loc[:as_of]
        if not rh.empty:
            regime_row = rh.iloc[-1]

    checks = {
        "trend": _check_trend(row, cfg),
        "momentum": _check_momentum(row, cfg),
        "volume": _check_volume(row, cfg),
        "regime": _check_regime(regime_row, cfg),
    }

    reason: dict = {}
    for name, (ok, detail) in checks.items():
        reason.update(detail)
        if not ok:
            return Rejection(
                symbol=symbol,
                date=bar_date,
                failed_rule=name,
                detail=str(detail.get("why", detail)),
            )

    # --- risk levels --------------------------------------------------
    atr_val = float(row["atr"])
    if pd.isna(atr_val) or atr_val <= 0:
        return Rejection(symbol, bar_date, "atr", "ATR is not usable")

    stop_dist = s["stop"]["atr_multiplier"] * atr_val
    stop_price = execution_price - stop_dist

    # --- cost-aware target ---------------------------------------------
    gross_rr, err = solve_gross_reward_risk(
        entry_price=execution_price,
        stop_price=stop_price,
        cost_pct=cost_pct,
        min_reward_risk=float(s["target"]["reward_risk"]),
        max_gross_rr=float(s["target"].get("max_gross_rr", 4.0)),
    )
    if gross_rr <= 0:
        return Rejection(symbol, bar_date, "cost_target", err)

    target_price = execution_price + gross_rr * stop_dist
    reason["gross_reward_risk"] = gross_rr
    reason["cost_ratio_c_over_r"] = (cost_pct / 100.0) * execution_price / stop_dist

    return Signal(
        symbol=symbol,
        market=market,
        date=bar_date,
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
