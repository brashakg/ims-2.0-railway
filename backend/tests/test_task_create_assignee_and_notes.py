"""R27 + R05 (audit 2026-11-04).

R27: POST /tasks saved a task for a person who does not exist, while the
reassignment doors refuse exactly that person. Creation must apply the same
named-recipient rule (tasks._named_target) before writing anything, and keep
the deliberately supported role-addressed task ("cashier" -> CASHIER).

R05: "Add note" (PATCH notes) replaced the previous note, so the first note
was lost. Every note is kept, attributed and timestamped, in order.

Real repositories over mongomock; the router's repositories use only the
in-memory database.
"""

from copy import deepcopy
from datetime import datetime, timedelta

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import tasks
from api.routers.auth import get_current_user
from database.repositories.task_repository import TaskRepository
from database.repositories.user_repository import UserRepository


MANAGER = {"user_id": "manager", "full_name": "Meera Manager", "roles": ["STORE_MANAGER"],
           "store_ids": ["S1"], "active_store_id": "S1"}
USERS = [
    {"user_id": "manager", "roles": ["STORE_MANAGER"], "store_ids": ["S1"]},
    {"user_id": "same-shop", "roles": ["SALES_STAFF"], "store_ids": ["S1"]},
    {"user_id": "other-shop", "roles": ["SALES_STAFF"], "store_ids": ["S2"]},
    {"user_id": "inactive", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "is_active": False},
    {"user_id": "admin", "roles": ["ADMIN"], "store_ids": []},
]


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().db
    db.users.insert_many([{"is_active": True, **deepcopy(u)} for u in USERS])
    monkeypatch.setattr(tasks, "get_task_repository", lambda: TaskRepository(db.tasks))
    monkeypatch.setattr(tasks, "get_user_repository", lambda: UserRepository(db.users))
    monkeypatch.setattr(tasks, "get_db", lambda: None)
    app = FastAPI()
    app.include_router(tasks.router, prefix="/tasks")
    actor = dict(MANAGER)
    app.dependency_overrides[get_current_user] = lambda: actor
    with TestClient(app) as client:
        yield client, db, actor


def _create(client, assigned_to):
    due = (datetime.now() + timedelta(hours=3)).isoformat()
    return client.post("/tasks", json={
        "title": "Count display pieces", "priority": "P3",
        "assigned_to": assigned_to, "due_date": due, "type": "manual",
    })


@pytest.mark.parametrize("target", ["audit-no-such-user-identity-proof", "inactive", "other-shop"])
def test_create_refuses_a_recipient_reassignment_refuses_and_writes_nothing(world, target):
    client, db, _ = world
    response = _create(client, target)
    assert response.status_code == 400, response.text
    assert "active colleague" in response.json()["detail"]
    assert db.tasks.count_documents({}) == 0


@pytest.mark.parametrize("target", ["same-shop", "manager"])
def test_create_accepts_a_colleague_at_the_shop(world, target):
    client, db, _ = world
    response = _create(client, target)
    assert response.status_code == 201, response.text
    saved = db.tasks.find_one({"task_id": response.json()["task_id"]})
    assert saved["assigned_to"] == target and saved["store_id"] == "S1"


def test_create_keeps_role_addressed_tasks(world):
    client, db, _ = world
    response = _create(client, "cashier")
    assert response.status_code == 201, response.text
    assert db.tasks.find_one({})["assigned_to"] == "CASHIER"


def test_create_refuses_when_staff_records_are_unavailable(world, monkeypatch):
    client, db, _ = world
    monkeypatch.setattr(tasks, "get_user_repository", lambda: None)
    assert _create(client, "same-shop").status_code == 503
    assert db.tasks.count_documents({}) == 0


def _seed(db, **extra):
    db.tasks.insert_one({
        "task_id": "T1", "title": "Count", "status": "OPEN", "store_id": "S1",
        "assigned_to": "same-shop", "priority": "P2", "history": [], **extra,
    })


