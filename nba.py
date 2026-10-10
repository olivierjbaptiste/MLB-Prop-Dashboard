#!/usr/bin/env python3
"""
nba.py — Diamond Analytics: NBA data layer (ESPN-backed).

Mirrors dashboard.py's design:
  • At request time the Flask app reads a committed snapshot (fast, no network).
  • The daily GitHub Action runs `python nba.py` with _FORCE_LIVE=True, which
    fetches fresh from ESPN, writes nba_snapshot.json, and commits it.
    Render then auto-deploys.

Data sources (all proven to work from a GitHub runner on 2026-10-10):
  • scoreboard  — today's games
  • teams       — 30 teams (abbr/name/id)
  • injuries    — player status feed  (THE make-or-break feed for props)
  • gamelog     — per-player game-by-game → season avgs + last-5 form

Stdlib only (urllib). Defensive parsing + self-diagnostics: run it directly
and it prints what it parsed and dumps the raw shape of anything empty.
"""

import os
import io
import json
import gzip
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ── time (Eastern), mirrors dashboard.py ──────────────────────────────
ET = timezone(timedelta(hours=-4))
def _et_now():   return datetime.now(ET)
def _et_today(): return _et_now().date()

# ── snapshot (mirrors dashboard.py _read/_write_snapshot) ─────────────
_SNAP_DIR  = os.path.dirname(os.path.abspath(__file__))
SNAP_NAME  = "nba_snapshot.json"
_FORCE_LIVE = False          # the pull script flips this to True

# First validation run stays small/fast. Raise (or set env to 0 = unlimited)
# once the ESPN shapes are confirmed so it covers the whole slate.
PLAYER_CAP = int(os.environ.get("NBA_PLAYER_CAP", "40"))

UA          = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ESPN        = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_COMMON = "https://site.api.espn.com/apis/common/v3/sports/basketball/nba"
ESPN_CORE   = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def _snap_path():
    return os.path.join(_SNAP_DIR, SNAP_NAME)

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


# ── fetch helper (urllib, gzip, retries) ──────────────────────────────
def _fetch_json(url, timeout=15, attempts=3):
    """Return parsed JSON dict/list, or None. Never raises."""
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
                print(f"    fetch fail: {url[:70]}… → {type(e).__name__}")
            else:
                time.sleep(1.2 * (i + 1))
    return None


def _num(x):
    """Coerce an ESPN stat cell to float. Handles '34', '34:00', '4-9', '-', ''."""
    if x is None:
        return None
    s = str(x).strip()
    if s in ("", "-", "--"):
        return 0.0
    if ":" in s:                      # minutes 'MM:SS' → minutes
        try:
            m, sec = s.split(":")[:2]
            return round(int(m) + int(sec) / 60.0, 1)
        except Exception:
            return None
    if "-" in s and s.count("-") == 1 and not s.startswith("-"):  # 'made-att'
        try:
            return float(s.split("-")[0])
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None


# ── 1. games (scoreboard) ─────────────────────────────────────────────
def get_games(date_obj=None):
    d = date_obj or _et_today()
    data = _fetch_json(f"{ESPN}/scoreboard?dates={d.strftime('%Y%m%d')}")
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
            st = (ev.get("status") or {}).get("type", {})
            games.append({
                "game_id":   ev.get("id"),
                "home_id":   ht.get("id"),
                "away_id":   at.get("id"),
                "home_abb":  ht.get("abbreviation"),
                "away_abb":  at.get("abbreviation"),
                "home_name": ht.get("displayName"),
                "away_name": at.get("displayName"),
                "status":    st.get("shortDetail") or st.get("state"),
                "start":     ev.get("date"),
            })
        except Exception:
            continue
    return games


# ── 2. teams ──────────────────────────────────────────────────────────
def get_teams():
    data = _fetch_json(f"{ESPN}/teams")
    out = {}
    if not data:
        return out
    try:
        teams = data["sports"][0]["leagues"][0]["teams"]
        for t in teams:
            tm = t.get("team", {})
            tid = tm.get("id")
            if tid:
                out[str(tid)] = {
                    "abb":  tm.get("abbreviation"),
                    "name": tm.get("displayName"),
                }
    except Exception:
        pass
    return out


