"""Indicators, candles, data cleaning and the NSE file parsers."""

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scanner import candles  # noqa: E402
from scanner.data import clean_frame, drop_unfinished, split_download  # noqa: E402
from scanner.fmt import indian, shares  # noqa: E402
from scanner.indicators import atr, ema, pullback_legs, rsi, sma, to_monthly, to_weekly, zigzag  # noqa: E402
from scanner.market import Calendar  # noqa: E402
from scanner.nse_extras import parse_event_calendar, parse_fno_ban  # noqa: E402
from scanner.universe import parse_index_csv  # noqa: E402


class Indicators(unittest.TestCase):
    def test_sma_and_ema(self):
        s = pd.Series([1.0, 2, 3, 4, 5])
        self.assertTrue(math.isnan(sma(s, 3).iloc[1]))
        self.assertAlmostEqual(sma(s, 3).iloc[-1], 4.0)
        e = ema(pd.Series([10.0] * 30), 20)
        self.assertAlmostEqual(e.iloc[-1], 10.0)

    def test_rsi_edges(self):
        up = pd.Series(np.arange(1, 40, dtype=float))
        self.assertAlmostEqual(rsi(up, 14).iloc[-1], 100.0)
        flat = pd.Series([5.0] * 40)
        self.assertAlmostEqual(rsi(flat, 14).iloc[-1], 50.0)
        zig = pd.Series([10.0 + (i % 2) for i in range(200)])
        self.assertAlmostEqual(rsi(zig, 14).iloc[-1], 50.0, delta=3.0)

    def test_atr_is_range_for_gapless_bars(self):
        idx = pd.bdate_range("2026-01-01", periods=40)
        df = pd.DataFrame({"Open": 100.0, "High": 102.0, "Low": 98.0, "Close": 100.0, "Volume": 1.0}, index=idx)
        self.assertAlmostEqual(atr(df, 14).iloc[-1], 4.0)

    def test_resample(self):
        idx = pd.bdate_range("2026-01-01", "2026-03-31")
        df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10.0}, index=idx)
        self.assertEqual(len(to_monthly(df)), 3)
        wk = to_weekly(df)
        self.assertTrue((wk.index.dayofweek == 4).all())
        self.assertEqual(wk["Volume"].iloc[1], 50.0)

    def test_zigzag_and_legs(self):
        high = [100, 98, 90, 95, 97, 93, 95, 96]
        low = [99, 92, 86, 91, 95, 92, 94, 95]
        piv = zigzag(high, low, 0.025)
        self.assertEqual([p[2] for p in piv][:4], ["H", "L", "H", "L"])
        legs = pullback_legs(piv)
        self.assertAlmostEqual(legs[0], 0.14, places=2)
        self.assertLess(legs[1], legs[0])


class Candles(unittest.TestCase):
    def test_hammer(self):
        self.assertTrue(candles.hammer(candles.Bar(99.5, 100.3, 97.5, 100.2)))
        self.assertFalse(candles.hammer(candles.Bar(100, 102, 99.5, 101.5)))

    def test_engulfing_piercing_inside(self):
        prev = candles.Bar(102, 102.5, 99.5, 100)
        self.assertTrue(candles.bullish_engulfing(prev, candles.Bar(99.8, 103, 99.5, 102.6)))
        self.assertTrue(candles.piercing(prev, candles.Bar(99.6, 101.6, 99.3, 101.4)))
        self.assertTrue(candles.inside_bar(prev, candles.Bar(100.2, 101, 100, 100.8)))
        found = candles.detect(prev, candles.Bar(99.8, 103, 99.5, 102.6))
        self.assertEqual(candles.first_trigger(found, candles.TRIGGERS_A), "bullish_engulfing")


