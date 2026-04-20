"""
IMS 2.0 - Workshop KPIs + Pending Jobs Report tests (Phase 6.4)
=================================================================
Covers:

  - GET /api/v1/workshop/dashboard-kpis
    - Auth required
    - Empty envelope when repo unavailable
    - Status-wise counts + overdue detection
    - completed_today / delivered_today reflect today's closures
    - avg_turnaround_days requires ≥5 samples

  - GET /api/v1/reports/workshop/pending-jobs
    - Auth required
    - Empty envelope when DB absent
    - Aging buckets (0-3d, 3-7d, 7+d) classify correctly
    - Overdue rises to the top of `data`
    - by_technician aggregated + sorted desc
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Helpers
# ============================================================================


def _mk_job(
    job_id: str,
    status: str,
    created_days_ago: int = 1,
    expected_days_from_now: int = 1,
    completed_today: bool = False,
    delivered_today: bool = False,
    technician_id: str | None = None,
):
    """Build a workshop_jobs doc matching the repo schema."""
    now = datetime.now()
    doc: Dict[str, Any] = {
        "job_id": job_id,
        "job_number": f"WSJ-{job_id}",
        "order_id": f"ORD-{job_id}",
        "status": status,
        "store_id": "BV-TEST-01",
        "technician_id": technician_id,
        "created_at": (now - timedelta(days=created_days_ago)).isoformat(),
        "expected_date": (now + timedelta(days=expected_days_from_now)).isoformat(),
    }
    if completed_today:
        doc["completed_at"] = now.isoformat()
    if delivered_today and status == "DELIVERED":
        doc["status_updated_at"] = now.isoformat()
    return doc


class FakeRepo:
    """WorkshopJobRepository stub."""
    def __init__(self, jobs: List[Dict[str, Any]]):
        self._jobs = jobs

    def find_by_store(self, store_id):
        return [j for j in self._jobs if j.get("store_id") == store_id]

    def find_pending(self, store_id):
        return [
            j for j in self._jobs
            if j.get("store_id") == store_id
            and j.get("status") in ("PENDING", "IN_PROGRESS")
        ]


class FakeCollection:
    """MongoDB collection stand-in — only used by aggregate()."""
    def __init__(self, docs):
        self._docs = docs


class FakeDB:
    def __init__(self, jobs):
        self.is_connected = True
        self._jobs = jobs

    def get_collection(self, name):
        if name == "workshop_jobs":
            return FakeCollection(self._jobs)
        return FakeCollection([])


@pytest.fixture
def patched_repo(monkeypatch):
    """Patch workshop.get_workshop_repository() to return FakeRepo."""
    from api.routers import workshop as workshop_module

    def install(jobs: List[Dict[str, Any]]):
        repo = FakeRepo(jobs)
        monkeypatch.setattr(workshop_module, "get_workshop_repository", lambda: repo)
        return repo

    return install


@pytest.fixture
def patched_db_with_repo(monkeypatch):
    """
    Patch the reports endpoint's dependencies so it sees a fake DB and
    a fake WorkshopJobRepository instance.
    """
    def install(jobs):
        fake_db = FakeDB(jobs)
        fake_repo = FakeRepo(jobs)

        # Patch reports module's get_db import
        from api.routers import reports as reports_module
        monkeypatch.setattr(reports_module, "get_db", lambda: fake_db)
        # The endpoint constructs WorkshopJobRepository(db.get_collection...)
        # so we patch the class in its source module to return our fake.
        from database.repositories import workshop_repository as wr_module
        monkeypatch.setattr(
            wr_module, "WorkshopJobRepository",
            lambda _collection: fake_repo,
        )
        return fake_repo

    return install


# ============================================================================
# Workshop /dashboard-kpis
# ============================================================================


class TestKpisAuthAndEnvelope:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/workshop/dashboard-kpis")
        assert resp.status_code == 401

    def test_empty_when_repo_absent(self, client, auth_headers, monkeypatch):
        from api.routers import workshop as workshop_module
        monkeypatch.setattr(workshop_module, "get_workshop_repository", lambda: None)
        resp = client.get("/api/v1/workshop/dashboard-kpis", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["pending"] == 0
        assert body["overdue"] == 0
        assert body["avg_turnaround_days"] is None
        assert body["store_id"] == "BV-TEST-01"
        assert "as_of" in body


def test_kpis_counts_by_status(client, auth_headers, patched_repo):
    jobs = [
        _mk_job("j1", "PENDING"),
        _mk_job("j2", "IN_PROGRESS"),
        _mk_job("j3", "IN_PROGRESS"),
        _mk_job("j4", "READY"),
        _mk_job("j5", "QC_FAILED"),
        _mk_job("j6", "DELIVERED"),
    ]
    patched_repo(jobs)

    body = client.get("/api/v1/workshop/dashboard-kpis", headers=auth_headers).json()
    # pending = PENDING + IN_PROGRESS (matches the page's "Active Jobs")
    assert body["pending"] == 3
    assert body["in_progress"] == 2
    assert body["ready_for_pickup"] == 1
    assert body["qc_failed"] == 1


def test_kpis_detects_overdue(client, auth_headers, patched_repo):
    """A PENDING job with expected_date in the past counts as overdue."""
    jobs = [
        _mk_job("j1", "PENDING", expected_days_from_now=-2),   # overdue
        _mk_job("j2", "IN_PROGRESS", expected_days_from_now=-5),  # overdue
        _mk_job("j3", "PENDING", expected_days_from_now=3),     # not overdue
        _mk_job("j4", "DELIVERED", expected_days_from_now=-10), # closed, not overdue
    ]
    patched_repo(jobs)
    body = client.get("/api/v1/workshop/dashboard-kpis", headers=auth_headers).json()
    assert body["overdue"] == 2


def test_kpis_completed_today(client, auth_headers, patched_repo):
    jobs = [
        _mk_job("j1", "COMPLETED", completed_today=True),
        _mk_job("j2", "READY", completed_today=True),
        _mk_job("j3", "DELIVERED", delivered_today=True),
        _mk_job("j4", "READY", completed_today=False),  # no completed_at
    ]
    patched_repo(jobs)
    body = client.get("/api/v1/workshop/dashboard-kpis", headers=auth_headers).json()
    assert body["completed_today"] == 2
    assert body["delivered_today"] == 1


def test_kpis_avg_turnaround_needs_min_5_samples(client, auth_headers, patched_repo):
    """With <5 closed jobs, avg_turnaround_days is None to avoid noisy averages."""
    jobs = [
        _mk_job(f"j{i}", "COMPLETED", created_days_ago=5, completed_today=True)
        for i in range(4)
    ]
    patched_repo(jobs)
    body = client.get("/api/v1/workshop/dashboard-kpis", headers=auth_headers).json()
    assert body["avg_turnaround_days"] is None


def test_kpis_avg_turnaround_computed(client, auth_headers, patched_repo):
    """5+ samples produces a real rolling average."""
    jobs = [
        _mk_job(f"j{i}", "COMPLETED", created_days_ago=3, completed_today=True)
        for i in range(6)
    ]
    patched_repo(jobs)
    body = client.get("/api/v1/workshop/dashboard-kpis", headers=auth_headers).json()
    assert body["avg_turnaround_days"] is not None
    # Each job took ~3 days; allow small float tolerance
    assert 2.5 <= body["avg_turnaround_days"] <= 3.5


# ============================================================================
# /reports/workshop/pending-jobs
# ============================================================================


class TestReportAuthAndEnvelope:
    def test_requires_auth(self, client):
        resp = client.get("/api/v1/reports/workshop/pending-jobs")
        assert resp.status_code == 401

    def test_empty_when_db_absent(self, client, auth_headers, monkeypatch):
        from api.routers import reports as reports_module
        monkeypatch.setattr(reports_module, "get_db", lambda: None)
        resp = client.get(
            "/api/v1/reports/workshop/pending-jobs", headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["summary"]["total_pending"] == 0
        assert body["summary"]["overdue"] == 0


def test_report_aging_buckets(client, auth_headers, patched_db_with_repo):
    """0-3d / 3-7d / 7+d aging buckets classify by created_at age."""
    jobs = [
        _mk_job("j1", "PENDING", created_days_ago=1),   # 0-3d
        _mk_job("j2", "IN_PROGRESS", created_days_ago=2),  # 0-3d
        _mk_job("j3", "PENDING", created_days_ago=5),   # 3-7d
        _mk_job("j4", "IN_PROGRESS", created_days_ago=10), # 7+d
        _mk_job("j5", "IN_PROGRESS", created_days_ago=15), # 7+d
        _mk_job("j_done", "DELIVERED", created_days_ago=2),  # excluded
    ]
    patched_db_with_repo(jobs)

    body = client.get(
        "/api/v1/reports/workshop/pending-jobs", headers=auth_headers,
    ).json()
    assert body["summary"]["total_pending"] == 5
    assert body["summary"]["by_aging_bucket"] == {"0-3d": 2, "3-7d": 1, "7+d": 2}


def test_report_overdue_counts_and_sort(client, auth_headers, patched_db_with_repo):
    """Overdue rows float to the top, then by age desc."""
    jobs = [
        _mk_job("j_fresh", "PENDING", created_days_ago=1, expected_days_from_now=5),
        _mk_job("j_overdue_old", "PENDING", created_days_ago=8, expected_days_from_now=-3),
        _mk_job("j_overdue_new", "IN_PROGRESS", created_days_ago=2, expected_days_from_now=-1),
        _mk_job("j_normal_old", "PENDING", created_days_ago=6, expected_days_from_now=2),
    ]
    patched_db_with_repo(jobs)

    body = client.get(
        "/api/v1/reports/workshop/pending-jobs", headers=auth_headers,
    ).json()
    assert body["summary"]["overdue"] == 2
    # First two rows should be overdue (sorted among themselves by age desc)
    assert body["data"][0]["is_overdue"] is True
    assert body["data"][1]["is_overdue"] is True
    assert body["data"][0]["job_id"] == "j_overdue_old"  # older overdue
    assert body["data"][1]["job_id"] == "j_overdue_new"
    # Then non-overdue by age desc
    assert body["data"][2]["job_id"] == "j_normal_old"
    assert body["data"][3]["job_id"] == "j_fresh"


def test_report_technician_breakdown(client, auth_headers, patched_db_with_repo):
    """by_technician lists each tech with pending count, sorted desc."""
    jobs = [
        _mk_job("j1", "PENDING", technician_id="tech-A"),
        _mk_job("j2", "PENDING", technician_id="tech-A"),
        _mk_job("j3", "IN_PROGRESS", technician_id="tech-A"),
        _mk_job("j4", "PENDING", technician_id="tech-B"),
        _mk_job("j5", "PENDING", technician_id=None),  # unassigned
    ]
    patched_db_with_repo(jobs)

    body = client.get(
        "/api/v1/reports/workshop/pending-jobs", headers=auth_headers,
    ).json()
    by_tech = body["summary"]["by_technician"]
    # Sorted by count desc
    assert by_tech[0]["technician_id"] == "tech-A"
    assert by_tech[0]["count"] == 3
    # Second entry — could be tech-B or unassigned depending on hash order
    seen = {(row["technician_id"], row["count"]) for row in by_tech}
    assert ("tech-B", 1) in seen
    assert ("unassigned", 1) in seen
