#!/usr/bin/env python3
"""Phase 2C tests — matchup point-in-time defense (no leakage). Offline/pure."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matchup_eval as me


def _defense():
    # One defense D allowed WR receiving yards: wk1=200, wk2=100, wk3(=target)=999
    # League (two teams D and E) so a league average exists.
    allowed = {
        ("D", "rec_yds_WR"): {1: 200.0, 2: 100.0, 3: 999.0},
        ("E", "rec_yds_WR"): {1: 100.0, 2: 100.0, 3: 100.0},
    }
    weeks_played = {"D": {1, 2, 3}, "E": {1, 2, 3}}
    league_allowed = {"rec_yds_WR": {1: 300.0, 2: 200.0, 3: 1099.0}}
    def_games_wk = {1: {"D", "E"}, 2: {"D", "E"}, 3: {"D", "E"}}
    return {"allowed": allowed, "weeks_played": weeks_played,
            "league_allowed": league_allowed, "def_games_wk": def_games_wk}


class TestPointInTimeDefense(unittest.TestCase):
    def test_factor_excludes_target_week(self):
        d = _defense()
        # entering week 3, D's prior allowed = (200+100)/2 = 150; league prior =
        # (300+200)/(2+2) = 125; raw = 150/125 = 1.2 -> clamped at +CLAMP.
        fb, fc, n = me.matchup_factor(d, "D", "rec_yds_WR", week=3)
        self.assertEqual(n, 2)                       # only weeks 1,2 counted
        self.assertAlmostEqual(fb, me._clamp(1.2), places=4)
        # the week-3 value (999) must NOT influence the week-3 factor
        d2 = _defense(); d2["allowed"][("D", "rec_yds_WR")][3] = 5.0
        fb2, _, _ = me.matchup_factor(d2, "D", "rec_yds_WR", week=3)
        self.assertAlmostEqual(fb, fb2, places=9)    # identical → no leakage

    def test_insufficient_prior_is_neutral(self):
        d = _defense()
        fb, fc, n = me.matchup_factor(d, "D", "rec_yds_WR", week=2)  # only 1 prior game
        self.assertEqual((fb, fc), (1.0, 1.0))       # below MIN_DEF_GAMES

    def test_shrunk_factor_between_one_and_full(self):
        d = _defense()
        fb, fc, n = me.matchup_factor(d, "D", "rec_yds_WR", week=3)
        # shrink pulls the factor toward 1
        self.assertTrue(min(1.0, fb) <= fc <= max(1.0, fb))
        self.assertNotAlmostEqual(fc, fb)            # shrinkage actually applied

    def test_metric_mapping(self):
        self.assertEqual(me._metric("pass_yds", "QB"), "pass_yds")
        self.assertIsNone(me._metric("pass_yds", "WR"))
        self.assertEqual(me._metric("rec_yds", "TE"), "rec_yds_TE")
        self.assertIsNone(me._metric("rush_yds", "WR"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
