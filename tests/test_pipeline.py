"""The whole scan on a synthetic market, then the report page built from it."""

import csv
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "report"))

import build_report  # noqa: E402
import synthetic as syn  # noqa: E402
from scanner.data import MemoryProvider  # noqa: E402
from scanner.market import IST  # noqa: E402
from scanner.run import load_config, run_scan  # noqa: E402

NOW = datetime(2026, 10, 24, 7, 0, tzinfo=IST)


def no_nan(_):
    raise ValueError("NaN in JSON")


def scan(tmp, nifty_trend=0.30, vix=13.0):
    rows, caps, daily, hourly = syn.market(nifty_trend, vix)
    daily["^NSEI"] = syn.nifty(nifty_trend)
    daily["^INDIAVIX"] = syn.vix(vix)
    out = run_scan(
        load_config(), MemoryProvider(daily, hourly), tmp, universe_rows=rows, caps=caps,
        extras=False, now=NOW, log=lambda *_: None,
    )
    return out, json.loads((Path(tmp) / "latest.json").read_text(encoding="utf-8"), parse_constant=no_nan)


class Pipeline(unittest.TestCase):
    def test_strong_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, saved = scan(tmp)
            self.assertEqual(saved["as_of"], "2026-10-23")
            self.assertEqual(saved["next_session"], "2026-10-26")
            self.assertEqual(saved["regime"]["state"], "FULL")
            got = {e["symbol"]: e["strategy"] for e in saved["entries"]}
            self.assertEqual(got.get("APULL"), "A")
            self.assertEqual(got.get("BBASE"), "B")
            self.assertEqual(got.get("COVER"), "C")
            self.assertIn("ECRASH", [r["symbol"] for r in saved["risky"]])
            listed = {e["symbol"] for e in saved["entries"] + saved["watchlist"]}
            self.assertNotIn("FILLIQ", listed)
            for e in saved["entries"]:
                self.assertIn(e["symbol"], saved["charts"])
                self.assertEqual(set(e["mtf"]), {"monthly", "weekly", "daily", "hourly"})
                self.assertIn("h", saved["charts"][e["symbol"]])
            cover = [e for e in saved["entries"] if e["symbol"] == "COVER"][0]
            self.assertEqual(cover["sizing"]["risk_pct"], 0.5)
            with open(Path(tmp) / "picks_log.csv", encoding="utf-8") as fh:
                log = list(csv.DictReader(fh))
            self.assertEqual(len(log), len(saved["entries"]) + len(saved["watchlist"]))

    def test_hostile_market_lists_no_trades(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, saved = scan(tmp, nifty_trend=-0.25)
            self.assertEqual(saved["regime"]["state"], "NO_LONGS")
            self.assertEqual(saved["entries"], [])
            self.assertEqual(saved["watchlist"], [])
            self.assertIn("hostile", saved["message"])

    def test_picks_log_keeps_history(self):
        with tempfile.TemporaryDirectory() as prev, tempfile.TemporaryDirectory() as tmp:
            (Path(prev) / "picks_log.csv").write_text(
                "date,symbol,strategy,state,score,close,entry,stop,target1\n2026-10-22,OLD,A,READY,70,100,101,95,113\n",
                encoding="utf-8",
            )
            rows, caps, daily, hourly = syn.market()
            run_scan(load_config(), MemoryProvider(daily, hourly), tmp, prev_dir=prev, universe_rows=rows,
                     caps=caps, extras=False, now=NOW, log=lambda *_: None)
            text = (Path(tmp) / "picks_log.csv").read_text(encoding="utf-8")
            self.assertIn("2026-10-22,OLD", text)
            self.assertIn("2026-10-23,", text)


class Report(unittest.TestCase):
    def test_page_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, saved = scan(tmp)
            notes = {"summary": "Line one.\nLine two.", "stocks": {"BBASE": {"news": "concern", "note": "Promoter ne stake becha", "verdict": "avoid"}}}
            page = build_report.build(saved, notes, 250000)
            self.assertTrue(page.startswith("<title>NSE Pre-market Brief</title>"))
            self.assertEqual(re.findall(r"\{\{[A-Z]+\}\}", page), [])
            data = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page, re.S).group(1))
            self.assertEqual(data["default_capital"], 250000)
            self.assertIn("APULL", data["plans"])
            self.assertIn('id="s-APULL"', page)
            self.assertNotIn('id="s-BBASE"', page)
            self.assertIn("Promoter ne stake becha", page)
            self.assertIn("Check ke baad hataye gaye", page)
            self.assertIn('id="r-ECRASH"', page)

    def test_quantity_rules(self):
        plan = {"entry": 500.0, "risk_per_share": 10.0, "risk_pct": 1.0, "max_position_pct": 25}
        self.assertEqual(build_report.size_for(plan, 100000)["qty"], 50)
        capped = build_report.size_for({**plan, "risk_per_share": 2.0}, 100000)
        self.assertEqual(capped["qty"], 50)
        self.assertTrue(capped["capped"])

    def test_empty_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, saved = scan(tmp, nifty_trend=-0.25)
            page = build_report.build(saved, None, 100000)
            self.assertIn("aaj koi naya long trade nahi", page)


if __name__ == "__main__":
    unittest.main()
