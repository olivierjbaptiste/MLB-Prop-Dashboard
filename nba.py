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
import re
import json
import gzip
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta, tzinfo

# ── time (Eastern) ────────────────────────────────────────────────────
# America/New_York shifts EDT(-4)/EST(-5) across the NBA season (the DST change
# falls mid-season), so a flat offset would roll the date wrong around midnight.
# Prefer the IANA tz database (present on Linux/Render, and on Windows when the
# `tzdata` package is installed). If it's genuinely unavailable, fall back to a
# tzinfo that still implements the US Eastern DST rule — never a flat offset.
class _USEasternDST(tzinfo):
    """US Eastern with DST rules: EDT (UTC-4) from the 2nd Sunday of March to the
    1st Sunday of November, EST (UTC-5) otherwise. A dependency-free safety net."""
    _EDT = timedelta(hours=-4)
    _EST = timedelta(hours=-5)

    @staticmethod
    def _nth_sunday(year, month, n):
        d = datetime(year, month, 1)
        first_sun = 1 + (6 - d.weekday()) % 7      # Monday=0 … Sunday=6
        return datetime(year, month, first_sun + 7 * (n - 1))

    def _is_dst(self, dt):
        y = dt.year
        start = self._nth_sunday(y, 3, 2).replace(hour=2)   # 2nd Sun Mar 02:00
        end = self._nth_sunday(y, 11, 1).replace(hour=2)    # 1st Sun Nov 02:00
        naive = dt.replace(tzinfo=None)
        return start <= naive < end

    def utcoffset(self, dt):
        return self._EDT if (dt and self._is_dst(dt)) else self._EST

    def dst(self, dt):
        return timedelta(hours=1) if (dt and self._is_dst(dt)) else timedelta(0)

    def tzname(self, dt):
        return "EDT" if (dt and self._is_dst(dt)) else "EST"


try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    # ZoneInfo can import yet raise at use time if no tz data is present; verify.
    datetime.now(ET).utcoffset()
except Exception:                                   # pragma: no cover
    ET = _USEasternDST()
def _et_now():   return datetime.now(ET)
def _et_today(): return _et_now().date()

# ── snapshot (mirrors dashboard.py _read/_write_snapshot) ─────────────
_SNAP_DIR  = os.path.dirname(os.path.abspath(__file__))
SNAP_NAME  = "nba_snapshot.json"
_FORCE_LIVE = False          # the pull script flips this to True

# 0 = full slate (no cap) — the default now that the pipeline is proven.
# Set NBA_PLAYER_CAP to a small number to keep a run fast while testing.
PLAYER_CAP = int(os.environ.get("NBA_PLAYER_CAP") or "0")

UA          = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ESPN        = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_COMMON = "https://site.api.espn.com/apis/common/v3/sports/basketball/nba"
ESPN_CORE   = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
BBREF       = "https://www.basketball-reference.com"


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
def _fetch_json(url, timeout=15, attempts=3, quiet=False):
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
                if not quiet:
                    print(f"    fetch fail: {url[:70]}… → {type(e).__name__}")
            else:
                time.sleep(1.2 * (i + 1))
    return None


