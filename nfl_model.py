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

import math

W_SEASON = 0.55
W_L3     = 0.45
CLAMP    = 0.20          # matchup factor capped at ±20%
GS_CLAMP = 0.18          # game-script factor capped at ±18%
LEAGUE_TOTAL = 45.0      # ~average NFL game total, for the pace scale
ABSORB_RATE = 0.85      # share of an OUT player's volume the rest of the room absorbs
MAX_BOOST   = 1.60      # cap on the volume boost one player can get
TD_CLAMP    = 0.25      # TD-matchup factor capped at ±25%
TD_ENV_CLAMP = 0.22     # TD scoring-environment factor capped at ±22%
TD_PRIOR_K    = 4.0     # pseudo-games of prior — shrinks small-sample TD rates
TD_PRIOR_RATE = 0.25    # league-ish TD/game the prior pulls toward
TD_MAX_PROB   = 0.72    # realistic anytime-TD ceiling (books rarely price past this)

# which TD-allowed metrics drive each position's anytime-TD matchup
_TD_DEF = {
    "RB": ["rush_td_RB", "rec_td_RB"],
    "WR": ["rec_td_WR"],
    "TE": ["rec_td_TE"],
    "QB": ["rush_td_RB"],   # mobile-QB rushing TD, proxied by rush TDs allowed
}
_TD_METRICS = ["rush_td_RB", "rec_td_WR", "rec_td_TE", "rec_td_RB"]

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
    for m in _ALL_METRICS + _TD_METRICS:
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


def _american_odds(prob):
    """Fair American odds implied by a hit probability."""
    if prob <= 0.0 or prob >= 1.0:
        return None
    if prob >= 0.5:
        return -round(100 * prob / (1 - prob))
    return round(100 * (1 - prob) / prob)


def td_matchup(player, avg, td):
    """How many TDs the opponent allows this position vs league, clamped."""
    pos = player.get("pos")
    keys = _TD_DEF.get(pos)
    opp = player.get("opp")
    if not keys or not opp or opp not in td:
        return 1.0
    v = sum((td[opp].get(k) or 0) for k in keys)
    a = sum((avg.get(k) or 0) for k in keys)
    if a and v:
        return _clamp(v / a, TD_CLAMP)
    return 1.0


def project_td(p, avg, td):
    """Anytime-TD probability from blended TD rate × matchup × scoring env ×
    volume (an injured teammate's goal-line / target share)."""
    pos = p.get("pos")
    if pos not in _TD_DEF:
        return
    g = p.get("g") or 0
    # season TD total (prefer the raw count; fall back to per-game × games)
    td_total = p.get("td_total")
    if td_total is None:
        td_total = ((p.get("rush_td") or 0) + (p.get("rec_td") or 0)) * g
    # empirical-Bayes shrink toward a league prior — three games of data can't
    # establish a real TD rate, so a 1-for-1 fluke gets pulled way down
    rate = (td_total + TD_PRIOR_K * TD_PRIOR_RATE) / (g + TD_PRIOR_K)
    if rate <= 0:
        p["anytime_td"] = 0.0
        return
    lam = rate                                      # expected TDs / game

    mf = td_matchup(p, avg, td)

    # scoring environment: high total → more TDs; favorites score more
    env = 1.0
    tot = p.get("total")
    if tot:
        try: env *= float(tot) / LEAGUE_TOTAL
        except Exception: pass
    s = p.get("spread")
    if s is not None:
        try: env *= (1 - 0.012 * max(-14.0, min(14.0, float(s))))
        except Exception: pass
    env = _clamp(env, TD_ENV_CLAMP)

    # volume share from injured teammates (RB → carries, pass-catcher → targets)
    vb = p.get("vb_rush", 1.0) if pos == "QB" else (
         p.get("vb_rush", 1.0) if pos == "RB" else p.get("vb_rec", 1.0))

    lam_adj = lam * mf * env * vb
    prob = min(TD_MAX_PROB, 1 - math.exp(-lam_adj))
    p["td_lambda"]  = round(lam_adj, 3)
    p["td_mf"]      = round(mf, 3)
    p["anytime_td"] = round(prob * 100, 1)         # percent
    p["td_odds"]    = _american_odds(prob)


