"""
Catalog page PART 2: select, flag, archive, restore (owner 2026-10-12)
=====================================================================
Mockup artboards Main + FlagDialog. Through the REAL routers (a small app
over the catalog + tasks routers, the role gates are the routes' own):

  * who may flag: ADMIN / SUPERADMIN / STORE_MANAGER; never a counter role,
    never a CATALOG_MANAGER (owner: catalogue managers clear flags, they do
    not raise them). Archive and Restore: ADMIN / SUPERADMIN only.
  * ONE task per person per flag action, assigned to the PERSON who added the
    product; a gone user's products go to an active CATALOG_MANAGER.
  * the same open type is never flagged twice on a product
  * Mark fixed and task completion close each other (one rule)
  * archive = the existing soft delete: delists a live product (spy, never a
    real Shopify call), hides it from the Catalog list and the till search;
    the dry run counts units at the shops and live listings
  * restore: active again, NOT re-published online, an audit row written
  * the rbac rows equal the code gates; raising a flag is its own capability

***** SAFETY: every Shopify call is a spy. Nothing reaches Shopify. *****

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_catalog_flags_archive.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from api import dependencies as deps  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.routers import tasks as tasks_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import catalog_flags, rbac_policy, shopify_push  # noqa: E402
from api.services.capabilities import capability_for, _CAP_ROLE_HINT  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.task_repository import TaskRepository  # noqa: E402
from database.repositories.user_repository import UserRepository  # noqa: E402

GID = "gid://shopify/Product/777"


def _user(uid, *roles):
    """The shape a real request's current_user has: the access token's payload
    (auth.py login / store switch) -- user_id, username, roles. NO full_name:
    a display name must come from users."""
    return {"user_id": uid, "username": uid.replace("u-", ""), "roles": list(roles)}


ADMIN = _user("u-admin", "ADMIN")
STORE_MGR = _user("u-sm", "STORE_MANAGER")
CAT_MGR = _user("u-mansi", "CATALOG_MANAGER")


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        # The DatabaseConnection shape (conn.<collection>): the archive's
        # held-receipt check reads conn.grns (dependencies.get_grn_repository).
        if name.startswith("_"):
            raise AttributeError(name)
        return self.db[name]


class _DelistSpy:
    """push_product_delist without the network: records the call, then does
    what the LIVE take-down does on success -- THE real write-back
    (_writeback_product with status DRAFT: ecom.status DRAFT and the
    take-down marker ecom.taken_down_at), so the restore tests read the
    state a real archive leaves behind."""

    def __init__(self):
        self.calls = []

    async def __call__(self, db, product):
        from api.services.shopify_push.writeback import _writeback_product

        self.calls.append(copy.deepcopy(product))
        gid = (product.get("ecom") or {}).get("shopify_product_id")
        _writeback_product(db, product.get("id"), gid, status="DRAFT")
        return shopify_push.PushResult(
            mode=shopify_push.MODE_LIVE, entity="product", action="delist",
            target_id=product.get("id"), ok=True,
            shopify_id=(product.get("ecom") or {}).get("shopify_product_id"),
        )


def _spine(pid, created_by, **extra):
    doc = {
        "product_id": pid, "pim_product_id": f"T-{pid}", "sku": f"SKU-{pid}",
        "name": f"Frame {pid}", "brand": "Carrera", "model": pid, "category": "SUNGLASS",
        "mrp": 5000, "is_active": True, "created_by": created_by,
        "created_at": datetime(2026, 10, 1, 6, 0), "updated_at": datetime(2026, 10, 1, 6, 0),
    }
    doc.update(extra)
    return doc


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "")
    db = mongomock.MongoClient().db
    db.users.insert_many([
        {"user_id": "u-admin", "username": "admin", "full_name": "Avinash Kumar", "roles": ["ADMIN"],
         "is_active": True},
        {"user_id": "u-sm", "username": "sm", "full_name": "Ravi Sinha", "roles": ["STORE_MANAGER"],
         "is_active": True},
        {"user_id": "u-mansi", "full_name": "Mansi Kumari", "roles": ["CATALOG_MANAGER"], "is_active": True},
        {"user_id": "u-akshay", "full_name": "Akshay Ovhal", "roles": ["CATALOG_MANAGER"], "is_active": True},
        {"user_id": "u-left", "full_name": "Left Us", "roles": ["CATALOG_MANAGER"], "is_active": False},
    ])
    db.products.insert_many([
        _spine("P1", "u-mansi"),
        _spine("P2", "u-mansi"),
        _spine("P3", "u-akshay"),
        _spine("P4", "u-left"),
    ])
    db.catalog_products.insert_many([
        {"id": "T-P1", "sku": "SKU-P1", "title": "Frame P1", "is_active": True,
         "images": ["https://cdn.example.com/p1.jpg"],
         "ecom": {"status": "PUBLISHED", "shopify_product_id": GID, "locally_modified": False,
                  "shopify_inventory_item_id": "gid://shopify/InventoryItem/701"}},
        {"id": "T-P2", "sku": "SKU-P2", "title": "Frame P2", "is_active": True,
         "ecom": {"status": "DRAFT", "locally_modified": False}},
    ])
    db.stock_units.insert_many([
        {"product_id": "P1", "store_id": "S1", "status": "AVAILABLE"},
        {"product_id": "P1", "store_id": "S2", "status": "reserved"},
        {"product_id": "P1", "store_id": "S1", "status": "SOLD"},
    ])
    conn = _Conn(db)
    repo = lambda: ProductRepository(db.products)  # noqa: E731
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_product_repository", repo)
    monkeypatch.setattr(products_mod, "get_product_repository", repo)
    monkeypatch.setattr(deps, "get_user_repository", lambda: UserRepository(db.users))
    monkeypatch.setattr(deps, "get_task_repository", lambda: TaskRepository(db.tasks))
    monkeypatch.setattr(tasks_mod, "get_task_repository", lambda: TaskRepository(db.tasks))
    monkeypatch.setattr(deps, "get_audit_repository", lambda: AuditRepository(db.audit_logs))
    monkeypatch.setattr(catalog_mod, "_get_db", lambda: db)
    monkeypatch.setattr(catalog_mod, "_catalog_coll", lambda: db.catalog_products)
    spy = _DelistSpy()
    monkeypatch.setattr(shopify_push, "push_product_delist", spy)
    variant_calls = []

    async def _variant_spy(db_, twin):
        variant_calls.append(twin.get("id"))
        return shopify_push.PushResult(
            mode=shopify_push.MODE_LIVE, entity="product", action="delist",
            target_id=twin.get("id"), ok=True,
        )

    monkeypatch.setattr(shopify_push, "_delist_variant_row", _variant_spy)

    class _NoCache:
        TTL_MEDIUM = 0

        def get(self, k):
            return None

        def set(self, k, v, ttl=0):
            pass

        def delete_pattern(self, p):
            pass

    import api.services.cache as cache_mod

    monkeypatch.setattr(cache_mod, "cache", _NoCache())

    app = FastAPI()
    app.include_router(catalog_mod.router, prefix="/api/v1/catalog")
    app.include_router(tasks_mod.router, prefix="/api/v1/tasks")
    who = {"user": ADMIN}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    client = TestClient(app)

    def call(user, method, path, **kw):
        who["user"] = user
        return client.request(method, path, **kw)

    return {"db": db, "call": call, "spy": spy, "variant_calls": variant_calls}


def _flags(db, pid):
    return (db.products.find_one({"product_id": pid}) or {}).get("flags") or []


def _flag(env, user, ids, type_="needs_revision", note="Lens colour is wrong"):
    return env["call"](user, "POST", "/api/v1/catalog/flags",
                       json={"product_ids": ids, "type": type_, "note": note})


# ---------------------------------------------------------------------------
# Who may flag / archive / restore
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("user", [ADMIN, STORE_MGR, _user("u-root", "SUPERADMIN")])
def test_flaggers_may_flag(env, user):
    assert _flag(env, user, ["P1"]).status_code == 200


@pytest.mark.parametrize(
    "user",
    [CAT_MGR, _user("u-c", "CASHIER"), _user("u-s", "SALES_STAFF"), _user("u-sc", "SALES_CASHIER"),
     _user("u-o", "OPTOMETRIST"), _user("u-a", "AREA_MANAGER")],
)
def test_no_one_else_may_flag(env, user):
    assert _flag(env, user, ["P1"]).status_code == 403
    assert _flags(env["db"], "P1") == []


@pytest.mark.parametrize("door", ["bulk-archive", "bulk-restore"])
def test_store_manager_gets_403_on_archive_and_restore(env, door):
    res = env["call"](STORE_MGR, "POST", f"/api/v1/catalog/products/{door}",
                      json={"product_ids": ["P1"], "apply": True})
    assert res.status_code == 403
    assert env["db"].products.find_one({"product_id": "P1"})["is_active"] is True


def test_bulk_doors_take_at_most_200(env):
    ids = [f"X{i}" for i in range(201)]
    for path in ("/api/v1/catalog/products/bulk-archive", "/api/v1/catalog/flags"):
        res = env["call"](ADMIN, "POST", path, json={"product_ids": ids, "type": "price_check"})
        assert res.status_code == 422, path


# ---------------------------------------------------------------------------
# One task per person, assigned to the person who added the product
# ---------------------------------------------------------------------------


def test_one_task_per_person_per_flag_action(env):
    res = _flag(env, ADMIN, ["P1", "P2", "P3"], type_="photo_redo", note="Front photo is 3rd")
    assert res.status_code == 200
    body = res.json()
    assert [r["status"] for r in body["results"]] == ["done", "done", "done"]
    tasks = list(env["db"].tasks.find({}))
    assert len(tasks) == 2
    by_person = {t["assigned_to"]: t for t in tasks}
    assert set(by_person) == {"u-mansi", "u-akshay"}
    mansi = by_person["u-mansi"]
    assert mansi["title"] == "Needs photo redo: 2 products to fix"
    # The flagger's FULL name from users, never the token's username.
    assert "Note from Avinash Kumar: Front photo is 3rd" in mansi["description"]
    assert mansi["created_by_name"] == "Avinash Kumar"
    assert [link["to"] for link in mansi["links"]] == [
        "/catalog/add?edit=P1", "/catalog/add?edit=P2"]
    assert mansi["store_id"] is None and mansi["status"] == "OPEN"
    # Each flag carries its task.
    for pid, person in (("P1", "u-mansi"), ("P2", "u-mansi"), ("P3", "u-akshay")):
        (flag,) = _flags(env["db"], pid)
        assert flag["status"] == "open" and flag["type"] == "photo_redo"
        assert flag["task_id"] == by_person[person]["task_id"]
        assert flag["by"] == "u-admin" and flag["by_name"] == "Avinash Kumar"
    # A second action is a second task, even for the same person.
    _flag(env, STORE_MGR, ["P1"], type_="price_check", note="")
    assert env["db"].tasks.count_documents({"assigned_to": "u-mansi"}) == 2


def test_a_gone_users_products_go_to_an_active_catalog_manager(env):
    _flag(env, ADMIN, ["P4"])
    (task,) = list(env["db"].tasks.find({}))
    assert task["assigned_to"] in {"u-mansi", "u-akshay"}


def test_the_same_open_type_is_not_flagged_twice(env):
    _flag(env, ADMIN, ["P1"])
    res = _flag(env, ADMIN, ["P1", "P2"])
    assert [r["status"] for r in res.json()["results"]] == ["already", "done"]
    assert len(_flags(env["db"], "P1")) == 1
    (second,) = [t for t in env["db"].tasks.find({}) if "P2" in t["payload"]["product_ids"]]
    assert second["payload"]["product_ids"] == ["P2"]


def test_something_else_needs_a_note(env):
    res = _flag(env, ADMIN, ["P1"], type_="other", note="  ")
    assert res.status_code == 400
    assert "note" in res.json()["detail"]
    assert _flag(env, ADMIN, ["P1"], type_="bogus").status_code == 400


def test_a_flag_is_not_an_edit(env):
    before = env["db"].products.find_one({"product_id": "P1"})
    _flag(env, ADMIN, ["P1"])
    after = env["db"].products.find_one({"product_id": "P1"})
    assert after["updated_at"] == before["updated_at"]
    assert "updated_by" not in after


# ---------------------------------------------------------------------------
# Mark fixed <-> task completion: one rule
# ---------------------------------------------------------------------------


def _fix(env, pid, user=CAT_MGR):
    ids = [f["flag_id"] for f in _flags(env["db"], pid) if f["status"] == "open"]
    return env["call"](user, "POST", "/api/v1/catalog/flags/fix",
                       json={"product_id": pid, "flag_ids": ids})


def test_marking_every_flag_fixed_closes_the_task(env):
    _flag(env, ADMIN, ["P1", "P2"])
    (task,) = list(env["db"].tasks.find({}))
    assert _fix(env, "P1").status_code == 200
    assert env["db"].tasks.find_one({"task_id": task["task_id"]})["status"] == "OPEN"
    res = _fix(env, "P2")
    assert res.status_code == 200
    assert res.json()["flags"][0]["status"] == "fixed"
    done = env["db"].tasks.find_one({"task_id": task["task_id"]})
    assert done["status"] == "COMPLETED"
    assert done["completed_by"] == "u-mansi"
    (flag,) = _flags(env["db"], "P2")
    assert flag["fixed_by"] == "u-mansi" and flag["fixed_at"] is not None


def test_completing_the_task_fixes_its_flags(env):
    _flag(env, ADMIN, ["P1", "P2"])
    _flag(env, ADMIN, ["P1"], type_="price_check", note="")
    first = env["db"].tasks.find_one({"payload.flag_type": "needs_revision"})
    res = env["call"](CAT_MGR, "PATCH", f"/api/v1/tasks/{first['task_id']}/complete",
                      json={"completion_notes": "All fixed"})
    assert res.status_code == 200, res.text
    p1 = {f["type"]: f["status"] for f in _flags(env["db"], "P1")}
    assert p1 == {"needs_revision": "fixed", "price_check": "open"}  # the other task's flag stays
    assert _flags(env["db"], "P2")[0]["status"] == "fixed"


class _RaceColl:
    """The products collection with one action slipped in right after the
    next read: the window in which a read-then-write of the flags list
    would lose what happened meanwhile."""

    def __init__(self, coll, between):
        self._c, self._between = coll, between

    def __getattr__(self, name):
        return getattr(self._c, name)

    def find_one(self, *a, **kw):
        doc = self._c.find_one(*a, **kw)
        between, self._between = self._between, None
        if between:
            between()
        return doc


def _race(monkeypatch, env, between):
    race = _RaceColl(env["db"].products, between)
    monkeypatch.setattr(catalog_flags, "_products", lambda: race)


def test_a_flag_raised_during_a_fix_stays_with_its_task(env, monkeypatch):
    _flag(env, ADMIN, ["P1"])  # needs_revision, task T1
    (t1,) = list(env["db"].tasks.find({}))
    t2 = {}

    def store_manager_flags_price():
        t2.update(catalog_flags.raise_flags(["P1"], "price_check", "", STORE_MGR)["tasks"][0])

    _race(monkeypatch, env, store_manager_flags_price)
    assert _fix(env, "P1").status_code == 200  # Mark fixed on needs_revision
    flags = {f["type"]: f for f in _flags(env["db"], "P1")}
    assert flags["needs_revision"]["status"] == "fixed"
    assert flags["price_check"]["status"] == "open"
    assert flags["price_check"]["task_id"] == t2["task_id"]
    tasks = {t["task_id"]: t["status"] for t in env["db"].tasks.find({})}
    assert tasks == {t1["task_id"]: "COMPLETED", t2["task_id"]: "OPEN"}


def test_two_fixes_at_once_never_reopen_a_finished_flag(env, monkeypatch):
    _flag(env, ADMIN, ["P1"])
    _flag(env, ADMIN, ["P1"], type_="price_check", note="")
    first, second = _flags(env["db"], "P1")
    _race(monkeypatch, env, lambda: catalog_flags.fix_flags("P1", [first["flag_id"]], CAT_MGR))
    res = env["call"](CAT_MGR, "POST", "/api/v1/catalog/flags/fix",
                      json={"product_id": "P1", "flag_ids": [second["flag_id"]]})
    assert res.status_code == 200
    assert [f["status"] for f in _flags(env["db"], "P1")] == ["fixed", "fixed"]
    assert {t["status"] for t in env["db"].tasks.find({})} == {"COMPLETED"}


def test_a_flag_withdrawn_during_a_fix_stays_withdrawn(env, monkeypatch):
    _flag(env, ADMIN, ["P1"])
    (task,) = list(env["db"].tasks.find({}))
    _race(monkeypatch, env, lambda: catalog_flags.on_task_closed(task, ADMIN, "CANCELLED"))
    assert _fix(env, "P1").status_code == 200
    (flag,) = _flags(env["db"], "P1")
    assert (flag["status"], flag["fixed_by"]) == ("withdrawn", "u-admin")


def test_a_flag_fixed_before_its_task_existed_closes_that_task(env, monkeypatch):
    from api.services import task_triggers

    real = task_triggers.create_system_task

    def fixed_first(*a, **kw):
        (flag,) = _flags(env["db"], "P1")
        catalog_flags.fix_flags("P1", [flag["flag_id"]], CAT_MGR)
        return real(*a, **kw)

    monkeypatch.setattr(task_triggers, "create_system_task", fixed_first)
    _flag(env, ADMIN, ["P1"])
    (task,) = list(env["db"].tasks.find({}))
    assert task["status"] == "COMPLETED"
    assert _flags(env["db"], "P1")[0]["task_id"] == task["task_id"]


def test_two_people_flagging_the_same_type_at_once_make_one_flag(env, monkeypatch):
    inner = {}
    _race(monkeypatch, env, lambda: inner.update(
        catalog_flags.raise_flags(["P1"], "needs_revision", "", STORE_MGR)))
    res = _flag(env, ADMIN, ["P1"])
    assert inner["results"][0]["status"] == "done"
    assert res.json()["results"][0]["status"] == "already" and res.json()["tasks"] == []
    (flag,) = _flags(env["db"], "P1")
    (task,) = list(env["db"].tasks.find({}))
    assert flag["status"] == "open" and flag["task_id"] == task["task_id"]


def test_cataloguers_mark_fixed_store_managers_do_not(env):
    _flag(env, ADMIN, ["P1"])
    assert _fix(env, "P1", user=STORE_MGR).status_code == 403
    assert _fix(env, "P1", user=_user("u-c", "CASHIER")).status_code == 403


# ---------------------------------------------------------------------------
# Archive -- the existing soft delete, in bulk
# ---------------------------------------------------------------------------


def _archive(env, ids, apply=True, user=ADMIN):
    return env["call"](user, "POST", "/api/v1/catalog/products/bulk-archive",
                       json={"product_ids": ids, "apply": apply})


def test_the_dry_run_counts_units_at_shops_and_live_listings(env):
    res = _archive(env, ["P1", "P2", "NOPE"], apply=False)
    rows = {r["product_id"]: r for r in res.json()["results"]}
    assert rows["P1"]["in_stock_units"] == 2  # available + reserved; sold is gone
    assert rows["P1"]["live_online"] is True
    assert rows["P2"]["in_stock_units"] == 0 and rows["P2"]["live_online"] is False
    assert rows["NOPE"] == {"product_id": "NOPE", "in_stock_units": 0, "live_online": False,
                            "status": "refused", "reason": "Product not found"}
    assert env["db"].products.find_one({"product_id": "P1"})["is_active"] is True
    assert env["spy"].calls == []


def _list(user, **kw):
    params = {
        "category": None, "brand": None, "search": None, "match": None, "tag": None,
        "created_by": None, "store_id": None, "skip": 0, "limit": 50, "is_active": None,
        "photo": None, "sort": None, "frame_color": None, "lens_colour": None, "gender": None,
        "shape": None, "frame_type": None, "frame_material": None, "updated_by": None,
        "date_on": None, "date_from": None, "date_to": None, "flag": None,
        "current_user": user,
    }
    params.update(kw)
    return [p["product_id"] for p in asyncio.run(products_mod.list_products(**params))["products"]]


def test_archive_delists_a_live_product_and_hides_it(env):
    res = _archive(env, ["P1", "P2"])
    assert [r["status"] for r in res.json()["results"]] == ["done", "done"]
    assert len(env["spy"].calls) == 1 and env["spy"].calls[0]["id"] == "T-P1"
    spine = env["db"].products.find_one({"product_id": "P1"})
    assert spine["is_active"] is False and spine["deleted_at"] and spine["deleted_by"] == "u-admin"
    twin = env["db"].catalog_products.find_one({"id": "T-P1"})
    assert twin["is_active"] is False and twin["deleted_by"] == "u-admin"
    assert twin["ecom"]["shopify_product_id"] == GID, "never a hard delete"
    assert env["db"].products.count_documents({}) == 4
    # Gone from the Catalog list (with and without Include inactive) and the till.
    assert "P1" not in _list(ADMIN, sort="edited", is_active="true")
    assert "P1" not in _list(ADMIN, sort="edited", is_active="all")
    assert "P1" not in _list(_user("u-c", "CASHIER"), search="Carrera")
    assert set(_list(ADMIN, sort="edited", flag="archived")) == {"P1", "P2"}
    # A second archive refuses honestly.
    again = _archive(env, ["P1"]).json()["results"][0]
    assert again["status"] == "refused" and again["reason"] == "Already archived"


def test_the_single_delete_door_and_the_bulk_door_are_one_rule(env):
    asyncio.run(catalog_mod.delete_catalog_product("T-P1", current_user=ADMIN))
    spine = env["db"].products.find_one({"product_id": "P1"})
    assert spine["is_active"] is False and spine["deleted_by"] == "u-admin"
    assert len(env["spy"].calls) == 1


def test_every_archive_and_restore_door_clears_the_cached_lists(env, monkeypatch):
    import api.services.cache as cache_mod

    seen = []

    class _Spy(cache_mod.cache.__class__):
        def delete_pattern(self, p):
            # What the next uncached list read would see at that moment.
            seen.append((p, bool(env["db"].products.find_one({"product_id": "P1"})["deleted_at"])))

    monkeypatch.setattr(cache_mod, "cache", _Spy())
    asyncio.run(catalog_mod.delete_catalog_product("T-P1", current_user=ADMIN))
    assert seen == [("products:*", True)]  # the single Delete door, after the write
    seen.clear()
    _restore(env, ["P1"])
    assert seen == [("products:*", False)]
    seen.clear()
    _archive(env, ["P1"])
    assert seen == [("products:*", True)]


def test_a_blank_deleted_at_is_not_archived_anywhere(env):
    env["db"].products.update_one({"product_id": "P3"}, {"$set": {"deleted_at": ""}})
    assert "P3" in _list(ADMIN, sort="edited", is_active="true")
    assert "P3" not in _list(ADMIN, sort="edited", flag="archived")
    from api.services.catalog_list import ARCHIVED, NOT_ARCHIVED

    every = env["db"].products.count_documents({})
    assert (env["db"].products.count_documents(ARCHIVED)
            + env["db"].products.count_documents(NOT_ARCHIVED)) == every
    _archive(env, ["P1"])
    env["db"].products.update_one({"product_id": "P2"}, {"$unset": {"deleted_at": ""}})
    assert env["db"].products.count_documents(ARCHIVED) == 1
    assert env["db"].products.count_documents(NOT_ARCHIVED) == every - 1


# ---------------------------------------------------------------------------
# Restore -- back in the catalogue and at the till, NOT back online
# ---------------------------------------------------------------------------


def test_restore_brings_it_back_unpublished(env):
    _archive(env, ["P1"])
    res = env["call"](ADMIN, "POST", "/api/v1/catalog/products/bulk-restore",
                      json={"product_ids": ["P1", "P2"], "apply": True})
    assert [(r["status"], r.get("reason")) for r in res.json()["results"]] == [
        ("done", None), ("refused", "Not archived")]
    spine = env["db"].products.find_one({"product_id": "P1"})
    assert spine["is_active"] is True
    assert spine["deleted_at"] is None and spine["deleted_by"] is None
    twin = env["db"].catalog_products.find_one({"id": "T-P1"})
    assert twin["is_active"] is True and twin["deleted_at"] is None
    assert twin["ecom"]["locally_modified"] is False, "a restore never queues a publish"
    assert twin["ecom"]["online_state"] == "DELISTED", "still off the website"
    assert len(env["spy"].calls) == 1  # the archive's take-down only
    (audit,) = [a for a in env["db"].audit_logs.find({}) if a.get("action") == "product.restored"]
    assert audit["entity_id"] == "P1" and audit["actor"] == "u-admin"
    assert "P1" in _list(ADMIN, sort="edited", is_active="true")
    assert "P1" in _list(_user("u-c", "CASHIER"), search="Carrera")


# ---------------------------------------------------------------------------
# The imported (review) segment reads who added it from its spine
# ---------------------------------------------------------------------------


def _imported(user, sort):
    return asyncio.run(
        catalog_mod.list_catalog_products(
            category=None, brand=None, search=None, is_active="all", needs_review=None,
            source=None, photo=None, limit=50, page=1, sort=sort, current_user=user,
        )
    )["products"]


def test_imported_rows_read_attribution_through_their_spine(env):
    env["db"].catalog_products.insert_one(
        {"id": "IMP-1", "sku": "IMP-SKU", "title": "Imported thing", "needs_review": True,
         "is_active": True, "created_by": "someone-on-the-twin"}
    )
    rows = {r["id"]: r for r in _imported(ADMIN, "name_asc")}
    assert rows["T-P1"]["created_by"] == "u-mansi"
    assert rows["T-P1"]["created_by_name"] == "Mansi Kumari"  # from users
    assert rows["T-P1"]["created_at"] == datetime(2026, 10, 1, 6, 0)
    # No spine: nothing (the page shows '-'), never the twin's own guess.
    assert rows["IMP-1"]["created_by"] is None and rows["IMP-1"]["created_at"] is None
    assert [r["id"] for r in _imported(ADMIN, "name_asc")] == ["T-P1", "T-P2", "IMP-1"]


def test_imported_rows_carry_no_attribution_without_a_sort_or_for_counter_roles(env):
    assert "created_by_name" not in _imported(ADMIN, None)[0]
    assert all("created_by_name" not in r for r in _imported(_user("u-c", "CASHIER"), "name_asc"))


# ---------------------------------------------------------------------------
# The rbac rows equal the code gates
# ---------------------------------------------------------------------------


def _row(method, path):
    (row,) = [r for r in rbac_policy.POLICY if r["method"] == method and r["path"] == path]
    return set(row["allowed"])


def test_rbac_rows_equal_the_code_gates():
    assert _row("POST", "/api/v1/catalog/flags") == set(catalog_flags.FLAGGER_ROLES) | {"SUPERADMIN"}
    assert _row("POST", "/api/v1/catalog/flags/fix") == set(catalog_flags.FIXER_ROLES) | {"SUPERADMIN"}
    assert _row("POST", "/api/v1/catalog/products/bulk-archive") == {"ADMIN", "SUPERADMIN"}
    assert _row("POST", "/api/v1/catalog/products/bulk-restore") == {"ADMIN", "SUPERADMIN"}
    assert _row("GET", "/api/v1/products/facets") == {
        "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"}
    assert set(products_mod._CATALOGUER_VIEW_ROLES) == _row("GET", "/api/v1/products/facets")


def test_raising_a_flag_is_its_own_capability():
    assert capability_for("POST", "/api/v1/catalog/flags") == "catalog:flag"
    assert _CAP_ROLE_HINT["catalog:flag"] == {"ADMIN", "STORE_MANAGER", "SUPERADMIN"}
    assert capability_for("POST", "/api/v1/catalog/flags/fix") == "catalog:write"


# ---------------------------------------------------------------------------
# Panel round: the rules the earlier tests could not tell apart
# ---------------------------------------------------------------------------


def _restore(env, ids, user=ADMIN):
    return env["call"](user, "POST", "/api/v1/catalog/products/bulk-restore",
                       json={"product_ids": ids, "apply": True})


def _add_size_of_p1(env):
    """P5: a SIZE VARIANT of P1 (owns no listing; rides T-P1's live listing
    through its catalog_variants row)."""
    db = env["db"]
    db.products.insert_one(_spine("P5", "u-mansi"))
    db.catalog_products.insert_one(
        {"id": "T-P5", "sku": "SKU-P5", "title": "Frame P1 size 52", "is_active": True,
         "ecom": {"variant_of": {"product_id": "P1", "twin_id": "T-P1", "sku": "SKU-P1"},
                  "locally_modified": False}})
    db.catalog_variants.insert_one(
        {"sku": "SKU-P5", "parent_product_id": "T-P1",
         "shopify_variant_id": "gid://shopify/ProductVariant/55",
         "shopify_inventory_item_id": "gid://shopify/InventoryItem/755"})


def _off_sale_online(env, sku):
    """THE quantity rule's off-sale set (an inactive spine lists 0 online)."""
    from api.services.online_stock_writeback import _sku_to_pid

    return sku in _sku_to_pid(_Conn(env["db"]), [sku])[1]


