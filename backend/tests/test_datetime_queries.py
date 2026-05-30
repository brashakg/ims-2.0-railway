"""
IMS 2.0 - String-vs-BSON-datetime query regression tests
=========================================================
Three endpoints silently returned empty because a Mongo query compared a
datetime bound against a field that is actually persisted as an ISO *string*
(or vice-versa). Mongo's BSON type-bracketing means a datetime $lt/$gte bound
never matches a string field and vice-versa, so the filters matched nothing.

These tests write rows the EXACT way the production code paths write them
(ISO date strings via date.isoformat(), BSON datetime via
datetime.now(timezone.utc)) into the real local Mongo, then call the endpoint
through the app and assert the row is now visible:

  1. /api/v1/workshop/overdue       -- find_overdue (expected_date string)
  2. /api/v1/inventory/expiring     -- find_expiring (expiry_date string)
  3. /api/v1/jarvis/agents/activity -- get_agent_activity (agent_audit_log
                                       timestamp persisted as BSON datetime)

Run with a real Mongo:
    JWT_SECRET_KEY=test MONGO_HOST=127.0.0.1 \
        python -m pytest backend/tests/test_datetime_queries.py -q

When no DB is reachable the endpoints fail soft (empty envelope); those tests
skip rather than fail so the suite stays green on DB-less local runs.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STORE = "BV-TEST-01"  # matches conftest auth_headers active_store_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _live_db():
    """Return the connected app DB, or None when no Mongo is reachable."""
    try:
        from database.connection import get_db

        db = get_db()
        if db is not None and getattr(db, "is_connected", False):
            return db
    except Exception:  # noqa: BLE001
        pass
    return None


@pytest.fixture
def db(client):
    """Connected app DB (the session `client` runs startup which connects it).

    Skips the test when no Mongo is reachable so DB-less runs don't fail.
    """
    handle = _live_db()
    if handle is None:
        pytest.skip("No MongoDB reachable (set MONGO_HOST=127.0.0.1)")
    return handle


# ---------------------------------------------------------------------------
# 1. Workshop overdue -- expected_date is an ISO date STRING
# ---------------------------------------------------------------------------


def test_overdue_includes_past_expected_date_job(db, client, auth_headers):
    """A PENDING job whose expected_date (ISO string) is in the past must show
    up in /workshop/overdue. Before the fix the datetime $lt bound never
    matched the string field, so this returned 0."""
    coll = db.get_collection("workshop_jobs")
    # Persist expected_date the way create_job does: date.isoformat() -> "YYYY-MM-DD"
    past = (datetime.now() - timedelta(days=3)).date().isoformat()
    future = (datetime.now() + timedelta(days=5)).date().isoformat()
    coll.insert_many(
        [
            {
                "job_id": "DTQ-OVERDUE-1",
                "job_number": "WSJ-DTQ-OVERDUE-1",
                "order_id": "ORD-DTQ-1",
                "store_id": STORE,
                "status": "PENDING",
                "expected_date": past,
                "created_at": past,
            },
            {
                "job_id": "DTQ-OVERDUE-2",
                "job_number": "WSJ-DTQ-OVERDUE-2",
                "order_id": "ORD-DTQ-2",
                "store_id": STORE,
                "status": "IN_PROGRESS",
                "expected_date": past,
                "created_at": past,
            },
            # Control rows that must NOT be flagged overdue:
            {
                "job_id": "DTQ-FUTURE",
                "job_number": "WSJ-DTQ-FUTURE",
                "order_id": "ORD-DTQ-3",
                "store_id": STORE,
                "status": "PENDING",
                "expected_date": future,  # not yet due
                "created_at": past,
            },
            {
                "job_id": "DTQ-DELIVERED",
                "job_number": "WSJ-DTQ-DELIVERED",
                "order_id": "ORD-DTQ-4",
                "store_id": STORE,
                "status": "DELIVERED",  # closed
                "expected_date": past,
                "created_at": past,
            },
        ]
    )

    resp = client.get("/api/v1/workshop/overdue", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    ids = {j["id"] for j in body["jobs"]}
    assert "DTQ-OVERDUE-1" in ids
    assert "DTQ-OVERDUE-2" in ids
    # Future + delivered jobs are not overdue
    assert "DTQ-FUTURE" not in ids
    assert "DTQ-DELIVERED" not in ids
    assert body["total"] == 2


def test_overdue_empty_when_nothing_past_due(db, client, auth_headers):
    """Sanity: with only future-dated open jobs, overdue is empty (no false
    positives from the new string comparison)."""
    coll = db.get_collection("workshop_jobs")
    future = (datetime.now() + timedelta(days=10)).date().isoformat()
    coll.insert_one(
        {
            "job_id": "DTQ-ONLYFUTURE",
            "job_number": "WSJ-DTQ-ONLYFUTURE",
            "order_id": "ORD-DTQ-9",
            "store_id": STORE,
            "status": "PENDING",
            "expected_date": future,
            "created_at": future,
        }
    )
    body = client.get("/api/v1/workshop/overdue", headers=auth_headers).json()
    ids = {j["id"] for j in body["jobs"]}
    assert "DTQ-ONLYFUTURE" not in ids


# ---------------------------------------------------------------------------
# 2. Inventory expiring -- expiry_date is an ISO date STRING
# ---------------------------------------------------------------------------


def test_expiring_includes_stock_within_window(db, client, auth_headers):
    """Stock expiring in 30 days (expiry_date stored as an ISO date string by
    add_stock) must appear in /inventory/expiring. Before the fix the datetime
    bounds never matched the string field, so this returned 0."""
    coll = db.get_collection("stock_units")
    soon = (datetime.now() + timedelta(days=30)).date().isoformat()
    far = (datetime.now() + timedelta(days=120)).date().isoformat()
    already = (datetime.now() - timedelta(days=2)).date().isoformat()
    coll.insert_many(
        [
            {
                "stock_id": "DTQ-EXP-SOON",
                "product_id": "P-DTQ-1",
                "store_id": STORE,
                "status": "AVAILABLE",
                "expiry_date": soon,  # in window
            },
            {
                "stock_id": "DTQ-EXP-FAR",
                "product_id": "P-DTQ-2",
                "store_id": STORE,
                "status": "AVAILABLE",
                "expiry_date": far,  # outside 90d window
            },
            {
                "stock_id": "DTQ-EXP-PAST",
                "product_id": "P-DTQ-3",
                "store_id": STORE,
                "status": "AVAILABLE",
                "expiry_date": already,  # already expired
            },
        ]
    )

    resp = client.get("/api/v1/inventory/expiring?days=90", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = {i.get("stock_id") for i in items}
    assert "DTQ-EXP-SOON" in ids
    # Far-future + already-expired stock is outside the "expiring soon" window
    assert "DTQ-EXP-FAR" not in ids
    assert "DTQ-EXP-PAST" not in ids


def test_expiring_respects_days_window(db, client, auth_headers):
    """A unit expiring in 60 days is excluded from a 30-day window but included
    in a 90-day window -- the string bound tracks the `days` parameter."""
    coll = db.get_collection("stock_units")
    in_60 = (datetime.now() + timedelta(days=60)).date().isoformat()
    coll.insert_one(
        {
            "stock_id": "DTQ-EXP-60D",
            "product_id": "P-DTQ-60",
            "store_id": STORE,
            "status": "AVAILABLE",
            "expiry_date": in_60,
        }
    )
    narrow = client.get(
        "/api/v1/inventory/expiring?days=30", headers=auth_headers
    ).json()["items"]
    wide = client.get(
        "/api/v1/inventory/expiring?days=90", headers=auth_headers
    ).json()["items"]
    assert "DTQ-EXP-60D" not in {i.get("stock_id") for i in narrow}
    assert "DTQ-EXP-60D" in {i.get("stock_id") for i in wide}


# ---------------------------------------------------------------------------
# 3. Jarvis activity feed -- agent_audit_log.timestamp is a BSON DATETIME
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_audit_log(db):
    """agent_audit_log is NOT in conftest's churn list, so isolate it here."""
    coll = db.get_collection("agent_audit_log")
    coll.delete_many({"agent_id": "taskmaster", "action": {"$regex": "^DTQ-"}})
    yield coll
    coll.delete_many({"agent_id": "taskmaster", "action": {"$regex": "^DTQ-"}})


