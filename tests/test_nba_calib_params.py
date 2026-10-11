#!/usr/bin/env python3
"""Phase 4.1 — the BUNDLED calibration params (nba_calib_params.json) must stay
valid and in sync with the model. Offline; uses the committed params file."""
import os
import sys
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_prob as P

PARAMS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "nba_calib_params.json")


@unittest.skipUnless(os.path.exists(PARAMS_FILE), "bundled params not present")
class TestBundledParams(unittest.TestCase):
    def setUp(self):
        with open(PARAMS_FILE) as f:
            self.blob = json.load(f)
        self.params = self.blob["params"]

    def test_version_and_models_match_code(self):
        self.assertEqual(self.blob["calibration_version"], P.CALIBRATION_VERSION)
        self.assertEqual(self.blob["models"], P.DEFAULT_MODEL_BY_PROP)

    def test_every_prop_present_with_buckets(self):
        for prop in P.PROPS:
            self.assertIn(prop, self.params)
            pp = self.params[prop]
            self.assertIn("edges", pp)
            self.assertTrue(pp["buckets"])            # 4 volume buckets

    def test_edges_full_precision(self):
        # edges must NOT be rounded (rounding flips buckets at boundaries)
        for prop in P.PROPS:
            for e in self.params[prop]["edges"]:
                self.assertIsInstance(e, float)

    def test_produces_valid_probabilities(self):
        for prop in P.PROPS:
            model = P.DEFAULT_MODEL_BY_PROP[prop]
            for proj, line in ((5.0, 4.5), (20.0, 24.5), (35.0, 30.5)):
                t = P.over_under_push(prop, proj, line, model, self.params[prop])
                self.assertIsNotNone(t)
                self.assertAlmostEqual(sum(t), 1.0, places=6)
                for v in t:
                    self.assertTrue(0.0 <= v <= 1.0 + 1e-9)

    def test_monotonic_in_line(self):
        pp = self.params["pts"]
        lo = P.prob_over("pts", 25.0, 15.5, "empirical", pp)
        hi = P.prob_over("pts", 25.0, 35.5, "empirical", pp)
        self.assertGreater(lo, hi)


if __name__ == "__main__":
    unittest.main(verbosity=2)
