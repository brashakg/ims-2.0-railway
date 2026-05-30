"""
IMS 2.0 - Inter-store transfer REAL stock movement (SYSTEM_INTENT 5)
====================================================================
The `/api/v1/transfers` lifecycle used to mutate only the `stock_transfers`
document - it never moved serialized `stock_units`. A "completed" transfer
therefore left BOTH stores' on-hand wrong. SYSTEM_INTENT 5 requires:

  * SHIP    -> source-store on-hand drops by the shipped qty (units flipped
               to TRANSFERRED, no longer counted as on-hand at the source).
  * RECEIVE -> destination-store on-hand rises by the received qty (fresh
               AVAILABLE units minted at the destination).

This test drives the real endpoint functions against the REAL repositories
(StockRepository / ProductRepository) on a throwaway Mongo database at
MONGO_HOST=127.0.0.1, so the on-hand math is exercised end to end:

    seed N AVAILABLE units at store A
      -> create + approve + ship + receive a transfer of K to store B
      -> assert A on-hand == N - K  and  B on-hand == K
      -> re-ship + re-receive  ->  no double-move (still N-K / K)

A pure-helper layer (_line_ship_qty) is tested without any DB.

If Mongo isn't reachable the DB-backed tests SKIP (they don't fail), so the
suite stays green on machines without a local mongod while still proving the
behavior on CI's `mongo:7.0`.
"""

from __future__ import annotations

import os
import sys
import uuid
import asyncio

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import transfers  # noqa: E402


# ===========================================================================
# Pure helper: per-line ship quantity (no DB)
# ===========================================================================


def test_line_ship_qty_prefers_explicit_shipped():
    assert transfers._line_ship_qty({"quantity_shipped": 3, "quantity_requested": 5}) == 3


def test_line_ship_qty_falls_back_to_requested():
    assert transfers._line_ship_qty({"quantity_shipped": 0, "quantity_requested": 5}) == 5


def test_line_ship_qty_handles_missing_and_garbage():
    assert transfers._line_ship_qty({}) == 0
    assert transfers._line_ship_qty({"quantity_requested": "nope"}) == 0
    assert transfers._line_ship_qty({"quantity_requested": 4.0}) == 4


# ===========================================================================
# DB-backed lifecycle against the real repos
# ===========================================================================


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))


def _mongo_db():
    """Connect to a throwaway DB on the local mongo, or skip if unreachable."""
    try:
        from pymongo import MongoClient
    except Exception:  # noqa: BLE001
        pytest.skip("pymongo not installed")
    try:
        client = MongoClient(
            MONGO_HOST, MONGO_PORT, serverSelectionTimeoutMS=1500
        )
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no local mongo at {MONGO_HOST}:{MONGO_PORT} ({exc})")
    name = f"ims_test_transfer_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


def _on_hand(db, product_id, store_id):
    """On-hand at a store == AVAILABLE serialized units for the product.

    Uses the same availability statuses the inventory ledger counts, so this
    assertion mirrors what the Stock Ledger / POS would actually show.
    """
    avail = ["AVAILABLE", "available", "IN_STOCK", "in_stock"]
    return db.get_collection("stock_units").count_documents(
        {"product_id": product_id, "store_id": store_id, "status": {"$in": avail}}
    )


@pytest.fixture
def lifecycle(monkeypatch):
    """Real StockRepository on a throwaway mongo DB, wired into the transfers
    router (including transfer-doc + audit persistence)."""
    from database.repositories.product_repository import StockRepository

    client, db, name = _mongo_db()
    stock_repo = StockRepository(db.get_collection("stock_units"))

    monkeypatch.setattr(transfers, "get_stock_repository", lambda: stock_repo)
    # Persist transfer docs + audit rows into the same throwaway DB.
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


