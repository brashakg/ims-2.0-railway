"""Who sees a task - owner ruling 2026-10-12 (and 2026-09-03 'assign to PEOPLE').

  * a staff member sees -- and may act on -- a task only when it is assigned to
    them (their user id) or to their ROLE at their shop (no person on it yet);
  * whoever raised a task BY HAND (POST /tasks) also sees it and can follow it
    up; a system, SOP or review task never makes anyone its creator;
  * only a manager cancels or reassigns a task; staff start and complete;
  * STORE_MANAGER: every task of their shop(s); AREA_MANAGER: every task of
    their area's shops; ADMIN / SUPERADMIN: all.

Before this, every task stamped to a shop showed to every role at that shop,
and GET /tasks/overdue, GET /tasks/{id}, GET /tasks/summary and PATCH
/tasks/{id} did not even have the list's rules (#1171 R1-40/42/59/60). A
task you cannot see is 404 everywhere, never 403, so ids do not leak.

The world runs on mongomock behind the REAL TaskRepository, so every filter
the routes build is evaluated by a real query engine, not a hand-rolled
matcher.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import dashboard_widgets as widgets_mod  # noqa: E402
from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.reports import router as reports_router  # noqa: E402
from api.services.rbac_policy import policy_for  # noqa: E402
from api.services.file_store import InMemoryFileStore  # noqa: E402
from database.repositories.task_repository import TaskRepository  # noqa: E402

S1, S2, S3 = "BV-S1", "BV-S2", "BV-S3"
STAFF_ROLES = [
    "SALES_STAFF",
    "CASHIER",
    "OPTOMETRIST",
    "WORKSHOP_STAFF",
    "CATALOG_MANAGER",
    "ACCOUNTANT",
]
PAST = datetime.now() - timedelta(days=2)
FUTURE = datetime.now() + timedelta(days=2)


def uid(role: str) -> str:
    return f"u-{role.lower()}"


USERS = {
    **{r: {"user_id": uid(r), "roles": [r], "store_ids": [S1], "active_store_id": S1} for r in STAFF_ROLES},
    "STORE_MANAGER": {"user_id": "u-mgr", "roles": ["STORE_MANAGER"], "store_ids": [S1], "active_store_id": S1},
    "AREA_MANAGER": {"user_id": "u-area", "roles": ["AREA_MANAGER"], "store_ids": [S1, S2], "active_store_id": S1},
    "ADMIN": {"user_id": "u-admin", "roles": ["ADMIN"], "store_ids": [], "active_store_id": None},
    "SUPERADMIN": {"user_id": "u-super", "roles": ["SUPERADMIN"], "store_ids": [], "active_store_id": None},
}


def _task(task_id, assigned_to, store_id, status="OPEN", due=PAST, **extra):
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "status": status,
        "priority": "P2",
        "assigned_to": assigned_to,
        "assigned_by": "system",
        **extra,
        "store_id": store_id,
        "due_at": due,
        "created_at": datetime.now() - timedelta(days=3),
        "escalation_level": 0,
        "history": [],
    }


TASKS = (
    # One task per staff PERSON at S1 (open, overdue) ...
    [_task(f"P-{r}", uid(r), S1) for r in STAFF_ROLES]
    # ... and one addressed to each staff ROLE at S1 (in progress, not due).
    + [_task(f"R-{r}", r, S1, "IN_PROGRESS", FUTURE) for r in STAFF_ROLES]
    + [
        _task("P-PEER", "u-sales-peer", S1),  # another sales person, same shop
        _task("R-SALES-S2", "SALES_STAFF", S2),  # the sales ROLE, other shop
        _task("U-S1", None, S1, "ESCALATED"),  # nobody on it yet
        _task("P-S2", "u-x", S2, "COMPLETED"),
        _task("P-S3", "u-y", S3),
        _task("G", None, None),  # no shop at all
        _task("P-SALES-S3", uid("SALES_STAFF"), S3),  # yours, at another shop
        # Raised BY HAND by the sales person for someone else: POST /tasks
        # stamps assigned_by + created_via "manual". The creator follows it up.
        _task("C-SALES", "u-sales-peer", S1, assigned_by=uid("SALES_STAFF"), created_via="manual"),
        # NOT raised by hand: a button the sales person pressed (walk-out,
        # accountability, an SOP run) stamps them as assigned_by, and a review
        # task names them created_by. Neither makes them its creator.
        _task("C-SALES-AUTO", "u-sales-peer", S1, assigned_by=uid("SALES_STAFF")),
        _task("C-SALES-S2", "u-x", S2, assigned_by=None, created_by=uid("SALES_STAFF")),
        # A role name is matched as the WHOLE assigned_to, never a part of it:
        # CASHIER is inside SALES_CASHIER, and real user ids carry role names
        # (user-cashier-bok1, user-accountant, cashier-bok2). No staff role
        # here sees these.
        _task("R-SALES_CASHIER", "SALES_CASHIER", S1),
        _task("P-CASHIER-2", "user-cashier-bok1", S1),
        _task("P-ACCOUNTANT-2", "user-accountant", S1),
        _task("P-CASHIER-3", "cashier-bok2", S1),  # an id that STARTS with a role
    ]
)
ALL = {t["task_id"] for t in TASKS}
ASSIGNEES = sorted({t["assigned_to"] for t in TASKS if t["assigned_to"]})
# The colleague's task carries a real file, so a 404 on its /file route can
# only come from the visibility door, never from "No file attached".
PEER_FILE = b"the colleague's private scan"
S1_IDS = {t["task_id"] for t in TASKS if t["store_id"] == S1}
S2_IDS = {t["task_id"] for t in TASKS if t["store_id"] == S2}
OVERDUE = {t["task_id"] for t in TASKS if t["status"] in ("OPEN", "IN_PROGRESS") and t["due_at"] < datetime.now()}


# Every single-task route, with the write it makes. ONE list for both halves of
# the rule: on a colleague's task each route is 404 and writes nothing; on a
# task addressed to your role each one works. So a route that adds back its
# own role list, or skips the visibility door, goes red here.
TASK_ROUTES = {
    "open": ("get", "", {}, None),
    "file": ("get", "/file", {}, None),
    "patch notes": ("patch", "", {"json": {"notes": "on it"}}, lambda d: d.get("notes") == "on it"),
    "patch status": ("patch", "", {"json": {"status": "IN_PROGRESS"}}, lambda d: d["status"] == "IN_PROGRESS"),
    "put": ("put", "", {"json": {"notes": "on it"}}, lambda d: d.get("notes") == "on it"),
    "complete": ("patch", "/complete", {"json": {"completion_notes": "done it"}}, lambda d: d["status"] == "COMPLETED"),
    "acknowledge": ("post", "/acknowledge", {}, lambda d: d["status"] == "IN_PROGRESS"),
    "start": ("post", "/start", {}, lambda d: d["status"] == "IN_PROGRESS"),
    "escalate": ("post", "/escalate", {}, lambda d: d["status"] == "ESCALATED"),
}

# Cancel and reassign: a manager's call, never staff's (owner ruling
# 2026-10-13), even on the staff member's own task. Escalating TO a named
# person hands the task to them, so it is a reassignment too.
MANAGER_ROUTES = {
    "cancel": ("patch", "", {"json": {"status": "CANCELLED"}}, lambda d: d["status"] == "CANCELLED"),
    "cancel via put": ("put", "", {"json": {"status": "cancelled"}}, lambda d: d["status"] == "CANCELLED"),
    "patch assignee": ("patch", "", {"json": {"assigned_to": "u-mgr"}}, lambda d: d["assigned_to"] == "u-mgr"),
    "put assignee": ("put", "", {"json": {"assigned_to": "u-mgr"}}, lambda d: d["assigned_to"] == "u-mgr"),
    "reassign": ("post", "/reassign", {"json": {"assigned_to": "u-mgr"}}, lambda d: d["assigned_to"] == "u-mgr"),
    "escalate to": ("post", "/escalate", {"params": {"escalate_to": "u-mgr"}}, lambda d: d["assigned_to"] == "u-mgr"),
}
MANAGERS = ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"]


def expected(role: str) -> set:
    if role in STAFF_ROLES:
        mine = {f"P-{role}", f"R-{role}"}
        return mine | ({"P-SALES-S3", "C-SALES"} if role == "SALES_STAFF" else set())
    if role == "STORE_MANAGER":
        return S1_IDS
    if role == "AREA_MANAGER":
        return S1_IDS | S2_IDS
    return ALL


ROLES = STAFF_ROLES + ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"]


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().db
    db.tasks.insert_many([dict(t) for t in TASKS])
    files = InMemoryFileStore()
    fid = files.put(content=PEER_FILE, filename="scan.pdf", mime_type="application/pdf",
                    metadata={"kind": "task_attachment", "uploaded_by": "u-mgr", "store_id": S1})
    db.tasks.update_one({"task_id": "P-PEER"}, {"$set": {"attachment": {
        "file_id": fid, "filename": "scan.pdf", "mime_type": "application/pdf"}}})
    monkeypatch.setattr(tasks_mod, "get_file_store", lambda: files)
    repo = TaskRepository(db.tasks)
    monkeypatch.setattr(tasks_mod, "get_task_repository", lambda: repo)
    monkeypatch.setattr(tasks_mod, "get_user_repository", lambda: None)
    monkeypatch.setattr(tasks_mod, "get_db", lambda: None)
    for name in ("overview", "finance_ops"):
        mod = importlib.import_module(f"api.routers.reports.{name}")
        monkeypatch.setattr(mod, "get_task_repository", lambda: repo)
    overview = importlib.import_module("api.routers.reports.overview")
    for getter in ("get_order_repository", "get_stock_repository", "get_customer_repository"):
        monkeypatch.setattr(overview, getter, lambda: None)
    monkeypatch.setattr(widgets_mod, "_coll", lambda name: db[name])

    app = FastAPI()
    # main.py mounts the widgets router FIRST at bare /api/v1.
    app.include_router(widgets_mod.router, prefix="/api/v1")
    app.include_router(tasks_mod.router, prefix="/api/v1/tasks")
    app.include_router(reports_router, prefix="/api/v1/reports")
    who = {"user": None}

    async def _user():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _user
    client = TestClient(app)

    def as_role(role):
        who["user"] = USERS[role]
        return client

    return as_role, db


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return {t["task_id"] for t in resp.json()["tasks"]}


@pytest.mark.parametrize("role", ROLES)
def test_list_shows_only_what_the_role_may_see(world, role):
    as_role, _ = world
    r = as_role(role).get("/api/v1/tasks")
    assert _ids(r) == expected(role)
    assert r.json()["total"] == len(expected(role))


@pytest.mark.parametrize("role", ROLES)
def test_an_assignee_filter_never_widens_the_list(world, role):
    """?assigned_to= only narrows: a staff member asking for a colleague's
    tasks gets nothing, a manager gets that person's tasks at their shop."""
    as_role, _ = world
    c = as_role(role)
    for who in ASSIGNEES:
        r = c.get("/api/v1/tasks", params={"assigned_to": who})
        want = expected(role) & {t["task_id"] for t in TASKS if t["assigned_to"] == who}
        assert _ids(r) == want, who
        assert r.json()["total"] == len(want), who


