"""
IMS 2.0 - Transfers object-level IDOR guard
===========================================
Before this fix, every object-level transfer endpoint (PUT /{id}, /approve,
/start-picking, /complete-picking, /ship, /receive, /complete, /cancel,
/bulk-approve, /create-shiprocket-shipment, GET /{id}, GET /{id}/tracking)
fetched the transfer by id with ZERO store-membership check, so any
STORE_MANAGER / WORKSHOP_STAFF / AREA_MANAGER could drive ANOTHER pair of
stores' transfer -- and /ship + /receive move REAL stock_units. POST / also
accepted an arbitrary from_location_id, letting a store-A manager drain
store B. GET /pending leaked every store's pipeline.

These tests drive the endpoint functions directly (same pattern as
test_transfer_stock_movement.py) with EVERY repo/db accessor monkeypatched:
  * _transfers_coll  -> in-test fake collection (all docs seeded explicitly)
  * _get_db          -> None  (audit / ledger / mirror-purchase fail-soft skip)
  * get_stock_repository -> None (or an in-test fake for the stock-move test)

Assertions are field-level (status_code / detail / specific doc fields) --
never whole-JSON substring scans.

Guard contract under test (_assert_transfer_access):
  side="source" -> update/approve/picking/ship/cancel/bulk-approve/shiprocket
  side="dest"   -> receive/complete
  side="either" -> GET /{id} + GET /{id}/tracking
  create        -> validate_store_access(from_location_id)
  GET /pending  -> store-filtered for non-ADMIN/SUPERADMIN via user_store_scope
"""

from __future__ import annotations

import os
import sys
import asyncio

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import transfers  # noqa: E402


# ===========================================================================
# Fakes (no Mongo, no network)
# ===========================================================================


class _FakeColl:
    """Minimal stand-in for the pymongo `stock_transfers` collection."""

    def __init__(self):
        self.docs = {}

    def update_one(self, flt, update, upsert=False):
        _id = flt["id"]
        doc = self.docs.get(_id, {})
        doc.update(update["$set"])
        self.docs[_id] = doc

    def find_one(self, flt, projection=None):
        d = self.docs.get(flt["id"])
        return dict(d) if d else None

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs.values()]

    def count_documents(self, flt=None):
        return len(self.docs)


class _FakeStockRepo:
    """Minimal serialized-stock repo: one dict per unit, keyed by stock_id."""

    def __init__(self, units):
        self.units = {u["stock_id"]: dict(u) for u in units}

    def find_by_id(self, sid):
        u = self.units.get(sid)
        return dict(u) if u else None

    def find_many(self, flt, limit=None):
        out = [
            dict(u)
            for u in self.units.values()
            if all(u.get(k) == v for k, v in flt.items())
        ]
        return out[:limit] if limit else out

    def update(self, sid, fields):
        if sid in self.units:
            self.units[sid].update(fields)
            return True
        return False


# ===========================================================================
# Fixtures + seed helpers (every doc the handlers read is seeded here)
# ===========================================================================


@pytest.fixture()
def coll(monkeypatch):
    """Isolate ALL persistence: fake transfers collection, no db (audit/
    ledger/mirror-purchase skip fail-soft), no stock repo (lifecycle still
    advances; the stock-move test installs its own fake repo)."""
    fake = _FakeColl()
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: fake)
    monkeypatch.setattr(transfers, "_get_db", lambda: None)
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: None)
    transfers.STOCK_TRANSFERS.clear()
    return fake


def _user(role, stores):
    return {
        "user_id": f"u-{role.lower()}-{'-'.join(stores) or 'hq'}",
        "username": f"{role.lower()}",
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": stores[0] if stores else None,
    }


def _seed_transfer(tid, frm, to, status, **extra):
    doc = {
        "id": tid,
        "transfer_number": f"TRF-202606-{tid}",
        "transfer_type": "store_to_store",
        "from_location_id": frm,
        "from_location_name": f"Store {frm}",
        "to_location_id": to,
        "to_location_name": f"Store {to}",
        "items": [
            {
                "id": f"{tid}-line-1",
                "transfer_id": tid,
                "product_id": "P1",
                "sku": "SKU-1",
                "product_name": "Frame",
                "quantity_requested": 2,
                "quantity_shipped": 2,
                "quantity_received": 0,
                "quantity_damaged": 0,
                "status": "pending",
            }
        ],
        "total_items": 2,
        "total_value": 0,
        "priority": "normal",
        "status": status,
        "status_history": [],
        "tracking_number": None,
        "created_at": "2026-06-10T09:00:00",
        "updated_at": "2026-06-10T09:00:00",
    }
    doc.update(extra)
    transfers._save_transfer(doc)
    return doc


