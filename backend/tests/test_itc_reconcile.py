"""Unit tests for services/itc_reconcile.py (GST input-credit reconciliation).

Covers:
  * Build register: intra-state CGST/SGST split + inter-state IGST routing.
  * Reconcile sum identity: safe + mismatch + at_risk == total booked ITC.
  * Bucket boundaries (matched / mismatch with tolerance / only_in_books / only_in_2b).
  * Place_of_supply absent -> default intra-state behaviour preserved.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.itc_reconcile import (  # noqa: E402
    build_itc_register, reconcile_gstr2b, _norm_inv, _is_interstate, _state_code,
)


def test_norm_inv():
    # Punctuation/case/leading-zeros differences shouldn't break matching.
    assert _norm_inv("INV/001") == _norm_inv("inv-001") == _norm_inv("INV 001")


def test_state_code_parsing():
    assert _state_code("27") == "27"
    assert _state_code("27-Maharashtra") == "27"
    assert _state_code("Maharashtra (27)") == "27"
    # GSTIN: first 2 chars are the state code.
    assert _state_code("27AAPFU0939F1ZV") == "27"
    assert _state_code(None) == ""
    assert _state_code("") == ""


def test_is_interstate_missing_pos_defaults_intrastate():
    # No place_of_supply -> assume intra so existing rows aren't reclassified.
    assert _is_interstate(None, "20") is False
    assert _is_interstate("", "20") is False
    assert _is_interstate("20", None) is False


def test_is_interstate_real_states():
    assert _is_interstate("27", "20") is True       # MH vs JH
    assert _is_interstate("20", "20") is False      # JH vs JH
    assert _is_interstate("27-MH", "20-JH") is True


def test_build_itc_register_intrastate_default():
    bills = [
        {"bill_date": "2026-04-10", "taxable_amount": 1000, "tax_amount": 50},
        {"bill_date": "2026-04-20", "taxable_amount": 2000, "tax_amount": 100},
        {"bill_date": "2026-05-02", "taxable_amount": 500, "tax_amount": 25},
    ]
    reg = build_itc_register(bills)
    assert reg["total_itc"] == 175.0
    assert reg["total_taxable"] == 3500.0
    assert reg["total_igst"] == 0.0
    apr = next(p for p in reg["periods"] if p["period"] == "2026-04")
    assert apr["tax"] == 150.0
    assert apr["cgst"] == 75.0 and apr["sgst"] == 75.0
    assert apr["igst"] == 0.0
    assert apr["bills"] == 2
    # newest period first
    assert reg["periods"][0]["period"] == "2026-05"


def test_build_itc_register_interstate_routes_to_igst():
    # Entity in Jharkhand (20). One MH bill (place_of_supply 27) -> IGST.
    bills = [
        {
            "bill_date": "2026-04-10",
            "taxable_amount": 1000,
            "tax_amount": 50,
            "place_of_supply": "20",  # same state -> intra
        },
        {
            "bill_date": "2026-04-15",
            "taxable_amount": 2000,
            "tax_amount": 100,
            "place_of_supply": "27",  # different state -> IGST
        },
    ]
    reg = build_itc_register(bills, entity_state="20")
    assert reg["total_itc"] == 150.0
    assert reg["total_cgst"] == 25.0     # 50/2 from the intra bill
    assert reg["total_sgst"] == 25.0
    assert reg["total_igst"] == 100.0    # full tax from the inter bill
    period = reg["periods"][0]
    assert period["cgst"] == 25.0
    assert period["sgst"] == 25.0
    assert period["igst"] == 100.0
    assert period["tax"] == 150.0  # total tax across both


def test_build_itc_register_no_entity_state_intrastate_fallback():
    # When entity_state is unknown, behave as before: no IGST routing.
    bills = [
        {
            "bill_date": "2026-04-10",
            "taxable_amount": 1000,
            "tax_amount": 100,
            "place_of_supply": "27",
        },
    ]
    reg = build_itc_register(bills, entity_state=None)
    assert reg["total_igst"] == 0.0
    assert reg["total_cgst"] == 50.0
    assert reg["total_sgst"] == 50.0


def test_reconcile_buckets_and_sum_identity():
    """Sum identity: safe + mismatch + at_risk == total booked ITC."""
    books = [
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "INV/001", "tax": 50,
         "bill_id": "b1", "vendor_name": "Alpha", "bill_date": "2026-04-10"},
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "INV-002", "tax": 80,
         "bill_id": "b2", "vendor_name": "Alpha", "bill_date": "2026-04-12"},
        {"gstin": "29AAGCB1234J1Z5", "invoice_no": "X9", "tax": 30,
         "bill_id": "b3", "vendor_name": "Beta", "bill_date": "2026-04-15"},
    ]
    gstr2b = [
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "inv001", "taxable": 1000, "tax": 50},
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "INV 002", "taxable": 1600, "tax": 90},
        {"gstin": "33AAAAA0000A1Z5", "invoice_no": "Z1", "taxable": 400, "tax": 20},
    ]
    r = reconcile_gstr2b(books, gstr2b, as_of_iso="2026-05-24")
    s = r["summary"]
    assert s["matched"] == 1
    assert s["mismatch"] == 1
    assert s["only_in_books"] == 1
    assert s["only_in_2b"] == 1
    assert s["itc_safe_to_claim"] == 50.0      # b1
    assert s["itc_in_mismatch"] == 80.0        # b2's BOOK tax (not the portal value)
    assert s["itc_at_risk"] == 30.0            # b3
    # Sum identity: 50 + 80 + 30 == 160 == total booked ITC.
    total = s["itc_safe_to_claim"] + s["itc_in_mismatch"] + s["itc_at_risk"]
    assert round(total, 2) == s["total_book_itc"]
    assert s["total_book_itc"] == 160.0
    assert r["only_in_books"][0]["vendor_name"] == "Beta"
    assert r["matched"][0]["bill_id"] == "b1"


def test_reconcile_empty():
    r = reconcile_gstr2b([], [])
    assert r["summary"]["matched"] == 0
    assert r["summary"]["total_book_itc"] == 0.0
    assert r["summary"]["itc_in_mismatch"] == 0.0
    assert r["only_in_2b"] == []


def test_reconcile_all_matched():
    """When nothing falls into mismatch/at_risk, mismatch+at_risk == 0 and
    safe == total."""
    books = [
        {"gstin": "27AAPFU0939F1ZV", "invoice_no": "A", "tax": 100,
         "bill_id": "b1", "vendor_name": "V"},
    ]
    g2b = [{"gstin": "27AAPFU0939F1ZV", "invoice_no": "A", "tax": 100, "taxable": 1000}]
    r = reconcile_gstr2b(books, g2b)
    s = r["summary"]
    assert s["itc_safe_to_claim"] == 100.0
    assert s["itc_in_mismatch"] == 0.0
    assert s["itc_at_risk"] == 0.0
    assert s["total_book_itc"] == 100.0


# ---------------------------------------------------------------------------
# D-3 regression: odd-paise tax must not drift (cgst + sgst == tax exactly)
# ---------------------------------------------------------------------------


def test_cgst_sgst_exact_sum_odd_paise_tax_501():
    """tax=5.01 (odd paise) -- pre-fix: 2.50+2.50=5.00 (drift -0.01).
    Post-fix residual trick: half=2.50, sgst=5.01-2.50=2.51, sum=5.01 exact."""
    bills = [{"bill_date": "2026-04-10", "taxable_amount": 100, "tax_amount": 5.01}]
    reg = build_itc_register(bills)
    period = reg["periods"][0]
    assert period["cgst"] + period["sgst"] == 5.01, (
        "cgst+sgst must equal tax exactly (no paisa drift)"
    )
    assert reg["total_cgst"] + reg["total_sgst"] == reg["total_itc"]


def test_cgst_sgst_exact_sum_odd_paise_tax_101():
    """tax=1.01 -- pre-fix: 0.51+0.51=1.02 (drift +0.01).
    Post-fix: half=0.51, sgst=1.01-0.51=0.50, sum=1.01 exact."""
    bills = [{"bill_date": "2026-04-10", "taxable_amount": 20, "tax_amount": 1.01}]
    reg = build_itc_register(bills)
    period = reg["periods"][0]
    assert period["cgst"] + period["sgst"] == 1.01, (
        "cgst+sgst must equal tax exactly (no paisa drift)"
    )
    assert reg["total_cgst"] + reg["total_sgst"] == reg["total_itc"]


def test_cgst_sgst_exact_even_paise_unaffected():
    """Even-paise tax (5.00) must still split exactly 2.50 + 2.50."""
    bills = [{"bill_date": "2026-04-10", "taxable_amount": 100, "tax_amount": 5.00}]
    reg = build_itc_register(bills)
    period = reg["periods"][0]
    assert period["cgst"] == 2.50
    assert period["sgst"] == 2.50
    assert period["cgst"] + period["sgst"] == 5.00


def test_cgst_sgst_accumulation_no_drift_multi_bills():
    """Multiple odd-paise bills -- accumulated cgst+sgst must equal total_itc."""
    bills = [
        {"bill_date": "2026-04-10", "taxable_amount": 100, "tax_amount": 5.01},
        {"bill_date": "2026-04-15", "taxable_amount": 20, "tax_amount": 1.01},
        {"bill_date": "2026-04-20", "taxable_amount": 50, "tax_amount": 2.55},
    ]
    reg = build_itc_register(bills)
    # Total tax = 5.01 + 1.01 + 2.55 = 8.57
    assert reg["total_itc"] == 8.57
    # Accumulated cgst+sgst must equal total_itc exactly.
    assert round(reg["total_cgst"] + reg["total_sgst"], 2) == reg["total_itc"]
