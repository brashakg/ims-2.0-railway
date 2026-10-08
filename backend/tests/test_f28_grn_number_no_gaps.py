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

from api.routers.vendors.grn import _number_stranded_receipts, list_grns  # noqa: E402
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


# ---------------------------------------------------------------------------
# Verifier round 4: a placeholder is never handed out, accepted or overwritten
# ---------------------------------------------------------------------------

from api.routers.vendors.grn_accept import accept_grn  # noqa: E402


def test_a_retry_inside_the_minute_never_names_the_placeholder(monkeypatch):
    """The worker died between the insert and the number and the user retried
    at once: the row is too young for the healer, so the duplicate guard
    finds it. It used to answer 'Goods receipt PENDING/<uuid> already exists
    ... finish (accept)'; it must not hand out a placeholder as a number."""
    db = mongomock.MongoClient().db
    store = _wire(monkeypatch, GRNRepository(db.grns))
    _counting_minter(monkeypatch)
    _stranded(db, "G-FRESH", 0, po_id="PO1", vendor_id="V1", vendor_invoice_no="JOT/26-27/0451")

    with pytest.raises(HTTPException) as exc:
        _create(_body(store))
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == "GRN_DUPLICATE" and detail["grn_number"] is None
    assert "PENDING/" not in detail["message"]


def test_accept_refuses_a_receipt_on_its_placeholder_and_numbers_a_stranded_one(monkeypatch):
    """Accepting a receipt still on PENDING/<uuid> stamped that placeholder on
    every minted unit, the audit row and the bill draft for good. A fresh one
    is refused (its own request is still numbering it); a stranded one (older
    than the minute) is numbered first, so the accept carries the real number."""
    db = mongomock.MongoClient().db
    _wire(monkeypatch, GRNRepository(db.grns))
    _counting_minter(monkeypatch)
    seen = []
    monkeypatch.setattr(
        v, "_accept_grn_claimed", lambda grn_id, grn, *a, **k: seen.append(grn["grn_number"]) or {}
    )
    _stranded(db, "G-FRESH", 0)
    _stranded(db, "G-DEAD", 5)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(accept_grn("G-FRESH", current_user=_user()))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "GRN_NUMBER_PENDING"
    assert seen == []  # nothing minted

    asyncio.run(accept_grn("G-DEAD", current_user=_user()))
    assert seen == ["RCPT/BV-TEST-01/26-27/0001"]


def test_the_healer_never_overwrites_the_number_its_own_request_wrote(monkeypatch):
    """A live create stalled past the minute: a healer claims the row, and the
    live request writes its number X before the healer's final write. The
    healer used to overwrite X with Y, so the live response (and any task or
    message built from it) said X while the row said Y."""
    db = mongomock.MongoClient().db
    _stranded(db, "G-SLOW", 5)

    def _mint(store):
        # The live request lands its own number between claim and write.
        db.grns.update_one({"grn_id": "G-SLOW"}, {"$set": {"grn_number": "RCPT/LIVE/0007"}})
        return "RCPT/HEALER/0008"

    monkeypatch.setattr(v, "generate_grn_number", _mint)
    _number_stranded_receipts(GRNRepository(db.grns))
    assert db.grns.find_one({"grn_id": "G-SLOW"})["grn_number"] == "RCPT/LIVE/0007"


# ---------------------------------------------------------------------------
# Verifier round 6: the pending receipts panel reads only the list
# ---------------------------------------------------------------------------


def _list(status="PENDING"):
    return asyncio.run(
        list_grns(
            store_id=None, status=status, po_id=None, grn_subtype=None,
            dc_matched=None, vendor_id=None, date_from=None, date_to=None,
            skip=0, limit=50, current_user=_user(),
        )
    )


def test_the_pending_panel_never_names_a_placeholder(monkeypatch):
    """A worker died between the insert and the number and nothing else was
    created or accepted since (one quiet shop). The panel's only source is
    the list: it printed 'PENDING/G-DEAD' as the receipt number, the void
    confirm read 'Void PENDING/G-DEAD?' and the accept toast 'GRN
    PENDING/G-DEAD accepted'. The list numbers a stranded row itself; a row
    its own request is still numbering is left out until it has a number."""
    db = mongomock.MongoClient().db
    _wire(monkeypatch, GRNRepository(db.grns))
    _counting_minter(monkeypatch)
    _stranded(db, "G-DEAD", 30)
    _stranded(db, "G-FRESH", 0)

    rows = _list()["grns"]

    assert [(r["grn_id"], r["grn_number"]) for r in rows] == [
        ("G-DEAD", "RCPT/BV-TEST-01/26-27/0001")
    ]
    assert db.grns.find_one({"grn_id": "G-DEAD"})["grn_number"] == "RCPT/BV-TEST-01/26-27/0001"


# ---------------------------------------------------------------------------
# Verifier round 8: the live create numbers its row through the same claim
# ---------------------------------------------------------------------------