# ── 3. injuries (keyed by athlete id AND lowercase name) ──────────────
def get_injuries():
    data = _fetch_json(f"{ESPN}/injuries")
    by_id, by_name = {}, {}
    if not data:
        return by_id, by_name
    for team_block in data.get("injuries", []):
        for inj in team_block.get("injuries", []):
            try:
                ath = inj.get("athlete", {}) or {}
                aid = str(ath.get("id")) if ath.get("id") else None
                nm  = (ath.get("displayName") or "").strip()
                rec = {
                    "status": inj.get("status") or (inj.get("type", {}) or {}).get("description"),
                    "detail": (inj.get("details", {}) or {}).get("detail")
                              or (inj.get("type", {}) or {}).get("description"),
                    "name":   nm,
                }
                if aid:
                    by_id[aid] = rec
                if nm:
                    by_name[nm.lower()] = rec
            except Exception:
                continue
    return by_id, by_name


# ── 4. team roster → player list ──────────────────────────────────────
def get_team_roster(team_id):
    data = _fetch_json(f"{ESPN}/teams/{team_id}/roster")
    players = []
    if not data:
        return players
    aths = data.get("athletes", [])
    # ESPN returns either grouped-by-position (each has 'items') or a flat list
    if aths and isinstance(aths[0], dict) and "items" in aths[0]:
        flat = []
        for grp in aths:
            flat.extend(grp.get("items", []))
        aths = flat
    for a in aths:
        try:
            pos = a.get("position", {})
            players.append({
                "id":   str(a.get("id")),
                "name": a.get("displayName") or a.get("fullName"),
                "pos":  (pos.get("abbreviation") if isinstance(pos, dict) else pos) or "",
            })
        except Exception:
            continue
    return players


# ── 5. per-player season averages + last-5 form (from gamelog) ────────
def get_player_log(athlete_id):
    """Return dict of season averages + last-5 form, or None."""
    data = _fetch_json(f"{ESPN_COMMON}/athletes/{athlete_id}/gamelog")
    if not data:
        return None
    names  = [str(n).lower() for n in (data.get("names")  or [])]
    labels = [str(l).upper() for l in (data.get("labels") or [])]

    def idx(name_opts, label_opts):
        for n in name_opts:
            if n in names:
                return names.index(n)
        for l in label_opts:
            if l in labels:
                return labels.index(l)
        return None

    i_min = idx(["minutes"],                         ["MIN"])
    i_pts = idx(["points"],                           ["PTS"])
    i_reb = idx(["rebounds", "totalrebounds"],        ["REB"])
    i_ast = idx(["assists"],                          ["AST"])
    i_tpm = idx(["threepointfieldgoalsmade",
                 "threepointfieldgoals"],             ["3PT"])

    # collect every game's stat row across all seasonTypes/categories
    rows = []
    for stype in (data.get("seasonTypes") or []):
        for cat in (stype.get("categories") or []):
            for ev in (cat.get("events") or []):
                stats = ev.get("stats")
                if isinstance(stats, list) and stats:
                    rows.append(stats)

    if not rows:
        return {"gp": 0, "ppg": None, "rpg": None, "apg": None,
                "tpm": None, "mpg": None, "_nolog": True}

    def col_avg(i, rowset):
        if i is None:
            return None
        vals = [_num(r[i]) for r in rowset if i < len(r)]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    last5 = rows[:5]   # ESPN lists most-recent first
    return {
        "gp":  len(rows),
        "ppg": col_avg(i_pts, rows),
        "rpg": col_avg(i_reb, rows),
        "apg": col_avg(i_ast, rows),
        "tpm": col_avg(i_tpm, rows),
        "mpg": col_avg(i_min, rows),
        "ppg_l5": col_avg(i_pts, last5),
        "rpg_l5": col_avg(i_reb, last5),
        "apg_l5": col_avg(i_ast, last5),
        "min_l5": col_avg(i_min, last5),
    }


# ── 6. team defense (opponent stats per team) ────────────────────────
# Which season's numbers to use. In preseason / very early season the new
# season has no data yet, so we try the current end-year first, then last
# season as the baseline (same idea as the players' season averages).
def _season_candidates():
    t = _et_today()
    cur_end = t.year + 1 if t.month >= 10 else t.year
    return [cur_end, cur_end - 1]

