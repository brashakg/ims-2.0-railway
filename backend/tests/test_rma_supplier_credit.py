"""Canonical RMA credits keep real note evidence and the maker-checker seam."""
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import vendor_rma as router
from api.routers.auth import get_current_user
from api.services import rma_supplier_credit as adapter
from api.services import supplier_credit_confirmation as canonical
from api.services.vendor_rma import VendorRMAEngine, _now
from test_return_provenance import db  # noqa: F401
from test_supplier_credit_evidence import ready  # noqa: F401
from test_supplier_credit_confirmation import payload


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


@pytest.fixture
def world(db, ready, monkeypatch):
    doc = {"rma_id": "RMA1", "vendor_id": "V1", "store_id": "S1", "status": "DISPATCHED",
           "lines": [{"product_id": "P1", "quantity": 1, "unit_cost_paise": 8000, "reason": "DEFECTIVE"}],
           "expected_credit_paise": 8000, "received_credit_paise": 0, "variance_paise": 8000,
           "credit_notes": [], "status_history": []}
    db.vendor_rmas.insert_one(deepcopy(doc))
    monkeypatch.setattr(router, "_get_db", lambda: db)
    user = {"user_id": "accounts", "roles": ["ACCOUNTANT"], "store_ids": ["S1"], "active_store_id": "S1"}
    app = FastAPI()
    app.include_router(router.router, prefix="/vendor-rma")
    app.dependency_overrides[get_current_user] = lambda: user
    return db, doc, TestClient(app), user


def body(db, doc, **kwargs):
    return payload(db, adapter.normalize(doc), **kwargs)


def send(world, data):
    return world[2].post("/vendor-rma/RMA1/credit-note", json=data)


def approved(db, **overrides):
    note = {"request_id": "AP1", "action_type": "rtv", "status": "APPROVED", "consumed": False,
            "store_id": "S1", "context": {"rma_id": "RMA1"}, "amount": 100,
            "approval_token": "test-approval", "requested_by": "requester", "reviewed_by": "checker",
            "expires_at": _now() + timedelta(hours=1), **overrides}
    db.approval_requests.insert_one(note)
    return {"approval_request_id": "AP1", "approval_token": "test-approval"}


def test_real_note_posts_once_updates_rma_and_never_creates_shadow_return(world):
    db, doc, _, _ = world
    original = list(db.vendor_bills.find())
    data = body(db, doc)
    result = send(world, data)
    assert result.status_code == 200, result.text
    assert result.json()["confirmation"]["posting_status"] == "POSTED"
    assert result.json()["received_credit_paise"] == 8400
    assert result.json()["variance_paise"] == -400  # existing RMA estimate arithmetic
    assert db.vendor_returns.count_documents({}) == 0
    assert list(db.vendor_bills.find()) == original
    event = db.supplier_credit_confirmations.find_one({})
    assert event["return_kind"] == "RMA" and event["return_id"] == "RMA1"
    assert db.vendor_rmas.find_one({})["credit_notes"][0]["credit_note_date"] == data["credit_note_date"]
    assert db.audit_logs.count_documents({"action": "rma_credit_recorded"}) == 1
    again = send(world, data)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 8400


def test_partial_notes_preserve_existing_legacy_total_and_close_retry(world):
    db, doc, _, _ = world
    db.vendor_rmas.update_one({}, {"$set": {"received_credit_paise": 1000,
        "credit_notes": [{"credit_note_number": "LEGACY", "received_paise": 1000}]}})
    first = body(db, doc, amount=42, base=40, cgst=1, sgst=1)
    second = body(db, doc, amount=42, base=40, cgst=1, sgst=1, number="SUP-CN-2")
    assert send(world, first).status_code == 200
    assert send(world, second).status_code == 200
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 9400
    assert VendorRMAEngine(db).close_rma("RMA1", actor="manager")["ok"] is True
    assert send(world, second).status_code == 200
    assert db.vendor_rmas.find_one({})["status"] == "CLOSED"


