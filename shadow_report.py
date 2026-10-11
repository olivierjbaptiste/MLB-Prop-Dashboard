#!/usr/bin/env python3
"""
shadow_report.py — Phase 3D Task 5: combined NFL + NBA v1/v2 shadow performance.

Reads the grade reports (NFL grade_shadow + NBA nba_grade) and renders ONE
comparison by sport, prop, and model version. It strictly separates three kinds
of evidence so nothing is overstated:

  • RESEARCH  — historical backtest / calibration (vs statistical thresholds).
  • PROSPECTIVE SHADOW — predictions recorded PREGAME and graded against actual
    results and the EXACT recorded sportsbook lines.
  • PLACED BETS — real money wagered (none; always reported as such until real
    bets exist).

Closing-line value is shown only when reliable closing prices were recorded.
Nothing is labelled profitable without adequate graded sample. Read-only.
"""

import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
GRADES = os.path.join(HERE, "grades")
MIN_N = 200     # below this, results are diagnostics, not conclusions


def _load(name):
    path = os.path.join(GRADES, name)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _nfl_rows(rep):
    out = []
    if not rep:
        return out
    for prop, b in rep.get("by_prop", {}).items():
        out.append({"sport": "NFL", "prop": prop, "n": b.get("n"),
                    "v1_brier": (b.get("v1") or {}).get("brier"),
                    "v2_brier": (b.get("v2") or {}).get("brier"),
                    "v1_roi": ((b.get("v1") or {}).get("paper_roi") or {}).get("roi_pct"),
                    "v2_roi": ((b.get("v2") or {}).get("paper_roi") or {}).get("roi_pct")})
    return out


def _nba_rows(rep):
    out = []
    if not rep:
        return out
    for prop, b in rep.get("by_prop", {}).items():
        pr = b.get("v2_prob_vs_recorded_line") or {}
        pb = b.get("v2_paper_bets") or {}
        out.append({"sport": "NBA", "prop": prop, "n": b.get("n_matched"),
                    "v1_proj_mae": (b.get("v1_projection") or {}).get("mae"),
                    "v2_proj_mae": (b.get("v2_projection") or {}).get("mae"),
                    "v2_brier": pr.get("brier"), "v2_ece": pr.get("ece"),
                    "v2_roi": pb.get("roi_pct"),
                    "v2_record": pb.get("record")})
    return out


def build_report(nfl_grade=None, nba_grade=None):
    nfl = _nfl_rows(nfl_grade)
    nba = _nba_rows(nba_grade)
    lines = ["# Diamond Analytics — Shadow Performance (v1 vs v2)", ""]
    lines.append("Evidence tiers: **RESEARCH** (historical, vs statistical "
                 "thresholds) · **PROSPECTIVE SHADOW** (recorded pregame, graded vs "
                 "real lines) · **PLACED BETS** (real money).")
    lines.append("")
    lines.append("## Placed bets")
    lines.append("- None. No real wagers have been recorded.")
    lines.append("")
    lines.append("## Prospective shadow — graded pregame predictions")
    if not nfl and not nba:
        lines.append("- No graded prospective predictions yet. Run the recorders on "
                     "live slates and the graders after games finish; this section "
                     "fills in automatically.")
    else:
        if nba:
            lines.append("\n### NBA (v2 probabilities vs the exact recorded line)")
            for r in nba:
                conc = "" if (r["n"] or 0) >= MIN_N else "  _(small sample — diagnostic)_"
                lines.append("- **{}**: n={} · proj MAE v1 {} / v2 {} · v2 Brier {} ECE {} · "
                             "paper ROI {}%{}".format(
                                 r["prop"].upper(), r["n"], r["v1_proj_mae"], r["v2_proj_mae"],
                                 r["v2_brier"], r["v2_ece"], r["v2_roi"], conc))
        if nfl:
            lines.append("\n### NFL (v1 vs v2 calibrated probabilities)")
            for r in nfl:
                conc = "" if (r["n"] or 0) >= MIN_N else "  _(small sample — diagnostic)_"
                lines.append("- **{}**: n={} · Brier v1 {} / v2 {} · paper ROI v1 {}% / v2 {}%{}".format(
                    r["prop"].upper(), r["n"], r["v1_brier"], r["v2_brier"],
                    r["v1_roi"], r["v2_roi"], conc))
    lines.append("")
    lines.append("## Research (historical backtest / calibration)")
    lines.append("- NFL: empirical/conditional distributions beat fixed-CV normal "
                 "(rush/rec yards, receptions) out-of-sample.")
    lines.append("- NBA: empirical best for points & PRA; negative binomial best for "
                 "rebounds/assists/threes (Phase 3C).")
    lines.append("- These are vs statistical thresholds, NOT betting markets, and are "
                 "not profitability claims.")
    lines.append("")
    lines.append("## Closing-line value")
    lines.append("- Tracked only where reliable closing prices are recorded; none yet.")
    lines.append("")
    lines.append("_No model is labelled profitable; prospective samples are still "
                 "accruing._")
    return "\n".join(lines)


def run():
    txt = build_report(_load("grade_report.json"), _load("nba_grade_report.json"))
    out = os.path.join(HERE, "SHADOW_PERFORMANCE.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    print("\nwrote " + out)


if __name__ == "__main__":
    run()
