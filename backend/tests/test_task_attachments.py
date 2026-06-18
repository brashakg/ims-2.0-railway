"""
Task attachments (owner item #5 — file-sharing via tasks)
=========================================================
Covers the attach-file-to-task feature:

  1. Create a task with a valid attachment_file_id -> persisted on the doc
  2. Create with a forged/missing attachment_file_id -> 400 (not a later 404)
  3. Upload endpoint validates MIME + size, returns a usable file_id
  4. Download streams the attached file for a caller who can see the task
  5. Download requires task access (cross-store store-scoped caller -> 403)
  6. Download a task with no attachment -> 404
  7. PATCH attaches a file to an existing task; empty string clears it

The router calls get_task_repository() / get_file_store() directly (plain
functions, not Depends), so we patch them at module level. The file store is
swapped for the in-memory test implementation via set_file_store().
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRepo:
    """Minimal in-memory task repo."""

    def __init__(self, tasks: Optional[List[Dict[str, Any]]] = None) -> None:
        self._tasks: Dict[str, Dict[str, Any]] = {
            t["task_id"]: dict(t) for t in (tasks or [])
        }

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
            return True
        return False


def _task(**kwargs) -> Dict[str, Any]:
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


def _make_app(roles=None, active_store="S1", store_ids=None) -> FastAPI:
    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/tasks")

    async def _user():
        u: Dict[str, Any] = {
            "user_id": "u-test",
            "active_store_id": active_store,
            "roles": roles if roles is not None else ["STORE_MANAGER"],
        }
        if store_ids is not None:
            u["store_ids"] = store_ids
        return u

    app.dependency_overrides[get_current_user] = _user
    return app


@pytest.fixture
def fs():
    """A fresh in-memory file store wired in for the duration of a test."""
    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


def _future_due() -> str:
    return (datetime.now() + timedelta(hours=2)).isoformat()


# ---------------------------------------------------------------------------
# 1. Create with a valid attachment -> persisted
# ---------------------------------------------------------------------------


def test_create_task_with_attachment_persists(fs):
    file_id = fs.put(content=b"hello", filename="note.pdf", mime_type="application/pdf")
    repo = _FakeRepo()
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).post(
            "/tasks",
            json={
                "title": "Share this file",
                "assigned_to": "user-1",
                "due_date": _future_due(),
                "attachment_file_id": file_id,
                "attachment_filename": "note.pdf",
                "attachment_mime": "application/pdf",
            },
        )
    assert r.status_code == 201, r.text
    created = next(iter(repo._tasks.values()))
    assert created["attachment"]["file_id"] == file_id
    assert created["attachment"]["filename"] == "note.pdf"


# ---------------------------------------------------------------------------
# 2. Create with a forged/missing attachment_file_id -> 400
# ---------------------------------------------------------------------------


def test_create_task_with_forged_attachment_rejected(fs):
    repo = _FakeRepo()
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).post(
            "/tasks",
            json={
                "title": "Share this file",
                "assigned_to": "user-1",
                "due_date": _future_due(),
                "attachment_file_id": "does-not-exist",
            },
        )
    assert r.status_code == 400
    assert "attachment_file_id" in r.json()["detail"]
    # Nothing was persisted.
    assert len(repo._tasks) == 0


def test_create_task_without_attachment_still_works(fs):
    repo = _FakeRepo()
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).post(
            "/tasks",
            json={
                "title": "No file task",
                "assigned_to": "user-1",
                "due_date": _future_due(),
            },
        )
    assert r.status_code == 201
    created = next(iter(repo._tasks.values()))
    assert created["attachment"] is None


# ---------------------------------------------------------------------------
# 3. Upload endpoint
# ---------------------------------------------------------------------------


def test_upload_file_returns_file_id(fs):
    app = _make_app()
    r = TestClient(app).post(
        "/tasks/upload-file",
        files={"file": ("note.pdf", b"%PDF-1.4 hello", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_id"]
    assert body["persisted"] is True
    # The bytes are actually retrievable from the store.
    assert fs.get(body["file_id"]) is not None


def test_upload_file_rejects_bad_mime(fs):
    app = _make_app()
    r = TestClient(app).post(
        "/tasks/upload-file",
        files={"file": ("evil.exe", b"MZ...", "application/x-msdownload")},
    )
    assert r.status_code == 400


def test_upload_file_storage_down_returns_503():
    set_file_store(None)
    app = _make_app()
    with patch.object(tasks_mod, "get_file_store", return_value=None):
        r = TestClient(app).post(
            "/tasks/upload-file",
            files={"file": ("note.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# 4. Download — caller who can see the task gets the file
# ---------------------------------------------------------------------------


def test_download_attachment_for_task_owner(fs):
    file_id = fs.put(content=b"abc123", filename="x.png", mime_type="image/png")
    repo = _FakeRepo(
        [_task(attachment={"file_id": file_id, "filename": "x.png", "mime_type": "image/png"})]
    )
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).get("/tasks/TASK-AABBCCDD/file")
    assert r.status_code == 200, r.text
    assert r.content == b"abc123"
    assert "image/png" in r.headers["content-type"]


# ---------------------------------------------------------------------------
# 5. Download requires task access (cross-store store-scoped caller -> 403)
# ---------------------------------------------------------------------------


def test_download_cross_store_denied(fs):
    file_id = fs.put(content=b"secret", filename="x.pdf", mime_type="application/pdf")
    repo = _FakeRepo(
        [_task(store_id="S2", attachment={"file_id": file_id, "filename": "x.pdf"})]
    )
    # A store-scoped caller whose reach is only S1 must not fetch an S2 task file.
    app = _make_app(roles=["SALES_STAFF"], active_store="S1", store_ids=["S1"])
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).get("/tasks/TASK-AABBCCDD/file")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 6. Download a task with no attachment -> 404
# ---------------------------------------------------------------------------


def test_download_no_attachment_404(fs):
    repo = _FakeRepo([_task()])  # no attachment
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).get("/tasks/TASK-AABBCCDD/file")
    assert r.status_code == 404


def test_download_missing_task_404(fs):
    repo = _FakeRepo([])
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).get("/tasks/TASK-NONE/file")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7. PATCH attaches a file to an existing task; empty string clears it
# ---------------------------------------------------------------------------


def test_patch_attach_file_to_existing_task(fs):
    file_id = fs.put(content=b"later", filename="late.pdf", mime_type="application/pdf")
    repo = _FakeRepo([_task()])
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).patch(
            "/tasks/TASK-AABBCCDD",
            json={"attachment_file_id": file_id, "attachment_filename": "late.pdf"},
        )
    assert r.status_code == 200, r.text
    assert repo._tasks["TASK-AABBCCDD"]["attachment"]["file_id"] == file_id


def test_patch_forged_attachment_rejected(fs):
    repo = _FakeRepo([_task()])
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).patch(
            "/tasks/TASK-AABBCCDD",
            json={"attachment_file_id": "nope"},
        )
    assert r.status_code == 400


def test_patch_clear_attachment_with_empty_string(fs):
    file_id = fs.put(content=b"x", filename="x.pdf", mime_type="application/pdf")
    repo = _FakeRepo(
        [_task(attachment={"file_id": file_id, "filename": "x.pdf"})]
    )
    app = _make_app()
    with patch.object(tasks_mod, "get_task_repository", return_value=repo):
        r = TestClient(app).patch(
            "/tasks/TASK-AABBCCDD",
            json={"attachment_file_id": ""},
        )
    assert r.status_code == 200, r.text
    assert repo._tasks["TASK-AABBCCDD"]["attachment"] is None
