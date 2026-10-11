#!/usr/bin/env python3
"""Phase 3B regression tests for the fixed NBA gamelog parser (nba.py).
Pure/offline: a synthetic ESPN-shaped gamelog exercises every confirmed bug."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba


def _ev(eid, mins, pts, reb=5, ast=4, tpm=2):
    # stats order mirrors labels below: MIN, PTS, REB, AST, 3PT
    return {"eventId": eid, "stats": [str(mins), str(pts), str(reb), str(ast), str(tpm)]}


def _gamelog():
    """Regular season (6 played, 1 DNP, 1 no-id DNP, + a duplicate across a
    second category), plus a preseason and a playoff game that must be ignored.
    Events are listed OLDEST-first to prove date sorting, not list order."""
    reg_games = [
        _ev("g1", 30, 10), _ev("g2", 30, 20), _ev("g3", 30, 30),
        _ev("g4", 30, 40), _ev("g5", 30, 50), _ev("g6", 30, 60),
        _ev("dnp", 0, 0),                        # DNP: 0 minutes -> excluded
        {"stats": ["0", "0", "0", "0", "0"]},    # missing eventId + DNP -> no crash
    ]
    dates = {"g1": "2024-11-01", "g2": "2024-11-03", "g3": "2024-11-05",
             "g4": "2024-11-07", "g5": "2024-11-09", "g6": "2024-11-11",
             "dnp": "2024-11-13", "pre": "2024-10-05", "post": "2025-04-20"}
    events = {eid: {"gameDate": d + "T00:00Z", "opponent": {"abbreviation": "LAL"},
                    "homeAway": "home"} for eid, d in dates.items()}
    return {
        "labels": ["MIN", "PTS", "REB", "AST", "3PT"],
        "names": ["minutes", "points", "rebounds", "assists", "threepointfieldgoalsmade"],
        "events": events,
        "seasonTypes": [
            {"displayName": "2024-25 Regular Season",
             "categories": [
                 {"events": reg_games},
                 {"events": [_ev("g3", 30, 30)]},   # duplicate game in 2nd category
             ]},
            {"displayName": "2024 Preseason",
             "categories": [{"events": [_ev("pre", 20, 999)]}]},
            {"displayName": "2025 Postseason",
             "categories": [{"events": [_ev("post", 38, 500)]}]},
        ],
    }


class TestGamelogParse(unittest.TestCase):
    def setUp(self):
        self.games = nba._gamelog_games(_gamelog())

    def test_dedup_by_event(self):
        reg = [g for g in self.games if g["season_type"] == "REG"]
        # 6 played + 1 DNP + 1 no-id DNP = 8; the duplicate g3 is NOT double-counted
        self.assertEqual(len(reg), 8)
        g3s = [g for g in reg if g.get("date") == "2024-11-05"]
        self.assertEqual(len(g3s), 1)

    def test_season_types_parsed(self):
        kinds = {g["season_type"] for g in self.games}
        self.assertEqual(kinds, {"REG", "PRE", "POST"})

    def test_missing_event_id_no_crash(self):
        # the no-id row is present with an empty date, not dropped or raising
        self.assertTrue(any(g["date"] == "" for g in self.games))


class TestSummarize(unittest.TestCase):
    def setUp(self):
        self.s = nba._summarize_reg(nba._gamelog_games(_gamelog()))

    def test_preseason_and_playoffs_excluded(self):
        # 999 (pre) and 500 (post) would blow up the average if included
        self.assertEqual(self.s["ppg"], 35.0)       # mean of 10..60
        self.assertLess(self.s["ppg"], 100)

    def test_dnp_excluded_from_gp_and_avg(self):
        self.assertEqual(self.s["gp"], 6)           # DNP + no-id DNP excluded

    def test_last5_is_most_recent_by_date(self):
        # 5 newest are pts 20,30,40,50,60 -> 40.0 (NOT 10..50 from list order)
        self.assertEqual(self.s["ppg_l5"], 40.0)
        self.assertEqual(self.s["min_l5"], 30.0)

    def test_return_keys_preserved(self):
        for k in ("gp", "ppg", "rpg", "apg", "tpm", "mpg",
                  "ppg_l5", "rpg_l5", "apg_l5", "min_l5"):
            self.assertIn(k, self.s)

    def test_empty_gamelog(self):
        s = nba._summarize_reg([])
        self.assertEqual(s["gp"], 0)
        self.assertIsNone(s["ppg"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