def test_restore_keeps_the_take_down_marker_so_no_sync_relists_it(env):
    from api.services.shopify_live_sync import select_dirty_products

    _archive(env, ["P1"])
    twin = env["db"].catalog_products.find_one({"id": "T-P1"})
    assert twin["ecom"]["status"] == "DRAFT" and twin["ecom"]["taken_down_at"]
    _restore(env, ["P1"])
    twin = env["db"].catalog_products.find_one({"id": "T-P1"})
    assert twin["ecom"]["taken_down_at"], "restore must keep the take-down marker"
    assert twin["ecom"]["status"] == "DRAFT"
    # Someone edits it afterwards: the 01:00 / 09:00 sync still leaves it off.
    env["db"].catalog_products.update_one({"id": "T-P1"}, {"$set": {"ecom.locally_modified": True}})
    dirty, skipped = select_dirty_products(env["db"])
    assert [d["id"] for d in dirty] == [] and skipped == 1


def test_restoring_a_size_on_a_live_listing_keeps_it_off_sale(env):
    _add_size_of_p1(env)
    _archive(env, ["P5", "P2"])
    assert env["variant_calls"] == ["T-P5"]  # the size, never the parent listing
    assert _off_sale_online(env, "SKU-P5")
    rows = {r["product_id"]: r for r in _restore(env, ["P5", "P2"]).json()["results"]}
    size = rows["P5"]
    assert size["status"] == "done" and size["switched_on"] is False
    assert "live on the website" in size["note"]
    spine = env["db"].products.find_one({"product_id": "P5"})
    assert spine["deleted_at"] is None and spine["is_active"] is False
    assert _off_sale_online(env, "SKU-P5"), "a restore never puts a size back on sale online"
    # A product on no live listing comes back switched on.
    assert rows["P2"]["switched_on"] is True and "note" not in rows["P2"]
    assert env["db"].products.find_one({"product_id": "P2"})["is_active"] is True


