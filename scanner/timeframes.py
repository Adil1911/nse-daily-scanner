"""Monthly, weekly and hourly views of a stock (the daily view is the strategy check)."""

from __future__ import annotations

import math

import pandas as pd

from .indicators import ema, rsi, sma, to_monthly, to_weekly

UP, DOWN, SIDEWAYS, NA = "UP", "DOWN", "SIDEWAYS", "NA"
LABEL = {UP: "Uptrend", DOWN: "Downtrend", SIDEWAYS: "Sideways", NA: "Data nahi"}


def _r(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v, nd)


def monthly_view(daily: pd.DataFrame) -> dict:
    mo = to_monthly(daily)
    if len(mo) < 14:
        return {"trend": NA, "label": LABEL[NA]}
    c = mo["Close"]
    s10 = sma(c, 10)
    last, avg, avg_prev = float(c.iloc[-1]), float(s10.iloc[-1]), float(s10.iloc[-4])
    if last > avg and avg > avg_prev:
        trend = UP
    elif last < avg and avg < avg_prev:
        trend = DOWN
    else:
        trend = SIDEWAYS
    return {
        "trend": trend,
        "label": LABEL[trend],
        "vs_sma10_pct": _r((last / avg - 1) * 100),
        "rsi14": _r(rsi(c, 14).iloc[-1], 1),
        "detail": f"Monthly close 10-mahine avg se {(last / avg - 1) * 100:+.1f}%",
    }


def weekly_view(daily: pd.DataFrame) -> dict:
    wk = to_weekly(daily)
    if len(wk) < 35:
        return {"trend": NA, "label": LABEL[NA]}
    c = wk["Close"]
    s10, s30 = sma(c, 10), sma(c, 30)
    last, f, s = float(c.iloc[-1]), float(s10.iloc[-1]), float(s30.iloc[-1])
    if last > s and f > s:
        trend = UP
    elif last < s and f < s:
        trend = DOWN
    else:
        trend = SIDEWAYS
    return {
        "trend": trend,
        "label": LABEL[trend],
        "vs_sma30_pct": _r((last / s - 1) * 100),
        "rsi14": _r(rsi(c, 14).iloc[-1], 1),
        "detail": f"Weekly close 30-hafte avg se {(last / s - 1) * 100:+.1f}%",
    }


def hourly_view(hourly: pd.DataFrame | None) -> dict:
    if hourly is None or len(hourly) < 60:
        return {"trend": NA, "label": LABEL[NA], "turning_up": False}
    c = hourly["Close"]
    e20, e50 = ema(c, 20), ema(c, 50)
    last, a, b = float(c.iloc[-1]), float(e20.iloc[-1]), float(e50.iloc[-1])
    if last > a > b:
        trend = UP
    elif last < a < b:
        trend = DOWN
    else:
        trend = SIDEWAYS
    recent = c.iloc[-7:]
    turning_up = bool(last > a and (recent < e20.iloc[-7:]).any())
    rng = (float(hourly["High"].iloc[-7:].max()) - float(hourly["Low"].iloc[-7:].min())) / last * 100
    if turning_up:
        detail = "Hourly chart 20 EMA ke upar wapas aaya (entry ke liye achha)"
    elif trend == UP:
        detail = "Hourly uptrend mein"
    elif trend == DOWN:
        detail = "Hourly abhi neeche ja raha: trigger ka intezaar karo"
    else:
        detail = "Hourly sideways"
    return {
        "trend": trend,
        "label": LABEL[trend],
        "turning_up": turning_up,
        "rsi14": _r(rsi(c, 14).iloc[-1], 1),
        "range_7h_pct": _r(rng),
        "last_bar": hourly.index[-1].strftime("%Y-%m-%d %H:%M"),
        "detail": detail,
    }
