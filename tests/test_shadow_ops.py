#!/usr/bin/env python3
"""Phase 4 — orchestration + monitoring. Pure/offline (fixtures, no live capture)."""
import os
import sys
import json
import random
import tempfile
import unittest
from datetime import datetime, timezone

os.environ["DIAMOND_NO_STARTUP"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_prob as P
import nba_record as R
import shadow_ops as OPS
import shadow_status as ST

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def _params():
    rng = random.Random(1)
    pairs = {p: [(max(1., b * rng.uniform(.6, 1.4)), max(0., round(b * rng.gauss(1, .3))))
                 for _ in range(400)]
             for p, b in (("pts", 20.), ("reb", 6.), ("ast", 4.), ("tpm", 2.), ("pra", 30.))}
    return P.fit(pairs)


def _snap(start="2026-10-11T23:30Z"):
    return {"ts": NOW.timestamp() - 3600, "season": "2027", "today": "2026-10-11",
            "games": [{"game_id": "401900001", "home_id": "1", "away_id": "2",
                       "home_abb": "DAL", "away_abb": "PHX", "start": start, "status": ""}],
            "team_defense": {},
            "players": [{"id": "00-STAR", "name": "Star Wing", "team": "DAL", "team_id": "1",
                         "pos": "F", "status": "", "gp": 20, "mpg": 34., "min_l5": 35.,
                         "ppg": 26., "ppg_l5": 28., "rpg": 6., "rpg_l5": 6.5, "apg": 5.,
                         "apg_l5": 5.5, "tpm": 3., "recent_min": [33, 34, 36, 35, 37]}],
            "odds": {"star wing": {"pts": {"offers": [{"book": "DK", "line": 25.5,
                     "over_price": -110, "under_price": -110, "ts": "2026-10-11T17:30Z"}]}}}}


class TestCapture(unittest.TestCase):
    def setUp(self):
        self._log = R.LOG_PATH
        self.tmp = tempfile.mkdtemp()
        R.LOG_PATH = os.path.join(self.tmp, "nba_log.jsonl")

    def tearDown(self):
        R.LOG_PATH = self._log

    def test_dry_run_writes_nothing(self):
        runlog = os.path.join(self.tmp, "runs.jsonl")
        e = OPS.capture_nba(_snap(), NOW, _params(), log_path=runlog, dry_run=True)
        self.assertEqual(e["outcome"], "dry_run")
        self.assertGreater(e["would_record"], 0)
        self.assertFalse(os.path.exists(R.LOG_PATH))        # nothing written

    def test_real_capture_writes_and_logs(self):
        runlog = os.path.join(self.tmp, "runs.jsonl")
        e = OPS.capture_nba(_snap(), NOW, _params(), log_path=runlog)
        self.assertEqual(e["outcome"], "ok")
        self.assertGreater(e["rows_added"], 0)
        self.assertTrue(os.path.exists(R.LOG_PATH))
        self.assertTrue(os.path.exists(runlog))

    def test_refuses_after_tipoff(self):
        runlog = os.path.join(self.tmp, "runs.jsonl")
        e = OPS.capture_nba(_snap(start="2026-10-11T17:00Z"), NOW, _params(), log_path=runlog)
        self.assertEqual(e["outcome"], "refused")
        self.assertFalse(os.path.exists(R.LOG_PATH))        # no records on refusal

    def test_failure_is_logged_not_raised(self):
        runlog = os.path.join(self.tmp, "runs.jsonl")
        # a broken snapshot (players not a list) -> handled, logged failed, no crash
        e = OPS.capture_nba({"players": 123, "games": [], "ts": NOW.timestamp()},
                            NOW, _params(), log_path=runlog)
        self.assertIn(e["outcome"], ("failed", "refused"))


class TestStatus(unittest.TestCase):
    def test_status_reads_runs(self):
        tmp = tempfile.mkdtemp()
        runlog = os.path.join(tmp, "runs.jsonl")
        OPS.log_run({"sport": "nba", "action": "capture", "outcome": "ok", "rows_added": 5},
                    path=runlog)
        saved = ST.RUN_LOG
        try:
            ST.RUN_LOG = runlog
            rep = ST.status()
            self.assertIn("nba", rep["sports"])
            self.assertEqual(rep["sports"]["nba"]["last_capture"]["outcome"], "ok")
        finally:
            ST.RUN_LOG = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