@pytest.mark.parametrize("role", ROLES)
def test_overdue_shows_only_what_the_role_may_see(world, role):
    as_role, _ = world
    r = as_role(role).get("/api/v1/tasks/overdue")
    assert _ids(r) == expected(role) & OVERDUE
    assert r.json()["total"] == len(expected(role) & OVERDUE)


@pytest.mark.parametrize("role", ROLES)
def test_summary_counts_equal_the_lists(world, role):
    """THE rule for counts and lists: the default list is every task but the
    cancelled (PENDING kept -- the transfer, reminder and online-Rx producers
    write it), and the summary total counts exactly that list."""
    as_role, db = world
    c = as_role(role)
    me, shop = USERS[role]["user_id"], USERS[role]["active_store_id"] or S1
    db.tasks.insert_many([
        _task("X-CANC", me, shop, "CANCELLED"),
        _task("X-canc", me, shop, "cancelled"),
        _task("X-PEND", me, shop, "PENDING"),
        _task("X-pend", me, shop, "pending"),
    ])
    s = c.get("/api/v1/tasks/summary").json()
    for status in ("OPEN", "IN_PROGRESS", "COMPLETED", "ESCALATED"):
        listed = c.get("/api/v1/tasks", params={"status": status}).json()["total"]
        assert s["summary"][status] == listed, status
    everything = c.get("/api/v1/tasks").json()
    shown = {t["task_id"] for t in everything["tasks"]}
    assert {"X-PEND", "X-pend"} <= shown and not {"X-CANC", "X-canc"} & shown
    assert s["total"] == everything["total"] == len(expected(role)) + 2
    assert s["overdue"] == c.get("/api/v1/tasks/overdue").json()["total"]
    assert s["open"] == s["summary"]["OPEN"] + s["summary"]["IN_PROGRESS"]


