#!/usr/bin/env python3
"""Phase 3B — nba_validate.py integrity checks. Offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_validate as v

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                   "nba_history_fixture.csv")


class TestValidate(unittest.TestCase):
    def test_clean_fixture(self):
        rep = v.validate(v.load_csv(FIX))
        self.assertEqual(rep["duplicates"], 0)
        self.assertEqual(rep["missing"]["player_id"], 0)
        self.assertIn("REG", rep["season_types"])
        self.assertIn("PRE", rep["season_types"])
        self.assertIn("POST", rep["season_types"])
        self.assertGreater(rep["dnp"], 0)            # fixture has DNP rows

    def test_detects_duplicate(self):
        rows = [
            {"player_id": "A", "season": "2025", "season_type": "REG",
             "game_date": "2025-01-05", "opponent": "LAL", "min": "30",
             "pts": "20", "reb": "5", "ast": "4", "tpm": "2"},
            {"player_id": "A", "season": "2025", "season_type": "REG",
             "game_date": "2025-01-05", "opponent": "LAL", "min": "31",
             "pts": "22", "reb": "5", "ast": "4", "tpm": "2"},
        ]
        rep = v.validate(rows)
        self.assertEqual(rep["duplicates"], 1)
        self.assertTrue(any("duplicate" in f for f in rep["flags"]))

    def test_flags_out_of_window(self):
        rows = [{"player_id": "A", "season": "2025", "season_type": "REG",
                 "game_date": "2023-01-05", "opponent": "LAL", "min": "30",
                 "pts": "20", "reb": "5", "ast": "4", "tpm": "2"}]
        rep = v.validate(rows)
        self.assertTrue(any("out-of-window" in f or "window" in f for f in rep["flags"]))

    def test_83pt_game_not_flagged(self):
        # Bam Adebayo's real 83-point game must NOT be flagged as an outlier
        rows = [{"player_id": "bam", "season": "2026", "season_type": "REG",
                 "game_date": "2026-03-10", "opponent": "WSH", "min": "42",
                 "pts": "83", "reb": "9", "ast": "3", "tpm": "7"}]
        rep = v.validate(rows)
        self.assertNotIn("pts", rep["stat_outliers"])
        # but an impossible 101 still flags
        rows[0]["pts"] = "101"
        self.assertIn("pts", v.validate(rows)["stat_outliers"])

    def test_flags_outliers_and_missing(self):
        rows = [{"player_id": "", "season": "2025", "season_type": "REG",
                 "game_date": "2025-01-05", "opponent": "", "min": "99",
                 "pts": "200", "reb": "5", "ast": "4", "tpm": "2"}]
        rep = v.validate(rows)
        self.assertEqual(rep["missing"]["player_id"], 1)
        self.assertEqual(rep["missing"]["opponent"], 1)
        self.assertIn("pts", rep["stat_outliers"])
        self.assertIn("min", rep["stat_outliers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
