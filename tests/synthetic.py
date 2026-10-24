"""A small synthetic market with hand-built setups, for offline tests.

Every series ends on Friday 2026-10-23. Prices are built from segments of
(days, total_return, noise); candles and volumes are then shaped so each named
stock shows exactly one textbook pattern.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

END = "2026-10-23"


def build_closes(segments, start: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    closes = [start]
    for days, total, noise in segments:
        step = (1 + total) ** (1 / days) - 1
        for _ in range(days):
            closes.append(closes[-1] * (1 + step + (rng.normal(0, noise) if noise else 0.0)))
    return np.array(closes[1:])


def frame(closes, volumes, seed: int, wick: float = 0.004, end: str = END) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 7)
    n = len(closes)
    opens = np.empty(n)
    opens[0] = closes[0]
    opens[1:] = closes[:-1] * (1 + rng.normal(0, 0.0015, n - 1))
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, wick, n)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, wick, n)))
    idx = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": np.asarray(volumes, dtype=float)},
        index=idx,
    )


def volumes(n: int, base: float, seed: int, shape=None) -> np.ndarray:
    rng = np.random.default_rng(seed + 3)
    v = base * (1 + rng.normal(0, 0.12, n)).clip(0.5, None)
    if shape:
        for start, end, mult in shape:  # negative indices from the end
            v[start:end if end else None] *= mult
    return v


def set_bar(df: pd.DataFrame, i: int, o: float, h: float, l: float, c: float, v: float | None = None) -> None:
    df.iloc[i, df.columns.get_loc("Open")] = o
    df.iloc[i, df.columns.get_loc("High")] = h
    df.iloc[i, df.columns.get_loc("Low")] = l
    df.iloc[i, df.columns.get_loc("Close")] = c
    if v is not None:
        df.iloc[i, df.columns.get_loc("Volume")] = v


def clean_tail(df: pd.DataFrame, bars: int, wick: float = 0.003) -> None:
    """Rebuild the last `bars` candles from closes with small, regular wicks."""
    n = len(df)
    for i in range(n - bars, n):
        prev_c = float(df["Close"].iloc[i - 1])
        c = float(df["Close"].iloc[i])
        o = prev_c * (1.0005 if c >= prev_c else 0.9995)
        set_bar(df, i, o, max(o, c) * (1 + wick), min(o, c) * (1 - wick), c)


def hammer_bar(df: pd.DataFrame, i: int = -1) -> None:
    prev_c = float(df["Close"].iloc[i - 1])
    o = prev_c * 0.995
    low = prev_c * 0.975
    c = prev_c * 1.002
    h = c * 1.001
    set_bar(df, i, o, h, low, c)


def stock_a_pullback() -> pd.DataFrame:
    segs = [(400, 0.60, 0.008), (40, 0.06, 0.005), (12, 0.08, 0.0), (5, -0.065, 0.0), (1, 0.002, 0.0)]
    c = build_closes(segs, 400.0, seed=11)
    n = len(c)
    v = volumes(n, 300_000, 11, shape=[(-18, -6, 1.3), (-6, -1, 0.6)])
    for k, mult in enumerate((0.75, 0.68, 0.62, 0.56, 0.5)):
        v[n - 6 + k] = 300_000 * mult
    v[-1] = 300_000 * 0.8
    df = frame(c, v, seed=11)
    clean_tail(df, 18)
    hammer_bar(df)
    return df


def stock_b_base() -> pd.DataFrame:
    base = [(6, -0.12, 0.0), (7, 0.11, 0.0), (5, -0.07, 0.0), (5, 0.06, 0.0), (4, -0.035, 0.0), (3, 0.02, 0.0)]
    segs = [(350, 0.25, 0.008), (80, 0.55, 0.004)] + base
    c = build_closes(segs, 200.0, seed=22)
    n = len(c)
    v = volumes(n, 500_000, 22, shape=[(-30, -24, 1.2), (-24, -12, 0.9), (-12, -5, 0.7), (-5, None, 0.45)])
    df = frame(c, v, seed=22)
    clean_tail(df, 32)
    return df


def stock_c_bounce() -> pd.DataFrame:
    segs = [(450, 0.50, 0.007), (30, 0.08, 0.003), (4, -0.10, 0.0), (1, 0.002, 0.0)]
    c = build_closes(segs, 1000.0, seed=33)
    n = len(c)
    v = volumes(n, 400_000, 33, shape=[(-5, -1, 1.4)])
    v[-1] = 400_000 * 1.1
    df = frame(c, v, seed=33)
    clean_tail(df, 6)
    hammer_bar(df)
    return df


def stock_downtrend() -> pd.DataFrame:
    c = build_closes([(470, -0.40, 0.008)], 800.0, seed=44)
    return frame(c, volumes(len(c), 400_000, 44), seed=44)


def stock_crash() -> pd.DataFrame:
    c = build_closes([(440, 0.45, 0.007), (25, 0.02, 0.004), (1, -0.11, 0.0), (3, -0.01, 0.0)], 600.0, seed=55)
    n = len(c)
    v = volumes(n, 600_000, 55, shape=[(-4, None, 2.2)])
    df = frame(c, v, seed=55)
    clean_tail(df, 4)
    return df


def stock_illiquid() -> pd.DataFrame:
    c = build_closes([(460, 0.5, 0.01)], 20.0, seed=66)
    return frame(c, volumes(len(c), 20_000, 66), seed=66)


def random_stock(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = rng.uniform(-0.2, 0.5)
    c = build_closes([(480, drift, 0.012)], float(rng.uniform(100, 2500)), seed=seed)
    return frame(c, volumes(len(c), float(rng.uniform(1e5, 2e6)), seed), seed=seed)


def nifty(trend: float = 0.30) -> pd.DataFrame:
    """Uptrend by default; a negative `trend` ends the index below its 200 DMA."""
    last = 0.02 if trend > 0 else -0.06
    c = build_closes([(470, trend, 0.002), (30, last, 0.001)], 19000.0, seed=1)
    return frame(c, np.full(len(c), 1.0), seed=1, wick=0.002)


def vix(level: float = 13.0) -> pd.DataFrame:
    c = np.full(260, level)
    return frame(c, np.zeros(260), seed=2, wick=0.01)


def hourly_from_daily(df: pd.DataFrame, days: int = 60, seed: int = 5, last_turn_up: bool = True) -> pd.DataFrame:
    """Seven bars a day (9:15 ... 15:15) walking from each day's open to its close."""
    rng = np.random.default_rng(seed)
    rows, idx = [], []
    for ts, r in df.iloc[-days:].iterrows():
        path = np.linspace(r["Open"], r["Close"], 8)
        for k in range(7):
            o, c = path[k], path[k + 1]
            jitter = abs(rng.normal(0, 0.0015))
            rows.append((o, max(o, c) * (1 + jitter), min(o, c) * (1 - jitter), c, r["Volume"] / 7))
            idx.append(ts + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(hours=k))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex(idx))