def project_player(p, data, avg):
    td = data.get("team_defense") or {}
    gs = game_script(p)
    bucket = _status_bucket(p.get("status"))
    pos = p.get("pos")

    wx = p.get("wx") or {}
    wx_pass = wx.get("pass_mult", 1.0) if not wx.get("indoor") else 1.0
    wx_rush = wx.get("rush_mult", 1.0) if not wx.get("indoor") else 1.0

    out = {}
    def put(prop, base, area):
        if base is None: return
        mf = matchup(p, avg, td, prop)
        if prop in ("rec_yds", "rec"):
            vb = p.get("vb_rec", 1.0)
        elif prop == "rush_yds":
            vb = p.get("vb_rush", 1.0)
        else:
            vb = 1.0
        wxm = wx_rush if area == "rush" else wx_pass
        val = _blend(base, p.get(prop + "_l3")) * mf * gs[area] * vb * wxm
        out["proj_" + prop] = 0.0 if bucket == "out" else max(0.0, round(val, 1))
        out["mf_" + prop] = round(mf, 3)

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

    if bucket != "out":
        project_td(p, avg, td)
    else:
        p["anytime_td"] = 0.0

    # implied team total from the Vegas line: total/2 shifted by the spread
    # (favorite — negative spread — is implied to score more)
    s, tot = p.get("spread"), p.get("total")
    if s is not None and tot:
        try:
            p["team_total"] = round(float(tot) / 2 - float(s) / 2, 1)
        except Exception:
            pass

    p.update(out)
    p["gs_pass"] = round(gs["pass"], 3)
    p["gs_rush"] = round(gs["rush"], 3)
    p["gs_rec"]  = round(gs["rec"], 3)
    p["confidence"] = conf
    p["playable"] = (bucket != "out")
    p["status_flag"] = bucket
    return p


# ── injury-driven volume redistribution ───────────────────────────────
# When a player is ruled OUT, his opportunity (targets for pass-catchers,
# carries for RBs) flows to the rest of the room. NFL props move hard off
# these role changes, so this is the single biggest accuracy lever.
def _redis_pool(roster, vol_field, boost_field):
    out_names = [p.get("name") for p in roster
                 if _status_bucket(p.get("status")) == "out" and (p.get(vol_field) or 0) > 0]
    vacated = sum((p.get(vol_field) or 0) for p in roster
                  if _status_bucket(p.get("status")) == "out")
    if vacated <= 0:
        return
    active = [p for p in roster
              if _status_bucket(p.get("status")) != "out" and (p.get(vol_field) or 0) > 0]
    tot = sum((p.get(vol_field) or 0) for p in active)
    if tot <= 0:
        return
    why = ", ".join(n for n in out_names[:2] if n)
    for p in active:
        own = p.get(vol_field) or 0
        absorbed = vacated * (own / tot) * ABSORB_RATE
        boost = min((own + absorbed) / own, MAX_BOOST)
        if boost > 1.01:
            p[boost_field] = round(boost, 3)
            p[boost_field + "_why"] = why


# ── hit rate / consistency ────────────────────────────────────────────
# For each player/prop: how often they cleared a line near their projection
# over the last 5 and the full season, split by how tough the opponent's
# positional defense was. Pure game-log math off the committed snapshot.
_PROPS_FOR_POS = {
    "QB": ["pass_yds"],
    "RB": ["rush_yds", "rec_yds", "rec"],
    "WR": ["rec_yds", "rec"],
    "TE": ["rec_yds", "rec"],
}

def _round_line(prop, v):
    if v is None or v <= 0:
        return None
    if prop == "rec":
        return round(v * 2) / 2.0          # nearest 0.5 reception
    return float(round(v / 5.0) * 5)       # nearest 5 yards

