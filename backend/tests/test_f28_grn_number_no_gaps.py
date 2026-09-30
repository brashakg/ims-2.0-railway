"""Audit F28 (2026-09-29): a refused receipt burned a receipt number.

The GRN number (RCPT/{store}/{FY}/{serial}) is a GST document series and must
run without gaps. _create_grn_impl minted it BEFORE the duplicate-invoice
guard, so a correctly refused duplicate consumed RCPT/.../0002 and the real
receipt came out 0003 (series 0001, 0003, 0004). The same held for the DC
duplicate guard and the tally (untallied lines) refusal.

Contract: every refusal answers before the number is minted.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import GRNCreate, GRNItemCreate, create_grn  # noqa: E402
from tests.test_grn_duplicate_guard import _MemGRNRepo, _attach, _user, _wire  # noqa: E402


def _counting_minter(mp):
    minted = []

    def _mint(store):
        minted.append(store)
        return f"RCPT/{store}/26-27/{len(minted):04d}"

    mp.setattr(v, "generate_grn_number", _mint)
    return minted


def _body(store, tallied=True, **kw):
    return GRNCreate(
        po_id=kw.pop("po_id", "PO1"),
        vendor_invoice_no=kw.pop("invoice_no", "JOT/26-27/0451"),
        items=[
            GRNItemCreate(
                product_id="P1",
                received_qty=10,
                accepted_qty=10,
                rejected_qty=0,
                tallied=tallied,
            )
        ],
        **_attach(store),
        **kw,
    )


def _create(grn):
    return asyncio.run(create_grn(grn, current_user=_user()))


def test_refused_duplicate_invoice_takes_no_receipt_number(monkeypatch):
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    minted = _counting_minter(monkeypatch)
    first = _create(_body(store))
    assert first["grn_number"].endswith("/0001")
    with pytest.raises(HTTPException) as exc:
        _create(_body(store))
    assert exc.value.status_code == 409
    assert len(minted) == 1  # the refusal burned nothing
    nxt = _create(_body(store, invoice_no="JOT/26-27/0452"))
    assert nxt["grn_number"].endswith("/0002")  # no gap


def test_refused_untallied_receipt_takes_no_receipt_number(monkeypatch):
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    minted = _counting_minter(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        _create(_body(store, tallied=False))
    assert exc.value.status_code == 422
    assert minted == []


def test_refused_duplicate_challan_takes_no_receipt_number(monkeypatch):
    repo = _MemGRNRepo(
        [
            {
                "grn_id": "DC-1",
                "grn_subtype": "DELIVERY_CHALLAN",
                "vendor_id": "V1",
                "dc_number": "DC-77",
                "store_id": "BV-TEST-01",
            }
        ]
    )
    store = _wire(monkeypatch, repo)
    minted = _counting_minter(monkeypatch)

    class _Db:
        def get_collection(self, name):
            class _C:
                def find_one(self, flt, proj=None):
                    return repo.find_one(flt)

            return _C()

    monkeypatch.setattr(v, "_get_db", lambda: _Db())
    dc = GRNCreate(
        po_id=None,
        vendor_id="V1",
        vendor_invoice_no="",
        grn_subtype="DELIVERY_CHALLAN",
        dc_number="DC-77",
        dc_date="2026-09-17",
        items=[GRNItemCreate(product_id="P1", received_qty=2, accepted_qty=2, rejected_qty=0)],
    )
    with pytest.raises(HTTPException) as exc:
        _create(dc)
    assert exc.value.status_code == 409
    assert minted == []


# ---------------------------------------------------------------------------
# Verifier round 2: two identical receipts at the same instant. Both pass the
# duplicate pre-check before either inserts (4 uvicorn workers); the unique
# index (uniq_std_vendor_invoice_store) refuses the loser's INSERT -- and the
# loser must not have taken a number by then.
# ---------------------------------------------------------------------------

import itertools  # noqa: E402
import threading  # noqa: E402

_LIVE = {"PENDING", "PARTIALLY_ACCEPTED", "ACCEPTED"}


class _IndexedGRNRepo(_MemGRNRepo):
    """_MemGRNRepo plus the uniq_std_vendor_invoice_store partial unique index
    and grn_number's unique index, enforced atomically the way Mongo does. A
    refused insert returns None, exactly as BaseRepository.create swallows a
    DuplicateKeyError."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def create(self, doc):
        def key(d):
            return (d.get("vendor_id"), d.get("vendor_invoice_no_norm"), d.get("store_id"))

        with self._lock:
            for d in self.docs:
                if d.get("grn_number") == doc.get("grn_number"):
                    return None
                if (
                    doc.get("vendor_invoice_no_norm")
                    and d.get("status") in _LIVE
                    and key(d) == key(doc)
                ):
                    return None
            self.docs.append(dict(doc))
            return doc


