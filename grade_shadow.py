#!/usr/bin/env python3
"""
grade_shadow.py — Phase 2E/3: grade the NFL shadow prediction log against real
final player statistics, and compare v1 vs v2.

Design guarantees
-----------------
  • READ-ONLY on the prediction log. This module never rewrites, reorders, or
    deletes predictions_log.jsonl. It only appends a separate report under
    grades/ (git-ignored).
  • Real actuals only. Final stats come from nflverse weekly release CSVs
    (stats_player_week_<season>.csv) — the same public source the model is built
    on. Missing actuals are skipped, never invented.
  • Identical eligible set for both models. A (season, week, player_id, prop)
    observation is graded only if BOTH v1 and v2 produced a row for it AND a real
    actual exists. v1 and v2 are scored on exactly the same observations.
  • Same event. Both models are scored on P(Over) against the realized
    over/under outcome, using the full O/U distribution each logged.
  • Correlated observations handled. Confidence intervals on the v1−v2 metric
    differences come from a player-clustered bootstrap (resample players, not
    rows), because a player's weeks are correlated.
  • Separated by prop and model version. Every metric is reported per prop and
    per model_version; nothing is pooled across props silently.
  • No profitability claims on thin samples. Paper ROI from the captured odds is
    reported as a diagnostic with its sample size and is explicitly flagged
    "insufficient sample" below MIN_ROI_N. Profit is never claimed from a small
    sample or from statistical calibration alone.

Metrics per (prop, model): n, Brier(over), log loss(over), ECE (calibration),
projection error (MAE/RMSE/bias), prop W/L/P on the logged pick, and paper ROI.
UTC throughout.
"""

import os
import csv
import json
import math
import random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "predictions_log.jsonl")
OUT_DIR = os.path.join(HERE, "grades")

OU_PROPS = ("pass_yds", "rush_yds", "rec_yds", "rec")
FIELD = {"pass_yds": "passing_yards", "rush_yds": "rushing_yards",
         "rec_yds": "receiving_yards", "rec": "receptions"}

MIN_ROI_N = 200          # below this, ROI is a diagnostic, NOT a profitability claim
_EPS = 1e-9
_CLIP = 1e-6             # log-loss probability clip


# ─────────────────────────── small helpers ────────────────────────────
def _num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def _decimal(american):
    """American odds → decimal payout multiplier on a 1u stake (profit)."""
    a = _num(american)
    if a is None:
        return None
    return (a / 100.0) if a > 0 else (100.0 / abs(a))


def _outcome(actual, line):
    """Realized side: 'over' / 'under' / 'push' (integer line exact tie)."""
    if actual is None or line is None:
        return None
    if abs(actual - line) < _EPS:
        return "push"
    return "over" if actual > line else "under"


# ─────────────────────────── load inputs ──────────────────────────────
def load_log(path=LOG_PATH):
    """Read the append-only log. Returns list of dicts. Never writes."""
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                pass            # a malformed line is skipped, never repaired
    return recs


def load_actuals(seasons, fetch=None):
    """{(player_id, season, week): {field: value}} from nflverse weekly CSVs.
    `fetch(season) -> list[dict rows]` is injectable for offline testing."""
    if fetch is None:
        import nfl
        fetch = lambda s: nfl._fetch_csv(
            "{}/stats_player/stats_player_week_{}.csv".format(nfl.NFLV, s))
    out = {}
    for season in sorted(set(int(s) for s in seasons)):
        for r in fetch(season) or []:
            pid = r.get("player_id")
            wk = _num(r.get("week"))
            if not pid or wk is None:
                continue
            key = (pid, season, int(wk))
            out[key] = {fld: _num(r.get(col)) for fld, col in FIELD.items()}
    return out


# ─────────────────────────── core grading ─────────────────────────────
def _latest_per_obs(records, model_version):
    """One row per (season, week, player_id, prop) for a model: the latest
    pregame capture (max snapshot_ts), so a moved line isn't double-counted."""
    best = {}
    for r in records:
        if r.get("model_version") != model_version:
            continue
        if r.get("prop") not in OU_PROPS:
            continue
        if r.get("line") is None or r.get("p_over") is None:
            continue
        key = (r.get("player_id"), int(r.get("season")), int(r.get("week") or 0), r.get("prop"))
        ts = r.get("snapshot_ts") or 0
        if key not in best or ts > (best[key].get("snapshot_ts") or 0):
            best[key] = r
    return best


