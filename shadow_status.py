#!/usr/bin/env python3
"""
shadow_status.py — Phase 4: did NFL/NBA shadow tracking run? Health at a glance.

Reads the run log (shadow_runs.jsonl) and the prediction logs, and reports, per
sport: the last capture/grade attempt and its outcome, how many predictions are
recorded (by model version), how many are still ungraded, and any recent
failures. Read-only. Run it any time:  python shadow_status.py
"""

import os
import json
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_LOG = os.path.join(HERE, "shadow_runs.jsonl")
LOGS = {"nfl": os.path.join(HERE, "predictions_log.jsonl"),
        "nba": os.path.join(HERE, "nba_predictions_log.jsonl")}
GRADES = {"nfl": os.path.join(HERE, "grades", "grade_report.json"),
          "nba": os.path.join(HERE, "grades", "nba_grade_report.json")}


def _read_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out


def status():
    runs = _read_jsonl(RUN_LOG)
    rep = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sports": {}}
    for sport in ("nfl", "nba"):
        preds = _read_jsonl(LOGS[sport])
        by_ver = {}
        graded_keys = set()
        for r in preds:
            by_ver[r.get("model_version")] = by_ver.get(r.get("model_version"), 0) + 1
        srun = [r for r in runs if r.get("sport") == sport]
        last_cap = next((r for r in reversed(srun) if r.get("action") == "capture"), None)
        last_grade = next((r for r in reversed(srun) if r.get("action") == "grade"), None)
        fails = [r for r in srun if r.get("outcome") == "failed"][-3:]
        rep["sports"][sport] = {
            "predictions_total": len(preds),
            "by_model_version": by_ver,
            "last_capture": last_cap,
            "last_grade": last_grade,
            "recent_failures": fails,
            "grade_report_exists": os.path.exists(GRADES[sport]),
        }
    return rep


def _print(rep):
    print("Shadow tracking status @ {}".format(rep["generated_at"]))
    for sport, s in rep["sports"].items():
        print("\n[{}]  predictions: {}  ({})".format(
            sport.upper(), s["predictions_total"],
            ", ".join("{}={}".format(k, v) for k, v in s["by_model_version"].items()) or "none"))
        for label, r in (("last capture", s["last_capture"]), ("last grade", s["last_grade"])):
            if r:
                print("  {}: {} @ {}  (+{} rows){}".format(
                    label, r.get("outcome"), r.get("ts"), r.get("rows_added", 0),
                    "  reason=" + r["reason"] if r.get("reason") else ""))
            else:
                print("  {}: never run".format(label))
        if s["recent_failures"]:
            print("  ⚠ recent failures: {}".format(
                "; ".join(f.get("error", "?") for f in s["recent_failures"])))
        print("  grade report present: {}".format(s["grade_report_exists"]))


if __name__ == "__main__":
    _print(status())
