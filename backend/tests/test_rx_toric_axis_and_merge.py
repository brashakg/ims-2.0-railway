"""
IMS 2.0 - PATIENT SAFETY regressions on the clinical Rx write paths
===================================================================
Four findings, all on backend/api/routers/clinical.py + prescriptions.py:

F11  Toric Rx saved with NO axis. A non-zero CYL (astigmatism) is ground at a
     specific axis; without one the lens is un-grindable, so the lab guesses an
     axis -> headaches / blur / remake. Every eye-capture path must reject it,
     naming the eye and the cylinder. A zero / absent CYL is unaffected.

F20  Axis rounded for VALIDATION (int(round(90.5)) -> 91) but the RAW 90.5 was
     stored, so the stored Rx and the whole-degree workshop spec disagreed. The
     axis must be a whole degree 1-180: a fractional axis is REJECTED, and what
     survives validation is stored as a whole int.

F12  PUT /prescriptions/{id} $set the WHOLE eye sub-document, so a one-field eye
     edit silently BLANKED that eye's other powers (sph/cyl/axis/add/pd) and the
     corrected Rx then dispensed with missing values. The eyes are now
     deep-merged with the stored document at the router (the shared repository's
     $set semantics are untouched - every module depends on them).

F19  GET /prescriptions/{id}/validate carried its own stale copy of the limits
     (SPH +/-20, ADD <= 3.50) and reported legitimate high-power patients as
     "out of range". It now uses the canonical rx_validation limits (SPH +/-25,
     ADD 4.00) like every other path.

Bare-app + dependency-override / monkeypatch harness (mirrors
test_clinical_lifecycle.py and test_prescriptions_update.py) - no DB required.
ASCII only (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical, prescriptions  # noqa: E402
from api.routers.clinical import (  # noqa: E402
    _axis_for_storage,
    _validate_eye_test_rx,
)
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# In-memory fakes + clients
# ============================================================================


class _FakeTestRepo:
    """Stand-in for EyeTestRepository: one test doc, completable once."""

    def __init__(self, doc):
        self._doc = doc
        self.complete_calls = 0

    def find_by_id(self, test_id):
        if self._doc and self._doc.get("test_id") == test_id:
            return dict(self._doc)
        return None

    def complete_test(self, test_id, right_eye, left_eye, pd=None, notes=None,
                      lens_recommendation=None, coating_recommendation=None,
                      clinical_findings=None, soap_note=None):
        if not self._doc or self._doc.get("test_id") != test_id:
            return False
        self.complete_calls += 1
        self._doc["status"] = "COMPLETED"
        self._doc["prescription"] = {"right_eye": right_eye, "left_eye": left_eye}
        return True


class _FakeQueueRepo:
    def update_status(self, queue_id, status):
        return True

    def find_by_id(self, queue_id):
        return {"queue_id": queue_id}

    def update(self, queue_id, data):
        return True


class _FakeRxRepo:
    """Stand-in for PrescriptionRepository (eye-test auto-create path)."""

    def __init__(self):
        self.created = []

    def find_by_eye_test(self, eye_test_id):
        for rx in self.created:
            if rx.get("eye_test_id") == eye_test_id:
                return rx
        return None

    def find_by_id(self, _id):
        for rx in self.created:
            if rx.get("prescription_id") == _id:
                return rx
        return None

    def create(self, data):
        doc = dict(data)
        # The real repository assigns the id when the create door omits it.
        doc.setdefault("prescription_id", f"rx-{len(self.created) + 1}")
        self.created.append(doc)
        return doc


class _FakeSingleRxRepo:
    """Stand-in for PrescriptionRepository holding ONE editable doc."""

    def __init__(self, doc):
        self._doc = doc
        self.updates = []

    def find_by_id(self, _id):
        if self._doc and self._doc.get("prescription_id") == _id:
            return dict(self._doc)
        return None

    def update(self, _id, data):
        if not self._doc or self._doc.get("prescription_id") != _id:
            return False
        self.updates.append(dict(data))
        self._doc.update(data)
        return True


def _clinical_client(monkeypatch, *, test_repo=None, queue_repo=None, rx_repo=None,
                     roles=("OPTOMETRIST",)):
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake_user():
        return {
            "user_id": "u-opto",
            "username": "opto",
            "full_name": "Dr Test",
            "active_store_id": "store-001",
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(clinical, "get_eye_test_repository", lambda: test_repo)
    monkeypatch.setattr(clinical, "get_eye_test_queue_repository", lambda: queue_repo)
    monkeypatch.setattr(clinical, "get_prescription_repository", lambda: rx_repo)
    return TestClient(app)


def _rx_client(monkeypatch, repo, roles=("OPTOMETRIST",)):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": "u-opto",
            "username": "opto",
            "full_name": "Dr Test",
            "active_store_id": "store-001",
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: repo)
    # No customer lookup in these tests -- the create door skips it when None.
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: None)
    return TestClient(app)


def _seed_rx_doc():
    """A stored spectacle Rx with BOTH eyes fully populated."""
    return {
        "prescription_id": "rx-1",
        "prescription_number": "RX-260808-ABC123",
        "patient_id": "pat-1",
        "customer_id": "cust-1",
        "store_id": "store-001",
        "rx_kind": "SPECTACLE",
        "source": "TESTED_AT_STORE",
        "optometrist_id": "opt-1",
        "prescription_date": "2026-05-01T10:00:00",
        "expiry_date": "2027-05-01T10:00:00",
        "validity_months": 12,
        "right_eye": {
            "sph": "-1.00", "cyl": "-0.50", "axis": 90, "add": "2.00", "pd": "32",
        },
        "left_eye": {
            "sph": "-1.25", "cyl": "-0.25", "axis": 85, "add": "2.00", "pd": "32",
        },
        "created_by": "opt-1",
    }


def _seed_cl_rx_doc():
    """A stored CONTACT-LENS Rx whose right eye is TORIC (cyl + axis)."""
    doc = _seed_rx_doc()
    doc["rx_kind"] = "CONTACT_LENS"
    doc["modality"] = "MONTHLY"
    doc["cl_right"] = {
        "cl_power": -3.0, "cl_cyl": -1.75, "cl_axis": 170,
        "base_curve": 8.6, "diameter": 14.2,
    }
    doc["cl_left"] = {
        "cl_power": -2.0, "cl_cyl": None, "cl_axis": None,
        "base_curve": 8.6, "diameter": 14.2,
    }
    return doc


def _eye_test_doc():
    return {
        "test_id": "t-1",
        "queue_id": "q-1",
        "status": "IN_PROGRESS",
        "customer_id": "c-1",
        "store_id": "store-001",
    }


# ============================================================================
# F11 - a toric Rx (non-zero CYL) MUST carry an axis
# ============================================================================


class TestF11ToricRequiresAxis:
    def test_validator_rejects_cyl_without_axis_naming_the_eye(self):
        with pytest.raises(HTTPException) as exc:
            _validate_eye_test_rx("Right eye", {"sph": "-1.00", "cyl": "-1.25"})
        assert exc.value.status_code == 422
        detail = exc.value.detail
        # Plain English, names the eye AND the cylinder the clinician typed.
        assert "Right eye" in detail
        assert "-1.25" in detail
        assert "no axis" in detail
        assert "1-180" in detail

    def test_validator_rejects_blank_axis_with_cyl(self):
        with pytest.raises(HTTPException) as exc:
            _validate_eye_test_rx("Left eye", {"cyl": "-0.75", "axis": "   "})
        assert exc.value.status_code == 422
        assert "Left eye" in exc.value.detail

    @pytest.mark.parametrize(
        "eye",
        [
            {"sph": "-1.00"},                          # no cyl key at all
            {"sph": "-1.00", "cyl": None},             # cyl not entered
            {"sph": "-1.00", "cyl": ""},               # cyl blank
            {"sph": "-1.00", "cyl": "0"},              # plano cyl
            {"sph": "-1.00", "cyl": "0.00"},           # plano cyl, 2dp
            {"sphere": "-1.00", "cylinder": 0},        # alias shape, numeric 0
        ],
    )
    def test_zero_or_absent_cyl_needs_no_axis(self, eye):
        # No exception: an eye with no astigmatism needs no axis.
        _validate_eye_test_rx("Right eye", eye)

    def test_toric_with_axis_passes(self):
        _validate_eye_test_rx("Right eye", {"cyl": "-1.25", "axis": 90})

    def test_complete_test_rejects_toric_without_axis(self, monkeypatch):
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": -1.25, "cylinder": -1.25, "axis": None},
                "leftEye": {"sphere": -1.00, "cylinder": 0, "axis": None},
                "pd": 62,
            },
        )
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        # Nothing persisted: no Rx minted, the test is NOT marked complete.
        assert rx_repo.created == []
        assert test_repo.complete_calls == 0
        assert test_repo._doc["status"] == "IN_PROGRESS"

    def test_complete_test_allows_plano_cyl_without_axis(self, monkeypatch):
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": -1.25, "cylinder": 0, "axis": None},
                "leftEye": {"sphere": -1.00, "cylinder": 0, "axis": None},
                "pd": 62,
            },
        )
        assert resp.status_code == 200, resp.text
        assert len(rx_repo.created) == 1
        assert rx_repo.created[0]["right_eye"]["axis"] is None

    def test_lens_power_combo_rejects_toric_without_axis(self, monkeypatch):
        # NOTE: this endpoint's role gate is malformed upstream
        # (require_roles(_CLINICAL_ROLES) passes the tuple as ONE role), so only
        # SUPERADMIN's hardcoded bypass gets through today. Left alone on
        # purpose -- an RBAC change does not belong in a clinical-safety fix.
        client = _clinical_client(monkeypatch, roles=("SUPERADMIN",))
        resp = client.post(
            "/clinical/lens-power-combos",
            json={
                "name": "Myopia mild SVS",
                "right_eye": {"sph": "-1.00", "cyl": "-0.50"},
                "left_eye": {"sph": "-1.00", "cyl": "0"},
            },
        )
        # A saved template is reused on every future patient - the un-grindable
        # combo must never be storable.
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]

    def test_create_prescription_rejects_toric_without_axis(self, monkeypatch):
        client = _rx_client(monkeypatch, _FakeSingleRxRepo(_seed_rx_doc()))
        resp = client.post(
            "/prescriptions",
            json={
                "patient_id": "pat-1",
                "customer_id": "cust-1",
                "optometrist_id": "opt-1",
                "right_eye": {"sph": "-1.00", "cyl": "-1.25"},
                "left_eye": {"sph": "-1.00", "cyl": "0"},
            },
        )
        assert resp.status_code == 422, resp.text
        assert "axis" in resp.text

    def test_update_rejects_adding_cyl_to_an_axis_less_eye(self, monkeypatch):
        doc = _seed_rx_doc()
        # Stored right eye is plano with NO axis.
        doc["right_eye"] = {"sph": "-1.00", "cyl": "0", "axis": None, "pd": "32"}
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"cyl": "-1.25"}})
        # The MERGED eye is what would be stored - and it has no axis.
        assert resp.status_code == 400, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []
        assert repo._doc["right_eye"]["cyl"] == "0"

    def test_update_accepts_cyl_when_the_stored_eye_has_an_axis(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"cyl": "-1.25"}})
        assert resp.status_code == 200, resp.text
        assert repo._doc["right_eye"]["cyl"] == "-1.25"
        assert repo._doc["right_eye"]["axis"] == 90

    def test_version_patch_rejects_toric_without_axis(self, monkeypatch):
        doc = _seed_rx_doc()
        doc["status"] = "in_progress"
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.patch(
            "/prescriptions/rx-1/version/after_testing",
            json={"right_eye": {"sphere": -1.0, "cylinder": -1.25}},
        )
        assert resp.status_code == 422, resp.text
        # A plain string detail (not a Pydantic error list) so the UI toast is
        # readable - `final` is mirrored to top-level on finalize.
        assert isinstance(resp.json()["detail"], str)
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []

    def test_finalize_rejects_a_legacy_toric_final_without_axis(self, monkeypatch):
        """Finalize MIRRORS versions.final into the top-level eyes POS and the
        workshop read, so it is an Rx write of its own. A `final` captured
        before the gate existed must not be mirrored un-grindable."""
        doc = _seed_rx_doc()
        doc["status"] = "in_progress"
        doc["versions"] = {
            "before_testing": None,
            "after_testing": None,
            "manual": None,
            "final": {
                "right_eye": {"sphere": -1.0, "cylinder": -1.25, "axis": None},
                "left_eye": {"sphere": -1.0, "cylinder": 0, "axis": None},
                "signed_off_by": "opt-1",
            },
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.post("/prescriptions/rx-1/finalize")
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []
        assert repo._doc["status"] == "in_progress"

    def test_finalize_still_works_for_a_valid_final(self, monkeypatch):
        doc = _seed_rx_doc()
        doc["status"] = "in_progress"
        doc["versions"] = {
            "before_testing": None,
            "after_testing": None,
            "manual": None,
            "final": {
                "right_eye": {"sphere": -1.0, "cylinder": -1.25, "axis": 90},
                "left_eye": {"sphere": -1.0, "cylinder": 0, "axis": None},
                "signed_off_by": "opt-1",
            },
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.post("/prescriptions/rx-1/finalize")
        assert resp.status_code == 200, resp.text
        assert repo._doc["status"] == "finalized"


# ============================================================================
# F20 - the axis is a WHOLE degree: reject 90.5, never round it
# ============================================================================


class TestF20FractionalAxisRejected:
    def test_validator_rejects_fractional_axis(self):
        with pytest.raises(HTTPException) as exc:
            _validate_eye_test_rx("Right eye", {"cyl": "-1.00", "axis": 90.5})
        assert exc.value.status_code == 422
        assert "whole number" in exc.value.detail
        assert "Right eye" in exc.value.detail

    def test_validator_rejects_fractional_axis_as_string(self):
        with pytest.raises(HTTPException) as exc:
            _validate_eye_test_rx("Left eye", {"cyl": "-1.00", "axis": "90.5"})
        assert exc.value.status_code == 422
        assert "whole number" in exc.value.detail

    def test_validator_still_rejects_out_of_range_axis(self):
        with pytest.raises(HTTPException) as exc:
            _validate_eye_test_rx("Right eye", {"cyl": "-1.00", "axis": 200})
        assert exc.value.status_code == 422
        assert "1-180" in exc.value.detail

    def test_axis_180_and_1_are_accepted(self):
        # Domain is 1-180 inclusive (0 is NOT a valid axis in IMS).
        _validate_eye_test_rx("Right eye", {"cyl": "-1.00", "axis": 180})
        _validate_eye_test_rx("Right eye", {"cyl": "-1.00", "axis": 1})
        with pytest.raises(HTTPException):
            _validate_eye_test_rx("Right eye", {"cyl": "-1.00", "axis": 0})

    def test_complete_test_rejects_fractional_axis_and_stores_nothing(self, monkeypatch):
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": -1.25, "cylinder": -0.50, "axis": 90.5},
                "leftEye": {"sphere": -1.00, "cylinder": 0, "axis": None},
                "pd": 62,
            },
        )
        assert resp.status_code == 422, resp.text
        assert "whole number" in resp.json()["detail"]
        assert rx_repo.created == []
        assert test_repo.complete_calls == 0

    def test_stored_axis_matches_what_was_validated(self, monkeypatch):
        """Validation and STORAGE must agree: a whole axis is persisted as the
        int the workshop spec expects, not as the caller's raw '90' / 90.0."""
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": -1.25, "cylinder": -0.50, "axis": "90"},
                "leftEye": {"sphere": -1.00, "cylinder": -0.25, "axis": 85.0},
                "pd": 62,
            },
        )
        assert resp.status_code == 200, resp.text
        stored = rx_repo.created[0]
        assert stored["right_eye"]["axis"] == 90
        assert isinstance(stored["right_eye"]["axis"], int)
        assert stored["left_eye"]["axis"] == 85
        assert isinstance(stored["left_eye"]["axis"], int)

    @pytest.mark.parametrize(
        "eye,expected",
        [
            ({"axis": 90}, 90),
            ({"axis": "90"}, 90),
            ({"axis": 90.0}, 90),
            ({"axis": " 45 "}, 45),
            ({"axis": None}, None),
            ({"axis": ""}, None),
            ({}, None),
        ],
    )
    def test_axis_for_storage_normalises(self, eye, expected):
        assert _axis_for_storage(eye) == expected

    def test_axis_for_storage_never_fabricates(self):
        # An absent axis stays blank - never a fabricated 0 / 180 on a billable Rx.
        assert _axis_for_storage({"sph": "-1.00"}) is None
        assert _axis_for_storage(None) is None


