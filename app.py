"""
app.py - Flask web server for MLB HR Prop Dashboard
"""

import os
import json
import threading
from datetime import datetime
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


# Load data immediately on startup — not in background
# This ensures data is ready when first request comes in
print("Starting MLB HR Prop Dashboard...")
refresh_data()
print("Startup data load complete")


def get_nba_data():
    """Read the committed NBA snapshot and attach projections. Pure/local —
    never hits the network at request time (the GitHub Action refreshes it)."""
    try:
        d = nba.read_snapshot() or {}
        if d.get("players"):
            nba_model.project_all(d)
        return d
    except Exception as e:
        print(f"NBA load error: {e}")
        return {}


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
