"""Price data providers.

YahooProvider reads Yahoo Finance through the yfinance package (NSE symbols
end in .NS). MemoryProvider serves frames held in memory, for tests and
replays. Both return {ticker: DataFrame} with columns Open, High, Low, Close,
Volume. Daily frames are indexed by date, hourly frames by India-time
timestamps (no time zone attached).
"""

from __future__ import annotations

import math
import time as _time
from datetime import date

import pandas as pd

from .indicators import OHLCV
from .market import IST


def clean_frame(df: pd.DataFrame | None, intraday: bool = False) -> pd.DataFrame | None:
    if df is None or len(df) == 0:
        return None
    if not all(col in df.columns for col in OHLCV):
        return None
    out = df[OHLCV].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[out["Close"] > 0]
    if out.empty:
        return None
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert(IST).tz_localize(None)
    if not intraday:
        idx = idx.normalize()
    out = out.copy()
    out.index = idx
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["Volume"] = out["Volume"].fillna(0.0).astype(float)
    return out if not out.empty else None


def split_download(df: pd.DataFrame | None, tickers: list[str], intraday: bool = False) -> dict:
    """Split one yfinance.download() result into a frame per ticker.

    Handles both column layouts yfinance uses: (ticker, field) when
    group_by="ticker", and (field, ticker) otherwise.
    """
    out: dict[str, pd.DataFrame] = {}
    if df is None or len(df) == 0:
        return out
    if isinstance(df.columns, pd.MultiIndex):
        level0 = set(df.columns.get_level_values(0))
        level1 = set(df.columns.get_level_values(1))
        for t in tickers:
            if t in level0:
                sub = df[t]
            elif t in level1:
                sub = df.xs(t, axis=1, level=1)
            else:
                continue
            frame = clean_frame(sub, intraday)
            if frame is not None:
                out[t] = frame
    elif len(tickers) == 1:
        frame = clean_frame(df, intraday)
        if frame is not None:
            out[tickers[0]] = frame
    return out


class YahooProvider:
    name = "Yahoo Finance"

    def __init__(self, chunk: int = 100, pause: float = 1.5, log=print):
        self.chunk = chunk
        self.pause = pause
        self.log = log

    def _download(self, tickers: list[str], period: str, interval: str, intraday: bool) -> dict:
        import yfinance as yf

        df = yf.download(
            tickers=tickers,
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=False,
            actions=False,
            threads=True,
            progress=False,
            timeout=30,
        )
        return split_download(df, tickers, intraday)

    def _batch(self, tickers: list[str], period: str, interval: str, intraday: bool) -> dict:
        result: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), self.chunk):
            part = tickers[i : i + self.chunk]
            for attempt in range(3):
                try:
                    result.update(self._download(part, period, interval, intraday))
                    break
                except Exception as exc:
                    self.log(f"  Yahoo batch {i // self.chunk + 1} failed ({exc.__class__.__name__}); retrying")
                    _time.sleep(5 * (attempt + 1))
            _time.sleep(self.pause)
        missing = [t for t in tickers if t not in result]
        if missing:
            self.log(f"  retrying {len(missing)} tickers slowly")
            for i in range(0, len(missing), 10):
                part = missing[i : i + 10]
                try:
                    result.update(self._download(part, period, interval, intraday))
                except Exception as exc:
                    self.log(f"  slow retry failed ({exc.__class__.__name__})")
                _time.sleep(self.pause * 2)
        return result

    def daily(self, tickers: list[str], period: str = "5y") -> dict:
        return self._batch(tickers, period, "1d", intraday=False)

    def hourly(self, tickers: list[str], period: str = "60d") -> dict:
        return self._batch(tickers, period, "60m", intraday=True)

    def index_daily(self, ticker: str, period: str = "5y") -> pd.DataFrame | None:
        return self.daily([ticker], period).get(ticker)

    def earnings_dates(self, tickers: list[str]) -> dict[str, list[date]]:
        import yfinance as yf

        out: dict[str, list[date]] = {}
        for t in tickers:
            try:
                cal = yf.Ticker(t).calendar
            except Exception:
                continue
            raw = None
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date")
            elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                raw = list(cal.loc["Earnings Date"].values)
            if raw is None:
                continue
            if not isinstance(raw, (list, tuple)):
                raw = [raw]
            dates = []
            for d in raw:
                try:
                    dates.append(pd.Timestamp(d).date())
                except Exception:
                    continue
            if dates:
                out[t] = sorted(set(dates))
            _time.sleep(0.3)
        return out


class MemoryProvider:
    """Serves prepared frames: {ticker: DataFrame}."""

    name = "memory"

    def __init__(self, daily: dict, hourly: dict | None = None, earnings: dict | None = None):
        self._daily = daily
        self._hourly = hourly or {}
        self._earnings = earnings or {}

    def daily(self, tickers, period="5y"):
        return {t: self._daily[t] for t in tickers if t in self._daily}

    def hourly(self, tickers, period="60d"):
        return {t: self._hourly[t] for t in tickers if t in self._hourly}

    def index_daily(self, ticker, period="5y"):
        return self._daily.get(ticker)

    def earnings_dates(self, tickers):
        return {t: self._earnings[t] for t in tickers if t in self._earnings}


def drop_unfinished(df: pd.DataFrame, now_naive_ist: pd.Timestamp, intraday: bool) -> pd.DataFrame:
    """Remove bars that had not finished at `now` (India time, no zone)."""
    if df is None or df.empty:
        return df
    if intraday:
        # an hourly bar starting at t ends at t + 60 minutes (the last one at 15:30)
        ends = df.index + pd.Timedelta(minutes=60)
        session_close = df.index.normalize() + pd.Timedelta(hours=15, minutes=30)
        ends = ends.where(ends <= session_close, session_close)
        return df[ends <= now_naive_ist]
    today = now_naive_ist.normalize()
    final_after = today + pd.Timedelta(hours=16)
    if df.index[-1] >= today and now_naive_ist < final_after:
        return df[df.index < today]
    return df


def nan_to_none(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value
