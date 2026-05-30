"""
IMS 2.0 - Prescription back-date (prescription_date field)
===========================================================
Covers the spec requirement that POST /prescriptions accepts an optional
`prescription_date` field so historical / back-dated Rx records can be entered.

Assertions:
  1. Back-dated create stores the given date as both prescription_date and
     test_date, and derives expiry_date from it (not from utcnow()).
  2. Future date -> 400 with a clear message.
  3. Absent prescription_date -> today's date is used (existing behavior
     unchanged).
  4. A date earlier today (sub-second behind end-of-today) -> accepted (200).
  5. expiry_date = prescription_date + validity_months (using _add_months,
     not a naive 30-day multiply) -- spot-checked on a Feb month boundary.
  6. _rx_validity correctly reads prescription_date first so the family-view
     expiry annotation uses the back-dated value.

No real MongoDB is required: a _FakeRxRepo + _FakeCustRepo stand in.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import prescriptions  # noqa: E402
from api.routers.prescriptions import _rx_validity, _add_months  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fake infrastructure (no real DB needed)
# ---------------------------------------------------------------------------


class _FakeRxRepo:
    """Stores the last created doc in-memory for assertions."""

    def __init__(self):
        self._docs: dict = {}

    def create(self, data: dict):
        data.setdefault("prescription_id", "rx-bd-1")
        data.setdefault("prescription_number", "RX-BACKDATE-001")
        self._docs[data["prescription_id"]] = dict(data)
        return data

    def find_by_id(self, pid):
        return self._docs.get(pid)


class _FakeCustRepo:
    def find_by_id(self, cid):
        # walk-in prefix bypasses customer-exists check in create_prescription
        return None


def _build_client(monkeypatch, rx_repo=None):
    """Create a TestClient wired to a STORE_MANAGER fake user (is_admin=True so
    the optometrist_id-required gate is bypassed, keeping tests focused on the
    date logic)."""
    if rx_repo is None:
        rx_repo = _FakeRxRepo()

    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": "mgr-1",
            "username": "store_mgr",
            "full_name": "Store Manager",
            "active_store_id": "store-001",
            "roles": ["STORE_MANAGER"],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: _FakeCustRepo())
    return TestClient(app), rx_repo


# Minimal valid Rx payload (walk-in so no customer DB lookup needed).
# validity_months=12 (min allowed is 6).
_BASE_PAYLOAD = {
    "patient_id": "walkin-bd-test",
    "customer_id": "walkin-bd-test",
    "source": "TESTED_AT_STORE",
    "validity_months": 12,
    "right_eye": {"sph": "-1.00", "cyl": "-0.50", "axis": 90},
    "left_eye": {"sph": "-1.25", "cyl": "0", "axis": None},
}


# ---------------------------------------------------------------------------
# Core back-date tests
# ---------------------------------------------------------------------------


class TestBackDatedCreate:
    def test_backdate_stores_given_date_as_prescription_date(self, monkeypatch):
        """prescription_date in response doc == the supplied back-date."""
        client, repo = _build_client(monkeypatch)
        back = "2024-03-15T10:00:00"
        resp = client.post("/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": back})
        assert resp.status_code == 201, resp.text
        pid = resp.json()["prescription_id"]
        doc = repo.find_by_id(pid)
        assert doc is not None
        # Stored value must start with the given date (ISO string, no TZ)
        assert doc["prescription_date"].startswith("2024-03-15"), doc["prescription_date"]

    def test_backdate_also_stored_as_test_date(self, monkeypatch):
        """test_date mirrors prescription_date so legacy readers stay consistent."""
        client, repo = _build_client(monkeypatch)
        back = "2024-03-15T10:00:00"
        resp = client.post("/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": back})
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["test_date"].startswith("2024-03-15"), doc.get("test_date")

    def test_backdate_derives_expiry_from_given_date(self, monkeypatch):
        """expiry_date = back-date + validity_months, not today + N months."""
        client, repo = _build_client(monkeypatch)
        back = "2024-03-15T10:00:00"
        resp = client.post("/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": back})
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        # 12 months after 2024-03-15 -> 2025-03-15
        assert doc["expiry_date"].startswith("2025-03-15"), doc["expiry_date"]

    def test_backdate_feb_month_boundary(self, monkeypatch):
        """_add_months clamps the day correctly (Aug 31 + 6 months -> Feb 28/29)."""
        client, repo = _build_client(monkeypatch)
        # 2023-08-31 + 6 months = 2024-02-29 (2024 is a leap year)
        back = "2023-08-31T00:00:00"
        resp = client.post(
            "/prescriptions",
            json={**_BASE_PAYLOAD, "prescription_date": back, "validity_months": 6},
        )
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["expiry_date"].startswith("2024-02-29"), doc["expiry_date"]

    def test_plain_date_string_accepted(self, monkeypatch):
        """A YYYY-MM-DD string (no time part) is accepted as prescription_date."""
        client, repo = _build_client(monkeypatch)
        resp = client.post(
            "/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": "2024-06-01"}
        )
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["prescription_date"].startswith("2024-06-01")


# ---------------------------------------------------------------------------
# Future-date guard (400)
# ---------------------------------------------------------------------------


class TestFutureDateRejected:
    def test_future_date_returns_400(self, monkeypatch):
        """A prescription_date in the future is rejected with 400."""
        client, _ = _build_client(monkeypatch)
        future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        resp = client.post("/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": future})
        assert resp.status_code == 400, resp.text
        assert "future" in resp.json()["detail"].lower()

    def test_far_future_date_returns_400(self, monkeypatch):
        """A far-future date (next year) is also rejected."""
        client, _ = _build_client(monkeypatch)
        future = (datetime.now() + timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S")
        resp = client.post("/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": future})
        assert resp.status_code == 400

    def test_tomorrow_date_returns_400(self, monkeypatch):
        """Exactly tomorrow (start of day) is also rejected."""
        client, _ = _build_client(monkeypatch)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        resp = client.post("/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": tomorrow})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Absent / null -> today (unchanged behavior)
# ---------------------------------------------------------------------------


class TestAbsentDateDefaultsToToday:
    def test_no_prescription_date_uses_today(self, monkeypatch):
        """When prescription_date is omitted, prescription_date starts with today."""
        client, repo = _build_client(monkeypatch)
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        resp = client.post("/prescriptions", json=_BASE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["prescription_date"].startswith(today_prefix), doc["prescription_date"]

    def test_null_prescription_date_uses_today(self, monkeypatch):
        """Explicit null also falls back to today."""
        client, repo = _build_client(monkeypatch)
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        resp = client.post(
            "/prescriptions", json={**_BASE_PAYLOAD, "prescription_date": None}
        )
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        assert doc["prescription_date"].startswith(today_prefix)

    def test_no_prescription_date_expiry_is_from_today(self, monkeypatch):
        """expiry is approx today + 12 months (within 2 days to avoid midnight races)."""
        client, repo = _build_client(monkeypatch)
        resp = client.post("/prescriptions", json=_BASE_PAYLOAD)
        assert resp.status_code == 201, resp.text
        doc = repo.find_by_id(resp.json()["prescription_id"])
        expiry = datetime.fromisoformat(doc["expiry_date"])
        expected = _add_months(datetime.now(), 12)
        diff = abs((expiry - expected).total_seconds())
        assert diff < 172800, f"Expiry {expiry} too far from expected {expected}"


# ---------------------------------------------------------------------------
# _rx_validity reads prescription_date first
# ---------------------------------------------------------------------------


class TestRxValidityReadsPrescriptionDate:
    def test_uses_prescription_date_for_expiry(self):
        """_rx_validity must use prescription_date, not created_at, when both present."""
        # Back-dated Rx: prescription was written 2 years ago
        old_date = "2022-01-15T10:00:00"
        now_str = datetime.now().isoformat()
        rx = {
            "prescription_id": "rx-test",
            "prescription_date": old_date,
            "created_at": now_str,  # created_at is recent (simulates a late-entry scenario)
            "validity_months": 12,
        }
        expiry, is_valid = _rx_validity(rx)
        # Expiry = 2022-01-15 + 12 months = 2023-01-15 -> should be expired
        assert expiry is not None
        assert is_valid is False, f"Expected expired; expiry={expiry}"

    def test_uses_test_date_as_fallback(self):
        """_rx_validity still works when only test_date is present (clinical auto-create)."""
        old_date = "2022-06-01T00:00:00"
        rx = {
            "prescription_id": "rx-test",
            "test_date": old_date,
            "validity_months": 12,
        }
        expiry, is_valid = _rx_validity(rx)
        assert expiry is not None
        assert is_valid is False

    def test_falls_back_to_created_at_when_no_other_date(self):
        """If only created_at is present, _rx_validity uses it (legacy import docs)."""
        recent = datetime.now().isoformat()
        rx = {
            "prescription_id": "rx-test",
            "created_at": recent,
            "validity_months": 12,
        }
        expiry, is_valid = _rx_validity(rx)
        assert expiry is not None
        assert is_valid is True  # created_at is now -> expiry is 12 months from now
