"""Run one scan and write latest.json (plus the cached stock lists and a picks log)."""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
import time
from datetime import date
from pathlib import Path

import pandas as pd

from . import __version__
from .data import drop_unfinished
from .indicators import daily_indicators, ema, ret, sma, to_monthly, to_weekly
from .market import Calendar, now_ist
from .nse_extras import fetch_fno_ban, fetch_results_calendar
from .regime import SIZE_MULT, compute_regime
from .risk import risk_flags, severity
from .scoring import score
from .strategies import Ctx, eval_a, eval_b, eval_c, metrics, r2
from .timeframes import hourly_view, monthly_view, weekly_view
from .universe import INDEX_NAMES, load_caps, load_universe

ROOT = Path(__file__).resolve().parent.parent
LIVE_STATES = ("READY", "BROKE_OUT", "WAIT", "WATCH")
ENTRY_STATES = ("READY", "BROKE_OUT")
STATE_LABEL = {
    "READY": "Entry ready",
    "BROKE_OUT": "Breakout ho chuka",
    "WAIT": "Trigger candle ka intezaar",
    "WATCH": "Base ban rahi, pivot door",
    "WEAK_BREAKOUT": "Kamzor breakout",
    "EXTENDED": "Bahut upar nikal gaya",
    "FAIL": "Setup nahi",
}
STRATEGY_KEYS = {"A": "strategy_a", "B": "strategy_b", "C": "strategy_c"}


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _log(msg: str) -> None:
    print(msg, flush=True)


def _num(x, nd: int = 2):
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return x
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, nd)


