#!/usr/bin/env python3
"""
tzutil.py — correct US Eastern time for Diamond Analytics.

Replaces the old fixed `timezone(timedelta(hours=-4))`, which was EDT-only and
drifted by an hour once daylight saving ended. Uses the real America/New_York
zone when the IANA database is available (Linux runner, Render) and falls back
to a self-contained, transition-hour-accurate DST rule otherwise (e.g. a
Windows box without the `tzdata` package).

The fallback class `_USEastern` is always defined (not only when zoneinfo is
missing) so it can be unit-tested on any platform.
"""

import datetime as _dt


class _USEastern(_dt.tzinfo):
    """US Eastern with the federal DST rule, accurate to the transition hour:
    EDT (UTC-4) from 02:00 on the 2nd Sunday of March to 02:00 on the 1st
    Sunday of November; EST (UTC-5) the rest of the year."""

    @staticmethod
    def _nth_sunday(year, month, n):
        first = _dt.date(year, month, 1)
        first_sunday = first + _dt.timedelta(days=(6 - first.weekday()) % 7)
        return first_sunday + _dt.timedelta(weeks=n - 1)

    def _transitions(self, year):
        start = _dt.datetime.combine(self._nth_sunday(year, 3, 2), _dt.time(2, 0))
        end = _dt.datetime.combine(self._nth_sunday(year, 11, 1), _dt.time(2, 0))
        return start, end

    def _is_edt(self, dt):
        if dt is None:
            return False
        naive = dt.replace(tzinfo=None)
        start, end = self._transitions(naive.year)
        return start <= naive < end

    def utcoffset(self, dt):
        return _dt.timedelta(hours=-4 if self._is_edt(dt) else -5)

    def dst(self, dt):
        return _dt.timedelta(hours=1) if self._is_edt(dt) else _dt.timedelta(0)

    def tzname(self, dt):
        return "EDT" if self._is_edt(dt) else "EST"


try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only when no IANA tz database is present
    ET = _USEastern()


def et_now():
    return _dt.datetime.now(ET)


def et_today():
    return et_now().date()
