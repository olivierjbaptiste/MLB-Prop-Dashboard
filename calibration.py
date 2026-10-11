#!/usr/bin/env python3
"""
calibration.py — Phase 2C: are the NFL model's over/under probabilities reliable?

The model assumes a player's actual ~ Normal(projection, (CV*projection)^2) with
a fixed per-prop CV (nfl_model._CV). We test that assumption against REALIZED
outcomes, out of sample, with no sportsbook lines needed:

  1. Standardized residuals z = (actual - proj)/(CV*proj). If the model were
     calibrated, z ~ N(0,1): mean≈0 (unbiased), std≈1 (right spread). std>1
     means OVERCONFIDENT (real spread wider than assumed) — edges would be too
     aggressive. We also report the CV that WOULD calibrate each prop.

  2. Threshold calibration at ±σ offsets from the projection (half-σ steps, so
     these are effectively half-point lines → no push): for each offset t the
     model says P(over)=1-Φ(t); we compare to the realized over-rate. Brier
     score and log loss summarize sharpness+calibration.

Pushes: the synthetic thresholds here are off the projection, so push mass is
~0. The integer-line push handling (unconditional vs conditional-on-no-push)
lives in nfl_model._blend_ou and is tested in test_phase1.py — it is NOT
re-litigated here. Market-implied comparison is OMITTED: we have no reliable
free historical prop lines and will not fabricate them.

Dev seasons 2022-2024. nflverse weekly data, stdlib.
"""

import sys
import math

import backtest
import nfl_model

CV = nfl_model._CV
OFFSETS = [-1.0, -0.5, 0.0, 0.5, 1.0]   # in units of the assumed sigma


def _phi(z):
    return nfl_model._normcdf(z)


def collect(prop, seasons, w=0.45):
    recs = backtest.collect_records(list(seasons), prop)
    out = []
    for r in recs:
        s, l3, a = r["season"], r["last3"], r["actual"]
        proj = (1 - w) * s + w * l3 if (s is not None and l3 is not None) else (s if s is not None else l3)
        if proj is None or proj <= 0 or a is None:
            continue
        out.append((proj, a))
    return out


def residual_diagnostics(pairs, cv):
    zs = [(a - p) / (cv * p) for p, a in pairs if p > 0]
    n = len(zs)
    if n < 2:
        return {"n": n}
    mean = sum(zs) / n
    std = math.sqrt(sum((z - mean) ** 2 for z in zs) / (n - 1))
    return {"n": n, "z_mean": round(mean, 3), "z_std": round(std, 3),
            "cv_assumed": cv, "cv_empirical": round(cv * std, 3),
            "verdict": ("overconfident" if std > 1.07 else
                        "underconfident" if std < 0.93 else "well-calibrated")}


def threshold_calibration(pairs, cv):
    rows = []
    brier_terms, logloss_terms = [], []
    for t in OFFSETS:
        p = 1 - _phi(t)                        # model P(over proj + t*sigma)
        overs = 0
        for proj, a in pairs:
            line = proj + t * cv * proj
            y = 1 if a > line else 0
            overs += y
            brier_terms.append((p - y) ** 2)
            pc = min(1 - 1e-9, max(1e-9, p))
            logloss_terms.append(-(y * math.log(pc) + (1 - y) * math.log(1 - pc)))
        n = len(pairs)
        rows.append({"offset": t, "pred_p": round(p, 3),
                     "realized": round(overs / n, 3) if n else None, "n": n})
    brier = round(sum(brier_terms) / len(brier_terms), 4) if brier_terms else None
    logloss = round(sum(logloss_terms) / len(logloss_terms), 4) if logloss_terms else None
    return rows, brier, logloss


def run(seasons=(2022, 2023, 2024)):
    print("=" * 74)
    print("NFL PROBABILITY CALIBRATION  (normal + fixed-CV vs realized)  dev %s" % (seasons,))
    print("=" * 74)
    for prop in backtest.FIELDS:
        cv = CV[prop]
        pairs = collect(prop, seasons)
        d = residual_diagnostics(pairs, cv)
        rows, brier, logloss = threshold_calibration(pairs, cv)
        print(f"\n{prop}   n={d.get('n',0)}   CV assumed={cv}")
        print(f"  standardized residual: mean {d.get('z_mean')}  std {d.get('z_std')}  "
              f"-> {d.get('verdict')}  (CV that calibrates ≈ {d.get('cv_empirical')})")
        print(f"  Brier {brier}   LogLoss {logloss}")
        print(f"  {'line offset':>12} {'model P(over)':>14} {'realized':>10} {'n':>7}")
        for r in rows:
            print(f"  {r['offset']:+12.1f}σ {r['pred_p']:14} {r['realized']:10} {r['n']:7}")
    print("\nMarket-implied comparison omitted — no reliable free historical prop")
    print("odds; not fabricated. Calibration above is vs realized outcomes only.")


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    run(tuple(args) if args else (2022, 2023, 2024))