def _def_ranks(td):
    """For each defense metric, team -> percentile of yards/catches allowed
    (0 = toughest / allows least, 1 = softest / allows most)."""
    ranks = {}
    for m in _ALL_METRICS:
        pairs = [(t, r[m]) for t, r in td.items() if isinstance(r, dict) and r.get(m)]
        if len(pairs) < 2:
            continue
        pairs.sort(key=lambda x: x[1])
        n = len(pairs) - 1
        ranks[m] = {t: i / n for i, (t, _) in enumerate(pairs)}
    return ranks

def _bucket(opp, metric, ranks):
    r = ranks.get(metric, {}).get(opp)
    if r is None:
        return "mid"
    if r >= 0.60:
        return "soft"
    if r <= 0.40:
        return "tough"
    return "mid"

def compute_hit_rates(p, data, ranks):
    log = p.get("log") or []
    if not log:
        return
    pos = p.get("pos")
    hr = {}
    for prop in _PROPS_FOR_POS.get(pos, []):
        # line tracks the player's baseline production (what a book posts),
        # NOT our matchup/volume-adjusted projection — otherwise a soft spot
        # inflates the line and the hit rate reads artificially low.
        base = _blend(p.get(prop), p.get(prop + "_l3"))
        line = _round_line(prop, base)
        if line is None:
            continue
        metric = _DEF.get((prop, pos))
        this_b = _bucket(p.get("opp"), metric, ranks) if metric else "mid"
        entries = []
        for g in log:
            v = g.get(prop)
            if v is None:
                continue
            gb = _bucket(g.get("opp"), metric, ranks) if metric else "mid"
            entries.append((g.get("wk"), g.get("opp"), v, v >= line, gb))
        if not entries:
            continue
        l5 = entries[:5]
        sim = [e for e in entries if e[4] == this_b and this_b != "mid"]
        hr[prop] = {
            "line": line,
            "l5":  [sum(1 for e in l5 if e[3]), len(l5)],
            "szn": [sum(1 for e in entries if e[3]), len(entries)],
            "vs":  ([sum(1 for e in sim if e[3]), len(sim)] if sim else None),
            "vs_tier": this_b,
            "log": [{"wk": e[0], "opp": e[1], "v": e[2], "hit": e[3]} for e in l5],
        }
    if hr:
        p["hr"] = hr


# ── "avoid" flags ─────────────────────────────────────────────────────
# Risk signals that argue against a prop even when the projection looks fine:
# tough matchup, low implied team total, injury tag, adverse game script,
# a part-time snap role, or a cold recent run. Two or more → the card warns.
def compute_flags(p):
    pos = p.get("pos")
    tt = p.get("team_total")
    out = {}
    for prop in _PROPS_FOR_POS.get(pos, []):
        if p.get("proj_" + prop) is None:
            continue
        reasons = []
        mf = p.get("mf_" + prop)
        if mf is not None and mf <= 0.92:
            reasons.append("Tough matchup")
        if tt is not None and tt <= 17:
            reasons.append("Low team total (" + str(tt) + ")")
        if p.get("status_flag") == "flag":
            reasons.append(p.get("status") or "Questionable")
        area = "rush" if prop == "rush_yds" else ("pass" if prop == "pass_yds" else "rec")
        gsv = p.get("gs_" + area)
        if gsv is not None and gsv <= 0.95:
            reasons.append("Game script")
        snp = p.get("snap_pct")
        if snp is not None and snp < 45:
            reasons.append("Low snaps (" + str(snp) + "%)")
        wx = p.get("wx") or {}
        if wx.get("rough") and area in ("pass", "rec"):
            reasons.append("Weather")
        hr = (p.get("hr") or {}).get(prop)
        if hr and hr["l5"][1] >= 4 and hr["l5"][0] <= 1:
            reasons.append("Cold L5 (" + str(hr["l5"][0]) + "/" + str(hr["l5"][1]) + ")")
        if reasons:
            out[prop] = reasons
    if out:
        p["avoid"] = out


