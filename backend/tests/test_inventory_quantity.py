"""
IMS 2.0 - Inventory `quantity`-field regression suite
=====================================================
QA inventory agent root cause (2026-05-30):

`add_stock` (POST /inventory/stock/add) persisted serialized stock_units
WITHOUT a `quantity` field. Every read that aggregates `{"$sum": "$quantity"}`
or does `stock.get("quantity", 0)` then summed/read a MISSING field and got 0,
so on-hand showed as zero across:

  * /inventory/low-stock              (StockRepository.find_low_stock)
  * /inventory/transfer-recommendations
  * /inventory/sell-through-analysis
  * /inventory/overstock-analysis
  * /inventory/non-moving
  * /inventory/stock-count-scan       (system_count=0 -> false +variance;
                                       product_name=null)

The endpoints that already worked (/stock, /aging, power-grid) COUNT ROWS or
use `$ifNull`. The fix mirrors them:

  1. add_stock now stores `quantity: 1` per serialized unit (go-forward).
  2. The 5 read aggregations/reads use `{"$ifNull": ["$quantity", 1]}` (or a
     `.get("quantity", 1)` per-row default) so EXISTING units written before
     the fix (no `quantity` field) ALSO count as one unit each.
  3. stock-count-scan computes system_count by counting in-stock rows (not the
     scanned unit's raw quantity) and reconstructs product_name from
     brand + model (products carry no `name` field).

This module exercises a REAL mongo:7.0 (CI provides one; local dev falls back
to localhost). It is skipped fail-soft when Mongo is unreachable so it never
breaks the unit-test sweep on a laptop without Mongo.

The units seeded here deliberately have NO `quantity` field (raw insert_one,
not the repo) to prove the fixes count legacy/pre-fix data correctly. One test
drives add_stock end-to-end to prove the go-forward write stamps quantity=1.
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


STORE = "BV-QTY-01"


# ============================================================================
# Real mongo fixture (own throwaway db; skip fail-soft when absent)
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
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
        pytest.skip(f"Mongo unavailable at {uri}; skipping integration tests")
        return None

    db_name = f"ims_test_inv_qty_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


class _DBProxy:
    """get_db()-shaped wrapper exposing the test mongo db's collections by
    name (both `get_collection(name)` and `db.<name>` access)."""

    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


@pytest.fixture
def client(mongo_db, monkeypatch):
    """Bare app mounting the inventory router, with the router's DB/repo
    accessors pointed at the throwaway test db and an authenticated
    SUPERADMIN user (passes every inventory role gate)."""
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inventory, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        inventory,
        "get_stock_repository",
        lambda: StockRepository(mongo_db["stock_units"]),
    )
    monkeypatch.setattr(
        inventory,
        "get_product_repository",
        lambda: ProductRepository(mongo_db["products"]),
    )

    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")

    async def _fake_user():
        return {
            "user_id": "u-qty",
            "username": "qtyadmin",
            "roles": ["SUPERADMIN"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# ============================================================================
# Seed helpers -- units written WITHOUT a `quantity` field on purpose
# ============================================================================


def _seed_product(mongo_db, **over: Any) -> str:
    """Insert a product whose `_id == product_id` (matches the repo + the
    `{"_id": product_id}` joins in sell-through / overstock / non-moving).
    Products carry no `name` field -- only brand + model."""
    pid = over.pop("product_id", f"PRD-{uuid.uuid4().hex[:8]}")
    doc: Dict[str, Any] = {
        "_id": pid,
        "product_id": pid,
        "sku": f"SKU-{pid[-6:]}",
        "brand": "Ray-Ban",
        "model": "RB3025",
        "category": "SUNGLASS",
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "is_active": True,
    }
    doc.update(over)
    mongo_db["products"].insert_one(doc)
    return pid


def _seed_legacy_units(mongo_db, product_id: str, n: int, store_id: str = STORE):
    """Insert n serialized stock_units with NO `quantity` field (the pre-fix
    shape that caused on-hand to read as 0). Raw insert_one so the repo can't
    stamp anything."""
    for _ in range(n):
        mongo_db["stock_units"].insert_one(
            {
                "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
                "product_id": product_id,
                "store_id": store_id,
                "barcode": f"BC-{uuid.uuid4().hex[:10]}",
                "status": "AVAILABLE",
                "location_code": "DEFAULT",
            }
        )


def _seed_sold_order(mongo_db, product_id: str, qty: int, store_id: str = STORE):
    """A recent completed order so sell-through/overstock have sales > 0 and the
    product is NOT classified non-moving."""
    mongo_db["orders"].insert_one(
        {
            "order_id": f"ORD-{uuid.uuid4().hex[:8]}",
            "store_id": store_id,
            "status": "COMPLETED",
            "created_at": datetime.utcnow() - timedelta(days=2),
            "items": [{"product_id": product_id, "quantity": qty}],
        }
    )


# ============================================================================
# /low-stock -- the headline bug
# ============================================================================


class TestLowStockOnHand:
    def test_legacy_units_count_toward_on_hand(self, client, mongo_db):
        """3 legacy units (no quantity field) must report on-hand 3, not 0,
        and only surface as low-stock under the default threshold (5)."""
        pid = _seed_product(mongo_db)
        _seed_legacy_units(mongo_db, pid, 3)

        resp = client.get("/inventory/low-stock")
        assert resp.status_code == 200, resp.text
        items = {r["_id"]: r for r in resp.json()["items"]}
        assert pid in items, "product with on-hand units missing from low-stock"
        assert items[pid]["quantity"] == 3, (
            f"on-hand must reflect 3 serialized units, got "
            f"{items[pid]['quantity']} (the $quantity-sum-of-missing-field bug)"
        )

    def test_well_stocked_product_not_flagged_low(self, client, mongo_db):
        """10 legacy units is above the threshold -> not low-stock (proves the
        count is real, not a constant)."""
        pid = _seed_product(mongo_db)
        _seed_legacy_units(mongo_db, pid, 10)

        resp = client.get("/inventory/low-stock")
        assert resp.status_code == 200, resp.text
        ids = {r["_id"] for r in resp.json()["items"]}
        assert pid not in ids, "a 10-unit product must not appear as low-stock"


# ============================================================================
# /sell-through-analysis + /overstock-analysis + /non-moving
# ============================================================================


class TestSellThroughOnHand:
    def test_units_stocked_not_zero(self, client, mongo_db):
        pid = _seed_product(mongo_db, brand="Oakley")
        _seed_legacy_units(mongo_db, pid, 4)
        _seed_sold_order(mongo_db, pid, qty=2)

        resp = client.get("/inventory/sell-through-analysis?days=30")
        assert resp.status_code == 200, resp.text
        by_brand = {b["brand"]: b for b in resp.json()["brands"]}
        assert "Oakley" in by_brand
        row = by_brand["Oakley"]
        assert row["units_stocked"] == 4, (
            f"units_stocked must count 4 serialized units, got "
            f"{row['units_stocked']}"
        )
        # 2 sold / 4 stocked -> 50% sell-through (finite, not div-by-zero 0).
        assert row["units_sold"] == 2
        assert row["sell_through_percent"] == 50.0


class TestOverstockOnHand:
    def test_overstock_flags_when_stock_far_exceeds_sales(self, client, mongo_db):
        """20 on-hand units vs ~2 units/month sales over the window -> the
        product is overstocked. With the old quantity=0 read it never flagged."""
        pid = _seed_product(mongo_db, brand="Vogue")
        _seed_legacy_units(mongo_db, pid, 20)
        _seed_sold_order(mongo_db, pid, qty=2)

        resp = client.get(
            "/inventory/overstock-analysis?overstocking_threshold=3.0&days=30"
        )
        assert resp.status_code == 200, resp.text
        items = {i["product_id"]: i for i in resp.json()["items"]}
        assert pid in items, (
            "a 20-unit product with ~2 sales/mo must flag as overstocked"
        )
        assert items[pid]["current_stock"] == 20, (
            f"current_stock must reflect 20 units, got "
            f"{items[pid]['current_stock']}"
        )
        # product_name reconstructed from brand+model (no `name` on product).
        assert items[pid]["product_name"] == "Vogue RB3025"


class TestNonMovingOnHand:
    def test_non_moving_current_stock_not_zero(self, client, mongo_db):
        """A product never sold but holding 6 units must report current_stock
        6 (so dead-stock value isn't silently zeroed)."""
        pid = _seed_product(mongo_db, brand="Fossil")
        _seed_legacy_units(mongo_db, pid, 6)
        # No orders at all -> non-moving.

        resp = client.get("/inventory/non-moving?days=90")
        assert resp.status_code == 200, resp.text
        items = {i["product_id"]: i for i in resp.json()["products"]}
        assert pid in items, "never-sold in-stock product missing from non-moving"
        assert items[pid]["current_stock"] == 6, (
            f"current_stock must reflect 6 units, got "
            f"{items[pid]['current_stock']}"
        )


# ============================================================================
# /stock-count-scan -- no false variance, product_name reconstructed
# ============================================================================


class TestStockCountScan:
    def test_in_stock_unit_has_system_count_and_no_false_variance(
        self, client, mongo_db
    ):
        """Scan one of 5 in-stock units with a matching physical count of 5:
        system_count must be 5 (count of rows), variance 0 -- NOT a false +5
        from reading the scanned unit's missing `quantity`."""
        pid = _seed_product(mongo_db, brand="Titan", model="T100")
        _seed_legacy_units(mongo_db, pid, 5)
        # Grab a real barcode that was seeded for this product.
        unit = mongo_db["stock_units"].find_one({"product_id": pid})
        barcode = unit["barcode"]

        resp = client.post(
            "/inventory/stock-count-scan",
            json={"barcode": barcode, "physical_count": 5},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["system_count"] == 5, (
            f"system_count must count 5 in-stock rows, got {data['system_count']}"
        )
        assert data["variance"] == 0, (
            f"matching physical count must yield zero variance, got "
            f"{data['variance']} (the system_count=0 false-variance bug)"
        )
        # product_name reconstructed from brand + model, not null/"Unknown".
        assert data["product_name"] == "Titan T100"

    def test_system_count_at_least_one_for_single_unit(self, client, mongo_db):
        pid = _seed_product(mongo_db)
        _seed_legacy_units(mongo_db, pid, 1)
        unit = mongo_db["stock_units"].find_one({"product_id": pid})

        resp = client.post(
            "/inventory/stock-count-scan",
            json={"barcode": unit["barcode"], "physical_count": 1},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["system_count"] >= 1


# ============================================================================
# add_stock go-forward -- new units carry quantity=1
# ============================================================================


class TestAddStockStampsQuantity:
    def test_added_units_persist_quantity_one(self, client, mongo_db):
        pid = _seed_product(mongo_db)

        resp = client.post(
            "/inventory/stock/add",
            json={"product_id": pid, "quantity": 3},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["quantity"] == 3  # rows created

        rows = list(mongo_db["stock_units"].find({"product_id": pid}))
        assert len(rows) == 3
        assert all(r.get("quantity") == 1 for r in rows), (
            "each new serialized unit must persist quantity=1 for go-forward "
            "aggregation correctness"
        )

    def test_added_units_visible_to_low_stock(self, client, mongo_db):
        """End-to-end: add_stock then low-stock reflects the new on-hand."""
        pid = _seed_product(mongo_db)
        client.post("/inventory/stock/add", json={"product_id": pid, "quantity": 2})

        resp = client.get("/inventory/low-stock")
        assert resp.status_code == 200, resp.text
        items = {r["_id"]: r for r in resp.json()["items"]}
        assert items.get(pid, {}).get("quantity") == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
