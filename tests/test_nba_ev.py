#!/usr/bin/env python3
"""Phase 3C — NBA offer-specific EV. Pure/offline."""
import os
import sys
import random
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_prob as P
import nba_ev

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def _params():
    rng = random.Random(1)
    pairs = [(20 * rng.uniform(0.6, 1.4), max(0, round(rng.gauss(20, 8)))) for _ in range(800)]
    return P.fit(pairs, "pts")


class TestEV(unittest.TestCase):
    def setUp(self):
        self.params = _params()

    def test_uses_exact_offer(self):
        offer = {"player": "Star", "event_id": "401", "book": "DK", "line": 24.5,
                 "over_price": -110, "under_price": -110, "ts": "2026-10-11T17:00Z"}
        r = nba_ev.offer_ev("pts", 30.0, offer, "empirical", self.params, now=NOW)
        self.assertEqual(r["line"], 24.5)
        self.assertEqual(r["book"], "DK")
        self.assertEqual(r["event_id"], "401")
        self.assertIn(r["side"], ("Over", "Under"))
        self.assertIn(r["price"], (-110,))
        self.assertIn("not a verified betting opportunity", r["label"])

    def test_valid_prob_feeds_ev(self):
        offer = {"line": 8, "over_price": -115, "under_price": -105, "ts": "2026-10-11T17:00Z"}
        r = nba_ev.offer_ev("reb", 8.3, offer, "neg_binomial", self.params, now=NOW)
        self.assertAlmostEqual(r["p_over"] + r["p_push"] + r["p_under"], 1.0, places=6)

    def test_stale_offer_rejected(self):
        offer = {"line": 24.5, "over_price": -110, "under_price": -110,
                 "ts": "2026-10-10T00:00Z"}            # >12h old
        self.assertIsNone(nba_ev.offer_ev("pts", 25.0, offer, "empirical", self.params, now=NOW))

    def test_missing_price_rejected(self):
        offer = {"line": 24.5, "over_price": None, "under_price": None, "ts": "2026-10-11T17:00Z"}
        self.assertIsNone(nba_ev.offer_ev("pts", 25.0, offer, "empirical", self.params, now=NOW))

    def test_ev_formula_with_push(self):
        # p_win 0.5, push 0.1 -> p_lose 0.4; +100 -> EV = 0.5 - 0.4 = 0.10
        self.assertAlmostEqual(nba_ev._ev(0.5, 0.1, 100), 0.10, places=9)

    def test_devig_sums_to_one(self):
        fo, fu = nba_ev._devig(-110, -110)
        self.assertAlmostEqual(fo + fu, 1.0, places=9)

    def test_no_line_mixing(self):
        # two different lines must each be evaluated on their own price only
        o1 = {"line": 24.5, "over_price": -110, "under_price": -110, "ts": "2026-10-11T17:00Z"}
        o2 = {"line": 26.5, "over_price": +120, "under_price": -140, "ts": "2026-10-11T17:00Z"}
        r1 = nba_ev.offer_ev("pts", 27.0, o1, "empirical", self.params, now=NOW)
        r2 = nba_ev.offer_ev("pts", 27.0, o2, "empirical", self.params, now=NOW)
        self.assertEqual(r1["line"], 24.5)
        self.assertEqual(r2["line"], 26.5)
        self.assertNotEqual(r1["price"], r2["price"]) if r1["side"] != r2["side"] else None


if __name__ == "__main__":
    unittest.main(verbosity=2)
