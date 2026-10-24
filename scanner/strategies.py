"""Strategy A, B and C, written from the user's own swing-strategy skills.

Each evaluator returns a dict:
  strategy, name, state, checks[], failed[], trigger, plan, metrics
States:
  READY          every rule passes and the entry can be placed for the next session
  WAIT           every rule passes except the trigger candle (watchlist)
  WATCH          (B) a valid base, but price is more than 5% under the pivot
  BROKE_OUT      (B) broke above the pivot on volume and is still within 5% of it
  WEAK_BREAKOUT  (B) closed above the pivot on low volume: not an entry
  EXTENDED       (B) already more than 5% above the pivot: the trade is gone
  FAIL           a rule failed (see failed[])
Check status is pass, fail, soft (a near miss that lowers the score) or
unknown (could not be verified; Claude checks it before the report).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from . import candles
from .fmt import inr, shares
from .indicators import pullback_legs, ret, slope, zigzag

TICK = 0.05


def round_up(x: float, step: float = TICK) -> float:
    return round(math.ceil(x / step - 1e-9) * step, 2)


def round_down(x: float, step: float = TICK) -> float:
    return round(math.floor(x / step + 1e-9) * step, 2)


def r2(x):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 2)


@dataclass
class Ctx:
    symbol: str
    name: str
    sector: str
    cap: str | None
    df: pd.DataFrame
    ind: pd.DataFrame
    nifty_ret_3m: float
    sector_top: bool = False
    fno_ban: bool | None = None
    results_date: date | None = None
    results_in: int | None = None
    results_known: bool = False


class Checks:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, key: str, label: str, status: str, value=None) -> str:
        self.items.append({"key": key, "label": label, "status": status, "value": value})
        return status

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.items if c["status"] == "fail"]

    def count(self, status: str) -> int:
        return sum(1 for c in self.items if c["status"] == status)


def ok(cond) -> str:
    return "pass" if bool(cond) else "fail"


def bar(df: pd.DataFrame, i: int) -> candles.Bar:
    row = df.iloc[i]
    return candles.Bar(float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]))


# ---------------------------------------------------------------- shared

def metrics(ctx: Ctx) -> dict:
    df, ind = ctx.df, ctx.ind
    c = df["Close"]
    n = len(df)
    li = ind.iloc[-1]
    close = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    hi252 = df["High"].iloc[-252:].values
    lo252 = df["Low"].iloc[-252:].values
    days_since_52wh = int(np.argmax(hi252[::-1]))
    sma50_prev = float(ind["sma50"].iloc[-21]) if n > 21 else math.nan
    sma200_prev = float(ind["sma200"].iloc[-21]) if n > 21 else math.nan
    r3 = ret(c, 63)
    m = {
        "close": close,
        "prev_close": prev,
        "chg_pct": (close / prev - 1) * 100,
        "ema10": float(li["ema10"]),
        "ema20": float(li["ema20"]),
        "sma50": float(li["sma50"]),
        "sma200": float(li["sma200"]),
        "rsi14": float(li["rsi14"]),
        "rsi2": float(li["rsi2"]),
        "atr14": float(li["atr14"]),
        "atr_pct": float(li["atr14"]) / close * 100,
        "volume": float(df["Volume"].iloc[-1]),
        "vol20": float(li["vol20"]),
        "vol50": float(li["vol50"]),
        "atv20_cr": float(li["tv20"]) / 1e7,
        "ret_1w": ret(c, 5),
        "ret_1m": ret(c, 21),
        "ret_3m": r3,
        "ret_6m": ret(c, 126),
        "rs_3m": (r3 - ctx.nifty_ret_3m) * 100 if not math.isnan(r3) else math.nan,
        "high_52w": float(hi252.max()),
        "low_52w": float(lo252.min()),
        "days_since_52wh": days_since_52wh,
        "sma50_rising": bool(float(li["sma50"]) > sma50_prev) if not math.isnan(sma50_prev) else False,
        "sma200_slope_pct": (float(li["sma200"]) / sma200_prev - 1) * 100 if sma200_prev else math.nan,
    }
    m["dist_52wh_pct"] = (close / m["high_52w"] - 1) * 100
    m["ema20_dist_pct"] = (close / m["ema20"] - 1) * 100
    return m


def fno_status(ctx: Ctx) -> str:
    if ctx.fno_ban is None:
        return "unknown"
    return ok(not ctx.fno_ban)


def results_check(ctx: Ctx, buffer: int) -> tuple[str, str]:
    if ctx.results_date is not None and ctx.results_in is not None:
        value = f"{ctx.results_date:%d %b} ({ctx.results_in} session baad)"
        return ok(ctx.results_in > buffer), value
    if ctx.results_known:
        return "pass", "Koi results date paas nahi"
    return "unknown", "Date verify karni hai"


def stage1(ck: Checks, ctx: Ctx, m: dict, p: dict) -> None:
    ck.add("price", f"Price ₹{p['min_price']} se upar", ok(m["close"] > p["min_price"]), f"{inr(m['close'])}")
    ck.add(
        "liquidity",
        f"Roz ka turnover ₹{p['min_atv_cr']} Cr+",
        ok(m["atv20_cr"] >= p["min_atv_cr"]),
        f"₹{m['atv20_cr']:,.1f} Cr",
    )
    ck.add("fno_ban", "F&O ban mein nahi", fno_status(ctx))
    status, value = results_check(ctx, p["results_buffer_sessions"])
    ck.add("results", f"Agle {p['results_buffer_sessions']} session mein results nahi", status, value)


def higher_high(df: pd.DataFrame, n: int = 40) -> bool:
    if len(df) < 2 * n:
        return False
    recent = float(df["High"].iloc[-n:].max())
    before = float(df["High"].iloc[-2 * n : -n].max())
    return recent > before


def result(strategy: str, name: str, state: str, ck: Checks, trigger=None, plan=None, metrics_=None) -> dict:
    return {
        "strategy": strategy,
        "name": name,
        "state": state,
        "checks": ck.items,
        "failed": [c["label"] for c in ck.failed],
        "trigger": trigger,
        "trigger_label": candles.LABELS.get(trigger) if trigger else None,
        "plan": plan,
        "metrics": metrics_ or {},
    }


# ---------------------------------------------------------------- Strategy A

def find_pullback(df: pd.DataFrame, ind: pd.DataFrame, lookback: int) -> dict:
    n = len(df)
    hi = df["High"].iloc[-lookback:].values
    days = int(np.argmax(hi[::-1]))  # 0 = the high is today
    hi_pos = n - 1 - days
    swing_high = float(df["High"].iloc[hi_pos])
    out = {"days": days, "swing_high": swing_high, "hi_pos": hi_pos}
    if days == 0:
        return out
    pb = df.iloc[hi_pos + 1 :]
    vol20 = float(ind["vol20"].iloc[-1])
    out.update(
        {
            "low": float(pb["Low"].min()),
            "depth": (swing_high - float(pb["Low"].min())) / swing_high,
            "vol_ratio": float(pb["Volume"].mean()) / vol20 if vol20 > 0 else math.nan,
            "vol_slope": slope(pb["Volume"].values) if len(pb) >= 3 else math.nan,
            "held_50": bool((pb["Close"] >= ind["sma50"].iloc[hi_pos + 1 :]).all()),
        }
    )
    return out


def eval_a(ctx: Ctx, m: dict, p: dict) -> dict:
    df, ind = ctx.df, ctx.ind
    n = len(df)
    ck = Checks()
    stage1(ck, ctx, m, p)
    ck.add("above_200", "200 DMA ke upar", ok(m["close"] > m["sma200"]), f"200 DMA {inr(m['sma200'])}")
    ck.add("sma50_gt_200", "50 DMA, 200 DMA ke upar", ok(m["sma50"] > m["sma200"]))
    ck.add("sma50_rising", "50 DMA upar ja raha (20 din)", ok(m["sma50_rising"]))
    ck.add("above_50", "50 DMA ke upar", ok(m["close"] > m["sma50"]), f"50 DMA {inr(m['sma50'])}")
    ck.add("higher_high", "40 din mein higher high", ok(higher_high(df, 40)))
    ck.add(
        "rs_3m",
        "3 mahine mein Nifty se behtar",
        ok(m["rs_3m"] > 0),
        f"{m['ret_3m'] * 100:+.1f}% vs Nifty {ctx.nifty_ret_3m * 100:+.1f}%",
    )

    pb = find_pullback(df, ind, p["swing_lookback"])
    days = pb["days"]
    mets = {"pullback_days": days, "swing_high": r2(pb["swing_high"])}
    ck.add(
        "pullback_days",
        f"Swing high se {p['pullback_days_min']}-{p['pullback_days_max']} din ka pullback",
        ok(p["pullback_days_min"] <= days <= p["pullback_days_max"]),
        f"{days} din",
    )
    plan = None
    trigger = None
    if days > 0:
        depth = pb["depth"]
        near20 = abs(m["ema20_dist_pct"]) <= p["ema20_band"] * 100
        lvl_win = df["High"].iloc[-60 : -p["swing_lookback"]] if n > 60 else None
        at_level = False
        if lvl_win is not None and len(lvl_win):
            lvl = float(lvl_win.max())
            at_level = pb["swing_high"] > lvl * 1.02 and abs(m["close"] / lvl - 1) <= 0.03 and pb["low"] >= lvl * 0.97
        rsi_v = m["rsi14"]
        if p["rsi_min"] <= rsi_v <= p["rsi_max"]:
            rsi_status = "pass"
        elif p["rsi_max"] < rsi_v <= p["rsi_soft_max"]:
            rsi_status = "soft"
        else:
            rsi_status = "fail"
        vol_ok = not math.isnan(pb["vol_ratio"]) and pb["vol_ratio"] < 1.0
        ck.add(
            "pullback_depth",
            f"Pullback {p['depth_min'] * 100:.0f}-{p['depth_max'] * 100:.0f}% gehra",
            ok(p["depth_min"] <= depth <= p["depth_max"]),
            f"{depth * 100:.1f}%",
        )
        ck.add(
            "near_support",
            "20 EMA (3% ke andar) ya purane breakout level pe",
            ok(near20 or at_level),
            f"20 EMA se {m['ema20_dist_pct']:+.1f}%" + (" · breakout level" if at_level else ""),
        )
        ck.add(
            "pullback_volume",
            "Pullback mein volume 20-din avg se kam",
            ok(vol_ok),
            f"{pb['vol_ratio']:.2f}× avg" if not math.isnan(pb["vol_ratio"]) else None,
        )
        ck.add("held_50", "Pullback mein 50 DMA ke neeche close nahi", ok(pb["held_50"]))
        ck.add("rsi", f"RSI(14) {p['rsi_min']}-{p['rsi_max']}", rsi_status, f"{rsi_v:.1f}")

        found = candles.detect(bar(df, -2), bar(df, -1))
        trigger = candles.first_trigger(found, candles.TRIGGERS_A)
        last = bar(df, -1)
        entry = round_up(last.h + TICK)
        stop_struct = pb["low"] - TICK
        stop_atr = entry - p["atr_stop_mult"] * m["atr14"]
        wider = "pullback low" if stop_struct <= stop_atr else f"{p['atr_stop_mult']}× ATR"
        stop = round_down(min(stop_struct, stop_atr))
        risk = entry - stop
        risk_pct = risk / entry
        target = round_up(entry + p["target_r"] * risk)
        ck.add("stop_width", f"Stop {p['max_stop_pct'] * 100:.0f}% ke andar", ok(risk_pct <= p["max_stop_pct"]), f"{risk_pct * 100:.1f}%")
        notes = []
        if pb["swing_high"] < target:
            notes.append(f"Swing high {inr(pb['swing_high'])} target se pehle resistance hai")
        if not math.isnan(pb["vol_slope"]) and pb["vol_slope"] < 0:
            notes.append("Pullback mein volume ghat raha hai (achha sign)")
        plan = {
            "entry": entry,
            "entry_rule": "Trigger candle ke high ke upar buy-stop" if trigger else "Trigger candle bane tab uske high ke upar buy-stop (abhi provisional)",
            "stop": stop,
            "stop_rule": f"Pullback low ya 1.5× ATR, jo neeche ho ({wider})",
            "risk_per_share": r2(risk),
            "risk_pct": r2(risk_pct * 100),
            "target1": target,
            "target_rule": f"{p['target_r']:g}R pe 50% book",
            "rr": p["target_r"],
            "trail": "Baaki 10 EMA ke neeche daily close pe trail",
            "time_stop": p["time_stop_sessions"],
            "provisional": trigger is None,
            "notes": notes,
        }
        mets.update(
            {
                "pullback_depth_pct": r2(depth * 100),
                "pullback_vol_ratio": r2(pb["vol_ratio"]),
                "at_breakout_level": at_level,
            }
        )
    else:
        ck.add("pullback_depth", "Pullback hua ho", "fail", "Aaj hi naya high")

    state = "FAIL" if ck.failed else ("READY" if trigger else "WAIT")
    return result("A", p["name"], state, ck, trigger, plan if state != "FAIL" else None, mets)


# ---------------------------------------------------------------- Strategy B

def find_base(df: pd.DataFrame, ind: pd.DataFrame, end: int, p: dict) -> dict | None:
    """The base ending just before index `end`, starting at its left-side high."""
    lookback = p["base_max"]
    win_start = max(0, end - lookback)
    sub = df.iloc[win_start:end]
    if len(sub) < p["base_min"]:
        return None
    highs = sub["High"].values
    i_hi = int(np.argmax(highs))
    start = win_start + i_hi
    length = end - start
    top = float(highs[i_hi])
    # A left high in the window's first bar may only be the edge of an older, longer base.
    too_long = i_hi == 0 and win_start > 0 and float(df["High"].iloc[max(0, win_start - 20) : win_start].max()) >= top
    base = df.iloc[start:end]
    low = float(base["Low"].min())
    legs = pullback_legs(zigzag(base["High"].values, base["Low"].values, p["zigzag_pct"]))
    contracting = (
        len(legs) >= 2
        and all(legs[i + 1] <= legs[i] * 1.15 for i in range(len(legs) - 1))
        and legs[-1] < legs[0]
    )
    adv_from = max(0, start - p["advance_lookback"])
    adv_low = float(df["Low"].iloc[adv_from : start + 1].min())
    vol5 = float(df["Volume"].iloc[max(0, end - 5) : end].mean())
    vol50 = float(ind["vol50"].iloc[end - 1])
    base_vol = float(base["Volume"].mean())
    tail = base.iloc[-p["pivot_lookback"] :]
    return {
        "start": start,
        "end": end,
        "length": length,
        "high": top,
        "low": low,
        "depth": (top - low) / top,
        "legs": legs,
        "contracting": contracting,
        "too_long": too_long,
        "adv_low": adv_low,
        "advance": top / adv_low - 1 if adv_low > 0 else math.nan,
        "upper_half": low >= adv_low + 0.5 * (top - adv_low),
        "above50": float((base["Close"] > ind["sma50"].iloc[start:end]).mean()),
        "vol5": vol5,
        "vol50": vol50,
        "base_vol": base_vol,
        "dry": vol50 > 0 and vol5 < p["dry_volume_ratio"] * vol50 and vol5 < base_vol,
        "pivot": float(tail["High"].max()),
        "final_low": float(base["Low"].iloc[-p["final_low_lookback"] :].min()),
    }


def eval_b(ctx: Ctx, m: dict, p: dict) -> dict:
    df, ind = ctx.df, ctx.ind
    n = len(df)
    ck = Checks()
    stage1(ck, ctx, m, p)

    mode = "base"
    base = None
    yesterday = find_base(df, ind, n - 1, p)
    if yesterday and yesterday["length"] >= p["base_min"] and m["close"] > yesterday["pivot"]:
        base, mode = yesterday, "breakout"
    else:
        base = find_base(df, ind, n, p)
    if base is None:
        ck.add("base", "Base mili", "fail", "History kam")
        return result("B", p["name"], "FAIL", ck)

    ck.add("advance", "Base se pehle 3-6 mahine mein 30%+ tezi", ok(base["advance"] >= p["advance_min"]), f"{base['advance'] * 100:.0f}%")
    ck.add("above_200", "200 DMA ke upar", ok(m["close"] > m["sma200"]), f"200 DMA {inr(m['sma200'])}")
    ck.add("sma50_gt_200", "50 DMA, 200 DMA ke upar", ok(m["sma50"] > m["sma200"]))
    ck.add(
        "near_52wh",
        f"52W high se {p['max_from_52w_high'] * 100:.0f}% ke andar",
        ok(m["dist_52wh_pct"] >= -p["max_from_52w_high"] * 100),
        f"{m['dist_52wh_pct']:.1f}%",
    )
    ck.add(
        "rs_3m",
        "3 mahine mein Nifty se behtar",
        ok(m["rs_3m"] > 0),
        f"{m['ret_3m'] * 100:+.1f}% vs Nifty {ctx.nifty_ret_3m * 100:+.1f}%",
    )
    ck.add(
        "base_length",
        f"Base {p['base_min']}-{p['base_max']} session ki",
        ok(p["base_min"] <= base["length"] <= p["base_max"] and not base["too_long"]),
        f"{base['length']} session",
    )
    depth = base["depth"]
    if p["depth_min"] <= depth <= p["depth_max"]:
        depth_status = "pass"
    elif p["depth_max"] < depth <= p["depth_soft_max"]:
        depth_status = "soft"
    else:
        depth_status = "fail"
    ck.add("base_depth", f"Base {p['depth_min'] * 100:.0f}-{p['depth_max'] * 100:.0f}% gehri", depth_status, f"{depth * 100:.1f}%")
    seq = " → ".join(f"−{d * 100:.0f}%" for d in base["legs"]) or "—"
    ck.add("contraction", "Har pullback pichhle se chhota", ok(base["contracting"]), seq)
    ratio = base["vol5"] / base["vol50"] if base["vol50"] > 0 else math.nan
    ck.add("dry_volume", "Aakhri hafte volume sukha", ok(base["dry"]), f"{ratio:.2f}× 50-din avg" if not math.isnan(ratio) else None)
    ck.add(
        "above_50_base",
        "Base mein zyadatar 50 DMA ke upar",
        ok(base["above50"] >= p["above_50dma_share"]),
        f"{base['above50'] * 100:.0f}% din",
    )
    ck.add("upper_half", "Base pichhli tezi ke upper half mein", ok(base["upper_half"]))

    pivot = base["pivot"]
    vol_mult = m["volume"] / m["vol20"] if m["vol20"] > 0 else math.nan
    if mode == "breakout":
        ext = m["close"] / pivot - 1
        if ext > p["max_extension"]:
            state = "EXTENDED"
        elif not math.isnan(vol_mult) and vol_mult >= p["breakout_volume_mult"]:
            state = "BROKE_OUT"
        else:
            state = "WEAK_BREAKOUT"
    else:
        state = "READY" if pivot / m["close"] - 1 <= p["ready_within"] else "WATCH"

    mets = {
        "base_sessions": base["length"],
        "base_depth_pct": r2(depth * 100),
        "contraction": seq,
        "prior_advance_pct": r2(base["advance"] * 100),
        "pivot": r2(pivot),
        "pivot_dist_pct": r2((pivot / m["close"] - 1) * 100),
        "breakout_volume_x": r2(vol_mult) if mode == "breakout" else None,
        "dry_volume_x": r2(ratio),
    }
    plan = None
    if state in ("READY", "WATCH", "BROKE_OUT"):
        entry = round_up(pivot + TICK) if state != "BROKE_OUT" else round(m["close"], 2)
        stop = round_down(base["final_low"] - TICK)
        risk = entry - stop
        risk_pct = risk / entry if entry else math.nan
        mm = pivot + (base["high"] - base["low"])
        rr = (mm - entry) / risk if risk > 0 else math.nan
        target = round_up(min(mm, entry + p["min_rr"] * risk))
        ck.add("stop_width", f"Stop {p['max_stop_pct'] * 100:.0f}% ke andar", ok(risk > 0 and risk_pct <= p["max_stop_pct"]), f"{risk_pct * 100:.1f}%")
        ck.add("rr", f"Measured move kam se kam {p['min_rr']:g}R", ok(not math.isnan(rr) and rr >= p["min_rr"]), f"{rr:.1f}R" if not math.isnan(rr) else None)
        vol_needed = int(math.ceil(p["breakout_volume_mult"] * m["vol20"]))
        plan = {
            "entry": entry,
            "entry_rule": (
                f"Pivot {inr(pivot)} ke upar buy-stop, sirf jab us din volume {p['breakout_volume_mult']:g}× avg (~{shares(vol_needed)} shares) ho"
                if state != "BROKE_OUT"
                else f"Breakout ho chuka (volume {vol_mult:.1f}×). {inr(pivot * (1 + p['max_extension']))} se upar chase mat karo"
            ),
            "stop": stop,
            "stop_rule": "Aakhri contraction ke low ke neeche",
            "risk_per_share": r2(risk),
            "risk_pct": r2(risk_pct * 100),
            "target1": target,
            "target_rule": f"Measured move {inr(mm)} ya {p['min_rr']:g}R, jo pehle aaye: 50% book",
            "rr": r2(rr),
            "trail": "Baaki 10 EMA ke neeche daily close pe trail",
            "time_stop": p["time_stop_sessions"],
            "fast_fail": "3 session mein close wapas base ke andar aaye to turant exit",
            "provisional": state == "WATCH",
            "notes": [],
        }
    if ck.failed:
        state = "FAIL"
        plan = None
    return result("B", p["name"], state, ck, None, plan, mets)


# ---------------------------------------------------------------- Strategy C

def consecutive_down(close: pd.Series) -> int:
    c = close.values
    i = len(c) - 1
    if i >= 1 and c[i] >= c[i - 1]:
        i -= 1  # today bounced: count the run that ended yesterday
    k = 0
    while i >= 1 and c[i] < c[i - 1]:
        k += 1
        i -= 1
    return k


def eval_c(ctx: Ctx, m: dict, p: dict) -> dict:
    df, ind = ctx.df, ctx.ind
    ck = Checks()
    stage1(ck, ctx, m, p)
    if ctx.cap:
        ck.add("cap", "Largecap ya liquid midcap", ok(ctx.cap in p["allowed_caps"]), ctx.cap)
    else:
        ck.add("cap", "Largecap ya liquid midcap", "unknown", "Category nahi mili")
    ck.add("above_200", "200 DMA ke upar", ok(m["close"] > m["sma200"]), f"200 DMA {inr(m['sma200'])}")
    slope200 = m["sma200_slope_pct"]
    ck.add(
        "sma200_flat_up",
        "200 DMA flat ya upar",
        ok(not math.isnan(slope200) and slope200 >= p["sma200_flat_tolerance"] * 100),
        f"{slope200:+.2f}% (20 din)" if not math.isnan(slope200) else None,
    )
    ck.add(
        "recent_52wh",
        "Pichhle 6 mahine mein 52W high bana",
        ok(m["days_since_52wh"] <= p["high_52w_within_sessions"]),
        f"{m['days_since_52wh']} session pehle",
    )

    c = df["Close"]
    down_days = consecutive_down(c)
    w = p["decline_window"]
    drop = 1 - float(c.iloc[-1]) / float(c.iloc[-1 - w]) if len(c) > w else math.nan
    sharp = (down_days >= p["down_days_min"]) or (p["decline_min"] <= drop <= p["decline_max"])
    ck.add(
        "selloff",
        f"{p['down_days_min']}-5 din lagatar girawat ya 5 din mein 6-12% gira",
        ok(sharp),
        f"{down_days} din girawat · 5 din {-drop * 100:+.1f}%" if not math.isnan(drop) else f"{down_days} din",
    )
    rsi2_low = float(ind["rsi2"].iloc[-2:].min())
    rsi14_low = float(ind["rsi14"].iloc[-2:].min())
    ck.add(
        "oversold",
        f"RSI(2) < {p['rsi2_max']} ya RSI(14) < {p['rsi14_max']}",
        ok(rsi2_low < p["rsi2_max"] or rsi14_low < p["rsi14_max"]),
        f"RSI2 {rsi2_low:.0f} · RSI14 {rsi14_low:.0f}",
    )
    recent_low = float(df["Low"].iloc[-2:].min())
    band = p["support_band"]
    at50 = recent_low <= m["sma50"] * (1 + band) and m["close"] >= m["sma50"] * (1 - band)
    at200 = recent_low <= m["sma200"] * (1 + band) and m["close"] > m["sma200"]
    support = "50 DMA" if at50 else ("200 DMA" if at200 else None)
    ck.add("support", "50/200 DMA support pe", "pass" if support else "soft", support or "DMA support nahi (chart pe zone dekho)")
    swing = float(df["High"].iloc[-10:].max())
    total_decline = 1 - float(df["Low"].iloc[-3:].min()) / swing
    ck.add("decline_cap", f"Kul girawat {p['max_total_decline'] * 100:.0f}% se kam", ok(total_decline < p["max_total_decline"]), f"{total_decline * 100:.1f}%")

    found = candles.detect(bar(df, -2), bar(df, -1))
    trigger = candles.first_trigger(found, candles.TRIGGERS_C)
    last = bar(df, -1)
    entry = round_up(last.h + TICK)
    stop = round_down(min(last.l, float(df["Low"].iloc[-5:].min())) - TICK)
    risk = entry - stop
    risk_pct = risk / entry
    mean = m["ema20"]
    rr_mean = (mean - entry) / risk if risk > 0 else math.nan
    ck.add("stop_width", f"Stop {p['max_stop_pct'] * 100:.0f}% ke andar", ok(risk_pct <= p["max_stop_pct"]), f"{risk_pct * 100:.1f}%")
    ck.add(
        "rr",
        f"20 EMA tak kam se kam {p['min_rr']:g}R",
        ok(not math.isnan(rr_mean) and rr_mean >= p["min_rr"]),
        f"{rr_mean:.1f}R" if not math.isnan(rr_mean) else None,
    )
    ck.add("news", "Girawat ki wajah company-specific nahi", "unknown", "News check karni hai")
    prev_down_vol = float(df["Volume"].iloc[-2])
    tapering = m["volume"] < prev_down_vol

    plan = {
        "entry": entry,
        "entry_rule": "Reversal candle ke high ke upar buy-stop" if trigger else "Reversal candle bane tab uske high ke upar (abhi provisional)",
        "stop": stop,
        "stop_rule": "Reversal candle ya haal ke swing low ke neeche, jo neeche ho",
        "risk_per_share": r2(risk),
        "risk_pct": r2(risk_pct * 100),
        "target1": round_up(entry + p["min_rr"] * risk),
        "target_rule": f"{p['min_rr']:g}R ya 20 EMA {inr(mean)}, jo pehle aaye",
        "rr": p["min_rr"],
        "trail": "Trail mat karo: mean tak aate hi edge khatam",
        "time_stop": p["time_stop_sessions"],
        "provisional": trigger is None,
        "notes": ["Half size. Average down kabhi nahi."],
    }
    mets = {
        "down_days": down_days,
        "drop_5d_pct": r2(-drop * 100) if not math.isnan(drop) else None,
        "rsi2": r2(rsi2_low),
        "rsi14": r2(rsi14_low),
        "support": support,
        "volume_tapering": tapering,
        "mean_ema20": r2(mean),
    }
    state = "FAIL" if ck.failed else ("READY" if trigger else "WAIT")
    return result("C", p["name"], state, ck, trigger, plan if state != "FAIL" else None, mets)
