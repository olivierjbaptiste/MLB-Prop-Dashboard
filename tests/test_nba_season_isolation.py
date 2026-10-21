#!/usr/bin/env python3
"""Batch 1 Stage A — NBA season isolation, current-season tracking, season-type
classification, and opening-night confidence. Pure/offline (no network)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nba
import nba_model


# ── ESPN athlete-gamelog fixtures ──────────────────────────────────────
_NAMES = ["minutes", "points", "rebounds", "assists", "threePointFieldGoalsMade"]
_LABELS = ["MIN", "PTS", "REB", "AST", "3PT"]


def _events(n, tag, mn, pts, reb=8, ast=6, tpm=2, day0=1):
    evs, meta = [], {}
    for i in range(n):
        eid = f"{tag}-{i}"
        evs.append({"eventId": eid, "stats": [str(mn), str(pts), str(reb), str(ast), str(tpm)]})
        meta[eid] = {"gameDate": f"2026-04-{((day0 + i) % 28) + 1:02d}"}
    return evs, meta


def _gamelog(season_blocks):
    """season_blocks: list of (displayName, events). Returns an ESPN-shaped dict."""
    meta = {}
    stypes = []
    for name, (evs, m) in season_blocks:
        meta.update(m)
        stypes.append({"displayName": name, "categories": [{"events": evs}]})
    return {"names": _NAMES, "labels": _LABELS, "events": meta, "seasonTypes": stypes}


def _player_from(summary, status="active"):
    p = {"status": status, "team_id": "1"}
    p.update({k: summary.get(k) for k in
              ("gp", "mpg", "min_l5", "ppg", "rpg", "apg", "tpm",
               "ppg_l5", "rpg_l5", "apg_l5", "cur_gp", "baseline_season")})
    return p


NEUTRAL = {"games": [], "team_defense": {}}


class TestGamelogParsing(unittest.TestCase):
    def test_season_type_and_season_tagging(self):
        data = _gamelog([
            ("2025-26 Regular Season", _events(3, "r", 30, 20)),
            ("2025 Postseason", _events(2, "p", 32, 28)),
            ("2026 Preseason", _events(1, "e", 10, 8)),
        ])
        rows = nba._gamelog_games(data)
        self.assertEqual(len(rows), 6)
        self.assertEqual({g["season_type"] for g in rows}, {"REG", "POST", "PRE"})
        reg = [g for g in rows if g["season_type"] == "REG"]
        self.assertTrue(all(g["season"] == 2026 for g in reg))

    def test_dedupe_same_game_across_categories(self):
        evs, meta = _events(2, "g", 30, 20)
        data = {"names": _NAMES, "labels": _LABELS, "events": meta, "seasonTypes": [
            {"displayName": "2025-26 Regular Season",
             "categories": [{"events": evs}, {"events": evs}]}]}  # same events twice
        rows = nba._gamelog_games(data)
        self.assertEqual(len(rows), 2)   # not 4

    def test_season_from_display(self):
        self.assertEqual(nba._season_from_display("2025-26 Regular Season"), 2026)
        self.assertEqual(nba._season_from_display("2024 Postseason"), 2024)
        self.assertIsNone(nba._season_from_display("Career"))


class TestSummarizeReg(unittest.TestCase):
    def test_excludes_preseason_playoffs_and_old_seasons(self):
        data = _gamelog([
            ("2025-26 Regular Season", _events(82, "r25", 32, 25)),
            ("2025 Postseason", _events(12, "p25", 34, 30)),       # excluded
            ("2026 Preseason", _events(4, "e26", 10, 8)),          # excluded
            ("2024-25 Regular Season", _events(70, "r24", 30, 18)),  # older, excluded from avgs
        ])
        s = nba._summarize_reg(nba._gamelog_games(data), current_season=2027)
        self.assertEqual(s["gp"], 82)                 # never > 82
        self.assertEqual(s["baseline_season"], 2026)  # most recent REG season
        self.assertEqual(s["ppg"], 25.0)              # excludes playoff 30 / preseason 10 / old 18
        self.assertEqual(s["mpg"], 32.0)
        self.assertEqual(s["cur_gp"], 0)              # no 2026-27 games

    def test_gp_never_exceeds_82(self):
        # two full REG seasons present — gp must reflect ONE season
        data = _gamelog([
            ("2025-26 Regular Season", _events(82, "a", 30, 20)),
            ("2024-25 Regular Season", _events(82, "b", 30, 20)),
        ])
        s = nba._summarize_reg(nba._gamelog_games(data), current_season=2027)
        self.assertLessEqual(s["gp"], 82)
        self.assertEqual(s["baseline_season"], 2026)

    def test_current_season_games_counted_separately(self):
        data = _gamelog([
            ("2026-27 Regular Season", _events(8, "cur", 34, 28)),   # current season
            ("2025-26 Regular Season", _events(82, "prev", 32, 24)),
        ])
        s = nba._summarize_reg(nba._gamelog_games(data), current_season=2027)
        self.assertEqual(s["cur_gp"], 8)
        # with >=3 current games, baseline switches to the current season
        self.assertEqual(s["baseline_season"], 2027)
        self.assertEqual(s["gp"], 8)
        self.assertEqual(s["ppg"], 28.0)

    def test_baseline_stays_prior_when_current_sample_too_thin(self):
        # only 2 current-season games -> baseline should remain last season (stable)
        data = _gamelog([
            ("2026-27 Regular Season", _events(2, "cur", 40, 50)),   # noisy 2-game sample
            ("2025-26 Regular Season", _events(82, "prev", 32, 24)),
        ])
        s = nba._summarize_reg(nba._gamelog_games(data), current_season=2027)
        self.assertEqual(s["cur_gp"], 2)
        self.assertEqual(s["baseline_season"], 2026)   # not 2027
        self.assertEqual(s["ppg"], 24.0)               # stable last-season number, not 50

    def test_empty_and_dnp_only_safe(self):
        self.assertEqual(nba._summarize_reg([], current_season=2027)["gp"], 0)
        # a REG game with 0 minutes (DNP) must not count
        evs, meta = _events(1, "dnp", 0, 0)
        data = {"names": _NAMES, "labels": _LABELS, "events": meta, "seasonTypes": [
            {"displayName": "2025-26 Regular Season", "categories": [{"events": evs}]}]}
        s = nba._summarize_reg(nba._gamelog_games(data), current_season=2027)
        self.assertEqual(s["gp"], 0)
        self.assertEqual(s["cur_gp"], 0)


class TestGameSeasonType(unittest.TestCase):
    def test_scoreboard_season_type_mapping(self):
        self.assertEqual(nba._game_season_type({"season": {"type": 1}}), "preseason")
        self.assertEqual(nba._game_season_type({"season": {"type": 2}}), "regular")
        self.assertEqual(nba._game_season_type({"season": {"type": 3}}), "postseason")

    def test_missing_or_unknown_season_type_safe(self):
        self.assertEqual(nba._game_season_type({}), "unknown")
        self.assertEqual(nba._game_season_type({"season": {}}), "unknown")
        self.assertEqual(nba._game_season_type({"season": {"type": 99}}), "unknown")
        self.assertEqual(nba._game_season_type({"season": "bad"}), "unknown")


class TestOpeningNightConfidence(unittest.TestCase):
    def _starter_summary(self, cur_games):
        blocks = [("2025-26 Regular Season", _events(80, "prev", 34, 26))]
        if cur_games:
            blocks.insert(0, ("2026-27 Regular Season", _events(cur_games, "cur", 34, 26)))
        return nba._summarize_reg(nba._gamelog_games(_gamelog(blocks)), current_season=2027)

    def test_zero_current_games_is_not_high(self):
        p = _player_from(self._starter_summary(0))
        nba_model.project_player(p, NEUTRAL)
        self.assertNotEqual(p["confidence"], "high")
        self.assertEqual(p["confidence"], "low")
        self.assertEqual(p["evidence"], "historical")

    def test_thin_current_sample_capped_at_med(self):
        p = _player_from(self._starter_summary(3))   # 3 current games
        nba_model.project_player(p, NEUTRAL)
        self.assertIn(p["confidence"], ("med", "low"))
        self.assertNotEqual(p["confidence"], "high")
        self.assertEqual(p["evidence"], "thin")

    def test_established_current_starter_can_be_high(self):
        p = _player_from(self._starter_summary(25))   # solid current sample
        nba_model.project_player(p, NEUTRAL)
        self.assertEqual(p["confidence"], "high")
        self.assertEqual(p["evidence"], "current")

    def test_out_player_zeroed(self):
        p = _player_from(self._starter_summary(25), status="Out")
        nba_model.project_player(p, NEUTRAL)
        self.assertEqual(p["confidence"], "out")
        self.assertFalse(p["playable"])
        self.assertEqual(p["proj_pts"], 0.0)


class TestSeasonSchemaRobustness(unittest.TestCase):
    """Issue #3: a seasonType displayName without a year ('Regular Season') must
    not leave the season unidentified and zero out the summary."""

    def test_bare_regular_season_uses_default_season(self):
        evs, meta = _events(80, "r", 32, 25)
        data = {"names": _NAMES, "labels": _LABELS, "events": meta, "seasonTypes": [
            {"displayName": "Regular Season", "categories": [{"events": evs}]}]}
        # no year in the label -> caller's requested season is used
        rows = nba._gamelog_games(data, default_season=2026)
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(g["season"] == 2026 for g in rows))
        self.assertTrue(all(g["season_type"] == "REG" for g in rows))
        s = nba._summarize_reg(rows, current_season=2027)
        self.assertEqual(s["gp"], 80)
        self.assertEqual(s["baseline_season"], 2026)
        self.assertEqual(s["cur_gp"], 0)

    def test_bare_label_no_default_falls_back_to_game_date(self):
        # dates in Apr 2026 -> 2025-26 season (end-year 2026)
        evs = [{"eventId": f"d{i}", "stats": ["30", "20", "8", "6", "2"]} for i in range(5)]
        meta = {f"d{i}": {"gameDate": "2026-04-10"} for i in range(5)}
        data = {"names": _NAMES, "labels": _LABELS, "events": meta, "seasonTypes": [
            {"displayName": "Regular Season", "categories": [{"events": evs}]}]}
        rows = nba._gamelog_games(data)          # no default season at all
        self.assertTrue(all(g["season"] == 2026 for g in rows))

    def test_season_from_date(self):
        self.assertEqual(nba._season_from_date("2026-10-21"), 2027)   # Oct -> next year
        self.assertEqual(nba._season_from_date("2026-04-10"), 2026)   # Apr -> same year
        self.assertIsNone(nba._season_from_date(""))

    def test_opening_night_player_gets_valid_historical_projection(self):
        # zero 2026-27 games; only last season present under a bare label
        evs, meta = _events(78, "r", 33, 26)
        data = {"names": _NAMES, "labels": _LABELS, "events": meta, "seasonTypes": [
            {"displayName": "Regular Season", "categories": [{"events": evs}]}]}
        s = nba._summarize_reg(nba._gamelog_games(data, default_season=2026),
                               current_season=2027)
        self.assertEqual(s["cur_gp"], 0)
        self.assertEqual(s["gp"], 78)            # NOT zero — baseline still found
        self.assertEqual(s["ppg"], 26.0)
        p = _player_from(s)
        nba_model.project_player(p, NEUTRAL)
        self.assertGreater(p["proj_pts"], 0)     # a real projection exists
        self.assertEqual(p["evidence"], "historical")
        self.assertNotEqual(p["confidence"], "high")


class TestCrossResponseDedup(unittest.TestCase):
    """Issue #1 (CRITICAL): ESPN sometimes ignores ?season= and returns the SAME
    log for every season request. get_player_log() must dedupe ACROSS responses so
    game counts aren't multiplied by the number of seasons requested, and must
    classify each game by its own date — never silently refiling a 2025-26 game as
    2026-27 just because the current season was the one requested."""

    def _patched_log(self, responder, seasons=(2027, 2026)):
        orig_fetch, orig_cand = nba._fetch_json, nba._season_candidates
        nba._fetch_json = responder
        nba._season_candidates = lambda: list(seasons)
        try:
            return nba.get_player_log("123")
        finally:
            nba._fetch_json, nba._season_candidates = orig_fetch, orig_cand

    def test_ignored_param_same_log_twice_not_doubled(self):
        # The exact reported failure: identical 80-game 2025-26 REG log returned for
        # BOTH the 2027 and 2026 requests. Before dedup this produced gp=160.
        log = _gamelog([("2025-26 Regular Season", _events(80, "r", 32, 25))])
        s = self._patched_log(lambda url, **k: log)
        self.assertEqual(s["gp"], 80)                 # NOT 160
        self.assertLessEqual(s["gp"], 82)
        self.assertEqual(s["baseline_season"], 2026)
        self.assertEqual(s["cur_gp"], 0)              # Apr-2026 dates -> prior season

    def test_distinct_seasons_both_kept(self):
        # ESPN honors the param: current-season request returns current games, prior
        # request returns prior games. Distinct event ids -> both kept, none dropped.
        cur = _gamelog([("2026-27 Regular Season", _events(6, "cur", 34, 28))])
        prev = _gamelog([("2025-26 Regular Season", _events(80, "prev", 32, 24))])
        s = self._patched_log(lambda url, **k: cur if "season=2027" in url else prev)
        self.assertEqual(s["cur_gp"], 6)
        self.assertEqual(s["baseline_season"], 2027)  # >=3 current games
        self.assertEqual(s["gp"], 6)

    def test_conflicting_metadata_date_beats_requested_season(self):
        # Bare 'Regular Season' label returned for the 2027 request, but the games
        # are dated Apr 2026 -> they MUST classify as 2026, not the requested 2027.
        evs = [{"eventId": f"c{i}", "stats": ["30", "20", "8", "6", "2"]} for i in range(40)]
        meta = {f"c{i}": {"gameDate": "2026-04-05"} for i in range(40)}
        bare = {"names": _NAMES, "labels": _LABELS, "events": meta,
                "seasonTypes": [{"displayName": "Regular Season",
                                 "categories": [{"events": evs}]}]}
        s = self._patched_log(lambda url, **k: bare)
        self.assertEqual(s["gp"], 40)                 # deduped across both requests
        self.assertEqual(s["cur_gp"], 0)              # not misfiled as 2026-27
        self.assertEqual(s["baseline_season"], 2026)

    def test_missing_season_identifiers_not_dropped_not_doubled(self):
        # No year in the label AND no event metadata (no dates). The only guarantee
        # we can make is that games are neither dropped nor counted twice.
        evs = [{"eventId": f"n{i}", "stats": ["31", "22", "7", "5", "1"]} for i in range(50)]
        bare = {"names": _NAMES, "labels": _LABELS, "events": {},
                "seasonTypes": [{"displayName": "Regular Season",
                                 "categories": [{"events": evs}]}]}
        s = self._patched_log(lambda url, **k: bare)   # same payload for both requests
        self.assertEqual(s["gp"], 50)                  # eids dedupe -> 50, not 100

    def test_unverifiable_season_never_becomes_current_evidence(self):
        """Issue (review): ESPN ignores BOTH season queries and returns the same
        80-game response with a bare 'Regular Season' label, unique event ids, and
        NO game-date metadata. The response does not prove any 2026-27 games were
        played, so the player must NOT be scored as HIGH / current. The games stay
        usable as a historical baseline (a projection still exists)."""
        evs = [{"eventId": f"u{i}", "stats": ["34", "27", "9", "7", "2"]} for i in range(80)]
        bare = {"names": _NAMES, "labels": _LABELS, "events": {},   # no dates at all
                "seasonTypes": [{"displayName": "Regular Season",
                                 "categories": [{"events": evs}]}]}
        s = self._patched_log(lambda url, **k: bare)   # same payload for 2027 AND 2026
        # game-count fields
        self.assertEqual(s["gp"], 80)                  # deduped, usable baseline
        self.assertLessEqual(s["gp"], 82)
        self.assertEqual(s["cur_gp"], 0)               # NOT 80 — season unverified
        self.assertIsNone(s["baseline_season"])        # season can't be confirmed
        # resulting confidence label
        p = _player_from(s)
        nba_model.project_player(p, NEUTRAL)
        self.assertNotEqual(p["confidence"], "high")
        self.assertEqual(p["evidence"], "historical")  # not "current"
        self.assertGreater(p["proj_pts"], 0)           # projection still produced

    def test_dated_current_games_still_verified_as_current(self):
        # Control: when the current-season games DO carry dates in the new season,
        # they remain verified current-season evidence and can reach full tiers.
        evs = [{"eventId": f"v{i}", "stats": ["34", "27", "9", "7", "2"]} for i in range(20)]
        meta = {f"v{i}": {"gameDate": "2026-11-05"} for i in range(20)}   # 2026-27
        data = {"names": _NAMES, "labels": _LABELS, "events": meta,
                "seasonTypes": [{"displayName": "Regular Season",
                                 "categories": [{"events": evs}]}]}
        s = self._patched_log(lambda url, **k: data)
        self.assertEqual(s["cur_gp"], 20)              # dates verify the season
        self.assertEqual(s["baseline_season"], 2027)
        p = _player_from(s)
        nba_model.project_player(p, NEUTRAL)
        self.assertEqual(p["evidence"], "current")

    def test_all_fetches_fail_returns_none(self):
        s = self._patched_log(lambda url, **k: None)
        self.assertIsNone(s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