def _score_row(r, actual):
    """Per-observation scores for one model row given the realized actual."""
    line = _num(r.get("line"))
    oc = _outcome(actual, line)
    if oc is None:
        return None
    p_over = _num(r.get("p_over"))
    p_over = min(1.0, max(0.0, p_over))
    y = None if oc == "push" else (1.0 if oc == "over" else 0.0)   # push excluded from prob scoring
    brier = logloss = None
    if y is not None:
        brier = (p_over - y) ** 2
        pc = min(1 - _CLIP, max(_CLIP, p_over))
        logloss = -(y * math.log(pc) + (1 - y) * math.log(1 - pc))
    # projection error
    proj = _num(r.get("projection"))
    perr = (proj - actual) if proj is not None else None
    # the logged bet (model's own pick), graded on the real outcome
    side = (r.get("side") or "").lower()
    price = r.get("price")
    dec = _decimal(price)
    result, profit = None, None
    if side in ("over", "under") and dec is not None:
        if oc == "push":
            result, profit = "push", 0.0
        elif side == oc:
            result, profit = "win", dec
        else:
            result, profit = "loss", -1.0
    return {"p_over": p_over, "y": y, "brier": brier, "logloss": logloss,
            "perr": perr, "result": result, "profit": profit, "outcome": oc,
            "player_id": r.get("player_id")}


def _agg(scored):
    """Aggregate a list of per-obs score dicts into metrics."""
    probs = [s for s in scored if s["y"] is not None]
    n = len(scored)
    briers = [s["brier"] for s in probs]
    lls = [s["logloss"] for s in probs]
    perrs = [s["perr"] for s in scored if s["perr"] is not None]
    bets = [s for s in scored if s["result"] is not None]
    w = sum(1 for s in bets if s["result"] == "win")
    l = sum(1 for s in bets if s["result"] == "loss")
    pu = sum(1 for s in bets if s["result"] == "push")
    staked = sum(1.0 for s in bets if s["result"] != "push")
    profit = sum(s["profit"] for s in bets)
    out = {
        "n_obs": n, "n_scored": len(probs),
        "brier": round(sum(briers) / len(briers), 5) if briers else None,
        "log_loss": round(sum(lls) / len(lls), 5) if lls else None,
        "ece": _ece(probs),
        "proj_mae": round(sum(abs(e) for e in perrs) / len(perrs), 2) if perrs else None,
        "proj_rmse": round(math.sqrt(sum(e * e for e in perrs) / len(perrs)), 2) if perrs else None,
        "proj_bias": round(sum(perrs) / len(perrs), 2) if perrs else None,
        "bets": {"win": w, "loss": l, "push": pu},
    }
    roi = (profit / staked) if staked > 0 else None
    out["paper_roi"] = {
        "n_bets": len(bets), "units_staked": round(staked, 1),
        "units_profit": round(profit, 2),
        "roi_pct": round(100 * roi, 2) if roi is not None else None,
        "note": ("DIAGNOSTIC ONLY — insufficient sample, not a profitability claim"
                 if len(bets) < MIN_ROI_N else
                 "paper ROI on captured odds — not a realized or guaranteed return"),
    }
    return out


def _ece(probs, bins=10):
    """Expected calibration error on P(over) vs realized over-rate."""
    if not probs:
        return None
    buckets = defaultdict(list)
    for s in probs:
        b = min(bins - 1, int(s["p_over"] * bins))
        buckets[b].append(s)
    n = len(probs)
    ece = 0.0
    for b, items in buckets.items():
        conf = sum(s["p_over"] for s in items) / len(items)
        acc = sum(s["y"] for s in items) / len(items)
        ece += (len(items) / n) * abs(conf - acc)
    return round(ece, 4)


