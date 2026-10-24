"""Market regime from the Nifty 50 and India VIX.

The rules are the ones in the user's three swing strategies:

  Nifty above a rising 50 DMA and VIX < 16  -> FULL
      A full size, B allowed (its season), C usable but low priority
  Nifty between its 50 and 200 DMA          -> HALF
      A half size (2-3 positions), B skipped, C in its prime season
  Nifty below its 200 DMA                   -> NO_LONGS
      no new longs in any strategy

Above the 50 DMA but with VIX >= 16 or a 50 DMA that is not rising is not in
the strategy tables; it is treated as CAUTION: A half size, B skipped, C usable.
"""

from __future__ import annotations

import math

import pandas as pd

from .indicators import sma


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return math.nan
    return v


def compute_regime(nifty: pd.DataFrame, vix: pd.DataFrame | None, breadth: dict, vix_max: float = 16.0) -> dict:
    close = nifty["Close"]
    s50 = sma(close, 50)
    s200 = sma(close, 200)
    c = _f(close.iloc[-1])
    v50 = _f(s50.iloc[-1])
    v200 = _f(s200.iloc[-1])
    v50_prev = _f(s50.iloc[-21]) if len(s50) > 21 else math.nan
    rising = bool(v50 > v50_prev) if not math.isnan(v50_prev) else False
    vix_now = _f(vix["Close"].iloc[-1]) if vix is not None and len(vix) else math.nan
    vix_known = not math.isnan(vix_now)
    chg = (c / _f(close.iloc[-2]) - 1.0) * 100 if len(close) > 1 else math.nan

    reasons = []
    if c < v200:
        state = "NO_LONGS"
        modes = {"A": "off", "B": "off", "C": "off"}
        headline = "Nifty 200 DMA ke neeche hai: aaj koi naya long trade nahi."
        reasons.append(f"Nifty {c:,.0f} < 200 DMA {v200:,.0f}")
    elif c < v50:
        state = "HALF"
        modes = {"A": "half", "B": "off", "C": "prime"}
        headline = "Nifty 50 aur 200 DMA ke beech: A half size, B skip, C ka season."
        reasons.append(f"Nifty {c:,.0f} 50 DMA {v50:,.0f} ke neeche, 200 DMA {v200:,.0f} ke upar")
    else:
        full = rising and (vix_known and vix_now < vix_max)
        if full:
            state = "FULL"
            modes = {"A": "full", "B": "full", "C": "low"}
            headline = "Market strong hai: A aur B full size, C low priority."
        else:
            state = "CAUTION"
            modes = {"A": "half", "B": "off", "C": "low"}
            headline = "Nifty 50 DMA ke upar hai par signal kamzor: A half size, B skip."
        reasons.append(f"Nifty {c:,.0f} > 50 DMA {v50:,.0f}")
        if not rising:
            reasons.append("50 DMA abhi upar nahi ja raha")
        if not vix_known:
            reasons.append("India VIX ka data nahi mila")
        elif vix_now >= vix_max:
            reasons.append(f"India VIX {vix_now:.1f} (16 se upar)")
    if vix_known and state in ("FULL", "CAUTION") and vix_now < vix_max:
        reasons.append(f"India VIX {vix_now:.1f}")

    b_now = breadth.get("above50_pct")
    b_prev = breadth.get("above50_pct_5d_ago")
    breadth_improving = (
        b_now is not None and b_prev is not None and not math.isnan(b_now) and not math.isnan(b_prev) and b_now > b_prev
    )
    if state == "FULL" and not breadth_improving:
        reasons.append("Breadth sudhar nahi rahi: breakouts mein extra savdhani")

    return {
        "state": state,
        "headline": headline,
        "modes": modes,
        "reasons": reasons,
        "nifty_close": round(c, 2),
        "nifty_chg_pct": round(chg, 2) if not math.isnan(chg) else None,
        "nifty_sma50": round(v50, 2),
        "nifty_sma200": round(v200, 2),
        "sma50_rising": rising,
        "vix": round(vix_now, 2) if vix_known else None,
        "breadth_improving": breadth_improving,
        "as_of": close.index[-1].date().isoformat(),
    }


SIZE_MULT = {"full": 1.0, "half": 0.5, "prime": 1.0, "low": 1.0, "off": 0.0}
