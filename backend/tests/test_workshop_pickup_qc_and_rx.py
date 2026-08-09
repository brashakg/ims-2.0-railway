"""
IMS 2.0 - Workshop patient-safety regressions: QC-at-pickup + job prescription
==============================================================================
Two audit findings, both about a spectacle job reaching a PATIENT with something
unverified:

  FINDING 1 [P1, PATIENT SAFETY] "QC bypass at patient pickup"
      The shared scan gate (workshop.evaluate_scan_transition_gate, added by
      PR #958) gated -> READY but NOT -> DELIVERED. The PICKUP lab station
      advances a job straight to DELIVERED, and a gate block on a scan path is a
      HOLD (the station advances, only the status is withheld) -- so a job whose
      DISPATCH -> READY leg was held for missing QC kept moving down the bench
      and the next scan handed it to the patient with zero QC record. The same
      hole existed on the manager PATCH (READY -> DELIVERED was ungated on the
      reasoning that "READY is already QC-gated", which is false for rows that
      reached READY before the gate existed).
      Fixed by gating BOTH patient-facing targets through one predicate
      (_QC_REQUIRED_TARGETS / _qc_cleared), used by the scan gate AND the PATCH.

  FINDING 21 [P3] "workshop create_job stores prescription_id unverified"
      POST /workshop/jobs accepted any prescription_id: no existence check, no
      customer match, no expiry check. Fixed by reusing the canonical POS Rx gate
      rules (orders._validate_order_line_rx / BUG-006).

The end-to-end lab-scan walk (INTAKE..PICKUP against the fake Mongo) lives in
test_f2_lab_routing.py (test_3d/3e/3f); this file pins the gate decision itself,
the manager PATCH surface, and the create-time Rx verification.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_workshop_pickup_qc_and_rx.py -q
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api import dependencies as deps  # noqa: E402
from api.routers import workshop as wm  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


def _job(**kw) -> Dict[str, Any]:
    doc = {"job_id": "J1", "status": "READY", "store_id": "BV-TEST-01"}
    doc.update(kw)
    return doc


# ===========================================================================
# FINDING 1 -- the shared gate decision (pure, no DB, no HTTP)
# ===========================================================================


class TestSharedGateCoversDelivered:
    def test_delivered_blocked_without_qc(self):
        assert (
            wm.evaluate_scan_transition_gate(None, _job(), "DELIVERED")
            == "QC_REQUIRED"
        )

    def test_delivered_allowed_when_qc_passed(self):
        assert (
            wm.evaluate_scan_transition_gate(None, _job(qc_passed=True), "DELIVERED")
            is None
        )

    def test_delivered_allowed_when_qc_waived(self):
        assert (
            wm.evaluate_scan_transition_gate(None, _job(qc_waived=True), "DELIVERED")
            is None
        )

    def test_explicit_qc_fail_is_not_a_pass(self):
        """qc_passed=False is a RECORDED FAILURE, not a clearance."""
        assert (
            wm.evaluate_scan_transition_gate(
                None, _job(qc_passed=False, qc_waived=False), "DELIVERED"
            )
            == "QC_REQUIRED"
        )

    def test_truthy_junk_is_not_a_pass(self):
        """Only a literal True clears the gate -- a stray string / 1 must not."""
        assert (
            wm.evaluate_scan_transition_gate(
                None, _job(qc_passed="yes"), "DELIVERED"
            )
            == "QC_REQUIRED"
        )

    def test_ready_leg_still_gated(self):
        assert (
            wm.evaluate_scan_transition_gate(None, _job(status="COMPLETED"), "READY")
            == "QC_REQUIRED"
        )

    def test_lowercase_target_still_gated(self):
        """The gate normalises the target; a lower-case 'delivered' cannot slip."""
        assert (
            wm.evaluate_scan_transition_gate(None, _job(), "delivered")
            == "QC_REQUIRED"
        )

    def test_both_patient_facing_targets_are_in_the_set(self):
        assert wm._QC_REQUIRED_TARGETS == frozenset({"READY", "DELIVERED"})

    def test_block_message_is_plain_english(self):
        msg = wm.SCAN_GATE_MESSAGES["QC_REQUIRED"]
        assert "QC" in msg
        assert msg.isascii()  # no emoji / unicode (Windows cp1252)


# ===========================================================================
# FINDING 1 -- the manager-facing PATCH /jobs/{id}/status surface
# ===========================================================================


class _StatusRepo:
    """WorkshopJobRepository double for the status PATCH + QC endpoints."""

    def __init__(self, job: Dict[str, Any]):
        self._job = job
        self.updated: Optional[str] = None

    def find_by_id(self, job_id):
        return self._job if self._job.get("job_id") == job_id else None

    def update(self, job_id, data):
        if self._job.get("job_id") != job_id:
            return False
        self._job.update(data)
        return True

    def update_status(self, job_id, status, by_user=None, notes=None, **_pickup):
        self.updated = status
        self._job["status"] = status
        return True

    def add_qc_result(
        self,
        job_id,
        passed,
        notes,
        by_user,
        checklist_items=None,
        waived=False,
        waive_reason=None,
    ):
        self._job.update({"qc_passed": passed, "qc_waived": waived})
        return self.update_status(job_id, "READY" if (passed or waived) else "QC_FAILED", by_user)


def _status_client(monkeypatch, job, roles=("STORE_MANAGER",)):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    repo = _StatusRepo(job)
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(wm, "get_audit_repository", lambda: None)
    monkeypatch.setattr(wm, "get_db", lambda: None)

    async def _user():
        return {
            "user_id": "u1",
            "username": "mgr",
            "roles": list(roles),
            "active_store_id": "BV-TEST-01",
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), repo


def test_patch_delivered_blocked_without_qc(monkeypatch):
    """PATIENT SAFETY: a READY job carrying NO QC record (a row that reached the
    shelf before the QC gates existed) must not be handed over on a PATCH."""
    client, repo = _status_client(monkeypatch, _job(status="READY"))
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 400, resp.text
    assert "QC" in resp.text
    assert repo.updated is None  # the status write never happened


def test_patch_delivered_allowed_when_qc_passed(monkeypatch):
    client, repo = _status_client(monkeypatch, _job(status="READY", qc_passed=True))
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert repo.updated == "DELIVERED"


def test_patch_delivered_allowed_when_qc_waived(monkeypatch):
    client, repo = _status_client(monkeypatch, _job(status="READY", qc_waived=True))
    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert repo.updated == "DELIVERED"


def test_ready_job_without_qc_has_a_remedy(monkeypatch):
    """The gate must not strand a real customer at the counter. A job already on
    the pickup shelf with no QC record can be QC'd in place (the QC endpoints
    accept a READY job), after which the handover clears."""
    client, repo = _status_client(monkeypatch, _job(status="READY"))
    assert (
        client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"}).status_code
        == 400
    )

    qc = client.post("/workshop/jobs/J1/qc?passed=true")
    assert qc.status_code == 200, qc.text

    resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
    assert resp.status_code == 200, resp.text
    assert repo.updated == "DELIVERED"


def test_qc_fail_on_ready_job_pulls_it_off_the_shelf(monkeypatch):
    """A FAIL on a shelf job is the other half of the remedy: it must come back
    off the pickup shelf into QC_FAILED for rework, not stay READY."""
    client, repo = _status_client(monkeypatch, _job(status="READY"))
    qc = client.post("/workshop/jobs/J1/qc?passed=false")
    assert qc.status_code == 200, qc.text
    assert repo.updated == "QC_FAILED"


# ===========================================================================
# FINDING 21 -- POST /workshop/jobs verifies prescription_id
# ===========================================================================


class _CreateWorkshopRepo:
    def __init__(self):
        self.created = []

    def find_by_id(self, _job_id):
        return None

    def create(self, data):
        doc = dict(data)
        doc["job_id"] = "JID-1"
        self.created.append(doc)
        return doc

    def update(self, *_a, **_k):
        return True


class _OrderRepo:
    def __init__(self, order):
        self._order = order

    def find_by_id(self, order_id):
        if self._order and self._order.get("order_id") == order_id:
            return self._order
        return None

    def update(self, *_a, **_k):
        return True


class _RxRepo:
    def __init__(self, rows):
        self._rows = rows

    def find_by_id(self, rx_id):
        return self._rows.get(rx_id)


def _rx(customer_id="CUST-1", months_ago=1, validity_months=12):
    """A prescription doc. months_ago > validity_months makes it EXPIRED."""
    dated = datetime.now() - timedelta(days=int(30.5 * months_ago))
    return {
        "prescription_id": "RX-1",
        "customer_id": customer_id,
        "prescription_date": dated.isoformat(),
        "validity_months": validity_months,
    }


_ORDER = {"order_id": "ORD-1", "customer_id": "CUST-1", "store_id": "BV-TEST-01"}

_PAYLOAD = {
    "order_id": "ORD-1",
    "frame_details": {"brand": "Ray-Ban", "model": "RB1234"},
    "lens_details": {"type": "Single Vision", "coating": "Anti-Glare"},
    "prescription_id": "RX-1",
    "expected_date": "2026-12-01",
}


def _create_client(monkeypatch, rx_rows, roles=("SALES_STAFF",), order=None):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    wrepo = _CreateWorkshopRepo()
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: wrepo)
    monkeypatch.setattr(
        wm, "get_order_repository", lambda: _OrderRepo(_ORDER if order is None else order)
    )
    monkeypatch.setattr(wm, "get_audit_repository", lambda: None)
    monkeypatch.setattr(wm, "get_db", lambda: None)
    # The Rx repo is resolved lazily from api.dependencies inside the verifier.
    monkeypatch.setattr(
        deps,
        "get_prescription_repository",
        (lambda: None) if rx_rows is None else (lambda: _RxRepo(rx_rows)),
    )

    async def _user():
        return {
            "user_id": "u1",
            "username": "seller",
            "roles": list(roles),
            "active_store_id": "BV-TEST-01",
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app), wrepo


def _post(client, **overrides):
    body = dict(_PAYLOAD)
    body.update(overrides)
    return client.post("/workshop/jobs", json=body)


def test_create_rejects_unknown_prescription(monkeypatch):
    client, wrepo = _create_client(monkeypatch, {})  # no Rx exists
    resp = _post(client)
    assert resp.status_code == 422, resp.text
    assert "not found" in resp.text.lower()
    assert wrepo.created == []  # nothing was written


def test_create_rejects_prescription_of_another_customer(monkeypatch):
    """WRONG PATIENT -- a hard error, never a warning: this Rx would be ground
    into someone else's lenses."""
    client, wrepo = _create_client(monkeypatch, {"RX-1": _rx(customer_id="CUST-999")})
    resp = _post(client)
    assert resp.status_code == 422, resp.text
    assert "different customer" in resp.text.lower()
    assert wrepo.created == []


