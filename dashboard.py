"""
dashboard.py - MLB data logic
No pandas/numpy dependency - pure Python
Includes lineup fetching from MLB Stats API
"""

import requests
import json
from datetime import date, datetime
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
    {"name":"Pete Alonso",       "team":"NYM","hand":"R","avg":.254,"slg":.498,"hr":14,"barrel_pct":14.8,"avg_hit_speed":91.1,"iso":.244,"k_pct":23.8,"bb_pct":11.2,"woba":.358,"obp":.331},
    {"name":"Cal Raleigh",       "team":"SEA","hand":"S","avg":.241,"slg":.489,"hr":13,"barrel_pct":13.9,"avg_hit_speed":90.4,"iso":.248,"k_pct":26.1,"bb_pct":9.8, "woba":.345,"obp":.312},
    {"name":"Willy Adames",      "team":"SF", "hand":"R","avg":.251,"slg":.462,"hr":11,"barrel_pct":11.8,"avg_hit_speed":88.9,"iso":.211,"k_pct":24.8,"bb_pct":9.2, "woba":.338,"obp":.321},
    {"name":"William Contreras", "team":"MIL","hand":"R","avg":.268,"slg":.478,"hr":10,"barrel_pct":12.1,"avg_hit_speed":89.4,"iso":.210,"k_pct":19.8,"bb_pct":10.1,"woba":.348,"obp":.341},
    {"name":"Marcell Ozuna",     "team":"ATL","hand":"R","avg":.271,"slg":.501,"hr":12,"barrel_pct":13.4,"avg_hit_speed":90.1,"iso":.230,"k_pct":22.4,"bb_pct":8.8, "woba":.355,"obp":.332},
    {"name":"Teoscar Hernandez", "team":"LAD","hand":"R","avg":.261,"slg":.471,"hr":11,"barrel_pct":12.8,"avg_hit_speed":89.8,"iso":.210,"k_pct":24.1,"bb_pct":7.2, "woba":.342,"obp":.318},
    {"name":"Corey Seager",      "team":"TEX","hand":"L","avg":.278,"slg":.511,"hr":12,"barrel_pct":14.1,"avg_hit_speed":90.8,"iso":.233,"k_pct":18.8,"bb_pct":9.8, "woba":.365,"obp":.348},
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
    {"name":"Aaron Judge",      "team":"NYY","day_avg":.298,"day_slg":.589,"day_hr":6, "day_barrel":18.2,"night_avg":.321,"night_slg":.645,"night_hr":12,"night_barrel":22.1,"pref":"Night","gap":"Large"},
    {"name":"Shohei Ohtani",    "team":"LAD","day_avg":.312,"day_slg":.601,"day_hr":7, "day_barrel":20.1,"night_avg":.288,"night_slg":.571,"night_hr":9, "night_barrel":17.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Yordan Alvarez",   "team":"HOU","day_avg":.288,"day_slg":.551,"day_hr":5, "day_barrel":17.2,"night_avg":.311,"night_slg":.598,"night_hr":10,"night_barrel":20.8,"pref":"Night","gap":"Moderate"},
    {"name":"Bryce Harper",     "team":"PHI","day_avg":.278,"day_slg":.508,"day_hr":4, "day_barrel":14.8,"night_avg":.308,"night_slg":.562,"night_hr":10,"night_barrel":18.1,"pref":"Night","gap":"Large"},
    {"name":"Freddie Freeman",  "team":"LAD","day_avg":.321,"day_slg":.548,"day_hr":4, "day_barrel":15.1,"night_avg":.298,"night_slg":.501,"night_hr":7, "night_barrel":13.4,"pref":"Day",  "gap":"Moderate"},
    {"name":"Juan Soto",        "team":"NYM","day_avg":.301,"day_slg":.528,"day_hr":5, "day_barrel":16.2,"night_avg":.281,"night_slg":.501,"night_hr":8, "night_barrel":15.4,"pref":"Day",  "gap":"Small"},
    {"name":"Bobby Witt Jr",    "team":"KC", "day_avg":.318,"day_slg":.551,"day_hr":6, "day_barrel":16.1,"night_avg":.291,"night_slg":.498,"night_hr":7, "night_barrel":13.8,"pref":"Day",  "gap":"Moderate"},
    {"name":"Kyle Tucker",      "team":"HOU","day_avg":.288,"day_slg":.508,"day_hr":5, "day_barrel":14.8,"night_avg":.264,"night_slg":.471,"night_hr":8, "night_barrel":13.4,"pref":"Day",  "gap":"Moderate"},
]


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

    # Velocity tracker signal
    drop = velo_drop(pitcher)
    if drop is not None:
        if drop >= 2.0:
            signals.append({"label": f"Velo drop {drop:.1f} mph below season avg &#128308;", "good": True}); score += 12
        elif drop >= 1.0:
            signals.append({"label": f"Velo down {drop:.1f} mph vs season avg", "good": True}); score += 6
        elif drop <= -1.0:
            signals.append({"label": f"Velo up {abs(drop):.1f} mph — sharp &#128994;", "good": False}); score -= 4

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


