#!/usr/bin/env python3
"""
model_v2.py — Phase 2D: candidate NFL probability model (NOT production).

Version 1 (nfl_model.py) stays the default and is unchanged. This candidate
(version 2) replaces only the PROBABILITY layer — how a point projection becomes
P(over) / P(push) / P(under) for a sportsbook line — using the better-calibrated
distributions found in Phase 2D:

  • The fixed-CV normal was demonstrably miscalibrated: it is overconfident, and
    the residual spread does NOT scale proportionally with the projection (CV
    falls from ~1.2 at low projections to ~0.5 at high ones). A single CV is the
    wrong shape.
  • Skew-aware distributions (empirical ratio, lognormal) calibrate markedly
    better (lower log loss, ~60% lower calibration error) on 2022-2024 train →
    2024/2025 test.

v2 is configurable (DEFAULT_DIST), computes a VALID distribution (over+push+
under = 1), handles integer-line pushes, and computes EV against the EXACT
sportsbook offer (Phase 1 odds-matching preserved). Positive EV is reported as a
MODEL ESTIMATE, never asserted as profitable — we have no validated historical
prop prices.

CAL params are fit on 2022-2024 walk-forward residuals (dist_eval). Refit
periodically. Pure stdlib.
"""

import math

VERSION = "nfl-2026.2-candidate"
DEFAULT_DIST = "empirical"       # "empirical" | "lognormal" | "normal_fitted"

# Fitted on 2022-2024 (ratio = actual/projection; 21 quantiles at 0,5,...,100%).
CAL = {
    "pass_yds": {"cv_emp": 0.4915, "ln_mu": -0.0798, "ln_s": 0.5671, "p_zero": 0.0145,
                 "ratio_q": [0.0, 0.313, 0.5278, 0.6303, 0.6941, 0.7563, 0.8045, 0.8458,
                             0.9031, 0.9482, 0.9921, 1.0335, 1.0819, 1.1335, 1.1718,
                             1.2327, 1.2968, 1.3883, 1.516, 1.7738, 5.114]},
    "rush_yds": {"cv_emp": 0.8975, "ln_mu": -0.2427, "ln_s": 0.9084, "p_zero": 0.0691,
                 "ratio_q": [0.0, 0.0, 0.1073, 0.2112, 0.3, 0.3979, 0.4741, 0.5547,
                             0.6389, 0.7386, 0.8345, 0.931, 1.0228, 1.1374, 1.2713,
                             1.4223, 1.589, 1.8082, 2.1333, 2.6624, 7.917]},
    "rec_yds":  {"cv_emp": 0.8461, "ln_mu": -0.2033, "ln_s": 0.8342, "p_zero": 0.1027,
                 "ratio_q": [0.0, 0.0, 0.0, 0.1837, 0.2782, 0.3652, 0.4508, 0.5392,
                             0.6293, 0.7185, 0.8043, 0.9044, 1.0096, 1.1262, 1.2443,
                             1.3897, 1.565, 1.7637, 2.0537, 2.5806, 7.0905]},
    "rec":      {"cv_emp": 0.6859, "ln_mu": -0.0925, "ln_s": 0.6112, "p_zero": 0.0915,
                 "ratio_q": [0.0, 0.0, 0.1977, 0.3341, 0.4286, 0.5, 0.5714, 0.64, 0.733,
                             0.8163, 0.8922, 0.9668, 1.048, 1.1382, 1.2389, 1.3659,
                             1.5021, 1.6766, 1.8824, 2.2245, 6.414]},
}


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _ratio_cdf(prop, x):
    """Empirical P(ratio <= x) from stored quantiles (cum probs 0,0.05,...,1)."""
    qs = CAL[prop]["ratio_q"]
    k = len(qs) - 1                       # 20 steps
    if x <= qs[0]:
        return 0.0
    if x >= qs[-1]:
        return 1.0
    for i in range(k):
        if qs[i] <= x < qs[i + 1]:
            span = qs[i + 1] - qs[i]
            frac = (x - qs[i]) / span if span > 1e-12 else 0.0
            return (i + frac) / k
    return 1.0


