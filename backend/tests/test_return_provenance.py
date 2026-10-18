"""Original booked purchase evidence for returns; isolated in-memory Mongo.

This foundation is read-only. Route/posting integration is tested separately;
these tests do not claim that an unused helper fixes the existing issue engine.
"""
from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock

import mongomock
import pytest

from api.services.return_provenance import (
    ReturnProvenanceError, resolve_request_provenance, resolve_return_provenance,
)


@pytest.fixture(autouse=True)
def _isolate_db():
    """No application startup, network or shared test database."""
    yield


@pytest.fixture
def db():
    return mongomock.MongoClient().get_database("return_provenance")


def receipt(db, gid="G1", *, qty=4, product="P1", **extra):
    row = {
        "grn_id": gid, "vendor_id": "V1", "store_id": "S1", "status": "ACCEPTED",
        "items": [{"product_id": product, "accepted_qty": qty}],
        **extra,
    }
    db.grns.insert_one(row)
    return row


def line(*, qty=4, taxable=400, cgst=10, sgst=10, igst=0, **extra):
    return {
        "product_id": "P1", "qty": qty, "taxable": taxable, "gst_rate": 5,
        "cgst": cgst, "sgst": sgst, "igst": igst,
        "line_total": taxable + cgst + sgst + igst, **extra,
    }


def bill(db, bid="B1", *, gid="G1", **extra):
    row = {
        "bill_id": bid, "grn_id": gid, "vendor_id": "V1", "store_id": "S1",
        "status": "UNPAID", "received": True, "doc_type": "PURCHASE_INVOICE",
        "bill_number": f"INV-{bid}", "bill_date": datetime(2026, 1, 1),
        "recipient_entity_id": "E1", "recipient_gstin": "20AAAAA0000A1Z5",
        "vendor_gstin": "20BBBBB0000B1Z5", "lines": [line()], **extra,
    }
    db.vendor_bills.insert_one(row)
    return row


def unit(db, sid="U1", *, gid="G1", **extra):
    row = {
        "stock_id": sid, "product_id": "P1", "rtv_vendor_id": "R1",
        "store_id": "S1", "grn_id": gid, "source_type": "GRN", "source_id": gid,
        **extra,
    }
    db.stock_units.insert_one(row)
    return row


def returned(qty=1, **extra):
    return {
        "return_id": "R1", "vendor_id": "V1", "store_id": "S1",
        "items": [{"product_id": "P1", "quantity": qty}], **extra,
    }


def selection(qty=1, *, gid="G1", bid="B1", index=0, return_index=0, **extra):
    return {
        "return_line_index": return_index, "grn_id": gid, "quantity": qty,
        "bill_id": bid, "bill_line_index": index, **extra,
    }


def pending(code, db, return_doc=None, **kwargs):
    with pytest.raises(ReturnProvenanceError) as exc:
        resolve_return_provenance(db, return_doc or returned(), **kwargs)
    assert exc.value.code == code


def test_linked_old_bill_wins_new_receipts_and_current_master_tax(db):
    receipt(db)
    bill(db)
    unit(db)
    receipt(db, "G2", status="PENDING", created_at="2026-10-18")
    bill(db, "B2", gid="G2", lines=[line(cgst=0, sgst=0, igst=72, gst_rate=18)])
    db.products.insert_one({"product_id": "P1", "gst_rate": 18})
    db.vendors.insert_one({"vendor_id": "V1", "gstin": "27CHANGED0000X1Z5"})
    result = resolve_return_provenance(db, returned())
    assert [(r["bill_id"], r["grn_id"], r["stock_ids"]) for r in result] == [("B1", "G1", ["U1"])]
    assert result[0]["gst_rate"] == 5
    assert result[0]["tax_cap_paise"] == {"cgst": 250, "sgst": 250, "igst": 0}
    assert result[0]["supplier_amount_cap_paise"] == 10500
    assert result[0]["recipient_gstin"] == "20AAAAA0000A1Z5"


