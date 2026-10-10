#!/usr/bin/env python3
"""
nfl_source_test.py  —  Diamond Analytics: NFL data-source connectivity test.

Run this FROM A GITHUB ACTIONS RUNNER (same environment the app's data pulls
run in) to confirm which NFL data sources work before we build the model.

Tests, in order:
  1. ESPN hidden API   — scoreboard (+ ODDS: spread/total), teams, injuries,
                         player gamelog, team roster
  2. ESPN core API     — team statistics (offense; opponent/defense check)
  3. pro-football-reference — opponent stats page (positional defense source)

Stdlib only (urllib). Prints a clear PASS/FAIL report. Copy the whole log back.
"""

import json
import gzip
import io
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

ET = timezone(timedelta(hours=-4))
TODAY = datetime.now(ET).date()
SEASON_END = TODAY.year + 1 if TODAY.month >= 9 else TODAY.year  # NFL season spans two calendar years

ESPN  = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
CORE  = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
PFR   = "https://www.pro-football-reference.com"
UA    = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch(url, headers=None, timeout=20, attempts=2):
    """Return (status, seconds, body_text, err). Handles gzip. Never raises."""
    headers = {**UA, **(headers or {}), "Accept-Encoding": "gzip, identity"}
    last_err = ""
    for i in range(attempts):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return r.status, round(time.time() - t0, 2), raw.decode("utf-8", "replace"), ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if i == attempts - 1:
                return e.code, round(time.time() - t0, 2), "", last_err
        except Exception as e:
            last_err = f"{type(e).__name__}"
            if i == attempts - 1:
                return 0, round(time.time() - t0, 2), "", last_err
        time.sleep(2.0 * (i + 1))
    return 0, 0.0, "", last_err


def line(label, status, secs, note=""):
    ok = "PASS" if (isinstance(status, int) and 200 <= status < 300) else "FAIL"
    print(f"[{ok}]  {label:40s} HTTP {status:<4} {secs:>5}s  {note}")


print("=" * 74)
print(f"NFL DATA-SOURCE TEST  —  run date (ET): {TODAY}  (season end-year {SEASON_END})")
print("=" * 74)

# ── 1. ESPN ────────────────────────────────────────────────────────────
print("\n[1] ESPN hidden API  (scoreboard+odds, teams, injuries, gamelog, roster)")
print("-" * 74)

# scoreboard — this week's games, and crucially the ODDS (spread/total)
s, t, b, e = _fetch(f"{ESPN}/scoreboard")
note = e
odds_found = False
sample_team_id = None
sample_athlete_id = None
try:
    if 200 <= s < 300:
        d = json.loads(b)
        evs = d.get("events", [])
        note = f"{len(evs)} games"
        if evs:
            comp = (evs[0].get("competitions") or [{}])[0]
            # capture a team id for the roster test
            cs = comp.get("competitors", [])
            if cs:
                sample_team_id = (cs[0].get("team") or {}).get("id")
            odds = comp.get("odds")
            if odds:
                o0 = odds[0]
                odds_found = True
                note += f" | ODDS ok: spread='{o0.get('details')}' total={o0.get('overUnder')}"
            else:
                note += " | ODDS: none in scoreboard"
except Exception as ex:
    note = f"parse error: {ex}"
line("scoreboard (games + odds)", s, t, note)

# teams
s, t, b, e = _fetch(f"{ESPN}/teams")
nteams = e
try:
    if 200 <= s < 300:
        tms = json.loads(b)["sports"][0]["leagues"][0]["teams"]
        nteams = f"{len(tms)} teams"
        if not sample_team_id and tms:
            sample_team_id = tms[0]["team"]["id"]
except Exception as ex:
    nteams = f"parse error: {ex}"
line("teams list", s, t, nteams)

# injuries
s, t, b, e = _fetch(f"{ESPN}/injuries")
ninj = e
try:
    if 200 <= s < 300:
        blocks = json.loads(b).get("injuries", [])
        cnt = sum(len(tb.get("injuries", [])) for tb in blocks)
        ninj = f"{cnt} injury entries across {len(blocks)} teams"
except Exception as ex:
    ninj = f"parse error: {ex}"
line("injuries feed", s, t, ninj)

# roster (sample team) — also grab an athlete id for the gamelog test
if sample_team_id:
    s, t, b, e = _fetch(f"{ESPN}/teams/{sample_team_id}/roster")
    nros = e
    try:
        if 200 <= s < 300:
            aths = json.loads(b).get("athletes", [])
            flat = []
            for grp in aths:
                flat.extend(grp.get("items", []) if isinstance(grp, dict) and "items" in grp else [grp])
            nros = f"{len(flat)} players (team {sample_team_id})"
            if flat:
                sample_athlete_id = flat[0].get("id")
    except Exception as ex:
        nros = f"parse error: {ex}"
    line("team roster", s, t, nros)

# player gamelog  (Patrick Mahomes = 3139477 as a stable fallback)
aid = sample_athlete_id or "3139477"
s, t, b, e = _fetch(f"https://site.api.espn.com/apis/common/v3/sports/football/nfl/athletes/{aid}/gamelog")
ngl = e
try:
    if 200 <= s < 300:
        d = json.loads(b)
        ngl = f"gamelog ok (athlete {aid}); keys: {list(d.keys())[:6]}"
except Exception as ex:
    ngl = f"parse error: {ex}"
line("player gamelog", s, t, ngl)

# ── 2. ESPN core team statistics (offense + defense check) ─────────────
print("\n[2] ESPN core API  (team statistics — offense; check for opponent/def stats)")
print("-" * 74)
if sample_team_id:
    s, t, b, e = _fetch(f"{CORE}/seasons/{SEASON_END}/types/2/teams/{sample_team_id}/statistics")
    note = e
    try:
        if 200 <= s < 300:
            cats = ((json.loads(b).get("splits") or {}).get("categories")) or []
            names = []
            for c in cats:
                for st in (c.get("stats") or []):
                    names.append(str(st.get("name", "")).lower())
            opp = [n for n in names if "opp" in n or "against" in n or "allowed" in n]
            note = f"{len(names)} stats; opponent/def-named: {len(opp)}"
    except Exception as ex:
        note = f"parse error: {ex}"
    line(f"team statistics (szn {SEASON_END})", s, t, note)

# ── 3. pro-football-reference (positional defense source) ──────────────
print("\n[3] pro-football-reference  (opponent stats — positional defense)")
print("-" * 74)
s, t, b, e = _fetch(f"{PFR}/years/{SEASON_END}/opp.htm")
note = e
if 200 <= s < 300:
    has = "team_stats" in b or "advanced_defense" in b or "passing" in b.lower()
    note = f"{len(b)} bytes; looks like a stats page: {has}"
line("opponent stats page", s, t, note)

print("\n" + "=" * 74)
print("DONE. Copy EVERYTHING above and send it back.")
print("Key things to confirm: scoreboard ODDS present, gamelog ok, PFR reachable.")
print("=" * 74)
