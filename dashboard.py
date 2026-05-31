"""
dashboard.py
All MLB data loading, processing, and matchup analysis logic.
"""

import pandas as pd
import numpy as np
import requests
import json
from datetime import date

# ── PARKS ────────────────────────────────────────────────────────
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

# ── SAMPLE DATA ──────────────────────────────────────────────────
SAMPLE_BATTERS = [
    {"name":"Aaron Judge",       "team":"NYY","hand":"R","avg":.311,"slg":.621,"hr":18,"barrel_pct":20.4,"avg_hit_speed":93.2,"iso":.310,"k_pct":22.1,"bb_pct":14.8,"woba":.415,"obp":.412},
    {"name":"Shohei Ohtani",     "team":"LAD","hand":"L","avg":.298,"slg":.589,"hr":16,"barrel_pct":18.9,"avg_hit_speed":92.8,"iso":.291,"k_pct":19.8,"bb_pct":13.2,"woba":.399,"obp":.385},
    {"name":"Yordan Alvarez",    "team":"HOU","hand":"L","avg":.301,"slg":.578,"hr":15,"barrel_pct":19.1,"avg_hit_speed":92.4,"iso":.277,"k_pct":18.4,"bb_pct":11.9,"woba":.408,"obp":.388},
    {"name":"Bryce Harper",      "team":"PHI","hand":"L","avg":.295,"slg":.541,"hr":14,"barrel_pct":16.7,"avg_hit_speed":91.2,"iso":.246,"k_pct":20.1,"bb_pct":13.8,"woba":.395,"obp":.392},
    {"name":"Freddie Freeman",   "team":"LAD","hand":"L","avg":.308,"slg":.521,"hr":11,"barrel_pct":14.2,"avg_hit_speed":90.1,"iso":.213,"k_pct":15.2,"bb_pct":12.1,"woba":.389,"obp":.395},
    {"name":"Juan Soto",         "team":"NYM","hand":"L","avg":.289,"slg":.512,"hr":13,"barrel_pct":15.8,"avg_hit_speed":90.8,"iso":.223,"k_pct":17.9,"bb_pct":18.2,"woba":.392,"obp":.405},
    {"name":"Gunnar Henderson",  "team":"BAL","hand":"L","avg":.278,"slg":.509,"hr":14,"barrel_pct":15.2,"avg_hit_speed":90.4,"iso":.231,"k_pct":24.2,"bb_pct":11.2,"woba":.372,"obp":.362},
    {"name":"Matt Olson",        "team":"ATL","hand":"L","avg":.258,"slg":.498,"hr":15,"barrel_pct":17.8,"avg_hit_speed":91.9,"iso":.240,"k_pct":25.8,"bb_pct":12.8,"woba":.368,"obp":.352},
    {"name":"Rafael Devers",     "team":"BOS","hand":"L","avg":.278,"slg":.511,"hr":14,"barrel_pct":15.9,"avg_hit_speed":91.1,"iso":.233,"k_pct":21.4,"bb_pct":9.8, "woba":.368,"obp":.348},
    {"name":"Kyle Tucker",       "team":"HOU","hand":"L","avg":.274,"slg":.488,"hr":13,"barrel_pct":14.1,"avg_hit_speed":90.2,"iso":.214,"k_pct":19.2,"bb_pct":11.8,"woba":.365,"obp":.358},
    {"name":"Bobby Witt Jr",     "team":"KC", "hand":"R","avg":.302,"slg":.521,"hr":13,"barrel_pct":14.8,"avg_hit_speed":90.8,"iso":.219,"k_pct":20.1,"bb_pct":7.9, "woba":.375,"obp":.358},
    {"name":"Jose Ramirez",      "team":"CLE","hand":"S","avg":.282,"slg":.501,"hr":12,"barrel_pct":13.8,"avg_hit_speed":89.9,"iso":.219,"k_pct":14.8,"bb_pct":11.2,"woba":.372,"obp":.365},
    {"name":"Mookie Betts",      "team":"LAD","hand":"R","avg":.291,"slg":.498,"hr":12,"barrel_pct":13.9,"avg_hit_speed":89.8,"iso":.207,"k_pct":16.8,"bb_pct":11.4,"woba":.378,"obp":.374},
    {"name":"Ronald Acuna Jr",   "team":"ATL","hand":"R","avg":.294,"slg":.512,"hr":11,"barrel_pct":13.1,"avg_hit_speed":89.4,"iso":.218,"k_pct":20.8,"bb_pct":12.8,"woba":.375,"obp":.378},
    {"name":"Fernando Tatis Jr", "team":"SD", "hand":"R","avg":.271,"slg":.489,"hr":13,"barrel_pct":14.2,"avg_hit_speed":90.1,"iso":.218,"k_pct":23.8,"bb_pct":9.2, "woba":.358,"obp":.341},
    {"name":"Adolis Garcia",     "team":"TEX","hand":"R","avg":.261,"slg":.468,"hr":12,"barrel_pct":13.2,"avg_hit_speed":89.8,"iso":.207,"k_pct":26.8,"bb_pct":6.8, "woba":.342,"obp":.318},
    {"name":"Julio Rodriguez",   "team":"SEA","hand":"R","avg":.269,"slg":.468,"hr":11,"barrel_pct":12.8,"avg_hit_speed":89.1,"iso":.199,"k_pct":23.1,"bb_pct":9.1, "woba":.348,"obp":.338},
    {"name":"Corbin Carroll",    "team":"ARI","hand":"L","avg":.268,"slg":.442,"hr":8, "barrel_pct":9.8, "avg_hit_speed":87.2,"iso":.174,"k_pct":22.4,"bb_pct":10.9,"woba":.345,"obp":.355},
    {"name":"Bo Bichette",       "team":"TOR","hand":"R","avg":.285,"slg":.468,"hr":10,"barrel_pct":11.8,"avg_hit_speed":88.4,"iso":.183,"k_pct":21.8,"bb_pct":7.2, "woba":.352,"obp":.341},
    {"name":"Trea Turner",       "team":"PHI","hand":"R","avg":.281,"slg":.451,"hr":9, "barrel_pct":10.2,"avg_hit_speed":87.8,"iso":.170,"k_pct":18.9,"bb_pct":7.8, "woba":.348,"obp":.345},
    {"name":"Elly De La Cruz",   "team":"CIN","hand":"S","avg":.258,"slg":.468,"hr":12,"barrel_pct":13.1,"avg_hit_speed":90.2,"iso":.210,"k_pct":28.1,"bb_pct":8.1, "woba":.342,"obp":.312},
    {"name":"Sal Stewart",       "team":"CIN","hand":"R","avg":.243,"slg":.456,"hr":10,"barrel_pct":11.2,"avg_hit_speed":88.8,"iso":.213,"k_pct":24.8,"bb_pct":11.2,"woba":.338,"obp":.345},
    {"name":"Tyler Stephenson",  "team":"CIN","hand":"R","avg":.268,"slg":.493,"hr":9, "barrel_pct":12.1,"avg_hit_speed":89.2,"iso":.225,"k_pct":21.4,"bb_pct":12.2,"woba":.355,"obp":.354},
]