def _clean(obj):
    """JSON-safe copy: no NaN, no numpy scalars, dates as ISO strings."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, (date, pd.Timestamp)):
        return obj.isoformat()[:10]
    if hasattr(obj, "item"):
        obj = obj.item()
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    return obj


def daily_trend(m: dict) -> dict:
    c, s50, s200 = m["close"], m["sma50"], m["sma200"]
    if c > s50 > s200:
        trend, label = "UP", "Uptrend"
    elif c < s50 < s200:
        trend, label = "DOWN", "Downtrend"
    else:
        trend, label = "SIDEWAYS", "Sideways"
    return {"trend": trend, "label": label, "detail": f"Close 50 DMA se {(c / s50 - 1) * 100:+.1f}%, 200 DMA se {(c / s200 - 1) * 100:+.1f}%"}


def _points(index, values, nd=2, fmt="%Y-%m-%d"):
    out = []
    for t, v in zip(index, values):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        out.append([t.strftime(fmt), round(float(v), nd)])
    return out


def _bars(frame: pd.DataFrame, fmt="%Y-%m-%d"):
    return [
        [t.strftime(fmt), round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2), int(v)]
        for t, o, h, l, c, v in zip(
            frame.index, frame["Open"], frame["High"], frame["Low"], frame["Close"], frame["Volume"].fillna(0)
        )
    ]


def chart_data(df: pd.DataFrame, ind: pd.DataFrame, hdf: pd.DataFrame | None, rc: dict) -> dict:
    d = df.iloc[-rc["chart_daily_bars"] :]
    di = ind.loc[d.index]
    wk = to_weekly(df)
    w10, w30 = sma(wk["Close"], 10), sma(wk["Close"], 30)
    wk_tail = wk.iloc[-rc["chart_weekly_bars"] :]
    mo = to_monthly(df)
    m10 = sma(mo["Close"], 10)
    mo_tail = mo.iloc[-rc["chart_monthly_bars"] :]
    out = {
        "d": {
            "bars": _bars(d),
            "ema20": _points(d.index, di["ema20"].values),
            "sma50": _points(d.index, di["sma50"].values),
            "sma200": _points(d.index, di["sma200"].values),
        },
        "w": {
            "bars": _bars(wk_tail),
            "sma10": _points(wk_tail.index, w10.loc[wk_tail.index].values),
            "sma30": _points(wk_tail.index, w30.loc[wk_tail.index].values),
        },
        "m": {"bars": _bars(mo_tail), "sma10": _points(mo_tail.index, m10.loc[mo_tail.index].values)},
    }
    if hdf is not None and len(hdf):
        h = hdf.iloc[-rc["chart_hourly_bars"] :]
        e20, e50 = ema(hdf["Close"], 20), ema(hdf["Close"], 50)
        fmt = "%Y-%m-%d %H:%M"
        out["h"] = {
            "bars": _bars(h, fmt),
            "ema20": _points(h.index, e20.loc[h.index].values, fmt=fmt),
            "ema50": _points(h.index, e50.loc[h.index].values, fmt=fmt),
        }
    return out


def sector_table(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["sector"], []).append(r)
    table = []
    for name, items in groups.items():
        r1 = [x["ret_1m"] for x in items if not math.isnan(x["ret_1m"])]
        r3 = [x["ret_3m"] for x in items if not math.isnan(x["ret_3m"])]
        if not r1 or not r3:
            continue
        table.append(
            {
                "name": name,
                "count": len(items),
                "ret_1m_pct": statistics.median(r1) * 100,
                "ret_3m_pct": statistics.median(r3) * 100,
                "above50_pct": sum(1 for x in items if x["above50"]) / len(items) * 100,
            }
        )
    for t in table:
        t["_blend"] = (t["ret_1m_pct"] + t["ret_3m_pct"]) / 2
    table.sort(key=lambda t: t["_blend"], reverse=True)
    for i, t in enumerate(table, 1):
        t["rank"] = i
        del t["_blend"]
    return table


def _results_for(sym: str, as_of: date, cal: Calendar, dates: dict) -> tuple[date | None, int | None]:
    upcoming = [d for d in dates.get(sym, []) if d > as_of]
    if not upcoming:
        return None, None
    rd = min(upcoming)
    return rd, cal.sessions_between(as_of, rd)


def _sizing(strategy: str, cfg: dict, regime: dict) -> dict:
    p = cfg[STRATEGY_KEYS[strategy]]
    mult = SIZE_MULT.get(regime["modes"][strategy], 1.0)
    return {
        "risk_pct": round(p["risk_pct"] * mult, 3),
        "max_position_pct": p["max_position_pct"],
        "regime_mult": mult,
        "rule": f"Capital ka {p['risk_pct'] * mult:g}% risk, position max {p['max_position_pct']}% capital",
    }


def _item(sym: str, rec: dict, ev: dict, sc: int, cfg: dict, regime: dict) -> dict:
    ctx, m = rec["ctx"], rec["m"]
    return {
        "symbol": sym,
        "name": ctx.name,
        "sector": ctx.sector,
        "cap": ctx.cap,
        "strategy": ev["strategy"],
        "strategy_name": ev["name"],
        "state": ev["state"],
        "state_label": STATE_LABEL[ev["state"]],
        "score": sc,
        "close": _num(m["close"]),
        "chg_pct": _num(m["chg_pct"]),
        "rsi14": _num(m["rsi14"], 1),
        "atv20_cr": _num(m["atv20_cr"], 1),
        "dist_52wh_pct": _num(m["dist_52wh_pct"], 1),
        "rs_3m": _num(m["rs_3m"], 1),
        "atr_pct": _num(m["atr_pct"]),
        "trigger": ev["trigger"],
        "trigger_label": ev["trigger_label"],
        "plan": ev["plan"],
        "sizing": _sizing(ev["strategy"], cfg, regime),
        "mtf": rec["mtf"],
        "checks": ev["checks"],
        "metrics": ev["metrics"],
        "flags": rec["flags"],
        "results": {
            "date": ctx.results_date.isoformat() if ctx.results_date else None,
            "in_sessions": ctx.results_in,
            "status": "known" if (ctx.results_date or ctx.results_known) else "unverified",
        },
        "news": "unverified",
    }


def _picks_log(prev_dir: Path | None, out_dir: Path, as_of: date, items: list[dict]) -> None:
    fields = ["date", "symbol", "strategy", "state", "score", "close", "entry", "stop", "target1"]
    rows: list[dict] = []
    old = prev_dir / "picks_log.csv" if prev_dir else None
    if old and old.exists():
        with old.open(encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("date") != as_of.isoformat()]
    for it in items:
        plan = it.get("plan") or {}
        rows.append(
            {
                "date": as_of.isoformat(),
                "symbol": it["symbol"],
                "strategy": it["strategy"],
                "state": it["state"],
                "score": it["score"],
                "close": it["close"],
                "entry": plan.get("entry"),
                "stop": plan.get("stop"),
                "target1": plan.get("target1"),
            }
        )
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    (out_dir / "picks_log.csv").write_text(buf.getvalue(), encoding="utf-8")


def run_scan(
    cfg: dict,
    provider,
    out_dir,
    prev_dir=None,
    now=None,
    universe_rows: list[dict] | None = None,
    caps: dict | None = None,
    extras: bool = True,
    symbols: list[str] | None = None,
    limit: int | None = None,
    log=_log,
) -> dict:
    started = time.time()
    now = now or now_ist()
    now_naive = pd.Timestamp(now.replace(tzinfo=None))
    cal = Calendar(cfg.get("nse_holidays", []))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prev_dir = Path(prev_dir) if prev_dir else None
    sfx = cfg["universe"]["yahoo_suffix"]
    dcfg, rc = cfg["data"], cfg["report"]
    warnings: list[str] = []

    # 1. The stock list
    if universe_rows is None:
        rows, uni_source, uni_text = load_universe(
            cfg["universe"]["index"], prev_dir, ROOT / "data" / "nifty500_fallback.csv", log
        )
        (out_dir / "universe.csv").write_text(uni_text, encoding="utf-8")
        if uni_source != "nse":
            warnings.append(f"Aaj NSE se stock list nahi mili; {uni_source} copy use hui.")
    else:
        rows, uni_source = universe_rows, "given"
    if caps is None:
        caps, caps_source = load_caps(prev_dir, out_dir, log)
        if caps_source == "unavailable":
            warnings.append("Large/Mid/Small category nahi mili: Strategy C mein cap check 'unverified' hai.")
    else:
        caps_source = "given"
    if symbols:
        wanted = set(symbols)
        rows = [r for r in rows if r["symbol"] in wanted]
    if limit:
        rows = rows[:limit]
    meta = {r["symbol"]: r for r in rows}
    log(f"Universe: {len(rows)} stocks ({uni_source})")

    # 2. Nifty and VIX
    nifty = provider.index_daily(cfg["regime"]["nifty"], dcfg["daily_period"])
    if nifty is None or len(nifty) < 220:
        raise RuntimeError("Nifty 50 ka data nahi mila, scan ruk gaya.")
    nifty = drop_unfinished(nifty, now_naive, False)
    vix = provider.index_daily(cfg["regime"]["vix"], "1y")
    if vix is not None:
        vix = drop_unfinished(vix, now_naive, False)
    else:
        warnings.append("India VIX ka data nahi mila.")
    as_of = nifty.index[-1].date()
    next_session = cal.next_session(as_of)
    log(f"Last session: {as_of}, next: {next_session}")

    # 3. Daily bars for every stock
    tickers = [r["symbol"] + sfx for r in rows]
    raw = provider.daily(tickers, dcfg["daily_period"])
    frames: dict[str, pd.DataFrame] = {}
    failed, stale, short = [], [], []
    for r in rows:
        sym = r["symbol"]
        df = raw.get(sym + sfx)
        df = drop_unfinished(df, now_naive, False) if df is not None else None
        if df is None or df.empty:
            failed.append(sym)
        elif df.index[-1].date() < as_of:
            stale.append(sym)
        elif len(df) < dcfg["min_history_sessions"]:
            short.append(sym)
        else:
            frames[sym] = df
    log(f"Daily data: {len(frames)} usable, {len(failed)} failed, {len(stale)} stale, {len(short)} too new")
    if failed:
        warnings.append(f"{len(failed)} stocks ka data Yahoo se nahi aaya.")
    if len(frames) < 0.5 * max(1, len(rows)):
        warnings.append("Aadhe se kam stocks ka data mila: aaj ki list adhoori hai.")

    # 4. Indicators, breadth, sectors
    nifty_r3 = ret(nifty["Close"], 63)
    inds = {s: daily_indicators(df) for s, df in frames.items()}
    above_now, above_5, above200 = [], [], []
    basics = []
    for s, df in frames.items():
        ind = inds[s]
        c = df["Close"]
        if not math.isnan(ind["sma50"].iloc[-1]):
            above_now.append(c.iloc[-1] > ind["sma50"].iloc[-1])
        if len(df) > 6 and not math.isnan(ind["sma50"].iloc[-6]):
            above_5.append(c.iloc[-6] > ind["sma50"].iloc[-6])
        if not math.isnan(ind["sma200"].iloc[-1]):
            above200.append(c.iloc[-1] > ind["sma200"].iloc[-1])
        basics.append(
            {
                "sector": meta[s]["industry"],
                "ret_1m": ret(c, 21),
                "ret_3m": ret(c, 63),
                "above50": bool(c.iloc[-1] > ind["sma50"].iloc[-1]),
            }
        )
    pct = lambda xs: (sum(1 for x in xs if x) / len(xs) * 100) if xs else math.nan  # noqa: E731
    breadth = {
        "above50_pct": pct(above_now),
        "above50_pct_5d_ago": pct(above_5),
        "above200_pct": pct(above200),
        "count": len(above_now),
    }
    regime = compute_regime(nifty, vix, breadth, cfg["regime"]["vix_full_size_max"])
    sectors = sector_table(basics)
    top_sectors = {t["name"] for t in sectors[:3]}
    log(f"Regime: {regime['state']} | breadth {breadth['above50_pct']:.0f}% above 50 DMA")

    # 5. NSE extras
    if extras:
        fno = fetch_fno_ban(log)
        rescal = fetch_results_calendar(log)
    else:
        fno = {"status": "skipped", "symbols": [], "trade_date": None}
        rescal = {"status": "skipped", "source": None, "dates": {}}
    ban = set(fno["symbols"]) if fno["status"] == "ok" else None
    if fno["status"] != "ok":
        warnings.append("F&O ban list nahi mili: broker app mein check karein.")
    results_dates: dict = dict(rescal.get("dates") or {})
    results_known = rescal["status"] == "ok"
    if not results_known:
        warnings.append("NSE results calendar nahi mila: top stocks ki results date alag se check hogi.")

    # 6. Evaluate every stock
    recs: dict[str, dict] = {}

    def evaluate(sym: str, known: bool) -> dict:
        df, ind, info = frames[sym], inds[sym], meta[sym]
        rd, rin = _results_for(sym, as_of, cal, results_dates)
        ctx = Ctx(
            symbol=sym,
            name=info["name"],
            sector=info["industry"],
            cap=caps.get(sym) if caps else None,
            df=df,
            ind=ind,
            nifty_ret_3m=nifty_r3,
            sector_top=info["industry"] in top_sectors,
            fno_ban=(sym in ban) if ban is not None else None,
            results_date=rd,
            results_in=rin,
            results_known=known,
        )
        m = metrics(ctx)
        evs = {
            "A": eval_a(ctx, m, cfg["strategy_a"]),
            "B": eval_b(ctx, m, cfg["strategy_b"]),
            "C": eval_c(ctx, m, cfg["strategy_c"]),
        }
        flags = risk_flags(df, ind, m, evs, ctx.fno_ban, (rd, rin))
        return {"ctx": ctx, "m": m, "evs": evs, "flags": flags}

    for sym in frames:
        recs[sym] = evaluate(sym, results_known)

    # 7. Monthly and weekly views for live setups, then a first ranking
    def live(rec):
        return [ev for ev in rec["evs"].values() if ev["state"] in LIVE_STATES]

    for sym, rec in recs.items():
        if live(rec):
            rec["mtf"] = {
                "monthly": monthly_view(frames[sym]),
                "weekly": weekly_view(frames[sym]),
                "daily": daily_trend(rec["m"]),
                "hourly": {"trend": "NA", "label": "Data nahi", "turning_up": False},
            }
    ranked = sorted(
        (
            (score(ev, rec["m"], rec["mtf"], rec["ctx"].sector_top, rec["flags"]), sym)
            for sym, rec in recs.items()
            for ev in live(rec)
        ),
        reverse=True,
    )
    shortlist: list[str] = []
    for _, sym in ranked:
        if sym not in shortlist:
            shortlist.append(sym)

    # 8. Results dates from Yahoo for the shortlist when NSE's calendar failed
    if not results_known and shortlist:
        look = shortlist[: dcfg["max_earnings_lookups"]]
        found = provider.earnings_dates([s + sfx for s in look])
        for s in look:
            dates = found.get(s + sfx)
            if dates:
                results_dates[s] = dates
            recs[s] = {**evaluate(s, bool(dates)), "mtf": recs[s].get("mtf")}
            if recs[s]["mtf"] is None or not live(recs[s]):
                recs[s].pop("mtf", None)
        log(f"Results dates from Yahoo: {len(found)} of {len(look)}")

    # 9. Hourly bars for the best setups
    hourly_syms = [s for s in shortlist if "mtf" in recs[s]][: dcfg["max_hourly_symbols"]]
    hraw = provider.hourly([s + sfx for s in hourly_syms], dcfg["hourly_period"]) if hourly_syms else {}
    hourly: dict[str, pd.DataFrame] = {}
    for s in hourly_syms:
        h = hraw.get(s + sfx)
        if h is not None:
            h = drop_unfinished(h, now_naive, True)
            if h is not None and len(h):
                hourly[s] = h
                recs[s]["mtf"]["hourly"] = hourly_view(h)
    log(f"Hourly data: {len(hourly)} of {len(hourly_syms)}")

    # 10. Final lists
    modes = regime["modes"]
    cands = []
    for sym, rec in recs.items():
        if "mtf" not in rec:
            continue
        for ev in live(rec):
            sc = score(ev, rec["m"], rec["mtf"], rec["ctx"].sector_top, rec["flags"])
            cands.append((sc, sym, ev))
    cands.sort(key=lambda x: (-x[0], x[1]))

    entries: list[dict] = []
    watch: list[dict] = []
    used: set[str] = set()
    per_sector: dict[str, int] = {}
    c_entries = 0
    blocked_by_regime = 0
    for sc, sym, ev in cands:
        if sym in used:
            continue
        if modes[ev["strategy"]] == "off":
            blocked_by_regime += 1
            continue
        rec = recs[sym]
        sector = rec["ctx"].sector
        if ev["state"] in ENTRY_STATES and len(entries) < rc["max_entries"]:
            if per_sector.get(sector, 0) >= rc["max_per_sector"]:
                continue
            if ev["strategy"] == "C" and c_entries >= rc["max_c_entries"]:
                continue
            entries.append(_item(sym, rec, ev, sc, cfg, regime))
            per_sector[sector] = per_sector.get(sector, 0) + 1
            c_entries += ev["strategy"] == "C"
            used.add(sym)
        elif ev["state"] not in ENTRY_STATES and len(watch) < rc["max_watchlist"]:
            watch.append(_item(sym, rec, ev, sc, cfg, regime))
            used.add(sym)

    risky = []
    for sym, rec in recs.items():
        sev = severity(rec["flags"])
        m = rec["m"]
        if sev >= 3 and m["atv20_cr"] >= cfg["strategy_a"]["min_atv_cr"] and sym not in used:
            risky.append(
                {
                    "symbol": sym,
                    "name": rec["ctx"].name,
                    "sector": rec["ctx"].sector,
                    "close": _num(m["close"]),
                    "chg_pct": _num(m["chg_pct"]),
                    "atv20_cr": _num(m["atv20_cr"], 1),
                    "severity": sev,
                    "reasons": [f["label"] for f in sorted(rec["flags"], key=lambda f: -f["sev"])],
                    "_tv": m["atv20_cr"],
                }
            )
    risky.sort(key=lambda r: (-r["severity"], -r["_tv"]))
    risky = risky[: rc["max_risky"]]
    for r in risky:
        r.pop("_tv", None)

    # 11. Charts for everything listed
    charts = {}
    for it in entries + watch + risky:
        s = it["symbol"]
        if s not in charts:
            charts[s] = chart_data(frames[s], inds[s], hourly.get(s), rc)

    counts = {k: {} for k in ("A", "B", "C")}
    for rec in recs.values():
        for k, ev in rec["evs"].items():
            counts[k][ev["state"]] = counts[k].get(ev["state"], 0) + 1

    stocks = {}
    for sym, rec in recs.items():
        m, ctx = rec["m"], rec["ctx"]
        stocks[sym] = {
            "n": ctx.name,
            "s": ctx.sector,
            "cap": ctx.cap,
            "c": _num(m["close"]),
            "ch": _num(m["chg_pct"]),
            "r14": _num(m["rsi14"], 1),
            "a50": bool(m["close"] > m["sma50"]),
            "a200": bool(m["close"] > m["sma200"]),
            "e20": _num(m["ema20_dist_pct"], 1),
            "r3m": _num(m["ret_3m"] * 100 if not math.isnan(m["ret_3m"]) else math.nan, 1),
            "rs": _num(m["rs_3m"], 1),
            "atv": _num(m["atv20_cr"], 1),
            "d52": _num(m["dist_52wh_pct"], 1),
            "atr": _num(m["atr_pct"]),
            "A": rec["evs"]["A"]["state"],
            "B": rec["evs"]["B"]["state"],
            "C": rec["evs"]["C"]["state"],
            "fl": [f["key"] for f in rec["flags"]],
        }

    if regime["state"] == "NO_LONGS":
        message = f"Market hostile (Nifty 200 DMA ke neeche): koi entry nahi. {len(risky)} risky stocks."
    else:
        message = f"{len(entries)} entries, {len(watch)} watchlist, {len(risky)} risky. Regime: {regime['state']}."

    out = {
        "schema": 1,
        "version": __version__,
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of": as_of.isoformat(),
        "next_session": next_session.isoformat(),
        "message": message,
        "universe": {
            "index": INDEX_NAMES.get(cfg["universe"]["index"], cfg["universe"]["index"]),
            "source": uni_source,
            "caps_source": caps_source,
            "count": len(rows),
        },
        "data": {
            "provider": getattr(provider, "name", "unknown"),
            "usable": len(frames),
            "failed": sorted(failed),
            "stale": sorted(stale),
            "too_new": sorted(short),
            "hourly": len(hourly),
            "seconds": round(time.time() - started, 1),
        },
        "checks": {
            "fno_ban": fno,
            "results_calendar": {"status": rescal["status"], "source": rescal.get("source")},
        },
        "warnings": warnings,
        "regime": regime,
        "breadth": {k: _num(v, 1) for k, v in breadth.items()},
        "sectors": [{k: _num(v, 1) if k not in ("name", "count", "rank") else v for k, v in t.items()} for t in sectors],
        "counts": counts,
        "blocked_by_regime": blocked_by_regime,
        "entries": entries,
        "watchlist": watch,
        "risky": risky,
        "stocks": stocks,
        "charts": charts,
    }
    out = _clean(out)
    (out_dir / "latest.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _picks_log(prev_dir, out_dir, as_of, entries + watch)
    log(message)
    log(f"Done in {time.time() - started:.0f}s -> {out_dir / 'latest.json'}")
    return out
