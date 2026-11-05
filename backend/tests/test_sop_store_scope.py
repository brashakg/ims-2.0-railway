"""SOP templates + checklists keep to the caller's shops (audit R02).

The checklist READ used store_in_view, but the checklist WRITE took
payload.store_id as given, and template detail / edit / assign / list never
looked at the template's shop: a shop-A login ticked, read and edited shop B's
SOP. One door now opens every template (_sop_in_reach) and one rule resolves
the checklist's shop (_sop_checklist_scope), for the read and the write alike.

Rules pinned here:
  * a shop's template: its own shops only (AREA_MANAGER: his shops; admins:
    every shop) -- another shop's reads as 404, the same as a missing one;
  * a chain-wide template (no store_id): every login may read and tick it,
    only an admin may change it (it is every shop's procedure);
  * create stamps a shop the caller holds; archive / un-archive is admin-only
    on PATCH exactly as on DELETE; an archived SOP is not ticked;
  * a checklist is kept at the template's own shop (a chain-wide one at the
    caller's shop) -- never at a shop the caller does not hold.
A refused write leaves the template and the completions untouched.
"""
from __future__ import annotations

import copy
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from tests.strict_fakes import StrictCollection  # noqa: E402

A, B = "AUDTA", "AUDTB"
STEPS = [{"step_number": 1, "instruction": "Unlock"}, {"step_number": 2, "instruction": "Lights"}]


def _tpl(tid, store, **kw):
    return {"template_id": tid, "title": tid, "description": "orig", "steps": STEPS,
            "store_id": store, "is_active": True, "frequency": "DAILY", **kw}


def _user(roles, active, stores=None):
    return {"user_id": f"u-{roles[0]}", "roles": roles, "active_store_id": active,
            "store_ids": stores if stores is not None else ([active] if active else [])}


STAFF_A = _user(["SALES_STAFF"], A)
MGR_A = _user(["STORE_MANAGER"], A)
AREA_AB = _user(["AREA_MANAGER"], A, [A, B])
ADMIN = _user(["ADMIN"], A)


@pytest.fixture
def world(monkeypatch):
    tcol = StrictCollection("sop_templates", [
        _tpl("SOP-A", A), _tpl("SOP-B", B), _tpl("SOP-G", None),
        _tpl("SOP-A-OLD", A, is_active=False), _tpl("SOP-B-OLD", B, is_active=False),
    ])
    ccol = StrictCollection("sop_completions")
    monkeypatch.setattr(tasks_mod, "_sop_collection", lambda: tcol)
    monkeypatch.setattr(tasks_mod, "_sop_completions_collection", lambda: ccol)
    return tcol, ccol


def _client(user):
    app = FastAPI()
    app.include_router(tasks_mod.router, prefix="/tasks")

    async def _u():
        return dict(user)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _tick(user, tid, **kw):
    return _client(user).post("/tasks/sop-checklist/item",
                              json={"template_id": tid, "step_number": 1, "completed": True, **kw})


def _frozen(world):
    return copy.deepcopy(world[0].docs), copy.deepcopy(world[1].docs)


# -- checklist WRITE -----------------------------------------------------------


@pytest.mark.parametrize("user", [STAFF_A, MGR_A])
@pytest.mark.parametrize("extra", [{"store_id": B}, {}])
def test_shop_login_cannot_tick_another_shops_sop(world, user, extra):
    before = _frozen(world)
    r = _tick(user, "SOP-B", **extra)
    assert r.status_code == 404, r.text
    assert _frozen(world) == before


@pytest.mark.parametrize("tid", ["SOP-A", "SOP-G"])
def test_own_shop_ticks_its_sop_and_the_chain_wide_one(world, tid):
    r = _tick(STAFF_A, tid)
    assert r.status_code == 200, r.text
    assert r.json()["store_id"] == A
    assert world[1].docs[0]["store_id"] == A and world[1].docs[0]["template_id"] == tid


def test_shop_login_cannot_tick_a_chain_wide_sop_at_a_foreign_shop(world):
    r = _tick(STAFF_A, "SOP-G", store_id=B)
    assert r.status_code == 403
    assert world[1].docs == []


