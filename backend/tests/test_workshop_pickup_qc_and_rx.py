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
    def __init__(self, seed=None):
        self.created = []
        # Jobs that already exist in the collection (dedup fixtures).
        self._jobs = list(seed or [])

    def find_by_id(self, job_id):
        return next((j for j in self._jobs if j.get("job_id") == job_id), None)

    def find_by_order(self, order_id):
        return [j for j in self._jobs if j.get("order_id") == order_id]

    def create(self, data):
        doc = dict(data)
        doc["job_id"] = f"JID-{len(self._jobs) + 1}"
        self.created.append(doc)
        self._jobs.append(doc)
        return doc

    def update(self, *_a, **_k):
        return True


class _OrderRepo:
    def __init__(self, order):
        self._order = order
        self.updates = []

    def find_by_id(self, order_id):
        if self._order and self._order.get("order_id") == order_id:
            return self._order
        return None

    def update(self, order_id, data):
        self.updates.append((order_id, dict(data)))
        if self._order and self._order.get("order_id") == order_id:
            self._order.update(data)
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


def _create_client(monkeypatch, rx_rows, roles=("SALES_STAFF",), order=None, seed=None):
    app = FastAPI()
    app.include_router(wm.router, prefix="/workshop")
    wrepo = _CreateWorkshopRepo(seed=seed)
    orepo = _OrderRepo(dict(_ORDER) if order is None else order)
    monkeypatch.setattr(wm, "get_workshop_repository", lambda: wrepo)
    monkeypatch.setattr(wm, "get_order_repository", lambda: orepo)
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
    return TestClient(app), wrepo, orepo


def _post(client, **overrides):
    body = dict(_PAYLOAD)
    body.update(overrides)
    return client.post("/workshop/jobs", json=body)


def test_create_rejects_unknown_prescription(monkeypatch):
    client, wrepo, _orepo = _create_client(monkeypatch, {})  # no Rx exists
    resp = _post(client)
    assert resp.status_code == 422, resp.text
    assert "not found" in resp.text.lower()
    assert wrepo.created == []  # nothing was written


def test_create_rejects_prescription_of_another_customer(monkeypatch):
    """WRONG PATIENT -- a hard error, never a warning: this Rx would be ground
    into someone else's lenses."""
    client, wrepo, _orepo = _create_client(monkeypatch, {"RX-1": _rx(customer_id="CUST-999")})
    resp = _post(client)
    assert resp.status_code == 422, resp.text
    assert "different customer" in resp.text.lower()
    assert wrepo.created == []


def test_create_accepts_matching_prescription(monkeypatch):
    client, wrepo, _orepo = _create_client(monkeypatch, {"RX-1": _rx()})
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1
    assert wrepo.created[0]["prescription_id"] == "RX-1"


def test_create_without_prescription_id_is_unchanged(monkeypatch):
    """No prescription supplied -> nothing to verify, behaviour untouched."""
    client, wrepo, _orepo = _create_client(monkeypatch, {})  # repo would find nothing
    resp = _post(client, prescription_id="")
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_failsoft_when_prescription_repo_unavailable(monkeypatch):
    """Fail-SOFT exactly where the canonical POS gate does: a clinical-store
    outage must not 500 or stall the bench."""
    client, wrepo, _orepo = _create_client(monkeypatch, None)  # repo is None
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_blocks_expired_prescription_for_junior_staff(monkeypatch):
    client, wrepo, _orepo = _create_client(
        monkeypatch, {"RX-1": _rx(months_ago=30)}, roles=("SALES_STAFF",)
    )
    resp = _post(client)
    assert resp.status_code == 422, resp.text
    assert "expired" in resp.text.lower()
    assert wrepo.created == []


