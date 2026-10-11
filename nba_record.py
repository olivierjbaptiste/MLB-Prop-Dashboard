#!/usr/bin/env python3
"""
nba_record.py — Phase 3C Task 6: immutable prospective NBA prediction log.

Records BOTH model versions before tipoff so they can later be graded against
actual results: v1 (production point projection) and v2 (candidate point
projection + calibrated over/push/under probabilities and EV for the exact
offer). Mirrors the NFL recorder's guarantees.

Guarantees:
  • Pregame only — a player is recorded only if that player's game has not tipped
    off (compared in UTC).
  • Rejects invalid data — no snapshot, missing/stale timestamp, or no upcoming
    games → nothing recorded.
  • Immutable & idempotent — append-only; a .bak is written before each append;
    an exact (snapshot_ts | player | prop | model_version) record is never logged
    twice, so v1 and v2 coexist and re-running adds nothing.
  • Preserves the exact offer — book, line, American odds, retrieval timestamp —
    so both versions grade against the same line later. No fabricated offers.

Does NOT touch the live dashboard. UTC throughout. Log is git-ignored.
"""

import os
import json
import shutil
import hashlib
from datetime import datetime, timezone, timedelta

import nba_model
import nba_model_v2
import nba_prob
import nba_ev

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba_predictions_log.jsonl")
OU_PROPS = ("pts", "reb", "ast", "tpm", "pra")
_V1 = {"pts": "proj_pts", "reb": "proj_reb", "ast": "proj_ast", "tpm": "proj_tpm", "pra": "proj_pra"}
_V2 = {"pts": "proj_pts_v2", "reb": "proj_reb_v2", "ast": "proj_ast_v2",
       "tpm": "proj_tpm_v2", "pra": "proj_pra_v2"}

MAX_SNAPSHOT_AGE_H = 30
STALE_OFFER_HOURS = 12


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tipped(g, now):
    start = _parse_iso(g.get("start"))
    status = str(g.get("status") or "").lower()
    if "final" in status or "in progress" in status:
        return True
    return not (start and start > now)


def validate_snapshot(data, now=None):
    now = now or _now_utc()
    reasons = []
    if not data or not data.get("players"):
        return False, ["no snapshot data"]
    ts = data.get("ts")
    if not ts:
        reasons.append("missing snapshot timestamp")
    elif (now.timestamp() - ts) / 3600.0 > MAX_SNAPSHOT_AGE_H:
        reasons.append("snapshot is stale")
    games = data.get("games") or []
    if not any(not _tipped(g, now) for g in games):
        reasons.append("no upcoming (pregame) games")
    return (len(reasons) == 0), reasons


def _games_by_team(data):
    out = {}
    for g in data.get("games", []):
        for k in (str(g.get("home_id")), str(g.get("away_id"))):
            out[k] = g
    return out


def _offer_ok(offer, now):
    ts = _parse_iso(offer.get("ts"))
    return ts is None or (now - ts) <= timedelta(hours=STALE_OFFER_HOURS)


