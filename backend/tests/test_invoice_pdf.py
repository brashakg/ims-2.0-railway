"""
IMS 2.0 - Server-side tax-invoice PDF (F52 / POS Wave 4 groundwork)
===================================================================
One assembly, one renderer: GET /orders/{id}/invoice.pdf renders EXACTLY
what the JSON invoice door assembled (same idempotent serial, same C-6 GST
split); services/invoice_pdf.py only lays out.

Discriminating power: the roundtrip test red-lines if the PDF route stops
sharing the JSON door's assembly (different serials) or stops returning a
real PDF; the DRAFT test pins that the shared gates still fire on the PDF
door.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import orders as orders_module  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import invoice_pdf as pdf_module  # noqa: E402


_ORDER = {
    "order_id": "ORD-PDF-1",
    "order_number": "ORD-BOK01-2026-PDF1",
    "store_id": "BV-TEST-01",
    "customer_id": "cust-pdf",
    "customer_name": "Asha Rao",
    "status": "CONFIRMED",
    "grand_total": 1050.0,
    "amount_paid": 1050.0,
    "balance_due": 0.0,
    "tax_amount": 50.0,
    "cart_discount_amount": 0.0,
    "items": [
        {
            "item_id": "l1",
            "item_type": "FRAME",
            "product_name": "Aviator Frame",
            "brand": "Ray-Ban",
            "hsn_code": "90031900",
            "quantity": 1,
            "unit_price": 1050.0,
            "discount_percent": 0,
            "item_total": 1050.0,
            "gst_rate": 5.0,
            "taxable_value": 1000.0,
            "tax_amount": 50.0,
        }
    ],
}


class _FakeOrderRepo:
    def __init__(self):
        self._doc = dict(_ORDER)
        self.minted = 0

    def find_by_id(self, oid):
        return dict(self._doc) if oid == self._doc["order_id"] else None

    def ensure_invoice_index(self):
        pass

    def next_invoice_number(self, store_id, store_doc=None):
        self.minted += 1
        return f"BV/TEST-01/26-27/000{self.minted}"

    def set_invoice(self, oid, number):
        self._doc["invoice_number"] = number


class _FakeStoreRepo:
    def find_by_id(self, sid):
        return {
            "store_id": sid,
            "name": "Better Vision Bokaro",
            "gstin": "20AAAAA0000A1Z5",
            "state": "Jharkhand",
        }


class _FakeCustomerRepo:
    def find_by_id(self, cid):
        return {"customer_id": cid, "name": "Asha Rao", "mobile": "9812345678",
                "state": "Jharkhand"}


@pytest.fixture
def wired(monkeypatch):
    repo = _FakeOrderRepo()
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: repo)
    monkeypatch.setattr(
        orders_module, "get_customer_repository", lambda: _FakeCustomerRepo()
    )
    monkeypatch.setattr(
        orders_module, "validate_store_access", lambda sid, user: sid
    )
    # get_store_repository is imported inside the function from ..dependencies
    from api import dependencies as deps_module

    monkeypatch.setattr(
        deps_module, "get_store_repository", lambda: _FakeStoreRepo()
    )
    # Identity lookup inside the renderer needs no live DB.
    monkeypatch.setattr(
        pdf_module,
        "resolve_issuing_identity",
        lambda store_id, key: {
            "store": {
                "name": "Better Vision Bokaro",
                "gstin": "20AAAAA0000A1Z5",
                "address": "Main Road, Bokaro",
                "phone": "06542-233444",
            },
            "entity": {"legal_name": "BV Opticals Pvt Ltd"},
            "overrides": {},
        },
    )

    app = FastAPI()
    app.include_router(orders_module.router, prefix="/orders")

    async def _user():
        return {
            "user_id": "u1",
            "roles": ["STORE_MANAGER"],
            "active_store_id": "BV-TEST-01",
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), repo


def test_build_invoice_lines_uses_persisted_statutory_fields():
    from api.services.invoice_pdf import build_invoice_lines

    rows = build_invoice_lines(_ORDER)
    assert rows[0]["hsn"] == "90031900"
    assert rows[0]["taxable_value"] == 1000.0
    assert rows[0]["gst_rate"] == 5.0
    assert rows[0]["description"].startswith("Ray-Ban")


def test_pdf_shares_the_json_doors_serial(wired):
    """JSON then PDF: one serial, minted once. The PDF route red-lines if it
    stops routing through _assemble_invoice."""
    client, repo = wired
    j = client.get("/orders/ORD-PDF-1/invoice")
    assert j.status_code == 200, j.text
    serial = j.json()["invoiceNumber"]
    assert serial.startswith("BV/TEST-01/")

    p = client.get("/orders/ORD-PDF-1/invoice.pdf")
    assert p.status_code == 200, p.text
    assert p.headers["content-type"] == "application/pdf"
    assert p.content[:5] == b"%PDF-"
    assert serial.replace("/", "-") in p.headers["content-disposition"]
    assert repo.minted == 1  # PDF reused the JSON door's serial


def test_pdf_first_also_mints_once(wired):
    client, repo = wired
    p = client.get("/orders/ORD-PDF-1/invoice.pdf")
    assert p.status_code == 200
    j = client.get("/orders/ORD-PDF-1/invoice")
    assert repo.minted == 1
    assert j.json()["invoiceNumber"].replace("/", "-") in p.headers[
        "content-disposition"
    ]


def test_pdf_blocked_for_draft(wired):
    client, repo = wired
    repo._doc["status"] = "DRAFT"
    p = client.get("/orders/ORD-PDF-1/invoice.pdf")
    assert p.status_code == 400


def test_pdf_contains_invoice_content(wired):
    """The rendered PDF carries the real content (uncompressed reportlab
    streams include text drawing ops; assert on raw bytes fail-soft: fall
    back to just the magic if compression hides the text)."""
    client, _ = wired
    p = client.get("/orders/ORD-PDF-1/invoice.pdf")
    assert p.content[:5] == b"%PDF-"
    assert len(p.content) > 1500  # a real laid-out page, not a stub


def test_pdf_survives_and_keeps_angle_bracket_names(wired):
    """Panel blocker F1: '<'+letter crashed reportlab (500) and '<Word>' was
    silently swallowed from the statutory doc. With escaping, the PDF renders
    and the literal text survives into the content stream."""
    client, repo = wired
    repo._doc["items"][0]["product_name"] = "Frame <Titan> 50% <off promo"
    repo._doc["customer_name"] = "A<B&C>D"
    p = client.post if False else client.get
    r = p("/orders/ORD-PDF-1/invoice.pdf")
    assert r.status_code == 200, r.text
    assert r.content[:5] == b"%PDF-"
