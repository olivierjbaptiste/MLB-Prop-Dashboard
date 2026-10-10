#!/usr/bin/env python3
"""
nfl.py — Diamond Analytics: NFL data layer.

Two sources, by design:
  • nflverse (GitHub release CSVs) — player season + weekly stats, and the
    opponent/positional defense we COMPUTE from the weekly data. Reachable
    from the runner AND from a dev box, so this layer is built against real
    data, not blind.
  • ESPN hidden API — the live layer only: this week's games, injuries, and
    odds (spread/total for game script).

Mirrors nba.py: a committed snapshot (nfl_snapshot.json) the app reads fast;
the scheduled pull runs `python nfl.py` to refresh and commit it.

Stdlib only (urllib + csv). Defensive ESPN parsing + self-diagnostics.
"""

import os
import io
import csv
import json
import gzip
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ── time / season ──────────────────────────────────────────────────────
ET = timezone(timedelta(hours=-4))
def _et_now():   return datetime.now(ET)
def _et_today(): return _et_now().date()

def _season():
    """NFL season is labeled by its START year (Sep–Feb)."""
    t = _et_today()
    return t.year if t.month >= 9 else t.year - 1

# ── snapshot ───────────────────────────────────────────────────────────
_SNAP_DIR  = os.path.dirname(os.path.abspath(__file__))
SNAP_NAME  = "nfl_snapshot.json"
_FORCE_LIVE = False
PLAYER_CAP = int(os.environ.get("NFL_PLAYER_CAP") or "0")   # 0 = full slate

UA   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
NFLV = "https://github.com/nflverse/nflverse-data/releases/download"

# ESPN abbreviation -> nflverse abbreviation (only the ones that differ)
ESPN_TO_NFLV = {"LAR": "LA", "WSH": "WAS"}
def _nflv_abb(espn_abb):
    return ESPN_TO_NFLV.get(espn_abb, espn_abb)


def _snap_path(): return os.path.join(_SNAP_DIR, SNAP_NAME)

