"""Actual application readers consume one POSTED fact in its note period."""
import asyncio
import importlib

import pytest
from fastapi import HTTPException

from api.services.gstn_export import to_gstr3b_json
from api.services.payables_mask import strip_input_tax
from api.services import ap_engine
from test_return_provenance import db  # noqa: F401
from test_supplier_credit_confirmation import payload, post, return_doc  # noqa: F401
from test_supplier_credit_evidence import ready  # noqa: F401

ADMIN = {"user_id": "A", "roles": ["ADMIN"]}


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


def setup_report(db, monkeypatch):
    gst = importlib.import_module("api.routers.reports.gstr3b")
    finance = importlib.import_module("api.routers.finance")
    db.stores.update_one({"store_id": "S1"}, {"$set": {
        "store_id": "S1", "entity_id": "E1", "gstin": "20AAAAA0000A1Z5", "state_code": "20"}}, upsert=True)
    monkeypatch.setattr(gst, "_get_raw_db", lambda: db)
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    return gst, finance


def test_later_period_reversal_reaches_registration_report_portal_and_register(db, return_doc, monkeypatch):
    post(db, return_doc, payload(db, return_doc))
    gst, finance = setup_report(db, monkeypatch)
    report = gst._compute_gstr3b("2026-10", "S1")
    assert report["itcAvailable"]["centralTax"] == -2
    assert report["taxPaidCash"]["centralTax"] == 2
    assert report["itcAvailableGstin"]["centralTax"] == -2
    portal = to_gstr3b_json(report)
    assert portal["itc_elg"]["itc_net"]["camt"] == -2
    assert portal["itc_elg"]["itc_rev"][0]["camt"] == 2
    assert portal["itc_elg"]["itc_avl"][0]["camt"] == 0
    register = asyncio.run(finance.itc_register(period="2026-10", entity_id="E1", current_user=ADMIN))
    assert register["periods"][0]["tax"] == -4
    book_rows = finance._book_rows_from_db(db)
    actual = next(row for row in book_rows if row["invoice_no"] == "SUP-CN-1")
    assert actual["tax"] == -4 and actual["bill_date"] == "2026-10-18"


def test_summary_subtracts_actual_original_credit_without_inventing_rcm_liability(db, return_doc, monkeypatch):
    db.vendor_bills.update_one({}, {"$set": {"reverse_charge": True, "vendor_gstin": "URP"}})
    post(db, return_doc, payload(db, return_doc, amount=80))
    _, finance = setup_report(db, monkeypatch)
    summary = asyncio.run(finance.get_gst_summary(month=10, year=2026, current_user=ADMIN))
    assert summary["gst_input_credit"] == -4
    assert summary["net_gst_payable"] == 4


def test_credit_does_not_leak_to_another_registration_or_pending_document(db, return_doc, monkeypatch):
    post(db, return_doc, payload(db, return_doc))
    gst, _ = setup_report(db, monkeypatch)
    db.entities.update_one({"entity_id": "E1"}, {"$push": {"gstins": {"gstin": "27AAAAA0000A1Z5", "state_code": "27"}}})
    db.stores.insert_one({"store_id": "S2", "entity_id": "E1", "gstin": "27AAAAA0000A1Z5"})
    assert gst._compute_gstr3b("2026-10", "S2")["itcAvailable"]["centralTax"] == 0
    db.supplier_credit_confirmations.update_one({}, {"$set": {"posting_status": "PENDING_EVIDENCE"}})
    assert gst._compute_gstr3b("2026-10", "S1")["itcAvailable"]["centralTax"] == 0


def test_canonical_read_failure_never_yields_a_partial_report(db, return_doc, monkeypatch):
    post(db, return_doc, payload(db, return_doc))
    gst, finance = setup_report(db, monkeypatch)
    def unavailable(*_args, **_kwargs):
        raise OSError("confirmed credit read failed")
    monkeypatch.setattr(db.supplier_credit_confirmations, "find", unavailable)
    with pytest.raises(HTTPException) as caught:
        gst._compute_gstr3b("2026-10", "S1")
    assert caught.value.status_code == 503
    with pytest.raises(HTTPException):
        asyncio.run(finance.itc_register(period=None, entity_id=None, current_user=ADMIN))


def test_reversal_amount_remains_accounts_only():
    assert "itcReversed" not in strip_input_tax({"itcReversed": {"centralTax": 2}}, {"roles": ["STORE_MANAGER"]})


def test_supplier_ledger_cashflow_and_bill_read_use_the_same_actual_credit(db, return_doc, monkeypatch):
    from api.routers.vendors.ap_payments import _ledger_rows
    from api.routers.finance.cash_flow import _ap_rows
    from api.routers.purchase_invoices import stamp_bills_as_of
    from datetime import datetime
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: datetime(2026, 10, 18, 12))
    db.vendor_bills.update_one({}, {"$set": {"total_amount": 420}})
    post(db, return_doc, payload(db, return_doc))
    for rows in (_ledger_rows(db, "V1", "S1"), _ap_rows(db, "S1", "2026-10-18")):
        assert ap_engine.build_ledger(*rows)["closing_balance"] == 336
    stamped = stamp_bills_as_of(db, list(db.vendor_bills.find()))
    assert stamped[0]["outstanding"] == 336
