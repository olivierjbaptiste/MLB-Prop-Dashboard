#!/usr/bin/env python3
"""Phase 3D — NBA grading + full pipeline. Pure/offline.
snapshot -> v1/v2 projections -> calibrated probs -> exact-offer EV -> immutable
log -> actual results -> grading report."""
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
import nba_record as R
import nba_grade as G

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
        "team_defense": {}, "players": players,
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


# actual game results for the logged game (player_id, game_id)
ACTUALS = {("00-STAR", "401900001"): {"pts": 30.0, "reb": 5.0, "ast": 6.0,
                                      "tpm": 3.0, "min": 36.0, "pra": 41.0}}


class TestRecorderFields(unittest.TestCase):
    def test_required_fields_present(self):
        d = _projected()
        recs = R.build_records(d, NOW, _params())
        v2 = [r for r in recs if r["model_version"] == nba_model_v2.VERSION and r.get("line")]
        self.assertTrue(v2)
        r = v2[0]
        for k in ("player", "player_id", "event_id", "game_start", "game_date",
                  "projection", "p_over", "p_push", "p_under", "book", "side", "line",
                  "price", "price_over", "price_under", "ev_pct", "captured_at",
                  "data_as_of", "model_version", "calibration_version"):
            self.assertIn(k, r)

    def test_v1_has_no_fabricated_probability(self):
        d = _projected()
        recs = R.build_records(d, NOW, _params())
        v1 = [r for r in recs if r["model_version"] != nba_model_v2.VERSION]
        self.assertTrue(v1)
        self.assertTrue(all("p_over" not in r for r in v1))   # v1 = point projection only


class TestGrading(unittest.TestCase):
    def _log(self, tdir):
        d = _projected()
        recs = R.build_records(d, NOW, _params())
        path = os.path.join(tdir, "nba_log.jsonl")
        R.append_records(recs, path)
        return path, recs

    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tdir:
            path, recs = self._log(tdir)
            rep = G.grade(G.load_log(path), ACTUALS)
            self.assertIn("pts", rep["by_prop"])
            pts = rep["by_prop"]["pts"]
            # both models graded on projection
            self.assertIsNotNone(pts["v1_projection"]["mae"])
            self.assertIsNotNone(pts["v2_projection"]["mae"])
            # v2 probability graded vs the RECORDED line (25.5), actual 30 -> over hit
            self.assertIsNotNone(pts["v2_prob_vs_recorded_line"]["brier"])
            self.assertEqual(pts["v2_paper_bets"]["record"]["win"]
                             + pts["v2_paper_bets"]["record"]["loss"]
                             + pts["v2_paper_bets"]["record"]["push"], 1)

    def test_roi_flagged_insufficient(self):
        with tempfile.TemporaryDirectory() as tdir:
            path, _ = self._log(tdir)
            rep = G.grade(G.load_log(path), ACTUALS)
            for prop, b in rep["by_prop"].items():
                self.assertIn("insufficient sample", b["v2_paper_bets"]["note"])

    def test_missing_actual_skipped(self):
        with tempfile.TemporaryDirectory() as tdir:
            path, _ = self._log(tdir)
            rep = G.grade(G.load_log(path), {})      # no actuals
            self.assertEqual(rep["by_prop"], {})

    def test_grading_never_mutates_log(self):
        with tempfile.TemporaryDirectory() as tdir:
            path, _ = self._log(tdir)
            with open(path, "rb") as fh:
                before = fh.read()
            G.grade(G.load_log(path), ACTUALS)
            with open(path, "rb") as fh:
                after = fh.read()
            self.assertEqual(before, after)

    def test_v1_graded_projection_only(self):
        # v1 block has no probability metrics anywhere (not fabricated)
        with tempfile.TemporaryDirectory() as tdir:
            path, _ = self._log(tdir)
            rep = G.grade(G.load_log(path), ACTUALS)
            # the prob block belongs to v2 only; v1 appears only under v1_projection
            self.assertIn("v1_projection", rep["by_prop"]["pts"])
            self.assertNotIn("v1_prob", rep["by_prop"]["pts"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
