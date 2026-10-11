#!/usr/bin/env python3
"""
dist_eval.py — Phase 2D: variance structure + predictive-distribution comparison.

Two jobs:

TASK 2 (variance structure). We first make sure the overconfidence finding is
real and well-posed:
  • It measures OUT-OF-SAMPLE forecast error — walk-forward residual
    e = actual − projection, where the projection used only prior games. This is
    the uncertainty of the forecast, NOT a player's raw game-to-game spread
    around their own mean.
  • We check whether the residual spread actually scales with the projection
    (the premise of a CV model: sigma = CV·proj) by binning on projection and
    on games-of-history, and we report near-zero / outlier handling.

TASK 3 (distributions). We compare predictive distributions for P(actual > line):
  1. normal_fixed  — current model, Normal(proj, (CV_fixed·proj)^2)
  2. normal_fitted — Normal(proj, (CV_emp·proj)^2), CV fit on training years
  3. lognormal     — zero-inflated lognormal fit on training ratios r=actual/proj
  4. empirical     — historical ratio distribution (handles skew AND zeros)

Parameters are fit on EARLIER seasons and evaluated on LATER seasons
(chronological). Lines are clearly-labeled statistical thresholds relative to
the projection (m·proj), NOT sportsbook offers — we don't have historical
prop odds and won't fabricate them. Metrics: Brier, log loss, calibration by
bucket, reliability (ECE), sample size.

nflverse weekly data via backtest.collect_records (pre-game eligibility,
position filtering, leakage-guarded). Pure stdlib.
"""

import sys
import math

import backtest
import nfl_model

CV_FIXED = nfl_model._CV
# multiplicative thresholds relative to the projection (same lines for every
# candidate, so the realized over-rate is identical and only the predicted
# probability differs). Half-ish points → push mass ~0 for this calibration test.
THRESHOLDS = [0.5, 0.75, 1.0, 1.25, 1.5]


def _phi(z):
    return nfl_model._normcdf(z)


def collect(prop, seasons, w=0.45):
    """Walk-forward (proj, actual, year, nprior) with proj = the live blend."""
    out = []
    floor = backtest.VOL_FLOOR[prop]
    for r in backtest.collect_records(list(seasons), prop):
        s, l3 = r["season"], r["last3"]
        proj = (1 - w) * s + w * l3 if (s is not None and l3 is not None) else (s if s is not None else l3)
        if proj is None or proj < floor:
            continue
        out.append((proj, r["actual"], r["yr"], r.get("nprior")))
    return out


# ── TASK 2: variance structure ─────────────────────────────────────────
def variance_structure(prop, seasons):
    pairs = collect(prop, seasons)
    # residuals and ratios
    res = [(p, a, a - p) for (p, a, _, _) in pairs if p > 0]
    n = len(res)
    if n < 20:
        return {"n": n}
    import statistics as st
    overall_sd = st.pstdev([e for _, _, e in res])
    overall_cv = overall_sd / st.mean([p for p, _, _ in res])
    # bin by projection quartiles — does sd grow with proj (CV model) or stay flat?
    ps = sorted(p for p, _, _ in res)
    qs = [ps[int(q * (n - 1))] for q in (0.25, 0.5, 0.75)]
    bins = {"Q1": [], "Q2": [], "Q3": [], "Q4": []}
    for p, a, e in res:
        b = "Q1" if p <= qs[0] else "Q2" if p <= qs[1] else "Q3" if p <= qs[2] else "Q4"
        bins[b].append((p, e))
    bin_rows = []
    for b in ("Q1", "Q2", "Q3", "Q4"):
        vals = bins[b]
        if len(vals) < 5:
            continue
        mp = st.mean([p for p, _ in vals])
        sd = st.pstdev([e for _, e in vals])
        bin_rows.append({"bin": b, "mean_proj": round(mp, 1), "sd_resid": round(sd, 1),
                         "cv": round(sd / mp, 3) if mp else None, "n": len(vals)})
    return {"n": n, "overall_sd": round(overall_sd, 2), "overall_cv": round(overall_cv, 3),
            "cv_fixed": CV_FIXED[prop], "bins": bin_rows}


# ── TASK 3: predictive distributions ───────────────────────────────────
def _fit(prop, train_pairs):
    """Fit per-candidate parameters on training data only."""
    import statistics as st
    floor = backtest.VOL_FLOOR[prop]
    rs, ln_rs, zeros = [], [], 0
    resid_over_proj = []
    for p, a, _, _ in train_pairs:
        if p < floor:
            continue
        resid_over_proj.append((a - p) / p)
        if a <= 0:
            zeros += 1
            continue
        r = a / p
        rs.append(r)
        ln_rs.append(math.log(r))
    total = len(rs) + zeros
    cv_emp = st.pstdev(resid_over_proj) if len(resid_over_proj) > 2 else CV_FIXED[prop]
    ln_mu = st.mean(ln_rs) if ln_rs else 0.0
    ln_s = st.pstdev(ln_rs) if len(ln_rs) > 2 else 0.5
    p_zero = zeros / total if total else 0.0
    emp = sorted(rs + [0.0] * zeros)      # empirical ratio distribution incl. zeros
    return {"cv_fixed": CV_FIXED[prop], "cv_emp": round(cv_emp, 4),
            "ln_mu": ln_mu, "ln_s": ln_s, "p_zero": round(p_zero, 4), "emp": emp}


