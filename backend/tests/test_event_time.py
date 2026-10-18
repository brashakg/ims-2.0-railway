"""Timezone-only event encoding: fixed instants, not calendar-policy changes."""
from datetime import datetime, timedelta, timezone

import pytest

from api.utils import event_time as et


@pytest.mark.parametrize("value", [
    "2026-03-31T20:00:00", "2026-03-31T20:00:00Z",
    "2026-03-31T20:00:00+00:00", "2026-04-01T01:30:00+05:30",
    "2026-03-31T16:00:00-04:00", datetime(2026, 3, 31, 20),
    datetime(2026, 4, 1, 1, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
])
def test_same_event_instant_normalizes_without_losing_offset(value):
    assert et.parse_event_utc(value) == datetime(2026, 3, 31, 20, tzinfo=timezone.utc)
    assert et.parse_event_utc_naive(value) == datetime(2026, 3, 31, 20)


@pytest.mark.parametrize("value", [None, "", "bad", 0, [], {},
    "0001-01-01T00:00:00+05:30", "9999-12-31T23:59:59-04:00"])
def test_missing_or_malformed_event_has_no_invented_instant(value):
    assert et.parse_event_utc(value) is None


def test_deliberate_legacy_ist_can_be_read_only_with_explicit_frame():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert et.parse_event_utc("2026-04-01T01:30:00", naive_timezone=ist) == datetime(
        2026, 3, 31, 20, tzinfo=timezone.utc)


def test_new_clock_requests_timezone_instead_of_server_local_wall_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return datetime(2026, 3, 31, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(et, "datetime", Clock)
    assert et.utc_now_iso() == "2026-03-31T20:00:00+00:00"


def test_shared_bson_and_string_sample_name_one_instant_without_changing_type(monkeypatch):
    monkeypatch.setattr(et, "utc_now", lambda: datetime(2026, 3, 31, 20, tzinfo=timezone.utc))
    bson_sample = et.utc_now().replace(tzinfo=None)
    assert bson_sample == datetime(2026, 3, 31, 20) and bson_sample.tzinfo is None
    assert et.utc_event_iso(bson_sample) == "2026-03-31T20:00:00+00:00"
    assert et.parse_event_utc_naive(et.utc_event_iso(bson_sample)) == bson_sample