def test_a_failed_website_check_restores_switched_off(env, monkeypatch):
    from api.services.shopify_push import inventory

    _archive(env, ["P2"])

    def _dead(*a, **k):
        raise RuntimeError("catalog_products unavailable")

    monkeypatch.setattr(inventory, "skus_on_sale_online", _dead)
    row = _restore(env, ["P2"]).json()["results"][0]
    assert row["switched_on"] is False and "could not be checked" in row["note"]
    assert env["db"].products.find_one({"product_id": "P2"})["is_active"] is False


def test_the_dry_run_calls_a_failed_take_down_and_a_size_live(env):
    _add_size_of_p1(env)
    # An earlier take-down of P1 failed: Shopify still shows it.
    env["db"].catalog_products.update_one(
        {"id": "T-P1"}, {"$set": {"ecom.online_state": "DELIST_FAILED"}})
    rows = {r["product_id"]: r for r in _archive(env, ["P1", "P5", "P2"], apply=False).json()["results"]}
    assert rows["P1"]["live_online"] is True
    assert rows["P5"]["live_online"] is True  # its parent's listing is live
    assert rows["P2"]["live_online"] is False


def test_a_dead_website_read_never_says_nothing_is_live(env, monkeypatch):
    from api.services.shopify_push import inventory

    def _dead(*a, **k):
        raise RuntimeError("catalog_products unavailable")

    monkeypatch.setattr(inventory, "skus_on_sale_online", _dead)
    res = _archive(env, ["P1"], apply=False)
    assert res.status_code == 503
    assert "Could not check the website" in res.json()["detail"]


