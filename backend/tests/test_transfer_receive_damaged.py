"""
IMS 2.0 - Transfer RECEIVE hardening (BUG-009 / BUG-011 / BUG-019)
==================================================================
Three confirmed receive-side defects, re-stated for the S3 per-piece flow
(pieces scanned OUT into the box, scanned IN at the receiving shop):

  * BUG-009 - a piece damaged in the box must land in QUARANTINE at the
    receiving shop, never on the sellable shelf; destination sellable on-hand
    excludes it.
  * BUG-011 - a receive can never exceed what shipped: a piece that was not
    in the box is refused (400) and nothing moves.
  * BUG-019 - a receipt that does not match (pieces missing on the way or
    damaged) raises a follow-up task in the canonical `tasks` collection,
    store-scoped to the destination, when the box is accepted (Complete).

Runs on mongomock with the real StockRepository.
"""

from __future__ import annotations

import os
import sys
import asyncio

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from fastapi import HTTPException  # noqa: E402

from api.routers import transfers  # noqa: E402
import transfer_box as box  # noqa: E402


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

    db = mongomock.MongoClient()["ims_test_transfer_dmg"]
    stock_repo = StockRepository(db.get_collection("stock_units"))
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(
        transfers, "_transfers_coll", lambda: db.get_collection("stock_transfers")
    )
    monkeypatch.setattr(transfers, "_get_db", lambda: db)
    monkeypatch.setattr(transfers, "_writeback_units_left", lambda *a, **k: None)
    return {"db": db, "stock_repo": stock_repo}


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


def _shipped(lifecycle, product_id, n, k):
    """Seed n pieces at A, ask for k to B, pack and ship them."""
    _seed_units(lifecycle["stock_repo"], product_id, "A", n)
    payload = transfers.TransferInput(
        transfer_type=transfers.TransferType.STORE_TO_STORE,
        from_location_id="A",
        from_location_name="Store A",
        to_location_id="B",
        to_location_name="Store B",
        items=[
            transfers.TransferItemInput(
                product_id=product_id, sku="SKU-1", product_name="Frame", quantity_requested=k,
            )
        ],
    )
    t = asyncio.run(transfers.create_transfer(payload, current_user=_ADMIN))["transfer"]
    box.ship(transfers, t["id"], _ADMIN)
    return t["id"], t["items"][0]["id"]


# ===========================================================================
# BUG-009 - damaged pieces quarantined, good pieces available
# ===========================================================================


def test_receive_with_damaged_quarantines_only_damaged(lifecycle):
    db = lifecycle["db"]
    product_id = "PRD-DMG-1"
    K, DMG = 6, 2  # ship 6, of which 2 arrive damaged
    tid, line_id = _shipped(lifecycle, product_id, 10, K)

    received = box.receive(transfers, tid, _ADMIN, {line_id: (K, DMG)})["transfer"]

    assert received["status"] == transfers.TransferStatus.RECEIVED
    # Destination SELLABLE on-hand rose only by the GOOD pieces (K - DMG).
    assert _on_hand(db, product_id, "B") == K - DMG
    # The damaged pieces are at B but QUARANTINED -> excluded from on-hand/POS.
    assert _count_status(db, product_id, "B", transfers.STOCK_STATUS_QUARANTINED) == DMG
    line = received["items"][0]
    assert (line["quantity_received"], line["quantity_damaged"]) == (K, DMG)


def test_receive_all_damaged_quarantines_all(lifecycle):
    db = lifecycle["db"]
    product_id = "PRD-DMG-ALL"
    K = 3
    tid, line_id = _shipped(lifecycle, product_id, 5, K)
    box.receive(transfers, tid, _ADMIN, {line_id: (K, K)})
    assert _on_hand(db, product_id, "B") == 0
    assert _count_status(db, product_id, "B", transfers.STOCK_STATUS_QUARANTINED) == K


# ===========================================================================
# BUG-011 - a receive can never exceed what shipped
# ===========================================================================


def test_a_piece_not_in_the_box_is_refused_and_nothing_moves(lifecycle):
    db = lifecycle["db"]
    product_id = "PRD-OVER-1"
    tid, _ = _shipped(lifecycle, product_id, 10, 4)
    # One of the six pieces still on A's shelf, scanned at B.
    stray = db["stock_units"].find_one({"product_id": product_id, "store_id": "A",
                                        "status": "AVAILABLE"})["barcode"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(transfers.receive_transfer(
            tid, transfers.TransferPieceReceive(barcode=stray), current_user=_ADMIN
        ))
    assert exc.value.status_code == 400
    after = transfers._get_transfer(tid)
    assert after["status"] == transfers.TransferStatus.IN_TRANSIT
    assert _on_hand(db, product_id, "B") == 0


# ===========================================================================
# BUG-019 - a mismatched receipt raises a follow-up task (at Complete)
# ===========================================================================


def test_short_receipt_creates_discrepancy_task(lifecycle):
    db = lifecycle["db"]
    product_id = "PRD-SHORT-T"
    tid, line_id = _shipped(lifecycle, product_id, 10, 6)

    res = box.receive_complete(transfers, tid, _ADMIN, {line_id: (4, 0)})  # 2 short of 6
    task_id = res["transfer"]["discrepancy_task_id"]
    assert task_id
    task = db.get_collection("tasks").find_one({"task_id": task_id})
    assert task["store_id"] == "B"
    assert task["task_type"] == "transfer_discrepancy"
    assert task["transfer_id"] == tid
    assert task["discrepancy"]["short"] == 2
    assert task["priority"] == "P2"
    assert len(res["transfer"]["missing_pieces"]) == 2


def test_damaged_receipt_creates_discrepancy_task(lifecycle):
    db = lifecycle["db"]
    tid, line_id = _shipped(lifecycle, "PRD-DMG-T", 8, 5)
    res = box.receive_complete(transfers, tid, _ADMIN, {line_id: (5, 1)})
    task = db.get_collection("tasks").find_one({"task_id": res["transfer"]["discrepancy_task_id"]})
    assert task["discrepancy"]["damaged"] == 1


def test_clean_receipt_creates_no_task(lifecycle):
    db = lifecycle["db"]
    tid, line_id = _shipped(lifecycle, "PRD-CLEAN-T", 7, 4)
    res = box.receive_complete(transfers, tid, _ADMIN, {line_id: (4, 0)})
    assert res["transfer"].get("discrepancy_task_id") is None
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
    # A6 2026-09-27: the list/create stubs (fake TRF- number, wrote nothing)
    # followed them out.
    assert not hasattr(inventory, "list_transfers")
    assert not hasattr(inventory, "create_transfer")
    stub_paths = {
        "/transfers",
        "/transfers/{transfer_id}/send",
        "/transfers/{transfer_id}/receive",
    }
    live = {getattr(r, "path", "") for r in inventory.router.routes}
    assert not (stub_paths & live), f"dead stub still mounted: {stub_paths & live}"