class Data(unittest.TestCase):
    def _frame(self, n=5, tz=None):
        idx = pd.date_range("2026-10-19", periods=n, freq="D", tz=tz)
        return pd.DataFrame(
            {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Adj Close": 1.4, "Volume": 100}, index=idx
        )

    def test_split_ticker_first(self):
        a, b = self._frame(), self._frame()
        df = pd.concat({"AAA.NS": a, "BBB.NS": b}, axis=1)
        out = split_download(df, ["AAA.NS", "BBB.NS", "CCC.NS"])
        self.assertEqual(sorted(out), ["AAA.NS", "BBB.NS"])
        self.assertEqual(list(out["AAA.NS"].columns), ["Open", "High", "Low", "Close", "Volume"])

    def test_split_field_first(self):
        a = self._frame()
        df = pd.concat({"AAA.NS": a, "BBB.NS": a}, axis=1).swaplevel(0, 1, axis=1)
        out = split_download(df, ["AAA.NS", "BBB.NS"])
        self.assertEqual(sorted(out), ["AAA.NS", "BBB.NS"])

    def test_clean_frame_timezone_and_nan(self):
        df = self._frame(tz="Asia/Kolkata")
        df.iloc[2, df.columns.get_loc("Close")] = np.nan
        out = clean_frame(df)
        self.assertEqual(len(out), 4)
        self.assertIsNone(out.index.tz)

    def test_drop_unfinished(self):
        idx = pd.bdate_range(end="2026-10-23", periods=5)
        df = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=idx)
        during = pd.Timestamp("2026-10-23 11:00")
        self.assertEqual(drop_unfinished(df, during, False).index[-1], pd.Timestamp("2026-10-22"))
        evening = pd.Timestamp("2026-10-23 18:00")
        self.assertEqual(drop_unfinished(df, evening, False).index[-1], pd.Timestamp("2026-10-23"))
        hidx = pd.DatetimeIndex(["2026-10-23 13:15", "2026-10-23 14:15", "2026-10-23 15:15"])
        h = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1.0}, index=hidx)
        self.assertEqual(len(drop_unfinished(h, pd.Timestamp("2026-10-23 15:20"), True)), 2)
        self.assertEqual(len(drop_unfinished(h, pd.Timestamp("2026-10-23 15:31"), True)), 3)


class Calendars(unittest.TestCase):
    def test_sessions(self):
        cal = Calendar(["2026-10-20"])
        self.assertFalse(cal.is_session(pd.Timestamp("2026-10-20").date()))
        self.assertEqual(str(cal.next_session(pd.Timestamp("2026-10-23").date())), "2026-10-26")
        # Fri 16 -> Fri 23 Oct: Mon 19, Wed 21, Thu 22, Fri 23 (Tue 20 is a holiday)
        self.assertEqual(cal.sessions_between(pd.Timestamp("2026-10-16").date(), pd.Timestamp("2026-10-23").date()), 4)


class Parsers(unittest.TestCase):
    def test_index_csv(self):
        text = "﻿Company Name,Industry,Symbol,Series,ISIN Code\nReliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018\nSome Debt,Other,XYZ,N1,INE000\n"
        rows = parse_index_csv(text)
        self.assertEqual([r["symbol"] for r in rows], ["RELIANCE"])
        self.assertEqual(rows[0]["industry"], "Oil Gas & Consumable Fuels")

    def test_fno_ban(self):
        text = "Securities in Ban For Trade Date 26-OCT-2026:,\n1,RBLBANK\n2,SAIL\n"
        syms, d = parse_fno_ban(text)
        self.assertEqual(syms, {"RBLBANK", "SAIL"})
        self.assertEqual(d, "2026-10-26")
        syms, _ = parse_fno_ban("Securities in Ban For Trade Date 26-OCT-2026: NIL")
        self.assertEqual(syms, set())

    def test_event_calendar(self):
        items = [
            {"symbol": "TCS", "purpose": "Financial Results", "bm_desc": "", "date": "05-Nov-2026"},
            {"symbol": "INFY", "purpose": "Dividend", "bm_desc": "Interim dividend", "date": "06-Nov-2026"},
            {"symbol": "HDFCBANK", "purpose": "Board Meeting", "bm_desc": "To consider financial results", "date": "07-NOV-2026"},
        ]
        out = parse_event_calendar(items)
        self.assertEqual(sorted(out), ["HDFCBANK", "TCS"])
        self.assertEqual(str(out["HDFCBANK"][0]), "2026-11-07")


class Formatting(unittest.TestCase):
    def test_indian(self):
        self.assertEqual(indian(1234567.891), "12,34,567.89")
        self.assertEqual(indian(999.5, 0), "1,000")
        self.assertEqual(shares(537151), "5.4 lakh")


if __name__ == "__main__":
    unittest.main()
