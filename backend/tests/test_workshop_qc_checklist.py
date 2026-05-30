"""
IMS 2.0 - Workshop QC checklist + correctness-bug regression tests
===================================================================
Coverage:

  Part 1 — Bug regression tests (no DB needed):
    - Overdue detection uses date-only comparison (today's jobs NOT overdue)
    - `update_job` blocks READY/CANCELLED (not just COMPLETED/DELIVERED)
    - PATCH /status accepts JSON body (not just query params) -- frontend compat
    - PATCH /status maps PROCESSING -> IN_PROGRESS alias
    - State machine: PENDING->IN_PROGRESS->COMPLETED->READY->DELIVERED only
    - State machine: double-deliver (DELIVERED is terminal) blocked

  Part 2 — QC checklist feature:
    - POST /qc-checklist: role gate (sales/cashier blocked, workshop allowed)
    - POST /qc-checklist: wrong status returns 400
    - POST /qc-checklist: all pass -> READY, any fail -> QC_FAILED
    - POST /qc-checklist: waiver requires waive_reason
    - POST /qc-checklist: waiver with reason -> READY despite failures
    - Repository.add_qc_result: stores structured items + stamps status_updated_at
    - Repository.add_qc_result: appends to qc_history (survives re-QC)

  Part 3 — Repository unit tests:
    - find_overdue: date-only boundary (today NOT overdue, yesterday IS)
    - update_status(DELIVERED) stamps delivered_at
    - add_qc_result old API still works (no checklist_items arg)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import workshop as workshop_module
from api.routers.workshop import VALID_JOB_TRANSITIONS
from api.routers.auth import get_current_user


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _mk_job(
    job_id: str,
    status: str,
    expected_date: Optional[str] = None,
    created_days_ago: int = 1,
    completed_today: bool = False,
):
    now = datetime.now()
    doc: Dict[str, Any] = {
        "job_id": job_id,
        "job_number": f"WS-{job_id}",
        "order_id": f"ORD-{job_id}",
        "status": status,
        "store_id": "BV-TEST-01",
        "created_at": (now - timedelta(days=created_days_ago)).isoformat(),
        "expected_date": expected_date or (now + timedelta(days=3)).date().isoformat(),
    }
    if completed_today:
        doc["completed_at"] = now.isoformat()
    return doc


class FakeRepo:
    """Lightweight WorkshopJobRepository double — holds an in-memory job dict."""

    def __init__(self, jobs: Optional[List[Dict]] = None):
        self._jobs: Dict[str, Dict] = {j["job_id"]: j for j in (jobs or [])}
        self._status_updated: List[tuple] = []

    def find_by_id(self, job_id: str) -> Optional[Dict]:
        return self._jobs.get(job_id)

    def find_by_store(self, store_id: str) -> List[Dict]:
        return [j for j in self._jobs.values() if j.get("store_id") == store_id]

    def find_pending(self, store_id=None):
        return [
            j
            for j in self._jobs.values()
            if j.get("status") in ("PENDING", "IN_PROGRESS")
            and (store_id is None or j.get("store_id") == store_id)
        ]

    def update(self, job_id: str, fields: Dict) -> bool:
        if job_id in self._jobs:
            self._jobs[job_id].update(fields)
            return True
        return False

    def update_status(
        self, job_id: str, status: str, by_user: str = None, notes: str = None
    ) -> bool:
        self._status_updated.append((job_id, status))
        return self.update(
            job_id,
            {
                "status": status,
                "status_updated_at": datetime.now().isoformat(),
                **({"completed_at": datetime.now().isoformat()} if status == "COMPLETED" else {}),
                **({"delivered_at": datetime.now().isoformat()} if status == "DELIVERED" else {}),
            },
        )

    def add_qc_result(
        self,
        job_id: str,
        passed: bool,
        notes: str,
        by_user: str,
        checklist_items=None,
        waived=False,
        waive_reason=None,
    ) -> bool:
        target = "READY" if (passed or waived) else "QC_FAILED"
        qc_fields: Dict[str, Any] = {
            "qc_passed": passed,
            "qc_notes": notes,
            "qc_by": by_user,
            "qc_at": datetime.now().isoformat(),
            "qc_waived": waived,
        }
        if checklist_items:
            qc_fields["qc_checklist"] = checklist_items
        existing = self._jobs.get(job_id, {})
        history = list(existing.get("qc_history") or [])
        history.append({"passed": passed, "waived": waived, "notes": notes})
        qc_fields["qc_history"] = history
        self.update(job_id, qc_fields)
        return self.update_status(job_id, target, by_user)


def _client_with(monkeypatch, roles: List[str], fake_repo: Optional[FakeRepo] = None):
    """Build a TestClient with the given role and an optional fake repo."""
    app = FastAPI()
    app.include_router(workshop_module.router, prefix="/workshop")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "testuser",
            "full_name": "Test User",
            "active_store_id": "BV-TEST-01",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user

    if fake_repo is not None:
        monkeypatch.setattr(workshop_module, "get_workshop_repository", lambda: fake_repo)
        monkeypatch.setattr(workshop_module, "get_audit_repository", lambda: None)

    return TestClient(app)


# ---------------------------------------------------------------------------
QC_ITEMS_ALL_PASS = [
    {"key": "power", "label": "Power verification", "passed": True},
    {"key": "fitting", "label": "Fitting check", "passed": True},
    {"key": "cosmetic", "label": "Cosmetic check", "passed": True},
]

QC_ITEMS_ONE_FAIL = [
    {"key": "power", "label": "Power verification", "passed": True},
    {"key": "fitting", "label": "Fitting check", "passed": False, "note": "Left screw loose"},
    {"key": "cosmetic", "label": "Cosmetic check", "passed": True},
]


# ===========================================================================
# Part 1 — Bug regression
# ===========================================================================


class TestStateMachineTransitions:
    """State machine: only valid forward transitions are allowed."""

    def test_pending_can_go_to_in_progress(self):
        assert "IN_PROGRESS" in VALID_JOB_TRANSITIONS["PENDING"]

    def test_completed_gates_qc(self):
        assert "READY" in VALID_JOB_TRANSITIONS["COMPLETED"]
        assert "QC_FAILED" in VALID_JOB_TRANSITIONS["COMPLETED"]

    def test_ready_to_delivered(self):
        assert "DELIVERED" in VALID_JOB_TRANSITIONS["READY"]

    def test_delivered_is_terminal(self):
        assert len(VALID_JOB_TRANSITIONS["DELIVERED"]) == 0

    def test_cancelled_is_terminal(self):
        assert len(VALID_JOB_TRANSITIONS["CANCELLED"]) == 0

    def test_skip_blocked(self):
        assert "COMPLETED" not in VALID_JOB_TRANSITIONS["PENDING"]

    def test_backward_blocked(self):
        assert "IN_PROGRESS" not in VALID_JOB_TRANSITIONS["READY"]


class TestStatusEndpointBodyAndAlias:
    """PATCH /status accepts JSON body; PROCESSING is aliased to IN_PROGRESS."""

    def test_json_body_accepted(self, monkeypatch):
        job = _mk_job("j1", "PENDING")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.patch("/workshop/jobs/j1/status", json={"status": "IN_PROGRESS"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "IN_PROGRESS"

    def test_processing_alias_maps_to_in_progress(self, monkeypatch):
        """'PROCESSING' (legacy FE value) is silently mapped to 'IN_PROGRESS'."""
        job = _mk_job("j1", "PENDING")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.patch("/workshop/jobs/j1/status", json={"status": "PROCESSING"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "IN_PROGRESS"

    def test_query_param_still_works(self, monkeypatch):
        """Backward-compat: ?status= query param still accepted."""
        job = _mk_job("j1", "PENDING")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.patch("/workshop/jobs/j1/status?status=IN_PROGRESS")
        assert resp.status_code == 200, resp.text

    def test_missing_status_returns_422(self, monkeypatch):
        job = _mk_job("j1", "PENDING")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.patch("/workshop/jobs/j1/status", json={})
        assert resp.status_code == 422

    def test_double_deliver_blocked(self, monkeypatch):
        """DELIVERED is terminal — any further transition must be 400."""
        job = _mk_job("j1", "DELIVERED")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.patch("/workshop/jobs/j1/status", json={"status": "DELIVERED"})
        assert resp.status_code == 400

    def test_unknown_status_blocked(self, monkeypatch):
        job = _mk_job("j1", "PENDING")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.patch("/workshop/jobs/j1/status", json={"status": "NONEXISTENT"})
        assert resp.status_code == 400


class TestUpdateJobImmutabilityGuard:
    """PUT /jobs/{id} should block edits on terminal/post-QC states."""

    def _put(self, monkeypatch, status: str) -> int:
        job = _mk_job("j1", status)
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.put(
            "/workshop/jobs/j1",
            json={"fitting_instructions": "new instructions"},
        )
        return resp.status_code

    def test_pending_allowed(self, monkeypatch):
        assert self._put(monkeypatch, "PENDING") == 200

    def test_in_progress_allowed(self, monkeypatch):
        assert self._put(monkeypatch, "IN_PROGRESS") == 200

    def test_completed_blocked(self, monkeypatch):
        assert self._put(monkeypatch, "COMPLETED") == 400

    def test_ready_blocked(self, monkeypatch):
        # Bug fix: READY was previously NOT blocked
        assert self._put(monkeypatch, "READY") == 400

    def test_delivered_blocked(self, monkeypatch):
        assert self._put(monkeypatch, "DELIVERED") == 400

    def test_cancelled_blocked(self, monkeypatch):
        # Bug fix: CANCELLED was previously NOT blocked
        assert self._put(monkeypatch, "CANCELLED") == 400


class TestOverdueDetection:
    """Dashboard KPIs overdue count: today's jobs must NOT be flagged overdue."""

    def _kpi_overdue(self, monkeypatch, expected_date: str) -> int:
        job = _mk_job("j1", "PENDING", expected_date=expected_date)
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["SUPERADMIN"], repo)
        resp = client.get("/workshop/dashboard-kpis?store_id=BV-TEST-01")
        assert resp.status_code == 200
        return resp.json()["overdue"]

    def test_yesterday_overdue(self, monkeypatch):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert self._kpi_overdue(monkeypatch, yesterday) == 1

    def test_today_not_overdue(self, monkeypatch):
        # Bug fix: was comparing datetime string vs date string, causing
        # same-day jobs to appear overdue.
        today = date.today().isoformat()
        assert self._kpi_overdue(monkeypatch, today) == 0

    def test_tomorrow_not_overdue(self, monkeypatch):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert self._kpi_overdue(monkeypatch, tomorrow) == 0


