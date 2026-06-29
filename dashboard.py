"""
dashboard.py - MLB data logic
No pandas/numpy dependency - pure Python
Includes lineup fetching from MLB Stats API
"""

import requests
import json
import os
import time
import tempfile
import csv
import io
import unicodedata
from datetime import date, datetime, timedelta, timezone

# Eastern-time date for MLB slates. MLB files games under their US date, so a
# UTC date (the server's default) is wrong in the evening once UTC rolls past
# midnight — which would make "today's games" point at the wrong day.
try:
    from zoneinfo import ZoneInfo
    _ET_ZONE = ZoneInfo("America/New_York")
except Exception:
    _ET_ZONE = None

def _et_now():
    if _ET_ZONE is not None:
        return datetime.now(_ET_ZONE)
    return datetime.now(timezone.utc) - timedelta(hours=4)  # EDT fallback (baseball season)

def _et_today():
    return _et_now().strftime("%Y-%m-%d")
# Lineup cache — persists confirmed lineups through game time
_lineup_cache = {}  # {game_id: lineups_dict}
_picks_cache  = []  # Last known picks
_matchups_cache = [] # Last known matchups
_pitcher_splits_cache = {}  # {pid: {hr_risk_rhb, hr_risk_lhb} | None}
_h2h_cache = {}  # {(batter_id, pitcher_id): {pa,ab,h,hr,...} | None}
_form_cache = {}  # {(batter_id, season, window): {games,hr,slg,last_hr_ago,...} | None}
_pform_cache = {}  # {(pitcher_id, season, window): {starts,hr,ip,hr9,...} | None}

# ── Daily snapshots ───────────────────────────────────────────────
# The GitHub Action does the heavy fetching once a day and commits JSON
# snapshots next to this file. At request time the app reads those snapshots
# (fast, no network) and only falls back to live fetching if a snapshot is
# missing. pull_stats.py sets _FORCE_LIVE so the Action always fetches fresh.
_FORCE_LIVE = False
_SNAP_DIR = os.path.dirname(os.path.abspath(__file__))

def _snap_path(name):
    return os.path.join(_SNAP_DIR, name)

def _read_snapshot(name):
    """Return the parsed snapshot dict, or None if absent/unreadable."""
    try:
        with open(_snap_path(name), "r") as f:
            return json.load(f)
    except Exception:
        return None

def _write_snapshot(name, payload):
    payload = dict(payload)
    payload["ts"] = time.time()
    with open(_snap_path(name), "w") as f:
        json.dump(payload, f)
    return _snap_path(name)

def _snapshot_age_h(snap):
    try:
        return round((time.time() - float(snap.get("ts", 0))) / 3600, 1)
    except Exception:
        return None

try:
    from odds_free import get_free_props
    FREE_ODDS_AVAILABLE = True
except Exception:
    FREE_ODDS_AVAILABLE = False
    def get_free_props(): return []

try:
    from weather import get_all_weather
    WEATHER_AVAILABLE = True
except Exception:
    WEATHER_AVAILABLE = False
    def get_all_weather(games): return {}

PARKS = {
    "COL": {"name": "Coors Field",             "factor": 1.35, "friendly": True},
    "CIN": {"name": "Great American Ball Park", "factor": 1.28, "friendly": True},
    "NYY": {"name": "Yankee Stadium",           "factor": 1.22, "friendly": True},
    "PHI": {"name": "Citizens Bank Park",       "factor": 1.18, "friendly": True},
    "BOS": {"name": "Fenway Park",              "factor": 1.12, "friendly": True},
    "TEX": {"name": "Globe Life Field",         "factor": 1.10, "friendly": True},
    "ATL": {"name": "Truist Park",              "factor": 1.08, "friendly": True},
    "SF":  {"name": "Oracle Park",             "factor": 0.72, "friendly": False},
    "MIA": {"name": "loanDepot Park",          "factor": 0.78, "friendly": False},
    "OAK": {"name": "Oakland Coliseum",        "factor": 0.81, "friendly": False},
    "SEA": {"name": "T-Mobile Park",           "factor": 0.85, "friendly": False},
}

TEAM_ABB = {
    "New York Yankees":"NYY","Los Angeles Dodgers":"LAD","Atlanta Braves":"ATL",
    "Houston Astros":"HOU","Philadelphia Phillies":"PHI","Baltimore Orioles":"BAL",
    "New York Mets":"NYM","Boston Red Sox":"BOS","Minnesota Twins":"MIN",
    "San Diego Padres":"SD","Milwaukee Brewers":"MIL","Cleveland Guardians":"CLE",
    "Texas Rangers":"TEX","Toronto Blue Jays":"TOR","Seattle Mariners":"SEA",
    "Arizona Diamondbacks":"ARI","San Francisco Giants":"SF","Detroit Tigers":"DET",
    "Kansas City Royals":"KC","Cincinnati Reds":"CIN","Colorado Rockies":"COL",
    "Chicago Cubs":"CHC","St. Louis Cardinals":"STL","Miami Marlins":"MIA",
    "Pittsburgh Pirates":"PIT","Washington Nationals":"WSH","Oakland Athletics":"OAK",
    "Los Angeles Angels":"LAA","Tampa Bay Rays":"TB","Chicago White Sox":"CWS",
}

SAMPLE_BATTERS = [
    {"name":"Aaron Judge",       "team":"NYY","hand":"R","avg":.311,"slg":.621,"hr":18,"barrel_pct":20.4,"avg_hit_speed":93.2,"iso":.310,"k_pct":22.1,"bb_pct":14.8,"woba":.415,"obp":.412,"avg_14d":.358,"slg_14d":.712,"hr_14d":5,"barrel_14d":24.1},
    {"name":"Shohei Ohtani",     "team":"LAD","hand":"L","avg":.298,"slg":.589,"hr":16,"barrel_pct":18.9,"avg_hit_speed":92.8,"iso":.291,"k_pct":19.8,"bb_pct":13.2,"woba":.399,"obp":.385,"avg_14d":.241,"slg_14d":.421,"hr_14d":1,"barrel_14d":12.2},
    {"name":"Yordan Alvarez",    "team":"HOU","hand":"L","avg":.301,"slg":.578,"hr":15,"barrel_pct":19.1,"avg_hit_speed":92.4,"iso":.277,"k_pct":18.4,"bb_pct":11.9,"woba":.408,"obp":.388,"avg_14d":.328,"slg_14d":.621,"hr_14d":4,"barrel_14d":22.8},
    {"name":"Bryce Harper",      "team":"PHI","hand":"L","avg":.295,"slg":.541,"hr":14,"barrel_pct":16.7,"avg_hit_speed":91.2,"iso":.246,"k_pct":20.1,"bb_pct":13.8,"woba":.395,"obp":.392,"avg_14d":.218,"slg_14d":.381,"hr_14d":1,"barrel_14d":9.8},
    {"name":"Freddie Freeman",   "team":"LAD","hand":"L","avg":.308,"slg":.521,"hr":11,"barrel_pct":14.2,"avg_hit_speed":90.1,"iso":.213,"k_pct":15.2,"bb_pct":12.1,"woba":.389,"obp":.395,"avg_14d":.312,"slg_14d":.534,"hr_14d":2,"barrel_14d":15.1},
    {"name":"Juan Soto",         "team":"NYM","hand":"L","avg":.289,"slg":.512,"hr":13,"barrel_pct":15.8,"avg_hit_speed":90.8,"iso":.223,"k_pct":17.9,"bb_pct":18.2,"woba":.392,"obp":.405,"avg_14d":.341,"slg_14d":.598,"hr_14d":4,"barrel_14d":19.2},
    {"name":"Gunnar Henderson",  "team":"BAL","hand":"L","avg":.278,"slg":.509,"hr":14,"barrel_pct":15.2,"avg_hit_speed":90.4,"iso":.231,"k_pct":24.2,"bb_pct":11.2,"woba":.372,"obp":.362,"avg_14d":.198,"slg_14d":.348,"hr_14d":1,"barrel_14d":8.1},
    {"name":"Matt Olson",        "team":"ATL","hand":"L","avg":.258,"slg":.498,"hr":15,"barrel_pct":17.8,"avg_hit_speed":91.9,"iso":.240,"k_pct":25.8,"bb_pct":12.8,"woba":.368,"obp":.352,"avg_14d":.289,"slg_14d":.558,"hr_14d":4,"barrel_14d":21.4},
    {"name":"Rafael Devers",     "team":"BOS","hand":"L","avg":.278,"slg":.511,"hr":14,"barrel_pct":15.9,"avg_hit_speed":91.1,"iso":.233,"k_pct":21.4,"bb_pct":9.8, "woba":.368,"obp":.348,"avg_14d":.301,"slg_14d":.548,"hr_14d":3,"barrel_14d":18.2},
    {"name":"Kyle Tucker",       "team":"HOU","hand":"L","avg":.274,"slg":.488,"hr":13,"barrel_pct":14.1,"avg_hit_speed":90.2,"iso":.214,"k_pct":19.2,"bb_pct":11.8,"woba":.365,"obp":.358,"avg_14d":.211,"slg_14d":.368,"hr_14d":1,"barrel_14d":8.8},
    {"name":"Bobby Witt Jr",     "team":"KC", "hand":"R","avg":.302,"slg":.521,"hr":13,"barrel_pct":14.8,"avg_hit_speed":90.8,"iso":.219,"k_pct":20.1,"bb_pct":7.9, "woba":.375,"obp":.358,"avg_14d":.348,"slg_14d":.598,"hr_14d":4,"barrel_14d":18.9},
    {"name":"Jose Ramirez",      "team":"CLE","hand":"S","avg":.282,"slg":.501,"hr":12,"barrel_pct":13.8,"avg_hit_speed":89.9,"iso":.219,"k_pct":14.8,"bb_pct":11.2,"woba":.372,"obp":.365,"avg_14d":.278,"slg_14d":.492,"hr_14d":2,"barrel_14d":13.2},
    {"name":"Mookie Betts",      "team":"LAD","hand":"R","avg":.291,"slg":.498,"hr":12,"barrel_pct":13.9,"avg_hit_speed":89.8,"iso":.207,"k_pct":16.8,"bb_pct":11.4,"woba":.378,"obp":.374,"avg_14d":.318,"slg_14d":.541,"hr_14d":3,"barrel_14d":16.8},
    {"name":"Ronald Acuna Jr",   "team":"ATL","hand":"R","avg":.294,"slg":.512,"hr":11,"barrel_pct":13.1,"avg_hit_speed":89.4,"iso":.218,"k_pct":20.8,"bb_pct":12.8,"woba":.375,"obp":.378,"avg_14d":.188,"slg_14d":.312,"hr_14d":0,"barrel_14d":6.2},
    {"name":"Fernando Tatis Jr", "team":"SD", "hand":"R","avg":.271,"slg":.489,"hr":13,"barrel_pct":14.2,"avg_hit_speed":90.1,"iso":.218,"k_pct":23.8,"bb_pct":9.2, "woba":.358,"obp":.341,"avg_14d":.298,"slg_14d":.538,"hr_14d":3,"barrel_14d":16.1},
    {"name":"Adolis Garcia",     "team":"TEX","hand":"R","avg":.261,"slg":.468,"hr":12,"barrel_pct":13.2,"avg_hit_speed":89.8,"iso":.207,"k_pct":26.8,"bb_pct":6.8, "woba":.342,"obp":.318,"avg_14d":.178,"slg_14d":.298,"hr_14d":0,"barrel_14d":5.8},
    {"name":"Julio Rodriguez",   "team":"SEA","hand":"R","avg":.269,"slg":.468,"hr":11,"barrel_pct":12.8,"avg_hit_speed":89.1,"iso":.199,"k_pct":23.1,"bb_pct":9.1, "woba":.348,"obp":.338,"avg_14d":.271,"slg_14d":.471,"hr_14d":2,"barrel_14d":12.9},
    {"name":"Corbin Carroll",    "team":"ARI","hand":"L","avg":.268,"slg":.442,"hr":8, "barrel_pct":9.8, "avg_hit_speed":87.2,"iso":.174,"k_pct":22.4,"bb_pct":10.9,"woba":.345,"obp":.355,"avg_14d":.241,"slg_14d":.398,"hr_14d":1,"barrel_14d":8.8},
    {"name":"Bo Bichette",       "team":"TOR","hand":"R","avg":.285,"slg":.468,"hr":10,"barrel_pct":11.8,"avg_hit_speed":88.4,"iso":.183,"k_pct":21.8,"bb_pct":7.2, "woba":.352,"obp":.341,"avg_14d":.321,"slg_14d":.521,"hr_14d":3,"barrel_14d":14.8},
    {"name":"Trea Turner",       "team":"PHI","hand":"R","avg":.281,"slg":.451,"hr":9, "barrel_pct":10.2,"avg_hit_speed":87.8,"iso":.170,"k_pct":18.9,"bb_pct":7.8, "woba":.348,"obp":.345,"avg_14d":.278,"slg_14d":.448,"hr_14d":1,"barrel_14d":10.1},
    {"name":"Elly De La Cruz",   "team":"CIN","hand":"S","avg":.258,"slg":.468,"hr":12,"barrel_pct":13.1,"avg_hit_speed":90.2,"iso":.210,"k_pct":28.1,"bb_pct":8.1, "woba":.342,"obp":.312,"avg_14d":.301,"slg_14d":.548,"hr_14d":4,"barrel_14d":17.2},
    {"name":"Sal Stewart",       "team":"CIN","hand":"R","avg":.243,"slg":.456,"hr":10,"barrel_pct":11.2,"avg_hit_speed":88.8,"iso":.213,"k_pct":24.8,"bb_pct":11.2,"woba":.338,"obp":.345,"avg_14d":.248,"slg_14d":.461,"hr_14d":2,"barrel_14d":11.8},
    {"name":"Tyler Stephenson",  "team":"CIN","hand":"R","avg":.268,"slg":.493,"hr":9, "barrel_pct":12.1,"avg_hit_speed":89.2,"iso":.225,"k_pct":21.4,"bb_pct":12.2,"woba":.355,"obp":.354,"avg_14d":.258,"slg_14d":.481,"hr_14d":1,"barrel_14d":11.4},
    {"name":"Pete Alonso",       "team":"NYM","hand":"R","avg":.254,"slg":.498,"hr":14,"barrel_pct":14.8,"avg_hit_speed":91.1,"iso":.244,"k_pct":23.8,"bb_pct":11.2,"woba":.358,"obp":.331,"avg_14d":.289,"slg_14d":.558,"hr_14d":4,"barrel_14d":18.1},
    {"name":"Cal Raleigh",       "team":"SEA","hand":"S","avg":.241,"slg":.489,"hr":13,"barrel_pct":13.9,"avg_hit_speed":90.4,"iso":.248,"k_pct":26.1,"bb_pct":9.8, "woba":.345,"obp":.312,"avg_14d":.198,"slg_14d":.368,"hr_14d":1,"barrel_14d":8.4},
    {"name":"Willy Adames",      "team":"SF", "hand":"R","avg":.251,"slg":.462,"hr":11,"barrel_pct":11.8,"avg_hit_speed":88.9,"iso":.211,"k_pct":24.8,"bb_pct":9.2, "woba":.338,"obp":.321,"avg_14d":.241,"slg_14d":.448,"hr_14d":2,"barrel_14d":11.2},
    {"name":"William Contreras", "team":"MIL","hand":"R","avg":.268,"slg":.478,"hr":10,"barrel_pct":12.1,"avg_hit_speed":89.4,"iso":.210,"k_pct":19.8,"bb_pct":10.1,"woba":.348,"obp":.341,"avg_14d":.312,"slg_14d":.538,"hr_14d":3,"barrel_14d":15.8},
    {"name":"Marcell Ozuna",     "team":"ATL","hand":"R","avg":.271,"slg":.501,"hr":12,"barrel_pct":13.4,"avg_hit_speed":90.1,"iso":.230,"k_pct":22.4,"bb_pct":8.8, "woba":.355,"obp":.332,"avg_14d":.158,"slg_14d":.278,"hr_14d":0,"barrel_14d":4.8},
    {"name":"Teoscar Hernandez", "team":"LAD","hand":"R","avg":.261,"slg":.471,"hr":11,"barrel_pct":12.8,"avg_hit_speed":89.8,"iso":.210,"k_pct":24.1,"bb_pct":7.2, "woba":.342,"obp":.318,"avg_14d":.298,"slg_14d":.528,"hr_14d":3,"barrel_14d":15.4},
    {"name":"Corey Seager",      "team":"TEX","hand":"L","avg":.278,"slg":.511,"hr":12,"barrel_pct":14.1,"avg_hit_speed":90.8,"iso":.233,"k_pct":18.8,"bb_pct":9.8, "woba":.365,"obp":.348,"avg_14d":.268,"slg_14d":.498,"hr_14d":2,"barrel_14d":13.8},
]

