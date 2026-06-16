"""Purchase P1 / S1 -- PO per-line GST + residual fields.

create_po previously hardcoded a flat 18% GST AND stored lines with no
tax_rate, so the downstream invoice draft (lines_from_grn, which reads
po_line['tax_rate']) computed 0% tax. S1 resolves GST per line (server-side,
with product hsn/category fallback) and stamps the residual fields
(ordered_qty / received_qty / line_status) the receiving cockpit reads.
The endpoint is driven directly with monkeypatched repos -- no Mongo, no HTTP.
"""
from __future__ import annotations

import os
import sys
import asyncio

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import create_po, POCreate, POItemCreate  # noqa: E402
from api.services.gst_rates import resolve_gst_rate  # noqa: E402


class _FakePORepo:
    def __init__(self):
        self.created = None

    def create(self, doc):
        self.created = doc
        return doc


class _FakeVendorRepo:
    def find_by_id(self, vid):
        return {"vendor_id": vid, "trade_name": "Acme Optics"}


class _FakeProductRepo:
    def __init__(self, prods):
        self.prods = prods

    def find_by_id(self, pid):
        return self.prods.get(pid)


def _patch(mp, po_repo, prod_repo=None):
    mp.setattr(v, "get_purchase_order_repository", lambda: po_repo)
    mp.setattr(v, "get_vendor_repository", lambda: _FakeVendorRepo())
    mp.setattr(v, "get_product_repository", lambda: prod_repo)
    mp.setattr(v, "generate_po_number", lambda store: "PO-TEST-1")


def _user():
    return {"user_id": "u1", "roles": ["ADMIN"], "active_store_id": "BV-TEST-01"}


def test_per_line_gst_not_flat_18_plus_residual_fields(monkeypatch):
    po_repo = _FakePORepo()
    _patch(monkeypatch, po_repo)
    po = POCreate(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[
            POItemCreate(product_id="P1", product_name="Ray-Ban", sku="RB1",
                         quantity=2, unit_price=1000, gst_rate=5),
            POItemCreate(product_id="P2", product_name="Oakley SG", sku="OK1",
                         quantity=1, unit_price=2000, gst_rate=18),
        ],
    )
    asyncio.run(create_po(po, current_user=_user()))
    doc = po_repo.created
    assert doc is not None
    # subtotal 2000 + 2000 = 4000; tax = 100 (5% of 2000) + 360 (18% of 2000) = 460
    # -- NOT the old flat 18% of 4000 (= 720).
    assert doc["subtotal"] == 4000
    assert doc["tax_amount"] == 460
    assert doc["total_amount"] == 4460
    line0 = doc["items"][0]
    assert line0["tax_rate"] == 5
    assert line0["ordered_qty"] == 2
    assert line0["received_qty"] == 0
    assert line0["line_status"] == "OPEN"
    assert doc["items"][1]["tax_rate"] == 18


def test_gst_resolved_from_product_when_line_omits_rate(monkeypatch):
    po_repo = _FakePORepo()
    prod_repo = _FakeProductRepo(
        {"P1": {"product_id": "P1", "category": "FRAME", "hsn_code": "9003"}}
    )
    _patch(monkeypatch, po_repo, prod_repo)
    po = POCreate(
        vendor_id="V1",
        delivery_store_id="BV-TEST-01",
        items=[POItemCreate(product_id="P1", product_name="Frame", sku="F1",
                            quantity=1, unit_price=1000)],  # no gst_rate -> resolve
    )
    asyncio.run(create_po(po, current_user=_user()))
    line = po_repo.created["items"][0]
    # The stored rate must equal what the canonical resolver returns for this
    # product's hsn/category (frames = 5% in the static table) -- not 18%.
    expected = resolve_gst_rate(hsn_code="9003", category="FRAME")
    assert line["tax_rate"] == expected
    assert line["hsn"] == "9003"
    assert line["line_status"] == "OPEN"
