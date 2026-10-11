#!/usr/bin/env python3
"""Phase 2E/3 — grading the shadow log against (synthetic) final stats.
Offline/pure: actuals are injected, no network. Confirms both models are graded
on the identical eligible set and that grading never mutates the log."""
import os
import sys
import json
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["DIAMOND_NO_STARTUP"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nfl_model
import model_v2
import record_predictions as rp
import grade_shadow as gs

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nfl_snapshot_fixture.json")
NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)

# Synthetic realized stats for the three fixture players, week 6, 2026.
# Chosen so outcomes are deterministic: Lamb & Javonte go OVER, Dak goes UNDER.
ACTUAL_ROWS = {
    2026: [
        {"player_id": "00-0036322", "season": "2026", "week": "6",
         "receiving_yards": "95", "receptions": "7", "rushing_yards": "0", "passing_yards": "0"},
        {"player_id": "00-0037111", "season": "2026", "week": "6",
         "rushing_yards": "70", "receiving_yards": "0", "receptions": "0", "passing_yards": "0"},
        {"player_id": "00-0035000", "season": "2026", "week": "6",
         "passing_yards": "250", "rushing_yards": "0", "receiving_yards": "0", "receptions": "0"},
    ]
}


def _fetch(season):
    return ACTUAL_ROWS.get(int(season), [])


def _load_snapshot():
    with open(FIX) as f:
        d = json.load(f)
    d["ts"] = NOW.timestamp() - 3600
    nfl_model.project_all(d)
    return d


def _write_log():
    d = _load_snapshot()
    recs = rp.build_records(d, nfl_model.VERSION, NOW) + rp.build_v2_records(d, NOW)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in recs:
        tmp.write(json.dumps(r) + "\n")
    tmp.close()
    return tmp.name, recs


class TestGradingHelpers(unittest.TestCase):
    def test_outcome(self):
        self.assertEqual(gs._outcome(95, 84.5), "over")
        self.assertEqual(gs._outcome(50, 84.5), "under")
        self.assertEqual(gs._outcome(5.0, 5.0), "push")      # integer-line tie
        self.assertIsNone(gs._outcome(None, 84.5))

    def test_decimal(self):
        self.assertAlmostEqual(gs._decimal(100), 1.0, places=9)
        self.assertAlmostEqual(gs._decimal(-110), 100 / 110, places=9)
        self.assertAlmostEqual(gs._decimal(+150), 1.5, places=9)


class TestGrading(unittest.TestCase):
    def test_both_models_graded_same_set(self):
        path, recs = _write_log()
        try:
            actuals = gs.load_actuals([2026], fetch=_fetch)
            rep = gs.grade(recs, actuals)
            self.assertGreater(rep["eligible_observations"], 0)
            # every graded prop has the SAME n for v1 and v2 (identical eligible set)
            for prop, b in rep["by_prop"].items():
                self.assertEqual(b["v1"]["n_obs"], b["v2"]["n_obs"])
                self.assertEqual(b["v1"]["n_obs"], b["n"])
        finally:
            os.unlink(path)

    def test_metrics_and_outcomes(self):
        path, recs = _write_log()
        try:
            rep = gs.grade(recs, gs.load_actuals([2026], fetch=_fetch))
            # rec_yds: Lamb went over 95 vs ~84 line
            rec = rep["by_prop"].get("rec_yds")
            self.assertIsNotNone(rec)
            self.assertIsNotNone(rec["v1"]["brier"])
            self.assertIsNotNone(rec["v2"]["brier"])
            self.assertIsNotNone(rec["v1"]["log_loss"])
            # v1 took Over on Lamb and it hit -> a win on v1's pick
            self.assertEqual(rec["v1"]["bets"]["win"], 1)
        finally:
            os.unlink(path)

    def test_roi_flagged_insufficient(self):
        path, recs = _write_log()
        try:
            rep = gs.grade(recs, gs.load_actuals([2026], fetch=_fetch))
            for prop, b in rep["by_prop"].items():
                for m in ("v1", "v2"):
                    self.assertIn("insufficient sample", b[m]["paper_roi"]["note"])
        finally:
            os.unlink(path)

    def test_grading_never_mutates_log(self):
        path, recs = _write_log()
        try:
            with open(path, "rb") as fh:
                before = fh.read()
            gs.grade(gs.load_log(path), gs.load_actuals([2026], fetch=_fetch))
            with open(path, "rb") as fh:
                after = fh.read()
            self.assertEqual(before, after)          # read-only guarantee
        finally:
            os.unlink(path)

    def test_missing_actual_is_skipped_not_invented(self):
        path, recs = _write_log()
        try:
            rep = gs.grade(recs, {})                 # no actuals at all
            self.assertEqual(rep["eligible_observations"], 0)
            self.assertEqual(rep["by_prop"], {})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
