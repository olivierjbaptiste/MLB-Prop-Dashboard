#!/usr/bin/env python3
"""
nba_grade.py — Phase 3D: grade the NBA shadow log against actual final stats.

READ-ONLY on the prediction log. Actuals come from a regenerated history CSV
(the corrected fetcher's output, which carries game_id), joined to predictions on
(player_id, game_id). Never rewrites or reorders the log.

What it grades, per prop and per model_version:
  • Projection accuracy — MAE / RMSE / bias — for BOTH v1 and v2, on the matched
    set of observations graded for both (so v1 vs v2 is apples-to-apples).
  • Probability quality (v2 ONLY) — Brier, log loss, calibration — evaluated
    against the EXACT RECORDED SPORTSBOOK LINE (not a statistical threshold), using
    the calibrated p_over that was logged pregame.
  • Betting result (v2 ONLY) — W / L / push on the recorded side at the recorded
    price, and paper ROI.

v1 has no probability model, so it is graded on projections only — v1 probabilities
are never invented. Pushes return the stake. Inactive players / missing actuals /
voids are skipped. Duplicate records are de-duped to the latest pregame snapshot.
Paper ROI below MIN_ROI_N is a diagnostic, never a profitability claim.
"""

import os
import csv
import json
import math
import random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "nba_predictions_log.jsonl")
OUT_DIR = os.path.join(HERE, "grades")

PROPS = ("pts", "reb", "ast", "tpm", "pra")
MIN_ROI_N = 200
_EPS = 1e-9
_CLIP = 1e-6


def _num(x):
    try:
        return None if x in (None, "") else float(x)
    except Exception:
        return None


def _decimal(american):
    a = _num(american)
    if a is None:
        return None
    return (a / 100.0) if a > 0 else (100.0 / abs(a))


def _outcome(actual, line):
    if actual is None or line is None:
        return None
    if abs(actual - line) < _EPS:
        return "push"
    return "over" if actual > line else "under"


# ───────────────────────── inputs ─────────────────────────────────────
def load_log(path=LOG_PATH):
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    return recs


def load_actuals(path, rows=None):
    """{(player_id, game_id): {pts,reb,ast,tpm,pra,min}} from a history CSV
    (needs a game_id column). `rows` can be injected for testing."""
    raw = rows
    if raw is None:
        raw = []
        if path and os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                raw = list(csv.DictReader(f))
    out = {}
    for r in raw:
        pid = r.get("player_id"); gid = r.get("game_id")
        if not pid or not gid:
            continue
        pts, reb, ast = _num(r.get("pts")), _num(r.get("reb")), _num(r.get("ast"))
        out[(pid, gid)] = {
            "pts": pts, "reb": reb, "ast": ast, "tpm": _num(r.get("tpm")),
            "min": _num(r.get("min")),
            "pra": None if None in (pts, reb, ast) else pts + reb + ast,
        }
    return out


# ───────────────────────── grading ────────────────────────────────────
def _latest(records, model_version):
    """One row per (player_id, event_id, prop) for a model: latest snapshot."""
    best = {}
    for r in records:
        if r.get("model_version") != model_version:
            continue
        if r.get("prop") not in PROPS:
            continue
        key = (r.get("player_id"), r.get("event_id"), r.get("prop"))
        ts = r.get("snapshot_ts") or 0
        if key not in best or ts > (best[key].get("snapshot_ts") or 0):
            best[key] = r
    return best


def _agg(pairs):
    pairs = [(p, a) for p, a in pairs if p is not None and a is not None]
    n = len(pairs)
    if not n:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    e = [p - a for p, a in pairs]
    return {"n": n, "mae": round(sum(abs(x) for x in e) / n, 3),
            "rmse": round(math.sqrt(sum(x * x for x in e) / n), 3),
            "bias": round(sum(e) / n, 3)}


def _ece(items, bins=10):
    if not items:
        return None
    buck = defaultdict(list)
    for p, y in items:
        buck[min(bins - 1, int(p * bins))].append((p, y))
    n = len(items); e = 0.0
    for b, it in buck.items():
        conf = sum(p for p, _ in it) / len(it); acc = sum(y for _, y in it) / len(it)
        e += len(it) / n * abs(conf - acc)
    return round(e, 4)


def _cluster_ci(triples, iters=600, seed=17):
    data = [(pid, m - b) for pid, m, b in triples if m is not None and b is not None]
    if len(data) < 2:
        return None
    by = defaultdict(list)
    for pid, d in data:
        by[pid].append(d)
    players = list(by)
    obs = sum(d for _, d in data) / len(data)
    rng = random.Random(seed); means = []
    for _ in range(iters):
        s = []
        for _ in range(len(players)):
            s.extend(by[rng.choice(players)])
        if s:
            means.append(sum(s) / len(s))
    means.sort()
    lo = means[int(0.025 * len(means))]; hi = means[int(0.975 * len(means)) - 1]
    return {"mean_abs_err_diff": round(obs, 4), "ci95": [round(lo, 4), round(hi, 4)],
            "n_obs": len(data), "n_players": len(players),
            "significant": bool(lo > 0 or hi < 0)}


