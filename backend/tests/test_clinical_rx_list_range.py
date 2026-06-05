"""
IMS 2.0 - Clinical Rx-list + Test-History date-range
=====================================================
Regression tests for two audit fixes (both pages previously only ever showed
TODAY):

1. GET /clinical/tests gains a real date-RANGE branch
   - ``date=today`` still returns ONLY today's tests (legacy behaviour kept).
   - ``range=week|month|all`` and explicit ``from``/``to`` query the range
     server-side via EyeTestRepository.get_store_tests_in_range, instead of the
     Test-History page pulling today's rows and filtering Week/Month/All-Time in
     the browser.
   - Each COMPLETED test row is annotated with ``prescriptionId`` (the Rx
     auto-created on completion, looked up by eye_test_id) so the page's Print
     button can open the A5 card. A missing Rx leaves it unset (fail-soft).
   - The pure ``_resolve_test_date_range`` helper expands the keyword/explicit
     bounds correctly.

2. GET /prescriptions is the real Rx-library LIST (across dates)
   - Store-scoped to the caller's active store (or explicit store_id).
   - Honours an inclusive from_date/to_date window + customer_id filter.
   - Paginates (skip/limit) even on the store/customer paths (the repo helpers
     don't take skip/limit, so the router slices) and reports an honest total.

Mirrors the bare-app + dependency-override / monkeypatch pattern used across the
clinical/prescription tests so NO real MongoDB is required. ASCII only.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# In-memory fakes
# ============================================================================


class _FakeEyeTestRepo:
    """Stand-in for EyeTestRepository with a list of test docs.

    Records the arguments the router passes so a test can assert which code path
    fired (today shortcut vs whole-history vs range), and applies the same
    string-ISO date-range semantics the real repo does.
    """

    def __init__(self, docs):
        self._docs = docs
        self.today_calls = 0
        self.store_calls = 0
        self.range_calls = []

    def get_today_completed_tests(self, store_id):
        self.today_calls += 1
        today = date.today().isoformat()
        return [
            d
            for d in self._docs
            if d.get("store_id") == store_id
            and d.get("test_date") == today
            and d.get("status") == "COMPLETED"
        ]

    def get_store_tests(self, store_id, test_date=None, status=None):
        self.store_calls += 1
        out = [d for d in self._docs if d.get("store_id") == store_id]
        if test_date:
            out = [d for d in out if d.get("test_date") == test_date]
        if status:
            out = [d for d in out if d.get("status") == status]
        return out

    def get_store_tests_in_range(
        self, store_id, from_date=None, to_date=None, status=None, skip=0, limit=200
    ):
        self.range_calls.append((from_date, to_date))
        out = [d for d in self._docs if d.get("store_id") == store_id]
        if status:
            out = [d for d in out if d.get("status") == status]
        if from_date:
            out = [d for d in out if (d.get("test_date") or "") >= from_date]
        if to_date:
            out = [d for d in out if (d.get("test_date") or "") <= to_date]
        return out[skip : skip + limit]


class _FakeRxRepo:
    """Stand-in for PrescriptionRepository: eye-test lookup + store/customer
    filtered lists honouring from/to on prescription_date (string-comparable)."""

    def __init__(self, rx_docs=None, by_eye_test=None):
        self._rx = rx_docs or []
        self._by_eye_test = by_eye_test or {}

    # -- used by clinical /tests enrichment --
    def find_by_eye_test(self, eye_test_id):
        return self._by_eye_test.get(eye_test_id)

    # -- used by prescriptions list --
    def find_by_store(self, store_id, from_date=None, to_date=None):
        out = [r for r in self._rx if r.get("store_id") == store_id]
        # The real repo applies a datetime range on prescription_date; here the
        # seeded dates are ISO strings sorted lexically, equivalent for the test.
        if from_date is not None:
            f = from_date.isoformat()
            out = [r for r in out if (r.get("prescription_date") or "") >= f]
        if to_date is not None:
            t = to_date.isoformat()
            out = [r for r in out if (r.get("prescription_date") or "")[:10] <= t]
        out.sort(key=lambda r: r.get("prescription_date") or "", reverse=True)
        return out

    def find_by_customer(self, customer_id):
        out = [r for r in self._rx if r.get("customer_id") == customer_id]
        out.sort(key=lambda r: r.get("prescription_date") or "", reverse=True)
        return out

    def find_by_patient(self, patient_id):
        return [r for r in self._rx if r.get("patient_id") == patient_id]

    def find_by_optometrist(self, optometrist_id, from_date=None, to_date=None):
        return [r for r in self._rx if r.get("optometrist_id") == optometrist_id]

    def find_many(self, flt, skip=0, limit=100, sort=None):
        return list(self._rx)[skip : skip + limit]


def _clinical_client(monkeypatch, *, test_repo=None, rx_repo=None,
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
    monkeypatch.setattr(clinical, "get_eye_test_queue_repository", lambda: None)
    monkeypatch.setattr(clinical, "get_prescription_repository", lambda: rx_repo)
    return TestClient(app)


def _rx_client(monkeypatch, *, rx_repo=None, roles=("SALES_STAFF",),
               active_store="store-001"):
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Tester",
            "active_store_id": active_store,
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(prescriptions, "get_prescription_repository", lambda: rx_repo)
    monkeypatch.setattr(prescriptions, "get_customer_repository", lambda: None)
    return TestClient(app)


# ============================================================================
# 1a. Pure date-range resolver
# ============================================================================


class TestResolveTestDateRange:
    def test_explicit_from_to_wins_over_keyword(self):
        f, t = clinical._resolve_test_date_range("week", "2026-01-01", "2026-01-31")
        assert f == "2026-01-01"
        assert t == "2026-01-31"

    def test_today_keyword(self):
        iso = date.today().isoformat()
        assert clinical._resolve_test_date_range("today", None, None) == (iso, iso)

    def test_week_keyword_is_last_7_days_inclusive(self):
        f, t = clinical._resolve_test_date_range("week", None, None)
        assert t == date.today().isoformat()
        assert f == (date.today() - timedelta(days=6)).isoformat()

    def test_month_keyword_is_last_30_days(self):
        f, t = clinical._resolve_test_date_range("month", None, None)
        assert t == date.today().isoformat()
        assert f == (date.today() - timedelta(days=29)).isoformat()

    def test_all_and_unknown_and_empty_yield_no_bounds(self):
        assert clinical._resolve_test_date_range("all", None, None) == (None, None)
        assert clinical._resolve_test_date_range("banana", None, None) == (None, None)
        assert clinical._resolve_test_date_range(None, None, None) == (None, None)

    def test_one_open_bound(self):
        assert clinical._resolve_test_date_range(None, "2026-02-01", None) == (
            "2026-02-01",
            None,
        )
        assert clinical._resolve_test_date_range(None, None, "2026-02-28") == (
            None,
            "2026-02-28",
        )


# ============================================================================
# 1b. GET /clinical/tests range behaviour + Rx enrichment
# ============================================================================


def _seed_tests():
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=200)).isoformat()
    return [
        {
            "test_id": "t-today",
            "store_id": "store-001",
            "test_date": today,
            "status": "COMPLETED",
            "patient_name": "Today Patient",
        },
        {
            "test_id": "t-old",
            "store_id": "store-001",
            "test_date": old,
            "status": "COMPLETED",
            "patient_name": "Old Patient",
        },
    ]


class TestClinicalTestsRange:
    def test_date_today_uses_today_shortcut_only(self, monkeypatch):
        repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(monkeypatch, test_repo=repo)
        resp = client.get("/clinical/tests", params={"store_id": "store-001", "date": "today"})
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()["tests"]]
        assert ids == ["t-today"]
        assert repo.today_calls == 1
        assert repo.range_calls == []

    def test_range_all_returns_whole_history(self, monkeypatch):
        repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(monkeypatch, test_repo=repo)
        resp = client.get("/clinical/tests", params={"store_id": "store-001", "range": "all"})
        assert resp.status_code == 200
        ids = sorted(t["id"] for t in resp.json()["tests"])
        assert ids == ["t-old", "t-today"]
        # whole-history path, not the range query
        assert repo.store_calls == 1
        assert repo.range_calls == []

    def test_range_month_excludes_200_day_old_test(self, monkeypatch):
        repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(monkeypatch, test_repo=repo)
        resp = client.get(
            "/clinical/tests", params={"store_id": "store-001", "range": "month"}
        )
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()["tests"]]
        assert ids == ["t-today"]  # the 200-day-old row is outside the window
        assert len(repo.range_calls) == 1

    def test_explicit_from_to_queries_range(self, monkeypatch):
        repo = _FakeEyeTestRepo(_seed_tests())
        client = _clinical_client(monkeypatch, test_repo=repo)
        old = (date.today() - timedelta(days=200)).isoformat()
        resp = client.get(
            "/clinical/tests",
            params={"store_id": "store-001", "from": old, "to": old},
        )
        assert resp.status_code == 200
        ids = [t["id"] for t in resp.json()["tests"]]
        assert ids == ["t-old"]
        assert repo.range_calls == [(old, old)]

    def test_completed_test_is_annotated_with_prescription_id(self, monkeypatch):
        repo = _FakeEyeTestRepo(_seed_tests())
        rx_repo = _FakeRxRepo(
            by_eye_test={"t-today": {"prescription_id": "rx-today"}}
        )
        client = _clinical_client(monkeypatch, test_repo=repo, rx_repo=rx_repo)
        resp = client.get(
            "/clinical/tests", params={"store_id": "store-001", "range": "today"}
        )
        assert resp.status_code == 200
        row = next(t for t in resp.json()["tests"] if t["id"] == "t-today")
        assert row["prescriptionId"] == "rx-today"

    def test_missing_rx_link_is_fail_soft(self, monkeypatch):
        repo = _FakeEyeTestRepo(_seed_tests())
        rx_repo = _FakeRxRepo(by_eye_test={})  # no linked Rx
        client = _clinical_client(monkeypatch, test_repo=repo, rx_repo=rx_repo)
        resp = client.get(
            "/clinical/tests", params={"store_id": "store-001", "range": "today"}
        )
        assert resp.status_code == 200
        row = next(t for t in resp.json()["tests"] if t["id"] == "t-today")
        assert "prescriptionId" not in row or row.get("prescriptionId") is None


# ============================================================================
# 2. GET /prescriptions real Rx-library list
# ============================================================================


def _seed_rx():
    return [
        {
            "prescription_id": "rx-1",
            "store_id": "store-001",
            "customer_id": "cust-1",
            "patient_id": "pat-1",
            "rx_kind": "SPECTACLE",
            "prescription_date": "2026-05-20T10:00:00",
        },
        {
            "prescription_id": "rx-2",
            "store_id": "store-001",
            "customer_id": "cust-2",
            "patient_id": "pat-2",
            "rx_kind": "SPECTACLE",
            "prescription_date": "2026-05-10T10:00:00",
        },
        {
            "prescription_id": "rx-other-store",
            "store_id": "store-999",
            "customer_id": "cust-3",
            "rx_kind": "SPECTACLE",
            "prescription_date": "2026-05-15T10:00:00",
        },
    ]


class TestPrescriptionList:
    def test_lists_only_active_store(self, monkeypatch):
        client = _rx_client(monkeypatch, rx_repo=_FakeRxRepo(_seed_rx()))
        resp = client.get("/prescriptions")
        assert resp.status_code == 200
        body = resp.json()
        ids = {p["prescription_id"] for p in body["prescriptions"]}
        assert ids == {"rx-1", "rx-2"}  # the other store's Rx is excluded
        assert body["total"] == 2

    def test_explicit_store_id_overrides_active(self, monkeypatch):
        # An ADMIN is cross-store, so an explicit store_id legitimately overrides
        # the active store. A store-scoped role can NOT read another store's Rx
        # this way -- that cross-store IDOR (BUG-088) is covered in
        # test_pr1_security; here we assert the admin override still works.
        client = _rx_client(monkeypatch, rx_repo=_FakeRxRepo(_seed_rx()), roles=("ADMIN",))
        resp = client.get("/prescriptions", params={"store_id": "store-999"})
        assert resp.status_code == 200
        ids = {p["prescription_id"] for p in resp.json()["prescriptions"]}
        assert ids == {"rx-other-store"}

    def test_date_range_filters_server_side(self, monkeypatch):
        client = _rx_client(monkeypatch, rx_repo=_FakeRxRepo(_seed_rx()))
        # Only rx-1 (2026-05-20) falls in [2026-05-15, 2026-05-31]; rx-2 is older.
        resp = client.get(
            "/prescriptions",
            params={"from_date": "2026-05-15", "to_date": "2026-05-31"},
        )
        assert resp.status_code == 200
        ids = [p["prescription_id"] for p in resp.json()["prescriptions"]]
        assert ids == ["rx-1"]

    def test_customer_filter(self, monkeypatch):
        client = _rx_client(monkeypatch, rx_repo=_FakeRxRepo(_seed_rx()))
        resp = client.get("/prescriptions", params={"customer_id": "cust-2"})
        assert resp.status_code == 200
        ids = [p["prescription_id"] for p in resp.json()["prescriptions"]]
        assert ids == ["rx-2"]

    def test_pagination_applies_on_store_path(self, monkeypatch):
        client = _rx_client(monkeypatch, rx_repo=_FakeRxRepo(_seed_rx()))
        # limit=1 -> one row back, but total still reflects the full match count.
        resp = client.get("/prescriptions", params={"limit": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["prescriptions"]) == 1
        assert body["total"] == 2
        # skip=1 returns the second (older) row; newest-first sort puts rx-1 first.
        resp2 = client.get("/prescriptions", params={"limit": 1, "skip": 1})
        assert [p["prescription_id"] for p in resp2.json()["prescriptions"]] == ["rx-2"]

    def test_authenticated_sales_role_can_read(self, monkeypatch):
        # The list is AUTHENTICATED (read), so a sales role is allowed.
        client = _rx_client(monkeypatch, rx_repo=_FakeRxRepo(_seed_rx()), roles=("SALES_STAFF",))
        resp = client.get("/prescriptions")
        assert resp.status_code == 200


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