def _cdf_actual(prop, proj, x, dist):
    """P(actual <= x | projection) under the chosen distribution."""
    if proj <= 0:
        return None
    if dist == "empirical":
        return _ratio_cdf(prop, x / proj)
    if dist == "normal_fitted":
        return _phi((x - proj) / (CAL[prop]["cv_emp"] * proj))
    if dist == "lognormal":
        p0 = CAL[prop]["p_zero"]
        if x <= 0:
            return p0
        z = (math.log(x / proj) - CAL[prop]["ln_mu"]) / (CAL[prop]["ln_s"] or 1e-9)
        return p0 + (1 - p0) * _phi(z)
    raise ValueError("unknown dist: " + dist)


def over_under_push(prop, proj, line, dist=None):
    """Return a VALID (p_over, p_push, p_under) distribution summing to 1.
    Integer lines get a push band of ±0.5; half-point lines have no push."""
    dist = dist or DEFAULT_DIST
    if proj is None or proj <= 0 or line is None:
        return None
    integer = abs(line - round(line)) < 1e-9
    lo = (line - 0.5) if integer else line
    hi = (line + 0.5) if integer else line
    F_lo = _cdf_actual(prop, proj, lo, dist)
    F_hi = _cdf_actual(prop, proj, hi, dist)
    if F_lo is None or F_hi is None:
        return None
    under = max(0.0, F_lo)
    push = max(0.0, F_hi - F_lo)
    over = max(0.0, 1.0 - F_hi)
    s = over + push + under
    if s <= 0:
        return None
    return over / s, push / s, under / s


def _dec(am):
    if am is None:
        return None
    o = float(am)
    return (o / 100.0 + 1) if o > 0 else (100.0 / (-o) + 1) if o < 0 else 2.0


def _ev(p_win, p_push, price):
    d = _dec(price)
    if d is None:
        return None
    return p_win * (d - 1) - max(0.0, 1 - p_win - p_push)


def offer_ev(prop, proj, offer, dist=None):
    """EV for an EXACT sportsbook offer (its own line + its own prices).
    offer = {line, over_price, under_price, book, ts}. Returns the better side
    with a model EV estimate — never labeled 'profitable'."""
    dist = dist or DEFAULT_DIST
    line = offer.get("line")
    dp = over_under_push(prop, proj, line, dist)
    if dp is None:
        return None
    p_over, p_push, p_under = dp
    cands = []
    if offer.get("over_price") is not None:
        cands.append(("Over", p_over, _ev(p_over, p_push, offer["over_price"]), offer["over_price"]))
    if offer.get("under_price") is not None:
        cands.append(("Under", p_under, _ev(p_under, p_push, offer["under_price"]), offer["under_price"]))
    cands = [c for c in cands if c[2] is not None]
    if not cands:
        return None
    side, prob, ev, price = max(cands, key=lambda c: c[2])
    return {"model_version": VERSION, "dist": dist, "prop": prop,
            "book": offer.get("book"), "line": line, "side": side, "price": price,
            "p_over": round(p_over, 4), "p_push": round(p_push, 4), "p_under": round(p_under, 4),
            "model_prob": round(prob, 4), "ev_pct": round(ev * 100, 2), "ts": offer.get("ts"),
            "label": "model EV estimate — not a profitability guarantee"}


if __name__ == "__main__":
    # tiny demo: v2 probabilities for a receiving-yards line
    for dist in ("empirical", "lognormal", "normal_fitted"):
        o, p, u = over_under_push("rec_yds", 70.0, 64.5, dist)
        print(f"{dist:14} proj 70 line 64.5 -> over {o:.3f} push {p:.3f} under {u:.3f}")
