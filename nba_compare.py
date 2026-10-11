#!/usr/bin/env python3
"""
nba_compare.py — Phase 3C Task 5: NBA v1-vs-v2 comparison (offline / shadow).

For each playable player with a concrete sportsbook offer, show: the v1 point
projection, the v2 point projection, the v2 candidate calibrated over/push/under
probabilities, the exact offered line + odds, estimated EV, data freshness, and
model versions.

Read-only. Does NOT run on the live site and does NOT change any production
formula or recommendation. The snapshot must already be projected by BOTH
nba_model.project_all (v1) and nba_model_v2.project_all_v2 (v2). EV uses the exact
concrete offer only — lines are never mixed.
"""

import datetime

import nba_model
import nba_model_v2
import nba_prob
import nba_ev

OU_PROPS = ("pts", "reb", "ast", "tpm", "pra")
# v2 projection field per prop (pra handled from components)
_V2 = {"pts": "proj_pts_v2", "reb": "proj_reb_v2", "ast": "proj_ast_v2",
       "tpm": "proj_tpm_v2", "pra": "proj_pra_v2"}
_V1 = {"pts": "proj_pts", "reb": "proj_reb", "ast": "proj_ast",
       "tpm": "proj_tpm", "pra": "proj_pra"}


def _age_hours(data, now):
    ts = data.get("ts")
    if not ts:
        return None
    return round((now.timestamp() - ts) / 3600.0, 1)


def compare(data, params, now_iso=None):
    """Return comparison rows. `params` is fit({prop: pairs}) = {prop: params}."""
    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now_iso or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    age = _age_hours(data, now)
    odds = data.get("odds") or {}
    v1v = getattr(nba_model, "VERSION", "nba-v1")
    v2v = nba_model_v2.VERSION
    # map team_id -> game for event id
    games = {}
    for g in data.get("games", []):
        for k in (str(g.get("home_id")), str(g.get("away_id"))):
            games[k] = g

    rows = []
    for p in data.get("players", []):
        if not p.get("playable", True):
            continue
        book_lines = odds.get((p.get("name") or "").lower())
        if not book_lines:
            continue
        g = games.get(str(p.get("team_id")))
        event_id = g.get("game_id") if g else None
        for prop in OU_PROPS:
            v2_proj = p.get(_V2[prop])
            v1_proj = p.get(_V1[prop])
            ln = book_lines.get(prop)
            if v2_proj is None or not ln or not ln.get("offers"):
                continue
            for offer in ln["offers"]:
                o = dict(offer)
                o.setdefault("player", p.get("name"))
                o.setdefault("event_id", event_id)
                model = nba_prob.DEFAULT_MODEL_BY_PROP.get(prop, "empirical")
                ev = nba_ev.offer_ev(prop, v2_proj, o, model, params.get(prop), now)
                if ev is None:
                    continue
                rows.append({
                    "player": p.get("name"), "player_id": p.get("id"),
                    "event_id": event_id, "prop": prop,
                    "book": ev["book"], "line": ev["line"],
                    "v1_proj": v1_proj, "v2_proj": v2_proj,
                    "p_over": ev["p_over"], "p_push": ev["p_push"], "p_under": ev["p_under"],
                    "side": ev["side"], "odds": ev["price"], "ev_pct": ev["ev_pct"],
                    "model": model, "line_ts": ev["ts"],
                    "data_age_hours": age, "computed_at": now_iso,
                    "v1_version": v1v, "v2_version": v2v,
                    "note": "candidate calibrated estimate — not a production recommendation",
                })
    return rows


if __name__ == "__main__":
    import json
    import nba
    data = nba.read_snapshot() or {}
    if not data.get("players"):
        print("No NBA snapshot — run the NBA Data Pull first.")
    else:
        nba_model.project_all(data)
        nba_model_v2.project_all_v2(data)
        print("Note: probability params must be fit from history (nba_backtest). "
              "This CLI shows structure only.")