def _expect_403(coro):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 403
    return exc.value


# Canonical actors for a B -> C transfer.
MGR_A = _user("STORE_MANAGER", ["A"])  # foreign on both sides
MGR_B = _user("STORE_MANAGER", ["B"])  # source-side
MGR_C = _user("STORE_MANAGER", ["C"])  # dest-side
AM_A = _user("AREA_MANAGER", ["A"])  # area manager OUTSIDE the B/C territory
AM_B = _user("AREA_MANAGER", ["B"])  # area manager covering the source
WS_A = _user("WORKSHOP_STAFF", ["A"])  # workshop staff at a foreign store


# ===========================================================================
# Foreign-store callers are denied on every lifecycle action (the P1)
# ===========================================================================


def test_foreign_manager_cannot_ship(coll):
    _seed_transfer("t1", "B", "C", "approved")
    err = _expect_403(transfers.ship_transfer("t1", current_user=MGR_A))
    assert err.detail == "No access to this transfer's store"
    # The doc was not advanced and no stock flags were set.
    assert coll.docs["t1"]["status"] == "approved"
    assert "stock_shipped" not in coll.docs["t1"]


def test_foreign_workshop_staff_cannot_ship(coll):
    _seed_transfer("t1", "B", "C", "approved")
    _expect_403(transfers.ship_transfer("t1", current_user=WS_A))
    assert coll.docs["t1"]["status"] == "approved"


def test_foreign_manager_cannot_receive(coll):
    _seed_transfer("t2", "B", "C", "in_transit")
    err = _expect_403(
        transfers.receive_transfer("t2", items_received=[], current_user=MGR_A)
    )
    assert err.detail == "No access to this transfer's store"
    assert coll.docs["t2"]["status"] == "in_transit"


def test_foreign_area_manager_cannot_approve(coll):
    _seed_transfer("t3", "B", "C", "pending_approval")
    _expect_403(
        transfers.approve_transfer(
            "t3",
            approval=transfers.TransferApproval(approved=True),
            current_user=AM_A,
        )
    )
    assert coll.docs["t3"]["status"] == "pending_approval"
    assert coll.docs["t3"].get("approved_by") is None


def test_foreign_area_manager_cannot_cancel(coll):
    _seed_transfer("t4", "B", "C", "approved")
    _expect_403(
        transfers.cancel_transfer("t4", reason="hostile", current_user=AM_A)
    )
    assert coll.docs["t4"]["status"] == "approved"
    assert "cancelled_by" not in coll.docs["t4"]


def test_foreign_manager_cannot_update(coll):
    _seed_transfer("t5", "B", "C", "approved", notes="original")
    _expect_403(
        transfers.update_transfer(
            "t5",
            update=transfers.TransferUpdate(notes="tampered"),
            current_user=MGR_A,
        )
    )
    assert coll.docs["t5"]["notes"] == "original"


def test_foreign_manager_cannot_start_or_complete_picking(coll):
    _seed_transfer("t6", "B", "C", "approved")
    _expect_403(transfers.start_picking("t6", current_user=MGR_A))
    assert coll.docs["t6"]["status"] == "approved"

    _seed_transfer("t7", "B", "C", "picking")
    _expect_403(
        transfers.complete_picking("t7", items_picked=[], current_user=MGR_A)
    )
    assert coll.docs["t7"]["status"] == "picking"


def test_foreign_manager_cannot_complete(coll):
    _seed_transfer("t8", "B", "C", "received")
    _expect_403(transfers.complete_transfer("t8", current_user=MGR_A))
    assert coll.docs["t8"]["status"] == "received"


def test_foreign_manager_cannot_create_shiprocket_shipment(coll):
    _seed_transfer("t9", "B", "C", "approved")
    _expect_403(
        transfers.create_shiprocket_shipment_for_transfer(
            "t9", current_user=MGR_A
        )
    )
    assert coll.docs["t9"].get("shiprocket_shipment_id") is None


def test_foreign_manager_cannot_read_single_or_tracking(coll):
    _seed_transfer("t10", "B", "C", "in_transit", tracking_number="AWB1")
    _expect_403(transfers.get_transfer("t10", current_user=MGR_A))
    _expect_403(transfers.get_transfer_tracking("t10", current_user=MGR_A))


# ===========================================================================
# Side-correctness: source may ship (not receive); dest may receive (not ship)
# ===========================================================================


