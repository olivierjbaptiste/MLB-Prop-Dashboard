#!/usr/bin/env python3
"""
nba_backtest.py — Phase 3A: leak-free walk-forward backtest for the NBA model.

PURE / OFFLINE. It reads a history CSV (produced by nba_fetch_history.py where
ESPN is reachable) and evaluates projections against actual results with strict
no-leakage: a game is projected using ONLY that player's earlier games in the
SAME season.

What it answers (the Phase 3A questions):
  • How accurate is each prop projection (pts, reb, ast, tpm, pra, min)?
  • Does the production approach (per-minute rate × projected minutes, blended
    0.60 season / 0.40 last-5) beat simple baselines — season average, recent
    games, per-minute — on real data?
  • What recency weight is actually best per stat (a sweep, not the NFL weights)?
  • How accurate are projected minutes, and do minutes drive the stat error?

Honesty rules baked in:
  • Within-season walk-forward only (no cross-season "prior"); REG season only by
    default; DNP / zero-minute games excluded from history and from targets.
  • Pre-game eligibility (a minutes floor on PRIOR games only — no hindsight).
  • Player-clustered bootstrap CIs (a player's games are correlated).
  • No model is selected on the evaluation period; every method is reported.
  • No fabricated data: missing fields are skipped, never invented.

CSV schema (one row per player-game) — see nba_fetch_history.py:
  player_id, player, season, season_type, game_date, team, opponent, home,
  started, min, pts, reb, ast, tpm
"""

import os
import csv
import math
import random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "nba_history.csv")

PROPS = ("pts", "reb", "ast", "tpm", "pra", "min")
# counting stats that are modelled as rate × minutes (min & pra handled specially)
RATE_PROPS = ("pts", "reb", "ast", "tpm")

# Production-model constants (mirror nba_model.py so we test the REAL approach).
W_SEASON, W_L5 = 0.60, 0.40
MIN_GP_FOR_L5 = 3
# Pre-game eligibility: project a target only if the player's PRIOR minutes
# average clears this floor (decided with no hindsight). Keeps us on the
# rotation players props actually exist for.
MIN_FLOOR = 10.0
MIN_HISTORY = 3           # need at least this many prior games in the season


# ───────────────────────────── io ─────────────────────────────────────
def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def load_history(path=DEFAULT_CSV, rows=None):
    """Return typed rows. `rows` (list of dicts) can be injected for testing."""
    raw = rows
    if raw is None:
        raw = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                raw = list(csv.DictReader(f))
    out = []
    for r in raw:
        rec = {
            "player_id": r.get("player_id"), "player": r.get("player"),
            "season": r.get("season"), "season_type": (r.get("season_type") or "").upper(),
            "game_date": r.get("game_date"), "team": r.get("team"),
            "opponent": r.get("opponent"),
            "home": r.get("home"),
            "started": r.get("started"),
            "min": _f(r.get("min")), "pts": _f(r.get("pts")), "reb": _f(r.get("reb")),
            "ast": _f(r.get("ast")), "tpm": _f(r.get("tpm")),
        }
        rec["pra"] = (None if None in (rec["pts"], rec["reb"], rec["ast"])
                      else rec["pts"] + rec["reb"] + rec["ast"])
        out.append(rec)
    return out


def _played(g):
    """A real played game: minutes present and > 0 (excludes DNP / inactive)."""
    return g["min"] is not None and g["min"] > 0


def player_season_games(history, season_types=("REG",)):
    """{(player_id, season): [games sorted by date]} — played games only,
    filtered to the requested season types (REG by default)."""
    type_map = {"REG": "REGULAR", "POST": "POST", "PRE": "PRE"}
    wanted = set()
    for t in season_types:
        wanted.add(t)
        wanted.add(type_map.get(t, t))
    buckets = defaultdict(list)
    for g in history:
        if not _played(g):
            continue
        st = g["season_type"]
        if wanted and not any(w in st for w in wanted):
            continue
        buckets[(g["player_id"], g["season"])].append(g)
    for key in buckets:
        buckets[key].sort(key=lambda g: g["game_date"] or "")
    return buckets


