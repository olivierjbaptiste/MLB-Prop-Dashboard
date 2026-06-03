"""
weather.py
Free weather integration using Open-Meteo API
No API key required
Pulls conditions for tonight's MLB games
"""

import requests
import math
from datetime import date, datetime

# Stadium coordinates, dome status, and outfield orientation.
# "orient" = approximate compass bearing (deg, 0=N, 90=E) from home plate to center
# field. Best-effort values (most parks ~ENE per MLB rule); refine against a verified
# azimuth source. Only used for open-air parks (domes skip wind entirely).
STADIUMS = {
    "NYY": {"name":"Yankee Stadium",        "lat":40.8296, "lon":-73.9262, "dome":False,  "retractable":False, "orient":30},
    "NYM": {"name":"Citi Field",            "lat":40.7571, "lon":-73.8458, "dome":False,  "retractable":False, "orient":30},
    "BOS": {"name":"Fenway Park",           "lat":42.3467, "lon":-71.0972, "dome":False,  "retractable":False, "orient":45},
    "PHI": {"name":"Citizens Bank Park",    "lat":39.9061, "lon":-75.1665, "dome":False,  "retractable":False, "orient":20},
    "ATL": {"name":"Truist Park",           "lat":33.8908, "lon":-84.4678, "dome":False,  "retractable":False, "orient":25},
    "MIA": {"name":"loanDepot Park",        "lat":25.7781, "lon":-80.2197, "dome":True,   "retractable":True,  "orient":40},
    "WSH": {"name":"Nationals Park",        "lat":38.8730, "lon":-77.0074, "dome":False,  "retractable":False, "orient":30},
    "PIT": {"name":"PNC Park",              "lat":40.4469, "lon":-80.0057, "dome":False,  "retractable":False, "orient":115},
    "CIN": {"name":"Great American Ball Park","lat":39.0979,"lon":-84.5082,"dome":False,  "retractable":False, "orient":105},
    "CHC": {"name":"Wrigley Field",         "lat":41.9484, "lon":-87.6553, "dome":False,  "retractable":False, "orient":40},
    "CWS": {"name":"Guaranteed Rate Field", "lat":41.8300, "lon":-87.6338, "dome":False,  "retractable":False, "orient":75},
    "STL": {"name":"Busch Stadium",         "lat":38.6226, "lon":-90.1928, "dome":False,  "retractable":False, "orient":10},
    "MIL": {"name":"American Family Field", "lat":43.0280, "lon":-87.9712, "dome":True,   "retractable":True,  "orient":50},
    "MIN": {"name":"Target Field",          "lat":44.9817, "lon":-93.2781, "dome":False,  "retractable":False, "orient":80},
    "DET": {"name":"Comerica Park",         "lat":42.3390, "lon":-83.0485, "dome":False,  "retractable":False, "orient":25},
    "CLE": {"name":"Progressive Field",     "lat":41.4962, "lon":-81.6852, "dome":False,  "retractable":False, "orient":5},
    "KC":  {"name":"Kauffman Stadium",      "lat":39.0517, "lon":-94.4803, "dome":False,  "retractable":False, "orient":60},
    "TEX": {"name":"Globe Life Field",      "lat":32.7473, "lon":-97.0822, "dome":True,   "retractable":True,  "orient":70},
    "HOU": {"name":"Minute Maid Park",      "lat":29.7573, "lon":-95.3555, "dome":True,   "retractable":True,  "orient":20},
    "LAA": {"name":"Angel Stadium",         "lat":33.8003, "lon":-117.8827,"dome":False,  "retractable":False, "orient":45},
    "LAD": {"name":"Dodger Stadium",        "lat":34.0739, "lon":-118.2400,"dome":False,  "retractable":False, "orient":30},
    "SF":  {"name":"Oracle Park",           "lat":37.7786, "lon":-122.3893,"dome":False,  "retractable":False, "orient":100},
    "SD":  {"name":"Petco Park",            "lat":32.7076, "lon":-117.1570,"dome":False,  "retractable":False, "orient":0},
    "COL": {"name":"Coors Field",           "lat":39.7559, "lon":-104.9942,"dome":False,  "retractable":False, "orient":0},
    "ARI": {"name":"Chase Field",           "lat":33.4453, "lon":-112.0667,"dome":True,   "retractable":True,  "orient":25},
    "SEA": {"name":"T-Mobile Park",         "lat":47.5914, "lon":-122.3325,"dome":True,   "retractable":True,  "orient":65},
    "OAK": {"name":"Oakland Coliseum",      "lat":37.7516, "lon":-122.2005,"dome":False,  "retractable":False, "orient":60},
    "TB":  {"name":"Tropicana Field",       "lat":27.7683, "lon":-82.6534, "dome":True,   "retractable":False, "orient":45},
    "TOR": {"name":"Rogers Centre",         "lat":43.6414, "lon":-79.3894, "dome":True,   "retractable":True,  "orient":0},
    "BAL": {"name":"Camden Yards",          "lat":39.2838, "lon":-76.6218, "dome":False,  "retractable":False, "orient":30},
}

