"""Purchase P1 / S3 -- mandatory goods-receipt document gate.

The ops user physically receiving a STANDARD shipment MUST attach the vendor
invoice/challan (image or PDF) BEFORE the GRN is created, so the accountant
always has the source document to reconcile against. The owner's words:
"make sure the superadmin admin and or store manager HAS TO upload/attach an
image/pdf at this screen before proceeding."

Enforced as a LOUD 400 ({code: ATTACHMENT_REQUIRED}) in create_grn for a
STANDARD GRN with no attachment_file_id. A DELIVERY_CHALLAN is exempt at
receipt (its tax invoice arrives later and is attached at reconciliation).

The endpoint functions are driven directly with monkeypatched repos -- no
Mongo, no HTTP -- mirroring test_po_gst_and_residuals.py (S1).
"""
from __future__ import annotations

import os
import sys
import asyncio

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import (  # noqa: E402
    create_grn,
    upload_grn_doc,
    download_grn_doc,
    GRNCreate,
    GRNItemCreate,
)
from api.services.file_store import InMemoryFileStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeGRNRepo:
    def __init__(self, preset=None):
        self.created = None
        self._preset = preset

    def create(self, doc):
        self.created = doc
        return doc

    def find_one(self, query):
        return self._preset

    def find_by_id(self, grn_id):
        return self._preset


class _FakePORepo:
    """Returns a receivable PO so the STANDARD path validates."""

    def find_by_id(self, po_id):
        return {
            "po_id": po_id,
            "po_number": "PO-TEST-1",
            "vendor_id": "V1",
            "vendor_name": "Acme Optics",
            "status": "SENT",
            "items": [{"product_id": "P1", "quantity": 5}],
        }


class _FakeUpload:
    """Duck-types starlette UploadFile for the upload endpoint -- avoids
    version-specific UploadFile constructor differences across starlette."""

    def __init__(self, content, filename, content_type):
        self._content = content
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._content


def _patch_grn(mp, grn_repo, po_repo=None):
    mp.setattr(v, "get_grn_repository", lambda: grn_repo)
    mp.setattr(v, "get_purchase_order_repository", lambda: po_repo)
    mp.setattr(v, "generate_grn_number", lambda store: "GRN-TEST-1")


def _user(store="BV-TEST-01", roles=("ADMIN",)):
    return {"user_id": "u1", "roles": list(roles), "active_store_id": store}


def _std_items():
    return [GRNItemCreate(product_id="P1", received_qty=5, accepted_qty=5, rejected_qty=0)]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_standard_grn_without_attachment_is_rejected(monkeypatch):
    grn_repo = _FakeGRNRepo()
    _patch_grn(monkeypatch, grn_repo, _FakePORepo())
    grn = GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-9",
        items=_std_items(),
        # no attachment_file_id
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(create_grn(grn, current_user=_user()))
    assert exc.value.status_code == 400
    # Stable machine code the UI keys on to keep the user on the upload step.
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("code") == "ATTACHMENT_REQUIRED"
    # And nothing was persisted -- the gate fires BEFORE any save.
    assert grn_repo.created is None


def test_standard_grn_with_attachment_is_created(monkeypatch):
    grn_repo = _FakeGRNRepo()
    _patch_grn(monkeypatch, grn_repo, _FakePORepo())
    grn = GRNCreate(
        po_id="PO1",
        vendor_invoice_no="INV-9",
        items=_std_items(),
        attachment_file_id="file-123",
        attachment_filename="invoice.pdf",
        attachment_mime="application/pdf",
    )
    res = asyncio.run(create_grn(grn, current_user=_user()))
    assert res["grn_number"] == "GRN-TEST-1"
    # Persisted with the attachment metadata so the accountant console can link
    # back to the source document.
    assert grn_repo.created is not None
    assert grn_repo.created["attachment_file_id"] == "file-123"
    assert grn_repo.created["attachment_filename"] == "invoice.pdf"
    assert grn_repo.created["attachment_mime"] == "application/pdf"


