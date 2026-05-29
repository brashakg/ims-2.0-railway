"""
IMS 2.0 - Customer Follow-ups router tests
==========================================
Regression cover for the live 500s on BOTH `/follow-ups/` and
`/follow-ups/summary` (QA, 2026-05-29):

  1. `_get_db()` returned `get_db().db` -- the RAW pymongo Database, whose
     `__bool__` raises NotImplementedError. Every handler guards with
     `if not db or not db.is_connected:`, so `not db` 500'd on every call.
     Fix: `_get_db()` returns the bool-safe WRAPPER. -> test__get_db_*

  2. The list/complete/create handlers did `FollowUpResponse(**fu)` on stored
     docs; a datetime in a str field (pydantic v2 won't coerce) or a missing
     required field raised ValidationError -> 500. Fix: tolerant `_to_response`.
     -> test_to_response_*, test_list_tolerates_legacy_doc
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import follow_ups as fu_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


# ============================================================================
# 1. _get_db must return the bool-safe wrapper, NOT the raw pymongo Database
# ============================================================================


class _BoolRaisesDatabase:
    """Mimics pymongo Database: bool(db) raises NotImplementedError."""

    def __bool__(self):
        raise NotImplementedError(
            "Database objects do not implement truth value testing"
        )


class _Wrapper:
    """The connection wrapper: plain object (bool-safe), holds the raw db."""

    is_connected = True

    def __init__(self):
        self.db = _BoolRaisesDatabase()

    def get_collection(self, name):
        return None


def test_get_db_returns_wrapper_not_raw_database(monkeypatch):
    """_get_db() must hand back the wrapper (bool-safe), never `.db` (the raw
    pymongo Database whose __bool__ raises). This is the root of both 500s."""
    wrapper = _Wrapper()
    import database.connection as conn

    monkeypatch.setattr(conn, "get_db", lambda: wrapper, raising=False)
    got = fu_mod._get_db()
    assert got is wrapper
    # And the wrapper is bool-safe (the guard `if not db` must not raise).
    assert (not got) is False


# ============================================================================
# 2. _to_response tolerates legacy / partial / datetime-bearing docs
# ============================================================================


def test_to_response_coerces_datetime_and_defaults():
    now = datetime(2026, 5, 29, 10, 30, 0)
    out = fu_mod._to_response(
        {
            "_id": "abc123",  # no follow_up_id -> falls back to _id
            "customer_id": "C1",
            "store_id": "BV-PUN-01",
            "scheduled_date": now,  # datetime in a str field (used to 500)
            "created_at": now,
            # customer_name / customer_phone / notes / type / status MISSING
        }
    )
    assert out.follow_up_id == "abc123"
    assert out.created_at == now.isoformat()  # coerced to ISO string
    assert out.scheduled_date == now.isoformat()
    assert out.customer_name == ""  # missing required str -> safe default
    assert out.customer_phone == ""
    assert out.notes == ""
    assert out.type == "general"
    assert out.status == "pending"
    assert out.completed_at is None


# ============================================================================
# 3. Endpoint tests (fake bool-safe wrapper, no live DB)
# ============================================================================


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find(self, query=None, projection=None):
        return _Cursor(list(self.docs))

    def count_documents(self, query=None):
        return len(self.docs)


class _FakeWrapper:
    is_connected = True

    def __init__(self, docs):
        self._coll = _FakeColl(docs)

    def get_collection(self, name):
        return self._coll


def _staff_token(roles=("STORE_MANAGER",), store_id="BV-PUN-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": list(roles),
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


@pytest.fixture
def fu_client(monkeypatch):
    # NB: do NOT name this `client` — that shadows conftest's session-scoped
    # `client`, which the autouse `_isolate_db(client)` depends on, and the
    # override would then run this fixture (patching _get_db) for EVERY test.
    app = FastAPI()
    app.include_router(fu_mod.router, prefix="/api/v1/follow-ups")
    # A legacy doc that USED to 500 the list: datetime fields + missing
    # customer_name/phone/notes, plus a Mongo _id and no follow_up_id.
    legacy_doc = {
        "_id": "leg-1",
        "customer_id": "C1",
        "store_id": "BV-PUN-01",
        "type": "eye_test_reminder",
        "scheduled_date": datetime(2026, 5, 20, 9, 0, 0),
        "status": "pending",
        "created_at": datetime(2026, 5, 1, 9, 0, 0),
    }
    wrapper = _FakeWrapper([legacy_doc])
    monkeypatch.setattr(fu_mod, "_get_db", lambda: wrapper)
    return TestClient(app)


def test_list_tolerates_legacy_doc(fu_client):
    """GET /follow-ups/ must return 200 (not 500) even when a stored doc has
    datetime fields + missing required strings."""
    tok = _staff_token()
    r = fu_client.get(
        "/api/v1/follow-ups/?store_id=BV-PUN-01",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["follow_up_id"] == "leg-1"  # _id fallback
    assert body[0]["created_at"] == "2026-05-01T09:00:00"  # datetime -> ISO
    assert body[0]["customer_name"] == ""  # missing -> default, no 500


def test_summary_returns_200_not_bool_db_500(fu_client):
    """GET /follow-ups/summary must return 200 — the `not db` guard no longer
    crashes now that _get_db returns the bool-safe wrapper."""
    tok = _staff_token()
    r = fu_client.get(
        "/api/v1/follow-ups/summary?store_id=BV-PUN-01",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {
        "due_today",
        "this_week",
        "overdue",
        "completed_this_month",
        "pending_total",
    }
    assert all(isinstance(v, int) for v in body.values())
