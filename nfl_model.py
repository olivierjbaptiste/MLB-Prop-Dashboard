#!/usr/bin/env python3
"""
nfl_model.py — Diamond Analytics: NFL projection model.

Projects the four headline NFL props from the nfl.py snapshot:
  • Passing Yards   (QB)
  • Rushing Yards   (RB, mobile QB)
  • Receiving Yards (WR / TE / RB)
  • Receptions      (WR / TE / RB)

Each projection = blended volume/production  ×  opponent positional defense
×  game script (the Vegas spread & total).  Pure math, no network — runs
anywhere off the committed snapshot.

Game script is the NFL-specific lever: underdogs throw more (pass/receiving
up, rushing down); favorites run more late (rushing up). The total scales the
whole scoring environment.
"""

W_SEASON = 0.55
W_L3     = 0.45
CLAMP    = 0.20          # matchup factor capped at ±20%
GS_CLAMP = 0.18          # game-script factor capped at ±18%
LEAGUE_TOTAL = 45.0      # ~average NFL game total, for the pace scale

# which defense metric drives each (prop, position)
_DEF = {
    ("rec_yds", "WR"): "rec_yds_WR", ("rec_yds", "TE"): "rec_yds_TE", ("rec_yds", "RB"): "rec_yds_RB",
    ("rec",     "WR"): "rec_WR",     ("rec",     "TE"): "rec_TE",     ("rec",     "RB"): "rec_RB",
    ("rush_yds","RB"): "rush_yds_RB",
    ("pass_yds","QB"): "pass_yds",
}
_ALL_METRICS = ["pass_yds","rush_yds_RB","rec_yds_WR","rec_yds_TE","rec_yds_RB","rec_WR","rec_TE","rec_RB"]

OUT_WORDS  = ("out", "injured reserve", "ir", "suspend", "doubtful")
FLAG_WORDS = ("questionable", "day-to-day", "game time", "limited")


def _clamp(x, c): return max(1 - c, min(1 + c, x))

def _league_avgs(td):
    avg = {}
    for m in _ALL_METRICS:
        vals = [r[m] for r in td.values() if isinstance(r, dict) and r.get(m)]
        avg[m] = (sum(vals) / len(vals)) if vals else None
    return avg

def _status_bucket(status):
    if not status: return "ok"
    s = status.lower()
    if any(w in s for w in OUT_WORDS):  return "out"
    if any(w in s for w in FLAG_WORDS): return "flag"
    return "ok"

def _blend(season, l3):
    season = season or 0.0
    if not l3:
        return season
    return W_SEASON * season + W_L3 * l3


def game_script(p):
    """Return per-area multipliers {'pass','rush','rec'} from spread & total."""
    gs = {"pass": 1.0, "rush": 1.0, "rec": 1.0}
    s = p.get("spread")
    if s is not None:
        try:
            s = max(-14.0, min(14.0, float(s)))   # team spread, negative = favored
            gs["pass"] = 1 + 0.010 * s            # underdog (s>0) throws more
            gs["rec"]  = 1 + 0.008 * s
            gs["rush"] = 1 - 0.010 * s            # favorite (s<0) runs more
        except Exception:
            pass
    tot = p.get("total")
    if tot:
        try:
            tf = max(0.90, min(1.12, float(tot) / LEAGUE_TOTAL))
            for k in gs: gs[k] *= tf
        except Exception:
            pass
    for k in gs:
        gs[k] = _clamp(gs[k], GS_CLAMP)
    return gs


def matchup(player, avg, td, prop):
    pos = player.get("pos")
    key = _DEF.get((prop, pos))
    if not key: return 1.0
    opp = player.get("opp")
    if not opp or opp not in td: return 1.0
    a, v = avg.get(key), td[opp].get(key)
    if a and v:
        return _clamp(v / a, CLAMP)
    return 1.0


def project_player(p, data, avg):
    td = data.get("team_defense") or {}
    gs = game_script(p)
    bucket = _status_bucket(p.get("status"))
    pos = p.get("pos")

    out = {}
    def put(prop, base, area):
        if base is None: return
        val = _blend(base, p.get(prop + "_l3")) * matchup(p, avg, td, prop) * gs[area]
        out["proj_" + prop] = 0.0 if bucket == "out" else round(val, 1)

    if pos == "QB":
        put("pass_yds", p.get("pass_yds"), "pass")
        if (p.get("rush_yds") or 0) >= 12:      # mobile QB rushing prop
            put("rush_yds", p.get("rush_yds"), "rush")
    elif pos == "RB":
        put("rush_yds", p.get("rush_yds"), "rush")
        put("rec_yds",  p.get("rec_yds"),  "rec")
        put("rec",      p.get("rec"),      "rec")
    elif pos in ("WR", "TE"):
        put("rec_yds", p.get("rec_yds"), "rec")
        put("rec",     p.get("rec"),     "rec")

    g = p.get("g") or 0
    if bucket == "out":
        conf = "out"
    elif g <= 2:
        conf = "low"
    elif bucket == "flag":
        conf = "med"
    elif g >= 4:
        conf = "high"
    else:
        conf = "med"

    p.update(out)
    p["gs_pass"] = round(gs["pass"], 3)
    p["gs_rush"] = round(gs["rush"], 3)
    p["gs_rec"]  = round(gs["rec"], 3)
    p["confidence"] = conf
    p["playable"] = (bucket != "out")
    p["status_flag"] = bucket
    return p


def project_all(data):
    td = data.get("team_defense") or {}
    avg = _league_avgs(td)
    data["_league_avg"] = avg
    for p in data.get("players", []):
        project_player(p, data, avg)
    return data.get("players", [])


def board(players, prop, playable_only=True, top=None):
    field = "proj_" + prop
    rows = [p for p in players if (p.get(field) is not None) and (p.get("playable") or not playable_only)]
    rows.sort(key=lambda p: p.get(field, 0.0), reverse=True)
    return rows[:top] if top else rows


if __name__ == "__main__":
    import json, os
    SNAP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfl_snapshot.json")
    if not os.path.exists(SNAP):
        raise SystemExit("No nfl_snapshot.json — run the NFL Data Pull first.")
    data = json.load(open(SNAP))
    players = project_all(data)
    print(f"NFL PROJECTIONS — {data.get('today')} ({len(data.get('games',[]))} games, {len(players)} players)")
    for prop, label in [("rec_yds","RECEIVING YDS"),("rush_yds","RUSHING YDS"),
                        ("pass_yds","PASSING YDS"),("rec","RECEPTIONS")]:
        print(f"\n=== {label} (top 8) ===")
        for p in board(players, prop, top=8):
            tag = "" if p["status_flag"] == "ok" else f" [{p.get('status')}]"
            print(f"  {p['name']:22s} {p['team']:3s} {p['pos']:3s} vs {str(p.get('opp')):3s}  "
                  f"{p['proj_'+prop]:6}  ({p['confidence']}){tag}")
