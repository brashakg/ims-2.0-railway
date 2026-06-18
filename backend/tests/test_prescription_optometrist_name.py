"""
IMS 2.0 - Prescription optometrist NAME (backlog #2)
=====================================================
The owner saw a raw optometrist_id on Rx cards. The create-door now captures
the optometrist's NAME and persists it; read-back resolves it from the users
collection for older docs that only stored optometrist_id.

Assertions:
  1. A created Rx carries optometrist_name derived from the logged-in user
     (JWT full_name) when the optometrist is the current user.
  2. An explicit optometrist_name in the payload is honoured (wins over derive).
  3. GET /prescriptions/{id} read-back BACKFILLS optometrist_name from the users
     collection for an older doc that stored only optometrist_id.
  4. The family view also backfills optometrist_name on each row.

No real MongoDB is required: in-memory fake repos stand in.
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


# ---------------------------------------------------------------------------
# Fake infrastructure (no real DB needed)
# ---------------------------------------------------------------------------


class _FakeRxRepo:
    def __init__(self, seed=None):
        self._docs: dict = {}
        for d in seed or []:
            self._docs[d["prescription_id"]] = dict(d)

    def create(self, data: dict):
        data.setdefault("prescription_id", "rx-opt-1")
        data.setdefault("prescription_number", "RX-OPT-001")
        self._docs[data["prescription_id"]] = dict(data)
        return data

    def find_by_id(self, pid):
        return self._docs.get(pid)

    def find_by_customer(self, cid):
        return [d for d in self._docs.values() if d.get("customer_id") == cid]


class _FakeCustRepo:
    def __init__(self, customer=None):
        self._customer = customer

    def find_by_id(self, cid):
        return self._customer


class _FakeUserRepo:
    """Resolves a user doc by user_id for name backfill."""

    def __init__(self, users=None):
        self._users = users or {}

    def find_by_id(self, uid):
        return self._users.get(uid)


def _build_client(monkeypatch, *, rx_repo=None, cust=None, users=None, user=None):
    if rx_repo is None:
        rx_repo = _FakeRxRepo()

    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return user or {
            "user_id": "opt-1",
            "username": "dr_meera",
            "full_name": "Dr. Meera Iyer",
            "active_store_id": "store-001",
            "roles": ["OPTOMETRIST"],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(
        prescriptions, "get_customer_repository", lambda: _FakeCustRepo(cust)
    )
    monkeypatch.setattr(
        prescriptions, "get_user_repository", lambda: _FakeUserRepo(users)
    )
    return TestClient(app), rx_repo


_BASE_PAYLOAD = {
    "patient_id": "walkin-opt-test",
    "customer_id": "walkin-opt-test",
    "source": "TESTED_AT_STORE",
    "optometrist_id": "opt-1",
    "validity_months": 12,
    "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90},
    "left_eye": {"sph": "-1.25", "cyl": "0", "axis": None},
}


# ---------------------------------------------------------------------------
# Create stores the name
# ---------------------------------------------------------------------------


class TestCreateStoresOptometristName:
    def test_create_derives_name_from_logged_in_user(self, monkeypatch):
        """When the optometrist is the current user and no name is supplied,
        the JWT full_name is persisted as optometrist_name."""
        client, repo = _build_client(monkeypatch)
        resp = client.post("/prescriptions", json=_BASE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["optometrist_name"] == "Dr. Meera Iyer", doc.get("optometrist_name")
        # id is still stored unchanged
        assert doc["optometrist_id"] == "opt-1"

    def test_explicit_name_wins(self, monkeypatch):
        """An explicit optometrist_name in the payload is honoured."""
        client, repo = _build_client(monkeypatch)
        resp = client.post(
            "/prescriptions",
            json={**_BASE_PAYLOAD, "optometrist_name": "Dr. Explicit Name"},
        )
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["optometrist_name"] == "Dr. Explicit Name"

    def test_create_resolves_other_optometrist_from_users(self, monkeypatch):
        """When optometrist_id != current user (admin recording for an opto),
        the name is resolved from the users collection."""
        users = {"opt-2": {"user_id": "opt-2", "full_name": "Dr. Other Optom"}}
        admin = {
            "user_id": "admin-1",
            "username": "admin",
            "full_name": "Admin User",
            "active_store_id": "store-001",
            "roles": ["STORE_MANAGER"],
        }
        client, repo = _build_client(monkeypatch, users=users, user=admin)
        resp = client.post(
            "/prescriptions",
            json={**_BASE_PAYLOAD, "optometrist_id": "opt-2"},
        )
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["optometrist_name"] == "Dr. Other Optom"


# ---------------------------------------------------------------------------
# Read-back backfills the name for old docs
# ---------------------------------------------------------------------------


class TestReadBackResolvesName:
    def test_get_by_id_backfills_name(self, monkeypatch):
        """An older doc that has only optometrist_id gets optometrist_name
        resolved from the users collection on GET /prescriptions/{id}."""
        old_doc = {
            "prescription_id": "rx-old-1",
            "customer_id": "cust-1",
            "patient_id": "cust-1",
            "store_id": "store-001",
            "optometrist_id": "opt-9",
            # NOTE: no optometrist_name on the legacy doc
            "right_eye": {"sph": "-1.00"},
            "left_eye": {"sph": "-1.00"},
        }
        users = {"opt-9": {"user_id": "opt-9", "full_name": "Dr. Legacy Resolve"}}
        repo = _FakeRxRepo(seed=[old_doc])
        client, _ = _build_client(monkeypatch, rx_repo=repo, users=users)
        resp = client.get("/prescriptions/rx-old-1")
        assert resp.status_code == 200, resp.text
        assert resp.json().get("optometrist_name") == "Dr. Legacy Resolve"

    def test_family_view_backfills_name(self, monkeypatch):
        """Family view rows backfill optometrist_name for legacy docs."""
        old_doc = {
            "prescription_id": "rx-old-2",
            "customer_id": "cust-2",
            "patient_id": "pat-2",
            "store_id": "store-001",
            "optometrist_id": "opt-7",
            "right_eye": {"sph": "-2.00"},
            "left_eye": {"sph": "-2.00"},
        }
        cust = {
            "customer_id": "cust-2",
            "name": "Household Head",
            "store_id": "store-001",
            "patients": [{"patient_id": "pat-2", "name": "Child", "relation": "Son"}],
        }
        users = {"opt-7": {"user_id": "opt-7", "full_name": "Dr. Family Resolve"}}
        repo = _FakeRxRepo(seed=[old_doc])
        client, _ = _build_client(monkeypatch, rx_repo=repo, cust=cust, users=users)
        resp = client.get("/prescriptions/family/cust-2")
        assert resp.status_code == 200, resp.text
        members = resp.json()["members"]
        # find the row
        rows = [r for m in members for r in m["prescriptions"]]
        assert rows, "expected at least one Rx row"
        assert rows[0].get("optometrist_name") == "Dr. Family Resolve"