def get_pitcher_stats(name):
    """Look up a pitcher's stats from our sample data by name"""
    name_lower = name.lower()
    for p in SAMPLE_PITCHERS:
        if p['name'].lower() == name_lower:
            result = dict(p)
            vl, vc, _ = velo_status(result)
            result['velo_label']    = vl
            result['velo_col']      = vc
            result['velo_drop_val'] = velo_drop(result)
            return result
        last = p['name'].split()[-1].lower()
        if last == name_lower.split()[-1]:
            result = dict(p)
            vl, vc, _ = velo_status(result)
            result['velo_label']    = vl
            result['velo_col']      = vc
            result['velo_drop_val'] = velo_drop(result)
            return result
    return None


def get_game_lineup(game_id):
    """Fetch batting lineup for a game from MLB Stats API"""
    try:
        url  = f"https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
        data = requests.get(url, timeout=10).json()
        teams = data.get('teams', {})
        lineups = {}
        for side in ['away', 'home']:
            team_data  = teams.get(side, {})
            team_name  = team_data.get('team', {}).get('name', '')
            team_abb   = TEAM_ABB.get(team_name, team_name[:3].upper())
            bat_order  = team_data.get('batters', [])
            players    = team_data.get('players', {})
            lineup = []
            for pid in bat_order[:9]:
                key    = f'ID{pid}'
                player = players.get(key, {})
                pname  = player.get('person', {}).get('fullName', '')
                pos    = player.get('position', {}).get('abbreviation', '')
                if pname:
                    stats = get_batter_stats(pname) or {
                        'name': pname, 'team': team_abb,
                        'hand': '?', 'avg': None, 'slg': None,
                        'hr': None, 'barrel_pct': None,
                        'avg_hit_speed': None, 'iso': None,
                        'k_pct': None, 'woba': None
                    }
                    stats = dict(stats)
                    stats['name']     = pname
                    stats['position'] = pos
                    stats['order']    = bat_order.index(pid) + 1
                    stats['batter_score'] = batter_score(stats)
                    lineup.append(stats)
            lineups[side] = {'team': team_name, 'abb': team_abb, 'lineup': lineup}
        return lineups
    except Exception as e:
        print(f"  Lineup error for game {game_id}: {e}")
        return {}


def get_games():
    today = date.today().strftime("%Y-%m-%d")
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
                ap  = g.get("teams",{}).get("away",{}).get("probablePitcher",{}).get("fullName","TBD")
                hp  = g.get("teams",{}).get("home",{}).get("probablePitcher",{}).get("fullName","TBD")
                ve  = g.get("venue",{}).get("name","")
                gid = g.get("gamePk")
                pk  = PARKS.get(ha, {"name": ve, "factor": 1.00, "friendly": None})

                # Get pitcher stats
                away_pit_stats = get_pitcher_stats(ap) or {"name": ap, "team": aa, "hand": "R", "role": "SP"}
                home_pit_stats = get_pitcher_stats(hp) or {"name": hp, "team": ha, "hand": "R", "role": "SP"}
                away_pit_stats = dict(away_pit_stats)
                home_pit_stats = dict(home_pit_stats)
                away_pit_stats['vuln_score'] = pitcher_vuln(away_pit_stats)
                home_pit_stats['vuln_score'] = pitcher_vuln(home_pit_stats)

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
                })
        print(f"  {len(games)} games loaded")
        return games
    except Exception as e:
        print(f"Schedule error: {e}")
        return []


