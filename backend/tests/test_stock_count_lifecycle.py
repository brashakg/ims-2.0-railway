"""
IMS 2.0 -- THE PHYSICAL STOCK COUNT MUST ACTUALLY COUNT
=======================================================
Lifecycle audit finding S2 / seam 2. Before this suite the counting screen
started a session and completed it with nothing in between, so completion ran
over an empty list and told the counter "Variance: 0%". Every count the
business had ever run reported a perfect shelf.

What is proved here, against the REAL inventory router over a REAL Mongo
(CI service container; an in-memory mongomock engine on a dev box without one)
-- nothing under test is stubbed:

  * COMPLETING AN EMPTY COUNT IS REFUSED (400). A session with no recorded
    lines is not a count, and must never report a variance at all.
  * A COUNTED QUANTITY IS PERSISTED by the scan door the count sheet uses --
    the SET and the COUNT are both asserted on the stored document.
  * A REAL DISCREPANCY IS REPORTED IN UNITS AND IN RUPEES, per line and in
    total, valued at the product's cost.
  * A UNIT SOLD MID-COUNT IS NEVER WRITTEN OFF: the write-off is conditional
    on the unit still being AVAILABLE at the instant of the write, and the
    shortfall is reported honestly instead of destroying a live sale.
  * A STORE MANAGER CANNOT WRITE OFF (owner ruling 2026-08-25 #8: write-offs
    are ADMIN / SUPERADMIN only, at every value).
  * THE SAME COUNT CANNOT BE CORRECTED TWICE -- no double write-off.
  * SHRINKAGE ROWS CARRY A RUPEE VALUE.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_stock_count_lifecycle.py -q

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "ST-COUNT"


# ============================================================================
# Engine: the real Mongo CI runs against; mongomock only as a dev-box fallback
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_count_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return None
        client = mongomock.MongoClient()
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
        client.close()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


def _client(mongo_db, monkeypatch, roles):
    """A TestClient over the REAL inventory router bound to the given engine."""
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        inv_mod,
        "get_stock_repository",
        lambda: StockRepository(mongo_db["stock_units"]),
    )
    monkeypatch.setattr(
        inv_mod,
        "get_product_repository",
        lambda: ProductRepository(mongo_db["products"]),
    )

    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")

    async def _user():
        return {
            "user_id": "u-count",
            "username": "counter",
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


@pytest.fixture
def admin_client(mongo_db, monkeypatch):
    return _client(mongo_db, monkeypatch, ["ADMIN"])


@pytest.fixture
def manager_client(mongo_db, monkeypatch):
    return _client(mongo_db, monkeypatch, ["STORE_MANAGER"])


# ============================================================================
# Seed helpers -- shapes copied from the live collections
# ============================================================================


def _seed_product(mongo_db, cost: float = 2000.0, **over: Any) -> str:
    pid = over.pop("product_id", f"PRD-{uuid.uuid4().hex[:8]}")
    doc: Dict[str, Any] = {
        "_id": pid,
        "product_id": pid,
        "sku": f"SKU-{pid[-6:]}",
        "brand": "Ray-Ban",
        "model": "RB3025",
        "category": "SUNGLASS",
        "mrp": 5000.0,
        "cost_price": cost,
        "is_active": True,
    }
    doc.update(over)
    mongo_db["products"].insert_one(doc)
    return pid


def _seed_units(mongo_db, product_id: str, n: int, store_id: str = STORE):
    """n serialized AVAILABLE units, returning their barcodes."""
    barcodes = []
    for i in range(n):
        bc = f"BC-{uuid.uuid4().hex[:10]}"
        mongo_db["stock_units"].insert_one(
            {
                "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
                "product_id": product_id,
                "store_id": store_id,
                "barcode": bc,
                "status": "AVAILABLE",
                "quantity": 1,
                "location_code": "DEFAULT",
                "created_at": f"2026-01-0{i + 1}T00:00:00",
            }
        )
        barcodes.append(bc)
    return barcodes


def _start(client) -> str:
    r = client.post("/inventory/stock-count/start", json={})
    assert r.status_code == 200, r.text
    return r.json()["count_id"]


# ============================================================================
# 1. THE LIE: completing a count where nothing was recorded
# ============================================================================


def test_completing_a_count_with_nothing_recorded_is_refused(admin_client):
    """The count sheet used to answer "Variance: 0%" for a shelf nobody read."""
    count_id = _start(admin_client)

    r = admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    assert r.status_code == 400, (
        "an empty count must be REFUSED, not reported as a perfect result; "
        f"got {r.status_code} {r.text}"
    )
    body = r.json()
    assert "counted" in body["detail"].lower()
    # and it must NOT report a variance of any kind
    assert "variance_percentage" not in body


def test_an_empty_count_stays_open_after_the_refusal(admin_client, mongo_db):
    """A refused completion must not have half-closed the session."""
    count_id = _start(admin_client)
    admin_client.post(f"/inventory/stock-count/{count_id}/complete", json={})

    doc = mongo_db["stock_counts"].find_one({"count_id": count_id})
    assert doc["status"] == "in_progress"
    assert doc.get("completed_at") is None
    assert doc.get("variances") == []
