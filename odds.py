#!/usr/bin/env python3
"""
odds.py — Diamond Analytics: live sportsbook player-prop lines (The Odds API).

Used only by the data pull (on the runner, where ODDS_API_KEY lives as a
secret). It fetches player props one event at a time, aggregates a consensus
line + de-vigged fair probability across books, and the best available price
for line-shopping. The result is stored in the snapshot, so the edge math in
nfl_model.py runs at request time with NO further API calls — that's what
keeps credit use predictable.

Credit model (The Odds API): per-event endpoint costs
[markets returned] x [regions]. One US-region pull of the 5 markets below is
~5 credits per game, ~80 for a full NFL slate. Safe to skip entirely: with no
key set, this returns {} and the pipeline simply shows no edges.

Stdlib only. Defensive parsing — a book or market missing never raises.
"""

import os
import json
import time
import urllib.request
import urllib.error
from statistics import median

# Prefer ODDS_API_KEY2 (the NFL/NBA edges key), fall back to ODDS_API_KEY so
# this works whichever secret name holds the Odds API key.
ODDS_API_KEY = os.environ.get("ODDS_API_KEY2") or os.environ.get("ODDS_API_KEY", "")
BASE = "https://api.the-odds-api.com/v4"

SPORT = {"nfl": "americanfootball_nfl", "nba": "basketball_nba"}
REGIONS = "us"

# The Odds API market key -> our internal prop field
MARKET_TO_PROP = {
    "player_pass_yds":      "pass_yds",
    "player_rush_yds":      "rush_yds",
    "player_reception_yds": "rec_yds",
    "player_receptions":    "rec",
    "player_anytime_td":    "anytime_td",
}
NFL_MARKETS = list(MARKET_TO_PROP.keys())
OU_PROPS = {"pass_yds", "rush_yds", "rec_yds", "rec"}   # Over/Under with a point


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "diamond/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        remaining = r.headers.get("x-requests-remaining")
        used = r.headers.get("x-requests-used")
        body = json.loads(r.read().decode("utf-8", "replace"))
        return body, remaining, used