def _rows(user, **kw):
    params = {
        "category": None, "brand": None, "search": None, "match": None, "tag": None,
        "created_by": None, "store_id": None, "skip": 0, "limit": 50, "is_active": None,
        "photo": None, "sort": None, "frame_color": None, "lens_colour": None, "gender": None,
        "shape": None, "frame_type": None, "frame_material": None, "updated_by": None,
        "date_on": None, "date_from": None, "date_to": None, "flag": None,
        "current_user": user,
    }
    params.update(kw)
    out = asyncio.run(products_mod.list_products(**params))["products"]
    return {p["product_id"]: p for p in out}


def test_archive_and_restore_do_not_move_the_last_edit(env):
    # P3 was last edited by Akshay on 1 Sep, before edited_at existed (the
    # edit is on updated_at); P2 was never edited.
    db = env["db"]
    db.products.update_one({"product_id": "P3"}, {"$set": {
        "updated_by": "u-akshay", "updated_by_name": "akshay",
        "updated_at": datetime(2026, 9, 1, 6, 0)}})
    _archive(env, ["P3", "P2"])
    _restore(env, ["P3", "P2"])
    rows = _rows(ADMIN, sort="edited", is_active="true")
    assert rows["P3"]["last_edited_at"] == datetime(2026, 9, 1, 6, 0)
    assert rows["P3"]["updated_by_name"] == "Akshay Ovhal"
    assert rows["P2"]["last_edited_at"] == datetime(2026, 10, 1, 6, 0)  # its add date
    # Today in India: a restore that stamped "edited now" would land in it
    # at any hour (the box's UTC day missed it from 18:30 to midnight).
    today = ist_today().isoformat()
    assert _list(ADMIN, sort="edited", is_active="true", date_on="edited",
                 date_from=today, date_to=today) == []
    assert _list(ADMIN, sort="edited", is_active="true", updated_by="none",
                 date_on="edited", date_from=today, date_to=today) == []