def test_checklist_is_kept_at_the_templates_own_shop(world):
    # Area manager working from A ticks B's SOP: it lands on B's checklist.
    r = _tick(AREA_AB, "SOP-B")
    assert r.status_code == 200, r.text
    assert world[1].docs[0]["store_id"] == B
    # ...and naming a different shop for a shop's SOP is refused.
    r = _tick(ADMIN, "SOP-A", store_id=B)
    assert r.status_code == 400
    assert len(world[1].docs) == 1


def test_archived_sop_is_not_ticked(world):
    r = _tick(STAFF_A, "SOP-A-OLD")
    assert r.status_code == 409
    assert world[1].docs == []


def test_admin_ticks_any_shops_sop(world):
    r = _tick(ADMIN, "SOP-B", store_id=B)
    assert r.status_code == 200, r.text
    assert world[1].docs[0]["store_id"] == B


# -- checklist READ ------------------------------------------------------------


def test_checklist_read_hides_another_shops_template(world):
    c = _client(STAFF_A)
    assert c.get("/tasks/sop-checklist", params={"template_id": "SOP-B"}).status_code == 404
    assert c.get("/tasks/sop-checklist", params={"template_id": "SOP-B", "store_id": B}).status_code == 404
    ok = c.get("/tasks/sop-checklist", params={"template_id": "SOP-A"})
    assert ok.status_code == 200 and ok.json()["store_id"] == A


# -- template detail / list ----------------------------------------------------


@pytest.mark.parametrize("tid", ["SOP-B", "SOP-B-OLD"])
def test_detail_of_another_shops_template_is_404(world, tid):
    assert _client(MGR_A).get(f"/tasks/sop-templates/{tid}").status_code == 404


def test_detail_positive(world):
    assert _client(MGR_A).get("/tasks/sop-templates/SOP-A").status_code == 200
    assert _client(STAFF_A).get("/tasks/sop-templates/SOP-G").status_code == 200
    assert _client(ADMIN).get("/tasks/sop-templates/SOP-B").status_code == 200
    assert _client(AREA_AB).get("/tasks/sop-templates/SOP-B").status_code == 200


@pytest.mark.parametrize("active_only", ["true", "false"])
def test_unfiltered_list_keeps_to_own_shops(world, active_only):
    r = _client(MGR_A).get("/tasks/sop-templates", params={"active_only": active_only})
    ids = {t["template_id"] for t in r.json()["templates"]}
    assert "SOP-B" not in ids and "SOP-B-OLD" not in ids
    assert {"SOP-A", "SOP-G"} <= ids


def test_list_positive_hq(world):
    ids = {t["template_id"] for t in _client(ADMIN).get("/tasks/sop-templates").json()["templates"]}
    assert {"SOP-A", "SOP-B", "SOP-G"} <= ids
    ids = {t["template_id"] for t in _client(AREA_AB).get("/tasks/sop-templates").json()["templates"]}
    assert {"SOP-A", "SOP-B", "SOP-G"} <= ids
    assert _client(MGR_A).get("/tasks/sop-templates", params={"store_id": B}).status_code == 403


# -- template writes -----------------------------------------------------------


def test_manager_cannot_edit_or_assign_another_shops_template(world):
    before = _frozen(world)
    c = _client(MGR_A)
    assert c.patch("/tasks/sop-templates/SOP-B", json={"description": "x"}).status_code == 404
    assert c.post("/tasks/sop-templates/SOP-B/assign", json={"assigned_users": ["u1"]}).status_code == 404
    assert _frozen(world) == before


def test_manager_cannot_change_a_chain_wide_template(world):
    before = _frozen(world)
    c = _client(MGR_A)
    assert c.patch("/tasks/sop-templates/SOP-G", json={"description": "x"}).status_code == 403
    assert c.post("/tasks/sop-templates/SOP-G/assign", json={"assigned_roles": ["CASHIER"]}).status_code == 403
    assert _frozen(world) == before


def test_archive_state_is_admin_only_on_patch_too(world):
    before = _frozen(world)
    c = _client(MGR_A)
    assert c.patch("/tasks/sop-templates/SOP-A-OLD", json={"is_active": True}).status_code == 403
    assert c.patch("/tasks/sop-templates/SOP-A", json={"is_active": False}).status_code == 403
    assert _frozen(world) == before