def _am_to_prob(odds):
    """American odds -> implied probability (with vig)."""
    try:
        o = float(odds)
    except Exception:
        return None
    return (-o) / ((-o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


def _devig(p_a, p_b):
    """Two implied probs -> vig-free fair probs (normalize to 1)."""
    if p_a is None or p_b is None:
        return None, None
    s = p_a + p_b
    if s <= 0:
        return None, None
    return p_a / s, p_b / s


def _norm(name):
    return (name or "").strip().lower()


def get_events(sport_key):
    """Event list is free (0 credits). Returns [] on any failure."""
    if not ODDS_API_KEY:
        return []
    try:
        data, _, _ = _get(f"{BASE}/sports/{sport_key}/events?apiKey={ODDS_API_KEY}")
        return data or []
    except Exception as e:
        print(f"    odds: events fetch failed → {type(e).__name__}")
        return []


def _event_props(sport_key, event_id, markets):
    url = (f"{BASE}/sports/{sport_key}/events/{event_id}/odds?apiKey={ODDS_API_KEY}"
           f"&regions={REGIONS}&markets={','.join(markets)}&oddsFormat=american")
    return _get(url)


def _aggregate_event(data, lines):
    """Fold one event's bookmaker odds into `lines[player][prop]`.

    Each stored opportunity keeps CONCRETE per-book offers — (book, line, both
    prices, timestamp) that actually exist together at one sportsbook. We no
    longer glue a consensus line onto the best price from a different book, so
    EV is always computed against a real, bettable offer.
    """
    event_id = data.get("id")
    home, away = data.get("home_team"), data.get("away_team")
    tmp = {}   # (player, prop) -> [offer, ...]
    for bk in data.get("bookmakers", []):
        book = bk.get("title") or bk.get("key")
        for mk in bk.get("markets", []):
            prop = MARKET_TO_PROP.get(mk.get("key"))
            if not prop:
                continue
            last_update = mk.get("last_update")
            byplayer = {}
            for oc in mk.get("outcomes", []):
                player = _norm(oc.get("description") or oc.get("name"))
                side = (oc.get("name") or "").strip().lower()
                if not oc.get("description"):
                    side = "yes"          # anytime_td shape with player in name only
                byplayer.setdefault(player, {})[side] = oc
            for player, sides in byplayer.items():
                key = (player, prop)
                if prop in OU_PROPS:
                    ov, un = sides.get("over"), sides.get("under")
                    if ov and un and ov.get("point") is not None:
                        tmp.setdefault(key, []).append({
                            "book": book, "line": float(ov["point"]),
                            "over_price": ov.get("price"),
                            "under_price": un.get("price"),
                            "ts": last_update,
                        })
                else:  # anytime_td (Yes/No)
                    yes = sides.get("yes") or sides.get("over")
                    no  = sides.get("no")  or sides.get("under")
                    if yes:
                        tmp.setdefault(key, []).append({
                            "book": book,
                            "yes_price": yes.get("price"),
                            "no_price": no.get("price") if no else None,
                            "ts": last_update,
                        })
    for (player, prop), offers in tmp.items():
        dst = lines.setdefault(player, {})
        if prop in OU_PROPS:
            fairs = []
            for o in offers:
                fo, _ = _devig(_am_to_prob(o["over_price"]), _am_to_prob(o["under_price"]))
                if fo is not None:
                    fairs.append(fo)
            dst[prop] = {
                "consensus_line": round(median([o["line"] for o in offers]) * 2) / 2.0,
                "fair_over": round(median(fairs), 4) if fairs else None,
                "offers": offers,            # concrete per-book (book, line, prices, ts)
                "books": len(offers),
                "event_id": event_id, "home": home, "away": away,
            }
        else:
            fairs = []
            for o in offers:
                py, pn = _am_to_prob(o["yes_price"]), _am_to_prob(o.get("no_price"))
                if pn is not None:
                    fy, _ = _devig(py, pn)
                    if fy is not None:
                        fairs.append(fy)
                elif py is not None:
                    fairs.append(py)
            dst["anytime_td"] = {
                "fair_yes": round(median(fairs), 4) if fairs else None,
                "offers": offers,
                "books": len(offers),
                "event_id": event_id, "home": home, "away": away,
            }


def fetch_lines(sport="nfl", markets=None, event_cap=0):
    """Return {player_lower: {prop: {...}}} plus credits remaining. {} with no
    key, so callers can treat edges as an optional layer."""
    if not ODDS_API_KEY:
        print("    odds: no ODDS_API_KEY set — skipping edges")
        return {}, None
    sport_key = SPORT.get(sport, sport)
    markets = markets or NFL_MARKETS
    events = get_events(sport_key)
    if not events:
        return {}, None
    lines = {}
    remaining = None
    n = 0
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        try:
            data, remaining, _ = _event_props(sport_key, eid, markets)
        except urllib.error.HTTPError as e:
            if e.code == 422:   # event has no props yet (too early) — skip quietly
                continue
            print(f"    odds: event {eid[:8]} HTTP {e.code}")
            continue
        except Exception as e:
            print(f"    odds: event {eid[:8]} {type(e).__name__}")
            continue
        if data:
            _aggregate_event(data, lines)
        n += 1
        if event_cap and n >= event_cap:
            break
        time.sleep(0.1)
    print(f"    odds: {len(lines)} players priced across {n} events "
          f"(credits remaining: {remaining})")
    return lines, remaining


if __name__ == "__main__":
    import sys
    sport = sys.argv[1] if len(sys.argv) > 1 else "nfl"
    lines, rem = fetch_lines(sport)
    print(f"\n{len(lines)} players priced. Credits remaining: {rem}")
    for i, (pl, props) in enumerate(lines.items()):
        if i >= 8:
            break
        print(f"  {pl}: {json.dumps(props)[:160]}")
