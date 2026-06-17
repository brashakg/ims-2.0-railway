"""
IMS 2.0 - Tests for backend/api/services/print_legal.py
========================================================

Pure-helper unit tests (no DB, no FastAPI client). Covers:
  - amount_in_words: zero, single, tens, hundred, thousand, lakh, crore,
    paise, float fractional, negative (refund), junk input
  - hsn_tax_summary: intra-state emits CGST+SGST not IGST; inter-state emits
    IGST not CGST/SGST; multi-rate-per-HSN groups correctly; missing data
    defaults intra-state
  - copy_marker_block: rule_48 (invoice) vs rule_55 (challan); X-mark slot;
    unknown copy_type -> ORIGINAL
  - statutory_footer: per-doc text; retain override; junk -> default
  - declarations: per-doc text; unknown -> empty string
  - format_date: datetime / ISO / "YYYY-MM-DD" / None / junk
  - LegalHeader: real entity + store data; overrides win; gstin-by-state
    routing; rx_card adds NCAHP/DMC; copy marker is wired
  - StaffHeader: minimal shape; no GSTIN/CIN; overrides win
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.print_legal import (  # noqa: E402
    LegalHeader,
    StaffHeader,
    amount_in_words,
    copy_marker_block,
    declarations,
    format_date,
    format_datetime_ist,
    hsn_tax_summary,
    statutory_footer,
)


# ---------------------------------------------------------------------------
# amount_in_words
# ---------------------------------------------------------------------------


def test_amount_in_words_zero():
    assert amount_in_words(0) == "Indian Rupees Zero Only"


def test_amount_in_words_single_digit():
    assert amount_in_words(7) == "Indian Rupees Seven Only"


def test_amount_in_words_teens_and_tens():
    assert amount_in_words(19) == "Indian Rupees Nineteen Only"
    assert amount_in_words(20) == "Indian Rupees Twenty Only"
    assert amount_in_words(99) == "Indian Rupees Ninety-Nine Only"


def test_amount_in_words_hundred_and_three_digits():
    assert amount_in_words(100) == "Indian Rupees One Hundred Only"
    assert amount_in_words(999) == "Indian Rupees Nine Hundred Ninety-Nine Only"


def test_amount_in_words_thousand_grouping():
    assert amount_in_words(1000) == "Indian Rupees One Thousand Only"
    assert (
        amount_in_words(28110)
        == "Indian Rupees Twenty-Eight Thousand One Hundred Ten Only"
    )
    assert amount_in_words(99999) == (
        "Indian Rupees Ninety-Nine Thousand Nine Hundred Ninety-Nine Only"
    )


def test_amount_in_words_one_lakh():
    assert amount_in_words(100000) == "Indian Rupees One Lakh Only"


def test_amount_in_words_lakh_thousand_hundred():
    # 12,34,567 -> Twelve Lakh Thirty-Four Thousand Five Hundred Sixty-Seven
    assert amount_in_words(1234567) == (
        "Indian Rupees Twelve Lakh Thirty-Four Thousand Five Hundred Sixty-Seven Only"
    )


def test_amount_in_words_one_crore():
    assert amount_in_words(10000000) == "Indian Rupees One Crore Only"


def test_amount_in_words_crore_lakh_thousand_residual():
    # 9,99,99,999 -> Ninety-Nine Lakh ... (sanity)
    out = amount_in_words(99999999)
    assert "Crore" in out
    assert "Lakh" in out
    assert "Thousand" in out


def test_amount_in_words_paise_explicit():
    # Explicit paise argument.
    assert amount_in_words(28110, 25) == (
        "Indian Rupees Twenty-Eight Thousand One Hundred Ten and Twenty-Five Paise Only"
    )


def test_amount_in_words_paise_from_float_fraction():
    # Same value as a float -> paise comes from the fractional part.
    assert amount_in_words(28110.25) == (
        "Indian Rupees Twenty-Eight Thousand One Hundred Ten and Twenty-Five Paise Only"
    )


def test_amount_in_words_paise_carry_into_rupee():
    # 99.999 should round to 100.00 (paise=0 again).
    assert amount_in_words(99.999) == "Indian Rupees One Hundred Only"


def test_amount_in_words_negative_refund():
    out = amount_in_words(-1500)
    assert out.startswith("Less: Indian Rupees ")
    assert "One Thousand Five Hundred" in out


def test_amount_in_words_string_input():
    assert amount_in_words("12345") == (
        "Indian Rupees Twelve Thousand Three Hundred Forty-Five Only"
    )


def test_amount_in_words_junk_input():
    assert amount_in_words("abc") == "Indian Rupees Zero Only"
    assert amount_in_words(None) == "Indian Rupees Zero Only"


def test_amount_in_words_paise_overflow_normalises():
    # 100 paise should carry to 1Rs.
    assert amount_in_words(0, paise=100) == "Indian Rupees One Only"


# ---------------------------------------------------------------------------
# hsn_tax_summary
# ---------------------------------------------------------------------------


def test_hsn_summary_intra_state_emits_cgst_sgst():
    items = [
        {"hsn_code": "900311", "taxable_value": 1000.0, "gst_rate": 5.0, "qty": 1},
        {"hsn_code": "900150", "taxable_value": 2000.0, "gst_rate": 5.0, "qty": 2},
    ]
    out = hsn_tax_summary(items, place_of_supply="07", supplier_state="07")
    assert out["interstate"] is False
    # Row1: 5% of 1000 = 50 -> CGST 25, SGST 25; IGST 0
    row1 = next(r for r in out["rows"] if r["hsn"] == "900311")
    assert row1["cgst"] == 25.0
    assert row1["sgst"] == 25.0
    assert row1["igst"] == 0.0
    assert row1["total_tax"] == 50.0
    # Row2: 5% of 2000 = 100 -> CGST 50, SGST 50; IGST 0
    # Totals sum across both rows: CGST = 25+50 = 75, SGST = 25+50 = 75
    assert out["totals"]["cgst"] == 75.0
    assert out["totals"]["sgst"] == 75.0
    assert out["totals"]["igst"] == 0.0
    assert out["totals"]["taxable"] == 3000.0


def test_hsn_summary_inter_state_emits_igst_only():
    items = [
        {"hsn_code": "900311", "taxable_value": 1000.0, "gst_rate": 5.0},
    ]
    out = hsn_tax_summary(items, place_of_supply="27", supplier_state="07")
    assert out["interstate"] is True
    row = out["rows"][0]
    assert row["igst"] == 50.0
    assert row["cgst"] == 0.0
    assert row["sgst"] == 0.0
    assert out["totals"]["igst"] == 50.0
    assert out["totals"]["cgst"] == 0.0


def test_hsn_summary_groups_same_hsn_same_rate():
    items = [
        {"hsn_code": "900311", "taxable_value": 1000.0, "gst_rate": 5.0, "qty": 1},
        {"hsn_code": "900311", "taxable_value": 500.0, "gst_rate": 5.0, "qty": 1},
    ]
    out = hsn_tax_summary(items, place_of_supply="07", supplier_state="07")
    assert len(out["rows"]) == 1
    assert out["rows"][0]["taxable"] == 1500.0
    assert out["rows"][0]["line_count"] == 2
    assert out["rows"][0]["qty"] == 2.0


def test_hsn_summary_splits_same_hsn_different_rate():
    # Edge case: a single HSN sold at two rates within one bill (tax-holiday
    # line). Group key is (hsn, rate) so we get two rows.
    items = [
        {"hsn_code": "900311", "taxable_value": 1000.0, "gst_rate": 5.0},
        {"hsn_code": "900311", "taxable_value": 1000.0, "gst_rate": 0.0},
    ]
    out = hsn_tax_summary(items, place_of_supply="07", supplier_state="07")
    assert len(out["rows"]) == 2
    rates = sorted(r["rate"] for r in out["rows"])
    assert rates == [0.0, 5.0]


def test_hsn_summary_missing_place_defaults_intra_state():
    items = [{"hsn_code": "900311", "taxable_value": 100.0, "gst_rate": 18.0}]
    out = hsn_tax_summary(items)
    assert out["interstate"] is False
    assert out["totals"]["cgst"] > 0
    assert out["totals"]["igst"] == 0.0


def test_hsn_summary_ignores_non_dict_items():
    items = [None, "junk", 42, {"hsn_code": "9003", "taxable_value": 100, "gst_rate": 5.0}]
    out = hsn_tax_summary(items)
    assert len(out["rows"]) == 1


def test_hsn_summary_alt_keys():
    # Accept "taxable" / "rate" / "hsn" alternates.
    items = [{"hsn": "900150", "taxable": 200.0, "rate": 5.0, "quantity": 1}]
    out = hsn_tax_summary(items, place_of_supply="07", supplier_state="07")
    assert out["rows"][0]["hsn"] == "900150"
    assert out["rows"][0]["taxable"] == 200.0


def test_hsn_summary_gstin_routes_inter_state():
    # GSTIN starting with "07" (Delhi) -> intra-state when supplier is Delhi.
    items = [{"hsn_code": "9003", "taxable_value": 100.0, "gst_rate": 5.0}]
    out = hsn_tax_summary(items, place_of_supply="27AABCB1234M1Z5", supplier_state="07")
    assert out["interstate"] is True


# ---------------------------------------------------------------------------
# copy_marker_block
# ---------------------------------------------------------------------------


def test_copy_marker_rule_48_default():
    out = copy_marker_block()
    assert out["mode"] == "rule_48"
    assert out["active"] == "ORIGINAL FOR RECIPIENT"
    assert out["active_index"] == 0
    assert out["marks"] == ["X", " ", " "]
    assert "ORIGINAL FOR RECIPIENT (X)" in out["rendered"]
    assert "TRIPLICATE FOR SUPPLIER ( )" in out["rendered"]


def test_copy_marker_rule_48_duplicate():
    out = copy_marker_block("DUPLICATE")
    assert out["active"] == "DUPLICATE FOR TRANSPORTER"
    assert out["marks"] == [" ", "X", " "]


def test_copy_marker_rule_48_triplicate():
    out = copy_marker_block("TRIPLICATE")
    assert out["active"] == "TRIPLICATE FOR SUPPLIER"
    assert out["marks"] == [" ", " ", "X"]


def test_copy_marker_rule_55_uses_consignee_consignor():
    out = copy_marker_block("ORIGINAL", mode="rule_55")
    assert out["mode"] == "rule_55"
    assert "CONSIGNEE" in out["active"]
    assert "TRIPLICATE FOR CONSIGNOR" in out["rendered"]


def test_copy_marker_unknown_falls_back_to_original():
    out = copy_marker_block("QUADRUPLICATE")
    assert out["active_index"] == 0


# ---------------------------------------------------------------------------
# statutory_footer
# ---------------------------------------------------------------------------


def test_statutory_footer_tax_invoice():
    out = statutory_footer("tax_invoice")
    assert "Sec. 31" in out
    assert "Rule 46" in out
    assert "7 years" in out
    assert "Rule 56" in out


def test_statutory_footer_credit_note():
    out = statutory_footer("credit_note")
    assert "Sec. 34(1)" in out
    assert "Rule 53" in out


def test_statutory_footer_debit_note():
    out = statutory_footer("debit_note")
    assert "Sec. 34(3)" in out


def test_statutory_footer_z_report():
    out = statutory_footer("z_report")
    assert "SOP-FIN-02" in out


def test_statutory_footer_custom_retention():
    out = statutory_footer("tax_invoice", retain_years=5)
    assert "5 years" in out


def test_statutory_footer_unknown_falls_back():
    out = statutory_footer("widget")
    assert "Rule 56" in out


def test_statutory_footer_junk_retain_recovers():
    out = statutory_footer("tax_invoice", retain_years=None)  # type: ignore[arg-type]
    assert "7 years" in out


# ---------------------------------------------------------------------------
# declarations
# ---------------------------------------------------------------------------


def test_declarations_tax_invoice():
    out = declarations("tax_invoice")
    assert "actual price" in out
    assert "true and correct" in out


def test_declarations_credit_note():
    out = declarations("credit_note")
    assert "Section 34(1)" in out


def test_declarations_rx_card_brand_neutrality():
    out = declarations("rx_card")
    assert "any registered optician" in out
    assert "consideration" in out


def test_declarations_unknown_is_empty():
    assert declarations("widget") == ""


# ---------------------------------------------------------------------------
# format_date / format_datetime_ist
# ---------------------------------------------------------------------------


def test_format_date_datetime_input():
    assert format_date(datetime(2026, 4, 19)) == "19-Apr-2026"


def test_format_date_iso_input():
    assert format_date("2026-04-19") == "19-Apr-2026"
    assert format_date("2026-04-19T12:34:56") == "19-Apr-2026"
    assert format_date("2026-04-19T12:34:56Z") == "19-Apr-2026"


def test_format_date_junk_input():
    assert format_date(None) == ""
    assert format_date("") == ""
    assert format_date("not-a-date") == ""


def test_format_datetime_ist_label():
    out = format_datetime_ist(datetime(2026, 4, 19, 14, 22))
    assert out == "19-Apr-2026 14:22 IST"


# ---------------------------------------------------------------------------
# LegalHeader / StaffHeader
# ---------------------------------------------------------------------------


def _entity_fixture():
    """Real-looking but never-shipped fixture entity (no mock identities from
    the design system — invented for tests only)."""
    return {
        "name": "Acme Optical",
        "legal_name": "Acme Optical Private Limited",
        "pan": "AAACA1234A",
        "cin": "U33200JH2018PTC001234",
        "registered_address": "Plot 1, Industrial Area, Ranchi 834001",
        "registered_phone": "+91 651 999 0000",
        "registered_email": "ops@acmeoptical.test",
        "website": "acmeoptical.test",
        "logo_url": "",
        "gstins": [
            {
                "gstin": "20AAACA1234A1Z5",
                "state_code": "20",
                "state_name": "Jharkhand",
                "is_primary": True,
            },
            {
                "gstin": "27AAACA1234A1Z5",
                "state_code": "27",
                "state_name": "Maharashtra",
                "is_primary": False,
            },
        ],
    }


def _store_fixture(state_code="20"):
    return {
        "name": "Acme Ranchi Main Road",
        "store_code": "AC-RAN-01",
        "address": "Shop 14, Main Road",
        "city": "Ranchi",
        "state": "Jharkhand",
        "state_code": state_code,
        "pincode": "834001",
        "phone": "+91 651 123 4567",
        "email": "ranchi@acmeoptical.test",
    }


def test_legal_header_basic_shape():
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="tax_invoice",
        doc_number="AC/2025-26/0001",
        doc_date=datetime(2026, 4, 19),
    )
    assert out["doc_type"] == "tax_invoice"
    assert out["doc_number"] == "AC/2025-26/0001"
    assert out["doc_date"] == "19-Apr-2026"
    assert out["legal_name"] == "Acme Optical Private Limited"
    assert out["trade_name"] == "Acme Optical"
    assert out["gstin"] == "20AAACA1234A1Z5"
    assert out["state_code"] == "20"
    assert out["copy_marker"]["active"] == "ORIGINAL FOR RECIPIENT"
    assert out["reverse_charge"] is False
    # Meta strip is present
    meta_keys = [k for k, _ in out["meta"]]
    assert "Document No." in meta_keys
    assert "Date" in meta_keys
    # Supplier key-value table includes the statutory fields
    kv_keys = [k for k, _ in out["supplier_kv"]]
    for k in ("GSTIN / UIN", "PAN", "CIN", "Registered office"):
        assert k in kv_keys


def test_legal_header_picks_gstin_for_store_state():
    # Maharashtra store gets the Maharashtra GSTIN.
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(state_code="27"),
        doc_type="tax_invoice",
    )
    assert out["gstin"] == "27AAACA1234A1Z5"
    assert out["state_code"] == "27"


def test_legal_header_overrides_win():
    overrides = {
        "header_subtitle": "Eyewear since 2014",
        "signatory_name": "Avinash Kumar",
        "signatory_designation": "Director",
        "drug_licence_no": "DL-AC-RAN-2018-0001",
        "footer_terms": "Net 30. Subject to Ranchi jurisdiction.",
        "retention_years": 8,
    }
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="tax_invoice",
        overrides=overrides,
    )
    assert out["header_subtitle"] == "Eyewear since 2014"
    assert out["signatory_name"] == "Avinash Kumar"
    assert out["signatory_designation"] == "Director"
    assert out["footer_terms"] == "Net 30. Subject to Ranchi jurisdiction."
    assert out["retention_years"] == 8
    # Drug licence flows into the supplier KV table.
    kv = dict(out["supplier_kv"])
    assert kv.get("Drug Licence") == "DL-AC-RAN-2018-0001"


def test_legal_header_rx_card_adds_ncahp_and_dmc():
    overrides = {
        "ncahp_uid": "OPT-IN-22-04412",
        "dmc_reg": "DMC/R-4412/2014",
        "drug_licence_no": "DL-CL-RAN-2018-0001",
    }
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="rx_card",
        overrides=overrides,
    )
    kv = dict(out["supplier_kv"])
    assert kv.get("NCAHP UID") == "OPT-IN-22-04412"
    assert kv.get("State Council Reg") == "DMC/R-4412/2014"


def test_legal_header_empty_override_is_ignored():
    # An empty-string override must NOT blank a real default.
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="tax_invoice",
        overrides={"signatory_designation": ""},
    )
    assert out["signatory_designation"] == "Authorised Signatory"


def test_legal_header_copy_marker_duplicate():
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="tax_invoice",
        copy_marker="DUPLICATE",
    )
    assert out["copy_marker"]["active"] == "DUPLICATE FOR TRANSPORTER"


def test_legal_header_reverse_charge_flag():
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="tax_invoice",
        reverse_charge=True,
    )
    rc_row = next(v for k, v in out["meta"] if k == "Reverse Charge")
    assert rc_row == "Yes"


def test_legal_header_extra_meta_appended():
    out = LegalHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="tax_invoice",
        extra_meta=[("PO Ref", "PO-991")],
    )
    meta = dict(out["meta"])
    assert meta.get("PO Ref") == "PO-991"


def test_legal_header_fail_soft_on_empty_inputs():
    out = LegalHeader(None, None, doc_type="tax_invoice")
    assert isinstance(out, dict)
    assert out["legal_name"] == ""
    assert out["gstin"] == ""
    assert out["copy_marker"]["active"] == "ORIGINAL FOR RECIPIENT"


def test_staff_header_basic_shape():
    out = StaffHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="job_card",
        doc_number="JOB-0001",
        doc_date=datetime(2026, 4, 19),
    )
    assert out["doc_type"] == "job_card"
    assert out["doc_number"] == "JOB-0001"
    assert out["doc_date"] == "19-Apr-2026"
    assert "Acme Ranchi Main Road" in out["branch_label"]
    # Staff header MUST NOT carry GSTIN / CIN keys
    assert "gstin" not in out
    assert "supplier_kv" not in out
    # Internal mode -> rendered copy marker is the INTERNAL pseudo strip
    assert out["copy_marker"]["mode"] == "internal"


def test_staff_header_overrides_win():
    out = StaffHeader(
        _entity_fixture(),
        _store_fixture(),
        doc_type="z_report",
        overrides={"footer_terms": "Manager sign required > Rs 200 variance"},
    )
    assert out["footer_terms"].startswith("Manager sign required")


def test_staff_header_fail_soft_on_empty_inputs():
    out = StaffHeader(None, None, doc_type="z_report")
    assert isinstance(out, dict)
    assert out["trade_name"] == ""
    assert out["branch_label"] == ""


# ---------------------------------------------------------------------------
# STORE-SPECIFIC printouts: two different stores / entities must produce two
# different headers (store name + GSTIN). This is the core guarantee for the
# multi-store / multi-GSTIN owner -- a printed document carries the ISSUING
# store's identity, never one shared hardcoded company block.
# ---------------------------------------------------------------------------


def _entity_better_vision():
    return {
        "name": "Better Vision",
        "legal_name": "Better Vision Opticals Private Limited",
        "pan": "AAACB1111A",
        "registered_address": "HQ, Bokaro 827001",
        "gstins": [
            {
                "gstin": "20AAACB1111A1Z5",
                "state_code": "20",
                "state_name": "Jharkhand",
                "is_primary": True,
            },
        ],
    }


def _entity_wizopt():
    return {
        "name": "WizOpt",
        "legal_name": "WizOpt Eyewear LLP",
        "pan": "AAACW2222B",
        "registered_address": "HQ, Pune 411001",
        "gstins": [
            {
                "gstin": "27AAACW2222B1Z9",
                "state_code": "27",
                "state_name": "Maharashtra",
                "is_primary": True,
            },
        ],
    }


def test_two_stores_two_headers_and_gstins():
    """A sale at Better Vision Bokaro and a sale at WizOpt Pune must each print
    THAT store's name + entity GSTIN -- not one shared block."""
    bv_store = {
        "name": "Better Vision - Bokaro City Centre",
        "store_code": "BV-BOK-01",
        "address": "Shop 5, City Centre",
        "city": "Bokaro",
        "state": "Jharkhand",
        "state_code": "20",
        "pincode": "827004",
    }
    wo_store = {
        "name": "WizOpt - Pune FC Road",
        "store_code": "WO-PUN-01",
        "address": "12, FC Road",
        "city": "Pune",
        "state": "Maharashtra",
        "state_code": "27",
        "pincode": "411004",
    }

    bv = LegalHeader(_entity_better_vision(), bv_store, doc_type="tax_invoice")
    wo = LegalHeader(_entity_wizopt(), wo_store, doc_type="tax_invoice")

    # Store name on the document is the issuing store's, not shared.
    assert bv["store_name"] == "Better Vision - Bokaro City Centre"
    assert wo["store_name"] == "WizOpt - Pune FC Road"
    assert bv["store_name"] != wo["store_name"]

    # Legal/trade identity differs per entity.
    assert bv["legal_name"] == "Better Vision Opticals Private Limited"
    assert wo["legal_name"] == "WizOpt Eyewear LLP"
    assert bv["trade_name"] != wo["trade_name"]

    # GSTIN on the tax invoice is the issuing entity's registration for that
    # store's state -- two different GSTINs.
    assert bv["gstin"] == "20AAACB1111A1Z5"
    assert wo["gstin"] == "27AAACW2222B1Z9"
    assert bv["gstin"] != wo["gstin"]


