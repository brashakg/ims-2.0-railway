"""
IMS 2.0 - Audit hash-chain canonicalisation is Mongo-round-trip STABLE
=====================================================================
The tamper-evident chain (database/repositories/audit_chain.py) computes
entry_hash over canonical_json(_hashable_view(doc)) at WRITE time, and the
verifier (GET /api/v1/audit/verify) RE-computes it over the row read back from
Mongo. For verify to ever return intact:true, the two byte-strings must be
identical -- so canonical_json must render a value the SAME before and after a
Mongo write->read round-trip.

Mongo changes datetimes in two ways:
  * it truncates microseconds to MILLISECOND precision, and
  * it stores them as UTC and (with the default tz_aware=False client) returns
    them NAIVE -- so a tz-AWARE value written by agents/proposals._now()
    (datetime.now(timezone.utc)) comes back tz-naive.

These are PURE-FUNCTION tests (no mongo needed -- they run in every
environment, unlike the db-backed test_proposal_audit_chain.py which SKIPs
without a local mongod). They simulate the round-trip by hashing the in-memory
WRITE value against the naive-UTC-millisecond value Mongo would hand back, and
assert the hash is unchanged. They are the regression guard for:
  * the microsecond->millisecond fix, and
  * the tz-aware->naive-UTC fix (the residual test_proposal_audit_chain flake:
    the test only ran -- and so only failed -- when CI's mongo:7.0 answered
    within the 1.5s connect timeout; otherwise it skipped and looked green).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.repositories.audit_chain import (  # noqa: E402
    GENESIS_HASH,
    _json_default,
    canonical_json,
    compute_entry_hash,
)


# What pymongo hands back (default tz_aware=False): the same instant, in UTC,
# truncated to millisecond precision and tz-NAIVE.
def _as_mongo_readback(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=(dt.microsecond // 1000) * 1000)


# --------------------------------------------------------------------------
# _json_default renders write-value and Mongo-read-value identically
# --------------------------------------------------------------------------

def test_json_default_tz_aware_utc_matches_naive_readback():
    """A tz-aware UTC datetime (proposals._now()) must serialise to the SAME
    string as the naive-UTC value Mongo returns -- no stray +00:00 offset."""
    aware = datetime(2026, 6, 1, 12, 0, 0, 123456, tzinfo=timezone.utc)
    assert _json_default(aware) == _json_default(_as_mongo_readback(aware))
    # And specifically: no offset suffix in the canonical form.
    assert _json_default(aware) == "2026-06-01T12:00:00.123000"


def test_json_default_microsecond_truncated_to_ms():
    naive_us = datetime(2026, 6, 1, 12, 0, 0, 123456)
    assert _json_default(naive_us) == "2026-06-01T12:00:00.123000"
    assert _json_default(naive_us) == _json_default(_as_mongo_readback(naive_us))


def test_json_default_aware_non_utc_converts_to_utc_wallclock():
    """An aware datetime in a non-UTC zone must normalise to the UTC wall-clock
    Mongo actually stores (e.g. 17:30 IST == 12:00 UTC)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    aware_ist = datetime(2026, 6, 1, 17, 30, 0, 500000, tzinfo=ist)
    assert _json_default(aware_ist) == "2026-06-01T12:00:00.500000"
    assert _json_default(aware_ist) == _json_default(_as_mongo_readback(aware_ist))


def test_json_default_sub_millisecond_floors_to_zero():
    """microsecond < 1000 floors to 0 -> isoformat drops the fractional part;
    the naive readback (also .000) renders identically."""
    aware = datetime(2026, 6, 1, 12, 0, 0, 456, tzinfo=timezone.utc)
    assert _json_default(aware) == "2026-06-01T12:00:00"
    assert _json_default(aware) == _json_default(_as_mongo_readback(aware))


# --------------------------------------------------------------------------
# compute_entry_hash is stable across the write -> Mongo-readback round-trip
# --------------------------------------------------------------------------

def test_entry_hash_stable_for_tz_aware_timestamp():
    """The exact shape of an ai_proposal_rejected row: a tz-aware `timestamp`.
    The hash computed at write must equal the hash recomputed from the
    naive-UTC-ms value Mongo returns."""
    aware = datetime(2026, 6, 1, 12, 0, 0, 987654, tzinfo=timezone.utc)
    write_doc = {
        "action": "ai_proposal_rejected",
        "entity_type": "ai_proposal",
        "entity_id": "PROP-123",
        "user_id": "ceo@bv.in",
        "timestamp": aware,
    }
    read_doc = dict(write_doc, timestamp=_as_mongo_readback(aware))
    assert compute_entry_hash(GENESIS_HASH, 1, write_doc) == compute_entry_hash(
        GENESIS_HASH, 1, read_doc
    )


def test_entry_hash_stable_for_nested_aware_datetime_in_before_state():
    """before_state can embed an aware datetime (proposals stores created_at via
    _now()). Nested datetimes go through the same _json_default, so they must
    round-trip-match too."""
    aware = datetime(2026, 6, 1, 9, 15, 30, 271828, tzinfo=timezone.utc)
    write_doc = {
        "action": "ai_proposal_approved_advisory",
        "entity_id": "PROP-9",
        "timestamp": aware,
        "before_state": {"ceiling": 20, "created_at": aware},
    }
    read_doc = {
        "action": "ai_proposal_approved_advisory",
        "entity_id": "PROP-9",
        "timestamp": _as_mongo_readback(aware),
        "before_state": {"ceiling": 20, "created_at": _as_mongo_readback(aware)},
    }
    assert compute_entry_hash(GENESIS_HASH, 7, write_doc) == compute_entry_hash(
        GENESIS_HASH, 7, read_doc
    )


def test_canonical_json_has_no_timezone_offset():
    """Guard: the canonical form of an aware datetime must never carry a +00:00
    (or other) offset -- that is exactly the byte that broke the chain."""
    aware = datetime(2026, 6, 1, 12, 0, 0, 123000, tzinfo=timezone.utc)
    out = canonical_json({"timestamp": aware})
    assert "+00:00" not in out
    assert "+05:30" not in out


def test_naive_datetime_unaffected_regression():
    """Naive datetimes (the vast majority of audit callers use datetime.utcnow())
    are unchanged by the tz normalisation -- only ms truncation applies."""
    naive = datetime(2026, 6, 1, 12, 0, 0, 250000)
    assert _json_default(naive) == "2026-06-01T12:00:00.250000"
    assert compute_entry_hash(GENESIS_HASH, 1, {"timestamp": naive}) == compute_entry_hash(
        GENESIS_HASH, 1, {"timestamp": naive}
    )
