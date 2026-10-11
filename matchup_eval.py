#!/usr/bin/env python3
"""
matchup_eval.py — Phase 2C: does the opponent-defense matchup adjustment help?

Compares, on the SAME eligible player-game observations, out of sample:
  A. blend only                (season/last-3)
  B. blend x existing matchup   (clamped opponent-allowed vs league average)
  C. blend x shrunk matchup     (same factor, shrunk toward 1 by defensive
                                 sample size — an evidence-based alternative to
                                 reduce early-season noise)

CRITICAL — no leakage: the opponent defense entering week W is rebuilt from
weeks < W ONLY (point-in-time), never full-season or end-of-season stats. The
league average it is normalized against is also prior-weeks-only. A defense
with too few prior games gets a neutral factor of 1.0.

Uncertainty: player-clustered bootstrap on the MAE difference (B−A, C−A).
Because we test two alternatives x four props (8 comparisons), read the CIs
with a multiple-comparison lens (a 95% CI that barely excludes 0 is weak).

Dev seasons 2022-2024; 2025 is a diagnostic (already examined, not a clean
holdout); 2026 reserved for prospective tracking. nflverse weekly data, stdlib.
"""

import sys
import math
import random

import backtest
import nfl_model

CLAMP = getattr(nfl_model, "CLAMP", 0.20)
MIN_DEF_GAMES = 2        # need at least this many prior defensive games, else factor 1.0
SHRINK_K = 4.0           # alternative C: shrink factor toward 1 by prior games


def _metric(prop, pos):
    if prop == "pass_yds":
        return "pass_yds" if pos == "QB" else None
    if prop == "rush_yds":
        return "rush_yds_RB" if pos == "RB" else None
    if prop == "rec_yds":
        return {"WR": "rec_yds_WR", "TE": "rec_yds_TE", "RB": "rec_yds_RB"}.get(pos)
    if prop == "rec":
        return {"WR": "rec_WR", "TE": "rec_TE", "RB": "rec_RB"}.get(pos)
    return None


def _clamp(x):
    return max(1 - CLAMP, min(1 + CLAMP, x))


_SEASON_CACHE = {}

def load_season(season):
    """Return (player_logs_by_prop, defense structures) from one season's weekly
    data, regular season only. player_logs: {prop: {pid: [(week, value, opp, pos)]}}.
    defense: allowed[(defteam, metric)][week]=sum, weeks_played[defteam]=set,
    plus league totals per metric per week and defensive games per week."""
    if season in _SEASON_CACHE:
        return _SEASON_CACHE[season]
    import nfl
    rows = nfl._fetch_csv(f"{nfl.NFLV}/stats_player/stats_player_week_{season}.csv")
    plogs = {p: {} for p in backtest.FIELDS}
    allowed = {}          # (defteam, metric) -> {week: sum}
    weeks_played = {}     # defteam -> set(week)
    league_allowed = {}   # metric -> {week: sum}
    def_games_wk = {}     # week -> set(defteam)

    def add_def(defteam, metric, week, val):
        allowed.setdefault((defteam, metric), {})
        allowed[(defteam, metric)][week] = allowed[(defteam, metric)].get(week, 0.0) + val
        league_allowed.setdefault(metric, {})
        league_allowed[metric][week] = league_allowed[metric].get(week, 0.0) + val

    for r in rows:
        if (r.get("season_type") or "REG").upper() != "REG":
            continue
        pid = r.get("player_id")
        wk = nfl._num(r.get("week"))
        pos = r.get("position_group") or ""
        opp = r.get("opponent_team")
        if not pid or not wk or not opp:
            continue
        wk = int(wk)
        py, ry, rcy, rc = (nfl._num(r.get("passing_yards")), nfl._num(r.get("rushing_yards")),
                           nfl._num(r.get("receiving_yards")), nfl._num(r.get("receptions")))
        # defense that this player's opponent allowed, by the player's position
        weeks_played.setdefault(opp, set()).add(wk)
        def_games_wk.setdefault(wk, set()).add(opp)
        if pos == "QB":
            add_def(opp, "pass_yds", wk, py)
        elif pos == "RB":
            add_def(opp, "rush_yds_RB", wk, ry)
            add_def(opp, "rec_yds_RB", wk, rcy)
            add_def(opp, "rec_RB", wk, rc)
        elif pos == "WR":
            add_def(opp, "rec_yds_WR", wk, rcy)
            add_def(opp, "rec_WR", wk, rc)
        elif pos == "TE":
            add_def(opp, "rec_yds_TE", wk, rcy)
            add_def(opp, "rec_TE", wk, rc)
        # player production logs per prop (for the ones this position gets)
        for prop, positions in backtest.PROP_POS.items():
            if pos not in positions:
                continue
            val = {"pass_yds": py, "rush_yds": ry, "rec_yds": rcy, "rec": rc}[prop]
            plogs[prop].setdefault(pid, []).append((wk, val, opp, pos))
    defense = {"allowed": allowed, "weeks_played": weeks_played,
               "league_allowed": league_allowed, "def_games_wk": def_games_wk}
    _SEASON_CACHE[season] = (plogs, defense)
    return plogs, defense