@pytest.mark.parametrize("role", ROLES)
def test_shop_filter_and_its_counts_agree(world, role):
    """The Hub and the Tasks screens pass ?store_id=<active shop>."""
    as_role, _ = world
    c = as_role(role)
    listed = c.get("/api/v1/tasks", params={"store_id": S1})
    assert _ids(listed) == expected(role) & S1_IDS
    s = c.get("/api/v1/tasks/summary", params={"store_id": S1}).json()
    assert s["total"] == listed.json()["total"]
    open_s1 = c.get("/api/v1/tasks", params={"store_id": S1, "status": "OPEN"}).json()["total"]
    busy_s1 = c.get("/api/v1/tasks", params={"store_id": S1, "status": "IN_PROGRESS"}).json()["total"]
    assert s["open"] == open_s1 + busy_s1
    # The Hub's overdue card: the shop's overdue list, and its count, hold
    # only this shop (a sales person's own task at BV-S3 stays out).
    overdue = c.get("/api/v1/tasks/overdue", params={"store_id": S1})
    assert _ids(overdue) == expected(role) & OVERDUE & S1_IDS
    assert s["overdue"] == overdue.json()["total"]


@pytest.mark.parametrize("role", ROLES)
def test_the_hub_lists_every_task_its_open_count_counts(world, role):
    """The Hub's 'Open Tasks' figure and its 'Priority tasks' list are ONE
    request, GET /tasks?status=OPEN,IN_PROGRESS: a task the person has
    started stays listed, so the Hub never says 'Nothing urgent' beside
    'Open Tasks 1'. The summary's open count is the same set."""
    as_role, _ = world
    c = as_role(role)
    r = c.get("/api/v1/tasks", params={"store_id": S1, "status": "OPEN,IN_PROGRESS"})
    want = {t["task_id"] for t in TASKS if t["task_id"] in expected(role)
            and t["store_id"] == S1 and t["status"] in ("OPEN", "IN_PROGRESS")}
    assert _ids(r) == want and r.json()["total"] == len(want)
    assert f"R-{role}" in want or role not in STAFF_ROLES  # their started task
    assert c.get("/api/v1/tasks/summary", params={"store_id": S1}).json()["open"] == len(want)


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_the_creator_sees_and_follows_up_the_task_they_raised(world, role):
    """Owner question 1 (decided): a staff member who raises a task for a
    colleague keeps seeing it and can follow it up. A third person cannot."""
    as_role, db = world
    colleague = "CASHIER" if role != "CASHIER" else "SALES_STAFF"
    stranger = next(r for r in STAFF_ROLES if r not in (role, colleague))
    made = as_role(role).post("/api/v1/tasks", json={
        "title": "Ring Mrs Rao about her frame", "priority": "P2",
        "assigned_to": uid(colleague), "due_at": FUTURE.isoformat()})
    assert made.status_code == 201, made.text
    tid = made.json()["task_id"]
    assert tid in _ids(as_role(role).get("/api/v1/tasks"))
    assert as_role(role).get(f"/api/v1/tasks/{tid}").status_code == 200
    r = as_role(role).patch(f"/api/v1/tasks/{tid}", json={"notes": "chased her"})
    assert r.status_code == 200, r.text
    assert db.tasks.find_one({"task_id": tid}).get("notes") == "chased her"
    assert tid in _ids(as_role(colleague).get("/api/v1/tasks"))
    assert as_role(stranger).get(f"/api/v1/tasks/{tid}").status_code == 404


