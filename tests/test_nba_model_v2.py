#!/usr/bin/env python3
"""Phase 3B — candidate NBA v2 model. Pure/offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_model_v2 as v2


def _player(**kw):
    base = {"name": "P", "pos": "G", "status": "", "mpg": 30.0, "min_l5": 30.0,
            "ppg": 20.0, "ppg_l5": 20.0, "rpg": 5.0, "rpg_l5": 5.0,
            "apg": 4.0, "apg_l5": 4.0, "tpm": 2.0}
    base.update(kw)
    return base


class TestMinutes(unittest.TestCase):
    def test_ewma_uses_recent_series(self):
        # rising minutes -> EWMA above the flat season mean
        p = _player(mpg=25.0, min_l5=25.0, recent_min=[20, 24, 28, 32, 36])
        v2.project_player_v2(p)
        self.assertGreater(p["proj_min_v2"], 28.0)

    def test_fallback_without_recent(self):
        p = _player(mpg=30.0, min_l5=20.0)        # no recent_min
        v2.project_player_v2(p)
        self.assertIsNotNone(p["proj_min_v2"])
        # recent-leaning fallback (0.4 season / 0.6 recent) -> below the season 30
        self.assertLess(p["proj_min_v2"], 30.0)

    def test_ewma_newest_weighted(self):
        self.assertGreater(v2._ewma([10, 20, 30, 40, 50]), 30.0)


class TestProjection(unittest.TestCase):
    def test_outputs_valid_and_pra_is_sum(self):
        p = _player(recent_min=[30, 31, 29, 30, 32])
        v2.project_player_v2(p)
        for k in ("proj_min_v2", "proj_pts_v2", "proj_reb_v2", "proj_ast_v2",
                  "proj_tpm_v2", "proj_pra_v2"):
            self.assertIsNotNone(p[k])
        self.assertAlmostEqual(
            p["proj_pra_v2"], round(p["proj_pts_v2"] + p["proj_reb_v2"] + p["proj_ast_v2"], 1), places=1)

    def test_neutral_matchup_and_version(self):
        p = _player(recent_min=[30, 30, 30])
        v2.project_player_v2(p)
        self.assertEqual(p["v2_matchup"], "neutral")
        self.assertEqual(p["model_version_v2"], v2.VERSION)

    def test_out_player_zeroed(self):
        p = _player(status="Out", recent_min=[30, 30, 30])
        v2.project_player_v2(p)
        self.assertEqual(p["proj_pts_v2"], 0.0)
        self.assertEqual(p["proj_min_v2"], 0.0)

    def test_reb_weight_differs_from_pts(self):
        # rate weights: pts 0.60 season, reb 0.80 season — so when season and L5
        # rates differ, v2 weights them differently
        self.assertEqual(v2.RATE_WEIGHT["pts"], 0.60)
        self.assertEqual(v2.RATE_WEIGHT["reb"], 0.80)


if __name__ == "__main__":
    unittest.main(verbosity=2)