def test_activity_feed_returns_datetime_audit_row(
    db, client, auth_headers, clean_audit_log
):
    """An agent_audit_log row written with a BSON datetime timestamp (the way
    base.log_action persists every agent action) must appear in the activity
    feed. Before the fix the feed only queried `executed_at` with a STRING
    bound, so datetime `timestamp` rows were invisible."""
    coll = clean_audit_log
    coll.insert_one(
        {
            "agent_id": "taskmaster",
            "agent_name": "TASKMASTER",
            "action": "DTQ-auto-reorder",
            "details": {"sku": "P-DTQ-1", "qty": 12},
            "before_state": None,
            "after_state": None,
            "timestamp": datetime.now(timezone.utc),  # BSON datetime, not a string
        }
    )

    resp = client.get(
        "/api/v1/jarvis/agents/activity?agent_id=taskmaster", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    summaries = [e.get("summary", "") for e in body["events"]]
    assert any("DTQ-auto-reorder" in s for s in summaries), (
        f"datetime audit row missing from feed; got {summaries}"
    )
    # The matching event is attributed to taskmaster with a non-empty timestamp.
    match = next(e for e in body["events"] if "DTQ-auto-reorder" in e.get("summary", ""))
    assert match["agent_id"] == "taskmaster"
    assert match["kind"] == "task_execution"
    assert match["timestamp"]  # coerced to an ISO string, not empty


def test_activity_feed_returns_string_executed_at_row(
    db, client, auth_headers, clean_audit_log
):
    """Regression guard: the legacy `executed_at` ISO-string shape that
    taskmaster._audit_log writes must STILL surface after the fix."""
    coll = clean_audit_log
    coll.insert_one(
        {
            "agent_id": "taskmaster",
            "action": "DTQ-sla-escalation",
            "target": "TASK-DTQ-7",
            "safety_tier": 1,
            "before_state": None,
            "after_state": None,
            "executed_at": datetime.now(timezone.utc).isoformat(),  # ISO string
        }
    )

    body = client.get(
        "/api/v1/jarvis/agents/activity?agent_id=taskmaster", headers=auth_headers
    ).json()
    summaries = [e.get("summary", "") for e in body["events"]]
    assert any("DTQ-sla-escalation" in s for s in summaries), (
        f"string executed_at row missing from feed; got {summaries}"
    )
