"""Best-effort extras from NSE: the F&O ban list and upcoming results dates.

Both come from public NSE pages that sometimes refuse automated requests.
A failure is recorded as "unavailable" and never stops the scan; Claude then
checks the shortlisted stocks by search instead.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .universe import BROWSER_HEADERS

FNO_BAN_URLS = (
    "https://nsearchives.nseindia.com/content/fo/fo_secban.csv",
    "https://nsearchives.nseindia.com/archives/fo/sec_ban/fo_secban.csv",
)
NSE_HOME = "https://www.nseindia.com/"
EVENT_CALENDAR_URL = "https://www.nseindia.com/api/event-calendar"


def parse_fno_ban(text: str) -> tuple[set[str], str | None]:
    trade_date = None
    m = re.search(r"Trade Date\s+(\d{1,2}-[A-Za-z]{3}-\d{4})", text)
    if m:
        try:
            trade_date = datetime.strptime(m.group(1).title(), "%d-%b-%Y").date().isoformat()
        except ValueError:
            trade_date = None
    symbols = set()
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1]:
            symbols.add(parts[1].upper())
    return symbols, trade_date


def fetch_fno_ban(log=print) -> dict:
    import requests

    reasons = []
    for url in FNO_BAN_URLS:
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=20)
        except Exception as exc:
            reasons.append(f"{url.split('nseindia.com/')[-1]}: {exc.__class__.__name__}")
            continue
        if resp.status_code == 200 and "ban" in resp.text[:300].lower():
            symbols, trade_date = parse_fno_ban(resp.text)
            return {"status": "ok", "symbols": sorted(symbols), "trade_date": trade_date}
        reasons.append(f"{url.split('nseindia.com/')[-1]}: HTTP {resp.status_code}")
    log(f"  F&O ban list unavailable ({'; '.join(reasons)})")
    return {"status": "unavailable", "symbols": [], "trade_date": None, "reason": "; ".join(reasons)}


def parse_event_calendar(items) -> dict[str, list[date]]:
    """Results-related board meetings: {symbol: [dates]}."""
    out: dict[str, list[date]] = {}
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        text = f"{it.get('purpose', '')} {it.get('bm_desc', '')}".lower()
        if "result" not in text:
            continue
        sym = (it.get("symbol") or "").strip().upper()
        raw = (it.get("date") or "").strip()
        if not sym or not raw:
            continue
        for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                d = datetime.strptime(raw.title() if "%b" in fmt else raw, fmt).date()
                break
            except ValueError:
                d = None
        if d:
            out.setdefault(sym, []).append(d)
    return {k: sorted(set(v)) for k, v in out.items()}


def fetch_results_calendar(log=print) -> dict:
    import requests

    try:
        s = requests.Session()
        s.headers.update(BROWSER_HEADERS)
        s.get(NSE_HOME, timeout=20)
        resp = s.get(EVENT_CALENDAR_URL, timeout=20, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            data = parse_event_calendar(resp.json())
            return {"status": "ok", "source": "NSE event calendar", "dates": data}
        reason = f"HTTP {resp.status_code}"
    except Exception as exc:
        reason = exc.__class__.__name__
    log(f"  NSE event calendar unavailable ({reason})")
    return {"status": "unavailable", "source": None, "dates": {}, "reason": reason}
