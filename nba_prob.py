#!/usr/bin/env python3
"""
nba_prob.py — Phase 3C: calibrated NBA over/under/push probabilities.

PURE PYTHON (stdlib only), so it runs anywhere the model runs — no numpy/scipy.
Takes an NBA v2 point projection (the mean) and a sportsbook line, and returns a
VALID probability triple (over + push + under = 1) for a prop.

The props (points, rebounds, assists, threes, PRA) are all non-negative INTEGER
counts, so the push on an integer line is a real probability mass P(X == line),
and it is handled exactly by the count models and with a ±0.5 continuity
correction by the continuous ones.

Candidate distributions (chosen per prop later, by out-of-sample calibration):
  • normal_fixed    — Normal(μ, (cv·μ)²), one cv per prop (v1-style baseline).
  • normal_fitted   — Normal with cv fitted per projection-volume bucket.
  • poisson         — Poisson(μ); natural for low counts (threes).
  • neg_binomial    — Negative Binomial(mean μ, dispersion r per bucket); handles
                      the overdispersion (variance > mean) real box scores show.
  • empirical       — distribution-free: the empirical distribution of the ratio
                      actual/projection, per bucket; captures skew and zeros.

PRA is modelled DIRECTLY on realized PRA (not as a sum of independent pts/reb/
ast), so the dependence between the three is captured by the data itself.

`fit()` learns parameters from training (projection, actual) pairs ONLY — the
caller supplies pairs built with no future data (see the backtest).
"""

import math

PROPS = ("pts", "reb", "ast", "tpm", "pra")
MODELS = ("normal_fixed", "normal_fitted", "poisson", "neg_binomial", "empirical")

# Bumped whenever the per-prop model selection or fitting method changes, so every
# recorded prediction is traceable to the calibration that produced it.
CALIBRATION_VERSION = "nba-cal-2026.1"

# Initial candidate distribution per prop. These are the starting choices; the
# final per-prop model is selected by out-of-sample calibration (Task 3) on the
# regenerated clean data. Empirical is the distribution-free default; low-count
# threes start on negative binomial (a count model).
# FINAL per-prop models — selected by out-of-sample Brier (proper scoring) on the
# corrected data (fit 2023-24+2024-25, test 2025-26). Empirical wins significantly
# for points and PRA; negative binomial is the best proper-scoring model for the
# discrete count props (rebounds, assists, threes). See PHASE_3C_CALIBRATION.md.
DEFAULT_MODEL_BY_PROP = {"pts": "empirical", "reb": "neg_binomial", "ast": "neg_binomial",
                         "tpm": "neg_binomial", "pra": "empirical"}

# fixed-CV fallback per prop (used by normal_fixed and as a floor elsewhere)
FIXED_CV = {"pts": 0.45, "reb": 0.55, "ast": 0.55, "tpm": 0.75, "pra": 0.40}
_MIN_SIGMA = 1.0
_N_BUCKETS = 4          # projection-volume buckets for conditional fitting
_MIN_BUCKET = 200       # a bucket needs this many obs to fit its own params


# ───────────────────────── math helpers (stdlib) ──────────────────────
def _norm_cdf(x, mu, sigma):
    if sigma <= 0:
        return 0.0 if x < mu else 1.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _poisson_cdf(k, lam):
    """P(X <= k) for X~Poisson(lam), k integer >= -1."""
    if k < 0:
        return 0.0
    lam = max(lam, 1e-9)
    # stable term-by-term sum
    term = math.exp(-lam)
    s = term
    for i in range(1, int(k) + 1):
        term *= lam / i
        s += term
    return min(1.0, s)


