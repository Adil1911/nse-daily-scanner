"""The stock universe: Nifty 500 constituents from NSE's index files.

NSE publishes each index's constituents as a CSV with columns
Company Name, Industry, Symbol, Series, ISIN Code. The scanner downloads the
list each run and keeps the last good copy, so a failed download falls back
to yesterday's list instead of stopping the scan.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

ARCHIVE_BASES = (
    "https://nsearchives.nseindia.com/content/indices/",
    "https://archives.nseindia.com/content/indices/",
)
INDEX_FILES = {
    "nifty50": "ind_nifty50list.csv",
    "nifty100": "ind_nifty100list.csv",
    "nifty200": "ind_nifty200list.csv",
    "nifty500": "ind_nifty500list.csv",
    "midcap150": "ind_niftymidcap150list.csv",
}
INDEX_NAMES = {
    "nifty50": "NIFTY 50",
    "nifty100": "NIFTY 100",
    "nifty200": "NIFTY 200",
    "nifty500": "NIFTY 500",
}
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def parse_index_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for raw in reader:
        r = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
        sym = r.get("Symbol")
        # NSE fills a pending constituent change with a DUMMY... placeholder row.
        if not sym or sym.upper().startswith("DUMMY"):
            continue
        series = r.get("Series") or "EQ"
        if series not in ("EQ", "BE", "BZ"):
            continue
        rows.append(
            {
                "symbol": sym,
                "name": r.get("Company Name") or sym,
                "industry": r.get("Industry") or "Other",
                "isin": r.get("ISIN Code", ""),
            }
        )
    return rows


def fetch_index_csv(key: str, session=None, timeout: int = 20, log=print) -> str | None:
    import requests

    s = session or requests.Session()
    for base in ARCHIVE_BASES:
        url = base + INDEX_FILES[key]
        try:
            resp = s.get(url, headers=BROWSER_HEADERS, timeout=timeout)
        except Exception as exc:  # network errors only change the source, never stop the run
            log(f"  {url}: {exc.__class__.__name__}")
            continue
        if resp.status_code == 200 and "Symbol" in resp.text[:400]:
            return resp.text
        log(f"  {url}: HTTP {resp.status_code}")
    return None


def load_universe(index: str, cache_dir: Path | None, fallback: Path | None, log=print):
    """Return (rows, source, csv_text); source is 'nse', 'cache' or 'fallback'."""
    text = fetch_index_csv(index, log=log)
    if text:
        rows = parse_index_csv(text)
        if len(rows) >= 40:
            return rows, "nse", text
        log(f"  NSE list for {index} had only {len(rows)} rows; ignoring it")
    for path, source in ((cache_dir / "universe.csv" if cache_dir else None, "cache"), (fallback, "fallback")):
        if path and path.exists():
            text = path.read_text(encoding="utf-8")
            rows = parse_index_csv(text)
            if rows:
                return rows, source, text
    raise RuntimeError(
        "Could not get the stock list: NSE did not answer and there is no saved copy yet."
    )


def load_caps(prev_dir: Path | None, out_dir: Path | None, log=print) -> tuple[dict[str, str], str]:
    """Map symbol -> Large / Mid from the Nifty 100 and Midcap 150 lists.

    Symbols in neither list are smallcaps within the Nifty 500. Falls back to
    the previous run's copy, and returns ({}, 'unavailable') when there is none.
    """
    caps: dict[str, str] = {}
    got = 0
    for key, label in (("nifty100", "Large"), ("midcap150", "Mid")):
        text = fetch_index_csv(key, log=log)
        if text:
            got += 1
            for r in parse_index_csv(text):
                caps[r["symbol"]] = label
    source = "nse"
    if got < 2:
        # Half a list would mark midcaps as smallcaps, so use a whole old copy or nothing.
        old = prev_dir / "caps.json" if prev_dir else None
        if not (old and old.exists()):
            return {}, "unavailable"
        caps, source = json.loads(old.read_text(encoding="utf-8")), "cache"
    if out_dir:
        (out_dir / "caps.json").write_text(json.dumps(caps, indent=0, sort_keys=True), encoding="utf-8")
    return caps, source
