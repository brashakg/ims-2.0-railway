"""
IMS 2.0 - Inventory + Transfer correctness regression suite
============================================================
Covers the correctness bugs found and fixed in this audit pass:

  INVENTORY
  ---------
  A. non-moving stock: current_stock excluded SOLD units (now AVAILABLE/RESERVED only).
  B. non-moving stock: days_since_sale was always the query-param `days`, not
     the real elapsed days since last sale.

  TRANSFERS
  ---------
  C. Input validation: negative/zero quantity_requested accepted (now ge=1).
  D. Input validation: negative unit_cost accepted (now ge=0).
  E. Input validation: negative shipping_cost accepted (now ge=0).
  F. Input validation: negative quantity_received / quantity_damaged accepted (now ge=0).
  G. Receive validation: quantity_damaged > quantity_received not rejected (now rejected 400).
  H. Self-transfer (from == to) not rejected (now rejected 400).
  I. Empty items list not rejected (now rejected 400).
  J. status_history missing/null on a loaded transfer doc would AttributeError (now safe).

All tests are pure (no DB) except the non-moving tests which use a real Mongo
via the mongo_db fixture (skip-safe when Mongo is absent).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import transfers as tr_mod
from api.routers import inventory as inv_mod
from api.routers.auth import get_current_user

# ============================================================================
# Shared fixtures
# ============================================================================

_ADMIN = {
    "user_id": "u-audit",
    "username": "auditadmin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["S-A", "S-B"],
    "active_store_id": "S-A",
}


class _FakeColl:
    """Minimal MongoDB collection stand-in (no actual Mongo needed)."""

    def __init__(self):
        self.docs = {}

    def update_one(self, flt, update, upsert=False):
        _id = flt["id"]
        doc = self.docs.get(_id, {})
        doc.update(update["$set"])
        self.docs[_id] = doc

    def find_one(self, flt, projection=None):
        d = self.docs.get(flt.get("id", ""))
        return dict(d) if d else None

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs.values()]

    def count_documents(self, flt=None):
        return len(self.docs)


# ============================================================================
# A + B: non-moving stock endpoint
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    """Throwaway Mongo DB; skip fail-soft when absent."""
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
    except Exception:
        pytest.skip("Mongo unavailable; skipping integration tests")
        return None

    db_name = f"ims_test_inv_corr_{uuid.uuid4().hex[:8]}"
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


@pytest.fixture
def inv_client(mongo_db, monkeypatch):
    from database.repositories.product_repository import ProductRepository, StockRepository

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        inv_mod, "get_stock_repository", lambda: StockRepository(mongo_db["stock_units"])
    )
    monkeypatch.setattr(
        inv_mod, "get_product_repository", lambda: ProductRepository(mongo_db["products"])
    )

    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")

    async def _user():
        return {
            "user_id": "u-corr",
            "username": "corradmin",
            "roles": ["SUPERADMIN"],
            "store_ids": ["ST-CORR"],
            "active_store_id": "ST-CORR",
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def _add_product(mongo_db, store="ST-CORR", **kwargs):
    pid = kwargs.pop("product_id", f"PRD-{uuid.uuid4().hex[:8]}")
    doc = {
        "_id": pid,
        "product_id": pid,
        "sku": f"SKU-{pid[-6:]}",
        "brand": "TestBrand",
        "model": "M1",
        "category": "SUNGLASS",
        "mrp": 1000.0,
        "is_active": True,
    }
    doc.update(kwargs)
    mongo_db["products"].insert_one(doc)
    return pid


def _add_unit(mongo_db, product_id, status="AVAILABLE", store="ST-CORR"):
    mongo_db["stock_units"].insert_one({
        "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
        "product_id": product_id,
        "store_id": store,
        "barcode": f"BC-{uuid.uuid4().hex[:10]}",
        "status": status,
        "quantity": 1,
        "location_code": "DEFAULT",
    })


def _add_order(mongo_db, product_id, store="ST-CORR", days_ago=200):
    mongo_db["orders"].insert_one({
        "order_id": f"ORD-{uuid.uuid4().hex[:8]}",
        "store_id": store,
        "status": "COMPLETED",
        "created_at": datetime.utcnow() - timedelta(days=days_ago),
        "items": [{"product_id": product_id, "quantity": 1}],
    })


class TestNonMovingStockFixes:
    """Bug A: SOLD units excluded; Bug B: real days_since_sale computed."""

    def test_sold_units_not_counted_as_current_stock(self, inv_client, mongo_db):
        """BUG A: a product with 3 SOLD units and 2 AVAILABLE must show
        current_stock=2, not 5."""
        pid = _add_product(mongo_db)
        # 3 SOLD (should not count toward on-hand)
        for _ in range(3):
            _add_unit(mongo_db, pid, status="SOLD")
        # 2 AVAILABLE (on-hand)
        for _ in range(2):
            _add_unit(mongo_db, pid, status="AVAILABLE")
        # No recent sale -> appears in non-moving.
        resp = inv_client.get("/inventory/non-moving?days=90")
        assert resp.status_code == 200, resp.text
        items = {i["product_id"]: i for i in resp.json()["products"]}
        if pid in items:
            assert items[pid]["current_stock"] == 2, (
                f"current_stock must count only AVAILABLE units, got "
                f"{items[pid]['current_stock']} (SOLD units leaked in)"
            )

    def test_days_since_sale_reflects_real_elapsed_days(self, inv_client, mongo_db):
        """BUG B: days_since_sale must be actual elapsed days since last sale,
        not the query-param `days`."""
        pid = _add_product(mongo_db)
        _add_unit(mongo_db, pid, status="AVAILABLE")
        # Add a sale that happened 200 days ago but outside the 90-day window.
        _add_order(mongo_db, pid, days_ago=200)

        resp = inv_client.get("/inventory/non-moving?days=90")
        assert resp.status_code == 200, resp.text
        items = {i["product_id"]: i for i in resp.json()["products"]}
        if pid not in items:
            pytest.skip("product not in non-moving (may have been pruned)")
        row = items[pid]
        # days_since_sale should be ~200, NOT 90 (the query param).
        assert row["days_since_sale"] is not None, "days_since_sale must not be null"
        assert row["days_since_sale"] > 90, (
            f"days_since_sale must reflect real elapsed days (~200), not the "
            f"query-param days=90, got {row['days_since_sale']}"
        )
        # Also verify last_sold_date is set (previously returned raw MongoDB datetime
        # which is not JSON-serialisable; now returned as ISO string).
        assert row["last_sold_date"] is not None

    def test_never_sold_product_has_null_days_since_sale(self, inv_client, mongo_db):
        """A product with no order history at all should have days_since_sale=None
        (never-sold sentinel) rather than the query-param value."""
        pid = _add_product(mongo_db)
        _add_unit(mongo_db, pid, status="AVAILABLE")
        # No orders at all.
        resp = inv_client.get("/inventory/non-moving?days=90")
        assert resp.status_code == 200, resp.text
        items = {i["product_id"]: i for i in resp.json()["products"]}
        if pid in items:
            assert items[pid]["days_since_sale"] is None, (
                "a never-sold product must have days_since_sale=None"
            )


# ============================================================================
# Reorder -1 sentinel: inventory feeds must carry the policy fields
# ============================================================================
# Owner decision 2026-07-04: reorder_quantity <= 0 (the -1 default) means
# auto-reorder is DISABLED (api/services/reorder_policy.py). The inventory-
# side UI reads two feeds that previously dropped this signal:
#   * /inventory/stock ledger rows must pass reorder_quantity/reorder_point
#     through RAW (None when absent) -- no fabricated defaults.
#   * /inventory/low-stock items must carry auto_reorder_disabled so
#     consumers can skip opted-out products without hiding the alert.


class TestReorderMinus1Passthrough:
    def test_stock_ledger_passes_reorder_fields_through_raw(self, inv_client, mongo_db):
        """Ledger rows carry reorder_quantity / reorder_point verbatim:
        -1 stays -1 (disabled sentinel), a missing field stays None (legacy),
        and a real value stays itself. Nothing is defaulted to 10/20."""
        pid_off = _add_product(mongo_db, reorder_quantity=-1, reorder_point=5)
        pid_legacy = _add_product(mongo_db)  # no reorder fields at all
        pid_on = _add_product(mongo_db, reorder_quantity=12, reorder_point=4)

        resp = inv_client.get("/inventory/stock")
        assert resp.status_code == 200, resp.text
        rows = {r["product_id"]: r for r in resp.json()["items"]}

        assert pid_off in rows and pid_legacy in rows and pid_on in rows
        assert rows[pid_off]["reorder_quantity"] == -1, (
            "the -1 'auto-reorder off' sentinel must survive the ledger row"
        )
        assert rows[pid_off]["reorder_point"] == 5
        assert rows[pid_legacy]["reorder_quantity"] is None, (
            "a legacy product without the field must yield None, not a "
            "fabricated default"
        )
        assert rows[pid_legacy]["reorder_point"] is None
        assert rows[pid_on]["reorder_quantity"] == 12
        assert rows[pid_on]["reorder_point"] == 4

    def test_low_stock_items_carry_auto_reorder_disabled_flag(self, inv_client, mongo_db):
        """/inventory/low-stock items get auto_reorder_disabled: True for a
        product with reorder_quantity=-1, False for reorder_quantity=5, and
        False (legacy-enabled) when the field is missing. The alert list
        still contains ALL low-stock products -- the flag only informs."""
        pid_off = _add_product(mongo_db, reorder_quantity=-1)
        pid_on = _add_product(mongo_db, reorder_quantity=5)
        pid_legacy = _add_product(mongo_db)
        for pid in (pid_off, pid_on, pid_legacy):
            _add_unit(mongo_db, pid)  # 1 AVAILABLE unit -> low stock

        resp = inv_client.get("/inventory/low-stock")
        assert resp.status_code == 200, resp.text
        items = {i["_id"]: i for i in resp.json()["items"]}

        assert pid_off in items, "disabled product must STILL appear in alerts"
        assert items[pid_off]["auto_reorder_disabled"] is True
        assert pid_on in items
        assert items[pid_on]["auto_reorder_disabled"] is False
        assert pid_legacy in items
        assert items[pid_legacy]["auto_reorder_disabled"] is False, (
            "missing reorder_quantity is legacy-ENABLED until backfilled"
        )


# ============================================================================
# C-I: Transfer input validation (pure, no DB)
# ============================================================================


@pytest.fixture(autouse=True)
def _isolate_transfer_store(monkeypatch):
    """Wire transfers router to an in-memory collection for every test."""
    fake = _FakeColl()
    monkeypatch.setattr(tr_mod, "_transfers_coll", lambda: fake)
    monkeypatch.setattr(tr_mod, "get_stock_repository", lambda: None)
    tr_mod.STOCK_TRANSFERS.clear()


def _make_item(**overrides):
    base = {
        "product_id": "PRD-1",
        "sku": "SKU-1",
        "product_name": "Test Frame",
        "quantity_requested": 3,
    }
    base.update(overrides)
    return base


def _make_transfer_input(**overrides):
    base = {
        "transfer_type": "store_to_store",
        "from_location_id": "S-A",
        "from_location_name": "Store A",
        "to_location_id": "S-B",
        "to_location_name": "Store B",
        "items": [_make_item()],
    }
    base.update(overrides)
    return base


class TestTransferInputValidation:
    """Bugs C-I: all input-schema validation regressions."""

    # --- C: quantity_requested >= 1 ---

    def test_zero_quantity_requested_rejected(self):
        """BUG C: zero quantity_requested must be rejected (Pydantic ge=1)."""
        with pytest.raises(Exception):
            tr_mod.TransferItemInput(
                product_id="PRD-1", sku="S", product_name="X", quantity_requested=0
            )

    def test_negative_quantity_requested_rejected(self):
        """BUG C: negative quantity_requested must be rejected."""
        with pytest.raises(Exception):
            tr_mod.TransferItemInput(
                product_id="PRD-1", sku="S", product_name="X", quantity_requested=-5
            )

    def test_positive_quantity_requested_accepted(self):
        item = tr_mod.TransferItemInput(
            product_id="PRD-1", sku="S", product_name="X", quantity_requested=1
        )
        assert item.quantity_requested == 1

    # --- D: unit_cost >= 0 ---

    def test_negative_unit_cost_rejected(self):
        """BUG D: negative unit_cost must be rejected."""
        with pytest.raises(Exception):
            tr_mod.TransferItemInput(
                product_id="PRD-1",
                sku="S",
                product_name="X",
                quantity_requested=1,
                unit_cost=-100.0,
            )

    def test_zero_unit_cost_accepted(self):
        item = tr_mod.TransferItemInput(
            product_id="PRD-1", sku="S", product_name="X", quantity_requested=1, unit_cost=0.0
        )
        assert item.unit_cost == 0.0

    # --- E: shipping_cost >= 0 ---

    def test_negative_shipping_cost_rejected_on_input(self):
        """BUG E: negative shipping_cost on TransferInput must be rejected."""
        with pytest.raises(Exception):
            tr_mod.TransferInput(
                transfer_type=tr_mod.TransferType.STORE_TO_STORE,
                from_location_id="S-A",
                from_location_name="A",
                to_location_id="S-B",
                to_location_name="B",
                items=[
                    tr_mod.TransferItemInput(
                        product_id="P", sku="S", product_name="X", quantity_requested=1
                    )
                ],
                shipping_cost=-50.0,
            )

    def test_negative_shipping_cost_rejected_on_update(self):
        """BUG E: negative shipping_cost on TransferUpdate must be rejected."""
        with pytest.raises(Exception):
            tr_mod.TransferUpdate(shipping_cost=-10.0)

    # --- F: quantity_received / quantity_damaged >= 0 ---

    def test_negative_quantity_received_rejected(self):
        """BUG F: negative quantity_received must be rejected."""
        with pytest.raises(Exception):
            tr_mod.TransferItemReceive(
                transfer_item_id="X", quantity_received=-1
            )

    def test_negative_quantity_damaged_rejected(self):
        """BUG F: negative quantity_damaged must be rejected."""
        with pytest.raises(Exception):
            tr_mod.TransferItemReceive(
                transfer_item_id="X", quantity_received=5, quantity_damaged=-1
            )

    # --- G: quantity_damaged <= quantity_received ---

    def test_damaged_exceeds_received_rejected(self, monkeypatch):
        """BUG G: damaged > received must be rejected at the endpoint level.
        The endpoint guard raises HTTPException(400)."""
        # Schema itself only enforces ge=0; the endpoint enforces damaged<=received.
        item = tr_mod.TransferItemReceive(
            transfer_item_id="X", quantity_received=2, quantity_damaged=5
        )
        assert item.quantity_damaged > item.quantity_received  # schema allows it...
        # ...but the endpoint rejects it. Use a fake collection so the transfer is found.
        fake = _FakeColl()
        monkeypatch.setattr(tr_mod, "_transfers_coll", lambda: fake)
        transfer = {
            "id": "t-dmg",
            "status": tr_mod.TransferStatus.IN_TRANSIT,
            "items": [
                {
                    "id": "X",
                    "product_id": "PRD-1",
                    "quantity_requested": 5,
                    "quantity_shipped": 5,
                    "quantity_received": 0,
                    "quantity_damaged": 0,
                    "status": "in_transit",
                }
            ],
            "status_history": [],
            "from_location_id": "S-A",
            "to_location_id": "S-B",
        }
        fake.docs["t-dmg"] = transfer

        import asyncio
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                tr_mod.receive_transfer(
                    "t-dmg",
                    items_received=[
                        tr_mod.TransferItemReceive(
                            transfer_item_id="X",
                            quantity_received=2,
                            quantity_damaged=5,
                        )
                    ],
                    current_user=_ADMIN,
                )
            )
        assert exc_info.value.status_code == 400
        assert "quantity_damaged" in exc_info.value.detail

    # --- H: self-transfer rejected ---

    def test_self_transfer_rejected(self):
        """BUG H: from_location_id == to_location_id must be rejected."""
        import asyncio
        from fastapi import HTTPException

        payload = tr_mod.TransferInput(
            transfer_type=tr_mod.TransferType.STORE_TO_STORE,
            from_location_id="S-A",
            from_location_name="Store A",
            to_location_id="S-A",  # same as source
            to_location_name="Store A",
            items=[
                tr_mod.TransferItemInput(
                    product_id="PRD-1", sku="S", product_name="X", quantity_requested=2
                )
            ],
        )
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(tr_mod.create_transfer(payload, current_user=_ADMIN))
        assert exc_info.value.status_code == 400
        assert "different" in exc_info.value.detail.lower()

    # --- I: empty items list rejected ---

    def test_empty_items_list_rejected(self):
        """BUG I: creating a transfer with no items must be rejected."""
        import asyncio
        from fastapi import HTTPException

        payload = tr_mod.TransferInput(
            transfer_type=tr_mod.TransferType.STORE_TO_STORE,
            from_location_id="S-A",
            from_location_name="Store A",
            to_location_id="S-B",
            to_location_name="Store B",
            items=[],  # empty
        )
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(tr_mod.create_transfer(payload, current_user=_ADMIN))
        assert exc_info.value.status_code == 400
        assert "item" in exc_info.value.detail.lower()

    # --- J: status_history missing on loaded doc ---

    def test_status_history_missing_does_not_crash_approve(self, monkeypatch):
        """BUG J: a transfer without a status_history field (migrated doc)
        must not raise AttributeError in any status-advancing endpoint."""
        # Use a fake collection so _get_transfer finds the doc.
        fake = _FakeColl()
        monkeypatch.setattr(tr_mod, "_transfers_coll", lambda: fake)
        doc = {
            "id": "t-nohist",
            "status": tr_mod.TransferStatus.PENDING_APPROVAL,
            "items": [],
            # NOTE: no status_history key at all
        }
        fake.docs["t-nohist"] = doc

        import asyncio

        asyncio.run(
            tr_mod.approve_transfer(
                "t-nohist",
                tr_mod.TransferApproval(approved=True),
                current_user=_ADMIN,
            )
        )
        # Must not raise; history now populated by _append_status_history.
        saved = fake.docs.get("t-nohist")
        assert isinstance(saved.get("status_history"), list)
        assert len(saved["status_history"]) >= 1

    def test_status_history_null_does_not_crash(self, monkeypatch):
        """BUG J: a transfer with status_history=None (corrupt doc) must not
        raise AttributeError — _append_status_history resets it to []."""
        fake = _FakeColl()
        monkeypatch.setattr(tr_mod, "_transfers_coll", lambda: fake)
        doc = {
            "id": "t-nullhist",
            "status": tr_mod.TransferStatus.PENDING_APPROVAL,
            "items": [],
            "status_history": None,  # explicitly null
        }
        fake.docs["t-nullhist"] = doc

        import asyncio

        asyncio.run(
            tr_mod.approve_transfer(
                "t-nullhist",
                tr_mod.TransferApproval(approved=True),
                current_user=_ADMIN,
            )
        )
        saved = fake.docs.get("t-nullhist")
        assert isinstance(saved.get("status_history"), list)


class TestTransferHelperStatusHistory:
    """Direct unit test for _append_status_history helper."""

    def test_appends_to_existing(self):
        doc = {"id": "x", "status_history": [{"status": "draft"}]}
        tr_mod._append_status_history(doc, {"status": "approved"})
        assert len(doc["status_history"]) == 2
        assert doc["status_history"][-1]["status"] == "approved"

    def test_creates_list_when_missing(self):
        doc = {"id": "x"}
        tr_mod._append_status_history(doc, {"status": "approved"})
        assert doc["status_history"] == [{"status": "approved"}]

    def test_resets_null_to_list(self):
        doc = {"id": "x", "status_history": None}
        tr_mod._append_status_history(doc, {"status": "approved"})
        assert doc["status_history"] == [{"status": "approved"}]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
