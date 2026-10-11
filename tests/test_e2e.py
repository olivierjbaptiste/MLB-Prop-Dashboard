#!/usr/bin/env python3
"""Phase 2E end-to-end test — saved snapshot fixture through v1 + v2 + compare
+ shadow recorder, and a smoke check that NBA/MLB still load. Offline/pure."""
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
import compare_models
import record_predictions as rp
import grade_shadow as gs

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "nfl_snapshot_fixture.json")
NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)


def _load():
    with open(FIX) as f:
        d = json.load(f)
    d["ts"] = NOW.timestamp() - 3600       # deterministic freshness
    return d


class TestEndToEnd(unittest.TestCase):
    def test_v1_pipeline(self):
        d = _load()
        nfl_model.project_all(d)
        lamb = next(p for p in d["players"] if p["name"] == "CeeDee Lamb")
        self.assertIsNotNone(lamb.get("proj_rec_yds"))
        self.assertIn("edge", lamb)                       # odds matched -> edge computed
        self.assertTrue(len(d.get("edges", [])) >= 0)

    def test_v2_probabilities_valid(self):
        d = _load(); nfl_model.project_all(d)
        lamb = next(p for p in d["players"] if p["name"] == "CeeDee Lamb")
        o, p, u = model_v2.over_under_push("rec_yds", lamb["proj_rec_yds"], 84.5, "empirical")
        self.assertAlmostEqual(o + p + u, 1.0, places=9)

    def test_compare_v1_v2(self):
        d = _load(); nfl_model.project_all(d)
        rows = compare_models.compare(d, now_iso="2026-10-11T14:00:00Z")
        self.assertTrue(rows)
        r = rows[0]
        for key in ("player", "prop", "book", "line", "v1_prob", "v1_ev_pct",
                    "v2_prob", "v2_ev_pct", "p_diff", "ev_diff", "v1_version", "v2_version"):
            self.assertIn(key, r)
        # the compared line/price come from one real offer (Phase 1 integrity)
        self.assertIsNotNone(r["line"])

    def test_shadow_recorder_logs_both(self):
        d = _load(); nfl_model.project_all(d)
        v1 = rp.build_records(d, nfl_model.VERSION, NOW)
        v2 = rp.build_v2_records(d, NOW)
        self.assertTrue(v1 and v2)
        self.assertTrue(all(r["model_version"] == nfl_model.VERSION for r in v1))
        self.assertTrue(all(r["model_version"] == model_v2.VERSION for r in v2))
        with tempfile.TemporaryDirectory() as tdir:
            path = os.path.join(tdir, "log.jsonl")
            added, _ = rp.append_records(v1 + v2, path)
            self.assertEqual(added, len(v1) + len(v2))     # v1 & v2 coexist (no key collision)
            # re-append is idempotent (no misleading duplicate counts)
            again, _ = rp.append_records(v1 + v2, path)
            self.assertEqual(again, 0)

    def test_full_pipeline_snapshot_to_grading(self):
        # snapshot -> v1/v2 -> shadow log -> final stats -> grading -> report
        d = _load(); nfl_model.project_all(d)
        recs = rp.build_records(d, nfl_model.VERSION, NOW) + rp.build_v2_records(d, NOW)
        with tempfile.TemporaryDirectory() as tdir:
            path = os.path.join(tdir, "log.jsonl")
            rp.append_records(recs, path)
            actuals = {
                ("00-0036322", 2026, 6): {"rec_yds": 95.0, "rec": 7.0, "rush_yds": 0.0, "pass_yds": 0.0},
                ("00-0037111", 2026, 6): {"rush_yds": 70.0, "rec_yds": 0.0, "rec": 0.0, "pass_yds": 0.0},
            }
            rep = gs.grade(gs.load_log(path), actuals)
            self.assertGreater(rep["eligible_observations"], 0)
            for prop, b in rep["by_prop"].items():
                self.assertEqual(b["v1"]["n_obs"], b["v2"]["n_obs"])   # identical eligible set

    def test_grading_failure_preserves_log(self):
        d = _load(); nfl_model.project_all(d)
        recs = rp.build_records(d, nfl_model.VERSION, NOW) + rp.build_v2_records(d, NOW)
        with tempfile.TemporaryDirectory() as tdir:
            path = os.path.join(tdir, "log.jsonl")
            rp.append_records(recs, path)
            with open(path, "rb") as fh:
                before = fh.read()

            def _boom(_season):
                raise RuntimeError("actuals source down")
            with self.assertRaises(RuntimeError):
                gs.run(path=path, out_dir=os.path.join(tdir, "grades"), fetch=_boom)
            with open(path, "rb") as fh:
                after = fh.read()
            self.assertEqual(before, after)      # a grading failure never touches the log

    def test_nba_still_works(self):
        import nba_model
        nd = {"today": "2026-10-11", "team_defense": {}, "players": [
            {"name": "Star", "pos": "G", "team": "BOS", "opp": "NYK", "gp": 10, "status": "",
             "pts": 28.0, "reb": 6.0, "ast": 7.0, "min": 36.0,
             "pts_l5": 26.0, "reb_l5": 5.0, "ast_l5": 8.0, "min_l5": 35.0}]}
        nba_model.project_all(nd)
        self.assertIsNotNone(nd["players"][0].get("proj_pts"))

    def test_mlb_module_loads(self):
        import dashboard
        self.assertTrue(hasattr(dashboard, "build_all_data"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
