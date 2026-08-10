"""
IMS 2.0 - PATIENT SAFETY / AUDIT: per-eye axis provenance (axis_source)
======================================================================
POS used to fabricate an axis of 180 when a prescription carried none. It now
asks the counter for the axis instead, and stamps WHERE THAT AXIS CAME FROM on
the eye, as `EyeData.axis_source = "COUNTER_ENTERED"`.

Why the eye and not `remarks` (this is the whole point of the field):

  * `remarks` is projected to the OTP-gated CUSTOMER portal by
    portal._safe_prescription_view as "notes" and rendered by RxPortalPage, and
    it is printed on the patient-facing Rx card. Provenance there tells the
    PATIENT their axis was supplied at the counter.
  * No internal staff screen renders `remarks` at all, so the optician handling
    the remake dispute the marker exists for could never see it.
  * `remarks` is also a single free-text blob - it cannot say WHICH eye - and
    PrescriptionUpdate.remarks $sets it wholesale, so any later edit erases it.

These pin the carrier so it cannot drift back.  ASCII only (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


COUNTER = "COUNTER_ENTERED"


class _CapturingRepo:
    """Captures exactly what the create door persists."""

    def __init__(self):
        self.created = []

    def create(self, data):
        doc = dict(data)
        doc.setdefault("prescription_id", f"rx-{len(self.created) + 1}")
        doc.setdefault("prescription_number", "RX-TEST-000001")
        self.created.append(doc)
        return doc


def _client(monkeypatch, repo, roles=("OPTOMETRIST",)):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": "u-counter",
            "username": "counter",
            "full_name": "Counter Staff",
            "active_store_id": "store-001",
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: None)
    return TestClient(app)


def _body(**eyes):
    base = {
        "patient_id": "pat-1",
        "customer_id": "cust-1",
        "optometrist_id": "opt-1",
    }
    base.update(eyes)
    return base


class TestAxisSourcePersists:
    def test_counter_entered_axis_is_stored_on_that_eye(self, monkeypatch):
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={
                    "sph": "-2.00",
                    "cyl": "-1.25",
                    "axis": 85,
                    "axis_source": COUNTER,
                },
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        # Persisted automatically by right_eye.model_dump() -- no router change.
        assert doc["right_eye"]["axis_source"] == COUNTER
        assert doc["right_eye"]["axis"] == 85

    def test_the_other_eye_is_not_marked(self, monkeypatch):
        """Per-eye is the point: only the eye that was typed in is flagged."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90},
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        assert doc["right_eye"]["axis_source"] == COUNTER
        assert doc["left_eye"]["axis_source"] is None

    def test_a_clinician_recorded_rx_carries_no_marker(self, monkeypatch):
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85},
                left_eye={"sph": "-1.00", "cyl": "-0.75", "axis": 90},
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        assert doc["right_eye"]["axis_source"] is None
        assert doc["left_eye"]["axis_source"] is None


class TestProvenanceStaysOutOfPatientFacingFields:
    def test_marker_never_lands_in_remarks(self, monkeypatch):
        """`remarks` reaches the customer portal and the printed Rx card."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
                remarks="Dr. Rao",
            ),
        )
        assert resp.status_code == 201, resp.text
        doc = repo.created[0]
        assert doc["remarks"] == "Dr. Rao"
        # Nothing patient-reachable mentions the counter entry.
        for field in ("remarks", "lens_recommendation", "coating_recommendation", "ipd"):
            assert COUNTER not in str(doc.get(field) or "")


class TestAxisSourceIsAClosedVocabulary:
    @pytest.mark.parametrize("bad", ["counter_entered", "GUESSED", "", "COUNTER ENTERED"])
    def test_an_unknown_source_is_rejected_not_stored(self, monkeypatch, bad):
        """A Literal, not a bare str: a typo can never masquerade as provenance
        (and a silent rename would break loudly here rather than orphan rows)."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis": 85, "axis_source": bad},
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 422, resp.text
        assert repo.created == []

    def test_the_toric_axis_gate_is_unchanged_by_the_new_field(self, monkeypatch):
        """A cylinder with no axis is still rejected -- provenance is metadata,
        never a way to smuggle an axis-less toric past the clinical gate."""
        repo = _CapturingRepo()
        client = _client(monkeypatch, repo)
        resp = client.post(
            "/prescriptions",
            json=_body(
                right_eye={"sph": "-2.00", "cyl": "-1.25", "axis_source": COUNTER},
                left_eye={"sph": "-1.00", "cyl": "0"},
            ),
        )
        assert resp.status_code == 422, resp.text
        assert "axis" in resp.text
        assert repo.created == []
