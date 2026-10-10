#!/usr/bin/env python3
"""
nba_source_test.py  —  Diamond Analytics: NBA data-source connectivity test.

Purpose: run this FROM A GITHUB ACTIONS RUNNER (same environment your app's
data pulls run in) to learn which NBA data source actually works from where
Diamond lives. The answer decides the whole NBA model.

It tests, in order:
  1. ESPN hidden API      — schedule/scores, teams, injuries, player stats
  2. stats.nba.com        — the rich one (pace, defensive rating, matchup D)
  3. balldontlie          — clean averages (now needs a free API key)

Stdlib only (urllib) so it runs instantly with no pip install.
Prints a clear PASS/FAIL report. Copy the whole log and send it back.
"""

import json
import gzip
import io
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timezone, timedelta
import os

# Eastern "today" — matches how the MLB side thinks about a slate
ET = timezone(timedelta(hours=-4))
TODAY = datetime.now(ET).date()
TODAY_ESPN = TODAY.strftime("%Y%m%d")      # ESPN wants YYYYMMDD
TODAY_NBA  = TODAY.strftime("%Y-%m-%d")     # stats.nba.com wants YYYY-MM-DD

BDL_KEY = os.environ.get("BALLDONTLIE_KEY", "")  # optional free key via secret


def _fetch(url, headers=None, timeout=15, attempts=1):
    """Return (status, seconds, body_text, err). Handles gzip. Never raises."""
    headers = headers or {}
    headers.setdefault("Accept-Encoding", "gzip, identity")
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
            # read a bit of the error body for context
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            if i == attempts - 1:
                return e.code, round(time.time() - t0, 2), body, last_err
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            if i == attempts - 1:
                return 0, round(time.time() - t0, 2), "", last_err
        time.sleep(2.0 * (i + 1))   # backoff: 2s, 4s, 6s …
    return 0, 0.0, "", last_err


def _sample(body, n=240):
    s = body.strip().replace("\n", " ")
    return (s[:n] + " …") if len(s) > n else s


def line(label, status, secs, note=""):
    ok = "✅ PASS" if (isinstance(status, int) and 200 <= status < 300) else "❌ FAIL"
    print(f"{ok}  {label:38s} HTTP {status:<4} {secs:>5}s  {note}")


print("=" * 72)
print(f"NBA DATA-SOURCE TEST  —  run date (ET): {TODAY}")
print("=" * 72)

# ── 1. ESPN hidden API ────────────────────────────────────────────────
print("\n[1] ESPN hidden API  (no key — schedule, teams, injuries, stats)")
print("-" * 72)

ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 1a. scoreboard / today's slate
s, t, b, e = _fetch(
    f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={TODAY_ESPN}",
    headers=ua)
ngames = ""
try:
    ngames = f"{len(json.loads(b).get('events', []))} games today" if 200 <= s < 300 else e
except Exception:
    ngames = e or "parse error"
line("scoreboard (today's slate)", s, t, ngames)

# 1b. teams
s, t, b, e = _fetch(
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams", headers=ua)
nteams = ""
try:
    nteams = f"{len(json.loads(b)['sports'][0]['leagues'][0]['teams'])} teams" if 200 <= s < 300 else e
except Exception:
    nteams = e or "parse error"
line("teams list", s, t, nteams)

# 1c. injuries  (THE make-or-break feed for NBA props)
s, t, b, e = _fetch(
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries", headers=ua)
line("injuries feed", s, t, (_sample(b, 120) if 200 <= s < 300 else e))

# 1d. a player's game log / stats (LeBron = 1966, sample athlete)
s, t, b, e = _fetch(
    "https://site.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/1966/gamelog",
    headers=ua)
line("player gamelog (sample athlete)", s, t, (_sample(b, 120) if 200 <= s < 300 else e))

# ── 2. stats.nba.com ──────────────────────────────────────────────────
print("\n[2] stats.nba.com  (rich: pace, def rating, matchup D — blocks many cloud IPs)")
print("-" * 72)

nba_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# 2a. scoreboard (basic connectivity, 2 attempts because it's flaky)
s, t, b, e = _fetch(
    f"https://stats.nba.com/stats/scoreboardv2?GameDate={TODAY_NBA}&LeagueID=00&DayOffset=0",
    headers=nba_headers, timeout=30, attempts=4)
line("scoreboardv2 (connectivity)", s, t, (_sample(b, 90) if 200 <= s < 300 else e))

# 2b. the ADVANCED one we actually want: team pace + defensive rating
s, t, b, e = _fetch(
    "https://stats.nba.com/stats/leaguedashteamstats?"
    "Conference=&DateFrom=&DateTo=&Division=&GameScope=&GameSegment=&Height=&"
    "LastNGames=0&LeagueID=00&Location=&MeasureType=Advanced&Month=0&"
    "OpponentTeamID=0&Outcome=&PaceAdjust=N&PerMode=PerGame&Period=0&"
    "PlayerExperience=&PlayerPosition=&PlusMinus=N&Rank=N&Season=2025-26&"
    "SeasonSegment=&SeasonType=Regular+Season&ShotClockRange=&StarterBench=&"
    "TeamID=0&TwoWay=0&VsConference=&VsDivision=",
    headers=nba_headers, timeout=30, attempts=4)
line("leaguedashteamstats (PACE + DEF RTG)", s, t, (_sample(b, 90) if 200 <= s < 300 else e))

# ── 3. balldontlie ────────────────────────────────────────────────────
print("\n[3] balldontlie  (clean averages — now requires a free API key)")
print("-" * 72)
bdl_headers = {"User-Agent": "Mozilla/5.0"}
if BDL_KEY:
    bdl_headers["Authorization"] = BDL_KEY
s, t, b, e = _fetch("https://api.balldontlie.io/v1/teams", headers=bdl_headers)
note = ("key present" if BDL_KEY else "NO KEY SET — 401 expected, that's informative")
line(f"teams ({note})", s, t, (_sample(b, 90) if 200 <= s < 300 else e))

print("\n" + "=" * 72)
print("DONE. Copy EVERYTHING above and send it back.")
print("Legend: ✅=works from this runner, ❌=blocked/needs key/down.")
print("=" * 72)
