#!/usr/bin/env python3
"""Phase 2C tests — probability calibration diagnostics. Offline/pure."""
import os
import sys
import math
import random
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calibration as cal


class TestResidualDiagnostics(unittest.TestCase):
    def test_well_calibrated_detected(self):
        # actuals ~ Normal(proj, (cv*proj)^2) with proj=100, cv=0.5 -> z_std≈1
        rng = random.Random(1)
        cv = 0.5
        pairs = [(100.0, 100.0 + rng.gauss(0, cv * 100.0)) for _ in range(4000)]
        d = cal.residual_diagnostics(pairs, cv)
        self.assertAlmostEqual(d["z_std"], 1.0, delta=0.08)
        self.assertEqual(d["verdict"], "well-calibrated")

    def test_overconfident_detected(self):
        # true spread is 2x the assumed cv -> z_std≈2 -> overconfident
        rng = random.Random(2)
        cv = 0.5
        pairs = [(100.0, 100.0 + rng.gauss(0, 2 * cv * 100.0)) for _ in range(4000)]
        d = cal.residual_diagnostics(pairs, cv)
        self.assertGreater(d["z_std"], 1.5)
        self.assertEqual(d["verdict"], "overconfident")
        self.assertAlmostEqual(d["cv_empirical"], cv * d["z_std"], delta=0.01)


class TestThresholdCalibration(unittest.TestCase):
    def test_calibrated_realized_matches_predicted(self):
        rng = random.Random(3)
        cv = 0.5
        pairs = [(80.0, 80.0 + rng.gauss(0, cv * 80.0)) for _ in range(8000)]
        rows, brier, logloss = cal.threshold_calibration(pairs, cv)
        for r in rows:
            self.assertAlmostEqual(r["realized"], r["pred_p"], delta=0.03)
        self.assertTrue(0 < brier < 0.25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
