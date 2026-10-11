#!/usr/bin/env python3
"""Phase 2B tests — recency-weight sweep. Offline/pure. Run:
   python -m unittest tests/test_sweep.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sweep


def _rec(pid, season, last3, actual):
    return {"pid": pid, "season": season, "last3": last3, "actual": actual}


class TestWeightedProjection(unittest.TestCase):
    def test_endpoints(self):
        r = _rec("a", 100.0, 0.0, 50.0)
        self.assertAlmostEqual(sweep._proj(r, 0.0), 100.0)     # season-only
        self.assertAlmostEqual(sweep._proj(r, 0.50), 50.0)     # half/half
        self.assertAlmostEqual(sweep._proj(r, 0.45), 55.0)     # current

    def test_missing_handling(self):
        self.assertEqual(sweep._proj(_rec("a", None, None, 1), 0.3), None)
        self.assertEqual(sweep._proj(_rec("a", 10.0, None, 1), 0.3), 10.0)


class TestMetrics(unittest.TestCase):
    def test_mae(self):
        recs = [_rec("a", 12, 12, 10), _rec("a", 24, 24, 20)]  # errors +2,+4 at w=0
        m = sweep.wmetrics(recs, 0.0)
        self.assertEqual(m["n"], 2)
        self.assertAlmostEqual(m["mae"], 3.0)
        self.assertAlmostEqual(m["bias"], 3.0)


class TestClusterBootstrap(unittest.TestCase):
    def test_no_difference_same_weight(self):
        recs = [_rec("p%d" % i, 50, 40, 45) for i in range(40)]
        d = sweep.cluster_bootstrap_mae_diff(recs, 0.45, 0.45, B=200)
        self.assertAlmostEqual(d["obs"], 0.0, places=6)
        self.assertFalse(d["significant"])

    def test_detects_clear_edge(self):
        # season (w=0) nails it; recency (w=0.5) is always off by 10 -> w=0 better
        recs = [_rec("p%d" % i, 50, 70, 50) for i in range(60)]
        d = sweep.cluster_bootstrap_mae_diff(recs, 0.0, 0.5, B=300)
        self.assertLess(d["obs"], 0)          # w=0 lower error than w=0.5
        self.assertTrue(d["significant"])

    def test_clusters_counted_by_player(self):
        recs = [_rec("same", 50, 40, 45) for _ in range(100)]  # all one player
        d = sweep.cluster_bootstrap_mae_diff(recs, 0.3, 0.45, B=100)
        self.assertEqual(d["n_clusters"], 1)   # one cluster despite 100 rows


if __name__ == "__main__":
    unittest.main(verbosity=2)