def test_an_archived_product_is_never_flagged(env):
    _archive(env, ["P2"])
    res = _flag(env, ADMIN, ["P2"])
    assert res.json()["results"] == [{"product_id": "P2", "status": "refused", "reason": "Archived"}]
    assert res.json()["tasks"] == [] and _flags(env["db"], "P2") == []
    assert env["db"].tasks.count_documents({}) == 0


def test_cancelling_a_flag_task_withdraws_its_flags(env):
    _flag(env, ADMIN, ["P1"])
    (task,) = list(env["db"].tasks.find({}))
    res = env["call"](ADMIN, "PATCH", f"/api/v1/tasks/{task['task_id']}", json={"status": "CANCELLED"})
    assert res.status_code == 200, res.text
    assert env["db"].tasks.find_one({"task_id": task["task_id"]})["status"] == "CANCELLED"
    assert [f["status"] for f in _flags(env["db"], "P1")] == ["withdrawn"]
    # Nothing is left flagged with no live task, and it can be flagged again.
    again = _flag(env, ADMIN, ["P1"]).json()
    assert [r["status"] for r in again["results"]] == ["done"] and len(again["tasks"]) == 1
    assert env["db"].tasks.count_documents({"status": "OPEN"}) == 1


def _task_shape(tasks):
    return sorted((t["assigned_to"], t["assigned_to_name"], t["product_count"]) for t in tasks)


def test_the_flag_preview_is_the_same_rule_as_the_flag(env):
    # P4's adder has left: the server sends it to an active catalogue manager.
    body = {"product_ids": ["P4", "P1", "P3"], "type": "price_check", "note": "", "apply": False}
    preview = env["call"](ADMIN, "POST", "/api/v1/catalog/flags", json=body).json()
    assert env["db"].tasks.count_documents({}) == 0 and _flags(env["db"], "P4") == []
    assert all(t["assigned_to"] != "u-left" for t in preview["tasks"])
    real = env["call"](ADMIN, "POST", "/api/v1/catalog/flags", json=dict(body, apply=True)).json()
    assert _task_shape(preview["tasks"]) == _task_shape(real["tasks"])
    assert {t["assigned_to_name"] for t in real["tasks"]} <= {"Mansi Kumari", "Akshay Ovhal"}
    assert sum(t["product_count"] for t in real["tasks"]) == 3


def test_imported_rows_sort_on_the_dates_their_column_shows(env):
    db = env["db"]
    for pid, day in (("QA", 10), ("QB", 2), ("QC", 6)):
        db.products.insert_one(_spine(pid, "u-mansi", created_at=datetime(2026, 10, day, 6, 0),
                                      updated_at=datetime(2026, 10, day, 6, 0)))
        db.catalog_products.insert_one({"id": f"T-{pid}", "sku": f"SKU-{pid}",
                                        "title": f"Frame {pid}", "is_active": True})
    db.catalog_products.delete_many({"id": {"$in": ["T-P1", "T-P2"]}})
    rows = _imported(ADMIN, "newest")
    assert [r["id"] for r in rows] == ["T-QA", "T-QC", "T-QB"]
    assert [r["created_at"].day for r in rows] == [10, 6, 2]
    assert [r["id"] for r in _imported(ADMIN, "oldest")] == ["T-QB", "T-QC", "T-QA"]
    # Recently edited: QB, edited by a person on 11 Oct, comes first.
    db.products.update_one({"product_id": "QB"}, {"$set": {
        "updated_by": "u-akshay", "edited_at": datetime(2026, 10, 11, 6, 0)}})
    rows = _imported(ADMIN, "edited")
    assert [r["id"] for r in rows] == ["T-QB", "T-QA", "T-QC"]
    assert rows[0]["last_edited_at"] == datetime(2026, 10, 11, 6, 0)


