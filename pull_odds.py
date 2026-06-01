#!/usr/bin/env python3
"""Daily HR-prop odds pull for the MLB dashboard.

Runs once a day in GitHub Actions using the ODDS_API_KEY repository secret and
writes odds_cache.json, which the live app reads. The app itself never spends
credits (no cold-start leak).

Player props only exist for games that HAVEN'T started yet, so we pull only
upcoming events. Cost: ~1 credit per event. MAX_EVENTS=12 -> ~12/day -> ~360/mo,
well under the 500/month free tier.
"""
import os
import sys
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY    = os.environ.get("ODDS_API_KEY", "").strip()
MAX_EVENTS = 12
BASE       = "https://api.the-odds-api.com/v4/sports/baseball_mlb"


def _get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode())


def _is_upcoming(ev):
    ct = ev.get("commence_time")
    if not ct:
        return True
    try:
        start = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        return start > datetime.now(timezone.utc)
    except Exception:
        return True


def main():
    if not API_KEY:
        print("ERROR: ODDS_API_KEY is not set.")
        sys.exit(1)

    try:
        events = _get(f"{BASE}/events?apiKey={urllib.parse.quote(API_KEY)}")
    except Exception as e:
        print(f"ERROR: could not fetch events: {e}")
        sys.exit(1)

    if not isinstance(events, list):
        print(f"ERROR: unexpected events response: {events}")
        sys.exit(1)

    upcoming = [e for e in events if _is_upcoming(e)]
    upcoming.sort(key=lambda e: e.get("commence_time", ""))
    print(f"Events total: {len(events)} | upcoming: {len(upcoming)} | pulling: {min(len(upcoming), MAX_EVENTS)}")

    props = []
    pulled = 0
    for ev in upcoming[:MAX_EVENTS]:
        try:
            q = urllib.parse.urlencode({
                "apiKey":     API_KEY,
                "regions":    "us",
                "markets":    "batter_home_runs",
                "oddsFormat": "american",
            })
            data = _get(f"{BASE}/events/{ev['id']}/odds?{q}")
            pulled += 1
            books = data.get("bookmakers", [])
            market_keys = sorted({mk.get("key") for bk in books for mk in bk.get("markets", [])})
            before = len(props)
            for bk in books:
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
            print(f"  {ev.get('away_team','?')} @ {ev.get('home_team','?')} "
                  f"start={ev.get('commence_time','?')} books={len(books)} "
                  f"markets={market_keys or 'none'} +{len(props)-before} props")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            print(f"  event {ev.get('id','?')} HTTP {e.code}: {body}")
        except Exception as e:
            print(f"  event {ev.get('id','?')} failed: {e}")

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
