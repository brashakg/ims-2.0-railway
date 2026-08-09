"""
IMS 2.0 - Order-side patient handover is QC-gated (PR #971 fix round)
=====================================================================
THE SHIP-BLOCKER the adversarial panel found: PR #971 gated the Workshop screen
but not the Orders screen -- and the Orders screen is the one the counter
actually uses, because payment and the invoice live there.

POST /orders/{id}/deliver checked only store access, the Rx hold, the
READY -> DELIVERED transition and payment != UNPAID. It never read
order["workshop_job_id"] (the reverse pointer the order itself carries) and
never consulted the QC predicate, so a job the workshop gate was actively
holding walked out in one click, the NPS survey fired, and the workshop row
stayed READY forever.

Both endpoints also carried NO role gate at all (bare Depends(get_current_user)),
unlike /cancel -- any authenticated user could deliver an order.

Covers: deliver refuses an un-QC'd linked job, allows a QC-cleared one, resolves
the job by reverse pointer AND by find_by_order fallback, leaves non-workshop
orders alone, fails soft on infrastructure, and is POS_WRITE_ROLES-gated. Same
for /ready.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers import orders as om  # noqa: E402
from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "BV-TEST-01"


def _order(**kw) -> Dict[str, Any]:
    doc = {
        "order_id": "ORD-1",
        "order_number": "SO-1",
        "store_id": STORE,
        "customer_id": "CUST-1",
        "status": "READY",
        "payment_status": "PAID",
        "items": [{"item_type": "OPTICAL_LENS", "product_id": "L1"}],
    }
    doc.update(kw)
    return doc


def _wjob(**kw) -> Dict[str, Any]:
    doc = {
        "job_id": "JID-1",
        "job_number": "WS-1",
        "order_id": "ORD-1",
        "status": "READY",
        "store_id": STORE,
    }
    doc.update(kw)
    return doc


class _OrderRepo:
    def __init__(self, order):
        self._order = order
        self.status_updates: List[str] = []

    def find_by_id(self, order_id):
        return self._order if self._order.get("order_id") == order_id else None

    def update_status(self, order_id, status, by_user=None):
        self.status_updates.append(status)
        self._order["status"] = status
        return True

    def update(self, *_a, **_k):
        return True


class _WorkshopRepo:
    """Doubles the two lookups assert_linked_job_qc_cleared uses."""

    def __init__(self, jobs: Optional[List[Dict[str, Any]]] = None, boom: bool = False):
        self._jobs = list(jobs or [])
        self._boom = boom

    def find_by_id(self, job_id):
        if self._boom:
            raise RuntimeError("workshop store unreachable")
        return next((j for j in self._jobs if j.get("job_id") == job_id), None)

    def find_by_order(self, order_id):
        if self._boom:
            raise RuntimeError("workshop store unreachable")
        return [j for j in self._jobs if j.get("order_id") == order_id]


def _client(monkeypatch, order, jobs, roles=("SALES_CASHIER",), wrepo=None):
    app = FastAPI()
    app.include_router(om.router, prefix="/api/v1/orders")
    orepo = _OrderRepo(order)
    workshop_repo = wrepo if wrepo is not None else _WorkshopRepo(jobs)

    monkeypatch.setattr(om, "get_order_repository", lambda: orepo)
    # assert_linked_job_qc_cleared resolves the repo through the workshop module.
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: workshop_repo)

    async def _user():
        return {
            "user_id": "u1",
            "username": "counter",
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), orepo


# ===========================================================================
# POST /orders/{id}/deliver -- the handover door the counter actually uses
# ===========================================================================


def test_deliver_blocked_when_linked_job_has_no_qc(monkeypatch):
    """THE SHIP-BLOCKER regression: an un-QC'd linked job must stop the handover."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob()]  # no qc_passed
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    body = resp.json()["detail"]
    assert "QC" in body
    assert "WS-1" in body  # names the job so the counter can find it
    assert orepo.status_updates == []  # never flipped, so NPS never fired


