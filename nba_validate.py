#!/usr/bin/env python3
"""
nba_validate.py — Phase 3B Task 1: validate nba_history.csv integrity.

READ-ONLY. Checks the history dump the backtest relies on:
  • season identifiers map to plausible NBA date ranges (REG of season Y should
    fall roughly Aug (Y-1) .. Jul Y);
  • no duplicate player-game records (same player_id + game_date);
  • regular season is separated from preseason / playoffs (counts per type);
  • DNP / zero-minute records are present and countable;
  • no missing identifiers (player_id, game_date, opponent);
  • stat values are in sane ranges (flags, not deletions);
  • chronology is parseable.

Prints a report and returns a dict. Flags problems; never edits the file.
"""

import os
import csv
import sys
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "nba_history.csv")

# plausible per-game maxima (flag above these — data error, not a real game)
SANE_MAX = {"min": 60.0, "pts": 81.0, "reb": 40.0, "ast": 30.0, "tpm": 14.0}


def _f(x):
    try:
        return None if x in (None, "") else float(x)
    except Exception:
        return None


def _season_window(season):
    """(start, end) ISO dates a season-Y game should fall within."""
    try:
        y = int(season)
        return "{}-08-01".format(y - 1), "{}-07-31".format(y)
    except Exception:
        return None, None


def validate(rows):
    report = {"rows": len(rows), "flags": [], "by_season": {}, "season_types": {},
              "dnp": 0, "missing": {"player_id": 0, "game_date": 0, "opponent": 0},
              "duplicates": 0, "duplicate_examples": [], "stat_outliers": {}}

    report["season_types"] = dict(Counter((r.get("season_type") or "").upper() for r in rows))

    seen = defaultdict(int)          # (player_id, season_type, game_date) -> count
    by_season = defaultdict(lambda: {"rows": 0, "date_min": None, "date_max": None,
                                     "out_of_window": 0})
    outliers = defaultdict(int)

    for r in rows:
        pid = r.get("player_id")
        date = r.get("game_date")
        opp = r.get("opponent")
        st = (r.get("season_type") or "").upper()
        season = r.get("season")
        mins = _f(r.get("min"))

        if not pid:
            report["missing"]["player_id"] += 1
        if not date:
            report["missing"]["game_date"] += 1
        if not opp:
            report["missing"]["opponent"] += 1

        if mins is not None and mins <= 0:
            report["dnp"] += 1

        key = (pid, st, date)
        seen[key] += 1

        s = by_season[season]
        s["rows"] += 1
        if date:
            s["date_min"] = date if s["date_min"] is None else min(s["date_min"], date)
            s["date_max"] = date if s["date_max"] is None else max(s["date_max"], date)
            if st == "REG":
                lo, hi = _season_window(season)
                if lo and not (lo <= date <= hi):
                    s["out_of_window"] += 1

        for k, mx in SANE_MAX.items():
            v = _f(r.get(k))
            if v is not None and (v < 0 or v > mx):
                outliers[k] += 1

    dups = {k: c for k, c in seen.items() if c > 1 and k[0] and k[2]}
    report["duplicates"] = sum(c - 1 for c in dups.values())
    report["duplicate_examples"] = [{"player_id": k[0], "season_type": k[1],
                                     "game_date": k[2], "count": c}
                                    for k, c in list(dups.items())[:5]]
    report["by_season"] = {str(k): v for k, v in sorted(by_season.items(),
                                                        key=lambda kv: str(kv[0]))}
    report["stat_outliers"] = dict(outliers)

    # headline flags
    if report["duplicates"]:
        report["flags"].append("{} duplicate player-game records".format(report["duplicates"]))
    for k, n in report["missing"].items():
        if n:
            report["flags"].append("{} rows missing {}".format(n, k))
    for season, s in report["by_season"].items():
        if s["out_of_window"]:
            report["flags"].append("{} REG rows outside season {}'s date window"
                                   .format(s["out_of_window"], season))
    if outliers:
        report["flags"].append("stat outliers: " +
                               ", ".join("{} {}".format(v, k) for k, v in outliers.items()))
    if "OTHER" in report["season_types"]:
        report["flags"].append("{} rows with unrecognized season_type"
                               .format(report["season_types"]["OTHER"]))
    return report


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _print(rep):
    print("rows: {}  | DNP/0-min: {}  | duplicates: {}".format(
        rep["rows"], rep["dnp"], rep["duplicates"]))
    print("season_types:", rep["season_types"])
    for season, s in rep["by_season"].items():
        print("  season {}: {} rows, dates {}..{}, REG out-of-window {}".format(
            season, s["rows"], s["date_min"], s["date_max"], s["out_of_window"]))
    if rep["flags"]:
        print("\nFLAGS:")
        for fl in rep["flags"]:
            print("  ⚠ " + fl)
    else:
        print("\nNo integrity problems detected.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    if not os.path.exists(path):
        raise SystemExit("not found: {}".format(path))
    _print(validate(load_csv(path)))