def _fetch_text(url, timeout=20, attempts=2, quiet=False):
    """Return page text, or None. Browser UA so bbref doesn't 403. Never raises."""
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={
                **UA, "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "gzip, identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw.decode("utf-8", "replace")
        except Exception as e:
            if i == attempts - 1:
                if not quiet:
                    print(f"    text fetch fail: {url[:70]}… → {type(e).__name__}")
            else:
                time.sleep(2.0 * (i + 1))
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


# ESPN scoreboard season.type integers → our labels (same mapping scores.py uses).
_SCOREBOARD_SEASON_TYPE = {1: "preseason", 2: "regular", 3: "postseason", 4: "offseason"}


def _game_season_type(ev):
    """Season type for a scoreboard event, handled safely.
    ESPN puts it at event.season.type (int). Unknown/missing -> 'unknown'."""
    season = ev.get("season")
    if not isinstance(season, dict):
        return "unknown"
    t = season.get("type")
    if t is None:
        return "unknown"
    try:
        return _SCOREBOARD_SEASON_TYPE.get(int(t), "unknown")
    except (TypeError, ValueError):
        return "unknown"


# Whether the most recent get_games() actually reached the ESPN scoreboard.
# Lets build_nba_data distinguish a genuine off-day (fetch ok, zero games) from
# a failed fetch (both otherwise look like an empty game list).
_last_schedule_fetched = False


# ── 1. games (scoreboard) ─────────────────────────────────────────────
def get_games(date_obj=None):
    global _last_schedule_fetched
    d = date_obj or _et_today()
    data = _fetch_json(f"{ESPN}/scoreboard?dates={d.strftime('%Y%m%d')}")
    # A fetch only counts as a successful schedule read if the response is a dict
    # that actually carries an `events` list. ESPN returning {} (or any payload
    # without a valid events list) is a MALFORMED response, not an off-day — it
    # must mark schedule_fetched False so the UI reports a source error rather than
    # masquerading as "no games today". A genuine off-day is {"events": []}.
    valid_schedule = isinstance(data, dict) and isinstance(data.get("events"), list)
    _last_schedule_fetched = valid_schedule
    games = []
    if not valid_schedule:
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
                # Authoritative lifecycle from ESPN: state is pre|in|post, and
                # status_name is e.g. STATUS_SCHEDULED / STATUS_IN_PROGRESS /
                # STATUS_FINAL / STATUS_POSTPONED. The UI uses these (not fuzzy
                # status-text matching) to decide what has started.
                "state":       (st.get("state") or "").lower(),
                "status_name": (st.get("name") or "").upper(),
                "completed":   bool(st.get("completed")),
                "start":     ev.get("date"),
                "season_type": _game_season_type(ev),
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
# ── season-aware game-log parsing ─────────────────────────────────────
# Averages must come from REGULAR-season games of ONE season only — never a
# mix of preseason, playoffs, or multiple seasons (which previously inflated
# games-played past 82 and polluted per-game averages). We also track how many
# games the player has in the CURRENT season separately, so the model can tell
# a projection backed by current-season evidence apart from one resting on last
# season's baseline.
MIN_BASELINE_GP = 3   # a season needs at least this many REG games to be the baseline


def _season_type_from_display(display):
    """ESPN seasonType displayName -> REG | POST | PRE | OTHER."""
    d = (display or "").lower()
    if "regular" in d:
        return "REG"
    if "post" in d or "playoff" in d:
        return "POST"
    if "pre" in d:
        return "PRE"
    return "OTHER"


def _season_from_display(display, fallback=None):
    """'2025-26 Regular Season' -> 2026 (NBA end-year)."""
    m = re.search(r"(\d{4})-(\d{2})", display or "")
    if m:
        return int(m.group(1)) + 1
    m = re.search(r"(\d{4})", display or "")
    return int(m.group(1)) if m else fallback


def _season_from_date(date_str):
    """'2026-10-21' -> 2027 (NBA end-year). An NBA season starting in Oct belongs
    to the next calendar year; months Jan-Jun belong to the current year."""
    m = re.match(r"(\d{4})-(\d{2})", date_str or "")
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return y + 1 if mo >= 7 else y


def _gamelog_games(data, default_season=None):
    """Per-game rows from an ESPN athlete gamelog: one row per event (de-duped
    across categories), each tagged with season + season_type. DNP rows are kept
    here; _summarize_reg filters on minutes.

    Season is identified from the seasonType label ('2025-26 …'); when the label
    omits the year (ESPN sometimes returns a bare 'Regular Season'), we fall back
    to `default_season` (the season the caller requested) and then to the game's
    own date — so a game is never dropped for an unidentified season."""
    if not data:
        return []
    names = [str(n).lower() for n in (data.get("names") or [])]
    labels = [str(l).upper() for l in (data.get("labels") or [])]

    def idx(name_opts, label_opts):
        for n in name_opts:
            if n in names:
                return names.index(n)
        for l in label_opts:
            if l in labels:
                return labels.index(l)
        return None

    I = {"min": idx(["minutes"], ["MIN"]),
         "pts": idx(["points"], ["PTS"]),
         "reb": idx(["rebounds", "totalrebounds"], ["REB"]),
         "ast": idx(["assists"], ["AST"]),
         "tpm": idx(["threepointfieldgoalsmade", "threepointfieldgoals"], ["3PT"])}
    meta = data.get("events") or {}
    out, seen = [], set()
    for stype in (data.get("seasonTypes") or []):
        st = _season_type_from_display(stype.get("displayName"))
        # Explicit year in the label is the strongest signal; but it may be absent
        # (bare 'Regular Season'). We DON'T jump to default_season here — the game's
        # own date is more reliable and prevents a last-season game returned for a
        # new-season request (ignored ?season=) from being mislabeled as current.
        hdr_season = _season_from_display(stype.get("displayName"))
        for cat in (stype.get("categories") or []):
            for ev in (cat.get("events") or []):
                eid = ev.get("eventId") or ev.get("id")
                stats = ev.get("stats")
                if not isinstance(stats, list) or not stats:
                    continue
                m = meta.get(str(eid), {}) if eid else {}
                date = (m.get("gameDate") or m.get("date") or "")[:10]
                # A season is VERIFIED only when the response itself establishes it —
                # an explicit year in the label or the game's own date. default_season
                # is the REQUESTED query year; ESPN may ignore ?season= and return a
                # different season's log, so default_season is an unverified last
                # resort: it keeps a game usable as a historical baseline but must
                # never, on its own, count as current-season evidence.
                vseason = hdr_season if hdr_season is not None else _season_from_date(date)
                season = vseason if vseason is not None else default_season
                key = eid if eid else (season, st, date)
                if key in seen:                  # same game under >1 category
                    continue
                seen.add(key)

                def cell(k, stats=stats):
                    i = I[k]
                    return _num(stats[i]) if (i is not None and i < len(stats)) else None
                out.append({
                    "eid": eid, "season": season, "season_verified": vseason is not None,
                    "season_type": st, "date": date,
                    "min": cell("min"), "pts": cell("pts"), "reb": cell("reb"),
                    "ast": cell("ast"), "tpm": cell("tpm"),
                })
    return out


def _summarize_reg(games, current_season=None, min_baseline_gp=MIN_BASELINE_GP):
    """Season averages + last-5 form from REGULAR-season, played games of a single
    baseline season. Also reports current-season games played, kept separate so a
    projection resting on last season's numbers is never mistaken for one backed
    by current-season evidence. Pure; no network.

    Returns gp/ppg/… for the baseline season, plus:
      baseline_season  — the season the averages come from (NBA end-year), or None
      cur_gp           — REG games the player has in `current_season` (0 on opening night)
    """
    reg = [g for g in games if g.get("season_type") == "REG"
           and g.get("min") is not None and g["min"] > 0]
    # Current-season evidence requires a VERIFIED season (label year or game date).
    # A game whose season is only the requested query year is NOT proof the player
    # has played in the current season, so it must not raise cur_gp (which drives
    # the HIGH / "current" confidence tier). Rows without the flag (none exist in
    # this codebase; _gamelog_games always sets it) are treated as verified.
    cur_gp = sum(1 for g in reg
                 if current_season is not None
                 and g.get("season") == current_season
                 and g.get("season_verified", True))
    empty = {"gp": 0, "ppg": None, "rpg": None, "apg": None, "tpm": None,
             "mpg": None, "ppg_l5": None, "rpg_l5": None, "apg_l5": None,
             "min_l5": None, "baseline_season": None, "cur_gp": cur_gp, "_nolog": True}
    if not reg:
        return empty

    by_season = {}
    for g in reg:
        if g.get("season") is not None:
            by_season.setdefault(g["season"], []).append(g)
    if not by_season:
        return empty
    # Baseline = most recent season with a real sample; fall back to the most
    # recent season present if none clears the bar. This keeps last season's
    # stable numbers until the new season has enough games to stand on its own.
    eligible = sorted([s for s, gs in by_season.items() if len(gs) >= min_baseline_gp],
                      reverse=True)
    baseline_season = eligible[0] if eligible else max(by_season)
    base = sorted(by_season[baseline_season],
                  key=lambda g: g.get("date") or "", reverse=True)
    # If the baseline we landed on is the CURRENT season but nothing in it is
    # verified, we can't honestly label these as current-season numbers. Keep the
    # games for a historical projection, but report no specific season so the UI
    # shows a neutral "last-season baseline" instead of claiming current-season
    # evidence we don't actually have.
    baseline_verified = any(g.get("season_verified", True) for g in by_season[baseline_season])
    reported_baseline = (None if (current_season is not None
                                  and baseline_season == current_season
                                  and not baseline_verified)
                         else baseline_season)

    def avg(k, rows):
        vals = [g[k] for g in rows if g.get(k) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    last5 = base[:5]
    return {
        "gp": len(base),
        "ppg": avg("pts", base), "rpg": avg("reb", base), "apg": avg("ast", base),
        "tpm": avg("tpm", base), "mpg": avg("min", base),
        "ppg_l5": avg("pts", last5), "rpg_l5": avg("reb", last5),
        "apg_l5": avg("ast", last5), "min_l5": avg("min", last5),
        "baseline_season": reported_baseline,
        "cur_gp": cur_gp,
    }


def get_player_log(athlete_id):
    """Season averages + last-5 form (REG, played games) + a separate current-season
    game count. Explicitly requests the current and prior seasons, because the
    default gamelog endpoint returns only the current season — which is EMPTY on
    opening night, so without the prior-season request an opening-night player would
    have zero games and no projection. None only if every fetch fails."""
    seasons = _season_candidates()          # [current_end, prior_end], e.g. [2027, 2026]
    base_url = f"{ESPN_COMMON}/athletes/{athlete_id}/gamelog"

    def _merge(dst, seen, rows):
        """Add rows not already present. If ESPN ignores ?season= and returns the
        same log for both requests, the same games arrive twice — dedupe by event
        id (falling back to season+type+date) so game counts aren't doubled."""
        for g in rows:
            k = g.get("eid") or (g.get("season"), g.get("season_type"), g.get("date"))
            if k in seen:
                continue
            seen.add(k)
            dst.append(g)

    games, seen, got_any = [], set(), False
    for yr in seasons:
        data = _fetch_json(f"{base_url}?season={yr}")
        if data:
            got_any = True
            _merge(games, seen, _gamelog_games(data, default_season=yr))
    if not got_any:
        # season parameter unsupported, or every season fetch failed — fall back to
        # the default endpoint once (season inferred from labels/dates).
        data = _fetch_json(base_url)
        if not data:
            return None
        _merge(games, seen, _gamelog_games(data))
    return _summarize_reg(games, current_season=seasons[0])


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
    "pace":    ["pacefactor", "pace", "avgpace"],
    # the team's OWN offensive output (ESPN core stats — confirmed names)
    "off_pts": ["avgpoints"],
    "off_reb": ["avgrebounds"],
    "off_ast": ["avgassists"],
    "off_tpm": ["avgthreepointfieldgoalsmade"],
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
                _fetch_json(f"{ESPN_CORE}/seasons/{season}/types/2/teams/{tid}/statistics",
                            attempts=1, quiet=True))
            rec = _parse_team_def(flat)
            if rec:
                rec["season"] = season
                out[abb] = rec
                break
        time.sleep(0.15)
    return out


# ── 6b. opponent defense from Basketball-Reference ────────────────────
# bbref's league page carries an "Opponent Per Game" table — i.e. what each
# team ALLOWS. That's the stat-specific defense ESPN doesn't give us:
# opp points, rebounds, assists, and threes made against each team.
def _bb_cell(row, stat):
    """Pull one data-stat value out of a bbref table row (tags stripped)."""
    m = re.search(r'data-stat="' + stat + r'"[^>]*>(?:<[^>]+>)*([^<]*)', row)
    return m.group(1).strip() if m else ""


def get_team_defense_bbref(teams):
    """{espn_abb: {opp_pts, opp_reb, opp_ast, opp_tpm, bbref_season}} parsed
    from bbref's Opponent Per Game table. Keyed to ESPN abbreviations by
    matching the team nickname (last word), so bbref's own team codes
    (BRK/PHO/CHO…) don't have to line up with ESPN's."""
    # nickname -> ESPN abbreviation, from the ESPN teams dict
    nick2abb = {}
    for meta in teams.values():
        nm = (meta.get("name") or "").strip().lower()
        abb = meta.get("abb")
        if nm and abb:
            nick2abb[nm.split()[-1]] = abb

    # parse each candidate season; keep whichever has the fuller table (the
    # upcoming season's page can exist but be empty during preseason)
    best = {}
    for season in _season_candidates():
        html = _fetch_text(f"{BBREF}/leagues/NBA_{season}.html", quiet=True)
        if not html or "per_game-opponent" not in html:
            continue
        parsed = _parse_bbref_opp(html, nick2abb, season)
        if len(parsed) > len(best):
            best = parsed
        if len(best) >= 25:        # got a full slate — no need to try older
            break
        time.sleep(1.5)            # be polite between bbref page loads
    return best


def _parse_bbref_opp(html, nick2abb, season):
    out = {}
    # bbref defers many tables inside HTML comments — unwrap them first
    html = html.replace("<!--", "").replace("-->", "")
    start = html.find('id="per_game-opponent"')
    if start == -1:
        return out
    block = html[start:html.find("</table>", start)]
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S):
        name = _bb_cell(row, "team") or _bb_cell(row, "team_name")
        if not name:
            continue
        abb = nick2abb.get(name.strip().lower().split()[-1])
        if not abb:
            continue   # skips "Team" header rows, "League Average", unmatched
        rec = {}
        # bbref prefixes every opponent-allowed column with opp_
        pts = _num(_bb_cell(row, "opp_pts"))
        reb = _num(_bb_cell(row, "opp_trb"))
        ast = _num(_bb_cell(row, "opp_ast"))
        tpm = _num(_bb_cell(row, "opp_fg3"))
        if pts: rec["opp_pts"] = round(pts, 2)
        if reb: rec["opp_reb"] = round(reb, 2)
        if ast: rec["opp_ast"] = round(ast, 2)
        if tpm: rec["opp_tpm"] = round(tpm, 2)
        if rec:
            rec["bbref_season"] = season
            out[abb] = rec
    return out


