#!/usr/bin/env python3
"""
record_predictions.py — Phase 2C: immutable prospective prediction log.

Captures the NFL model's projections (and edges, when the snapshot carries
sportsbook lines) to an append-only JSONL file BEFORE kickoff, so predictions
can later be graded against actual results (Phase 4).

Guarantees:
  • Pregame only — a player is recorded only if that player's game has not yet
    started and is not final/in-progress (compared in UTC).
  • Rejects invalid data — no snapshot, missing timestamp, stale snapshot, or no
    upcoming games → nothing is recorded.
  • Expired offers dropped — a sportsbook line older than STALE_OFFER_HOURS is
    not attached (the projection is still recorded, without price).
  • Immutable — append-only; existing lines are never rewritten or deleted; each
    row carries a content hash; a prior backup copy is written before appending.
  • Dedupe without blocking updates — an exact (snapshot_ts | player | prop)
    record is never logged twice, but a later snapshot (new data-as-of time) is
    recorded as new rows.
  • No fabrication — missing projections or prices are omitted, never invented.

Does NOT touch the live dashboard. UTC throughout.
"""

import os
import json
import shutil
import hashlib
from datetime import datetime, timezone, timedelta

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "predictions_log.jsonl")
_PROPS = ("pass_yds", "rush_yds", "rec_yds", "rec", "anytime_td")

MAX_SNAPSHOT_AGE_H = 30      # older snapshot → refuse to record (stale)
STALE_OFFER_HOURS = 12       # sportsbook line older than this → drop the price


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


def validate_snapshot(data, now=None):
    """Return (ok, reasons). Stale/invalid data must not produce recordings."""
    now = now or _now_utc()
    reasons = []
    if not data or not data.get("players"):
        return False, ["no snapshot data"]
    ts = data.get("ts")
    if not ts:
        reasons.append("missing snapshot timestamp")
    else:
        age_h = (now.timestamp() - ts) / 3600.0
        if age_h > MAX_SNAPSHOT_AGE_H:
            reasons.append("snapshot is {:.1f}h old".format(age_h))
    games = data.get("games") or []
    pregame = 0
    for g in games:
        start = _parse_iso(g.get("start"))
        status = str(g.get("status") or "").lower()
        if start and start > now and "final" not in status:
            pregame += 1
    if pregame == 0:
        reasons.append("no upcoming (pregame) games in snapshot")
    return (len(reasons) == 0), reasons


def _game_is_pregame(g, now):
    start = _parse_iso(g.get("start"))
    status = str(g.get("status") or "").lower()
    if "final" in status or "in progress" in status:
        return False
    return bool(start and start > now)


def build_records(data, model_version, now=None):
    """Pure: projected snapshot → immutable pregame prediction records."""
    now = now or _now_utc()
    captured_at = _iso(now)
    snap_ts = data.get("ts")
    data_as_of = _iso(datetime.fromtimestamp(snap_ts, timezone.utc)) if snap_ts else None

    games = {}
    for g in data.get("games", []):
        for key in (g.get("home_nflv"), g.get("away_nflv")):
            if key:
                games[key] = g

    recs = []
    for p in data.get("players", []):
        if not p.get("playable"):
            continue
        g = games.get(p.get("team"))
        if not g or not _game_is_pregame(g, now):      # pregame only
            continue
        edge = p.get("edge") or {}
        for prop in _PROPS:
            proj = p.get("anytime_td") if prop == "anytime_td" else p.get("proj_" + prop)
            if proj is None:
                continue
            e = edge.get(prop) or {}
            # drop an expired sportsbook offer (keep the projection)
            offer_ts = _parse_iso(e.get("ts"))
            offer_ok = bool(e) and (offer_ts is None or
                                    (now - offer_ts) <= timedelta(hours=STALE_OFFER_HOURS))
            # Full v1 O/U distribution (fixed-CV normal — the production layer),
            # stored so v1 and v2 can be graded on the identical event P(Over).
            dist_block = {"p_over": None, "p_push": None, "p_under": None}
            if offer_ok and prop in ("pass_yds", "rush_yds", "rec_yds", "rec"):
                ln = e.get("line")
                if ln is not None and proj:
                    try:
                        import nfl_model as _m
                        sigma = max(1.0, _m._CV[prop] * proj)
                        o, pu, u = _m._ou_probs(proj, ln, sigma)
                        dist_block = {"p_over": round(o, 6), "p_push": round(pu, 6),
                                      "p_under": round(u, 6)}
                    except Exception:
                        pass
            price_block = {
                "book": e.get("book"), "side": e.get("side"), "line": e.get("line"),
                "price": e.get("price"), "model_prob": e.get("prob"),
                "ev_pct": e.get("ev_pct"), "line_ts": e.get("ts"),
            } if (e and offer_ok) else {
                "book": None, "side": None, "line": None, "price": None,
                "model_prob": None, "ev_pct": None, "line_ts": None,
                "offer_expired": bool(e and not offer_ok) or None,
            }
            price_block.update(dist_block)
            rec = {
                "captured_at": captured_at,
                "data_as_of": data_as_of,
                "snapshot_ts": snap_ts,
                "model_version": model_version,
                "season": data.get("season"),
                "week": data.get("week") or g.get("week"),
                "game_id": g.get("game_id"),
                "event_id": g.get("game_id"),
                "game_start": g.get("start"),
                "game_date": g.get("start") or data.get("today"),
                "team": p.get("team"), "opp": p.get("opp"), "home": p.get("home"),
                "player_id": p.get("id"), "player": p.get("name"),
                "prop": prop, "projection": proj,
                # model_version in the key so v1 and v2 shadow rows coexist without collision
                "dedupe_key": "{}|{}|{}|{}".format(snap_ts, p.get("id") or p.get("name"), prop, model_version),
            }
            rec.update(price_block)
            rec["hash"] = hashlib.sha256(
                json.dumps(rec, sort_keys=True).encode()).hexdigest()[:16]
            recs.append(rec)
    return recs


