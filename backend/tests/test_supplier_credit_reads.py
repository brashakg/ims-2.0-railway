"""Canonical event reaches AP and tax once, in its own confirmed period."""
from copy import deepcopy
from xml.etree import ElementTree

import pytest

from api.services import ap_engine, itc_reconcile
from api.services.supplier_credit_reads import (
    confirmed_allocations, confirmed_ap_rows, confirmed_itc_heads, confirmed_itc_rows, payable_credit_rows,
    confirmed_tally_xml,
)
from test_return_provenance import db  # noqa: F401
from test_supplier_credit_confirmation import payload, post, return_doc  # noqa: F401
from test_supplier_credit_evidence import ready  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


def test_posted_credit_reduces_ap_once_and_itc_in_actual_note_period(db, return_doc):
    event, _ = post(db, return_doc, payload(db, return_doc))
    db.vendor_bills.update_one({}, {"$set": {"total_amount": 420}})
    original = db.vendor_bills.find_one({})
    notes = payable_credit_rows(db, vendor_id="V1")
    assert len(notes) == 1
    assert notes[0]["bill_id"] == "B1"
    assert notes[0]["debit_note_number"] == event["credit_note_number"]
    assert notes[0]["amount"] == 84
    assert ap_engine.bill_outstanding(original, [], notes) == 336
    assert ap_engine.build_ledger([original], [], notes)["total_debit_notes"] == 84
    assert confirmed_itc_heads(db, period="2026-01") == {"cgst": 0, "sgst": 0, "igst": 0}
    assert confirmed_itc_heads(db, period="2026-10") == {"cgst": 2, "sgst": 2, "igst": 0}
    rows = confirmed_itc_rows(db)
    register = itc_reconcile.build_itc_register([original, *rows])
    periods = {row["period"]: row for row in register["periods"]}
    assert periods["2026-01"]["tax"] == 20
    assert periods["2026-10"]["tax"] == -4
    assert register["total_cgst"] == 8


@pytest.mark.parametrize("status", ["PENDING_EVIDENCE", "PREPARING"])
def test_nonposted_notes_never_reduce_ap_or_gst(db, return_doc, status):
    post(db, return_doc, payload(db, return_doc))
    db.supplier_credit_confirmations.update_one({}, {"$set": {"posting_status": status}})
    assert confirmed_ap_rows(db) == confirmed_itc_rows(db) == []
    assert confirmed_itc_heads(db) == {"cgst": 0, "sgst": 0, "igst": 0}


def test_manual_adjustments_and_internal_requests_are_not_reposted(db, return_doc):
    db.vendor_debit_notes.insert_one({"debit_note_id": "MANUAL", "vendor_id": "V1", "bill_id": "B1", "amount": 10})
    db.debit_notes.insert_one({"rtv_id": "R1", "amount": 999, "status": "ISSUED"})
    post(db, return_doc, payload(db, return_doc))
    notes = payable_credit_rows(db, vendor_id="V1", bill_id="B1")
    assert sum(row["amount"] for row in notes) == 94
    assert len(notes) == 2


@pytest.mark.parametrize("filters", [{"store_id": "S2"}, {"entity_id": "E2"}, {"gstin": "27AAAAA0000A1Z5"}, {"vendor_id": "V2"}, {"bill_id": "B2"}])
def test_allocation_read_scope_is_exact(db, return_doc, filters):
    post(db, return_doc, payload(db, return_doc))
    assert confirmed_allocations(db, **filters) == []


def test_row_filters_apply_to_each_allocation_not_just_the_event_match(db, return_doc):
    post(db, return_doc, payload(db, return_doc))
    event = db.supplier_credit_confirmations.find_one({})
    foreign = {**deepcopy(event["allocations"][0]), "store_id": "S2", "recipient_gstin": "27AAAAA0000A1Z5", "bill_id": "B2"}
    db.supplier_credit_confirmations.update_one({}, {"$push": {"allocations": foreign}})
    assert len(confirmed_allocations(db, store_id="S1")) == 1
    assert confirmed_itc_heads(db, store_id="S1")["cgst"] == 2


def test_rcm_reader_reduces_only_supplier_base_and_preserves_original_reversal(db, return_doc):
    db.vendor_bills.update_one({}, {"$set": {"reverse_charge": True, "vendor_gstin": "URP"}})
    post(db, return_doc, payload(db, return_doc, amount=80))
    assert confirmed_ap_rows(db)[0]["amount"] == 80
    assert confirmed_itc_heads(db)["cgst"] == 2


def test_nonclaimed_credit_reduces_ap_without_a_negative_itc_row(db, return_doc):
    db.vendor_bills.update_one({}, {"$set": {"itc_eligible": False}})
    post(db, return_doc, payload(db, return_doc))
    assert confirmed_ap_rows(db)[0]["amount"] == 84
    assert confirmed_itc_rows(db) == []
    assert confirmed_itc_heads(db) == {"cgst": 0, "sgst": 0, "igst": 0}


def test_read_failure_propagates_instead_of_returning_only_legacy_figures(db, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("canonical event read unavailable")
    db.vendor_debit_notes.insert_one({"amount": 10})
    monkeypatch.setattr(db.supplier_credit_confirmations, "find", unavailable)
    with pytest.raises(OSError, match="canonical event"):
        payable_credit_rows(db)


@pytest.mark.parametrize("rcm,claimed,amount,tax", [(False, True, 84, 4), (False, False, 84, 0), (True, True, 80, 4)])
def test_tally_exports_only_actual_supplier_amount_and_original_claimed_heads(db, return_doc, rcm, claimed, amount, tax):
    db.vendor_bills.update_one({}, {"$set": {"reverse_charge": rcm, "itc_eligible": claimed}})
    post(db, return_doc, payload(db, return_doc, amount=amount))
    request = {"rtv_ref": {"id": "R1"}, "vendor": {"name": "Original supplier"},
               "debit_note_number": "INTERNAL-999", "totals": {"grand_total_paise": 999999}}
    xml = ElementTree.fromstring(confirmed_tally_xml(db, request))
    voucher = xml.find(".//VOUCHER")
    assert voucher.findtext("VOUCHERNUMBER") == "SUP-CN-1"
    assert voucher.findtext("DATE") == "20261018"
    entries = {leg.findtext("LEDGERNAME"): float(leg.findtext("AMOUNT")) for leg in voucher.findall("ALLLEDGERENTRIES.LIST")}
    assert entries["Original supplier"] == -amount
    assert entries["Purchase Returns"] == amount - tax
    assert sum(entries.values()) == 0


def test_multiple_partial_notes_export_distinct_actual_vouchers_and_unconfirmed_request_blocks(db, return_doc):
    request = {"rtv_ref_id": "R1", "vendor": {"name": "Supplier"}}
    with pytest.raises(ValueError, match="supplier_confirmation_required"):
        confirmed_tally_xml(db, request)
    post(db, return_doc, payload(db, return_doc, 42, base=40, cgst=1, sgst=1))
    post(db, db.vendor_returns.find_one({}), payload(db, return_doc, 42, number="SECOND", base=40, cgst=1, sgst=1))
    xml = ElementTree.fromstring(confirmed_tally_xml(db, request))
    vouchers = xml.findall(".//VOUCHER")
    assert len(vouchers) == 2
    assert {v.findtext("VOUCHERNUMBER") for v in vouchers} == {"SUP-CN-1", "SECOND"}
    assert len({v.findtext("GUID") for v in vouchers}) == 2
