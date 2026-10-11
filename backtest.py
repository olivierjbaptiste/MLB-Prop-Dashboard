#!/usr/bin/env python3
"""
backtest.py — Phase 2: historical baseline evaluation for NFL projections.

Measures whether the projection CORE — the 55% season / 45% last-3 blend used
by nfl_model — beats dumb baselines OUT OF SAMPLE, before any formula change.
Point-projection accuracy only (MAE / RMSE / bias); probability calibration is
a separate step that needs historical lines.

Rigor guarantees (all unit-tested or documented):
  • Strict walk-forward: a game in week W is projected from that player's games
    in weeks < W only. No future information touches a projection.
  • Pre-game eligibility: a prediction is scored only if the player's PRIOR
    games (not the full season) meet a volume floor — so sample selection uses
    no hindsight either.
  • Regular season only (season_type REG), one season at a time, so playoffs
    and season boundaries never mix populations.
  • Position-appropriate: passing→QB, rushing→RB/QB, receiving→WR/TE/RB.
  • Paired-difference 95% CIs quantify whether blend-vs-baseline gaps are real
    or noise.

Scope: evaluates the blend, NOT the full model (matchup, game script, weather,
injury redistribution, TD, calibration, EV) — those are separate steps.

Data: nflverse weekly CSVs (real, public, reachable offline and on the runner).
Pure stdlib.
"""

import sys
import math

try:
    from nfl_model import W_SEASON, W_L3
except Exception:
    W_SEASON, W_L3 = 0.55, 0.45

FIELDS = {
    "pass_yds": "passing_yards",
    "rush_yds": "rushing_yards",
    "rec_yds":  "receiving_yards",
    "rec":      "receptions",
}
# Positions a prop is actually meaningful for.
PROP_POS = {
    "pass_yds": ("QB",),
    "rush_yds": ("RB", "QB"),
    "rec_yds":  ("WR", "TE", "RB"),
    "rec":      ("WR", "TE", "RB"),
}
# Pre-game volume floor: a prediction is scored only when the player's PRIOR
# average in this stat clears the floor (so we evaluate on players the prop
# applies to, decided with no hindsight).
VOL_FLOOR = {"pass_yds": 50.0, "rush_yds": 10.0, "rec_yds": 15.0, "rec": 1.5}


def _mean(xs):
    xs = list(xs)
    return (sum(xs) / len(xs)) if xs else None


def _blend(season, last3):
    """Mirror nfl_model._blend: None = missing (skip), 0.0 counts."""
    if season is None and last3 is None:
        return None
    if last3 is None:
        return season
    if season is None:
        return last3
    return W_SEASON * season + W_L3 * last3


def project_from_history(prior_values):
    """Project from a player's values for games STRICTLY BEFORE the target
    game (oldest→newest). That is the only input, so no future data leaks in."""
    if not prior_values:
        return {}
    season = _mean(prior_values)
    last3 = _mean(prior_values[-3:])
    return {"blend": _blend(season, last3), "season": season, "last3": last3}


def metrics(pairs):
    """pairs: [(projection, actual)] -> error metrics (projection − actual)."""
    pairs = [(p, a) for p, a in pairs if p is not None and a is not None]
    n = len(pairs)
    if not n:
        return {"n": 0}
    errs = [p - a for p, a in pairs]
    return {
        "n": n,
        "mae":  round(sum(abs(e) for e in errs) / n, 2),
        "rmse": round(math.sqrt(sum(e * e for e in errs) / n), 2),
        "bias": round(sum(errs) / n, 2),
    }


def _diff_stats(diffs):
    """Paired difference of absolute errors (blend − baseline). Negative mean =
    blend has lower error. 95% CI excluding 0 = statistically meaningful."""
    n = len(diffs)
    if n < 2:
        return {"n": n}
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    se = math.sqrt(var / n)
    lo, hi = mean - 1.96 * se, mean + 1.96 * se
    return {"n": n, "mean_abs_err_diff": round(mean, 3), "se": round(se, 3),
            "ci95": [round(lo, 3), round(hi, 3)],
            "significant": bool(lo > 0 or hi < 0)}


def walk_forward_records(logs, min_history=3, vol_floor=0.0):
    """Return one record per scored prediction: {blend, season, last3, actual}."""
    recs = []
    for pid, log in logs.items():
        ordered = [v for _, v in sorted(log, key=lambda r: r[0])]
        for i in range(len(ordered)):
            if i < min_history:
                continue
            prior = ordered[:i]                 # strictly earlier games — no leakage
            pm = _mean(prior)
            if pm is None or pm < vol_floor:    # pre-game eligibility, no hindsight
                continue
            proj = project_from_history(prior)
            recs.append({"pid": pid, "blend": proj["blend"], "season": proj["season"],
                         "last3": proj["last3"], "actual": ordered[i], "nprior": len(prior)})
    return recs