def test_ship_then_receive_moves_on_hand(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-MOVE-1"
    N, K = 10, 4

    _seed_units(stock_repo, product_id, "A", N)
    assert _on_hand(db, product_id, "A") == N
    assert _on_hand(db, product_id, "B") == 0

    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    # SUPERADMIN auto-approves on create -> straight to ship.
    assert transfer["status"] == transfers.TransferStatus.APPROVED

    shipped = asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))["transfer"]
    assert shipped["stock_shipped"] is True
    assert shipped["stock_units_moved_out"] == K
    # Source on-hand dropped by K; the K units are now TRANSFERRED, not gone.
    assert _on_hand(db, product_id, "A") == N - K
    assert (
        db.get_collection("stock_units").count_documents(
            {"product_id": product_id, "store_id": "A", "status": "TRANSFERRED"}
        )
        == K
    )
    # Destination not credited until RECEIVE.
    assert _on_hand(db, product_id, "B") == 0

    line_id = shipped["items"][0]["id"]
    received = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=K
                )
            ],
            current_user=_ADMIN,
        )
    )["transfer"]
    assert received["status"] == transfers.TransferStatus.RECEIVED
    # Destination on-hand rose by K; source unchanged from the post-ship state.
    assert _on_hand(db, product_id, "B") == K
    assert _on_hand(db, product_id, "A") == N - K


def test_reship_and_rereceive_do_not_double_move(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-IDEMP-1"
    N, K = 8, 3

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]

    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    assert _on_hand(db, product_id, "A") == N - K

    # Re-ship: the doc is IN_TRANSIT now so the endpoint guard would 400 in
    # the HTTP path, but even if the move helper itself is re-invoked it must
    # be a no-op thanks to the stock_shipped flag.
    moved = transfers._apply_ship_stock_move(transfers._get_transfer(tid))
    assert moved["stock_units_moved_out"] == K  # unchanged
    assert _on_hand(db, product_id, "A") == N - K  # still only K moved out

    line_id = transfer["items"][0]["id"]
    asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=K
                )
            ],
            current_user=_ADMIN,
        )
    )
    assert _on_hand(db, product_id, "B") == K

    # Re-receive the SAME qty: must not mint more destination units.
    after = transfers._apply_receive_stock_move(transfers._get_transfer(tid))
    transfers._save_transfer(after)
    assert _on_hand(db, product_id, "B") == K  # no duplicates


def test_partial_receive_then_complete_mints_only_delta(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-PARTIAL-1"
    N, K = 9, 5

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))
    line_id = transfer["items"][0]["id"]

    # First receive only 2 of 5.
    part = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=2
                )
            ],
            current_user=_ADMIN,
        )
    )["transfer"]
    assert part["status"] == transfers.TransferStatus.PARTIALLY_RECEIVED
    assert _on_hand(db, product_id, "B") == 2

    # Now receive the full 5 (cumulative) -> only 3 NEW units minted (delta).
    full = asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=line_id, quantity_received=5
                )
            ],
            current_user=_ADMIN,
        )
    )["transfer"]
    assert full["status"] == transfers.TransferStatus.RECEIVED
    assert _on_hand(db, product_id, "B") == 5  # 2 + 3, no double count


def test_ship_caps_at_available_source_units(lifecycle):
    """Never move phantom stock: if the source holds fewer AVAILABLE units than
    requested, only what exists leaves the floor (and on-hand can't go below 0)."""
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-SHORT-1"
    N, K = 2, 5  # request 5 but only 2 on hand

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    shipped = asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))["transfer"]
    assert shipped["stock_units_moved_out"] == N  # only the 2 that existed
    assert _on_hand(db, product_id, "A") == 0
    assert shipped["items"][0]["quantity_shipped"] == N


def test_fail_soft_when_stock_repo_down(lifecycle, monkeypatch):
    """No stock layer -> the transfer lifecycle still advances the doc; it just
    doesn't move units (pre-fix behavior, but no crash)."""
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: None)
    product_id = "PRD-FAILSOFT-1"
    transfer = _make_transfer(product_id, 3)
    tid = transfer["id"]
    shipped = asyncio.run(transfers.ship_transfer(tid, current_user=_ADMIN))["transfer"]
    assert shipped["status"] == transfers.TransferStatus.IN_TRANSIT
    # No stock_shipped flag set when there's no repo to move with.
    assert not shipped.get("stock_shipped")
