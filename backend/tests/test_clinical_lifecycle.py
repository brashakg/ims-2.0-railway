"""
IMS 2.0 - Clinical eye-test lifecycle hardening
================================================
Regression tests for three correctness bugs in the clinical queue / eye-test
flow (backend/api/routers/clinical.py), all exercised with in-memory fakes so
no MongoDB is required:

1. complete_test idempotency
   - Re-completing an already-COMPLETED test must NOT mint a second prescription
     (a double-click / retry / page reload used to create duplicate Rx rows).
   - Completing an unknown test_id 404s instead of silently creating an orphan
     prescription.
   - The happy path still creates exactly ONE prescription stamped with the
     eye_test_id.

2. update_queue_status validation
   - An invalid status value is rejected with 400 (it used to return a
     misleading 200 "Status updated" while the repo silently no-op'd).
   - A valid status still succeeds and is echoed back normalised.

Mirrors the bare-app + dependency-override / monkeypatch pattern used in
test_clinical_rx.py. ASCII only (Windows cp1252).
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

from api.routers import clinical  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# In-memory fakes
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
                       lens_recommendation=None, coating_recommendation=None):
        if not self._doc or self._doc.get("test_id") != test_id:
            return False
        self.complete_calls += 1
        # Mirror the real repo: flips status to COMPLETED + stamps the Rx blob.
        self._doc["status"] = "COMPLETED"
        self._doc["prescription"] = {"right_eye": right_eye, "left_eye": left_eye}
        return True


class _FakeQueueRepo:
    """Stand-in for EyeTestQueueRepository.update_status with the real allow-list."""

    _VALID = ("WAITING", "IN_PROGRESS", "COMPLETED", "CANCELLED", "NO_SHOW")

    def __init__(self):
        self.status_calls = []

    def update_status(self, queue_id, status):
        self.status_calls.append((queue_id, status))
        return status in self._VALID

    def find_by_id(self, queue_id):
        return {"queue_id": queue_id}

    def update(self, queue_id, data):
        return True


class _FakeRxRepo:
    """Stand-in for PrescriptionRepository: tracks created Rx + eye_test lookup."""

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
        self.created.append(dict(data))
        return dict(data)


def _client(monkeypatch, *, test_repo=None, queue_repo=None, rx_repo=None,
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


_GOOD_BODY = {
    "rightEye": {"sphere": -1.25, "cylinder": -0.50, "axis": 90, "add": 0},
    "leftEye": {"sphere": -1.00, "cylinder": -0.25, "axis": 85, "add": 0},
    "pd": 62,
}


# ============================================================================
# 1. complete_test idempotency
# ============================================================================


class TestCompleteTestIdempotency:
    def test_first_completion_creates_one_prescription(self, monkeypatch):
        test_repo = _FakeTestRepo(
            {"test_id": "t-1", "queue_id": "q-1", "status": "IN_PROGRESS",
             "customer_id": "c-1", "store_id": "store-001"}
        )
        rx_repo = _FakeRxRepo()
        client = _client(monkeypatch, test_repo=test_repo,
                         queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)

        resp = client.post("/clinical/tests/t-1/complete", json=_GOOD_BODY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["prescriptionId"] is not None
        assert len(rx_repo.created) == 1
        assert rx_repo.created[0]["eye_test_id"] == "t-1"

    def test_double_completion_does_not_duplicate_prescription(self, monkeypatch):
        """The core bug: a second /complete on the same test must not create a
        second prescription. Returns the existing one + alreadyCompleted flag."""
        test_repo = _FakeTestRepo(
            {"test_id": "t-1", "queue_id": "q-1", "status": "IN_PROGRESS",
             "customer_id": "c-1", "store_id": "store-001"}
        )
        rx_repo = _FakeRxRepo()
        client = _client(monkeypatch, test_repo=test_repo,
                         queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)

        first = client.post("/clinical/tests/t-1/complete", json=_GOOD_BODY)
        assert first.status_code == 200
        first_rx_id = first.json()["prescriptionId"]

        # Second call (e.g. double-click / retry): test is now COMPLETED.
        second = client.post("/clinical/tests/t-1/complete", json=_GOOD_BODY)
        assert second.status_code == 200
        second_body = second.json()

        # CRUCIAL: still only one prescription, and it's the same one.
        assert len(rx_repo.created) == 1
        assert second_body["prescriptionId"] == first_rx_id
        assert second_body.get("alreadyCompleted") is True
        # The repo's complete_test was only invoked once.
        assert test_repo.complete_calls == 1

    def test_unknown_test_returns_404(self, monkeypatch):
        test_repo = _FakeTestRepo(
            {"test_id": "t-1", "status": "IN_PROGRESS", "customer_id": "c-1"}
        )
        rx_repo = _FakeRxRepo()
        client = _client(monkeypatch, test_repo=test_repo,
                         queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)

        resp = client.post("/clinical/tests/does-not-exist/complete", json=_GOOD_BODY)
        assert resp.status_code == 404
        # No orphan Rx minted for a non-existent test.
        assert len(rx_repo.created) == 0

    def test_completion_still_blocks_out_of_range_rx(self, monkeypatch):
        """The pre-existing 422 validation must survive the idempotency rewrite."""
        test_repo = _FakeTestRepo(
            {"test_id": "t-1", "status": "IN_PROGRESS", "customer_id": "c-1"}
        )
        rx_repo = _FakeRxRepo()
        client = _client(monkeypatch, test_repo=test_repo,
                         queue_repo=_FakeQueueRepo(), rx_repo=rx_repo)

        bad = {
            "rightEye": {"sphere": 99, "cylinder": 0, "axis": 90},  # SPH 99 > +20
            "leftEye": {"sphere": 0, "cylinder": 0, "axis": 90},
        }
        resp = client.post("/clinical/tests/t-1/complete", json=bad)
        assert resp.status_code == 422
        assert len(rx_repo.created) == 0


# ============================================================================
# 2. update_queue_status validation
# ============================================================================


class TestQueueStatusValidation:
    def test_invalid_status_rejected_400(self, monkeypatch):
        queue_repo = _FakeQueueRepo()
        client = _client(monkeypatch, queue_repo=queue_repo)
        resp = client.patch("/clinical/queue/q-1/status", json={"status": "BANANA"})
        assert resp.status_code == 400
        # Repo must NOT have been asked to apply garbage.
        assert queue_repo.status_calls == []

    def test_valid_status_succeeds_and_is_normalised(self, monkeypatch):
        queue_repo = _FakeQueueRepo()
        client = _client(monkeypatch, queue_repo=queue_repo)
        resp = client.patch(
            "/clinical/queue/q-1/status", json={"status": "in_progress"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "IN_PROGRESS"
        assert queue_repo.status_calls == [("q-1", "IN_PROGRESS")]

    def test_empty_status_rejected(self, monkeypatch):
        client = _client(monkeypatch, queue_repo=_FakeQueueRepo())
        resp = client.patch("/clinical/queue/q-1/status", json={"status": ""})
        assert resp.status_code == 400
