"""
IMS 2.0 - Transfer RECEIVE hardening (BUG-009 / BUG-011 / BUG-019)
==================================================================
Covers three confirmed receive-side defects on the real /api/v1/transfers
lifecycle:

  * BUG-009 - damaged-in-transit units used to be re-homed as AVAILABLE
    (sellable). They must now land in QUARANTINE: good units AVAILABLE,
    damaged units QUARANTINED, and destination sellable on-hand must EXCLUDE
    the damaged ones.
  * BUG-011 - quantity_received was accepted uncapped, inflating the doc/summary
    and falsely marking a partial transfer RECEIVED. An over-receive (received >
    shipped on a line) must now be rejected with a 400.
  * BUG-019 - a receive mismatch (short / surplus / damaged) used to close the
    transfer silently. A follow-up task must now be created in the canonical
    `tasks` collection, store-scoped to the destination.

The DB-backed tests drive the real endpoint functions against the REAL
StockRepository on a throwaway Mongo at MONGO_HOST=127.0.0.1 (same harness as
test_transfer_stock_movement.py). If no local mongod is reachable they SKIP so
the suite stays green off-CI while still proving the behavior on CI's mongo.

Pure-logic checks (no DB) lock the over-receive 400 and the mismatch-task
helper's shape so they run everywhere.
"""

from __future__ import annotations

import os
import sys
import uuid
import asyncio

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import transfers  # noqa: E402


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))


def _mongo_db():
    try:
        from pymongo import MongoClient
    except Exception:  # noqa: BLE001
        pytest.skip("pymongo not installed")
    try:
        client = MongoClient(MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local mongo at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_test_transfer_dmg_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


def _on_hand(db, product_id, store_id):
    """Sellable on-hand == AVAILABLE/IN_STOCK serialized units (the POS view).
    A QUARANTINED unit must NOT be counted here."""
    avail = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]
    return db.get_collection("stock_units").count_documents(
        {"product_id": product_id, "store_id": store_id, "status": {"$in": avail}}
    )


def _count_status(db, product_id, store_id, status):
    return db.get_collection("stock_units").count_documents(
        {"product_id": product_id, "store_id": store_id, "status": status}
    )


@pytest.fixture
def lifecycle(monkeypatch):
    from database.repositories.product_repository import StockRepository

    client, db, name = _mongo_db()
    stock_repo = StockRepository(db.get_collection("stock_units"))

    monkeypatch.setattr(transfers, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(
        transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers")
    )
    monkeypatch.setattr(transfers, "_get_db", lambda: db)

    try:
        yield {"db": db, "stock_repo": stock_repo}
    finally:
        client.drop_database(name)
        client.close()


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["A", "B"],
    "active_store_id": "A",
}


def _seed_units(stock_repo, product_id, store_id, n):
    for i in range(n):
        stock_repo.create(
            {
                "product_id": product_id,
                "store_id": store_id,
                "barcode": f"{store_id}-{product_id}-{i}",
                "status": "AVAILABLE",
                "location_code": "DEFAULT",
            }
        )


def _make_transfer(product_id, qty, n_name="Frame"):
    payload = transfers.TransferInput(
        transfer_type=transfers.TransferType.STORE_TO_STORE,
        from_location_id="A",
        from_location_name="Store A",
        to_location_id="B",
        to_location_name="Store B",
        items=[
            transfers.TransferItemInput(
                product_id=product_id,
                sku="SKU-1",
                product_name=n_name,
                quantity_requested=qty,
            )
        ],
    )
    res = asyncio.run(transfers.create_transfer(payload, current_user=_ADMIN))
    return res["transfer"]


# ===========================================================================
# BUG-009 - damaged units quarantined, good units available
# ===========================================================================


def test_receive_with_damaged_quarantines_only_damaged(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-DMG-1"
    N, K, DMG = 10, 6, 2  # ship 6, of which 2 arrive damaged

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    received = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id,
                    quantity_received=K,
                    quantity_damaged=DMG,
                )
            ],
            current_user=_ADMIN,
        )
    )["transfer"]

    assert received["status"] == transfers.TransferStatus.RECEIVED
    # Destination SELLABLE on-hand rose only by the GOOD units (K - DMG).
    assert _on_hand(db, product_id, "B") == K - DMG
    # The damaged units are at B but QUARANTINED -> excluded from on-hand/POS.
    assert _count_status(db, product_id, "B", transfers.STOCK_STATUS_QUARANTINED) == DMG
    # Doc tracks the quarantined count distinctly.
    assert received.get("stock_units_quarantined") == DMG
    assert received.get("stock_units_moved_in") == K - DMG


