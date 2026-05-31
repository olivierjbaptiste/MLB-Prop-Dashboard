"""
odds_free.py
Free HR prop odds - multiple source fallback
"""

import requests
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_dk_events():
    """Get today's MLB event IDs from DraftKings"""
    try:
        url = "https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240"
        r = requests.get(url, headers=HEADERS, timeout=12)
        print(f"  DK events status: {r.status_code}")
        if r.status_code != 200:
            return []
        data = r.json()
        # Log structure so we can see what comes back
        top_keys = list(data.keys())
        print(f"  DK top keys: {top_keys}")
        eg = data.get("eventGroup", {})
        print(f"  eventGroup keys: {list(eg.keys())[:8]}")
        events = eg.get("events", [])
        print(f"  Events found: {len(events)}")
        return [e.get("eventId") for e in events if e.get("eventId")]
    except Exception as e:
        print(f"  DK events error: {e}")
        return []


def get_dk_hr_props(event_ids):
    """Get HR props for specific events"""
    props = []
    # Try multiple category/subcategory combos
    combos = [
        ("743",  "4519"),  # Batter props / HR
        ("743",  "6004"),
        ("583",  "6004"),
        ("1000", "4519"),
        ("1000", "6004"),
    ]
    for cat, sub in combos:
        try:
            url = f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240/categories/{cat}/subcategories/{sub}"
            r = requests.get(url, headers=HEADERS, timeout=12)
            print(f"  DK cat {cat}/sub {sub}: status {r.status_code}")
            if r.status_code != 200:
                continue
            data = r.json()
            eg   = data.get("eventGroup", {})
            cats = eg.get("offerCategories", [])
            print(f"  offerCategories: {len(cats)}")
            for c in cats:
                for sub_desc in c.get("offerSubcategoryDescriptors", []):
                    sub_cat = sub_desc.get("offerSubcategory", {})
                    offers  = sub_cat.get("offers", [])
                    print(f"  Offers in subcategory: {len(offers)}")
                    for offer_group in offers:
                        for offer in offer_group:
                            label = offer.get("label","").lower()
                            if "home run" in label or "hr" in label or "to hit" in label:
                                for outcome in offer.get("outcomes",[]):
                                    player = outcome.get("participant","") or outcome.get("label","")
                                    odds_str = outcome.get("oddsAmerican","")
                                    line   = outcome.get("line", 0.5)
                                    side   = outcome.get("label","Over")
                                    if player:
                                        try: odds_int = int(str(odds_str).replace("+",""))
                                        except: odds_int = -110
                                        props.append({
                                            "player": player,
                                            "line":   float(line) if line else 0.5,
                                            "odds":   odds_int,
                                            "book":   "draftkings",
                                            "side":   side,
                                        })
            if props:
                print(f"  Found {len(props)} HR props with cat {cat}/sub {sub}")
                return props
        except Exception as e:
            print(f"  DK cat {cat}/sub {sub} error: {e}")
    return props


def get_dk_by_event(event_ids):
    """Try getting props by individual event ID"""
    props = []
    for eid in event_ids[:5]:
        try:
            url = f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/events/{eid}/offers/583"
            r = requests.get(url, headers=HEADERS, timeout=10)
            print(f"  DK event {eid}: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"  Event keys: {list(data.keys())[:5]}")
                # Parse offers
                for offer in data.get("offers", []):
                    label = offer.get("label","").lower()
                    if "home run" in label or "hr" in label:
                        for oc in offer.get("outcomes",[]):
                            player = oc.get("participant","") or oc.get("label","")
                            if player:
                                props.append({
                                    "player": player,
                                    "line":   0.5,
                                    "odds":   int(oc.get("oddsAmerican","-110") or -110),
                                    "book":   "draftkings",
                                    "side":   oc.get("label","Over"),
                                })
        except Exception as e:
            print(f"  Event {eid} error: {e}")
    return props


def get_fanduel_props():
    """FanDuel public API"""
    props = []
    try:
        # FanDuel uses this format for MLB player props
        url = "https://sbapi.fanduel.com/api/sport-event-tabs?betOfferCategoryId=PLAYER_BATTER_HR&competitionId=american-baseball&_ak=FhMFpcPWXMeyZxOx"
        r = requests.get(url, headers=HEADERS, timeout=12)
        print(f"  FanDuel status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"  FD keys: {list(data.keys())[:5]}")
            runners = data.get("attachments",{}).get("runners",{})
            markets = data.get("attachments",{}).get("markets",{})
            print(f"  FD runners: {len(runners)}, markets: {len(markets)}")
            for rid, runner in runners.items():
                mid    = str(runner.get("marketId",""))
                market = markets.get(mid,{})
                mname  = market.get("marketName","").lower()
                if "home run" not in mname and "hr" not in mname:
                    continue
                player = runner.get("runnerName","")
                sp     = runner.get("winRunnerOdds",{}).get("americanDisplayOdds",{}).get("americanOddsInt")
                if player and sp is not None:
                    props.append({
                        "player": player,
                        "line":   0.5,
                        "odds":   int(sp),
                        "book":   "fanduel",
                        "side":   "Over",
                    })
    except Exception as e:
        print(f"  FanDuel error: {e}")
    return props


def get_free_props():
    """Main entry — tries all sources"""
    print("  Loading free HR prop lines...")
    all_props = []

    # DraftKings via category endpoint
    dk = get_dk_hr_props([])
    all_props.extend(dk)

    if not all_props:
        # Try via event IDs
        event_ids = get_dk_events()
        if event_ids:
            dk2 = get_dk_by_event(event_ids)
            all_props.extend(dk2)

    # FanDuel
    fd = get_fanduel_props()
    all_props.extend(fd)

    print(f"  Total free props: {len(all_props)}")
    return all_props
