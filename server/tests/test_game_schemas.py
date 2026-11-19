"""Response formatting for the games API. No database needed."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.app.schemas.game import format_clock


@pytest.mark.parametrize(
    "utc_time, expected",
    [
        # Indiana is UTC-5 in November.
        (datetime(2026, 11, 20, 16, 30, tzinfo=timezone.utc), "11:30 AM"),
        (datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc), "12:00 PM"),
        (datetime(2026, 11, 20, 18, 5, tzinfo=timezone.utc), "1:05 PM"),
        (datetime(2026, 11, 21, 5, 15, tzinfo=timezone.utc), "12:15 AM"),
    ],
)
def test_format_clock_uses_campus_time(utc_time, expected):
    assert format_clock(utc_time) == expected
