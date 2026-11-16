"""D81(1), #1393: the 'points not yet taken back' task is a head-office item.

An edit that lowers what a bill earns, after the customer spent those points,
puts a task on the Tasks board (edit_ops._points_task). Closing it by hand
waives points, so -- the same rule as owner ruling 15 (11-09) for #1397's
points exception -- it is routed to ADMIN/SUPERADMIN and only ADMIN/SUPERADMIN
may complete or cancel it. A store or area manager who reaches the shop still
sees it; the person holding it, if not head office, cannot close it either.
IMS itself still closes it once the claw lands.

Driven through the real task routes (test_task_visibility's world) and the real
TaskRepository / UserRepository over mongomock. No emoji (Windows cp1252).
"""
import mongomock
import pytest

from test_task_visibility import S1, _task, world  # noqa: F401  (fixture)

TID = "PTS-EXC"
REF = "order_points_unclawed:ORD-9"


def _points_task(db, holder="u-admin"):
    db.tasks.insert_one(_task(TID, holder, S1, source_ref=REF, category="Accounts"))
    return db.tasks.find_one({"task_id": TID})


CLOSERS = [
    ("patch", f"/api/v1/tasks/{TID}/complete", {"completion_notes": "looked at it"}),
    ("patch", f"/api/v1/tasks/{TID}", {"status": "CANCELLED"}),
    ("put", f"/api/v1/tasks/{TID}", {"status": "CANCELLED"}),
]


@pytest.mark.parametrize("role,holder", [
    ("STORE_MANAGER", "u-admin"),
    ("AREA_MANAGER", "u-admin"),
    ("STORE_MANAGER", "u-mgr"),  # the task-holder, raised before D81 or reassigned
])
def test_a_manager_or_holder_sees_it_but_cannot_close_it(world, role, holder):
    as_role, db = world
    before = _points_task(db, holder)
    client = as_role(role)
    assert client.get(f"/api/v1/tasks/{TID}").status_code == 200
    for verb, url, body in CLOSERS:
        r = getattr(client, verb)(url, json=body)
        assert r.status_code == 403, (verb, url, r.text)
        assert "admin" in r.json()["detail"].lower()
    assert db.tasks.find_one({"task_id": TID}) == before


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
@pytest.mark.parametrize("verb,url,body,status", [
    ("patch", f"/api/v1/tasks/{TID}/complete", {"completion_notes": "settled by hand"}, "COMPLETED"),
    ("patch", f"/api/v1/tasks/{TID}", {"status": "CANCELLED"}, "CANCELLED"),
])
def test_head_office_closes_it(world, role, verb, url, body, status):
    as_role, db = world
    _points_task(db)
    r = getattr(as_role(role), verb)(url, json=body)
    assert r.status_code == 200, r.text
    assert db.tasks.find_one({"task_id": TID})["status"] == status


def test_a_manager_still_closes_an_ordinary_task(world):
    as_role, db = world
    db.tasks.insert_one(_task("ORD-TASK", "u-mgr", S1, source_ref="order_stock_unsettled:ORD-9"))
    r = as_role("STORE_MANAGER").patch("/api/v1/tasks/ORD-TASK/complete", json={"completion_notes": "done"})
    assert r.status_code == 200, r.text


@pytest.fixture
def boards(monkeypatch):
    from api import dependencies as dm
    from database.repositories.task_repository import TaskRepository
    from database.repositories.user_repository import UserRepository

    db = mongomock.MongoClient().db
    tasks, users = TaskRepository(db.tasks), UserRepository(db.users)
    monkeypatch.setattr(dm, "get_task_repository", lambda: tasks)
    monkeypatch.setattr(dm, "get_user_repository", lambda: users)
    db.users.insert_many([
        {"user_id": "u-mgr", "roles": ["STORE_MANAGER"], "store_ids": [S1], "is_active": True},
        {"user_id": "u-super", "roles": ["SUPERADMIN"], "store_ids": [], "is_active": True},
    ])
    return db


ORDER = {"order_id": "ORD-9", "order_number": "BV-9", "store_id": S1, "customer_id": "C-1"}


@pytest.mark.parametrize("reason", ["balance_underflow", "claw_move_underflow"])
def test_the_task_is_routed_to_head_office(boards, reason):
    from api.routers.orders.edit_ops import _points_task

    boards.users.insert_one({"user_id": "u-admin", "roles": ["ADMIN"], "store_ids": [], "is_active": True})
    _points_task(dict(ORDER), {"ok": False, "reason": reason, "delta": 10})
    row = boards.tasks.find_one({"source_ref": REF})
    assert row["assigned_to"] == "u-admin" and row["store_id"] == S1 and row["status"] == "OPEN"
    # IMS still closes it itself once the claw lands.
    _points_task(dict(ORDER), {"ok": True, "clawed": 10})
    assert boards.tasks.find_one({"source_ref": REF})["status"] == "COMPLETED"


def test_with_no_admin_it_goes_to_a_superadmin(boards):
    from api.routers.orders.edit_ops import _points_task

    _points_task(dict(ORDER), {"ok": False, "reason": "balance_underflow", "delta": 10})
    assert boards.tasks.find_one({"source_ref": REF})["assigned_to"] == "u-super"