def test_same_product_units_from_different_receipts_keep_each_original_tax_head(db):
    receipt(db)
    bill(db)
    unit(db)
    receipt(db, "G2")
    bill(db, "B2", gid="G2", lines=[line(cgst=0, sgst=0, igst=72, gst_rate=18)])
    unit(db, "U2", gid="G2")
    result = resolve_return_provenance(db, returned(2))
    assert {r["bill_id"]: r["tax_cap_paise"] for r in result} == {
        "B1": {"cgst": 250, "sgst": 250, "igst": 0},
        "B2": {"cgst": 0, "sgst": 0, "igst": 1800},
    }
    assert sum(r["supplier_amount_cap_paise"] for r in result) == 22300


def test_unique_accepted_origin_ignores_pending_and_void_receipts(db):
    receipt(db)
    bill(db)
    receipt(db, "G2", status="PENDING")
    receipt(db, "G3", status="VOID")
    assert resolve_return_provenance(db, returned())[0]["grn_id"] == "G1"


def test_unlinked_second_accepted_unbilled_receipt_is_still_ambiguous(db):
    receipt(db)
    bill(db)
    receipt(db, "G2")
    pending("AMBIGUOUS_RECEIPT", db)
    resolved = resolve_return_provenance(db, returned(), selections=[selection()])
    assert resolved[0]["bill_id"] == "B1"


@pytest.mark.parametrize("change", [{}, {"status": "PENDING"}, {"received": False}, {"source_transfer_id": "T1"}])
def test_no_booked_origin_cannot_borrow_current_vendor_eligibility(db, change):
    receipt(db)
    if change:
        bill(db, **change)
    db.vendors.insert_one({"vendor_id": "V1", "gstin": "20BBBBB0000B1Z5"})
    pending("UNBOOKED_ORIGIN", db)


@pytest.mark.parametrize("change", [{"itc_eligible": False}, {"itc_blocked": True}])
def test_ineligible_original_keeps_gross_supplier_amount_but_no_reversal(db, change):
    receipt(db)
    bill(db, **change)
    origin = resolve_return_provenance(db, returned())[0]
    assert origin["claims_credit"] is False
    assert origin["itc_cap_paise"] == {"cgst": 0, "sgst": 0, "igst": 0}
    assert origin["supplier_amount_cap_paise"] == 10500


def test_reverse_charge_supplier_cap_excludes_self_paid_tax(db):
    receipt(db)
    bill(db, reverse_charge=True)
    origin = resolve_return_provenance(db, returned())[0]
    assert origin["supplier_amount_cap_paise"] == 10000
    assert origin["itc_cap_paise"] == {"cgst": 250, "sgst": 250, "igst": 0}


def test_transfer_uses_immutable_original_grn_not_transfer_source(db):
    receipt(db, store_id="ORIGINAL-SHOP")
    bill(db, store_id="ORIGINAL-SHOP")
    unit(db, source_type="TRANSFER", source_id="NEW-TRANSFER")
    origin = resolve_return_provenance(db, returned())[0]
    assert origin["store_id"] == "ORIGINAL-SHOP"
    assert origin["grn_id"] == "G1"


def test_legacy_unit_grn_source_is_supported_but_transfer_id_is_not_a_receipt(db):
    receipt(db)
    bill(db)
    unit(db, grn_id=None)
    assert resolve_return_provenance(db, returned())[0]["grn_id"] == "G1"
    db.stock_units.update_one({}, {"$set": {"source_type": "TRANSFER"}})
    pending("UNIT_ORIGIN_MISSING", db)