SECTORS = ["Information Technology", "Capital Goods", "Financial Services", "Healthcare", "Automobile and Auto Components", "FMCG"]


def market(nifty_trend: float = 0.30, vix_level: float = 13.0):
    """(universe_rows, caps, daily, hourly) for the pipeline test."""
    named = {
        "APULL": (stock_a_pullback(), "Information Technology", "Large"),
        "BBASE": (stock_b_base(), "Capital Goods", "Mid"),
        "COVER": (stock_c_bounce(), "Financial Services", "Large"),
        "DDOWN": (stock_downtrend(), "Healthcare", "Mid"),
        "ECRASH": (stock_crash(), "Automobile and Auto Components", "Large"),
        "FILLIQ": (stock_illiquid(), "FMCG", None),
    }
    rows, caps, daily, hourly = [], {}, {}, {}
    for sym, (df, sector, cap) in named.items():
        rows.append({"symbol": sym, "name": f"{sym.title()} Ltd", "industry": sector, "isin": ""})
        if cap:
            caps[sym] = cap
        daily[sym + ".NS"] = df
        hourly[sym + ".NS"] = hourly_from_daily(df, seed=len(sym))
    for k in range(16):
        sym = f"RND{k:02d}"
        rows.append({"symbol": sym, "name": f"Random {k}", "industry": SECTORS[k % len(SECTORS)], "isin": ""})
        caps[sym] = ("Large", "Mid", "Small")[k % 3]
        daily[sym + ".NS"] = random_stock(100 + k)
    daily["^NSEI"] = nifty(nifty_trend)
    daily["^INDIAVIX"] = vix(vix_level)
    return rows, caps, daily, hourly
