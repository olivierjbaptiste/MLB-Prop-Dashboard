#!/usr/bin/env python3
"""Phase 2D tests — candidate model v2 probability layer. Offline/pure."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import model_v2 as v2


class TestDistributionValidity(unittest.TestCase):
    def test_sums_to_one_halfpoint(self):
        for dist in ("empirical", "lognormal", "normal_fitted"):
            o, p, u = v2.over_under_push("rec_yds", 70.0, 64.5, dist)
            self.assertAlmostEqual(o + p + u, 1.0, places=9)
            self.assertAlmostEqual(p, 0.0, places=9)        # half-point: no push
            for v in (o, u):
                self.assertGreaterEqual(v, 0.0)

    def test_sums_to_one_integer_with_push(self):
        for dist in ("empirical", "lognormal", "normal_fitted"):
            o, p, u = v2.over_under_push("rec", 5.0, 5.0, dist)   # integer line
            self.assertAlmostEqual(o + p + u, 1.0, places=9)
            self.assertGreaterEqual(p, 0.0)

    def test_monotonic_in_line(self):
        # higher line -> lower P(over)
        lo = v2.over_under_push("rush_yds", 60.0, 40.5, "empirical")[0]
        hi = v2.over_under_push("rush_yds", 60.0, 90.5, "empirical")[0]
        self.assertGreater(lo, hi)

    def test_skew_lowers_over_at_projection(self):
        # realized over-rate at the projection is < 0.5 for skewed props
        o, _, _ = v2.over_under_push("rec_yds", 60.0, 60.0, "empirical")
        self.assertLess(o, 0.5)


class TestEV(unittest.TestCase):
    def test_offer_ev_uses_exact_offer(self):
        offer = {"book": "DK", "line": 64.5, "over_price": -110, "under_price": -110,
                 "ts": "2026-10-11T13:00Z"}
        r = v2.offer_ev("rec_yds", 80.0, offer, "empirical")   # proj >> line -> Over value
        self.assertEqual(r["book"], "DK")
        self.assertEqual(r["line"], 64.5)
        self.assertIn(r["side"], ("Over", "Under"))
        self.assertIn(r["price"], (-110,))
        self.assertIn("not a profitability guarantee", r["label"])

    def test_ev_formula(self):
        # p_win 0.6, no push, +100 -> EV = 0.6*1 - 0.4 = 0.20
        self.assertAlmostEqual(v2._ev(0.6, 0.0, 100), 0.20, places=9)
        self.assertAlmostEqual(v2._ev(0.5, 0.1, -110), 0.5 * (100 / 110) - 0.4, places=6)

    def test_valid_distribution_feeds_ev(self):
        offer = {"line": 5.0, "over_price": -120, "under_price": 100}
        r = v2.offer_ev("rec", 5.3, offer, "empirical")
        self.assertAlmostEqual(r["p_over"] + r["p_push"] + r["p_under"], 1.0, places=6)


class TestThresholdValidity(unittest.TestCase):
    def test_bounds_and_sum_across_lines(self):
        for prop in ("pass_yds", "rush_yds", "rec_yds", "rec"):
            proj = 60.0
            for line in [l / 2.0 for l in range(0, 400)]:   # 0 .. 199.5
                dp = v2.over_under_push(prop, proj, line, "empirical")
                if dp is None:
                    continue
                o, p, u = dp
                for v in (o, p, u):
                    self.assertTrue(0.0 <= v <= 1.0)
                self.assertAlmostEqual(o + p + u, 1.0, places=9)

    def test_p_over_decreases_p_under_increases(self):
        prev_over, prev_under = None, None
        for line in [20.5, 40.5, 60.5, 80.5, 100.5, 120.5]:
            o, _, u = v2.over_under_push("rec_yds", 60.0, line, "empirical")
            if prev_over is not None:
                self.assertLessEqual(o, prev_over + 1e-9)       # P(over) non-increasing
                self.assertGreaterEqual(u, prev_under - 1e-9)   # P(under) non-decreasing
            prev_over, prev_under = o, u

    def test_integer_push_nonnegative(self):
        o, p, u = v2.over_under_push("rec", 5.0, 5.0, "empirical")   # integer line
        self.assertGreater(p, 0.0)
        self.assertAlmostEqual(o + p + u, 1.0, places=9)

    def test_unrealistic_lines_no_error(self):
        # zero, negative, absurdly high, tiny projection — must not raise
        self.assertIsNotNone(v2.over_under_push("rush_yds", 50.0, 0.0, "empirical"))
        self.assertIsNotNone(v2.over_under_push("rush_yds", 50.0, 9999.0, "empirical"))
        self.assertIsNone(v2.over_under_push("rush_yds", 0.0, 50.0, "empirical"))   # proj<=0
        # negative line → everything is "over"
        o, p, u = v2.over_under_push("rush_yds", 50.0, -10.0, "empirical")
        self.assertAlmostEqual(o, 1.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
