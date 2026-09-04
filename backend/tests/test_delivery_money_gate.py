"""
IMS 2.0 - Delivery money gate tests (the non-counter doors)
===========================================================
An adversarial audit confirmed three doors let goods leave the shop with NO
payment check and no manager in the loop, bypassing the owner ruling that the
counter deliver door (orders.py) already enforces:

  1. labels.py  POST /workshop/jobs/{id}/scan-advance  (PICKUP -> DELIVERED)
  2. workshop.py PATCH /jobs/{id}/status               (READY -> DELIVERED)
  3. shipping.py POST /shipments                       (defaults courier Prepaid)
  4. (found during the fix) lab_routing PICKUP station scan -> DELIVERED,
     which routes through workshop.evaluate_scan_transition_gate

All four now clear ONE shared implementation: services/delivery_gate.py
(assert_handover_payment = the UNPAID hard block + the CREDIT_DELIVERY
manager/token gate, extracted verbatim from orders.py).

DISCRIMINATING POWER: every blocking test here was measured by reverting the
door's gate call and confirming the test FAILS (the request succeeds again).
No fixture supplies the answer - the fakes carry real money fields and the
assertions check both the HTTP outcome AND that no status write landed.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ["DISPATCH_MODE"] = "off"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import labels as labels_mod  # noqa: E402
from api.routers import shipping as shipping_mod  # noqa: E402
from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import shiprocket  # noqa: E402


# ============================================================================
# Fakes (money fields are REAL data the gate must read - never defaults)
# ============================================================================


def _order(
    order_id: str = "ORD-J1",
    payment_status: str = "PARTIAL",
    balance_due: float = 5000.0,
    status: str = "READY",
    **extra: Any,
) -> Dict[str, Any]:
    doc = {
        "order_id": order_id,
        "order_number": "INV-9001",
        "customer_id": "CUST-1",
        "customer_name": "Asha",
        "customer_phone": "9876543210",
        "store_id": "BV-TEST-01",
        "status": status,
        "payment_status": payment_status,
        "balance_due": balance_due,
        "grand_total": 12000.0,
        "items": [
            {"product_name": "Frame", "sku": "F-1", "quantity": 1, "item_total": 12000}
        ],
    }
    doc.update(extra)
    return doc


def _job(status: str = "READY", order_id: str = "ORD-J1", **extra: Any) -> Dict[str, Any]:
    doc = {
        "job_id": "J1",
        "job_number": "WS-J1",
        "order_id": order_id,
        "status": status,
        "store_id": "BV-TEST-01",
        "customer_name": "Asha",
        "qc_passed": True,  # QC is NOT the thing under test here
    }
    doc.update(extra)
    return doc


class FakeJobRepo:
    def __init__(self, job: Dict[str, Any]):
        self._job = job
        self.status_writes: List[str] = []

    def find_by_id(self, job_id):
        return dict(self._job) if self._job.get("job_id") == job_id else None

    def update_status(self, job_id, status, by_user=None, notes=None, **_pickup):
        self.status_writes.append(status)
        self._job["status"] = status
        return True

    def update(self, job_id, data):
        self._job.update(data)
        return True


class FakeOrderRepo:
    def __init__(self, order: Optional[Dict[str, Any]]):
        self._order = order

    def find_by_id(self, order_id):
        if self._order and self._order.get("order_id") == order_id:
            return dict(self._order)
        return None


class RecordingApprovalEngine:
    """Stands in for services.approvals.ApprovalEngine. Configured per-test to
    accept or refuse; records the binding kwargs so a test can prove the door
    passes store/order binding through (not just 'a token was seen')."""

    outcome: Dict[str, Any] = {"ok": False, "error": "not configured"}
    calls: List[Dict[str, Any]] = []

    def __init__(self, db=None):
        pass

    def consume_approval(self, **kwargs):
        RecordingApprovalEngine.calls.append(kwargs)
        return dict(RecordingApprovalEngine.outcome)


@pytest.fixture(autouse=True)
def _reset_approval_engine():
    RecordingApprovalEngine.outcome = {"ok": False, "error": "not configured"}
    RecordingApprovalEngine.calls = []
    yield


def _user(roles, store="BV-TEST-01"):
    async def _dep():
        return {
            "user_id": "u-test",
            "username": "tester",
            "full_name": "Tester",
            "roles": list(roles),
            "store_ids": [store],
            "active_store_id": store,
        }

    return _dep


# ============================================================================
# A. workshop PATCH /jobs/{id}/status -> DELIVERED
# ============================================================================


def _patch_client(monkeypatch, job, order, roles=("WORKSHOP_STAFF",)):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    jrepo = FakeJobRepo(job)
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: jrepo)
    monkeypatch.setattr(wm, "get_order_repository", lambda: FakeOrderRepo(order))
    monkeypatch.setattr(wm, "get_audit_repository", lambda: None)
    monkeypatch.setattr(wm, "get_db", lambda: None)
    app.dependency_overrides[get_current_user] = _user(roles)
    return TestClient(app), jrepo


def test_patch_delivered_blocked_for_staff_when_balance_due(monkeypatch):
    """THE finding-2 hole: WORKSHOP_STAFF PATCHing a QC-cleared job to
    DELIVERED while Rs 5,000 is still owed must be refused 403 - the owner's
    credit-delivery ruling excludes this role from taking that decision."""
    client, jrepo = _patch_client(monkeypatch, _job(), _order())
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 403, resp.text
    assert "manager" in resp.text.lower()
    assert jrepo.status_writes == []  # goods did not leave


def test_patch_delivered_blocked_for_staff_when_unpaid(monkeypatch):
    """Zero payment on record -> hard 400, same as the counter door."""
    client, jrepo = _patch_client(
        monkeypatch, _job(), _order(payment_status="UNPAID", balance_due=12000.0)
    )
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 400, resp.text
    assert "partial payment" in resp.text.lower()
    assert jrepo.status_writes == []


def test_patch_delivered_manager_may_take_credit_decision(monkeypatch):
    client, jrepo = _patch_client(
        monkeypatch, _job(), _order(), roles=("STORE_MANAGER",)
    )
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert jrepo.status_writes == ["DELIVERED"]


def test_patch_delivered_staff_ok_when_fully_paid(monkeypatch):
    client, jrepo = _patch_client(
        monkeypatch, _job(), _order(payment_status="PAID", balance_due=0.0)
    )
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert jrepo.status_writes == ["DELIVERED"]


def test_patch_delivered_staff_ok_with_valid_credit_token(monkeypatch):
    """A manager's CREDIT_DELIVERY token authorises a non-manager, and the
    consume is bound to THIS store + order (drift guard: the binding kwargs
    must reach the ApprovalEngine)."""
    monkeypatch.setattr(
        "api.services.approvals.ApprovalEngine", RecordingApprovalEngine
    )
    RecordingApprovalEngine.outcome = {"ok": True}
    client, jrepo = _patch_client(monkeypatch, _job(), _order())
    resp = client.patch(
        "/workshop/jobs/J1/status",
        json={"status": "DELIVERED", "approval_token": "TOK-1"},
    )
    assert resp.status_code == 200, resp.text
    assert jrepo.status_writes == ["DELIVERED"]
    (call,) = RecordingApprovalEngine.calls
    assert call["action_type"] == "CREDIT_DELIVERY"
    assert call["approval_token"] == "TOK-1"
    assert call["expected_store_id"] == "BV-TEST-01"
    assert call["expected_context"] == {"order_id": "ORD-J1"}


def test_patch_delivered_staff_refused_with_bad_token(monkeypatch):
    monkeypatch.setattr(
        "api.services.approvals.ApprovalEngine", RecordingApprovalEngine
    )
    RecordingApprovalEngine.outcome = {"ok": False, "error": "already_consumed"}
    client, jrepo = _patch_client(monkeypatch, _job(), _order())
    resp = client.patch(
        "/workshop/jobs/J1/status",
        json={"status": "DELIVERED", "approval_token": "TOK-DEAD"},
    )
    assert resp.status_code == 403, resp.text
    assert jrepo.status_writes == []


def test_patch_delivered_skips_gate_when_order_already_delivered(monkeypatch):
    """The counter door already took (and audited) the credit decision; the
    job record must not strand behind it."""
    client, jrepo = _patch_client(
        monkeypatch, _job(), _order(status="DELIVERED", balance_due=5000.0)
    )
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert jrepo.status_writes == ["DELIVERED"]


def test_patch_delivered_failsoft_when_order_unresolvable(monkeypatch):
    """Infrastructure/data fail-soft: no linked order found -> the QC-cleared
    delivery proceeds (logged), matching assert_linked_job_qc_cleared."""
    client, jrepo = _patch_client(monkeypatch, _job(), None)
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert jrepo.status_writes == ["DELIVERED"]


# ============================================================================
# B. labels POST /workshop/jobs/{id}/scan-advance (PICKUP -> DELIVERED)
# ============================================================================


def _scan_client(monkeypatch, job, order, roles=("CASHIER",)):
    app = FastAPI()
    app.include_router(labels_mod.router, prefix="/labels")
    jrepo = FakeJobRepo(job)
    monkeypatch.setattr(labels_mod, "get_workshop_repository", lambda: jrepo)
    monkeypatch.setattr(labels_mod, "get_audit_repository", lambda: None)
    monkeypatch.setattr(labels_mod, "get_db", lambda: None)
    # The money leg resolves the order through workshop's namespace.
    monkeypatch.setattr(wm, "get_order_repository", lambda: FakeOrderRepo(order))
    app.dependency_overrides[get_current_user] = _user(roles)
    return TestClient(app), jrepo


def test_scan_pickup_blocked_for_cashier_when_balance_due(monkeypatch):
    """THE finding-1 hole: a barcode scan at PICKUP handed the glasses over
    with zero payment check. Now: ok=false / PAYMENT_DUE, no status write."""
    client, jrepo = _scan_client(monkeypatch, _job(), _order())
    resp = client.post(
        "/labels/workshop/jobs/J1/scan-advance",
        json={"scanned_code": "WS-J1", "station": "PICKUP"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False, data
    assert data["reason"] == "PAYMENT_DUE"
    assert "manager" in data["message"].lower()
    assert jrepo.status_writes == []  # the job is still on the shelf


def test_scan_pickup_manager_passes(monkeypatch):
    client, jrepo = _scan_client(
        monkeypatch, _job(), _order(), roles=("STORE_MANAGER",)
    )
    resp = client.post(
        "/labels/workshop/jobs/J1/scan-advance",
        json={"scanned_code": "WS-J1", "station": "PICKUP"},
    )
    assert resp.json()["ok"] is True, resp.text
    assert jrepo.status_writes == ["DELIVERED"]


def test_scan_pickup_cashier_ok_when_fully_paid(monkeypatch):
    client, jrepo = _scan_client(
        monkeypatch, _job(), _order(payment_status="PAID", balance_due=0.0)
    )
    resp = client.post(
        "/labels/workshop/jobs/J1/scan-advance",
        json={"scanned_code": "WS-J1", "station": "PICKUP"},
    )
    assert resp.json()["ok"] is True, resp.text
    assert jrepo.status_writes == ["DELIVERED"]


def test_scan_pickup_cashier_ok_with_valid_credit_token(monkeypatch):
    monkeypatch.setattr(
        "api.services.approvals.ApprovalEngine", RecordingApprovalEngine
    )
    RecordingApprovalEngine.outcome = {"ok": True}
    client, jrepo = _scan_client(monkeypatch, _job(), _order())
    resp = client.post(
        "/labels/workshop/jobs/J1/scan-advance",
        json={"scanned_code": "WS-J1", "station": "PICKUP", "approval_token": "TOK-2"},
    )
    assert resp.json()["ok"] is True, resp.text
    assert jrepo.status_writes == ["DELIVERED"]
    assert RecordingApprovalEngine.calls[0]["action_type"] == "CREDIT_DELIVERY"


def test_scan_earlier_stations_never_money_gated(monkeypatch):
    """The money rule is a HANDOVER rule - a bench move (COMPLETED -> READY via
    QC... here IN_PROGRESS -> COMPLETED at FITTING) must not ask for money."""
    job = _job(status="IN_PROGRESS")
    client, jrepo = _scan_client(monkeypatch, job, _order())
    resp = client.post(
        "/labels/workshop/jobs/J1/scan-advance",
        json={"scanned_code": "WS-J1", "station": "FITTING"},
    )
    assert resp.json()["ok"] is True, resp.text
    assert jrepo.status_writes == ["COMPLETED"]


# ============================================================================
# C. the shared scan gate itself (covers door 4: the lab-station PICKUP scan,
#    which calls evaluate_scan_transition_gate with NO current_user)
# ============================================================================


class TestScanGateMoneyLeg:
    def test_no_caller_context_is_never_privileged(self, monkeypatch):
        """lab_routing calls the gate with 3 args (no user, no token). With
        money due that MUST block - fail-closed - because the lab scan path
        cannot carry a manager identity or a token."""
        monkeypatch.setattr(wm, "get_order_repository", lambda: FakeOrderRepo(_order()))
        assert (
            wm.evaluate_scan_transition_gate(None, _job(), "DELIVERED")
            == "PAYMENT_DUE"
        )

    def test_manager_context_passes(self, monkeypatch):
        monkeypatch.setattr(wm, "get_order_repository", lambda: FakeOrderRepo(_order()))
        assert (
            wm.evaluate_scan_transition_gate(
                None, _job(), "DELIVERED",
                current_user={"roles": ["STORE_MANAGER"]},
            )
            is None
        )

    def test_paid_order_passes_without_context(self, monkeypatch):
        monkeypatch.setattr(
            wm,
            "get_order_repository",
            lambda: FakeOrderRepo(_order(payment_status="PAID", balance_due=0.0)),
        )
        assert wm.evaluate_scan_transition_gate(None, _job(), "DELIVERED") is None

    def test_payment_due_has_a_message(self):
        assert "PAYMENT_DUE" in wm.SCAN_GATE_MESSAGES
        assert "manager" in wm.SCAN_GATE_MESSAGES["PAYMENT_DUE"].lower()

    def test_ready_leg_is_not_money_gated(self, monkeypatch):
        """READY puts the job on the shelf - goods have not left. Money is a
        DELIVERED-only rule."""
        monkeypatch.setattr(wm, "get_order_repository", lambda: FakeOrderRepo(_order()))
        assert (
            wm.evaluate_scan_transition_gate(None, _job(status="COMPLETED"), "READY")
            is None
        )


# ============================================================================
# D. shipping POST /shipments (courier door)
# ============================================================================


import jwt  # noqa: E402

from api.routers import auth as auth_mod  # noqa: E402


def _ship_token(roles, store_id="BV-TEST-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "store_ids": [store_id],
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


class _FakeColl:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(doc)


def _ship_client(monkeypatch, order):
    app = FastAPI()
    app.include_router(shipping_mod.router, prefix="/api/v1/shipping")
    coll = _FakeColl()
    monkeypatch.setattr(shipping_mod, "get_order_repository", lambda: FakeOrderRepo(order))
    monkeypatch.setattr(shipping_mod, "get_customer_repository", lambda: None)
    monkeypatch.setattr(shipping_mod, "_shipments_coll", lambda: coll)
    monkeypatch.setattr(shipping_mod, "_get_db", lambda: None)
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "off")
    return TestClient(app), coll


def test_ship_prepaid_blocked_when_balance_due(monkeypatch):
    """THE finding-3 hole: book_shipment dispatched goods on a part-paid order
    with the courier told Prepaid (collect nothing). Non-manager -> 403 and
    NOTHING is booked or persisted."""
    client, coll = _ship_client(monkeypatch, _order(order_id="ORD-S1"))
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-S1"},
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 403, resp.text
    assert "manager" in resp.text.lower()
    assert coll.docs == []


def test_ship_prepaid_blocked_when_fully_unpaid_even_for_manager(monkeypatch):
    """Rs 18,000 fully unpaid + courier told Prepaid = the audited leak. The
    counter door's rule applies verbatim: zero payment on record ships for
    NOBODY as Prepaid - book it COD (or record a payment) instead."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-S1", payment_status="UNPAID", balance_due=18000.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-S1"},
        headers={"Authorization": f"Bearer {_ship_token(['STORE_MANAGER'])}"},
    )
    assert resp.status_code == 400, resp.text
    assert "partial payment" in resp.text.lower()
    assert coll.docs == []