@pytest.mark.parametrize("role", ROLES)
def test_get_by_id_is_404_for_a_task_the_role_cannot_see(world, role):
    as_role, _ = world
    c = as_role(role)
    for tid in sorted(ALL):
        code = c.get(f"/api/v1/tasks/{tid}").status_code
        assert code == (200 if tid in expected(role) else 404), (tid, code)


@pytest.mark.parametrize("role", ROLES)
def test_patch_cannot_touch_a_task_the_role_cannot_see(world, role):
    as_role, db = world
    c = as_role(role)
    for tid in sorted(ALL):
        r = c.patch(f"/api/v1/tasks/{tid}", json={"status": "CANCELLED", "notes": "x"})
        doc = db.tasks.find_one({"task_id": tid})
        if tid in expected(role):
            assert r.status_code != 404, (tid, r.text)
        else:
            assert r.status_code == 404, (tid, r.status_code)
            assert doc["status"] != "CANCELLED" and "notes" not in doc, tid


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_no_task_route_acts_on_a_colleagues_task(world, role):
    """A person-assigned task of ANOTHER person at the same shop: every
    single-task route says 'not found' and nothing is written."""
    as_role, db = world
    c = as_role(role)
    other = "P-PEER"
    before = db.tasks.find_one({"task_id": other})
    for name, (method, tail, kw, _) in TASK_ROUTES.items():
        r = getattr(c, method)(f"/api/v1/tasks/{other}{tail}", **kw)
        assert r.status_code == 404, f"{name} -> {r.status_code}"
        assert PEER_FILE not in r.content, f"{name} leaked the file"
    assert db.tasks.find_one({"task_id": other}) == before
    # The file is really there: the shop's manager downloads it.
    r = as_role("STORE_MANAGER").get(f"/api/v1/tasks/{other}/file")
    assert r.status_code == 200 and r.content == PEER_FILE, r.status_code