def build_records(data, now=None, params=None):
    """Pure: projected snapshot → immutable pregame records for v1 and v2.
    `params` (from nba_prob.fit(dict)) enables the v2 calibrated probabilities."""
    now = now or _now_utc()
    captured_at = _iso(now)
    snap_ts = data.get("ts")
    data_as_of = _iso(datetime.fromtimestamp(snap_ts, timezone.utc)) if snap_ts else None
    odds = data.get("odds") or {}
    games = _games_by_team(data)
    v1v = getattr(nba_model, "VERSION", "nba-v1")
    v2v = nba_model_v2.VERSION

    recs = []
    for p in data.get("players", []):
        if not p.get("playable", True):
            continue
        g = games.get(str(p.get("team_id")))
        if not g or _tipped(g, now):                 # pregame only
            continue
        event_id = g.get("game_id")
        book_lines = odds.get((p.get("name") or "").lower()) or {}
        for prop in OU_PROPS:
            v1_proj = p.get(_V1[prop])
            v2_proj = p.get(_V2[prop])
            base = {
                "captured_at": captured_at, "data_as_of": data_as_of, "snapshot_ts": snap_ts,
                "season": data.get("season"), "event_id": event_id,
                "game_start": g.get("start"),
                "game_date": data.get("today") or (g.get("start") or "")[:10],
                "team": p.get("team"),
                "player_id": p.get("id"), "player": p.get("name"), "prop": prop,
            }
            # locate a concrete, non-stale offer (never fabricated)
            offer = None
            ln = book_lines.get(prop)
            if ln and ln.get("offers"):
                for o in ln["offers"]:
                    if _offer_ok(o, now):
                        offer = o
                        break

            # v1 row (point projection; no calibrated prob in v1)
            if v1_proj is not None:
                r = dict(base); r["model_version"] = v1v; r["projection"] = v1_proj
                _attach_offer(r, offer)
                r["dedupe_key"] = _key(snap_ts, p, prop, v1v)
                _hash(r); recs.append(r)

            # v2 row (point projection + calibrated over/push/under + EV)
            if v2_proj is not None:
                r = dict(base); r["model_version"] = v2v; r["projection"] = v2_proj
                model = nba_prob.DEFAULT_MODEL_BY_PROP.get(prop, "empirical")
                r["dist_model"] = model
                r["calibration_version"] = nba_prob.CALIBRATION_VERSION
                if offer is not None and params is not None:
                    o = dict(offer); o.setdefault("player", p.get("name"))
                    o.setdefault("event_id", event_id)
                    ev = nba_ev.offer_ev(prop, v2_proj, o, model, params.get(prop), now)
                    if ev:
                        r.update({"book": ev["book"], "side": ev["side"], "line": ev["line"],
                                  "price": ev["price"], "line_ts": ev["ts"],
                                  # preserve BOTH prices from the exact offer for grading
                                  "price_over": offer.get("over_price"),
                                  "price_under": offer.get("under_price"),
                                  "p_over": ev["p_over"], "p_push": ev["p_push"],
                                  "p_under": ev["p_under"], "model_prob": ev["model_prob"],
                                  "ev_pct": ev["ev_pct"]})
                if "p_over" not in r:
                    # still record the calibrated distribution even without an offer
                    t = None
                    if params is not None:
                        t, _ = nba_prob.default_triple(prop, v2_proj, _default_line(v2_proj), params)
                    if t:
                        r["p_over"], r["p_push"], r["p_under"] = [round(x, 4) for x in t]
                    else:
                        r["p_over"] = r["p_push"] = r["p_under"] = None
                    r.setdefault("book", None); r.setdefault("line", None); r.setdefault("price", None)
                r["label"] = "shadow candidate — not a verified betting opportunity"
                r["dedupe_key"] = _key(snap_ts, p, prop, v2v)
                _hash(r); recs.append(r)
    return recs


def _default_line(proj):
    return round(proj * 2) / 2.0 - 0.5     # a nominal half-point line near the projection


def _attach_offer(r, offer):
    if offer:
        r.update({"book": offer.get("book"), "line": offer.get("line"),
                  "price_over": offer.get("over_price"), "price_under": offer.get("under_price"),
                  "line_ts": offer.get("ts")})
    else:
        r.update({"book": None, "line": None})


def _key(snap_ts, p, prop, ver):
    return "{}|{}|{}|{}".format(snap_ts, p.get("id") or p.get("name"), prop, ver)


def _hash(r):
    r["hash"] = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()[:16]


def _existing_keys(path):
    keys = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    keys.add(json.loads(line).get("dedupe_key"))
                except Exception:
                    pass
    return keys


def append_records(recs, path=None):
    path = path or LOG_PATH            # resolve dynamically (respects reassignment)
    existing = _existing_keys(path)
    new = [r for r in recs if r["dedupe_key"] not in existing]
    if not new:
        return 0, len(recs)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    with open(path, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r) + "\n")
    return len(new), len(recs) - len(new)


def load_params(path=None):
    """Load the packaged calibration params (nba_fit_params.py output), or None.
    Prefers the compact committed file; no history CSV is needed at run time."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, "nba_calib_params.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        return blob.get("params", blob)
    except Exception:
        return None


def capture(path=LOG_PATH, now=None, params_path=None):
    """Operational entrypoint: read the live NBA snapshot, project v1 + v2, and
    record both pregame. v2 carries calibrated probabilities when the params file
    is present. Refuses to record stale/invalid snapshots. Does NOT touch the
    live dashboard."""
    import nba
    now = now or _now_utc()
    data = nba.read_snapshot() or {}
    if data.get("players"):
        nba_model.project_all(data)
        nba_model_v2.project_all_v2(data)
    ok, reasons = validate_snapshot(data, now)
    if not ok:
        print("REFUSED to record — " + "; ".join(reasons))
        return 0
    params = load_params(params_path)
    if params is None:
        print("WARNING: no nba_prob_params.json — recording v2 point projections "
              "WITHOUT calibrated probabilities. Run nba_fit_params.py first.")
    recs = build_records(data, now, params)
    added, skipped = append_records(recs, path)
    v1n = sum(1 for r in recs if r["model_version"] != nba_model_v2.VERSION)
    print("recorded {} new rows ({} already logged) -> {}  [v1:{} v2:{}]"
          .format(added, skipped, path, v1n, len(recs) - v1n))
    return added


if __name__ == "__main__":
    capture()