def _cluster_ci(pairs, metric, iters=1000, seed=13):
    """Player-clustered bootstrap 95% CI for mean (v2−v1) of a per-obs metric.
    `pairs`: list of (player_id, v1_val, v2_val) with both non-None."""
    data = [(pid, b - a) for pid, a, b in pairs if a is not None and b is not None]
    if len(data) < 2:
        return None
    by_player = defaultdict(list)
    for pid, d in data:
        by_player[pid].append(d)
    players = list(by_player.keys())
    obs_mean = sum(d for _, d in data) / len(data)
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        samp = []
        for _ in range(len(players)):
            samp.extend(by_player[rng.choice(players)])
        if samp:
            means.append(sum(samp) / len(samp))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return {"mean_diff": round(obs_mean, 5), "ci95": [round(lo, 5), round(hi, 5)],
            "n_obs": len(data), "n_players": len(players),
            "significant": bool(lo > 0 or hi < 0)}


def grade(records, actuals, v1_version="nfl-2026.1", v2_version="nfl-2026.2-candidate"):
    """Pure core: log records + actuals → comparison report. No I/O, no mutation."""
    v1 = _latest_per_obs(records, v1_version)
    v2 = _latest_per_obs(records, v2_version)
    # identical eligible set: present for BOTH models AND actual available
    common = []
    for key in set(v1) & set(v2):
        pid, season, week, prop = key
        act = actuals.get((pid, season, week))
        if not act:
            continue
        a = act.get(prop)          # actuals are keyed by short prop name
        if a is None:
            continue
        common.append((key, a))

    by_prop = {p: {"v1": [], "v2": [], "pairs_brier": [], "pairs_ll": []} for p in OU_PROPS}
    for key, a in common:
        pid, season, week, prop = key
        s1 = _score_row(v1[key], a)
        s2 = _score_row(v2[key], a)
        if not s1 or not s2:
            continue
        by_prop[prop]["v1"].append(s1)
        by_prop[prop]["v2"].append(s2)
        by_prop[prop]["pairs_brier"].append((pid, s1["brier"], s2["brier"]))
        by_prop[prop]["pairs_ll"].append((pid, s1["logloss"], s2["logloss"]))

    report = {"eligible_observations": len(common), "by_prop": {},
              "v1_version": v1_version, "v2_version": v2_version,
              "disclaimer": "All figures are model/paper diagnostics on captured "
                            "odds — not verified or guaranteed betting returns."}
    for prop in OU_PROPS:
        blk = by_prop[prop]
        if not blk["v1"]:
            continue
        report["by_prop"][prop] = {
            "n": len(blk["v1"]),
            "v1": _agg(blk["v1"]),
            "v2": _agg(blk["v2"]),
            "v2_minus_v1_brier": _cluster_ci(blk["pairs_brier"], "brier"),
            "v2_minus_v1_log_loss": _cluster_ci(blk["pairs_ll"], "logloss"),
        }
    return report


# ─────────────────────────── disk wrapper ─────────────────────────────
def run(path=LOG_PATH, out_dir=OUT_DIR, fetch=None):
    """Load the log, pull real actuals for the seasons present, grade, and write
    a report under grades/ (git-ignored). The log itself is never modified."""
    records = load_log(path)
    if not records:
        print("No predictions logged yet — nothing to grade.")
        return None
    seasons = {int(r["season"]) for r in records if r.get("season")}
    actuals = load_actuals(seasons, fetch=fetch)
    report = grade(records, actuals)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "grade_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    _print(report)
    print("\nwrote {}  (log left unchanged: {} rows)".format(out, len(records)))
    return report


def _print(rep):
    print("Eligible observations graded (present for BOTH models): {}"
          .format(rep["eligible_observations"]))
    for prop, b in rep["by_prop"].items():
        print("\n{}  (n={})".format(prop, b["n"]))
        for m in ("v1", "v2"):
            a = b[m]
            roi = a["paper_roi"]
            print("  {:3} brier={} logloss={} ece={} | W-L-P {}-{}-{} | ROI {}% [{}]"
                  .format(m, a["brier"], a["log_loss"], a["ece"],
                          a["bets"]["win"], a["bets"]["loss"], a["bets"]["push"],
                          roi["roi_pct"], roi["n_bets"]))
        for label, key in (("Δbrier", "v2_minus_v1_brier"), ("Δlogloss", "v2_minus_v1_log_loss")):
            d = b[key]
            if d:
                print("  {} v2−v1: {} CI{} {}".format(
                    label, d["mean_diff"], d["ci95"],
                    "(significant)" if d["significant"] else "(ns)"))


if __name__ == "__main__":
    run()
