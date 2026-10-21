# Diamond Analytics — NBA Batch 1 (final, supersedes `diamond-nba-batch1-corrected.zip`)

This is the final Batch 1 package. It keeps every earlier change (Stage A model/
data corrections, Stage B opening-night display safety, and the two prior
correction passes) and fixes the three reproduced edge cases from your latest
review. Only those three corrections were made — no other behavior changed, no
unrelated features added.

Changed files vs production `main` (repo-relative):
- `app.py`
- `nba.py`
- `nba_model.py`
- `templates/index.html`
- `tests/test_nba_season_isolation.py`
- `tests/test_nba_display_safety.py`

Verified byte-identical to `main` (untouched): `scores.py` (Live Scores),
`nfl.py`, `nfl_model.py`, `dashboard.py` (MLB). Experimental models,
`phase1-fixes`, and `diamond-automation` were not touched.

---

## Fix 1 — Opening-night confidence can no longer go falsely HIGH when season metadata is missing

**Reproduced:** ESPN ignores both season queries and returns the same 80-game
response with a bare `Regular Season` label, unique event ids, and **no game-date
metadata**. Previously `get_player_log()` returned `gp=80, cur_gp=80,
baseline_season=2027` and the player was labeled **HIGH / current**, even though
nothing in the response proved any 2026-27 games were played.

**Root cause:** the season was derived as `header-year → game-date →
default_season`, and `default_season` is just the *requested query year*. When the
response couldn't establish the season, the requested current year was used and
then counted as current-season evidence.

**Fix (`nba.py`):** each parsed game now carries `season_verified`, which is true
**only** when the season came from a label year or the game's own date — never from
`default_season` alone. `_summarize_reg()` now:
- counts `cur_gp` only from **verified** current-season games (the query year by
  itself never raises it), and
- reports `baseline_season = None` when the baseline it lands on is the current
  season but none of its games are verified — so the UI shows a neutral
  "last-season baseline" instead of claiming a season it can't confirm.

The games are still used for a **historical** projection (nothing is dropped), so a
valid projection still exists — it simply isn't dressed up as current-season
evidence.

**Result on the reproduced input:** `gp=80, cur_gp=0, baseline_season=None`,
confidence **low**, evidence **historical**, `proj_pts > 0`. When the current-season
games *do* carry new-season dates, they remain verified and can still reach the
full tier (control test included).

New tests (`tests/test_nba_season_isolation.py::TestCrossResponseDedup`):
- `test_unverifiable_season_never_becomes_current_evidence` — asserts the game-count
  fields (`gp`, `cur_gp`, `baseline_season`) **and** the resulting confidence/
  evidence label.
- `test_dated_current_games_still_verified_as_current` — control: dated current-
  season games stay verified → evidence `current`.

## Fix 2 — Malformed ESPN scoreboard responses no longer masquerade as off-days

**Reproduced:** `_fetch_json()` returns `{}`. `get_games()` set
`schedule_fetched=True` (because the payload wasn't `None`) while producing zero
games, so the board looked like a legitimate off-day.

**Fix (`nba.py`):** `get_games()` now treats a fetch as successful **only** when the
response is a dict that actually carries an `events` **list**. A malformed payload
(`{}`, a missing/!list `events`, or `None`) sets `schedule_fetched=False`, which
drives `_nba_status()` to `source_error`. A genuine off-day — `{"events": []}` —
still sets `schedule_fetched=True` and reports `no_games`.

New tests (`tests/test_nba_display_safety.py::TestScheduleFetchValidity`): `{}`,
missing `events` key, `None`, and the `{"events": []}` off-day control — plus an
end-to-end check that a `{}` response yields `source_error`, not `no_games`.

## Fix 3 — Stale empty snapshots no longer report "no games today"

**Reproduced:** an empty snapshot with today's date, `schedule_fetched=True`, and a
timestamp 72h old returned `no_games`. The max-age bound was enforced only on
populated snapshots.

**Fix (`app.py`):** the empty-snapshot off-day branch of `_nba_status()` now applies
the same `MAX_SNAPSHOT_AGE_H` (30h) bound as the populated branch. A today-dated but
hours-old empty snapshot returns `stale`; a recent empty snapshot for today still
returns `no_games`.

New tests (`tests/test_nba_display_safety.py::TestNbaStatusFreshness`):
`test_old_empty_snapshot_today_is_stale_not_no_games` and the recent-off-day control
`test_fresh_empty_snapshot_today_is_no_games`.

---

## Test results

Command: `python -m unittest discover -s tests -p "test_*.py"` (with
`DIAMOND_NO_STARTUP=1`, the production-neutral env guard that skips the MLB startup
pull for importability; it is a no-op in production).

```
Ran 85 tests — OK
```

Breakdown: 64 NBA tests across the two shipped files + 21 existing Live Scores
tests (`test_scores*.py`, unchanged and still passing, confirming Live Scores is
intact). The fail-closed rendering and valid-slate rendering were re-checked after
these changes (valid snapshot → `ok`, players `pregame_ok`, cards show; the
baseline change does not affect the render path).

## Mock vs. real ESPN testing — disclosure

All automated tests are **offline / mock-based**: ESPN-shaped fixtures are fed
directly into the pure parsing/classification functions, with `_fetch_json`
monkeypatched — no network calls, no paid API usage (both required by the task
constraints and by the sandbox, which blocks ESPN/espncdn).

- **Covered by mocks (high confidence):** season verification and the
  current-season-evidence gate, cross-response dedup, schedule-fetch validity,
  snapshot freshness/age/classification, started/postponed detection, fail-closed
  rendering, DST fallback.
- **Not exercised against live ESPN here:** ESPN's exact current response shape
  (field names, whether `?season=` is honored today, event-id/date presence). This
  is the right subject for your planned **real ESPN NBA data pull in an isolated
  local workspace** — run `nba.build_nba_data()` once and confirm, for a sample of
  players, `gp ≤ 82`, that `cur_gp` only rises when games carry new-season dates,
  and that `schedule_fetched`/timestamps populate as expected — before production
  integration.

## Integration

Drop-in replacements at the repo-relative paths shown. Stage and commit them
yourself (no `git add -A`). `scores.py`, `nfl*.py`, and `dashboard.py` are
unchanged and need not be touched. Paths are kept repository-relative so the files
copy cleanly into your future NBA release branch.
