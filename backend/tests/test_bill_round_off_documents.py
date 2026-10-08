"""
IMS 2.0 - Bill round off on the invoice and in the GST returns (owner ruling
2026-10-08). The invoice and the A4 receipt (the same server document) print a
separate "Round off" line read off the order; GSTR-1 / GSTR-3B taxable value and
tax, and the e-invoice taxable and tax figures, are exactly what the lines carry
-- the round off is neither. The e-invoice carries it in its own RndOffAmt field.
Anchors as in test_bill_round_off.py.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_loyalty import patched_loyalty  # noqa: E402,F401  (fixture)


# ============================================================================
# 1. The invoice and the A4 receipt show the Round off line
# ============================================================================


def test_invoice_payload_and_pdf_show_round_off(monkeypatch):
    from tests.test_invoice_pdf import _FakeCustomerRepo, _FakeStoreRepo
    from tests.test_invoice_pdf_statutory import _pdf_text
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import dependencies as deps_module
    from api.routers import orders as orders_module
    from api.routers.auth import get_current_user
    from api.services import invoice_pdf as pdf_module

    doc = {
        "order_id": "ORD-RO", "order_number": "ORD-RO-1", "store_id": "BV-TEST-01",
        "customer_id": "cust-pdf", "customer_name": "Asha Rao", "status": "CONFIRMED",
        "grand_total": 1001.0, "round_off": 0.5, "amount_paid": 1001.0,
        "balance_due": 0.0, "tax_amount": 47.64, "cart_discount_amount": 0.0,
        "items": [{"item_id": "l1", "item_type": "FRAME", "product_name": "Aviator",
                   "hsn_code": "90031900", "quantity": 1, "unit_price": 1000.5,
                   "item_total": 1000.5, "gst_rate": 5.0, "taxable_value": 952.86,
                   "tax_amount": 47.64}],
    }

    class _Repo:
        def find_by_id(self, oid):
            return dict(doc)

        def ensure_invoice_index(self):
            pass

        def next_invoice_number(self, store_id, store_doc=None):
            return "BV/TEST-01/26-27/0001"

        def set_invoice(self, oid, number):
            doc["invoice_number"] = number

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: _Repo())
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: _FakeCustomerRepo())
    monkeypatch.setattr(orders_module, "validate_store_access", lambda sid, user: sid)
    monkeypatch.setattr(deps_module, "get_store_repository", lambda: _FakeStoreRepo())
    monkeypatch.setattr(
        pdf_module,
        "resolve_issuing_identity",
        lambda store_id, key: {
            "store": {"name": "BV", "gstin": "20AAAAA0000A1Z5", "address": "x"},
            "entity": {"legal_name": "BV Opticals Pvt Ltd"},
            "overrides": {},
        },
    )
    app = FastAPI()
    app.include_router(orders_module.router, prefix="/orders")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u1", "roles": ["STORE_MANAGER"], "active_store_id": "BV-TEST-01"
    }
    client = TestClient(app)

    j = client.get("/orders/ORD-RO/invoice").json()
    assert j["roundOff"] == 0.5
    assert j["grandTotal"] == 1001.0
    # The tax summary is the lines' own figures -- the round off is not in it.
    assert j["taxTotals"]["taxable"] == 952.86

    text = _pdf_text(client.get("/orders/ORD-RO/invoice.pdf").content)
    assert "Round off" in text
    assert "0.50" in text
    assert "One Thousand One" in text  # amount in words is the rounded total


# ============================================================================
# 2. GSTR-1 / GSTR-3B / e-invoice: taxable value and tax unchanged
# ============================================================================


def _mixed_order(**over):
    from api.routers.orders import _compute_per_category_gst

    items = [
        {"hsn_code": "900311", "item_type": "FRAME", "category": "FRAME", "item_total": 999.99},
        {"hsn_code": "9004", "item_type": "SUNGLASSES", "category": "SUNGLASSES", "item_total": 500.50},
    ]
    gst = _compute_per_category_gst(items, 0)
    doc = {
        "order_id": "ORD-G1", "order_number": "ORD-G1", "invoice_number": "INV-1",
        "store_id": "store-001", "customer_id": "", "status": "COMPLETED",
        "created_at": datetime(2026, 4, 15, 10, 0, 0),
        "grand_total": gst["grand_total"], "round_off": gst["round_off"],
        "tax_amount": gst["tax"], "items": items,
    }
    doc.update(over)
    return doc


def test_gst_base_backs_round_off_out_of_taxable():
    from api.routers.reports.gst_base import _order_taxable_and_tax

    assert _order_taxable_and_tax(_mixed_order()) == (1376.52, 123.97)


def _gstr1_for(monkeypatch, order):
    import api.routers.reports as r
    from api.routers.reports import _compute_gstr1
    from tests.test_gstr1_b2cs_hsn import _DB, _STORE, _Coll

    db = _DB({
        "stores": _Coll([_STORE]),
        "customers": _Coll([{"customer_id": "B2B-1", "gstin": "07AAAAA1111A1Z1",
                             "name": "Acme", "state": "Delhi"}]),
        "orders": _Coll([order]),
        "credit_note_ledger": _Coll([]),
    })
    monkeypatch.setattr(r, "_get_raw_db", lambda: db)
    return _compute_gstr1("2026-04", "store-001")


def test_gstr1_consumer_buckets_unchanged_by_round_off(monkeypatch):
    rounded = _gstr1_for(monkeypatch, _mixed_order())
    unrounded = _gstr1_for(
        monkeypatch, _mixed_order(grand_total=1500.49, round_off=None)
    )
    assert rounded["b2cs"] == unrounded["b2cs"]
    by = {row["gstRate"]: row for row in rounded["b2cs"]}
    assert (by[5]["taxableValue"], by[5]["totalTax"]) == (952.37, 47.62)
    assert (by[18]["taxableValue"], by[18]["totalTax"]) == (424.15, 76.35)


def test_gstr1_b2b_row_taxable_and_tax_unchanged(monkeypatch):
    row = _gstr1_for(monkeypatch, _mixed_order(customer_id="B2B-1"))["b2b"][0]
    assert row["taxableValue"] == 1376.52
    assert row["totalTax"] == 123.97
    # The invoice value is the invoice's own printed total.
    assert row["invoiceValue"] == 1500.0


def test_einvoice_carries_round_off_field():
    from api.services.einvoice import _build_einvoice_json as build_einvoice_payload

    payload = build_einvoice_payload(
        {"invoice_number": "INV-1", "grand_total": 1500.0, "round_off": -0.49,
         "taxable_amount": 1376.52, "cgst_amount": 61.98, "sgst_amount": 61.99,
         "items": []}
    )
    val = payload["ValDtls"]
    assert val["RndOffAmt"] == -0.49
    assert val["TotInvVal"] == 1500.0
    assert val["AssVal"] == 1376.52


def test_loyalty_earn_basis_is_taxable_value_without_round_off(
    client, auth_headers, patched_loyalty
):
    patched_loyalty["orders"].seed("ORD-L", "cust-l", 10001.0)
    patched_loyalty["orders"]._orders["ORD-L"]["round_off"] = 0.5
    r = client.post(
        "/api/v1/loyalty/earn",
        json={"customer_id": "cust-l", "order_id": "ORD-L"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    row = patched_loyalty["txns"].find_for_customer("cust-l")[0]
    assert row["rupee_value"] == 10000.5


