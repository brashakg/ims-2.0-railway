"""
IMS 2.0 - Cross-layer lens catalog + stock integration test (B' sub-PR 1)
==========================================================================
Round-trip writes through the actual router code against a REAL mongo:7.0
(CI provides one as a service; local dev falls back to localhost). Skipped
fail-soft when Mongo is unreachable so it never breaks the unit-test sweep
on a developer laptop.

What this catches that the FakeColl unit tests do not:
  - the routers and the DB schema agree on the collection names
    (lens_catalog + lens_stock_lines + lens_stock_audit + lens_enum_config);
  - find_one_and_update with a real $expr predicate behaves the same as
    the in-memory FakeMongo (Mongo's real CAS prevents oversell);
  - audit rows actually materialise on the real driver path.

Mirrors test_fixtures_integration.py.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-integration-tests")


@pytest.fixture(scope="module")
def mongo_db():
    """Real mongo:7.0 connection. Skip the test module when absent."""
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
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip("Mongo unavailable; skipping integration tests")
        return None

    db_name = "ims_test_lens_{rand}".format(rand=uuid.uuid4().hex[:8])
    db = client[db_name]
    # Seed the enum_config so create_lens_line can pass validation.
    db["lens_enum_config"].insert_one(
        {"enum_id": "coatings", "items": ["ANTI_BLUE", "HC"]}
    )
    db["lens_enum_config"].insert_one(
        {"enum_id": "brands", "items": ["IntegrationCo"]}
    )
    db["lens_enum_config"].insert_one(
        {"enum_id": "series", "items": []}
    )
    db["lens_enum_config"].insert_one(
        {"enum_id": "indexes", "items": [1.50, 1.60, 1.67]}
    )
    db["lens_enum_config"].insert_one(
        {"enum_id": "materials", "items": ["CR39", "MR8"]}
    )
    db["lens_enum_config"].insert_one(
        {"enum_id": "lens_types", "items": ["SV", "PROGRESSIVE"]}
    )
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


from api.routers import lens_catalog as catalog_router  # noqa: E402
from api.routers import lens_stock as stock_router  # noqa: E402


class _DBShim:
    def __init__(self, real_db):
        self._db = real_db

    def get_collection(self, name):
        return self._db[name]


def _wire(monkeypatch, mongo_db):
    shim = _DBShim(mongo_db)
    monkeypatch.setattr(catalog_router, "_get_db", lambda: shim)
    monkeypatch.setattr(stock_router, "_get_db", lambda: shim)
    monkeypatch.setattr(catalog_router, "get_audit_repository", lambda: None)


def _admin():
    return {
        "user_id": "u-int-{rand}".format(rand=uuid.uuid4().hex[:6]),
        "username": "integration-admin",
        "roles": ["SUPERADMIN"],
        "store_ids": ["STR-INT-1"],
        "active_store_id": "STR-INT-1",
    }


def test_full_round_trip_catalog_stock_reserve_commit(mongo_db, monkeypatch):
    """Walk the full chain end-to-end against real mongo:
       create catalog -> create stock cell -> reserve -> commit -> assert
       audit rows materialised.
    """
    _wire(monkeypatch, mongo_db)
    user = _admin()

    # 1. Create a lens line.
    payload = catalog_router.LensLineCreate(
        brand="IntegrationCo",
        series="ProSeries",
        index=1.60,
        material="MR8",
        lens_type="SV",
        coating="ANTI_BLUE",
        mrp=4500.0,
    )
    created = asyncio.run(catalog_router.create_lens_line(payload, user))
    assert created["status"] == "success"
    lens_line_id = created["lens_line"]["lens_line_id"]
    assert lens_line_id == "integrationco-proseries-1p60-mr8-sv-anti-blue"

    # 2. Create a stock cell at on_hand=10.
    cell_payload = stock_router.StockCellCreate(
        lens_line_id=lens_line_id,
        store_id="STR-INT-1",
        sph=-2.0,
        cyl=0.0,
        add=None,
        on_hand=10,
    )
    cell = asyncio.run(stock_router.create_cell(cell_payload, user))
    assert cell["status"] == "success"
    assert cell["cell"]["on_hand"] == 10
    assert cell["cell"]["available"] == 10

    # 3. Reserve 3 -> available drops to 7, on_hand unchanged.
    reserve_payload = stock_router.ReserveCommitReleasePayload(
        store_id="STR-INT-1",
        sph=-2.0,
        cyl=0.0,
        add=None,
        qty=3,
        source_type="POS",
        source_id="order-int-1",
    )
    reserved = asyncio.run(
        stock_router.reserve_cell(lens_line_id, reserve_payload, user)
    )
    assert reserved["cell"]["on_hand"] == 10
    assert reserved["cell"]["reserved"] == 3
    assert reserved["cell"]["available"] == 7

    # 4. Commit 2 -> on_hand drops to 8, reserved drops to 1.
    commit_payload = stock_router.ReserveCommitReleasePayload(
        store_id="STR-INT-1",
        sph=-2.0,
        cyl=0.0,
        add=None,
        qty=2,
        source_type="WORKSHOP",
        source_id="job-int-1",
    )
    committed = asyncio.run(
        stock_router.commit_cell(lens_line_id, commit_payload, user)
    )
    assert committed["cell"]["on_hand"] == 8
    assert committed["cell"]["reserved"] == 1

    # 5. Assert audit rows materialised.
    audit_docs = list(mongo_db["lens_stock_audit"].find(
        {"lens_line_id": lens_line_id}
    ))
    actions = [d["action"] for d in audit_docs]
    assert "create" in actions
    assert "reserve" in actions
    assert "commit" in actions
    # Cleanup is module-scope drop_database -- intentionally do NOT call
    # delete_lens_line here: it 409s (correctly) because the cell still
    # carries on_hand=8 + reserved=1. The fixture tears down the whole
    # test DB after the module runs.