def test_create_allows_expired_prescription_for_store_manager(monkeypatch):
    """Same expiry-override role list as the POS order path
    (orders._RX_EXPIRY_OVERRIDE_ROLES) -- Store-Manager and up may proceed."""
    client, wrepo, _orepo = _create_client(
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
    client, wrepo, _orepo = _create_client(monkeypatch, {"RX-1": rx})
    resp = _post(client)
    assert resp.status_code == 201, resp.text
    assert len(wrepo.created) == 1


def test_create_still_404s_on_unknown_order(monkeypatch):
    """Order resolution is unchanged and still runs BEFORE the Rx check."""
    client, wrepo, _orepo = _create_client(monkeypatch, {"RX-1": _rx()})
    resp = _post(client, order_id="ORD-NOPE")
    assert resp.status_code == 404, resp.text
    assert wrepo.created == []


# ===========================================================================
# FIX ROUND (adversarial patient-safety panel on PR #971)
# ===========================================================================


class TestGateFailsClosedOnUnknownStatus:
    """The gate used to test membership of the named pair {READY, DELIVERED},
    which fails OPEN: a station config pointing PICKUP at an arbitrary status
    (proven reachable by the panel) produced a target the check did not
    recognise, so the handover sailed through un-QC'd. The gate now enumerates
    the statuses that are provably NOT patient-facing and treats everything else
    as patient-facing."""

    def test_arbitrary_status_is_treated_as_patient_facing(self):
        assert (
            wm.evaluate_scan_transition_gate(None, _job(), "COLLECTED")
            == "QC_REQUIRED"
        )

    def test_arbitrary_status_allowed_once_qc_is_on_file(self):
        assert (
            wm.evaluate_scan_transition_gate(None, _job(qc_passed=True), "COLLECTED")
            is None
        )

    def test_bench_internal_target_is_not_qc_gated(self):
        """Requiring QC to mark work finished would invert the workflow."""
        assert (
            wm.evaluate_scan_transition_gate(
                None, _job(status="IN_PROGRESS"), "COMPLETED"
            )
            is None
        )

    def test_qc_failed_branch_stays_open(self):
        """Requiring QC in order to RECORD a QC failure would be circular."""
        assert (
            wm.evaluate_scan_transition_gate(
                None, _job(status="COMPLETED"), "QC_FAILED"
            )
            is None
        )


class TestQcAcceptsTheHeldState:
    """No lab station ever sets COMPLETED, so a job whose DISPATCH -> READY leg
    is HELD for missing QC parks at IN_PROGRESS. QC used to refuse exactly that
    state, making the gate a dead end at a live counter."""

    def test_qc_accepted_on_in_progress(self, monkeypatch):
        client, repo = _status_client(monkeypatch, _job(status="IN_PROGRESS"))
        resp = client.post("/workshop/jobs/J1/qc?passed=true")
        assert resp.status_code == 200, resp.text
        assert repo.updated == "READY"

    def test_qc_checklist_accepted_on_in_progress(self, monkeypatch):
        client, repo = _status_client(monkeypatch, _job(status="IN_PROGRESS"))
        resp = client.post(
            "/workshop/jobs/J1/qc-checklist",
            json={"checklist": [{"key": "power", "label": "Power", "passed": True}]},
        )
        assert resp.status_code == 200, resp.text
        assert repo.updated == "READY"

    def test_held_job_can_be_qcd_then_delivered(self, monkeypatch):
        """End to end on the state the gate actually produces."""
        client, repo = _status_client(monkeypatch, _job(status="IN_PROGRESS"))
        assert client.post("/workshop/jobs/J1/qc?passed=true").status_code == 200
        resp = client.patch("/workshop/jobs/J1/status", json={"status": "DELIVERED"})
        assert resp.status_code == 200, resp.text
        assert repo.updated == "DELIVERED"

    def test_pending_still_refused(self, monkeypatch):
        """A QC pass routes to READY; allowing it from PENDING would skip the
        sales-confirm and Delivery-Challan gates on the -> IN_PROGRESS leg."""
        client, repo = _status_client(monkeypatch, _job(status="PENDING"))
        assert client.post("/workshop/jobs/J1/qc?passed=true").status_code == 400
        assert repo.updated is None

    def test_delivered_still_refused(self, monkeypatch):
        """A handed-over job must not be retro-QC'd."""
        client, repo = _status_client(monkeypatch, _job(status="DELIVERED"))
        assert client.post("/workshop/jobs/J1/qc?passed=true").status_code == 400
        assert repo.updated is None


class _FakeStationCollection:
    def __init__(self):
        self.docs: list = []

    def count_documents(self, flt):
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in flt.items()))

    def find_one(self, flt):
        return next(
            (d for d in self.docs if all(d.get(k) == v for k, v in flt.items())), None
        )

    def find(self, flt=None):
        flt = flt or {}
        return [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())]

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("station_id")})()

    def update_one(self, flt, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set") or {})
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class _FakeStationDb:
    def __init__(self):
        self._cols: Dict[str, _FakeStationCollection] = {}

    def get_collection(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeStationCollection()
        return self._cols[name]


class TestStationConfigCannotDisableTheGate:
    """advances_job_status is written straight into workshop_jobs.status by a
    scan and is editable at STORE_MANAGER tier, so an unvalidated value let a
    manager point PICKUP at an arbitrary status -- switching a patient-safety
    gate off by config, with no audit row."""

    def test_arbitrary_advance_status_rejected(self):
        from api.services import lab_routing

        ok, station, reason = lab_routing.upsert_station(
            _FakeStationDb(),
            store_id="BV-TEST-01",
            code="PICKUP",
            advances_job_status="COLLECTED",
        )
        assert ok is False
        assert station is None
        assert reason == "INVALID_ADVANCE_STATUS"

    def test_canonical_values_still_accepted(self):
        from api.services import lab_routing

        for value in lab_routing.VALID_ADVANCES_JOB_STATUS:
            ok, _station, reason = lab_routing.upsert_station(
                _FakeStationDb(),
                store_id="BV-TEST-01",
                code="PICKUP",
                advances_job_status=value,
            )
            assert ok is True, f"{value} rejected: {reason}"

    def test_blank_still_clears_the_flag(self):
        """The documented way to make a station status-neutral; not a bypass."""
        from api.services import lab_routing

        ok, station, _reason = lab_routing.upsert_station(
            _FakeStationDb(),
            store_id="BV-TEST-01",
            code="PICKUP",
            advances_job_status="",
        )
        assert ok is True
        assert station.get("advances_job_status") is None

    def test_every_permitted_value_is_a_known_workshop_status(self):
        """Nothing outside the workshop state machine can reach
        workshop_jobs.status via a station scan."""
        from api.services import lab_routing

        assert "COLLECTED" not in lab_routing.VALID_ADVANCES_JOB_STATUS
        for value in lab_routing.VALID_ADVANCES_JOB_STATUS:
            assert value in wm.VALID_JOB_TRANSITIONS


class TestRxScopeMirrorsTheOrderGate:
    """orders._validate_order_line_rx exempts frame-only and contact-lens lines
    via is_rx_required_line (owner decision 2026-06-18), so running the EXPIRY
    branch unconditionally 422'd a job the order path had deliberately passed --
    and it fired AFTER the money was taken."""

    @staticmethod
    def _order_with(items):
        return {
            "order_id": "ORD-1",
            "customer_id": "CUST-1",
            "store_id": "BV-TEST-01",
            "items": items,
        }

    def test_frame_only_order_not_blocked_by_expired_rx(self, monkeypatch):
        client, wrepo, _orepo = _create_client(
            monkeypatch,
            {"RX-1": _rx(months_ago=30)},
            roles=("SALES_STAFF",),
            order=self._order_with([{"item_type": "FRAME", "product_id": "F1"}]),
        )
        resp = _post(client)
        assert resp.status_code == 201, resp.text
        assert len(wrepo.created) == 1

    def test_contact_lens_order_not_blocked_by_expired_rx(self, monkeypatch):
        client, wrepo, _orepo = _create_client(
            monkeypatch,
            {"RX-1": _rx(months_ago=30)},
            roles=("SALES_STAFF",),
            order=self._order_with([{"item_type": "CONTACT_LENS", "product_id": "CL1"}]),
        )
        assert _post(client).status_code == 201
        assert len(wrepo.created) == 1

    def test_spectacle_lens_order_still_blocked_by_expired_rx(self, monkeypatch):
        client, wrepo, _orepo = _create_client(
            monkeypatch,
            {"RX-1": _rx(months_ago=30)},
            roles=("SALES_STAFF",),
            order=self._order_with([{"item_type": "OPTICAL_LENS", "product_id": "L1"}]),
        )
        resp = _post(client)
        assert resp.status_code == 422, resp.text
        assert "expired" in resp.text.lower()
        assert wrepo.created == []

    def test_wrong_patient_is_hard_even_on_a_frame_only_order(self, monkeypatch):
        """Scope only relaxes EXPIRY. A wrong-customer Rx is wrong for a frame or
        contact-lens job too, and blocking it cannot false-block a correct one."""
        client, wrepo, _orepo = _create_client(
            monkeypatch,
            {"RX-1": _rx(customer_id="CUST-999")},
            order=self._order_with([{"item_type": "FRAME", "product_id": "F1"}]),
        )
        resp = _post(client)
        assert resp.status_code == 422, resp.text
        assert "different customer" in resp.text.lower()
        assert wrepo.created == []

    def test_unclassifiable_order_still_expiry_checked(self, monkeypatch):
        """Fail-SAFE: an order with no readable lines keeps the expiry gate."""
        client, _wrepo, _orepo = _create_client(
            monkeypatch,
            {"RX-1": _rx(months_ago=30)},
            roles=("SALES_STAFF",),
            order={"order_id": "ORD-1", "customer_id": "CUST-1", "store_id": "BV-TEST-01"},
        )
        assert _post(client).status_code == 422


class TestCreateJobIsIdempotentPerOrder:
    """create_job had NO dedup while the payment auto-confirm safety net does, so
    the default POS prescription sale created TWO PENDING lab jobs -- a duplicate
    lens grind and a duplicate external-lab order, in real rupees."""

    def test_second_call_returns_the_same_job(self, monkeypatch):
        client, wrepo, _orepo = _create_client(monkeypatch, {"RX-1": _rx()})
        first = _post(client)
        assert first.status_code == 201, first.text
        second = _post(client)
        assert second.status_code == 200, second.text
        assert second.json()["job_id"] == first.json()["job_id"]
        assert second.json()["existing"] is True
        assert len(wrepo.created) == 1
        assert len(wrepo.find_by_order("ORD-1")) == 1

    def test_pos_sequence_yields_one_job_matching_the_order_pointer(self, monkeypatch):
        """The real counter path: the payment auto-confirm safety net creates the
        job and stamps order.workshop_job_id, THEN the client calls create_job.
        The Rx must land on the job the ORDER points at."""
        seeded = {
            "job_id": "JID-SAFETY-NET",
            "job_number": "WS-SAFETY-NET",
            "order_id": "ORD-1",
            "status": "PENDING",
        }
        order = dict(_ORDER)
        order["workshop_job_id"] = "JID-SAFETY-NET"
        client, wrepo, _orepo = _create_client(
            monkeypatch, {"RX-1": _rx()}, order=order, seed=[seeded]
        )
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["job_id"] == "JID-SAFETY-NET" == order["workshop_job_id"]
        assert wrepo.created == []
        assert len(wrepo.find_by_order("ORD-1")) == 1

    def test_dedup_backfills_a_missing_reverse_pointer(self, monkeypatch):
        seeded = {
            "job_id": "JID-EXISTING",
            "job_number": "WS-EXISTING",
            "order_id": "ORD-1",
            "status": "PENDING",
        }
        client, _wrepo, orepo = _create_client(
            monkeypatch, {"RX-1": _rx()}, order=dict(_ORDER), seed=[seeded]
        )
        assert _post(client).json()["job_id"] == "JID-EXISTING"
        assert (
            "ORD-1",
            {
                "workshop_job_id": "JID-EXISTING",
                "workshop_job_number": "WS-EXISTING",
            },
        ) in orepo.updates

    def test_duplicate_call_with_bad_rx_returns_existing_instead_of_422(self, monkeypatch):
        """ORDERING is deliberate: the dedup runs BEFORE Rx verification. A repeat
        request is a duplicate REQUEST, not a new clinical decision -- it must
        harmlessly return the existing job rather than 422."""
        seeded = {
            "job_id": "JID-EXISTING",
            "job_number": "WS-EXISTING",
            "order_id": "ORD-1",
            "status": "PENDING",
        }
        client, wrepo, _orepo = _create_client(
            monkeypatch, {}, order=dict(_ORDER), seed=[seeded]  # Rx does NOT exist
        )
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["job_id"] == "JID-EXISTING"
        assert wrepo.created == []

    def test_prefers_the_pointed_job_when_duplicates_already_exist(self, monkeypatch):
        """1 of 4 LIVE prod jobs is already a duplicate pair created before this
        dedup existed, so the tie-break is real. Rule: return the job the ORDER
        points at; handing back the other one is the exact harm being fixed."""
        older = {
            "job_id": "JID-OLD", "job_number": "WS-OLD", "order_id": "ORD-1",
            "status": "PENDING", "created_at": "2026-08-01T10:00:00",
        }
        newer = {
            "job_id": "JID-NEW", "job_number": "WS-NEW", "order_id": "ORD-1",
            "status": "PENDING", "created_at": "2026-08-02T10:00:00",
        }
        order = dict(_ORDER)
        order["workshop_job_id"] = "JID-NEW"
        client, wrepo, _orepo = _create_client(
            monkeypatch, {"RX-1": _rx()}, order=order, seed=[older, newer]
        )
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["job_id"] == "JID-NEW"
        assert wrepo.created == []

    def test_falls_back_to_the_oldest_duplicate_when_the_pointer_is_stale(self, monkeypatch):
        """Deterministic tie-break -- find_by_order does not sort, so an
        unpointed (or dangling-pointer) duplicate pair must not resolve by luck."""
        older = {
            "job_id": "JID-OLD", "job_number": "WS-OLD", "order_id": "ORD-1",
            "status": "PENDING", "created_at": "2026-08-01T10:00:00",
        }
        newer = {
            "job_id": "JID-NEW", "job_number": "WS-NEW", "order_id": "ORD-1",
            "status": "PENDING", "created_at": "2026-08-02T10:00:00",
        }
        order = dict(_ORDER)
        order["workshop_job_id"] = "JID-GONE"  # points at a job that no longer exists
        client, _wrepo, _orepo = _create_client(
            monkeypatch, {"RX-1": _rx()}, order=order, seed=[newer, older]
        )
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["job_id"] == "JID-OLD"

    def test_a_different_order_still_gets_its_own_job(self, monkeypatch):
        seeded = {
            "job_id": "JID-OTHER",
            "job_number": "WS-OTHER",
            "order_id": "ORD-OTHER",
            "status": "PENDING",
        }
        client, wrepo, _orepo = _create_client(
            monkeypatch, {"RX-1": _rx()}, seed=[seeded]
        )
        resp = _post(client)
        assert resp.status_code == 201, resp.text
        assert len(wrepo.created) == 1
        assert wrepo.created[0]["order_id"] == "ORD-1"