# ============================================================================
# F12 - a partial eye edit must not blank the rest of that eye
# ============================================================================


class TestF12PartialEyeEditPreservesPowers:
    def test_single_field_eye_patch_keeps_the_other_powers(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"sph": "-2.00"}})
        assert resp.status_code == 200, resp.text
        eye = repo._doc["right_eye"]
        # The supplied field changed...
        assert eye["sph"] == "-2.00"
        # ...and NOTHING else was blanked.
        assert eye["cyl"] == "-0.50"
        assert eye["axis"] == 90
        assert eye["add"] == "2.00"
        assert eye["pd"] == "32"

    def test_the_other_eye_is_untouched(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"sph": "-2.00"}})
        assert resp.status_code == 200
        assert repo._doc["left_eye"] == {
            "sph": "-1.25", "cyl": "-0.25", "axis": 85, "add": "2.00", "pd": "32",
        }

    def test_the_write_itself_carries_the_merged_eye(self, monkeypatch):
        """Guard the actual $set payload: the repository is handed the FULL
        merged eye, so it cannot matter that base_repository.update uses $set."""
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        client.put("/prescriptions/rx-1", json={"right_eye": {"axis": 100}})
        assert len(repo.updates) == 1
        written = repo.updates[0]["right_eye"]
        assert written["axis"] == 100
        assert written["sph"] == "-1.00" and written["cyl"] == "-0.50"
        assert written["add"] == "2.00" and written["pd"] == "32"

    def test_explicit_null_still_clears_a_field(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"pd": None}})
        assert resp.status_code == 200, resp.text
        assert repo._doc["right_eye"]["pd"] is None
        # Everything the caller did NOT send survives.
        assert repo._doc["right_eye"]["sph"] == "-1.00"
        assert repo._doc["right_eye"]["axis"] == 90

    def test_full_eye_replacement_still_works(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put(
            "/prescriptions/rx-1",
            json={
                "right_eye": {
                    "sph": "-3.00", "cyl": "-1.00", "axis": 10, "add": "0", "pd": "31",
                }
            },
        )
        assert resp.status_code == 200, resp.text
        eye = repo._doc["right_eye"]
        assert (eye["sph"], eye["cyl"], eye["axis"], eye["pd"]) == (
            "-3.00", "-1.00", 10, "31",
        )

    def test_version_history_block_is_preserved(self, monkeypatch):
        """The 4-version block (versions.before_testing/.../final) is separate
        from the top-level eyes and must survive an eye edit untouched."""
        doc = _seed_rx_doc()
        doc["status"] = "finalized"
        doc["versions"] = {
            "before_testing": {"right_eye": {"sphere": -0.75, "cylinder": 0}},
            "after_testing": None,
            "manual": None,
            "final": {
                "right_eye": {"sphere": -1.0, "cylinder": -0.5, "axis": 90},
                "signed_off_by": "opt-1",
            },
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"sph": "-2.00"}})
        assert resp.status_code == 200, resp.text
        assert repo._doc["versions"]["final"]["right_eye"]["sphere"] == -1.0
        assert repo._doc["versions"]["before_testing"]["right_eye"]["sphere"] == -0.75
        assert "versions" not in repo.updates[0]

    def test_canonical_aliases_stay_in_step_with_the_merge(self, monkeypatch):
        """A finalized Rx carries BOTH sph and the legacy `sphere` alias (the
        finalize mirror writes both, and progression reads the alias). A merge
        must not leave the pair disagreeing."""
        doc = _seed_rx_doc()
        doc["right_eye"] = {
            "sph": "-1.00", "sphere": "-1.00",
            "cyl": "-0.50", "cylinder": "-0.50",
            "add": "2.00", "addition": "2.00",
            "axis": 90, "pd": "32",
        }
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"right_eye": {"sph": "-2.00"}})
        assert resp.status_code == 200, resp.text
        eye = repo._doc["right_eye"]
        assert eye["sph"] == "-2.00" and eye["sphere"] == "-2.00"
        # Untouched pairs keep their stored values.
        assert eye["cyl"] == "-0.50" and eye["cylinder"] == "-0.50"

    def test_merge_helper_is_pure_and_does_not_invent_aliases(self):
        merged = prescriptions._merge_eye_subdoc(
            {"sph": "-1.00", "cyl": "-0.50", "axis": 90}, {"sph": "-2.00"}
        )
        assert merged == {"sph": "-2.00", "cyl": "-0.50", "axis": 90}
        # No `sphere` alias existed on the stored eye -> none is created.
        assert "sphere" not in merged

    def test_merge_helper_tolerates_a_missing_stored_eye(self):
        assert prescriptions._merge_eye_subdoc(None, {"sph": "-2.00"}) == {
            "sph": "-2.00"
        }