def test_delivery_challan_without_attachment_is_exempt(monkeypatch):
    # A DC's tax invoice arrives later, so receipt-time attachment is NOT
    # required -- the DC must still be loggable.
    grn_repo = _FakeGRNRepo()
    _patch_grn(monkeypatch, grn_repo, _FakePORepo())
    grn = GRNCreate(
        grn_subtype="DELIVERY_CHALLAN",
        vendor_id="V1",
        dc_number="DC-77",
        dc_date="2026-06-16",
        items=_std_items(),
        # no attachment_file_id -- exempt
    )
    res = asyncio.run(create_grn(grn, current_user=_user()))
    assert res["grn_subtype"] == "DELIVERY_CHALLAN"
    assert grn_repo.created is not None
    # DC attachment is None at receipt (attached later at reconciliation).
    assert grn_repo.created["attachment_file_id"] is None


# ---------------------------------------------------------------------------
# upload-doc endpoint
# ---------------------------------------------------------------------------


def test_upload_doc_persists_and_returns_file_id(monkeypatch):
    store = InMemoryFileStore()
    monkeypatch.setattr(v, "get_file_store", lambda: store)
    up = _FakeUpload(b"%PDF-1.4 fake", "invoice.pdf", "application/pdf")
    res = asyncio.run(upload_grn_doc(file=up, current_user=_user()))
    assert res["persisted"] is True
    assert res["file_id"]
    assert res["mime"] == "application/pdf"
    # The bytes are actually retrievable from the store.
    rec = store.get(res["file_id"])
    assert rec is not None
    assert rec[0] == b"%PDF-1.4 fake"


def test_upload_doc_rejects_bad_mime(monkeypatch):
    store = InMemoryFileStore()
    monkeypatch.setattr(v, "get_file_store", lambda: store)
    up = _FakeUpload(b"MZ...", "trojan.exe", "application/x-msdownload")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(upload_grn_doc(file=up, current_user=_user()))
    assert exc.value.status_code == 400


def test_upload_doc_rejects_empty(monkeypatch):
    store = InMemoryFileStore()
    monkeypatch.setattr(v, "get_file_store", lambda: store)
    up = _FakeUpload(b"", "empty.png", "image/png")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(upload_grn_doc(file=up, current_user=_user()))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# download-doc endpoint (store-scope)
# ---------------------------------------------------------------------------


def test_download_doc_streams_in_scope(monkeypatch):
    store = InMemoryFileStore()
    fid = store.put(content=b"img-bytes", filename="inv.png", mime_type="image/png")
    grn = {"grn_id": "G1", "store_id": "BV-TEST-01", "attachment_file_id": fid}
    monkeypatch.setattr(v, "get_grn_repository", lambda: _FakeGRNRepo(preset=grn))
    monkeypatch.setattr(v, "get_file_store", lambda: store)
    # Store-level caller whose store matches -> streams.
    res = asyncio.run(
        download_grn_doc(
            "G1",
            current_user=_user(store="BV-TEST-01", roles=("STORE_MANAGER",)),
        )
    )
    # StreamingResponse with the right media type.
    assert res.media_type == "image/png"
    assert res.status_code == 200


def test_download_doc_cross_store_is_404(monkeypatch):
    store = InMemoryFileStore()
    fid = store.put(content=b"img-bytes", filename="inv.png", mime_type="image/png")
    grn = {"grn_id": "G1", "store_id": "BV-OTHER-99", "attachment_file_id": fid}
    monkeypatch.setattr(v, "get_grn_repository", lambda: _FakeGRNRepo(preset=grn))
    monkeypatch.setattr(v, "get_file_store", lambda: store)
    # A store-level caller for a DIFFERENT store must not see the doc exists.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            download_grn_doc(
                "G1",
                current_user=_user(store="BV-TEST-01", roles=("STORE_MANAGER",)),
            )
        )
    assert exc.value.status_code == 404
