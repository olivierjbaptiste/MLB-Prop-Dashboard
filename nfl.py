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

def _fetch_text(url, timeout=120, attempts=3):
    """Return the raw decoded body of a URL (for large CSVs we stream, not
    materialize as dict rows)."""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "gzip, identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw.decode("utf-8", "replace")
        except Exception as e:
            if i == attempts - 1:
                print(f"    text fetch fail: {url[:66]}… → {type(e).__name__}")
            else:
                time.sleep(1.5 * (i + 1))
    return ""

def _num(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ── nflverse: usage detail (snap share, red-zone opportunity) ──────────
def load_snaps(season):
    """player-name(lower) -> season average offensive snap share (0–100)."""
    rows = _fetch_csv(f"{NFLV}/snap_counts/snap_counts_{season}.csv")
    agg = {}
    for r in rows:
        key = (r.get("player") or "").strip().lower()
        if not key:
            continue
        d = agg.setdefault(key, {"sum": 0.0, "n": 0})
        d["sum"] += _num(r.get("offense_pct"))   # 0–1 per game
        d["n"]   += 1
    return {k: round(100 * v["sum"] / v["n"], 1) for k, v in agg.items() if v["n"]}

def load_redzone(season):
    """player_id -> red-zone opportunity counts (season), from play-by-play.
    rz_* = inside the 20, rz10_* = inside the 10 (goal-line)."""
    text = _fetch_text(f"{NFLV}/pbp/play_by_play_{season}.csv", timeout=180)
    rz = {}
    if not text:
        print("    red-zone: play-by-play unavailable — skipping usage detail")
        return rz
    def slot(pid):
        return rz.setdefault(pid, {"rz_car": 0, "rz10_car": 0, "rz_tgt": 0, "rz10_tgt": 0})
    for row in csv.DictReader(io.StringIO(text)):
        try:
            y = float(row.get("yardline_100"))
        except Exception:
            continue
        if y > 20:
            continue
        if row.get("rush_attempt") == "1" and row.get("rusher_player_id"):
            d = slot(row["rusher_player_id"]); d["rz_car"] += 1
            if y <= 10: d["rz10_car"] += 1
        if row.get("pass_attempt") == "1" and row.get("receiver_player_id"):
            d = slot(row["receiver_player_id"]); d["rz_tgt"] += 1
            if y <= 10: d["rz10_tgt"] += 1
    print(f"    red-zone: {len(rz)} players with inside-20 opportunity")
    return rz


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
        allw = wk_by_player.get(pid, [])
        recent = allw[:3]
        rg = len(recent) or 1
        def l3(field):
            return round(sum(_num(w.get(field)) for w in recent) / rg, 2)
        # per-game log (actuals, most-recent first) for hit-rate / consistency
        log = [{
            "wk":       int(_num(w.get("week")) or 0),
            "opp":      w.get("opponent_team"),
            "pass_yds": round(_num(w.get("passing_yards")), 1),
            "rush_yds": round(_num(w.get("rushing_yards")), 1),
            "rec_yds":  round(_num(w.get("receiving_yards")), 1),
            "rec":      round(_num(w.get("receptions")), 1),
        } for w in allw[:6]]
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
            # touchdown production (for Anytime TD)
            "rush_td":  pg(r, "rushing_tds", g),
            "rec_td":   pg(r, "receiving_tds", g),
            "td_total": int(_num(r.get("rushing_tds")) + _num(r.get("receiving_tds"))),
            # last-3 per-game
            "pass_yds_l3": l3("passing_yards"),
            "rush_yds_l3": l3("rushing_yards"),
            "rec_yds_l3":  l3("receiving_yards"),
            "rec_l3":      l3("receptions"),
            "rush_td_l3":  l3("rushing_tds"),
            "rec_td_l3":   l3("receiving_tds"),
            "log": log,
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
            "rec_yds_RB": 0.0, "rec_WR": 0.0, "rec_TE": 0.0, "rec_RB": 0.0,
            "rush_td_RB": 0.0, "rec_td_WR": 0.0, "rec_td_TE": 0.0, "rec_td_RB": 0.0})
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
            s["rush_td_RB"]  += _num(r.get("rushing_tds"))
            s["rec_td_RB"]   += _num(r.get("receiving_tds"))
        elif pos == "WR":
            s["rec_yds_WR"] += _num(r.get("receiving_yards"))
            s["rec_WR"]     += _num(r.get("receptions"))
            s["rec_td_WR"]  += _num(r.get("receiving_tds"))
        elif pos == "TE":
            s["rec_yds_TE"] += _num(r.get("receiving_yards"))
            s["rec_TE"]     += _num(r.get("receptions"))
            s["rec_td_TE"]  += _num(r.get("receiving_tds"))
    out = {}
    for team, s in agg.items():
        g = len(s["weeks"]) or 1
        out[team] = {k: round(v / g, 2) for k, v in s.items() if k != "weeks"}
        out[team]["games"] = g
    return out