# Wind direction interpretation for HR props — relative to the park's real outfield.
def wind_hr_impact(wind_deg, wind_speed, cf_bearing=65):
    """Wind impact on HRs, relative to the park's actual outfield orientation.
    cf_bearing = compass bearing (deg, 0=N, 90=E) from home plate to center field.
    Returns (label, color, score) with score in -2..+2."""
    if not wind_speed or wind_speed < 5:
        return "Calm", "#94a3b8", 0
    try:
        wind_deg = float(wind_deg); wind_speed = float(wind_speed); cf = float(cf_bearing)
    except (TypeError, ValueError):
        return f"Wind {wind_speed:.0f}mph", "#94a3b8", 0

    wind_to  = (wind_deg + 180) % 360                 # direction the wind blows TOWARD
    diff     = ((wind_to - cf + 180) % 360) - 180     # -180..180; + = toward RF side, - = toward LF
    out_comp = math.cos(math.radians(diff))           # +1 straight out, -1 straight in, 0 = pure cross

    def field(d):
        return "CF" if abs(d) <= 22 else ("RF" if d > 0 else "LF")

    if out_comp >= 0.35:                              # blowing OUT toward the outfield
        fld = field(diff)
        mag = out_comp * wind_speed
        if mag >= 12: return f"Wind {wind_speed:.0f}mph out to {fld}", "#4ade80", 2
        if mag >= 6:  return f"Wind {wind_speed:.0f}mph out to {fld}", "#86efac", 1
        return f"Light wind out to {fld} {wind_speed:.0f}mph", "#94a3b8", 0
    if out_comp <= -0.35:                             # blowing IN from the outfield
        fdiff = ((wind_deg - cf + 180) % 360) - 180   # name the field the wind comes FROM
        fld = field(fdiff)
        mag = -out_comp * wind_speed
        if mag >= 12: return f"Wind {wind_speed:.0f}mph in from {fld}", "#f87171", -2
        if mag >= 6:  return f"Wind {wind_speed:.0f}mph in from {fld}", "#fb923c", -1
        return f"Light wind in from {fld} {wind_speed:.0f}mph", "#94a3b8", 0
    lr = "L\u2192R" if diff > 0 else "R\u2192L"        # crosswind (neutral for carry)
    return f"Cross wind {wind_speed:.0f}mph ({lr})", "#facc15", 0


def get_weather_rating(temp_f, wind_label, wind_score, conditions, dome):
    """Overall HR weather rating"""
    if dome:
        return "Dome &#127968;", "#60a5fa", "Indoor — weather not a factor"

    score = wind_score
    if temp_f >= 80: score += 1
    elif temp_f <= 50: score -= 1

    if "rain" in conditions.lower() or "drizzle" in conditions.lower():
        score -= 2

    if score >= 2:
        return "Great &#128293;", "#4ade80", "Ideal HR conditions"
    elif score == 1:
        return "Good &#9989;", "#86efac", "Favorable for HRs"
    elif score == 0:
        return "Neutral &#9898;", "#94a3b8", "Normal conditions"
    elif score == -1:
        return "Caution &#9888;", "#fb923c", "Slightly suppressed"
    else:
        return "Avoid &#10060;", "#f87171", "HR suppressing conditions"


def get_game_weather(home_team_abb):
    """Fetch current weather for a stadium"""
    stadium = STADIUMS.get(home_team_abb)
    if not stadium:
        return None

    if stadium.get('dome'):
        return {
            "stadium":    stadium['name'],
            "dome":       True,
            "retractable": stadium.get('retractable', False),
            "rating":     "Dome &#127968;",
            "rating_col": "#60a5fa",
            "rating_desc": "Indoor — weather irrelevant",
            "temp_f":     72,
            "conditions": "Indoor",
            "wind_label": "N/A",
            "wind_col":   "#94a3b8",
            "wind_score": 0,
            "humidity":   50,
        }

    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude":            stadium['lat'],
            "longitude":           stadium['lon'],
            "current":             "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code,precipitation",
            "wind_speed_unit":     "mph",
            "temperature_unit":    "fahrenheit",
            "forecast_days":       1,
        }
        r = requests.get(url, params=params, timeout=8)
        if r.status_code != 200:
            return None

        data    = r.json()
        current = data.get('current', {})

        temp_f     = current.get('temperature_2m', 70)
        wind_speed = current.get('wind_speed_10m', 0)
        wind_deg   = current.get('wind_direction_10m', 180)
        humidity   = current.get('relative_humidity_2m', 50)
        wcode      = current.get('weather_code', 0)

        # Weather code to condition
        if wcode == 0:   conditions = "Clear &#9728;"
        elif wcode <= 3: conditions = "Partly Cloudy &#9925;"
        elif wcode <= 49: conditions = "Foggy &#127787;"
        elif wcode <= 67: conditions = "Rainy &#127783;"
        elif wcode <= 77: conditions = "Snow &#10052;"
        elif wcode <= 82: conditions = "Showers &#127783;"
        else:             conditions = "Stormy &#26928;"

        wind_label, wind_col, wind_score = wind_hr_impact(
            wind_deg, wind_speed, stadium.get('orient', 65))
        rating, rating_col, rating_desc = get_weather_rating(
            temp_f, wind_label, wind_score, conditions, False)

        return {
            "stadium":     stadium['name'],
            "dome":        False,
            "retractable": False,
            "rating":      rating,
            "rating_col":  rating_col,
            "rating_desc": rating_desc,
            "temp_f":      round(temp_f),
            "conditions":  conditions,
            "wind_label":  wind_label,
            "wind_col":    wind_col,
            "wind_score":  wind_score,
            "humidity":    round(humidity),
        }

    except Exception as e:
        print(f"  Weather error for {home_team_abb}: {e}")
        return None


def get_all_weather(games):
    """Fetch weather for all tonight's games"""
    weather = {}
    for g in games:
        home_abb = g.get('home_abb','')
        if home_abb:
            print(f"  Weather: {home_abb}...")
            w = get_game_weather(home_abb)
            if w:
                weather[home_abb] = w
    return weather
