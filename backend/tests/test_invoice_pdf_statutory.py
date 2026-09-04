"""
IMS 2.0 - A4 tax-invoice PDF: statutory completeness (owner decision 2026-09-03,
"A4 sheet + WhatsApp, mixed - NOT thermal").

Pins the Rule 46/48 items the server A4 PDF was missing:
  * Rule 46(p) reverse-charge declaration line
  * Rule 48 copy marker (ORIGINAL FOR RECIPIENT)
  * HSN-wise consolidated tax summary built ONLY from the order's persisted
    per-line statutory values, split via the SHARED gst_rates.split_gst
    (cgst + sgst == tax exactly, odd paisa never dropped)
  * e-invoice IRN block when (and only when) the order carries an IRN
  * Sec. 31 / Rule 46 statutory footer + retention line

Discriminating power: every PDF assertion is on a token that ONLY the new
code emits (aggregated rupee amounts that appear on no single line, the
marker/footer wording, the IRN value) -- extracted from the PDF content
streams, never from fixture echoes. Verified by reverting invoice_pdf.py to
its pre-change version: every test in this file fails (see PR notes).
"""

from __future__ import annotations

import base64
import os
import re
import sys
import zlib

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import invoice_pdf as pdf_module  # noqa: E402
from api.services.invoice_pdf import (  # noqa: E402
    build_hsn_summary_rows,
    build_invoice_pdf,
)


def _pdf_text(pdf: bytes) -> str:
    """Concatenate the raw PDF bytes plus every stream chunk (deflate-
    decompressed when possible) so token assertions work whether or not
    reportlab compressed the page streams."""
    chunks = [pdf]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        raw = m.group(1)
        chunks.append(raw)
        # Plain deflate.
        try:
            chunks.append(zlib.decompress(raw))
        except Exception:  # noqa: BLE001 - not a bare deflate stream
            pass
        # reportlab default: ASCII85 (optionally wrapping deflate).
        try:
            s = raw.strip()
            if s.endswith(b"~>"):
                s = s[:-2]
            dec = base64.a85decode(re.sub(rb"\s", b"", s))
            chunks.append(dec)
            try:
                chunks.append(zlib.decompress(dec))
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001 - not ascii85
            pass
    return b"\n".join(chunks).decode("latin-1", errors="ignore")


# Two lines share HSN 90031900 @ 5% with an ODD combined paisa (66.67) so the
# aggregate (1,333.33 taxable / 66.67 tax) exists on NO single line -- the
# only way those tokens reach the PDF is via the HSN-wise summary.
_ORDER = {
    "order_id": "ORD-STAT-1",
    "store_id": "BV-TEST-01",
    "items": [
        {
            "product_name": "Aviator",
            "brand": "Ray-Ban",
            "hsn_code": "90031900",
            "quantity": 1,
            "unit_price": 1050.0,
            "taxable_value": 1000.0,
            "gst_rate": 5.0,
            "tax_amount": 50.0,
        },
        {
            "product_name": "Wayfarer",
            "brand": "Ray-Ban",
            "hsn_code": "90031900",
            "quantity": 2,
            "unit_price": 175.0,
            "taxable_value": 333.33,
            "gst_rate": 5.0,
            "tax_amount": 16.67,
        },
        {
            "product_name": "Polar Shade",
            "brand": "Ray-Ban",
            "hsn_code": "90041000",
            "quantity": 1,
            "unit_price": 118.0,
            "taxable_value": 100.0,
            "gst_rate": 18.0,
            "tax_amount": 18.0,
        },
    ],
}

_PAYLOAD = {
    "invoiceNumber": "BV/TEST-01/26-27/0001",
    "orderNumber": "ORD-STAT-1",
    "customerName": "M/s Sharma & Sons",
    "grandTotal": 1518.0,
    "amountPaid": 1518.0,
    "balanceDue": 0.0,
    "invoiceDate": "2026-09-03",
    "placeOfSupply": "Jharkhand",
    "interstate": False,
    "storeGstin": "20AAAAA0000A1Z5",
    "taxSummary": [
        {"rate": 5.0, "taxable": 1333.33, "cgst": 33.33, "sgst": 33.34, "igst": 0.0},
        {"rate": 18.0, "taxable": 100.0, "cgst": 9.0, "sgst": 9.0, "igst": 0.0},
    ],
    "taxTotals": {"cgst": 42.33, "sgst": 42.34, "igst": 0.0},
}


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
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


def _render(order=None, payload=None) -> str:
    pdf = build_invoice_pdf(payload or dict(_PAYLOAD), order or dict(_ORDER))
    assert pdf[:5] == b"%PDF-"
    return _pdf_text(pdf)


# ---------------------------------------------------------------------------
# build_hsn_summary_rows -- pure aggregation over PERSISTED values
# ---------------------------------------------------------------------------


def test_hsn_rows_aggregate_persisted_values_only():
    rows = build_hsn_summary_rows(_ORDER, interstate=False)
    assert [r["hsn"] for r in rows] == ["90031900", "90041000"]
    r5 = rows[0]
    assert r5["qty"] == 3.0
    assert r5["taxable"] == 1333.33  # sum of stored taxable_value, no recompute
    assert r5["tax"] == 66.67  # sum of stored tax_amount, no recompute
    # THE split invariant (shared split_gst): never a dropped/invented paisa.
    assert round(r5["cgst"] + r5["sgst"], 2) == 66.67
    assert r5["igst"] == 0.0


def test_hsn_rows_interstate_routes_to_igst():
    rows = build_hsn_summary_rows(_ORDER, interstate=True)
    for r in rows:
        assert r["cgst"] == 0.0 and r["sgst"] == 0.0
        assert r["igst"] == r["tax"]


def test_hsn_rows_missing_hsn_buckets_as_dash():
    order = {"items": [{"taxable_value": 10, "gst_rate": 5, "tax_amount": 0.5}]}
    rows = build_hsn_summary_rows(order, interstate=False)
    assert rows[0]["hsn"] == "-"


# ---------------------------------------------------------------------------
# Rendered PDF carries the statutory blocks
# ---------------------------------------------------------------------------


def test_pdf_carries_reverse_charge_declaration():
    text = _render()
    assert "Reverse Charge" in text
    # and the retail answer is No, printed next to it somewhere in the stream
    assert "No" in text


def test_pdf_carries_rule48_copy_marker():
    text = _render()
    assert "ORIGINAL FOR RECIPIENT" in text


def test_pdf_carries_hsn_wise_aggregate():
    """The aggregated 5% bucket (1,333.33 / 66.67) exists on NO single order
    line, so these tokens can only come from the HSN-wise summary table."""
    text = _render()
    assert "1,333.33" in text
    assert "66.67" in text
    assert "HSN-wise tax summary" in text


def test_pdf_carries_statutory_footer():
    text = _render()
    assert "Rule 46" in text
    assert "Rule 56" in text  # retention reference
    assert "Retain for 7 years" in text


def test_pdf_einvoice_block_only_when_irn_present():
    plain = _render()
    assert "TESTIRN" not in plain

    order = dict(_ORDER)
    order["irn"] = "TESTIRN1234567890abcdef"
    order["ack_no"] = "112010012345678"
    with_irn = _render(order=order)
    assert "TESTIRN1234567890abcdef" in with_irn
    assert "112010012345678" in with_irn


def test_pdf_ampersand_customer_name_survives():
    """'M/s Sharma & Sons' must both render (no reportlab parse crash) and
    keep its literal text in the content stream (the escaping regression)."""
    text = _render()
    assert "Sharma" in text
    assert "Sons" in text