def _stalled_repo(db, stall):
    """GRNRepository whose create() is followed by `stall(db, row_id)`: the
    request freezes right after its insert while the rest of the shop runs."""

    class _Stalled(GRNRepository):
        def create(self, doc, **kw):
            created = super().create(doc, **kw)
            stall(db, doc["grn_id"])
            return created

    return _Stalled(db.grns)


def _a_minute_passes(db, grn_id, **extra):
    old = datetime.now() - timedelta(minutes=5)
    db.grns.update_one({"grn_id": grn_id}, {"$set": {"created_at": old, **extra}})


def test_a_create_stalled_past_the_minute_keeps_the_number_the_healer_gave_it(monkeypatch):
    """The request stalled over a minute between its insert and its number.
    Another create's healer numbered the row 0001 (and an accept in that
    window stamps 0001 on the units). The stalled request then minted 0002
    and wrote it over 0001: 0001 vanished from the GST series and the
    receipt's number no longer matched its stock."""
    db = mongomock.MongoClient().db

    def stall(db, grn_id):
        _a_minute_passes(db, grn_id)
        _number_stranded_receipts(GRNRepository(db.grns))  # the next create
        assert db.grns.find_one({"grn_id": grn_id})["grn_number"].endswith("/0001")

    store = _wire(monkeypatch, _stalled_repo(db, stall))
    minted = _counting_minter(monkeypatch)

    res = _create(_body(store))

    (row,) = list(db.grns.find())
    assert row["grn_number"] == "RCPT/BV-TEST-01/26-27/0001"  # never overwritten
    assert res["grn_number"] == row["grn_number"]
    assert len(minted) == 1  # no serial spent


def test_a_create_whose_claim_is_taken_over_never_overwrites_the_number(monkeypatch):
    """The request claimed its row, then stalled over a minute before its
    write: a healer took the dead-looking claim over and wrote its number.
    The stalled write must land nowhere, and the response names the row's
    number (the one serial it minted is spent: the ponytail in grn.py)."""
    db = mongomock.MongoClient().db
    store = _wire(monkeypatch, GRNRepository(db.grns))
    seq = itertools.count(1)
    calls = []

    def _mint(store_id):
        calls.append(store_id)
        if len(calls) == 1:  # the live request, stalled after its claim
            (row,) = list(db.grns.find())
            _a_minute_passes(db, row["grn_id"], numbering_claimed_at=datetime.now() - timedelta(minutes=5))
            _number_stranded_receipts(GRNRepository(db.grns))
        return f"RCPT/{store_id}/26-27/{next(seq):04d}"

    monkeypatch.setattr(v, "generate_grn_number", _mint)

    res = _create(_body(store))

    (row,) = list(db.grns.find())
    assert row["grn_number"] == "RCPT/BV-TEST-01/26-27/0001"  # the healer's
    assert res["grn_number"] == row["grn_number"]


# ---------------------------------------------------------------------------
# Round 11: void and GET /grn/{grn_id} read one receipt
# ---------------------------------------------------------------------------


def test_void_and_the_receipt_read_never_carry_the_placeholder(monkeypatch):
    """A worker died between the insert and the number. Voiding that receipt
    wrote 'PENDING/G-VOID' into the immutable void audit and the response,
    and GET /grn/{id} returned it as the receipt number. Both number a
    stranded receipt first; one its own request is still numbering is
    refused by the void (nothing written) and shows no number on the read."""
    from database.repositories.product_repository import StockRepository

    db = mongomock.MongoClient().db
    _wire(monkeypatch, GRNRepository(db.grns))
    _counting_minter(monkeypatch)
    audits = []

    class _Audit:
        def create(self, doc):
            audits.append(doc)
            return doc

    monkeypatch.setattr(v, "get_audit_repository", lambda: _Audit())
    monkeypatch.setattr(v, "get_stock_repository", lambda: StockRepository(db.stock_units))
    # A batch of older stranded rows ahead of it: one receipt's read numbers
    # THAT receipt, not whichever 20 the healer's sweep picks up first.
    for i in range(20):
        _stranded(db, f"G-OTHER-{i}", 10, po_id="PO2")
    _stranded(db, "G-VOID", 5, po_id="PO1")
    _stranded(db, "G-READ", 5, po_id="PO1")
    _stranded(db, "G-FRESH", 0, po_id="PO1")

    res = asyncio.run(v.void_grn("G-VOID", current_user=_user()))
    assert res["grn_number"] == "RCPT/BV-TEST-01/26-27/0001"
    assert [a["details"]["grn_number"] for a in audits] == ["RCPT/BV-TEST-01/26-27/0001"]
    assert db.grns.find_one({"grn_id": "G-VOID"})["status"] == "VOID"

    assert asyncio.run(v.get_grn("G-READ", current_user=_user()))["grn_number"] == (
        "RCPT/BV-TEST-01/26-27/0002"
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(v.void_grn("G-FRESH", current_user=_user()))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "GRN_NUMBER_PENDING"
    assert len(audits) == 1 and db.grns.find_one({"grn_id": "G-FRESH"})["status"] == "PENDING"
    assert asyncio.run(v.get_grn("G-FRESH", current_user=_user()))["grn_number"] is None
