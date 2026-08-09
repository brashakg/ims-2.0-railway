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
    """Previously ANY authenticated user could hand goods to a customer."""
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
