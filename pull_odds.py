#!/usr/bin/env python3
"""Daily HR-prop odds pull for the MLB dashboard.

Runs once a day inside GitHub Actions using the ODDS_API_KEY repository secret,
and writes odds_cache.json which the live app reads. This keeps the deployed
app from ever spending Odds API credits itself (no cold-start credit leak).

Cost: ~1 credit per event. MAX_EVENTS=12 -> ~12 credits/day -> ~360/month,
comfortably under the 500/month free tier.
"""
import os
import sys
import json
import time
import urllib.parse
import urllib.request

API_KEY    = os.environ.get("ODDS_API_KEY", "").strip()
MAX_EVENTS = 12  # cap credits per run; raise toward 15 only if budget allows
BOOKS      = "draftkings,fanduel,betmgm"
BASE       = "https://api.the-odds-api.com/v4/sports/baseball_mlb"


def _get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    if not API_KEY:
        print("ERROR: ODDS_API_KEY is not set.")
        sys.exit(1)

    # The /events endpoint does not count against the quota.
    try:
        events = _get(f"{BASE}/events?apiKey={urllib.parse.quote(API_KEY)}")
    except Exception as e:
        print(f"ERROR: could not fetch events: {e}")
        sys.exit(1)

    if not isinstance(events, list):
        print(f"ERROR: unexpected events response: {events}")
        sys.exit(1)

    props = []
    pulled = 0
    for ev in events[:MAX_EVENTS]:
        try:
            q = urllib.parse.urlencode({
                "apiKey":     API_KEY,
                "markets":    "batter_home_runs",
                "oddsFormat": "american",
                "bookmakers": BOOKS,
            })
            data = _get(f"{BASE}/events/{ev['id']}/odds?{q}")
            pulled += 1
            for bk in data.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    if mk.get("key") == "batter_home_runs":
                        for oc in mk.get("outcomes", []):
                            props.append({
                                "player": oc.get("description", ""),
                                "line":   oc.get("point", 0.5),
                                "odds":   oc.get("price", -110),
                                "book":   bk.get("key", ""),
                                "side":   oc.get("name", ""),
                            })
        except Exception as e:
            print(f"  event {ev.get('id', '?')} failed: {e}")

    out = {
        "ts":            time.time(),
        "date":          time.strftime("%Y-%m-%d"),
        "events_pulled": pulled,
        "props":         props,
    }
    with open("odds_cache.json", "w") as f:
        json.dump(out, f)
    print(f"Wrote odds_cache.json: {len(props)} prop lines from {pulled} events.")


if __name__ == "__main__":
    main()