def matchup_factor(defense, defteam, metric, week):
    """Opponent's per-game `metric` allowed through week-1, vs league per-game
    through week-1, clamped. Returns (factor_B, factor_C, n_prior_games)."""
    if not metric:
        return 1.0, 1.0, 0
    wk = defense["allowed"].get((defteam, metric), {})
    team_allowed = sum(v for w, v in wk.items() if w < week)
    team_games = sum(1 for w in defense["weeks_played"].get(defteam, ()) if w < week)
    lg = defense["league_allowed"].get(metric, {})
    league_allowed = sum(v for w, v in lg.items() if w < week)
    league_games = sum(len(d) for w, d in defense["def_games_wk"].items() if w < week)
    if team_games < MIN_DEF_GAMES or league_games <= 0 or league_allowed <= 0:
        return 1.0, 1.0, team_games
    raw = (team_allowed / team_games) / (league_allowed / league_games)
    fb = _clamp(raw)
    shrink = team_games / (team_games + SHRINK_K)      # alt C: trust grows with games
    fc = 1 + (fb - 1) * shrink
    return fb, fc, team_games


def eval_prop(season, prop, min_history=3):
    plogs, defense = load_season(season)
    vol = backtest.VOL_FLOOR[prop]
    recs = []
    for pid, log in plogs[prop].items():
        log = sorted(log, key=lambda t: t[0])
        for i in range(len(log)):
            if i < min_history:
                continue
            prior = [v for (_, v, _, _) in log[:i]]
            if backtest._mean(prior) is None or backtest._mean(prior) < vol:
                continue
            week, actual, opp, pos = log[i]
            season_rate = backtest._mean(prior)
            last3 = backtest._mean(prior[-3:])
            blend = backtest._blend(season_rate, last3)
            if blend is None:
                continue
            metric = _metric(prop, pos)
            fb, fc, _ = matchup_factor(defense, opp, metric, week)
            recs.append({"pid": pid, "actual": actual,
                         "A": blend, "B": blend * fb, "C": blend * fc})
    return recs


def _metrics(recs, key):
    errs = [r[key] - r["actual"] for r in recs if r[key] is not None and r["actual"] is not None]
    n = len(errs)
    if not n:
        return {"n": 0}
    return {"n": n,
            "mae": round(sum(abs(e) for e in errs) / n, 3),
            "rmse": round(math.sqrt(sum(e * e for e in errs) / n), 3),
            "bias": round(sum(errs) / n, 3)}


def _cluster_boot(recs, key, ref, B=1000, seed=0):
    rng = random.Random(seed)
    agg = {}
    for r in recs:
        a, pk, pr = r["actual"], r[key], r[ref]
        if a is None or pk is None or pr is None:
            continue
        g = agg.setdefault(r["pid"], [0.0, 0.0, 0])
        g[0] += abs(pk - a); g[1] += abs(pr - a); g[2] += 1
    players = list(agg.values())
    if not players:
        return {"n_clusters": 0}

    def diff(sample):
        sw = sum(x[0] for x in sample); sr = sum(x[1] for x in sample); n = sum(x[2] for x in sample)
        return (sw / n - sr / n) if n else None
    obs = diff(players)
    k = len(players)
    boots = sorted(d for d in (diff([players[rng.randrange(k)] for _ in range(k)])
                               for _ in range(B)) if d is not None)
    lo, hi = boots[int(0.025 * len(boots))], boots[min(len(boots) - 1, int(0.975 * len(boots)))]
    return {"obs": round(obs, 3), "ci95": [round(lo, 3), round(hi, 3)],
            "significant": bool(lo > 0 or hi < 0), "n_clusters": k}


def run(seasons=(2022, 2023, 2024), boot=1000):
    print("=" * 76)
    print("NFL MATCHUP VALIDATION  (A=blend, B=+matchup, C=+shrunk matchup)")
    print("point-in-time opponent defense (weeks < W only)  |  dev %s" % (seasons,))
    print("=" * 76)
    for prop in backtest.FIELDS:
        recs = []
        for s in seasons:
            recs.extend(eval_prop(s, prop))
        A, Bm, Cm = _metrics(recs, "A"), _metrics(recs, "B"), _metrics(recs, "C")
        print(f"\n{prop}  ({'/'.join(backtest.PROP_POS[prop])})   predictions: {A.get('n',0)}")
        for name, m in (("A blend", A), ("B +matchup", Bm), ("C +shrunk", Cm)):
            if m.get("n"):
                print(f"  {name:12s} MAE {m['mae']:8} RMSE {m['rmse']:8} bias {m['bias']:8}")
        bvsa = _cluster_boot(recs, "B", "A", B=boot)
        cvsa = _cluster_boot(recs, "C", "A", B=boot)
        print(f"  B−A ΔMAE {bvsa['obs']:+.3f} CI[{bvsa['ci95'][0]:+.3f},{bvsa['ci95'][1]:+.3f}] "
              f"{'SIGNIFICANT' if bvsa['significant'] else 'ns'}")
        print(f"  C−A ΔMAE {cvsa['obs']:+.3f} CI[{cvsa['ci95'][0]:+.3f},{cvsa['ci95'][1]:+.3f}] "
              f"{'SIGNIFICANT' if cvsa['significant'] else 'ns'}")
    print("\n(8 comparisons total — apply a multiple-comparison lens; a CI that")
    print(" barely excludes 0 is weak evidence.)")


if __name__ == "__main__":
    args = [int(a) for a in sys.argv[1:]]
    run(tuple(args) if args else (2022, 2023, 2024))
