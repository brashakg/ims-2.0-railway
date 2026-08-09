"""
IMS 2.0 - Endless-aisle <-> transfer cancel back-sync
=====================================================
An endless-aisle (feature #38) request creates a linked stock_transfers doc
(endless_aisle_request_id) and advances the REQUEST label
PENDING->ACCEPTED->TRANSFER_CREATED->SHIPPED->DELIVERED. Before this fix,
cancel_transfer never looked at endless_aisle_request_id, so cancelling the
linked transfer stranded the request at TRANSFER_CREATED pointing at a
CANCELLED transfer -- from which the (decorative) EA ship/deliver endpoints
could still march it to DELIVERED.

The fix: on cancel_transfer of an EA-linked transfer, back-sync the request to
CANCELLED via endless_aisle.cancel_linked_request -- but ONLY when CANCELLED is
a legal transition (pre-ship states), fail-soft, and touching only the request
status (no stock, no GST).

Scope guard: this session intentionally does NOT change whether/how EA moves
stock or books GST -- only the cancel back-sync. These tests pin that boundary.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import transfers  # noqa: E402
from api.services import endless_aisle as svc  # noqa: E402


# ===========================================================================
# Fake endless_aisle_requests collection (find_one + guarded find_one_and_update)
# ===========================================================================


class _FakeEAColl:
    def __init__(self):
        self.docs = {}  # keyed by request_id
        self.raise_on_update = False

    def find_one(self, flt, projection=None):
        d = self.docs.get(flt.get("request_id"))
        if d is None:
            return None
        if "status" in flt and d.get("status") != flt["status"]:
            return None
        return dict(d)

    def find_one_and_update(self, flt, update, return_document=None):
        if self.raise_on_update:
            raise RuntimeError("db boom")
        rid = flt.get("request_id")
        d = self.docs.get(rid)
        if d is None:
            return None
        # Guarded transition: filter pins the current status -> a concurrent
        # loser (status already moved) matches nothing.
        if "status" in flt and d.get("status") != flt["status"]:
            return None
        for op, fields in update.items():
            if op == "$set":
                d.update(fields)
            elif op == "$push":
                for k, v in fields.items():
                    d.setdefault(k, []).append(v)
        self.docs[rid] = d
        return dict(d)


class _FakeDb:
    def __init__(self, ea_coll):
        self._ea = ea_coll

    def get_collection(self, name):
        if name == svc.COLLECTION:
            return self._ea
        return None


_ACTOR = {"user_id": "u-admin", "username": "admin"}


def _seed_request(ea, rid, status):
    ea.docs[rid] = {
        "_id": rid,
        "request_id": rid,
        "status": status,
        "product_id": "P1",
        "qty": 1,
        "selling_store_id": "A",
        "source_store_id": "B",
        "status_history": [],
    }


# ===========================================================================
# Unit: cancel_linked_request -- only pre-ship states flip; fail-soft always
# ===========================================================================


@pytest.mark.parametrize(
    "start",
    [svc.STATUS_PENDING, svc.STATUS_ACCEPTED, svc.STATUS_TRANSFER_CREATED],
)
def test_preship_request_flips_to_cancelled(start):
    ea = _FakeEAColl()
    _seed_request(ea, "EAR-1", start)
    out = svc.cancel_linked_request(_FakeDb(ea), "EAR-1", actor=_ACTOR)
    assert out is not None
    assert out["status"] == svc.STATUS_CANCELLED
    assert out["cancelled_via"] == "transfer_cancel"
    assert ea.docs["EAR-1"]["status"] == svc.STATUS_CANCELLED


@pytest.mark.parametrize(
    "start",
    [
        svc.STATUS_SHIPPED,
        svc.STATUS_DELIVERED,
        svc.STATUS_REJECTED,
        svc.STATUS_CANCELLED,
    ],
)
def test_terminal_or_postship_request_is_left_untouched(start):
    # SHIPPED/DELIVERED are post-ship; REJECTED/CANCELLED are terminal. CANCELLED
    # is not a legal transition from any of them -> never force-cancel.
    ea = _FakeEAColl()
    _seed_request(ea, "EAR-2", start)
    out = svc.cancel_linked_request(_FakeDb(ea), "EAR-2", actor=_ACTOR)
    assert out is None
    assert ea.docs["EAR-2"]["status"] == start


def test_missing_request_returns_none_no_raise():
    ea = _FakeEAColl()
    assert svc.cancel_linked_request(_FakeDb(ea), "EAR-nope", actor=_ACTOR) is None


def test_none_db_and_empty_id_are_fail_soft():
    assert svc.cancel_linked_request(None, "EAR-1", actor=_ACTOR) is None
    ea = _FakeEAColl()
    assert svc.cancel_linked_request(_FakeDb(ea), "", actor=_ACTOR) is None


def test_db_error_is_swallowed():
    ea = _FakeEAColl()
    _seed_request(ea, "EAR-3", svc.STATUS_TRANSFER_CREATED)
    ea.raise_on_update = True
    # Must NOT raise -- fail-soft returns None and leaves the doc untouched.
    assert svc.cancel_linked_request(_FakeDb(ea), "EAR-3", actor=_ACTOR) is None
    assert ea.docs["EAR-3"]["status"] == svc.STATUS_TRANSFER_CREATED


# ===========================================================================
# Integration: cancel_transfer wires the back-sync, gated on the link, fail-soft
# ===========================================================================


class _FakeTransfersColl:
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


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["A", "B"],
    "active_store_id": "A",
}


def _seed_transfer(tcoll, tid, ea_request_id=None):
    doc = {
        "id": tid,
        "transfer_number": f"TRF-EA-{tid}",
        "transfer_type": "store_to_store",
        "from_location_id": "B",
        "from_location_name": "Store B",
        "to_location_id": "A",
        "to_location_name": "Store A",
        "items": [{"id": f"{tid}-l1", "transfer_id": tid, "product_id": "P1",
                   "quantity_requested": 1}],
        "status": "pending",  # the EA writer's legacy status (cancellable)
        "status_history": [],
        "created_at": "2026-08-01T10:00:00",
    }
    if ea_request_id:
        doc["endless_aisle_request_id"] = ea_request_id
    tcoll.docs[tid] = doc


@pytest.fixture()
def wired(monkeypatch):
    tcoll = _FakeTransfersColl()
    ea = _FakeEAColl()
    db = _FakeDb(ea)
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: tcoll)
    monkeypatch.setattr(transfers, "_get_db", lambda: db)
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: None)
    transfers.STOCK_TRANSFERS.clear()
    return tcoll, ea


def _cancel(tid):
    return asyncio.run(
        transfers.cancel_transfer(tid, reason="test", current_user=_ADMIN)
    )


def test_cancelling_ea_linked_transfer_cancels_the_request(wired):
    tcoll, ea = wired
    _seed_transfer(tcoll, "t-ea", ea_request_id="EAR-9")
    _seed_request(ea, "EAR-9", svc.STATUS_TRANSFER_CREATED)

    res = _cancel("t-ea")

    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
    assert ea.docs["EAR-9"]["status"] == svc.STATUS_CANCELLED
    assert ea.docs["EAR-9"]["cancelled_via"] == "transfer_cancel"


def test_cancelling_non_ea_transfer_touches_no_request(wired):
    tcoll, ea = wired
    _seed_transfer(tcoll, "t-plain", ea_request_id=None)
    _seed_request(ea, "EAR-other", svc.STATUS_TRANSFER_CREATED)

    res = _cancel("t-plain")

    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
    # An unrelated request is never touched.
    assert ea.docs["EAR-other"]["status"] == svc.STATUS_TRANSFER_CREATED


def test_backsync_failure_does_not_break_the_cancel(wired, monkeypatch):
    tcoll, ea = wired
    _seed_transfer(tcoll, "t-boom", ea_request_id="EAR-boom")
    _seed_request(ea, "EAR-boom", svc.STATUS_TRANSFER_CREATED)

    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(svc, "cancel_linked_request", _boom)

    # The committed cancel must still return 200 even if the back-sync explodes.
    res = _cancel("t-boom")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
