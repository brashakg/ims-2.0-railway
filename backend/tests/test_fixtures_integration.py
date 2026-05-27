"""
IMS 2.0 - Cross-layer fixtures + placements integration tests (v2-2a)
======================================================================
Round-trip writes through the actual router code against a REAL mongo:7.0
(CI provides one as a service; local dev can fall back to localhost). Skipped
fail-soft when Mongo is unreachable so it never breaks the unit-test sweep
on a developer laptop.

Mirrors the pattern in test_stock_integration.py.

What this catches that the FakeColl unit tests do not:
  - the routers and the DB schema agree on the collection names
    (display_fixtures + display_placements);
  - the routers' _get_db() / get_collection() path works through the real
    pymongo driver, not just an in-memory shim;
  - documents written by create_fixture surface in list_fixtures (no
    silent collection-name typo).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def mongo_db():
    """Real mongo:7.0 connection. Skip the test module fail-soft if absent.

    Tries MONGODB_URL (CI), MONGODB_URI (local), then localhost. A 2s timeout
    keeps the test from hanging on a laptop without Mongo installed.
    """
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()  # raises if unreachable
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip("Mongo unavailable at {uri}; skipping integration tests".format(uri=uri))
        return None

    db_name = "ims_test_fixtures_{rand}".format(rand=uuid.uuid4().hex[:8])
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


# Required env var BEFORE importing the routers (auth.py raises otherwise).
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-integration-tests")

from api.routers import display_fixtures as fixtures_router  # noqa: E402
from api.routers import display_placements as placements_router  # noqa: E402


class _DBShim:
    """Minimal stand-in for the connection wrapper get_db() returns. The two
    routers call _get_db() -> shim -> get_collection(name)."""

    def __init__(self, real_db):
        self._db = real_db

    def get_collection(self, name):
        return self._db[name]


def _wire(monkeypatch, mongo_db):
    """Patch both routers' _get_db to the live test DB + null out the audit
    repo (the live audit_logs collection has a strict schema that doesn't
    accept our before/after structure, and audit failure is fail-soft
    anyway). Mirrors what test_stock_integration.py does for inventory."""
    shim = _DBShim(mongo_db)
    monkeypatch.setattr(fixtures_router, "_get_db", lambda: shim)
    monkeypatch.setattr(placements_router, "_get_db", lambda: shim)
    monkeypatch.setattr(fixtures_router, "get_audit_repository", lambda: None)
    monkeypatch.setattr(placements_router, "get_audit_repository", lambda: None)


def _admin():
    return {
        "user_id": "u-int-{rand}".format(rand=uuid.uuid4().hex[:6]),
        "username": "integration-admin",
        "roles": ["SUPERADMIN"],
        "store_ids": ["STR-INT-1"],
        "active_store_id": "STR-INT-1",
    }


def test_fixture_create_then_list_round_trip(mongo_db, monkeypatch):
    """Write a fixture via create_fixture, then surface it via list_fixtures.
    If anyone re-points the routers at the wrong collection, this fails."""
    _wire(monkeypatch, mongo_db)
    user = _admin()
    code = "INT-W-{rand}".format(rand=uuid.uuid4().hex[:4]).upper()
    payload = fixtures_router.FixtureCreate(
        store_id="STR-INT-1",
        code=code,
        name="Integration wall",
        type="wall",
        floor="ground",
        zone="A",
        capacity=50,
        merch=["Frame"],
    )
    created = asyncio.run(fixtures_router.create_fixture(payload, user))
    assert created["status"] == "success"
    fixture_id = created["fixture"]["fixture_id"]

    listed = asyncio.run(
        fixtures_router.list_fixtures(
            store_id="STR-INT-1",
            type=None,
            floor=None,
            zone=None,
            active=True,
            current_user=user,
        )
    )
    codes = [f["code"] for f in listed["fixtures"]]
    assert code in codes, (
        "Wrote fixture {code} but did not see it in list_fixtures. "
        "Likely cause: collection-name typo in the router.".format(code=code)
    )

    # Cleanup -- soft-delete so this test module is idempotent if a later
    # test runs the same code accidentally.
    asyncio.run(fixtures_router.delete_fixture(fixture_id, user))


def test_placement_create_then_list_by_sku(mongo_db, monkeypatch):
    """Create a fixture, place a SKU on it, then list by SKU and assert the
    placement surfaces with the expected qty."""
    _wire(monkeypatch, mongo_db)
    user = _admin()
    code = "INT-P-{rand}".format(rand=uuid.uuid4().hex[:4]).upper()
    sku = "INT-SKU-{rand}".format(rand=uuid.uuid4().hex[:6])

    fixture = asyncio.run(
        fixtures_router.create_fixture(
            fixtures_router.FixtureCreate(
                store_id="STR-INT-1",
                code=code,
                name="Integration pillar",
                type="pillar",
                floor="ground",
                zone="A",
                capacity=30,
                merch=["Frame"],
            ),
            user,
        )
    )
    fixture_id = fixture["fixture"]["fixture_id"]

    placement = asyncio.run(
        placements_router.create_placement(
            placements_router.PlacementCreate(
                sku=sku,
                store_id="STR-INT-1",
                fixture_id=fixture_id,
                qty=4,
                position="shelf-1 . slot-02",
            ),
            user,
        )
    )
    assert placement["status"] == "success"
    placement_id = placement["placement"]["placement_id"]

    listed = asyncio.run(
        placements_router.list_placements(
            store_id="STR-INT-1", sku=sku, fixture_id=None, current_user=user
        )
    )
    skus = [p["sku"] for p in listed["placements"]]
    assert sku in skus
    matching = next(p for p in listed["placements"] if p["sku"] == sku)
    assert matching["qty"] == 4
    assert matching["fixture_id"] == fixture_id

    # Cleanup
    asyncio.run(placements_router.delete_placement(placement_id, user))
    asyncio.run(fixtures_router.delete_fixture(fixture_id, user))


def test_placement_create_stacks_through_real_db(mongo_db, monkeypatch):
    """The 'stack qty on duplicate (sku, fixture)' branch must work with the
    real (store_id, sku, fixture_id) UNIQUE index -- if anyone breaks the
    stacking branch, the second create would 11000 instead of stacking."""
    _wire(monkeypatch, mongo_db)
    user = _admin()
    code = "INT-S-{rand}".format(rand=uuid.uuid4().hex[:4]).upper()
    sku = "INT-SKU-{rand}".format(rand=uuid.uuid4().hex[:6])

    fixture = asyncio.run(
        fixtures_router.create_fixture(
            fixtures_router.FixtureCreate(
                store_id="STR-INT-1",
                code=code,
                name="Integration stack",
                type="counter",
                floor="ground",
                zone="B",
                capacity=20,
                merch=["Frame"],
            ),
            user,
        )
    )
    fixture_id = fixture["fixture"]["fixture_id"]

    first = asyncio.run(
        placements_router.create_placement(
            placements_router.PlacementCreate(
                sku=sku, store_id="STR-INT-1", fixture_id=fixture_id, qty=2
            ),
            user,
        )
    )
    placement_id = first["placement"]["placement_id"]
    assert first["stacked"] is False

    second = asyncio.run(
        placements_router.create_placement(
            placements_router.PlacementCreate(
                sku=sku, store_id="STR-INT-1", fixture_id=fixture_id, qty=3
            ),
            user,
        )
    )
    assert second["stacked"] is True
    assert second["placement"]["qty"] == 5
    assert second["placement"]["placement_id"] == placement_id

    # Cleanup
    asyncio.run(placements_router.delete_placement(placement_id, user))
    asyncio.run(fixtures_router.delete_fixture(fixture_id, user))