def test_missing_original_evidence_keeps_actual_note_pending_without_rma_money(world):
    db, doc, client, _ = world
    data = body(db, doc)
    db.vendor_bills.delete_many({})
    data["allocations"] = []
    result = send(world, data)
    assert result.status_code == 200, result.text
    assert result.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE"
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 0
    shown = client.post("/vendor-rma/RMA1/credit-preview", json={"confirmation_id": data["idempotency_key"]})
    assert shown.status_code == 200 and shown.json()["confirmation"]["credit_note_date"] == data["credit_note_date"]


@pytest.mark.parametrize("missing", ["credit_note_date", "idempotency_key"])
def test_legacy_note_number_and_amount_requires_actual_date_and_retry_identity(world, missing):
    db, doc, _, _ = world
    data = body(db, doc)
    data.pop(missing)
    result = send(world, data)
    assert result.status_code == 422
    assert missing in str(result.json()["detail"])
    assert db.supplier_credit_confirmations.count_documents({}) == 0


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "SALES_STAFF", "CASHIER"])
def test_preview_and_confirmation_remain_accounts_only(world, role):
    db, doc, client, user = world
    user["roles"] = [role]
    assert send(world, body(db, doc)).status_code == 403
    assert client.post("/vendor-rma/RMA1/credit-preview", json={}).status_code == 403


def test_foreign_shop_is_hidden_and_unpriced_rma_keeps_existing_gate(world):
    db, doc, client, user = world
    user.update(store_ids=["S2"], active_store_id="S2")
    assert send(world, body(db, doc)).status_code == 404
    assert client.post("/vendor-rma/RMA1/credit-preview", json={}).status_code == 404
    user.update(store_ids=["S1"], active_store_id="S1")
    db.vendor_rmas.update_one({}, {"$set": {"expected_credit_paise": None}})
    assert send(world, body(db, doc)).json()["detail"]["error"] == "price_required"


@pytest.mark.parametrize("change", [
    {"reviewed_by": "accounts"}, {"reviewed_by": "requester"}, {"store_id": "S2"},
    {"context": {"rma_id": "OTHER"}}, {"amount": 1}, {"action_type": "refund"},
])
def test_second_person_and_exact_rma_shop_amount_approval_bindings_remain(world, monkeypatch, change):
    db, doc, _, _ = world
    monkeypatch.setattr(router, "credit_requires_approval", lambda *_a, **_k: True)
    result = send(world, {**body(db, doc), **approved(db, **change)})
    assert result.status_code == 403, result.text
    assert db.approval_requests.find_one({})["consumed"] is False
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_consumed_approval_recovers_after_failure_before_canonical_write(world, monkeypatch):
    db, doc, _, _ = world
    monkeypatch.setattr(router, "credit_requires_approval", lambda *_a, **_k: True)
    data = {**body(db, doc), **approved(db)}
    real = canonical.record_supplier_confirmation
    monkeypatch.setattr(canonical, "record_supplier_confirmation", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("before event")))
    assert send(world, data).status_code == 503
    assert db.approval_requests.find_one({})["status"] == "CONSUMED"
    monkeypatch.setattr(canonical, "record_supplier_confirmation", real)
    assert send(world, data).status_code == 200
    assert send(world, data).json()["idempotent"] is True
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 8400


def test_consumed_approval_cannot_authorize_new_uuid_or_amount(world, monkeypatch):
    db, doc, _, _ = world
    monkeypatch.setattr(router, "credit_requires_approval", lambda *_a, **_k: True)
    data = {**body(db, doc, amount=42, base=40, cgst=1, sgst=1), **approved(db)}
    assert send(world, data).status_code == 200
    changed = {**data, "idempotency_key": str(uuid4()), "credit_note_number": "OTHER"}
    assert send(world, changed).status_code == 403
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1


