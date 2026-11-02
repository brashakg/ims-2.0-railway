"""
IMS 2.0 - Inter-store transfer REAL stock movement (SYSTEM_INTENT 5)
====================================================================
The `/api/v1/transfers` lifecycle used to mutate only the `stock_transfers`
document - it never moved serialized `stock_units`. A "completed" transfer
therefore left BOTH stores' on-hand wrong. SYSTEM_INTENT 5 requires:

  * SHIP    -> source-store on-hand drops by the shipped qty (units flipped
               to TRANSFERRED, no longer counted as on-hand at the source).
  * RECEIVE -> destination-store on-hand rises by the received qty by RE-HOMING
               the same shipped units (TRANSFERRED -> AVAILABLE at the
               destination store, keeping each unit's original barcode). A
               transfer never mints new stock - the unit just changes location.

This test drives the real endpoint functions against the REAL repositories
(StockRepository / ProductRepository) on a throwaway Mongo database at
MONGO_HOST=127.0.0.1, so the on-hand math is exercised end to end:

    seed N AVAILABLE units at store A
      -> create + approve + ship + receive a transfer of K to store B
      -> assert A on-hand == N - K  and  B on-hand == K
      -> re-ship + re-receive  ->  no double-move (still N-K / K)

Runs on mongomock (the per-piece flow: pack, ship, scan in).

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
import transfer_box as box  # noqa: E402


# ===========================================================================
# DB-backed lifecycle against the real repos
# ===========================================================================


MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))


def _mongo_db():
    """A throwaway mongomock database (S3: runs everywhere, no mongod)."""
    mongomock = pytest.importorskip("mongomock")
    name = f"ims_test_transfer_{uuid.uuid4().hex[:8]}"
    client = mongomock.MongoClient()
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
    monkeypatch.setattr(transfers, "_writeback_units_left", lambda *a, **k: None)

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

    shipped = box.ship(transfers, tid, _ADMIN)["transfer"]
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

    received = box.receive(transfers, tid, _ADMIN)["transfer"]
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

    box.ship(transfers, tid, _ADMIN)
    assert _on_hand(db, product_id, "A") == N - K

    # Re-ship: the doc is IN_TRANSIT now so the endpoint guard would 400 in
    # the HTTP path, but even if the move helper itself is re-invoked it must
    # be a no-op thanks to the stock_shipped flag.
    moved = transfers._apply_ship_stock_move(transfers._get_transfer(tid))
    assert moved["stock_units_moved_out"] == K  # unchanged
    assert _on_hand(db, product_id, "A") == N - K  # still only K moved out

    box.receive(transfers, tid, _ADMIN)
    assert _on_hand(db, product_id, "B") == K

    # Scanning the SAME pieces again (status RECEIVED: refused) never mints.
    first = transfers._get_transfer(tid)["items"][0]["received_stock_ids"][0]
    code = db["stock_units"].find_one({"stock_id": first})["barcode"]
    with pytest.raises(Exception):
        asyncio.run(transfers.receive_transfer(
            tid, transfers.TransferPieceReceive(barcode=code), current_user=_ADMIN
        ))
    assert _on_hand(db, product_id, "B") == K  # no duplicates


def test_partial_receive_then_complete_moves_only_delta(lifecycle):
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-PARTIAL-1"
    N, K = 9, 5

    _seed_units(stock_repo, product_id, "A", N)
    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    box.ship(transfers, tid, _ADMIN)
    line_id = transfer["items"][0]["id"]

    # First scan in only 2 of 5.
    part = box.receive(transfers, tid, _ADMIN, {line_id: (2, 0)})["transfer"]
    assert part["status"] == transfers.TransferStatus.PARTIALLY_RECEIVED
    assert _on_hand(db, product_id, "B") == 2

    # Scanning all 5 again: the first 2 count once, 3 more land.
    full = box.receive(transfers, tid, _ADMIN, {line_id: (5, 0)})["transfer"]
    assert full["status"] == transfers.TransferStatus.RECEIVED
    assert _on_hand(db, product_id, "B") == 5  # 2 + 3, no double count


def test_received_units_keep_original_barcode_and_no_mint(lifecycle):
    """A transfer re-homes the SAME physical units: destination on-hand is made
    of the very units shipped from the source (same ids + same barcodes), and
    the total number of stock_unit docs for the product never grows (no minting).
    This locks the 'barcode follows the unit, unique per unit per purchase' rule.
    """
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    units = db.get_collection("stock_units")
    product_id = "PRD-BARCODE-1"
    N, K = 6, 4

    _seed_units(stock_repo, product_id, "A", N)
    total_before = units.count_documents({"product_id": product_id})
    barcodes_before = {
        d["barcode"] for d in units.find({"product_id": product_id}, {"barcode": 1})
    }

    transfer = _make_transfer(product_id, K)
    tid = transfer["id"]
    box.ship(transfers, tid, _ADMIN)
    box.receive(transfers, tid, _ADMIN)

    # No new stock_unit docs were created - the units just changed location.
    assert units.count_documents({"product_id": product_id}) == total_before == N
    # The K units now AVAILABLE at B carry their ORIGINAL (source) barcodes.
    dest = list(units.find({"product_id": product_id, "store_id": "B",
                            "status": "AVAILABLE"}, {"barcode": 1}))
    assert len(dest) == K
    dest_barcodes = {d["barcode"] for d in dest}
    assert dest_barcodes <= barcodes_before  # subset: same physical labels moved
    # And none of them were re-minted with a destination-prefixed barcode.
    assert all(not bc.startswith("B-") for bc in dest_barcodes)


def test_ask_caps_at_free_source_units(lifecycle):
    """Never move phantom stock: S3 caps the request itself at what is FREE at
    the source, and a short-packed box ships only the pieces scanned."""
    db = lifecycle["db"]
    stock_repo = lifecycle["stock_repo"]
    product_id = "PRD-SHORT-1"
    _seed_units(stock_repo, product_id, "A", 2)
    with pytest.raises(Exception) as exc:
        _make_transfer(product_id, 5)  # request 5 but only 2 free
    assert getattr(exc.value, "status_code", None) == 409
    transfer = _make_transfer(product_id, 2)
    shipped = box.ship(
        transfers, transfer["id"], _ADMIN, per_line={transfer["items"][0]["id"]: 1}
    )["transfer"]
    assert shipped["stock_units_moved_out"] == 1
    assert _on_hand(db, product_id, "A") == 1
    assert shipped["items"][0]["quantity_shipped"] == 1


def test_ship_refuses_when_the_stock_records_cannot_be_read(lifecycle, monkeypatch):
    """S3: a per-piece move never runs blind -- no database, no ship (503),
    instead of advancing the doc without moving the pieces."""
    db = lifecycle["db"]
    product_id = "PRD-FAILSOFT-1"
    _seed_units(lifecycle["stock_repo"], product_id, "A", 3)
    transfer = _make_transfer(product_id, 3)
    tid = transfer["id"]
    box.pack_all(transfers, tid, _ADMIN)
    monkeypatch.setattr(transfers, "_get_db", lambda: None)
    with pytest.raises(Exception) as exc:
        asyncio.run(transfers.ship_transfer(tid, "T1", None, "DTDC", False, _ADMIN))
    assert getattr(exc.value, "status_code", None) == 503
    assert _on_hand(db, product_id, "A") == 3