# ───────────────────────── projection methods ─────────────────────────
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _blend(season, recent, n, w_season=W_SEASON, w_recent=W_L5):
    """Season/recent blend with the early-season guard (mirrors nba_model)."""
    if season is None and recent is None:
        return None
    if recent is None or n is None or n < MIN_GP_FOR_L5:
        return season if season is not None else recent
    if season is None:
        return recent
    return w_season * season + w_recent * recent


def _rate(total, minutes):
    if total is None or not minutes:
        return None
    return total / minutes if minutes > 0 else None


def project_methods(prior, prop, weights=(W_SEASON, W_L5)):
    """Return {method: projection} for one target, from PRIOR games only.
    `prior` is a chronological list of played games strictly before the target."""
    if prop == "min":
        s = _mean([g["min"] for g in prior])
        l5 = _mean([g["min"] for g in prior[-5:]])
        l10 = _mean([g["min"] for g in prior[-10:]])
        return {"season": s, "last5": l5, "last10": l10,
                "model": _blend(s, l5, len(prior), *weights)}

    actuals_prop = prop
    # projected minutes (shared input for per-minute methods)
    s_min = _mean([g["min"] for g in prior])
    l5_min = _mean([g["min"] for g in prior[-5:]])
    proj_min = _blend(s_min, l5_min, len(prior), *weights)

    if prop == "pra":
        # model PRA as the sum of the three component rate projections
        out = {}
        comp = {c: project_methods(prior, c, weights) for c in ("pts", "reb", "ast")}
        for m in ("season", "last5", "model", "permin"):
            vals = [comp[c].get(m) for c in ("pts", "reb", "ast")]
            out[m] = None if any(v is None for v in vals) else sum(vals)
        return out

    # counting stat: season avg, recent avg, per-minute × minutes, blended model
    season_avg = _mean([g[actuals_prop] for g in prior])
    l5_avg = _mean([g[actuals_prop] for g in prior[-5:]])
    # per-minute rate (season) × projected minutes
    s_rate = _rate(_mean([g[actuals_prop] for g in prior]), s_min)
    l5_rate = _rate(_mean([g[actuals_prop] for g in prior[-5:]]), l5_min)
    permin = None if (s_rate is None or proj_min is None) else s_rate * proj_min
    model_rate = _blend(s_rate, l5_rate, len(prior), *weights)
    model = None if (model_rate is None or proj_min is None) else model_rate * proj_min
    return {"season": season_avg, "last5": l5_avg, "permin": permin, "model": model}


# ───────────────────────────── metrics ────────────────────────────────
def _agg(pairs):
    pairs = [(p, a) for p, a in pairs if p is not None and a is not None]
    n = len(pairs)
    if not n:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    errs = [p - a for p, a in pairs]
    return {"n": n,
            "mae": round(sum(abs(e) for e in errs) / n, 3),
            "rmse": round(math.sqrt(sum(e * e for e in errs) / n), 3),
            "bias": round(sum(errs) / n, 3)}