# ============================================================================
# F19 - the advisory /validate endpoint uses the CANONICAL limits
# ============================================================================


def _doc_with_eyes(right_eye, left_eye):
    doc = _seed_rx_doc()
    doc["right_eye"] = right_eye
    doc["left_eye"] = left_eye
    return doc


class TestF19ValidateUsesCanonicalLimits:
    def test_legitimate_high_power_rx_is_valid(self, monkeypatch):
        """SPH -22.00 and ADD +3.75 are inside the canonical limits (SPH +/-25,
        ADD 4.00). The endpoint's own stale copy (+/-20, 3.50) reported both as
        out of range, so real high-power patients looked invalid."""
        repo = _FakeSingleRxRepo(
            _doc_with_eyes(
                {"sph": "-22.00", "cyl": "-0.50", "axis": 90, "add": "3.75"},
                {"sph": "22.00", "cyl": "-0.50", "axis": 85, "add": "3.75"},
            )
        )
        client = _rx_client(monkeypatch, repo)
        resp = client.get("/prescriptions/rx-1/validate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["issues"] == []
        assert body["valid"] is True

    def test_add_at_the_canonical_ceiling_is_valid(self, monkeypatch):
        repo = _FakeSingleRxRepo(
            _doc_with_eyes(
                {"sph": "0", "cyl": "0", "axis": None, "add": "4.00"},
                {"sph": "0", "cyl": "0", "axis": None, "add": "4.00"},
            )
        )
        client = _rx_client(monkeypatch, repo)
        body = client.get("/prescriptions/rx-1/validate").json()
        assert body["valid"] is True, body["issues"]

    def test_beyond_the_canonical_limit_is_still_flagged(self, monkeypatch):
        repo = _FakeSingleRxRepo(
            _doc_with_eyes(
                {"sph": "-30.00", "cyl": "-0.50", "axis": 90, "add": "0"},
                {"sph": "0", "cyl": "0", "axis": None, "add": "4.25"},
            )
        )
        client = _rx_client(monkeypatch, repo)
        body = client.get("/prescriptions/rx-1/validate").json()
        assert body["valid"] is False
        assert any("sph" in i.lower() and "-30" in i for i in body["issues"])
        assert any("add" in i.lower() for i in body["issues"])

    def test_stored_toric_without_axis_is_reported(self, monkeypatch):
        """Legacy data written before the write-path gate: the advisory check
        must surface it so the Rx gets corrected before the lens is ground."""
        repo = _FakeSingleRxRepo(
            _doc_with_eyes(
                {"sph": "-1.00", "cyl": "-1.25", "axis": None, "add": "0"},
                {"sph": "-1.00", "cyl": "0", "axis": None, "add": "0"},
            )
        )
        client = _rx_client(monkeypatch, repo)
        body = client.get("/prescriptions/rx-1/validate").json()
        assert body["valid"] is False
        assert any("axis" in i.lower() for i in body["issues"])

    def test_off_grid_power_is_flagged(self, monkeypatch):
        # +1.30 is not on the 0.25-diopter grid - the write paths reject it, so
        # the advisory report must not call it clean either.
        repo = _FakeSingleRxRepo(
            _doc_with_eyes(
                {"sph": "1.30", "cyl": "0", "axis": None, "add": "0"},
                {"sph": "0", "cyl": "0", "axis": None, "add": "0"},
            )
        )
        client = _rx_client(monkeypatch, repo)
        body = client.get("/prescriptions/rx-1/validate").json()
        assert body["valid"] is False
        assert any("0.25" in i for i in body["issues"])

    def test_endpoint_follows_the_canonical_limit_table(self, monkeypatch):
        """BEHAVIOURAL proof of delegation: move the CANONICAL limit and the
        endpoint's verdict must move with it.

        The bug being guarded is a second, drifting copy of the ranges inside
        the handler. If such a copy existed, tightening
        ``rx_validation._RX_LIMITS`` would leave the endpoint's answer
        unchanged. Replaces a check that grepped the handler's source for stale
        numeric literals ("-20.0", "3.50", ...) -- which a reformat, a renamed
        constant or a helper extraction would defeat, and which asserted
        against whatever body inspect.getsource happened to return.
        """
        from api.services import rx_validation

        rx = _doc_with_eyes(
            {"sph": "-8.00", "cyl": "0", "axis": None, "add": "0"},
            {"sph": "0", "cyl": "0", "axis": None, "add": "0"},
        )

        # Baseline: -8.00 D is comfortably inside the canonical +/-25 range.
        client = _rx_client(monkeypatch, _FakeSingleRxRepo(rx))
        body = client.get("/prescriptions/rx-1/validate").json()
        assert body["valid"] is True, body

        # Tighten the ONE canonical table; the same Rx must now be reported
        # out of range. A handler carrying its own copy would still say valid.
        monkeypatch.setitem(rx_validation._RX_LIMITS, "sph", (-5.0, 5.0))
        client = _rx_client(monkeypatch, _FakeSingleRxRepo(rx))
        body = client.get("/prescriptions/rx-1/validate").json()
        assert body["valid"] is False, (
            "the validate endpoint is not reading rx_validation._RX_LIMITS -- "
            "a stale duplicate of the ranges has been reintroduced"
        )
        assert any("sph" in i.lower() for i in body["issues"]), body["issues"]


# ============================================================================
# PANEL ROUND 2 - the body the SHIPPED client actually sends
# ============================================================================
# Every case above sends explicit nulls. The production edit form (PrescriptionForm
# -> sales.ts toPrescriptionCreatePayload -> ClinicPrescriptionHistory, the ONLY
# PUT /prescriptions/{id} caller) used to DROP a blanked key instead: a cleared
# CYL/AXIS never reached the server, the deep-merge restored the stored values,
# and the API answered 200 while the clinical correction was discarded. sales.ts
# now emits `null` for a blank; this pins the resulting contract end-to-end.


def _cleared_cyl_axis_body():
    """The EXACT right_eye body toPrescriptionCreatePayload emits when an
    optometrist blanks the CYL and AXIS boxes on a prefilled toric Rx.
    Mirrors frontend/src/__tests__/rxClearPower.test.ts."""
    return {
        "right_eye": {
            "sph": "-2",
            "cyl": None,
            "axis": None,
            "add": "2",
            "pd": "32",
            "prism": None,
            "base": None,
            "acuity": None,
        }
    }


class TestClearingAPowerFromTheRealClient:
    def test_cleared_cyl_and_axis_are_actually_removed(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json=_cleared_cyl_axis_body())
        assert resp.status_code == 200, resp.text
        eye = repo._doc["right_eye"]
        # The mis-keyed cylinder is GONE - the patient is no longer dispensed a
        # toric lens they do not need.
        assert eye["cyl"] is None
        assert eye["axis"] is None
        # What the clinician kept is still there.
        assert eye["sph"] == "-2"
        assert eye["pd"] == "32"
        assert eye["add"] == "2"
        # The other eye was not part of the patch and is untouched.
        assert repo._doc["left_eye"]["cyl"] == "-0.25"

    def test_clearing_only_the_axis_of_a_toric_eye_is_rejected(self, monkeypatch):
        """The safety gate still applies to the real client's body: dropping the
        axis while the cylinder stays is the un-grindable Rx."""
        body = _cleared_cyl_axis_body()
        body["right_eye"]["cyl"] = "-0.50"  # cylinder kept, axis cleared
        repo = _FakeSingleRxRepo(_seed_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json=body)
        assert resp.status_code == 400, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []

    def test_a_legacy_out_of_range_add_can_be_cleared_not_deadlocked(self, monkeypatch):
        """A stored ADD below the canonical 0.75 floor must not make the record
        permanently uneditable: clearing the box sends null, so the merged eye
        is valid and the edit goes through."""
        doc = _seed_rx_doc()
        doc["right_eye"]["add"] = "0.50"  # legacy, below the canonical floor
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        body = _cleared_cyl_axis_body()
        body["right_eye"]["add"] = None
        body["right_eye"]["cyl"] = None
        resp = client.put("/prescriptions/rx-1", json=body)
        assert resp.status_code == 200, resp.text
        assert repo._doc["right_eye"]["add"] is None


# ============================================================================
# PANEL ROUND 2 - the toric gate on the CONTACT-LENS half of the same endpoint
# ============================================================================
# A soft toric CL is ORDERED by power/cyl/axis. With a cylinder and no axis the
# lens cannot be ordered, so the counter guesses (usually 180) and the patient
# wears a rotationally-wrong toric. The CL axis domain is 0-180 (NOT the
# spectacle 1-180).


class TestContactLensToricAxisGate:
    def test_put_clearing_cl_axis_on_a_toric_cl_is_rejected(self, monkeypatch):
        """The merged CL sub-document is what gets written, so it is what must
        be validated: the patch alone ({cl_axis: null}) looks clean."""
        repo = _FakeSingleRxRepo(_seed_cl_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"cl_right": {"cl_axis": None}})
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert "contact-lens cylinder" in detail and "-1.75" in detail
        assert "0-180" in detail
        assert repo.updates == []
        # The stored lens still has its axis.
        assert repo._doc["cl_right"]["cl_axis"] == 170

    def test_put_adding_a_cl_cylinder_to_an_axis_less_cl_is_rejected(self, monkeypatch):
        repo = _FakeSingleRxRepo(_seed_cl_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"cl_left": {"cl_cyl": -1.75}})
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        assert repo.updates == []

    def test_put_partial_cl_edit_still_preserves_the_other_fit_params(self, monkeypatch):
        """The F12 merge itself must keep working for CL eyes."""
        repo = _FakeSingleRxRepo(_seed_cl_rx_doc())
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"cl_right": {"cl_power": -3.25}})
        assert resp.status_code == 200, resp.text
        eye = repo._doc["cl_right"]
        assert eye["cl_power"] == -3.25
        assert eye["cl_cyl"] == -1.75 and eye["cl_axis"] == 170
        assert eye["base_curve"] == 8.6 and eye["diameter"] == 14.2

    def test_put_merged_cl_out_of_range_fit_param_is_rejected(self, monkeypatch):
        """A legacy/imported base_curve outside 8.0-9.5 must not be silently
        re-persisted by an edit the endpoint reports as validated."""
        doc = _seed_cl_rx_doc()
        doc["cl_right"]["base_curve"] = 12.0
        repo = _FakeSingleRxRepo(doc)
        client = _rx_client(monkeypatch, repo)
        resp = client.put("/prescriptions/rx-1", json={"cl_right": {"cl_power": -3.25}})
        assert resp.status_code == 422, resp.text
        assert "base_curve" in resp.json()["detail"]
        assert repo.updates == []

    def test_create_toric_cl_without_axis_is_rejected(self, monkeypatch):
        rx_repo = _FakeRxRepo()
        client = _rx_client(monkeypatch, rx_repo)
        resp = client.post(
            "/prescriptions",
            json={
                "patient_id": "pat-1",
                "customer_id": "cust-1",
                "optometrist_id": "u-opto",
                "rx_kind": "CONTACT_LENS",
                "modality": "MONTHLY",
                "cl_right": {
                    "cl_power": -3.0, "cl_cyl": -1.75,
                    "base_curve": 8.6, "diameter": 14.2,
                },
            },
        )
        assert resp.status_code == 422, resp.text
        assert "no axis" in resp.json()["detail"]
        assert rx_repo.created == []

    def test_create_toric_cl_with_axis_zero_is_accepted(self, monkeypatch):
        """CL axis 0 IS valid (the CL domain is 0-180, unlike spectacles)."""
        rx_repo = _FakeRxRepo()
        client = _rx_client(monkeypatch, rx_repo)
        resp = client.post(
            "/prescriptions",
            json={
                "patient_id": "pat-1",
                "customer_id": "cust-1",
                "optometrist_id": "u-opto",
                "rx_kind": "CONTACT_LENS",
                "modality": "MONTHLY",
                "cl_right": {
                    "cl_power": -3.0, "cl_cyl": -1.75, "cl_axis": 0,
                    "base_curve": 8.6, "diameter": 14.2,
                },
            },
        )
        assert resp.status_code == 201, resp.text
        assert len(rx_repo.created) == 1
        assert rx_repo.created[0]["cl_right"]["cl_axis"] == 0

    def test_non_toric_cl_needs_no_axis(self, monkeypatch):
        rx_repo = _FakeRxRepo()
        client = _rx_client(monkeypatch, rx_repo)
        resp = client.post(
            "/prescriptions",
            json={
                "patient_id": "pat-1",
                "customer_id": "cust-1",
                "optometrist_id": "u-opto",
                "rx_kind": "CONTACT_LENS",
                "modality": "MONTHLY",
                "cl_right": {
                    "cl_power": -3.0, "base_curve": 8.6, "diameter": 14.2,
                },
            },
        )
        assert resp.status_code == 201, resp.text
        assert rx_repo.created[0]["cl_right"]["cl_axis"] is None

    def test_validator_accepts_a_model_and_a_dict_identically(self):
        toric_no_axis = {
            "cl_power": -3.0, "cl_cyl": -1.75, "cl_axis": None,
            "base_curve": 8.6, "diameter": 14.2,
        }
        with pytest.raises(HTTPException) as from_dict:
            prescriptions._validate_cl_eye("Right eye", toric_no_axis)
        with pytest.raises(HTTPException) as from_model:
            prescriptions._validate_cl_eye(
                "Right eye", prescriptions.CLEyeData(**toric_no_axis)
            )
        assert from_dict.value.status_code == from_model.value.status_code == 422
        assert from_dict.value.detail == from_model.value.detail


