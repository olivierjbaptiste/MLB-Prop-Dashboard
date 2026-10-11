#!/usr/bin/env python3
"""
sweep.py — Phase 2B: NFL recency-weight sweep.

Tests recency weights 0%..50% (season weight = 1 − recency) for each prop, out
of sample, against the current 55/45 model and a season-only model. Because the
same player contributes many correlated observations, uncertainty on the
MAE-difference is estimated with a PLAYER-CLUSTERED bootstrap (resample players
with replacement), not a naive iid interval.

Development seasons: 2022–2024 (chronological, all weeks walk-forward).
2025 is reported as a diagnostic — NOT an untouched holdout (we've seen it).
2026 is reserved for prospective, write-once validation (see record_predictions.py).

Pure stdlib. Reuses backtest.collect_records (pre-game eligibility, position
filtering, regular-season only, leakage-guarded).
"""

import sys
import math
import random

import backtest

WEIGHTS = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
CURRENT = 0.45          # the live model's recency weight
DEV_SEASONS = (2022, 2023, 2024)


def _proj(rec, w):
    s, l3 = rec["season"], rec["last3"]
    if s is None and l3 is None:
        return None
    if l3 is None:
        return s
    if s is None:
        return l3
    return (1 - w) * s + w * l3


def wmetrics(recs, w):
    errs = [(_proj(r, w) - r["actual"]) for r in recs
            if _proj(r, w) is not None and r["actual"] is not None]
    n = len(errs)
    if not n:
        return {"n": 0}
    return {"n": n,
            "mae":  round(sum(abs(e) for e in errs) / n, 3),
            "rmse": round(math.sqrt(sum(e * e for e in errs) / n), 3),
            "bias": round(sum(errs) / n, 3)}


def cluster_bootstrap_mae_diff(recs, w, ref_w, B=1000, seed=0):
    """95% CI for MAE(w) − MAE(ref_w), resampling PLAYERS with replacement so
    repeated observations from the same player are handled. Negative = w better."""
    rng = random.Random(seed)
    agg = {}   # pid -> [sum|err_w|, sum|err_ref|, count]
    for r in recs:
        a = r["actual"]
        pw, pr = _proj(r, w), _proj(r, ref_w)
        if a is None or pw is None or pr is None:
            continue
        g = agg.setdefault(r["pid"], [0.0, 0.0, 0])
        g[0] += abs(pw - a); g[1] += abs(pr - a); g[2] += 1
    players = list(agg.values())
    if not players:
        return {"n_clusters": 0}

    def diff(sample):
        sw = sum(x[0] for x in sample)
        sr = sum(x[1] for x in sample)
        n = sum(x[2] for x in sample)
        return (sw / n - sr / n) if n else None

    obs = diff(players)
    k = len(players)
    boots = []
    for _ in range(B):
        samp = [players[rng.randrange(k)] for _ in range(k)]
        d = diff(samp)
        if d is not None:
            boots.append(d)
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[min(len(boots) - 1, int(0.975 * len(boots)))]
    return {"obs": round(obs, 3), "ci95": [round(lo, 3), round(hi, 3)],
            "significant": bool(lo > 0 or hi < 0), "n_clusters": k}


def sweep_prop(prop, dev_seasons=DEV_SEASONS, boot=1000):
    recs = backtest.collect_records(list(dev_seasons), prop)
    rows = [(w, wmetrics(recs, w)) for w in WEIGHTS]
    valid = [(w, m) for w, m in rows if m.get("n")]
    best_mae = min(valid, key=lambda x: x[1]["mae"])[0]
    best_rmse = min(valid, key=lambda x: x[1]["rmse"])[0]
    # is the best MAE weight meaningfully better than the current 55/45 model?
    vs_current = cluster_bootstrap_mae_diff(recs, best_mae, CURRENT, B=boot)
    vs_season = cluster_bootstrap_mae_diff(recs, best_mae, 0.0, B=boot)
    return {"rows": rows, "best_mae": best_mae, "best_rmse": best_rmse,
            "n": valid[0][1]["n"] if valid else 0,
            "vs_current": vs_current, "vs_season": vs_season, "recs": recs}


def per_season(prop, seasons):
    out = {}
    for s in seasons:
        recs = backtest.collect_records([s], prop)
        out[s] = {w: wmetrics(recs, w) for w in (0.0, CURRENT)}
    return out


def run(boot=1000):
    print("=" * 74)
    print("NFL RECENCY-WEIGHT SWEEP  (dev 2022-2024, walk-forward, OOS)")
    print("blend = (1-w)*season + w*last3   |   current model w=0.45   |   lower MAE better")
    print("player-clustered bootstrap CIs (B=%d)" % boot)
    print("=" * 74)
    recommendations = {}
    for prop in backtest.FIELDS:
        r = sweep_prop(prop, boot=boot)
        print(f"\n{prop}  ({'/'.join(backtest.PROP_POS[prop])})   predictions: {r['n']}")
        print(f"  {'w':>5} {'MAE':>8} {'RMSE':>8} {'bias':>8}")
        for w, m in r["rows"]:
            if not m.get("n"):
                continue
            mark = ""
            if w == r["best_mae"]:
                mark += " <-best MAE"
            if abs(w - CURRENT) < 1e-9:
                mark += " (current)"
            print(f"  {w:5.2f} {m['mae']:8} {m['rmse']:8} {m['bias']:8}{mark}")
        vc, vs = r["vs_current"], r["vs_season"]
        print(f"  best(w={r['best_mae']:.2f}) vs current(0.45): ΔMAE {vc['obs']:+.3f} "
              f"CI[{vc['ci95'][0]:+.3f},{vc['ci95'][1]:+.3f}] "
              f"{'SIGNIFICANT' if vc['significant'] else 'not significant'} "
              f"({vc['n_clusters']} players)")
        print(f"  best(w={r['best_mae']:.2f}) vs season-only(0.00): ΔMAE {vs['obs']:+.3f} "
              f"CI[{vs['ci95'][0]:+.3f},{vs['ci95'][1]:+.3f}] "
              f"{'SIGNIFICANT' if vs['significant'] else 'not significant'}")
        recommendations[prop] = (r["best_mae"], vc["significant"])
    print("\n" + "-" * 74)
    print("RECOMMENDED recency weight per prop (dev 2022-2024):")
    for prop, (w, sig) in recommendations.items():
        note = "meaningfully beats 0.45" if sig else "NOT meaningfully different from 0.45 — keep 0.45"
        print(f"  {prop:9s} best w={w:.2f}   {note}")
    return recommendations


def diagnostic_2025(recommendations):
    print("\n" + "=" * 74)
    print("2025 DIAGNOSTIC  (already examined — NOT an untouched holdout)")
    print("=" * 74)
    for prop, (w, _) in recommendations.items():
        recs = backtest.collect_records([2025], prop)
        mc, m45, m0 = wmetrics(recs, w), wmetrics(recs, CURRENT), wmetrics(recs, 0.0)
        if not mc.get("n"):
            continue
        print(f"  {prop:9s} n={mc['n']:5}  "
              f"rec(w={w:.2f}) MAE {mc['mae']:7} | 0.45 MAE {m45['mae']:7} | season MAE {m0['mae']:7}")


if __name__ == "__main__":
    B = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    recs = run(B)
    diagnostic_2025(recs)
