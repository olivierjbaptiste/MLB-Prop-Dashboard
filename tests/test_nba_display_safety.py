#!/usr/bin/env python3
"""Batch 1 Stage B + correction pass — NBA display safety: started/off-game
detection (ESPN state first, anchored status text as last resort), snapshot
freshness, unmatched players, and timezone handling. Pure/offline."""
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta

os.environ["DIAMOND_NO_STARTUP"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
import nba


NOW = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)   # 7:30pm ET opening night


class TestGameTippedMatrix(unittest.TestCase):
    """The exact cases from the review. ESPN's explicit state is authoritative;
    status text is only a fallback and must be word-anchored."""

    def test_not_started_future(self):
        g = {"state": "pre", "status_name": "STATUS_SCHEDULED",
             "status": "7:30 PM ET", "start": "2026-10-21T00:00Z"}
        self.assertFalse(app._game_tipped(g, NOW))

    def test_scheduled(self):
        self.assertFalse(app._game_tipped(
            {"state": "pre", "status_name": "STATUS_SCHEDULED", "status": "Scheduled"}, NOW))

    def test_in_progress(self):
        self.assertTrue(app._game_tipped(
            {"state": "in", "status_name": "STATUS_IN_PROGRESS", "status": "In Progress"}, NOW))

    def test_each_quarter(self):
        for q in ("1st", "2nd", "3rd", "4th"):
            self.assertTrue(app._game_tipped({"status": "5:00 - " + q}, NOW), q)   # text fallback
            self.assertTrue(app._game_tipped({"state": "in", "status": q}, NOW), q)

    def test_halftime(self):
        self.assertTrue(app._game_tipped({"status": "Halftime"}, NOW))
        self.assertTrue(app._game_tipped({"state": "in", "status": "Halftime"}, NOW))

    def test_overtime(self):
        self.assertTrue(app._game_tipped({"status": "OT"}, NOW))
        self.assertTrue(app._game_tipped({"status": "2OT"}, NOW))
        self.assertTrue(app._game_tipped({"status": "Overtime"}, NOW))

    def test_final(self):
        self.assertTrue(app._game_tipped(
            {"state": "post", "status_name": "STATUS_FINAL", "status": "Final"}, NOW))
        self.assertTrue(app._game_tipped({"completed": True, "status": "Final"}, NOW))

    def test_postponed_is_not_tipped(self):
        # even with a past scheduled time, a postponed game has not started
        g = {"state": "pre", "status_name": "STATUS_POSTPONED",
             "status": "Postponed", "start": "2026-10-20T22:00Z"}
        self.assertFalse(app._game_tipped(g, NOW))

    def test_missing_start_time(self):
        self.assertTrue(app._game_tipped({"status": "3rd"}, NOW))           # live text
        self.assertFalse(app._game_tipped({"status": "Scheduled"}, NOW))    # pregame text
        self.assertFalse(app._game_tipped({}, NOW))                         # nothing -> safe

    def test_exact_tipoff_boundary(self):
        at_now = {"start": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}
        one_min_later = {"start": (NOW + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.assertTrue(app._game_tipped(at_now, NOW))
        self.assertFalse(app._game_tipped(one_min_later, NOW))

    def test_ot_substring_does_not_match_not_started(self):
        # the original bug: "Not Started" contains "ot"
        self.assertFalse(app._game_tipped({"status": "Not Started"}, NOW))

    def test_naive_timestamp_treated_as_utc(self):
        self.assertTrue(app._game_tipped({"start": "2026-10-20T23:00:00"}, NOW))


class TestBlockReason(unittest.TestCase):
    def test_reasons(self):
        self.assertIsNone(app._game_block_reason(
            {"state": "pre", "status_name": "STATUS_SCHEDULED",
             "start": "2026-10-21T00:00Z"}, NOW))
        self.assertEqual(app._game_block_reason({"state": "in"}, NOW), "started")
        self.assertEqual(app._game_block_reason(
            {"status_name": "STATUS_POSTPONED"}, NOW), "postponed")
        self.assertEqual(app._game_block_reason(
            {"status_name": "STATUS_CANCELED"}, NOW), "canceled")


class TestScheduleFetchValidity(unittest.TestCase):
    """Issue (review): a malformed ESPN scoreboard response must not be recorded as
    a successful schedule read, or it masquerades as an off-day. Only a response
    that actually carries an events list counts; {"events": []} is a real off-day."""

    def _get_games(self, payload):
        orig = nba._fetch_json
        nba._fetch_json = lambda url, **k: payload
        try:
            return nba.get_games(), nba._last_schedule_fetched
        finally:
            nba._fetch_json = orig

    def test_empty_dict_is_not_a_successful_fetch(self):
        games, fetched = self._get_games({})         # malformed — no events list
        self.assertEqual(games, [])
        self.assertIs(fetched, False)                # -> source_error, not no_games

    def test_missing_events_key_is_not_a_successful_fetch(self):
        games, fetched = self._get_games({"leagues": [{}]})
        self.assertEqual(games, [])
        self.assertIs(fetched, False)

    def test_none_response_is_not_a_successful_fetch(self):
        games, fetched = self._get_games(None)
        self.assertEqual(games, [])
        self.assertIs(fetched, False)

    def test_events_empty_list_is_a_real_off_day(self):
        games, fetched = self._get_games({"events": []})
        self.assertEqual(games, [])
        self.assertIs(fetched, True)                 # legitimate off-day

    def test_malformed_then_status_is_source_error_not_no_games(self):
        # end-to-end: a {} schedule response must drive _nba_status to source_error
        _, fetched = self._get_games({})
        s = app._nba_status({"schedule_fetched": fetched, "games": [], "players": []}, NOW)
        self.assertEqual(s["state"], "source_error")


class TestPostponedFromText(unittest.TestCase):
    """Issue #4: when ESPN omits status_name, the human-readable status TEXT must
    still identify a game that isn't happening, so its players stay off the board."""

    def test_postponed_text_without_status_name(self):
        self.assertEqual(app._game_block_reason({"status": "Postponed"}, NOW), "postponed")
        self.assertEqual(app._game_block_reason({"status": "PPD"}, NOW), "postponed")
        self.assertFalse(app._game_tipped({"status": "Postponed"}, NOW))

    def test_canceled_text_without_status_name(self):
        self.assertEqual(app._game_block_reason({"status": "Game Canceled"}, NOW), "canceled")
        self.assertEqual(app._game_block_reason({"status": "Cancelled"}, NOW), "canceled")

    def test_suspended_and_delayed_text(self):
        self.assertEqual(app._game_block_reason({"status": "Suspended"}, NOW), "suspended")
        self.assertEqual(app._game_block_reason({"status": "Delayed"}, NOW), "delayed")

    def test_postponed_text_with_past_start_still_not_tipped(self):
        # a past scheduled time must not override the postponed text
        g = {"status": "Postponed", "start": "2026-10-20T22:00Z"}
        self.assertFalse(app._game_tipped(g, NOW))
        self.assertEqual(app._game_block_reason(g, NOW), "postponed")


class TestNbaStatusFreshness(unittest.TestCase):
    def _fresh(self, **extra):
        et = app._et_today_str()
        d = {"today": et, "ts": NOW.timestamp(), "schedule_fetched": True,
             "players": [{"team_id": "1"}],
             "games": [{"home_id": "1", "away_id": "2", "start": "2026-10-20T23:00Z"}]}
        d.update(extra)
        return d

    def test_todays_snapshot_is_ok(self):
        s = app._nba_status(self._fresh(), NOW)
        self.assertEqual(s["state"], "ok")

    def test_yesterday_snapshot_is_stale(self):
        y = (nba._et_today() - timedelta(days=1)).strftime("%Y-%m-%d")
        s = app._nba_status(self._fresh(today=y), NOW)
        self.assertEqual(s["state"], "stale")
        self.assertIn("not current pregame picks", s["reason"])

    def test_missing_timestamp_is_stale(self):
        d = self._fresh(); d.pop("ts")
        self.assertEqual(app._nba_status(d, NOW)["state"], "stale")

    def test_invalid_timestamp_is_stale(self):
        self.assertEqual(app._nba_status(self._fresh(ts="bad"), NOW)["state"], "stale")

    def test_old_timestamp_is_stale(self):
        old = (NOW - timedelta(hours=48)).timestamp()
        self.assertEqual(app._nba_status(self._fresh(ts=old), NOW)["state"], "stale")

    def test_future_timestamp_beyond_skew_is_stale(self):
        # A timestamp implausibly in the future can't be trusted as "today's" data.
        fut = (NOW + timedelta(hours=10)).timestamp()
        self.assertEqual(app._nba_status(self._fresh(ts=fut), NOW)["state"], "stale")

    def test_slightly_future_timestamp_within_skew_is_ok(self):
        # Modest clock skew between the builder and the server must NOT trip stale.
        near = (NOW + timedelta(hours=1)).timestamp()
        self.assertEqual(app._nba_status(self._fresh(ts=near), NOW)["state"], "ok")

    def test_populated_snapshot_with_failed_schedule_is_stale(self):
        # Players present but the schedule refresh failed -> the slate may be
        # incomplete/out of date, so it must not read as a clean "ok" board.
        self.assertEqual(
            app._nba_status(self._fresh(schedule_fetched=False), NOW)["state"], "stale")

    def test_missing_today_metadata_is_stale(self):
        d = self._fresh(); d.pop("today")
        self.assertEqual(app._nba_status(d, NOW)["state"], "stale")

    def test_genuine_off_day_today(self):
        et = app._et_today_str()
        s = app._nba_status({"today": et, "ts": NOW.timestamp(),
                             "schedule_fetched": True, "games": [], "players": []}, NOW)
        self.assertEqual(s["state"], "no_games")

    def test_old_empty_snapshot_today_is_stale_not_no_games(self):
        # Issue (review): an EMPTY snapshot with today's date and schedule_fetched
        # True but a 72h-old timestamp must not report no_games — the max-age bound
        # applies to empty snapshots exactly as it does to populated ones.
        et = app._et_today_str()
        old = (NOW - timedelta(hours=72)).timestamp()
        s = app._nba_status({"today": et, "ts": old, "schedule_fetched": True,
                             "games": [], "players": []}, NOW)
        self.assertEqual(s["state"], "stale")

    def test_fresh_empty_snapshot_today_is_no_games(self):
        # Control: a recent empty snapshot for today is a genuine off-day.
        et = app._et_today_str()
        s = app._nba_status({"today": et, "ts": NOW.timestamp(), "schedule_fetched": True,
                             "games": [], "players": []}, NOW)
        self.assertEqual(s["state"], "no_games")

    def test_off_day_but_stale_date(self):
        y = (nba._et_today() - timedelta(days=1)).strftime("%Y-%m-%d")
        s = app._nba_status({"today": y, "ts": NOW.timestamp(),
                             "schedule_fetched": True, "games": [], "players": []}, NOW)
        self.assertEqual(s["state"], "stale")

    def test_failed_fetch_is_source_error(self):
        s = app._nba_status({"schedule_fetched": False, "games": [], "players": []}, NOW)
        self.assertEqual(s["state"], "source_error")

    def test_incomplete_when_games_but_no_players(self):
        s = app._nba_status({"schedule_fetched": True,
                             "games": [{"game_id": "1"}], "players": []}, NOW)
        self.assertEqual(s["state"], "incomplete")

    def test_ok_counts_tipped_games(self):
        et = app._et_today_str()
        d = {"today": et, "ts": NOW.timestamp(), "players": [{"team_id": "1"}],
             "games": [{"state": "in", "home_id": "1", "away_id": "2"},
                       {"state": "pre", "home_id": "3", "away_id": "4",
                        "start": "2026-10-21T02:00Z"}]}
        s = app._nba_status(d, NOW)
        self.assertEqual(s["state"], "ok")
        self.assertEqual(s["tipped_games"], 1)


class TestEnrichment(unittest.TestCase):
    def _run(self, snap):
        orig = nba.read_snapshot
        nba.read_snapshot = lambda: snap
        try:
            return app.get_nba_data()
        finally:
            nba.read_snapshot = orig

    def _player(self, name, team_id):
        return {"id": name, "name": name, "team_id": team_id, "status": "active",
                "gp": 80, "mpg": 34, "min_l5": 34, "ppg": 25, "rpg": 8, "apg": 6,
                "tpm": 2, "ppg_l5": 25, "rpg_l5": 8, "apg_l5": 6,
                "cur_gp": 0, "baseline_season": 2026}

    def test_started_postponed_unmatched_all_excluded(self):
        # Clock-relative: get_nba_data() uses the real wall clock, so build the
        # snapshot against "now" rather than a hardcoded date (otherwise the
        # future-timestamp guard legitimately flags a fixed date as stale).
        now = datetime.now(timezone.utc)
        et = app._et_today_str()
        future = (now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        snap = {
            "today": et, "ts": now.timestamp(), "schedule_fetched": True,
            "games": [
                {"game_id": "g1", "home_id": "1", "away_id": "2", "state": "in",
                 "status_name": "STATUS_IN_PROGRESS", "status": "5:00 - 1st"},
                {"game_id": "g2", "home_id": "3", "away_id": "4", "state": "pre",
                 "status_name": "STATUS_SCHEDULED", "status": "10:00 PM ET",
                 "start": future},
                {"game_id": "g3", "home_id": "5", "away_id": "6", "state": "pre",
                 "status_name": "STATUS_POSTPONED", "status": "Postponed"},
            ],
            "players": [
                self._player("Started", "1"),        # game in progress -> blocked
                self._player("Pregame", "3"),         # clean upcoming -> shown
                self._player("Postponed", "5"),       # postponed -> blocked
                self._player("Unmatched", "99"),      # no game -> blocked
            ],
            "team_defense": {},
        }
        d = self._run(snap)
        by = {p["name"]: p for p in d["players"]}
        self.assertTrue(by["Started"]["tipped"])
        self.assertFalse(by["Started"]["pregame_ok"])
        self.assertEqual(by["Postponed"]["block_reason"], "postponed")
        self.assertFalse(by["Postponed"]["pregame_ok"])
        self.assertEqual(by["Unmatched"]["block_reason"], "unmatched")
        self.assertFalse(by["Unmatched"]["pregame_ok"])
        self.assertTrue(by["Pregame"]["pregame_ok"])
        self.assertEqual(by["Pregame"]["block_reason"], None)
        # opening-night confidence still applied
        self.assertNotEqual(by["Pregame"]["confidence"], "high")
        self.assertEqual(d["data_status"]["state"], "ok")


class TestNoBaselineProjectionDisplay(unittest.TestCase):
    """A missing historical player log must not produce a displayed 0.0 pick.

    Guard all five NBA prop tabs through the shared buildNBA filtering path.
    This structural regression check requires no browser/runtime dependency.
    """

    def test_shared_nba_cards_require_nonzero_baseline_games(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
        code = html.split("function buildNBA(prop){", 1)[1].split("// ── NFL TAB", 1)[0]
        card_filter = code.split("var rows =", 1)[1].split("}).sort", 1)[0]
        self.assertIn("if (!(Number(p.gp) > 0)) return false;", card_filter)
        self.assertIn("p[cfg.field]==null", card_filter)


if __name__ == "__main__":
    unittest.main(verbosity=2)