def test_imported_rows_with_equal_keys_come_in_id_order(env):
    db = env["db"]
    db.catalog_products.delete_many({})
    for tid in ("T-Z3", "T-Z1", "T-Z2"):  # stored out of id order, one title, no spine
        db.catalog_products.insert_one({"id": tid, "sku": tid, "title": "Same", "is_active": True})
    for sort in ("name_asc", "name_desc", "newest", "edited"):
        assert [r["id"] for r in _imported(ADMIN, sort)] == ["T-Z1", "T-Z2", "T-Z3"], sort


# ---------------------------------------------------------------------------
# Who sees a flag task, who closes it (one rule: catalog_flags.clears_flags)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["SALES_STAFF", "OPTOMETRIST", "WORKSHOP_STAFF", "CASHIER"])
def test_counter_roles_never_open_list_or_cancel_a_flag_task(env, role):
    _flag(env, ADMIN, ["P1"])
    (task,) = list(env["db"].tasks.find({}))
    tid = task["task_id"]
    counter = _user("u-c", role)
    # Not one of theirs: the one task door answers 404 (task_visibility).
    res = env["call"](counter, "PATCH", f"/api/v1/tasks/{tid}", json={"status": "CANCELLED"})
    assert res.status_code == 404
    assert [f["status"] for f in _flags(env["db"], "P1")] == ["open"]
    assert env["db"].tasks.find_one({"task_id": tid})["status"] == "OPEN"
    assert env["call"](counter, "GET", f"/api/v1/tasks/{tid}").status_code == 404
    listed = env["call"](counter, "GET", "/api/v1/tasks").json()["tasks"]
    assert tid not in [t["task_id"] for t in listed]
    # The people who handle flags still see it.
    for user in (ADMIN, CAT_MGR, STORE_MGR):
        listed = env["call"](user, "GET", "/api/v1/tasks").json()["tasks"]
        assert tid in [t["task_id"] for t in listed]


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER"])
def test_closing_a_flag_task_takes_the_people_who_mark_fixed(env, role):
    _flag(env, ADMIN, ["P1"])
    (task,) = list(env["db"].tasks.find({}))
    tid = task["task_id"]
    mgr = _user("u-m", role)
    # A STORE_MANAGER raises flags, so sees the task but may not close it
    # (403); an AREA_MANAGER does not handle flags, so it is not found (404).
    want = 403 if role == "STORE_MANAGER" else 404
    res = env["call"](mgr, "PATCH", f"/api/v1/tasks/{tid}/complete",
                      json={"completion_notes": "done"})
    assert res.status_code == want
    assert [f["status"] for f in _flags(env["db"], "P1")] == ["open"]
    cancel = env["call"](mgr, "PATCH", f"/api/v1/tasks/{tid}", json={"status": "CANCELLED"})
    assert cancel.status_code == want
    assert [f["status"] for f in _flags(env["db"], "P1")] == ["open"]


# ---------------------------------------------------------------------------
# The archive dialog: what is on sale online, and a dead stock read
# ---------------------------------------------------------------------------


def test_the_dry_run_calls_a_live_listing_live_without_an_inventory_item(env):
    # T-P2 is PUBLISHED with a gid but IMS never recorded its inventory item:
    # the take-down still runs on it, so the dialog must say it is live.
    env["db"].catalog_products.update_one(
        {"id": "T-P2"},
        {"$set": {"ecom.status": "PUBLISHED",
                  "ecom.shopify_product_id": "gid://shopify/Product/778"}},
    )
    rows = {r["product_id"]: r for r in _archive(env, ["P2"], apply=False).json()["results"]}
    assert rows["P2"]["live_online"] is True
    _archive(env, ["P2"])
    assert [c["id"] for c in env["spy"].calls] == ["T-P2"]


def test_the_dry_run_calls_a_listing_with_a_shared_inventory_item_live(env):
    # T-P2 claims T-P1's inventory item: the stock writer sends neither, yet
    # both listings are up on the website.
    db = env["db"]
    ecom = dict(db.catalog_products.find_one({"id": "T-P1"})["ecom"])
    ecom["shopify_product_id"] = "gid://shopify/Product/779"
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"ecom": ecom}})
    rows = {r["product_id"]: r for r in _archive(env, ["P1", "P2"], apply=False).json()["results"]}
    assert rows["P1"]["live_online"] is True and rows["P2"]["live_online"] is True


def test_a_dead_stock_read_never_says_no_units_at_the_shops(env, monkeypatch):
    from api.services import item_events

    def _dead(*a, **k):
        raise RuntimeError("stock_units unavailable")

    monkeypatch.setattr(item_events, "on_hand_match", _dead)
    res = _archive(env, ["P1"], apply=False)
    assert res.status_code == 503
    assert "Could not check the stock at the shops" in res.json()["detail"]


# ---------------------------------------------------------------------------
# Counter roles never read attribution or flags, by any product door
# ---------------------------------------------------------------------------


_PRIVATE = ("created_by", "created_by_name", "updated_by", "updated_by_name", "deleted_by",
            "flags")


@pytest.mark.parametrize("role", ["CASHIER", "SALES_STAFF"])
def test_no_product_door_hands_a_counter_role_attribution_or_flags(env, role):
    _flag(env, ADMIN, ["P1"], note="MRP looks low")
    env["db"].products.update_one({"product_id": "P1"}, {"$set": {"updated_by": "u-mansi"}})
    _archive(env, ["P2"])
    counter = _user("u-c", role)
    docs = list(_rows(counter).values())  # the legacy default path lists archived rows too
    docs.append(asyncio.run(products_mod.get_product("P1", current_user=counter)))
    docs.append(asyncio.run(products_mod.get_product_by_sku("SKU-P1", current_user=counter)))
    docs.append(asyncio.run(products_mod.get_product("P2", current_user=counter)))
    assert {d["product_id"] for d in docs} >= {"P1", "P2"}
    for d in docs:
        assert not [f for f in _PRIVATE if f in d], d["product_id"]
    # The cataloguer ladder still reads them.
    admin_p1 = asyncio.run(products_mod.get_product("P1", current_user=ADMIN))
    assert admin_p1["flags"][0]["note"] == "MRP looks low"
    assert admin_p1["created_by"] == "u-mansi"
    assert asyncio.run(products_mod.get_product("P2", current_user=ADMIN))["deleted_by"] == "u-admin"


# ---------------------------------------------------------------------------
# The review list (imports with no spine yet): its own dates and edits
# ---------------------------------------------------------------------------


def _three_imports(db):
    db.catalog_products.delete_many({})
    for tid, day in (("T-A", 1), ("T-B", 9), ("T-C", 5)):
        db.catalog_products.insert_one({
            "id": tid, "sku": f"IMP-{tid}", "title": f"Import {tid}", "needs_review": True,
            "is_active": True, "migrated_at": datetime(2026, 10, day, 6, 0),
        })


def test_imports_with_no_spine_sort_on_their_import_date(env):
    _three_imports(env["db"])
    legacy = [r["id"] for r in _imported(ADMIN, None)]
    assert legacy == ["T-B", "T-C", "T-A"]
    assert [r["id"] for r in _imported(ADMIN, "newest")] == legacy
    assert [r["id"] for r in _imported(ADMIN, "edited")] == legacy  # none edited yet
    rows = _imported(ADMIN, "oldest")
    assert [r["id"] for r in rows] == ["T-A", "T-C", "T-B"]
    assert [r["created_at"].day for r in rows] == [1, 5, 9]  # the Added column's day
    assert all(r["created_by"] is None for r in rows)  # who added it is never guessed


