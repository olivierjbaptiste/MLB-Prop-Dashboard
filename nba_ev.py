#!/usr/bin/env python3
"""
nba_ev.py — Phase 3C: offer-specific expected value for NBA props.

For ONE concrete sportsbook offer — the line, the over price and the under price
that exist together at one book — compute EV using the calibrated probability
(nba_prob) for that EXACT line. Odds from one line are never mixed with another
line. Pushes return the stake. Stale or incomplete offers are rejected, never
patched. No synthetic offers are ever created.

Preserved per offer (for later grading): player, event_id, book, prop, side,
exact line, American odds, retrieval timestamp.

PURE PYTHON. Mirrors the NFL EV design (model_v2.offer_ev) for consistency.
"""

import math
from datetime import datetime, timezone, timedelta

import nba_prob

STALE_OFFER_HOURS = 12
_LABEL = "model estimate — not a verified betting opportunity"


def _decimal(american):
    a = _num(american)
    if a is None:
        return None
    return (a / 100.0) if a > 0 else (100.0 / abs(a))


def _num(x):
    try:
        return None if x in (None, "") else float(x)
    except Exception:
        return None


def _implied(american):
    a = _num(american)
    if a is None:
        return None
    return (100.0 / (a + 100.0)) if a > 0 else (abs(a) / (abs(a) + 100.0))


def _devig(over_price, under_price):
    """No-vig fair probabilities from the two prices (reference only)."""
    io, iu = _implied(over_price), _implied(under_price)
    if io is None or iu is None or (io + iu) <= 0:
        return None, None
    return io / (io + iu), iu / (io + iu)


def _parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _ev(p_win, p_push, american):
    """EV per 1u stake. Push returns the stake (0 profit)."""
    dec = _decimal(american)
    if dec is None:
        return None
    p_lose = max(0.0, 1.0 - p_win - p_push)
    return p_win * dec - p_lose          # push contributes 0


def offer_ev(prop, proj, offer, model, params, now=None):
    """Calibrated EV for one exact offer. Returns a dict, or None if the offer is
    stale/incomplete or the projection is unusable. Never fabricates prices."""
    now = now or datetime.now(timezone.utc)
    line = _num(offer.get("line"))
    if line is None or proj is None or proj <= 0:
        return None
    # reject stale odds
    ts = _parse_iso(offer.get("ts"))
    if ts is not None and (now - ts) > timedelta(hours=STALE_OFFER_HOURS):
        return None
    over_price = offer.get("over_price")
    under_price = offer.get("under_price")
    if over_price is None and under_price is None:
        return None                                     # no real price → reject

    triple = nba_prob.over_under_push(prop, proj, line, model, params)
    if triple is None:
        return None
    p_over, p_push, p_under = triple

    cands = []
    if over_price is not None:
        ev = _ev(p_over, p_push, over_price)
        if ev is not None:
            cands.append(("Over", p_over, over_price, ev))
    if under_price is not None:
        ev = _ev(p_under, p_push, under_price)
        if ev is not None:
            cands.append(("Under", p_under, under_price, ev))
    if not cands:
        return None
    side, p_win, price, ev = max(cands, key=lambda c: c[3])
    fair_over, fair_under = _devig(over_price, under_price)

    return {
        "player": offer.get("player"), "event_id": offer.get("event_id"),
        "book": offer.get("book"), "prop": prop, "model": model,
        "projection": round(proj, 2), "line": line, "side": side,
        "price": price, "ts": offer.get("ts"),
        "p_over": round(p_over, 4), "p_push": round(p_push, 4), "p_under": round(p_under, 4),
        "model_prob": round(p_win, 4),
        "fair_prob_novig": round((fair_over if side == "Over" else fair_under), 4)
                           if fair_over is not None else None,
        "ev_pct": round(ev * 100, 2),
        "label": _LABEL,
    }


def compare_offer(prop, v1_proj, v2_proj, offer, model, params, now=None):
    """Side-by-side row: v1 projection, v2 projection + calibrated prob/EV for the
    SAME exact offer. v1 has no calibrated prob here (point only). Read-only."""
    r = offer_ev(prop, v2_proj, offer, model, params, now)
    if r is None:
        return None
    r = dict(r)
    r["v1_projection"] = None if v1_proj is None else round(v1_proj, 2)
    r["v2_projection"] = r.pop("projection")
    return r
