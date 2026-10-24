#!/usr/bin/env python3
"""Build the daily report page from the scanner's latest.json.

    python report/build_report.py --data latest.json --out report.html \
        [--notes notes.json] [--capital 100000]

notes.json is written by Claude after it checks news and results dates:
{
  "summary": "two or three lines for the top of the page",
  "stocks": {"SYMBOL": {"news": "ok|concern|unverified", "note": "...",
                        "results": "2026-11-05" or null, "verdict": "keep|avoid"}}
}
The page is self-contained except for two scripts allowed in artifacts: the
Google Fonts stylesheet and TradingView's lightweight-charts from unpkg.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TITLE = "NSE Pre-market Brief"
TIMEFRAMES = (("m", "Monthly"), ("w", "Weekly"), ("d", "Daily"), ("h", "Hourly"))
TREND_ARROW = {"UP": "▲", "DOWN": "▼", "SIDEWAYS": "▶", "NA": "–"}
TREND_WORD = {"UP": "Up", "DOWN": "Down", "SIDEWAYS": "Sideways", "NA": "n/a"}
MARK = {"pass": "✓", "fail": "✕", "soft": "~", "unknown": "?"}
STATE_NAME = {"FULL": "FULL SIZE", "CAUTION": "CAUTION", "HALF": "HALF SIZE", "NO_LONGS": "NO NEW LONGS"}
MODE_TEXT = {"full": "Full size", "half": "Half size", "prime": "Iska season", "low": "Low priority", "off": "Skip"}
STRAT_SHORT = {"A": "Pullback", "B": "Breakout", "C": "Bounce"}


# ---------------------------------------------------------------- formatting

def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def indian(n: float, nd: int = 2) -> str:
    neg = n < 0
    whole, _, frac = f"{abs(n):.{nd}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("−" if neg else "") + whole + (f".{frac}" if nd else "")


def rupees(n, nd: int = 2) -> str:
    return "—" if n is None else "₹" + indian(float(n), nd)


def signed(x, nd: int = 1, unit: str = "%") -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:+.{nd}f}{unit}".replace("-", "−")


def day_label(d: date) -> str:
    return f"{d:%a}, {d.day} {d:%b}"


def size_for(plan: dict, capital: float) -> dict:
    risk_amt = capital * plan["risk_pct"] / 100
    qty = math.floor(risk_amt / plan["risk_per_share"]) if plan["risk_per_share"] > 0 else 0
    cap = math.floor(capital * plan["max_position_pct"] / 100 / plan["entry"])
    capped = qty > cap
    qty = min(qty, cap)
    return {"qty": qty, "value": qty * plan["entry"], "risk": qty * plan["risk_per_share"], "capped": capped}


def qty_text(plan: dict, capital: float) -> tuple[str, bool]:
    s = size_for(plan, capital)
    if s["qty"] < 1:
        return "Is capital pe 1 share bhi rule ke andar nahi aata.", True
    tail = (
        f" (position limit {plan['max_position_pct']}% ki wajah se kam)"
        if s["capped"]
        else f" ({plan['risk_pct']:g}% risk)"
    )
    return f"Qty <b>{s['qty']}</b> · {rupees(s['value'], 0)} · SL lage to loss ~{rupees(s['risk'], 0)}{esc(tail)}", False


# ---------------------------------------------------------------- pieces

def masthead(d: dict) -> str:
    nxt = date.fromisoformat(d["next_session"])
    as_of = date.fromisoformat(d["as_of"])
    gen = datetime.fromisoformat(d["generated_at"])
    return (
        '<header class="mast">'
        f'<p class="eyebrow">Pre-market brief · {esc(d["universe"]["index"])}</p>'
        f"<h1>{esc(day_label(nxt))}</h1>"
        f'<p class="sub">{esc(day_label(as_of))} ke close ka data · scan {gen:%H:%M} IST · '
        f'{d["data"]["usable"]} of {d["universe"]["count"]} stocks</p>'
        "</header>"
    )


def regime_block(d: dict) -> str:
    r, b = d["regime"], d["breadth"]
    nifty = r["nifty_close"]
    facts = [
        ("Nifty 50", f'{indian(nifty, 0)} <small>{signed(r.get("nifty_chg_pct"))}</small>'),
        ("50 DMA", f'{indian(r["nifty_sma50"], 0)} <small>Nifty {signed((nifty / r["nifty_sma50"] - 1) * 100)}</small>'),
        ("200 DMA", f'{indian(r["nifty_sma200"], 0)} <small>Nifty {signed((nifty / r["nifty_sma200"] - 1) * 100)}</small>'),
        ("India VIX", f'{r["vix"]:.1f}' if r.get("vix") is not None else "—"),
    ]
    if b.get("above50_pct") is not None:
        prev = b.get("above50_pct_5d_ago")
        facts.append(
            (
                "50 DMA ke upar stocks",
                f'{b["above50_pct"]:.0f}%' + (f" <small>5 din pehle {prev:.0f}%</small>" if prev is not None else ""),
            )
        )
    dl = "".join(f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in facts)
    modes = "".join(
        f'<span class="mode{" off" if r["modes"][k] == "off" else ""}"><b>{k}</b> {STRAT_SHORT[k]}: {MODE_TEXT[r["modes"][k]]}</span>'
        for k in ("A", "B", "C")
    )
    reasons = "".join(f"<li>{esc(x)}</li>" for x in r.get("reasons", []))
    return (
        f'<section class="regime state-{esc(r["state"])}" aria-label="Market regime">'
        f'<div class="regime-top"><span class="state-pill">{STATE_NAME.get(r["state"], r["state"])}</span>'
        f'<h2>{esc(r["headline"])}</h2></div>'
        f'<dl class="facts">{dl}</dl>'
        f'<div class="modes">{modes}</div>'
        f'<ul class="reasons">{reasons}</ul>'
        "</section>"
    )


def claude_note(notes: dict) -> str:
    text = (notes or {}).get("summary")
    if not text:
        return ""
    paras = "".join(f"<p>{esc(p.strip())}</p>" for p in str(text).split("\n") if p.strip())
    checked = (notes or {}).get("checked_at")
    when = ""
    if checked:
        try:
            when = f" · {datetime.fromisoformat(checked):%H:%M} IST"
        except ValueError:
            when = ""
    return f'<section class="note" aria-label="Claude ka note"><p class="eyebrow">Claude ka note{esc(when)}</p>{paras}</section>'


def capital_block(capital: float) -> str:
    return (
        '<div class="capital">'
        '<label for="capital">Mera capital (₹)</label>'
        f'<input id="capital" type="number" inputmode="numeric" min="1000" step="1000" value="{int(capital)}">'
        '<span class="hint">Har trade ki quantity isi pe nikalti hai (A/B: 1% risk, C: 0.5%). '
        "Ye number sirf aapke browser mein save hota hai.</span>"
        "</div>"
    )


def tf_chips(mtf: dict) -> str:
    out = []
    for key, short in (("monthly", "M"), ("weekly", "W"), ("daily", "D"), ("hourly", "H")):
        v = mtf.get(key) or {}
        t = v.get("trend", "NA")
        tip = v.get("detail") or v.get("label") or ""
        out.append(
            f'<li class="{esc(t)}" title="{esc(tip)}"><b>{short}</b>{TREND_ARROW.get(t, "–")} {TREND_WORD.get(t, t)}</li>'
        )
    return f'<ul class="tf" aria-label="Timeframes: monthly, weekly, daily, hourly">{"".join(out)}</ul>'


def ticket(plan: dict, provisional: bool) -> str:
    entry, stop, tgt = plan["entry"], plan["stop"], plan["target1"]
    return (
        f'<div class="ticket{" prov" if provisional else ""}">'
        f'<div class="entry"><span class="lbl">Entry</span><span class="val">{rupees(entry)}</span>'
        f'<span class="pct muted">{"provisional" if provisional else "buy-stop"}</span></div>'
        f'<div class="stop"><span class="lbl">Stop-loss</span><span class="val">{rupees(stop)}</span>'
        f'<span class="pct down-t">{signed((stop / entry - 1) * 100)}</span></div>'
        f'<div class="tgt"><span class="lbl">Target 1</span><span class="val">{rupees(tgt)}</span>'
        f'<span class="pct up-t">{signed((tgt / entry - 1) * 100)}</span></div>'
        "</div>"
    )


def rr_bar(plan: dict) -> str:
    risk = plan["entry"] - plan["stop"]
    reward = plan["target1"] - plan["entry"]
    if risk <= 0 or reward <= 0:
        return ""
    ratio = reward / risk
    total = risk + reward
    return (
        '<div class="rr" aria-label="Risk to reward">'
        f'<span>Risk : Reward <b>1 : {ratio:.1f}</b></span>'
        f'<span class="bars" aria-hidden="true"><span class="risk" style="flex:{risk / total:.4f}"></span>'
        f'<span class="reward" style="flex:{reward / total:.4f}"></span></span>'
        "</div>"
    )


def setup_line(it: dict) -> str:
    m = it.get("metrics") or {}
    s = it["strategy"]
    parts = []
    if it.get("trigger_label"):
        parts.append(f"Trigger candle: <b>{esc(it['trigger_label'])}</b>")
    if s == "A":
        if m.get("pullback_depth_pct") is not None:
            parts.append(f"Pullback <b>{m['pullback_depth_pct']:.1f}%</b> in {m.get('pullback_days')} din")
        if m.get("pullback_vol_ratio") is not None:
            parts.append(f"pullback volume {m['pullback_vol_ratio']:.2f}× avg")
    elif s == "B":
        if m.get("pivot") is not None:
            parts.append(f"Pivot <b>{rupees(m['pivot'])}</b>")
        parts.append(f"base {m.get('base_sessions')} session, {float(m.get('base_depth_pct') or 0):.1f}% gehri")
        if m.get("contraction"):
            parts.append(f"contraction {esc(m['contraction'])}")
        if m.get("breakout_volume_x"):
            parts.append(f"breakout volume {m['breakout_volume_x']:.1f}×")
    elif s == "C":
        parts.append(f"{m.get('down_days')} din girawat")
        if m.get("rsi2") is not None:
            parts.append(f"RSI(2) {m['rsi2']:.0f}")
        parts.append(f"support: {esc(m.get('support') or 'DMA nahi, chart pe zone dekho')}")
        if m.get("mean_ema20"):
            parts.append(f"mean (20 EMA) {rupees(m['mean_ema20'])}")
    return f'<p class="facts-line">{" · ".join(parts)}</p>' if parts else ""


def plan_notes(plan: dict) -> str:
    items = [plan.get("entry_rule"), plan.get("stop_rule"), plan.get("target_rule"), plan.get("trail")]
    if plan.get("time_stop"):
        items.append(f"Time stop: {plan['time_stop']} session mein kuch na ho to exit")
    if plan.get("fast_fail"):
        items.append(plan["fast_fail"])
    items += plan.get("notes") or []
    return '<ul class="notes">' + "".join(f"<li>{esc(x)}</li>" for x in items if x) + "</ul>"


def checks_list(checks: list[dict]) -> str:
    passed = sum(1 for c in checks if c["status"] == "pass")
    rows = "".join(
        f'<li class="{esc(c["status"])}"><span class="mk" aria-label="{esc(c["status"])}">{MARK.get(c["status"], "?")}</span>'
        f'<span>{esc(c["label"])}</span><span class="v">{esc(c.get("value") or "")}</span></li>'
        for c in checks
    )
    return f'<details class="checks"><summary>Saare rules ({passed}/{len(checks)} pass)</summary><ul>{rows}</ul></details>'


def chart_block(sym: str, autoload: bool, tf: str = "d") -> str:
    tabs = "".join(
        f'<button type="button" role="tab" data-tf="{k}" aria-selected="{"true" if k == tf else "false"}">{label}</button>'
        for k, label in TIMEFRAMES
    )
    return (
        f'<div class="chart-block" data-sym="{esc(sym)}" data-tf="{tf}"{" data-autoload" if autoload else ""}>'
        f'<div class="tabs" role="tablist" aria-label="{esc(sym)} chart timeframe">{tabs}</div>'
        '<div class="readout" aria-live="polite"></div>'
        f'<div class="chart" role="img" aria-label="{esc(sym)} candlestick chart"></div>'
        '<ul class="legend"></ul>'
        "</div>"
    )


def flags_html(it: dict, note: dict | None) -> str:
    out = []
    if note:
        news = note.get("news")
        text = note.get("note") or ""
        if news == "ok":
            out.append(f'<p class="flag ok"><b>News ✓</b><span>{esc(text or "Claude ne check kiya, koi company-specific problem nahi mili.")}</span></p>')
        elif news == "concern":
            out.append(f'<p class="flag bad"><b>News ⚠</b><span>{esc(text)}</span></p>')
    results = it.get("results") or {}
    noted = (note or {}).get("results")
    rdate = noted if noted not in (None, "none") else results.get("date")
    checked = noted is not None
    if rdate:
        out.append(f'<p class="flag warn"><b>Results</b><span>{esc(rdate)}: is date se pehle position ka plan bana ke rakhein.</span></p>')
    elif results.get("status") == "unverified" and not checked:
        out.append('<p class="flag warn"><b>Check</b><span>Results date verify nahi hui: order se pehle broker app ya NSE pe dekh lein.</span></p>')
    if it["strategy"] == "C" and not (note and note.get("news") == "ok"):
        out.append('<p class="flag warn"><b>News?</b><span>Bounce trade se pehle girawat ki wajah zaroor dekhein. Company-specific kharab news ho to skip.</span></p>')
    for f in it.get("flags") or []:
        if f["key"] in ("volatile", "distribution", "gap_down", "weak_rs"):
            out.append(f'<p class="flag warn"><b>Dhyan</b><span>{esc(f["label"])}</span></p>')
    return "".join(out)


def entry_card(it: dict, notes: dict, capital: float) -> str:
    sym = it["symbol"]
    plan = it["plan"]
    note = (notes or {}).get("stocks", {}).get(sym)
    qty, short = qty_text({**plan, **it["sizing"]}, capital)
    cap = f" · {esc(it['cap'])}cap" if it.get("cap") else ""
    state_cls = "state" + (" wait" if it["state"] not in ("READY", "BROKE_OUT") else "")
    score = int(it["score"])
    return (
        f'<article class="card" id="s-{esc(sym)}">'
        '<div class="card-head">'
        f'<div class="sym"><h3>{esc(sym)}</h3><span class="co">{esc(it["name"])} · {esc(it["sector"])}{cap}</span>'
        f'<span class="px">{rupees(it["close"])} <span class="{"up-t" if (it.get("chg_pct") or 0) >= 0 else "down-t"}">{signed(it.get("chg_pct"))}</span></span></div>'
        '<div class="badges">'
        f'<span class="strat"><i>{esc(it["strategy"])}</i>{esc(it["strategy_name"])}</span>'
        f'<span class="{state_cls}">{esc(it["state_label"])}</span>'
        f'<span class="score">Score <b>{score}</b><span class="bar" aria-hidden="true"><span style="width:{score}%"></span></span></span>'
        "</div></div>"
        f"{tf_chips(it['mtf'])}"
        f"{ticket(plan, bool(plan.get('provisional')))}"
        f"{rr_bar(plan)}"
        f'<p class="qty{" short" if short else ""}" data-qty="{esc(sym)}">{qty}</p>'
        f"{setup_line(it)}"
        f"{flags_html(it, note)}"
        f"{chart_block(sym, autoload=True)}"
        f"{plan_notes(plan)}"
        f"{checks_list(it['checks'])}"
        "</article>"
    )


def watch_row(it: dict, notes: dict, capital: float) -> str:
    sym = it["symbol"]
    plan = it.get("plan") or {}
    waiting = {
        "WAIT": "Trigger candle bane tab entry",
        "WATCH": "Base ban rahi hai, pivot abhi door",
    }.get(it["state"], it["state_label"])
    level = f"Trigger ~{rupees(plan['entry'])}" if plan.get("entry") else ""
    body = []
    if plan:
        body.append(ticket(plan, True))
        qty, short = qty_text({**plan, **it["sizing"]}, capital)
        body.append(f'<p class="qty{" short" if short else ""}" data-qty="{esc(sym)}">{qty}</p>')
    body.append(tf_chips(it["mtf"]))
    body.append(setup_line(it))
    body.append(chart_block(sym, autoload=False))
    body.append(checks_list(it["checks"]))
    return (
        f'<details class="row" id="w-{esc(sym)}"><summary>'
        f'<span class="r-sym"><b>{esc(sym)}</b><span>{esc(it["strategy"])} · {esc(it["strategy_name"])}</span></span>'
        f'<span class="r-right">{rupees(it["close"])}<small>{esc(level)}</small></span>'
        f'<span class="r-sub">{esc(waiting)} · score {int(it["score"])}</span>'
        f'</summary><div class="r-body">{"".join(body)}</div></details>'
    )


def risky_row(it: dict) -> str:
    sym = it["symbol"]
    sev = min(5, int(it["severity"]))
    ticks = "".join(f'<span class="{"on" if i < sev else ""}"></span>' for i in range(5))
    reasons = it.get("reasons") or []
    body = '<ul class="notes">' + "".join(f"<li>{esc(r)}</li>" for r in reasons) + "</ul>" + chart_block(sym, autoload=False)
    return (
        f'<details class="row risky" id="r-{esc(sym)}"><summary>'
        f'<span class="r-sym"><b>{esc(sym)}</b><span>{esc(it["sector"])}</span></span>'
        f'<span class="r-right">{rupees(it["close"])}<small class="{"up-t" if (it.get("chg_pct") or 0) >= 0 else "down-t"}">{signed(it.get("chg_pct"))}</small></span>'
        f'<span class="r-sub"><span class="sev" aria-label="Risk {sev} of 5">{ticks}</span> {esc(reasons[0] if reasons else "")}</span>'
        f'</summary><div class="r-body">{body}</div></details>'
    )


def sectors_block(d: dict) -> str:
    rows = []
    for s in d.get("sectors", []):
        top = s["rank"] <= 3
        r1, r3 = s.get("ret_1m_pct"), s.get("ret_3m_pct")
        rows.append(
            f'<tr class="{"top" if top else ""}"><td class="n">{s["rank"]}</td><td>{esc(s["name"])}</td>'
            f'<td class="n {"up-t" if (r1 or 0) >= 0 else "down-t"}">{signed(r1)}</td>'
            f'<td class="n {"up-t" if (r3 or 0) >= 0 else "down-t"}">{signed(r3)}</td>'
            f'<td class="n">{(s.get("above50_pct") or 0):.0f}%</td><td class="n">{s["count"]}</td></tr>'
        )
    if not rows:
        return ""
    return (
        '<section class="sec" aria-labelledby="sec-sectors"><div class="sec-head"><h2 id="sec-sectors">Sectors</h2></div>'
        '<p class="sec-note">Median return, Nifty 500 ke andar. Strategy A top 3 sectors ko pasand karti hai.</p>'
        '<div class="tbl-wrap"><table><thead><tr><th class="n">#</th><th>Sector</th><th class="n">1M</th>'
        '<th class="n">3M</th><th class="n">50 DMA ke upar</th><th class="n">Stocks</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def footer(d: dict) -> str:
    data = d["data"]
    items = [
        f"Data: {esc(data['provider'])} (free, delayed). {data['usable']} stocks ka poora data mila"
        + (f", {len(data['failed'])} ka nahi" if data.get("failed") else "")
        + (f", {len(data['stale'])} ka purana" if data.get("stale") else "")
        + ".",
        f"Hourly chart {data.get('hourly', 0)} top setups ke liye.",
    ]
    items += [esc(w) for w in d.get("warnings", [])]
    lis = "".join(f"<li>{x}</li>" for x in items)
    return (
        '<footer class="foot">'
        f"<ul>{lis}</ul>"
        "<p>A = Trend Pullback, B = Consolidation Breakout, C = Oversold Bounce: aapke apne strategy rules. "
        "Har stock pehle regime filter, phir monthly, weekly, daily aur hourly chart se check hota hai.</p>"
        "<p>Ye rules poore karne wale setups ki list hai, kharidne ki salah nahi. Stop-loss ek plan hai, guarantee nahi: "
        "gap mein price stop ke paar khul sakta hai. SEBI-registered adviser ki jagah nahi.</p>"
        "</footer>"
    )


# ---------------------------------------------------------------- page

def build(d: dict, notes: dict | None, capital: float) -> str:
    notes = notes or {}
    stock_notes = notes.get("stocks", {})
    entries = [e for e in d.get("entries", []) if (stock_notes.get(e["symbol"]) or {}).get("verdict") != "avoid"]
    removed = [e for e in d.get("entries", []) if (stock_notes.get(e["symbol"]) or {}).get("verdict") == "avoid"]
    watch = d.get("watchlist", [])
    risky = d.get("risky", [])
    regime = d["regime"]

    parts = [masthead(d), regime_block(d), claude_note(notes)]
    if entries or watch:
        parts.append(capital_block(capital))

    # entries
    sec = ['<section class="sec" aria-labelledby="sec-entries"><div class="sec-head"><h2 id="sec-entries">Aaj ki entries</h2>'
           f'<span class="count">{len(entries)}</span></div>']
    if regime["state"] == "NO_LONGS":
        sec.append('<p class="empty">Nifty apne 200 DMA ke neeche hai, isliye aaj koi naya long trade nahi. '
                   "Aapke rules yahi kehte hain: aise market mein long list dena sabse mehenga 'help' hota hai.</p>")
    elif not entries:
        sec.append('<p class="empty">Aaj koi setup saare rules poore nahi karta. Zero bhi sahi answer hai; '
                   "filters dheele karke trade mat banaiye. Neeche watchlist dekhiye.</p>")
    else:
        sec.append('<p class="sec-note">Buy-stop order: price entry ke upar jaaye tabhi trade bane. '
                   "Fill hote hi stop-loss order lagaiye. Breakout (B) sirf volume ke saath lein.</p>")
        sec += [entry_card(e, notes, capital) for e in entries]
    if removed:
        lis = "".join(
            f"<li><b>{esc(e['symbol'])}</b>: {esc((stock_notes.get(e['symbol']) or {}).get('note') or 'news/results check mein problem')}</li>"
            for e in removed
        )
        sec.append(f'<div class="empty"><p class="eyebrow">Check ke baad hataye gaye</p><ul class="notes">{lis}</ul></div>')
    sec.append("</section>")
    parts.append("".join(sec))

    if watch:
        parts.append(
            '<section class="sec" aria-labelledby="sec-watch"><div class="sec-head"><h2 id="sec-watch">Watchlist</h2>'
            f'<span class="count">{len(watch)}</span></div>'
            '<p class="sec-note">Setup lagbhag taiyaar, bas trigger ka intezaar. Tap karke chart aur levels dekhein.</p>'
            f'<div class="rows">{"".join(watch_row(w, notes, capital) for w in watch)}</div></section>'
        )
    if risky:
        parts.append(
            '<section class="sec" aria-labelledby="sec-risky"><div class="sec-head"><h2 id="sec-risky">Risky: abhi inse bacho</h2>'
            f'<span class="count">{len(risky)}</span></div>'
            '<p class="sec-note">Zyada trade hone wale stocks jinke chart pe taaza nuksaan ke nishan hain. Bar jitne bhare, risk utna zyada.</p>'
            f'<div class="rows">{"".join(risky_row(r) for r in risky)}</div></section>'
        )
    parts.append(sectors_block(d))
    parts.append(footer(d))
    body = "\n".join(p for p in parts if p)

    plans, levels = {}, {}
    for it in entries + watch:
        p = it.get("plan")
        if not p:
            continue
        plans[it["symbol"]] = {
            "entry": p["entry"],
            "risk_per_share": p["risk_per_share"],
            "risk_pct": it["sizing"]["risk_pct"],
            "max_position_pct": it["sizing"]["max_position_pct"],
        }
        levels[it["symbol"]] = {"entry": p["entry"], "stop": p["stop"], "target": p["target1"]}
    wanted = {it["symbol"] for it in entries + watch + risky}
    payload = {
        "default_capital": capital,
        "plans": plans,
        "levels": levels,
        "charts": {k: v for k, v in (d.get("charts") or {}).items() if k in wanted},
    }
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = (HERE / "template.html").read_text(encoding="utf-8")
    return template.replace("{{TITLE}}", esc(TITLE)).replace("{{BODY}}", body).replace("{{DATA}}", data_json)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the NSE pre-market report page")
    ap.add_argument("--data", required=True, help="latest.json from the scanner")
    ap.add_argument("--out", required=True, help="where to write the HTML page")
    ap.add_argument("--notes", help="notes.json from Claude's news and results check")
    ap.add_argument("--capital", type=float, default=100000, help="default capital in rupees for quantities")
    args = ap.parse_args(argv)
    d = json.loads(Path(args.data).read_text(encoding="utf-8"))
    notes = json.loads(Path(args.notes).read_text(encoding="utf-8")) if args.notes else None
    page = build(d, notes, args.capital)
    Path(args.out).write_text(page, encoding="utf-8")
    print(f"Wrote {args.out} ({len(page) / 1024:.0f} KB): {len(d.get('entries', []))} entries, "
          f"{len(d.get('watchlist', []))} watchlist, {len(d.get('risky', []))} risky")
    return 0


if __name__ == "__main__":
    sys.exit(main())
