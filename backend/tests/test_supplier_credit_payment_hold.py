"""Accepted-only billing and actually billed rejected custody are distinct."""
from copy import deepcopy

import pytest

from api.services.receipt_payment_hold import rejected_receipt_hold
from api.services.supplier_credit_evidence import credit_preview
from test_return_provenance import bill, db, line, rejected  # noqa: F401
from test_supplier_credit_confirmation import payload, post


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


def setup_rejection(db, *, billed_qty=1, accepted=0):
    ret = rejected(db)
    ret.update(status="received_by_vendor", return_type="credit_note", total_value=80,
               items=[{"product_id": "P1", "quantity": 1, "unit_price": 80}])
    db.grns.update_one({}, {"$set": {"items.0.accepted_qty": accepted}})
    bill(db, bill_date="2026-01-01", lines=[line(qty=billed_qty)], taxable_amount=400, tax_amount=20,
         cgst_total=10, sgst_total=10, igst_total=0)
    db.entities.insert_one({"entity_id": "E1", "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
    db.vendor_returns.insert_one(deepcopy(ret))
    return ret


def test_accepted_only_bill_pays_after_request_confirmed_without_posting_credit(db):
    ret = setup_rejection(db, billed_qty=1, accepted=1)
    assert rejected_receipt_hold(db, "B1") is None
    assert credit_preview(db, ret)["source_status"] == "PENDING_EVIDENCE"
    assert db.supplier_credit_confirmations.count_documents({}) == 0
    db.vendor_returns.update_one({}, {"$set": {"status": "draft"}})
    assert "Confirm" in rejected_receipt_hold(db, "B1")


def test_billed_rejected_unit_requires_actual_fully_confirmed_supplier_credit(db):
    ret = setup_rejection(db)
    assert "actual credit" in rejected_receipt_hold(db, "B1")
    result = credit_preview(db, ret)
    assert result["source_status"] == "RESOLVED"
    event, _ = post(db, ret, payload(db, ret))
    assert event["sources"][0]["receipt_disposition"] == "REJECTED"
    assert rejected_receipt_hold(db, "B1") is None


def test_partial_supplier_note_does_not_release_a_bill_for_billed_rejected_goods(db):
    ret = setup_rejection(db)
    post(db, ret, payload(db, ret, 42, base=40, cgst=1, sgst=1))
    assert "actual credit" in rejected_receipt_hold(db, "B1")


@pytest.mark.parametrize("mutation", ["header", "second_bill", "missing_unit", "unknown_quantity", "cancelled"])
def test_ambiguous_or_unconfirmed_evidence_keeps_payment_on_hold(db, mutation):
    ret = setup_rejection(db, billed_qty=1, accepted=1)
    if mutation == "header":
        db.vendor_bills.update_one({}, {"$unset": {"lines": ""}})
    elif mutation == "second_bill":
        bill(db, bid="B2")
    elif mutation == "missing_unit":
        db.stock_units.delete_many({})
    elif mutation == "unknown_quantity":
        db.grns.update_one({}, {"$unset": {"items.0.accepted_qty": ""}})
    else:
        db.vendor_returns.update_one({}, {"$set": {"status": "cancelled"}})
    assert rejected_receipt_hold(db, "B1")
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_lookup_failure_and_legacy_manual_note_do_not_silently_release_hold(db, monkeypatch):
    setup_rejection(db)
    db.vendor_debit_notes.insert_one({"bill_id": "B1", "amount": 999})
    assert rejected_receipt_hold(db, "B1")
    def unavailable(*_args, **_kwargs):
        raise OSError("read failed")
    monkeypatch.setattr(db.grns, "find_one", unavailable)
    assert "unavailable" in rejected_receipt_hold(db, "B1")
