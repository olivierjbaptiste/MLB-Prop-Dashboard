#!/usr/bin/env python3
"""
nba_fit_params.py — Phase 3D: fit the NBA probability parameters from history and
save them for the live recorder. Run whenever the history is regenerated.

Builds walk-forward v2 (projection, actual) pairs from the corrected history CSV
(uses ALL completed seasons, since this is the production parameter set), fits
nba_prob per prop, and writes nba_prob_params.json (git-ignored). The recorder
loads this file so it never has to refit on every slate.

Stdlib only. Usage (PowerShell):
    python nba_fit_params.py nba_history.csv
"""

import os
import sys
import csv
import json
from collections import defaultdict

import nba_prob

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "nba_calib_params.json")   # compact, committed, versioned
RATIO_GRID = 1500        # empirical ratios downsampled to this many quantile points
MIN_GP, MIN_FLOOR, MIN_HISTORY = 3, 10.0, 3
W2 = {"pts": 0.6, "reb": 0.8, "ast": 0.8, "tpm": 0.8}


def _f(x):
    try:
        return None if x in (None, "") else float(x)
    except Exception:
        return None


def _blend(s, r, n, ws, wr):
    if s is None and r is None:
        return None
    if r is None or n < MIN_GP:
        return s if s is not None else r
    if s is None:
        return r
    return ws * s + wr * r


def _rate(t, m):
    return (t / m) if (t is not None and m and m > 0) else None


def _ewma(xs, hl=5):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    lam = 0.5 ** (1 / hl); num = den = 0.0
    for i, x in enumerate(reversed(xs)):
        num += lam ** i * x; den += lam ** i
    return num / den if den else None


def build_pairs(path):
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8"))
            if (r.get("season_type") or "").upper() == "REG" and _f(r.get("min")) and _f(r["min"]) > 0]
    games = defaultdict(list)
    for r in rows:
        games[(r["player_id"], r["season"])].append(r)
    for k in games:
        games[k].sort(key=lambda r: (r.get("game_date") or "", r.get("game_id") or ""))
    pairs = {p: [] for p in nba_prob.PROPS}
    for (pid, season), gs in games.items():
        cnt = 0; smin = 0.0
        sums = {"pts": 0., "reb": 0., "ast": 0., "tpm": 0.}
        rl = {"pts": [], "reb": [], "ast": [], "tpm": []}; rmin = []
        for g in gs:
            am = _f(g["min"])
            if cnt >= MIN_HISTORY and smin / cnt >= MIN_FLOOR:
                n = cnt; s_min = smin / cnt; l5_min = sum(rmin[-5:]) / len(rmin[-5:]); ew = _ewma(rmin)
                comp = {}
                for prop in ("pts", "reb", "ast", "tpm"):
                    sa = sums[prop] / cnt; l5a = sum(rl[prop][-5:]) / len(rl[prop][-5:])
                    mr = _blend(_rate(sa, s_min), _rate(l5a, l5_min), n, W2[prop], 1 - W2[prop])
                    proj = None if (mr is None or ew is None) else mr * ew
                    av = _f(g[prop])
                    if proj and proj > 0 and av is not None:
                        pairs[prop].append((proj, av)); comp[prop] = (proj, av)
                if all(c in comp for c in ("pts", "reb", "ast")):
                    pairs["pra"].append((sum(comp[c][0] for c in ("pts", "reb", "ast")),
                                         sum(comp[c][1] for c in ("pts", "reb", "ast"))))
            cnt += 1; smin += am
            for p in sums:
                sums[p] += _f(g[p]) or 0; rl[p].append(_f(g[p]) or 0)
            rmin.append(am)
    return pairs


def _grid(sorted_vals, n=RATIO_GRID):
    """Downsample a sorted list to n quantile points (keeps the empirical CDF
    essentially unchanged while making the params file small enough to commit)."""
    m = len(sorted_vals)
    if m <= n:
        return [round(float(v), 5) for v in sorted_vals]
    return [round(float(sorted_vals[min(m - 1, int(i * (m - 1) / (n - 1)))]), 5)
            for i in range(n)]


def _compact(params):
    """Shrink empirical ratio arrays to a quantile grid, in place-ish copy."""
    out = {}
    for prop, pp in params.items():
        c = {"edges": [round(float(e), 4) for e in pp["edges"]],
             "cv_global": pp["cv_global"], "nb_r_global": pp["nb_r_global"],
             "ratios_global": _grid(pp["ratios_global"]),
             "fixed_cv": pp["fixed_cv"], "buckets": {}}
        for b, bk in pp["buckets"].items():
            c["buckets"][str(b)] = {"cv": bk["cv"], "nb_r": bk["nb_r"],
                                    "ratios": _grid(bk["ratios"]), "n": bk["n"]}
        out[prop] = c
    return out


def main(path, out=OUT):
    pairs = build_pairs(path)
    params = _compact(nba_prob.fit(pairs))
    blob = {"calibration_version": nba_prob.CALIBRATION_VERSION,
            "models": nba_prob.DEFAULT_MODEL_BY_PROP,
            "fit_pairs": {p: len(pairs[p]) for p in pairs}, "params": params}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    size = os.path.getsize(out)
    print("fit+compacted params for {} props ({} pairs) -> {}  [{} ; {:.0f} KB]".format(
        len(params), {p: len(pairs[p]) for p in pairs}, out,
        nba_prob.CALIBRATION_VERSION, size / 1024))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python nba_fit_params.py nba_history.csv")
    main(sys.argv[1])
