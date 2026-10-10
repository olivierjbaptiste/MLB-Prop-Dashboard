#!/usr/bin/env python3
"""
nba_model.py — Diamond Analytics: NBA projection model (ESPN-only, v1).

Takes the snapshot built by nba.py (nba_snapshot.json) and projects each
player's box score: Points (headline), Rebounds, Assists, 3PM, and PRA.

This module is PURE MATH — no network. It reads the committed snapshot, so
it runs anywhere (your Windows box included): `python nba_model.py`.

Honesty about what this v1 can and can't see:
  • ESPN gives us season averages + last-5 form + minutes + injury status.
    That covers the biggest accuracy drivers EXCEPT opponent pace and
    positional defense — those need stats.nba.com, which is IP-blocked from
    the runner. So the pace/defense multiplier is a STUB (=1.0) for now, and
    the code is written so dropping that layer in later touches one function
    (`matchup_factor`). Everything else is real.
  • Minutes is the #1 lever, so projections are RATE-based: we compute
    per-minute production, blend season with recent form, then multiply by
    projected minutes. If a player's role/minutes change, the projection
    scales with it — which is the whole point.
  • Early-season guard: with few current-season games, last-5 is preseason
    noise, so the blend leans on the season baseline until a real sample
    builds up. Anyone flagged OUT is excluded and marked.
"""

import os
import json

SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_snapshot.json")

# ── tunables (all in one place so they're easy to calibrate) ──────────
W_SEASON       = 0.60   # weight on season baseline rate
W_L5           = 0.40   # weight on last-5 recent-form rate
MIN_GP_FOR_L5  = 3      # need this many games before trusting last-5 at all
BENCH_MIN      = 12.0   # under this projected minutes => low-confidence/bench
STARTER_MIN    = 28.0   # at/above this => starter-grade confidence

OUT_WORDS      = ("out", "suspend", "inactive", "not with team")  # => won't play
FLAG_WORDS     = ("day-to-day", "game time", "game-time", "questionable",
                  "doubtful", "probable", "gtd")                   # => playable, flagged


# ── small helpers ─────────────────────────────────────────────────────
def _rate(total, minutes):
    """Per-minute rate; 0 if we can't form one."""
    if total and minutes and minutes > 0:
        return total / minutes
    return 0.0

def _blend(season, l5, gp):
    """Blend a season rate with a last-5 rate, with an early-season guard."""
    s = season or 0.0
    if not l5 or not gp or gp < MIN_GP_FOR_L5:
        return s
    return W_SEASON * s + W_L5 * l5

def _status_bucket(status):
    """Return 'out' | 'flag' | 'ok' from an ESPN status string."""
    if not status:
        return "ok"
    s = status.lower()
    if any(w in s for w in OUT_WORDS):
        return "out"
    if any(w in s for w in FLAG_WORDS):
        return "flag"
    return "ok"


# ── matchup layer (team-level opponent defense, from ESPN) ────────────
MATCHUP_CLAMP = 0.15   # never let one matchup swing a projection more than ±15%

# which team_defense field drives which stat
_DEF_FIELD = {"pts": "opp_pts", "reb": "opp_reb",
              "ast": "opp_ast", "tpm": "opp_tpm"}


def _opponent_abb(p, data):
    """The abbreviation of the team this player faces today, or None."""
    tid = str(p.get("team_id"))
    for g in data.get("games", []):
        if str(g.get("home_id")) == tid:
            return g.get("away_abb")
        if str(g.get("away_id")) == tid:
            return g.get("home_abb")
    return None


def _league_avgs(td):
    """League-average opponent values across all teams we have defense for."""
    avg = {}
    for field in _DEF_FIELD.values():
        vals = [r[field] for r in td.values()
                if isinstance(r, dict) and r.get(field)]
        avg[field] = (sum(vals) / len(vals)) if vals else None
    return avg


def matchup_factors(player, data):
    """
    Per-stat multiplier from the opponent's team defense: a team that allows
    more of a stat than the league average pushes that projection up, and a
    stingy team pushes it down. Clamped to ±15% so one odd number can't blow
    up a line. Falls back to a neutral 1.0 whenever the data isn't there, so
    the board never breaks when ESPN's team stats are missing.

    This is the team-level (Tier 1) version. Positional defense (points
    allowed to a player's position specifically) is the Tier 2 upgrade and
    would refine these same factors once a stats.nba.com feed exists.
    """
    out = {"pts": 1.0, "reb": 1.0, "ast": 1.0, "tpm": 1.0}
    td = data.get("team_defense") or {}
    if not td:
        return out
    opp = _opponent_abb(player, data)
    if not opp or opp not in td:
        return out
    avg = data.get("_league_avg")
    if avg is None:
        avg = _league_avgs(td)
        data["_league_avg"] = avg    # cache on the data dict for the whole run
    for stat, field in _DEF_FIELD.items():
        a, v = avg.get(field), td[opp].get(field)
        if a and v:
            f = v / a
            out[stat] = max(1 - MATCHUP_CLAMP, min(1 + MATCHUP_CLAMP, f))
    return out


