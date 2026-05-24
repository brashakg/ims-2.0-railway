"""Unit tests for services/itc_reconcile.py (GST input-credit reconciliation)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.itc_reconcile import (  # noqa: E402
    build_itc_register, reconcile_gstr2b, _norm_inv,
)


def test_norm_inv():
    # Punctuation/case/leading-zeros differences shouldn't break matching.
    assert _norm_inv("INV/001") == _norm_inv("inv-001") == _norm_inv("INV 001")


def test_build_itc_register():
    bills = [
        {"bill_date": "2026-04-10", "taxable_amount": 1000, "tax_amount": 50},
        {"bill_date": "2026-04-20", "taxable_amount": 2000, "tax_amount": 100},
        {"bill_date": "2026-05-02", "taxable_amount": 500, "tax_amount": 25},
    ]
    reg = build_itc_register(bills)
    assert reg["total_itc"] == 175.0
    assert reg["total_taxable"] == 3500.0
    apr = next(p for p in reg["periods"] if p["period"] == "2026-04")
    assert apr["tax"] == 150.0
    assert apr["cgst"] == 75.0 and apr["sgst"] == 75.0
    assert apr["bills"] == 2
    # newest period first
    assert reg["periods"][0]["period"] == "2026-05"


def test_reconcile_buckets():
    books = [
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "INV/001", "tax": 50, "bill_id": "b1", "vendor_name": "Alpha", "bill_date": "2026-04-10"},
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "INV-002", "tax": 80, "bill_id": "b2", "vendor_name": "Alpha", "bill_date": "2026-04-12"},
        {"gstin": "29AAGCB1234J1Z5", "invoice_no": "X9", "tax": 30, "bill_id": "b3", "vendor_name": "Beta", "bill_date": "2026-04-15"},
    ]
    gstr2b = [
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "inv001", "taxable": 1000, "tax": 50},   # matches b1
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "INV 002", "taxable": 1600, "tax": 90},  # matches b2 but tax differs -> mismatch
        {"gstin": "33AAAAA0000A1Z5", "invoice_no": "Z1", "taxable": 400, "tax": 20},         # only in 2B
    ]
    r = reconcile_gstr2b(books, gstr2b, as_of_iso="2026-05-24")
    s = r["summary"]
    assert s["matched"] == 1          # b1
    assert s["mismatch"] == 1         # b2 (50 vs ... 80 vs 90)
    assert s["only_in_books"] == 1    # b3 (Beta) -> ITC at risk
    assert s["only_in_2b"] == 1       # Z1
    assert s["itc_safe_to_claim"] == 50.0
    assert s["itc_at_risk"] == 30.0
    assert r["only_in_books"][0]["vendor_name"] == "Beta"
    assert r["matched"][0]["bill_id"] == "b1"


def test_reconcile_empty():
    r = reconcile_gstr2b([], [])
    assert r["summary"]["matched"] == 0
    assert r["only_in_2b"] == []