def test_deliver_allowed_when_linked_job_qc_passed(monkeypatch):
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_passed=True)]
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 200, resp.text
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_allowed_when_linked_job_qc_waived(monkeypatch):
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_waived=True)]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_finds_the_job_without_a_reverse_pointer(monkeypatch):
    """Orders predating the workshop_job_id pointer still resolve via
    find_by_order -- otherwise the gate would silently pass on legacy rows."""
    client, orepo = _client(monkeypatch, _order(), [_wjob()])  # no pointer
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert orepo.status_updates == []


def test_deliver_unaffected_for_an_order_with_no_workshop_job(monkeypatch):
    """A frame-only / accessory sale has nothing to check and must not be
    blocked -- the mirror failure would be turning away a paying customer."""
    client, orepo = _client(monkeypatch, _order(), [])
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_ignores_a_cancelled_job(monkeypatch):
    """A cancelled workshop job is not being handed over."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="CANCELLED")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_fails_soft_when_workshop_store_is_down(monkeypatch):
    """Infrastructure fail-soft only: an outage must not strand a paid customer
    at the counter. (A job that IS readable and un-QC'd is still a hard block --
    see the first test.)"""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), None, wrepo=_WorkshopRepo(boom=True)
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_deliver_uses_the_same_predicate_as_the_workshop_gate(monkeypatch):
    """One source of truth: orders imports workshop.qc_cleared, so a truthy-junk
    qc_passed fails closed on BOTH doors identically."""
    assert wm.qc_cleared is wm._qc_cleared
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_passed="yes")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


# ===========================================================================
# Role gate -- both endpoints previously had NONE
# ===========================================================================


def test_deliver_is_role_gated(monkeypatch):
    """Previously ANY authenticated user could hand goods to a customer.
    ACCOUNTANT is out of scope for a counter handover."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("ACCOUNTANT",),
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 403, resp.text
    assert orepo.status_updates == []


