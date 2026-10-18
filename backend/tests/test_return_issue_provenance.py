"""Application regressions for A03; the real issue engine must use billed facts."""
from unittest.mock import Mock

import mongomock
import pytest

from api.services.rtv_debit_note import DebitNoteEngine, tally_build_debit_note_xml


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().get_database("return_issue_provenance")
    seller = {"entity_id": "E1", "gstin": "20AAAAA0000A1Z5", "name": "Original buyer"}
    vendor = {"vendor_id": "V1", "gstin": "20BBBBB0000B1Z5", "name": "Current vendor"}
    ret = {
        "return_id": "R1", "vendor_id": "V1", "store_id": "S1", "status": "received_by_vendor",
        "return_type": "credit_note", "items": [{"product_id": "P1", "quantity": 1, "unit_price": 100}],
    }
    db.grns.insert_one({
        "grn_id": "G1", "vendor_id": "V1", "store_id": "S1", "status": "ACCEPTED", "created_at": "2026-01-01",
        "items": [{"product_id": "P1", "accepted_qty": 2}],
    })
    db.vendor_bills.insert_one({
        "bill_id": "B1", "grn_id": "G1", "vendor_id": "V1", "store_id": "S1", "status": "UNPAID",
        "doc_type": "PURCHASE_INVOICE", "received": True, "itc_eligible": True,
        "recipient_entity_id": "E1", "recipient_gstin": seller["gstin"], "vendor_gstin": vendor["gstin"],
        "bill_number": "OLD-INVOICE", "bill_date": "2026-01-01", "taxable_amount": 200,
        "cgst_total": 5, "sgst_total": 5, "igst_total": 0, "tax_amount": 10, "total_amount": 210,
        "lines": [{"product_id": "P1", "qty": 2, "unit_price": 100, "taxable": 200, "gst_rate": 5,
                   "cgst": 5, "sgst": 5, "igst": 0, "line_total": 210, "hsn": "9003"}],
    })
    db.products.insert_one({"product_id": "P1", "category": "SUNGLASS", "gst_rate": 18, "hsn": "9004"})
    # This test deliberately changes the current catalogue rate. Keep the
    # catalogue's HSN lookup local; the fixed issue path should not consult it.
    monkeypatch.setattr("api.routers.vendors.gst._po_line_gst_rate", lambda _line, product: (product["gst_rate"], "fixture"))
    engine = DebitNoteEngine(db)
    engine.ensure_indexes()
    monkeypatch.setattr(engine, "_audit", Mock())
    return db, ret, vendor, seller, engine


def issue(world):
    _, ret, vendor, seller, engine = world
    return engine.issue(ret, vendor, actor="ACCOUNTS", seller=seller)


def test_pending_new_receipt_does_not_replace_the_only_accepted_purchase(world):
    db, _, _, _, _ = world
    db.grns.insert_one({
        "grn_id": "NEW", "vendor_id": "V1", "store_id": "S1", "status": "PENDING", "created_at": "2026-10-18",
        "items": [{"product_id": "P1", "accepted_qty": 2}],
    })
    result = issue(world)
    assert result["ok"] is True
    assert result["debit_note"]["totals"]["tax_paise"] == 500
    assert result["debit_note"]["original_invoice"]["number"] == "OLD-INVOICE"


def test_unlinked_multiple_accepted_receipts_are_not_silently_sorted_by_newest(world):
    db, _, _, _, _ = world
    db.grns.insert_one({
        "grn_id": "NEW", "vendor_id": "V1", "store_id": "S1", "status": "ACCEPTED", "created_at": "2026-10-18",
        "items": [{"product_id": "P1", "accepted_qty": 2}],
    })
    result = issue(world)
    assert result["ok"] is False
    assert result["http"] == 422
    assert db.debit_notes.count_documents({}) == 0


