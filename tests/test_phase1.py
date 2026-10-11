#!/usr/bin/env python3
"""
Phase 1 regression tests — correctness & reliability fixes.

Run from the repo root:   python -m unittest tests/test_phase1.py -v

These are pure/offline (no network): they import the models and the odds
aggregator and exercise the exact bugs the review flagged, each written so it
would FAIL against the pre-fix code and PASS after the fix.
"""
import os
import sys
import math
import datetime as dt
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nfl_model
import nba_model
import odds
import tzutil


# ── P1.2  _blend: a real 0 must count; None means missing ──────────────
class TestBlendZeroVsMissing(unittest.TestCase):
    def test_nfl_zero_counts(self):
        # last-3 of 0 is a real cold streak — it must pull the blend down,
        # not be dropped as if missing.
        blended = nfl_model._blend(50.0, 0.0)
        expected = nfl_model.W_SEASON * 50.0 + nfl_model.W_L3 * 0.0
        self.assertAlmostEqual(blended, expected)
        self.assertNotAlmostEqual(blended, 50.0)   # the old bug returned 50.0

    def test_nfl_none_is_missing(self):
        self.assertEqual(nfl_model._blend(50.0, None), 50.0)
        self.assertEqual(nfl_model._blend(None, 42.0), 42.0)
        self.assertIsNone(nfl_model._blend(None, None))

    def test_nba_zero_counts(self):
        gp = nba_model.MIN_GP_FOR_L5 + 2          # enough games to trust L5
        blended = nba_model._blend(20.0, 0.0, gp)
        expected = nba_model.W_SEASON * 20.0 + nba_model.W_L5 * 0.0
        self.assertAlmostEqual(blended, expected)
        self.assertNotAlmostEqual(blended, 20.0)

    def test_nba_gp_guard_preserved(self):
        # too few games -> still fall back to season (heuristic kept)
        self.assertEqual(nba_model._blend(20.0, 5.0, 1), 20.0)


# ── P1.3  timezone: real America/New_York, DST-aware ───────────────────
class TestTimezoneDST(unittest.TestCase):
    def test_edt_in_summer(self):
        off = tzutil.ET.utcoffset(dt.datetime(2026, 7, 15, 12, 0))
        self.assertEqual(off, dt.timedelta(hours=-4))

    def test_est_after_dst_ends(self):
        # DST ends first Sunday of Nov (Nov 1, 2026) -> should be EST (-5)
        off = tzutil.ET.utcoffset(dt.datetime(2026, 11, 15, 12, 0))
        self.assertEqual(off, dt.timedelta(hours=-5))

    def test_data_layers_use_it(self):
        import nfl, nba
        self.assertIs(nfl.ET, tzutil.ET)
        self.assertIs(nba.ET, tzutil.ET)


# ── P1.5  odds math: conversions, de-vig, push, EV ─────────────────────
class TestOddsMath(unittest.TestCase):
    def test_american_to_decimal(self):
        self.assertAlmostEqual(nfl_model._dec(100), 2.0)
        self.assertAlmostEqual(nfl_model._dec(150), 2.5)
        self.assertAlmostEqual(nfl_model._dec(-200), 1.5)
        self.assertIsNone(nfl_model._dec(None))

    def test_implied(self):
        self.assertAlmostEqual(nfl_model._implied(-110), 110 / 210, places=4)
        self.assertAlmostEqual(nfl_model._implied(100), 0.5, places=4)

    def test_devig_sums_to_one(self):
        a = nfl_model._devig2(-110, -110)
        self.assertAlmostEqual(a, 0.5, places=4)
        # two sides of a de-vigged market sum to 1
        over = nfl_model._devig2(-140, 120)
        under = nfl_model._devig2(120, -140)
        self.assertAlmostEqual(over + under, 1.0, places=4)

    def test_halfpoint_has_no_push(self):
        po, pp, pu = nfl_model._ou_probs(80.0, 75.5, 12.0)
        self.assertEqual(pp, 0.0)
        self.assertAlmostEqual(po + pu, 1.0, places=6)

    def test_integer_line_has_push(self):
        po, pp, pu = nfl_model._ou_probs(75.0, 75.0, 12.0)
        self.assertGreater(pp, 0.0)
        self.assertAlmostEqual(po + pp + pu, 1.0, places=6)

    def test_ev_counts_push_as_no_loss(self):
        # win 50%, push 10%, lose 40% at +100 -> 0.5*1 - 0.4 = 0.10
        self.assertAlmostEqual(nfl_model._ev(0.5, 0.1, 100), 0.10, places=6)
        # a pure push (stake back) is zero EV, not a loss
        self.assertAlmostEqual(nfl_model._ev(0.0, 1.0, -110), 0.0, places=6)


# ── P1.1  odds matching: EV uses a REAL single-book offer ──────────────
def _two_book_event():
    """DraftKings posts 74.5 (-110/-110); FanDuel posts 78.5 (+100/-130).
    The consensus line (76.5) exists at NEITHER book."""
    return {
        "id": "evt_test", "home_team": "Dallas Cowboys", "away_team": "Tampa Bay Buccaneers",
        "bookmakers": [
            {"title": "DraftKings", "markets": [
                {"key": "player_reception_yds", "last_update": "2026-10-11T01:00:00Z",
                 "outcomes": [
                     {"name": "Over",  "description": "CeeDee Lamb", "point": 74.5, "price": -110},
                     {"name": "Under", "description": "CeeDee Lamb", "point": 74.5, "price": -110}]}]},
            {"title": "FanDuel", "markets": [
                {"key": "player_reception_yds", "last_update": "2026-10-11T01:05:00Z",
                 "outcomes": [
                     {"name": "Over",  "description": "CeeDee Lamb", "point": 78.5, "price": 100},
                     {"name": "Under", "description": "CeeDee Lamb", "point": 78.5, "price": -130}]}]},
        ],
    }


class TestOddsMatching(unittest.TestCase):
    def setUp(self):
        self.lines = {}
        odds._aggregate_event(_two_book_event(), self.lines)
        self.rec = self.lines["ceedee lamb"]["rec_yds"]

    def test_offers_are_concrete(self):
        offers = self.rec["offers"]
        self.assertEqual(len(offers), 2)
        books = {o["book"] for o in offers}
        self.assertEqual(books, {"DraftKings", "FanDuel"})
        # each offer keeps its own line + both prices + timestamp together
        dk = next(o for o in offers if o["book"] == "DraftKings")
        self.assertEqual((dk["line"], dk["over_price"], dk["under_price"]), (74.5, -110, -110))
        self.assertTrue(dk["ts"])

    def test_edge_uses_a_real_offer_not_a_synthetic_mix(self):
        # moderate projection -> a real, in-range edge on one side
        players = [{"name": "CeeDee Lamb", "pos": "WR", "team": "DAL", "opp": "TB",
                    "playable": True, "proj_rec_yds": 90.0}]
        nfl_model.compute_edges(players, {"ceedee lamb": {"rec_yds": self.rec}})
        e = players[0]["edge"]["rec_yds"]
        # the posted line must be one a book ACTUALLY offered, never the 76.5 consensus
        self.assertIn(e["line"], (74.5, 78.5))
        self.assertNotEqual(e["line"], 76.5)
        # the price must belong to the SAME offer as that line
        offer = next(o for o in self.rec["offers"] if o["line"] == e["line"])
        posted = offer["over_price"] if e["side"] == "Over" else offer["under_price"]
        self.assertEqual(e["price"], posted)
        self.assertIn(e["book"], ("DraftKings", "FanDuel"))
        self.assertTrue(e.get("ts"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