SAMPLE_PITCHERS = [
    {"name":"Cody Poteet",     "team":"CIN","hand":"R","role":"SP","era":4.85,"whip":1.45,"k9":7.8, "hr9":2.21,"barrel_pct":13.8,"ev_allowed":92.1,"iso_allowed":.258,"hr_risk_rhb":1.82,"hr_risk_lhb":2.41,"k_pct":18.2,"bb_pct":9.8, "swstr":11.2,"gb_pct":30.2,"velo_season":93.1,"velo_recent":91.2},
    {"name":"Grant Holmes",    "team":"CIN","hand":"R","role":"SP","era":4.12,"whip":1.30,"k9":8.8, "hr9":1.55,"barrel_pct":10.8,"ev_allowed":90.8,"iso_allowed":.182,"hr_risk_rhb":0.95,"hr_risk_lhb":0.62,"k_pct":21.5,"bb_pct":10.8,"swstr":14.0,"gb_pct":38.4,"velo_season":93.8,"velo_recent":92.1},
    {"name":"Trevor Megill",   "team":"MIL","hand":"R","role":"RP","era":4.21,"whip":1.38,"k9":9.8, "hr9":1.98,"barrel_pct":12.1,"ev_allowed":91.2,"iso_allowed":.241,"hr_risk_rhb":1.88,"hr_risk_lhb":2.08,"k_pct":22.8,"bb_pct":11.2,"swstr":13.8,"gb_pct":28.1,"velo_season":95.2,"velo_recent":94.8},
    {"name":"Dean Kremer",     "team":"BAL","hand":"R","role":"SP","era":4.52,"whip":1.35,"k9":7.9, "hr9":1.91,"barrel_pct":11.2,"ev_allowed":91.1,"iso_allowed":.228,"hr_risk_rhb":1.71,"hr_risk_lhb":2.11,"k_pct":19.8,"bb_pct":8.9, "swstr":12.1,"gb_pct":32.1,"velo_season":92.4,"velo_recent":91.8},
    {"name":"Zach Eflin",      "team":"TB", "hand":"R","role":"SP","era":4.12,"whip":1.28,"k9":8.2, "hr9":1.82,"barrel_pct":10.8,"ev_allowed":90.8,"iso_allowed":.218,"hr_risk_rhb":1.62,"hr_risk_lhb":1.98,"k_pct":20.1,"bb_pct":7.8, "swstr":12.8,"gb_pct":34.8,"velo_season":91.8,"velo_recent":90.2},
    {"name":"Freddy Peralta",  "team":"MIL","hand":"R","role":"SP","era":3.12,"whip":1.08,"k9":11.8,"hr9":0.98,"barrel_pct":8.1, "ev_allowed":89.1,"iso_allowed":.181,"hr_risk_rhb":0.88,"hr_risk_lhb":1.08,"k_pct":28.4,"bb_pct":9.2, "swstr":17.2,"gb_pct":32.8,"velo_season":93.4,"velo_recent":93.6},
    {"name":"Dylan Cease",     "team":"SD", "hand":"R","role":"SP","era":3.28,"whip":1.12,"k9":11.2,"hr9":0.82,"barrel_pct":7.8, "ev_allowed":88.8,"iso_allowed":.172,"hr_risk_rhb":0.72,"hr_risk_lhb":0.92,"k_pct":26.8,"bb_pct":11.2,"swstr":16.8,"gb_pct":38.9,"velo_season":96.1,"velo_recent":95.8},
    {"name":"Blake Snell",     "team":"SF", "hand":"L","role":"SP","era":3.45,"whip":1.18,"k9":11.9,"hr9":0.88,"barrel_pct":7.2, "ev_allowed":88.2,"iso_allowed":.168,"hr_risk_rhb":0.98,"hr_risk_lhb":0.78,"k_pct":27.1,"bb_pct":12.8,"swstr":17.8,"gb_pct":34.1,"velo_season":93.8,"velo_recent":91.4},
    {"name":"Gerrit Cole",     "team":"NYY","hand":"R","role":"SP","era":2.95,"whip":1.02,"k9":11.4,"hr9":0.92,"barrel_pct":6.8, "ev_allowed":88.1,"iso_allowed":.162,"hr_risk_rhb":0.82,"hr_risk_lhb":1.02,"k_pct":27.8,"bb_pct":7.8, "swstr":16.4,"gb_pct":38.4,"velo_season":96.8,"velo_recent":96.9},
    {"name":"Spencer Strider", "team":"ATL","hand":"R","role":"SP","era":2.81,"whip":0.99,"k9":13.2,"hr9":0.85,"barrel_pct":6.2, "ev_allowed":87.8,"iso_allowed":.158,"hr_risk_rhb":0.75,"hr_risk_lhb":0.95,"k_pct":31.2,"bb_pct":8.4, "swstr":19.8,"gb_pct":35.2,"velo_season":98.2,"velo_recent":97.8},
    {"name":"Zack Wheeler",    "team":"PHI","hand":"R","role":"SP","era":2.58,"whip":0.98,"k9":10.8,"hr9":0.71,"barrel_pct":5.8, "ev_allowed":87.1,"iso_allowed":.148,"hr_risk_rhb":0.62,"hr_risk_lhb":0.81,"k_pct":26.4,"bb_pct":6.8, "swstr":15.8,"gb_pct":42.1,"velo_season":97.4,"velo_recent":97.2},
    {"name":"Corbin Burnes",   "team":"BAL","hand":"R","role":"SP","era":2.78,"whip":0.97,"k9":10.2,"hr9":0.68,"barrel_pct":5.2, "ev_allowed":86.8,"iso_allowed":.138,"hr_risk_rhb":0.58,"hr_risk_lhb":0.78,"k_pct":25.8,"bb_pct":6.2, "swstr":14.8,"gb_pct":48.2,"velo_season":94.8,"velo_recent":95.1},
    {"name":"Logan Webb",      "team":"SF", "hand":"R","role":"SP","era":3.08,"whip":1.05,"k9":8.4, "hr9":0.58,"barrel_pct":4.8, "ev_allowed":86.2,"iso_allowed":.128,"hr_risk_rhb":0.48,"hr_risk_lhb":0.68,"k_pct":20.8,"bb_pct":7.2, "swstr":12.4,"gb_pct":54.8,"velo_season":91.2,"velo_recent":91.4},
    {"name":"Framber Valdez",  "team":"HOU","hand":"L","role":"SP","era":3.02,"whip":1.11,"k9":8.8, "hr9":0.51,"barrel_pct":4.4, "ev_allowed":85.8,"iso_allowed":.118,"hr_risk_rhb":0.61,"hr_risk_lhb":0.41,"k_pct":21.8,"bb_pct":9.2, "swstr":13.2,"gb_pct":58.4,"velo_season":93.1,"velo_recent":92.8},
    {"name":"Josh Hader",      "team":"HOU","hand":"L","role":"RP","era":1.88,"whip":0.88,"k9":14.8,"hr9":0.42,"barrel_pct":5.8, "ev_allowed":87.8,"iso_allowed":.128,"hr_risk_rhb":0.52,"hr_risk_lhb":0.32,"k_pct":34.8,"bb_pct":9.8, "swstr":24.8,"gb_pct":28.4,"velo_season":95.8,"velo_recent":95.4},
    {"name":"Emmanuel Clase",  "team":"CLE","hand":"R","role":"RP","era":1.42,"whip":0.78,"k9":9.8, "hr9":0.18,"barrel_pct":3.2, "ev_allowed":85.1,"iso_allowed":.098,"hr_risk_rhb":0.08,"hr_risk_lhb":0.28,"k_pct":24.2,"bb_pct":4.8, "swstr":18.2,"gb_pct":62.1,"velo_season":100.2,"velo_recent":100.4},
    {"name":"Devin Williams",  "team":"MIL","hand":"R","role":"RP","era":1.98,"whip":0.92,"k9":15.2,"hr9":0.28,"barrel_pct":4.2, "ev_allowed":86.2,"iso_allowed":.108,"hr_risk_rhb":0.18,"hr_risk_lhb":0.38,"k_pct":36.8,"bb_pct":10.2,"swstr":26.4,"gb_pct":41.2,"velo_season":87.4,"velo_recent":87.8},
]