# ── weather (outdoor games only) ───────────────────────────────────────
# Home team -> stadium coords + roof. "dome" covers fixed domes, fixed-canopy
# (SoFi) and retractable roofs (assumed closed in bad weather) — all treated
# as controlled, so no weather is fetched or applied. "out" = open-air.
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
STADIUMS = {
    "ARI": (33.5276, -112.2626, "dome"), "ATL": (33.7554, -84.4009, "dome"),
    "BAL": (39.2780, -76.6227, "out"),   "BUF": (42.7738, -78.7870, "out"),
    "CAR": (35.2258, -80.8528, "out"),   "CHI": (41.8623, -87.6167, "out"),
    "CIN": (39.0954, -84.5160, "out"),   "CLE": (41.5061, -81.6995, "out"),
    "DAL": (32.7473, -97.0945, "dome"),  "DEN": (39.7439, -105.0201, "out"),
    "DET": (42.3400, -83.0456, "dome"),  "GB":  (44.5013, -88.0622, "out"),
    "HOU": (29.6847, -95.4107, "dome"),  "IND": (39.7601, -86.1639, "dome"),
    "JAX": (30.3240, -81.6373, "out"),   "KC":  (39.0489, -94.4839, "out"),
    "LA":  (33.9535, -118.3392, "dome"), "LAC": (33.9535, -118.3392, "dome"),
    "LV":  (36.0909, -115.1833, "dome"), "MIA": (25.9580, -80.2389, "out"),
    "MIN": (44.9736, -93.2575, "dome"),  "NE":  (42.0909, -71.2643, "out"),
    "NO":  (29.9511, -90.0812, "dome"),  "NYG": (40.8135, -74.0745, "out"),
    "NYJ": (40.8135, -74.0745, "out"),   "PHI": (39.9008, -75.1675, "out"),
    "PIT": (40.4468, -80.0158, "out"),   "SEA": (47.5952, -122.3316, "out"),
    "SF":  (37.4030, -121.9700, "out"),  "TB":  (27.9759, -82.5033, "out"),
    "TEN": (36.1665, -86.7713, "out"),   "WAS": (38.9076, -76.8645, "out"),
}

def _wx_impact(wind, precip, pop):
    """Pass/rush multipliers from forecast. Wind hurts throwing most; heavy
    precip trims passing and nudges rushing up. Deliberately mild — game
    script is the bigger lever."""
    pm, rm = 1.0, 1.0
    w = wind or 0
    if   w >= 25: pm *= 0.90
    elif w >= 18: pm *= 0.94
    elif w >= 13: pm *= 0.975
    wet = (precip or 0) >= 0.05 or (pop or 0) >= 60
    if wet:
        pm *= 0.97
        rm *= 1.02
    return round(pm, 3), round(rm, 3)

