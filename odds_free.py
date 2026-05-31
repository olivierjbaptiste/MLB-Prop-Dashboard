"""
odds_free.py
Free HR prop odds from DraftKings and FanDuel public APIs
No API key required - uses their public sportsbook endpoints
"""

import requests
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_dk_props():
    """
    Pull HR prop lines from DraftKings public sportsbook API.
    Uses their publicly accessible endpoint — no key needed.
    """
    props = []
    try:
        # Step 1: Get today's MLB events from DraftKings
        events_url = "https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240"
        r = requests.get(events_url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            print(f"  DK events error: {r.status_code}")
            return []

        data = r.json()
        event_group = data.get("eventGroup", {})
        events = []
        for eg in event_group.get("offerCategories", []):
            for sub in eg.get("offerSubcategoryDescriptors", []):
                for offer in sub.get("offerSubcategory", {}).get("offers", []):
                    for o in offer:
                        events.append(o)

        # Step 2: Get player prop category (Home Runs)
        # DK category 743 = Batter Props, subcategory 4519 = Home Runs
        props_url = "https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240/categories/743/subcategories/4519"
        rp = requests.get(props_url, headers=HEADERS, timeout=12)

        if rp.status_code == 200:
            pdata = rp.json()
            # Parse the offer structure
            for cat in pdata.get("eventGroup", {}).get("offerCategories", []):
                for sub in cat.get("offerSubcategoryDescriptors", []):
                    offers_list = sub.get("offerSubcategory", {}).get("offers", [])
                    for offer_group in offers_list:
                        for offer in offer_group:
                            label = offer.get("label", "")
                            if "home run" not in label.lower() and "hr" not in label.lower():
                                continue
                            for outcome in offer.get("outcomes", []):
                                player = outcome.get("participant", "")
                                line   = outcome.get("line", 0.5)
                                odds   = outcome.get("oddsAmerican", "")
                                side   = outcome.get("label", "Over")
                                if player and odds:
                                    try:
                                        odds_int = int(odds)
                                    except:
                                        odds_int = -110
                                    props.append({
                                        "player": player,
                                        "line":   float(line) if line else 0.5,
                                        "odds":   odds_int,
                                        "book":   "draftkings",
                                        "side":   side,
                                    })
            print(f"  DraftKings: {len(props)} HR props loaded")
        else:
            print(f"  DK props error: {rp.status_code}")
            # Try alternate category IDs
            for cat_id, sub_id in [("583", "6004"), ("743", "6004"), ("1000", "4519")]:
                alt_url = f"https://sportsbook.draftkings.com/sites/US-SB/api/v5/eventgroups/84240/categories/{cat_id}/subcategories/{sub_id}"
                ra = requests.get(alt_url, headers=HEADERS, timeout=10)
                print(f"  Alt {cat_id}/{sub_id}: {ra.status_code}")
                if ra.status_code == 200:
                    print(f"  Found data with cat {cat_id}/sub {sub_id}")
                    break

    except Exception as e:
        print(f"  DraftKings error: {e}")

    return props


def get_fd_props():
    """
    Pull HR prop lines from FanDuel public API.
    """
    props = []
    try:
        # FanDuel public API for MLB player props
        url = "https://sbapi.fanduel.com/api/sport-event-tabs?betOfferCategoryId=PLAYER_BATTER_HR&competitionId=american-baseball"
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200:
            # Try alternate FD endpoint
            url2 = "https://sbapi.fanduel.com/api/tab-page-components?_ak=FhMFpcPWXMeyZxOx&tab=player-props&competitionId=americal-baseball&limit=25"
            r = requests.get(url2, headers=HEADERS, timeout=12)

        if r.status_code == 200:
            data = r.json()
            # Parse FanDuel structure
            events = data.get("attachments", {}).get("events", {})
            markets = data.get("attachments", {}).get("markets", {})
            runners = data.get("attachments", {}).get("runners", {})

            for runner_id, runner in runners.items():
                market_id = str(runner.get("marketId", ""))
                market = markets.get(market_id, {})
                market_name = market.get("marketName", "").lower()
                if "home run" not in market_name and "to hit a hr" not in market_name:
                    continue
                player = runner.get("runnerName", "")
                side   = runner.get("handicap", "")
                sp     = runner.get("winRunnerOdds", {}).get("americanDisplayOdds", {}).get("americanOddsInt")
                if player and sp is not None:
                    props.append({
                        "player": player,
                        "line":   0.5,
                        "odds":   int(sp),
                        "book":   "fanduel",
                        "side":   "Over",
                    })
            print(f"  FanDuel: {len(props)} HR props loaded")
        else:
            print(f"  FanDuel error: {r.status_code}")

    except Exception as e:
        print(f"  FanDuel error: {e}")

    return props


def get_free_props():
    """
    Main entry point — tries DraftKings then FanDuel.
    Returns combined list of HR props.
    """
    print("  Loading free HR prop lines...")
    all_props = []

    dk = get_dk_props()
    all_props.extend(dk)

    fd = get_fd_props()
    all_props.extend(fd)

    print(f"  Total free props loaded: {len(all_props)}")
    return all_props