# candidate ESPN stat names (lowercased) for each opponent metric we want.
# We try several because ESPN's naming varies; the diagnostic below prints
# every name it actually saw so we can lock these down on the first run.
_DEF_KEYS = {
    "opp_pts": ["opponentpointspergame", "avgpointsagainst", "pointsagainst",
                "opppointspergame", "opponentpoints", "pointsagainstpergame"],
    "opp_reb": ["opponentreboundspergame", "reboundsagainst",
                "opponentrebounds", "opprebounds", "opponenttotalreboundspergame"],
    "opp_ast": ["opponentassistspergame", "assistsagainst", "opponentassists"],
    "opp_tpm": ["opponentthreepointfieldgoalsmadepergame",
                "opponentthreepointfieldgoalsmade",
                "threepointfieldgoalsmadeagainst"],
    "pace":    ["pace", "pacefactor", "avgpace"],
}


def _flatten_team_stats(data):
    """Return {stat_name_lower: per_game_value} from a core statistics payload."""
    flat = {}
    if not isinstance(data, dict):
        return flat
    cats = (((data.get("splits") or {}).get("categories")) or [])
    for cat in cats:
        for st in (cat.get("stats") or []):
            nm = str(st.get("name") or "").lower()
            if not nm:
                continue
            val = st.get("perGameValue")
            if val is None:
                val = st.get("value")
            if val is None:
                val = _num(st.get("displayValue"))
            if val is not None:
                flat[nm] = float(val)
    return flat


def _parse_team_def(flat):
    """Pick opponent metrics out of a flattened stat map."""
    rec = {}
    for key, cands in _DEF_KEYS.items():
        for c in cands:
            if c in flat and flat[c]:
                rec[key] = round(flat[c], 2)
                break
    return rec


def get_team_defense(teams):
    """{team_abb: {opp_pts, opp_reb, opp_ast, opp_tpm, pace?, season}} for all
    teams we can parse. Returns {} if ESPN's core stats are unreachable (the
    model then simply falls back to a neutral 1.0 matchup factor)."""
    out = {}
    seasons = _season_candidates()
    for tid, meta in teams.items():
        abb = meta.get("abb")
        if not abb:
            continue
        for season in seasons:
            flat = _flatten_team_stats(
                _fetch_json(f"{ESPN_CORE}/seasons/{season}/types/2/teams/{tid}/statistics"))
            rec = _parse_team_def(flat)
            if rec:
                rec["season"] = season
                out[abb] = rec
                break
        time.sleep(0.15)
    return out


# ── assemble everything ───────────────────────────────────────────────
def build_nba_data():
    today = _et_today()
    print(f"  Building NBA data for {today} …")

    games = get_games(today)
    print(f"    games: {len(games)}")

    teams = get_teams()
    print(f"    teams: {len(teams)}")

    inj_id, inj_name = get_injuries()
    print(f"    injuries: {len(inj_id)} by-id / {len(inj_name)} by-name")

    team_defense = get_team_defense(teams)
    print(f"    team defense parsed: {len(team_defense)}/{len(teams)} teams")

    # teams playing today → the player universe
    playing = set()
    for g in games:
        if g.get("home_id"): playing.add(str(g["home_id"]))
        if g.get("away_id"): playing.add(str(g["away_id"]))
    # if no games today (off day), fall back to all teams so the pull still works
    if not playing:
        playing = set(teams.keys())
    print(f"    teams playing/scanned: {len(playing)}")

    team_abb = {tid: teams.get(tid, {}).get("abb", "") for tid in playing}

    players = []
    fetched = 0
    for tid in sorted(playing):
        roster = get_team_roster(tid)
        for p in roster:
            if PLAYER_CAP and fetched >= PLAYER_CAP:
                break
            log = get_player_log(p["id"]) or {}
            fetched += 1
            time.sleep(0.25)   # be polite to ESPN
            status = inj_id.get(p["id"]) or inj_name.get((p["name"] or "").lower()) or {}
            players.append({
                "id":     p["id"],
                "name":   p["name"],
                "team":   team_abb.get(tid, ""),
                "team_id": tid,
                "pos":    p["pos"],
                "status": status.get("status"),
                "status_detail": status.get("detail"),
                **{k: log.get(k) for k in
                   ("gp","ppg","rpg","apg","tpm","mpg",
                    "ppg_l5","rpg_l5","apg_l5","min_l5")},
            })
        if PLAYER_CAP and fetched >= PLAYER_CAP:
            print(f"    (player cap {PLAYER_CAP} reached — raise NBA_PLAYER_CAP for full slate)")
            break
    print(f"    players built: {len(players)}")

    return {
        "ts":     time.time(),
        "today":  today.strftime("%Y-%m-%d"),
        "games":  games,
        "teams":  teams,
        "players": players,
        "team_defense": team_defense,
        "injuries_count": len(inj_id) + len(inj_name),
    }