def test_create_accepts_matching_prescription(monkeypatch):
    client, wrepo = _create_client(monkeypatch, {"RX-1": _rx()})
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1
    assert wrepo.created[0]["prescription_id"] == "RX-1"


def test_create_without_prescription_id_is_unchanged(monkeypatch):
    """No prescription supplied -> nothing to verify, behaviour untouched."""
    client, wrepo = _create_client(monkeypatch, {})  # repo would find nothing
    resp = _post(client, prescription_id="")
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_failsoft_when_prescription_repo_unavailable(monkeypatch):
    """Fail-SOFT exactly where the canonical POS gate does: a clinical-store
    outage must not 500 or stall the bench."""
    client, wrepo = _create_client(monkeypatch, None)  # repo is None
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_blocks_expired_prescription_for_junior_staff(monkeypatch):
    client, wrepo = _create_client(
        monkeypatch, {"RX-1": _rx(months_ago=30)}, roles=("SALES_STAFF",)
    )
    resp = _post(client)
    assert resp.status_code == 422, resp.text
    assert "expired" in resp.text.lower()
    assert wrepo.created == []


def test_create_allows_expired_prescription_for_store_manager(monkeypatch):
    """Same expiry-override role list as the POS order path
    (orders._RX_EXPIRY_OVERRIDE_ROLES) -- Store-Manager and up may proceed."""
    client, wrepo = _create_client(
        monkeypatch, {"RX-1": _rx(months_ago=30)}, roles=("STORE_MANAGER",)
    )
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_allows_prescription_with_no_customer_on_file(monkeypatch):
    """Mirrors the canonical POS gate: the customer match needs BOTH sides. An Rx
    row with no customer_id is a data-quality gap, not a wrong-patient signal, so
    it passes here exactly as it does at billing."""
    rx = _rx()
    rx.pop("customer_id")
    client, wrepo = _create_client(monkeypatch, {"RX-1": rx})
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_still_404s_on_unknown_order(monkeypatch):
    """Order resolution is unchanged and still runs BEFORE the Rx check."""
    client, wrepo = _create_client(monkeypatch, {"RX-1": _rx()})
    resp = _post(client, order_id="ORD-NOPE")
    assert resp.status_code == 404, resp.text
    assert wrepo.created == []
