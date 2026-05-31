"""
app.py - Flask web server for MLB HR Prop Dashboard
"""

import os
import json
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify
import dashboard as db

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
    return render_template("index.html",
                           data=data_json,
                           updated=updated,
                           today=today,
                           loading=False)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    threading.Thread(target=refresh_data, daemon=True).start()
    return jsonify({"status": "refreshing"})


@app.route("/api/data")
def api_data():
    if not _data:
        refresh_data()
    return jsonify(_data or {})


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "updated": _updated,
        "batters": len(_data.get("batters", [])) if _data else 0,
        "pitchers": len(_data.get("pitchers", [])) if _data else 0,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
