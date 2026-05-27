"""
IMS 2.0 - Cross-layer stock integration tests
=============================================
Catches the class of bug that mock-collection unit tests cannot: writes go
through one layer (StockRepository -> stock_units), reads go through a
DIFFERENT layer (inventory._on_hand_by_product, finance.outstanding,
returns reactivation) -> if the two layers ever disagree on the collection
name or the field shape, prod silently breaks while unit tests stay green.

This module talks to a REAL mongo:7.0 instance (CI provides one as a service;
local dev can fall back to localhost). Skipped fail-soft when Mongo is
unreachable so it never breaks the unit-test sweep on a developer laptop.

Bugs caught here:
  * The `stock` vs `stock_units` split-brain (B1): writes go to stock_units,
    inventory.py read sites used to point at the empty `stock` collection.
  * The CREDIT-tender amount_paid bug fixed in PR #256: payment goes in via
    add_payment, AR read computes off payment_status. Split = silent.
  * mark_sold wiring (B2): an order creation flips a stock_unit to SOLD with
    the order_id stamped; a later restock reactivation can find THAT unit.

If you add a new write-then-read pair that crosses Mongo collections, add
a round-trip test here.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

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
        pytest.skip(f"Mongo unavailable at {uri}; skipping integration tests")
        return None

    # Each test module gets its own db so parallel runs don't collide.
    db_name = f"ims_test_integration_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


def _stock_repo(mongo_db):
    """Wire a real StockRepository against the test mongo db's stock_units."""
    from database.repositories.product_repository import StockRepository

    return StockRepository(mongo_db["stock_units"])


def _order_repo(mongo_db):
    from database.repositories.order_repository import OrderRepository

    return OrderRepository(mongo_db["orders"])


# ============================================================================
# 1. The stock/stock_units split-brain class of bug (B1 guard)
# ============================================================================


def test_grn_then_on_hand_read_round_trip(mongo_db):
    """Write a stock_unit via StockRepository (the path GRN acceptance takes),
    then read on-hand for that product via inventory._on_hand_by_product.

    If anyone re-introduces a `get_collection("stock")` read in inventory.py,
    this test goes red because the unit was written to `stock_units` and the
    bad read points at an empty `stock` collection.
    """
    from api.routers.inventory import _on_hand_by_product

    repo = _stock_repo(mongo_db)
    pid = f"PRD-INT-{uuid.uuid4().hex[:6]}"
    sid = f"STK-{uuid.uuid4().hex[:8]}"
    created = repo.create(
        {
            "stock_id": sid,
            "product_id": pid,
            "store_id": "STORE-INT-1",
            "status": "AVAILABLE",
            "quantity": 1,
        }
    )
    assert created is not None

    class _DBProxy:
        """Shim so _on_hand_by_product (which calls db.get_collection(...))
        sees our test mongo db directly. Mirrors the get_db() shape."""

        def get_collection(self, name):
            return mongo_db[name]

    on_hand = _on_hand_by_product(_DBProxy(), [pid], store_id="STORE-INT-1")
    assert on_hand.get(pid) == 1, (
        f"Expected 1 unit on-hand for {pid} but got {on_hand}. "
        "Likely cause: someone re-introduced get_collection('stock') in "
        "inventory.py - canonical collection is 'stock_units'."
    )


def test_grn_then_on_hand_store_scoped(mongo_db):
    """Cross-store scoping survives the round-trip too: a unit at store A must
    NOT count toward store B's on-hand."""
    from api.routers.inventory import _on_hand_by_product

    repo = _stock_repo(mongo_db)
    pid = f"PRD-INT-{uuid.uuid4().hex[:6]}"
    repo.create(
        {
            "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
            "product_id": pid,
            "store_id": "STORE-A",
            "status": "AVAILABLE",
            "quantity": 1,
        }
    )
    repo.create(
        {
            "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
            "product_id": pid,
            "store_id": "STORE-B",
            "status": "AVAILABLE",
            "quantity": 1,
        }
    )

    class _DBProxy:
        def get_collection(self, name):
            return mongo_db[name]

    a = _on_hand_by_product(_DBProxy(), [pid], store_id="STORE-A")
    b = _on_hand_by_product(_DBProxy(), [pid], store_id="STORE-B")
    assert a.get(pid) == 1
    assert b.get(pid) == 1


# ============================================================================
# 2. AR / outstanding round-trip (PR #256 fix - CREDIT tender stays unpaid)
# ============================================================================


