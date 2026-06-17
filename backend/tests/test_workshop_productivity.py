"""
IMS 2.0 - Workshop Productivity Report tests
============================================
Covers GET /api/v1/reports/workshop/productivity:

  - Auth + role gate (workshop-report roles only; sales staff 403)
  - Empty envelope when DB absent / no jobs
  - Per-technician jobs_completed counted from CLOSED jobs in the window
  - Window filter on completed_at (date-range inclusive)
  - avg_turnaround_days per technician
  - qc_fail_rate from qc_history (any failed attempt) + qc_passed fallback
  - on_time_rate from completed_at vs expected_date
  - utilization = tech jobs / busiest tech jobs (relative load index)
  - store totals reflect the same window population
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


STORE = "BV-TEST-01"


def _mk_job(
    job_id: str,
    status: str = "COMPLETED",
    technician_id: str | None = "tech-a",
    created_days_ago: int = 5,
    completed_days_ago: int = 0,
    expected_days_from_completed: int = 1,
    qc_passed: bool | None = None,
    qc_history: List[Dict[str, Any]] | None = None,
    remake: bool = False,
) -> Dict[str, Any]:
    """Build a CLOSED workshop_jobs doc for the productivity window."""
    now = datetime.now()
    completed = now - timedelta(days=completed_days_ago)
    created = now - timedelta(days=created_days_ago)
    expected = completed + timedelta(days=expected_days_from_completed)
    doc: Dict[str, Any] = {
        "job_id": job_id,
        "job_number": f"WSJ-{job_id}",
        "order_id": f"ORD-{job_id}",
        "status": status,
        "store_id": STORE,
        "technician_id": technician_id,
        "created_at": created.isoformat(),
        "completed_at": completed.isoformat(),
        "expected_date": expected.date().isoformat(),
    }
    if qc_passed is not None:
        doc["qc_passed"] = qc_passed
    if qc_history is not None:
        doc["qc_history"] = qc_history
    if remake:
        doc["remake_reasons"] = [{"code": "POWER", "at": completed.isoformat()}]
    return doc


class FakeRepo:
    def __init__(self, jobs: List[Dict[str, Any]]):
        self._jobs = jobs

    def find_by_store(self, store_id):
        return [j for j in self._jobs if j.get("store_id") == store_id]


class FakeDB:
    def __init__(self, jobs):
        self.is_connected = True
        self._jobs = jobs

    def get_collection(self, name):
        return None  # endpoint constructs the repo via a patched class


@pytest.fixture
def install(monkeypatch):
    """Patch the reports endpoint's get_db + WorkshopJobRepository to a fake."""

    def _install(jobs: List[Dict[str, Any]]):
        fake_db = FakeDB(jobs)
        fake_repo = FakeRepo(jobs)
        from api.routers import reports as reports_module

        monkeypatch.setattr(reports_module, "get_db", lambda: fake_db)
        from database.repositories import workshop_repository as wr_module

        monkeypatch.setattr(
            wr_module, "WorkshopJobRepository", lambda _coll: fake_repo
        )
        return fake_repo

    return _install


URL = "/api/v1/reports/workshop/productivity"


# ---------------------------------------------------------------------------
# Auth + role gate
# ---------------------------------------------------------------------------


def test_requires_auth(client):
    assert client.get(URL).status_code == 401


def test_sales_staff_forbidden(client, staff_headers, install):
    install([])
    resp = client.get(URL, headers=staff_headers)
    assert resp.status_code == 403


