"""Supplier confirmation preview proves original evidence, never master tax."""
from unittest.mock import Mock
from datetime import datetime

import pytest

from api.services.return_provenance import ReturnProvenanceError
from api.services.supplier_credit_evidence import (
    SupplierCreditError, credit_preview, paise, resolve_credit_evidence,
)
from test_return_provenance import bill, db, line, receipt, returned, selection, unit  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_db():
    """Use only each test's in-memory database; no application lifespan."""
    yield


@pytest.fixture
def ready(db):
    receipt(db)
    bill(db, bill_date="2026-01-01", taxable_amount=400, tax_amount=20, cgst_total=10, sgst_total=10, igst_total=0)
    db.entities.insert_one({"entity_id": "E1", "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
    return returned(items=[{"product_id": "P1", "product_name": "Original frame", "quantity": 1, "unit_price": 80}])


@pytest.mark.parametrize("value", [-1, "nan", "Infinity", None, True, 0.001, "garbage", 1_000_000_001])
def test_amounts_reject_invalid_or_sub_paise_values(value):
    with pytest.raises(SupplierCreditError, match="two decimal"):
        paise(value)


def test_original_requested_value_and_original_caps_are_distinct(db, ready):
    before = list(db.vendor_bills.find())
    result = credit_preview(db, ready)
    assert result["source_status"] == "RESOLVED"
    assert result["expected_credit_amount"] == result["remaining_amount"] == 84
    source = result["sources"][0]
    assert source["suggested"] == {"taxable": 80, "cgst": 2, "sgst": 2, "igst": 0, "total": 84}
    assert source["remaining_caps"] == {"taxable": 100, "cgst": 2.5, "sgst": 2.5, "igst": 0, "total": 105}
    assert result["source_selections"] == [selection(stock_ids=[])]
    assert list(db.vendor_bills.find()) == before
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_original_tax_reader_accepts_production_connection_collection_interface(db, ready):
    class Connection:
        def get_collection(self, name):
            return db.get_collection(name)
    assert credit_preview(Connection(), ready)["source_status"] == "RESOLVED"


@pytest.mark.parametrize("patch", [{"bill_date": datetime(2026, 1, 1)}, {"itc_blocked": True}])
def test_disagreeing_legacy_tax_readers_require_reconciliation_not_a_guessed_reversal(db, ready, patch):
    db.vendor_bills.update_one({}, {"$set": patch})
    preview = credit_preview(db, ready)
    assert preview["source_status"] == "PENDING_EVIDENCE"
    assert preview["pending_reasons"][0]["code"] == "ORIGINAL_TAX_CLAIM_INCONSISTENT"


@pytest.mark.parametrize("patch,code", [
    ({"recipient_gstin": None}, "ORIGINAL_REGISTRATION_REQUIRED"),
    ({"recipient_gstin": "27AAAAA0000A1Z5"}, "ORIGINAL_REGISTRATION_REQUIRED"),
    ({"recipient_entity_id": "other"}, "ORIGINAL_REGISTRATION_REQUIRED"),
    ({"vendor_gstin": "URP"}, "ORIGINAL_SUPPLIER_GSTIN_REQUIRED"),
    ({"vendor_gstin": None}, "ORIGINAL_SUPPLIER_GSTIN_REQUIRED"),
    ({"bill_date": "not a date"}, "ORIGINAL_DATE_REQUIRED"),
    ({"bill_number": ""}, "ORIGINAL_NUMBER_REQUIRED"),
    ({"cgst_total": 0}, "ORIGINAL_TAX_TOTAL_MISMATCH"),
    ({"sgst_total": None}, "ORIGINAL_TAX_TOTAL_REQUIRED"),
    ({"tax_amount": 0}, "ORIGINAL_TAX_TOTAL_MISMATCH"),
    ({"tax_amount": None}, "ORIGINAL_TAX_TOTAL_REQUIRED"),
])
def test_original_claims_need_stamped_identity_and_matching_header(db, ready, patch, code):
    db.vendor_bills.update_one({"bill_id": "B1"}, {"$set": patch})
    db.vendors.insert_one({"vendor_id": "V1", "gstin": "20BBBBB0000B1Z5"})
    result = credit_preview(db, ready)
    assert result["source_status"] == "PENDING_EVIDENCE"
    assert result["pending_reasons"][0]["code"] == code
    assert result["sources"] == []
    assert result["expected_credit_amount"] is None


def test_original_supplier_field_missing_is_not_replaced_from_current_vendor(db, ready):
    db.vendor_bills.update_one({}, {"$unset": {"vendor_gstin": ""}})
    db.vendors.insert_one({"vendor_id": "V1", "gstin": "20BBBBB0000B1Z5"})
    with pytest.raises(ReturnProvenanceError) as caught:
        resolve_credit_evidence(db, ready)
    assert caught.value.code == "ORIGINAL_SUPPLIER_GSTIN_REQUIRED"


def test_blocked_original_has_commercial_tax_but_no_claimed_credit(db, ready):
    db.vendor_bills.update_one({}, {"$set": {"vendor_gstin": "URP", "itc_eligible": False}})
    source = resolve_credit_evidence(db, ready)[0]
    assert source["tax_cap_paise"] == {"cgst": 250, "sgst": 250, "igst": 0}
    assert source["itc_cap_paise"] == {"cgst": 0, "sgst": 0, "igst": 0}
    preview = credit_preview(db, ready)
    assert preview["expected_credit_amount"] == 84
    assert preview["sources"][0]["claims_credit"] is False


def test_rcm_can_keep_original_unregistered_supplier_and_excludes_self_paid_tax(db, ready):
    db.vendor_bills.update_one({}, {"$set": {"vendor_gstin": "URP", "reverse_charge": True}})
    preview = credit_preview(db, ready)
    assert preview["source_status"] == "RESOLVED"
    assert preview["expected_credit_amount"] == 80
    assert preview["sources"][0]["suggested"] == {"taxable": 80, "cgst": 2, "sgst": 2, "igst": 0, "total": 80}
    assert resolve_credit_evidence(db, ready)[0]["itc_cap_paise"]["cgst"] == 250


def test_return_linked_to_other_shop_cannot_disclose_bill_values(db, ready):
    unit(db)
    db.grns.update_one({}, {"$set": {"store_id": "S2"}})
    db.vendor_bills.update_one({}, {"$set": {"store_id": "S2"}})
    result = credit_preview(db, ready, allowed_store_ids={"S1"})
    assert result["pending_reasons"][0]["code"] == "ORIGINAL_SHOP_ACCESS_REQUIRED"
    assert result["sources"] == []
    assert result["choices"][0]["candidates"] == []


def test_candidates_use_linked_origin_and_actual_unit_label(db, ready):
    unit(db, barcode="BV-FR-123")
    receipt(db, "G2")
    bill(db, "B2", gid="G2")
    candidate = credit_preview(db, ready)["choices"][0]["candidates"]
    assert len(candidate) == 1
    assert candidate[0]["bill_id"] == "B1"
    assert candidate[0]["stock_units"] == [{"stock_id": "U1", "label": "BV-FR-123"}]


def test_ambiguous_candidates_are_not_chosen_latest_and_selection_resolves(db, ready):
    receipt(db, "G2")
    bill(db, "B2", gid="G2", bill_date="2026-01-01", tax_amount=20, cgst_total=10, sgst_total=10, igst_total=0)
    result = credit_preview(db, ready)
    assert result["source_status"] == "PENDING_EVIDENCE"
    assert {c["bill_id"] for c in result["choices"][0]["candidates"]} == {"B1", "B2"}
    selected = credit_preview(db, ready, selections=[selection(gid="G2", bid="B2")])
    assert selected["sources"][0]["bill_id"] == "B2"


def test_unbooked_and_header_only_choices_are_disabled(db, ready):
    db.vendor_bills.delete_many({})
    result = credit_preview(db, ready)
    assert result["choices"][0]["candidates"][0]["disabled_reason"] == "No original booked bill."
    bill(db, lines=[])
    result = credit_preview(db, ready)
    assert "breakup" in result["choices"][0]["candidates"][0]["disabled_reason"]


def event_for(db, ready, *, status="POSTED", amount=42, identifier="confirmed"):
    source = resolve_credit_evidence(db, ready)[0]
    event = {"confirmation_id": identifier, "return_id": "R1", "posting_status": status,
             "credit_note_number": "SUPPLIER-1", "credit_note_date": "2026-10-18", "received_paise": paise(amount),
             "source_selections": [selection(stock_ids=[])], "allocations": [{"source_key": source["source_key"],
              "taxable_paise": 4000, "cgst_paise": 100, "sgst_paise": 100, "igst_paise": 0}],
             "pending_reasons": []}
    db.supplier_credit_confirmations.insert_one(event)
    return event


def test_preview_recovers_posted_lost_response_and_remaining_partial_amount(db, ready):
    event_for(db, ready)
    result = credit_preview(db, ready, confirmation_id="confirmed")
    assert result["confirmation"]["posting_status"] == "POSTED"
    assert result["posted_amount"] == result["remaining_amount"] == 42
    assert result["sources"][0]["suggested"]["total"] == 42
    assert result["sources"][0]["remaining_caps"]["total"] == 63
    assert result["confirmation"]["allocations"][0]["taxable"] == 40


@pytest.mark.parametrize("status", ["PENDING_EVIDENCE", "PREPARING"])
def test_pending_note_preloads_without_affecting_available_credit(db, ready, status):
    event_for(db, ready, status=status)
    result = credit_preview(db, ready, confirmation_id="confirmed")
    assert result["confirmation"]["received_amount"] == 42
    assert result["posted_amount"] == 0
    assert result["remaining_amount"] == 84


def test_unknown_expected_amount_remains_unknown_after_posted_partial(db, ready):
    event_for(db, ready)
    ready["items"][0]["unit_price"] = None
    result = credit_preview(db, ready)
    assert result["posted_amount"] == 42
    assert result["expected_credit_amount"] is result["remaining_amount"] is None


def test_preload_cannot_read_another_return_confirmation(db, ready):
    event_for(db, ready)
    another = {**ready, "return_id": "R2"}
    with pytest.raises(SupplierCreditError) as caught:
        credit_preview(db, another, confirmation_id="confirmed")
    assert caught.value.status == 404


def test_same_identifier_in_rma_and_vendor_return_has_separate_history_and_sources(db, ready):
    event_for(db, ready)
    rma = {**ready, "return_kind": "RMA"}
    ordinary_key = resolve_credit_evidence(db, ready)[0]["source_key"]
    assert resolve_credit_evidence(db, rma)[0]["source_key"] != ordinary_key
    assert credit_preview(db, ready)["posted_amount"] == 42
    assert credit_preview(db, rma)["posted_amount"] == 0
    with pytest.raises(SupplierCreditError) as caught:
        credit_preview(db, rma, confirmation_id="confirmed")
    assert caught.value.status == 404
    db.supplier_credit_confirmations.update_one({}, {"$set": {"return_kind": "RMA"}})
    assert credit_preview(db, ready)["confirmations"] == []
    assert credit_preview(db, rma)["posted_amount"] == 42


def test_source_key_changes_when_original_evidence_changes(db, ready):
    original = resolve_credit_evidence(db, ready)[0]["source_key"]
    db.vendor_bills.update_one({}, {"$set": {"bill_number": "CORRECTED"}})
    assert resolve_credit_evidence(db, ready)[0]["source_key"] != original


def test_read_failure_propagates_without_zero_or_pending_figures(db, ready, monkeypatch):
    monkeypatch.setattr(db.entities, "find_one", Mock(side_effect=RuntimeError("lookup unavailable")))
    with pytest.raises(RuntimeError, match="lookup unavailable"):
        credit_preview(db, ready)
