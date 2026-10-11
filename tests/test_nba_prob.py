#!/usr/bin/env python3
"""Phase 3C — NBA probability engine. Pure/offline."""
import os
import sys
import random
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba_prob as P


def _pairs(mu, cv, n=1200, seed=3):
    """Synthetic (projection, actual) pairs: integer actuals around mu."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        proj = mu * rng.uniform(0.6, 1.4)
        actual = max(0, round(rng.gauss(proj, cv * proj)))
        out.append((proj, actual))
    return out


class TestFit(unittest.TestCase):
    def test_fit_structure(self):
        params = P.fit(_pairs(20, 0.4), "pts")
        self.assertIn("buckets", params)
        self.assertIn("ratios_global", params)
        self.assertTrue(params["edges"])


class TestValidity(unittest.TestCase):
    def setUp(self):
        self.params = {p: P.fit(_pairs(15, 0.5), p) for p in P.PROPS}

    def test_sums_to_one_all_models_all_lines(self):
        for model in P.MODELS:
            for line in [l / 2.0 for l in range(0, 120)]:    # 0 .. 59.5
                t = P.over_under_push("pts", 18.0, line, model, self.params["pts"])
                if t is None:
                    continue
                o, pu, u = t
                for v in (o, pu, u):
                    self.assertTrue(0.0 <= v <= 1.0 + 1e-9)
                self.assertAlmostEqual(o + pu + u, 1.0, places=9)

    def test_integer_line_has_push_count_models(self):
        for model in ("poisson", "neg_binomial", "normal_fitted"):
            o, pu, u = P.over_under_push("reb", 8.0, 8, model, self.params["reb"])
            self.assertGreater(pu, 0.0)
            self.assertAlmostEqual(o + pu + u, 1.0, places=9)

    def test_halfpoint_no_push(self):
        for model in P.MODELS:
            o, pu, u = P.over_under_push("ast", 6.0, 5.5, model, self.params["ast"])
            self.assertAlmostEqual(pu, 0.0, places=9)

    def test_monotonic_in_line(self):
        lo = P.prob_over("pts", 20.0, 10.5, "empirical", self.params["pts"])
        hi = P.prob_over("pts", 20.0, 30.5, "empirical", self.params["pts"])
        self.assertGreater(lo, hi)

    def test_nonpositive_proj_none(self):
        self.assertIsNone(P.over_under_push("pts", 0.0, 10.5, "poisson", self.params["pts"]))

    def test_low_count_threes_poisson(self):
        # threes: small mean, integer push present and sensible
        o, pu, u = P.over_under_push("tpm", 2.0, 2, "poisson", self.params["tpm"])
        self.assertAlmostEqual(o + pu + u, 1.0, places=9)
        self.assertGreater(pu, 0.0)

    def test_pra_direct_modelling(self):
        # pra params fit on pra actuals; a valid triple is produced
        t = P.over_under_push("pra", 30.0, 29.5, "empirical", self.params["pra"])
        self.assertAlmostEqual(sum(t), 1.0, places=9)


class TestFinalConfig(unittest.TestCase):
    def test_locked_model_selection(self):
        # the Phase 3C calibration result — guard against accidental changes
        self.assertEqual(P.DEFAULT_MODEL_BY_PROP, {
            "pts": "empirical", "reb": "neg_binomial", "ast": "neg_binomial",
            "tpm": "neg_binomial", "pra": "empirical"})

    def test_calibration_version_present(self):
        self.assertTrue(P.CALIBRATION_VERSION)


class TestCountCorrectness(unittest.TestCase):
    def test_poisson_matches_formula(self):
        # P(X<=0)=e^-lam
        import math
        self.assertAlmostEqual(P._poisson_cdf(0, 3.0), math.exp(-3.0), places=9)

    def test_nb_tends_to_poisson_large_r(self):
        # with huge r, NB cdf ~ Poisson cdf
        self.assertAlmostEqual(P._nb_cdf(5, 4.0, 1e9), P._poisson_cdf(5, 4.0), places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
