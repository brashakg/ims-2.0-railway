"""
IMS 2.0 - Family Rx view
========================
GET /prescriptions/family/{customer_id} groups a customer account's
prescriptions by family member (patient), annotates validity, lists patients
with no Rx, and surfaces account-less prescriptions under 'Unlinked patient'.
Fake-repo TestClient; no live DB.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


class _FakeRxRepo:
    def __init__(self, rxs):
        self._rxs = rxs

    def find_by_customer(self, cid):
        return [dict(r) for r in self._rxs if r.get("customer_id") == cid]


class _FakeCustRepo:
    def __init__(self, customers):
        self._c = customers

    def find_by_id(self, cid):
        return self._c.get(cid)


def _client():
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/api/v1/prescriptions")

    async def _u():
        return {"user_id": "u1", "roles": ["STORE_MANAGER"], "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _seed(monkeypatch, rxs, customers):
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: _FakeRxRepo(rxs))
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: _FakeCustRepo(customers))


def test_family_groups_by_patient_with_validity(monkeypatch):
    now = datetime.now().replace(microsecond=0)
    recent = now.isoformat()
    old = now.replace(year=now.year - 2).isoformat()
    rxs = [
        {"prescription_id": "rx1", "customer_id": "C1", "patient_id": "p1", "store_id": "S1", "test_date": recent, "validity_months": 12},
        {"prescription_id": "rx2", "customer_id": "C1", "patient_id": "p1", "store_id": "S1", "test_date": old, "validity_months": 12},
        {"prescription_id": "rx3", "customer_id": "C1", "patient_id": "p2", "store_id": "S1", "test_date": recent, "validity_months": 12},
        {"prescription_id": "rx4", "customer_id": "C1", "patient_id": "pX", "store_id": "S1", "test_date": recent, "validity_months": 12, "patient_name": "Legacy Kid"},
    ]
    customer = {
        "customer_id": "C1", "name": "Head",
        "patients": [
            {"patient_id": "p1", "name": "Head", "relation": "Self"},
            {"patient_id": "p2", "name": "Spouse", "relation": "Other"},
            {"patient_id": "p3", "name": "Child", "relation": "Other"},  # no Rx
        ],
    }
    _seed(monkeypatch, rxs, {"C1": customer})

    r = _client().get("/api/v1/prescriptions/family/C1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_prescriptions"] == 4
    by_pid = {m["patient_id"]: m for m in body["members"]}

    # p1: 2 Rx, 1 valid (recent), 1 expired (2y old)
    assert by_pid["p1"]["prescription_count"] == 2
    assert by_pid["p1"]["valid_count"] == 1
    assert by_pid["p1"]["latest"]["is_valid"] is True   # most recent first
    # p2: one valid
    assert by_pid["p2"]["valid_count"] == 1
    # p3: listed even with no prescriptions
    assert by_pid["p3"]["prescription_count"] == 0
    # account-less prescription surfaces as Unlinked, name from patient_name
    assert by_pid["pX"]["name"] == "Legacy Kid"
    assert by_pid["pX"]["prescription_count"] == 1


def test_family_404_when_customer_missing(monkeypatch):
    _seed(monkeypatch, [], {})
    assert _client().get("/api/v1/prescriptions/family/NOPE").status_code == 404


def test_family_empty_when_no_rx(monkeypatch):
    _seed(monkeypatch, [], {"C2": {"customer_id": "C2", "name": "Solo", "patients": [{"patient_id": "p1", "name": "Solo", "relation": "Self"}]}})
    body = _client().get("/api/v1/prescriptions/family/C2").json()
    assert body["total_prescriptions"] == 0
    assert body["member_count"] == 1
    assert body["members"][0]["prescription_count"] == 0