def _redistribute(players):
    teams = {}
    for p in players:
        teams.setdefault(p.get("team"), []).append(p)
    for roster in teams.values():
        _redis_pool(roster, "targets", "vb_rec")    # receiving opportunity
        _redis_pool(roster, "carries", "vb_rush")   # rushing opportunity


# ── same-game correlations (SGP stack builder) ────────────────────────
# Which legs move together in the same game. Grounded in real usage: a QB's
# passing output and his pass-catchers' receiving output rise together, most
# strongly for the highest target share. The lead back is game-script
# dependent — it stacks with a negative spread (favored, running late) and
# fades when the team is a big underdog throwing to catch up.
def _strength(share):
    if share is None:            return "Light"
    if share >= 25:              return "Strong"
    if share >= 15:              return "Moderate"
    return "Light"

def build_correlations(players):
    teams = {}
    for p in players:
        if p.get("playable"):
            teams.setdefault(p.get("team"), []).append(p)
    panels = []
    for team, roster in teams.items():
        qb = max((p for p in roster if p.get("pos") == "QB" and p.get("proj_pass_yds")),
                 key=lambda p: p.get("proj_pass_yds", 0), default=None)
        catchers = sorted(
            [p for p in roster if p.get("pos") in ("WR", "TE", "RB") and p.get("proj_rec_yds")],
            key=lambda p: (p.get("tgt_share") or 0), reverse=True)[:4]
        rb = max((p for p in roster if p.get("pos") == "RB" and p.get("proj_rush_yds")),
                 key=lambda p: p.get("proj_rush_yds", 0), default=None)
        if not qb and not catchers:
            continue
        stack = []
        for c in catchers:
            stack.append({
                "name": c.get("name"), "pos": c.get("pos"),
                "prop": "rec_yds", "proj": c.get("proj_rec_yds"),
                "tgt_share": c.get("tgt_share"),
                "strength": _strength(c.get("tgt_share")),
            })
        rb_note = None
        if rb:
            s = rb.get("spread")
            dirn = "stack" if (s is not None and s < -2) else ("fade" if (s is not None and s > 3) else "neutral")
            rb_note = {
                "name": rb.get("name"), "proj": rb.get("proj_rush_yds"),
                "carry_share": rb.get("carry_share"), "spread": s, "dir": dirn,
            }
        panels.append({
            "team": team, "opp": (qb or catchers[0]).get("opp"),
            "total": (qb or catchers[0]).get("total"),
            "team_total": (qb or catchers[0]).get("team_total"),
            "qb": ({"name": qb.get("name"), "proj": qb.get("proj_pass_yds")} if qb else None),
            "stack": stack, "rb": rb_note,
        })
    panels.sort(key=lambda x: (x.get("team_total") or 0), reverse=True)
    return panels


# ── edges vs the book (needs live prop lines from odds.py) ────────────
# Converts a projection into a probability of clearing the posted line using a
# per-prop variance, then compares to the book's de-vigged price and ranks by
# expected value at the best available number. Anytime TD compares our modeled
# probability directly. No edges appear unless the snapshot carries odds.
_CV = {"pass_yds": 0.26, "rush_yds": 0.42, "rec_yds": 0.50, "rec": 0.38}

def _normcdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))

def _dec(am):
    o = float(am)
    return (o / 100.0 + 1) if o > 0 else (100.0 / (-o) + 1)

def _best_side(cands):
    """cands: [(side, our_prob, american_price)] -> the +EV pick."""
    best = None
    for side, prob, px in cands:
        if px is None:
            continue
        ev = prob * _dec(px) - 1
        if best is None or ev > best["ev"]:
            best = {"side": side, "prob": round(prob, 4), "price": px, "ev": ev}
    return best