DAY_NIGHT = [
    {"name":"Aaron Judge",       "team":"NYY","day_avg":.298,"day_slg":.589,"day_hr":6, "day_barrel":18.2,"night_avg":.321,"night_slg":.645,"night_hr":12,"night_barrel":22.1,"pref":"Night","gap":"Large"},
    {"name":"Shohei Ohtani",     "team":"LAD","day_avg":.312,"day_slg":.601,"day_hr":7, "day_barrel":20.1,"night_avg":.288,"night_slg":.571,"night_hr":9, "night_barrel":17.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Yordan Alvarez",    "team":"HOU","day_avg":.288,"day_slg":.551,"day_hr":5, "day_barrel":17.2,"night_avg":.311,"night_slg":.598,"night_hr":10,"night_barrel":20.8,"pref":"Night","gap":"Moderate"},
    {"name":"Bryce Harper",      "team":"PHI","day_avg":.278,"day_slg":.508,"day_hr":4, "day_barrel":14.8,"night_avg":.308,"night_slg":.562,"night_hr":10,"night_barrel":18.1,"pref":"Night","gap":"Large"},
    {"name":"Freddie Freeman",   "team":"LAD","day_avg":.321,"day_slg":.548,"day_hr":4, "day_barrel":15.1,"night_avg":.298,"night_slg":.501,"night_hr":7, "night_barrel":13.4,"pref":"Day",  "gap":"Moderate"},
    {"name":"Juan Soto",         "team":"NYM","day_avg":.301,"day_slg":.528,"day_hr":5, "day_barrel":16.2,"night_avg":.281,"night_slg":.501,"night_hr":8, "night_barrel":15.4,"pref":"Day",  "gap":"Small"},
    {"name":"Bobby Witt Jr",     "team":"KC", "day_avg":.318,"day_slg":.551,"day_hr":6, "day_barrel":16.1,"night_avg":.291,"night_slg":.498,"night_hr":7, "night_barrel":13.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Kyle Tucker",       "team":"HOU","day_avg":.288,"day_slg":.508,"day_hr":5, "day_barrel":14.8,"night_avg":.264,"night_slg":.471,"night_hr":8, "night_barrel":13.4,"pref":"Day",  "gap":"Moderate"},
    {"name":"Matt Olson",        "team":"ATL","day_avg":.241,"day_slg":.468,"day_hr":5, "day_barrel":15.8,"night_avg":.268,"night_slg":.521,"night_hr":10,"night_barrel":19.1,"pref":"Night","gap":"Moderate"},
    {"name":"Rafael Devers",     "team":"BOS","day_avg":.261,"day_slg":.481,"day_hr":4, "day_barrel":13.9,"night_avg":.289,"night_slg":.531,"night_hr":10,"night_barrel":17.2,"pref":"Night","gap":"Moderate"},
    {"name":"Gunnar Henderson",  "team":"BAL","day_avg":.261,"day_slg":.478,"day_hr":4, "day_barrel":13.8,"night_avg":.289,"night_slg":.531,"night_hr":10,"night_barrel":16.2,"pref":"Night","gap":"Moderate"},
    {"name":"Mookie Betts",      "team":"LAD","day_avg":.308,"day_slg":.521,"day_hr":5, "day_barrel":14.8,"night_avg":.278,"night_slg":.478,"night_hr":7, "night_barrel":13.1,"pref":"Day",  "gap":"Small"},
    {"name":"Ronald Acuna Jr",   "team":"ATL","day_avg":.278,"day_slg":.481,"day_hr":3, "day_barrel":11.8,"night_avg":.305,"night_slg":.531,"night_hr":8, "night_barrel":14.1,"pref":"Night","gap":"Moderate"},
    {"name":"Fernando Tatis Jr", "team":"SD", "day_avg":.258,"day_slg":.458,"day_hr":4, "day_barrel":12.8,"night_avg":.279,"night_slg":.511,"night_hr":9, "night_barrel":15.1,"pref":"Night","gap":"Moderate"},
    {"name":"Adolis Garcia",     "team":"TEX","day_avg":.248,"day_slg":.438,"day_hr":3, "day_barrel":11.2,"night_avg":.268,"night_slg":.488,"night_hr":9, "night_barrel":14.4,"pref":"Night","gap":"Moderate"},
    {"name":"Julio Rodriguez",   "team":"SEA","day_avg":.281,"day_slg":.491,"day_hr":5, "day_barrel":13.8,"night_avg":.261,"night_slg":.448,"night_hr":6, "night_barrel":12.1,"pref":"Day",  "gap":"Small"},
    {"name":"Pete Alonso",       "team":"NYM","day_avg":.271,"day_slg":.521,"day_hr":6, "day_barrel":16.2,"night_avg":.248,"night_slg":.481,"night_hr":8, "night_barrel":13.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Jose Ramirez",      "team":"CLE","day_avg":.298,"day_slg":.528,"day_hr":5, "day_barrel":14.8,"night_avg":.271,"night_slg":.481,"night_hr":7, "night_barrel":12.9,"pref":"Day",  "gap":"Small"},
    {"name":"Bo Bichette",       "team":"TOR","day_avg":.298,"day_slg":.491,"day_hr":5, "day_barrel":13.1,"night_avg":.274,"night_slg":.448,"night_hr":5, "night_barrel":10.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Trea Turner",       "team":"PHI","day_avg":.294,"day_slg":.478,"day_hr":4, "day_barrel":11.8,"night_avg":.271,"night_slg":.431,"night_hr":5, "night_barrel":9.1, "pref":"Day",  "gap":"Small"},
    {"name":"William Contreras", "team":"MIL","day_avg":.291,"day_slg":.501,"day_hr":4, "day_barrel":13.2,"night_avg":.261,"night_slg":.461,"night_hr":6, "night_barrel":11.4,"pref":"Day",  "gap":"Moderate"},
    {"name":"Elly De La Cruz",   "team":"CIN","day_avg":.241,"day_slg":.441,"day_hr":4, "day_barrel":11.8,"night_avg":.268,"night_slg":.488,"night_hr":8, "night_barrel":14.1,"pref":"Night","gap":"Moderate"},
    {"name":"Teoscar Hernandez", "team":"LAD","day_avg":.278,"day_slg":.498,"day_hr":4, "day_barrel":13.8,"night_avg":.251,"night_slg":.451,"night_hr":7, "night_barrel":11.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Corey Seager",      "team":"TEX","day_avg":.291,"day_slg":.528,"day_hr":5, "day_barrel":15.1,"night_avg":.268,"night_slg":.498,"night_hr":7, "night_barrel":13.2,"pref":"Day",  "gap":"Moderate"},
    {"name":"Marcell Ozuna",     "team":"ATL","day_avg":.248,"day_slg":.461,"day_hr":4, "day_barrel":11.8,"night_avg":.281,"night_slg":.521,"night_hr":8, "night_barrel":14.4,"pref":"Night","gap":"Moderate"},
    {"name":"Willy Adames",      "team":"SF", "day_avg":.268,"day_slg":.478,"day_hr":4, "day_barrel":12.8,"night_avg":.241,"night_slg":.448,"night_hr":7, "night_barrel":11.1,"pref":"Day",  "gap":"Moderate"},
    {"name":"Cal Raleigh",       "team":"SEA","day_avg":.258,"day_slg":.501,"day_hr":5, "day_barrel":14.8,"night_avg":.231,"night_slg":.478,"night_hr":8, "night_barrel":13.1,"pref":"Day",  "gap":"Moderate"},
    {"name":"Nolan Arenado",     "team":"STL","day_avg":.271,"day_slg":.488,"day_hr":4, "day_barrel":13.1,"night_avg":.258,"night_slg":.468,"night_hr":7, "night_barrel":12.4,"pref":"Day",  "gap":"Small"},
    {"name":"Paul Goldschmidt",  "team":"STL","day_avg":.288,"day_slg":.511,"day_hr":4, "day_barrel":14.2,"night_avg":.268,"night_slg":.481,"night_hr":7, "night_barrel":12.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Cody Bellinger",    "team":"CHC","day_avg":.291,"day_slg":.491,"day_hr":3, "day_barrel":12.1,"night_avg":.268,"night_slg":.461,"night_hr":6, "night_barrel":11.1,"pref":"Day",  "gap":"Small"},
    {"name":"Marcus Semien",     "team":"TEX","day_avg":.278,"day_slg":.468,"day_hr":3, "day_barrel":11.4,"night_avg":.258,"night_slg":.441,"night_hr":6, "night_barrel":10.2,"pref":"Day",  "gap":"Small"},
    {"name":"Xander Bogaerts",   "team":"SD", "day_avg":.268,"day_slg":.458,"day_hr":3, "day_barrel":10.8,"night_avg":.251,"night_slg":.431,"night_hr":5, "night_barrel":9.8, "pref":"Day",  "gap":"Small"},
    {"name":"Anthony Rizzo",     "team":"NYY","day_avg":.258,"day_slg":.471,"day_hr":3, "day_barrel":11.8,"night_avg":.241,"night_slg":.448,"night_hr":7, "night_barrel":12.8,"pref":"Night","gap":"Moderate"},
    {"name":"Nathaniel Lowe",    "team":"TEX","day_avg":.291,"day_slg":.478,"day_hr":3, "day_barrel":10.8,"night_avg":.271,"night_slg":.451,"night_hr":4, "night_barrel":9.4, "pref":"Day",  "gap":"Small"},
    {"name":"Cedric Mullins",    "team":"BAL","day_avg":.271,"day_slg":.421,"day_hr":2, "day_barrel":8.8, "night_avg":.251,"night_slg":.398,"night_hr":3, "night_barrel":8.1, "pref":"Day",  "gap":"Small"},
    {"name":"Tyler Stephenson",  "team":"CIN","day_avg":.278,"day_slg":.491,"day_hr":3, "day_barrel":12.4,"night_avg":.261,"night_slg":.468,"night_hr":5, "night_barrel":11.8,"pref":"Day",  "gap":"Small"},
    {"name":"Sal Stewart",       "team":"CIN","day_avg":.251,"day_slg":.461,"day_hr":3, "day_barrel":11.4,"night_avg":.238,"night_slg":.448,"night_hr":6, "night_barrel":11.1,"pref":"Night","gap":"Small"},
    {"name":"Corbin Carroll",    "team":"ARI","day_avg":.281,"day_slg":.468,"day_hr":4, "day_barrel":11.1,"night_avg":.258,"night_slg":.421,"night_hr":4, "night_barrel":8.8, "pref":"Day",  "gap":"Moderate"},
]

# Build a lookup dict for fast access
DN_LOOKUP = {p['name'].lower(): p for p in DAY_NIGHT}


def get_daynight_for_batters(batters):
    """
    Match live batters to day/night split data.
    Returns only batters we have split data for.
    """
    result = []
    for b in batters:
        name_lower = b.get('name','').lower()
        dn = DN_LOOKUP.get(name_lower)
        if dn:
            merged = dict(dn)
            # Update team from live data if available
            if b.get('team'):
                merged['team'] = b['team']
            result.append(merged)
    return result


def batter_score(r):
    s = 0
    bp  = r.get('barrel_pct')
    ev  = r.get('avg_hit_speed')
    iso = r.get('iso')
    slg = r.get('slg')
    if bp  is not None: s += min(bp / 20 * 35, 35)
    if ev  is not None: s += min((ev - 80) / 12 * 25, 25)
    if iso is not None: s += min(iso / .300 * 20, 20)
    if slg is not None: s += min(slg / .500 * 20, 20)
    return round(min(s, 100), 1)


def batter_score_with_form(r):
    """Batter score including recent form bonus"""
    base = batter_score(r)
    _, _, _, form_adj = form_trend(r)
    return round(min(max(base + form_adj, 0), 100), 1)


def velo_drop(r):
    """Returns mph drop (positive = dropped, negative = gained)"""
    vs = r.get('velo_season')
    vr = r.get('velo_recent')
    if vs is None or vr is None:
        return None
    return round(vs - vr, 1)


def velo_status(r):
    """Returns label, color, and signal strength for velocity change"""
    drop = velo_drop(r)
    if drop is None:
        return "N/A", "#94a3b8", 0
    if drop >= 2.0:
        return f"&#128308; Down {drop:.1f} mph (Significant)", "#f87171", 2
    elif drop >= 1.0:
        return f"&#128308; Down {drop:.1f} mph", "#fb923c", 1
    elif drop <= -1.0:
        return f"&#128994; Up {abs(drop):.1f} mph", "#4ade80", -1
    else:
        return f"&#128309; Holding ({drop:+.1f} mph)", "#facc15", 0


def form_trend(r):
    """
    Returns trend label, emoji, color, and score adjustment
    based on last 14 days vs season average
    """
    avg_s  = r.get('avg')
    avg_r  = r.get('avg_14d')
    slg_s  = r.get('slg')
    slg_r  = r.get('slg_14d')
    bp_s   = r.get('barrel_pct')
    bp_r   = r.get('barrel_14d')
    hr_r   = r.get('hr_14d', 0)

    if avg_s is None or avg_r is None:
        return "Neutral", "&#10145;", "#94a3b8", 0

    score = 0
    # AVG delta
    avg_delta = avg_r - avg_s
    if avg_delta >= .040:   score += 2
    elif avg_delta >= .020: score += 1
    elif avg_delta <= -.040: score -= 2
    elif avg_delta <= -.020: score -= 1

    # SLG delta
    if slg_s and slg_r:
        slg_delta = slg_r - slg_s
        if slg_delta >= .060:   score += 2
        elif slg_delta >= .030: score += 1
        elif slg_delta <= -.060: score -= 2
        elif slg_delta <= -.030: score -= 1

    # Barrel% delta
    if bp_s and bp_r:
        bp_delta = bp_r - bp_s
        if bp_delta >= 4:   score += 2
        elif bp_delta >= 2: score += 1
        elif bp_delta <= -4: score -= 2
        elif bp_delta <= -2: score -= 1

    # Recent HRs bonus
    if hr_r and hr_r >= 4: score += 1

    if score >= 4:
        return "On Fire",    "&#128293;", "#4ade80",  15
    elif score >= 2:
        return "Hot",        "&#128293;", "#86efac",  8
    elif score == 1:
        return "Warm",       "&#127777;", "#facc15",  4
    elif score == 0:
        return "Neutral",    "&#10145;",  "#94a3b8",  0
    elif score == -1:
        return "Cool",       "&#10052;",  "#93c5fd",  -4
    elif score <= -2:
        return "Cold",       "&#10052;",  "#60a5fa",  -8
    else:
        return "Ice Cold",   "&#10052;",  "#f87171",  -15


def pitcher_vuln(r):
    s = 0
    hr9 = r.get('hr9')
    bp  = r.get('barrel_pct')
    iso = r.get('iso_allowed')
    ev  = r.get('ev_allowed')
    if hr9 is not None: s += min(hr9 / 1.8 * 35, 35)
    if bp  is not None: s += min(bp / 10 * 30, 30)
    if iso is not None: s += min(iso / .200 * 20, 20)
    if ev  is not None: s += min((ev - 85) / 7 * 15, 15)
    # Velocity drop adds to vulnerability
    drop = velo_drop(r)
    if drop is not None and drop >= 1.0:
        s += min(drop * 5, 10)
    return round(min(s, 100), 1)


def matchup_score(batter, pitcher):
    signals = []
    score   = 50
    b_hand  = batter.get('hand', 'R')
    p_hand  = pitcher.get('hand', 'R')

    if (b_hand in ('L','S') and p_hand == 'R') or (b_hand in ('R','S') and p_hand == 'L'):
        signals.append({"label": "Platoon Advantage", "good": True}); score += 10
    else:
        signals.append({"label": "Same-Hand Matchup", "good": False}); score -= 5

    hr_risk = pitcher.get('hr_risk_rhb') if b_hand in ('R','S') else pitcher.get('hr_risk_lhb')
    side    = "RHB" if b_hand in ('R','S') else "LHB"
    if hr_risk is not None:
        lbl = f"HR Risk vs {side}: {hr_risk:.2f}"
        if hr_risk >= 1.8:   signals.append({"label": lbl+" (Ideal)",     "good": True});  score += 20
        elif hr_risk >= 1.5: signals.append({"label": lbl+" (Favorable)", "good": True});  score += 12
        elif hr_risk >= 1.0: signals.append({"label": lbl+" (Average)",   "good": None})
        else:                signals.append({"label": lbl+" (Avoid)",     "good": False}); score -= 15

    b_bp = batter.get('barrel_pct'); p_bp = pitcher.get('barrel_pct')
    if b_bp is not None and p_bp is not None:
        if b_bp >= 15 and p_bp >= 8:
            signals.append({"label": f"Elite Barrel matchup ({b_bp:.1f}% vs {p_bp:.1f}% allowed)", "good": True}); score += 15
        elif b_bp >= 12 and p_bp >= 6:
            signals.append({"label": f"Favorable Barrel ({b_bp:.1f}% vs {p_bp:.1f}%)", "good": True}); score += 8
        elif b_bp < 8:
            signals.append({"label": f"Low Barrel% ({b_bp:.1f}%)", "good": False}); score -= 10

    b_ev = batter.get('avg_hit_speed'); p_ev = pitcher.get('ev_allowed')
    if b_ev is not None and p_ev is not None:
        if b_ev >= 91 and p_ev >= 90:
            signals.append({"label": f"Hard Contact ({b_ev:.1f} vs {p_ev:.1f} allowed)", "good": True}); score += 10
        elif b_ev < 87:
            signals.append({"label": f"Soft contact ({b_ev:.1f} mph)", "good": False}); score -= 8

    b_iso = batter.get('iso'); p_iso = pitcher.get('iso_allowed')
    if b_iso is not None and p_iso is not None:
        if b_iso >= 0.250 and p_iso >= 0.180:
            signals.append({"label": f"Power vs vulnerable (ISO {b_iso:.3f} vs {p_iso:.3f})", "good": True}); score += 12
        elif b_iso < 0.150:
            signals.append({"label": f"Low power (ISO {b_iso:.3f})", "good": False}); score -= 8

    # Batted-ball profile: how hard, in the air, and pulled
    b_hh = batter.get('hard_hit_pct')
    if b_hh is not None:
        if b_hh >= 50:   signals.append({"label": f"Elite hard contact ({b_hh:.0f}% hard-hit)", "good": True}); score += 5
        elif b_hh >= 42: signals.append({"label": f"Hard-hit {b_hh:.0f}%", "good": True}); score += 2

    b_fb = batter.get('fb_pct')
    if b_fb is not None:
        if b_fb >= 40:   signals.append({"label": f"Gets it airborne (FB {b_fb:.0f}%)", "good": True}); score += 5
        elif b_fb <= 27: signals.append({"label": f"Ground-ball lean (FB {b_fb:.0f}%)", "good": False}); score -= 7

    b_pull = batter.get('pull_pct')
    if b_pull is not None and b_pull >= 46:
        signals.append({"label": f"Pull-side power ({b_pull:.0f}% pull)", "good": True}); score += 4

    b_mev = batter.get('max_ev')
    if b_mev is not None and b_mev >= 116:
        signals.append({"label": f"Huge EV ceiling ({b_mev:.0f} mph)", "good": True}); score += 3

    b_la = batter.get('launch_angle')
    if b_la is not None:
        if 12 <= b_la <= 24:
            signals.append({"label": f"HR-optimal launch angle ({b_la:.0f}\u00b0)", "good": True}); score += 4
        elif b_la < 6:
            signals.append({"label": f"Low launch angle ({b_la:.0f}\u00b0)", "good": False}); score -= 5

    b_xslg = batter.get('xslg')
    if b_xslg is not None:
        if b_xslg >= 0.500:
            signals.append({"label": f"Elite xSLG ({b_xslg:.3f})", "good": True}); score += 5
        elif b_xslg >= 0.430:
            signals.append({"label": f"Strong xSLG ({b_xslg:.3f})", "good": True}); score += 2

    b_k = batter.get('k_pct'); p_k = pitcher.get('k_pct')
    if b_k is not None and p_k is not None:
        if b_k > 25 and p_k > 28:
            signals.append({"label": f"High K risk ({b_k:.1f}% vs {p_k:.1f}% K pitcher)", "good": False}); score -= 12
        elif b_k < 17 and p_k > 25:
            signals.append({"label": f"Good discipline vs K pitcher", "good": True}); score += 8

    p_sw = pitcher.get('swstr')
    if p_sw is not None:
        if p_sw >= 18:   signals.append({"label": f"Elite whiff pitcher (SwStr {p_sw:.1f}%)", "good": False}); score -= 8
        elif p_sw <= 11: signals.append({"label": f"Hittable pitcher (SwStr {p_sw:.1f}%)",   "good": True});  score += 6

    # Near HR count signal
    near_hr = batter.get('near_hr')
    if near_hr is not None:
        if near_hr >= 8:
            signals.append({"label": f"Due — {near_hr} near HRs (390ft+ outs)", "good": True}); score += 12
        elif near_hr >= 5:
            signals.append({"label": f"{near_hr} near HRs this season", "good": True}); score += 6
        elif near_hr >= 3:
            signals.append({"label": f"{near_hr} near HRs this season", "good": None})

    # Velocity tracker signal
    drop = velo_drop(pitcher)
    if drop is not None:
        if drop >= 2.0:
            signals.append({"label": f"Velo drop {drop:.1f} mph below season avg &#128308;", "good": True}); score += 12
        elif drop >= 1.0:
            signals.append({"label": f"Velo down {drop:.1f} mph vs season avg", "good": True}); score += 6
        elif drop <= -1.0:
            signals.append({"label": f"Velo up {abs(drop):.1f} mph — sharp &#128994;", "good": False}); score -= 4

    # Recent form signal
    trend_label, trend_emoji, trend_col, form_adj = form_trend(batter)
    if form_adj >= 8:
        signals.append({"label": f"{trend_emoji} {trend_label} last 14 days (AVG {batter.get('avg_14d', 0):.3f})", "good": True})
        score += form_adj
    elif form_adj >= 4:
        signals.append({"label": f"{trend_emoji} {trend_label} last 14 days", "good": True})
        score += form_adj
    elif form_adj <= -8:
        signals.append({"label": f"{trend_emoji} {trend_label} last 14 days (AVG {batter.get('avg_14d', 0):.3f})", "good": False})
        score += form_adj
    elif form_adj <= -4:
        signals.append({"label": f"{trend_emoji} {trend_label} last 14 days", "good": False})
        score += form_adj

    return max(0, min(100, round(score))), signals


def get_batter_stats(name):
    """Look up a player's stats from our sample data by name"""
    name_lower = name.lower()
    for b in SAMPLE_BATTERS:
        if b['name'].lower() == name_lower:
            return b
        # Partial match — last name
        last = b['name'].split()[-1].lower()
        if last == name_lower.split()[-1]:
            return b
    return None


def get_batter_vs_pitcher(batter_id, pitcher_id):
    """Career batter-vs-pitcher line (PA/AB/H/HR/AVG/SLG). Cached per pair.
    Bounded in practice to the day's ~15 top picks. Returns dict or None."""
    if not batter_id or not pitcher_id:
        return None
    try:
        batter_id = int(batter_id); pitcher_id = int(pitcher_id)
    except (TypeError, ValueError):
        return None
    key = (batter_id, pitcher_id)
    if key in _h2h_cache:
        return _h2h_cache[key]

    result = None
    try:
        url = (f"https://statsapi.mlb.com/api/v1/people/{batter_id}/stats"
               f"?stats=vsPlayerTotal&group=hitting&opposingPlayerId={pitcher_id}")
        data = requests.get(url, timeout=8).json()
        for blk in (data.get("stats") or []):
            for sp in blk.get("splits", []):
                st = sp.get("stat") or {}
                ab = _to_int(st.get("atBats"))
                pa = _to_int(st.get("plateAppearances")) or ab
                if pa and pa > 0:
                    result = {
                        "pa":  pa,
                        "ab":  ab,
                        "h":   _to_int(st.get("hits")),
                        "hr":  _to_int(st.get("homeRuns")),
                        "rbi": _to_int(st.get("rbi")),
                        "avg": st.get("avg"),
                        "slg": st.get("slg"),
                        "ops": st.get("ops"),
                    }
                    break
            if result:
                break
    except Exception:
        result = None

    _h2h_cache[key] = result
    return result


def get_recent_form(batter_id, season=None, window=15):
    """Recent-form line from the game log: last-N HR, recent SLG/ISO, and how many
    games since the hitter last homered. Cached per (id, season, window)."""
    if not batter_id:
        return None
    try:
        batter_id = int(batter_id)
    except (TypeError, ValueError):
        return None
    yr = season or _et_now().year
    key = (batter_id, yr, window)
    if key in _form_cache:
        return _form_cache[key]

    result = None
    try:
        url = (f"https://statsapi.mlb.com/api/v1/people/{batter_id}/stats"
               f"?stats=gameLog&group=hitting&season={yr}")
        data = requests.get(url, timeout=8).json()
        stats = data.get("stats") or []
        splits = stats[0].get("splits", []) if stats else []
        splits = sorted([s for s in splits if s.get("stat")], key=lambda s: s.get("date", ""))
        if splits:
            recent = splits[-window:]
            ab = h = tb = hr = pa = 0
            for s in recent:
                st = s["stat"]
                ab += _to_int(st.get("atBats"))
                h  += _to_int(st.get("hits"))
                tb += _to_int(st.get("totalBases"))
                hr += _to_int(st.get("homeRuns"))
                pa += _to_int(st.get("plateAppearances"))
            # Games since the last HR (scan the full season from newest game backward).
            last_hr_ago = None
            for i, s in enumerate(reversed(splits)):
                if _to_int(s["stat"].get("homeRuns")) >= 1:
                    last_hr_ago = i
                    break
            result = {
                "games": len(recent), "hr": hr, "ab": ab, "pa": pa,
                "slg": round(tb / ab, 3) if ab else None,
                "avg": round(h / ab, 3) if ab else None,
                "iso": round((tb - h) / ab, 3) if ab else None,
                "last_hr_ago": last_hr_ago,   # 0 = homered most recent game; None = none this season
            }
    except Exception:
        result = None

    _form_cache[key] = result
    return result


def get_pitcher_recent_form(pitcher_id, season=None, window=4):
    """Pitcher's recent HR vulnerability from the game log: HR allowed, innings, and
    HR/9 over the last N starts, plus starts since the pitcher last gave up a homer.
    A starter serving up homers lately is a tailwind for our HR picks. Cached per
    (id, season, window); only the day's ~30 starters get looked up. Note: the game
    log carries no pitch velocity (that's Statcast), so this is HR/9-based."""
    if not pitcher_id:
        return None
    try:
        pitcher_id = int(pitcher_id)
    except (TypeError, ValueError):
        return None
    yr = season or _et_now().year
    key = (pitcher_id, yr, window)
    if key in _pform_cache:
        return _pform_cache[key]

    result = None
    try:
        url = (f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
               f"?stats=gameLog&group=pitching&season={yr}")
        data = requests.get(url, timeout=8).json()
        stats = data.get("stats") or []
        splits = stats[0].get("splits", []) if stats else []
        splits = sorted([s for s in splits if s.get("stat")], key=lambda s: s.get("date", ""))
        # Prefer actual starts; fall back to all appearances if none are flagged.
        starts = [s for s in splits if _to_int(s["stat"].get("gamesStarted")) >= 1]
        used = starts if starts else splits
        if used:
            recent = used[-window:]
            hr = er = 0
            ip = 0.0
            for s in recent:
                st = s["stat"]
                hr += _to_int(st.get("homeRuns"))
                er += _to_int(st.get("earnedRuns"))
                ip += (_parse_ip(st.get("inningsPitched")) or 0.0)
            # Starts since the pitcher last allowed a HR (0 = gave one up last time out).
            last_hr_ago = None
            for i, s in enumerate(reversed(used)):
                if _to_int(s["stat"].get("homeRuns")) >= 1:
                    last_hr_ago = i
                    break
            result = {
                "starts": len(recent), "hr": hr,
                "ip": round(ip, 1),
                "hr9": round(hr / ip * 9, 2) if ip else None,
                "era": round(er / ip * 9, 2) if ip else None,
                "last_hr_ago": last_hr_ago,  # 0 = HR allowed last start; None = none this season
            }
    except Exception:
        result = None

    _pform_cache[key] = result
    return result


def get_pitcher_hand_splits(pid, season=None):
    """Real HR-risk by batter hand from the pitcher's vs-L / vs-R splits.
    Returns {hr_risk_rhb, hr_risk_lhb} (either may be None on small samples),
    or None if unavailable. Cached per pid for the process. Bounded in practice
    to the day's ~30 starters since only they are looked up with a pid."""
    if pid is None:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid in _pitcher_splits_cache:
        return _pitcher_splits_cache[pid]

    LEAGUE_HR_PER_PA = 0.030   # ~league-average HR rate; risk is expressed relative to it
    result = None
    yr = season or _et_now().year
    for y in (yr, yr - 1):
        try:
            url = (f"https://statsapi.mlb.com/api/v1/people/{pid}/stats"
                   f"?stats=statSplits&group=pitching&season={y}&sitCodes=vr,vl")
            data = requests.get(url, timeout=8).json()
            stats = data.get("stats") or []
            splits = stats[0].get("splits", []) if stats else []
            by = {}
            for s in splits:
                code = (s.get("split") or {}).get("code")
                st   = s.get("stat") or {}
                hr   = _to_int(st.get("homeRuns"))
                pa   = _to_int(st.get("plateAppearances")) or _to_int(st.get("battersFaced"))
                if code in ("vr", "vl") and pa and pa >= 25:
                    by[code] = round(max(0.3, min(3.0, (hr / pa) / LEAGUE_HR_PER_PA)), 2)
            if by:
                result = {"hr_risk_rhb": by.get("vr"), "hr_risk_lhb": by.get("vl")}
                break
        except Exception:
            continue

    _pitcher_splits_cache[pid] = result
    return result


def get_pitcher_stats(name, pid=None):
    """Real live stats for a pitcher, matched by MLBAM id first, then name.
    Falls back to the static sample list, then None."""
    def _finish(rec, pid_for_splits=None):
        result = dict(rec)
        vl, vc, _ = velo_status(result)
        result['velo_label']    = vl
        result['velo_col']      = vc
        result['velo_drop_val'] = velo_drop(result)
        # Replace derived platoon HR-risk with real vs-L / vs-R splits when we
        # have the pitcher's id (the day's starters).
        if pid_for_splits is not None:
            sp = get_pitcher_hand_splits(pid_for_splits)
            if sp:
                if sp.get("hr_risk_rhb") is not None: result["hr_risk_rhb"] = sp["hr_risk_rhb"]
                if sp.get("hr_risk_lhb") is not None: result["hr_risk_lhb"] = sp["hr_risk_lhb"]
                result["splits_real"] = True
        return result

    pool = get_live_pitcher_pool()
    if pid is not None:
        try:
            rec = pool["by_id"].get(int(pid))
        except (TypeError, ValueError):
            rec = None
        if rec:
            return _finish(rec, pid)
    if name:
        rec = pool["by_name"].get(_norm_name(name))
        if rec:
            return _finish(rec, pid)

    name_lower = name.lower()
    for p in SAMPLE_PITCHERS:
        if p['name'].lower() == name_lower:
            return _finish(p, pid)
        last = p['name'].split()[-1].lower()
        if last == name_lower.split()[-1]:
            return _finish(p, pid)
    return None


# MLB player ID lookup for headshots
# URL: https://img.mlbstatic.com/mlb-photos/image/upload/.../v1/people/{ID}/headshot/67/current
PLAYER_IDS = {
    "Aaron Judge":        592450,
    "Shohei Ohtani":      660271,
    "Yordan Alvarez":     670541,
    "Bryce Harper":       547180,
    "Freddie Freeman":    518692,
    "Juan Soto":          665742,
    "Gunnar Henderson":   683002,
    "Matt Olson":         621566,
    "Rafael Devers":      646240,
    "Kyle Tucker":        663656,
    "Bobby Witt Jr":      677951,
    "Jose Ramirez":       608070,
    "Mookie Betts":       605141,
    "Ronald Acuna Jr":    660670,
    "Fernando Tatis Jr":  665487,
    "Adolis Garcia":      666969,
    "Julio Rodriguez":    677594,
    "Corbin Carroll":     682998,
    "Bo Bichette":        666182,
    "Trea Turner":        607208,
    "Pete Alonso":        624413,
    "Elly De La Cruz":    682829,
    "William Contreras":  661388,
    "Marcell Ozuna":      542303,
    "Teoscar Hernandez":  606192,
    "Corey Seager":       608369,
    "Willy Adames":       642715,
    "Cal Raleigh":        663728,
    "Sal Stewart":        694973,
    "Tyler Stephenson":   663886,
    "Nolan Arenado":      571448,
    "Paul Goldschmidt":   502671,
    "Cody Bellinger":     641355,
    "Marcus Semien":      543760,
    "Anthony Rizzo":      519203,
    "Nathaniel Lowe":     663993,
    "Josh Bell":          605137,
    "Cedric Mullins":     656429,
}

def get_headshot_url(name, player_id=None):
    pid = player_id or PLAYER_IDS.get(name)
    if pid:
        return (
            "https://img.mlbstatic.com/mlb-photos/image/upload/"
            "d_people:generic:headshot:67:current.png/w_120,q_auto:best/"
            f"v1/people/{pid}/headshot/67/current"
        )
    return None

# Statcast data for top power hitters — overlaid on live roster data
STATCAST_OVERLAY = {
    "Aaron Judge":        {"barrel_pct":20.4,"avg_hit_speed":93.2,"iso":.310,"woba":.415},
    "Shohei Ohtani":      {"barrel_pct":18.9,"avg_hit_speed":92.8,"iso":.291,"woba":.399},
    "Yordan Alvarez":     {"barrel_pct":19.1,"avg_hit_speed":92.4,"iso":.277,"woba":.408},
    "Bryce Harper":       {"barrel_pct":16.7,"avg_hit_speed":91.2,"iso":.246,"woba":.395},
    "Matt Olson":         {"barrel_pct":17.8,"avg_hit_speed":91.9,"iso":.240,"woba":.368},
    "Freddie Freeman":    {"barrel_pct":14.2,"avg_hit_speed":90.1,"iso":.213,"woba":.389},
    "Juan Soto":          {"barrel_pct":15.8,"avg_hit_speed":90.8,"iso":.223,"woba":.392},
    "Gunnar Henderson":   {"barrel_pct":15.2,"avg_hit_speed":90.4,"iso":.231,"woba":.372},
    "Rafael Devers":      {"barrel_pct":15.9,"avg_hit_speed":91.1,"iso":.233,"woba":.368},
    "Kyle Tucker":        {"barrel_pct":14.1,"avg_hit_speed":90.2,"iso":.214,"woba":.365},
    "Bobby Witt Jr":      {"barrel_pct":14.8,"avg_hit_speed":90.8,"iso":.219,"woba":.375},
    "Jose Ramirez":       {"barrel_pct":13.8,"avg_hit_speed":89.9,"iso":.219,"woba":.372},
    "Mookie Betts":       {"barrel_pct":13.9,"avg_hit_speed":89.8,"iso":.207,"woba":.378},
    "Ronald Acuna Jr":    {"barrel_pct":13.1,"avg_hit_speed":89.4,"iso":.218,"woba":.375},
    "Fernando Tatis Jr":  {"barrel_pct":14.2,"avg_hit_speed":90.1,"iso":.218,"woba":.358},
    "Adolis Garcia":      {"barrel_pct":13.2,"avg_hit_speed":89.8,"iso":.207,"woba":.342},
    "Julio Rodriguez":    {"barrel_pct":12.8,"avg_hit_speed":89.1,"iso":.199,"woba":.348},
    "Corbin Carroll":     {"barrel_pct":9.8, "avg_hit_speed":87.2,"iso":.174,"woba":.345},
    "Bo Bichette":        {"barrel_pct":11.8,"avg_hit_speed":88.4,"iso":.183,"woba":.352},
    "Trea Turner":        {"barrel_pct":10.2,"avg_hit_speed":87.8,"iso":.170,"woba":.348},
    "Elly De La Cruz":    {"barrel_pct":13.1,"avg_hit_speed":90.2,"iso":.210,"woba":.342},
    "Pete Alonso":        {"barrel_pct":14.8,"avg_hit_speed":91.1,"iso":.244,"woba":.358},
    "Cal Raleigh":        {"barrel_pct":13.9,"avg_hit_speed":90.4,"iso":.248,"woba":.345},
    "William Contreras":  {"barrel_pct":12.1,"avg_hit_speed":89.4,"iso":.210,"woba":.348},
    "Marcell Ozuna":      {"barrel_pct":13.4,"avg_hit_speed":90.1,"iso":.230,"woba":.355},
    "Teoscar Hernandez":  {"barrel_pct":12.8,"avg_hit_speed":89.8,"iso":.210,"woba":.342},
    "Corey Seager":       {"barrel_pct":14.1,"avg_hit_speed":90.8,"iso":.233,"woba":.365},
    "Willy Adames":       {"barrel_pct":11.8,"avg_hit_speed":88.9,"iso":.211,"woba":.338},
    "Sal Stewart":        {"barrel_pct":11.2,"avg_hit_speed":88.8,"iso":.213,"woba":.338},
    "Tyler Stephenson":   {"barrel_pct":12.1,"avg_hit_speed":89.2,"iso":.225,"woba":.355},
    "Jose Abreu":         {"barrel_pct":11.8,"avg_hit_speed":89.4,"iso":.198,"woba":.332},
    "Marcus Semien":      {"barrel_pct":10.8,"avg_hit_speed":88.4,"iso":.192,"woba":.341},
    "Nolan Arenado":      {"barrel_pct":12.4,"avg_hit_speed":89.8,"iso":.215,"woba":.348},
    "Paul Goldschmidt":   {"barrel_pct":13.1,"avg_hit_speed":90.2,"iso":.221,"woba":.358},
    "Xander Bogaerts":    {"barrel_pct":10.2,"avg_hit_speed":87.8,"iso":.188,"woba":.338},
    "Cody Bellinger":     {"barrel_pct":11.4,"avg_hit_speed":88.8,"iso":.196,"woba":.345},
    "Josh Bell":          {"barrel_pct":10.8,"avg_hit_speed":88.4,"iso":.189,"woba":.338},
    "Anthony Rizzo":      {"barrel_pct":11.2,"avg_hit_speed":88.9,"iso":.201,"woba":.341},
    "Nathaniel Lowe":     {"barrel_pct":9.8, "avg_hit_speed":87.8,"iso":.178,"woba":.335},
    "Cedric Mullins":     {"barrel_pct":8.4, "avg_hit_speed":86.8,"iso":.158,"woba":.318},
}

# Recent form data for live batters (14-day splits)
FORM_OVERLAY = {
    "Aaron Judge":       {"avg_14d":.358,"slg_14d":.712,"hr_14d":5,"barrel_14d":24.1},
    "Shohei Ohtani":     {"avg_14d":.241,"slg_14d":.421,"hr_14d":1,"barrel_14d":12.2},
    "Yordan Alvarez":    {"avg_14d":.328,"slg_14d":.621,"hr_14d":4,"barrel_14d":22.8},
    "Bryce Harper":      {"avg_14d":.218,"slg_14d":.381,"hr_14d":1,"barrel_14d":9.8},
    "Juan Soto":         {"avg_14d":.341,"slg_14d":.598,"hr_14d":4,"barrel_14d":19.2},
    "Matt Olson":        {"avg_14d":.289,"slg_14d":.558,"hr_14d":4,"barrel_14d":21.4},
    "Bobby Witt Jr":     {"avg_14d":.348,"slg_14d":.598,"hr_14d":4,"barrel_14d":18.9},
    "Pete Alonso":       {"avg_14d":.289,"slg_14d":.558,"hr_14d":4,"barrel_14d":18.1},
    "Elly De La Cruz":   {"avg_14d":.301,"slg_14d":.548,"hr_14d":4,"barrel_14d":17.2},
    "Mookie Betts":      {"avg_14d":.318,"slg_14d":.541,"hr_14d":3,"barrel_14d":16.8},
    "William Contreras": {"avg_14d":.312,"slg_14d":.538,"hr_14d":3,"barrel_14d":15.8},
    "Bo Bichette":       {"avg_14d":.321,"slg_14d":.521,"hr_14d":3,"barrel_14d":14.8},
    "Teoscar Hernandez": {"avg_14d":.298,"slg_14d":.528,"hr_14d":3,"barrel_14d":15.4},
    "Ronald Acuna Jr":   {"avg_14d":.188,"slg_14d":.312,"hr_14d":0,"barrel_14d":6.2},
    "Gunnar Henderson":  {"avg_14d":.198,"slg_14d":.348,"hr_14d":1,"barrel_14d":8.1},
    "Adolis Garcia":     {"avg_14d":.178,"slg_14d":.298,"hr_14d":0,"barrel_14d":5.8},
    "Marcell Ozuna":     {"avg_14d":.158,"slg_14d":.278,"hr_14d":0,"barrel_14d":4.8},
}


def load_live_batters():
    """
    Pull all active hitters from MLB Stats API.
    Returns list of batter dicts with live stats.
    Falls back to sample data if API fails.
    """
    if not _FORCE_LIVE:
        snap = _read_snapshot("live_batters.json")
        if snap and snap.get("batters"):
            print(f"  Using committed batter snapshot ({len(snap['batters'])} batters, {_snapshot_age_h(snap)}h old)")
            return snap["batters"]
    try:
        print("  Loading live batter stats from MLB Stats API...")

        # Step 1: Get all player bio data (batting hand)
        bio_map = {}
        r_bio = requests.get(
            "https://statsapi.mlb.com/api/v1/sports/1/players",
            params={"season": "2026"},
            timeout=15
        )
        if r_bio.status_code == 200:
            for p in r_bio.json().get("people", []):
                pid  = p.get("id")
                hand = p.get("batSide", {}).get("code", "R")
                team = p.get("currentTeam", {}).get("abbreviation", "")
                pos  = p.get("primaryPosition", {}).get("abbreviation", "")
                if pid:
                    bio_map[pid] = {"hand": hand, "team": team, "pos": pos}
            print(f"    Bio data loaded for {len(bio_map)} players")

        # Step 2: Get bulk hitting stats
        batters = []
        for offset in [0, 250, 500]:
            r_stats = requests.get(
                "https://statsapi.mlb.com/api/v1/stats",
                params={
                    "stats":    "season",
                    "group":    "hitting",
                    "season":   "2026",
                    "sportId":  "1",
                    "limit":    "250",
                    "offset":   str(offset),
                    "minAtBats": "30",
                },
                timeout=15
            )
            if r_stats.status_code != 200:
                break

            splits = r_stats.json().get("stats", [{}])[0].get("splits", [])
            if not splits:
                break

            for s in splits:
                st   = s.get("stat", {})
                p    = s.get("player", {})
                pid  = p.get("id")
                name = p.get("fullName", "")
                team_info = s.get("team", {})
                team_abb  = TEAM_ABB.get(team_info.get("name",""), team_info.get("abbreviation",""))

                bio  = bio_map.get(pid, {})
                hand = bio.get("hand", "R")

                try:
                    avg = float(st.get("avg", 0) or 0)
                    slg = float(st.get("slg", st.get("sluggingPct", 0)) or 0)
                    obp = float(st.get("obp", 0) or 0)
                    hr  = int(st.get("homeRuns", 0) or 0)
                    ab  = int(st.get("atBats", 0) or 0)
                    h   = int(st.get("hits", 0) or 0)
                    bb  = int(st.get("baseOnBalls", 0) or 0)
                    so  = int(st.get("strikeOuts", 0) or 0)
                    pa  = int(st.get("plateAppearances", 0) or 0)
                except:
                    continue

                if pa < 30 or not name:
                    continue

                # Calculate ISO and K%/BB%
                iso   = round(slg - avg, 3) if slg and avg else None
                k_pct = round(so / pa * 100, 1) if pa > 0 else None
                bb_pct= round(bb / pa * 100, 1) if pa > 0 else None

                bat = {
                    "name":      name,
                    "team":      team_abb or bio.get("team",""),
                    "hand":      hand,
                    "avg":       avg,
                    "slg":       slg,
                    "obp":       obp,
                    "hr":        hr,
                    "iso":       iso,
                    "k_pct":     k_pct,
                    "bb_pct":    bb_pct,
                    "woba":      None,
                    "player_id": pid,
                    "headshot":  get_headshot_url(name, pid),
                    # Statcast overlay for known players
                    "barrel_pct":      STATCAST_OVERLAY.get(name, {}).get("barrel_pct"),
                    "avg_hit_speed":   STATCAST_OVERLAY.get(name, {}).get("avg_hit_speed"),
                    "near_hr":         STATCAST_OVERLAY.get(name, {}).get("near_hr"),
                    # Form overlay
                    "avg_14d":         FORM_OVERLAY.get(name, {}).get("avg_14d"),
                    "slg_14d":         FORM_OVERLAY.get(name, {}).get("slg_14d"),
                    "hr_14d":          FORM_OVERLAY.get(name, {}).get("hr_14d"),
                    "barrel_14d":      FORM_OVERLAY.get(name, {}).get("barrel_14d"),
                }
                # Use woba from statcast if available
                if name in STATCAST_OVERLAY:
                    bat["woba"] = STATCAST_OVERLAY[name].get("woba")

                batters.append(bat)

        if batters:
            print(f"    {len(batters)} live batters loaded")
            return batters
        else:
            print("    API returned no batters — using sample data")
            return None

    except Exception as e:
        print(f"    Live batter load error: {e}")
        return None


def get_game_lineup(game_id):
    """Fetch batting lineup for a game from MLB Stats API
    Works for pre-game, in-progress, and completed games"""
    try:
        url  = f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
        data = requests.get(url, timeout=10).json()
        teams = data.get('teams', {})
        lineups = {}

        for side in ['away', 'home']:
            team_data = teams.get(side, {})
            team_name = team_data.get('team', {}).get('name', '')
            team_abb  = TEAM_ABB.get(team_name, team_name[:3].upper())
            players   = team_data.get('players', {})

            # Try batting order first — the official 'batters' array is only
            # populated once the lineup is officially posted, so use it as the
            # "confirmed" signal.
            official_order = team_data.get('batters', [])
            bat_order = official_order

            # If no batting order yet try battingOrder from team info
            if not bat_order:
                bat_order = team_data.get('battingOrder', [])

            confirmed = bool(official_order)
            lineup = []

            if bat_order:
                for pid in bat_order[:9]:
                    key    = f'ID{pid}'
                    player = players.get(key, {})
                    pname  = player.get('person', {}).get('fullName', '')
                    pos    = player.get('position', {}).get('abbreviation', '')
                    pid_int = player.get('person', {}).get('id')
                    if pname:
                        stats = get_batter_stats(pname) or {
                            'name': pname, 'team': team_abb,
                            'hand': '?', 'avg': None, 'slg': None,
                            'hr': None, 'barrel_pct': None,
                            'avg_hit_speed': None, 'iso': None,
                            'k_pct': None, 'woba': None
                        }
                        stats = dict(stats)
                        stats['name']      = pname
                        stats['position']  = pos
                        stats['order']     = bat_order.index(pid) + 1
                        stats['confirmed'] = confirmed
                        stats['player_id'] = pid_int
                        stats['headshot']  = get_headshot_url(pname, pid_int)
                        stats['batter_score'] = batter_score(stats)
                        lineup.append(stats)
            else:
                # Fallback: use all batters listed in players dict
                # sorted by batting order if available
                batter_list = []
                for key, player in players.items():
                    pos = player.get('position', {}).get('type', '')
                    if pos in ('Hitter', 'Batter') or player.get('position', {}).get('abbreviation') not in ('P', 'TWP'):
                        pname   = player.get('person', {}).get('fullName', '')
                        pid_int = player.get('person', {}).get('id')
                        bo      = player.get('battingOrder', 999)
                        if pname and bo != 999:
                            batter_list.append((bo, pname, pid_int, player.get('position',{}).get('abbreviation','')))
                batter_list.sort()
                for bo, pname, pid_int, pos in batter_list[:9]:
                    stats = get_batter_stats(pname) or {
                        'name': pname, 'team': team_abb,
                        'hand': '?', 'avg': None, 'slg': None,
                        'hr': None, 'barrel_pct': None,
                        'avg_hit_speed': None, 'iso': None,
                        'k_pct': None, 'woba': None
                    }
                    stats = dict(stats)
                    stats['name']      = pname
                    stats['position']  = pos
                    stats['order']     = int(str(bo)[0]) if bo < 100 else int(bo/100)
                    stats['confirmed'] = confirmed
                    stats['player_id'] = pid_int
                    stats['headshot']  = get_headshot_url(pname, pid_int)
                    stats['batter_score'] = batter_score(stats)
                    lineup.append(stats)

            lineups[side] = {'team': team_name, 'abb': team_abb, 'lineup': lineup}
            print(f"    {side} lineup: {len(lineup)} batters")

        return lineups
    except Exception as e:
        print(f"  Lineup error for game {game_id}: {e}")
        import traceback; traceback.print_exc()
        return {}


def get_games():
    today = _et_today()
    try:
        url  = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,venue,linescore"
        data = requests.get(url, timeout=10).json()
        games = []
        for de in data.get("dates", []):
            for g in de.get("games", []):
                at  = g.get("teams",{}).get("away",{}).get("team",{}).get("name","")
                ht  = g.get("teams",{}).get("home",{}).get("team",{}).get("name","")
                aa  = TEAM_ABB.get(at, at[:3].upper())
                ha  = TEAM_ABB.get(ht, ht[:3].upper())
                ap_pp = g.get("teams",{}).get("away",{}).get("probablePitcher",{})
                hp_pp = g.get("teams",{}).get("home",{}).get("probablePitcher",{})
                ap  = ap_pp.get("fullName","TBD")
                hp  = hp_pp.get("fullName","TBD")
                ap_id = ap_pp.get("id")
                hp_id = hp_pp.get("id")
                ve  = g.get("venue",{}).get("name","")
                gid = g.get("gamePk")
                pk  = PARKS.get(ha, {"name": ve, "factor": 1.00, "friendly": None})

                # Get pitcher stats
                away_pit_stats = get_pitcher_stats(ap, ap_id) or {"name": ap, "team": aa, "hand": "R", "role": "SP"}
                home_pit_stats = get_pitcher_stats(hp, hp_id) or {"name": hp, "team": ha, "hand": "R", "role": "SP"}
                away_pit_stats = dict(away_pit_stats)
                home_pit_stats = dict(home_pit_stats)
                away_pit_stats['vuln_score'] = pitcher_vuln(away_pit_stats)
                home_pit_stats['vuln_score'] = pitcher_vuln(home_pit_stats)
                away_pit_stats['headshot'] = get_headshot_url(ap, ap_id)
                home_pit_stats['headshot'] = get_headshot_url(hp, hp_id)

                # Live score / game state from the hydrated linescore
                ls      = g.get("linescore", {}) or {}
                a_score = g.get("teams",{}).get("away",{}).get("score")
                h_score = g.get("teams",{}).get("home",{}).get("score")
                abstate = g.get("status",{}).get("abstractGameState","")  # Preview / Live / Final
                _stmap  = {"Top":"Top","Bottom":"Bot","Middle":"Mid","End":"End"}
                cur_inn = ls.get("currentInning")
                inn_str = ""
                if abstate == "Live" and cur_inn:
                    inn_str = (_stmap.get(ls.get("inningState",""), ls.get("inningState","")) + " " + str(cur_inn)).strip()

                games.append({
                    "game_id":          gid,
                    "away":             at,
                    "away_abb":         aa,
                    "home":             ht,
                    "home_abb":         ha,
                    "away_pitcher":     ap,
                    "home_pitcher":     hp,
                    "away_pitcher_stats": away_pit_stats,
                    "home_pitcher_stats": home_pit_stats,
                    "venue":            ve,
                    "park_factor":      pk["factor"],
                    "park_name":        pk["name"],
                    "park_friendly":    pk.get("friendly"),
                    "status":           g.get("status",{}).get("detailedState",""),
                    "away_score":       a_score,
                    "home_score":       h_score,
                    "game_state":       abstate,
                    "inning":           inn_str,
                    "game_time_utc":    g.get("gameDate", ""),
                })
        print(f"  {len(games)} games loaded")
        return games
    except Exception as e:
        print(f"Schedule error: {e}")
        return []


def build_projected_lineup(team_abb, batter_pool):
    """Projected lineup for when the official batting order hasn't posted yet.
    Pulls the team's regulars from the season batter pool, ranks them by power
    (HR, then SLG), assigns a projected order, and tags them confirmed=False so
    the UI clearly marks them PROJECTED. Lets the slate populate at any hour
    instead of sitting empty until lineups post."""
    if not batter_pool:
        return []
    team_bats = [b for b in batter_pool if b.get('team') == team_abb]
    if not team_bats:
        return []
    team_bats = sorted(
        team_bats,
        key=lambda b: ((b.get('hr') or 0), (b.get('slg') or 0)),
        reverse=True
    )
    lineup = []
    for i, b in enumerate(team_bats[:9]):
        bb = dict(b)
        bb['order']        = i + 1
        bb['confirmed']    = False
        bb['position']     = b.get('position', b.get('pos', ''))
        bb['batter_score'] = batter_score(bb)
        lineup.append(bb)
    return lineup


def get_game_matchups(games, batter_pool=None):
    global _lineup_cache, _picks_cache, _matchups_cache
    """
    For each game fetch the lineup and calculate matchup scores
    for each batter vs the opposing starting pitcher
    """
    matchups = []
    print(f"  Loading lineups for {len(games)} games...")
    for g in games:
        game_id = g.get('game_id')
        if not game_id:
            print(f"  Skipping {g.get('away_abb','?')} @ {g.get('home_abb','?')} — no game ID")
            continue

        print(f"  Loading lineup for {g['away_abb']} @ {g['home_abb']} (ID:{game_id})...")
        lineups = get_game_lineup(game_id)

        park_factor  = g.get('park_factor', 1.0)
        park_name    = g.get('park_name', '')
        park_friendly = g.get('park_friendly')

        # Away batters vs Home pitcher
        away_lineup = lineups.get('away', {}).get('lineup', [])
        if len(away_lineup) < 5:
            proj = build_projected_lineup(g.get('away_abb',''), batter_pool)
            if proj:
                print(f"    Using projected away lineup for {g.get('away_abb','')} ({len(proj)} batters)")
                away_lineup = proj
        home_pit    = g['home_pitcher_stats']

        away_scored = []
        for bat in away_lineup:
            ms, sigs = matchup_score(bat, home_pit)
            b = dict(bat)
            b['matchup_score'] = ms
            b['signals']       = sigs
            b['batter_score']  = batter_score(b)
            away_scored.append(b)
        away_scored.sort(key=lambda x: x['matchup_score'], reverse=True)

        # Home batters vs Away pitcher
        home_lineup = lineups.get('home', {}).get('lineup', [])
        if len(home_lineup) < 5:
            proj = build_projected_lineup(g.get('home_abb',''), batter_pool)
            if proj:
                print(f"    Using projected home lineup for {g.get('home_abb','')} ({len(proj)} batters)")
                home_lineup = proj
        away_pit    = g['away_pitcher_stats']

        home_scored = []
        for bat in home_lineup:
            ms, sigs = matchup_score(bat, away_pit)
            b = dict(bat)
            b['matchup_score'] = ms
            b['signals']       = sigs
            b['batter_score']  = batter_score(b)
            home_scored.append(b)
        home_scored.sort(key=lambda x: x['matchup_score'], reverse=True)

        matchups.append({
            "game":         f"{g['away_abb']} @ {g['home_abb']}",
            "away_abb":     g['away_abb'],
            "home_abb":     g['home_abb'],
            "venue":        g['venue'],
            "park_factor":  park_factor,
            "park_name":    park_name,
            "park_friendly": park_friendly,
            "status":       g.get('status',''),
            "away_pitcher": home_pit,
            "home_pitcher": away_pit,
            "away_batters": away_scored,
            "home_batters": home_scored,
        })

    return matchups


def get_top_picks(matchups, props):
    """
    Generate daily top picks based on matchup score + park + prop line
    """
    picks = []
    prop_lookup = {}
    for p in props:
        key = p.get('player','').lower()
        if key not in prop_lookup:
            prop_lookup[key] = []
        prop_lookup[key].append(p)

    try:
        bullpen_map = get_bullpen_map()
    except Exception:
        bullpen_map = {}

    for m in matchups:
        pf = m.get('park_factor', 1.0) or 1.0
        # Park: convert the factor into points (1.15 -> +4, 0.85 -> -4), capped.
        park_pts = max(-6, min(6, round((pf - 1.0) * 28)))

        # Weather: wind out / heat help, wind in / cold / rain hurt. Dome = neutral.
        wx = m.get('weather') or {}
        weather_pts = 0
        env_sigs = []
        if park_pts >= 3:
            env_sigs.append({"label": "Hitter-friendly park", "good": True})
        elif park_pts <= -3:
            env_sigs.append({"label": "Pitcher-friendly park", "good": False})
        if wx and not wx.get('dome'):
            ws = wx.get('wind_score', 0) or 0
            weather_pts += ws * 2
            tf = wx.get('temp_f', 70)
            tf = 70 if tf is None else tf
            if tf >= 80:   weather_pts += 1
            elif tf <= 50: weather_pts -= 1
            cond = (wx.get('conditions', '') or '').lower()
            if 'rain' in cond or 'drizzle' in cond: weather_pts -= 2
            if ws >= 1:    env_sigs.append({"label": "Wind blowing out", "good": True})
            elif ws <= -1: env_sigs.append({"label": "Wind blowing in", "good": False})
            if tf >= 80:   env_sigs.append({"label": "Hot \u2014 ball carries", "good": True})
            elif tf <= 50: env_sigs.append({"label": "Cold \u2014 suppresses HR", "good": False})
        weather_pts = max(-5, min(5, weather_pts))
        env_adj = park_pts + weather_pts

        for side, batters, pitcher in [
            ('away', m['away_batters'], m['home_pitcher']),
            ('home', m['home_batters'], m['away_pitcher'])
        ]:
            for bat in batters:
                ms = bat.get('matchup_score', 50)
                if ms < 60:
                    continue
                # Park + weather folded into the score that drives the pick.
                adj_ms = max(0, min(100, ms + env_adj))

                # Find prop line — ignore junk/suspended lines (e.g. +10000) and
                # take the best realistic price across books.
                MAX_SANE_ODDS = 1000   # drop anything longer than +1000 as not a real line
                bat_props = prop_lookup.get(bat['name'].lower(), [])
                best_prop = None
                for p in bat_props:
                    if p.get('side','').lower() != 'over':
                        continue
                    o = p.get('odds')
                    if o is None or o > MAX_SANE_ODDS:
                        continue
                    if best_prop is None or o > best_prop.get('odds', -99999):
                        best_prop = p

                # Confidence rating (park + weather now baked into adj_ms)
                signals_good = sum(1 for s in bat.get('signals',[]) if s.get('good') is True)
                confidence = "STRONG" if (adj_ms >= 78 and signals_good >= 3) else \
                             "GOOD"   if (adj_ms >= 70 and signals_good >= 2) else \
                             "MODERATE"

                # Calculate fair odds from the environment-adjusted score
                hr_prob = max(0.08, min(0.35, (adj_ms - 40) * 0.005 + 0.10))
                fair_odds = round(-(hr_prob / (1 - hr_prob)) * 100) if hr_prob >= 0.5 else round((1 - hr_prob) / hr_prob * 100)
                # Edge % vs prop line (positive = value bet)
                prop_odds_val = best_prop.get('odds') if best_prop else None
                edge_pct = None
                if prop_odds_val is not None:
                    if prop_odds_val > 0:
                        market_prob = 100 / (prop_odds_val + 100)
                    else:
                        market_prob = abs(prop_odds_val) / (abs(prop_odds_val) + 100)
                    edge_pct = round((hr_prob - market_prob) * 100, 1)

                picks.append({
                    "name":        bat['name'],
                    "team":        bat.get('team',''),
                    "order":       bat.get('order'),
                    "confirmed":   bat.get('confirmed', False),
                    "game":        m['game'],
                    "pitcher":     pitcher.get('name',''),
                    "pitcher_hand": pitcher.get('hand','R'),
                    "matchup_score": ms,
                    "pick_score":  adj_ms,
                    "opp_bullpen": bullpen_map.get((pitcher or {}).get('team')),
                    "batter_score": bat.get('batter_score', 0),
                    "barrel_pct":  bat.get('barrel_pct'),
                    "avg_hit_speed": bat.get('avg_hit_speed'),
                    "iso":         bat.get('iso'),
                    "hard_hit_pct": bat.get('hard_hit_pct'),
                    "fb_pct":      bat.get('fb_pct'),
                    "pull_pct":    bat.get('pull_pct'),
                    "max_ev":      bat.get('max_ev'),
                    "launch_angle": bat.get('launch_angle'),
                    "xslg":        bat.get('xslg'),
                    "savant":      bat.get('savant', False),
                    "park_factor": m['park_factor'],
                    "park_friendly": m['park_friendly'],
                    "park_name":   m['park_name'],
                    "confidence":  confidence,
                    "signals":     list(bat.get('signals', [])) + env_sigs,
                    "prop_line":   best_prop.get('line') if best_prop else None,
                    "prop_odds":   best_prop.get('odds') if best_prop else None,
                    "prop_book":   best_prop.get('book') if best_prop else None,
                    "fair_odds":   fair_odds,
                    "hr_prob":     round(hr_prob * 100, 1),
                    "edge_pct":    edge_pct,
                    "form_label":  bat.get('form_label'),
                    "form_emoji":  bat.get('form_emoji'),
                    "form_col":    bat.get('form_col'),
                    "form_adj":    bat.get('form_adj'),
                    "hr_14d":      bat.get('hr_14d'),
                    "weather":     m.get('weather'),
                    "headshot":    bat.get('headshot') or get_headshot_url(bat.get('name','')),
                    "near_hr":     bat.get('near_hr'),
                    "player_id":   bat.get('player_id'),
                    "_pitcher_id": pitcher.get('player_id'),
                })

    picks.sort(key=lambda x: (
        0 if x['confidence']=='STRONG' else 1 if x['confidence']=='GOOD' else 2,
        -x['matchup_score']
    ))
    top = picks[:15]
    # Career batter-vs-pitcher line + pitch-type matchup for the picks shown (bounded + cached).
    for p in top:
        p['h2h']       = get_batter_vs_pitcher(p.get('player_id'), p.get('_pitcher_id'))
        p['pitch_mix'] = pitch_matchup(p.get('player_id'), p.get('_pitcher_id'))
        p['form']      = get_recent_form(p.get('player_id'))
        p['pitcher_form'] = get_pitcher_recent_form(p.get('_pitcher_id'))
        p.pop('_pitcher_id', None)
    return top


# ── Credit-safe odds caching ───────────────────────────────────────────────
# The Odds API free tier is 500 credits/month. A live pull costs ~8 credits, and
# the data build can run many times a day (Render free-tier cold starts), so we
# cache the odds and reuse them instead of re-pulling on every build.
#  - odds_cache.json in the app dir is the PREFERRED source: a once-daily job can
#    write it so the app never spends a credit itself (zero cold-start risk).
#  - Otherwise we fall back to a short-lived local cache + a live pull.
_ODDS_CACHE_DIR_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'odds_cache.json')
_ODDS_CACHE_TMP_FILE  = os.path.join(tempfile.gettempdir(), 'hr_odds_cache.json')
_ODDS_CACHE_TTL       = 6 * 3600   # reuse a live pull for 6 hours

def _load_odds_cache():
    # 1) Prefer a pre-fetched snapshot committed/written by the daily job.
    try:
        if os.path.exists(_ODDS_CACHE_DIR_FILE):
            with open(_ODDS_CACHE_DIR_FILE) as f:
                data = json.load(f)
            return data.get('props', []), 'daily-snapshot'
    except Exception:
        pass
    # 2) Fall back to a recent local live-pull cache.
    try:
        if os.path.exists(_ODDS_CACHE_TMP_FILE):
            with open(_ODDS_CACHE_TMP_FILE) as f:
                data = json.load(f)
            if (time.time() - data.get('ts', 0)) < _ODDS_CACHE_TTL:
                return data.get('props', []), 'local-cache'
    except Exception:
        pass
    return None, None

def _save_odds_cache(props):
    try:
        with open(_ODDS_CACHE_TMP_FILE, 'w') as f:
            json.dump({'ts': time.time(), 'props': props}, f)
    except Exception:
        pass

def get_props_cached(api_key):
    """Credit-safe odds: reuse a cached snapshot when available; only spend
    Odds API credits on a genuine cache miss."""
    cached, source = _load_odds_cache()
    if cached is not None:
        print(f"  Using cached odds ({len(cached)} props, source={source}) — no credits spent")
        return cached
    if not api_key or api_key == "YOUR_ODDS_API_KEY_HERE":
        return []
    props = get_props(api_key)   # live pull — costs credits
    if props:
        _save_odds_cache(props)
    return props


def get_props(api_key):
    if not api_key or api_key == "YOUR_ODDS_API_KEY_HERE":
        return []
    try:
        events = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/events",
            params={"apiKey": api_key}, timeout=10).json()
        props = []
        for ev in events[:8]:
            try:
                r = requests.get(
                    f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{ev['id']}/odds",
                    params={"apiKey": api_key, "markets": "batter_home_runs",
                            "oddsFormat": "american",
                            "bookmakers": "draftkings,fanduel,betmgm"},
                    timeout=10)
                if r.status_code == 200:
                    for bk in r.json().get("bookmakers", []):
                        for mk in bk.get("markets", []):
                            if mk.get("key") == "batter_home_runs":
                                for oc in mk.get("outcomes", []):
                                    props.append({
                                        "player": oc.get("description",""),
                                        "line":   oc.get("point", 0.5),
                                        "odds":   oc.get("price", -110),
                                        "book":   bk.get("key",""),
                                        "side":   oc.get("name","")
                                    })
            except:
                pass
        print(f"  {len(props)} prop lines loaded")
        return props
    except Exception as e:
        print(f"Odds error: {e}")
        return []


# Batted-ball profile (hard-hit%, fly-ball%, pull%, max EV). Fly-ball% and pull%
# are set by real tendency, not just power, so they add independent HR signal.
BATTED_BALL = {
    "Aaron Judge":       {"hard_hit_pct":60,"fb_pct":40,"pull_pct":43,"max_ev":121},
    "Shohei Ohtani":     {"hard_hit_pct":56,"fb_pct":42,"pull_pct":46,"max_ev":119},
    "Yordan Alvarez":    {"hard_hit_pct":58,"fb_pct":36,"pull_pct":44,"max_ev":117},
    "Bryce Harper":      {"hard_hit_pct":52,"fb_pct":34,"pull_pct":48,"max_ev":116},
    "Freddie Freeman":   {"hard_hit_pct":48,"fb_pct":28,"pull_pct":40,"max_ev":113},
    "Juan Soto":         {"hard_hit_pct":52,"fb_pct":38,"pull_pct":45,"max_ev":116},
    "Gunnar Henderson":  {"hard_hit_pct":48,"fb_pct":40,"pull_pct":44,"max_ev":115},
    "Matt Olson":        {"hard_hit_pct":52,"fb_pct":42,"pull_pct":50,"max_ev":116},
    "Rafael Devers":     {"hard_hit_pct":51,"fb_pct":36,"pull_pct":46,"max_ev":115},
    "Kyle Tucker":       {"hard_hit_pct":46,"fb_pct":38,"pull_pct":47,"max_ev":113},
    "Bobby Witt Jr":     {"hard_hit_pct":50,"fb_pct":30,"pull_pct":42,"max_ev":116},
    "Jose Ramirez":      {"hard_hit_pct":42,"fb_pct":40,"pull_pct":50,"max_ev":110},
    "Mookie Betts":      {"hard_hit_pct":44,"fb_pct":42,"pull_pct":48,"max_ev":112},
    "Ronald Acuna Jr":   {"hard_hit_pct":50,"fb_pct":32,"pull_pct":44,"max_ev":117},
    "Fernando Tatis Jr": {"hard_hit_pct":48,"fb_pct":36,"pull_pct":45,"max_ev":116},
    "Adolis Garcia":     {"hard_hit_pct":44,"fb_pct":42,"pull_pct":48,"max_ev":114},
    "Julio Rodriguez":   {"hard_hit_pct":50,"fb_pct":30,"pull_pct":42,"max_ev":116},
    "Corbin Carroll":    {"hard_hit_pct":38,"fb_pct":38,"pull_pct":46,"max_ev":110},
    "Bo Bichette":       {"hard_hit_pct":42,"fb_pct":30,"pull_pct":44,"max_ev":111},
    "Trea Turner":       {"hard_hit_pct":40,"fb_pct":32,"pull_pct":44,"max_ev":111},
    "Elly De La Cruz":   {"hard_hit_pct":50,"fb_pct":34,"pull_pct":43,"max_ev":119},
    "Sal Stewart":       {"hard_hit_pct":40,"fb_pct":36,"pull_pct":44,"max_ev":110},
    "Tyler Stephenson":  {"hard_hit_pct":44,"fb_pct":32,"pull_pct":42,"max_ev":112},
    "Pete Alonso":       {"hard_hit_pct":50,"fb_pct":44,"pull_pct":50,"max_ev":118},
    "Cal Raleigh":       {"hard_hit_pct":46,"fb_pct":46,"pull_pct":52,"max_ev":115},
    "Willy Adames":      {"hard_hit_pct":42,"fb_pct":42,"pull_pct":48,"max_ev":113},
    "William Contreras": {"hard_hit_pct":44,"fb_pct":34,"pull_pct":43,"max_ev":112},
    "Marcell Ozuna":     {"hard_hit_pct":50,"fb_pct":38,"pull_pct":46,"max_ev":115},
    "Teoscar Hernandez": {"hard_hit_pct":46,"fb_pct":40,"pull_pct":46,"max_ev":114},
    "Corey Seager":      {"hard_hit_pct":50,"fb_pct":36,"pull_pct":45,"max_ev":116},
}

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def add_batted_ball(bat):
    """Attach hard_hit_pct / fb_pct / pull_pct / max_ev. Uses curated values when
    known. For others, only DERIVES estimates when the batter has at least one
    real power input (barrel% or EV) — otherwise leaves them blank so we never
    show a fabricated, identical value on hitters we have no data for."""
    name = bat.get('name', '')
    if name in BATTED_BALL:
        bat.update(BATTED_BALL[name])
        return bat
    bp  = bat.get('barrel_pct')
    ev  = bat.get('avg_hit_speed')
    iso = bat.get('iso')
    if bp is None and ev is None:
        return bat  # no real signal — leave blank, don't invent numbers
    bpv  = bp if bp is not None else 10.0
    evv  = ev if ev is not None else 88.0
    isov = iso if iso is not None else 0.16
    bat.setdefault('hard_hit_pct', round(_clamp((evv - 84) * 4 + 30, 28, 60)))
    bat.setdefault('max_ev',       round(_clamp(evv + 17 + bpv * 0.45, 106, 120)))
    bat.setdefault('fb_pct',       round(_clamp(30 + (isov - 0.18) * 55, 24, 45)))
    bat.setdefault('pull_pct',     round(_clamp(40 + (bpv - 12) * 0.7, 36, 52)))
    return bat


# ──────────────────────────────────────────────────────────────────────────
# Real batted-ball data from Baseball Savant (replaces curated estimates).
# Pulls the public leaderboard CSVs once per process (cached), matches players
# by MLBAM id (fallback: normalized name), and overlays REAL values. Any fetch
# or parse failure leaves the curated/derived values untouched — no regression.
# ──────────────────────────────────────────────────────────────────────────
SAVANT_TTL   = 6 * 3600          # refetch at most every 6h per process
_savant_cache = {"ts": 0.0, "by_id": {}, "by_name": {}}

# Target field -> candidate CSV column names (lowercased). First match wins,
# so header variations across Savant leaderboards degrade gracefully.
_SAVANT_FIELDS = {
    "barrel_pct":    ["brl_percent", "barrel_batted_rate"],
    "hard_hit_pct":  ["ev95percent", "hard_hit_percent"],
    "avg_hit_speed": ["avg_hit_speed", "exit_velocity_avg"],
    "max_ev":        ["max_hit_speed", "exit_velocity_max"],
    "launch_angle":  ["avg_hit_angle", "launch_angle_avg"],
    "sweet_spot":    ["anglesweetspotpercent", "sweet_spot_percent"],
    "xslg":          ["est_slg", "xslg"],
    "xwoba":         ["est_woba", "xwoba"],
    "fb_pct":        ["fb_percent", "flyballs_percent", "air_percent"],
    "pull_pct":      ["pull_percent", "pull_pct"],
}

def _norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace(".", "").replace(",", "")
    toks = [t for t in s.split() if t not in ("jr", "sr", "ii", "iii", "iv")]
    return " ".join(toks)

def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0

def _parse_ip(v):
    """MLB innings pitched like '45.2' = 45 innings + 2 outs = 45.667."""
    if v in (None, ""):
        return None
    try:
        s = str(v)
        if "." in s:
            whole, frac = s.split(".", 1)
            return int(whole or 0) + int(frac[0]) / 3.0
        return float(s)
    except (TypeError, ValueError):
        return None

def _fetch_savant_csv(url):
    tag = url.split("/leaderboard/")[-1].split("?")[0]
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (compatible; DiamondAnalytics/1.0)"})
    except Exception as e:
        print(f"  Savant request error [{tag}]: {e}")
        return []
    ctype = (r.headers.get("content-type") or "").lower()
    if r.status_code != 200:
        print(f"  Savant HTTP {r.status_code} [{tag}] ctype={ctype}")
        return []
    text = r.text or ""
    head = text.lstrip()[:80].replace("\n", " ")
    if head[:5].lower() in ("<html", "<!doc") or "<!doctype" in text[:200].lower() or "text/html" in ctype:
        print(f"  Savant returned HTML not CSV [{tag}] ctype={ctype} head='{head[:50]}'")
        return []
    rows = list(csv.DictReader(io.StringIO(text)))
    cols = list(rows[0].keys())[:8] if rows else []
    print(f"  Savant CSV ok [{tag}]: {len(rows)} rows, cols={cols}")
    return rows

def _savant_row_id_name(low):
    pid = None
    for k in ("player_id", "mlbam_id", "mlb_id", "key_mlbam"):
        if k in low:
            try:
                pid = int(float(low[k])); break
            except (TypeError, ValueError):
                pass
    name = None
    for k in ("last_name, first_name", "player_name", "name"):
        if k in low and low[k]:
            v = low[k]
            if "," in v:
                last, first = [x.strip() for x in v.split(",", 1)]
                name = f"{first} {last}"
            else:
                name = v
            break
    if not name:
        fn = low.get("first_name") or low.get("name_first") or ""
        ln = low.get("last_name") or low.get("name_last") or ""
        if fn or ln:
            name = f"{fn} {ln}".strip()
    return pid, name

def _savant_extract(low):
    out = {}
    for field, cands in _SAVANT_FIELDS.items():
        for c in cands:
            if c in low and low[c] not in (None, "", "null"):
                val = _to_float(low[c])
                if val is not None:
                    out[field] = val
                    break
    return out

def load_savant_statcast():
    """Fetch + cache real batted-ball data keyed by MLBAM id and normalized name."""
    now = time.time()
    if _savant_cache["by_id"] and (now - _savant_cache["ts"]) < SAVANT_TTL:
        return _savant_cache
    if not _FORCE_LIVE:
        snap = _read_snapshot("savant_batters.json")
        if snap and snap.get("by_id"):
            _savant_cache.update({
                "ts": now,
                "by_id": {int(k): v for k, v in snap["by_id"].items()},
                "by_name": snap.get("by_name", {}),
            })
            print(f"  Using committed Savant batter snapshot ({len(_savant_cache['by_id'])} players, {_snapshot_age_h(snap)}h old)")
            return _savant_cache
    if os.environ.get("SAVANT_DISABLE"):
        return _savant_cache

    by_id, by_name = {}, {}
    this_year = datetime.now().year
    for yr in (this_year, this_year - 1):   # current season, fall back to last
        urls = [
            f"https://baseballsavant.mlb.com/leaderboard/statcast?type=batter&year={yr}&position=&team=&min=q&csv=true",
            f"https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=batter&year={yr}&position=&team=&min=q&csv=true",
            f"https://baseballsavant.mlb.com/leaderboard/batted-ball?type=batter&year={yr}&min=q&csv=true",
        ]
        for url in urls:
            try:
                rows = _fetch_savant_csv(url)
            except Exception as e:
                print(f"  Savant fetch failed ({url.split('?')[0]}): {e}")
                continue
            for row in rows:
                low = {(k or "").strip().lower(): v for k, v in row.items()}
                pid, name = _savant_row_id_name(low)
                stats = _savant_extract(low)
                if not stats:
                    continue
                if pid is not None:
                    by_id.setdefault(pid, {}).update(stats)
                if name:
                    by_name.setdefault(_norm_name(name), {}).update(stats)
        if by_id or by_name:
            break  # this season returned data; don't fall back

    if by_id or by_name:
        _savant_cache.update({"ts": now, "by_id": by_id, "by_name": by_name})
        print(f"  Savant data loaded: {len(by_id)} players by id, {len(by_name)} by name")
    else:
        print("  Savant returned no rows — keeping curated/derived batted-ball data")
    return _savant_cache

def apply_savant(bat):
    """Overlay real Savant values onto a batter. Only overwrites fields Savant
    actually provides; everything else keeps its curated/derived value."""
    cache = load_savant_statcast()
    rec = None
    pid = bat.get("player_id")
    if pid is not None:
        try:
            rec = cache["by_id"].get(int(pid))
        except (TypeError, ValueError):
            rec = None
    if rec is None:
        rec = cache["by_name"].get(_norm_name(bat.get("name", "")))
    if not rec:
        return bat
    for k, v in rec.items():
        if v is None:
            continue
        bat[k] = round(v, 3) if k in ("xslg", "xwoba") else round(v, 1)
    bat["savant"] = True
    return bat


# ──────────────────────────────────────────────────────────────────────────
# Live PITCHER data. Real season stats (ERA/WHIP/K9/HR9/K%) for every pitcher
# from the MLB Stats API, plus contact-quality-allowed (barrel%, EV, xSLG) from
# Baseball Savant. HR-risk by batter hand is derived from the real HR rate with
# a platoon adjustment. Falls back to SAMPLE_PITCHERS on any failure.
# ──────────────────────────────────────────────────────────────────────────
_savant_pitcher_cache = {"ts": 0.0, "by_id": {}, "by_name": {}}

def load_savant_pitchers():
    now = time.time()
    if _savant_pitcher_cache["by_id"] and (now - _savant_pitcher_cache["ts"]) < SAVANT_TTL:
        return _savant_pitcher_cache
    if os.environ.get("SAVANT_DISABLE"):
        return _savant_pitcher_cache
    by_id, by_name = {}, {}
    this_year = datetime.now().year
    for yr in (this_year, this_year - 1):
        urls = [
            f"https://baseballsavant.mlb.com/leaderboard/statcast?type=pitcher&year={yr}&position=&team=&min=q&csv=true",
            f"https://baseballsavant.mlb.com/leaderboard/expected_statistics?type=pitcher&year={yr}&position=&team=&min=q&csv=true",
        ]
        for url in urls:
            try:
                rows = _fetch_savant_csv(url)
            except Exception as e:
                print(f"  Savant pitcher fetch failed: {e}")
                continue
            for row in rows:
                low = {(k or "").strip().lower(): v for k, v in row.items()}
                pid, name = _savant_row_id_name(low)
                stats = _savant_extract(low)
                if not stats:
                    continue
                if pid is not None:
                    by_id.setdefault(pid, {}).update(stats)
                if name:
                    by_name.setdefault(_norm_name(name), {}).update(stats)
        if by_id or by_name:
            break
    if by_id or by_name:
        _savant_pitcher_cache.update({"ts": now, "by_id": by_id, "by_name": by_name})
        print(f"  Savant pitcher data loaded: {len(by_id)} by id")
    return _savant_pitcher_cache

def load_live_pitchers():
    """All pitchers' real season stats from the MLB Stats API, enriched with
    Savant contact-allowed data. Returns a list of pitcher dicts, or [] on failure."""
    if not _FORCE_LIVE:
        snap = _read_snapshot("live_pitchers.json")
        if snap and snap.get("pitchers"):
            print(f"  Using committed pitcher snapshot ({len(snap['pitchers'])} pitchers, {_snapshot_age_h(snap)}h old)")
            return snap["pitchers"]
    try:
        print("  Loading live pitcher stats from MLB Stats API...")
        bio = {}
        rb = requests.get("https://statsapi.mlb.com/api/v1/sports/1/players",
                          params={"season": str(datetime.now().year)}, timeout=15)
        if rb.status_code == 200:
            for p in rb.json().get("people", []):
                pid = p.get("id")
                if pid:
                    bio[pid] = {
                        "hand": p.get("pitchHand", {}).get("code", "R"),
                        "team": p.get("currentTeam", {}).get("abbreviation", ""),
                    }
        savant = load_savant_pitchers()
        pitchers, seen = [], set()
        for offset in [0, 250, 500, 750]:
            rs = requests.get("https://statsapi.mlb.com/api/v1/stats",
                              params={"stats": "season", "group": "pitching",
                                      "season": str(datetime.now().year), "sportId": "1",
                                      "limit": "250", "offset": str(offset)}, timeout=15)
            if rs.status_code != 200:
                break
            splits = rs.json().get("stats", [{}])[0].get("splits", [])
            if not splits:
                break
            for s in splits:
                st = s.get("stat", {}); p = s.get("player", {})
                pid = p.get("id"); name = p.get("fullName", "")
                if not pid or pid in seen or not name:
                    continue
                ip = _parse_ip(st.get("inningsPitched"))
                if ip is None or ip < 5:
                    continue
                seen.add(pid)
                so  = _to_int(st.get("strikeOuts")); bb = _to_int(st.get("baseOnBalls"))
                hr  = _to_int(st.get("homeRuns"));   tbf = _to_int(st.get("battersFaced"))
                gs  = _to_int(st.get("gamesStarted")); gp = _to_int(st.get("gamesPlayed"))
                era = _to_float(st.get("era")); whip = _to_float(st.get("whip"))
                baa = _to_float(st.get("avg"))
                bm  = bio.get(pid, {})
                k9     = round(so * 9 / ip, 1) if ip else None
                hr9    = round(hr * 9 / ip, 2) if ip else None
                k_pct  = round(so / tbf * 100, 1) if tbf else None
                bb_pct = round(bb / tbf * 100, 1) if tbf else None
                role   = "SP" if (gs >= 5 or (gp and gs / gp >= 0.5)) else "RP"
                pit = {
                    "name": name, "team": bm.get("team", "") or "", "hand": bm.get("hand", "R"),
                    "role": role, "era": era, "whip": whip, "k9": k9, "hr9": hr9,
                    "k_pct": k_pct, "bb_pct": bb_pct, "baa": baa, "player_id": pid,
                    "ip": round(ip, 1) if ip else None, "hr": hr,
                    "barrel_pct": None, "ev_allowed": None, "iso_allowed": None, "xslg_allowed": None,
                    "swstr": None, "gb_pct": None, "velo_season": None, "velo_recent": None,
                }
                rec = savant.get("by_id", {}).get(pid) or savant.get("by_name", {}).get(_norm_name(name))
                if rec:
                    if rec.get("barrel_pct") is not None:    pit["barrel_pct"]  = round(rec["barrel_pct"], 1)
                    if rec.get("avg_hit_speed") is not None: pit["ev_allowed"]  = round(rec["avg_hit_speed"], 1)
                    if rec.get("xslg") is not None:          pit["xslg_allowed"] = round(rec["xslg"], 3)
                    pit["savant"] = True
                # HR-risk by hand from real HR rate (+ a platoon adjustment)
                base = (hr9 / 1.3) if hr9 is not None else (
                    pit["barrel_pct"] / 11.0 if pit["barrel_pct"] is not None else None)
                if base is not None:
                    if pit["hand"] == "L":
                        pit["hr_risk_rhb"] = round(base * 1.08, 2)
                        pit["hr_risk_lhb"] = round(base * 0.90, 2)
                    else:
                        pit["hr_risk_rhb"] = round(base * 0.96, 2)
                        pit["hr_risk_lhb"] = round(base * 1.10, 2)
                pitchers.append(pit)
        if pitchers:
            print(f"  {len(pitchers)} live pitchers loaded")
            return pitchers
    except Exception as e:
        print(f"  load_live_pitchers failed: {e}")
    return []

_live_pitchers_cache = {"ts": 0.0, "pool": [], "by_id": {}, "by_name": {}}

def get_live_pitcher_pool():
    now = time.time()
    if _live_pitchers_cache["pool"] and (now - _live_pitchers_cache["ts"]) < SAVANT_TTL:
        return _live_pitchers_cache
    pool = load_live_pitchers()
    if pool:
        _live_pitchers_cache.update({
            "ts": now, "pool": pool,
            "by_id": {p["player_id"]: p for p in pool if p.get("player_id")},
            "by_name": {_norm_name(p["name"]): p for p in pool},
        })
    return _live_pitchers_cache


# ── BULLPEN HR/9 ────────────────────────────────────────────────────────────
# Aggregate each team's relievers (from the live pool, no extra calls) into a
# bullpen HR/9 — context for once the starter exits and for the HR environment.
_bullpen_cache = {"ts": -1, "map": {}}

def get_bullpen_map():
    global _bullpen_cache
    pool = get_live_pitcher_pool()
    ts = pool.get("ts", 0)
    if _bullpen_cache["map"] and _bullpen_cache["ts"] == ts:
        return _bullpen_cache["map"]

    agg = {}
    for p in pool.get("pool", []) or []:
        if p.get("role") != "RP":
            continue
        team = p.get("team") or ""
        if not team:
            continue
        a = agg.setdefault(team, {"hr_sum": 0.0, "ip_sum": 0.0, "hr9s": [], "n": 0})
        ip, hr, hr9 = p.get("ip"), p.get("hr"), p.get("hr9")
        if ip and hr is not None:
            a["hr_sum"] += hr; a["ip_sum"] += ip
        if hr9 is not None:
            a["hr9s"].append(hr9)
        a["n"] += 1

    bmap = {}
    for team, a in agg.items():
        if a["ip_sum"] >= 20:                       # innings-weighted (accurate)
            hr9 = round(a["hr_sum"] / a["ip_sum"] * 9, 2)
        elif a["hr9s"]:                             # fallback: unweighted mean
            hr9 = round(sum(a["hr9s"]) / len(a["hr9s"]), 2)
        else:
            continue
        bmap[team] = {"hr9": hr9, "n": a["n"]}

    vals = [v["hr9"] for v in bmap.values()]
    if vals:
        avg = round(sum(vals) / len(vals), 2)
        for v in bmap.values():
            v["lg_avg"] = avg
            if v["hr9"] >= avg + 0.25:
                v["label"], v["good"] = "homer-prone", True
            elif v["hr9"] <= avg - 0.25:
                v["label"], v["good"] = "lockdown", False
            else:
                v["label"], v["good"] = "league-avg", None

    _bullpen_cache = {"ts": ts, "map": bmap}
    return bmap


# ── PITCH-TYPE MATCHUP (Savant pitch-arsenal-stats) ─────────────────────────
# Two leaderboards, long format (one row per player per pitch type):
#   type=pitcher → how often each pitch is thrown (arsenal/usage)
#   type=batter  → how the hitter slugs vs each pitch type
_pitch_bat_cache = {"ts": 0, "by_id": {}}
_pitch_pit_cache = {"ts": 0, "by_id": {}}
PITCH_NAMES = {
    "FF":"4-Seam","SI":"Sinker","FC":"Cutter","SL":"Slider","ST":"Sweeper",
    "CU":"Curve","KC":"Knuckle-Curve","CH":"Changeup","FS":"Splitter",
    "SV":"Slurve","KN":"Knuckleball","FO":"Forkball","EP":"Eephus","SC":"Screwball"
}

def _pa_f(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def _fetch_pitch_arsenal(kind):
    """kind = 'batter' | 'pitcher'. Rows with lowercased keys, current year then prior."""
    for yr in (_et_now().year, _et_now().year - 1):
        url = (f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
               f"?type={kind}&year={yr}&min=10&csv=true")
        rows = _fetch_savant_csv(url)
        if rows:
            return [{(k or '').strip().lower(): v for k, v in r.items()} for r in rows]
    return []

def load_pitch_arsenal_batters():
    if not _FORCE_LIVE:
        snap = _read_snapshot("pitch_bat.json")
        if snap and snap.get("by_id"):
            return {"ts": time.time(), "by_id": {int(k): v for k, v in snap["by_id"].items()}}
    by_id = {}
    for low in _fetch_pitch_arsenal("batter"):
        pid, _ = _savant_row_id_name(low)
        pt = (low.get("pitch_type") or "").upper()
        if not pid or not pt:
            continue
        slg = _pa_f(low.get("slg"))
        if slg is None: slg = _pa_f(low.get("est_slg"))
        pa = _pa_f(low.get("pa")) or _pa_f(low.get("pitches"))
        by_id.setdefault(pid, {})[pt] = {
            "slg": slg, "woba": _pa_f(low.get("woba")),
            "pa": int(pa) if pa else None,
            "name": low.get("pitch_name") or PITCH_NAMES.get(pt, pt),
        }
    return {"ts": time.time(), "by_id": by_id}

def load_pitch_arsenal_pitchers():
    if not _FORCE_LIVE:
        snap = _read_snapshot("pitch_pit.json")
        if snap and snap.get("by_id"):
            return {"ts": time.time(), "by_id": {int(k): v for k, v in snap["by_id"].items()}}
    raw = {}
    for low in _fetch_pitch_arsenal("pitcher"):
        pid, _ = _savant_row_id_name(low)
        pt = (low.get("pitch_type") or "").upper()
        if not pid or not pt:
            continue
        cnt = _pa_f(low.get("pitches")) or _pa_f(low.get("n")) or 0
        raw.setdefault(pid, []).append({
            "pitch_type": pt,
            "name": low.get("pitch_name") or PITCH_NAMES.get(pt, pt),
            "count": cnt,
            "slg_allowed": _pa_f(low.get("slg")),
        })
    by_id = {}
    for pid, arr in raw.items():
        total = sum(p["count"] for p in arr) or 1
        for p in arr:
            p["usage"] = round(p["count"] / total * 100, 1)
        arr.sort(key=lambda p: -p["usage"])
        by_id[pid] = arr
    return {"ts": time.time(), "by_id": by_id}

def get_pitch_bat_pool():
    global _pitch_bat_cache
    if not _pitch_bat_cache["by_id"] or (time.time() - _pitch_bat_cache["ts"]) > SAVANT_TTL:
        try:
            _pitch_bat_cache = load_pitch_arsenal_batters()
        except Exception as e:
            print("  pitch-arsenal batters load failed:", e)
    return _pitch_bat_cache

def get_pitch_pit_pool():
    global _pitch_pit_cache
    if not _pitch_pit_cache["by_id"] or (time.time() - _pitch_pit_cache["ts"]) > SAVANT_TTL:
        try:
            _pitch_pit_cache = load_pitch_arsenal_pitchers()
        except Exception as e:
            print("  pitch-arsenal pitchers load failed:", e)
    return _pitch_pit_cache

def pitch_matchup(batter_id, pitcher_id, top_n=3):
    """Pitcher's most-used pitches + how the hitter slugs vs each.
    Returns {pitches:[{name,pitch_type,usage,slg,pa}], edge: bool} or None."""
    if not batter_id or not pitcher_id:
        return None
    try:
        batter_id = int(batter_id); pitcher_id = int(pitcher_id)
    except (TypeError, ValueError):
        return None
    arsenal = get_pitch_pit_pool()["by_id"].get(pitcher_id)
    bat     = get_pitch_bat_pool()["by_id"].get(batter_id)
    if not arsenal or not bat:
        return None
    out = []
    for p in arsenal[:top_n]:
        bp = bat.get(p["pitch_type"]) or {}
        out.append({
            "name": p["name"], "pitch_type": p["pitch_type"], "usage": p["usage"],
            "slg": bp.get("slg"), "pa": bp.get("pa"),
        })
    if not out:
        return None
    top = out[0]
    edge = (top.get("slg") is not None and top["slg"] >= 0.450 and top["usage"] >= 25)
    return {"pitches": out, "edge": edge}


def build_batters():
    # Try live MLB Stats API first — falls back to sample data
    live = load_live_batters()
    source = live if live else SAMPLE_BATTERS

    batters = []
    for b in source:
        bat = dict(b)
        add_batted_ball(bat)
        apply_savant(bat)
        bat['batter_score'] = batter_score(bat)
        fl, fe, fc, fa = form_trend(bat)
        bat['form_label']  = fl
        bat['form_emoji']  = fe
        bat['form_col']    = fc
        bat['form_adj']    = fa
        bat['batter_score_with_form'] = batter_score_with_form(bat)
        # Add headshot if not already set
        if not bat.get('headshot'):
            bat['headshot'] = get_headshot_url(bat.get('name',''))
        # Add near HR from statcast overlay if not set
        if not bat.get('near_hr'):
            bat['near_hr'] = STATCAST_OVERLAY.get(bat.get('name',''), {}).get('near_hr')
        batters.append(bat)
    return sorted(batters, key=lambda x: x.get('batter_score_with_form', 0), reverse=True)


def build_pitchers():
    pool = get_live_pitcher_pool()["pool"]
    source = pool if pool else SAMPLE_PITCHERS
    pitchers = []
    for p in source:
        pit = dict(p)
        pit['vuln_score']   = pitcher_vuln(pit)
        vl, vc, vs_score    = velo_status(pit)
        pit['velo_label']   = vl
        pit['velo_col']     = vc
        pit['velo_drop_val']= velo_drop(pit)
        pitchers.append(pit)
    return sorted(pitchers, key=lambda x: x.get('vuln_score', 0), reverse=True)


def get_week_schedule():
    """Pull probable starters for the next 7 days"""
    from datetime import timedelta
    week_games = []
    today = date.today()
    for i in range(7):
        day = (today + timedelta(days=i)).strftime("%Y-%m-%d")
        day_label = (today + timedelta(days=i)).strftime("%a %b %d")
        try:
            url  = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={day}&hydrate=probablePitcher,team,venue"
            data = requests.get(url, timeout=10).json()
            for de in data.get("dates", []):
                for g in de.get("games", []):
                    at = g.get("teams",{}).get("away",{}).get("team",{}).get("name","")
                    ht = g.get("teams",{}).get("home",{}).get("team",{}).get("name","")
                    aa = TEAM_ABB.get(at, at[:3].upper())
                    ha = TEAM_ABB.get(ht, ht[:3].upper())
                    ap = g.get("teams",{}).get("away",{}).get("probablePitcher",{}).get("fullName","TBD")
                    hp = g.get("teams",{}).get("home",{}).get("probablePitcher",{}).get("fullName","TBD")
                    ve = g.get("venue",{}).get("name","")
                    pk = PARKS.get(ha, {"name": ve, "factor": 1.00, "friendly": None})

                    # Get pitcher vulnerability from our data
                    ap_stats = get_pitcher_stats(ap) or {}
                    hp_stats = get_pitcher_stats(hp) or {}

                    week_games.append({
                        "date":         day,
                        "date_label":   day_label,
                        "away":         at,
                        "away_abb":     aa,
                        "home":         ht,
                        "home_abb":     ha,
                        "away_pitcher": ap,
                        "home_pitcher": hp,
                        "away_hr9":     ap_stats.get("hr9"),
                        "away_vuln":    ap_stats.get("vuln_score"),
                        "away_velo":    ap_stats.get("velo_label"),
                        "away_velo_col":ap_stats.get("velo_col"),
                        "home_hr9":     hp_stats.get("hr9"),
                        "home_vuln":    hp_stats.get("vuln_score"),
                        "home_velo":    hp_stats.get("velo_label"),
                        "home_velo_col":hp_stats.get("velo_col"),
                        "venue":        ve,
                        "park_factor":  pk["factor"],
                        "park_friendly":pk.get("friendly"),
                    })
        except Exception as e:
            print(f"  Week schedule error for {day}: {e}")
    print(f"  Week schedule: {len(week_games)} games over 7 days")
    return week_games


def get_yesterdays_results():
    """
    Pull yesterday's MLB game results from Stats API.
    Returns list of games with scores for results checking.
    """
    from datetime import timedelta
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    results = []
    try:
        url  = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={yesterday}&hydrate=linescore,team"
        data = requests.get(url, timeout=10).json()
        for de in data.get("dates", []):
            for g in de.get("games", []):
                status = g.get("status", {}).get("detailedState", "")
                if "Final" not in status and "Completed" not in status:
                    continue
                at    = g.get("teams",{}).get("away",{}).get("team",{}).get("name","")
                ht    = g.get("teams",{}).get("home",{}).get("team",{}).get("name","")
                aa    = TEAM_ABB.get(at, at[:3].upper())
                ha    = TEAM_ABB.get(ht, ht[:3].upper())
                a_hrs = g.get("teams",{}).get("away",{}).get("leagueRecord",{})
                h_hrs = g.get("teams",{}).get("home",{}).get("leagueRecord",{})
                # Get HR data from linescore
                ls    = g.get("linescore", {})
                innings = ls.get("innings", [])
                results.append({
                    "date":     yesterday,
                    "away":     aa,
                    "home":     ha,
                    "status":   status,
                    "game_id":  g.get("gamePk"),
                    "innings":  len(innings),
                })
        print(f"  Yesterday's results: {len(results)} completed games")
    except Exception as e:
        print(f"  Yesterday results error: {e}")
    return results


def get_game_hr_leaders(game_id):
    """Get HR hitters from a specific game"""
    try:
        url  = f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
        data = requests.get(url, timeout=10).json()
        hr_hitters = []
        for side in ["away", "home"]:
            team   = data.get("teams", {}).get(side, {})
            players = team.get("players", {})
            for pid, player in players.items():
                stats = player.get("stats", {}).get("batting", {})
                hrs   = stats.get("homeRuns", 0)
                name  = player.get("person", {}).get("fullName", "")
                if hrs and hrs > 0:
                    hr_hitters.append({
                        "name": name,
                        "hrs":  hrs,
                        "team": team.get("team", {}).get("abbreviation", "")
                    })
        return hr_hitters
    except Exception as e:
        print(f"  Game HR leaders error for {game_id}: {e}")
        return []


def load_results_log():
    """Read the auto-graded results log written by the daily Action."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_log.json")
        with open(p) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            return d
    except Exception:
        pass
    return {"entries": []}


# ── STRIKEOUT PROJECTIONS ──────────────────────────────────────────
# Free model: pitcher K-rate x matchup (opposing lineup K%) x workload.
# Uses only data already pulled (MLB Stats API) — NO Odds API calls.
SK_LEAGUE_BAT_K = 22.0   # league-average batter strikeout rate (%)
SK_EXP_IP_SP    = 5.6    # expected innings for a starter (refinable per-pitcher later)


def _sk_avg_lineup_k(batters):
    """Average strikeout rate of a lineup (%), or None if unavailable."""
    ks = [b.get("k_pct") for b in (batters or [])
          if isinstance(b.get("k_pct"), (int, float)) and b.get("k_pct")]
    return round(sum(ks) / len(ks), 1) if ks else None


def _sk_expected_ip(pit):
    """Expected innings tonight from the pitcher's recent starts (avg IP/start).
    Reuses the cached recent-form lookup (no Odds API); clamps to a sane starter
    range and falls back to the league default when the log is too thin."""
    pid = pit.get("player_id")
    form = get_pitcher_recent_form(pid) if pid else None
    if form and form.get("starts", 0) >= 2 and form.get("ip"):
        return max(4.0, min(7.0, round(form["ip"] / form["starts"], 1)))
    return SK_EXP_IP_SP


def get_strikeout_projections(matchups):
    """Projected strikeouts for each starting pitcher on the slate.

    proj_K = expected_BF x pitcher_K_per_BF x opponent_factor
      - pitcher_K_per_BF : season K% (per batter faced), fallback from K/9
      - opponent_factor  : opposing lineup K% / league avg (log5-style), clamped
      - expected_BF      : expected_IP x (3 + WHIP)  (WHIP approximates baserunners)
    Returns a list ranked by projected Ks (desc). No Odds API usage.
    """
    out = []
    # Within each matchup the pitcher stored under "<side>_pitcher" faces the
    # lineup stored under "<side>_batters"; the opponent is that side's team.
    for m in matchups or []:
        pairs = [
            (m.get("away_pitcher"), m.get("away_batters"), m.get("away_abb")),
            (m.get("home_pitcher"), m.get("home_batters"), m.get("home_abb")),
        ]
        for pit, lineup, opp_abb in pairs:
            if not isinstance(pit, dict):
                continue
            name = (pit.get("name") or "").strip()
            if not name or name.upper() == "TBD":
                continue

            k_pct = pit.get("k_pct")
            k9    = pit.get("k9")
            if isinstance(k_pct, (int, float)) and k_pct > 0:
                p_k = k_pct / 100.0
            elif isinstance(k9, (int, float)) and k9 > 0:
                p_k   = k9 / 38.0          # ~38 batters faced per 9 IP
                k_pct = round(p_k * 100, 1)
            else:
                continue                   # no usable strikeout rate -> skip

            whip = pit.get("whip")
            whip = whip if isinstance(whip, (int, float)) and whip > 0 else 1.30

            opp_k = _sk_avg_lineup_k(lineup)
            if opp_k:
                opp_factor = max(0.82, min(1.22, opp_k / SK_LEAGUE_BAT_K))
            else:
                opp_factor = 1.0

            exp_ip = _sk_expected_ip(pit)
            exp_bf = exp_ip * (3.0 + whip)
            proj_k = exp_bf * p_k * opp_factor

            out.append({
                "pitcher":    name,
                "team":       pit.get("team", ""),
                "hand":       pit.get("hand", "R"),
                "opp":        opp_abb or "",
                "game":       m.get("game", ""),
                "headshot":   pit.get("headshot", ""),
                "proj_k":     round(proj_k, 1),
                "k_pct":      round(k_pct, 1) if isinstance(k_pct, (int, float)) else None,
                "k9":         k9 if isinstance(k9, (int, float)) else None,
                "opp_k_pct":  opp_k,
                "opp_factor": round(opp_factor, 2),
                "exp_ip":     exp_ip,
                "exp_bf":     round(exp_bf, 1),
                "whip":       round(whip, 2),
            })

    out.sort(key=lambda x: x["proj_k"], reverse=True)
    return out


# ── BATTER PROP PROJECTIONS (hits / total bases / stolen bases) ──────
# Free models built only from data already on hand (lineup batter stats +
# opposing starter). NO Odds API calls.
#   proj_H  = expected_AB x AVG x opp_factor
#   proj_TB = expected_AB x SLG x opp_factor
#   proj_SB = season SB / games played      (no opponent data -> rate only)
# expected_AB = expected_PA(by lineup spot) x AB-share(from BB%).
BP_LEAGUE_WHIP = 1.30   # league-average WHIP, the hit-environment baseline

# expected plate appearances by lineup spot (leadoff bats most, 9-hole least)
_BP_PA_BY_SPOT = {1: 4.65, 2: 4.55, 3: 4.45, 4: 4.35, 5: 4.25,
                  6: 4.15, 7: 4.05, 8: 3.95, 9: 3.85}

# Season stolen-base reference for the sample-batter pool (SB, games played).
# Self-contained like the strikeout constants; players not listed simply get
# no SB projection (same limitation as any stat missing from the pool).
SB_SEASON = {
    "Elly De La Cruz": (40, 72), "Bobby Witt Jr": (26, 72), "Corbin Carroll": (24, 70),
    "Jose Ramirez": (24, 70), "Shohei Ohtani": (22, 70), "Trea Turner": (21, 70),
    "Julio Rodriguez": (20, 70), "Ronald Acuna Jr": (18, 68), "Fernando Tatis Jr": (17, 70),
    "Kyle Tucker": (16, 70), "Gunnar Henderson": (15, 70), "Mookie Betts": (12, 70),
    "Willy Adames": (12, 70), "Bryce Harper": (10, 70), "Freddie Freeman": (9, 70),
    "Adolis Garcia": (9, 70), "Aaron Judge": (8, 70), "Teoscar Hernandez": (8, 70),
    "Juan Soto": (7, 70), "Bo Bichette": (6, 70), "Sal Stewart": (4, 60),
    "Rafael Devers": (3, 70), "William Contreras": (3, 70), "Corey Seager": (3, 68),
    "Pete Alonso": (2, 70), "Yordan Alvarez": (1, 65), "Matt Olson": (1, 70),
    "Tyler Stephenson": (1, 65), "Marcell Ozuna": (1, 70), "Cal Raleigh": (0, 68),
}


def _bp_expected_pa(order):
    """Expected plate appearances from the batter's lineup spot."""
    try:
        return _BP_PA_BY_SPOT.get(int(order), 4.2)
    except (TypeError, ValueError):
        return 4.2


def _bp_opp_factor(pitcher):
    """Hit environment from the opposing starter's WHIP vs league (proxy),
    clamped to a sane range. Higher WHIP -> more baserunners -> more hits."""
    if not isinstance(pitcher, dict):
        return 1.0
    whip = pitcher.get("whip")
    if isinstance(whip, (int, float)) and whip > 0:
        return max(0.85, min(1.18, whip / BP_LEAGUE_WHIP))
    return 1.0


def get_batter_projections(matchups):
    """Per-batter hits / total-bases / stolen-base projections for the slate.

    Returns one flat list; each frontend tab sorts by its own projection.
    A batter faces the pitcher stored under the SAME side key (mirroring the
    strikeout pairing): away_batters face m['away_pitcher'], etc.
    """
    out = []
    for m in matchups or []:
        pairs = [
            (m.get("away_batters"), m.get("away_pitcher"), m.get("away_abb"), m.get("home_abb")),
            (m.get("home_batters"), m.get("home_pitcher"), m.get("home_abb"), m.get("away_abb")),
        ]
        for lineup, opp_pit, team_abb, opp_abb in pairs:
            opp_factor = _bp_opp_factor(opp_pit)
            opp_name = (opp_pit.get("name", "") if isinstance(opp_pit, dict) else "")
            for bat in (lineup or []):
                if not isinstance(bat, dict):
                    continue
                name = (bat.get("name") or "").strip()
                if not name:
                    continue

                avg = bat.get("avg")
                slg = bat.get("slg")
                bb_pct = bat.get("bb_pct")

                exp_pa = _bp_expected_pa(bat.get("order"))
                share = 1.0 - ((bb_pct / 100.0) if isinstance(bb_pct, (int, float)) else 0.085) - 0.02
                share = max(0.80, min(0.95, share))
                exp_ab = exp_pa * share

                proj_h = (exp_ab * avg * opp_factor) if isinstance(avg, (int, float)) and avg > 0 else None
                proj_tb = (exp_ab * slg * opp_factor) if isinstance(slg, (int, float)) and slg > 0 else None

                sb_gp = SB_SEASON.get(name)
                if sb_gp and sb_gp[1] > 0:
                    sb_n, gp_n = sb_gp
                    proj_sb = max(0.0, min(1.2, sb_n / gp_n))
                else:
                    sb_n = gp_n = None
                    proj_sb = None

                if proj_h is None and proj_tb is None and proj_sb is None:
                    continue   # nothing to rank this batter on

                out.append({
                    "name":       name,
                    "team":       bat.get("team", team_abb or ""),
                    "hand":       bat.get("hand", "R"),
                    "order":      bat.get("order"),
                    "opp":        opp_abb or "",
                    "opp_pitcher": opp_name,
                    "game":       m.get("game", ""),
                    "headshot":   bat.get("headshot", ""),
                    "avg":        round(avg, 3) if isinstance(avg, (int, float)) else None,
                    "slg":        round(slg, 3) if isinstance(slg, (int, float)) else None,
                    "exp_ab":     round(exp_ab, 1),
                    "opp_factor": round(opp_factor, 2),
                    "proj_h":     round(proj_h, 2) if proj_h is not None else None,
                    "proj_tb":    round(proj_tb, 2) if proj_tb is not None else None,
                    "proj_sb":    round(proj_sb, 2) if proj_sb is not None else None,
                    "sb":         sb_n,
                    "gp":         gp_n,
                })
    return out


def build_all_data(odds_api_key=""):
    games    = get_games()
    # Read the daily odds snapshot regardless of whether a key is set in THIS
    # environment. In bulletproof mode the key lives only in GitHub, so the app
    # has no key locally — but it should still read odds_cache.json. The cached
    # fetch only spends credits on a genuine cache miss WITH a key present.
    props = get_props_cached(odds_api_key)
    if not props:
        props = get_free_props()
    batters  = build_batters()
    pitchers = build_pitchers()

    print("  Building game matchups from lineups...")
    matchups = get_game_matchups(games, batters)

    print("  Fetching weather data...")
    weather = get_all_weather(games)
    print(f"  Weather loaded for {len(weather)} stadiums")

    # Attach weather to each game and matchup BEFORE scoring picks, so park +
    # weather can be folded into the pick score (not just shown).
    for g in games:
        g['weather'] = weather.get(g.get('home_abb',''), None)
    for m in matchups:
        m['weather'] = weather.get(m.get('home_abb',''), None)

    print("  Building top picks...")
    top_picks = get_top_picks(matchups, props)
    print(f"  {len(top_picks)} top picks generated")

    # Cache matchups and picks so they persist through game time
    if matchups:
        _matchups_cache.clear()
        _matchups_cache.extend(matchups)
    if top_picks:
        _picks_cache.clear()
        _picks_cache.extend(top_picks)

    # Use cached data if live fetch returned nothing
    final_matchups  = matchups  if matchups  else _matchups_cache
    final_top_picks = top_picks if top_picks else _picks_cache

    print("  Loading week schedule...")
    week_schedule = get_week_schedule()

    print("  Loading yesterday's results...")
    yesterday_results = get_yesterdays_results()

    # Global name -> headshot URL map so any tab can show a player photo
    headshots = {}
    for b in batters:
        nm = b.get('name'); hs = b.get('headshot')
        if nm and hs:
            headshots[nm] = hs
    for nm, pid in PLAYER_IDS.items():
        if nm not in headshots:
            u = get_headshot_url(nm, pid)
            if u:
                headshots[nm] = u
    for g in games:
        for nm, st in [(g.get('away_pitcher'), g.get('away_pitcher_stats')),
                       (g.get('home_pitcher'), g.get('home_pitcher_stats'))]:
            if nm and st and st.get('headshot'):
                headshots[nm] = st['headshot']

    return {
        "games":             games,
        "props":             props,
        "batters":           batters,
        "pitchers":          pitchers,
        "matchups":          final_matchups,
        "strikeout_projections": get_strikeout_projections(final_matchups),
        "batter_projections":    get_batter_projections(final_matchups),
        "top_picks":         final_top_picks,
        "weather":           weather,
        "week_schedule":     week_schedule,
        "yesterday_results": yesterday_results,
        "headshots":         headshots,
        "daynight":          get_daynight_for_batters(batters) if batters else DAY_NIGHT,
        "today":             date.today().strftime("%Y-%m-%d"),
        "parks":             PARKS,
        "results_log":       load_results_log(),
    }