@pytest.mark.parametrize("ret,unit_change,code", [
    (returned(2), {}, "INCOMPLETE_UNIT_LINKS"),
    (returned(stock_ids=["U1", "U2"]), {}, "INCOMPLETE_UNIT_LINKS"),
    (returned(grn_id="G2"), {}, "CONFLICTING_RECEIPT"),
    (returned(), {"store_id": "OTHER"}, "INVALID_UNIT_OWNER"),
    (returned(), {"product_id": "OTHER"}, "UNEXPECTED_LINKED_UNIT"),
    (returned(), {"receipt_disposition": "REJECTED"}, "REJECTED_ORIGIN_UNBILLED"),
])
def test_physical_links_are_complete_and_rejected_custody_is_not_billed_evidence(db, ret, unit_change, code):
    receipt(db)
    bill(db)
    unit(db, **unit_change)
    pending(code, db, ret)


@pytest.mark.parametrize("extra", [{"vendor_id": "OTHER"}, {"store_id": "OTHER"}, {"status": "PENDING"}])
def test_explicit_selection_cannot_cross_supplier_shop_or_pending_receipt(db, extra):
    receipt(db, **extra)
    bill(db)
    pending("INVALID_RECEIPT_OWNER", db, selections=[selection()])


def test_selected_receipt_cannot_replace_linked_physical_origin(db):
    receipt(db)
    bill(db)
    unit(db)
    receipt(db, "G2")
    bill(db, "B2", gid="G2")
    pending("SELECTION_QUANTITY_MISMATCH", db, selections=[selection(gid="G2", bid="B2")])


def test_multiple_bill_lines_require_explicit_selection(db):
    receipt(db)
    bill(db, lines=[line(), line(cgst=0, sgst=0, igst=72, gst_rate=18)])
    pending("AMBIGUOUS_BILL_LINE", db)
    origin = resolve_return_provenance(db, returned(), selections=[selection(index=1)])[0]
    assert origin["bill_line_index"] == 1
    assert origin["tax_cap_paise"]["igst"] == 1800


def test_multiple_bills_cannot_choose_first_and_selection_cannot_name_unrelated_bill(db):
    receipt(db)
    bill(db)
    bill(db, "B2")
    pending("AMBIGUOUS_BILL_LINE", db)
    pending("UNBOOKED_ORIGIN", db, selections=[selection(bid="UNRELATED")])
    assert resolve_return_provenance(db, returned(), selections=[selection(bid="B2")])[0]["bill_id"] == "B2"


def test_same_receipt_split_across_bills_needs_exact_unit_selections(db):
    receipt(db)
    bill(db)
    bill(db, "B2")
    unit(db)
    unit(db, "U2")
    choices = [selection(), selection(bid="B2")]
    pending("EXPLICIT_UNIT_SELECTION_REQUIRED", db, returned(2), selections=choices)
    choices[0]["stock_ids"] = ["U1"]
    choices[1]["stock_ids"] = ["U2"]
    result = resolve_return_provenance(db, returned(2), selections=choices)
    assert [(r["bill_id"], r["stock_ids"]) for r in result] == [("B1", ["U1"]), ("B2", ["U2"])]
    choices[1]["stock_ids"] = ["U1"]
    pending("EXPLICIT_UNIT_SELECTION_REQUIRED", db, returned(2), selections=choices)


def test_repeated_product_return_lines_require_disjoint_explicit_unit_origins(db):
    receipt(db)
    bill(db)
    unit(db)
    unit(db, "U2")
    ret = returned(items=[{"product_id": "P1", "quantity": 1}, {"product_id": "P1", "quantity": 1}])
    pending("DUPLICATE_RETURN_PRODUCT", db, ret)
    choices = [selection(stock_ids=["U1"]), selection(return_index=1, stock_ids=["U2"])]
    result = resolve_return_provenance(db, ret, selections=choices)
    assert [r["return_line_index"] for r in result] == [0, 1]
    assert [r["stock_ids"] for r in result] == [["U1"], ["U2"]]


def test_original_bill_quantity_cap_applies_across_consolidated_receipts(db):
    receipt(db, qty=3)
    receipt(db, "G2", qty=3)
    bill(db, gid=None, linked_dc_ids=["G1", "G2"], lines=[line(qty=4)])
    pending("ORIGINAL_QUANTITY_EXCEEDED", db, returned(6), selections=[selection(3), selection(3, gid="G2")])


