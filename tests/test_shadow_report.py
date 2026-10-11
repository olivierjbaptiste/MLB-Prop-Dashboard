#!/usr/bin/env python3
"""Phase 3D — combined shadow performance report. Offline."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shadow_report as SR


class TestReport(unittest.TestCase):
    def test_empty_states_no_data(self):
        txt = SR.build_report(None, None)
        self.assertIn("No graded prospective predictions yet", txt)
        self.assertIn("None. No real wagers", txt)

    def test_distinguishes_tiers_and_flags_small_sample(self):
        nba = {"by_prop": {"pts": {
            "n_matched": 12,
            "v1_projection": {"mae": 5.1}, "v2_projection": {"mae": 5.0},
            "v2_prob_vs_recorded_line": {"brier": 0.24, "ece": 0.03},
            "v2_paper_bets": {"roi_pct": 8.0, "record": {"win": 6, "loss": 6, "push": 0}},
        }}}
        txt = SR.build_report(None, nba)
        self.assertIn("PROSPECTIVE SHADOW", txt)
        self.assertIn("RESEARCH", txt)
        self.assertIn("small sample", txt)        # n=12 flagged diagnostic
        self.assertIn("No model is labelled profitable", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
