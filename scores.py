#!/usr/bin/env python3
"""
scores.py — live scores for MLB, NBA, NFL via ESPN's FREE public scoreboard.

Same data source nba.py / nfl.py already use for schedules — no key, no paid
requests. A short in-process cache means one ESPN call per league/date serves all
viewers for the cache window (polite on the API). Normalized to a clean shape the
dashboard renders, with a `last_updated` timestamp and a `stale` flag so the UI
never shows old scores as if they were live.

Does NOT touch predictions, odds, or the Best Edges engine. Pure data proxy.
"""

import io
import gzip
import json
import time
import urllib.request

ESPN = {
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "mlb": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
}
LEAGUES = tuple(ESPN.keys())
UA = {"User-Agent": "Mozilla/5.0 (DiamondAnalytics scores)"}
CACHE_TTL = 15          # seconds a fetched scoreboard is reused (API-friendly)
_SEASON_TYPE = {1: "preseason", 2: "regular", 3: "postseason", 4: "offseason"}

_cache = {}             # key -> {"ts": epoch, "data": normalized}


# ───────────────────────────── fetch ──────────────────────────────────
def _fetch_json(url, timeout=8, attempts=2):
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={**UA, "Accept-Encoding": "gzip, identity"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            if i == attempts - 1:
                return None
            time.sleep(0.6)
    return None


def scoreboard_url(league, date=None, week=None):
    url = ESPN[league]
    params = []
    if week and league == "nfl":
        params.append("week={}".format(int(week)))
    elif date:
        params.append("dates={}".format(str(date).replace("-", "")))      # YYYYMMDD
    if params:
        url += "?" + "&".join(params)
    return url


# ─────────────────────────── normalize ────────────────────────────────
def _num(x):
    try:
        return None if x in (None, "") else int(float(x))
    except Exception:
        return None


def _state(status):
    t = (status or {}).get("type") or {}
    st = (t.get("state") or "").lower()
    name = (t.get("name") or "").upper()
    if "POSTPONED" in name:
        return "postponed"
    if "CANCEL" in name:
        return "canceled"
    if "SUSPEND" in name:
        return "suspended"
    if st == "in":
        return "live"
    if st == "post":
        return "final"
    if st == "pre":
        return "scheduled"
    return st or "scheduled"


def _team(c):
    tm = c.get("team") or {}
    rec = ""
    recs = c.get("records") or []
    if recs and isinstance(recs, list):
        rec = (recs[0] or {}).get("summary") or ""
    return {
        "abbr": tm.get("abbreviation") or tm.get("shortDisplayName") or "",
        "name": tm.get("displayName") or tm.get("name") or "",
        "logo": tm.get("logo") or "",
        "score": _num(c.get("score")),
        "record": rec,
        "winner": bool(c.get("winner")),
    }


def normalize(espn, league, requested_date=None):
    """Pure: ESPN scoreboard JSON -> normalized dict. Never raises."""
    out = {"league": league, "source": "ESPN", "date": requested_date,
           "week": None, "season_type": None, "games": []}
    if not isinstance(espn, dict):
        return out
    wk = (espn.get("week") or {})
    out["week"] = wk.get("number")
    season = (espn.get("season") or {}) or ((espn.get("leagues") or [{}])[0].get("season") or {})
    season_type = season.get("type")
    if isinstance(season_type, dict):
        season_type = season_type.get("type", season_type.get("id"))
    if isinstance(season_type, str) and season_type.isdigit():
        season_type = int(season_type)
    out["season_type"] = _SEASON_TYPE.get(season_type)
    for ev in espn.get("events", []):
        try:
            comp = (ev.get("competitions") or [{}])[0]
            status = comp.get("status") or ev.get("status") or {}
            cs = comp.get("competitors", [])
            home = next((c for c in cs if c.get("homeAway") == "home"), {})
            away = next((c for c in cs if c.get("homeAway") == "away"), {})
            stype = (ev.get("season") or {}).get("type")
            t = status.get("type") or {}
            out["games"].append({
                "id": ev.get("id"),
                "state": _state(status),
                "detail": t.get("shortDetail") or t.get("description") or "",
                "completed": bool(t.get("completed")),
                "start_utc": ev.get("date"),
                "period": status.get("period"),
                "clock": status.get("displayClock"),
                "competition": _SEASON_TYPE.get(stype),
                "home": _team(home),
                "away": _team(away),
                "note": ((comp.get("notes") or [{}])[0] or {}).get("headline") or "",
            })
        except Exception:
            continue
    return out


# ───────────────────────── cached public API ──────────────────────────
def get_scores(league, date=None, week=None, now=None, fetch=None):
    """Return normalized scores + last_updated + stale. Uses a short cache; on an
    ESPN failure, returns the last good data flagged stale (never fabricated)."""
    league = (league or "").lower()
    if league not in ESPN:
        return {"error": "unknown league", "league": league, "games": [], "stale": True}
    now = now or time.time()
    fetch = fetch or (lambda: _fetch_json(scoreboard_url(league, date, week)))
    key = (league, date, week)
    cached = _cache.get(key)

    if cached and (now - cached["ts"]) < CACHE_TTL:
        d = dict(cached["data"])
        d["last_updated"] = _iso(cached["ts"]); d["stale"] = False
        d["age_seconds"] = round(now - cached["ts"], 1)
        return d

    espn = fetch()
    if espn is None:
        if cached:                                   # serve last good, flagged stale
            d = dict(cached["data"])
            d["last_updated"] = _iso(cached["ts"]); d["stale"] = True
            d["age_seconds"] = round(now - cached["ts"], 1)
            d["error"] = "scores source unavailable — showing last update"
            return d
        return {"error": "scores source unavailable", "league": league,
                "games": [], "stale": True, "last_updated": None}

    data = normalize(espn, league, date)
    _cache[key] = {"ts": now, "data": data}
    d = dict(data)
    d["last_updated"] = _iso(now); d["stale"] = False; d["age_seconds"] = 0.0
    return d


def _iso(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    import sys
    lg = sys.argv[1] if len(sys.argv) > 1 else "nba"
    print(json.dumps(get_scores(lg), indent=2)[:1500])