def test_ship_cod_booking_is_exempt(monkeypatch):
    """A web COD order imports as UNPAID - booking it COD is legitimate: the
    courier collects on delivery. No money gate."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-S1", payment_status="UNPAID", balance_due=18000.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-S1", "address": {"payment_method": "COD"}},
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 201, resp.text
    assert len(coll.docs) == 1


def test_ship_prepaid_ok_when_paid(monkeypatch):
    """A prepaid web order with payment captured upstream (ingest wrote
    payment_status PAID / balance_due 0) dispatches with zero friction."""
    client, coll = _ship_client(
        monkeypatch,
        _order(order_id="ORD-S1", payment_status="PAID", balance_due=0.0),
    )
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-S1"},
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 201, resp.text
    assert len(coll.docs) == 1


def test_ship_manager_may_take_credit_decision_on_partial(monkeypatch):
    client, coll = _ship_client(monkeypatch, _order(order_id="ORD-S1"))
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-S1"},
        headers={"Authorization": f"Bearer {_ship_token(['STORE_MANAGER'])}"},
    )
    assert resp.status_code == 201, resp.text
    assert len(coll.docs) == 1


def test_ship_token_authorises_non_manager(monkeypatch):
    monkeypatch.setattr(
        "api.services.approvals.ApprovalEngine", RecordingApprovalEngine
    )
    RecordingApprovalEngine.outcome = {"ok": True}
    client, coll = _ship_client(monkeypatch, _order(order_id="ORD-S1"))
    resp = client.post(
        "/api/v1/shipping/shipments",
        json={"order_id": "ORD-S1", "approval_token": "TOK-3"},
        headers={"Authorization": f"Bearer {_ship_token(['CASHIER'])}"},
    )
    assert resp.status_code == 201, resp.text
    assert len(coll.docs) == 1
    (call,) = RecordingApprovalEngine.calls
    assert call["expected_context"] == {"order_id": "ORD-S1"}


# ============================================================================
# E. one-implementation guard: the routers must not carry their own copy
# ============================================================================


def test_gate_manager_set_is_the_canonical_one():
    """The shared service's manager set must be the owner's ruling set. If a
    second set ever appears in one of the three routers, the AST check below
    catches the drift shape that bit isInterStateSupply."""
    from api.services.delivery_gate import CREDIT_DELIVERY_MANAGER_ROLES

    assert CREDIT_DELIVERY_MANAGER_ROLES == (
        "SUPERADMIN",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
    )


def test_doors_delegate_not_reimplement():
    """Each fixed door must reference the shared gate, and none may define its
    own credit-delivery function. (Source-level, spelling-exact on purpose:
    a renamed second copy still fails the runtime tests above.)"""
    import inspect

    ws_src = inspect.getsource(wm)
    lb_src = inspect.getsource(labels_mod)
    sp_src = inspect.getsource(shipping_mod)

    assert "delivery_gate" in ws_src
    assert "assert_handover_payment" in sp_src
    # labels + lab_routing route through the shared scan gate; the money leg
    # lives once, in workshop.gate_job_handover_payment.
    assert "evaluate_scan_transition_gate" in lb_src
    for src, name in ((lb_src, "labels"), (sp_src, "shipping")):
        assert "def _gate_credit_delivery" not in src, name
        assert "CREDIT_DELIVERY_MANAGER_ROLES =" not in src, name
