"""Existing Issue Credit cannot bypass evidence, roles, shops or write fences."""
import asyncio

import pytest
from fastapi import HTTPException

from api.routers import vendor_returns as route
from api.routers.vendors import ap_bills, ap_payments
from api.services.capabilities import capability_for
from test_return_provenance import db  # noqa: F401
from test_supplier_credit_confirmation import payload, return_doc  # noqa: F401
from test_supplier_credit_evidence import ready  # noqa: F401

ADMIN = {"user_id": "A", "roles": ["ADMIN"]}


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


def call_preview(rid, **kwargs):
    return asyncio.run(route.preview_supplier_credit(rid, route.SupplierCreditPreview(), **kwargs))


def test_actual_note_api_posts_and_retries_once_with_truthful_return_amount(db, return_doc, monkeypatch):
    monkeypatch.setattr(route, "_get_db", lambda: db)
    body = route.SupplierCreditConfirmation(**payload(db, return_doc))
    result = asyncio.run(route.confirm_supplier_credit("R1", body, current_user=ADMIN))
    assert result["summary"] == {"posted_amount": 84, "remaining_amount": 0, "settlement_status": "CONFIRMED"}
    assert result["return"]["credit_note_number"] == "SUP-CN-1"
    assert result["return"]["credit_note_amount"] == 84
    assert "credit_confirmation_fence" not in result["return"]
    retry = asyncio.run(route.confirm_supplier_credit("R1", body, current_user=ADMIN))
    assert retry["idempotent"] is True
    assert len(call_preview("R1", current_user=ADMIN)["confirmations"]) == 1


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "WORKSHOP_STAFF", "CASHIER"])
def test_money_preview_denies_nonaccounts_even_for_in_process_call(db, return_doc, monkeypatch, role):
    monkeypatch.setattr(route, "_get_db", lambda: db)
    with pytest.raises(HTTPException) as caught:
        call_preview("R1", current_user={"user_id": "X", "roles": [role], "store_id": "S1"})
    assert caught.value.status_code == 403


def test_other_shop_and_missing_return_have_same_404(db, return_doc, monkeypatch):
    monkeypatch.setattr(route, "_get_db", lambda: db)
    user = {"user_id": "X", "roles": ["ACCOUNTANT"], "store_id": "S2"}
    for rid in ("R1", "unknown"):
        with pytest.raises(HTTPException) as caught:
            call_preview(rid, current_user=user)
        assert caught.value.status_code == 404


def test_old_status_and_manual_return_credit_doors_cannot_post(db, return_doc, monkeypatch):
    monkeypatch.setattr(route, "_get_db", lambda: db)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(route.update_return_status("R1", route.VendorReturnStatusUpdate(status="credit_issued"), current_user=ADMIN))
    assert caught.value.status_code == 422
    for kind in (None, "RETURN_CN"):
        args = {"amount": 84, "date": "2026-10-18", "reason": "return"}
        if kind:
            args["cn_type"] = kind
        with pytest.raises(HTTPException) as caught:
            asyncio.run(ap_payments.create_debit_note("V1", ap_bills.DebitNoteCreate(**args), current_user=ADMIN))
        assert caught.value.status_code == 422
    assert db.vendor_debit_notes.count_documents({}) == 0


def test_return_status_change_cannot_race_a_confirmation_fence(db, return_doc, monkeypatch):
    monkeypatch.setattr(route, "_get_db", lambda: db)
    db.vendor_returns.update_one({}, {"$set": {"credit_confirmation_fence": "active"}})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(route.update_return_status("R1", route.VendorReturnStatusUpdate(status="replaced"), current_user=ADMIN))
    assert caught.value.status_code == 409
    assert db.vendor_returns.find_one({})["status"] == "received_by_vendor"


def test_confirmation_routes_have_separate_capability():
    assert capability_for("POST", "/api/v1/vendor-returns/{return_id}/credit-preview") == "vendor-returns:confirm-credit"
    assert capability_for("POST", "/api/v1/vendor-rma/{rma_id}/credit-note") == "vendor-rma:confirm-credit"
