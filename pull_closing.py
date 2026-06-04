#!/usr/bin/env python3
"""Closing-line pull for CLV (closing line value).

Runs a SECOND time each day, ~30-60 min before first pitch. For the picks we
already logged this morning (the "open" line), it re-fetches the current HR-prop
odds (the "close") and records closing odds + CLV onto each pick in
results_log.json. The next day's grading then has both numbers.

Budget: scoped to ONLY the games we have picks in (not the full slate), so it
adds ~1 credit per picked game. Pair with a trimmed morning MAX_EVENTS to stay
under the 500/month free tier (see note at bottom).

CLV convention (HR "over"/yes bets): you BEAT THE CLOSE when the line shortened
after you bet — i.e. the closing implied probability is higher than the implied
probability at the price you logged. clv_pct = (close_implied - open_implied)*100.
"""
import os
import sys
import json
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

API_KEY     = os.environ.get("ODDS_API_KEY", "").strip()
MAX_CLOSING = 10                      # cap on events fetched at close (credit guard)
BASE        = "https://api.the-odds-api.com/v4/sports/baseball_mlb"

# Pick team abbreviations -> the-odds-api full team names (for scoping events).
TEAM_NAMES = {
    "NYY":"New York Yankees","NYM":"New York Mets","BOS":"Boston Red Sox",
    "PHI":"Philadelphia Phillies","ATL":"Atlanta Braves","MIA":"Miami Marlins",
    "WSH":"Washington Nationals","PIT":"Pittsburgh Pirates","CIN":"Cincinnati Reds",
    "CHC":"Chicago Cubs","CWS":"Chicago White Sox","STL":"St. Louis Cardinals",
    "MIL":"Milwaukee Brewers","MIN":"Minnesota Twins","DET":"Detroit Tigers",
    "CLE":"Cleveland Guardians","KC":"Kansas City Royals","TEX":"Texas Rangers",
    "HOU":"Houston Astros","LAA":"Los Angeles Angels","LAD":"Los Angeles Dodgers",
    "SF":"San Francisco Giants","SD":"San Diego Padres","COL":"Colorado Rockies",
    "ARI":"Arizona Diamondbacks","SEA":"Seattle Mariners","OAK":"Athletics",
    "TB":"Tampa Bay Rays","TOR":"Toronto Blue Jays","BAL":"Baltimore Orioles",
}


def _get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def _norm(name):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    n = " ".join(n.replace(".", "").split())
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if n.endswith(suf):
            n = n[: -len(suf)]
    return n.strip()


def _implied(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    return 100.0 / (o + 100.0) if o > 0 else (-o) / (-o + 100.0)


def _is_upcoming(ev):
    ct = ev.get("commence_time")
    if not ct:
        return False
    try:
        return datetime.fromisoformat(ct.replace("Z", "+00:00")) > datetime.now(timezone.utc)
    except Exception:
        return False


def _et_today():
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def main():
    if not API_KEY:
        print("ERROR: ODDS_API_KEY is not set."); sys.exit(1)

    try:
        with open("results_log.json") as f:
            log = json.load(f)
    except Exception as e:
        print(f"No results_log.json to update ({e})."); return

    entries = log.get("entries", [])
    today = _et_today()
    entry = next((e for e in entries if e.get("date") == today and not e.get("graded")), None)
    if entry is None:
        entry = next((e for e in reversed(entries) if not e.get("graded")), None)
    if entry is None or not entry.get("picks"):
        print("No ungraded picks to price at close."); return

    picks = entry["picks"]
    want_players = {_norm(p.get("player", "")) for p in picks}
    want_teams   = {TEAM_NAMES.get(p.get("team", "")) for p in picks if p.get("team")}
    want_teams.discard(None)

    try:
        events = _get(f"{BASE}/events?apiKey={urllib.parse.quote(API_KEY)}")
    except Exception as e:
        print(f"ERROR: could not fetch events: {e}"); sys.exit(1)
    if not isinstance(events, list):
        print(f"ERROR: unexpected events response: {events}"); sys.exit(1)

    # Only the upcoming games we actually have picks in (keeps credit cost low).
    scoped = [e for e in events if _is_upcoming(e)
              and (e.get("home_team") in want_teams or e.get("away_team") in want_teams)]
    scoped.sort(key=lambda e: e.get("commence_time", ""))
    scoped = scoped[:MAX_CLOSING]
    print(f"Closing pull: {len(scoped)} picked games still upcoming "
          f"(of {len([e for e in events if _is_upcoming(e)])} upcoming).")

    # player -> best (most favorable) closing OVER odds across books
    close_best = {}
    pulled = 0
    for ev in scoped:
        try:
            q = urllib.parse.urlencode({
                "apiKey": API_KEY, "regions": "us",
                "markets": "batter_home_runs", "oddsFormat": "american",
            })
            data = _get(f"{BASE}/events/{ev['id']}/odds?{q}")
            pulled += 1
            for bk in data.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    if mk.get("key") != "batter_home_runs":
                        continue
                    for oc in mk.get("outcomes", []):
                        if (oc.get("name") or "").lower() not in ("over", "yes"):
                            continue
                        nm = _norm(oc.get("description", ""))
                        price = oc.get("price")
                        if nm and price is not None:
                            cur = close_best.get(nm)
                            if cur is None or _implied(price) < _implied(cur):  # better price = lower implied
                                close_best[nm] = price
        except urllib.error.HTTPError as e:
            print(f"  event {ev.get('id','?')} HTTP {e.code}")
        except Exception as e:
            print(f"  event {ev.get('id','?')} failed: {e}")

    updated = 0
    for p in picks:
        if p.get("odds") is None:
            continue                                   # no opening price -> CLV n/a
        close = close_best.get(_norm(p.get("player", "")))
        if close is None:
            continue
        oi, ci = _implied(p["odds"]), _implied(close)
        if oi is None or ci is None:
            continue
        p["close_odds"] = close
        p["clv_pct"]    = round((ci - oi) * 100, 1)
        p["beat_close"] = ci > oi
        updated += 1

    with open("results_log.json", "w") as f:
        json.dump(log, f)
    print(f"Closing pull done: priced {updated}/{len(picks)} picks at close "
          f"from {pulled} events.")


if __name__ == "__main__":
    main()

# Budget: morning pull (MAX_EVENTS) + this closing pull both cost ~1 credit/event.
# To stay under 500/month, trim pull_odds.py MAX_EVENTS to ~8 (=>~240/mo) and keep
# this scoped to picked games (~6/day => ~180/mo) — ~420/mo total.