# ── read path used by the Flask app ───────────────────────────────────
def load_nba(force=False):
    """Return NBA data: committed snapshot unless force-live."""
    if not (force or _FORCE_LIVE):
        snap = read_snapshot()
        if snap and snap.get("players"):
            print(f"  Using NBA snapshot ({len(snap['players'])} players, "
                  f"{_snapshot_age_h(snap)}h old)")
            return snap
    return build_nba_data()


# ── diagnostics (run directly) ────────────────────────────────────────
def _dump_shape(label, url):
    print(f"\n  ── raw shape: {label} ──")
    data = _fetch_json(url)
    if data is None:
        print("     (no data)")
        return
    if isinstance(data, dict):
        print("     top-level keys:", list(data.keys())[:15])
    sample = json.dumps(data)[:600]
    print("     sample:", sample, "…")


def _dump_def_names(tid):
    """Print every stat name ESPN exposes for one team, so we can map the
    opponent metrics precisely if _DEF_KEYS missed them."""
    for season in _season_candidates():
        data = _fetch_json(f"{ESPN_CORE}/seasons/{season}/types/2/teams/{tid}/statistics")
        flat = _flatten_team_stats(data)
        if flat:
            print(f"\n  ── team {tid} stat names (season {season}, {len(flat)} stats) ──")
            for nm in sorted(flat.keys()):
                print(f"       {nm} = {flat[nm]}")
            return
    print(f"\n  ── team {tid}: no stats returned for either season ──")


if __name__ == "__main__":
    _FORCE_LIVE = True
    print("=" * 68)
    print(f"NBA DATA PULL  —  {_et_now():%Y-%m-%d %H:%M ET}")
    print("=" * 68)

    data = build_nba_data()
    ok = write_snapshot(data)
    print(f"\n  snapshot written: {ok}  ({SNAP_NAME})")

    # quick report
    print("\n  === SAMPLE GAMES ===")
    for g in data["games"][:3]:
        print(f"    {g.get('away_abb')} @ {g.get('home_abb')}  "
              f"[{g.get('status')}]")
    print("\n  === SAMPLE PLAYERS (with parsed stats) ===")
    shown = 0
    for p in data["players"]:
        if p.get("ppg") is not None:
            print(f"    {p['name']:22s} {p['team']:4s} {p['pos']:3s}  "
                  f"PTS {p['ppg']}  REB {p['rpg']}  AST {p['apg']}  "
                  f"3PM {p['tpm']}  MIN {p['mpg']}  (GP {p['gp']})"
                  + (f"  [{p['status']}]" if p.get('status') else ""))
            shown += 1
        if shown >= 8:
            break
    if shown == 0:
        print("    ⚠ no player stats parsed — dumping gamelog shape to fix parser:")
        if data["players"]:
            _dump_shape("gamelog",
                        f"{ESPN_COMMON}/athletes/{data['players'][0]['id']}/gamelog")

    # team defense report
    print("\n  === TEAM DEFENSE (opponent per-game) ===")
    td = data.get("team_defense") or {}
    if td:
        for abb in list(td.keys())[:6]:
            r = td[abb]
            print(f"    {abb:4s} oppPTS {r.get('opp_pts')}  oppREB {r.get('opp_reb')}"
                  f"  oppAST {r.get('opp_ast')}  opp3PM {r.get('opp_tpm')}"
                  f"  pace {r.get('pace')}  (szn {r.get('season')})")
    else:
        print("    ⚠ no team defense parsed — dumping one team's stat names to map them:")
        if data.get("teams"):
            _dump_def_names(sorted(data["teams"].keys())[0])

    # dump shapes for anything that came back empty, so we can fix parsers
    if not data["games"]:
        _dump_shape("scoreboard", f"{ESPN}/scoreboard")
    if not data["teams"]:
        _dump_shape("teams", f"{ESPN}/teams")
    if data["injuries_count"] == 0:
        _dump_shape("injuries", f"{ESPN}/injuries")

    print("\n" + "=" * 68)
    print("DONE. Copy the whole log and send it back.")
    print("=" * 68)