SAMPLE_PITCHERS = [
    {"name":"Cody Poteet",     "team":"CIN","hand":"R","role":"SP","era":4.85,"whip":1.45,"k9":7.8, "hr9":2.21,"barrel_pct":13.8,"ev_allowed":92.1,"iso_allowed":.258,"hr_risk_rhb":1.82,"hr_risk_lhb":2.41,"k_pct":18.2,"bb_pct":9.8, "swstr":11.2,"gb_pct":30.2},
    {"name":"Grant Holmes",    "team":"CIN","hand":"R","role":"SP","era":4.12,"whip":1.30,"k9":8.8, "hr9":1.55,"barrel_pct":10.8,"ev_allowed":90.8,"iso_allowed":.182,"hr_risk_rhb":0.95,"hr_risk_lhb":0.62,"k_pct":21.5,"bb_pct":10.8,"swstr":14.0,"gb_pct":38.4},
    {"name":"Trevor Megill",   "team":"MIL","hand":"R","role":"RP","era":4.21,"whip":1.38,"k9":9.8, "hr9":1.98,"barrel_pct":12.1,"ev_allowed":91.2,"iso_allowed":.241,"hr_risk_rhb":1.88,"hr_risk_lhb":2.08,"k_pct":22.8,"bb_pct":11.2,"swstr":13.8,"gb_pct":28.1},
    {"name":"Dean Kremer",     "team":"BAL","hand":"R","role":"SP","era":4.52,"whip":1.35,"k9":7.9, "hr9":1.91,"barrel_pct":11.2,"ev_allowed":91.1,"iso_allowed":.228,"hr_risk_rhb":1.71,"hr_risk_lhb":2.11,"k_pct":19.8,"bb_pct":8.9, "swstr":12.1,"gb_pct":32.1},
    {"name":"Zach Eflin",      "team":"TB", "hand":"R","role":"SP","era":4.12,"whip":1.28,"k9":8.2, "hr9":1.82,"barrel_pct":10.8,"ev_allowed":90.8,"iso_allowed":.218,"hr_risk_rhb":1.62,"hr_risk_lhb":1.98,"k_pct":20.1,"bb_pct":7.8, "swstr":12.8,"gb_pct":34.8},
    {"name":"Freddy Peralta",  "team":"MIL","hand":"R","role":"SP","era":3.12,"whip":1.08,"k9":11.8,"hr9":0.98,"barrel_pct":8.1, "ev_allowed":89.1,"iso_allowed":.181,"hr_risk_rhb":0.88,"hr_risk_lhb":1.08,"k_pct":28.4,"bb_pct":9.2, "swstr":17.2,"gb_pct":32.8},
    {"name":"Dylan Cease",     "team":"SD", "hand":"R","role":"SP","era":3.28,"whip":1.12,"k9":11.2,"hr9":0.82,"barrel_pct":7.8, "ev_allowed":88.8,"iso_allowed":.172,"hr_risk_rhb":0.72,"hr_risk_lhb":0.92,"k_pct":26.8,"bb_pct":11.2,"swstr":16.8,"gb_pct":38.9},
    {"name":"Blake Snell",     "team":"SF", "hand":"L","role":"SP","era":3.45,"whip":1.18,"k9":11.9,"hr9":0.88,"barrel_pct":7.2, "ev_allowed":88.2,"iso_allowed":.168,"hr_risk_rhb":0.98,"hr_risk_lhb":0.78,"k_pct":27.1,"bb_pct":12.8,"swstr":17.8,"gb_pct":34.1},
    {"name":"Gerrit Cole",     "team":"NYY","hand":"R","role":"SP","era":2.95,"whip":1.02,"k9":11.4,"hr9":0.92,"barrel_pct":6.8, "ev_allowed":88.1,"iso_allowed":.162,"hr_risk_rhb":0.82,"hr_risk_lhb":1.02,"k_pct":27.8,"bb_pct":7.8, "swstr":16.4,"gb_pct":38.4},
    {"name":"Spencer Strider", "team":"ATL","hand":"R","role":"SP","era":2.81,"whip":0.99,"k9":13.2,"hr9":0.85,"barrel_pct":6.2, "ev_allowed":87.8,"iso_allowed":.158,"hr_risk_rhb":0.75,"hr_risk_lhb":0.95,"k_pct":31.2,"bb_pct":8.4, "swstr":19.8,"gb_pct":35.2},
    {"name":"Zack Wheeler",    "team":"PHI","hand":"R","role":"SP","era":2.58,"whip":0.98,"k9":10.8,"hr9":0.71,"barrel_pct":5.8, "ev_allowed":87.1,"iso_allowed":.148,"hr_risk_rhb":0.62,"hr_risk_lhb":0.81,"k_pct":26.4,"bb_pct":6.8, "swstr":15.8,"gb_pct":42.1},
    {"name":"Corbin Burnes",   "team":"BAL","hand":"R","role":"SP","era":2.78,"whip":0.97,"k9":10.2,"hr9":0.68,"barrel_pct":5.2, "ev_allowed":86.8,"iso_allowed":.138,"hr_risk_rhb":0.58,"hr_risk_lhb":0.78,"k_pct":25.8,"bb_pct":6.2, "swstr":14.8,"gb_pct":48.2},
    {"name":"Logan Webb",      "team":"SF", "hand":"R","role":"SP","era":3.08,"whip":1.05,"k9":8.4, "hr9":0.58,"barrel_pct":4.8, "ev_allowed":86.2,"iso_allowed":.128,"hr_risk_rhb":0.48,"hr_risk_lhb":0.68,"k_pct":20.8,"bb_pct":7.2, "swstr":12.4,"gb_pct":54.8},
    {"name":"Framber Valdez",  "team":"HOU","hand":"L","role":"SP","era":3.02,"whip":1.11,"k9":8.8, "hr9":0.51,"barrel_pct":4.4, "ev_allowed":85.8,"iso_allowed":.118,"hr_risk_rhb":0.61,"hr_risk_lhb":0.41,"k_pct":21.8,"bb_pct":9.2, "swstr":13.2,"gb_pct":58.4},
    {"name":"Josh Hader",      "team":"HOU","hand":"L","role":"RP","era":1.88,"whip":0.88,"k9":14.8,"hr9":0.42,"barrel_pct":5.8, "ev_allowed":87.8,"iso_allowed":.128,"hr_risk_rhb":0.52,"hr_risk_lhb":0.32,"k_pct":34.8,"bb_pct":9.8, "swstr":24.8,"gb_pct":28.4},
    {"name":"Emmanuel Clase",  "team":"CLE","hand":"R","role":"RP","era":1.42,"whip":0.78,"k9":9.8, "hr9":0.18,"barrel_pct":3.2, "ev_allowed":85.1,"iso_allowed":.098,"hr_risk_rhb":0.08,"hr_risk_lhb":0.28,"k_pct":24.2,"bb_pct":4.8, "swstr":18.2,"gb_pct":62.1},
    {"name":"Devin Williams",  "team":"MIL","hand":"R","role":"RP","era":1.98,"whip":0.92,"k9":15.2,"hr9":0.28,"barrel_pct":4.2, "ev_allowed":86.2,"iso_allowed":.108,"hr_risk_rhb":0.18,"hr_risk_lhb":0.38,"k_pct":36.8,"bb_pct":10.2,"swstr":26.4,"gb_pct":41.2},
]