@pytest.mark.parametrize("route", TASK_ROUTES)
def test_staff_can_work_a_task_addressed_to_their_role(world, route):
    """A role-assigned task with no person yet is theirs to do: seeing it is
    being able to act on it, on every single-task route."""
    as_role, db = world
    method, tail, kw, landed = TASK_ROUTES[route]
    peer_file = db.tasks.find_one({"task_id": "P-PEER"})["attachment"]
    for role in STAFF_ROLES:
        tid = f"R-{role}"
        # Not started yet, so start / acknowledge really write; carries a file.
        db.tasks.update_one({"task_id": tid}, {"$set": {"status": "OPEN", "attachment": peer_file}})
        r = getattr(as_role(role), method)(f"/api/v1/tasks/{tid}{tail}", **kw)
        assert r.status_code == 200, (role, r.status_code, r.text)
        if landed:
            assert landed(db.tasks.find_one({"task_id": tid})), role
        elif route == "file":
            assert r.content == PEER_FILE, role
        else:
            assert r.json()["task_id"] == tid, role


def test_manager_sees_and_works_a_staff_members_task(world):
    as_role, db = world
    c = as_role("STORE_MANAGER")
    assert c.get("/api/v1/tasks/P-SALES_STAFF").status_code == 200
    r = c.patch("/api/v1/tasks/P-SALES_STAFF/complete", json={"completion_notes": "closed out"})
    assert r.status_code == 200, r.text
    assert db.tasks.find_one({"task_id": "P-SALES_STAFF"})["status"] == "COMPLETED"


@pytest.mark.parametrize("role", ROLES)
def test_my_tasks_never_reaches_past_the_rule(world, role):
    as_role, _ = world
    got = _ids(as_role(role).get("/api/v1/tasks/my-tasks"))
    assert got <= expected(role)
    assert got == {t["task_id"] for t in TASKS if t["assigned_to"] == USERS[role]["user_id"]
                   and t["status"] in ("OPEN", "IN_PROGRESS", "ESCALATED")}


@pytest.mark.parametrize("role", ROLES)
def test_report_task_counts_count_only_what_the_role_may_see(world, role):
    as_role, _ = world
    c = as_role(role)
    shop = USERS[role]["active_store_id"]
    params = {"store_id": shop} if shop else {}

    seen = {t["task_id"]: t for t in TASKS if t["task_id"] in expected(role)}

    def listed(status):
        n = c.get("/api/v1/tasks", params={**params, "status": status}).json()["total"]
        # ... and that list is the rule's, not merely "the same as the list".
        assert n == sum(1 for t in seen.values() if t["status"] == status and (not shop or t["store_id"] == shop))
        return n

    rep = c.get("/api/v1/reports/tasks/summary", params=params).json()["summary"]
    assert rep["open"] == listed("OPEN")
    assert rep["in_progress"] == listed("IN_PROGRESS")
    assert rep["completed"] == listed("COMPLETED")
    late = c.get("/api/v1/tasks/overdue", params=params).json()["total"]
    assert late == sum(1 for t in seen.values() if t["task_id"] in OVERDUE and (not shop or t["store_id"] == shop))
    assert rep["overdue"] == late
    if shop:
        dash = c.get("/api/v1/reports/dashboard", params=params).json()
        assert dash["appointmentsToday"] == listed("OPEN") + listed("IN_PROGRESS")


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_auto_escalate_only_reaches_tasks_the_caller_can_see(world, role):
    as_role, db = world
    before = {t["task_id"]: t["status"] for t in db.tasks.find()}
    r = as_role(role).post("/api/v1/tasks/auto-escalate-overdue")
    assert r.status_code == 200, r.text
    changed = {t["task_id"] for t in db.tasks.find() if t["status"] != before[t["task_id"]]}
    assert changed and changed <= expected(role)


