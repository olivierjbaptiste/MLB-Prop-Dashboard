# NBA Batch 1 — no-historical-data UI safety fix

The October 20 ESPN rehearsal returned one player with `gp=0` and `proj_pts=0.0` (Jaden Akins). The model outputs 0.0 when historical production is missing; it is **not** an empirically supported prediction of zero points. The pre-existing NBA UI filter allowed this value because it is not null.

This patch changes only the shared NBA projection-card filter in `templates/index.html` to require `Number(p.gp)>0` before showing a player on Points, PRA, Rebounds, Assists, and 3PM tabs. Team ratings, NFL, MLB, and Live Scores are not changed. Players with a valid baseline remain visible. An offline structural regression check was added to `tests/test_nba_display_safety.py`.

The patch does not change the underlying model or fix concerns with weighting last-season final five games. NBA model accuracy remains unvalidated and historical projections should be clearly labeled low confidence at opening night.

To apply from isolated `Diamond-NBA-Batch1-Release` clone: expand ZIP in that folder with `Expand-Archive -Force`; verify `git status -sb`, then run `python -m unittest discover -s tests -p "test_*.py"`. Do not apply to production or push before reviewing full data and rendering.
