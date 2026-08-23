"""
IMS 2.0 - Audit time-filter unit tests
=======================================
`settings._audit_time_filter` turns inclusive YYYY-MM-DD bounds into a Mongo
`timestamp` range clause for the SUPERADMIN Activity Log query. Pure + DB-free.

BUG-104 (round 3): the typed bounds are IST calendar days, but the audit
`timestamp` is a stored naive-UTC instant (audit_activity middleware stamps
datetime.utcnow()), so each bound must move BACKWARD 5h30m through
ist_day_start_utc. IST midnight on day D IS 18:30 UTC on D-1; the end of IST
day D is the next IST day start minus one microsecond. These tests pin that
shape exactly -- the old naive-midnight clause hid every 00:00-05:30-IST
action on the first requested day and showed the same band after the last.
Fail-soft behaviour (an unparseable date is ignored, never raised) unchanged.
"""

import os
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers.settings import _audit_time_filter  # noqa: E402


def test_no_bounds_returns_empty():
    assert _audit_time_filter(None, None) == {}
    assert _audit_time_filter("", "") == {}


def test_start_only_is_gte_the_ist_day_start_in_utc():
    # IST midnight 1-May is 18:30 UTC on 30 April -- the bound moves BACKWARD.
    flt = _audit_time_filter("2026-05-01", None)
    assert flt == {"timestamp": {"$gte": datetime(2026, 4, 30, 18, 30, 0)}}


def test_end_only_is_lte_end_of_the_ist_day_in_utc():
    # End of IST 1-May = IST midnight 2-May (18:30 UTC 1-May) minus 1 us.
    flt = _audit_time_filter(None, "2026-05-01")
    assert flt == {"timestamp": {"$lte": datetime(2026, 5, 1, 18, 29, 59, 999999)}}


def test_both_bounds_span_full_inclusive_ist_range():
    flt = _audit_time_filter("2026-05-01", "2026-05-07")
    clause = flt["timestamp"]
    assert clause["$gte"] == datetime(2026, 4, 30, 18, 30, 0)
    assert clause["$lte"] == datetime(2026, 5, 7, 18, 29, 59, 999999)


def test_single_day_from_equals_to_covers_the_whole_ist_day():
    flt = _audit_time_filter("2026-05-03", "2026-05-03")
    clause = flt["timestamp"]
    # A from==to range must return that entire IST day's rows: an action at
    # 00:30 IST 3 May is stored 19:00 UTC 2 May and MUST fall inside; an
    # action at 00:30 IST 4 May (19:00 UTC 3 May) must fall outside.
    assert clause["$gte"] == datetime(2026, 5, 2, 18, 30, 0)
    assert clause["$lte"] == datetime(2026, 5, 3, 18, 29, 59, 999999)
    assert clause["$gte"] <= datetime(2026, 5, 2, 19, 0, 0) <= clause["$lte"]
    assert not (clause["$gte"] <= datetime(2026, 5, 3, 19, 0, 0) <= clause["$lte"])
    # Afternoon positive control: 16:30 IST 3 May == 11:00 UTC 3 May is in.
    assert clause["$gte"] <= datetime(2026, 5, 3, 11, 0, 0) <= clause["$lte"]


def test_adjacent_ranges_tile_without_gap_or_overlap():
    """The seam: day D's upper bound is exactly 1 microsecond before day
    D+1's lower bound, so no audit row is dropped or double-listed when the
    operator pages through consecutive days."""
    d3 = _audit_time_filter("2026-05-03", "2026-05-03")["timestamp"]
    d4 = _audit_time_filter("2026-05-04", "2026-05-04")["timestamp"]
    from datetime import timedelta

    assert d4["$gte"] - d3["$lte"] == timedelta(microseconds=1)


def test_unparseable_date_is_ignored_not_fatal():
    # Garbage start, valid end -> only the end clause survives; no exception.
    flt = _audit_time_filter("not-a-date", "2026-05-07")
    assert flt == {"timestamp": {"$lte": datetime(2026, 5, 7, 18, 29, 59, 999999)}}
    # Both garbage -> empty (query widens rather than 500s).
    assert _audit_time_filter("nope", "also-nope") == {}