def test_receive_all_damaged_quarantines_all(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-DMG-ALL"
    N, K = 5, 3

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=K, quantity_damaged=K
                )
            ],
            current_user=_ADMIN,
        )
    )
    # None sellable at the destination; all K quarantined.
    assert _on_hand(db, product_id, "B") == 0
    assert _count_status(db, product_id, "B", transfers.STOCK_STATUS_QUARANTINED) == K


# ===========================================================================
# BUG-011 - quantity_received cannot exceed quantity_shipped
# ===========================================================================


def test_over_receive_is_rejected(lifecycle):
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-OVER-1"
    N, K = 10, 4

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    # Receiving 6 against a 4-unit shipment must be a hard 400.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            transfers.receive_transfer(
                tid,
                items_received=[
                    transfers.TransferItemReceive(
                        transfer_item_id=line_id, quantity_received=K + 2
                    )
                ],
                current_user=_ADMIN,
            )
        )
    assert exc.value.status_code == 400
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "RECEIVE_EXCEEDS_SHIPPED"


def test_over_receive_does_not_inflate_or_mark_received(lifecycle):
    """The over-receive 400 must fire BEFORE any doc/stock mutation, so the
    transfer is not falsely advanced to RECEIVED."""
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-OVER-2"
    N, K = 8, 5

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    with pytest.raises(HTTPException):
        asyncio.run(
            transfers.receive_transfer(
                tid,
                items_received=[
                    transfers.TransferItemReceive(
                        transfer_item_id=line_id, quantity_received=99
                    )
                ],
                current_user=_ADMIN,
            )
        )
    after = transfers._get_transfer(tid)
    assert after["status"] == transfers.TransferStatus.IN_TRANSIT
    # No units re-homed to B.
    assert _on_hand(db, product_id, "B") == 0


# ===========================================================================
# BUG-019 - a receive mismatch raises a follow-up task
# ===========================================================================


def test_short_receive_creates_discrepancy_task(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-SHORT-T"
    N, K = 10, 6

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    res = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=4  # 2 short of 6
                )
            ],
            current_user=_ADMIN,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.PARTIALLY_RECEIVED
    task_id = res["summary"]["discrepancy_task_id"]
    assert task_id
    task = db.get_collection("tasks").find_one({"task_id": task_id})
    assert task is not None
    # Store-scoped to the DESTINATION; carries the transfer id + short count.
    assert task["store_id"] == "B"
    assert task["task_type"] == "transfer_discrepancy"
    assert task["transfer_id"] == tid
    assert task["discrepancy"]["short"] == 2
    assert task["priority"] == "P2"  # short shipment is the urgent tier


def test_damaged_receive_creates_discrepancy_task(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-DMG-T"
    N, K, DMG = 8, 5, 1

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    res = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=K, quantity_damaged=DMG
                )
            ],
            current_user=_ADMIN,
        )
    )
    # Full received (status RECEIVED) but damaged -> still a discrepancy task.
    assert res["transfer"]["status"] == transfers.TransferStatus.RECEIVED
    task_id = res["summary"]["discrepancy_task_id"]
    assert task_id
    task = db.get_collection("tasks").find_one({"task_id": task_id})
    assert task["discrepancy"]["damaged"] == DMG


def test_clean_receive_creates_no_task(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-CLEAN-T"
    N, K = 7, 4

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    res = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=K  # exact, undamaged
                )
            ],
            current_user=_ADMIN,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.RECEIVED
    assert res["summary"]["discrepancy_task_id"] is None
    assert db.get_collection("tasks").count_documents({"transfer_id": tid}) == 0


# ===========================================================================
# BUG-019 - mismatch-task helper shape (pure, no DB lifecycle)
# ===========================================================================


def test_create_mismatch_task_fail_soft_without_db(monkeypatch):
    """No DB -> helper returns None and never raises (side-channel)."""
    monkeypatch.setattr(transfers, "_get_db", lambda: None)
    out = transfers._create_receive_mismatch_task(
        {"id": "T1", "to_location_id": "B"},
        {"short": 1, "surplus": 0, "damaged": 0},
    )
    assert out is None


# ===========================================================================
# BUG-018 - the dead-stub inventory transfer endpoints are GONE
# ===========================================================================


def test_inventory_transfer_send_receive_stubs_removed():
    """The fake-success POST /inventory/transfers/{id}/send and .../receive
    stubs (which moved no stock) must no longer exist on the inventory router.
    Callers must use the real /transfers/* workflow."""
    from api.routers import inventory

    assert not hasattr(inventory, "send_transfer")
    assert not hasattr(inventory, "receive_transfer")
    stub_paths = {
        "/transfers/{transfer_id}/send",
        "/transfers/{transfer_id}/receive",
    }
    live = {getattr(r, "path", "") for r in inventory.router.routes}
    assert not (stub_paths & live), f"dead stub still mounted: {stub_paths & live}"
