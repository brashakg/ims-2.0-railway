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
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.task_repository import TaskRepository  # noqa: E402
from database.repositories.user_repository import UserRepository  # noqa: E402

GID = "gid://shopify/Product/777"


def _user(uid, *roles, name=None):
    return {"user_id": uid, "username": uid, "full_name": name or uid, "roles": list(roles)}


ADMIN = _user("u-admin", "ADMIN", name="Avinash")
STORE_MGR = _user("u-sm", "STORE_MANAGER", name="Ravi")
CAT_MGR = _user("u-mansi", "CATALOG_MANAGER", name="Mansi Kumari")


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]


class _DelistSpy:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, product):
        self.calls.append(copy.deepcopy(product))
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
         "ecom": {"status": "PUBLISHED", "shopify_product_id": GID, "locally_modified": False}},
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

    return {"db": db, "call": call, "spy": spy}


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
    assert "Note from Avinash: Front photo is 3rd" in mansi["description"]
    assert [link["to"] for link in mansi["links"]] == [
        "/catalog/add?edit=P1", "/catalog/add?edit=P2"]
    assert mansi["store_id"] is None and mansi["status"] == "OPEN"
    # Each flag carries its task.
    for pid, person in (("P1", "u-mansi"), ("P2", "u-mansi"), ("P3", "u-akshay")):
        (flag,) = _flags(env["db"], pid)
        assert flag["status"] == "open" and flag["type"] == "photo_redo"
        assert flag["task_id"] == by_person[person]["task_id"]
        assert flag["by"] == "u-admin" and flag["by_name"] == "Avinash"
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
