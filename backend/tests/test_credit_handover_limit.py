"""The actual counter/workshop handover doors share credit capacity checks."""
from copy import deepcopy

import pytest

from api.routers import orders, workshop
from api.routers.orders import delivery
from api.routers.auth import get_current_user
from api.services import delivery_gate, approvals
from tests.test_credit_limit_atomic import env, sale, USER
from tests.test_delivery_money_gate import FakeJobRepo, _job


@pytest.fixture
def handover(env, monkeypatch):
    db, repo, client = env
    monkeypatch.setattr(orders, "get_order_repository", lambda: repo)
    monkeypatch.setattr(delivery, "_get_db", lambda: db)
    monkeypatch.setattr(delivery_gate, "_resolve_db", lambda supplied: db)
    monkeypatch.setattr(workshop, "get_order_repository", lambda: repo)
    monkeypatch.setattr(workshop, "get_db", lambda: None)
    monkeypatch.setattr(workshop, "get_audit_repository", lambda: None)
    monkeypatch.setattr(workshop, "assert_linked_job_qc_cleared", lambda order: None)
    jrepo = FakeJobRepo(_job(order_id="O1", store_id="S1"))
    monkeypatch.setattr(workshop, "get_workshop_repository", lambda: jrepo)
    client.app.add_api_route("/orders/{order_id}/deliver", delivery.deliver_order, methods=["POST"])
    client.app.include_router(workshop.router, prefix="/workshop")
    return db, repo, client, jrepo


@pytest.mark.parametrize("door", ["counter", "workshop"])
@pytest.mark.parametrize("due,allowed", [(100, True), (101, False)])
def test_manager_handover_enforces_limit_and_books_the_existing_unpaid_balance(handover, door, due, allowed):
    db, _, client, jrepo = handover
    sale(db, total=150, amount_paid=150-due, balance_due=due,
         status="READY", payment_status="PARTIAL")  # legacy payment legs absent
    if door == "counter":
        response = client.post("/orders/O1/deliver")
    else:
        response = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert response.status_code == (200 if allowed else 400), response.text
    order = db.orders.find_one({"order_id": "O1"})
    assert (order["amount_paid"], order["balance_due"]) == (150-due, due)
    assert len(order["payments"]) == (1 if allowed else 0)
    if allowed:
        assert order["payments"][0]["method"] == "CREDIT"
        assert order["payments"][0]["amount"] == due
    else:
        assert order["status"] == "READY"
        assert jrepo.status_writes == []


@pytest.mark.parametrize("due,allowed", [(100, True), (101, False)])
def test_pin_authorises_person_but_cannot_override_credit_limit(handover, monkeypatch, due, allowed):
    db, _, client, _ = handover
    sale(db, total=due+10, amount_paid=10, balance_due=due, status="READY", payment_status="PARTIAL")
    client.app.dependency_overrides[get_current_user] = lambda: {**USER, "roles": ["CASHIER"]}
    calls = []
    class Approved:
        def __init__(self, **kwargs):
            pass
        def consume_approval(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True}
    monkeypatch.setattr(approvals, "ApprovalEngine", Approved)
    response = client.post("/orders/O1/deliver", json={"approval_token": "manager-pin"})
    assert response.status_code == (200 if allowed else 400), response.text
    assert calls[0]["expected_context"] == {"order_id": "O1"}
    assert len(db.orders.find_one({"order_id": "O1"})["payments"]) == (1 if allowed else 0)


def test_retry_after_lost_handover_payment_reply_never_duplicates_promise(handover, monkeypatch):
    db, repo, _, _ = handover
    order = sale(db, total=150, balance_due=100, amount_paid=50, status="READY", payment_status="PARTIAL")
    original = db.orders.update_one
    def reply_lost(query, update, *args, **kwargs):
        result = original(query, update, *args, **kwargs)
        if "$push" in update:
            raise RuntimeError("reply lost")
        return result
    monkeypatch.setattr(db.orders, "update_one", reply_lost)
    delivery_gate.assert_handover_payment(order, approval_token=None, current_user=USER, db=db)
    delivery_gate.assert_handover_payment(repo.find_by_id("O1"), approval_token=None, current_user=USER, db=db)
    assert len(db.orders.find_one({"order_id": "O1"})["payments"]) == 1


def test_changed_handover_order_is_refused_before_writing_a_promise(handover, monkeypatch):
    db, _, _, _ = handover
    order = sale(db, total=100, balance_due=60, amount_paid=40, status="READY", payment_status="PARTIAL")
    db.orders.update_one({"order_id": "O1"}, {"$set": {"balance_due": 70}})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        delivery_gate.assert_handover_payment(order, approval_token=None, current_user=USER, db=db)
    assert exc.value.status_code == 409
    assert db.orders.find_one({"order_id": "O1"})["payments"] == []


@pytest.mark.parametrize("balance", [None, "", "  "])
@pytest.mark.parametrize("door,role", [
    ("counter", "STORE_MANAGER"), ("counter", "CASHIER"), ("workshop", "STORE_MANAGER"),
])
def test_unrecorded_balance_cannot_bypass_handover_credit_control(handover, balance, door, role):
    db, _, client, jrepo = handover
    sale(db, total=150, amount_paid=10, balance_due=balance,
         status="READY", payment_status="PARTIAL")
    client.app.dependency_overrides[get_current_user] = lambda: {**USER, "roles": [role]}
    response = (
        client.post("/orders/O1/deliver") if door == "counter"
        else client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    )
    assert response.status_code == 400, response.text
    assert "outstanding balance is not recorded" in response.text
    order = db.orders.find_one({"order_id": "O1"})
    assert order["status"] == "READY"
    assert order["payments"] == []
    assert jrepo.status_writes == []
