#!/usr/bin/env python3
"""Phase 2C tests — hardened immutable prediction recorder. Offline/pure."""
import os
import sys
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import record_predictions as rp

NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)   # fixed "now" for tests


def _snapshot(start="2026-10-11T17:00Z", status="Sun 1:00 PM EDT", ts=None, offer_ts="2026-10-11T13:30Z"):
    if ts is None:
        ts = NOW.timestamp() - 3600            # 1h old by default (fresh)
    return {
        "ts": ts, "season": 2026, "today": "2026-10-11",
        "games": [{"game_id": "401772510", "home_nflv": "DAL", "away_nflv": "PHI",
                   "start": start, "status": status}],
        "players": [
            {"id": "00-0036322", "name": "CeeDee Lamb", "team": "DAL", "opp": "PHI",
             "home": True, "playable": True, "proj_rec_yds": 92.0, "anytime_td": 48.0,
             "edge": {"rec_yds": {"book": "DK", "side": "Under", "line": 84.5,
                                  "price": -110, "prob": 0.58, "ev_pct": 7.2,
                                  "ts": offer_ts}}},
            {"id": "00-0000001", "name": "Benched Guy", "team": "DAL", "opp": "PHI",
             "playable": False, "proj_rec_yds": 10.0},   # OUT
        ],
    }


class TestValidity(unittest.TestCase):
    def test_fresh_pregame_valid(self):
        ok, reasons = rp.validate_snapshot(_snapshot(), NOW)
        self.assertTrue(ok, reasons)

    def test_missing_timestamp_rejected(self):
        d = _snapshot(); d["ts"] = None
        ok, reasons = rp.validate_snapshot(d, NOW)
        self.assertFalse(ok)
        self.assertIn("missing snapshot timestamp", reasons)

    def test_stale_snapshot_rejected(self):
        d = _snapshot(ts=NOW.timestamp() - 40 * 3600)   # 40h old
        ok, reasons = rp.validate_snapshot(d, NOW)
        self.assertFalse(ok)

    def test_all_games_started_rejected(self):
        d = _snapshot(start="2026-10-11T13:00Z")        # already kicked off (before NOW)
        ok, reasons = rp.validate_snapshot(d, NOW)
        self.assertFalse(ok)
        self.assertIn("no upcoming (pregame) games in snapshot", reasons)


class TestBuildRecords(unittest.TestCase):
    def test_pregame_only_and_fields(self):
        recs = rp.build_records(_snapshot(), "nfl-test", NOW)
        self.assertTrue(recs)
        self.assertTrue(all(r["player"] == "CeeDee Lamb" for r in recs))   # OUT skipped
        rec = next(r for r in recs if r["prop"] == "rec_yds")
        self.assertEqual(rec["player_id"], "00-0036322")
        self.assertEqual(rec["game_id"], "401772510")
        self.assertEqual(rec["side"], "Under")
        self.assertEqual(rec["line"], 84.5)
        self.assertEqual(rec["price"], -110)
        self.assertEqual(rec["projection"], 92.0)
        self.assertTrue(rec["data_as_of"].endswith("Z"))
        self.assertEqual(len(rec["hash"]), 16)

    def test_games_in_progress_excluded(self):
        recs = rp.build_records(_snapshot(start="2026-10-11T13:00Z"), "v", NOW)  # started
        self.assertEqual(recs, [])

    def test_expired_offer_dropped_projection_kept(self):
        # offer timestamp 20h before NOW -> expired; projection still recorded
        recs = rp.build_records(_snapshot(offer_ts="2026-10-10T18:00Z"), "v", NOW)
        rec = next(r for r in recs if r["prop"] == "rec_yds")
        self.assertEqual(rec["projection"], 92.0)
        self.assertIsNone(rec["price"])
        self.assertTrue(rec.get("offer_expired"))

    def test_missing_odds_graceful(self):
        d = _snapshot()
        d["players"][0]["edge"] = {}       # no sportsbook data
        recs = rp.build_records(d, "v", NOW)
        rec = next(r for r in recs if r["prop"] == "rec_yds")
        self.assertEqual(rec["projection"], 92.0)
        self.assertIsNone(rec["price"])


class TestImmutableAppend(unittest.TestCase):
    def test_idempotent_same_snapshot(self):
        recs = rp.build_records(_snapshot(), "v", NOW)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            a1, _ = rp.append_records(recs, path)
            a2, s2 = rp.append_records(recs, path)      # same snapshot again
            self.assertGreater(a1, 0)
            self.assertEqual(a2, 0)
            with open(path) as f:
                self.assertEqual(sum(1 for _ in f), a1)

    def test_updated_snapshot_appends(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            rp.append_records(rp.build_records(_snapshot(), "v", NOW), path)
            s2 = _snapshot(); s2["ts"] = NOW.timestamp() - 600   # a later pull
            later = NOW + timedelta(minutes=30)
            a, _ = rp.append_records(rp.build_records(s2, "v", later), path)
            self.assertGreater(a, 0)                     # legitimate update recorded
            self.assertTrue(os.path.exists(path + ".bak"))   # backup written


if __name__ == "__main__":
    unittest.main(verbosity=2)