def collect_records(seasons, prop, min_history=3):
    """Per-prediction {pid, season_rate, last3, actual, season} across seasons,
    with this prop's pre-game eligibility applied. Reused by the weight sweep."""
    out = []
    for s in seasons:
        logs = load_week_logs(s, prop)
        for r in walk_forward_records(logs, min_history, VOL_FLOOR[prop]):
            r["yr"] = s
            out.append(r)
    return out


def summarize(recs):
    out = {m: metrics([(r[m], r["actual"]) for r in recs]) for m in ("blend", "season", "last3")}
    out["diff"] = {}
    for base in ("season", "last3"):
        ds = [abs(r["blend"] - r["actual"]) - abs(r[base] - r["actual"])
              for r in recs if r["blend"] is not None and r[base] is not None]
        out["diff"][base] = _diff_stats(ds)
    return out


def walk_forward(logs, min_history=3, vol_floor=0.0):
    return summarize(walk_forward_records(logs, min_history, vol_floor))


# ── data loading (nflverse weekly, regular season) ─────────────────────
def load_week_logs(season, prop):
    """{player_id: [(week, value)]} for the positions that get `prop`, regular
    season only. No volume/hindsight filter here — eligibility is applied
    per-prediction in walk_forward_records using prior games only."""
    import nfl
    field = FIELDS[prop]
    positions = PROP_POS[prop]
    rows = nfl._fetch_csv(f"{nfl.NFLV}/stats_player/stats_player_week_{season}.csv")
    logs = {}
    for r in rows:
        st = (r.get("season_type") or "REG").upper()
        if st != "REG":                                  # no playoffs
            continue
        pid = r.get("player_id")
        wk = nfl._num(r.get("week"))
        if not pid or not wk:
            continue
        if (r.get("position_group") or "") not in positions:
            continue
        logs.setdefault(pid, []).append((int(wk), nfl._num(r.get(field))))
    return logs


def run(season, label=""):
    print("=" * 72)
    print(f"NFL BASELINE BACKTEST — season {season}{label}  (walk-forward, OOS)")
    print(f"blend = {W_SEASON:.2f}*season + {W_L3:.2f}*last3   |   lower MAE/RMSE better")
    print("=" * 72)
    season_summary = {}
    for prop in FIELDS:
        logs = load_week_logs(season, prop)
        recs = walk_forward_records(logs, min_history=3, vol_floor=VOL_FLOOR[prop])
        s = summarize(recs)
        season_summary[prop] = s
        n = s["blend"].get("n", 0)
        print(f"\n{prop}  ({FIELDS[prop]}, {'/'.join(PROP_POS[prop])})   "
              f"players:{len(logs)}  scored:{n}")
        print(f"  {'method':8s} {'MAE':>7} {'RMSE':>7} {'bias':>7}")
        for m in ("blend", "season", "last3"):
            r = s[m]
            if r.get("n"):
                print(f"  {m:8s} {r['mae']:7} {r['rmse']:7} {r['bias']:7}")
        for base in ("season", "last3"):
            d = s["diff"][base]
            if d.get("n", 0) >= 2:
                verdict = "SIGNIFICANT" if d["significant"] else "not significant (CI spans 0)"
                sign = "blend better" if d["mean_abs_err_diff"] < 0 else "baseline better"
                print(f"    blend−{base}: ΔMAE {d['mean_abs_err_diff']:+.3f} "
                      f"95%CI[{d['ci95'][0]:+.3f},{d['ci95'][1]:+.3f}]  {verdict} ({sign})")
    return season_summary


def run_multi(seasons, holdout=None):
    for s in seasons:
        lbl = "  [HOLDOUT — not for tuning]" if s == holdout else ""
        run(s, lbl)
        print()
    print("Point-accuracy only; evaluates the blend, not the full model.")
    print("No accuracy claim is made beyond these measured numbers.")


if __name__ == "__main__":
    # Usage: backtest.py [season ...]  — the LAST season is reported as the
    # untouched holdout (still run, just labeled; never used for tuning).
    args = [int(a) for a in sys.argv[1:]]
    if len(args) >= 2:
        run_multi(args, holdout=args[-1])
    elif len(args) == 1:
        run(args[0])
    else:
        import nfl
        run(nfl._season())