def test_payment_then_payment_status_round_trip(mongo_db):
    """Apply a CREDIT-tender payment via OrderRepository.add_payment, then read
    the order back: payment_status MUST be CREDIT (not PAID) and the balance
    must remain. This guards PR #256 - if anyone re-introduces the bug where
    CREDIT tenders count toward amount_paid, this fails.
    """
    repo = _order_repo(mongo_db)
    oid = f"ORD-INT-{uuid.uuid4().hex[:6]}"
    repo.create(
        {
            "order_id": oid,
            "order_number": f"INV-{uuid.uuid4().hex[:6]}",
            "store_id": "STORE-INT-1",
            "customer_id": "CUST-INT-1",
            "grand_total": 1500.0,
            "amount_paid": 0.0,
            "balance_due": 1500.0,
            "payment_status": "UNPAID",
            "payments": [],
            "items": [],
        }
    )
    ok = repo.add_payment(
        oid,
        {
            "method": "CREDIT",
            "amount": 1500.0,
            "reference": "credit-promise",
            "timestamp": datetime.now().isoformat(),
        },
    )
    assert ok is True

    doc = repo.find_by_id(oid)
    assert doc is not None
    # CREDIT does NOT count as cash received.
    assert doc.get("amount_paid") == 0.0
    # Balance stays because the tender was a promise, not a payment.
    assert doc.get("balance_due") == 1500.0
    # Status flips to CREDIT (a receivable, NOT a paid order).
    assert doc.get("payment_status") == "CREDIT"


def test_cash_payment_then_paid_status(mongo_db):
    """Counter-test: a real CASH tender DOES flip the order to PAID."""
    repo = _order_repo(mongo_db)
    oid = f"ORD-INT-{uuid.uuid4().hex[:6]}"
    repo.create(
        {
            "order_id": oid,
            "order_number": f"INV-{uuid.uuid4().hex[:6]}",
            "store_id": "STORE-INT-1",
            "customer_id": "CUST-INT-1",
            "grand_total": 500.0,
            "amount_paid": 0.0,
            "balance_due": 500.0,
            "payment_status": "UNPAID",
            "payments": [],
            "items": [],
        }
    )
    repo.add_payment(
        oid,
        {
            "method": "CASH",
            "amount": 500.0,
            "reference": "cash",
            "timestamp": datetime.now().isoformat(),
        },
    )
    doc = repo.find_by_id(oid)
    assert doc.get("amount_paid") == 500.0
    assert doc.get("balance_due") == 0.0
    assert doc.get("payment_status") == "PAID"


# ============================================================================
# 3. mark_sold wiring (B2) - order create then stock unit visible as SOLD
# ============================================================================


def test_mark_sold_round_trip(mongo_db):
    """A stock_unit AVAILABLE -> mark_sold flips it to SOLD with order_id.
    Then _on_hand_by_product NO LONGER counts it (SOLD is not an on-hand status).
    This is what lets the B2 wiring be observable: after an order ships, the
    on-hand count drops, exactly as the floor expects.
    """
    from api.routers.inventory import _on_hand_by_product

    repo = _stock_repo(mongo_db)
    pid = f"PRD-INT-{uuid.uuid4().hex[:6]}"
    sid = f"STK-{uuid.uuid4().hex[:8]}"
    repo.create(
        {
            "stock_id": sid,
            "product_id": pid,
            "store_id": "STORE-INT-2",
            "status": "AVAILABLE",
            "quantity": 1,
        }
    )

    class _DBProxy:
        def get_collection(self, name):
            return mongo_db[name]

    # Before sale: 1 on hand.
    pre = _on_hand_by_product(_DBProxy(), [pid], store_id="STORE-INT-2")
    assert pre.get(pid) == 1

    # Mark sold (this is exactly what orders.py::_mark_units_sold does).
    order_id = f"ORD-INT-{uuid.uuid4().hex[:6]}"
    assert repo.mark_sold(sid, order_id) is True

    # After sale: 0 on hand.
    post = _on_hand_by_product(_DBProxy(), [pid], store_id="STORE-INT-2")
    assert post.get(pid, 0) == 0

    # The unit still exists, just SOLD - and carries the order_id so a
    # later restock reactivation can find IT specifically.
    sold_doc = mongo_db["stock_units"].find_one({"stock_id": sid})
    assert sold_doc is not None
    assert sold_doc["status"] == "SOLD"
    assert sold_doc["order_id"] == order_id
