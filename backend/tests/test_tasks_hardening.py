"""
Tasks/SOP hardening regression tests (harden-tasks phase)
=========================================================
Covers the correctness/validation bugs fixed in this PR:

  1. Task lifecycle: terminal tasks block all mutations
  2. No double-complete (COMPLETED and CANCELLED both block /complete)
  3. No backward transitions via PATCH (e.g. COMPLETED -> OPEN blocked;
     PATCH with new_status=COMPLETED redirected to /complete)
  4. /start blocked on CANCELLED (and COMPLETED)
  5. /reassign blocked on CANCELLED (not just COMPLETED)
  6. /acknowledge blocked on COMPLETED/CANCELLED; idempotent on IN_PROGRESS
  7. /escalate blocked on terminal tasks
  8. due_at-in-the-past blocked on create
  9. SOP step_number duplicate rejected
 10. SOP invalid frequency/category rejected
 11. completion_notes empty string rejected (min_length=3)
 12. /sop-checklist/item rejects step_number=0 (schema ge=1)
 13. Status transition history is recorded on PATCH
 14. /complete records history entry
 15. assigned_to empty string rejected
 16. TaskReassign.assigned_to empty string rejected
 17. Priority enum validation (P0-P4 only)

The router calls get_task_repository() directly (plain function, not
Depends), so we patch it at module level with unittest.mock.patch.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Fake repo helpers
# ---------------------------------------------------------------------------


class _FakeRepo:
    """Minimal in-memory task repo for testing lifecycle guards."""

    def __init__(self, tasks: Optional[List[Dict[str, Any]]] = None) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {
            t["task_id"]: dict(t) for t in (tasks or [])
        }
        self.updates: List[tuple] = []

    def find_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._tasks.get(task_id)

    def find_many(self, *_a, **_kw) -> List[Dict[str, Any]]:
        return list(self._tasks.values())

    def count(self, *_a, **_kw) -> int:
        return len(self._tasks)

    def create(self, doc: dict) -> dict:
        self._tasks[doc["task_id"]] = doc
        return doc

    def update(self, task_id: str, data: dict) -> bool:
        if task_id in self._tasks:
            self._tasks[task_id].update(data)
            self.updates.append((task_id, dict(data)))
            return True
        return False


def _task(**kwargs) -> Dict[str, Any]:
    """Build a minimal valid task dict for tests."""
    defaults = {
        "task_id": "TASK-AABBCCDD",
        "title": "Test Task",
        "status": "OPEN",
        "priority": "P2",
        "assigned_to": "user-1",
        "store_id": "S1",
        "escalation_level": 0,
        "history": [],
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "due_at": datetime.now() + timedelta(hours=4),
    }
    defaults.update(kwargs)
    return defaults


def _make_app() -> FastAPI:
    """Build a test FastAPI app with a fake authenticated user."""
    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/tasks")

    async def _user():
        return {
            "user_id": "u-test",
            "active_store_id": "S1",
            "roles": ["STORE_MANAGER"],
        }

    app.dependency_overrides[get_current_user] = _user
    return app


_APP = _make_app()


def _client_with_repo(repo: Any) -> TestClient:
    """Return a TestClient whose underlying calls to get_task_repository()
    are patched to return ``repo``."""
    # We must build a *new* client each call so the context manager works.
    return TestClient(_APP)


# ---------------------------------------------------------------------------
# 1. Terminal tasks block all mutations via PATCH
# ---------------------------------------------------------------------------


class TestTerminalTaskBlocked:
    def test_patch_completed_task_rejected(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"priority": "P1"}
            )
        assert r.status_code == 400
        assert "COMPLETED" in r.json()["detail"]

    def test_patch_cancelled_task_rejected(self):
        repo = _FakeRepo([_task(status="CANCELLED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"priority": "P1"}
            )
        assert r.status_code == 400
        assert "CANCELLED" in r.json()["detail"]

    def test_put_alias_also_blocked(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).put(
                "/tasks/TASK-AABBCCDD", json={"priority": "P1"}
            )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 2. No double-complete and no completing a CANCELLED task
# ---------------------------------------------------------------------------


class TestCompleteEndpointGuards:
    def test_double_complete_returns_400(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "Already done"},
            )
        assert r.status_code == 400
        assert "COMPLETED" in r.json()["detail"]

    def test_cancelled_task_cannot_be_completed(self):
        repo = _FakeRepo([_task(status="CANCELLED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "Trying anyway"},
            )
        assert r.status_code == 400
        assert "CANCELLED" in r.json()["detail"]

    def test_open_task_can_be_completed(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "Done!"},
            )
        assert r.status_code == 200
        assert r.json()["status"] == "COMPLETED"

    def test_escalated_task_can_be_completed(self):
        repo = _FakeRepo([_task(status="ESCALATED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "Resolved escalation"},
            )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 3. PATCH with status=COMPLETED redirected (must use /complete)
# ---------------------------------------------------------------------------


class TestPatchStatusCompletedBlocked:
    def test_patch_status_completed_blocked(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"status": "COMPLETED"}
            )
        assert r.status_code == 400
        assert "complete" in r.json()["detail"].lower()

    def test_patch_status_cancelled_allowed(self):
        """Manager can cancel an OPEN task via PATCH."""
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"status": "CANCELLED"}
            )
        assert r.status_code == 200

    def test_patch_status_in_progress_allowed(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"status": "IN_PROGRESS"}
            )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 4. /start blocked on CANCELLED (and COMPLETED)
# ---------------------------------------------------------------------------


class TestStartTaskGuards:
    def test_start_completed_task_blocked(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/start")
        assert r.status_code == 400
        assert "COMPLETED" in r.json()["detail"]

    def test_start_cancelled_task_blocked(self):
        repo = _FakeRepo([_task(status="CANCELLED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/start")
        assert r.status_code == 400
        assert "CANCELLED" in r.json()["detail"]

    def test_start_open_task_ok(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/start")
        assert r.status_code == 200
        assert r.json()["status"] == "IN_PROGRESS"

    def test_start_in_progress_idempotent(self):
        repo = _FakeRepo([_task(status="IN_PROGRESS")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/start")
        assert r.status_code == 200
        assert "progress" in r.json()["message"].lower()


# ---------------------------------------------------------------------------
# 5. /reassign blocked on CANCELLED (not just COMPLETED)
# ---------------------------------------------------------------------------


class TestReassignGuards:
    def test_reassign_completed_blocked(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post(
                "/tasks/TASK-AABBCCDD/reassign", json={"assigned_to": "u2"}
            )
        assert r.status_code == 400
        assert "COMPLETED" in r.json()["detail"]

    def test_reassign_cancelled_blocked(self):
        repo = _FakeRepo([_task(status="CANCELLED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post(
                "/tasks/TASK-AABBCCDD/reassign", json={"assigned_to": "u2"}
            )
        assert r.status_code == 400
        assert "CANCELLED" in r.json()["detail"]

    def test_reassign_open_task_ok(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post(
                "/tasks/TASK-AABBCCDD/reassign",
                json={"assigned_to": "u2", "reason": "Vacation"},
            )
        assert r.status_code == 200
        assert r.json()["assigned_to"] == "u2"


# ---------------------------------------------------------------------------
# 6. /acknowledge blocked on terminal; idempotent on IN_PROGRESS
# ---------------------------------------------------------------------------


class TestAcknowledgeGuards:
    def test_acknowledge_completed_blocked(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/acknowledge")
        assert r.status_code == 400

    def test_acknowledge_cancelled_blocked(self):
        repo = _FakeRepo([_task(status="CANCELLED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/acknowledge")
        assert r.status_code == 400

    def test_acknowledge_in_progress_idempotent(self):
        repo = _FakeRepo([_task(status="IN_PROGRESS")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/acknowledge")
        assert r.status_code == 200
        assert "already" in r.json()["message"].lower()

    def test_acknowledge_open_transitions_to_in_progress(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post("/tasks/TASK-AABBCCDD/acknowledge")
        assert r.status_code == 200
        assert r.json()["status"] == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# 7. /escalate blocked on terminal tasks
# ---------------------------------------------------------------------------


class TestEscalateGuards:
    def test_escalate_completed_blocked(self):
        repo = _FakeRepo([_task(status="COMPLETED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post(
                "/tasks/TASK-AABBCCDD/escalate",
                params={"escalate_to": "mgr-1"},
            )
        assert r.status_code == 400
        assert "COMPLETED" in r.json()["detail"]

    def test_escalate_cancelled_blocked(self):
        repo = _FakeRepo([_task(status="CANCELLED")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post(
                "/tasks/TASK-AABBCCDD/escalate",
                params={"escalate_to": "mgr-1"},
            )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 8. due_at in the past blocked on create
# ---------------------------------------------------------------------------


class TestDueDateValidation:
    def _create_app(self):
        app = FastAPI()
        app.include_router(tasks_mod.router, prefix="/tasks")

        async def _user():
            return {
                "user_id": "u1",
                "active_store_id": "S1",
                "roles": ["STORE_MANAGER"],
            }

        app.dependency_overrides[get_current_user] = _user
        return app

    def test_past_due_date_rejected(self):
        past = (datetime.now() - timedelta(days=1)).isoformat()
        r = TestClient(self._create_app()).post(
            "/tasks",
            json={
                "title": "Past task",
                "assigned_to": "u1",
                "due_at": past,
            },
        )
        assert r.status_code == 422

    def test_future_due_date_accepted(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        repo = _FakeRepo()
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(self._create_app()).post(
                "/tasks",
                json={
                    "title": "Future task",
                    "assigned_to": "u1",
                    "due_at": future,
                },
            )
        # Schema validation passes (repo create gets called)
        assert r.status_code != 422

    def test_slightly_past_within_grace_accepted(self):
        """Timestamps up to 5 min in the past are allowed (clock drift)."""
        slightly_past = (datetime.now() - timedelta(minutes=2)).isoformat()
        repo = _FakeRepo()
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(self._create_app()).post(
                "/tasks",
                json={
                    "title": "Slight past task",
                    "assigned_to": "u1",
                    "due_at": slightly_past,
                },
            )
        assert r.status_code != 422


# ---------------------------------------------------------------------------
# 9 + 10. SOP template validation
# ---------------------------------------------------------------------------


class TestSopTemplateValidation:
    def _sop_app(self):
        app = FastAPI()
        app.include_router(tasks_mod.router, prefix="/tasks")

        async def _user():
            return {
                "user_id": "u-admin",
                "active_store_id": "S1",
                "roles": ["SUPERADMIN"],
            }

        app.dependency_overrides[get_current_user] = _user
        return app

    def test_duplicate_step_numbers_rejected(self):
        steps = [
            {"step_number": 1, "instruction": "First"},
            {"step_number": 1, "instruction": "Duplicate"},  # duplicate!
        ]
        r = TestClient(self._sop_app()).post(
            "/tasks/sop-templates",
            json={
                "title": "Test SOP",
                "category": "Operations",
                "frequency": "DAILY",
                "steps": steps,
            },
        )
        assert r.status_code == 422
        assert "duplicate" in r.text.lower() or "Duplicate" in r.text

    def test_unique_step_numbers_accepted(self):
        steps = [
            {"step_number": 1, "instruction": "First"},
            {"step_number": 2, "instruction": "Second"},
        ]
        # With no DB available, the endpoint returns 503 (DB unavailable).
        # The important thing is it does NOT return 422 (schema error).
        r = TestClient(self._sop_app()).post(
            "/tasks/sop-templates",
            json={
                "title": "Test SOP",
                "category": "Operations",
                "frequency": "DAILY",
                "steps": steps,
            },
        )
        assert r.status_code != 422

    def test_invalid_category_rejected(self):
        r = TestClient(self._sop_app()).post(
            "/tasks/sop-templates",
            json={
                "title": "Bad Category SOP",
                "category": "NONEXISTENT",
                "frequency": "DAILY",
            },
        )
        assert r.status_code == 422

    def test_invalid_frequency_rejected(self):
        r = TestClient(self._sop_app()).post(
            "/tasks/sop-templates",
            json={
                "title": "Bad Freq SOP",
                "category": "Operations",
                "frequency": "HOURLY",  # not valid
            },
        )
        assert r.status_code == 422

    def test_valid_category_and_frequency_accepted(self):
        """No schema validation error for valid inputs."""
        for cat in ["Operations", "Finance", "Sales", "Clinical", "Workshop"]:
            r = TestClient(self._sop_app()).post(
                "/tasks/sop-templates",
                json={"title": "Test", "category": cat, "frequency": "DAILY"},
            )
            assert r.status_code != 422, f"Unexpected 422 for category={cat}"

    def test_update_template_invalid_frequency_rejected(self):
        r = TestClient(self._sop_app()).patch(
            "/tasks/sop-templates/SOP-TEST",
            json={"frequency": "HOURLY"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 11. completion_notes empty string rejected (min_length=3)
# ---------------------------------------------------------------------------


class TestCompletionNotesValidation:
    def test_empty_notes_rejected(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": ""},
            )
        assert r.status_code == 422

    def test_too_short_notes_rejected(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "ab"},  # 2 chars, min is 3
            )
        assert r.status_code == 422

    def test_valid_notes_accepted(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "Done"},
            )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# 12. SOP checklist schema: step_number must be >= 1
# ---------------------------------------------------------------------------


class TestSopStepNumberValidation:
    def test_sop_checklist_toggle_schema_requires_step_ge1(self):
        """step_number must be >= 1 per the schema (SopChecklistItemToggle)."""
        with patch.object(tasks_mod, "get_task_repository", return_value=_FakeRepo()):
            r = TestClient(_APP).post(
                "/tasks/sop-checklist/item",
                json={
                    "template_id": "SOP-ABC",
                    "step_number": 0,  # invalid, ge=1
                    "completed": True,
                },
            )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 13. Status transition history recorded on PATCH
# ---------------------------------------------------------------------------


class TestHistoryRecording:
    def test_patch_status_records_history_entry(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"status": "IN_PROGRESS"}
            )
        assert r.status_code == 200
        # Check history was appended in the repo update
        assert len(repo.updates) >= 1
        _, update_data = repo.updates[-1]
        history = update_data.get("history", [])
        assert any(
            h.get("action") == "status_change"
            and h.get("from") == "OPEN"
            and h.get("to") == "IN_PROGRESS"
            for h in history
        ), f"Expected status_change history entry, got: {history}"

    def test_complete_records_history_entry(self):
        repo = _FakeRepo([_task(status="IN_PROGRESS")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD/complete",
                json={"completion_notes": "All done"},
            )
        assert r.status_code == 200
        assert len(repo.updates) >= 1
        _, update_data = repo.updates[-1]
        history = update_data.get("history", [])
        assert any(
            h.get("action") == "completed"
            for h in history
        ), f"Expected 'completed' history entry, got: {history}"


# ---------------------------------------------------------------------------
# 14. assigned_to must be non-empty (min_length=1)
# ---------------------------------------------------------------------------


class TestAssignedToValidation:
    def _create_app(self):
        app = FastAPI()
        app.include_router(tasks_mod.router, prefix="/tasks")

        async def _user():
            return {"user_id": "u1", "active_store_id": "S1", "roles": ["STORE_MANAGER"]}

        app.dependency_overrides[get_current_user] = _user
        return app

    def test_create_task_empty_assigned_to_rejected(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        r = TestClient(self._create_app()).post(
            "/tasks",
            json={
                "title": "Test Task",
                "assigned_to": "",  # empty string
                "due_at": future,
            },
        )
        assert r.status_code == 422

    def test_reassign_empty_assigned_to_rejected(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).post(
                "/tasks/TASK-AABBCCDD/reassign",
                json={"assigned_to": ""},
            )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 15. Priority enum validation at schema level
# ---------------------------------------------------------------------------


class TestPriorityValidation:
    def _create_app(self):
        app = FastAPI()
        app.include_router(tasks_mod.router, prefix="/tasks")

        async def _user():
            return {"user_id": "u1", "active_store_id": "S1", "roles": ["STORE_MANAGER"]}

        app.dependency_overrides[get_current_user] = _user
        return app

    def test_invalid_priority_on_create_rejected(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        r = TestClient(self._create_app()).post(
            "/tasks",
            json={
                "title": "Bad Priority",
                "assigned_to": "u1",
                "due_at": future,
                "priority": "P9",  # invalid
            },
        )
        assert r.status_code == 422

    def test_valid_priorities_accepted(self):
        for p in ["P0", "P1", "P2", "P3", "P4"]:
            repo = _FakeRepo([_task(status="OPEN", task_id="TASK-PTEST001")])
            with patch.object(tasks_mod, "get_task_repository", return_value=repo):
                r = TestClient(_APP).patch(
                    "/tasks/TASK-PTEST001", json={"priority": p}
                )
            assert r.status_code == 200, f"Expected 200 for priority {p}, got {r.status_code}"

    def test_invalid_priority_on_update_rejected(self):
        repo = _FakeRepo([_task(status="OPEN")])
        with patch.object(tasks_mod, "get_task_repository", return_value=repo):
            r = TestClient(_APP).patch(
                "/tasks/TASK-AABBCCDD", json={"priority": "HIGH"}  # not P0-P4
            )
        assert r.status_code == 422