def _nb_cdf(k, mu, r):
    """P(X <= k) for Negative Binomial with mean mu, size r (dispersion).
    var = mu + mu^2/r. As r→∞ this tends to Poisson."""
    if k < 0:
        return 0.0
    mu = max(mu, 1e-9)
    r = max(r, 1e-6)
    p = r / (r + mu)                       # P(success)
    # pmf(0) = p^r ; pmf(i) = pmf(i-1) * (r+i-1)/i * (1-p)
    term = p ** r
    s = term
    for i in range(1, int(k) + 1):
        term *= (r + i - 1) / i * (1 - p)
        s += term
    return min(1.0, s)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _var(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


# ───────────────────────────── fitting ────────────────────────────────
def _bucket_edges(projs, n=_N_BUCKETS):
    s = sorted(p for p in projs if p is not None and p > 0)
    if len(s) < n:
        return []
    return [s[int(len(s) * i / n)] for i in range(1, n)]


def _bucket_of(proj, edges):
    b = 0
    for e in edges:
        if proj > e:
            b += 1
        else:
            break
    return b


def fit(pairs, prop=None):
    """Fit probability parameters.

    • fit({prop: [(proj, actual), ...], ...})  -> {prop: params}  (all props)
    • fit([(proj, actual), ...], prop)         -> params for one prop

    Uses only the data passed in (no leakage)."""
    if isinstance(pairs, dict):
        return {p: fit(v, p) for p, v in pairs.items()}
    if prop is None:
        raise ValueError("prop is required when pairs is a list")
    pairs = [(p, a) for p, a in pairs if p is not None and a is not None and p > 0]
    edges = _bucket_edges([p for p, _ in pairs])
    buckets = {}
    groups = {}
    for p, a in pairs:
        groups.setdefault(_bucket_of(p, edges), []).append((p, a))
    # global fallbacks
    g_ratios = sorted(a / p for p, a in pairs)
    g_cv = _global_cv(pairs)
    g_r = _global_nb_r(pairs)
    for b, grp in groups.items():
        cv = _global_cv(grp)
        r = _global_nb_r(grp)
        ratios = sorted(a / p for p, a in grp)
        ok = len(grp) >= _MIN_BUCKET
        buckets[b] = {
            "cv": cv if ok else g_cv,
            "nb_r": r if ok else g_r,
            "ratios": ratios if ok else g_ratios,
            "n": len(grp),
        }
    return {"prop": prop, "edges": edges, "buckets": buckets,
            "cv_global": g_cv, "nb_r_global": g_r, "ratios_global": g_ratios,
            "fixed_cv": FIXED_CV.get(prop, 0.5)}


def _global_cv(pairs):
    """CV such that sd(actual) ≈ cv·mean(proj), from realized spread."""
    if len(pairs) < 2:
        return 0.5
    resid_var = _var([a for _, a in pairs])     # spread of actuals
    mproj = _mean([p for p, _ in pairs]) or 1.0
    sd = math.sqrt(max(resid_var, 1e-9))
    return max(0.15, min(1.5, sd / mproj))


def _global_nb_r(pairs):
    """Method-of-moments dispersion r from mean/variance of actuals."""
    actuals = [a for _, a in pairs]
    m = _mean(actuals) or 1.0
    v = _var(actuals)
    if v <= m:                   # not overdispersed → effectively Poisson
        return 1e6
    return max(0.3, m * m / (v - m))


def _params_for(params, proj):
    b = _bucket_of(proj, params["edges"])
    return params["buckets"].get(b) or {
        "cv": params["cv_global"], "nb_r": params["nb_r_global"],
        "ratios": params["ratios_global"]}


# ──────────────────── the probability triple ──────────────────────────
def _triple_from_cdf(cdf, line):
    """Given a cdf function over integers (cdf(k)=P(X<=k)) and a line, return
    (over, push, under) with sportsbook integer-push semantics."""
    if abs(line - round(line)) < 1e-9:                 # integer line → real push
        L = int(round(line))
        under = cdf(L - 1)
        push = cdf(L) - cdf(L - 1)
        over = 1.0 - cdf(L)
    else:                                              # half-point → no push
        L = math.floor(line)
        under = cdf(L)
        push = 0.0
        over = 1.0 - cdf(L)
    over = max(0.0, over); under = max(0.0, under); push = max(0.0, push)
    s = over + push + under
    if s <= 0:
        return 0.0, 1.0, 0.0
    return over / s, push / s, under / s


def _empirical_cdf_fn(proj, ratios):
    """Continuous model via empirical ratio distribution, continuity-corrected:
    cdf(k) = P(actual <= k) = P(ratio <= (k+0.5)/proj)."""
    n = len(ratios)

    def frac_le(t):
        # fraction of ratios <= t  (ratios sorted)
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if ratios[mid] <= t:
                lo = mid + 1
            else:
                hi = mid
        return lo / n if n else 0.0

    return lambda k: frac_le((k + 0.5) / proj) if proj > 0 else 0.0


def over_under_push(prop, proj, line, model, params):
    """Return (over, push, under) for a prop, projection, line and model.
    Valid probabilities that sum to 1. None if projection is non-positive."""
    if proj is None or proj <= 0:
        return None
    bp = _params_for(params, proj)
    if model == "normal_fixed":
        sigma = max(_MIN_SIGMA, params["fixed_cv"] * proj)
        cdf = lambda k: _norm_cdf(k + 0.5, proj, sigma)
    elif model == "normal_fitted":
        sigma = max(_MIN_SIGMA, bp["cv"] * proj)
        cdf = lambda k: _norm_cdf(k + 0.5, proj, sigma)
    elif model == "poisson":
        cdf = lambda k: _poisson_cdf(k, proj)
    elif model == "neg_binomial":
        r = bp["nb_r"]
        cdf = lambda k: _nb_cdf(k, proj, r)
    elif model == "empirical":
        cdf = _empirical_cdf_fn(proj, bp["ratios"])
    else:
        raise ValueError("unknown model: " + model)
    return _triple_from_cdf(cdf, line)


def prob_over(prop, proj, line, model, params):
    t = over_under_push(prop, proj, line, model, params)
    return None if t is None else t[0]


def default_triple(prop, proj, line, params_by_prop):
    """Use the per-prop default model and that prop's params. `params_by_prop` is
    the {prop: params} object from fit(dict). Returns (triple, model_name)."""
    model = DEFAULT_MODEL_BY_PROP.get(prop, "empirical")
    pp = params_by_prop.get(prop) if isinstance(params_by_prop, dict) and prop in params_by_prop \
        else params_by_prop
    return over_under_push(prop, proj, line, model, pp), model
