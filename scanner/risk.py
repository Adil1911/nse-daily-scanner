"""Warning signs that make a stock risky to buy right now.

Each flag has a severity: 3 fresh damage, 2 a clear warning, 1 background
weakness. A stock with total severity 3 or more goes on the risky list,
most-traded names first, because those are the ones people are tempted by.
"""

from __future__ import annotations

import math

import pandas as pd

from .fmt import inr


def risk_flags(df: pd.DataFrame, ind: pd.DataFrame, m: dict, evals: dict, fno_ban: bool | None, results: tuple) -> list[dict]:
    flags: list[dict] = []

    def add(key: str, label: str, sev: int) -> None:
        flags.append({"key": key, "label": label, "sev": sev})

    c = df["Close"]
    s200 = ind["sma200"]
    n = len(df)
    if m["close"] < m["sma200"]:
        was_above = n > 30 and float((c.iloc[-25:-3] > s200.iloc[-25:-3]).mean()) >= 0.8
        if was_above:
            add("broke_200", "Abhi-abhi 200 DMA toda", 3)
        else:
            add("below_200", "200 DMA ke neeche (downtrend)", 1)

    day_ret = c.pct_change()
    worst = float(day_ret.iloc[-5:].min())
    if worst <= -0.09:
        add("crash", f"Haal mein ek din {abs(worst) * 100:.0f}% gira", 3)

    heavy_down = (c.diff() < 0) & (df["Volume"] >= 1.5 * ind["vol20"])
    dist_days = int(heavy_down.iloc[-10:].sum())
    if dist_days >= 2:
        add("distribution", f"10 din mein {dist_days} baar bhaari volume pe girawat", 2)

    gap = df["Open"] < df["Low"].shift(1) * 0.97
    if bool(gap.iloc[-3:].any()):
        add("gap_down", "Haal mein 3%+ gap-down", 2)

    prior_high = df["High"].rolling(20).max().shift(1)
    broke = c > prior_high
    window = broke.iloc[-7:-1]
    if window.any():
        i = window[window].index[-1]
        level = float(prior_high.loc[i])
        if m["close"] < level * 0.98:
            add("failed_breakout", f"Breakout fail: {inr(level, 0)} ke neeche wapas", 2)

    if m["ema20_dist_pct"] > 10 or m["rsi14"] > 75:
        add("overextended", f"Bahut tez chal chuka (20 EMA se {m['ema20_dist_pct']:+.0f}%, RSI {m['rsi14']:.0f})", 2)

    if m["atr_pct"] > 5:
        add("volatile", f"Bahut volatile (roz ~{m['atr_pct']:.1f}% hilta hai)", 1)

    results_date, results_in = results
    if results_in is not None and results_in <= 5:
        add("results_soon", f"Results {results_date:%d %b} ko: gap ka risk", 2)

    if fno_ban:
        add("fno_ban", "F&O ban mein", 2)

    if not math.isnan(m["rs_3m"]) and m["rs_3m"] < -10:
        add("weak_rs", f"3 mahine mein Nifty se {abs(m['rs_3m']):.0f}% peeche", 1)

    b = evals.get("B", {}).get("state")
    if b == "WEAK_BREAKOUT":
        add("weak_breakout", "Breakout kamzor volume pe hua", 2)
    elif b == "EXTENDED":
        add("chase", "Breakout ke baad pivot se 5%+ upar: chase mat karo", 2)
    return flags


def severity(flags: list[dict]) -> int:
    return sum(f["sev"] for f in flags)