def test_lost_rma_summary_write_reply_does_not_double_credit(world, monkeypatch):
    db, doc, _, _ = world
    real = db.vendor_rmas.update_one

    def lost_reply(query, update, *args, **kwargs):
        result = real(query, update, *args, **kwargs)
        if update.get("$inc", {}).get("received_credit_paise"):
            raise RuntimeError("committed summary but response lost")
        return result

    monkeypatch.setattr(db.vendor_rmas, "update_one", lost_reply)
    data = body(db, doc)
    assert send(world, data).status_code == 200
    assert send(world, data).status_code == 200
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 8400


def test_active_confirmation_fence_blocks_price_reject_close_and_legacy_credit(world):
    db, _, _, _ = world
    engine = VendorRMAEngine(db)
    db.vendor_rmas.update_one({}, {"$set": {"credit_confirmation_fence": "posting"}})
    assert engine.price_lines("RMA1", unit_costs_paise=[7000], actor="a")["http"] == 409
    assert engine.reject("RMA1", actor="a")["http"] == 409
    assert engine.record_credit_note("RMA1", credit_note_number="LEGACY", received_amount=80, actor="a")["http"] == 409
    db.vendor_rmas.update_one({}, {"$set": {"status": "CREDIT_RECEIVED"}})
    assert engine.close_rma("RMA1", actor="a", write_off_variance=True)["http"] == 409


def test_fenced_failure_can_retry_and_never_writes_vendor_return(world, monkeypatch):
    db, doc, _, _ = world
    real = canonical._reserve_bill
    monkeypatch.setattr(canonical, "_reserve_bill", lambda *_a: (_ for _ in ()).throw(
        adapter.SupplierCreditError("BUSY", "retry", 409)))
    data = body(db, doc)
    assert send(world, data).status_code == 409
    assert "credit_confirmation_fence" not in db.vendor_rmas.find_one({})
    assert db.vendor_returns.count_documents({}) == 0
    monkeypatch.setattr(canonical, "_reserve_bill", real)
    assert send(world, data).status_code == 200


def test_confirmation_uuid_cannot_cross_return_workflows(world):
    db, doc, _, _ = world
    data = body(db, doc)
    db.supplier_credit_confirmations.insert_one({"_id": data["idempotency_key"], "return_kind": "VENDOR_RETURN"})
    assert send(world, data).status_code == 409


def zero_body():
    return {"credit_note_number": "ZERO-ACK", "credit_note_date": "2026-10-18",
            "idempotency_key": str(uuid4()), "received_amount": 0, "allocations": []}


def test_zero_acknowledgement_preserves_writeoff_but_has_no_accounting_effect(world):
    db, _, client, _ = world
    db.vendor_bills.delete_many({})  # no source proof needed for a non-posting acknowledgement
    db.purchase_orders.insert_one({"po_id": "PO1", "payment_hold": True, "rejected_qty": 1})
    data = zero_body()
    result = send(world, data)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "CREDIT_RECEIVED"
    assert result.json()["received_credit_paise"] == 0
    assert result.json()["variance_paise"] == 8000
    assert "confirmation" not in result.json()
    assert db.supplier_credit_confirmations.count_documents({}) == 0
    assert db.supplier_credit_bill_guards.count_documents({}) == 0
    assert db.purchase_orders.find_one({})["payment_hold"] is True
    assert client.post("/vendor-rma/RMA1/close", json={"write_off_variance": True}).status_code == 200
    again = send(world, data)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert again.json()["status"] == "CLOSED"
    assert len(db.vendor_rmas.find_one({})["credit_notes"]) == 1


