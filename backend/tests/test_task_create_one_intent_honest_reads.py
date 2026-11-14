"""PR #1431 round 2 (Codex round-1 findings, decision D49).

R44  One request key gives ONE task, also when two submits overlap exactly
     (the second lookup runs before the first insert) and when the earlier
     task cannot be read back; the same key with CHANGED content is refused
     with 409 naming the saved task, never acknowledged as the old task.
R26  A task-list / my-tasks / overdue / detail / checklist read that fails
     is a 503, never an empty list or "nothing ticked".
R27  An unreadable staff record is a 503, never "pick an active colleague".
D49  An offset-aware deadline hours ahead is not refused as "past" because
     the host clock runs on another zone.

Real repositories over mongomock; a driver error is injected with pymongo's
own AutoReconnect at the collection, under the repository (where
BaseRepository used to swallow it).
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect

from api.routers import tasks
from api.routers.auth import get_current_user
from database.repositories.task_repository import TaskRepository
from database.repositories.user_repository import UserRepository


MANAGER = {"user_id": "manager", "full_name": "Meera Manager", "roles": ["STORE_MANAGER"],
           "store_ids": ["S1"], "active_store_id": "S1"}
USERS = [
    {"user_id": "manager", "roles": ["STORE_MANAGER"], "store_ids": ["S1"]},
    {"user_id": "same-shop", "roles": ["SALES_STAFF"], "store_ids": ["S1"]},
]
DUE = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()


class Flaky:
    """A real (mongomock) collection whose chosen reads raise AutoReconnect."""

    def __init__(self, inner, *failing):
        self._inner = inner
        self.failing = set(failing)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _maybe_fail(self, op):
        if op in self.failing:
            raise AutoReconnect("primary unreachable (test)")

    def find_one(self, *a, **k):
        self._maybe_fail("find_one")
        return self._inner.find_one(*a, **k)

    def find(self, *a, **k):
        self._maybe_fail("find")
        return self._inner.find(*a, **k)

    def count_documents(self, *a, **k):
        self._maybe_fail("count_documents")
        return self._inner.count_documents(*a, **k)


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().db
    db.users.insert_many([{"is_active": True, **deepcopy(u)} for u in USERS])
    state = {"tasks": Flaky(db.tasks), "users": Flaky(db.users), "completions": Flaky(db.sop_completions)}
    monkeypatch.setattr(tasks, "get_task_repository", lambda: TaskRepository(state["tasks"]))
    monkeypatch.setattr(tasks, "get_user_repository", lambda: UserRepository(state["users"]))
    monkeypatch.setattr(tasks, "_sop_collection", lambda: db.sop_templates)
    monkeypatch.setattr(tasks, "_sop_completions_collection", lambda: state["completions"])
    monkeypatch.setattr(tasks, "get_db", lambda: None)
    app = FastAPI()
    app.include_router(tasks.router, prefix="/tasks")
    app.dependency_overrides[get_current_user] = lambda: dict(MANAGER)
    with TestClient(app) as client:
        yield client, db, state


def _body(key, title="Count display pieces", **extra):
    return {"title": title, "priority": "P3", "assigned_to": "same-shop", "due_date": DUE,
            "type": "manual", "client_request_id": key, **extra}


def _rows(db, key):
    return list(db.tasks.find({"client_request_id": key}))


# --------------------------------------------------------------------- R44 --

def test_changed_content_under_the_same_key_is_refused_naming_the_saved_task(world):
    client, db, _ = world
    first = client.post("/tasks", json=_body("k-edit", "Original title"))
    assert first.status_code == 201, first.text
    saved_before = db.tasks.find_one({"task_id": first.json()["task_id"]})
    changed = client.post("/tasks", json=_body("k-edit", "Changed after lost reply"))
    assert changed.status_code == 409, changed.text
    detail = changed.json()["detail"]
    assert first.json()["task_id"] in detail and "Original title" in detail and "NOT saved" in detail
    assert len(_rows(db, "k-edit")) == 1
    assert db.tasks.find_one({"task_id": first.json()["task_id"]}) == saved_before
    # The unchanged retry still returns the saved task.
    again = client.post("/tasks", json=_body("k-edit", "Original title"))
    assert again.status_code == 201 and again.json() == {
        "task_id": first.json()["task_id"], "message": "Task already created", "replayed": True}


@pytest.mark.parametrize("field,value", [("assigned_to", "manager"), ("priority", "P1"),
                                         ("description", "now with a note"),
                                         ("due_date", (datetime.now(timezone.utc) + timedelta(days=5)).isoformat())])
def test_every_material_change_is_a_conflict(world, field, value):
    client, db, _ = world
    assert client.post("/tasks", json=_body("k-field")).status_code == 201
    assert client.post("/tasks", json=_body("k-field", **{field: value})).status_code == 409
    assert len(_rows(db, "k-field")) == 1


def test_overlapping_submits_with_one_key_make_one_task(world, monkeypatch):
    """The second submit's lookup runs BEFORE the first one's insert (exactly
    what Codex's barrier forced on native Mongo): both see no task. The
    insert must still land once, and the loser must answer with the winner."""
    client, db, _ = world
    real_find_one = TaskRepository.find_one
    raced = {"done": False}

    def lookup_then_competitor_inserts(self, flt, **kw):
        found = real_find_one(self, flt, **kw)
        if not raced["done"] and found is None:
            raced["done"] = True
            # The overlapping request completes its whole create right here.
            winner = TestClient(client.app).post("/tasks", json=_body("k-race", "Overlap"))
            assert winner.status_code == 201, winner.text
            raced["winner"] = winner.json()["task_id"]
        return found

    monkeypatch.setattr(TaskRepository, "find_one", lookup_then_competitor_inserts)
    loser = client.post("/tasks", json=_body("k-race", "Overlap"))
    assert loser.status_code == 201, loser.text
    assert loser.json()["task_id"] == raced["winner"] and loser.json()["replayed"] is True
    assert len(_rows(db, "k-race")) == 1


def test_overlap_with_changed_content_is_a_conflict_not_a_second_task(world, monkeypatch):
    client, db, _ = world
    real_find_one = TaskRepository.find_one
    raced = {"done": False}

    def lookup_then_competitor_inserts(self, flt, **kw):
        found = real_find_one(self, flt, **kw)
        if not raced["done"] and found is None:
            raced["done"] = True
            assert TestClient(client.app).post("/tasks", json=_body("k-race2", "First form")).status_code == 201
        return found

    monkeypatch.setattr(TaskRepository, "find_one", lookup_then_competitor_inserts)
    assert client.post("/tasks", json=_body("k-race2", "Edited form")).status_code == 409
    assert [r["title"] for r in _rows(db, "k-race2")] == ["First form"]


def test_an_unreadable_earlier_create_is_503_never_a_second_task(world):
    client, db, state = world
    assert client.post("/tasks", json=_body("k-seq")).status_code == 201
    state["tasks"].failing.add("find_one")
    retry = client.post("/tasks", json=_body("k-seq"))
    assert retry.status_code == 503, retry.text
    assert len(_rows(db, "k-seq")) == 1
    state["tasks"].failing.clear()
    again = client.post("/tasks", json=_body("k-seq"))
    assert again.status_code == 201 and again.json()["replayed"] is True
    assert len(_rows(db, "k-seq")) == 1


def test_one_key_belongs_to_one_person(world):
    client, db, _ = world
    mine = client.post("/tasks", json=_body("shared-key")).json()["task_id"]
    client.app.dependency_overrides[get_current_user] = lambda: {**MANAGER, "user_id": "same-shop",
                                                                 "roles": ["STORE_MANAGER"]}
    theirs = client.post("/tasks", json=_body("shared-key", "Their own task"))
    assert theirs.status_code == 201 and theirs.json()["task_id"] != mine
    assert theirs.json().get("replayed") is None


# --------------------------------------------------------------------- R27 --

def test_an_unreadable_assignee_is_503_not_an_invalid_colleague(world):
    client, db, state = world
    state["users"].failing.add("find_one")
    response = client.post("/tasks", json=_body(None))
    assert response.status_code == 503, response.text
    assert "Staff records" in response.json()["detail"]
    assert db.tasks.count_documents({}) == 0
    state["users"].failing.clear()
    assert client.post("/tasks", json=_body(None)).status_code == 201


# --------------------------------------------------------------------- R26 --

@pytest.mark.parametrize("path,op", [
    ("/tasks?store_id=S1", "find"), ("/tasks?store_id=S1", "count_documents"),
    ("/tasks/my-tasks", "find"), ("/tasks/overdue?store_id=S1", "find"),
])
def test_task_list_read_outage_is_503_not_an_empty_list(world, path, op):
    client, db, state = world
    assert client.post("/tasks", json=_body("k-list")).status_code == 201
    assert client.get(path).status_code == 200
    state["tasks"].failing.add(op)
    response = client.get(path)
    assert response.status_code == 503, response.text
    state["tasks"].failing.clear()
    assert client.get(path).status_code == 200


def test_task_summary_count_outage_is_503_not_zeros(world):
    client, db, state = world
    assert client.post("/tasks", json=_body("k-sum")).status_code == 201
    assert client.get("/tasks/summary?store_id=S1").json()["total"] == 1
    state["tasks"].failing.add("count_documents")
    assert client.get("/tasks/summary?store_id=S1").status_code == 503


def test_task_detail_read_outage_is_503_not_not_found(world):
    client, db, state = world
    task_id = client.post("/tasks", json=_body("k-detail")).json()["task_id"]
    state["tasks"].failing.add("find_one")
    assert client.get(f"/tasks/{task_id}").status_code == 503


def test_checklist_completion_read_outage_is_503_not_nothing_ticked(world):
    client, db, state = world
    db.sop_templates.insert_one({"template_id": "TPL-1", "title": "Open the shop", "is_active": True,
                                 "frequency": "DAILY", "store_id": "S1", "assigned_roles": ["STORE_MANAGER"],
                                 "steps": [{"step_number": 1, "instruction": "Unlock"}]})
    ticked = client.post("/tasks/sop-checklist/item", json={"template_id": "TPL-1", "step_number": 1,
                                                             "completed": True, "store_id": "S1"})
    assert ticked.status_code == 200, ticked.text
    url = "/tasks/sop-checklist?template_id=TPL-1&store_id=S1"
    assert client.get(url).json()["progress"]["done"] == 1
    state["completions"].failing.add("find_one")
    outage = client.get(url)
    assert outage.status_code == 503, outage.text
    assert db.sop_completions.find_one({"template_id": "TPL-1"})["status"] == "COMPLETED"
    state["completions"].failing.clear()
    assert client.get(url).json()["progress"]["done"] == 1


# --------------------------------------------------------------------- D49 --

def test_an_offset_aware_deadline_hours_ahead_is_not_past_on_any_host_clock(world):
    """5 hours ahead, written at UTC-12:00. Dropping the offset and comparing
    with the host clock (the old rule) reads it as ~7 hours AGO on a UTC or
    India-time host; it is 5 hours in the future."""
    client, db, _ = world
    due = (datetime.now(timezone.utc) + timedelta(hours=5)).astimezone(timezone(timedelta(hours=-12)))
    response = client.post("/tasks", json=_body(None, due_date=due.isoformat()))
    assert response.status_code == 201, response.text
    # A deadline really in the past is still refused.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).astimezone(timezone(timedelta(hours=14)))
    assert client.post("/tasks", json=_body(None, due_date=past.isoformat())).status_code == 422
