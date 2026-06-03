#!/usr/bin/env python3
"""
pull_stats.py — run once a day by the GitHub Action.

Does the heavy fetching (live batter stats, live pitcher stats, Baseball Savant)
ONE time and writes committed JSON snapshots next to dashboard.py. The deployed
app then reads those snapshots instead of fetching at request time, so page loads
are fast and don't depend on statsapi/Savant being reachable.

Safety: a failed/empty fetch never overwrites a good existing snapshot.
"""
import dashboard as d

# Force fresh live fetches (bypass the snapshot-read shortcuts in the loaders).
d._FORCE_LIVE = True


def main():
    wrote = []

    # 1) Live batter season stats (raw; overlay is applied in-app, cheaply)
    try:
        batters = d.load_live_batters()
    except Exception as e:
        batters = []
        print("  batters fetch error:", e)
    if batters:
        d._write_snapshot("live_batters.json", {"batters": batters})
        wrote.append(f"live_batters.json ({len(batters)} batters)")
    else:
        print("  No live batters fetched — keeping existing snapshot")

    # 2) Savant batted-ball map (HH%, EV, barrel%, launch angle, xSLG)
    try:
        sav = d.load_savant_statcast()
    except Exception as e:
        sav = {"by_id": {}, "by_name": {}}
        print("  Savant batter fetch error:", e)
    if sav.get("by_id") or sav.get("by_name"):
        d._write_snapshot("savant_batters.json",
                          {"by_id": sav.get("by_id", {}), "by_name": sav.get("by_name", {})})
        wrote.append(f"savant_batters.json ({len(sav.get('by_id', {}))} players)")
    else:
        print("  No Savant batter data — keeping existing snapshot")

    # 3) Live pitcher pool (real season stats + Savant contact-allowed overlay)
    try:
        pitchers = d.load_live_pitchers()
    except Exception as e:
        pitchers = []
        print("  pitchers fetch error:", e)
    if pitchers:
        d._write_snapshot("live_pitchers.json", {"pitchers": pitchers})
        wrote.append(f"live_pitchers.json ({len(pitchers)} pitchers)")
    else:
        print("  No live pitchers fetched — keeping existing snapshot")

    # 4) Pitch-arsenal: hitter SLG by pitch type + pitcher usage by pitch type
    try:
        pbat = d.load_pitch_arsenal_batters()
    except Exception as e:
        pbat = {"by_id": {}}
        print("  pitch-arsenal batters fetch error:", e)
    if pbat.get("by_id"):
        d._write_snapshot("pitch_bat.json", {"by_id": pbat["by_id"]})
        wrote.append(f"pitch_bat.json ({len(pbat['by_id'])} hitters)")
    else:
        print("  No pitch-arsenal batter data — keeping existing snapshot")

    try:
        ppit = d.load_pitch_arsenal_pitchers()
    except Exception as e:
        ppit = {"by_id": {}}
        print("  pitch-arsenal pitchers fetch error:", e)
    if ppit.get("by_id"):
        d._write_snapshot("pitch_pit.json", {"by_id": ppit["by_id"]})
        wrote.append(f"pitch_pit.json ({len(ppit['by_id'])} pitchers)")
    else:
        print("  No pitch-arsenal pitcher data — keeping existing snapshot")

    print("Snapshots written:", ", ".join(wrote) if wrote else "none")


if __name__ == "__main__":
    main()