def grade(records, actuals, v1_version="nba-v1", v2_version=None):
    if v2_version is None:
        import nba_model_v2
        v2_version = nba_model_v2.VERSION
    v1 = _latest(records, v1_version)
    v2 = _latest(records, v2_version)
    report = {"by_prop": {}, "v1_version": v1_version, "v2_version": v2_version,
              "disclaimer": "Projections graded for both models; probabilities/ROI "
                            "graded for v2 only against the EXACT recorded sportsbook "
                            "line. Paper results are diagnostics, not verified profit."}

    for prop in PROPS:
        # matched projection set: present for both models AND actual available
        matched = []
        for key in set(v1) & set(v2):
            pid, gid, pr = key
            if pr != prop:
                continue
            act = actuals.get((pid, gid))
            if not act:
                continue
            a = act.get(prop)
            if a is None:                     # inactive / missing actual → skip
                continue
            matched.append((key, a))
        if not matched:
            continue
        v1_pairs = [(v1[k].get("projection"), a) for k, a in matched]
        v2_pairs = [(v2[k].get("projection"), a) for k, a in matched]
        proj_ci = _cluster_ci([(k[0],
                                abs(v2[k]["projection"] - a) if v2[k].get("projection") is not None else None,
                                abs(v1[k]["projection"] - a) if v1[k].get("projection") is not None else None)
                               for k, a in matched])

        # v2 probability + betting grading against the RECORDED line
        prob_items = []; briers = []; lls = []
        bets = {"win": 0, "loss": 0, "push": 0}; staked = 0.0; profit = 0.0
        for k, a in matched:
            r = v2[k]
            line = _num(r.get("line"))
            p_over = _num(r.get("p_over"))
            if line is None or p_over is None:
                continue
            oc = _outcome(a, line)
            if oc is None:
                continue
            if oc != "push":
                y = 1.0 if oc == "over" else 0.0
                pc = min(1 - _CLIP, max(_CLIP, p_over))
                briers.append((p_over - y) ** 2)
                lls.append(-(y * math.log(pc) + (1 - y) * math.log(1 - pc)))
                prob_items.append((p_over, y))
            side = (r.get("side") or "").lower()
            dec = _decimal(r.get("price"))
            if side in ("over", "under") and dec is not None:
                if oc == "push":
                    bets["push"] += 1
                elif side == oc:
                    bets["win"] += 1; staked += 1; profit += dec
                else:
                    bets["loss"] += 1; staked += 1; profit += -1.0

        roi = (profit / staked) if staked > 0 else None
        report["by_prop"][prop] = {
            "n_matched": len(matched),
            "v1_projection": _agg(v1_pairs),
            "v2_projection": _agg(v2_pairs),
            "v2_minus_v1_proj_abs_err": proj_ci,
            "v2_prob_vs_recorded_line": {
                "n": len(prob_items),
                "brier": round(sum(briers) / len(briers), 5) if briers else None,
                "log_loss": round(sum(lls) / len(lls), 5) if lls else None,
                "ece": _ece(prob_items),
            },
            "v2_paper_bets": {
                "record": bets, "units_staked": round(staked, 1),
                "units_profit": round(profit, 2),
                "roi_pct": round(100 * roi, 2) if roi is not None else None,
                "note": ("DIAGNOSTIC — insufficient sample, not a profitability claim"
                         if (bets["win"] + bets["loss"]) < MIN_ROI_N else
                         "paper ROI on recorded odds — not a verified return"),
            },
        }
    return report


def run(log_path=LOG_PATH, actuals_path=None, out_dir=OUT_DIR):
    records = load_log(log_path)
    if not records:
        print("No NBA predictions logged yet — nothing to grade.")
        return None
    actuals = load_actuals(actuals_path)
    if not actuals:
        print("No actuals available — provide a regenerated history CSV with game_id.")
        return None
    report = grade(records, actuals)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "nba_grade_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    _print(report)
    print("\nwrote {}  (log unchanged: {} rows)".format(out, len(records)))
    return report


def _print(rep):
    for prop, b in rep["by_prop"].items():
        print("\n{}  (matched n={})".format(prop.upper(), b["n_matched"]))
        print("  v1 proj MAE {} | v2 proj MAE {}".format(
            b["v1_projection"]["mae"], b["v2_projection"]["mae"]))
        pr = b["v2_prob_vs_recorded_line"]
        print("  v2 prob vs recorded line: brier={} logloss={} ece={} (n={})".format(
            pr["brier"], pr["log_loss"], pr["ece"], pr["n"]))
        pb = b["v2_paper_bets"]
        print("  v2 paper: W-L-P {}-{}-{} ROI {}% [{}]".format(
            pb["record"]["win"], pb["record"]["loss"], pb["record"]["push"],
            pb["roi_pct"], pb["note"]))


if __name__ == "__main__":
    import sys
    run(actuals_path=sys.argv[1] if len(sys.argv) > 1 else None)