def test_no_hardcoded_brand_fallback_in_header():
    """With no entity/store data, the header must NOT invent a brand name --
    it returns empty strings the renderer hides, never 'Better Vision'."""
    out = LegalHeader(None, None, doc_type="tax_invoice")
    assert out["legal_name"] == ""
    assert out["trade_name"] == ""
    assert out["store_name"] == ""
    assert out["gstin"] == ""
    # Defensive: no brand string leaked into any header value.
    blob = " ".join(str(v) for v in out.values())
    assert "Better Vision" not in blob
    assert "WizOpt" not in blob


def test_rendered_challan_uses_passed_store_not_brand():
    """The server-rendered delivery challan HTML must show the passed store's
    identity, and a different store must change the rendered output."""
    from api.services.print_render import render_delivery_challan

    bv_html = render_delivery_challan(
        entity=_entity_better_vision(),
        store={
            "name": "Better Vision - Bokaro City Centre",
            "address": "Shop 5, City Centre",
            "city": "Bokaro",
            "state": "Jharkhand",
            "state_code": "20",
            "pincode": "827004",
        },
        challan_number="BV/DC/0001",
        challan_date=datetime(2026, 4, 19),
        items=[{"name": "Frame", "qty": 1, "hsn_code": "9003"}],
    )
    wo_html = render_delivery_challan(
        entity=_entity_wizopt(),
        store={
            "name": "WizOpt - Pune FC Road",
            "address": "12, FC Road",
            "city": "Pune",
            "state": "Maharashtra",
            "state_code": "27",
            "pincode": "411004",
        },
        challan_number="WO/DC/0001",
        challan_date=datetime(2026, 4, 19),
        items=[{"name": "Frame", "qty": 1, "hsn_code": "9003"}],
    )
    assert "Better Vision - Bokaro City Centre" in bv_html
    assert "WizOpt - Pune FC Road" in wo_html
    # The two stores' challans differ -- neither leaks the other's identity.
    assert "WizOpt - Pune FC Road" not in bv_html
    assert "Better Vision - Bokaro City Centre" not in wo_html
