"""Price indicators used by the scanner.

Every function takes pandas objects indexed by date (daily) or timestamp
(hourly) with columns Open, High, Low, Close, Volume. Each value uses bars up
to and including its own bar, never later ones.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

OHLCV = ["Open", "High", "Low", "Close", "Volume"]

_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(series: pd.Series, n: int) -> pd.Series:
    """Wilder's moving average, the smoothing behind RSI and ATR."""
    return series.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = rma(delta.clip(lower=0.0), n)
    loss = rma((-delta).clip(lower=0.0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + gain / loss)
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((loss == 0) & (gain == 0), 50.0)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    parts = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1
    )
    return parts.max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    try:
        out = df[OHLCV].resample(rule).agg(_AGG)
    except ValueError:
        if rule != "ME":
            raise
        out = df[OHLCV].resample("M").agg(_AGG)  # pandas < 2.2
    return out.dropna(subset=["Close"])


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    return resample_ohlcv(df, "W-FRI")


def to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    return resample_ohlcv(df, "ME")


def ret(close: pd.Series, n: int) -> float:
    """Return over the last n bars, as a fraction (0.05 = +5%)."""
    if len(close) <= n:
        return math.nan
    a = float(close.iloc[-1 - n])
    b = float(close.iloc[-1])
    return b / a - 1.0 if a else math.nan


def slope(values) -> float:
    y = np.asarray(values, dtype=float)
    if len(y) < 2 or np.isnan(y).any():
        return math.nan
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def zigzag(high, low, pct: float) -> list[tuple[int, float, str]]:
    """Swing points that are at least `pct` apart.

    Returns [(index, price, "H" or "L"), ...]. The last point is the current,
    still unconfirmed extreme.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    n = len(high)
    if n < 2:
        return []
    pivots: list[tuple[int, float, str]] = []
    up_i, up_v = 0, high[0]
    dn_i, dn_v = 0, low[0]
    direction = 0  # 0 undecided, 1 tracking a high, -1 tracking a low
    for i in range(1, n):
        h, l = high[i], low[i]
        if direction == 0:
            if h > up_v:
                up_i, up_v = i, h
            if l < dn_v:
                dn_i, dn_v = i, l
            if dn_v <= up_v * (1 - pct) and dn_i > up_i:
                pivots.append((up_i, up_v, "H"))
                direction = -1
            elif up_v >= dn_v * (1 + pct) and up_i > dn_i:
                pivots.append((dn_i, dn_v, "L"))
                direction = 1
        elif direction == 1:
            if h > up_v:
                up_i, up_v = i, h
            elif l <= up_v * (1 - pct):
                pivots.append((up_i, up_v, "H"))
                direction = -1
                dn_i, dn_v = i, l
        else:
            if l < dn_v:
                dn_i, dn_v = i, l
            elif h >= dn_v * (1 + pct):
                pivots.append((dn_i, dn_v, "L"))
                direction = 1
                up_i, up_v = i, h
    if direction == 1:
        pivots.append((up_i, up_v, "H"))
    elif direction == -1:
        pivots.append((dn_i, dn_v, "L"))
    return pivots


def pullback_legs(pivots: list[tuple[int, float, str]]) -> list[float]:
    """Depth of each high-to-low leg, as a fraction of the high."""
    legs = []
    for a, b in zip(pivots, pivots[1:]):
        if a[2] == "H" and b[2] == "L" and a[1] > 0:
            legs.append((a[1] - b[1]) / a[1])
    return legs


def daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"]
    v = df["Volume"]
    ind = pd.DataFrame(index=df.index)
    ind["ema10"] = ema(c, 10)
    ind["ema20"] = ema(c, 20)
    ind["sma50"] = sma(c, 50)
    ind["sma200"] = sma(c, 200)
    ind["rsi14"] = rsi(c, 14)
    ind["rsi2"] = rsi(c, 2)
    ind["atr14"] = atr(df, 14)
    ind["vol20"] = sma(v, 20)
    ind["vol50"] = sma(v, 50)
    ind["tv20"] = sma(c * v, 20)
    return ind