def test_zero_acknowledgement_keeps_variance_approval_and_retry_binding(world, monkeypatch):
    db, _, _, _ = world
    monkeypatch.setattr(router, "credit_requires_approval", lambda received, variance, **_k: variance >= 8000)
    data = zero_body()
    assert send(world, data).status_code == 403
    data.update(approved(db, amount=0))
    assert send(world, data).status_code == 200
    assert db.approval_requests.find_one({})["consumed"] is True
    assert send(world, data).json()["idempotent"] is True
    assert send(world, {**data, "idempotency_key": str(uuid4()), "credit_note_number": "OTHER"}).status_code == 403
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_zero_acknowledgement_lost_reply_is_recoverable_and_cannot_turn_positive(world, monkeypatch):
    db, doc, _, _ = world
    real = db.vendor_rmas.update_one

    def lose_once(query, update, *args, **kwargs):
        result = real(query, update, *args, **kwargs)
        if update.get("$push", {}).get("credit_notes", {}).get("operational_acknowledgement_id"):
            raise RuntimeError("zero acknowledgement committed but reply lost")
        return result

    monkeypatch.setattr(db.vendor_rmas, "update_one", lose_once)
    data = zero_body()
    assert send(world, data).status_code == 503
    assert send(world, data).json()["idempotent"] is True
    changed = {**body(db, doc), "idempotency_key": data["idempotency_key"]}
    assert send(world, changed).status_code == 409
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_canonical_zero_never_creates_event_or_reservation(world):
    db, doc, _, _ = world
    with pytest.raises(adapter.SupplierCreditError, match="positive"):
        canonical.record_supplier_confirmation(db, adapter.normalize(doc), zero_body(), actor_id="accounts")
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_zero_cannot_allocate_credit_and_legacy_duplicate_note_is_preserved(world):
    db, doc, _, _ = world
    data = body(db, doc)
    assert send(world, {**data, "received_amount": 0}).status_code == 422
    db.vendor_rmas.update_one({}, {"$push": {"credit_notes": {
        "credit_note_number": data["credit_note_number"], "received_paise": 1000}}})
    assert send(world, data).status_code == 409
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_lost_approval_consume_reply_resumes_same_note_without_second_approval(world, monkeypatch):
    db, doc, _, _ = world
    monkeypatch.setattr(router, "credit_requires_approval", lambda *_a, **_k: True)
    real = router._consume_rtv_approval

    def lose_reply(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("approval consumed but reply lost")

    monkeypatch.setattr(router, "_consume_rtv_approval", lose_reply)
    data = {**body(db, doc), **approved(db)}
    assert send(world, data).status_code == 503
    assert send(world, data).status_code == 200
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 8400


def test_rma_mutation_before_posting_fence_rejects_stale_approval_snapshot(world, monkeypatch):
    db, doc, _, _ = world
    real = adapter.fence

    def changed(database, event, normalized):
        database.vendor_rmas.update_one({}, {"$set": {"expected_credit_paise": 9000}})
        return real(database, event, normalized)

    monkeypatch.setattr(adapter, "fence", changed)
    assert send(world, body(db, doc)).status_code == 409
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 0


def test_failed_fence_cleanup_can_resume_only_its_prior_token(world, monkeypatch):
    db, doc, _, _ = world
    real_reserve, real_release = canonical._reserve_bill, adapter.release
    monkeypatch.setattr(canonical, "_reserve_bill", lambda *_a: (_ for _ in ()).throw(
        adapter.SupplierCreditError("BUSY", "retry", 409)))
    monkeypatch.setattr(adapter, "release", lambda *_a: (_ for _ in ()).throw(RuntimeError("cleanup unavailable")))
    data = body(db, doc)
    assert send(world, data).status_code == 503
    old_token = db.vendor_rmas.find_one({})["credit_confirmation_fence"]
    assert db.supplier_credit_confirmations.find_one({})["fenced_token"] == old_token
    monkeypatch.setattr(canonical, "_reserve_bill", real_reserve)
    monkeypatch.setattr(adapter, "release", real_release)
    assert send(world, data).status_code == 200


@pytest.mark.parametrize("changed", ["actor", "amount", "date", "number"])
def test_consumed_approval_binding_refuses_changed_retry_before_event(world, monkeypatch, changed):
    db, doc, _, user = world
    monkeypatch.setattr(router, "credit_requires_approval", lambda *_a, **_k: True)
    data = {**body(db, doc), **approved(db)}
    monkeypatch.setattr(canonical, "record_supplier_confirmation", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("before event")))
    assert send(world, data).status_code == 503
    if changed == "actor":
        user["user_id"] = "another-accountant"
    elif changed == "amount":
        data["received_amount"] = 83
    elif changed == "date":
        data["credit_note_date"] = "2026-10-17"
    else:
        data["credit_note_number"] = "CHANGED"
    assert send(world, data).status_code == 403
    assert db.supplier_credit_confirmations.count_documents({}) == 0


