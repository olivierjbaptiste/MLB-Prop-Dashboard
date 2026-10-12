#!/usr/bin/env python3
"""Live-scores proxy (scores.py) — ESPN scoreboard normalizer + cache.

Pure/offline: every test injects a fake `fetch` so nothing touches the network.
Covers the four situations the dashboard must render honestly — scheduled, live,
final, no-game — across leagues, plus caching, stale fallback, and URL building.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scores as S


# ─────────────────────────── ESPN fixtures ────────────────────────────
def _ev(eid, state, name, detail, home, away, period=None, clock=None,
        completed=False, season_type=2, date="2026-10-11T23:00Z", note=""):
    def comp(side, t):
        c = {"homeAway": side, "team": {
            "abbreviation": t["abbr"], "displayName": t["name"],
            "logo": t.get("logo", "")}}
        if t.get("score") is not None:
            c["score"] = str(t["score"])
        if t.get("rec"):
            c["records"] = [{"summary": t["rec"]}]
        if t.get("winner"):
            c["winner"] = True
        return c
    competition = {
        "status": {"period": period, "displayClock": clock,
                   "type": {"state": state, "name": name,
                            "shortDetail": detail, "completed": completed}},
        "competitors": [comp("home", home), comp("away", away)],
    }
    if note:
        competition["notes"] = [{"headline": note}]
    return {"id": eid, "date": date, "season": {"type": season_type},
            "competitions": [competition]}


def fx_live():
    return {"season": {"type": 2}, "week": {"number": 6}, "events": [_ev(
        "401", "in", "STATUS_IN_PROGRESS", "3rd Quarter",
        {"abbr": "LAL", "name": "Los Angeles Lakers", "logo": "lal.png",
         "score": 58, "rec": "2-1"},
        {"abbr": "BOS", "name": "Boston Celtics", "logo": "bos.png",
         "score": 61, "rec": "3-0"},
        period=3, clock="5:21")]}


def fx_final():
    return {"season": {"type": 2}, "events": [_ev(
        "402", "post", "STATUS_FINAL", "Final",
        {"abbr": "GSW", "name": "Golden State Warriors", "score": 110,
         "winner": True, "rec": "1-2"},
        {"abbr": "PHX", "name": "Phoenix Suns", "score": 98, "rec": "0-3"},
        period=4, clock="0:00", completed=True)]}


def fx_sched():
    return {"season": {"type": 1}, "events": [_ev(
        "403", "pre", "STATUS_SCHEDULED", "10/12 - 7:30 PM ET",
        {"abbr": "MIA", "name": "Miami Heat"},
        {"abbr": "NYK", "name": "New York Knicks"},
        period=0, clock="0:00", season_type=1, date="2026-10-12T23:30Z")]}


def fx_ppd():
    return {"events": [_ev(
        "404", "pre", "STATUS_POSTPONED", "Postponed",
        {"abbr": "NYM", "name": "New York Mets"},
        {"abbr": "ATL", "name": "Atlanta Braves"})]}


def fx_empty():
    return {"season": {"type": 2}, "events": []}


# ───────────────────────────── normalize ──────────────────────────────
class TestNormalize(unittest.TestCase):
    def setUp(self):
        S._cache.clear()

    def test_scheduled(self):
        g = S.normalize(fx_sched(), "nba", "20261012")["games"][0]
        self.assertEqual(g["state"], "scheduled")
        self.assertFalse(g["completed"])
        self.assertIsNone(g["away"]["score"])      # no fabricated score
        self.assertIsNone(g["home"]["score"])
        self.assertEqual(g["away"]["abbr"], "NYK")
        self.assertEqual(g["competition"], "preseason")
        self.assertTrue(g["start_utc"])

    def test_live(self):
        n = S.normalize(fx_live(), "nba", "20261011")
        g = n["games"][0]
        self.assertEqual(g["state"], "live")
        self.assertEqual(g["away"]["score"], 61)   # ints, not strings
        self.assertEqual(g["home"]["score"], 58)
        self.assertEqual(g["period"], 3)
        self.assertEqual(g["clock"], "5:21")
        self.assertEqual(g["away"]["record"], "3-0")
        self.assertEqual(n["week"], 6)

    def test_final(self):
        g = S.normalize(fx_final(), "nba", "20261011")["games"][0]
        self.assertEqual(g["state"], "final")
        self.assertTrue(g["completed"])
        self.assertTrue(g["home"]["winner"])
        self.assertFalse(g["away"]["winner"])
        self.assertEqual(g["home"]["score"], 110)

    def test_no_game(self):
        n = S.normalize(fx_empty(), "nba", "20261011")
        self.assertEqual(n["games"], [])
        self.assertEqual(n["season_type"], "regular")

    def test_postponed(self):
        g = S.normalize(fx_ppd(), "mlb", "20261011")["games"][0]
        self.assertEqual(g["state"], "postponed")
        self.assertIsNone(g["away"]["score"])

    def test_never_raises_on_garbage(self):
        for junk in (None, [], "nope", {"events": "bad"},
                     {"events": [{"no": "competitions"}]},
                     {"events": [{"competitions": []}]}):
            out = S.normalize(junk, "nba", "20261011")
            self.assertEqual(out["league"], "nba")
            self.assertIsInstance(out["games"], list)


class TestStateMapping(unittest.TestCase):
    def test_states(self):
        mk = lambda st, nm: {"type": {"state": st, "name": nm}}
        self.assertEqual(S._state(mk("in", "STATUS_IN_PROGRESS")), "live")
        self.assertEqual(S._state(mk("post", "STATUS_FINAL")), "final")
        self.assertEqual(S._state(mk("pre", "STATUS_SCHEDULED")), "scheduled")
        self.assertEqual(S._state(mk("pre", "STATUS_POSTPONED")), "postponed")
        self.assertEqual(S._state(mk("pre", "STATUS_CANCELED")), "canceled")
        self.assertEqual(S._state(mk("in", "STATUS_SUSPENDED")), "suspended")
        self.assertEqual(S._state({}), "scheduled")

    def test_num(self):
        self.assertEqual(S._num("58"), 58)
        self.assertEqual(S._num("58.0"), 58)
        self.assertIsNone(S._num(None))
        self.assertIsNone(S._num(""))
        self.assertIsNone(S._num("x"))


class TestUrl(unittest.TestCase):
    def test_nfl_week_beats_date(self):
        u = S.scoreboard_url("nfl", date="20261011", week=6)
        self.assertIn("week=6", u)
        self.assertNotIn("dates=", u)

    def test_date_param(self):
        u = S.scoreboard_url("nba", date="20261011")
        self.assertIn("dates=20261011", u)
        self.assertNotIn("week=", u)

    def test_bare(self):
        self.assertEqual(S.scoreboard_url("mlb"), S.ESPN["mlb"])


# ─────────────────────────── cached public API ────────────────────────
class TestGetScores(unittest.TestCase):
    def setUp(self):
        S._cache.clear()

    def test_fresh_fetch(self):
        d = S.get_scores("nba", date="20261011", fetch=fx_live, now=1000.0)
        self.assertFalse(d["stale"])
        self.assertEqual(d["age_seconds"], 0.0)
        self.assertTrue(d["last_updated"])
        self.assertEqual(sum(1 for g in d["games"] if g["state"] == "live"), 1)

    def test_cache_hit_does_not_refetch(self):
        S.get_scores("nba", date="20261011", fetch=fx_live, now=1000.0)
        boom = lambda: (_ for _ in ()).throw(AssertionError("refetched within TTL"))
        d = S.get_scores("nba", date="20261011", fetch=boom, now=1000.0 + S.CACHE_TTL - 1)
        self.assertFalse(d["stale"])
        self.assertGreater(d["age_seconds"], 0)

    def test_cache_expires(self):
        S.get_scores("nba", date="20261011", fetch=fx_sched, now=1000.0)
        d = S.get_scores("nba", date="20261011", fetch=fx_live, now=1000.0 + S.CACHE_TTL + 1)
        self.assertEqual(sum(1 for g in d["games"] if g["state"] == "live"), 1)
        self.assertFalse(d["stale"])

    def test_stale_fallback_serves_last_good(self):
        S.get_scores("nba", date="20261011", fetch=fx_live, now=1000.0)
        d = S.get_scores("nba", date="20261011", fetch=lambda: None,
                         now=1000.0 + S.CACHE_TTL + 100)
        self.assertTrue(d["stale"])               # flagged, never shown as live
        self.assertIn("error", d)
        self.assertEqual(len(d["games"]), 1)      # last good data still there
        self.assertGreater(d["age_seconds"], S.CACHE_TTL)

    def test_cold_failure_empty(self):
        d = S.get_scores("nfl", date="20261011", fetch=lambda: None, now=5000.0)
        self.assertTrue(d["stale"])
        self.assertEqual(d["games"], [])
        self.assertIsNone(d["last_updated"])

    def test_unknown_league(self):
        d = S.get_scores("soccer")
        self.assertIn("error", d)
        self.assertTrue(d["stale"])
        self.assertEqual(d["games"], [])

    def test_cache_key_isolates_league_and_date(self):
        S.get_scores("nba", date="20261011", fetch=fx_live, now=1000.0)
        # different date must not read the nba/20261011 cache entry
        d = S.get_scores("nba", date="20261012", fetch=fx_sched, now=1000.0)
        self.assertEqual(d["games"][0]["state"], "scheduled")
        d2 = S.get_scores("mlb", date="20261011", fetch=fx_ppd, now=1000.0)
        self.assertEqual(d2["games"][0]["state"], "postponed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
