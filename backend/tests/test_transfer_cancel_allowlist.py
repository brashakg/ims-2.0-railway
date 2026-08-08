"""
IMS 2.0 - Transfer CANCEL allowlist (PR #959 round 2)
=====================================================
cancel_transfer used a status DENYLIST (completed / cancelled / in_transit),
which still ACCEPTED a cancel at RECEIVED / PARTIALLY_RECEIVED -- i.e. after
ship moved real units out of the source store and receive re-homed them at the
destination. Cancelling there performs no stock reversal (the units stay
re-homed while the doc reads CANCELLED) and permanently skips the
inter-entity/inter-state GST deemed-supply mirror invoice, which books only
at /complete.

The guard is now an ALLOWLIST of strictly pre-ship statuses
(CANCELLABLE_TRANSFER_STATUSES): draft, pending_approval, approved, picking,
packed, rejected, plus the legacy lowercase "pending" the endless-aisle writer
stamps. Matched case-insensitively (the agent-proposals writer stamps
uppercase "DRAFT").

These tests drive the real endpoint function with the same isolated fake
collection harness as test_idor_transfers.py (no Mongo needed -- cancel moves
no stock).
"""

from __future__ import annotations

import os
import sys
import asyncio

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import transfers  # noqa: E402


class _FakeColl:
    """Minimal stand-in for the stock_transfers pymongo collection."""

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


@pytest.fixture()
def coll(monkeypatch):
    fake = _FakeColl()
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: fake)
    monkeypatch.setattr(transfers, "_get_db", lambda: None)
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: None)
    transfers.STOCK_TRANSFERS.clear()
    return fake


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["A", "B"],
    "active_store_id": "A",
}


def _seed(coll, tid, status):
    coll.docs[tid] = {
        "id": tid,
        "transfer_number": f"TRF-202608-{tid}",
        "transfer_type": "store_to_store",
        "from_location_id": "A",
        "from_location_name": "Store A",
        "to_location_id": "B",
        "to_location_name": "Store B",
        "items": [
            {
                "id": f"{tid}-line-1",
                "transfer_id": tid,
                "product_id": "P1",
                "sku": "SKU-1",
                "product_name": "Frame",
                "quantity_requested": 2,
            }
        ],
        "status": status,
        "status_history": [],
        "created_at": "2026-08-01T10:00:00",
    }


def _cancel(tid):
    return asyncio.run(
        transfers.cancel_transfer(tid, reason="test", current_user=_ADMIN)
    )


def _expect_400(tid):
    with pytest.raises(HTTPException) as exc:
        _cancel(tid)
    assert exc.value.status_code == 400
    return exc.value


# ===========================================================================
# Post-ship statuses must be REJECTED (the P0: no stock reversal + skipped
# GST mirror invoice).
# ===========================================================================


def test_cancel_at_received_is_rejected(coll):
    _seed(coll, "t-recv", "received")
    _expect_400("t-recv")
    assert coll.docs["t-recv"]["status"] == "received"
    assert "cancelled_at" not in coll.docs["t-recv"]


def test_cancel_at_partially_received_is_rejected(coll):
    _seed(coll, "t-part", "partially_received")
    _expect_400("t-part")
    assert coll.docs["t-part"]["status"] == "partially_received"
    assert "cancelled_at" not in coll.docs["t-part"]


def test_cancel_at_in_transit_still_rejected_with_original_message(coll):
    _seed(coll, "t-transit", "in_transit")
    err = _expect_400("t-transit")
    assert "in transit" in str(err.detail)
    assert coll.docs["t-transit"]["status"] == "in_transit"


def test_cancel_at_completed_and_cancelled_keep_original_message(coll):
    _seed(coll, "t-done", "completed")
    err = _expect_400("t-done")
    assert "completed or already cancelled" in str(err.detail)

    _seed(coll, "t-gone", "cancelled")
    err = _expect_400("t-gone")
    assert "completed or already cancelled" in str(err.detail)


# ===========================================================================
# Pre-ship statuses must SUCCEED (nothing has moved; cancel is harmless).
# ===========================================================================


def test_cancel_at_packed_succeeds(coll):
    _seed(coll, "t-packed", "packed")
    res = _cancel("t-packed")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
    assert coll.docs["t-packed"]["status"] == "cancelled"
    assert coll.docs["t-packed"]["cancellation_reason"] == "test"


def test_cancel_at_rejected_succeeds(coll):
    # REJECTED is pre-ship (zero stock moved) -- kept cancellable server-side
    # so dead requests can be tidied, even though the FE deliberately hides
    # the button there.
    _seed(coll, "t-rej", "rejected")
    res = _cancel("t-rej")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
    assert coll.docs["t-rej"]["status"] == "cancelled"


def test_cancel_all_other_preship_statuses_succeed(coll):
    for i, status in enumerate(["draft", "pending_approval", "approved", "picking"]):
        tid = f"t-pre-{i}"
        _seed(coll, tid, status)
        res = _cancel(tid)
        assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


# ===========================================================================
# Legacy writers: uppercase DRAFT (agent proposals) and lowercase pending
# (endless aisle) are both pre-ship and must stay cancellable.
# ===========================================================================


def test_cancel_legacy_uppercase_draft_succeeds(coll):
    _seed(coll, "t-updraft", "DRAFT")
    res = _cancel("t-updraft")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


def test_cancel_legacy_pending_succeeds(coll):
    _seed(coll, "t-legacy", "pending")
    res = _cancel("t-legacy")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


def test_allowlist_is_strictly_preship(coll):
    # The allowlist must never contain a post-ship status.
    for banned in (
        "in_transit",
        "partially_received",
        "received",
        "completed",
        "cancelled",
        "sent",
    ):
        assert banned not in transfers.CANCELLABLE_TRANSFER_STATUSES