def test_positive_credit_wins_before_zero_cas_without_lost_total(world, monkeypatch):
    db, doc, _, _ = world
    positive = body(db, doc)
    real = adapter.record_zero

    def race(database, snapshot, submitted, **kwargs):
        canonical.record_supplier_confirmation(database, adapter.normalize(snapshot), positive, actor_id="accounts")
        return real(database, snapshot, submitted, **kwargs)

    monkeypatch.setattr(adapter, "record_zero", race)
    assert send(world, zero_body()).status_code == 409
    current = db.vendor_rmas.find_one({})
    assert current["received_credit_paise"] == 8400 and len(current["credit_notes"]) == 1


def test_zero_wins_before_positive_fence_and_positive_retry_uses_fresh_state(world, monkeypatch):
    db, doc, _, _ = world
    real = adapter.fence

    def race(database, event, normalized):
        adapter.record_zero(database, normalized["_rma_snapshot"], canonical._input(zero_body(), allow_zero=True), actor_id="accounts")
        return real(database, event, normalized)

    monkeypatch.setattr(adapter, "fence", race)
    data = body(db, doc)
    assert send(world, data).status_code == 409
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 0
    monkeypatch.setattr(adapter, "fence", real)
    assert send(world, data).status_code == 200
    assert db.vendor_rmas.find_one({})["received_credit_paise"] == 8400


def test_completed_partial_credit_prevents_stale_close_writeoff(world, monkeypatch):
    db, doc, _, _ = world
    first = body(db, doc, amount=42, base=40, cgst=1, sgst=1)
    assert send(world, first).status_code == 200
    second = body(db, doc, amount=42, base=40, cgst=1, sgst=1, number="SECOND")
    real = db.vendor_rmas.find_one_and_update

    def race(query, update, *args, **kwargs):
        if update.get("$set", {}).get("status") == "CLOSED":
            assert send(world, second).status_code == 200
        return real(query, update, *args, **kwargs)

    monkeypatch.setattr(db.vendor_rmas, "find_one_and_update", race)
    result = VendorRMAEngine(db).close_rma("RMA1", actor="accounts", write_off_variance=True)
    assert result["http"] == 409
    current = db.vendor_rmas.find_one({})
    assert current["received_credit_paise"] == 8400 and current["status"] == "CREDIT_RECEIVED"
    assert "written_off_paise" not in current


@pytest.mark.parametrize("zero", [False, True])
def test_close_wins_before_zero_or_positive_claim_and_keeps_writtenoff_total(world, monkeypatch, zero):
    db, doc, _, _ = world
    assert send(world, zero_body()).status_code == 200
    original = adapter.record_zero if zero else adapter.fence

    def race(database, *args, **kwargs):
        assert VendorRMAEngine(database).close_rma("RMA1", actor="accounts", write_off_variance=True)["ok"] is True
        return original(database, *args, **kwargs)

    monkeypatch.setattr(adapter, "record_zero" if zero else "fence", race)
    data = zero_body() if zero else body(db, doc)
    data["credit_note_number"] = "SECOND"
    assert send(world, data).status_code == 409
    current = db.vendor_rmas.find_one({})
    assert current["status"] == "CLOSED" and current["written_off_paise"] == 8000
    assert current["received_credit_paise"] == 0
