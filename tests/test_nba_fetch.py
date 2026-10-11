#!/usr/bin/env python3
"""Phase 3C — nba_fetch_history parser fixes: Eastern game dates (UTC +1 shift
corrected), All-Star exhibition games dropped, event id preserved. Offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_fetch_history as fh


def _data():
    # labels/names order: MIN,PTS,REB,AST,3PT
    return {
        "labels": ["MIN", "PTS", "REB", "AST", "3PT"],
        "names": ["minutes", "points", "rebounds", "assists", "threepointfieldgoalsmade"],
        "events": {
            # evening away tip: 00:30Z on the 23rd = 7:30pm ET on the 22nd
            "e1": {"gameDate": "2025-11-23T00:30Z", "opponent": {"abbreviation": "NO"},
                   "team": {"abbreviation": "ATL"}, "homeAway": "away"},
            # afternoon home tip: 18:00Z on the 23rd = 1:00pm ET same day
            "e2": {"gameDate": "2025-11-23T18:00Z", "opponent": {"abbreviation": "CHA"},
                   "team": {"abbreviation": "ATL"}, "homeAway": "home"},
            # All-Star exhibition — must be dropped
            "e3": {"gameDate": "2026-02-15T23:00Z", "opponent": {"abbreviation": "WORLD"},
                   "team": {"abbreviation": "STARS"}, "homeAway": "home"},
        },
        "seasonTypes": [
            {"displayName": "2025-26 Regular Season", "categories": [{"events": [
                {"eventId": "e1", "stats": ["36", "20", "5", "4", "2"]},
                {"eventId": "e2", "stats": ["33", "24", "6", "5", "3"]},
                {"eventId": "e3", "stats": ["20", "30", "3", "8", "6"]},
            ]}]},
        ],
    }


class TestFetchFixes(unittest.TestCase):
    def setUp(self):
        self.rows = fh.parse_gamelog(_data(), "00-X", "Test Player", 2026)
        self.by_opp = {r["opponent"]: r for r in self.rows}

    def test_allstar_dropped(self):
        self.assertNotIn("WORLD", self.by_opp)
        self.assertEqual(len(self.rows), 2)        # e1, e2 only

    def test_eastern_date_corrects_utc_shift(self):
        # evening away game: UTC says the 23rd, true Eastern date is the 22nd
        self.assertEqual(self.by_opp["NO"]["game_date"], "2025-11-22")

    def test_afternoon_date_unchanged(self):
        self.assertEqual(self.by_opp["CHA"]["game_date"], "2025-11-23")

    def test_no_date_collision_after_fix(self):
        dates = {r["game_date"] for r in self.rows}
        self.assertEqual(len(dates), 2)            # 11-22 and 11-23, not collided

    def test_game_id_preserved(self):
        self.assertEqual(self.by_opp["NO"]["game_id"], "e1")
        self.assertEqual(self.by_opp["CHA"]["game_id"], "e2")

    def test_et_date_helper(self):
        self.assertEqual(fh._et_date("2025-11-23T00:30Z"), "2025-11-22")
        self.assertEqual(fh._et_date(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
