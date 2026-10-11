#!/usr/bin/env python3
"""
tzutil.py — correct US Eastern time for Diamond Analytics.

Replaces the old fixed `timezone(timedelta(hours=-4))`, which was EDT-only and
drifted by an hour once daylight saving ended (first Sunday of November). This
uses the real America/New_York zone when the IANA database is available
(Linux runner, Render) and falls back to a self-contained DST-aware rule if it
isn't (e.g. a Windows box without the `tzdata` package), so games, odds, and
snapshot dates stay correct across the DST change everywhere.
"""

import datetime as _dt

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only when no IANA tz database is present
    class _USEastern(_dt.tzinfo):
        """US Eastern with the standard DST rule: 2nd Sun of March 02:00 →
        1st Sun of November 02:00 is EDT (-4), otherwise EST (-5)."""
        @staticmethod
        def _nth_sunday(year, month, n):
            d = _dt.date(year, month, 1)
            first_sun = d + _dt.timedelta(days=(6 - d.weekday()) % 7)
            return first_sun + _dt.timedelta(weeks=n - 1)

        def _is_edt(self, dt):
            y = dt.year
            start = self._nth_sunday(y, 3, 2)
            end = self._nth_sunday(y, 11, 1)
            return start <= dt.date() < end

        def utcoffset(self, dt):
            return _dt.timedelta(hours=-4 if self._is_edt(dt) else -5)

        def dst(self, dt):
            return _dt.timedelta(hours=1) if self._is_edt(dt) else _dt.timedelta(0)

        def tzname(self, dt):
            return "EDT" if self._is_edt(dt) else "EST"

    ET = _USEastern()


def et_now():
    return _dt.datetime.now(ET)


def et_today():
    return et_now().date()