def test_an_edit_saved_on_an_import_is_its_last_edit(env):
    _three_imports(env["db"])
    res = env["call"](CAT_MGR, "PUT", "/api/v1/catalog/products/T-A", json={"name": "Import A fixed"})
    assert res.status_code == 200, res.text
    rows = _imported(ADMIN, "edited")
    assert [r["id"] for r in rows] == ["T-A", "T-B", "T-C"]
    assert rows[0]["updated_by"] == "u-mansi" and rows[0]["updated_by_name"] == "Mansi Kumari"
    assert rows[0]["last_edited_at"] is not None


def test_a_copy_edit_on_a_twin_reaches_its_spine(env):
    res = env["call"](CAT_MGR, "PUT", "/api/v1/catalog/products/T-P2",
                      json={"name": "Frame P2 renamed"})
    assert res.status_code == 200, res.text
    spine = env["db"].products.find_one({"product_id": "P2"})
    assert spine["updated_by"] == "u-mansi" and spine["edited_at"] is not None
    rows = {r["id"]: r for r in _imported(ADMIN, "edited")}
    assert rows["T-P2"]["updated_by"] == "u-mansi"


# ---------------------------------------------------------------------------
# Round 4: one way back from an archive; restore puts it back as it was; a
# flag task goes only to someone who can open and close it, and stays there
# ---------------------------------------------------------------------------


def _switch_on_by_every_door(env, twin_id, spine_id):
    """PUT is_active true through each edit door; the status codes."""
    from fastapi import HTTPException

    from api.routers import product_master as pm_router
    from api.routers.product_master import ProductMasterUpdate
    from api.routers.products import ProductUpdate

    codes = [env["call"](CAT_MGR, "PUT", f"/api/v1/catalog/products/{twin_id}",
                         json={"is_active": True}).status_code]
    for door in (
        lambda: products_mod.update_product(spine_id, ProductUpdate(is_active=True), current_user=ADMIN),
        lambda: pm_router.update_master_product(
            spine_id, ProductMasterUpdate(is_active=True), current_user=ADMIN),
    ):
        try:
            asyncio.run(door())
            codes.append(200)
        except HTTPException as exc:
            codes.append(exc.status_code)
            assert exc.detail == "This product is archived - use Restore"
    return codes


def test_only_restore_brings_an_archived_product_back(env, monkeypatch):
    from api.routers import product_master as pm_router

    monkeypatch.setattr(pm_router, "get_product_repository",
                        lambda: ProductRepository(env["db"].products))
    _archive(env, ["P1"])
    res = env["call"](CAT_MGR, "PUT", "/api/v1/catalog/products/T-P1", json={"is_active": True})
    assert res.status_code == 409 and res.json()["detail"] == "This product is archived - use Restore"
    assert _switch_on_by_every_door(env, "T-P1", "P1") == [409, 409, 409]
    spine = env["db"].products.find_one({"product_id": "P1"})
    assert spine["is_active"] is False and spine["deleted_at"]
    assert env["db"].catalog_products.find_one({"id": "T-P1"})["is_active"] is False
    assert "P1" not in _list(_user("u-c", "CASHIER"), search="Carrera")  # the till
    # An archived spine whose twin was never stamped is refused through the twin too.
    env["db"].catalog_products.update_one({"id": "T-P1"}, {"$set": {"deleted_at": None}})
    assert env["call"](CAT_MGR, "PUT", "/api/v1/catalog/products/T-P1",
                       json={"is_active": True}).status_code == 409
    # Restore is the way back; then the switch is an ordinary edit again.
    assert _restore(env, ["P1"]).json()["results"][0]["status"] == "done"
    assert env["db"].products.find_one({"product_id": "P1"})["is_active"] is True
    # Switching OFF an archived product, and switching on one that is merely
    # off (the drawer's Not-for-POS switch), stay ordinary edits.
    _archive(env, ["P2"])
    assert env["call"](CAT_MGR, "PUT", "/api/v1/catalog/products/T-P2",
                       json={"is_active": False}).status_code == 200
    env["db"].products.update_one({"product_id": "P3"}, {"$set": {"is_active": False}})
    asyncio.run(products_mod.update_product(
        "P3", products_mod.ProductUpdate(is_active=True), current_user=ADMIN))
    assert env["db"].products.find_one({"product_id": "P3"})["is_active"] is True


def test_restore_puts_a_product_back_exactly_as_it_was(env):
    db = env["db"]
    # P2 was switched off before it was archived (a provisional PO buy is
    # born inactive); P1 was on sale.
    db.products.update_one({"product_id": "P2"}, {"$set": {"is_active": False}})
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"is_active": False}})
    _archive(env, ["P1", "P2"])
    rows = {r["product_id"]: r for r in _restore(env, ["P1", "P2"]).json()["results"]}
    assert rows["P1"]["switched_on"] is True and "note" not in rows["P1"]
    assert rows["P2"]["switched_on"] is False
    assert rows["P2"]["note"] == "Back in the catalogue switched off, as it was before it was archived."
    p2 = db.products.find_one({"product_id": "P2"})
    assert p2["is_active"] is False and p2["deleted_at"] is None
    assert not p2.get("active_before_archive")
    assert db.catalog_products.find_one({"id": "T-P2"})["is_active"] is False
    assert db.products.find_one({"product_id": "P1"})["is_active"] is True
    assert "P2" not in _list(_user("u-c", "CASHIER"), search="Carrera")  # never on sale
    (audit,) = [a for a in db.audit_logs.find({"entity_id": "P2"})
                if a.get("action") == "product.restored"]
    assert audit["switched_on"] is False
    # Deleting it again while archived (now switched off) never forgets
    # that it was on sale.
    _archive(env, ["P1"])
    asyncio.run(catalog_mod.delete_catalog_product("T-P1", current_user=ADMIN))
    assert _restore(env, ["P1"]).json()["results"][0]["switched_on"] is True
    # A product archived before this was remembered comes back on, as before.
    db.products.update_one({"product_id": "P3"}, {"$set": {
        "is_active": False, "deleted_at": "2026-10-01T00:00:00+00:00"}})
    assert _restore(env, ["P3"]).json()["results"][0]["switched_on"] is True
    assert db.products.find_one({"product_id": "P3"})["is_active"] is True


def test_the_review_drawers_restore_is_the_restore_door(env):
    db = env["db"]
    # An import with no spine, archived by the single Delete door.
    db.catalog_products.insert_one({"id": "T-IMP", "sku": "IMP-1", "title": "Imported",
                                    "needs_review": True, "is_active": True})
    asyncio.run(catalog_mod.delete_catalog_product("T-IMP", current_user=ADMIN))
    assert db.catalog_products.find_one({"id": "T-IMP"})["deleted_at"]
    assert env["call"](ADMIN, "PUT", "/api/v1/catalog/products/T-IMP",
                       json={"is_active": True}).status_code == 409
    row = _restore(env, ["T-IMP"]).json()["results"][0]
    assert (row["status"], row["switched_on"]) == ("done", True)
    twin = db.catalog_products.find_one({"id": "T-IMP"})
    assert twin["is_active"] is True and twin["deleted_at"] is None
    # A twin id with a spine restores the spine too (the till's truth).
    _archive(env, ["P2"])
    assert _restore(env, ["T-P2"]).json()["results"][0]["status"] == "done"
    p2 = db.products.find_one({"product_id": "P2"})
    assert p2["deleted_at"] is None and p2["is_active"] is True