# ===========================================================================
# Part 2 — QC checklist feature
# ===========================================================================


class TestQcChecklistRoleGate:
    """POST /qc-checklist gated to WORKSHOP_ROLES."""

    BLOCKED_ROLES = [["SALES_STAFF"], ["CASHIER"], ["SALES_CASHIER"]]
    ALLOWED_ROLES = [["WORKSHOP_STAFF"], ["STORE_MANAGER"], ["ADMIN"], ["SUPERADMIN"]]

    def _post(self, monkeypatch, roles: List[str]) -> int:
        job = _mk_job("j1", "COMPLETED")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, roles, repo)
        resp = client.post(
            "/workshop/jobs/j1/qc-checklist",
            json={"checklist": QC_ITEMS_ALL_PASS},
        )
        return resp.status_code

    @pytest.mark.parametrize("roles", BLOCKED_ROLES)
    def test_blocked_roles_get_403(self, monkeypatch, roles):
        assert self._post(monkeypatch, roles) == 403

    @pytest.mark.parametrize("roles", ALLOWED_ROLES)
    def test_allowed_roles_pass_gate(self, monkeypatch, roles):
        assert self._post(monkeypatch, roles) == 200


class TestQcChecklistStatusGate:
    """QC checklist only accepted for COMPLETED / QC_FAILED jobs."""

    def _post(self, monkeypatch, status: str) -> int:
        job = _mk_job("j1", status)
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["WORKSHOP_STAFF"], repo)
        resp = client.post(
            "/workshop/jobs/j1/qc-checklist",
            json={"checklist": QC_ITEMS_ALL_PASS},
        )
        return resp.status_code

    def test_completed_accepted(self, monkeypatch):
        assert self._post(monkeypatch, "COMPLETED") == 200

    def test_qc_failed_accepted(self, monkeypatch):
        assert self._post(monkeypatch, "QC_FAILED") == 200

    def test_pending_rejected(self, monkeypatch):
        assert self._post(monkeypatch, "PENDING") == 400

    def test_ready_rejected(self, monkeypatch):
        assert self._post(monkeypatch, "READY") == 400

    def test_delivered_rejected(self, monkeypatch):
        assert self._post(monkeypatch, "DELIVERED") == 400