@pytest.mark.parametrize("role", ROLES)
def test_the_old_whole_shop_task_widgets_are_gone(world, role):
    """GET /tasks/completion-stats and /tasks/escalations (dashboard_widgets)
    counted and listed every task of the shop for any signed-in role. No
    screen calls them; they are deleted, so those paths now fall to the task
    open route and find nothing."""
    as_role, _ = world
    c = as_role(role)
    assert c.get("/api/v1/tasks/completion-stats").status_code == 404
    assert c.get("/api/v1/tasks/escalations").status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/tasks/integrity/silent", "/api/v1/tasks/integrity/fake-closures"])
def test_integrity_lists_stay_inside_the_managers_shops(world, path):
    """A store manager signed in with no active shop: the integrity lists
    used to drop the shop filter and read every task in the chain."""
    as_role, db = world
    t0 = datetime.now() - timedelta(days=1)
    if path.endswith("silent"):  # every task open and never acknowledged
        db.tasks.update_many({}, {"$set": {"status": "OPEN", "created_at": t0}})
    else:  # every task "completed" 5 seconds after it was raised
        db.tasks.update_many({}, {"$set": {
            "status": "COMPLETED", "created_at": t0, "completed_at": t0 + timedelta(seconds=5)}})
    USERS["STORE_MANAGER_NO_SHOP"] = {**USERS["STORE_MANAGER"], "active_store_id": None}
    try:
        r = as_role("STORE_MANAGER_NO_SHOP").get(path)
    finally:
        USERS.pop("STORE_MANAGER_NO_SHOP")
    assert r.status_code == 200, r.text
    key = "silent" if path.endswith("silent") else "flagged"
    got = {t["task_id"] for t in r.json()[key]}
    assert got and got <= S1_IDS, got


@pytest.mark.parametrize("role", ["SALES_STAFF", "STORE_MANAGER", "AREA_MANAGER"])
def test_junk_store_ids_on_the_token_do_not_500_any_task_door(world, role):
    """auth copies store_ids from the user record unchecked, so a stray None
    (or a number, or "") reaches the rule. A bare sorted() of that raised
    TypeError: a 500 on every task door and on the Reports dashboard. The
    junk is dropped; the caller sees exactly what the clean token sees."""
    as_role, _ = world
    doors = ["/api/v1/tasks", "/api/v1/tasks/summary", "/api/v1/tasks/overdue",
             f"/api/v1/tasks/P-{role}" if role in STAFF_ROLES else "/api/v1/tasks/U-S1"]
    clean = {p: as_role(role).get(p).json() for p in doors}
    clean_dash = as_role(role).get("/api/v1/reports/dashboard", params={"store_id": S1}).json()
    USERS["JUNK"] = {**USERS[role], "store_ids": [*USERS[role]["store_ids"], None, 7, ""]}
    try:
        c = as_role("JUNK")
        for p in doors:
            r = c.get(p)
            assert r.status_code == 200, (p, r.text)
            assert r.json() == clean[p], p
        r = c.get("/api/v1/reports/dashboard", params={"store_id": S1})
        assert r.status_code == 200, r.text
        assert r.json()["appointmentsToday"] == clean_dash["appointmentsToday"]
    finally:
        USERS.pop("JUNK")


