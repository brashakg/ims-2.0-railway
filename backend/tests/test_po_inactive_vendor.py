"""L1-F40 -- a switched-off supplier gets no NEW purchase order.

The audit: with the vendor's is_active set to false (the Vendors screen's
switch), a Store Manager still raised a PO to it and got 201; ADMIN too. Every
door that starts or commits a new purchase now refuses it: create, editing a
draft (including switching a draft to that supplier), sending a draft, and the
lens top-up generator. Reading an existing order is untouched, and an active
supplier still orders as before (same-shop manager and HQ).

Handlers are driven directly with in-memory repos (no Mongo), like
test_po_store_boundary / test_po_edit_cancel_send.
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers import cl_po  # noqa: E402
from strict_fakes import matches  # noqa: E402

_VENDORS = {
    "V-ON": {"vendor_id": "V-ON", "trade_name": "Live Optics", "is_active": True},
    "V-OFF": {"vendor_id": "V-OFF", "trade_name": "Closed Optics", "is_active": False},
}


class _VendorRepo:
    def find_by_id(self, vid):
        doc = _VENDORS.get(vid)
        return copy.deepcopy(doc) if doc else None


class _PORepo:
    def __init__(self, po=None):
        self.pos = {po["po_id"]: copy.deepcopy(po)} if po else {}
        self.writes = []

    def find_by_id(self, pid):
        doc = self.pos.get(pid)
        return copy.deepcopy(doc) if doc else None

    def create(self, doc):
        self.writes.append(("create", doc))
        self.pos[doc["po_id"]] = copy.deepcopy(doc)
        return doc

    def update(self, pid, patch):
        self.writes.append(("update", patch))
        self.pos[pid].update(copy.deepcopy(patch))
        return True

    def update_if(self, pid, expected, patch):
        doc = self.pos.get(pid)
        if doc is None or not matches(doc, expected):
            return False
        return self.update(pid, patch)


class _AuditRepo:
    def create(self, doc):
        return doc


def _user(roles=("STORE_MANAGER",), active="S1"):
    return {
        "user_id": "mgr1",
        "username": "mgr1",
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": [active],
    }


def _draft(vendor_id):
    return {
        "po_id": "PO1",
        "po_number": "PO/S1/26-27/0001",
        "vendor_id": vendor_id,
        "vendor_name": _VENDORS[vendor_id]["trade_name"],
        "delivery_store_id": "S1",
        "status": "DRAFT",
        "items": [
            {"product_id": "P1", "product_name": "Frame", "sku": "P1",
             "quantity": 1, "ordered_qty": 1, "unit_price": 100, "tax_rate": 5}
        ],
    }


def _wire(monkeypatch, po=None):
    repo = _PORepo(po)
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: repo)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(v, "get_product_repository", lambda: None)
    monkeypatch.setattr(v, "get_grn_repository", lambda: None)
    monkeypatch.setattr(v, "get_audit_repository", lambda: _AuditRepo())
    monkeypatch.setattr(v, "get_store_repository", lambda: None)
    monkeypatch.setattr(v, "get_stock_repository", lambda: None)
    monkeypatch.setattr(v, "generate_po_number", lambda s: f"PO-{s}")
    return repo


def _create_body(vendor_id):
    return v.POCreate(
        vendor_id=vendor_id,
        delivery_store_id="S1",
        items=[v.POItemCreate(product_id="P1", product_name="Frame", sku="P1",
                              quantity=1, unit_price=100.0)],
    )


def _edit_body(**kw):
    return v.POUpdate(
        items=[v.POItemCreate(product_id="P1", product_name="Frame", sku="P1",
                              quantity=2, unit_price=100.0, gst_rate=5)],
        **kw,
    )


def _refused(exc):
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "VENDOR_INACTIVE"
    assert "Closed Optics" in exc.value.detail["message"]


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("roles", [("STORE_MANAGER",), ("ADMIN",), ("SUPERADMIN",)])
def test_create_po_for_switched_off_vendor_refused(monkeypatch, roles):
    repo = _wire(monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.create_po(_create_body("V-OFF"), _user(roles)))
    _refused(e)
    assert repo.writes == [], "a refused order writes nothing"


@pytest.mark.parametrize("roles", [("STORE_MANAGER",), ("ADMIN",)])
def test_create_po_for_active_vendor_still_works(monkeypatch, roles):
    repo = _wire(monkeypatch)
    out = asyncio.run(v.create_po(_create_body("V-ON"), _user(roles)))
    assert out["po_number"] == "PO-S1"
    assert repo.pos[out["po_id"]]["vendor_name"] == "Live Optics"


def test_create_po_unknown_vendor_still_404(monkeypatch):
    _wire(monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.create_po(_create_body("V-NOPE"), _user()))
    assert e.value.status_code == 404


# --------------------------------------------------------------------------- #
# edit a draft
# --------------------------------------------------------------------------- #


def test_edit_draft_switching_to_switched_off_vendor_refused(monkeypatch):
    repo = _wire(monkeypatch, _draft("V-ON"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.update_po("PO1", _edit_body(vendor_id="V-OFF"), _user()))
    _refused(e)
    assert repo.writes == []


def test_edit_draft_of_switched_off_vendor_refused(monkeypatch):
    repo = _wire(monkeypatch, _draft("V-OFF"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.update_po("PO1", _edit_body(), _user()))
    _refused(e)
    assert repo.writes == []


def test_edit_draft_moving_off_switched_off_vendor_allowed(monkeypatch):
    """The way out of a draft to a closed supplier: pick another one."""
    repo = _wire(monkeypatch, _draft("V-OFF"))
    asyncio.run(v.update_po("PO1", _edit_body(vendor_id="V-ON"), _user()))
    assert repo.pos["PO1"]["vendor_id"] == "V-ON"


def test_edit_draft_active_vendor_still_works(monkeypatch):
    repo = _wire(monkeypatch, _draft("V-ON"))
    asyncio.run(v.update_po("PO1", _edit_body(), _user()))
    assert repo.pos["PO1"]["items"][0]["quantity"] == 2


# --------------------------------------------------------------------------- #
# send a draft
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("roles", [("STORE_MANAGER",), ("ADMIN",)])
def test_send_draft_to_switched_off_vendor_refused(monkeypatch, roles):
    repo = _wire(monkeypatch, _draft("V-OFF"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.send_po("PO1", _user(roles)))
    _refused(e)
    assert repo.writes == []
    assert repo.pos["PO1"]["status"] == "DRAFT"


def test_send_draft_to_active_vendor_still_works(monkeypatch):
    repo = _wire(monkeypatch, _draft("V-ON"))
    asyncio.run(v.send_po("PO1", _user()))
    assert repo.pos["PO1"]["status"] == "SENT"


def test_existing_po_of_switched_off_vendor_still_readable(monkeypatch):
    _wire(monkeypatch, dict(_draft("V-OFF"), status="SENT"))
    out = asyncio.run(v.get_po("PO1", _user()))
    assert out["vendor_id"] == "V-OFF"


# --------------------------------------------------------------------------- #
# lens top-up generator
# --------------------------------------------------------------------------- #


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find_one(self, q, projection=None):
        return next((dict(d) for d in self.docs if matches(d, q)), None)


class _DB:
    def __init__(self, lines):
        self.cols = {"lens_catalog": _Coll(lines), "products": _Coll([])}

    def get_collection(self, name):
        return self.cols.setdefault(name, _Coll([]))


def test_cl_resolver_drops_switched_off_vendor(monkeypatch):
    monkeypatch.setattr(cl_po, "get_vendor_repository", lambda: _VendorRepo())
    db = _DB([
        {"lens_line_id": "LL-OFF", "preferred_vendor_id": "V-OFF"},
        {"lens_line_id": "LL-ON", "preferred_vendor_id": "V-ON"},
    ])
    resolve = cl_po._make_vendor_resolver(db)
    # A closed supplier's lines land in the no-vendor draft (send refused
    # until a vendor is chosen); a live supplier keeps its lines.
    assert resolve({"lens_line_id": "LL-OFF"}) is None
    assert resolve({"lens_line_id": "LL-ON"}) == "V-ON"


# --------------------------------------------------------------------------- #
# generated drafts, sent by the API (Codex 11-06: the guard only looked at a
# vendor_id that was there, so the no-vendor draft the generator makes for a
# switched-off supplier went SENT with nobody to send it to)
# --------------------------------------------------------------------------- #

_NEED = {"lens_line_id": "LL-OFF", "sph": -2.0, "cyl": 0.0, "add": None,
         "qty": 2, "description": "Acme One", "unit_price": 50}


def _generate(monkeypatch, repo, line_vendor):
    """The REAL generator writes the draft into `repo`; returns its po_id."""
    monkeypatch.setattr(cl_po, "_get_db", lambda: _DB(
        [{"lens_line_id": "LL-OFF", "preferred_vendor_id": line_vendor}]
    ))
    monkeypatch.setattr(cl_po, "_read_gap_planner_needs", lambda s, l: [dict(_NEED)])
    monkeypatch.setattr(cl_po, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(cl_po, "get_purchase_order_repository", lambda: repo)
    monkeypatch.setattr(cl_po, "get_audit_repository", lambda: None)
    monkeypatch.setattr(cl_po, "generate_po_number", lambda s: f"PO-{s}")
    out = asyncio.run(cl_po.generate_cl_po(
        cl_po.CLPOGenerateRequest(store_id="S1", source="gap-planner", dry_run=False),
        _user(),
    ))
    assert out["pos_created"] == 1
    return out["created_pos"][0]["po_id"]


@pytest.mark.parametrize("roles", [("STORE_MANAGER",), ("ADMIN",), ("SUPERADMIN",)])
def test_generated_draft_with_no_supplier_cannot_be_sent(monkeypatch, roles):
    repo = _wire(monkeypatch)
    po_id = _generate(monkeypatch, repo, "V-OFF")
    assert repo.pos[po_id]["vendor_id"] is None
    before = copy.deepcopy(repo.pos[po_id])
    writes = len(repo.writes)

    with pytest.raises(HTTPException) as e:
        asyncio.run(v.send_po(po_id, _user(roles)))

    assert e.value.status_code == 409
    assert e.value.detail["code"] == "VENDOR_REQUIRED"
    assert repo.pos[po_id] == before, "a refused send leaves the draft as it was"
    assert len(repo.writes) == writes


def test_generated_draft_to_active_supplier_still_sends(monkeypatch):
    repo = _wire(monkeypatch)
    po_id = _generate(monkeypatch, repo, "V-ON")
    assert repo.pos[po_id]["vendor_id"] == "V-ON"
    asyncio.run(v.send_po(po_id, _user()))
    assert repo.pos[po_id]["status"] == "SENT"


def test_send_rule_does_not_lean_on_the_source_tag(monkeypatch):
    """A proposal's no-vendor draft carries no `source`: same refusal, and the
    way out -- pick a live supplier on the draft -- then sends it."""
    repo = _wire(monkeypatch, dict(_draft("V-ON"), vendor_id=None, vendor_name=None))
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.send_po("PO1", _user()))
    assert e.value.detail["code"] == "VENDOR_REQUIRED"
    assert repo.pos["PO1"]["status"] == "DRAFT"

    asyncio.run(v.update_po("PO1", _edit_body(vendor_id="V-ON"), _user()))
    asyncio.run(v.send_po("PO1", _user()))
    assert repo.pos["PO1"]["status"] == "SENT"


def test_send_to_a_vendor_record_that_is_gone_refused(monkeypatch):
    """A vendor_id naming nothing is not a supplier either. BaseRepository
    answers None on a failed read too, so an unreadable vendor lands here."""
    repo = _wire(monkeypatch, dict(_draft("V-ON"), vendor_id="V-GONE"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(v.send_po("PO1", _user()))
    assert e.value.status_code == 404
    assert repo.pos["PO1"]["status"] == "DRAFT"
    assert repo.writes == []