def test_two_identical_receipts_at_once_leave_no_gap(monkeypatch):
    repo = _IndexedGRNRepo()
    store = _wire(monkeypatch, repo)
    seq = itertools.count(1)
    minted = []

    def _mint(store_id):
        n = f"RCPT/{store_id}/26-27/{next(seq):04d}"
        minted.append(n)
        return n

    monkeypatch.setattr(v, "generate_grn_number", _mint)

    # Hold both requests together just after the duplicate pre-check, so both
    # pass it before either inserts (the re-probe after a refused insert runs
    # with exclude_grn_id and is not held).
    real_check = v._find_duplicate_standard_grn
    barrier = threading.Barrier(2, timeout=10)

    def _held(grn_repo, po_id, vendor_id, invoice_no, exclude_grn_id=None):
        dup = real_check(grn_repo, po_id, vendor_id, invoice_no, exclude_grn_id=exclude_grn_id)
        if exclude_grn_id is None:
            barrier.wait()
        return dup

    monkeypatch.setattr(v, "_find_duplicate_standard_grn", _held)

    results, errors = [], []

    def _run():
        try:
            results.append(_create(_body(store)))
        except HTTPException as exc:
            errors.append(exc.status_code)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 1 and errors == [409]
    assert minted == [results[0]["grn_number"]]  # the loser took no number
    assert results[0]["grn_number"].endswith("/0001")
    (saved,) = repo.docs
    assert saved["grn_number"] == results[0]["grn_number"]  # stored as issued

    monkeypatch.setattr(v, "_find_duplicate_standard_grn", real_check)  # race over
    nxt = _create(_body(store, invoice_no="JOT/26-27/0466"))
    assert nxt["grn_number"].endswith("/0002")  # 0001, 0002: no gap


# ---------------------------------------------------------------------------
# Verifier round 3: a worker that dies between the insert and the number.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

import mongomock  # noqa: E402

from api.routers.vendors.grn_create import _number_stranded_receipts  # noqa: E402
from database.repositories.vendor_repository import GRNRepository  # noqa: E402


def _stranded(db, grn_id, minutes_ago, **extra):
    db.grns.insert_one({
        "grn_id": grn_id, "grn_number": f"PENDING/{grn_id}", "store_id": "BV-TEST-01",
        "status": "PENDING", "grn_subtype": "STANDARD",
        "created_at": datetime.now() - timedelta(minutes=minutes_ago), **extra,
    })


def test_a_receipt_stranded_on_its_placeholder_is_numbered_by_the_next_create(monkeypatch):
    """The worker was killed between the insert and the number: the row kept
    PENDING/<grn_id> for good. The next receipt created numbers it first (it
    came first), then takes its own. A row inserted a moment ago belongs to
    a request still in flight and is left to it."""
    db = mongomock.MongoClient().db
    store = _wire(monkeypatch, GRNRepository(db.grns))
    minted = _counting_minter(monkeypatch)
    _stranded(db, "G-DEAD", minutes_ago=5)
    _stranded(db, "G-LIVE", minutes_ago=0)

    res = _create(_body(store))

    dead = db.grns.find_one({"grn_id": "G-DEAD"})
    assert dead["grn_number"] == "RCPT/BV-TEST-01/26-27/0001"
    assert "numbering_claim" not in dead and "numbering_claimed_at" not in dead
    assert res["grn_number"] == "RCPT/BV-TEST-01/26-27/0002"
    assert db.grns.find_one({"grn_id": "G-LIVE"})["grn_number"] == "PENDING/G-LIVE"
    assert len(minted) == 2  # no serial spent


def test_a_stranded_receipt_is_numbered_once_even_by_two_creates(monkeypatch):
    """Another create holds a fresh claim on the row: this one leaves it (two
    numberings would spend a serial). A claim as old as the row is a dead
    worker's, and is taken over."""
    db = mongomock.MongoClient().db
    minted = _counting_minter(monkeypatch)
    now = datetime.now()
    _stranded(db, "G-HELD", 5, numbering_claim="other", numbering_claimed_at=now)
    _stranded(db, "G-ORPHAN", 5, numbering_claim="dead", numbering_claimed_at=now - timedelta(minutes=5))

    _number_stranded_receipts(GRNRepository(db.grns))
    _number_stranded_receipts(GRNRepository(db.grns))  # a second pass finds nothing left

    assert db.grns.find_one({"grn_id": "G-HELD"})["grn_number"] == "PENDING/G-HELD"
    assert db.grns.find_one({"grn_id": "G-ORPHAN"})["grn_number"] == "RCPT/BV-TEST-01/26-27/0001"
    assert len(minted) == 1