def test_receipt_quantity_cap_applies_across_different_bill_selections(db):
    receipt(db, qty=3)
    bill(db)
    bill(db, "B2")
    pending("RECEIPT_QUANTITY_EXCEEDED", db, returned(4), selections=[selection(2), selection(2, bid="B2")])


def test_split_receipts_do_not_round_same_original_paise_up_twice(db):
    receipt(db, qty=1)
    receipt(db, "G2", qty=1)
    bill(db, gid=None, linked_dc_ids=["G1", "G2"], lines=[line(qty=2, taxable=.01, cgst=.01, sgst=.01)])
    result = resolve_return_provenance(db, returned(2), selections=[selection(), selection(gid="G2")])
    assert sum(r["taxable_cap_paise"] for r in result) == 1
    assert sum(r["tax_cap_paise"]["cgst"] for r in result) == 1
    assert sum(r["tax_cap_paise"]["sgst"] for r in result) == 1
    assert sum(r["supplier_amount_cap_paise"] for r in result) == 3


@pytest.mark.parametrize("extra,code", [
    ({"igst": 2}, "AMBIGUOUS_TAX_HEADS"),
    ({"line_total": 1}, "AMBIGUOUS_ORIGIN_AMOUNT"),
    ({"cgst": None}, "INVALID_ORIGIN_AMOUNT"),
    ({"taxable": float("nan")}, "INVALID_ORIGIN_AMOUNT"),
    ({"taxable": -1}, "INVALID_ORIGIN_AMOUNT"),
])
def test_invalid_original_amounts_never_become_current_catalogue_tax(db, extra, code):
    receipt(db)
    original = line()
    original.update(extra)
    bill(db, lines=[original])
    pending(code, db)


def test_header_only_bill_stays_pending_without_a_billed_quantity(db):
    receipt(db)
    bill(db, lines=[], taxable_amount=100, tax_amount=5, cgst_total=2.5, sgst_total=2.5, igst_total=0)
    pending("HEADER_BREAKUP_REQUIRED", db)
    bill(db, "DETAILED")
    result = resolve_return_provenance(db, returned(), selections=[selection(bid="DETAILED")])
    assert result[0]["bill_id"] == "DETAILED"


def test_snapshot_changes_when_original_tax_or_credit_eligibility_changes(db):
    receipt(db)
    bill(db)
    initial = resolve_return_provenance(db, returned())[0]["bill_line_fingerprint"]
    db.vendor_bills.update_one({}, {"$set": {"itc_eligible": False}})
    blocked = resolve_return_provenance(db, returned())[0]["bill_line_fingerprint"]
    assert blocked != initial
    db.vendor_bills.update_one({}, {"$set": {"lines.0.cgst": 12, "lines.0.line_total": 422}})
    changed = resolve_return_provenance(db, returned())[0]["bill_line_fingerprint"]
    assert changed != blocked


def test_database_read_failure_propagates_without_guessed_figures():
    broken = Mock()
    broken.get_collection.side_effect = RuntimeError("read unavailable")
    with pytest.raises(RuntimeError, match="read unavailable"):
        resolve_return_provenance(broken, returned())


def test_resolution_is_read_only(db):
    receipt(db)
    bill(db)
    unit(db)
    before = {name: deepcopy(list(db[name].find())) for name in db.list_collection_names()}
    resolve_return_provenance(db, returned())
    assert {name: list(db[name].find()) for name in db.list_collection_names()} == before


def test_original_hsn_is_carried_without_catalogue_enrichment(db):
    receipt(db)
    bill(db, lines=[line(hsn="9003", description="Originally billed frame")])
    db.products.insert_one({"product_id": "P1", "hsn": "9004"})
    origin = resolve_return_provenance(db, returned())[0]
    assert (origin["hsn"], origin["description"]) == ("9003", "Originally billed frame")


