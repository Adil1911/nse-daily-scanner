"""Strategy A, B and C on hand-built charts, plus the market regime."""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import synthetic as syn  # noqa: E402
from scanner.indicators import daily_indicators, ret  # noqa: E402
from scanner.regime import compute_regime  # noqa: E402
from scanner.run import load_config  # noqa: E402
from scanner.strategies import Ctx, eval_a, eval_b, eval_c, metrics  # noqa: E402

CFG = load_config()
NIFTY_R3 = ret(syn.nifty()["Close"], 63)


def evaluate(df, cap="Large", **ctx_kw):
    ind = daily_indicators(df)
    ctx = Ctx("X", "X Ltd", "Sector", cap, df, ind, NIFTY_R3, **ctx_kw)
    m = metrics(ctx)
    return {
        "A": eval_a(ctx, m, CFG["strategy_a"]),
        "B": eval_b(ctx, m, CFG["strategy_b"]),
        "C": eval_c(ctx, m, CFG["strategy_c"]),
    }, m


class StrategyA(unittest.TestCase):
    def test_pullback_with_hammer_is_ready(self):
        evs, m = evaluate(syn.stock_a_pullback())
        a = evs["A"]
        self.assertEqual(a["state"], "READY", a["failed"])
        self.assertEqual(a["trigger"], "hammer")
        p = a["plan"]
        self.assertGreater(p["entry"], p["stop"])
        self.assertLessEqual(p["risk_pct"], 7.0)
        self.assertAlmostEqual(p["target1"], p["entry"] + 2 * p["risk_per_share"], delta=0.06)
        self.assertEqual(evs["B"]["state"], "FAIL")
        self.assertEqual(evs["C"]["state"], "FAIL")

    def test_without_trigger_it_waits(self):
        df = syn.stock_a_pullback()
        prev_c = float(df["Close"].iloc[-2])
        # a small red candle that is no trigger: not a hammer, not an inside bar
        syn.set_bar(df, -1, prev_c * 1.004, prev_c * 1.005, prev_c * 0.996, prev_c * 1.001)
        evs, _ = evaluate(df)
        self.assertEqual(evs["A"]["state"], "WAIT", evs["A"]["failed"])
        self.assertTrue(evs["A"]["plan"]["provisional"])

    def test_results_inside_buffer_fails(self):
        evs, _ = evaluate(syn.stock_a_pullback(), results_date=date(2026, 10, 27), results_in=2, results_known=True)
        self.assertEqual(evs["A"]["state"], "FAIL")
        self.assertIn("Agle 5 session mein results nahi", evs["A"]["failed"])

    def test_fno_ban_fails(self):
        evs, _ = evaluate(syn.stock_a_pullback(), fno_ban=True)
        self.assertEqual(evs["A"]["state"], "FAIL")


class StrategyB(unittest.TestCase):
    def test_contracting_base_near_pivot_is_ready(self):
        evs, m = evaluate(syn.stock_b_base(), cap="Mid")
        b = evs["B"]
        self.assertEqual(b["state"], "READY", b["failed"])
        legs = [float(x.strip("−%")) for x in b["metrics"]["contraction"].split(" → ")]
        self.assertEqual(legs, sorted(legs, reverse=True))
        self.assertGreaterEqual(b["plan"]["rr"], 2.5)
        self.assertLessEqual(b["plan"]["risk_pct"], 8.0)
        self.assertEqual(evs["A"]["state"], "FAIL")

    def _breakout(self, close_mult, vol_mult):
        df = syn.stock_b_base()
        evs, _ = evaluate(df.iloc[:-1].copy())
        prev_c = float(df["Close"].iloc[-2])
        pivot = float(df["High"].iloc[-11:-1].max())
        c = max(prev_c, pivot) * close_mult
        vol = float(df["Volume"].iloc[-30:-1].mean()) * vol_mult
        syn.set_bar(df, -1, prev_c, c * 1.003, prev_c * 0.998, c, vol)
        return evaluate(df, cap="Mid")[0]["B"]

    def test_breakout_on_volume(self):
        b = self._breakout(1.005, 3.0)
        self.assertEqual(b["state"], "BROKE_OUT", b["failed"])
        self.assertGreaterEqual(b["plan"]["rr"], 2.5)

    def test_late_breakout_fails_reward_risk(self):
        # 2% above the pivot the measured move no longer pays 2.5R from today's price
        b = self._breakout(1.02, 3.0)
        self.assertEqual(b["state"], "FAIL")
        self.assertIn("Measured move kam se kam 2.5R", b["failed"])

    def test_breakout_on_thin_volume(self):
        self.assertEqual(self._breakout(1.02, 0.8)["state"], "WEAK_BREAKOUT")

    def test_extended_breakout(self):
        self.assertEqual(self._breakout(1.08, 3.0)["state"], "EXTENDED")


class StrategyC(unittest.TestCase):
    def test_oversold_bounce_is_ready(self):
        evs, _ = evaluate(syn.stock_c_bounce())
        c = evs["C"]
        self.assertEqual(c["state"], "READY", c["failed"])
        p = c["plan"]
        self.assertLessEqual(p["risk_pct"], 6.0)
        self.assertAlmostEqual(p["target1"], p["entry"] + 1.5 * p["risk_per_share"], delta=0.06)
        news = [x for x in c["checks"] if x["key"] == "news"][0]
        self.assertEqual(news["status"], "unknown")

    def test_smallcap_is_rejected(self):
        evs, _ = evaluate(syn.stock_c_bounce(), cap="Small")
        self.assertEqual(evs["C"]["state"], "FAIL")

    def test_downtrend_never_qualifies(self):
        evs, _ = evaluate(syn.stock_downtrend())
        self.assertTrue(all(e["state"] == "FAIL" for e in evs.values()))


class Regime(unittest.TestCase):
    breadth = {"above50_pct": 60.0, "above50_pct_5d_ago": 55.0}

    def test_full(self):
        r = compute_regime(syn.nifty(), syn.vix(13), self.breadth)
        self.assertEqual(r["state"], "FULL")
        self.assertEqual(r["modes"], {"A": "full", "B": "full", "C": "low"})

    def test_high_vix_is_caution(self):
        r = compute_regime(syn.nifty(), syn.vix(21), self.breadth)
        self.assertEqual(r["state"], "CAUTION")
        self.assertEqual(r["modes"]["B"], "off")

    def test_below_200_is_no_longs(self):
        r = compute_regime(syn.nifty(-0.25), syn.vix(13), self.breadth)
        self.assertEqual(r["state"], "NO_LONGS")
        self.assertEqual(set(r["modes"].values()), {"off"})

    def test_between_averages_is_half(self):
        c = syn.build_closes([(450, 0.30, 0.002), (30, 0.03, 0.001), (20, -0.06, 0.0)], 19000.0, seed=1)
        df = syn.frame(c, [1.0] * len(c), seed=1, wick=0.002)
        r = compute_regime(df, syn.vix(13), self.breadth)
        self.assertEqual(r["state"], "HALF")
        self.assertEqual(r["modes"], {"A": "half", "B": "off", "C": "prime"})


if __name__ == "__main__":
    unittest.main()