def prob_over(model, proj, line, params):
    if proj <= 0:
        return None
    if model == "normal_fixed":
        return 1 - _phi((line - proj) / (params["cv_fixed"] * proj))
    if model == "normal_fitted":
        return 1 - _phi((line - proj) / (params["cv_emp"] * proj))
    if model == "lognormal":
        if line <= 0:
            return 1.0
        # actual = proj * r, ln r ~ N(mu,s); plus a zero mass that never goes over
        z = (math.log(line / proj) - params["ln_mu"]) / (params["ln_s"] or 1e-9)
        return (1 - params["p_zero"]) * (1 - _phi(z))
    if model == "empirical":
        emp = params["emp"]
        if not emp:
            return None
        thr = line / proj
        # fraction of historical ratios strictly greater than thr
        lo, hi = 0, len(emp)
        while lo < hi:
            mid = (lo + hi) // 2
            if emp[mid] <= thr:
                lo = mid + 1
            else:
                hi = mid
        return (len(emp) - lo) / len(emp)
    return None


MODELS = ["normal_fixed", "normal_fitted", "lognormal", "empirical"]


def evaluate(prop, train_seasons, test_seasons):
    train = collect(prop, train_seasons)
    test = collect(prop, test_seasons)
    params = _fit(prop, train)
    floor = backtest.VOL_FLOOR[prop]
    out = {}
    for model in MODELS:
        brier, logloss = [], []
        buckets = {}   # rounded pred prob -> [sum_outcome, count]
        for proj, a, _, _ in test:
            if proj < floor:
                continue
            for m in THRESHOLDS:
                line = m * proj
                p = prob_over(model, proj, line, params)
                if p is None:
                    continue
                p = min(1 - 1e-9, max(1e-9, p))
                y = 1 if a > line else 0
                brier.append((p - y) ** 2)
                logloss.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
                b = round(p, 1)
                g = buckets.setdefault(b, [0, 0])
                g[0] += y; g[1] += 1
        n = len(brier)
        ece = 0.0
        tot = sum(c for _, c in buckets.values())
        for b, (s, c) in buckets.items():
            ece += (c / tot) * abs((s / c) - b)
        out[model] = {"n": n,
                      "brier": round(sum(brier) / n, 4) if n else None,
                      "logloss": round(sum(logloss) / n, 4) if n else None,
                      "ece": round(ece, 4),
                      "calib": {b: [round(s / c, 3), c] for b, (s, c) in sorted(buckets.items())}}
    return params, out


def run(train=(2022, 2023), test=(2024,)):
    print("=" * 76)
    print("NFL PREDICTIVE-DISTRIBUTION COMPARISON")
    print("train %s  ->  test %s  (chronological; params fit on train only)" % (train, test))
    print("lower Brier / log loss / ECE = better calibrated" )
    print("=" * 76)
    for prop in backtest.FIELDS:
        print("\n### %s" % prop)
        vs = variance_structure(prop, train + test)
        if vs.get("bins"):
            print("  variance structure (does sd scale with proj? CV should be ~flat):")
            for b in vs["bins"]:
                print(f"    {b['bin']}  mean_proj {b['mean_proj']:7}  sd_resid {b['sd_resid']:7}  cv {b['cv']:6}  n {b['n']}")
            print(f"    overall cv {vs['overall_cv']}  (model assumes {vs['cv_fixed']})")
        params, res = evaluate(prop, train, test)
        print(f"  fitted CV={params['cv_emp']}  lognormal(mu={params['ln_mu']:.3f},s={params['ln_s']:.3f})  p_zero={params['p_zero']}")
        print(f"  {'model':14} {'Brier':>8} {'LogLoss':>8} {'ECE':>7} {'n':>7}")
        for m in MODELS:
            r = res[m]
            if r["n"]:
                print(f"  {m:14} {r['brier']:8} {r['logloss']:8} {r['ece']:7} {r['n']:7}")
        best = min(MODELS, key=lambda m: res[m]["logloss"] if res[m]["n"] else 9e9)
        print(f"  -> best (log loss): {best}")


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:]]
    if len(a) >= 2:
        run(tuple(a[:-1]), (a[-1],))
    else:
        run()
