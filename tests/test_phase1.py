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

# Import app.py for the stale-protection tests without triggering its live
# MLB startup data pull.
os.environ["DIAMOND_NO_STARTUP"] = "1"

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


# ── P2.1  push + market blend must form ONE valid distribution ─────────
class TestPushBlendDistribution(unittest.TestCase):
    def _model_dist(self):
        # integer line with real push mass
        return nfl_model._ou_probs(74.0, 74.0, 12.0)   # (over, push, under)

    def test_old_approach_overshoots_one(self):
        # Document the bug: blending the two win-probs but keeping the RAW push
        # leaves total mass > 1 on an integer line.
        p_over, p_push, p_under = self._model_dist()
        self.assertGreater(p_push, 0.0)
        a = nfl_model.EDGE_ANCHOR
        fair_over = 0.5
        blended_over = a * p_over + (1 - a) * fair_over
        blended_under = a * p_under + (1 - a) * (1 - fair_over)
        old_total = blended_over + blended_under + p_push      # the pre-fix math
        self.assertGreater(old_total, 1.0 + 1e-9)              # excess probability mass

    def test_blend_ou_sums_to_one(self):
        p_over, p_push, p_under = self._model_dist()
        for fair in (0.5, 0.42, 0.6, None):
            bo, bp, bu = nfl_model._blend_ou(p_over, p_push, p_under, fair, nfl_model.EDGE_ANCHOR)
            self.assertAlmostEqual(bo + bp + bu, 1.0, places=9,
                                   msg="blended over+push+under must sum to 1 (fair=%s)" % fair)
            for v in (bo, bp, bu):
                self.assertGreaterEqual(v, -1e-9)

    def test_halfpoint_blend_has_no_push(self):
        po, pp, pu = nfl_model._ou_probs(80.0, 75.5, 12.0)
        bo, bp, bu = nfl_model._blend_ou(po, pp, pu, 0.5, nfl_model.EDGE_ANCHOR)
        self.assertAlmostEqual(bp, 0.0)
        self.assertAlmostEqual(bo + bu, 1.0, places=9)

    def test_edge_ev_uses_valid_distribution(self):
        # An edge's stored (prob, push) must leave a non-negative loss mass and
        # EV must equal prob*(dec-1) - (1 - prob - push).
        offer = {"book": "DK", "line": 74.0, "over_price": -110, "under_price": -110,
                 "ts": "2026-10-11T01:00:00Z"}
        ln = {"offers": [offer, dict(offer, book="FD")], "books": 2, "fair_over": 0.5}
        players = [{"name": "RB One", "pos": "RB", "team": "DAL", "opp": "PHI",
                    "playable": True, "proj_rush_yds": 74.0}]
        nfl_model.compute_edges(players, {"rb one": {"rush_yds": ln}})
        e = players[0].get("edge", {}).get("rush_yds")
        self.assertIsNotNone(e)
        prob, push = e["prob"], e["push"]
        lose = 1 - prob - push
        self.assertGreaterEqual(lose, -1e-9)
        self.assertLessEqual(prob + push, 1.0 + 1e-9)
        dec = nfl_model._dec(e["price"])
        # EV matches the valid-distribution formula (tolerance covers the stored
        # ev_pct being rounded to 1 dp vs prob/push rounded to 4 dp)
        self.assertAlmostEqual(e["ev_pct"] / 100.0, prob * (dec - 1) - lose, delta=0.005)


# ── P2.2  stale-snapshot protection ────────────────────────────────────
import app   # noqa: E402

def _fresh_snapshot():
    import time as _t
    return {"ts": _t.time(), "today": "2026-10-11",
            "games": [{"status": "Sun 1:00 PM"}],
            "players": [{"name": "x"}],
            "edges": [{"name": "x", "ev_pct": 9.0}]}

class TestStaleProtection(unittest.TestCase):
    def test_unavailable_when_no_players(self):
        st = app._data_status({"games": [], "players": []}, "nfl")
        self.assertFalse(st["available"])
        self.assertTrue(st["stale"])

    def test_missing_timestamp_is_stale(self):
        d = _fresh_snapshot(); d["ts"] = None
        st = app._data_status(d, "nfl")
        self.assertTrue(st["stale"])
        self.assertIn("missing snapshot timestamp", st["reasons"])

    def test_old_timestamp_is_stale(self):
        import time as _t
        d = _fresh_snapshot(); d["ts"] = _t.time() - 1000 * 3600   # ~41 days old
        st = app._data_status(d, "nfl")
        self.assertTrue(st["stale"])

    def test_all_games_final_is_stale(self):
        d = _fresh_snapshot()
        d["games"] = [{"status": "Final"}, {"status": "Final/OT"}]
        st = app._data_status(d, "nfl")
        self.assertTrue(st["stale"])
        self.assertEqual(st["upcoming"], 0)

    def test_fresh_snapshot_is_ok(self):
        st = app._data_status(_fresh_snapshot(), "nfl")
        self.assertTrue(st["available"])
        self.assertFalse(st["stale"])

    def test_stale_withholds_edges(self):
        d = _fresh_snapshot(); d["ts"] = None    # stale
        st = app._data_status(d, "nfl")
        app._guard_recommendations(d, st)
        self.assertEqual(d["edges"], [])          # betting recs withheld
        self.assertEqual(d.get("edges_withheld"), 1)

    def test_fresh_keeps_edges(self):
        d = _fresh_snapshot()
        st = app._data_status(d, "nfl")
        app._guard_recommendations(d, st)
        self.assertEqual(len(d["edges"]), 1)
        self.assertIsNone(d.get("edges_withheld"))


# ── P2.3  tz fallback: exact DST transition hours ──────────────────────
class TestTZFallbackTransitions(unittest.TestCase):
    def setUp(self):
        self.et = tzutil._USEastern()

    def test_fall_back_2026(self):
        # 1st Sunday of Nov 2026 = Nov 1; transition at 02:00 local
        before = dt.datetime(2026, 11, 1, 1, 30)   # still EDT
        after  = dt.datetime(2026, 11, 1, 2, 30)   # now EST
        self.assertEqual(self.et.utcoffset(before), dt.timedelta(hours=-4))
        self.assertEqual(self.et.utcoffset(after),  dt.timedelta(hours=-5))

    def test_spring_forward_2026(self):
        # 2nd Sunday of March 2026 = Mar 8; transition at 02:00 local
        before = dt.datetime(2026, 3, 8, 1, 30)    # still EST
        after  = dt.datetime(2026, 3, 8, 3, 30)    # now EDT
        self.assertEqual(self.et.utcoffset(before), dt.timedelta(hours=-5))
        self.assertEqual(self.et.utcoffset(after),  dt.timedelta(hours=-4))

    def test_names(self):
        self.assertEqual(self.et.tzname(dt.datetime(2026, 7, 1, 12)), "EDT")
        self.assertEqual(self.et.tzname(dt.datetime(2026, 12, 1, 12)), "EST")


if __name__ == "__main__":
    unittest.main(verbosity=2)