@pytest.mark.parametrize("route", MANAGER_ROUTES)
def test_only_a_manager_cancels_or_reassigns_a_task(world, route):
    """Owner ruling 2026-10-13 (control over convenience): staff start and
    complete their own tasks but never cancel or hand them on - 403 on their
    own task, nothing written. A manager does both."""
    as_role, db = world
    method, tail, kw, landed = MANAGER_ROUTES[route]
    for role in STAFF_ROLES:
        for tid in (f"P-{role}", f"R-{role}"):  # their own, and their role's
            before = db.tasks.find_one({"task_id": tid})
            r = getattr(as_role(role), method)(f"/api/v1/tasks/{tid}{tail}", **kw)
            assert r.status_code == 403, (tid, r.status_code, r.text)
            assert db.tasks.find_one({"task_id": tid}) == before, tid
    for i, role in enumerate(MANAGERS):
        tid = f"P-{STAFF_ROLES[i]}"  # a staff member's task at BV-S1
        r = getattr(as_role(role), method)(f"/api/v1/tasks/{tid}{tail}", **kw)
        assert r.status_code == 200, (role, r.status_code, r.text)
        assert landed(db.tasks.find_one({"task_id": tid})), role


@pytest.mark.parametrize("path", ["/api/v1/tasks/auto-generate", "/api/v1/tasks/{task_id}/reassign"])
def test_the_manager_only_rows_name_the_code_gate(path):
    """The rbac row the middleware enforces names exactly the roles the
    handler's own gate admits, so neither can drift from the other."""
    row = policy_for("POST", path)
    assert set(row["allowed"]) == set(tasks_mod.MANAGER_ROLES), row


def test_pressing_auto_generate_never_makes_anyone_a_tasks_creator(world, monkeypatch):
    """The panel's probe (round 6): POST /tasks/auto-generate was open to every
    signed-in role and stamped the presser as assigned_by on every SOP task
    it issued - so a sales person who pressed it became the 'creator' of the
    cashier's cash-drawer SOP and could open it. Now staff cannot press it,
    the SOP tasks are the system's, and the cashier's stays 'not found'."""
    as_role, db = world
    db.sop_templates.insert_one({
        "template_id": "SOP-CASH", "title": "Count the cash drawer", "frequency": "DAILY",
        "is_active": True, "store_id": S1, "assigned_users": [uid("CASHIER")],
        "steps": [{"instruction": "Count the float"}],
    })
    monkeypatch.setattr(tasks_mod, "_sop_collection", lambda: db.sop_templates)
    assert as_role("SALES_STAFF").post("/api/v1/tasks/auto-generate").status_code == 403
    assert db.tasks.count_documents({"source": "SOP"}) == 0
    r = as_role("STORE_MANAGER").post("/api/v1/tasks/auto-generate")
    assert r.status_code == 200 and r.json()["generated"] == 1, r.text
    sop = db.tasks.find_one({"source": "SOP"})
    assert sop["assigned_to"] == uid("CASHIER")
    assert sop["assigned_by"] == "system" and "created_via" not in sop
    assert as_role("CASHIER").get(f"/api/v1/tasks/{sop['task_id']}").status_code == 200
    assert as_role("SALES_STAFF").get(f"/api/v1/tasks/{sop['task_id']}").status_code == 404


def test_roles_are_matched_whatever_their_case(world):
    """A legacy row addressed to "cashier", a token that says "cashier", and a
    task raised for "cashier" all mean the CASHIER role."""
    as_role, db = world
    db.tasks.insert_one(_task("R-lower", "cashier", S1))
    assert "R-lower" in _ids(as_role("CASHIER").get("/api/v1/tasks"))
    for role in ("CASHIER", "STORE_MANAGER", "ADMIN"):
        USERS["LOWER"] = {**USERS[role], "roles": [role.lower()]}
        try:
            assert _ids(as_role("LOWER").get("/api/v1/tasks")) == expected(role) | {"R-lower"}, role
        finally:
            USERS.pop("LOWER")
    made = as_role("STORE_MANAGER").post("/api/v1/tasks", json={
        "title": "Re-count the float", "priority": "P2", "assigned_to": "cashier",
        "due_at": FUTURE.isoformat()})
    assert made.status_code == 201, made.text
    assert db.tasks.find_one({"task_id": made.json()["task_id"]})["assigned_to"] == "CASHIER"


def test_a_token_with_no_user_id_has_no_tasks_of_its_own(world):
    """my-tasks asks for assigned_to = the caller's id. With no id that is
    assigned_to = None - every unassigned task in the chain - unless the
    visibility rule still narrows it."""
    as_role, _ = world
    USERS["NO_ID"] = {**USERS["SALES_STAFF"], "user_id": None}
    try:
        assert _ids(as_role("NO_ID").get("/api/v1/tasks/my-tasks")) == set()
    finally:
        USERS.pop("NO_ID")