def get_weather(games):
    """Attach a 'wx' block to each game: indoor games are marked controlled;
    outdoor games get the forecast nearest kickoff."""
    for g in games:
        home = g.get("home_nflv")
        info = STADIUMS.get(home)
        start = g.get("start") or ""
        if not info:
            continue
        lat, lon, roof = info
        if roof != "out":
            g["wx"] = {"indoor": True, "note": "Indoors (climate-controlled)"}
            continue
        try:
            url = (f"{OPEN_METEO}?latitude={lat}&longitude={lon}"
                   "&hourly=temperature_2m,precipitation,precipitation_probability,"
                   "wind_speed_10m,wind_gusts_10m"
                   "&temperature_unit=fahrenheit&wind_speed_unit=mph"
                   "&precipitation_unit=inch&forecast_days=8")
            data = _fetch_json(url, quiet=True)
            h = (data or {}).get("hourly") or {}
            times = h.get("time") or []
            if not times:
                continue
            key = start[:13]                      # "YYYY-MM-DDTHH" (both UTC)
            idx = next((i for i, t in enumerate(times) if t[:13] == key), None)
            if idx is None:                       # fall back to nearest available
                idx = min(range(len(times)), key=lambda i: abs(i - len(times)//2))
            def at(field):
                arr = h.get(field) or []
                return arr[idx] if idx < len(arr) else None
            wind = at("wind_speed_10m"); gust = at("wind_gusts_10m")
            precip = at("precipitation"); pop = at("precipitation_probability")
            temp = at("temperature_2m")
            eff_wind = max(wind or 0, (gust or 0) * 0.7)   # gusts matter for throws
            pm, rm = _wx_impact(eff_wind, precip, pop)
            bits = []
            if temp is not None: bits.append(f"{round(temp)}°F")
            if wind is not None: bits.append(f"{round(wind)} mph wind" + (f" (g {round(gust)})" if gust else ""))
            if pop:              bits.append(f"{round(pop)}% precip")
            g["wx"] = {
                "indoor": False, "temp": temp, "wind": wind, "gust": gust,
                "precip": precip, "pop": pop, "pass_mult": pm, "rush_mult": rm,
                "note": " · ".join(bits) or "Outdoors",
                "rough": pm <= 0.95,
            }
        except Exception as e:
            print(f"    weather fail {home}: {type(e).__name__}")
    done = sum(1 for g in games if g.get("wx"))
    print(f"    weather: {done}/{len(games)} games")
    return games


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

    # usage detail: snap share + red-zone opportunity (enrichment — a failure
    # here must not sink the pull)
    try:
        snaps = load_snaps(season)
    except Exception as e:
        print(f"    snap load error: {e}"); snaps = {}
    try:
        rz = load_redzone(season)
    except Exception as e:
        print(f"    red-zone load error: {e}"); rz = {}

    # team target / carry totals (full roster) for usage share
    team_tgt, team_car = {}, {}
    for p in players.values():
        t = p.get("team")
        team_tgt[t] = team_tgt.get(t, 0.0) + (p.get("targets") or 0) * (p.get("g") or 0)
        team_car[t] = team_car.get(t, 0.0) + (p.get("carries") or 0) * (p.get("g") or 0)

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

    # outdoor-game weather (live layer, runner-only like ESPN)
    try:
        get_weather(games)
    except Exception as e:
        print(f"    weather load error: {e}")

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
        wx = g.get("wx")
        if h: ctx[h] = {"opp": a, "spread": hs, "total": tot, "home": True,  "wx": wx}
        if a: ctx[a] = {"opp": h, "spread": (-hs if hs is not None else None), "total": tot, "home": False, "wx": wx}

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
        p2["wx"] = c.get("wx")
        p2["status"] = st.get("status")
        p2["status_detail"] = st.get("detail")
        # usage detail
        p2["snap_pct"] = snaps.get(nm)
        rzd = rz.get(p.get("id"))
        if rzd:
            p2["rz_car"], p2["rz10_car"] = rzd["rz_car"], rzd["rz10_car"]
            p2["rz_tgt"], p2["rz10_tgt"] = rzd["rz_tgt"], rzd["rz10_tgt"]
        tt, ct = team_tgt.get(p["team"], 0), team_car.get(p["team"], 0)
        p_tgt_tot = (p.get("targets") or 0) * (p.get("g") or 0)
        p_car_tot = (p.get("carries") or 0) * (p.get("g") or 0)
        p2["tgt_share"]   = round(100 * p_tgt_tot / tt, 1) if tt else None
        p2["carry_share"] = round(100 * p_car_tot / ct, 1) if ct else None
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