def test_admin_allowed_empty(client, auth_headers, monkeypatch):
    """No DB -> honest empty envelope, never raises."""
    from api.routers import reports as reports_module

    monkeypatch.setattr(reports_module, "get_db", lambda: None)
    resp = client.get(URL, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["technicians"] == []
    assert body["totals"]["jobs_completed"] == 0
    assert body["totals"]["avg_turnaround_days"] is None
    assert "from_date" in body and "to_date" in body


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_jobs_completed_per_technician(client, auth_headers, install):
    jobs = [
        _mk_job("j1", technician_id="tech-a", completed_days_ago=1),
        _mk_job("j2", technician_id="tech-a", completed_days_ago=2),
        _mk_job("j3", technician_id="tech-b", completed_days_ago=1),
        # PENDING job -> not closed -> excluded
        _mk_job("j4", status="PENDING", technician_id="tech-a"),
    ]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    by_tech = {t["technician_id"]: t for t in body["technicians"]}
    assert by_tech["tech-a"]["jobs_completed"] == 2
    assert by_tech["tech-b"]["jobs_completed"] == 1
    assert body["totals"]["jobs_completed"] == 3
    assert body["totals"]["technicians_active"] == 2


def test_window_filters_on_completed_at(client, auth_headers, install):
    """Only jobs closed within [from_date, to_date] are scored."""
    jobs = [
        _mk_job("in1", completed_days_ago=2),    # inside default 30d
        _mk_job("old1", completed_days_ago=60),  # outside default 30d
    ]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    assert body["totals"]["jobs_completed"] == 1
    # Explicit narrow window excludes the in1 job too
    today = datetime.now().date()
    frm = (today - timedelta(days=10)).isoformat()
    to = (today - timedelta(days=5)).isoformat()
    body2 = client.get(
        URL, headers=auth_headers, params={"from_date": frm, "to_date": to}
    ).json()
    assert body2["totals"]["jobs_completed"] == 0


def test_avg_turnaround_days(client, auth_headers, install):
    # created 5d before now, completed today -> ~5 day turnaround
    jobs = [_mk_job("j1", created_days_ago=5, completed_days_ago=0)]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    t = body["technicians"][0]
    assert t["avg_turnaround_days"] is not None
    assert 4.5 <= t["avg_turnaround_days"] <= 5.5


def test_qc_fail_rate_from_history(client, auth_headers, install):
    jobs = [
        # passed-only history -> not a fail
        _mk_job("p1", qc_history=[{"passed": True}]),
        # one failed attempt then pass -> counts as a fail (rework happened)
        _mk_job("f1", qc_history=[{"passed": False}, {"passed": True}]),
        # qc_passed=False fallback -> fail
        _mk_job("f2", qc_passed=False),
        # no QC at all -> not in qc sample
        _mk_job("n1"),
    ]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    t = body["technicians"][0]
    assert t["qc_jobs"] == 3          # p1, f1, f2 went through QC
    assert t["qc_fail_rate"] == round(2 / 3, 4)
    assert body["totals"]["qc_fail_rate"] == round(2 / 3, 4)


def test_on_time_rate(client, auth_headers, install):
    jobs = [
        # completed today, expected tomorrow -> on time
        _mk_job("ot1", completed_days_ago=0, expected_days_from_completed=1),
        # completed today, expected yesterday -> late
        _mk_job("late1", completed_days_ago=0, expected_days_from_completed=-1),
    ]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    assert body["totals"]["on_time_rate"] == 0.5


def test_utilization_relative_to_busiest(client, auth_headers, install):
    jobs = [
        _mk_job("a1", technician_id="tech-a", completed_days_ago=1),
        _mk_job("a2", technician_id="tech-a", completed_days_ago=2),
        _mk_job("a3", technician_id="tech-a", completed_days_ago=3),
        _mk_job("b1", technician_id="tech-b", completed_days_ago=1),
    ]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    by_tech = {t["technician_id"]: t for t in body["technicians"]}
    assert by_tech["tech-a"]["utilization"] == 1.0       # busiest
    assert by_tech["tech-b"]["utilization"] == round(1 / 3, 4)


def test_remake_rate(client, auth_headers, install):
    jobs = [
        _mk_job("r1", remake=True, completed_days_ago=1),
        _mk_job("r2", remake=False, completed_days_ago=1),
    ]
    install(jobs)
    body = client.get(URL, headers=auth_headers).json()
    assert body["totals"]["remake_rate"] == 0.5