def _merge_defense(pace_def, opp_def):
    """Combine ESPN pace with bbref opponent allowances, keyed by ESPN abb."""
    merged = {}
    for abb in set(pace_def) | set(opp_def):
        merged[abb] = {**pace_def.get(abb, {}), **opp_def.get(abb, {})}
    return merged


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

    pace_def = get_team_defense(teams)           # ESPN → pace
    opp_def  = get_team_defense_bbref(teams)     # bbref → opponent allowances
    team_defense = _merge_defense(pace_def, opp_def)
    print(f"    team pace parsed: {len(pace_def)}/{len(teams)} teams")
    print(f"    bbref opponent defense parsed: {len(opp_def)}/{len(teams)} teams")

    # teams playing today → the player universe
    playing = set()
    for g in games:
        if g.get("home_id"): playing.add(str(g["home_id"]))
        if g.get("away_id"): playing.add(str(g["away_id"]))
    # on a true off day (no games) the board is simply empty — we don't scan
    # all 30 rosters for nothing
    if not playing:
        print("    no games today — player board will be empty")
    print(f"    teams playing: {len(playing)}")

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
                    "ppg_l5","rpg_l5","apg_l5","min_l5",
                    "cur_gp","baseline_season")},
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
        # True = schedule source was reached (so zero games means a real off-day);
        # False = the scoreboard fetch failed (empty board is a data problem).
        "schedule_fetched": _last_schedule_fetched,
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
        data = _fetch_json(f"{ESPN_CORE}/seasons/{season}/types/2/teams/{tid}/statistics",
                           attempts=1, quiet=True)
        flat = _flatten_team_stats(data)
        if flat:
            print(f"\n  ── team {tid} stat names (season {season}, {len(flat)} stats) ──")
            for nm in sorted(flat.keys()):
                print(f"       {nm} = {flat[nm]}")
            return
    print(f"\n  ── team {tid}: no stats returned for either season ──")


