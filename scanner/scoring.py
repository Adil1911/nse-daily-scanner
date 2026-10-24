"""Setup score, 0-100: how cleanly a setup meets its rules and how many
timeframes agree. It ranks setups against each other; it is not a forecast."""

from __future__ import annotations

import math

BASE = {"READY": 60, "BROKE_OUT": 58, "WAIT": 45, "WATCH": 35}
TREND_POINTS = {"UP": 8, "SIDEWAYS": 2, "DOWN": -8, "NA": 0}
STRONG_TRIGGERS = {"bullish_engulfing", "hammer"}


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score(ev: dict, m: dict, mtf: dict, sector_top: bool, flags: list[dict]) -> int:
    s = BASE.get(ev["state"], 0)
    if s == 0:
        return 0
    s += TREND_POINTS.get(mtf.get("monthly", {}).get("trend", "NA"), 0)
    s += TREND_POINTS.get(mtf.get("weekly", {}).get("trend", "NA"), 0)
    h = mtf.get("hourly", {})
    if h.get("turning_up") or h.get("trend") == "UP":
        s += 6
    elif h.get("trend") == "DOWN":
        s -= 4
    if not math.isnan(m.get("rs_3m", math.nan)):
        s += clamp(m["rs_3m"] / 2, -4, 8)
    if sector_top:
        s += 4

    met = ev.get("metrics", {})
    if ev["strategy"] == "A":
        vr = met.get("pullback_vol_ratio")
        if vr is not None and vr < 0.7:
            s += 4
        d = met.get("pullback_depth_pct")
        if d is not None and 5 <= d <= 9:
            s += 2
    elif ev["strategy"] == "B":
        dv = met.get("dry_volume_x")
        if dv is not None and dv < 0.6:
            s += 3
        if m.get("dist_52wh_pct", -100) >= -10:
            s += 3
    elif ev["strategy"] == "C":
        if met.get("support"):
            s += 3
        if met.get("volume_tapering"):
            s += 2
    if ev.get("trigger") in STRONG_TRIGGERS:
        s += 3

    soft = sum(1 for c in ev["checks"] if c["status"] == "soft")
    unknown = sum(1 for c in ev["checks"] if c["status"] == "unknown")
    s -= 3 * soft + 1 * unknown
    s -= 2 * sum(f["sev"] for f in flags if f["key"] in ("volatile", "distribution", "gap_down"))
    return int(round(clamp(s, 0, 100)))