def test_approving_an_archived_import_is_refused_only_restore_brings_it_back(env):
    db = env["db"]
    db.catalog_products.insert_one({
        "id": "T-IMP", "sku": "IMP-1", "title": "Vogue VO5051", "name": "Vogue VO5051",
        "brand": "Vogue", "category": "FRAME", "hsn_code": "900311", "gst_rate": 5.0,
        "mrp": 5000.0, "offer_price": 4500.0, "pricing": {"mrp": 5000.0, "offer_price": 4500.0},
        "attributes": {"brand_name": "Vogue", "model_no": "VO5051", "colour_code": "BLK"},
        "is_active": True, "needs_review": True, "source": "bvi_import",
    })

    def promote(dry):
        return env["call"](CAT_MGR, "POST", "/api/v1/catalog/products/T-IMP/promote",
                           params={"dry_run": str(dry).lower()})

    ok = promote(True)
    assert ok.status_code == 200 and ok.json()["ok"] is True, ok.text  # approvable as it stands
    asyncio.run(catalog_mod.delete_catalog_product("T-IMP", current_user=ADMIN))
    for dry in (True, False):
        res = promote(dry)
        assert res.status_code == 409 and res.json()["detail"] == "This product is archived - use Restore"
    assert db.products.find_one({"product_id": "T-IMP"}) is None  # nothing on sale at the till
    assert "T-IMP" not in _list(_user("u-c", "CASHIER"), search="Vogue")
    # Restore is the way back, and then it can be approved.
    assert _restore(env, ["T-IMP"]).json()["results"][0]["status"] == "done"
    assert promote(True).json()["ok"] is True


# ---------------------------------------------------------------------------
# THE twin -> spine rule: the Added column reads the spine every door acts on
# ---------------------------------------------------------------------------


def test_an_imported_row_and_every_door_find_the_same_spine(env):
    db = env["db"]
    db.catalog_products.delete_many({})
    db.products.delete_many({})
    db.products.insert_many([
        # T-X: spine A links T-X as its pim_product_id (the create door's
        # link); spine B only shares T-X's sku. THE rule picks A, both ways.
        _spine("A", "u-akshay", pim_product_id="T-X", sku="SKU-A"),
        _spine("B", "u-mansi", pim_product_id="other", sku="SKU-X"),
        # T-Y: a spine shares its id, another has its sku. The id wins.
        _spine("T-Y", "u-akshay", pim_product_id=None, sku="SKU-Y2"),
        _spine("D", "u-mansi", pim_product_id=None, sku="SKU-Y"),
    ])
    db.catalog_products.insert_many([
        {"id": "T-X", "sku": "SKU-X", "title": "X", "needs_review": True, "is_active": True},
        {"id": "T-Y", "sku": "SKU-Y", "title": "Y", "needs_review": True, "is_active": True},
    ])
    rows = {r["id"]: r for r in _imported(ADMIN, "name_asc")}
    assert (rows["T-X"]["created_by"], rows["T-X"]["created_by_name"]) == ("u-akshay", "Akshay Ovhal")
    assert (rows["T-Y"]["created_by"], rows["T-Y"]["created_by_name"]) == ("u-akshay", "Akshay Ovhal")
    # The edit door acts on that same spine, and the column then shows its edit.
    res = env["call"](CAT_MGR, "PUT", "/api/v1/catalog/products/T-X", json={"name": "X renamed"})
    assert res.status_code == 200, res.text
    assert db.products.find_one({"product_id": "A"})["updated_by"] == "u-mansi"
    assert not db.products.find_one({"product_id": "B"}).get("updated_by")
    assert _imported(ADMIN, "edited")[0]["id"] == "T-X"

    def deleted(pid):
        doc = db.products.find_one({"product_id": pid}) or db.catalog_products.find_one({"id": pid})
        return bool(doc.get("deleted_at"))

    # The Catalog page's archive reads the rule the other way: B has no twin
    # (T-X's spine is A), so archiving B archives B alone ...
    assert _archive(env, ["B"]).json()["results"][0]["status"] == "done"
    assert (deleted("B"), deleted("T-X"), deleted("A")) == (True, False, False)
    # ... and archiving A archives A's own twin and spine.
    assert _archive(env, ["A"]).json()["results"][0]["status"] == "done"
    assert (deleted("A"), deleted("T-X")) == (True, True)
    # Restoring B brings back B alone: T-X is A's.
    assert _restore(env, ["B"]).json()["results"][0]["status"] == "done"
    assert (deleted("B"), deleted("A"), deleted("T-X")) == (False, True, True)
    # The Needs-review drawer's Restore on T-X restores exactly what A's
    # archive archived.
    row = _restore(env, ["T-X"]).json()["results"][0]
    assert row["status"] == "done", row
    assert (deleted("A"), deleted("T-X")) == (False, False)
    # The single Delete door on T-X archives A, never B.
    asyncio.run(catalog_mod.delete_catalog_product("T-X", current_user=ADMIN))
    assert (deleted("A"), deleted("T-X"), deleted("B")) == (True, True, False)


@pytest.mark.parametrize("role", ["AREA_MANAGER", "ACCOUNTANT", "STORE_MANAGER", "SALES_STAFF"])
def test_a_flag_task_goes_only_to_someone_who_can_open_and_close_it(env, role):
    db = env["db"]
    # Bought on a PO by someone who cannot clear flags: the PO door stamps
    # them as the adder.
    db.users.insert_one({"user_id": "u-buyer", "full_name": "Buyer", "roles": [role],
                         "is_active": True})
    db.products.insert_one(_spine("P9", "u-buyer", is_active=False))
    db.products.insert_one(_spine("P8", "u-admin"))
    body = _flag(env, ADMIN, ["P9", "P8"]).json()
    people = {t["assigned_to"] for t in body["tasks"]}
    assert len(people) == 2 and "u-admin" in people  # an adder who clears flags keeps theirs
    assert people - {"u-admin"} <= {"u-mansi", "u-akshay"}
    (task,) = [t for t in db.tasks.find({}) if "P9" in t["payload"]["product_ids"]]
    who = _user(task["assigned_to"], "CATALOG_MANAGER")
    assert env["call"](who, "GET", f"/api/v1/tasks/{task['task_id']}").status_code == 200
    res = env["call"](who, "PATCH", f"/api/v1/tasks/{task['task_id']}/complete",
                      json={"completion_notes": "Fixed"})
    assert res.status_code == 200, res.text
    assert _flags(db, "P9")[0]["status"] == "fixed"


def test_a_flag_task_never_climbs_the_escalation_ladder(env):
    from datetime import timedelta

    from api.services.task_sla import should_escalate

    db = env["db"]
    db.users.insert_one({"user_id": "u-area", "full_name": "Area", "roles": ["AREA_MANAGER"],
                         "is_active": True})
    _flag(env, ADMIN, ["P1"])
    (task,) = list(db.tasks.find({}))
    tid, owner = task["task_id"], task["assigned_to"]
    later = datetime.now() + timedelta(days=30)
    assert should_escalate(task, now=later) == (False, "")
    # Unacknowledged for days: the SLA scan leaves it with its cataloguer.
    db.tasks.update_one({"task_id": tid},
                        {"$set": {"created_at": datetime.now() - timedelta(days=5)}})
    res = env["call"](ADMIN, "POST", "/api/v1/tasks/auto-escalate-overdue")
    assert res.status_code == 200, res.text
    after = db.tasks.find_one({"task_id": tid})
    assert (after["status"], after["assigned_to"]) == ("OPEN", owner)
    res = env["call"](ADMIN, "POST", f"/api/v1/tasks/{tid}/escalate")
    assert res.status_code == 400 and "not escalated" in res.json()["detail"]
    assert db.tasks.find_one({"task_id": tid})["assigned_to"] == owner
    # Any other task still climbs: the rule is the flag task's alone.
    other = dict(task, payload={"kind": "something_else"})
    assert should_escalate(other, now=later)[0] is True
