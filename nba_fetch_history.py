#!/usr/bin/env python3
"""
nba_fetch_history.py — Phase 3A: dump per-game NBA history to nba_history.csv.

RUN THIS ON A MACHINE THAT CAN REACH ESPN (your Windows box, or the GitHub
Action). ESPN is blocked from the Claude workspace, so the real backtest numbers
come from running this, then `nba_backtest.py` on the CSV it writes.

Unlike nba.get_player_log (which pools every game into season averages and keeps
no dates), this keeps ONE ROW PER PLAYER-GAME with the game date, opponent, and
season type — exactly what leak-free backtesting needs. It also de-dupes by
event id, so a player's game is never counted twice across ESPN stat categories.

Usage (PowerShell):
    python nba_fetch_history.py --seasons 2024 2025 2026 --out nba_history.csv
    python nba_fetch_history.py --seasons 2025 --cap 40        # quick sample
    python nba_fetch_history.py --diagnose 1966                # dump one gamelog shape

`--seasons` are NBA end-years: 2024 = the 2023-24 season, 2025 = 2024-25, etc.
No paid APIs. Polite delays between calls. Stdlib only.
"""

import sys
import csv
import time
import argparse

import nba   # reuse its _fetch_json, get_teams, get_team_roster, _num

COLS = ["player_id", "player", "season", "season_type", "game_date",
        "team", "opponent", "home", "started", "min", "pts", "reb", "ast", "tpm"]


def _season_type(display):
    d = (display or "").lower()
    if "regular" in d:
        return "REG"
    if "post" in d or "playoff" in d:
        return "POST"
    if "pre" in d:
        return "PRE"
    return "OTHER"


def _season_end_year(display, fallback):
    """'2024-25 Regular Season' -> 2025 (end year). Falls back to the queried season."""
    import re
    m = re.search(r"(\d{4})-(\d{2})", display or "")
    if m:
        return int(m.group(1)) + 1
    m = re.search(r"(\d{4})", display or "")
    return int(m.group(1)) if m else fallback


def _col_index(data):
    names = [str(n).lower() for n in (data.get("names") or [])]
    labels = [str(l).upper() for l in (data.get("labels") or [])]

    def idx(name_opts, label_opts):
        for n in name_opts:
            if n in names:
                return names.index(n)
        for l in label_opts:
            if l in labels:
                return labels.index(l)
        return None
    return {
        "min": idx(["minutes"], ["MIN"]),
        "pts": idx(["points"], ["PTS"]),
        "reb": idx(["rebounds", "totalrebounds"], ["REB"]),
        "ast": idx(["assists"], ["AST"]),
        "tpm": idx(["threepointfieldgoalsmade", "threepointfieldgoals"], ["3PT"]),
    }


def parse_gamelog(data, athlete_id, player_name, queried_season):
    """ESPN athlete gamelog JSON -> list of per-game rows (de-duped by event)."""
    if not data:
        return []
    ci = _col_index(data)
    events_meta = data.get("events") or {}
    rows, seen = [], set()
    for stype in (data.get("seasonTypes") or []):
        st = _season_type(stype.get("displayName"))
        season = _season_end_year(stype.get("displayName"), queried_season)
        for cat in (stype.get("categories") or []):
            for ev in (cat.get("events") or []):
                eid = ev.get("eventId") or ev.get("id")
                stats = ev.get("stats")
                if not isinstance(stats, list) or not stats:
                    continue
                dedupe = (season, st, eid)
                if eid and dedupe in seen:        # same game across categories
                    continue
                if eid:
                    seen.add(dedupe)
                meta = events_meta.get(str(eid), {}) if eid else {}
                opp = (meta.get("opponent") or {})
                def cell(k):
                    i = ci[k]
                    return nba._num(stats[i]) if (i is not None and i < len(stats)) else None
                rows.append({
                    "player_id": athlete_id, "player": player_name,
                    "season": season, "season_type": st,
                    "game_date": (meta.get("gameDate") or meta.get("date") or "")[:10],
                    "team": (meta.get("team") or {}).get("abbreviation") or "",
                    "opponent": opp.get("abbreviation") or "",
                    "home": 1 if meta.get("homeAway") == "home" else (0 if meta.get("homeAway") else ""),
                    "started": "",     # ESPN gamelog does not expose a start flag
                    "min": cell("min"), "pts": cell("pts"), "reb": cell("reb"),
                    "ast": cell("ast"), "tpm": cell("tpm"),
                })
    return rows


def athlete_ids(cap=0):
    """Current-roster athlete ids across all 30 teams (the prop population)."""
    teams = nba.get_teams()
    ids = []
    for tid in teams:
        for p in nba.get_team_roster(tid):
            ids.append((p["id"], p["name"]))
            if cap and len(ids) >= cap:
                return ids
        time.sleep(0.2)
    return ids


def fetch(seasons, cap=0, out="nba_history.csv"):
    ids = athlete_ids(cap)
    print("players to fetch: {}  seasons: {}".format(len(ids), seasons))
    all_rows, done = [], 0
    for aid, name in ids:
        for season in seasons:
            url = "{}/athletes/{}/gamelog?season={}".format(nba.ESPN_COMMON, aid, season)
            data = nba._fetch_json(url, quiet=True)
            all_rows.extend(parse_gamelog(data, aid, name, season))
            time.sleep(0.25)       # polite to ESPN
        done += 1
        if done % 25 == 0:
            print("  {}/{} players, {} rows so far".format(done, len(ids), len(all_rows)))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)
    played = sum(1 for r in all_rows if r["min"] and r["min"] > 0)
    print("\nwrote {} rows ({} played) -> {}".format(len(all_rows), played, out))
    if played == 0:
        print("  No played games parsed. Run with --diagnose <athleteId> and send "
              "the output back so the parser can be locked to ESPN's shape.")
    return out


def diagnose(athlete_id):
    """Dump one gamelog's structure so the parser can be corrected if ESPN's
    shape differs (same round-trip pattern nba.py uses)."""
    import json
    url = "{}/athletes/{}/gamelog".format(nba.ESPN_COMMON, athlete_id)
    data = nba._fetch_json(url)
    if not data:
        print("No data (ESPN unreachable from here?). URL:", url)
        return
    print("top-level keys:", list(data.keys()))
    print("labels:", data.get("labels"))
    print("names:", data.get("names"))
    sts = data.get("seasonTypes") or []
    print("seasonTypes:", [s.get("displayName") for s in sts])
    if sts:
        cat = (sts[0].get("categories") or [{}])[0]
        evs = cat.get("events") or []
        print("first category events:", len(evs))
        if evs:
            print("sample event keys:", list(evs[0].keys()))
            print("sample stats row:", evs[0].get("stats"))
            eid = evs[0].get("eventId") or evs[0].get("id")
            meta = (data.get("events") or {}).get(str(eid), {})
            print("sample event meta keys:", list(meta.keys()))
            print("  gameDate:", meta.get("gameDate") or meta.get("date"),
                  "| opponent:", (meta.get("opponent") or {}).get("abbreviation"),
                  "| homeAway:", meta.get("homeAway"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    ap.add_argument("--cap", type=int, default=0)
    ap.add_argument("--out", default="nba_history.csv")
    ap.add_argument("--diagnose", type=str, default=None,
                    help="athlete id: dump one gamelog's shape and exit")
    args = ap.parse_args()
    if args.diagnose:
        diagnose(args.diagnose)
    else:
        fetch(args.seasons, args.cap, args.out)
