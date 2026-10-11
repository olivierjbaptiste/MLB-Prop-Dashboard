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
    """Walk-forward rows (proj, actual, year, nprior, pid), proj = the live blend.
    Unpack by index so 4- or 5-length rows both work."""
    out = []
    floor = backtest.VOL_FLOOR[prop]
    for r in backtest.collect_records(list(seasons), prop):
        s, l3 = r["season"], r["last3"]
        proj = (1 - w) * s + w * l3 if (s is not None and l3 is not None) else (s if s is not None else l3)
        if proj is None or proj < floor:
            continue
        out.append((proj, r["actual"], r["yr"], r.get("nprior"), r.get("pid")))
    return out


# ── TASK 2: variance structure ─────────────────────────────────────────
def variance_structure(prop, seasons):
    pairs = collect(prop, seasons)
    # residuals and ratios
    res = [(row[0], row[1], row[1] - row[0]) for row in pairs if row[0] > 0]
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
    for row in train_pairs:
        p, a = row[0], row[1]
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
MIN_BUCKET = 400       # min train obs per projection bucket, else fall back to pooled


# ── projection-conditional empirical (Task 3) ──────────────────────────
def fit_conditional(prop, train):
    """Fit separate ratio distributions for low/med/high projection buckets,
    with cut-points taken from the TRAINING projection tertiles. Any bucket with
    too few observations falls back to the pooled distribution (no overfitting)."""
    import bisect
    floor = backtest.VOL_FLOOR[prop]
    rows = [(row[0], row[1]) for row in train if row[0] >= floor]
    pooled = sorted((a / p) if p > 0 else 0.0 for p, a in rows)
    n = len(rows)
    if n < 3 * MIN_BUCKET:
        return {"cutoffs": None, "pooled": pooled}
    projs = sorted(p for p, _ in rows)
    c1, c2 = projs[n // 3], projs[2 * n // 3]
    buckets = {"lo": [], "mid": [], "hi": []}
    for p, a in rows:
        r = (a / p) if p > 0 else 0.0
        b = "lo" if p <= c1 else "mid" if p <= c2 else "hi"
        buckets[b].append(r)
    out = {"cutoffs": (c1, c2), "pooled": pooled}
    for b in ("lo", "mid", "hi"):
        vals = sorted(buckets[b])
        out[b] = vals if len(vals) >= MIN_BUCKET else pooled
    return out


def prob_over_cond(prop, proj, line, cond):
    import bisect
    if proj <= 0:
        return None
    emp = cond["pooled"]
    if cond.get("cutoffs"):
        c1, c2 = cond["cutoffs"]
        emp = cond["lo"] if proj <= c1 else cond["mid"] if proj <= c2 else cond["hi"]
    if not emp:
        return None
    idx = bisect.bisect_right(emp, line / proj)
    return (len(emp) - idx) / len(emp)


ALL_MODELS = MODELS + ["empirical_cond"]


def evaluate(prop, train_seasons, test_seasons):
    """Return (params, cond, summary_by_model, per_prediction_logloss)."""
    train = collect(prop, train_seasons)
    test = collect(prop, test_seasons)
    params = _fit(prop, train)
    cond = fit_conditional(prop, train)
    floor = backtest.VOL_FLOOR[prop]
    agg = {m: {"brier": [], "ll": [], "buckets": {}} for m in ALL_MODELS}
    perpred = []
    for row in test:
        proj, a = row[0], row[1]
        pid = row[4] if len(row) > 4 else None
        if proj < floor:
            continue
        lls = {}
        for m in ALL_MODELS:
            tot_ll, got = 0.0, False
            for mm in THRESHOLDS:
                line = mm * proj
                p = (prob_over_cond(prop, proj, line, cond) if m == "empirical_cond"
                     else prob_over(m, proj, line, params))
                if p is None:
                    continue
                p = min(1 - 1e-9, max(1e-9, p))
                y = 1 if a > line else 0
                ll = -(y * math.log(p) + (1 - y) * math.log(1 - p))
                agg[m]["brier"].append((p - y) ** 2)
                agg[m]["ll"].append(ll)
                bk = round(p, 1)
                g = agg[m]["buckets"].setdefault(bk, [0, 0])
                g[0] += y; g[1] += 1
                tot_ll += ll; got = True
            if got:
                lls[m] = tot_ll
        if lls:
            perpred.append((pid, lls))
    out = {}
    for m in ALL_MODELS:
        br, ll, bks = agg[m]["brier"], agg[m]["ll"], agg[m]["buckets"]
        n = len(br)
        tot = sum(c for _, c in bks.values())
        ece = sum((c / tot) * abs((s / c) - b) for b, (s, c) in bks.items()) if tot else 0.0
        out[m] = {"n": n, "brier": round(sum(br) / n, 4) if n else None,
                  "logloss": round(sum(ll) / n, 4) if n else None, "ece": round(ece, 4)}
    return params, cond, out, perpred


def cluster_ll_ci(perpred, model_a, model_b, B=600, seed=0):
    """95% CI for mean per-prediction logloss(model_a) − logloss(model_b),
    resampling PLAYERS. Negative = model_a better calibrated."""
    import random
    agg = {}
    for pid, lls in perpred:
        if model_a not in lls or model_b not in lls:
            continue
        g = agg.setdefault(pid, [0.0, 0.0, 0])
        g[0] += lls[model_a]; g[1] += lls[model_b]; g[2] += 1
    players = list(agg.values())
    if len(players) < 2:
        return {}
    rng = random.Random(seed)

    def diff(s):
        sa = sum(x[0] for x in s); sb = sum(x[1] for x in s); n = sum(x[2] for x in s)
        return (sa / n - sb / n) if n else None
    obs = diff(players)
    k = len(players)
    boots = sorted(d for d in (diff([players[rng.randrange(k)] for _ in range(k)])
                               for _ in range(B)) if d is not None)
    lo, hi = boots[int(0.025 * len(boots))], boots[min(len(boots) - 1, int(0.975 * len(boots)))]
    return {"obs": round(obs, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "significant": bool(lo > 0 or hi < 0), "n_clusters": k}


def run(train=(2022, 2023, 2024), test=(2025,)):
    print("=" * 78)
    print("NFL v1-vs-v2 CALIBRATION  (v1=normal_fixed, v2=empirical)")
    print("train %s -> test %s (chronological; fit on train only; 2025=diagnostic)" % (train, test))
    print("lower Brier / LogLoss / ECE = better calibrated. 5 thresholds each 0.5-1.5x proj")
    print("=" * 78)
    for prop in backtest.FIELDS:
        print("\n### %s" % prop)
        vs = variance_structure(prop, train + test)
        if vs.get("bins"):
            cvs = ", ".join("%s %.2f" % (b["bin"], b["cv"]) for b in vs["bins"])
            print(f"  variance structure CV by proj-quartile: {cvs}  (model assumes {vs['cv_fixed']})")
        params, cond, res, perpred = evaluate(prop, train, test)
        print(f"  {'model':14} {'Brier':>8} {'LogLoss':>8} {'ECE':>7} {'n':>7}")
        for m in ALL_MODELS:
            r = res[m]
            if r["n"]:
                tag = " (v1)" if m == "normal_fixed" else " (v2)" if m == "empirical" else ""
                print(f"  {m:14} {r['brier']:8} {r['logloss']:8} {r['ece']:7} {r['n']:7}{tag}")
        v2v1 = cluster_ll_ci(perpred, "empirical", "normal_fixed")
        cvp = cluster_ll_ci(perpred, "empirical_cond", "empirical")
        if v2v1:
            print(f"  v2-v1 logloss diff {v2v1['obs']:+.4f} CI[{v2v1['ci95'][0]:+.4f},{v2v1['ci95'][1]:+.4f}] "
                  f"{'SIGNIFICANT (v2 better)' if (v2v1['significant'] and v2v1['obs']<0) else 'ns'} ({v2v1['n_clusters']} players)")
        if cvp:
            print(f"  conditional-pooled logloss diff {cvp['obs']:+.4f} CI[{cvp['ci95'][0]:+.4f},{cvp['ci95'][1]:+.4f}] "
                  f"{'SIGNIFICANT' if cvp['significant'] else 'ns'}")


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:]]
    if len(a) >= 2:
        run(tuple(a[:-1]), (a[-1],))
    else:
        run()
