from datetime import datetime
from zoneinfo import ZoneInfo

import timeutil

LA = "America/Los_Angeles"


def test_local_afternoon_becomes_utc():
    # Thursday 2pm in LA before DST ends is UTC-7.
    assert timeutil.to_utc("2026-10-29T14:00", LA) == "2026-10-29T21:00:00Z"


def test_dst_change_shifts_utc_offset():
    # US DST ends 2026-11-01. Same wall-clock time is an hour later in UTC afterwards.
    # A fixed offset would book the meeting an hour off.
    assert timeutil.to_utc("2026-11-02T14:00", LA) == "2026-11-02T22:00:00Z"


def test_explicit_offset_wins_over_session_tz():
    assert timeutil.to_utc("2026-10-29T14:00+01:00", LA) == "2026-10-29T13:00:00Z"


def test_day_range_covers_whole_local_day():
    # "Tomorrow" in LA must not cut off evening meetings that fall on the next UTC date.
    assert timeutil.day_range_utc("2026-10-28", "2026-10-28", LA) == (
        "2026-10-28T07:00:00Z", "2026-10-29T07:00:00Z")


def test_date_context_names_weekdays():
    now = datetime(2026, 10, 27, 9, 0, tzinfo=ZoneInfo(LA))
    ctx = timeutil.date_context(LA, now)
    assert "Tuesday 2026-10-27 09:00" in ctx
    assert "Thu 2026-10-29" in ctx
