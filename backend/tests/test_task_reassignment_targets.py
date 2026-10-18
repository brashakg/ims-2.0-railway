"""A named task recipient must exist, be active, and reach the task's shop.

Exercise all three reassignment HTTP doors with real repositories over
mongomock: a foreign recipient otherwise gains visibility merely by being
persisted as assigned_to. Router repositories use only the in-memory database.
"""

from copy import deepcopy

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import tasks
from api.routers.auth import get_current_user
from database.repositories.task_repository import TaskRepository
from database.repositories.user_repository import UserRepository


DOORS = [("PATCH", ""), ("PUT", ""), ("POST", "/reassign")]
MANAGER = {"user_id": "manager", "roles": ["STORE_MANAGER"], "store_ids": ["S1"]}
USERS = [
    {"user_id": "same-shop", "roles": ["SALES_STAFF"], "store_ids": ["S1"]},
    {"user_id": "other-shop", "roles": ["SALES_STAFF"], "store_ids": ["S2"]},
    {"user_id": "inactive", "roles": ["SALES_STAFF"], "store_ids": ["S1"], "is_active": False},
    {"user_id": "area", "roles": ["AREA_MANAGER"], "store_ids": ["S1", "S2"]},
    {"user_id": "admin", "roles": ["ADMIN"], "store_ids": []},
    {"user_id": "superadmin", "roles": ["SUPERADMIN"], "store_ids": []},
    {"user_id": "no-shop", "roles": ["SALES_STAFF"], "store_ids": []},
]


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().db
    db.tasks.insert_one({
        "task_id": "T1", "title": "Count this shop's stock", "status": "OPEN",
        "store_id": "S1", "assigned_to": "original", "priority": "P2",
        "history": [{"action": "created", "by": "manager"}],
    })
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


def request_assignment(client, door, target):
    method, tail = door
    # A rejected reassignment must also reject other fields in that request.
    payload = {"assigned_to": target}
    if method != "POST":
        payload.update(title="Changed title", notes="Changed notes")
    else:
        payload["reason"] = "Cover the morning shift"
    return client.request(method, f"/tasks/T1{tail}", json=payload)


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("target", ["missing", "inactive", "other-shop"])
def test_reassignment_rejects_invalid_recipient_without_any_write(world, door, target):
    client, db, _ = world
    before = db.tasks.find_one({"task_id": "T1"})
    response = request_assignment(client, door, target)
    assert response.status_code == 400, response.text
    assert "active colleague" in response.json()["detail"]
    assert db.tasks.find_one({"task_id": "T1"}) == before


@pytest.mark.parametrize("door", DOORS)
def test_foreign_recipient_never_gains_visibility_after_rejected_assignment(world, door):
    client, db, actor = world
    response = request_assignment(client, door, "other-shop")
    assert response.status_code == 400, response.text
    actor.clear()
    actor.update(db.users.find_one({"user_id": "other-shop"}))
    assert client.get("/tasks/T1").status_code == 404


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("target", ["same-shop", "area", "admin", "superadmin"])
def test_reassignment_accepts_colleagues_with_existing_shop_or_hq_reach(world, door, target):
    client, db, _ = world
    response = request_assignment(client, door, target)
    assert response.status_code == 200, response.text
    saved = db.tasks.find_one({"task_id": "T1"})
    assert saved["assigned_to"] == target
    assert saved["store_id"] == "S1"
    if door[0] == "POST":
        assert saved["history"][-1]["action"] == "reassigned"
        assert saved["history"][-1]["from"] == "original"
        assert saved["history"][-1]["to"] == target
        assert saved["history"][-1]["by"] == "manager"
        assert saved["history"][-1]["reason"] == "Cover the morning shift"
    else:
        assert saved["title"] == "Changed title"
        assert saved["notes"] == "Changed notes"


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("target", ["admin", "superadmin", "same-shop"])
def test_unattributed_task_keeps_existing_hq_recipient_rule(world, door, target):
    client, db, actor = world
    actor.clear()
    actor.update(db.users.find_one({"user_id": "admin"}))
    db.tasks.update_one({"task_id": "T1"}, {"$set": {"store_id": None}})
    before = db.tasks.find_one({"task_id": "T1"})
    response = request_assignment(client, door, target)
    if target == "same-shop":
        assert response.status_code == 400, response.text
        assert db.tasks.find_one({"task_id": "T1"}) == before
    else:
        assert response.status_code == 200, response.text
        assert db.tasks.find_one({"task_id": "T1"})["assigned_to"] == target


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("target", ["no-shop", "SALES_STAFF"])
def test_reassignment_requires_a_person_with_shop_reach(world, door, target):
    client, db, _ = world
    before = db.tasks.find_one({"task_id": "T1"})
    response = request_assignment(client, door, target)
    assert response.status_code == 400, response.text
    assert db.tasks.find_one({"task_id": "T1"}) == before


@pytest.mark.parametrize("door", DOORS)
def test_recipient_records_unavailable_refuses_assignment(world, door, monkeypatch):
    client, db, _ = world
    monkeypatch.setattr(tasks, "get_user_repository", lambda: None)
    before = db.tasks.find_one({"task_id": "T1"})
    response = request_assignment(client, door, "same-shop")
    assert response.status_code == 503, response.text
    assert db.tasks.find_one({"task_id": "T1"}) == before


@pytest.mark.parametrize("door", DOORS)
def test_a_staff_member_still_cannot_reassign_their_own_task(world, door):
    client, db, actor = world
    actor.clear()
    actor.update(db.users.find_one({"user_id": "same-shop"}))
    db.tasks.update_one({"task_id": "T1"}, {"$set": {"assigned_to": "same-shop"}})
    before = db.tasks.find_one({"task_id": "T1"})
    response = request_assignment(client, door, "admin")
    assert response.status_code == 403, response.text
    assert db.tasks.find_one({"task_id": "T1"}) == before


@pytest.mark.parametrize("door", DOORS)
def test_a_manager_cannot_reassign_an_invisible_task(world, door):
    client, db, _ = world
    db.tasks.update_one({"task_id": "T1"}, {"$set": {"store_id": "S2"}})
    before = db.tasks.find_one({"task_id": "T1"})
    response = request_assignment(client, door, "other-shop")
    assert response.status_code == 404, response.text
    assert db.tasks.find_one({"task_id": "T1"}) == before