def test_source_manager_can_ship_and_stock_moves(coll, monkeypatch):
    """The guard is purely additive: the source-store manager still ships and
    the REAL stock move still happens (units flip AVAILABLE -> TRANSFERRED)."""
    repo = _FakeStockRepo(
        [
            {"stock_id": "u1", "product_id": "P1", "store_id": "B",
             "status": "AVAILABLE"},
            {"stock_id": "u2", "product_id": "P1", "store_id": "B",
             "status": "AVAILABLE"},
        ]
    )
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: repo)
    _seed_transfer("t11", "B", "C", "approved")

    res = asyncio.run(transfers.ship_transfer("t11", current_user=MGR_B))
    assert res["transfer"]["status"] == transfers.TransferStatus.IN_TRANSIT
    assert res["transfer"]["stock_shipped"] is True
    assert res["transfer"]["stock_units_moved_out"] == 2
    assert repo.units["u1"]["status"] == "TRANSFERRED"
    assert repo.units["u2"]["status"] == "TRANSFERRED"
    assert coll.docs["t11"]["status"] == "in_transit"


def test_dest_manager_cannot_ship(coll):
    _seed_transfer("t12", "B", "C", "approved")
    _expect_403(transfers.ship_transfer("t12", current_user=MGR_C))
    assert coll.docs["t12"]["status"] == "approved"


def test_dest_manager_can_receive(coll):
    _seed_transfer("t13", "B", "C", "in_transit")
    res = asyncio.run(
        transfers.receive_transfer(
            "t13",
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id="t13-line-1", quantity_received=2
                )
            ],
            current_user=MGR_C,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.RECEIVED
    assert res["transfer"]["received_by"] == MGR_C["user_id"]
    assert res["summary"]["received"] == 2
    assert coll.docs["t13"]["status"] == "received"


def test_source_manager_cannot_receive(coll):
    _seed_transfer("t14", "B", "C", "in_transit")
    _expect_403(
        transfers.receive_transfer("t14", items_received=[], current_user=MGR_B)
    )
    assert coll.docs["t14"]["status"] == "in_transit"


def test_complete_is_dest_side(coll):
    _seed_transfer("t15", "B", "C", "received")
    _expect_403(transfers.complete_transfer("t15", current_user=MGR_B))
    assert coll.docs["t15"]["status"] == "received"

    res = asyncio.run(transfers.complete_transfer("t15", current_user=MGR_C))
    assert res["transfer"]["status"] == transfers.TransferStatus.COMPLETED
    assert coll.docs["t15"]["status"] == "completed"
    assert coll.docs["t15"]["completed_by"] == MGR_C["user_id"]


def test_update_is_source_side(coll):
    _seed_transfer("t16", "B", "C", "approved", notes="original")
    _expect_403(
        transfers.update_transfer(
            "t16",
            update=transfers.TransferUpdate(notes="dest-edit"),
            current_user=MGR_C,
        )
    )
    res = asyncio.run(
        transfers.update_transfer(
            "t16",
            update=transfers.TransferUpdate(notes="source-edit"),
            current_user=MGR_B,
        )
    )
    assert res["transfer"]["notes"] == "source-edit"
    assert coll.docs["t16"]["notes"] == "source-edit"