def compute_edges(players, odds_lines):
    edges = []
    for p in players:
        if not p.get("playable"):
            continue
        book = odds_lines.get((p.get("name") or "").lower())
        if not book:
            continue
        pe = {}
        for prop in ("pass_yds", "rush_yds", "rec_yds", "rec"):
            ln = book.get(prop)
            proj = p.get("proj_" + prop)
            if not ln or proj is None or ln.get("line") is None:
                continue
            line = ln["line"]
            sigma = max(1.0, _CV.get(prop, 0.4) * max(proj, line))
            prob_over = 1 - _normcdf((line - proj) / sigma)
            best = _best_side([("Over", prob_over, ln.get("best_over_price")),
                               ("Under", 1 - prob_over, ln.get("best_under_price"))])
            if not best:
                continue
            best.update({"prop": prop, "line": line, "proj": proj,
                         "fair_over": ln.get("fair_over"), "books": ln.get("books"),
                         "ev_pct": round(best.pop("ev") * 100, 1)})
            pe[prop] = best
        td = book.get("anytime_td")
        if td and p.get("anytime_td"):
            our = p["anytime_td"] / 100.0
            best = _best_side([("Yes", our, td.get("best_yes_price")),
                               ("No", 1 - our, td.get("best_no_price"))])
            if best:
                best.update({"prop": "anytime_td", "fair_yes": td.get("fair_yes"),
                             "books": td.get("books"), "ev_pct": round(best.pop("ev") * 100, 1)})
                pe["anytime_td"] = best
        if pe:
            p["edge"] = pe
            for prop, e in pe.items():
                edges.append({"name": p.get("name"), "team": p.get("team"),
                              "opp": p.get("opp"), "pos": p.get("pos"), **e})
    edges.sort(key=lambda e: e.get("ev_pct", -999), reverse=True)
    return edges


# ── defense-vs-position grades (for the matchup page) ─────────────────
def build_def_grades(td):
    metrics = ["pass_yds", "rush_yds_RB", "rec_yds_WR", "rec_yds_TE", "rec_yds_RB"]
    grades = {}
    for m in metrics:
        pairs = [(t, r[m]) for t, r in td.items() if isinstance(r, dict) and r.get(m)]
        if len(pairs) < 2:
            continue
        pairs.sort(key=lambda x: x[1])          # ascending = toughest (allows least) first
        n = len(pairs)
        for i, (t, _) in enumerate(pairs):
            pct = i / (n - 1)
            label = "Tough" if pct <= 0.33 else ("Soft" if pct >= 0.67 else "Avg")
            grades.setdefault(t, {})[m] = {"rank": i + 1, "n": n, "label": label}
    return grades


def project_all(data):
    td = data.get("team_defense") or {}
    avg = _league_avgs(td)
    data["_league_avg"] = avg
    players = data.get("players", [])
    _redistribute(players)
    for p in players:
        project_player(p, data, avg)
    ranks = _def_ranks(td)
    for p in players:
        if p.get("playable"):
            compute_hit_rates(p, data, ranks)
            compute_flags(p)
    data["stacks"] = build_correlations(players)
    data["def_grades"] = build_def_grades(td)
    data["edges"] = compute_edges(players, data.get("odds") or {})
    return players


def board(players, prop, playable_only=True, top=None):
    field = "proj_" + prop
    rows = [p for p in players if (p.get(field) is not None) and (p.get("playable") or not playable_only)]
    rows.sort(key=lambda p: p.get(field, 0.0), reverse=True)
    return rows[:top] if top else rows


def td_board(players, playable_only=True, top=None):
    rows = [p for p in players if p.get("anytime_td") and (p.get("playable") or not playable_only)]
    rows.sort(key=lambda p: p.get("anytime_td", 0.0), reverse=True)
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

    print("\n=== ANYTIME TD (top 12) ===")
    for p in td_board(players, top=12):
        odds = p.get("td_odds")
        odds = (f"{odds:+d}" if odds is not None else "—")
        print(f"  {p['name']:22s} {p['team']:3s} {p['pos']:3s} vs {str(p.get('opp')):3s}  "
              f"{p['anytime_td']:5}%  (fair {odds})")
