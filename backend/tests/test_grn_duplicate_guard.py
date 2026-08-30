"""P0-1 (launch gate) -- STANDARD goods receipts get the duplicate guard the
DELIVERY_CHALLAN subtype always had.

A vendor invoice number identifies ONE physical delivery and ONE bill. Before
this guard, POST /vendors/grn twice with an identical payload created two
GRNs: 20 units minted for 10 delivered, and punctuation-variant invoice
numbers then booked the payable twice (the gate's driven repro: Rs 63,000
x2). The DC subtype had exactly the guard needed; the STANDARD branch had
drifted without it -- the repo's dominant defect class, admitted by the
code's own comments.

Contract under test (all through the REAL create_grn / express_receive_grn
handlers, fake repos only -- the test_grn_attachment_gate pattern):

  * a second non-VOID STANDARD GRN with the same (po_id OR vendor_id,
    vendor_invoice_no) is a 409 GRN_DUPLICATE, nothing saved;
  * the comparison is case/punctuation-FOLDED via the ONE normaliser
    (purchase_invoice_engine.normalize_invoice_no) the payable dedupe uses;
  * the 409 tells staff the receipt EXISTS and where to finish it -- never
    "try again" (a retry is exactly what double-minted stock before);
  * express inherits the guard through the shared impl: the
    EXPRESS_PARTIAL-then-human-retry sequence is refused at create time;
  * a legitimately split delivery (same PO, DIFFERENT invoice numbers)
    still works; a VOIDed receipt frees its number (the sanctioned
    void-then-recreate correction path); DC rows never collide with it;
  * the racy check-then-insert has an index backstop: create() returning
    None (swallowed DuplicateKeyError) re-probes and maps to the SAME 409.
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
from api.routers.vendors import (  # noqa: E402
    ExpressGRNCreate,
    ExpressGRNItemCreate,
    GRNCreate,
    GRNItemCreate,
    create_grn,
    express_receive_grn,
)
from api.services.file_store import InMemoryFileStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes (test_grn_attachment_gate pattern, plus a doc list so a SECOND create
# can see the first -- the whole point of a duplicate guard)
# ---------------------------------------------------------------------------


class _MemGRNRepo:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]
        self.fail_create = False

    def create(self, doc):
        if self.fail_create:
            return None  # the repo layer swallows DuplicateKeyError -> None
        self.docs.append(dict(doc))
        return doc

    def find_many(self, flt=None, sort=None, skip=0, limit=100):
        out = [
            d
            for d in self.docs
            if all(d.get(k) == val for k, val in (flt or {}).items())
        ]
        return out[: limit or len(out)]

    def find_one(self, query=None):
        rows = self.find_many(query, limit=1)
        return rows[0] if rows else None

    def find_by_id(self, grn_id):
        return self.find_one({"grn_id": grn_id})


class _FakePORepo:
    def find_by_id(self, po_id):
        return {
            "po_id": po_id,
            "po_number": "PO-TEST-1",
            "vendor_id": "V1",
            "vendor_name": "Acme Optics",
            "status": "SENT",
            "items": [{"product_id": "P1", "quantity": 10}],
        }


def _user(store="BV-TEST-01", roles=("ADMIN",)):
    return {"user_id": "u1", "roles": list(roles), "active_store_id": store}


def _wire(mp, grn_repo):
    mp.setattr(v, "get_grn_repository", lambda: grn_repo)
    mp.setattr(v, "get_purchase_order_repository", lambda: _FakePORepo())
    mp.setattr(v, "generate_grn_number", lambda store: "GRN-TEST-NEXT")
    store = InMemoryFileStore()
    mp.setattr(v, "get_file_store", lambda: store)
    return store


def _attach(store):
    fid = store.put(
        content=b"%PDF-1.4 real",
        filename="invoice.pdf",
        mime_type="application/pdf",
        metadata={"kind": "grn_document", "uploaded_by": "u1"},
    )
    return {
        "attachment_file_id": fid,
        "attachment_filename": "invoice.pdf",
        "attachment_mime": "application/pdf",
    }


def _grn_body(store, po_id="PO1", invoice_no="GO-INV-9007"):
    return GRNCreate(
        po_id=po_id,
        vendor_invoice_no=invoice_no,
        items=[
            GRNItemCreate(
                product_id="P1",
                received_qty=10,
                accepted_qty=10,
                rejected_qty=0,
                tallied=True,
            )
        ],
        **_attach(store),
    )


def _create(grn):
    return asyncio.run(create_grn(grn, current_user=_user()))


def _existing(status="PENDING", invoice_no="GO-INV-9007", subtype="STANDARD"):
    """A GRN row as a previous create left it (the guard's prey)."""
    return {
        "grn_id": "G-FIRST",
        "grn_number": "GRN-TEST-1",
        "po_id": "PO1",
        "vendor_id": "V1",
        "store_id": "BV-TEST-01",
        "status": status,
        "grn_subtype": subtype,
        "vendor_invoice_no": invoice_no,
    }


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_second_identical_standard_grn_is_409_and_nothing_saved(monkeypatch):
    """The gate's exact repro: POST /vendors/grn twice, identical payload.
    First 201s; the second must 409 GRN_DUPLICATE and save NOTHING (before
    the guard: two GRNs, 20 units minted for 10 delivered)."""
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    res = _create(_grn_body(store))
    assert res["grn_number"]
    assert len(repo.docs) == 1
    with pytest.raises(HTTPException) as exc:
        _create(_grn_body(store))
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict) and detail["code"] == "GRN_DUPLICATE"
    assert len(repo.docs) == 1  # nothing new persisted


