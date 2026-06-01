#!/usr/bin/env python3
"""Daily results logging + grading for the MLB HR dashboard.

Run by the GitHub Action (after pull_odds.py). Maintains results_log.json:
  - Logs the day's top picks (with their odds) once per day.
  - Grades earlier ungraded days against real MLB home-run results.

The app reads results_log.json and shows an auto-graded Model Track Record.
This script NEVER raises out — a failure here must not break the Action.
"""
import os
import sys
import json
from datetime import date

LOG_FILE     = "results_log.json"
TOP_N        = 10      # number of the day's picks to track
UNIT_LOSS    = -1.0    # 1 unit staked per pick


def american_profit(odds, units=1.0):
    """Profit in units on a winning bet at American odds."""
    try:
        o = float(odds)
    except Exception:
        return 0.0
    if o > 0:
        return units * (o / 100.0)
    if o < 0:
        return units * (100.0 / abs(o))
    return 0.0


def load_log():
    try:
        with open(LOG_FILE) as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            return d
    except Exception:
        pass
    return {"entries": []}


def save_log(log):
    try:
        with open(LOG_FILE, "w") as f:
            json.dump(log, f)
        print(f"Wrote {LOG_FILE}: {len(log.get('entries', []))} day(s).")
    except Exception as e:
        print(f"  save error: {e}")


def hr_hitters_for_date(dstr, dashboard):
    """Lowercased set of player names who homered on date dstr (YYYY-MM-DD)."""
    import urllib.request
    names = set()
    try:
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={dstr}"
        with urllib.request.urlopen(url, timeout=20) as r:
            sched = json.loads(r.read().decode())
        gids = []
        for de in sched.get("dates", []):
            for g in de.get("games", []):
                status = g.get("status", {}).get("detailedState", "")
                if "Final" in status or "Completed" in status:
                    gids.append(g.get("gamePk"))
        for gid in gids:
            for h in dashboard.get_game_hr_leaders(gid):
                nm = (h.get("name") or "").strip().lower()
                if nm:
                    names.add(nm)
    except Exception as e:
        print(f"  grading fetch error for {dstr}: {e}")
    return names


def grade_entry(entry, dashboard):
    names = hr_hitters_for_date(entry.get("date", ""), dashboard)
    if not names:
        print(f"  no HR data for {entry.get('date')} yet — leaving ungraded")
        return False
    wins = 0
    for p in entry.get("picks", []):
        hit = (p.get("player", "").strip().lower() in names)
        p["outcome"] = "WIN" if hit else "LOSS"
        p["pl"] = round(american_profit(p.get("odds", -110)) if hit else UNIT_LOSS, 3)
        wins += 1 if hit else 0
    entry["graded"] = True
    print(f"  graded {entry.get('date')}: {wins}/{len(entry.get('picks', []))} hit")
    return True


def todays_picks(dashboard):
    key = os.environ.get("ODDS_API_KEY", "")
    data = dashboard.build_all_data(key)
    out = []
    for p in (data.get("top_picks", []) or [])[:TOP_N]:
        out.append({
            "player":     p.get("name", ""),
            "team":       p.get("team", ""),
            "pitcher":    p.get("pitcher", ""),
            "confidence": p.get("confidence", ""),
            "line":       p.get("prop_line"),
            "odds":       p.get("prop_odds"),
            "edge":       p.get("edge_pct"),
        })
    return out


def main():
    today = date.today().strftime("%Y-%m-%d")
    log = load_log()

    try:
        import dashboard
    except Exception as e:
        print(f"could not import dashboard: {e}")
        save_log(log)
        return

    # 1) Grade past ungraded days (never today's — games not done yet)
    for entry in log["entries"]:
        if not entry.get("graded") and entry.get("date") != today:
            try:
                grade_entry(entry, dashboard)
            except Exception as e:
                print(f"  grade error for {entry.get('date')}: {e}")

    # 2) Log today's picks once
    if not any(e.get("date") == today for e in log["entries"]):
        try:
            picks = todays_picks(dashboard)
            if picks:
                log["entries"].insert(0, {"date": today, "picks": picks, "graded": False})
                print(f"  logged {len(picks)} picks for {today}")
            else:
                print("  no picks generated for today — nothing logged")
        except Exception as e:
            print(f"  pick logging error: {e}")
    else:
        print(f"  {today} already logged — skipping")

    # Keep the log from growing without bound (last ~120 days)
    log["entries"] = log["entries"][:120]
    save_log(log)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"log_results fatal (ignored): {e}")
    sys.exit(0)  # never fail the Action
