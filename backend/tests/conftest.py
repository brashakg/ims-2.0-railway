"""
IMS 2.0 - Test Configuration
==============================
Shared fixtures for backend tests.
Uses FastAPI TestClient with the real app but no external DB dependency.
"""

import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Cross-test isolation on CI's SHARED mongo
# ---------------------------------------------------------------------------
# CI runs a real `mongo:7.0`; every HTTP (`client`) test talks to the SAME app
# database (`ims_2_0`). Without cleanup, rows written by one test leak into the
# next -- which is exactly how a test that asserts "expected empty" (e.g.
# reports/non-moving-stock) flakes depending on test order, and how a real
# Stock Ledger bug stayed hidden behind that noise. (`mongo_db`-style tests are
# already isolated -- they use their own throwaway `ims_test_*` databases.)
#
# Fix: before each `client` test, clear EVERY collection in the app DB except a
# small preserve-set of seeded/reference collections (a DENYLIST, not an
# allowlist) so every test starts from a known-empty state AND a newly-added
# transactional collection can never silently start leaking across the test
# order. (The allowlist this replaced went stale four times -- marketplace_channels,
# leaves, attendance, pt_slabs each leaked until someone remembered to list it,
# each surfacing only as an order-dependent CI flake.) We deliberately do NOT
# touch the startup-seeded / reference collections (see _PRESERVE_COLLECTIONS)
# so store/user validation and the idempotent startup seeds keep working.
# Fail-soft: with no DB connected (local runs), this is a no-op and DB-needing
# tests still skip.
# Collections SEEDED at startup / holding reference data the whole session
# depends on -- these are NEVER wiped. migrations.py seeds the superadmin
# `users` row, a `stores` doc and the `lens_enum_config` singleton; main.py's
# lifespan seeds `hsn_gst_master` (services/gst_rates) and the 8 `agent_config`
# rows; `sso_jti` is the JWT-revocation TTL collection. EVERYTHING ELSE in the
# app DB is transactional and is cleared before each test (see
# _reset_churn_collections). Add a collection here only if it is startup-seeded
# or otherwise expected to persist across the whole session.
_PRESERVE_COLLECTIONS = frozenset(
    {
        "stores",
        "users",
        "entities",
        "hsn_gst_master",
        "agent_config",
        "lens_enum_config",
        "sso_jti",
    }
)


def _reset_churn_collections():
    """Empty the transactional collections in the app DB so each HTTP test is
    isolated on CI's shared mongo. No-op (fail-soft) when no DB is connected."""
    try:
        from database.connection import get_db

        db = get_db()
        if not (db and getattr(db, "is_connected", False)):
            return
        mongo = getattr(db, "db", None)
        if mongo is None:
            return
        for name in mongo.list_collection_names():
            if name in _PRESERVE_COLLECTIONS or name.startswith("system."):
                continue
            try:
                mongo[name].delete_many({})
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def _noop_close(*_args, **_kwargs):
    """No-op replacement for close_db (see app fixture)."""
    return None


@pytest.fixture(scope="session", autouse=True)
def _patch_close_db():
    """Runs once before ANY test (session + autouse): neuter close_db so NO
    TestClient lifespan teardown closes the shared singleton Mongo client. The
    teardown fires from the session `client` fixture AND from any test that
    opens its own `with TestClient(app)`; either one calling the real close_db
    made every later test fail with 'pymongo.errors.InvalidOperation: Cannot
    use MongoClient after close'. Patching here (autouse, not inside the `app`
    fixture) guarantees it applies even for tests that never request
    `app`/`client`. init_db is idempotent so the live client is reused across
    tests; the process exit reclaims it. Other shutdown steps still run."""
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
    try:
        import api.main as _main

        _main.close_db = _noop_close
    except Exception:  # noqa: BLE001
        pass
    try:
        import database.connection as _conn

        _conn.close_db = _noop_close
    except Exception:  # noqa: BLE001
        pass
    # Bulletproof: neuter MongoClient.close itself for the whole test session.
    # Neutering close_db (above) only covers the lifespan path; the shared client
    # was STILL torn down mid-session via some other route (a stray
    # `with MongoClient(...)`, a duplicate-module disconnect, or GC), so every
    # later test 500'd with 'Cannot use MongoClient after close'. Patching the
    # method means NO path can close the shared client during the run; the CI
    # process is ephemeral, so the OS reclaims the sockets at exit.
    try:
        from pymongo import MongoClient

        MongoClient.close = lambda self, *a, **k: None  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001
        pass
    yield


@pytest.fixture(scope="session")
def app():
    """Create the FastAPI app for testing."""
    # Set test env vars before importing the app
    os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
    os.environ.setdefault("MONGODB_URI", "")  # empty = no DB
    from api.main import app as _app

    return _app


@pytest.fixture(scope="session")
def client(app):
    """Session-scoped TestClient: the app lifespan runs ONCE (startup connects
    the DB; shutdown closes it only at session end). Per-test isolation is the
    job of the autouse `_isolate_db` fixture below. Session scope deliberately
    avoids the per-test lifespan churn -- a function-scoped client closed the
    shared Mongo client on every test teardown, so a later test hit
    'pymongo.errors.InvalidOperation: Cannot use MongoClient after close'."""
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate_db(client):
    """Before EVERY test, clear the transactional collections from the app DB so
    no test inherits rows left behind by an earlier one on CI's shared mongo.
    Requests the session-scoped `client` so the DB is connected first. Fail-soft
    no-op when no DB is connected (local runs)."""
    _reset_churn_collections()
    yield


@pytest.fixture
def auth_headers(client):
    """Get a valid JWT for an admin user by calling login.
    Falls back to creating a token directly if login requires a DB.
    """
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-admin-001",
            "username": "testadmin",
            "roles": ["SUPERADMIN"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def staff_headers():
    """JWT for a regular sales staff user."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-staff-001",
            "username": "teststaff",
            "roles": ["SALES_STAFF"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
            "discount_cap": 10.0,
        }
    )
    return {"Authorization": f"Bearer {token}"}
