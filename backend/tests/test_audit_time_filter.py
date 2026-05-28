"""
IMS 2.0 - Audit time-filter unit tests
=======================================
`settings._audit_time_filter` turns inclusive YYYY-MM-DD bounds into a Mongo
`timestamp` range clause for the SUPERADMIN Activity Log query. Pure + DB-free:
exercises the boundary math (end-of-day on the upper bound) and the fail-soft
behaviour (an unparseable date is ignored, never raised).
"""

import os
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.routers.settings import _audit_time_filter  # noqa: E402


def test_no_bounds_returns_empty():
    assert _audit_time_filter(None, None) == {}
    assert _audit_time_filter("", "") == {}


def test_start_only_is_gte_midnight():
    flt = _audit_time_filter("2026-05-01", None)
    assert flt == {"timestamp": {"$gte": datetime(2026, 5, 1, 0, 0, 0)}}


def test_end_only_is_lte_end_of_day():
    flt = _audit_time_filter(None, "2026-05-01")
    assert flt == {"timestamp": {"$lte": datetime(2026, 5, 1, 23, 59, 59, 999999)}}


def test_both_bounds_span_full_inclusive_range():
    flt = _audit_time_filter("2026-05-01", "2026-05-07")
    clause = flt["timestamp"]
    assert clause["$gte"] == datetime(2026, 5, 1, 0, 0, 0)
    assert clause["$lte"] == datetime(2026, 5, 7, 23, 59, 59, 999999)


def test_single_day_from_equals_to_covers_whole_day():
    flt = _audit_time_filter("2026-05-03", "2026-05-03")
    clause = flt["timestamp"]
    # A from==to range must still return that entire day's rows.
    assert clause["$gte"] == datetime(2026, 5, 3, 0, 0, 0)
    assert clause["$lte"] == datetime(2026, 5, 3, 23, 59, 59, 999999)


def test_unparseable_date_is_ignored_not_fatal():
    # Garbage start, valid end -> only the end clause survives; no exception.
    flt = _audit_time_filter("not-a-date", "2026-05-07")
    assert flt == {"timestamp": {"$lte": datetime(2026, 5, 7, 23, 59, 59, 999999)}}
    # Both garbage -> empty (query widens rather than 500s).
    assert _audit_time_filter("nope", "also-nope") == {}
