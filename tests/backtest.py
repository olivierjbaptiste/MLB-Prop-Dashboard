#!/usr/bin/env python3
"""
backtest.py — Phase 2: historical baseline evaluation for NFL projections.

Purpose: measure whether the projection CORE — the 55% season / 45% last-3
blend used by nfl_model — actually beats dumb baselines, OUT OF SAMPLE, before
we change any formula. Point-projection accuracy only here (MAE / RMSE / bias);
probability calibration is a separate step that needs historical lines.

Method: strict walk-forward. For each player's game in week W we project using
ONLY that player's games in weeks < W (season-to-date mean and last-3 mean),
then compare to the actual week-W result. No future information ever touches a
projection — the leakage guard is unit-tested.

Scope note: this evaluates the blend itself, not the full model (opponent
matchup, game script, weather, injury redistribution). Those multipliers need
per-game historical opponent/line context and are the next evaluation step;
this isolates the one knob the brief asked about first — the 55/45 weighting.

Data: nflverse weekly CSV (reachable offline and from the runner). Pure stdlib.
"""

import sys
import math

# Keep the blend weights in sync with the live model so we're testing the real thing.
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
# Each prop is only meaningful for the positions that actually get it, and only
# for players with real involvement — otherwise the metrics drown in 0≈0.
PROP_POS = {
    "pass_yds": ("QB",),
    "rush_yds": ("RB", "QB"),
    "rec_yds":  ("WR", "TE", "RB"),
    "rec":      ("WR", "TE", "RB"),
}
VOL_FLOOR = {"pass_yds": 50.0, "rush_yds": 10.0, "rec_yds": 15.0, "rec": 1.5}


def _mean(xs):
    xs = list(xs)
    return (sum(xs) / len(xs)) if xs else None


def _blend(season, last3):
    """Mirror nfl_model._blend semantics: None = missing (skip), 0.0 counts."""
    if season is None and last3 is None:
        return None
    if last3 is None:
        return season
    if season is None:
        return last3
    return W_SEASON * season + W_L3 * last3


def project_from_history(prior_values):
    """Given a player's values for every game STRICTLY BEFORE the target game
    (oldest→newest), return the three projections under evaluation.

    prior_values is the only input, so by construction no future data leaks in.
    """
    if not prior_values:
        return {}
    season = _mean(prior_values)
    last3 = _mean(prior_values[-3:])
    return {
        "blend":  _blend(season, last3),
        "season": season,
        "last3":  last3,
    }


def metrics(pairs):
    """pairs: [(projection, actual)] -> error metrics (projection minus actual)."""
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


def walk_forward(logs, min_history=3):
    """logs: {player_id: [(week, value), ...]} -> metrics per method.

    For each game after the first `min_history` games, project from the prior
    games only and score against the actual.
    """
    buckets = {"blend": [], "season": [], "last3": []}
    for pid, log in logs.items():
        ordered = [v for _, v in sorted(log, key=lambda r: r[0])]
        for i in range(len(ordered)):
            if i < min_history:
                continue
            prior = ordered[:i]                 # strictly earlier games — no leakage
            actual = ordered[i]
            proj = project_from_history(prior)
            for m in buckets:
                buckets[m].append((proj.get(m), actual))
    return {m: metrics(pairs) for m, pairs in buckets.items()}


# ── data loading (nflverse weekly) ─────────────────────────────────────
def load_week_logs(season, prop, min_games=4):
    """Build {player_id: [(week, value)]} for the positions that actually get
    `prop`, keeping only players with real season-long involvement in it."""
    import nfl  # reuse the project's fetch helper + NFLV base
    field = FIELDS[prop]
    positions = PROP_POS[prop]
    floor = VOL_FLOOR[prop]
    rows = nfl._fetch_csv(
        f"{nfl.NFLV}/stats_player/stats_player_week_{season}.csv")
    logs = {}
    for r in rows:
        pid = r.get("player_id")
        wk = nfl._num(r.get("week"))
        if not pid or not wk:
            continue
        if (r.get("position_group") or "") not in positions:
            continue
        logs.setdefault(pid, []).append((int(wk), nfl._num(r.get(field))))
    out = {}
    for pid, g in logs.items():
        if len(g) < min_games:
            continue
        vals = [v for _, v in g]
        if (sum(vals) / len(vals)) < floor:      # must actually get this prop
            continue
        out[pid] = g
    return out


def run(season=None):
    import nfl
    season = season or nfl._season()
    print("=" * 66)
    print(f"NFL BASELINE BACKTEST — season {season}  (walk-forward, out-of-sample)")
    print(f"blend = {W_SEASON:.2f}*season + {W_L3:.2f}*last3   |   lower MAE/RMSE is better")
    print("=" * 66)
    for prop, field in FIELDS.items():
        logs = load_week_logs(season, prop)
        res = walk_forward(logs)
        print(f"\n{prop}  ({field}, {'/'.join(PROP_POS[prop])})   "
              f"players: {len(logs)}  predictions scored: {res['blend'].get('n', 0)}")
        print(f"  {'method':8s} {'MAE':>7} {'RMSE':>7} {'bias':>7}")
        for m in ("blend", "season", "last3"):
            r = res[m]
            if r.get("n"):
                print(f"  {m:8s} {r['mae']:7} {r['rmse']:7} {r['bias']:7}")
        best = min(("blend", "season", "last3"),
                   key=lambda m: res[m].get("rmse", 9e9) if res[m].get("n") else 9e9)
        print(f"  -> lowest RMSE: {best}")
    print("\nNote: point-accuracy only; this evaluates the blend, not the full")
    print("model (matchup/script/weather). No accuracy claim beyond these numbers.")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else None)