def test_source_area_manager_can_approve(coll):
    _seed_transfer("t17", "B", "C", "pending_approval")
    res = asyncio.run(
        transfers.approve_transfer(
            "t17",
            approval=transfers.TransferApproval(approved=True),
            current_user=AM_B,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.APPROVED
    assert coll.docs["t17"]["approved_by"] == AM_B["user_id"]


def test_source_manager_picking_flow_still_works(coll):
    _seed_transfer("t18", "B", "C", "approved")
    res = asyncio.run(transfers.start_picking("t18", current_user=MGR_B))
    assert res["transfer"]["status"] == transfers.TransferStatus.PICKING
    res = asyncio.run(
        transfers.complete_picking(
            "t18",
            items_picked=[{"item_id": "t18-line-1", "quantity_picked": 2}],
            current_user=MGR_B,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.PACKED
    assert coll.docs["t18"]["items"][0]["quantity_shipped"] == 2


def test_either_side_can_read_single_and_tracking(coll):
    _seed_transfer("t19", "B", "C", "in_transit", tracking_number="AWB19")
    for user in (MGR_B, MGR_C):
        got = asyncio.run(transfers.get_transfer("t19", current_user=user))
        assert got["transfer"]["id"] == "t19"
        trk = asyncio.run(
            transfers.get_transfer_tracking("t19", current_user=user)
        )
        assert trk["tracking_number"] == "AWB19"


# ===========================================================================
# ADMIN / SUPERADMIN bypass (cross-store by design)
# ===========================================================================


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admin_roles_bypass_all_sides(coll, role):
    hq = _user(role, [])
    _seed_transfer("t20", "B", "C", "approved")
    res = asyncio.run(transfers.ship_transfer("t20", current_user=hq))
    assert res["transfer"]["status"] == transfers.TransferStatus.IN_TRANSIT

    _seed_transfer("t21", "B", "C", "in_transit")
    res = asyncio.run(
        transfers.receive_transfer(
            "t21",
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id="t21-line-1", quantity_received=2
                )
            ],
            current_user=hq,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.RECEIVED

    _seed_transfer("t22", "D", "E", "pending_approval")
    res = asyncio.run(
        transfers.approve_transfer(
            "t22",
            approval=transfers.TransferApproval(approved=True),
            current_user=hq,
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.APPROVED

    _seed_transfer("t23", "D", "E", "approved")
    res = asyncio.run(
        transfers.cancel_transfer("t23", reason="hq call", current_user=hq)
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


# ===========================================================================
# Create: arbitrary from_location_id is rejected
# ===========================================================================


def _transfer_input(frm, to):
    return transfers.TransferInput(
        transfer_type=transfers.TransferType.STORE_TO_STORE,
        from_location_id=frm,
        from_location_name=f"Store {frm}",
        to_location_id=to,
        to_location_name=f"Store {to}",
        items=[
            transfers.TransferItemInput(
                product_id="P1", sku="SKU-1", product_name="Frame",
                quantity_requested=1,
            )
        ],
    )


def test_create_with_foreign_source_store_403(coll):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            transfers.create_transfer(_transfer_input("B", "C"), current_user=MGR_A)
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "No access to store B"
    assert coll.docs == {}  # nothing persisted


def test_create_from_own_store_ok(coll):
    res = asyncio.run(
        transfers.create_transfer(_transfer_input("A", "B"), current_user=MGR_A)
    )
    assert res["transfer"]["from_location_id"] == "A"
    assert res["transfer"]["status"] == transfers.TransferStatus.PENDING_APPROVAL
    assert res["transfer"]["id"] in coll.docs


def test_admin_can_create_from_any_store(coll):
    hq = _user("ADMIN", [])
    res = asyncio.run(
        transfers.create_transfer(_transfer_input("B", "C"), current_user=hq)
    )
    # Admin creation keeps its pre-fix auto-approve behavior.
    assert res["transfer"]["status"] == transfers.TransferStatus.APPROVED


# ===========================================================================
# Bulk approve: per-item deny, mixed batch still approves in-scope ids
# ===========================================================================


def test_bulk_approve_skips_foreign_transfers(coll):
    _seed_transfer("bk1", "B", "C", "pending_approval")
    _seed_transfer("bk2", "D", "E", "pending_approval")
    res = asyncio.run(
        transfers.bulk_approve_transfers(
            ["bk1", "bk2", "missing"], current_user=AM_B
        )
    )
    assert res["approved_count"] == 1
    assert coll.docs["bk1"]["status"] == "approved"
    assert coll.docs["bk2"]["status"] == "pending_approval"  # untouched
    errors_by_id = {e["id"]: e["error"] for e in res["errors"]}
    assert errors_by_id["bk2"] == "No access to this transfer's store"
    assert errors_by_id["missing"] == "Not found"


# ===========================================================================
# GET /pending: store-scoped callers only see their own pipeline
# ===========================================================================


def test_pending_list_filtered_for_store_scoped_caller(coll):
    _seed_transfer("p1", "A", "B", "pending_approval")
    _seed_transfer("p2", "B", "C", "in_transit")
    _seed_transfer("p3", "D", "E", "pending_approval")

    res = asyncio.run(transfers.get_pending_transfers(current_user=MGR_A))
    seen = [t["id"] for t in res["pending_approval"]]
    assert seen == ["p1"]  # the foreign D->E pending transfer is hidden
    assert res["in_transit"] == []  # B->C does not touch store A

    # B sees both sides it participates in (dest of p1, source of p2).
    res_b = asyncio.run(transfers.get_pending_transfers(current_user=MGR_B))
    assert [t["id"] for t in res_b["pending_approval"]] == ["p1"]
    assert [t["id"] for t in res_b["in_transit"]] == ["p2"]


def test_pending_location_param_cannot_widen_scope(coll):
    _seed_transfer("p4", "D", "E", "pending_approval")
    res = asyncio.run(
        transfers.get_pending_transfers(location_id="D", current_user=MGR_A)
    )
    assert res["pending_approval"] == []


def test_pending_admin_sees_all(coll):
    _seed_transfer("p5", "A", "B", "pending_approval")
    _seed_transfer("p6", "D", "E", "pending_approval")
    res = asyncio.run(
        transfers.get_pending_transfers(current_user=_user("ADMIN", []))
    )
    assert {t["id"] for t in res["pending_approval"]} == {"p5", "p6"}