@pytest.mark.parametrize("choice", [None, selection(index=False), selection(index=-1), selection(index="0"), selection(return_index=True)])
def test_invalid_source_selection_cannot_alias_a_real_bill_line(db, choice):
    receipt(db)
    bill(db)
    pending("INVALID_SELECTION", db, selections=[choice])


@pytest.mark.parametrize("qty", [0, -1, .5, float("inf"), None])
def test_invalid_return_quantity_is_never_coerced_to_one(db, qty):
    receipt(db)
    bill(db)
    with pytest.raises(ReturnProvenanceError):
        resolve_return_provenance(db, returned(qty))


def test_unique_unbilled_request_has_custody_but_no_invented_financial_evidence(db):
    receipt(db)
    result = resolve_request_provenance(db, returned())
    assert result == [{
        "origin_status": "UNBOOKED", "return_line_index": 0, "product_id": "P1",
        "quantity": 1, "grn_id": "G1", "stock_ids": [], "store_id": "S1", "vendor_id": "V1",
    }]
    pending("UNBOOKED_ORIGIN", db)


def test_internal_request_does_not_guess_between_accepted_origins(db):
    receipt(db)
    receipt(db, "G2")
    with pytest.raises(ReturnProvenanceError) as exc:
        resolve_request_provenance(db, returned())
    assert exc.value.code == "AMBIGUOUS_RECEIPT"


def test_internal_request_cannot_turn_a_wrong_explicit_bill_into_unbilled_goods(db):
    receipt(db)
    with pytest.raises(ReturnProvenanceError) as exc:
        resolve_request_provenance(db, returned(), selections=[selection(bid="MISSING")])
    assert exc.value.code == "UNBOOKED_ORIGIN"


def rejected(db):
    receipt(db, qty=0, items=[{"product_id": "P1", "accepted_qty": 0, "rejected_qty": 1}])
    unit(db, receipt_disposition="REJECTED", grn_line_index=0, quantity=1)
    return returned(receipt_rejection=True, grn_id="G1", grn_line_index=0, stock_ids=["U1"])


def test_a08_rejected_goods_can_have_internal_request_without_accepted_quantity_or_bill(db):
    ret = rejected(db)
    result = resolve_request_provenance(db, ret)
    assert result == [{
        "origin_status": "REJECTED_CUSTODY", "return_line_index": 0, "product_id": "P1", "quantity": 1,
        "grn_id": "G1", "grn_line_index": 0, "stock_ids": ["U1"], "store_id": "S1", "vendor_id": "V1",
    }]
    pending("REJECTED_ORIGIN_UNBILLED", db, ret)


@pytest.mark.parametrize("field,value", [
    ("receipt_disposition", "ACCEPTED"), ("grn_id", "WRONG"), ("grn_line_index", 1),
    ("product_id", "WRONG"), ("store_id", "WRONG"), ("quantity", 2),
])
def test_a08_request_requires_exact_rejected_custody_identity(db, field, value):
    ret = rejected(db)
    db.stock_units.update_one({}, {"$set": {field: value}})
    with pytest.raises(ReturnProvenanceError) as exc:
        resolve_request_provenance(db, ret)
    assert exc.value.code == "INVALID_REJECTED_ORIGIN"


def test_a08_request_cannot_borrow_accepted_goods_bill_tax(db):
    ret = rejected(db)
    bill(db)
    assert resolve_request_provenance(db, ret)[0]["origin_status"] == "REJECTED_CUSTODY"
    with pytest.raises(ReturnProvenanceError) as exc:
        resolve_request_provenance(db, ret, selections=[selection()])
    assert exc.value.code == "REJECTED_ORIGIN_UNBILLED"


def test_a08_request_fails_closed_if_receipt_read_is_unavailable(db, monkeypatch):
    ret = rejected(db)
    monkeypatch.setattr(db.grns, "find_one", Mock(side_effect=RuntimeError("receipt unavailable")))
    with pytest.raises(RuntimeError, match="receipt unavailable"):
        resolve_request_provenance(db, ret)
