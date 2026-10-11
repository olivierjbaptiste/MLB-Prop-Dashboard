#!/usr/bin/env python3
"""Phase 3C — NBA v1/v2 comparison + shadow recorder. Pure/offline."""
import os
import sys
import json
import random
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_model
import nba_model_v2
import nba_prob as P
import nba_compare
import nba_record as R

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def _params():
    rng = random.Random(1)
    pairs = {}
    for prop, base in (("pts", 20.), ("reb", 6.), ("ast", 4.), ("tpm", 2.), ("pra", 30.)):
        pairs[prop] = [(max(1., base * rng.uniform(.6, 1.4)),
                        max(0., round(base * rng.gauss(1, .3)))) for _ in range(800)]
    return P.fit(pairs)


def _snapshot():
    players = [{
        "id": "00-STAR", "name": "Star Wing", "team": "DAL", "team_id": "1", "pos": "F",
        "status": "", "gp": 20, "mpg": 34.0, "min_l5": 35.0,
        "ppg": 26.0, "ppg_l5": 28.0, "rpg": 6.0, "rpg_l5": 6.5, "apg": 5.0, "apg_l5": 5.5,
        "tpm": 3.0, "recent_min": [33, 34, 36, 35, 37],
    }]
    return {
        "ts": NOW.timestamp() - 3600, "season": "2027", "today": "2026-10-11",
        "games": [{"game_id": "401900001", "home_id": "1", "away_id": "2",
                   "home_abb": "DAL", "away_abb": "PHX",
                   "start": "2026-10-11T23:30Z", "status": "Sat 7:30 PM ET"}],
        "team_defense": {},
        "players": players,
        "odds": {"star wing": {
            "pts": {"offers": [{"book": "DK", "line": 25.5, "over_price": -110,
                                "under_price": -110, "ts": "2026-10-11T17:30Z"}]},
            "reb": {"offers": [{"book": "FD", "line": 6.5, "over_price": -115,
                                "under_price": -105, "ts": "2026-10-11T17:30Z"}]},
        }},
    }


def _projected():
    d = _snapshot()
    nba_model.project_all(d)
    nba_model_v2.project_all_v2(d)
    return d


class TestCompare(unittest.TestCase):
    def test_rows_have_required_fields(self):
        d = _projected()
        rows = nba_compare.compare(d, _params(), now_iso="2026-10-11T18:00:00Z")
        self.assertTrue(rows)
        r = rows[0]
        for k in ("player", "player_id", "event_id", "prop", "line", "v1_proj", "v2_proj",
                  "p_over", "p_push", "p_under", "ev_pct", "odds", "data_age_hours",
                  "v1_version", "v2_version"):
            self.assertIn(k, r)
        self.assertAlmostEqual(r["p_over"] + r["p_push"] + r["p_under"], 1.0, places=5)

    def test_line_from_real_offer(self):
        d = _projected()
        rows = nba_compare.compare(d, _params())
        pts = next(r for r in rows if r["prop"] == "pts")
        self.assertEqual(pts["line"], 25.5)       # exact offer line preserved


class TestRecorder(unittest.TestCase):
    def test_pregame_v1_and_v2_recorded(self):
        d = _projected()
        recs = R.build_records(d, NOW, _params())
        vers = {r["model_version"] for r in recs}
        self.assertIn(nba_model_v2.VERSION, vers)
        self.assertTrue(any(r["model_version"] != nba_model_v2.VERSION for r in recs))  # v1 too
        # v2 rows carry calibrated probabilities
        v2 = [r for r in recs if r["model_version"] == nba_model_v2.VERSION]
        self.assertTrue(all("p_over" in r for r in v2))

    def test_idempotent_append(self):
        d = _projected()
        recs = R.build_records(d, NOW, _params())
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "log.jsonl")
            a, _ = R.append_records(recs, path)
            self.assertEqual(a, len(recs))
            b, _ = R.append_records(recs, path)
            self.assertEqual(b, 0)                 # no duplicates

    def test_rejects_stale_snapshot(self):
        d = _projected()
        d["ts"] = NOW.timestamp() - 40 * 3600       # 40h old
        ok, reasons = R.validate_snapshot(d, NOW)
        self.assertFalse(ok)

    def test_skips_after_tipoff(self):
        d = _projected()
        d["games"][0]["start"] = "2026-10-11T17:00Z"   # already started vs NOW 18:00
        recs = R.build_records(d, NOW, _params())
        self.assertEqual(recs, [])                  # not pregame → nothing recorded


if __name__ == "__main__":
    unittest.main(verbosity=2)