def test_punctuation_and_case_variant_invoice_number_still_409(monkeypatch):
    """'GO-INV-9007' vs 'go inv/9007' is the SAME piece of paper -- the exact
    variance that booked the payable twice in the gate's money repro."""
    repo = _MemGRNRepo([_existing(invoice_no="GO-INV-9007")])
    store = _wire(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        _create(_grn_body(store, invoice_no="go inv/9007"))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "GRN_DUPLICATE"
    assert len(repo.docs) == 1


def test_409_says_receipt_exists_and_where_to_finish_it_never_try_again(
    monkeypatch,
):
    """The person reading this 409 just watched a submit apparently fail;
    'try again' is exactly the instruction that used to double-mint stock.
    A PENDING twin's message must name the receipt and point at the pending
    receipts panel."""
    repo = _MemGRNRepo([_existing(status="PENDING")])
    store = _wire(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        _create(_grn_body(store))
    msg = exc.value.detail["message"].lower()
    assert "already exists" in msg
    assert "grn-test-1" in msg  # names the existing receipt
    assert "pending receipts" in msg  # where to finish it
    assert "try again" not in msg


def test_accepted_twin_message_warns_goods_already_on_shelf(monkeypatch):
    repo = _MemGRNRepo([_existing(status="ACCEPTED")])
    store = _wire(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        _create(_grn_body(store))
    msg = exc.value.detail["message"].lower()
    assert "already on the shelf" in msg
    assert "try again" not in msg


def test_express_partial_then_human_retry_is_refused_at_create(monkeypatch):
    """THE P0-1 trigger sequence: express receive left a PARTIALLY_ACCEPTED
    receipt (a line held for cataloguing), the user saw a failure face and
    re-submitted express. The retry must die at create time with the
    duplicate 409 -- through the REAL /grn/express handler, proving express
    inherits the guard via the shared impl -- and the message must route the
    user to the pending receipts panel, not invite another attempt."""
    repo = _MemGRNRepo([_existing(status="PARTIALLY_ACCEPTED")])
    store = _wire(monkeypatch, repo)
    body = ExpressGRNCreate(
        po_id="PO1",
        vendor_invoice_no="GO-INV-9007",
        items=[
            ExpressGRNItemCreate(
                product_id="P1", received_qty=10, accepted_qty=10, rejected_qty=0
            )
        ],
        **_attach(store),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(express_receive_grn(body, current_user=_user()))
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == "GRN_DUPLICATE"
    assert detail["grn_id"] == "G-FIRST"
    msg = detail["message"].lower()
    assert "pending receipts" in msg
    assert "try again" not in msg
    assert len(repo.docs) == 1  # no second receipt row


def test_same_vendor_invoice_on_a_different_po_still_409(monkeypatch):
    """One bill = one receipt, even if the clerk picked the wrong PO the
    second time: the guard matches per vendor, not just per PO."""
    repo = _MemGRNRepo([_existing()])
    store = _wire(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        _create(_grn_body(store, po_id="PO2"))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "GRN_DUPLICATE"


def test_split_delivery_different_invoice_numbers_both_pass(monkeypatch):
    """Two real part-shipments of one PO arrive with DIFFERENT invoice
    numbers -- the everyday legitimate case the guard must not break."""
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    _create(_grn_body(store, invoice_no="GO-INV-9007"))
    _create(_grn_body(store, invoice_no="GO-INV-9008"))
    assert len(repo.docs) == 2


def test_voided_receipt_frees_its_invoice_number(monkeypatch):
    """Void-then-recreate is the sanctioned correction path -- a VOID twin
    must not block the corrected receipt."""
    repo = _MemGRNRepo([_existing(status="VOID")])
    store = _wire(monkeypatch, repo)
    res = _create(_grn_body(store))
    assert res["grn_number"]
    assert len(repo.docs) == 2


def test_dc_rows_never_collide_with_the_standard_guard(monkeypatch):
    """A Delivery Challan carrying the same vendor_invoice_no (attached later
    at reconciliation) is a different document class with its own guard."""
    repo = _MemGRNRepo(
        [_existing(subtype="DELIVERY_CHALLAN", invoice_no="GO-INV-9007")]
    )
    store = _wire(monkeypatch, repo)
    res = _create(_grn_body(store))
    assert res["grn_number"]
    assert len(repo.docs) == 2


def test_created_doc_carries_the_folded_norm_for_the_unique_index(monkeypatch):
    """The uniq_std_vendor_invoice_store partial unique index (schemas.py)
    keys on vendor_invoice_no_norm -- the create path must stamp it, folded,
    or the atomic race backstop indexes nothing."""
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    _create(_grn_body(store, invoice_no="go inv/9007"))
    assert repo.docs[0]["vendor_invoice_no_norm"] == "GOINV9007"


def test_race_backstop_maps_swallowed_duplicate_key_to_the_same_409(
    monkeypatch,
):
    """Two truly concurrent creates can both pass the app-level probe; the
    partial unique index then makes the loser's insert fail, which the repo
    layer swallows into create() -> None. That None must re-probe and become
    the SAME 409 -- never a false 'GRN created', never a blind 500 when a
    rival row holds the number."""
    repo = _MemGRNRepo([_existing()])
    store = _wire(monkeypatch, repo)
    body = _grn_body(store)
    # Simulate the race: the app-level probe misses (rival not yet visible),
    # but the insert collides on the unique index.
    real_find = repo.find_many
    calls = {"n": 0}

    def racy_find_many(flt=None, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:  # the guard's two pre-insert probes see nothing
            return []
        return real_find(flt, **kw)

    repo.find_many = racy_find_many
    repo.fail_create = True
    with pytest.raises(HTTPException) as exc:
        _create(body)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "GRN_DUPLICATE"


def test_unique_index_spec_matches_the_dc_convention():
    """schemas.py must declare uniq_std_vendor_invoice_store exactly as the
    STANDARD twin of uniq_dc_vendor_number_store: unique per (vendor, folded
    invoice number, store), partial over live STANDARD rows only -- a VOIDed
    receipt leaves the index so the sanctioned void-then-recreate correction
    path still works, and DC rows (norm stamped None) never enter it."""
    from database.schemas import get_all_indexes

    specs = [
        i
        for i in get_all_indexes()["grns"]
        if i.get("name") == "uniq_std_vendor_invoice_store"
    ]
    assert len(specs) == 1
    spec = specs[0]
    assert spec["keys"] == [
        ("vendor_id", 1),
        ("vendor_invoice_no_norm", 1),
        ("store_id", 1),
    ]
    assert spec["unique"] is True
    pfe = spec["partialFilterExpression"]
    assert pfe["grn_subtype"] == "STANDARD"
    assert pfe["vendor_invoice_no_norm"] == {"$type": "string"}
    assert set(pfe["status"]["$in"]) == {
        "PENDING",
        "PARTIALLY_ACCEPTED",
        "ACCEPTED",
    }  # VOID stays OUT of the index


def test_create_failure_with_no_rival_is_a_loud_500(monkeypatch):
    """A save failure that is NOT a duplicate must stay a loud 500 -- never a
    silent success and never a lying 409."""
    repo = _MemGRNRepo()
    store = _wire(monkeypatch, repo)
    repo.fail_create = True
    with pytest.raises(HTTPException) as exc:
        _create(_grn_body(store))
    assert exc.value.status_code == 500