def read_snapshot():
    try:
        with open(_snap_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def write_snapshot(payload):
    try:
        with open(_snap_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return True
    except Exception as e:
        print(f"  snapshot write error: {e}")
        return False

def _snapshot_age_h(snap):
    try:
        return round((time.time() - snap.get("ts", 0)) / 3600, 1)
    except Exception:
        return None


# ── fetch helpers ──────────────────────────────────────────────────────
def _fetch_json(url, timeout=20, attempts=3, quiet=False):
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "gzip, identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            if i == attempts - 1:
                if not quiet:
                    print(f"    json fetch fail: {url[:66]}… → {type(e).__name__}")
            else:
                time.sleep(1.2 * (i + 1))
    return None

def _fetch_csv(url, timeout=40, attempts=3):
    """Return list of dict rows from a CSV URL (nflverse release), or []."""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "gzip, identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                text = raw.decode("utf-8", "replace")
                return list(csv.DictReader(io.StringIO(text)))
        except Exception as e:
            if i == attempts - 1:
                print(f"    csv fetch fail: {url[:66]}… → {type(e).__name__}")
            else:
                time.sleep(1.5 * (i + 1))
    return []

def _num(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ── nflverse: player production (season + last-3 form) ─────────────────
def load_players(season):
    """Build per-player per-game production from nflverse season + weekly."""
    reg  = _fetch_csv(f"{NFLV}/stats_player/stats_player_reg_{season}.csv")
    week = _fetch_csv(f"{NFLV}/stats_player/stats_player_week_{season}.csv")
    print(f"    nflverse: {len(reg)} season rows, {len(week)} weekly rows")

    # last-3-game form per player
    wk_by_player = {}
    for r in week:
        wk_by_player.setdefault(r.get("player_id"), []).append(r)
    for pid in wk_by_player:
        wk_by_player[pid].sort(key=lambda r: _num(r.get("week")), reverse=True)

    def pg(row, field, g):
        return round(_num(row.get(field)) / g, 2) if g else 0.0

    players = {}
    for r in reg:
        pid = r.get("player_id")
        g = int(_num(r.get("games")) or 0)
        if not pid or g <= 0:
            continue
        recent = wk_by_player.get(pid, [])[:3]
        rg = len(recent) or 1
        def l3(field):
            return round(sum(_num(w.get(field)) for w in recent) / rg, 2)
        players[pid] = {
            "id": pid,
            "name": r.get("player_display_name") or r.get("player_name"),
            "pos": r.get("position_group") or r.get("position") or "",
            "team": r.get("recent_team"),
            "headshot": r.get("headshot_url") or "",
            "g": g,
            # season per-game
            "pass_yds": pg(r, "passing_yards", g),
            "rush_yds": pg(r, "rushing_yards", g),
            "rec_yds":  pg(r, "receiving_yards", g),
            "rec":      pg(r, "receptions", g),
            "targets":  pg(r, "targets", g),
            "carries":  pg(r, "carries", g),
            "att":      pg(r, "attempts", g),
            # last-3 per-game
            "pass_yds_l3": l3("passing_yards"),
            "rush_yds_l3": l3("rushing_yards"),
            "rec_yds_l3":  l3("receiving_yards"),
            "rec_l3":      l3("receptions"),
        }
    return players, week


# ── nflverse: opponent defense allowed, by position ────────────────────
def compute_team_defense(week_rows):
    """
    {team: {games, pass_yds, rush_yds_RB, rec_yds_WR, rec_yds_TE, rec_yds_RB,
            rec_WR, rec_TE, rec_RB}} — per-game amounts each DEFENSE allows.
    Built by summing what opponents did against each team, from weekly data.
    """
    agg = {}
    def slot(team):
        return agg.setdefault(team, {"weeks": set(), "pass_yds": 0.0,
            "rush_yds_RB": 0.0, "rec_yds_WR": 0.0, "rec_yds_TE": 0.0,
            "rec_yds_RB": 0.0, "rec_WR": 0.0, "rec_TE": 0.0, "rec_RB": 0.0})
    for r in week_rows:
        opp = r.get("opponent_team")
        if not opp:
            continue
        pos = r.get("position_group")
        s = slot(opp)
        s["weeks"].add(r.get("week"))
        if pos == "QB":
            s["pass_yds"] += _num(r.get("passing_yards"))
        elif pos == "RB":
            s["rush_yds_RB"] += _num(r.get("rushing_yards"))
            s["rec_yds_RB"]  += _num(r.get("receiving_yards"))
            s["rec_RB"]      += _num(r.get("receptions"))
        elif pos == "WR":
            s["rec_yds_WR"] += _num(r.get("receiving_yards"))
            s["rec_WR"]     += _num(r.get("receptions"))
        elif pos == "TE":
            s["rec_yds_TE"] += _num(r.get("receiving_yards"))
            s["rec_TE"]     += _num(r.get("receptions"))
    out = {}
    for team, s in agg.items():
        g = len(s["weeks"]) or 1
        out[team] = {k: round(v / g, 2) for k, v in s.items() if k != "weeks"}
        out[team]["games"] = g
    return out


# ── ESPN live: games, odds, injuries ───────────────────────────────────
def get_games(date_obj=None):
    """This week's games with ESPN+nflverse abbrs and (if present) odds."""
    url = f"{ESPN}/scoreboard"
    if date_obj:
        url += f"?dates={date_obj.strftime('%Y%m%d')}"
    data = _fetch_json(url)
    games = []
    if not data:
        return games
    for ev in data.get("events", []):
        try:
            comp = (ev.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), {})
            away = next((c for c in cs if c.get("homeAway") == "away"), {})
            ht, at = home.get("team", {}), away.get("team", {})
            g = {
                "game_id":  ev.get("id"),
                "home_abb": ht.get("abbreviation"),
                "away_abb": at.get("abbreviation"),
                "home_nflv": _nflv_abb(ht.get("abbreviation")),
                "away_nflv": _nflv_abb(at.get("abbreviation")),
                "status":   ((ev.get("status") or {}).get("type") or {}).get("shortDetail"),
                "start":    ev.get("date"),
                "home_spread": None, "total": None,
            }
            odds = comp.get("odds") or []
            if odds:
                o = odds[0]
                g["home_spread"] = o.get("spread")
                g["total"] = o.get("overUnder")
            games.append(g)
        except Exception:
            continue
    return games

def get_odds(event_id):
    """Fallback odds via the summary endpoint's pickcenter. Returns (home_spread,total)."""
    data = _fetch_json(f"{ESPN}/summary?event={event_id}", quiet=True)
    if not data:
        return None, None
    pc = data.get("pickcenter") or []
    if pc:
        o = pc[0]
        return o.get("spread"), o.get("overUnder")
    return None, None

def get_injuries():
    data = _fetch_json(f"{ESPN}/injuries")
    by_name = {}
    if not data:
        return by_name
    for tb in data.get("injuries", []):
        for inj in tb.get("injuries", []):
            try:
                ath = inj.get("athlete", {}) or {}
                nm = (ath.get("displayName") or "").strip()
                if nm:
                    by_name[nm.lower()] = {
                        "status": inj.get("status") or (inj.get("type", {}) or {}).get("description"),
                        "detail": (inj.get("details", {}) or {}).get("detail"),
                    }
            except Exception:
                continue
    return by_name


# ── assemble ───────────────────────────────────────────────────────────
def build_nfl_data():
    season = _season()
    print(f"  Building NFL data — season {season}, {_et_today()}")

    players, week_rows = load_players(season)
    team_defense = compute_team_defense(week_rows)
    print(f"    players: {len(players)} | team defense: {len(team_defense)} teams")

    games = get_games()
    print(f"    games this week: {len(games)}")
    inj = get_injuries()
    print(f"    injuries: {len(inj)}")

    # fill missing odds from the per-game endpoint
    for g in games:
        if g.get("home_spread") is None and g.get("game_id"):
            hs, tot = get_odds(g["game_id"])
            if hs is not None:
                g["home_spread"] = hs
            if tot is not None:
                g["total"] = tot
            time.sleep(0.1)

    # which nflverse teams play this week, and each team's opponent + game script
    ctx = {}   # team(nflv) -> {opp, spread, total, home}
    for g in games:
        h, a = g.get("home_nflv"), g.get("away_nflv")
        hs = g.get("home_spread")
        try: hs = float(hs) if hs is not None else None
        except Exception: hs = None
        tot = g.get("total")
        try: tot = float(tot) if tot is not None else None
        except Exception: tot = None
        if h: ctx[h] = {"opp": a, "spread": hs, "total": tot, "home": True}
        if a: ctx[a] = {"opp": h, "spread": (-hs if hs is not None else None), "total": tot, "home": False}

    playing = set(ctx.keys())
    # build the active player pool: players on teams playing this week
    pool = []
    for p in players.values():
        if p["team"] not in playing:
            continue
        c = ctx.get(p["team"], {})
        nm = (p["name"] or "").lower()
        st = inj.get(nm) or {}
        p2 = dict(p)
        p2["opp"] = c.get("opp")
        p2["spread"] = c.get("spread")
        p2["total"] = c.get("total")
        p2["home"] = c.get("home")
        p2["status"] = st.get("status")
        p2["status_detail"] = st.get("detail")
        pool.append(p2)
        if PLAYER_CAP and len(pool) >= PLAYER_CAP:
            break
    print(f"    teams playing: {len(playing)} | active players: {len(pool)}")

    return {
        "ts": time.time(),
        "season": season,
        "today": _et_today().strftime("%Y-%m-%d"),
        "games": games,
        "players": pool,
        "team_defense": team_defense,
        "injuries_count": len(inj),
    }


def load_nfl(force=False):
    if not (force or _FORCE_LIVE):
        snap = read_snapshot()
        if snap and snap.get("players"):
            print(f"  Using NFL snapshot ({len(snap['players'])} players, "
                  f"{_snapshot_age_h(snap)}h old)")
            return snap
    return build_nfl_data()


# ── diagnostics ────────────────────────────────────────────────────────
if __name__ == "__main__":
    _FORCE_LIVE = True
    print("=" * 70)
    print(f"NFL DATA PULL — {_et_now():%Y-%m-%d %H:%M ET}")
    print("=" * 70)
    data = build_nfl_data()
    ok = write_snapshot(data)
    print(f"\n  snapshot written: {ok}  ({SNAP_NAME})")

    print("\n  === SAMPLE GAMES (with game script) ===")
    for g in data["games"][:4]:
        print(f"    {g.get('away_abb')} @ {g.get('home_abb')}  "
              f"spread(home) {g.get('home_spread')}  total {g.get('total')}  [{g.get('status')}]")
    if data["games"] and data["games"][0].get("home_spread") is None:
        print("    ⚠ no odds parsed — dumping one game's odds shape:")
        gid = data["games"][0].get("game_id")
        d = _fetch_json(f"{ESPN}/summary?event={gid}", quiet=True) or {}
        print("      summary keys:", list(d.keys())[:15])
        print("      pickcenter sample:", json.dumps(d.get("pickcenter"))[:400])

    print("\n  === SAMPLE PLAYERS (teams playing this week) ===")
    shown = 0
    for p in sorted(data["players"], key=lambda x: -(x.get("rec_yds") or 0)):
        print(f"    {p['name']:22s} {p['team']:4s} {p['pos']:3s} vs {str(p.get('opp')):4s}  "
              f"recYd {p['rec_yds']:5}  rushYd {p['rush_yds']:5}  passYd {p['pass_yds']:6}  "
              f"rec {p['rec']}" + (f"  [{p['status']}]" if p.get('status') else ""))
        shown += 1
        if shown >= 10:
            break
    if not data["players"]:
        print("    ⚠ no active players — likely ESPN games empty or team-abbr mismatch.")

    print("\n  === SAMPLE TEAM DEFENSE (per game allowed) ===")
    td = data["team_defense"]
    for t in list(td.keys())[:5]:
        r = td[t]
        print(f"    {t:4s} passYd {r.get('pass_yds')}  rushYd->RB {r.get('rush_yds_RB')}  "
              f"recYd->WR {r.get('rec_yds_WR')}  recYd->TE {r.get('rec_yds_TE')}  (g {r.get('games')})")

    print("\n" + "=" * 70)
    print("DONE. Copy the whole log and send it back.")
    print("=" * 70)