def test_manager_edits_and_assigns_own_template(world):
    c = _client(MGR_A)
    assert c.patch("/tasks/sop-templates/SOP-A", json={"description": "new"}).status_code == 200
    assert c.post("/tasks/sop-templates/SOP-A/assign", json={"assigned_users": []}).status_code == 200
    doc = next(d for d in world[0].docs if d["template_id"] == "SOP-A")
    assert doc["description"] == "new" and doc["assigned_users"] == []


def test_admin_edits_any_template_and_archive_state(world):
    c = _client(ADMIN)
    assert c.patch("/tasks/sop-templates/SOP-B", json={"description": "hq"}).status_code == 200
    assert c.patch("/tasks/sop-templates/SOP-G", json={"description": "hq"}).status_code == 200
    assert c.patch("/tasks/sop-templates/SOP-B-OLD", json={"is_active": True}).status_code == 200
    assert c.delete("/tasks/sop-templates/SOP-B").status_code == 200


def test_missing_template_is_404_on_every_door(world):
    c = _client(ADMIN)
    assert c.get("/tasks/sop-templates/NOPE").status_code == 404
    assert c.patch("/tasks/sop-templates/NOPE", json={"description": "x"}).status_code == 404
    assert c.post("/tasks/sop-templates/NOPE/assign", json={}).status_code == 404
    assert _tick(ADMIN, "NOPE").status_code == 404


def test_create_stamps_only_a_shop_the_caller_holds(world):
    body = {"title": "Close", "category": "Operations", "frequency": "DAILY"}
    c = _client(MGR_A)
    assert c.post("/tasks/sop-templates", json={**body, "store_id": B}).status_code == 403
    assert len(world[0].docs) == 5
    r = c.post("/tasks/sop-templates", json=body)
    assert r.status_code == 201 and r.json()["template"]["store_id"] == A
    r = _client(ADMIN).post("/tasks/sop-templates", json={**body, "store_id": B})
    assert r.status_code == 201 and r.json()["template"]["store_id"] == B


# -- who an SOP may be given to (Codex review of #1374) ------------------------
#
# The SOP's own doors were shut, but a second door stayed open: a shop-A
# manager named a shop-B-only employee on shop A's SOP, the generator issued
# that person a shop-A task, and the task detail then showed them shop A's
# procedure. Every SOP writer now holds a named person to _named_target -- the
# rule a task edit / reassign / escalate already uses: a real, active colleague
# who works at the SOP's shop (a chain-wide SOP, which only an admin edits: any
# active colleague). The generator re-checks legacy names against the shop it
# issues for and skips (and logs) the ones that shop may not reach.

PEOPLE = {
    "u-a1": {"user_id": "u-a1", "roles": ["SALES_STAFF"], "store_ids": [A], "is_active": True},
    "u-b1": {"user_id": "u-b1", "roles": ["SALES_STAFF"], "store_ids": [B], "is_active": True},
    "u-gone": {"user_id": "u-gone", "roles": ["SALES_STAFF"], "store_ids": [A], "is_active": False},
    "u-area": {"user_id": "u-area", "roles": ["AREA_MANAGER"], "store_ids": [A, B], "is_active": True},
}


class _People:
    def find_by_id(self, user_id):
        return copy.deepcopy(PEOPLE.get(user_id))

    def find_by_role(self, role, store_id=None):
        return [copy.deepcopy(u) for u in PEOPLE.values()
                if role in u["roles"] and u["is_active"] and (not store_id or store_id in u["store_ids"])]


class _Tasks:
    def __init__(self):
        self.rows = []

    def create(self, doc):
        self.rows.append(doc)
        return doc

    def find_many(self, flt=None):
        return [r for r in self.rows if r.get("store_id") == (flt or {}).get("store_id", r.get("store_id"))]


@pytest.fixture
def people(world, monkeypatch):
    monkeypatch.setattr(tasks_mod, "get_user_repository", lambda: _People())
    tasks = _Tasks()
    monkeypatch.setattr(tasks_mod, "get_task_repository", lambda: tasks)
    return tasks


def _doc(world, tid):
    return next(d for d in world[0].docs if d["template_id"] == tid)


BAD_PEOPLE = ["u-b1", "u-gone", "u-nobody"]  # another shop's, a leaver, nobody


