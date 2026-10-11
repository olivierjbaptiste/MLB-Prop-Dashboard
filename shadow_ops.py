#!/usr/bin/env python3
"""
shadow_ops.py — Phase 4: safe orchestration for automated shadow capture/grading.

Wraps the existing recorders and graders with the reliability the scheduled jobs
need:
  • Capture only genuine pregame games — decided by each game's ACTUAL start time
    (the recorders gate per game), never a fixed clock. Safe to run many times a
    day; duplicates are de-duped.
  • Fail safe — if a required service or snapshot is missing/stale, STOP that
    operation, write a clear failure to the run log, and PRESERVE existing records.
    Never fabricate statistics or prices.
  • Dry-run — compute and report what WOULD be recorded without writing.
  • Run log — every attempt appends a status line (checkpoint) so you can see
    whether NFL and NBA tracking ran.

Each run-status line: {ts, sport, action, outcome, rows_added, pregame_games,
reason, error}. outcome ∈ ok | dry_run | refused | failed.
Pure orchestration; no network at import. UTC throughout.
"""

import os
import json
import traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_LOG = os.path.join(HERE, "shadow_runs.jsonl")


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_run(entry, path=RUN_LOG):
    entry = dict(entry)
    entry.setdefault("ts", _iso(_now()))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _pregame_count(data, now, is_pregame):
    return sum(1 for g in data.get("games", []) if is_pregame(g, now))


# ───────────────────────────── NBA ────────────────────────────────────
def capture_nba(data, now=None, params=None, log_path=RUN_LOG, dry_run=False):
    """Project v1+v2, validate, and record pregame NBA predictions (or dry-run).
    `data` is a raw snapshot dict; projection happens here."""
    import nba_model
    import nba_model_v2
    import nba_record as R
    now = now or _now()
    try:
        if not data or not data.get("players"):
            return log_run({"sport": "nba", "action": "capture", "outcome": "refused",
                            "rows_added": 0, "reason": "no snapshot data"}, log_path)
        nba_model.project_all(data)
        nba_model_v2.project_all_v2(data)
        ok, reasons = R.validate_snapshot(data, now)
        pg = _pregame_count(data, now, lambda g, n: not R._tipped(g, n))
        if not ok:
            return log_run({"sport": "nba", "action": "capture", "outcome": "refused",
                            "rows_added": 0, "pregame_games": pg,
                            "reason": "; ".join(reasons)}, log_path)
        recs = R.build_records(data, now, params)
        if dry_run:
            return log_run({"sport": "nba", "action": "capture", "outcome": "dry_run",
                            "rows_added": 0, "would_record": len(recs),
                            "pregame_games": pg}, log_path)
        added, skipped = R.append_records(recs)
        return log_run({"sport": "nba", "action": "capture", "outcome": "ok",
                        "rows_added": added, "already_logged": skipped,
                        "pregame_games": pg,
                        "params_loaded": params is not None}, log_path)
    except Exception as e:
        return log_run({"sport": "nba", "action": "capture", "outcome": "failed",
                        "rows_added": 0, "error": "{}: {}".format(type(e).__name__, e),
                        "trace": traceback.format_exc()[-500:]}, log_path)


def grade_nba(actuals_path, log_path=RUN_LOG, dry_run=False):
    import nba_grade as G
    try:
        records = G.load_log()
        actuals = G.load_actuals(actuals_path)
        if not records:
            return log_run({"sport": "nba", "action": "grade", "outcome": "refused",
                            "reason": "no predictions logged"}, log_path)
        if not actuals:
            return log_run({"sport": "nba", "action": "grade", "outcome": "refused",
                            "reason": "no actuals (results not final / CSV missing)"}, log_path)
        rep = G.grade(records, actuals)
        if dry_run:
            return log_run({"sport": "nba", "action": "grade", "outcome": "dry_run",
                            "props_graded": len(rep.get("by_prop", {}))}, log_path)
        os.makedirs(G.OUT_DIR, exist_ok=True)
        with open(os.path.join(G.OUT_DIR, "nba_grade_report.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
        return log_run({"sport": "nba", "action": "grade", "outcome": "ok",
                        "props_graded": len(rep.get("by_prop", {}))}, log_path)
    except Exception as e:
        return log_run({"sport": "nba", "action": "grade", "outcome": "failed",
                        "error": "{}: {}".format(type(e).__name__, e)}, log_path)


# ───────────────────────────── NFL ────────────────────────────────────
def capture_nfl(data, now=None, log_path=RUN_LOG, dry_run=False):
    import nfl_model
    import record_predictions as RP
    now = now or _now()
    try:
        if not data or not data.get("players"):
            return log_run({"sport": "nfl", "action": "capture", "outcome": "refused",
                            "rows_added": 0, "reason": "no snapshot data"}, log_path)
        nfl_model.project_all(data)
        ok, reasons = RP.validate_snapshot(data, now)
        pg = _pregame_count(data, now, RP._game_is_pregame)
        if not ok:
            return log_run({"sport": "nfl", "action": "capture", "outcome": "refused",
                            "rows_added": 0, "pregame_games": pg,
                            "reason": "; ".join(reasons)}, log_path)
        recs = RP.build_records(data, nfl_model.VERSION, now) + RP.build_v2_records(data, now)
        if dry_run:
            return log_run({"sport": "nfl", "action": "capture", "outcome": "dry_run",
                            "rows_added": 0, "would_record": len(recs),
                            "pregame_games": pg}, log_path)
        added, skipped = RP.append_records(recs)
        return log_run({"sport": "nfl", "action": "capture", "outcome": "ok",
                        "rows_added": added, "already_logged": skipped,
                        "pregame_games": pg}, log_path)
    except Exception as e:
        return log_run({"sport": "nfl", "action": "capture", "outcome": "failed",
                        "rows_added": 0, "error": "{}: {}".format(type(e).__name__, e)}, log_path)


# ───────────────────── live entrypoints (read snapshots) ──────────────
def run_live(sport, action, dry_run=False, actuals_path=None):
    """CLI entrypoint. Reads the committed live snapshot and runs the operation.
    If the snapshot/service is unavailable, logs a failure and preserves records."""
    if sport == "nba" and action == "capture":
        import nba, nba_record
        return capture_nba(nba.read_snapshot() or {}, params=nba_record.load_params(),
                           dry_run=dry_run)
    if sport == "nfl" and action == "capture":
        import nfl
        return capture_nfl(nfl.read_snapshot() or {}, dry_run=dry_run)
    if sport == "nba" and action == "grade":
        return grade_nba(actuals_path, dry_run=dry_run)
    raise SystemExit("usage: shadow_ops.py <nba|nfl> <capture|grade> [--dry-run] [actuals.csv]")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    sport = args[0] if args else "nba"
    action = args[1] if len(args) > 1 else "capture"
    actuals = args[2] if len(args) > 2 else None
    print(json.dumps(run_live(sport, action, dry_run=dry, actuals_path=actuals), indent=2))
