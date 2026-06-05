"""
IMS 2.0 - Structured SOAP EHR tests (CLI-11)
=============================================
Tests for the SoapNote / SoapDxCode Pydantic models, the EyeTestRepository
save_soap_note / complete_test(soap_note=...) paths, and the SOAP-note HTTP
endpoints.

All tests run without a real MongoDB connection (fake repositories used for
repo tests; FastAPI TestClient used for endpoint tests with DB mocked out).
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Use the same default key as other clinical tests so the shared api.main.app
# instance (across all test modules in a pytest run) validates tokens consistently.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")


# ============================================================================
# SoapNote / SoapDxCode model validation
# ============================================================================


def test_soap_dx_code_requires_code():
    from pydantic import ValidationError
    from api.routers.clinical import SoapDxCode

    with pytest.raises(ValidationError):
        SoapDxCode(description="Myopia")  # code is required


def test_soap_dx_code_defaults_to_icd10():
    from api.routers.clinical import SoapDxCode

    dx = SoapDxCode(code="H52.1", description="Myopia")
    assert dx.system == "ICD-10"


def test_soap_note_all_optional():
    from api.routers.clinical import SoapNote

    # An empty SoapNote is valid — a refraction-only test may submit nothing.
    note = SoapNote()
    assert note.chief_complaint is None
    assert note.dx_codes is None
    assert note.plan is None


def test_soap_note_accepts_full_payload():
    from api.routers.clinical import SoapNote, SoapDxCode

    note = SoapNote(
        chiefComplaint="Blurry distance vision",
        historyPresentIllness="3 months gradual onset",
        iopRight=14.5,
        iopLeft=15.0,
        assessment="Myopia OU",
        dxCodes=[{"code": "H52.1", "description": "Myopia", "system": "ICD-10"}],
        plan="Prescribe spectacles",
        planReferral=False,
        planFollowUp=True,
        planFollowUpWeeks=24,
    )
    assert note.chief_complaint == "Blurry distance vision"
    assert note.iop_right == 14.5
    assert note.plan_follow_up_weeks == 24
    assert note.dx_codes is not None and len(note.dx_codes) == 1
    assert note.dx_codes[0].code == "H52.1"


def test_soap_note_dominant_eye_normalised():
    from api.routers.clinical import SoapNote

    note = SoapNote(dominantEye="r")
    assert note.dominant_eye == "RIGHT"

    note2 = SoapNote(dominantEye="LEFT")
    assert note2.dominant_eye == "LEFT"


def test_soap_note_bad_dominant_eye_rejected():
    from pydantic import ValidationError
    from api.routers.clinical import SoapNote

    with pytest.raises(ValidationError):
        SoapNote(dominantEye="middle")


def test_soap_note_iop_bound():
    from pydantic import ValidationError
    from api.routers.clinical import SoapNote

    with pytest.raises(ValidationError):
        SoapNote(iopRight=200)  # > 80 mmHg is rejected


def test_soap_note_follow_up_weeks_min():
    from pydantic import ValidationError
    from api.routers.clinical import SoapNote

    with pytest.raises(ValidationError):
        SoapNote(planFollowUpWeeks=0)  # must be >= 1


def test_soap_note_dump_is_snake_case():
    from api.routers.clinical import SoapNote

    note = SoapNote(chiefComplaint="blur", iopRight=14.0)
    dumped = note.model_dump(exclude_none=True)
    assert "chief_complaint" in dumped
    assert "iop_right" in dumped
    assert "chiefComplaint" not in dumped  # alias not in default dump


def test_eye_test_data_accepts_soap_note_field():
    """EyeTestData.soap_note is optional — absent means refraction-only."""
    from api.routers.clinical import EyeTestData

    # Without soap_note -> backward-compat.
    d = EyeTestData(rightEye={"sphere": -1.0}, leftEye={"sphere": -1.0})
    assert d.soap_note is None

    # With soap_note.
    d2 = EyeTestData(
        rightEye={"sphere": -1.0},
        leftEye={"sphere": -1.0},
        soapNote={"chiefComplaint": "blur", "assessment": "Myopia"},
    )
    assert d2.soap_note is not None
    assert d2.soap_note.chief_complaint == "blur"


# ============================================================================
# EyeTestRepository.complete_test + save_soap_note (fake DB)
# ============================================================================


class _FakeRepo:
    """Minimal fake that captures update() calls without a real DB."""

    def __init__(self):
        self._entity_name = "EyeTest"
        self._calls: list[dict] = []

    def update(self, _id, data):
        self._calls.append({"id": _id, "data": data})
        return True


def _make_repo(fake: _FakeRepo):
    from database.repositories.clinical_repository import EyeTestRepository

    repo = object.__new__(EyeTestRepository)
    repo.update = fake.update  # type: ignore[attr-defined]
    return repo


def test_complete_test_persists_soap_note():
    fake = _FakeRepo()
    repo = _make_repo(fake)

    repo.complete_test(
        "T1",
        {"sph": "-1"},
        {"sph": "-1"},
        soap_note={"chief_complaint": "blur", "dx_codes": [{"code": "H52.1"}]},
    )

    assert len(fake._calls) == 1
    update_data = fake._calls[0]["data"]
    assert "soap_note" in update_data
    assert update_data["soap_note"]["chief_complaint"] == "blur"


def test_complete_test_omits_soap_note_when_absent():
    fake = _FakeRepo()
    repo = _make_repo(fake)

    repo.complete_test("T2", {"sph": "-1"}, {"sph": "-1"})
    update_data = fake._calls[0]["data"]
    assert "soap_note" not in update_data


def test_save_soap_note_calls_update():
    fake = _FakeRepo()
    repo = _make_repo(fake)

    ok = repo.save_soap_note("T3", {"chief_complaint": "headache"})
    assert ok is True
    assert fake._calls[0]["data"] == {"soap_note": {"chief_complaint": "headache"}}


# ============================================================================
# HTTP endpoints: GET + POST /clinical/tests/{id}/soap-note
# ============================================================================


def _build_client():
    from api.main import app

    return TestClient(app)


def _auth_headers():
    """Minimal JWT for SUPERADMIN role (bypasses require_roles gates).

    The secret must match the JWT_SECRET_KEY the app was initialised with.
    We use os.environ to pick up whatever was set (matching the setdefault
    above) so this stays consistent when run in isolation or as part of the
    full pytest suite.
    """
    import jwt
    secret = os.environ.get("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
    payload = {
        "user_id": "u1",
        "username": "admin",
        "full_name": "Admin",
        "roles": ["SUPERADMIN"],
        "active_role": "SUPERADMIN",
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_get_soap_note_returns_null_when_no_db():
    """GET /clinical/tests/{id}/soap-note returns {soapNote: null} when DB is absent."""
    from unittest.mock import patch

    # Patch at the router's import location so the handler sees None.
    with patch("api.routers.clinical.get_eye_test_repository", return_value=None):
        client = _build_client()
        resp = client.get(
            "/api/v1/clinical/tests/T-MISSING/soap-note",
            headers=_auth_headers(),
        )
    # When no DB available, endpoint returns 200 with soapNote: null (fail-soft).
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("soapNote") is None


def test_post_soap_note_returns_503_when_no_db():
    """POST /clinical/tests/{id}/soap-note returns 503 when DB is unavailable."""
    from unittest.mock import patch

    with patch("api.routers.clinical.get_eye_test_repository", return_value=None):
        client = _build_client()
        resp = client.post(
            "/api/v1/clinical/tests/T1/soap-note",
            json={"chiefComplaint": "blur"},
            headers=_auth_headers(),
        )
    assert resp.status_code == 503


def test_post_soap_note_rejects_bad_iop():
    """The endpoint validates IOP bounds (0-80 mmHg) via Pydantic — no DB needed."""
    # Pydantic validation happens before the handler body, so no DB patch needed.
    client = _build_client()
    resp = client.post(
        "/api/v1/clinical/tests/T1/soap-note",
        json={"iopRight": 999},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_post_soap_note_happy_path():
    """POST saves the note and returns the camelCase-converted note."""
    from unittest.mock import MagicMock, patch

    mock_repo = MagicMock()
    mock_repo.find_by_id.return_value = {
        "test_id": "T2", "store_id": "S1", "customer_id": "C1"
    }
    mock_repo.save_soap_note.return_value = True

    with patch("api.routers.clinical.get_eye_test_repository", return_value=mock_repo):
        client = _build_client()
        resp = client.post(
            "/api/v1/clinical/tests/T2/soap-note",
            json={"chiefComplaint": "blur", "assessment": "Myopia", "iopRight": 14.5},
            headers=_auth_headers(),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["testId"] == "T2"
    # Response is camelCase.
    assert "soapNote" in body
    # save_soap_note was called exactly once.
    mock_repo.save_soap_note.assert_called_once()


def test_get_soap_note_returns_note_when_present():
    """GET returns the stored SOAP note when the test has one."""
    from unittest.mock import MagicMock, patch

    stored = {
        "chief_complaint": "blurry vision",
        "dx_codes": [{"code": "H52.1", "description": "Myopia", "system": "ICD-10"}],
        "recorded_by": "u1",
    }
    mock_repo = MagicMock()
    mock_repo.find_by_id.return_value = {
        "test_id": "T3", "store_id": "S1", "soap_note": stored
    }

    with patch("api.routers.clinical.get_eye_test_repository", return_value=mock_repo):
        client = _build_client()
        resp = client.get(
            "/api/v1/clinical/tests/T3/soap-note",
            headers=_auth_headers(),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["soapNote"] is not None
    # Should be camelCase.
    assert "chiefComplaint" in body["soapNote"]


def test_post_soap_note_404_on_missing_test():
    """POST returns 404 when the test_id doesn't exist in the DB."""
    from unittest.mock import MagicMock, patch

    mock_repo = MagicMock()
    mock_repo.find_by_id.return_value = None  # test not found

    with patch("api.routers.clinical.get_eye_test_repository", return_value=mock_repo):
        client = _build_client()
        resp = client.post(
            "/api/v1/clinical/tests/GHOST/soap-note",
            json={"chiefComplaint": "blur"},
            headers=_auth_headers(),
        )

    assert resp.status_code == 404
