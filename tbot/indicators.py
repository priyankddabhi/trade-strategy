"""Indicators on daily bars. A value on day t only uses data up to and including day t."""
from __future__ import annotations

import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def prior_high(s: pd.Series, n: int) -> pd.Series:
    """Highest value of the n days BEFORE today. Today is excluded, so a breakout can be detected."""
    return s.shift(1).rolling(n, min_periods=n).max()


def prior_mean(s: pd.Series, n: int) -> pd.Series:
    """Average of the n days BEFORE today."""
    return s.shift(1).rolling(n, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def roc(s: pd.Series, n: int) -> pd.Series:
    return s / s.shift(n) - 1