def _dump_bbref(teams):
    """Diagnose the bbref opponent table so we can fix the parser if it's empty."""
    for season in _season_candidates():
        url = f"{BBREF}/leagues/NBA_{season}.html"
        html = _fetch_text(url, quiet=True)
        if not html:
            print(f"    bbref {season}: no response (blocked or down?)")
            continue
        has = "per_game-opponent" in html
        print(f"    bbref {season}: {len(html)} bytes, per_game-opponent present: {has}")
        if not has:
            # maybe behind comments or renamed — show any opponent-ish table ids
            ids = re.findall(r'id="([^"]*opponent[^"]*)"', html)
            print(f"      opponent-ish table ids seen: {ids[:8]}")
            continue
        html = html.replace("<!--", "").replace("-->", "")
        start = html.find('id="per_game-opponent"')
        block = html[start:html.find("</table>", start)]
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", block, re.S)
        print(f"      rows in table: {len(rows)}")
        # show the first data row's raw cells so we can see the data-stat names
        for row in rows:
            nm = _bb_cell(row, "team_name") or _bb_cell(row, "team")
            if nm:
                stats = re.findall(r'data-stat="([^"]+)"', row)
                print(f"      sample row team='{nm}'  data-stats={stats[:20]}")
                print(f"        opp_pts={_bb_cell(row,'opp_pts')} opp_trb={_bb_cell(row,'opp_trb')} "
                      f"opp_ast={_bb_cell(row,'opp_ast')} opp_fg3={_bb_cell(row,'opp_fg3')}")
                break
        return
    print("    bbref: could not fetch either season page")


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
    # if bbref opponent allowances didn't come through, diagnose the page
    have_opp = any(td[a].get("opp_pts") for a in td)
    if not have_opp:
        print("    ⚠ no bbref opponent defense parsed — diagnosing the page:")
        _dump_bbref(data.get("teams") or {})

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