# ── the projection ────────────────────────────────────────────────────
def project_player(p, data):
    """Attach proj_* fields to one player dict. Returns the same dict."""
    bucket = _status_bucket(p.get("status"))

    gp   = p.get("gp") or 0
    mpg  = p.get("mpg") or 0.0
    minl = p.get("min_l5") or 0.0

    # projected minutes: blend season & recent, early-season guarded
    proj_min = _blend(mpg, minl, gp)

    # per-minute rates, blended, for each counting stat
    def proj_stat(season_tot, l5_tot):
        r_season = _rate(season_tot, mpg)
        r_l5     = _rate(l5_tot, minl)
        r        = _blend(r_season, r_l5, gp)
        return proj_min * r

    mf = matchup_factors(p, data)

    proj_pts = proj_stat(p.get("ppg"), p.get("ppg_l5")) * mf["pts"]
    proj_reb = proj_stat(p.get("rpg"), p.get("rpg_l5")) * mf["reb"]
    proj_ast = proj_stat(p.get("apg"), p.get("apg_l5")) * mf["ast"]
    proj_tpm = proj_stat(p.get("tpm"), None)            * mf["tpm"]  # no L5 3PM yet
    proj_pra = proj_pts + proj_reb + proj_ast

    # expose the opponent + factors so the board can show WHY a line moved
    p["opp"] = _opponent_abb(p, data)
    p["mf_pts"] = round(mf["pts"], 3)
    p["mf_reb"] = round(mf["reb"], 3)
    p["mf_ast"] = round(mf["ast"], 3)
    p["mf_tpm"] = round(mf["tpm"], 3)

    if bucket == "out":
        proj_min = proj_pts = proj_reb = proj_ast = proj_tpm = proj_pra = 0.0

    # confidence
    if bucket == "out":
        conf = "out"
    elif gp < 5:
        conf = "low"            # tiny current-season sample (e.g. preseason)
    elif proj_min < BENCH_MIN:
        conf = "low"
    elif proj_min >= STARTER_MIN and gp >= 20:
        conf = "high"
    else:
        conf = "med"
    if bucket == "flag" and conf == "high":
        conf = "med"            # a GTD tag caps confidence

    p["proj_min"] = round(proj_min, 1)
    p["proj_pts"] = round(proj_pts, 1)
    p["proj_reb"] = round(proj_reb, 1)
    p["proj_ast"] = round(proj_ast, 1)
    p["proj_tpm"] = round(proj_tpm, 1)
    p["proj_pra"] = round(proj_pra, 1)
    p["confidence"] = conf
    p["playable"] = (bucket != "out")
    p["status_flag"] = bucket   # 'ok' | 'flag' | 'out'
    return p


def project_all(data):
    """Project every player in a snapshot dict (mutates & returns the list)."""
    players = data.get("players", [])
    for p in players:
        project_player(p, data)
    return players


def board(players, prop="proj_pts", playable_only=True, top=None):
    """Return players sorted by a projection field, highest first."""
    rows = [p for p in players if (p.get("playable") or not playable_only)]
    rows.sort(key=lambda p: p.get(prop, 0.0), reverse=True)
    return rows[:top] if top else rows


# ── run directly: print boards from the local snapshot ────────────────
if __name__ == "__main__":
    if not os.path.exists(SNAP):
        raise SystemExit(f"No snapshot found at {SNAP}. Run the NBA Data Pull "
                         f"workflow first (it commits nba_snapshot.json).")

    with open(SNAP, "r", encoding="utf-8") as f:
        data = json.load(f)

    players = project_all(data)
    print("=" * 78)
    print(f"NBA PROJECTIONS  —  slate {data.get('today')}  "
          f"({len(data.get('games', []))} games, {len(players)} players)")
    print("=" * 78)

    def show(prop, label):
        print(f"\n  === {label} (top 12) ===")
        print(f"    {'PLAYER':22s} {'TM':4s} {'MIN':>5s} {'PROJ':>6s}  CONF  FLAG")
        for p in board(players, prop, top=12):
            tag = "" if p["status_flag"] == "ok" else f"  {p.get('status')}"
            print(f"    {p['name']:22s} {p['team']:4s} "
                  f"{p['proj_min']:5.1f} {p[prop]:6.1f}  {p['confidence']:4s}{tag}")

    show("proj_pts", "POINTS")
    show("proj_pra", "PRA (pts+reb+ast)")
    show("proj_reb", "REBOUNDS")
    show("proj_ast", "ASSISTS")
    show("proj_tpm", "3-POINTERS MADE")

    print("\n" + "=" * 78)
    print("Note: pace/defense multiplier is 1.0 (stub) until the stats.nba.com")
    print("pull is added. Preseason minutes are noisy — treat low-conf rows softly.")
    print("=" * 78)
