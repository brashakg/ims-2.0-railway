"""
IMS 2.0 - Prescription EDIT endpoint (PUT /api/v1/prescriptions/{id})
====================================================================
Covers the new clinic Edit path:

  * role gate mirrors create_prescription (clinical roles only; sales -> 403)
  * a valid partial edit returns 200 and persists ONLY the supplied keys
  * an out-of-range Rx power (SPH/CYL/ADD off the clinical grid) is rejected
    with 400 (deliberate business rejection, reusing `_validate_rx_value`)
  * an off-grid 0.25-step value is rejected 400
  * a 404 surfaces for a missing prescription
  * identity/provenance is never reassigned (patient_id/customer_id immutable)

Mirrors the bare-app + dependency-override pattern in test_clinical_rx.py so no
real database is required (an in-memory fake repo stands in).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _FakeRxRepo:
    """Minimal stand-in for PrescriptionRepository: in-memory doc + update."""

    def __init__(self, doc):
        self._doc = doc

    def find_by_id(self, _id):
        if self._doc and self._doc.get("prescription_id") == _id:
            return dict(self._doc)
        return None

    def update(self, _id, data):
        if not self._doc or self._doc.get("prescription_id") != _id:
            return False
        self._doc.update(data)
        return True


def _seed_doc():
    return {
        "prescription_id": "rx-1",
        "prescription_number": "RX-260530-ABC123",
        "patient_id": "pat-1",
        "customer_id": "cust-1",
        "store_id": "store-001",
        "rx_kind": "SPECTACLE",
        "source": "TESTED_AT_STORE",
        "optometrist_id": "opt-1",
        "prescription_date": "2026-05-01T10:00:00",
        "expiry_date": "2027-05-01T10:00:00",
        "validity_months": 12,
        "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90, "add": "0", "pd": "32"},
        "left_eye": {"sph": "-1.25", "cyl": "-0.25", "axis": 85, "add": "0", "pd": "32"},
        "remarks": "initial",
        "created_by": "opt-1",
    }


def _client(roles, repo, monkeypatch):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Dr Test",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: repo)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Role gating (mirrors create_prescription)
# ---------------------------------------------------------------------------

class TestEditGating:
    def test_sales_staff_blocked(self, monkeypatch):
        client = _client(["SALES_STAFF"], _FakeRxRepo(_seed_doc()), monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"remarks": "x"})
        assert resp.status_code == 403

    def test_cashier_blocked(self, monkeypatch):
        client = _client(["CASHIER"], _FakeRxRepo(_seed_doc()), monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"remarks": "x"})
        assert resp.status_code == 403

    def test_optometrist_allowed(self, monkeypatch):
        client = _client(["OPTOMETRIST"], _FakeRxRepo(_seed_doc()), monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"remarks": "x"})
        assert resp.status_code != 403

    def test_store_manager_allowed(self, monkeypatch):
        client = _client(["STORE_MANAGER"], _FakeRxRepo(_seed_doc()), monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"remarks": "x"})
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Valid edit -> 200, partial merge
# ---------------------------------------------------------------------------

class TestValidEdit:
    def test_valid_update_returns_200_and_persists(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put(
            "/prescriptions/rx-1",
            json={
                "right_eye": {"sph": "-2.00", "cyl": "-0.75", "axis": 100, "add": "0", "pd": "33"},
                "remarks": "rechecked",
                "lens_recommendation": "Progressive",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["prescription_id"] == "rx-1"
        # Persisted onto the doc.
        assert repo._doc["right_eye"]["sph"] == "-2.00"
        assert repo._doc["remarks"] == "rechecked"
        assert repo._doc["lens_recommendation"] == "Progressive"
        assert repo._doc.get("updated_by") == "u1"

    def test_partial_edit_does_not_blank_untouched_fields(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"remarks": "note only"})
        assert resp.status_code == 200
        # left_eye / right_eye untouched.
        assert repo._doc["left_eye"]["sph"] == "-1.25"
        assert repo._doc["right_eye"]["sph"] == "-1.00"

    def test_identity_fields_are_immutable(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        # patient_id/customer_id are not accepted by PrescriptionUpdate; the
        # extra keys are simply ignored and the original identity is preserved.
        resp = client.put(
            "/prescriptions/rx-1",
            json={"remarks": "x", "patient_id": "HACK", "customer_id": "HACK"},
        )
        assert resp.status_code == 200
        assert repo._doc["patient_id"] == "pat-1"
        assert repo._doc["customer_id"] == "cust-1"

    def test_validity_change_recomputes_expiry(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"validity_months": 6})
        assert resp.status_code == 200
        # expiry = prescription_date (2026-05-01) + 6 months.
        assert repo._doc["expiry_date"].startswith("2026-11-01")


# ---------------------------------------------------------------------------
# Out-of-range Rx -> 400
# ---------------------------------------------------------------------------

class TestOutOfRangeRejected:
    def test_sph_out_of_range_rejected_400(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put(
            "/prescriptions/rx-1",
            json={"right_eye": {"sph": "-25.00", "cyl": "0", "axis": 90}},
        )
        assert resp.status_code == 400, resp.text
        # The doc was NOT mutated by a rejected edit.
        assert repo._doc["right_eye"]["sph"] == "-1.00"

    def test_cyl_out_of_range_rejected_400(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put(
            "/prescriptions/rx-1",
            json={"left_eye": {"sph": "0", "cyl": "-9.00", "axis": 90}},
        )
        assert resp.status_code == 400

    def test_off_step_value_rejected_400(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        # +1.30 is not on the 0.25-diopter grid.
        resp = client.put(
            "/prescriptions/rx-1",
            json={"right_eye": {"sph": "1.30", "cyl": "0", "axis": 90}},
        )
        assert resp.status_code == 400

    def test_axis_out_of_range_rejected(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        # axis 200 fails the EyeDataEdit Field(le=180) -> 422 body-parse error.
        resp = client.put(
            "/prescriptions/rx-1",
            json={"right_eye": {"sph": "0", "cyl": "0", "axis": 200}},
        )
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 404 / no-op
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_prescription_404(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put("/prescriptions/nope", json={"remarks": "x"})
        assert resp.status_code == 404

    def test_empty_body_rejected_400(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={})
        assert resp.status_code == 400

    def test_no_db_returns_503(self, monkeypatch):
        client = _client(["OPTOMETRIST"], None, monkeypatch)
        resp = client.put("/prescriptions/rx-1", json={"remarks": "x"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Advisory /validate path (GET /prescriptions/{id}/validate)
# ---------------------------------------------------------------------------
# This path REPORTS issues on already-stored Rx values (it never blocks a
# write). Bug: the AXIS check used int(float(axis)), truncating 90.5 -> 90 so a
# non-whole axis slipped through as "valid". AXIS must be a whole degree 1..180.


def _doc_with_eyes(right_eye, left_eye):
    doc = _seed_doc()
    doc["right_eye"] = right_eye
    doc["left_eye"] = left_eye
    return doc


class TestValidateAdvisory:
    def test_clean_rx_reports_valid(self, monkeypatch):
        repo = _FakeRxRepo(_seed_doc())
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.get("/prescriptions/rx-1/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["issues"] == []

    def test_fractional_axis_flagged_as_non_whole(self, monkeypatch):
        # 90.5 is in 1..180 but NOT a whole number -> must be flagged.
        repo = _FakeRxRepo(
            _doc_with_eyes(
                {"sph": "-1.00", "cyl": "-0.50", "axis": 90.5, "add": "0"},
                {"sph": "-1.00", "cyl": "-0.50", "axis": 85, "add": "0"},
            )
        )
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.get("/prescriptions/rx-1/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert any("whole number" in iss for iss in body["issues"])

    def test_axis_out_of_range_still_flagged(self, monkeypatch):
        repo = _FakeRxRepo(
            _doc_with_eyes(
                {"sph": "-1.00", "cyl": "-0.50", "axis": 200, "add": "0"},
                {"sph": "-1.00", "cyl": "-0.50", "axis": 85, "add": "0"},
            )
        )
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.get("/prescriptions/rx-1/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert any("out of range (1-180)" in iss for iss in body["issues"])

    def test_sph_out_of_range_flagged(self, monkeypatch):
        repo = _FakeRxRepo(
            _doc_with_eyes(
                {"sph": "99", "cyl": "-0.50", "axis": 90, "add": "0"},
                {"sph": "-1.00", "cyl": "-0.50", "axis": 85, "add": "0"},
            )
        )
        client = _client(["OPTOMETRIST"], repo, monkeypatch)
        resp = client.get("/prescriptions/rx-1/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert any("SPH" in iss for iss in body["issues"])