def get_game_matchups(games):
    """
    For each game fetch the lineup and calculate matchup scores
    for each batter vs the opposing starting pitcher
    """
    matchups = []
    for g in games:
        game_id = g.get('game_id')
        if not game_id:
            continue

        print(f"  Loading lineup for {g['away_abb']} @ {g['home_abb']}...")
        lineups = get_game_lineup(game_id)

        park_factor  = g.get('park_factor', 1.0)
        park_name    = g.get('park_name', '')
        park_friendly = g.get('park_friendly')

        # Away batters vs Home pitcher
        away_lineup = lineups.get('away', {}).get('lineup', [])
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

    for m in matchups:
        park_boost = m['park_factor'] >= 1.10
        park_neg   = m['park_factor'] <= 0.85

        for side, batters, pitcher in [
            ('away', m['away_batters'], m['home_pitcher']),
            ('home', m['home_batters'], m['away_pitcher'])
        ]:
            for bat in batters:
                ms = bat.get('matchup_score', 50)
                if ms < 60:
                    continue

                # Find prop line
                bat_props = prop_lookup.get(bat['name'].lower(), [])
                best_prop = None
                for p in bat_props:
                    if p.get('side','').lower() == 'over':
                        best_prop = p
                        break

                # Confidence rating
                signals_good = sum(1 for s in bat.get('signals',[]) if s.get('good') is True)
                confidence = "STRONG" if (ms >= 75 and park_boost and signals_good >= 3) else \
                             "GOOD"   if (ms >= 70 and signals_good >= 2) else \
                             "MODERATE"

                if park_neg:
                    confidence = "MODERATE" if confidence == "STRONG" else confidence

                picks.append({
                    "name":        bat['name'],
                    "team":        bat.get('team',''),
                    "game":        m['game'],
                    "pitcher":     pitcher.get('name',''),
                    "pitcher_hand": pitcher.get('hand','R'),
                    "matchup_score": ms,
                    "batter_score": bat.get('batter_score', 0),
                    "barrel_pct":  bat.get('barrel_pct'),
                    "avg_hit_speed": bat.get('avg_hit_speed'),
                    "iso":         bat.get('iso'),
                    "park_factor": m['park_factor'],
                    "park_friendly": m['park_friendly'],
                    "park_name":   m['park_name'],
                    "confidence":  confidence,
                    "signals":     bat.get('signals', []),
                    "prop_line":   best_prop.get('line') if best_prop else None,
                    "prop_odds":   best_prop.get('odds') if best_prop else None,
                    "prop_book":   best_prop.get('book') if best_prop else None,
                })

    picks.sort(key=lambda x: (
        0 if x['confidence']=='STRONG' else 1 if x['confidence']=='GOOD' else 2,
        -x['matchup_score']
    ))
    return picks[:15]


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


def build_batters():
    batters = []
    for b in SAMPLE_BATTERS:
        bat = dict(b)
        bat['batter_score'] = batter_score(bat)
        batters.append(bat)
    return sorted(batters, key=lambda x: x.get('batter_score', 0), reverse=True)


def build_pitchers():
    pitchers = []
    for p in SAMPLE_PITCHERS:
        pit = dict(p)
        pit['vuln_score']   = pitcher_vuln(pit)
        vl, vc, vs_score    = velo_status(pit)
        pit['velo_label']   = vl
        pit['velo_col']     = vc
        pit['velo_drop_val']= velo_drop(pit)
        pitchers.append(pit)
    return sorted(pitchers, key=lambda x: x.get('vuln_score', 0), reverse=True)


def build_all_data(odds_api_key=""):
    games    = get_games()
    props    = get_props(odds_api_key)
    batters  = build_batters()
    pitchers = build_pitchers()

    print("  Building game matchups from lineups...")
    matchups = get_game_matchups(games)

    print("  Building top picks...")
    top_picks = get_top_picks(matchups, props)
    print(f"  {len(top_picks)} top picks generated")

    print("  Fetching weather data...")
    weather = get_all_weather(games)
    print(f"  Weather loaded for {len(weather)} stadiums")

    # Attach weather to each game and matchup
    for g in games:
        g['weather'] = weather.get(g.get('home_abb',''), None)
    for m in matchups:
        m['weather'] = weather.get(m.get('home_abb',''), None)

    # Add weather boost to top picks
    for pick in top_picks:
        for g in games:
            if g.get('away_abb') == pick.get('team') or g.get('home_abb') == pick.get('team'):
                pick['weather'] = g.get('weather')
                break

    return {
        "games":      games,
        "props":      props,
        "batters":    batters,
        "pitchers":   pitchers,
        "matchups":   matchups,
        "top_picks":  top_picks,
        "weather":    weather,
        "daynight":   DAY_NIGHT,
        "today":      date.today().strftime("%Y-%m-%d"),
        "parks":      PARKS,
    }
