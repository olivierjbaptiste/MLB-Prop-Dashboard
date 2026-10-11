#!/usr/bin/env python3
"""
compare_models.py — Phase 2E: safe v1-vs-v2 comparison (offline / shadow).

For the SAME player, game, book, prop, line and odds, computes v1 and v2
probabilities and EV and their difference. v1 and v2 share the point
projection; they differ ONLY in the probability layer (v1 = fixed-CV normal,
v2 = calibrated empirical). EV uses the exact concrete sportsbook offer
preserved from Phase 1 — never a synthetic line/price mix.

This module does NOT run on the live site and does NOT change any live formula.
It reads an already-projected snapshot and reports a comparison. Rows with
missing/stale offers are flagged, not fabricated.
"""

import nfl_model
import model_v2

OU_PROPS = ("pass_yds", "rush_yds", "rec_yds", "rec")


def _v1_probs(prop, proj, line):
    sigma = max(1.0, nfl_model._CV[prop] * proj)
    return nfl_model._ou_probs(proj, line, sigma)        # (over, push, under)


def _best_side(p_over, p_push, p_under, over_price, under_price):
    cands = []
    if over_price is not None:
        cands.append(("Over", p_over, nfl_model._ev(p_over, p_push, over_price), over_price))
    if under_price is not None:
        cands.append(("Under", p_under, nfl_model._ev(p_under, p_push, under_price), under_price))
    cands = [c for c in cands if c[2] is not None]
    if not cands:
        return None
    side, prob, ev, price = max(cands, key=lambda c: c[2])
    return {"side": side, "prob": round(prob, 4), "price": price, "ev_pct": round(ev * 100, 2)}


def compare(data, dist="empirical", now_iso=None):
    """Return comparison rows for every playable player/prop that has concrete
    offers in the snapshot. `data` must already be projected (nfl_model.project_all)."""
    import datetime
    now_iso = now_iso or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    odds = data.get("odds") or {}
    rows = []
    for p in data.get("players", []):
        if not p.get("playable"):
            continue
        book_lines = odds.get((p.get("name") or "").lower())
        if not book_lines:
            continue
        for prop in OU_PROPS:
            proj = p.get("proj_" + prop)
            ln = book_lines.get(prop)
            if proj is None or not ln or not ln.get("offers"):
                continue
            for offer in ln["offers"]:
                line = offer.get("line")
                if line is None:
                    continue
                # v1
                o1, pu1, u1 = _v1_probs(prop, proj, line)
                v1 = _best_side(o1, pu1, u1, offer.get("over_price"), offer.get("under_price"))
                # v2 (shares proj, different probability layer)
                v2 = model_v2.offer_ev(prop, proj, offer, dist)
                if not v1 or not v2:
                    continue
                rows.append({
                    "player": p.get("name"), "player_id": p.get("id"),
                    "game": "{}@{}".format(p.get("opp"), p.get("team")) if not p.get("home")
                            else "{}@{}".format(p.get("team"), p.get("opp")),
                    "prop": prop, "book": offer.get("book"), "line": line,
                    "v1_proj": proj, "v1_side": v1["side"], "v1_prob": v1["prob"],
                    "v1_ev_pct": v1["ev_pct"],
                    "v2_side": v2["side"], "v2_prob": v2["model_prob"], "v2_ev_pct": v2["ev_pct"],
                    "p_diff": round(v2["model_prob"] - v1["prob"], 4),
                    "ev_diff": round(v2["ev_pct"] - v1["ev_pct"], 2),
                    "v1_version": getattr(nfl_model, "VERSION", "nfl-2026.1"),
                    "v2_version": model_v2.VERSION, "dist": dist,
                    "line_ts": offer.get("ts"), "computed_at": now_iso,
                    "note": "model estimates — not verified betting opportunities",
                })
    return rows


if __name__ == "__main__":
    import json
    import nfl
    data = nfl.read_snapshot() or {}
    if not data.get("players"):
        print("No snapshot — run the NFL Data Pull first.")
    else:
        nfl_model.project_all(data)
        rows = compare(data)
        print("v1-vs-v2 comparison rows: {}".format(len(rows)))
        for r in rows[:10]:
            print(json.dumps(r))