def test_second_note_keeps_the_first(world):
    client, db, actor = world
    _seed(db)
    first = "First count: display one has 12 test units."
    second = "Second count: display two has 8 test units."
    assert client.patch("/tasks/T1", json={"notes": first}).status_code == 200
    actor.update(user_id="same-shop", full_name="Sam Staff", roles=["SALES_STAFF"])
    assert client.patch("/tasks/T1", json={"notes": second}).status_code == 200
    saved = db.tasks.find_one({"task_id": "T1"})
    assert [n["text"] for n in saved["notes_log"]] == [first, second]
    assert [n["by"] for n in saved["notes_log"]] == ["manager", "same-shop"]
    assert saved["notes_log"][0]["by_name"] == "Meera Manager"
    assert all(isinstance(n["at"], datetime) for n in saved["notes_log"])
    # The scalar still carries the latest note for older readers.
    assert saved["notes"] == second
    # The list door returns the trail the detail panel renders.
    listed = client.get("/tasks").json()["tasks"][0]
    assert [n["text"] for n in listed["notes_log"]] == [first, second]


def test_a_legacy_single_note_is_kept_when_the_next_note_arrives(world):
    client, db, _ = world
    _seed(db, notes="Old note written before the trail existed")
    assert client.patch("/tasks/T1", json={"notes": "New note"}).status_code == 200
    saved = db.tasks.find_one({"task_id": "T1"})
    assert [n["text"] for n in saved["notes_log"]] == [
        "Old note written before the trail existed", "New note"]
    assert saved["notes_log"][0].get("legacy") is True


def test_a_note_on_a_closed_task_is_refused_and_writes_nothing(world):
    client, db, _ = world
    _seed(db, status="COMPLETED")
    before = db.tasks.find_one({"task_id": "T1"})
    assert client.patch("/tasks/T1", json={"notes": "late"}).status_code == 400
    assert db.tasks.find_one({"task_id": "T1"}) == before


# R44: one create intent -> one task, also when the reply is lost and the
# screen sends the same request again.
# A retry resends the SAME body (the screen keeps the deadline it computed).
KEYED_DUE = (datetime.now() + timedelta(hours=3)).isoformat()


def _create_keyed(client, key, title="Count display pieces"):
    return client.post("/tasks", json={
        "title": title, "priority": "P3", "assigned_to": "same-shop",
        "due_date": KEYED_DUE, "type": "manual", "client_request_id": key,
    })


def test_a_repeated_create_with_the_same_key_returns_the_first_task(world):
    client, db, _ = world
    first = _create_keyed(client, "k-1")
    again = _create_keyed(client, "k-1")
    assert first.status_code == again.status_code == 201
    assert again.json()["task_id"] == first.json()["task_id"]
    assert again.json().get("replayed") is True
    assert db.tasks.count_documents({}) == 1


def test_separate_tasks_with_identical_titles_are_still_allowed(world):
    client, db, actor = world
    assert _create_keyed(client, "k-1").status_code == 201
    assert _create_keyed(client, "k-2").status_code == 201
    assert _create_keyed(client, None).status_code == 201
    assert _create_keyed(client, None).status_code == 201
    # Another person's key never returns this person's task.
    actor.update(user_id="admin", roles=["ADMIN"])
    other = _create_keyed(client, "k-1")
    assert other.status_code == 201 and other.json().get("replayed") is None
    assert db.tasks.count_documents({}) == 5


# R45: an integrity read that fails is a 503, never "0 suspect tasks".
class _BrokenCollection:
    def find(self, *_a, **_kw):
        raise RuntimeError("primary unreachable")


@pytest.mark.parametrize("path", ["/tasks/integrity/silent", "/tasks/integrity/fake-closures"])
def test_integrity_read_failure_is_not_a_zero(world, monkeypatch, path):
    client, db, _ = world
    _seed(db)
    assert client.get(path).status_code == 200
    broken = TaskRepository(db.tasks)
    broken.collection = _BrokenCollection()
    monkeypatch.setattr(tasks, "get_task_repository", lambda: broken)
    response = client.get(path)
    assert response.status_code == 503, response.text
    monkeypatch.setattr(tasks, "get_task_repository", lambda: None)
    assert client.get(path).status_code == 503
