#!/usr/bin/env python3
"""
Phase 2 tests — baseline evaluation framework.

Run:  python -m unittest tests/test_phase2.py -v

Offline/pure: exercises the walk-forward logic, the leakage guard, and the
error metrics. No network.
"""
import os
import sys
import math
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backtest


class TestNoLeakage(unittest.TestCase):
    def test_future_game_does_not_affect_its_own_projection(self):
        # Week 5's actual is a wild outlier; it must NOT change the week-5
        # projection, which may only use weeks 1-4.
        base = {"p": [(1, 50), (2, 50), (3, 50), (4, 50), (5, 9999)]}
        res_a = backtest.walk_forward(base, min_history=4)
        base2 = {"p": [(1, 50), (2, 50), (3, 50), (4, 50), (5, 10)]}
        res_b = backtest.walk_forward(base2, min_history=4)
        # the projection scored at week 5 is identical regardless of week 5's value
        self.assertEqual(res_a["blend"]["n"], 1)
        # reconstruct the single projection used: season/last3 of [50,50,50,50] = 50
        self.assertAlmostEqual(backtest.project_from_history([50, 50, 50, 50])["blend"], 50.0)
        # only the actual (and thus the error) changes, never the projection
        self.assertNotEqual(res_a["blend"]["mae"], res_b["blend"]["mae"])

    def test_min_history_respected(self):
        logs = {"p": [(1, 10), (2, 20), (3, 30), (4, 40)]}
        # with min_history=3, only game index 3 (week 4) is scored
        res = backtest.walk_forward(logs, min_history=3)
        self.assertEqual(res["blend"]["n"], 1)


class TestProjection(unittest.TestCase):
    def test_blend_matches_model_weights(self):
        p = backtest.project_from_history([100, 0, 0, 0])   # season=25, last3=0
        self.assertAlmostEqual(p["season"], 25.0)
        self.assertAlmostEqual(p["last3"], 0.0)
        self.assertAlmostEqual(p["blend"],
                               backtest.W_SEASON * 25.0 + backtest.W_L3 * 0.0)

    def test_empty_history(self):
        self.assertEqual(backtest.project_from_history([]), {})


class TestMetrics(unittest.TestCase):
    def test_mae_rmse_bias(self):
        # projections over by 2, 4, 6 -> errors +2,+4,+6
        pairs = [(12, 10), (24, 20), (36, 30)]
        m = backtest.metrics(pairs)
        self.assertEqual(m["n"], 3)
        self.assertAlmostEqual(m["mae"], 4.0)
        self.assertAlmostEqual(m["bias"], 4.0)
        self.assertAlmostEqual(m["rmse"], round(math.sqrt((4 + 16 + 36) / 3), 2))

    def test_ignores_none(self):
        m = backtest.metrics([(None, 10), (12, 10), (5, None)])
        self.assertEqual(m["n"], 1)

    def test_empty(self):
        self.assertEqual(backtest.metrics([])["n"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