def test_ready_is_role_gated(monkeypatch):
    client, orepo = _client(
        monkeypatch,
        _order(status="CONFIRMED", workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("WORKSHOP_STAFF",),
    )
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 403
    assert orepo.status_updates == []


def test_pos_roles_still_permitted(monkeypatch):
    for role in ("SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "ADMIN"):
        client, orepo = _client(
            monkeypatch,
            _order(workshop_job_id="JID-1"),
            [_wjob(qc_passed=True)],
            roles=(role,),
        )
        assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200, role
        assert orepo.status_updates == ["DELIVERED"], role


# ===========================================================================
# POST /orders/{id}/ready -- an order must not be advertised as collectable
# ===========================================================================


def test_ready_blocked_when_linked_job_has_no_qc(monkeypatch):
    client, orepo = _client(
        monkeypatch, _order(status="CONFIRMED", workshop_job_id="JID-1"), [_wjob()]
    )
    resp = client.post("/api/v1/orders/ORD-1/ready")
    assert resp.status_code == 400, resp.text
    assert "QC" in resp.json()["detail"]
    assert orepo.status_updates == []


def test_ready_allowed_when_qc_cleared(monkeypatch):
    client, orepo = _client(
        monkeypatch,
        _order(status="CONFIRMED", workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
    )
    assert client.post("/api/v1/orders/ORD-1/ready").status_code == 200
    assert orepo.status_updates == ["READY"]


# ===========================================================================
# MULTI-JOB ORDERS -- the P0 the first 13 tests could not reach
# ===========================================================================
# Every test above puts ONE job on the order, so the resolver's find_by_order
# branch was only ever exercised with the pointer absent. That is how a resolver
# which examined exactly ONE job when the pointer resolved stayed green: it is a
# NO-OP for a duplicate-job order, and one such order exists in prod today.


def test_pointer_at_qcd_job_does_not_hide_an_unqcd_sibling(monkeypatch):
    """THE P0. Order points at the QC'd job; the sibling the bench actually
    ground has no QC. Checking only the pointed-at job hands the patient
    un-QC'd spectacles at HTTP 200."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
            _wjob(job_id="JID-2", job_number="WS-2", status="COMPLETED"),  # no QC
        ],
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert "WS-2" in resp.json()["detail"]  # names the offending sibling
    assert orepo.status_updates == []


def test_multi_job_order_delivers_once_every_job_is_cleared(monkeypatch):
    """The mirror: ALL jobs cleared -> the handover proceeds. A gate that
    blocked a fully-QC'd duplicate pair forever would strand the customer."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
            _wjob(job_id="JID-2", job_number="WS-2", qc_waived=True),
        ],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_unworked_ghost_sibling_does_not_strand_a_finished_job(monkeypatch):
    """The live prod shape: the safety net made a PENDING ghost nobody worked,
    the real job is QC-passed. The ghost has produced nothing that could be
    un-QC'd, and QC refuses PENDING -- blocking here would strand a customer
    whose glasses are finished and on the shelf."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-GHOST"),
        [
            _wjob(job_id="JID-GHOST", job_number="WS-GHOST", status="PENDING"),
            _wjob(job_id="JID-REAL", job_number="WS-REAL", qc_passed=True),
        ],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_cross_order_pointer_is_ignored(monkeypatch):
    """find_by_id is keyed on job_id alone, so a stale/cross-order pointer used
    to make the gate judge a DIFFERENT order's QC record -- failing open here."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-OTHER"),
        [
            # Belongs to a different order but is QC'd -- must NOT satisfy us.
            _wjob(job_id="JID-OTHER", job_number="WS-OTHER", order_id="ORD-9", qc_passed=True),
            # This order's own job has no QC.
            _wjob(job_id="JID-1", job_number="WS-1", status="COMPLETED"),
        ],
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400, resp.text
    assert "WS-1" in resp.json()["detail"]
    assert orepo.status_updates == []


def test_stale_pointer_to_a_deleted_job_still_checks_the_real_one(monkeypatch):
    """A dangling pointer must not make the gate silently pass."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-GONE"),
        [_wjob(job_id="JID-1", job_number="WS-1", status="COMPLETED")],
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


# ===========================================================================
# SKIP RULES -- the gate must block only states QC can actually fix
# ===========================================================================


def test_legacy_delivered_job_does_not_strand_its_order(monkeypatch):
    """A job already handed over cannot be the handover this call is gating.
    QC refuses DELIVERED and VALID_JOB_TRANSITIONS leaves it terminal, so
    blocking would strand the order forever for EVERY role. Prod has one."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="DELIVERED")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_pending_job_does_not_strand_its_order(monkeypatch):
    """No lab work has begun, so nothing exists that could BE un-QC'd; and QC
    deliberately refuses PENDING (a pass would route to READY, skipping the
    sales-confirm and F9 DC gates). Two of four live jobs are PENDING."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status="PENDING")]
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_gate_blocks_only_states_qc_can_fix(monkeypatch):
    """THE INVARIANT, enforced rather than described: every job status the gate
    blocks on must be one the QC endpoints accept. Blocking a state whose named
    remedy the API refuses is the exact failure this PR was returned for."""
    blocked = set(wm.VALID_JOB_TRANSITIONS) - set(wm._HANDOVER_GATE_SKIP_STATUSES)
    assert blocked
    assert blocked <= set(wm._QC_INPUT_STATUSES)
    # And prove it end to end for each blocked status.
    for status in sorted(blocked):
        client, orepo = _client(
            monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(status=status)]
        )
        assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400, status
        assert orepo.status_updates == [], status


def test_ready_gate_runs_after_the_legality_check(monkeypatch):
    """An order in the wrong status must be told THAT, not handed a QC
    instruction it does not yet need."""
    client, _orepo = _client(
        monkeypatch, _order(status="DRAFT", workshop_job_id="JID-1"), [_wjob()]
    )
    resp = client.post("/api/v1/orders/ORD-1/deliver")
    assert resp.status_code == 400
    assert "Must be READY" in resp.json()["detail"]


# ===========================================================================
# The front-desk CASHIER must still be able to close a handover
# ===========================================================================


def test_cashier_can_close_a_handover(monkeypatch):
    """labels.SCAN_ROLES says verbatim that CASHIER is the front-desk role that
    scans a job to DELIVERED at pickup. Gating handover on POS_WRITE_ROLES (whose
    exclusion is scoped to order CREATION) left a cashier able to hand the
    glasses over but unable to close the order."""
    client, orepo = _client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [_wjob(qc_passed=True)],
        roles=("CASHIER",),
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 200
    assert orepo.status_updates == ["DELIVERED"]


def test_cashier_is_still_stopped_by_the_qc_gate(monkeypatch):
    """The QC gate, not the role list, is the patient-safety control."""
    client, orepo = _client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob()], roles=("CASHIER",)
    )
    assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 400
    assert orepo.status_updates == []


def test_out_of_scope_roles_are_still_refused(monkeypatch):
    for role in ("ACCOUNTANT", "CATALOG_MANAGER", "INVESTOR"):
        client, orepo = _client(
            monkeypatch,
            _order(workshop_job_id="JID-1"),
            [_wjob(qc_passed=True)],
            roles=(role,),
        )
        assert client.post("/api/v1/orders/ORD-1/deliver").status_code == 403, role
        assert orepo.status_updates == [], role


# ===========================================================================
# THE THIRD DOOR -- booking a courier shipment is also a handover
# ===========================================================================
# shipping.book_shipment calls assert_no_active_rx_hold under the comment
# "Mirrors the orders.py deliver/ready guard" but was never given the QC check,
# so a job the Orders screen refuses could simply be SHIPPED instead -- to a
# strictly wider role set (_FULFILMENT_ROLES includes CASHIER), and an ONLINE
# order also gets a "Fulfilled" push back to Shopify.


def _shipping_client(monkeypatch, order, jobs, roles=("STORE_MANAGER",)):
    from api.routers import shipping as sm

    app = FastAPI()
    app.include_router(sm.router, prefix="/api/v1/shipping")
    monkeypatch.setattr(sm, "get_order_repository", lambda: _OrderRepo(order))
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: _WorkshopRepo(jobs))

    async def _user():
        return {
            "user_id": "u1",
            "roles": list(roles),
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


_SHIPMENT_BODY = {
    "order_id": "ORD-1",
    "address": {
        "name": "Asha Verma",
        "phone": "9876500000",
        "address_line1": "12 MG Road",
        "city": "Bokaro",
        "state": "Jharkhand",
        "pincode": "827004",
    },
}


def test_booking_a_shipment_is_blocked_without_qc(monkeypatch):
    client = _shipping_client(monkeypatch, _order(workshop_job_id="JID-1"), [_wjob()])
    resp = client.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY)
    assert resp.status_code == 400, resp.text
    assert "QC" in resp.json()["detail"]


def test_booking_a_shipment_allowed_once_qc_is_cleared(monkeypatch):
    client = _shipping_client(
        monkeypatch, _order(workshop_job_id="JID-1"), [_wjob(qc_passed=True)]
    )
    resp = client.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY)
    assert resp.status_code == 201, resp.text


def test_shipment_gate_catches_the_unqcd_sibling_too(monkeypatch):
    client = _shipping_client(
        monkeypatch,
        _order(workshop_job_id="JID-1"),
        [
            _wjob(job_id="JID-1", job_number="WS-1", qc_passed=True),
            _wjob(job_id="JID-2", job_number="WS-2", status="COMPLETED"),
        ],
    )
    resp = client.post("/api/v1/shipping/shipments", json=_SHIPMENT_BODY)
    assert resp.status_code == 400, resp.text
    assert "WS-2" in resp.json()["detail"]
