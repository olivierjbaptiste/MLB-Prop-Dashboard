#!/usr/bin/env python3
"""Phase 3A — NBA walk-forward backtest. Pure/offline, injected rows + fixture."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_backtest as bt

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                   "nba_history_fixture.csv")


def _rows(pid, n, mins, pts, reb=4, ast=3, tpm=1, season=2025, st="REG", start_day=1):
    out = []
    for i in range(n):
        out.append({"player_id": pid, "player": "P", "season": season,
                    "season_type": st, "game_date": "2025-01-{:02d}".format(start_day + i),
                    "team": "DAL", "opponent": "PHX", "home": "1", "started": "",
                    "min": mins, "pts": pts, "reb": reb, "ast": ast, "tpm": tpm})
    return out


class TestDataHandling(unittest.TestCase):
    def test_played_excludes_dnp(self):
        self.assertTrue(bt._played({"min": 20}))
        self.assertFalse(bt._played({"min": 0}))
        self.assertFalse(bt._played({"min": None}))

    def test_pra_is_sum(self):
        h = bt.load_history(rows=_rows("A", 1, 30, 20, 5, 4, 2))
        self.assertAlmostEqual(h[0]["pra"], 29.0)

    def test_season_filter_excludes_pre_post(self):
        rows = _rows("A", 5, 30, 20) + \
               [dict(_rows("A", 1, 30, 99, season=2025, st="PRE")[0]),
                dict(_rows("A", 1, 30, 50, season=2025, st="POST")[0])]
        g = bt.player_season_games(bt.load_history(rows=rows), ("REG",))
        self.assertEqual(len(g[("A", 2025)]), 5)     # PRE/POST dropped

    def test_dnp_excluded_from_games(self):
        rows = _rows("A", 5, 30, 20)
        rows.append(dict(rows[0], game_date="2025-02-01", min=0, pts=0))
        g = bt.player_season_games(bt.load_history(rows=rows), ("REG",))
        self.assertEqual(len(g[("A", 2025)]), 5)     # the zero-minute game is out


class TestWalkForward(unittest.TestCase):
    def test_no_leakage_nprior(self):
        recs = bt.walk_forward(bt.load_history(rows=_rows("A", 10, 30, 20)), "pts")
        # first MIN_HISTORY games skipped; nprior == index (prior-only)
        self.assertTrue(all(r["nprior"] >= bt.MIN_HISTORY for r in recs))
        self.assertEqual([r["nprior"] for r in recs],
                         list(range(bt.MIN_HISTORY, 10)))

    def test_constant_player_zero_error(self):
        recs = bt.walk_forward(bt.load_history(rows=_rows("A", 12, 30, 20)), "pts")
        for r in recs:
            self.assertAlmostEqual(r["season"], 20.0, places=6)
            self.assertAlmostEqual(r["model"], 20.0, places=6)
            self.assertAlmostEqual(r["actual"], 20.0, places=6)

    def test_min_floor_eligibility(self):
        # a 6-minute player is below MIN_FLOOR -> no scored targets
        recs = bt.walk_forward(bt.load_history(rows=_rows("A", 10, 6.0, 4)), "pts")
        self.assertEqual(recs, [])

    def test_min_prop_uses_minutes(self):
        recs = bt.walk_forward(bt.load_history(rows=_rows("A", 8, 30, 20)), "min")
        self.assertTrue(recs)
        self.assertAlmostEqual(recs[0]["model"], 30.0, places=6)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.h = bt.load_history(FIX)

    def test_all_props_and_season_baseline_present(self):
        rep = bt.evaluate(self.h)
        for prop in bt.PROPS:
            self.assertIn(prop, rep)
        # the season-average baseline must not be dropped by key collision
        self.assertIn("season", rep["pts"]["methods"])
        self.assertIsNotNone(rep["pts"]["methods"]["season"]["mae"])

    def test_cluster_ci_structure(self):
        rep = bt.evaluate(self.h)
        ci = rep["pts"]["model_vs_best_baseline"]
        self.assertIn("ci95", ci)
        self.assertIn("significant", ci)
        self.assertGreater(ci["n_players"], 0)

    def test_sweep_grid(self):
        sw = bt.sweep_weights(self.h, "pts")
        self.assertIn(0.6, sw)
        self.assertIn(1.0, sw)
        for k, v in sw.items():
            self.assertIn("mae", v)

    def test_minutes_models(self):
        mm = bt.minutes_models(self.h)
        self.assertGreater(mm["n"], 0)
        for m in bt.MIN_METHODS:
            self.assertIn(m, mm["mae"])
        self.assertIn("season", mm["vs_blend"])      # CI vs production blend

    def test_ewma_recency(self):
        # rising series -> EWMA above the plain mean (newer weighted more)
        self.assertGreater(bt._ewma([10, 20, 30, 40, 50]), 30.0)

    def test_downstream_minutes(self):
        dm = bt.downstream_minutes(self.h, "pts")
        self.assertGreater(dm["n"], 0)
        self.assertIn("blend", dm["mae"])
        self.assertIsNone(bt.downstream_minutes(self.h, "min"))   # not a rate prop

    def test_matchup_eval_structure(self):
        m = bt.matchup_eval(self.h, "pts")
        self.assertIsNotNone(m)
        self.assertIn("neutral", m["mae"])
        self.assertIn("raw", m["mae"])
        self.assertIn("regular", m["mae"])
        self.assertGreater(m["n"], 0)
        self.assertIsNone(bt.matchup_eval(self.h, "pra"))   # only rate props


if __name__ == "__main__":
    unittest.main(verbosity=2)