def _cluster_ci(triples, iters=800, seed=11):
    """Player-clustered 95% CI for mean (method_abs_err − baseline_abs_err).
    triples: [(player_id, method_abs_err, baseline_abs_err)]. Negative = method
    better than baseline."""
    data = [(pid, m - b) for pid, m, b in triples if m is not None and b is not None]
    if len(data) < 2:
        return None
    by_p = defaultdict(list)
    for pid, d in data:
        by_p[pid].append(d)
    players = list(by_p)
    obs = sum(d for _, d in data) / len(data)
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        samp = []
        for _ in range(len(players)):
            samp.extend(by_p[rng.choice(players)])
        if samp:
            means.append(sum(samp) / len(samp))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return {"mean_abs_err_diff": round(obs, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "n_obs": len(data), "n_players": len(players),
            "significant": bool(lo > 0 or hi < 0)}


# ───────────────────────── walk-forward driver ────────────────────────
def walk_forward(history, prop, weights=(W_SEASON, W_L5),
                 season_types=("REG",), min_floor=MIN_FLOOR, min_history=MIN_HISTORY):
    """One record per scored target: {player_id, actual, method→proj}."""
    games_by = player_season_games(history, season_types)
    recs = []
    for (pid, season), games in games_by.items():
        for i in range(len(games)):
            if i < min_history:
                continue
            prior = games[:i]                       # strictly earlier — no leakage
            pm = _mean([g["min"] for g in prior])
            if pm is None or pm < min_floor:         # pre-game eligibility, no hindsight
                continue
            target = games[i]
            actual = target["pra"] if prop == "pra" else target[prop]
            if actual is None:
                continue
            projs = project_methods(prior, prop, weights)
            rec = {"player_id": pid, "season_yr": season, "actual": actual,
                   "nprior": len(prior)}
            rec.update(projs)      # may include a "season" method — distinct key
            recs.append(rec)
    return recs


def evaluate(history, props=PROPS, weights=(W_SEASON, W_L5), season_types=("REG",)):
    """Per-prop MAE/RMSE/bias for every method + clustered CI of model vs the
    best baseline."""
    report = {}
    for prop in props:
        recs = walk_forward(history, prop, weights, season_types)
        methods = sorted({m for r in recs for m in r if m not in
                          ("player_id", "season_yr", "actual", "nprior")})
        per_method = {m: _agg([(r.get(m), r["actual"]) for r in recs]) for m in methods}
        # choose the best baseline (exclude the model itself) by MAE
        base_methods = [m for m in methods if m != "model"]
        best_base = None
        for m in base_methods:
            if per_method[m]["mae"] is None:
                continue
            if best_base is None or per_method[m]["mae"] < per_method[best_base]["mae"]:
                best_base = m
        ci = None
        if best_base:
            triples = [(r["player_id"],
                        abs(r["model"] - r["actual"]) if r.get("model") is not None else None,
                        abs(r[best_base] - r["actual"]) if r.get(best_base) is not None else None)
                       for r in recs]
            ci = _cluster_ci(triples)
        report[prop] = {"n": len(recs), "methods": per_method,
                        "best_baseline": best_base,
                        "model_vs_best_baseline": ci}
    return report


def _ewma(xs, halflife=5):
    """Exponentially-weighted mean, newest games weighted most."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    lam = 0.5 ** (1.0 / halflife)
    num = den = 0.0
    for i, x in enumerate(reversed(xs)):      # i=0 is the newest game
        w = lam ** i
        num += w * x
        den += w
    return num / den if den else None


MIN_METHODS = ("season", "l3", "l5", "l10", "ewma", "blend")


def project_minutes(prior, method, weights=(W_SEASON, W_L5)):
    m = [g["min"] for g in prior]
    if method == "season":
        return _mean(m)
    if method == "l3":
        return _mean(m[-3:])
    if method == "l5":
        return _mean(m[-5:])
    if method == "l10":
        return _mean(m[-10:])
    if method == "ewma":
        return _ewma(m)
    if method == "blend":        # the production approach (0.60 season / 0.40 L5)
        return _blend(_mean(m), _mean(m[-5:]), len(m), *weights)
    return None


def _vs_reference(recs, methods, reference):
    """Clustered CI of each method's abs-err vs the reference method's."""
    out = {}
    for m in methods:
        if m == reference:
            continue
        triples = [(r["player_id"],
                    abs(r[m] - r["actual"]) if r.get(m) is not None else None,
                    abs(r[reference] - r["actual"]) if r.get(reference) is not None else None)
                   for r in recs]
        out[m] = _cluster_ci(triples)
    return out


def minutes_models(history, season_types=("REG",)):
    """Task 3: compare projected-minutes methods against the production blend."""
    games_by = player_season_games(history, season_types)
    recs = []
    for (pid, season), games in games_by.items():
        for i in range(len(games)):
            if i < MIN_HISTORY:
                continue
            prior = games[:i]
            pm = _mean([g["min"] for g in prior])
            if pm is None or pm < MIN_FLOOR:
                continue
            actual = games[i]["min"]
            if actual is None:
                continue
            rec = {"player_id": pid, "actual": actual}
            for meth in MIN_METHODS:
                rec[meth] = project_minutes(prior, meth)
            recs.append(rec)
    return {"n": len(recs),
            "mae": {m: _agg([(r[m], r["actual"]) for r in recs]) for m in MIN_METHODS},
            "vs_blend": _vs_reference(recs, MIN_METHODS, "blend")}


def downstream_minutes(history, prop, season_types=("REG",)):
    """Task 4: does a better minutes method improve the STAT? Projects
    stat = blended per-minute rate × minutes(method); compares methods. This
    isolates the minutes effect (the rate layer is held fixed)."""
    if prop not in RATE_PROPS + ("pra",):
        return None
    methods = ("season", "l5", "l10", "ewma", "blend")
    games_by = player_season_games(history, season_types)

    def rate_for(prior, stat):
        s = _rate(_mean([g[stat] for g in prior]), _mean([g["min"] for g in prior]))
        l5 = _rate(_mean([g[stat] for g in prior[-5:]]), _mean([g["min"] for g in prior[-5:]]))
        return _blend(s, l5, len(prior))

    recs = []
    for (pid, season), games in games_by.items():
        for i in range(len(games)):
            if i < MIN_HISTORY:
                continue
            prior = games[:i]
            pm = _mean([g["min"] for g in prior])
            if pm is None or pm < MIN_FLOOR:
                continue
            actual = games[i]["pra"] if prop == "pra" else games[i][prop]
            if actual is None:
                continue
            rec = {"player_id": pid, "actual": actual}
            comps = ("pts", "reb", "ast") if prop == "pra" else (prop,)
            rates = {c: rate_for(prior, c) for c in comps}
            for meth in methods:
                mins = project_minutes(prior, meth)
                if mins is None or any(rates[c] is None for c in comps):
                    rec[meth] = None
                else:
                    rec[meth] = sum(rates[c] * mins for c in comps)
            recs.append(rec)
    return {"n": len(recs),
            "mae": {m: _agg([(r[m], r["actual"]) for r in recs]) for m in methods},
            "vs_blend": _vs_reference(recs, methods, "blend")}


def matchup_eval(history, prop, k_shrink=20, season_types=("REG",)):
    """Does an opponent adjustment help, point-in-time and leak-free?

    For each target (player P vs opponent O on date D, season S), the opponent
    factor is built ONLY from games before D: (mean of `prop` all tracked players
    produced vs O before D) / (league mean before D). Three variants are compared:
      • neutral   — no adjustment (model projection as-is)
      • raw       — model × opponent factor
      • regular   — model × factor shrunk toward 1.0 by opponent sample size

    NOTE on pace double-counting: this uses only the per-game allowance factor and
    applies NO separate pace multiplier. nba_model currently multiplies pace × a
    per-game allowance, which double-counts pace (per-game allowances already embed
    the opponent's pace). This evaluation deliberately avoids that, to measure the
    allowance effect cleanly.
    """
    if prop not in RATE_PROPS:
        return None
    games_by = player_season_games(history, season_types)
    # per-season pools for point-in-time opponent / league baselines
    opp_pool = defaultdict(list)     # (season, opp) -> [(date, stat)]
    lg_pool = defaultdict(list)      # season -> [(date, stat)]
    for (pid, season), games in games_by.items():
        for g in games:
            v = g[prop]
            if v is None:
                continue
            opp_pool[(season, g["opponent"])].append((g["game_date"] or "", v))
            lg_pool[season].append((g["game_date"] or "", v))

    def _mean_before(pool, d):
        vals = [v for (dt, v) in pool if dt < d]
        return (sum(vals) / len(vals)) if vals else None, len(vals)

    neutral, raw, regular = [], [], []
    for (pid, season), games in games_by.items():
        for i in range(len(games)):
            if i < MIN_HISTORY:
                continue
            prior = games[:i]
            pm = _mean([g["min"] for g in prior])
            if pm is None or pm < MIN_FLOOR:
                continue
            target = games[i]
            actual = target[prop]
            if actual is None:
                continue
            base = project_methods(prior, prop)["model"]
            if base is None:
                continue
            d = target["game_date"] or ""
            opp_mean, n_opp = _mean_before(opp_pool[(season, target["opponent"])], d)
            lg_mean, _ = _mean_before(lg_pool[season], d)
            factor = 1.0
            if opp_mean and lg_mean:
                factor = opp_mean / lg_mean
            factor_reg = 1 + (factor - 1) * (n_opp / (n_opp + k_shrink))
            neutral.append((pid, abs(base - actual)))
            raw.append((pid, abs(base * factor - actual)))
            regular.append((pid, abs(base * factor_reg - actual)))

    def _m(xs):
        vals = [e for _, e in xs]
        return round(sum(vals) / len(vals), 4) if vals else None

    def _ci(a, b):
        triples = [(a[i][0], a[i][1], b[i][1]) for i in range(len(a))]
        return _cluster_ci(triples)

    return {"n": len(neutral),
            "mae": {"neutral": _m(neutral), "raw": _m(raw), "regular": _m(regular)},
            "raw_vs_neutral": _ci(raw, neutral),
            "regular_vs_neutral": _ci(regular, neutral)}


def sweep_weights(history, prop, season_types=("REG",),
                  grid=(0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0)):
    """MAE per season-weight for one stat (recent weight = 1 − season weight).
    Evidence for/against the 0.60/0.40 default — per stat, not the NFL weights."""
    out = {}
    for w_season in grid:
        recs = walk_forward(history, prop, (w_season, 1 - w_season), season_types)
        out[round(w_season, 2)] = _agg([(r.get("model"), r["actual"]) for r in recs])
    return out


# ───────────────────────────── cli ────────────────────────────────────
def main(path=DEFAULT_CSV):
    history = load_history(path)
    played = [g for g in history if _played(g)]
    print("history rows: {}  (played: {})".format(len(history), len(played)))
    if not played:
        print("No played games in {}. Run nba_fetch_history.py on a machine that "
              "can reach ESPN first (see PHASE_3A report).".format(path))
        return
    seasons = sorted({g["season"] for g in played if g["season"]})
    print("seasons present:", seasons)
    rep = evaluate(history)
    print("\n=== walk-forward accuracy (REG season, leak-free) ===")
    for prop, b in rep.items():
        print("\n{}  (n={})".format(prop.upper(), b["n"]))
        for m, a in b["methods"].items():
            print("  {:8} MAE {} RMSE {} bias {} (n={})".format(
                m, a["mae"], a["rmse"], a["bias"], a["n"]))
        ci = b["model_vs_best_baseline"]
        if ci:
            print("  model vs best baseline ({}): {} CI{} {}".format(
                b["best_baseline"], ci["mean_abs_err_diff"], ci["ci95"],
                "(significant)" if ci["significant"] else "(ns)"))
    print("\n=== recency-weight sweep (MAE by season weight) ===")
    for prop in RATE_PROPS + ("min",):
        sw = sweep_weights(history, prop)
        best = min((k for k in sw if sw[k]["mae"] is not None),
                   key=lambda k: sw[k]["mae"], default=None)
        line = "  ".join("{}:{}".format(k, sw[k]["mae"]) for k in sorted(sw))
        print("  {:4} {}   best season-weight={}".format(prop, line, best))

    print("\n=== projected-minutes methods (MAE vs production blend) ===")
    mm = minutes_models(history)
    for m, a in mm["mae"].items():
        tag = ""
        ci = mm["vs_blend"].get(m)
        if ci:
            tag = " vs blend {} CI{} {}".format(ci["mean_abs_err_diff"], ci["ci95"],
                                                "(sig)" if ci["significant"] else "(ns)")
        print("  {:7} MAE {} RMSE {} bias {}{}".format(m, a["mae"], a["rmse"], a["bias"], tag))

    print("\n=== downstream: stat MAE by minutes method (rate layer fixed) ===")
    for prop in RATE_PROPS + ("pra",):
        dm = downstream_minutes(history, prop)
        if not dm:
            continue
        best = min((m for m in dm["mae"] if dm["mae"][m]["mae"] is not None),
                   key=lambda m: dm["mae"][m]["mae"], default=None)
        line = "  ".join("{}:{}".format(m, dm["mae"][m]["mae"]) for m in dm["mae"])
        print("  {:4} {}   best={}".format(prop, line, best))

    print("\n=== opponent matchup (point-in-time; pace NOT double-counted) ===")
    for prop in RATE_PROPS:
        m = matchup_eval(history, prop)
        if not m:
            continue
        rv, gv = m["raw_vs_neutral"], m["regular_vs_neutral"]
        print("  {:4} MAE neutral {} raw {} regular {} | raw {} regular {}".format(
            prop, m["mae"]["neutral"], m["mae"]["raw"], m["mae"]["regular"],
            "(sig)" if rv and rv["significant"] else "(ns)",
            "(sig)" if gv and gv["significant"] else "(ns)"))


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV)