@pytest.mark.parametrize("who", BAD_PEOPLE)
def test_no_sop_door_names_a_person_outside_the_sops_shop(world, people, who):
    before = _frozen(world)
    c = _client(MGR_A)
    body = {"title": "Close", "category": "Operations", "frequency": "DAILY"}
    assert c.post("/tasks/sop-templates", json={**body, "assigned_users": [who]}).status_code == 400
    assert c.patch("/tasks/sop-templates/SOP-A", json={"assigned_users": ["u-a1", who]}).status_code == 400
    assert c.post("/tasks/sop-templates/SOP-A/assign", json={"assigned_users": [who]}).status_code == 400
    assert _frozen(world) == before  # nothing saved by any of the three


@pytest.mark.parametrize("who", ["u-gone", "u-nobody"])
def test_admin_names_only_an_active_colleague_on_a_chain_wide_sop(world, people, who):
    before = _frozen(world)
    assert _client(ADMIN).post("/tasks/sop-templates/SOP-G/assign", json={"assigned_users": [who]}).status_code == 400
    assert _frozen(world) == before


def test_same_shop_people_are_named_on_every_door(world, people):
    c = _client(MGR_A)
    body = {"title": "Close", "category": "Operations", "frequency": "DAILY"}
    r = c.post("/tasks/sop-templates", json={**body, "assigned_users": ["u-a1", "u-area"]})
    assert r.status_code == 201, r.text
    assert r.json()["template"]["assigned_users"] == ["u-a1", "u-area"]
    assert c.patch("/tasks/sop-templates/SOP-A", json={"assigned_users": ["u-area"]}).status_code == 200
    assert _doc(world, "SOP-A")["assigned_users"] == ["u-area"]
    assert c.post("/tasks/sop-templates/SOP-A/assign", json={"assigned_users": ["u-a1"]}).status_code == 200
    assert _doc(world, "SOP-A")["assigned_users"] == ["u-a1"]
    # Clearing is always allowed.
    assert c.post("/tasks/sop-templates/SOP-A/assign", json={"assigned_users": []}).status_code == 200


def test_admin_names_each_shops_people_and_anyone_on_a_chain_wide_sop(world, people):
    c = _client(ADMIN)
    assert c.post("/tasks/sop-templates/SOP-B/assign", json={"assigned_users": ["u-b1"]}).status_code == 200
    assert c.patch("/tasks/sop-templates/SOP-G", json={"assigned_users": ["u-a1", "u-b1"]}).status_code == 200
    assert _doc(world, "SOP-G")["assigned_users"] == ["u-a1", "u-b1"]
    # The shop rule binds HQ too: a shop's SOP goes to that shop's people.
    assert c.post("/tasks/sop-templates/SOP-A/assign", json={"assigned_users": ["u-b1"]}).status_code == 400


def _generate(user, store):
    return _client(user).post("/tasks/auto-generate", params={"store_id": store})


def test_generation_skips_legacy_names_the_shop_may_not_reach(world, people):
    _doc(world, "SOP-A")["assigned_users"] = ["u-b1", "u-a1", "u-gone", "u-nobody"]
    _doc(world, "SOP-G")["assigned_users"] = ["u-b1", "u-a1"]
    r = _generate(MGR_A, A)
    assert r.status_code == 200, r.text
    issued = {(t["sop_template_id"], t["assigned_to"]) for t in people.rows}
    assert issued == {("SOP-A", "u-a1"), ("SOP-G", "u-a1")}
    assert all(t["store_id"] == A for t in people.rows)


def test_generation_with_every_legacy_name_skipped_goes_to_the_presser(world, people):
    _doc(world, "SOP-A")["assigned_users"] = ["u-b1"]
    _doc(world, "SOP-G")["is_active"] = False
    r = _generate(MGR_A, A)
    assert r.status_code == 200, r.text
    assert [t["assigned_to"] for t in people.rows] == [MGR_A["user_id"]]
    assert r.json()["unassigned_templates"] == ["SOP-A"]


def test_area_manager_generates_his_second_shops_sop_for_its_people(world, people):
    _doc(world, "SOP-B")["assigned_users"] = ["u-b1", "u-area", "u-a1"]
    _doc(world, "SOP-G")["is_active"] = False
    r = _generate(AREA_AB, B)
    assert r.status_code == 200, r.text
    assert {t["assigned_to"] for t in people.rows} == {"u-b1", "u-area"}
    assert all(t["store_id"] == B for t in people.rows)
