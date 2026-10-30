import datetime
from unittest import mock

import dateutil.tz
import pytest
from typing import cast

from lib.media.dates import to_seconds, local_to_utc, utc_to_local


def test_to_seconds_reads_hours_minutes_and_seconds():
    """Strings of hours, minutes and seconds convert to seconds."""
    assert to_seconds("1:01:01") == 3661
    assert to_seconds("01:01") == 61
    assert to_seconds("01") == 1


class _WindowsLikeLocal(datetime.tzinfo):
    """New York time that refuses dates before 1971, as Windows does near the epoch."""

    _zone = cast(datetime.tzinfo, dateutil.tz.gettz("America/New_York"))

    def _check(self, dt):
        """Check a date, refusing one before 1971 as Windows does."""
        if dt is not None and dt.year < 1971:
            raise OSError(22, "Invalid argument")

    def utcoffset(self, dt):
        """New York's UTC offset, refused before 1971."""
        self._check(dt)
        return self._zone.utcoffset(dt)

    def dst(self, dt):
        """New York's daylight saving offset, refused before 1971."""
        self._check(dt)
        return self._zone.dst(dt)

    def tzname(self, dt):
        """New York's zone name."""
        return self._zone.tzname(dt)


@pytest.mark.parametrize("local, utc", [
    ("1955-07-04 20:00:00", "1955-07-05 00:00:00+00:00"),
    ("1969-12-31 23:00:00", "1970-01-01 04:00:00+00:00"),
    ("1970-07-01 12:00:00", "1970-07-01 16:00:00+00:00"),
    ("2026-10-01 12:00:00", "2026-10-01 16:00:00+00:00"),
])
def test_local_to_utc_around_the_epoch(local, utc):
    """Local times convert to UTC, borrowing the 1971 offset where Windows refuses."""
    with mock.patch("lib.media.dates.tzlocal", _WindowsLikeLocal):
        assert local_to_utc(local) == utc


@pytest.mark.parametrize("utc, local", [
    ("1955-07-05T00:00:00.000Z", "1955-07-04 20:00:00"),
    ("1970-01-01T04:00:00.000Z", "1969-12-31 23:00:00"),
    ("1970-07-01T16:00:00.000Z", "1970-07-01 12:00:00"),
    ("2026-10-01T16:00:00.000Z", "2026-10-01 12:00:00"),
])
def test_utc_to_local_around_the_epoch(utc, local):
    """UTC times convert to local, borrowing the 1971 offset where Windows refuses."""
    with mock.patch("lib.media.dates.tzlocal", _WindowsLikeLocal):
        assert utc_to_local(utc) == local
