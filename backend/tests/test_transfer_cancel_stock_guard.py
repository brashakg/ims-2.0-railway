"""
IMS 2.0 - Transfer CANCEL crash-window stock-integrity guard
============================================================
ship_transfer moves REAL stock_units OUT of the source store (per-unit
`claim_for_transfer`: AVAILABLE -> TRANSFERRED, each stamped with this
transfer's id) and only THEN sets `stock_shipped` / `shipped_stock_ids` in
memory and persists the doc via _save_transfer.

CRASH WINDOW: if the process dies between the per-unit claim writes (already
committed to stock_units) and _save_transfer, the persisted transfer doc is
left at a PRE-SHIP status (packed / approved) with NO stock_shipped flag and
NO shipped_stock_ids -- while the units are already TRANSFERRED in the
stock_units collection. The PR #959 allowlist reads the doc STATUS, so it would
WRONGLY accept a cancel there, doing NO stock reversal and skipping the GST
deemed-supply mirror invoice -> units stranded TRANSFERRED forever.

Because stock_shipped / shipped_stock_ids are exactly the fields lost in the
crash, the guard cannot rely on them. It queries the stock_units GROUND TRUTH
instead: any unit with this transfer's id in TRANSFERRED status => refuse the
cancel with 400 `transfer_stock_already_moved`. Fail-soft: no stock repo (or a
query error) never blocks a normal cancel.

Same isolated fake-collection harness as test_transfer_cancel_allowlist.py
(no Mongo needed), extended with a minimal fake StockRepository.
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


class _FakeStockRepo:
    """Minimal StockRepository stand-in for the cancel guard's ground-truth read.

    Seeded with a flat list of unit dicts; find_many filters on exact key match
    (enough for the guard's {transfer_id, status} query). `saw_write` proves the
    guard never mutates stock (a blocked cancel must touch nothing).
    """

    def __init__(self, units=None, accept_limit=True):
        self.units = list(units or [])
        self.accept_limit = accept_limit
        self.saw_write = False

    def find_many(self, flt, limit=None):
        if not self.accept_limit and limit is not None:
            # Emulate a repo/mock whose signature has no limit= kwarg.
            raise TypeError("find_many() got an unexpected keyword argument 'limit'")
        matches = [
            u for u in self.units if all(u.get(k) == v for k, v in (flt or {}).items())
        ]
        if limit:
            matches = matches[:limit]
        return [dict(u) for u in matches]

    # Any of these being called during a blocked cancel would be a bug.
    def claim_for_transfer(self, *a, **k):  # pragma: no cover - guard is read-only
        self.saw_write = True
        return False

    def update(self, *a, **k):  # pragma: no cover - guard is read-only
        self.saw_write = True
        return False


@pytest.fixture()
def coll(monkeypatch):
    """stock_transfers fake + NO stock repo by default (fail-soft baseline)."""
    fake = _FakeColl()
    monkeypatch.setattr(transfers, "_transfers_coll", lambda: fake)
    monkeypatch.setattr(transfers, "_get_db", lambda: None)
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: None)
    transfers.STOCK_TRANSFERS.clear()
    return fake


def _wire_stock_repo(monkeypatch, repo):
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: repo)


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["A", "B"],
    "active_store_id": "A",
}


def _seed(coll, tid, status, stock_shipped=None):
    doc = {
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
    if stock_shipped is not None:
        doc["stock_shipped"] = stock_shipped
    coll.docs[tid] = doc


def _units_transferred(tid, product_id="P1", n=2):
    return [
        {
            "stock_id": f"{tid}-U{i}",
            "product_id": product_id,
            "store_id": "A",
            "status": transfers.STOCK_STATUS_TRANSFERRED,
            "transfer_id": tid,
        }
        for i in range(n)
    ]


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
# CRASH ORPHAN: doc says pre-ship (allowlist would accept), but stock_units
# prove the units already moved -> cancel MUST be refused, doc untouched.
# ===========================================================================


def test_crash_orphan_packed_with_transferred_units_is_refused(coll, monkeypatch):
    _seed(coll, "t-orphan", "packed")  # NO stock_shipped flag -- lost in the crash
    repo = _FakeStockRepo(units=_units_transferred("t-orphan", n=2))
    _wire_stock_repo(monkeypatch, repo)

    err = _expect_400("t-orphan")
    assert isinstance(err.detail, dict)
    assert err.detail["code"] == "transfer_stock_already_moved"

    # The doc was NOT flipped and NOTHING was written to it.
    doc = coll.docs["t-orphan"]
    assert doc["status"] == "packed"
    assert "cancelled_at" not in doc
    assert "cancellation_reason" not in doc
    assert doc["status_history"] == []
    # The guard is read-only: it never touched stock.
    assert repo.saw_write is False


def test_crash_orphan_approved_with_transferred_units_is_refused(coll, monkeypatch):
    _seed(coll, "t-orphan2", "approved")
    repo = _FakeStockRepo(units=_units_transferred("t-orphan2", n=1))
    _wire_stock_repo(monkeypatch, repo)

    _expect_400("t-orphan2")
    assert coll.docs["t-orphan2"]["status"] == "approved"


def test_crash_orphan_guard_works_without_limit_kwarg(coll, monkeypatch):
    # A repo/mock whose find_many has no limit= must still be handled (TypeError
    # fallback path in the guard).
    _seed(coll, "t-orphan3", "packed")
    repo = _FakeStockRepo(units=_units_transferred("t-orphan3", n=2), accept_limit=False)
    _wire_stock_repo(monkeypatch, repo)

    err = _expect_400("t-orphan3")
    assert err.detail["code"] == "transfer_stock_already_moved"
    assert coll.docs["t-orphan3"]["status"] == "packed"


# ===========================================================================
# CLEAN PRE-SHIP: no TRANSFERRED units for this transfer -> allowlist wins,
# cancel succeeds (the guard must not over-block).
# ===========================================================================


def test_clean_preship_with_no_transferred_units_succeeds(coll, monkeypatch):
    _seed(coll, "t-clean", "packed")
    repo = _FakeStockRepo(units=[])  # nothing moved
    _wire_stock_repo(monkeypatch, repo)

    res = _cancel("t-clean")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
    assert coll.docs["t-clean"]["status"] == "cancelled"
    assert coll.docs["t-clean"]["cancellation_reason"] == "test"


def test_guard_ignores_units_from_a_different_transfer(coll, monkeypatch):
    # Units TRANSFERRED under ANOTHER transfer id must not block this one.
    _seed(coll, "t-mine", "packed")
    repo = _FakeStockRepo(units=_units_transferred("t-someone-else", n=3))
    _wire_stock_repo(monkeypatch, repo)

    res = _cancel("t-mine")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


def test_guard_ignores_already_received_units_of_this_transfer(coll, monkeypatch):
    # Units re-homed to the destination are AVAILABLE (not TRANSFERRED); a
    # transfer whose units all landed is not a crash orphan. (It would normally
    # be at received/completed and blocked by the allowlist anyway, but prove the
    # ground-truth query keys on TRANSFERRED specifically.)
    _seed(coll, "t-landed", "packed")
    landed = [
        {
            "stock_id": "t-landed-U0",
            "product_id": "P1",
            "store_id": "B",
            "status": transfers.STOCK_STATUS_AVAILABLE,
            "transfer_id": "t-landed",
        }
    ]
    repo = _FakeStockRepo(units=landed)
    _wire_stock_repo(monkeypatch, repo)

    res = _cancel("t-landed")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


# ===========================================================================
# FAIL-SOFT: no stock backend visible -> the guard must NOT block a normal
# cancel (preserves the pre-existing no-stock-backend behavior).
# ===========================================================================


def test_fail_soft_no_stock_repo_cancel_succeeds(coll):
    # `coll` fixture already stubs get_stock_repository -> None.
    _seed(coll, "t-nofs", "packed")
    res = _cancel("t-nofs")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED
    assert coll.docs["t-nofs"]["status"] == "cancelled"


def test_fail_soft_repo_query_error_cancel_succeeds(coll, monkeypatch):
    class _BoomRepo:
        def find_many(self, *a, **k):
            raise RuntimeError("stock backend unavailable")

    _seed(coll, "t-boom", "packed")
    _wire_stock_repo(monkeypatch, _BoomRepo())
    res = _cancel("t-boom")
    assert res["transfer"]["status"] == transfers.TransferStatus.CANCELLED


# ===========================================================================
# The guard is layered ON TOP of the allowlist: a post-ship status is still
# rejected by the allowlist first (guard is defense-in-depth, not a replacement).
# ===========================================================================


def test_post_ship_status_still_rejected_by_allowlist_even_with_units(coll, monkeypatch):
    _seed(coll, "t-recv", "received")
    repo = _FakeStockRepo(units=_units_transferred("t-recv", n=2))
    _wire_stock_repo(monkeypatch, repo)

    err = _expect_400("t-recv")
    # Allowlist message (string), not the guard's dict -- allowlist fires first.
    assert isinstance(err.detail, str)
    assert coll.docs["t-recv"]["status"] == "received"


def test_helper_returns_true_only_for_transferred_units(coll, monkeypatch):
    # Direct unit test of the ground-truth helper.
    repo = _FakeStockRepo(units=_units_transferred("t-h", n=1))
    _wire_stock_repo(monkeypatch, repo)
    assert transfers._transfer_has_moved_stock({"id": "t-h"}) is True
    assert transfers._transfer_has_moved_stock({"id": "t-other"}) is False
    assert transfers._transfer_has_moved_stock({}) is False