DAY_NIGHT = [
    {"name":"Aaron Judge",      "team":"NYY","day_avg":.298,"day_slg":.589,"day_hr":6, "day_barrel":18.2,"night_avg":.321,"night_slg":.645,"night_hr":12,"night_barrel":22.1,"pref":"Night","gap":"Large"},
    {"name":"Shohei Ohtani",    "team":"LAD","day_avg":.312,"day_slg":.601,"day_hr":7, "day_barrel":20.1,"night_avg":.288,"night_slg":.571,"night_hr":9, "night_barrel":17.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Yordan Alvarez",   "team":"HOU","day_avg":.288,"day_slg":.551,"day_hr":5, "day_barrel":17.2,"night_avg":.311,"night_slg":.598,"night_hr":10,"night_barrel":20.8,"pref":"Night","gap":"Moderate"},
    {"name":"Bryce Harper",     "team":"PHI","day_avg":.278,"day_slg":.508,"day_hr":4, "day_barrel":14.8,"night_avg":.308,"night_slg":.562,"night_hr":10,"night_barrel":18.1,"pref":"Night","gap":"Large"},
    {"name":"Freddie Freeman",  "team":"LAD","day_avg":.321,"day_slg":.548,"day_hr":4, "day_barrel":15.1,"night_avg":.298,"night_slg":.501,"night_hr":7, "night_barrel":13.4,"pref":"Day",  "gap":"Moderate"},
    {"name":"Juan Soto",        "team":"NYM","day_avg":.301,"day_slg":.528,"day_hr":5, "day_barrel":16.2,"night_avg":.281,"night_slg":.501,"night_hr":8, "night_barrel":15.4,"pref":"Day",  "gap":"Small"},
    {"name":"Gunnar Henderson", "team":"BAL","day_avg":.261,"day_slg":.478,"day_hr":4, "day_barrel":13.8,"night_avg":.289,"night_slg":.531,"night_hr":10,"night_barrel":16.2,"pref":"Night","gap":"Moderate"},
    {"name":"Matt Olson",       "team":"ATL","day_avg":.241,"day_slg":.468,"day_hr":5, "day_barrel":15.8,"night_avg":.268,"night_slg":.521,"night_hr":10,"night_barrel":19.1,"pref":"Night","gap":"Moderate"},
    {"name":"Bobby Witt Jr",    "team":"KC", "day_avg":.318,"day_slg":.551,"day_hr":6, "day_barrel":16.1,"night_avg":.291,"night_slg":.498,"night_hr":7, "night_barrel":13.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Kyle Tucker",      "team":"HOU","day_avg":.288,"day_slg":.508,"day_hr":5, "day_barrel":14.8,"night_avg":.264,"night_slg":.471,"night_hr":8, "night_barrel":13.4,"pref":"Day",  "gap":"Moderate"},
]


def safe(v):
    return v is None or (isinstance(v, float) and np.isnan(v))


def batter_score(r):
    s = 0
    bp  = r.get('barrel_pct', np.nan)
    ev  = r.get('avg_hit_speed', np.nan)
    iso = r.get('iso', np.nan)
    slg = r.get('slg', np.nan)
    if not safe(bp):  s += min(bp / 20 * 35, 35)
    if not safe(ev):  s += min((ev - 80) / 12 * 25, 25)
    if not safe(iso): s += min(iso / .300 * 20, 20)
    if not safe(slg): s += min(slg / .500 * 20, 20)
    return round(min(s, 100), 1)


def pitcher_vuln(r):
    s = 0
    hr9 = r.get('hr9', np.nan)
    bp  = r.get('barrel_pct', np.nan)
    iso = r.get('iso_allowed', np.nan)
    ev  = r.get('ev_allowed', np.nan)
    if not safe(hr9): s += min(hr9 / 1.8 * 35, 35)
    if not safe(bp):  s += min(bp / 10 * 30, 30)
    if not safe(iso): s += min(iso / .200 * 20, 20)
    if not safe(ev):  s += min((ev - 85) / 7 * 15, 15)
    return round(min(s, 100), 1)


def matchup_score(batter, pitcher):
    signals = []
    score   = 50
    b_hand  = batter.get('hand', 'R')
    p_hand  = pitcher.get('hand', 'R')

    # Platoon
    if (b_hand in ('L','S') and p_hand == 'R') or (b_hand in ('R','S') and p_hand == 'L'):
        signals.append({"label": "Platoon Advantage", "good": True})
        score += 10
    else:
        signals.append({"label": "Same-Hand Matchup", "good": False})
        score -= 5

    # HR Risk
    hr_risk = pitcher.get('hr_risk_rhb', np.nan) if b_hand in ('R','S') else pitcher.get('hr_risk_lhb', np.nan)
    side    = "RHB" if b_hand in ('R','S') else "LHB"
    if not safe(hr_risk):
        lbl = f"HR Risk vs {side}: {hr_risk:.2f}"
        if hr_risk >= 1.8:
            signals.append({"label": lbl + " (Ideal)", "good": True}); score += 20
        elif hr_risk >= 1.5:
            signals.append({"label": lbl + " (Favorable)", "good": True}); score += 12
        elif hr_risk >= 1.0:
            signals.append({"label": lbl + " (Average)", "good": None})
        else:
            signals.append({"label": lbl + " (Avoid)", "good": False}); score -= 15

    # Barrel matchup
    b_bp = batter.get('barrel_pct', np.nan)
    p_bp = pitcher.get('barrel_pct', np.nan)
    if not safe(b_bp) and not safe(p_bp):
        if b_bp >= 15 and p_bp >= 8:
            signals.append({"label": f"Elite Barrel matchup ({b_bp:.1f}% vs {p_bp:.1f}% allowed)", "good": True}); score += 15
        elif b_bp >= 12 and p_bp >= 6:
            signals.append({"label": f"Favorable Barrel ({b_bp:.1f}% vs {p_bp:.1f}% allowed)", "good": True}); score += 8
        elif b_bp < 8:
            signals.append({"label": f"Low Barrel% batter ({b_bp:.1f}%)", "good": False}); score -= 10

    # Exit velocity
    b_ev = batter.get('avg_hit_speed', np.nan)
    p_ev = pitcher.get('ev_allowed', np.nan)
    if not safe(b_ev) and not safe(p_ev):
        if b_ev >= 91 and p_ev >= 90:
            signals.append({"label": f"Hard Contact matchup ({b_ev:.1f} vs {p_ev:.1f} allowed)", "good": True}); score += 10
        elif b_ev < 87:
            signals.append({"label": f"Soft contact batter ({b_ev:.1f} mph)", "good": False}); score -= 8

    # ISO vs ISO allowed
    b_iso  = batter.get('iso', np.nan)
    p_isoa = pitcher.get('iso_allowed', np.nan)
    if not safe(b_iso) and not safe(p_isoa):
        if b_iso >= .250 and p_isoa >= .180:
            signals.append({"label": f"Power vs vulnerable pitcher (ISO {b_iso:.3f} vs {p_isoa:.3f})", "good": True}); score += 12
        elif b_iso < .150:
            signals.append({"label": f"Low power batter (ISO {b_iso:.3f})", "good": False}); score -= 8

    # K% risk
    b_k = batter.get('k_pct', np.nan)
    p_k = pitcher.get('k_pct', np.nan)
    if not safe(b_k) and not safe(p_k):
        if b_k > 25 and p_k > 28:
            signals.append({"label": f"High K risk ({b_k:.1f}% K vs {p_k:.1f}% K pitcher)", "good": False}); score -= 12
        elif b_k < 17 and p_k > 25:
            signals.append({"label": f"Good discipline vs K pitcher ({b_k:.1f}% K)", "good": True}); score += 8

    # SwStr
    p_sw = pitcher.get('swstr', np.nan)
    if not safe(p_sw):
        if p_sw >= 18:
            signals.append({"label": f"Elite whiff pitcher (SwStr {p_sw:.1f}%)", "good": False}); score -= 8
        elif p_sw <= 11:
            signals.append({"label": f"Hittable pitcher (SwStr {p_sw:.1f}%)", "good": True}); score += 6

    return max(0, min(100, round(score))), signals


def get_games():
    today = date.today().strftime("%Y-%m-%d")
    try:
        url  = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher,team,venue,linescore"
        data = requests.get(url, timeout=10).json()
        games = []
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
                games.append({
                    "away": at, "away_abb": aa, "home": ht, "home_abb": ha,
                    "away_pitcher": ap, "home_pitcher": hp, "venue": ve,
                    "park_factor": pk["factor"], "park_name": pk["name"],
                    "park_friendly": pk.get("friendly", None),
                    "status": g.get("status",{}).get("detailedState","")
                })
        return games
    except Exception as e:
        print(f"Schedule error: {e}")
        return []


def get_props(api_key):
    if not api_key or api_key == "YOUR_ODDS_API_KEY_HERE":
        return []
    try:
        events = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/events",
            params={"apiKey": api_key}, timeout=10).json()
        props = []
        for ev in events[:6]:
            try:
                r = requests.get(
                    f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{ev['id']}/odds",
                    params={"apiKey": api_key, "markets": "batter_home_runs",
                            "oddsFormat": "american", "bookmakers": "draftkings,fanduel,betmgm"},
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
        return props
    except Exception as e:
        print(f"Odds error: {e}")
        return []


def load_statcast(year=2026):
    try:
        from pybaseball import statcast_batter_exitvelo_barrels, statcast_pitcher_exitvelo_barrels
        import warnings; warnings.filterwarnings("ignore")
        be = statcast_batter_exitvelo_barrels(year, minBBE=50)
        pe = statcast_pitcher_exitvelo_barrels(year, minBBE=50)
        return be, pe
    except Exception as e:
        print(f"Statcast error: {e}")
        return None, None


def build_batters(be):
    if be is None:
        df = pd.DataFrame(SAMPLE_BATTERS)
        df['batter_score'] = df.apply(batter_score, axis=1)
        return df.sort_values('batter_score', ascending=False)
    try:
        df = be.copy()
        for c in list(df.columns):
            cl = c.lower()
            if 'barrel' in cl and 'pct' in cl:           df.rename(columns={c:'barrel_pct'}, inplace=True)
            elif 'avg_hit_speed' in cl:                   df.rename(columns={c:'avg_hit_speed'}, inplace=True)
            elif 'last_name' in cl and 'first' in cl:    df.rename(columns={c:'name'}, inplace=True)
        for col in ['avg','slg','obp','hr','iso','k_pct','bb_pct','woba','team','hand']:
            if col not in df.columns: df[col] = np.nan
        df['batter_score'] = df.apply(batter_score, axis=1)
        return df.sort_values('batter_score', ascending=False).head(60)
    except:
        df = pd.DataFrame(SAMPLE_BATTERS)
        df['batter_score'] = df.apply(batter_score, axis=1)
        return df.sort_values('batter_score', ascending=False)


def build_pitchers(pe):
    if pe is None:
        df = pd.DataFrame(SAMPLE_PITCHERS)
        df['vuln_score'] = df.apply(pitcher_vuln, axis=1)
        return df.sort_values('vuln_score', ascending=False)
    try:
        df = pe.copy()
        for c in list(df.columns):
            cl = c.lower()
            if 'barrel' in cl and 'pct' in cl:           df.rename(columns={c:'barrel_pct'}, inplace=True)
            elif 'avg_hit_speed' in cl:                   df.rename(columns={c:'ev_allowed'}, inplace=True)
            elif 'last_name' in cl and 'first' in cl:    df.rename(columns={c:'name'}, inplace=True)
        for col in ['era','whip','k9','hr9','iso_allowed','team','hand','role',
                    'hr_risk_rhb','hr_risk_lhb','k_pct','bb_pct','swstr','gb_pct']:
            if col not in df.columns: df[col] = np.nan
        df['vuln_score'] = df.apply(pitcher_vuln, axis=1)
        return df.sort_values('vuln_score', ascending=False).head(60)
    except:
        df = pd.DataFrame(SAMPLE_PITCHERS)
        df['vuln_score'] = df.apply(pitcher_vuln, axis=1)
        return df.sort_values('vuln_score', ascending=False)


def build_all_data(odds_api_key=""):
    games = get_games()
    props = get_props(odds_api_key)
    be, pe = load_statcast()
    batters_df  = build_batters(be)
    pitchers_df = build_pitchers(pe)
    return {
        "games":    games,
        "props":    props,
        "batters":  batters_df.to_dict('records'),
        "pitchers": pitchers_df.to_dict('records'),
        "daynight": DAY_NIGHT,
        "today":    date.today().strftime("%Y-%m-%d"),
        "parks":    PARKS,
    }
