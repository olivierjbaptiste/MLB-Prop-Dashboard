#!/usr/bin/env python3
"""
nba_model_v2.py — Phase 3B candidate NBA model (NON-replacing).

Evidence-backed changes vs v1 (nba_model.py), from the 74,707-record,
57,097-observation walk-forward backtest with a 2025-26 holdout:

  1. MINUTES → exponentially-weighted moving average (half-life 5 games) instead
     of the 0.60 season / 0.40 last-5 blend. EWMA beat the blend on the full data
     (−0.076 min MAE) and out-of-sample (−0.065, CI [−0.091, −0.040]) — the
     single biggest, most robust win, and minutes is the lever every stat
     multiplies through.
  2. RATE recency weights per stat: points keep 0.60 season; rebounds, assists,
     and threes move to 0.80 season (recent-5 was the noisiest input). A small
     but consistent out-of-sample gain.
  3. MATCHUP → neutral. The opponent adjustment showed no meaningful benefit
     across 57,097 obs (only assists significant, at a trivial 0.25% effect), and
     the v1 layer additionally double-counts pace. v2 uses a neutral 1.0 factor.

Net effect (v2 vs v1), full data AND 2026 holdout, every CI excludes 0:
  pts −0.010 · reb −0.011 · ast −0.006 · tpm −0.004 · pra −0.023 (MAE).
Modest but real and uniformly positive — and arguably simpler (one EWMA
parameter; no matchup multiplier to double-count).

PURE MATH, no network. Does NOT touch v1, the snapshot pipeline, the website, or
any other sport. Keep v1 (nba_model.py) as production until a shadow comparison
on live slates supports promotion.
"""

import nba_model   # reuse status buckets / confidence vocabulary for parity

VERSION = "nba-2026.2-candidate"
EWMA_HALFLIFE = 5
# season share per stat (recent share = 1 − this). pts stays at v1's 0.60.
RATE_WEIGHT = {"pts": 0.60, "reb": 0.80, "ast": 0.80, "tpm": 0.80}
W_MIN_SEASON_FALLBACK = 0.40     # if no recent series: lean minutes toward recent


def _ewma(vals, halflife=EWMA_HALFLIFE):
    """vals oldest→newest; newest weighted most."""
    xs = [v for v in vals if v is not None]
    if not xs:
        return None
    lam = 0.5 ** (1.0 / halflife)
    num = den = 0.0
    for i, x in enumerate(reversed(xs)):      # i=0 newest
        w = lam ** i
        num += w * x
        den += w
    return num / den if den else None


def _blend(season, recent, w_season):
    if season is None and recent is None:
        return None
    if recent is None:
        return season
    if season is None:
        return recent
    return w_season * season + (1 - w_season) * recent


def _rate(total, minutes):
    if total is None or not minutes or minutes <= 0:
        return None
    return total / minutes


def project_minutes_v2(p):
    """EWMA of the recent minutes series when present; otherwise a recent-leaning
    blend of season and last-5 minutes (graceful fallback for an old snapshot)."""
    recent = p.get("recent_min") or []
    ew = _ewma(recent)
    if ew is not None:
        return ew
    return _blend(p.get("mpg"), p.get("min_l5"), W_MIN_SEASON_FALLBACK)


def project_player_v2(p, data=None):
    """Attach proj_*_v2 fields. Same structure as v1: rate × minutes, neutral
    matchup, injury handling. Returns the same dict."""
    bucket = nba_model._status_bucket(p.get("status"))
    proj_min = project_minutes_v2(p)

    mpg = p.get("mpg")

    def stat(season_tot, l5_tot, prop):
        s_rate = _rate(season_tot, mpg)
        l5_rate = _rate(l5_tot, p.get("min_l5"))
        r = _blend(s_rate, l5_rate, RATE_WEIGHT[prop])
        return None if (r is None or proj_min is None) else r * proj_min

    proj_pts = stat(p.get("ppg"), p.get("ppg_l5"), "pts")
    proj_reb = stat(p.get("rpg"), p.get("rpg_l5"), "reb")
    proj_ast = stat(p.get("apg"), p.get("apg_l5"), "ast")
    proj_tpm = stat(p.get("tpm"), None, "tpm")        # no L5 3PM in snapshot → season
    comps = [proj_pts, proj_reb, proj_ast]
    proj_pra = None if any(c is None for c in comps) else sum(comps)

    if bucket == "out":
        proj_min = proj_pts = proj_reb = proj_ast = proj_tpm = proj_pra = 0.0

    def r1(x):
        return None if x is None else round(x, 1)

    p["proj_min_v2"] = r1(proj_min)
    p["proj_pts_v2"] = r1(proj_pts)
    p["proj_reb_v2"] = r1(proj_reb)
    p["proj_ast_v2"] = r1(proj_ast)
    p["proj_tpm_v2"] = r1(proj_tpm)
    p["proj_pra_v2"] = r1(proj_pra)
    p["v2_matchup"] = "neutral"
    p["model_version_v2"] = VERSION
    return p


def project_all_v2(data):
    for p in data.get("players", []):
        project_player_v2(p, data)
    return data.get("players", [])
