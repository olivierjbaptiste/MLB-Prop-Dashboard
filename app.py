"""
app.py
Flask web server for MLB HR Prop Dashboard
Serves live dashboard at a real URL via Render
"""

import os
import json
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import dashboard as db

app = Flask(__name__)

# ── CONFIG ───────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# ── CACHE ────────────────────────────────────────────────────────
# Data is cached so the site loads fast
# It refreshes automatically every morning at 9am
_cache = {"data": None, "updated": None, "loading": False}


def refresh_data():
    """Pull fresh data and update cache."""
    global _cache
    if _cache["loading"]:
        return
    _cache["loading"] = True
    print(f"[{datetime.now()}] Refreshing MLB data...")
    try:
        data = db.build_all_data(ODDS_API_KEY)
        _cache["data"]    = data
        _cache["updated"] = datetime.now().strftime("%B %d, %Y %I:%M %p")
        print(f"[{datetime.now()}] Data refresh complete")
    except Exception as e:
        print(f"[{datetime.now()}] Data refresh error: {e}")
    finally:
        _cache["loading"] = False


def get_data():
    """Get cached data, refresh if empty."""
    if _cache["data"] is None:
        refresh_data()
    return _cache["data"]


# ── SCHEDULER ────────────────────────────────────────────────────
# Automatically refresh data every morning at 9am
scheduler = BackgroundScheduler()
scheduler.add_job(refresh_data, 'cron', hour=9, minute=0)
scheduler.start()

# Load data on startup in background thread
threading.Thread(target=refresh_data, daemon=True).start()


# ── ROUTES ───────────────────────────────────────────────────────
@app.route("/")
def index():
    """Main dashboard page."""
    data    = get_data()
    updated = _cache.get("updated", "Loading...")
    return render_template("index.html",
                           data=json.dumps(data),
                           updated=updated,
                           today=data.get("today", "") if data else "",
                           loading=data is None)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Manual refresh endpoint."""
    threading.Thread(target=refresh_data, daemon=True).start()
    return jsonify({"status": "refreshing"})


@app.route("/api/data")
def api_data():
    """Returns current data as JSON."""
    data = get_data()
    if not data:
        return jsonify({"error": "Data loading"}), 503
    return jsonify(data)


@app.route("/health")
def health():
    """Health check for Render."""
    return jsonify({"status": "ok", "updated": _cache.get("updated")})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