# ============================================================================
# PANEL ROUND 2 - validation and STORAGE must resolve an alias the same way
# ============================================================================


class TestEyeTestStorageResolvesLikeTheValidator:
    def test_mixed_alias_cylinder_cannot_slip_past_the_gate(self, monkeypatch):
        """cylinder: 0 + cyl: '-1.50' used to validate as non-toric (first
        NON-blank wins) and then STORE -1.50 (first TRUTHY wins) with no axis."""
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": "-1.00", "cylinder": 0, "cyl": "-1.50",
                             "axis": None},
                "leftEye": {"sphere": "-1.00", "cylinder": 0, "axis": None},
            },
        )
        assert resp.status_code == 200, resp.text
        # Whatever was VALIDATED (the plano 0) is what is STORED - never the
        # -1.50 the gate never saw.
        assert rx_repo.created[0]["right_eye"]["cyl"] == "0"
        assert rx_repo.created[0]["right_eye"]["axis"] is None

    def test_plano_zero_is_preserved_not_blanked(self, monkeypatch):
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": 0, "cylinder": 0, "axis": None, "add": 0},
                "leftEye": {"sphere": 0, "cylinder": 0, "axis": None},
            },
        )
        assert resp.status_code == 200, resp.text
        eye = rx_repo.created[0]["right_eye"]
        # A genuine plano is "0" (the comment at the write site promises it),
        # not "" ("not tested").
        assert eye["sph"] == "0" and eye["cyl"] == "0"

    def test_blank_per_eye_pd_is_stored_blank_not_the_string_none(self, monkeypatch):
        """str(eye.get('pd', '')) stored the literal 'None' when the box was
        blank - which then failed the merged-eye validation on every later edit."""
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": -1.0, "cylinder": 0, "axis": None, "pd": None},
                "leftEye": {"sphere": -1.0, "cylinder": 0, "axis": None},
            },
        )
        assert resp.status_code == 200, resp.text
        assert rx_repo.created[0]["right_eye"]["pd"] == ""
        assert rx_repo.created[0]["left_eye"]["pd"] == ""

    def test_addition_keyed_near_add_is_not_dropped_on_storage(self, monkeypatch):
        test_repo = _FakeTestRepo(_eye_test_doc())
        rx_repo = _FakeRxRepo()
        client = _clinical_client(monkeypatch, test_repo=test_repo,
                                  queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)
        resp = client.post(
            "/clinical/tests/t-1/complete",
            json={
                "rightEye": {"sphere": -1.0, "cylinder": 0, "axis": None,
                             "addition": "2.00"},
                "leftEye": {"sphere": -1.0, "cylinder": 0, "axis": None},
            },
        )
        assert resp.status_code == 200, resp.text
        assert rx_repo.created[0]["right_eye"]["add"] == "2.00"
