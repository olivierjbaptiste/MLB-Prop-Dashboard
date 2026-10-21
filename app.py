"""
app.py - Flask web server for MLB HR Prop Dashboard
"""

import os
import re
import json
import threading
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
import dashboard as db
import nba
import nba_model
import nfl
import nfl_model

app = Flask(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# odds_free loaded via dashboard.py

# Simple cache
_data = None
_updated = None


def refresh_data():
    global _data, _updated
    print(f"[{datetime.now()}] Loading MLB data...")
    try:
        data = db.build_all_data(ODDS_API_KEY)
        _data    = data
        _updated = datetime.now().strftime("%B %d, %Y %I:%M %p")
        print(f"[{datetime.now()}] Data loaded successfully")
        print(f"  Batters:  {len(data.get('batters', []))}")
        print(f"  Pitchers: {len(data.get('pitchers', []))}")
        print(f"  Games:    {len(data.get('games', []))}")
        print(f"  Props:    {len(data.get('props', []))}")
    except Exception as e:
        print(f"[{datetime.now()}] Data load error: {e}")
        import traceback
        traceback.print_exc()


# Load data immediately on startup — not in background, so data is ready when the
# first request comes in. Skippable (DIAMOND_NO_STARTUP=1) so the module can be
# imported for tests/tooling without triggering a live data pull. In production
# the variable is unset, so startup runs exactly as before.
if os.environ.get("DIAMOND_NO_STARTUP") != "1":
    print("Starting MLB HR Prop Dashboard...")
    refresh_data()
    print("Startup data load complete")


def _parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# A game is "not happening as scheduled" (shown, but never as a pregame pick).
# Checked on BOTH the explicit status_name and the human status text, so a
# postponement is caught even when status_name is missing.
_NOT_HAPPENING = ("POSTPON", "CANCEL", "SUSPEND", "DELAY")
_NOT_HAPPENING_TEXT = re.compile(r"\b(postponed|cancell?ed|suspended|delayed|ppd)\b", re.I)
# Anchored status-text patterns — only a LAST resort when ESPN gives us neither an
# explicit state nor a usable tip time. Word-bounded so "ot" never matches "Not
# Started" and "final" never matches a substring of something else.
_STATUS_LIVE_RE = re.compile(r"\b(final|halftime|overtime|end of|in\s*progress|live)\b", re.I)
_STATUS_PERIOD_RE = re.compile(r"\b([1-4](st|nd|rd|th)|[1-4]?\s*ot)\b", re.I)

MAX_SNAPSHOT_AGE_H = 30   # a healthy daily snapshot is well under this
CLOCK_SKEW_H = 2          # tolerate small clock skew; reject timestamps further in the future


def _not_happening(g):
    name = str(g.get("status_name") or "").upper()
    if any(w in name for w in _NOT_HAPPENING):
        return True
    return bool(_NOT_HAPPENING_TEXT.search(str(g.get("status") or "")))


def _game_tipped(g, now):
    """True once a game has STARTED or FINISHED (so its players are no longer
    pregame). Order matters:
      1. Postponed / canceled / suspended / delayed  -> NOT tipped (never started).
      2. ESPN's explicit state 'in'/'post' or completed -> tipped (authoritative).
      3. Scheduled tip time has passed (covers the exact-tipoff boundary and a
         lagging 'pre' state) -> tipped.
      4. A future tip time -> NOT tipped.
      5. No usable time: anchored status-text patterns (never bare substrings)."""
    if _not_happening(g):
        return False
    state = str(g.get("state") or "").lower()
    if state in ("in", "post") or bool(g.get("completed")):
        return True
    start = _parse_iso(g.get("start"))
    if start is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return now >= start          # exact boundary counts as started
    status = str(g.get("status") or "")
    return bool(_STATUS_LIVE_RE.search(status) or _STATUS_PERIOD_RE.search(status))


def _game_block_reason(g, now):
    """Why a game's players must NOT appear as active pregame picks, or None if the
    game is a clean upcoming game."""
    if _not_happening(g):
        blob = (str(g.get("status_name") or "") + " " + str(g.get("status") or "")).upper()
        if "POSTPON" in blob or "PPD" in blob:
            return "postponed"
        if "CANCEL" in blob:
            return "canceled"
        if "SUSPEND" in blob:
            return "suspended"
        return "delayed"
    if _game_tipped(g, now):
        return "started"
    return None


def _et_today_str():
    try:
        return nba._et_today().strftime("%Y-%m-%d")
    except Exception:
        return None


def _nba_status(d, now=None):
    """Honest data state for the NBA board. Validates that the snapshot is TODAY's
    and recent before calling it current, distinguishes a real off-day from a
    failed/partial/stale refresh, and never silently presents old data as a live
    pregame slate."""
    now = now or datetime.now(timezone.utc)
    players = d.get("players") or []
    games = d.get("games") or []
    snap_date = d.get("today")
    et_today = _et_today_str()
    sched = d.get("schedule_fetched")

    # Timestamp: numeric, positive, and not implausibly in the future (clock skew).
    ts = d.get("ts")
    ts_num = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
    age_h = (now.timestamp() - ts_num) / 3600.0 if (ts_num and ts_num > 0) else None
    ts_valid = (age_h is not None) and (age_h >= -CLOCK_SKEW_H)
    meta_missing = (not snap_date) or (not ts_valid)

    if players:
        # A populated snapshot is only "ok" if its metadata proves it's today's,
        # recent, and backed by a successful schedule fetch.
        if meta_missing:
            return {"state": "stale", "today": snap_date,
                    "reason": "Snapshot metadata is missing or invalid — can't confirm "
                              "this is today's slate, so projections may be out of date."}
        if et_today and snap_date != et_today:
            return {"state": "stale", "today": snap_date, "snapshot_date": snap_date,
                    "reason": "Showing the " + snap_date + " slate — today's ("
                              + et_today + ") data hasn't refreshed yet. These are not "
                              "current pregame picks."}
        if age_h is not None and age_h > MAX_SNAPSHOT_AGE_H:
            return {"state": "stale", "today": snap_date,
                    "reason": "Snapshot is " + str(round(age_h)) + "h old — it may not "
                              "reflect the latest lineups."}
        if sched is False:
            return {"state": "stale", "today": snap_date,
                    "reason": "The schedule refresh failed on the last run, so this "
                              "slate may be incomplete or out of date."}
        tipped = sum(1 for g in games if _game_tipped(g, now))
        return {"state": "ok", "games": len(games), "tipped_games": tipped,
                "today": snap_date}

    # ── empty snapshot ──────────────────────────────────────────────────
    if not d:
        return {"state": "unavailable", "reason": "NBA data isn't loaded yet."}
    if games and not players:
        return {"state": "incomplete",
                "reason": "Games are scheduled today, but player data didn't load — "
                          "projections are unavailable."}
    if sched is False:
        return {"state": "source_error",
                "reason": "Couldn't reach the NBA schedule source — data is "
                          "temporarily unavailable."}
    if sched is True and not games:
        # A confirmed off-day — but ONLY if the metadata confirms it is for today.
        if meta_missing:
            return {"state": "stale", "today": snap_date,
                    "reason": "Can't confirm today's schedule — the snapshot's date or "
                              "timestamp is missing or invalid."}
        if et_today and snap_date != et_today:
            return {"state": "stale", "today": snap_date,
                    "reason": "Last data is from " + snap_date + "; today's schedule "
                              "hasn't been checked yet."}
        # Enforce the same max-age bound as a populated snapshot: a today-dated but
        # hours-old empty snapshot can't be trusted to prove today is an off-day.
        if age_h is not None and age_h > MAX_SNAPSHOT_AGE_H:
            return {"state": "stale", "today": snap_date,
                    "reason": "The last schedule check was " + str(round(age_h))
                              + "h ago — today's slate hasn't been confirmed recently."}
        return {"state": "no_games", "reason": "No NBA games are scheduled today."}
    return {"state": "empty", "reason": "No NBA games to show right now."}


def get_nba_data():
    """Read the committed NBA snapshot and attach projections. Pure/local —
    never hits the network at request time (the GitHub Action refreshes it).
    Also flags per-player whether that player's game has already started, and
    attaches an honest data-status for the UI."""
    try:
        d = nba.read_snapshot() or {}
        now = datetime.now(timezone.utc)
        if d.get("players"):
            nba_model.project_all(d)
            by_team = {}
            for g in d.get("games", []):
                for k in (g.get("home_id"), g.get("away_id")):
                    if k is not None:
                        by_team[str(k)] = g
            for p in d["players"]:
                g = by_team.get(str(p.get("team_id")))
                if g:
                    p["game_start"] = g.get("start")
                    p["game_status"] = g.get("status")
                    p["game_state"] = g.get("state")
                    p["game_season_type"] = g.get("season_type")
                    reason = _game_block_reason(g, now)
                    p["block_reason"] = reason               # None | started | postponed | ...
                    p["tipped"] = (reason == "started")
                    p["pregame_ok"] = (reason is None)
                else:
                    # A playable player we can't tie to a scheduled game is not a
                    # verified pregame opportunity — exclude it with a reason.
                    p["block_reason"] = "unmatched"
                    p["tipped"] = False
                    p["pregame_ok"] = False
        d["data_status"] = _nba_status(d, now)
        return d
    except Exception as e:
        print(f"NBA load error: {e}")
        return {"data_status": {"state": "error",
                                "reason": "NBA data is temporarily unavailable."}}


def get_nfl_data():
    """Read the committed NFL snapshot and attach projections. Pure/local."""
    try:
        d = nfl.read_snapshot() or {}
        if d.get("players"):
            nfl_model.project_all(d)
        return d
    except Exception as e:
        print(f"NFL load error: {e}")
        return {}


@app.route("/")
def index():
    try:
        data    = _data or db.build_all_data(ODDS_API_KEY)
        updated = _updated or "Just now"
        today   = data.get("today", "") if data else ""
        data_json = json.dumps(data or {})
    except Exception as e:
        print(f"Route error: {e}")
        import traceback; traceback.print_exc()
        data_json = "{}"
        updated   = "Error loading data"
        today     = ""
    try:
        nba_json = json.dumps(get_nba_data())
    except Exception as e:
        print(f"NBA route error: {e}")
        nba_json = "{}"
    try:
        nfl_json = json.dumps(get_nfl_data())
    except Exception as e:
        print(f"NFL route error: {e}")
        nfl_json = "{}"
    return render_template("index.html",
                           data=data_json,
                           nba_data=nba_json,
                           nfl_data=nfl_json,
                           updated=updated,
                           today=today,
                           loading=False)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    print("[REFRESH] Manual refresh triggered")
    threading.Thread(target=refresh_data, daemon=True).start()
    return jsonify({"status": "refreshing", "message": "Data reload started"})


@app.route("/api/data")
def api_data():
    if not _data:
        refresh_data()
    return jsonify(_data or {})


@app.route("/api/scores")
def api_scores():
    """Live scores for mlb | nba | nfl from ESPN's free scoreboard (cached).
    Query: ?league=nba&date=YYYYMMDD  (NFL also accepts &week=N). Read-only;
    does not touch predictions, odds, or the Best Edges engine."""
    import scores
    league = (request.args.get("league") or "nba").lower()
    if league not in scores.LEAGUES:
        return jsonify({"error": "league must be one of nba, nfl, mlb",
                        "games": [], "stale": True}), 400
    date = request.args.get("date")        # YYYYMMDD (optional; ESPN defaults to today)
    week = request.args.get("week")        # NFL only
    try:
        return jsonify(scores.get_scores(league, date=date, week=week))
    except Exception as e:
        print(f"scores route error: {e}")
        return jsonify({"error": "scores temporarily unavailable", "league": league,
                        "games": [], "stale": True, "last_updated": None}), 200


@app.route("/manifest.json")
def manifest():
    return jsonify({
        "name": "Diamond Analytics",
        "short_name": "Diamond",
        "description": "MLB Home Run Prop Research Dashboard",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#070f0a",
        "theme_color": "#22c55e",
        "icons": [
            {"src": "https://img.icons8.com/fluency/192/baseball.png", "sizes": "192x192", "type": "image/png"},
            {"src": "https://img.icons8.com/fluency/512/baseball.png", "sizes": "512x512", "type": "image/png"}
        ]
    })


@app.route("/health")
def health():
    return jsonify({
        "status":    "ok",
        "updated":   _updated,
        "batters":   len(_data.get("batters",   [])) if _data else 0,
        "pitchers":  len(_data.get("pitchers",  [])) if _data else 0,
        "games":     len(_data.get("games",     [])) if _data else 0,
        "matchups":  len(_data.get("matchups",  [])) if _data else 0,
        "top_picks": len(_data.get("top_picks", [])) if _data else 0,
        "props":     len(_data.get("props",     [])) if _data else 0,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
