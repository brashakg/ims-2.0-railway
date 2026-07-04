"""PO / GRN store-boundary enforcement (Finding 2).

A store-scoped VENDOR-role user must not create / read / send / cancel a PO for
another store, nor receive (GRN) against another store's PO. Cross-store roles
(ADMIN / AREA_MANAGER / SUPERADMIN) stay unrestricted, and a standard PO-backed
GRN is booked to the PO's delivery store -- not blindly the caller's active
store. These call the router functions directly (like test_grn_accept_store_guard)
so the guard is asserted BEFORE any mutation.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as vendors_mod  # noqa: E402


class _PORepo:
    def __init__(self, po):
        self.po = po
        self.mutations = []

    def find_by_id(self, pid):
        return dict(self.po) if self.po and pid == self.po["po_id"] else None

    def update(self, pid, patch):
        self.mutations.append((pid, patch))
        return True


class _GRNRepo:
    def __init__(self):
        self.created = []

    def create(self, doc):
        self.created.append(doc)
        return doc


class _FileStore:
    """A live file store whose get() always finds the attachment (gate passes)."""

    def get(self, _fid):
        return {"data": b"x", "filename": "inv.pdf"}


def _user(roles, active, stores=None):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": roles,
        "active_store_id": active,
        "store_ids": stores if stores is not None else ([active] if active else []),
    }


def _po(store_id, status="SENT"):
    return {
        "po_id": "PO-1",
        "po_number": "PO-2601-1",
        "vendor_id": "V1",
        "vendor_name": "Acme",
        "delivery_store_id": store_id,
        "status": status,
        "items": [
            {"product_id": "P1", "product_name": "Frame", "sku": "S1", "quantity": 5}
        ],
    }


# --------------------------------------------------------------------------- #
# PO reads / writes
# --------------------------------------------------------------------------- #


def test_get_po_cross_store_404(monkeypatch):
    monkeypatch.setattr(
        vendors_mod, "get_purchase_order_repository", lambda: _PORepo(_po("STORE-B"))
    )
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.get_po("PO-1", _user(["STORE_MANAGER"], "STORE-A")))
    assert e.value.status_code == 404


def test_get_po_same_store_ok(monkeypatch):
    monkeypatch.setattr(
        vendors_mod, "get_purchase_order_repository", lambda: _PORepo(_po("STORE-A"))
    )
    out = asyncio.run(vendors_mod.get_po("PO-1", _user(["STORE_MANAGER"], "STORE-A")))
    assert out["po_id"] == "PO-1"


def test_get_po_admin_cross_store_ok(monkeypatch):
    """ADMIN is cross-store by design -> may read any store's PO."""
    monkeypatch.setattr(
        vendors_mod, "get_purchase_order_repository", lambda: _PORepo(_po("STORE-B"))
    )
    out = asyncio.run(vendors_mod.get_po("PO-1", _user(["ADMIN"], "STORE-A")))
    assert out["po_id"] == "PO-1"


def test_send_po_cross_store_404_no_mutation(monkeypatch):
    repo = _PORepo(_po("STORE-B", status="DRAFT"))
    monkeypatch.setattr(vendors_mod, "get_purchase_order_repository", lambda: repo)
    monkeypatch.setattr(vendors_mod, "get_product_repository", lambda: None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.send_po("PO-1", _user(["STORE_MANAGER"], "STORE-A")))
    assert e.value.status_code == 404
    assert repo.mutations == [], "guard must fire before the status update"


def test_cancel_po_cross_store_404_no_mutation(monkeypatch):
    repo = _PORepo(_po("STORE-B", status="SENT"))
    monkeypatch.setattr(vendors_mod, "get_purchase_order_repository", lambda: repo)
    with pytest.raises(HTTPException) as e:
        asyncio.run(
            vendors_mod.cancel_po(
                "PO-1", "changed mind", _user(["STORE_MANAGER"], "STORE-A")
            )
        )
    assert e.value.status_code == 404
    assert repo.mutations == []


def test_create_po_other_store_403(monkeypatch):
    monkeypatch.setattr(
        vendors_mod, "get_purchase_order_repository", lambda: _PORepo(None)
    )
    monkeypatch.setattr(vendors_mod, "get_vendor_repository", lambda: None)
    body = vendors_mod.POCreate(
        vendor_id="V1",
        delivery_store_id="STORE-B",
        items=[
            vendors_mod.POItemCreate(
                product_id="P1",
                product_name="Frame",
                sku="S1",
                quantity=1,
                unit_price=100.0,
            )
        ],
    )
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.create_po(body, _user(["STORE_MANAGER"], "STORE-A")))
    assert e.value.status_code == 403


# --------------------------------------------------------------------------- #
# GRN receipt against a PO
# --------------------------------------------------------------------------- #


def _grn_body():
    return vendors_mod.GRNCreate(
        po_id="PO-1",
        vendor_invoice_no="INV-1",
        vendor_invoice_date="2026-05-02",
        items=[
            vendors_mod.GRNItemCreate(
                product_id="P1", received_qty=5, accepted_qty=5, rejected_qty=0
            )
        ],
        attachment_file_id="FILE-1",
        attachment_filename="inv.pdf",
        attachment_mime="application/pdf",
    )


def test_create_grn_cross_store_404(monkeypatch):
    """A STORE_MANAGER of STORE-A receiving STORE-B's PO gets 404 (no GRN)."""
    grn_repo = _GRNRepo()
    monkeypatch.setattr(vendors_mod, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(
        vendors_mod, "get_purchase_order_repository", lambda: _PORepo(_po("STORE-B"))
    )
    monkeypatch.setattr(vendors_mod, "get_file_store", lambda: _FileStore())
    monkeypatch.setattr(vendors_mod, "generate_grn_number", lambda s: "GRN-1")
    with pytest.raises(HTTPException) as e:
        asyncio.run(
            vendors_mod.create_grn(_grn_body(), _user(["STORE_MANAGER"], "STORE-A"))
        )
    assert e.value.status_code == 404
    assert grn_repo.created == [], "no GRN may be minted on a cross-store reject"


def test_create_grn_books_to_po_store_not_active(monkeypatch):
    """An ADMIN whose active store is A, receiving STORE-B's PO, must stamp the
    GRN to STORE-B (the PO's delivery store) -- not the caller's active store."""
    grn_repo = _GRNRepo()
    monkeypatch.setattr(vendors_mod, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(
        vendors_mod, "get_purchase_order_repository", lambda: _PORepo(_po("STORE-B"))
    )
    monkeypatch.setattr(vendors_mod, "get_audit_repository", lambda: None)
    monkeypatch.setattr(vendors_mod, "get_file_store", lambda: _FileStore())
    monkeypatch.setattr(vendors_mod, "generate_grn_number", lambda s: f"GRN-{s}")
    asyncio.run(vendors_mod.create_grn(_grn_body(), _user(["ADMIN"], "STORE-A")))
    assert len(grn_repo.created) == 1
    assert grn_repo.created[0]["store_id"] == "STORE-B"
    # Serial minted from the PO store, not the caller's active store.
    assert grn_repo.created[0]["grn_number"] == "GRN-STORE-B"