class TestQcChecklistOutcome:
    """Correct status transitions based on checklist results."""

    def _submit(self, monkeypatch, checklist, extra=None):
        job = _mk_job("j1", "COMPLETED")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["WORKSHOP_STAFF"], repo)
        payload = {"checklist": checklist}
        if extra:
            payload.update(extra)
        resp = client.post("/workshop/jobs/j1/qc-checklist", json=payload)
        return resp, repo

    def test_all_pass_transitions_to_ready(self, monkeypatch):
        resp, repo = self._submit(monkeypatch, QC_ITEMS_ALL_PASS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "READY"
        assert body["qc_passed"] is True
        assert repo._jobs["j1"]["status"] == "READY"

    def test_any_fail_transitions_to_qc_failed(self, monkeypatch):
        resp, repo = self._submit(monkeypatch, QC_ITEMS_ONE_FAIL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "QC_FAILED"
        assert body["qc_passed"] is False
        assert repo._jobs["j1"]["status"] == "QC_FAILED"

    def test_all_pass_checklist_stored(self, monkeypatch):
        resp, repo = self._submit(monkeypatch, QC_ITEMS_ALL_PASS)
        stored = repo._jobs["j1"].get("qc_checklist") or []
        assert len(stored) == 3
        assert all(item["passed"] for item in stored)
        # Each stored item must have checked_by + checked_at
        assert all("checked_by" in item and "checked_at" in item for item in stored)

    def test_failed_item_note_stored(self, monkeypatch):
        resp, repo = self._submit(monkeypatch, QC_ITEMS_ONE_FAIL)
        stored = repo._jobs["j1"].get("qc_checklist") or []
        failing = [i for i in stored if i["key"] == "fitting"]
        assert len(failing) == 1
        assert failing[0]["note"] == "Left screw loose"

    def test_status_updated_at_stamped(self, monkeypatch):
        """Bug regression: add_qc_result must stamp status_updated_at."""
        resp, repo = self._submit(monkeypatch, QC_ITEMS_ALL_PASS)
        assert "status_updated_at" in repo._jobs["j1"], (
            "status_updated_at must be set by update_status — "
            "was missing because add_qc_result previously bypassed update_status"
        )


class TestQcChecklistWaiver:
    """QC waiver: failures can be overridden with a reason."""

    def _submit_waived(self, monkeypatch, waive_reason=None):
        job = _mk_job("j1", "COMPLETED")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["STORE_MANAGER"], repo)
        payload = {
            "checklist": QC_ITEMS_ONE_FAIL,
            "waived": True,
        }
        if waive_reason is not None:
            payload["waive_reason"] = waive_reason
        resp = client.post("/workshop/jobs/j1/qc-checklist", json=payload)
        return resp, repo

    def test_waiver_without_reason_422(self, monkeypatch):
        resp, _ = self._submit_waived(monkeypatch, waive_reason=None)
        assert resp.status_code == 422

    def test_waiver_with_empty_reason_422(self, monkeypatch):
        resp, _ = self._submit_waived(monkeypatch, waive_reason="")
        assert resp.status_code == 422

    def test_waiver_with_reason_transitions_to_ready(self, monkeypatch):
        resp, repo = self._submit_waived(
            monkeypatch, waive_reason="Customer consent — minor scratch accepted"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "READY"
        assert body["waived"] is True
        assert repo._jobs["j1"]["status"] == "READY"


class TestQcHistory:
    """QC history accumulates across multiple QC attempts."""

    def test_history_grows_on_re_qc(self, monkeypatch):
        job = _mk_job("j1", "COMPLETED")
        repo = FakeRepo([job])
        client = _client_with(monkeypatch, ["WORKSHOP_STAFF"], repo)

        # First QC — fail
        resp1 = client.post(
            "/workshop/jobs/j1/qc-checklist",
            json={"checklist": QC_ITEMS_ONE_FAIL},
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "QC_FAILED"

        # Second QC — pass (from QC_FAILED)
        resp2 = client.post(
            "/workshop/jobs/j1/qc-checklist",
            json={"checklist": QC_ITEMS_ALL_PASS},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "READY"

        # History must have two entries
        history = repo._jobs["j1"].get("qc_history") or []
        assert len(history) == 2
        assert history[0]["passed"] is False
        assert history[1]["passed"] is True


# ===========================================================================
# Part 3 — Repository unit tests (pure logic; no HTTP stack)
# ===========================================================================


class TestWorkshopRepositoryOverdue:
    """find_overdue: date-only boundary (today NOT overdue, yesterday IS)."""

    def _make_repo_with_jobs(self, jobs):
        """Return a WorkshopJobRepository backed by an in-memory store."""
        from database.repositories.workshop_repository import WorkshopJobRepository
        from unittest.mock import MagicMock

        fake_collection = MagicMock()
        repo = WorkshopJobRepository(fake_collection)

        store = {j["job_id"]: j for j in jobs}

        def _find_many(filter_dict, sort=None, skip=0, limit=None):
            out = []
            for doc in store.values():
                match = True
                for k, v in filter_dict.items():
                    if isinstance(v, dict):
                        if "$in" in v and doc.get(k) not in v["$in"]:
                            match = False
                        elif "$lt" in v and not (str(doc.get(k, "")) < str(v["$lt"])):
                            match = False
                    else:
                        if doc.get(k) != v:
                            match = False
                if match:
                    out.append(doc)
            return out

        repo.find_many = _find_many
        return repo

    def test_yesterday_is_overdue(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        repo = self._make_repo_with_jobs(
            [_mk_job("yesterday", "PENDING", expected_date=yesterday)]
        )
        overdue = repo.find_overdue("BV-TEST-01")
        assert any(j["job_id"] == "yesterday" for j in overdue)

    def test_today_not_overdue(self):
        today = date.today().isoformat()
        repo = self._make_repo_with_jobs(
            [_mk_job("today", "PENDING", expected_date=today)]
        )
        overdue = repo.find_overdue("BV-TEST-01")
        assert not any(j["job_id"] == "today" for j in overdue)


class TestWorkshopRepositoryUpdateStatus:
    """update_status stamps delivered_at on DELIVERED transitions."""

    def _make_repo_with_job(self, job):
        from database.repositories.workshop_repository import WorkshopJobRepository
        from unittest.mock import MagicMock

        fake_collection = MagicMock()
        repo = WorkshopJobRepository(fake_collection)
        store = {job["job_id"]: dict(job)}

        def _find_by_id(jid):
            return store.get(jid)

        def _update(jid, fields):
            if jid in store:
                store[jid].update(fields)
                return True
            return False

        repo.find_by_id = _find_by_id
        repo.update = _update
        repo._store = store
        return repo, store

    def test_delivered_stamps_delivered_at(self):
        job = _mk_job("j1", "READY")
        repo, store = self._make_repo_with_job(job)
        result = repo.update_status("j1", "DELIVERED", "u1")
        assert result is True
        assert "delivered_at" in store["j1"], "delivered_at must be stamped on DELIVERED"

    def test_completed_stamps_completed_at(self):
        job = _mk_job("j1", "IN_PROGRESS")
        repo, store = self._make_repo_with_job(job)
        repo.update_status("j1", "COMPLETED", "u1")
        assert "completed_at" in store["j1"]

    def test_add_qc_result_stamps_status_updated_at(self):
        """QC pass must stamp status_updated_at (via update_status)."""
        job = _mk_job("j1", "COMPLETED")
        repo, store = self._make_repo_with_job(job)
        result = repo.add_qc_result("j1", True, "all good", "u1")
        assert result is True
        assert store["j1"]["status"] == "READY"
        assert "status_updated_at" in store["j1"], (
            "status_updated_at missing — add_qc_result must call update_status"
        )

    def test_add_qc_result_with_checklist_items(self):
        job = _mk_job("j1", "COMPLETED")
        repo, store = self._make_repo_with_job(job)
        items = [{"key": "power", "label": "Power", "passed": True, "note": ""}]
        repo.add_qc_result("j1", True, "ok", "u1", checklist_items=items)
        assert store["j1"].get("qc_checklist") == items

    def test_add_qc_result_old_api_no_checklist(self):
        """Calling without checklist_items (old API) still works."""
        job = _mk_job("j1", "COMPLETED")
        repo, store = self._make_repo_with_job(job)
        result = repo.add_qc_result("j1", False, "scratch", "u1")
        assert result is True
        assert store["j1"]["status"] == "QC_FAILED"

    def test_add_qc_result_waiver_transitions_to_ready(self):
        """waived=True -> READY even when passed=False."""
        job = _mk_job("j1", "COMPLETED")
        repo, store = self._make_repo_with_job(job)
        repo.add_qc_result(
            "j1",
            False,
            "waived",
            "u1",
            waived=True,
            waive_reason="Manager approved",
        )
        assert store["j1"]["status"] == "READY"
        assert store["j1"]["qc_waived"] is True