def build_v2_records(data, now=None, dist="empirical"):
    """Candidate-v2 shadow rows: same offers/snapshot as v1, v2 probability +
    EV, tagged with the v2 model version. v2 covers the four O/U props (no TD
    model). Projections come from v1 (shared); only the probability layer differs."""
    import model_v2
    now = now or _now_utc()
    captured_at = _iso(now)
    snap_ts = data.get("ts")
    data_as_of = _iso(datetime.fromtimestamp(snap_ts, timezone.utc)) if snap_ts else None
    odds = data.get("odds") or {}
    games = {}
    for g in data.get("games", []):
        for key in (g.get("home_nflv"), g.get("away_nflv")):
            if key:
                games[key] = g
    recs = []
    for p in data.get("players", []):
        if not p.get("playable"):
            continue
        g = games.get(p.get("team"))
        if not g or not _game_is_pregame(g, now):
            continue
        book_lines = odds.get((p.get("name") or "").lower())
        if not book_lines:
            continue
        for prop in ("pass_yds", "rush_yds", "rec_yds", "rec"):
            proj = p.get("proj_" + prop)
            ln = book_lines.get(prop)
            if proj is None or not ln or not ln.get("offers"):
                continue
            best = None
            for offer in ln["offers"]:
                offer_ts = _parse_iso(offer.get("ts"))
                if offer_ts is not None and (now - offer_ts) > timedelta(hours=STALE_OFFER_HOURS):
                    continue                              # expired offer
                ev = model_v2.offer_ev(prop, proj, offer, dist)
                if ev and (best is None or ev["ev_pct"] > best["ev_pct"]):
                    best = ev
            if not best:
                continue
            rec = {
                "captured_at": captured_at, "data_as_of": data_as_of, "snapshot_ts": snap_ts,
                "model_version": best["model_version"], "dist": dist,
                "season": data.get("season"), "week": data.get("week") or g.get("week"),
                "game_id": g.get("game_id"), "event_id": g.get("game_id"),
                "game_start": g.get("start"),
                "game_date": g.get("start") or data.get("today"),
                "team": p.get("team"), "opp": p.get("opp"), "home": p.get("home"),
                "player_id": p.get("id"), "player": p.get("name"),
                "prop": prop, "projection": proj,
                "book": best["book"], "side": best["side"], "line": best["line"],
                "price": best["price"], "model_prob": best["model_prob"], "ev_pct": best["ev_pct"],
                "p_over": best["p_over"], "p_push": best["p_push"], "p_under": best["p_under"],
                "line_ts": best["ts"],
                "label": "shadow model estimate — not a verified betting opportunity",
                "dedupe_key": "{}|{}|{}|{}".format(snap_ts, p.get("id") or p.get("name"), prop, best["model_version"]),
            }
            rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:16]
            recs.append(rec)
    return recs


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


def append_records(recs, path=LOG_PATH):
    """Append only records not already logged (exact dedupe key). A later
    pregame snapshot (new snapshot_ts) produces new keys → recorded. Writes a
    .bak of the prior log first. Never modifies existing lines."""
    existing = _existing_keys(path)
    new = [r for r in recs if r["dedupe_key"] not in existing]
    if not new:
        return 0, len(recs)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")           # backup before mutating
    with open(path, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r) + "\n")
    return len(new), len(recs) - len(new)


def capture(path=LOG_PATH, now=None, shadow=True):
    """Record v1 predictions (always) and, when shadow=True, v2 shadow rows too.
    Both share snapshot/offer identifiers for fair later grading."""
    import nfl
    import nfl_model
    now = now or _now_utc()
    data = nfl.read_snapshot() or {}
    if data.get("players"):
        nfl_model.project_all(data)
    ok, reasons = validate_snapshot(data, now)
    if not ok:
        print("REFUSED to record — " + "; ".join(reasons))
        return 0
    recs = build_records(data, getattr(nfl_model, "VERSION", "unknown"), now)
    if shadow:
        recs = recs + build_v2_records(data, now)
    added, skipped = append_records(recs, path)
    v1n = sum(1 for r in recs if "shadow" not in str(r.get("label", "")))
    v2n = len(recs) - v1n
    print("recorded {} new rows ({} already logged) -> {}  [v1:{} v2:{}]"
          .format(added, skipped, path, v1n, v2n))
    return added


if __name__ == "__main__":
    capture()