def test_same_product_mixed_credit_eligibility_does_not_use_the_first_units_bill_for_both(world):
    db, ret, _, _, _ = world
    ret["items"][0]["quantity"] = 2
    db.grns.insert_one({
        "grn_id": "G2", "vendor_id": "V1", "store_id": "S1", "status": "ACCEPTED",
        "items": [{"product_id": "P1", "accepted_qty": 2}],
    })
    second = db.vendor_bills.find_one({}, {"_id": 0})
    second.update(bill_id="B2", grn_id="G2", itc_eligible=False)
    db.vendor_bills.insert_one(second)
    db.stock_units.insert_many([
        {"stock_id": "U1", "product_id": "P1", "rtv_vendor_id": "R1", "grn_id": "G1", "store_id": "S1"},
        {"stock_id": "U2", "product_id": "P1", "rtv_vendor_id": "R1", "grn_id": "G2", "store_id": "S1"},
    ])
    result = issue(world)
    assert result["ok"] is True
    totals = result["debit_note"]["totals"]
    assert totals["tax_paise"] == 500
    assert totals["taxable_paise"] == 20500
    assert totals["grand_total_paise"] == 21000


def test_changed_vendor_master_cannot_reclassify_original_igst_as_cgst_sgst(world):
    db, _, _, _, _ = world
    db.vendor_bills.update_one({}, {"$set": {
        "vendor_gstin": "27BBBBB0000B1Z5", "cgst_total": 0, "sgst_total": 0, "igst_total": 10,
        "lines.0.cgst": 0, "lines.0.sgst": 0, "lines.0.igst": 10,
    }})
    result = issue(world)
    assert result["ok"] is True
    totals = result["debit_note"]["totals"]
    assert (totals["cgst_paise"], totals["sgst_paise"], totals["igst_paise"]) == (0, 0, 500)
    assert result["debit_note"]["vendor"]["gstin"] == "27BBBBB0000B1Z5"


def test_no_original_booked_bill_can_issue_request_without_inventing_previously_claimed_credit(world):
    db, _, _, _, _ = world
    db.vendor_bills.delete_many({})
    result = issue(world)
    assert result["ok"] is True
    note = result["debit_note"]
    assert note["document_kind"] == "INTERNAL_RETURN_REQUEST"
    assert note["accounting_status"] == "UNPOSTED"
    assert note["tax_status"] == "UNVERIFIED"
    assert note["totals"]["tax_paise"] == 0
    assert note["totals"]["grand_total_paise"] == 10000
    assert db.vendor_debit_notes.count_documents({}) == 0
    with pytest.raises(ValueError, match="supplier_confirmation_required"):
        tally_build_debit_note_xml(note)


def test_return_tax_override_cannot_replace_the_original_bill_rate(world):
    _, ret, _, _, _ = world
    ret["items"][0].update(gst_rate=18, hsn="9004")
    result = issue(world)
    assert result["ok"] is True
    assert result["debit_note"]["totals"]["tax_paise"] == 500
    assert result["debit_note"]["lines"][0]["hsn"] == "9003"


def test_a08_rejected_custody_keeps_priced_request_handoff_without_claiming_bill_tax(world):
    db, ret, _, _, _ = world
    ret.update(receipt_rejection=True, grn_id="G1", grn_line_index=0, stock_ids=["U1"])
    db.grns.update_one({}, {"$set": {"items.0.accepted_qty": 0, "items.0.rejected_qty": 1}})
    db.vendor_bills.delete_many({})
    db.stock_units.insert_one({
        "stock_id": "U1", "product_id": "P1", "rtv_vendor_id": "R1", "grn_id": "G1", "store_id": "S1",
        "grn_line_index": 0, "quantity": 1, "receipt_disposition": "REJECTED",
    })
    result = issue(world)
    assert result["ok"] is True
    note = result["debit_note"]
    assert note["document_kind"] == "INTERNAL_RETURN_REQUEST"
    assert note["tax_status"] == "UNVERIFIED"
    assert note["totals"]["grand_total_paise"] == 10000
    assert note["totals"]["tax_paise"] == 0
    assert db.vendor_debit_notes.count_documents({}) == 0


def test_traceable_internal_note_also_requires_supplier_confirmation_before_accounting_export(world):
    result = issue(world)
    assert result["ok"] is True
    note = result["debit_note"]
    assert note["accounting_status"] == "UNPOSTED"
    with pytest.raises(ValueError, match="supplier_confirmation_required"):
        tally_build_debit_note_xml(note)
