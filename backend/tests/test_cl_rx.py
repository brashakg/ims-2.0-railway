"""
IMS 2.0 - Contact-lens (CL) prescription module
================================================
Covers the additive CL Rx support layered onto the spectacle prescriptions
router (api/routers/prescriptions.py):

1. rx_kind defaulting -- a payload that omits rx_kind is persisted as
   SPECTACLE, so every pre-existing Rx + create call behaves identically.
2. CONTACT_LENS create -- CL block + top-level CL fields persist, with CL
   validation (modality enum + per-eye axis 0-180) instead of spectacle powers.
3. List filter by rx_kind -- GET /prescriptions?rx_kind=CONTACT_LENS returns
   only CL docs; legacy docs with no rx_kind count as SPECTACLE.
4. Print -- the print endpoint renders a CL card (brand/modality/BC/DIA, no PD)
   when rx_kind=CONTACT_LENS, else the existing spectacle card.

Style mirrors test_clinical_rx.py: a bare FastAPI app, get_current_user
dependency-overridden, and the repo getters monkeypatched to in-memory fakes.
No live DB.
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


# ============================================================================
# Fakes -- minimal in-memory stand-ins for the repos the router calls.
# ============================================================================


class _FakeRxRepo:
    """In-memory PrescriptionRepository. Captures created docs so a test can
    assert on what got persisted, and supports the find_* methods the list
    endpoint uses."""

    def __init__(self, seed=None):
        self.docs = list(seed or [])
        self.last_created = None

    def create(self, data):
        doc = dict(data)
        doc.setdefault("prescription_id", f"rx-{len(self.docs) + 1}")
        self.docs.append(doc)
        self.last_created = doc
        return doc

    def find_by_id(self, _id):
        for d in self.docs:
            if d.get("prescription_id") == _id:
                return d
        return None

    def find_by_patient(self, patient_id, limit=None):
        out = [d for d in self.docs if d.get("patient_id") == patient_id]
        return out[:limit] if limit else out

    def find_by_customer(self, customer_id):
        return [d for d in self.docs if d.get("customer_id") == customer_id]

    def find_by_store(self, store_id, from_date=None, to_date=None):
        return [d for d in self.docs if d.get("store_id") == store_id]

    def find_many(self, _query, skip=0, limit=50):
        return self.docs[skip: skip + limit]


class _FakeCustomerRepo:
    def find_by_id(self, _id):
        # Any non-walkin customer id resolves to a real customer.
        return {"customer_id": _id, "name": "Test Customer"}


# ============================================================================
# Client builder
# ============================================================================


def _client(monkeypatch, roles, rx_repo, customer_repo=None):
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
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(
        prescriptions,
        "get_customer_repository",
        lambda: customer_repo if customer_repo is not None else _FakeCustomerRepo(),
    )
    return TestClient(app)


# optometrist_id is required for a TESTED_AT_STORE Rx unless the user is an
# admin/manager; the OPTOMETRIST role used in these tests is not, so supply it.
_SPECTACLE_BODY = {
    "patient_id": "p1",
    "customer_id": "c1",
    "optometrist_id": "opt-1",
    "right_eye": {"sph": "-1.25", "cyl": "-0.50", "axis": 90},
    "left_eye": {"sph": "-1.00"},
}

_CL_BODY = {
    "patient_id": "p1",
    "customer_id": "c1",
    "optometrist_id": "opt-1",
    "rx_kind": "CONTACT_LENS",
    "cl_brand": "Acuvue",
    "cl_series": "Oasys",
    "modality": "FORTNIGHTLY",
    "cl_right": {
        "cl_power": -2.25,
        "base_curve": 8.6,
        "diameter": 14.2,
    },
    "cl_left": {
        "cl_power": -2.0,
        "cl_cyl": -0.75,
        "cl_axis": 180,
        "base_curve": 8.6,
        "diameter": 14.2,
    },
}


# ============================================================================
# 1. rx_kind defaulting -- existing Rx stays SPECTACLE
# ============================================================================


class TestRxKindDefaulting:
    def test_model_defaults_to_spectacle(self):
        m = prescriptions.PrescriptionCreate(patient_id="p", customer_id="c")
        assert m.rx_kind == "SPECTACLE"

    def test_spectacle_create_omitting_rx_kind_persists_spectacle(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        resp = client.post("/prescriptions", json=_SPECTACLE_BODY)
        assert resp.status_code == 201
        assert repo.last_created["rx_kind"] == "SPECTACLE"
        # No CL block leaks onto a spectacle doc.
        assert "cl_right" not in repo.last_created
        assert "cl_brand" not in repo.last_created

    def test_spectacle_power_validation_still_enforced(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        bad = dict(_SPECTACLE_BODY, right_eye={"sph": "-99"})
        resp = client.post("/prescriptions", json=bad)
        assert resp.status_code == 422


# ============================================================================
# 2. CONTACT_LENS create + validation
# ============================================================================


class TestContactLensCreate:
    def test_cl_create_persists_kind_and_block(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        resp = client.post("/prescriptions", json=_CL_BODY)
        assert resp.status_code == 201
        doc = repo.last_created
        assert doc["rx_kind"] == "CONTACT_LENS"
        assert doc["cl_brand"] == "Acuvue"
        assert doc["cl_series"] == "Oasys"
        assert doc["modality"] == "FORTNIGHTLY"
        assert doc["cl_right"]["cl_power"] == -2.25
        assert doc["cl_right"]["base_curve"] == 8.6
        assert doc["cl_left"]["cl_cyl"] == -0.75
        assert doc["cl_left"]["cl_axis"] == 180
        # Spectacle eyes still present (empty) so readers never KeyError.
        assert doc["right_eye"]["sph"] is None

    def test_cl_create_role_gated(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["SALES_STAFF"], repo)
        resp = client.post("/prescriptions", json=_CL_BODY)
        assert resp.status_code == 403

    def test_cl_bad_modality_rejected(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        bad = dict(_CL_BODY, modality="WEEKLY")
        resp = client.post("/prescriptions", json=bad)
        assert resp.status_code == 422
        assert "modality" in resp.json()["detail"].lower()

    def test_cl_axis_out_of_range_rejected(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        # axis 200 fails pydantic Field(le=180) -> 422 before the handler.
        bad = dict(
            _CL_BODY,
            cl_left={"cl_power": -2.0, "cl_cyl": -0.75, "cl_axis": 200},
        )
        resp = client.post("/prescriptions", json=bad)
        assert resp.status_code == 422

    def test_cl_power_out_of_range_rejected(self, monkeypatch):
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        bad = dict(_CL_BODY, cl_right={"cl_power": -99.0})
        resp = client.post("/prescriptions", json=bad)
        assert resp.status_code == 422

    def test_cl_create_without_modality_ok(self, monkeypatch):
        # modality is optional -- a CL Rx may be entered without it.
        repo = _FakeRxRepo()
        client = _client(monkeypatch, ["OPTOMETRIST"], repo)
        body = dict(_CL_BODY)
        body.pop("modality")
        resp = client.post("/prescriptions", json=body)
        assert resp.status_code == 201
        assert repo.last_created["modality"] is None


# ============================================================================
# 3. List filter by rx_kind
# ============================================================================


class TestListFilter:
    def _seed(self):
        return _FakeRxRepo(
            [
                {"prescription_id": "rx-spec", "customer_id": "c1", "store_id": "store-001", "rx_kind": "SPECTACLE"},
                {"prescription_id": "rx-cl", "customer_id": "c1", "store_id": "store-001", "rx_kind": "CONTACT_LENS"},
                # Legacy doc with no rx_kind -> must be treated as SPECTACLE.
                {"prescription_id": "rx-legacy", "customer_id": "c1", "store_id": "store-001"},
            ]
        )

    def test_filter_contact_lens(self, monkeypatch):
        client = _client(monkeypatch, ["OPTOMETRIST"], self._seed())
        resp = client.get("/prescriptions", params={"customer_id": "c1", "rx_kind": "CONTACT_LENS"})
        assert resp.status_code == 200
        ids = [p["prescription_id"] for p in resp.json()["prescriptions"]]
        assert ids == ["rx-cl"]

    def test_filter_spectacle_includes_legacy(self, monkeypatch):
        client = _client(monkeypatch, ["OPTOMETRIST"], self._seed())
        resp = client.get("/prescriptions", params={"customer_id": "c1", "rx_kind": "SPECTACLE"})
        assert resp.status_code == 200
        ids = sorted(p["prescription_id"] for p in resp.json()["prescriptions"])
        assert ids == ["rx-legacy", "rx-spec"]

    def test_no_filter_returns_all(self, monkeypatch):
        client = _client(monkeypatch, ["OPTOMETRIST"], self._seed())
        resp = client.get("/prescriptions", params={"customer_id": "c1"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 3


# ============================================================================
# 4. Print renders a CL card for a CONTACT_LENS Rx
# ============================================================================


class TestPrint:
    def test_print_cl_card(self, monkeypatch):
        repo = _FakeRxRepo(
            [
                {
                    "prescription_id": "rx-cl",
                    "store_id": "store-001",
                    "prescription_number": "RX-260524-CL0001",
                    "rx_kind": "CONTACT_LENS",
                    "cl_brand": "Acuvue",
                    "cl_series": "Oasys",
                    "modality": "FORTNIGHTLY",
                    "cl_right": {"cl_power": -2.25, "base_curve": 8.6, "diameter": 14.2},
                    "cl_left": {"cl_power": -2.0, "cl_cyl": -0.75, "cl_axis": 180},
                    "prescription_date": "2026-05-24T10:00:00",
                    "expiry_date": "2027-05-24T10:00:00",
                }
            ]
        )
        client = _client(monkeypatch, ["SALES_STAFF"], repo)
        resp = client.get("/prescriptions/rx-cl/print")
        assert resp.status_code == 200
        html = resp.json()["html"]
        assert "Contact Lens Prescription" in html
        assert "Acuvue" in html
        assert "FORTNIGHTLY" in html
        assert "-2.25" in html  # RE power
        assert "8.6" in html  # RE base curve
        assert "BC" in html and "DIA" in html  # CL-specific columns
        assert "PD" not in html  # a CL card has no PD column

    def test_print_spectacle_card_unchanged(self, monkeypatch):
        repo = _FakeRxRepo(
            [
                {
                    "prescription_id": "rx-spec",
                    "store_id": "store-001",
                    "prescription_number": "RX-260524-SP0001",
                    "rx_kind": "SPECTACLE",
                    "right_eye": {"sph": "-1.25", "cyl": "-0.50", "axis": 90, "pd": "32"},
                    "left_eye": {"sph": "-1.00"},
                    "prescription_date": "2026-05-24T10:00:00",
                    "expiry_date": "2027-05-24T10:00:00",
                }
            ]
        )
        client = _client(monkeypatch, ["SALES_STAFF"], repo)
        resp = client.get("/prescriptions/rx-spec/print")
        assert resp.status_code == 200
        html = resp.json()["html"]
        assert "Eye Prescription" in html
        assert "Contact Lens Prescription" not in html
        assert "PD" in html  # spectacle card keeps the PD column
        assert "-1.25" in html

    def test_print_legacy_doc_defaults_spectacle(self, monkeypatch):
        # No rx_kind on the doc -> spectacle card (back-compat).
        repo = _FakeRxRepo(
            [
                {
                    "prescription_id": "rx-legacy",
                    "store_id": "store-001",
                    "prescription_number": "RX-OLD",
                    "right_eye": {"sph": "0"},
                    "left_eye": {},
                    "prescription_date": "2026-01-01T10:00:00",
                    "expiry_date": "2027-01-01T10:00:00",
                }
            ]
        )
        client = _client(monkeypatch, ["SALES_STAFF"], repo)
        resp = client.get("/prescriptions/rx-legacy/print")
        assert resp.status_code == 200
        assert "Eye Prescription" in resp.json()["html"]


# ============================================================================
# 5. Pure HTML builders (no app)
# ============================================================================


class TestPureBuilders:
    def test_cl_builder_blanks_missing_cells(self):
        html = prescriptions._build_cl_print_html(
            {"prescription_number": "X", "rx_kind": "CONTACT_LENS"}
        )
        assert "Contact Lens Prescription" in html
        # Missing brand/power render as '-' not a crash.
        assert "<html>" in html

    def test_cl_builder_includes_color_when_present(self):
        html = prescriptions._build_cl_print_html(
            {"prescription_number": "X", "color": "Hazel", "modality": "COLOR"}
        )
        assert "Hazel" in html

    def test_spectacle_builder_blanks_missing(self):
        html = prescriptions._build_spectacle_print_html({"prescription_number": "X"})
        assert "Eye Prescription" in html
        assert "<html>" in html


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
