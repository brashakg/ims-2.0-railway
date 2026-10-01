"""Per-shop reorder levels - audit row F73, owner ruling D12 (2026-09-29).

The audit (F73): every product carried the form's default level of 5, and the
level was CHAIN-WIDE, so one frame sent to Bokaro showed "Low Stock" there.
The owner (D12): reorder points are PER SHOP. -1 (or missing) = NOT SET = no
low-stock alert; screens say "not set", never -1. A shop without its own level
has none -- the old chain-wide `reorder_point` is not a default for it.

Every test below reproduced one finding on main (committed as strict xfail
first); the fix made them pass.

THE CONTRACT the fix builds to (the only names these tests pin):
  * storage   `products.reorder_levels` = {<store_id>: int}; -1 / absent = not
              set. `_levels()` below is the one place that shape is written.
  * the rule  reorder_policy.reorder_level(product, *, store_id) -> int | None
              reorder_policy.is_low_stock(product, on_hand, *, store_id) -> bool
              The shop is REQUIRED (keyword-only): a reader that forgets it
              raises TypeError instead of silently reading a chain value.
  * the write PUT /api/v1/inventory/reorder-levels/{product_id}
              {"store_id": ..., "level": int | null}; null or -1 clears.
              STORE_MANAGER / AREA_MANAGER: their own shops; ADMIN/SUPERADMIN:
              any shop; everyone else 403. `_set_level()` is the one adapter.
  * migrate   scripts/migrate_reorder_levels_per_shop.py: plan(db) -> rows,
              apply(db, rows) -> counts, parse_args([]) is a dry run.

The world (the audit's own case): P-FRAME has its level typed for Dhanbad only
(2) and still carries the old form default `reorder_point: 5`; P-OWNER carries a
chain-wide 3 the owner typed before D12 (not yet migrated) and an explicit -1 at
Bokaro. One unit of each sits at Dhanbad and at Bokaro; Pune holds 15 of each
(a transfer donor). Every reader must say: Dhanbad -> P-FRAME is low (1 <= 2);
Bokaro and Pune -> nothing is low.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_per_shop_reorder_levels.py -q
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.dependencies as deps  # noqa: E402
import database.connection as dbconn  # noqa: E402
from api.routers import (  # noqa: E402
    analytics_router,
    catalog_router,
    dashboard_widgets_router,
    inventory_router,
    reports_router,
)
from api.routers.auth import get_current_user  # noqa: E402

DHN, BOK, PUN = "BV-DHN-02", "BV-BOK-01", "BV-PUN-01"

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"],
          "active_store_id": DHN, "store_ids": [DHN]}
_SUPER = {"user_id": "u-super", "username": "owner", "roles": ["SUPERADMIN"],
          "active_store_id": DHN, "store_ids": [DHN]}
_DHN_MGR = {"user_id": "m-dhn", "username": "dhn", "roles": ["STORE_MANAGER"],
            "active_store_id": DHN, "store_ids": [DHN]}
_BOK_MGR = {"user_id": "m-bok", "username": "bok", "roles": ["STORE_MANAGER"],
            "active_store_id": BOK, "store_ids": [BOK]}
_DHN_SALES = {"user_id": "s-dhn", "username": "sales", "roles": ["SALES_STAFF"],
              "active_store_id": DHN, "store_ids": [DHN]}


def _levels(by_store):
    """THE storage shape of a per-shop level (one place to change it)."""
    return {"reorder_levels": dict(by_store)}


def _product(pid, **extra):
    base = {
        "product_id": pid, "sku": f"SKU-{pid}", "barcode": f"SKU-{pid}",
        "name": f"Carrera {pid}", "brand": "Carrera", "category": "FRAME",
        "mrp": 9000.0, "offer_price": 9000.0, "cost_price": 4000.0,
        "is_active": True, "preferred_vendor_id": "V1",
        # auto-reorder ON (a positive qty): only the LEVEL decides below.
        # No `stock_quantity`: nothing maintains it on a current product (only
        # the TechCherry import wrote it), so on-hand per shop comes from
        # stock_units, as it does in production.
        "reorder_quantity": 2,
    }
    base.update(extra)
    return base


def _units(pid, store, n, status="AVAILABLE"):
    return [
        {"stock_id": f"U-{pid}-{store}-{i}", "product_id": pid, "sku": f"SKU-{pid}",
         "store_id": store, "status": status, "quantity": 1,
         "barcode": f"BC{pid}{store}{i}".replace("-", "")}
        for i in range(n)
    ]


def _orders(pid, store, units, days_ago=1):
    when = datetime.utcnow() - timedelta(days=days_ago)
    return [
        {"order_id": f"O-{pid}-{store}-{i}", "store_id": store, "status": "DELIVERED",
         "created_at": when,
         "items": [{"product_id": pid, "barcode": f"SKU-{pid}", "sku": f"SKU-{pid}",
                    "quantity": 1, "unit_price": 9000.0, "item_total": 9000.0,
                    "product_name": f"Carrera {pid}", "brand": "Carrera",
                    "category": "FRAME"}]}
        for i in range(units)
    ]


def _world():
    db = mongomock.MongoClient().db
    db.products.insert_many([
        _product("P-FRAME", reorder_point=5, **_levels({DHN: 2})),
        _product("P-OWNER", reorder_point=3, **_levels({BOK: -1})),
    ])
    db.stock_units.insert_many(
        _units("P-FRAME", DHN, 1) + _units("P-FRAME", BOK, 1) + _units("P-FRAME", PUN, 15)
        + _units("P-OWNER", DHN, 1) + _units("P-OWNER", BOK, 1) + _units("P-OWNER", PUN, 15)
    )
    db.orders.insert_many(
        _orders("P-FRAME", DHN, 10) + _orders("P-FRAME", BOK, 10)
        + _orders("P-OWNER", DHN, 10) + _orders("P-OWNER", BOK, 10)
    )
    return db


class _Conn:
    """What database.connection.get_db() hands out, over a mongomock db."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        return self.db[name]


@pytest.fixture
def world(monkeypatch):
    db = _world()
    conn = _Conn(db)
    originals = {deps.get_db, dbconn.get_db}
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(dbconn, "get_db", lambda: conn)
    # Every module that bound get_db by name at import time.
    for name, mod in list(sys.modules.items()):
        if (name.startswith(("api.", "agents.")) and mod is not None
                and getattr(mod, "get_db", None) in originals):
            monkeypatch.setattr(mod, "get_db", lambda: conn)
    from api.routers import jarvis
    monkeypatch.setattr(jarvis, "get_db_collection", lambda n: db[n])
    return db


def _client(user):
    app = FastAPI()
    app.include_router(dashboard_widgets_router, prefix="/api/v1")
    app.include_router(inventory_router, prefix="/api/v1/inventory")
    app.include_router(reports_router, prefix="/api/v1/reports")
    app.include_router(analytics_router, prefix="/api/v1/analytics")
    app.include_router(catalog_router, prefix="/api/v1/catalog")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _get(path, user=_ADMIN, **params):
    res = _client(user).get(path, params=params)
    assert res.status_code == 200, (path, res.status_code, res.text[:300])
    return res.json()


def _set_level(user, product_id, store_id, level):
    """THE write adapter (one place naming the route)."""
    return _client(user).put(
        f"/api/v1/inventory/reorder-levels/{product_id}",
        json={"store_id": store_id, "level": level},
    )


def _leaves(obj):
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _leaves(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in _leaves(v)]
    return [obj]


def _ids(rows):
    return {str(r.get("_id") or r.get("product_id")) for r in rows}


def _find(obj, key):
    """First value under `key` anywhere in a JSON response."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = _find(v, key)
            if got is not None:
                return got
    if isinstance(obj, list):
        for v in obj:
            got = _find(v, key)
            if got is not None:
                return got
    return None


# ---------------------------------------------------------------------------
# 1. The one rule, with the shop given
# ---------------------------------------------------------------------------


def test_the_rule_reads_the_shops_own_level_and_nothing_else():
    from api.services.reorder_policy import is_low_stock, reorder_level

    p = {"reorder_point": 5, **_levels({DHN: 2, PUN: -1, "BV-RAN-01": "x"})}
    assert reorder_level(p, store_id=DHN) == 2
    assert reorder_level(p, store_id=BOK) is None  # no chain default
    assert reorder_level(p, store_id=PUN) is None  # -1 = not set, never -1
    assert reorder_level(p, store_id="BV-RAN-01") is None  # garbage = not set
    assert reorder_level(p, store_id=None) is None
    assert reorder_level({"reorder_point": 3}, store_id=DHN) is None  # chain value ignored
    assert reorder_level({}, store_id=DHN) is None  # missing = not set
    assert reorder_level(_levels({DHN: 0}), store_id=DHN) == 0  # 0 is a typed level
    assert is_low_stock(p, 2, store_id=DHN) and not is_low_stock(p, 3, store_id=DHN)
    assert not is_low_stock(p, 0, store_id=BOK)  # not set: no alert even at 0
    with pytest.raises(TypeError):
        reorder_level(p)  # the shop is never optional


# ---------------------------------------------------------------------------
# 2. Every low-stock reader asks the rule for THIS shop
# ---------------------------------------------------------------------------


def test_low_stock_list_is_per_shop(world):
    dhn = _get("/api/v1/inventory/low-stock", store_id=DHN)["items"]
    bok = _get("/api/v1/inventory/low-stock", store_id=BOK)["items"]
    assert _ids(dhn) == {"P-FRAME"}
    assert [r.get("reorder_point") for r in dhn] == [2]
    assert bok == []  # the audit's Bokaro frame


def test_stock_low_stock_mode_is_per_shop(world):
    dhn = _get("/api/v1/inventory/stock", store_id=DHN, low_stock="true")["items"]
    bok = _get("/api/v1/inventory/stock", store_id=BOK, low_stock="true")["items"]
    assert _ids(dhn) == {"P-FRAME"}
    assert bok == []


def test_stock_ledger_row_carries_this_shops_level_and_verdict(world):
    def rows(store):
        items = _get("/api/v1/inventory/stock", store_id=store)["items"]
        return {r["product_id"]: (r.get("reorder_point"), bool(r.get("low_stock")))
                for r in items if r["product_id"] in ("P-FRAME", "P-OWNER")}

    assert rows(DHN) == {"P-FRAME": (2, True), "P-OWNER": (None, False)}
    # Bokaro: no level typed there; P-OWNER's -1 reads 'not set', never -1.
    assert rows(BOK) == {"P-FRAME": (None, False), "P-OWNER": (None, False)}


def test_stock_alerts_are_per_shop(world):
    def alerts(store):
        return {a["sku"]: a for a in _get("/api/v1/inventory/alerts", store_id=store)["alerts"]}

    dhn, bok = alerts(DHN), alerts(BOK)
    frame = dhn.get("SKU-P-FRAME") or {}
    assert frame.get("alertType") in ("LOW_STOCK", "REORDER_ALERT")
    assert (frame.get("reorderPoint"), frame.get("currentStock")) == (2, 1)
    others = [a for sku, a in dhn.items() if sku != "SKU-P-FRAME"] + list(bok.values())
    for a in others:  # no level at that shop: no low/reorder alert, 'not set'
        assert a["alertType"] not in ("LOW_STOCK", "REORDER_ALERT"), a
        assert a.get("reorderPoint") is None, a


def test_transfer_recommendations_only_refill_a_shop_that_has_a_level(world):
    def recs(store):
        res = _get("/api/v1/inventory/transfer-recommendations", store_id=store, threshold=5)
        return {(r["product_id"], r["from_store"]) for r in res.get("recommendations") or []}

    assert recs(DHN) == {("P-FRAME", PUN)}  # low at its own level; Pune has surplus
    assert recs(BOK) == set()  # no level at Bokaro: nothing to refill


def test_report_low_stock_counts_are_per_shop(world):
    for store, want in ((DHN, 1), (BOK, 0)):
        summary = _get("/api/v1/reports/inventory/summary", store_id=store)
        assert summary["summary"]["low_stock_count"] == want, store
        assert _find(_get("/api/v1/reports/dashboard", store_id=store), "lowStockItems") == want, store
        assert _get("/api/v1/reports/inventory", store_id=store)["lowStock"] == want, store


def test_analytics_low_stock_counts_are_per_shop(world):
    for store, want in ((DHN, 1), (BOK, 0)):
        summary = _get("/api/v1/analytics/dashboard-summary", store_id=store, period="month")
        intel = _get("/api/v1/analytics/inventory-intelligence", store_id=store)
        kpis = _get("/api/v1/analytics/enterprise-kpis", store_id=store, period="month")
        assert _find(summary, "low_stock_items") == want, store
        assert intel["low_stock"]["count"] == want, store
        assert _find(kpis, "low_stock_items") == want, store


def test_dashboard_widgets_are_per_shop(world):
    for store, want in ((DHN, 1), (BOK, 0)):
        status = _get("/api/v1/inventory/stock-count-status", store_id=store)
        assert status["low_stock"] == want, store
    digest = _get("/api/v1/admin/owner-digest", user=_SUPER)
    assert digest["today"]["low_stock"] == 1
    items = digest["expanded"]["low_stock_items"]
    assert [(i.get("sku"), i.get("store_id"), i.get("reorder_point")) for i in items] == [
        ("SKU-P-FRAME", DHN, 2)
    ]
    bok = _get("/api/v1/admin/owner-digest", user=_SUPER, store_id=BOK)
    assert (bok["today"]["low_stock"], bok["expanded"]["low_stock_items"]) == (0, [])


def test_jarvis_low_stock_is_per_shop(world):
    from api.routers import jarvis

    overview = jarvis.JarvisAnalyticsEngine._compute_overview_live()
    assert overview["inventory"]["low_stock_items"] == 1
    inv = jarvis.JarvisAnalyticsEngine._compute_inventory_live() or {}
    lows = [(a.get("sku"), a.get("reorder_point"))
            for a in inv.get("critical_alerts") or [] if a.get("type") == "low_stock"]
    assert (inv.get("totals") or {}).get("low_stock") == 1 and lows == [("SKU-P-FRAME", 2)]
    ctx = jarvis.JarvisAnalyticsEngine.get_extended_context()
    listed = [(r.get("name"), r.get("store_id"), r.get("reorder_point"))
              for r in ctx.get("low_stock_value_at_risk") or []]
    assert listed == [("Carrera P-FRAME", DHN, 2)]


def test_taskmaster_never_drafts_a_reorder(world):
    """TASKMASTER's chain-wide scan (a reorder_point on a unit row, drafts with
    no shop) is deleted: ORACLE is the one reorder engine, per product AND shop."""
    from agents.implementations.taskmaster import TaskmasterAgent

    agent = TaskmasterAgent(db=world)
    assert not hasattr(agent, "_draft_reorders")
    world.stock_units.update_many({"store_id": BOK}, {"$set": {"reorder_point": 5}})
    asyncio.run(agent.on_event("stock.below_reorder", {"sku": "SKU-P-FRAME"}))
    assert list(world.purchase_orders.find({})) == []


def test_oracle_proposals_carry_the_shops_level(world):
    from agents.implementations.oracle import OracleAgent

    asyncio.run(OracleAgent(db=world)._propose_reorders())
    got = {(p["payload"]["product_id"], p["payload"]["store_id"]): p["payload"].get("reorder_point")
           for p in world.ai_proposals.find({"type": "draft_po"})}
    assert got[("P-FRAME", DHN)] == 2
    assert got[("P-FRAME", BOK)] in (None, 0)
    assert got[("P-OWNER", DHN)] in (None, 0)


def test_purchase_recommendations_use_the_shops_level(world):
    def recs(store):
        res = _get("/api/v1/reports/purchase/recommendations", store_id=store, min_velocity=2)
        return {r["product_id"]: r["reorder_point"] for r in res["recommendations"]}

    assert recs(DHN) == {"P-FRAME": 2, "P-OWNER": None}
    assert recs(BOK) == {"P-FRAME": None, "P-OWNER": None}


def test_catalog_inventory_never_says_needs_reorder_on_a_chain_default(world):
    world.catalog_products.insert_one({
        "id": "C-FRAME", "sku": "SKU-P-FRAME", "title": "Carrera P-FRAME",
        "inventory": {"total_quantity": 2, "locations": {DHN: 1, BOK: 1},
                      "reorder_level": 5, "reorder_quantity": -1},
    })
    res = _client(_ADMIN).get("/api/v1/catalog/products/C-FRAME/inventory")
    # Deleting this unused, chain-wide reader is also a fix (404).
    assert res.status_code == 404 or res.json().get("needs_reorder") is not True


# ---------------------------------------------------------------------------
# 3. A manager sets their own shop's level; admins any shop
# ---------------------------------------------------------------------------


def _levels_of(world, pid):
    return (world.products.find_one({"product_id": pid}) or {}).get("reorder_levels") or {}


def test_store_manager_sets_their_own_shops_level_only(world):
    res = _set_level(_BOK_MGR, "P-FRAME", BOK, 1)
    assert res.status_code == 200, res.text
    assert _levels_of(world, "P-FRAME") == {DHN: 2, BOK: 1}  # Dhanbad untouched
    # ...and Bokaro's low stock now follows it, end to end.
    assert _ids(_get("/api/v1/inventory/low-stock", user=_BOK_MGR, store_id=BOK)["items"]) == {"P-FRAME"}


def test_store_manager_cannot_set_another_shops_level(world):
    assert _set_level(_BOK_MGR, "P-FRAME", BOK, 1).status_code == 200  # the route exists
    assert _set_level(_BOK_MGR, "P-FRAME", DHN, 9).status_code == 403
    assert _set_level(_DHN_SALES, "P-FRAME", DHN, 9).status_code == 403
    assert _levels_of(world, "P-FRAME") == {DHN: 2, BOK: 1}


def test_admin_sets_any_shop_and_clears_back_to_not_set(world):
    assert _set_level(_ADMIN, "P-FRAME", PUN, 4).status_code == 200
    assert _levels_of(world, "P-FRAME").get(PUN) == 4
    cleared = _set_level(_ADMIN, "P-FRAME", DHN, None)
    assert cleared.status_code == 200
    assert -1 not in _leaves(cleared.json())  # the API never echoes -1
    from api.services.reorder_policy import reorder_level

    doc = world.products.find_one({"product_id": "P-FRAME"})
    assert reorder_level(doc, store_id=DHN) is None
    assert _get("/api/v1/inventory/low-stock", store_id=DHN)["items"] == []


def test_a_minus_one_write_clears_the_level_and_is_never_stored_or_echoed(world):
    res = _set_level(_DHN_MGR, "P-FRAME", DHN, -1)
    assert res.status_code == 200, res.text
    assert -1 not in _leaves(res.json())
    assert _levels_of(world, "P-FRAME") == {}  # cleared, not stored as -1


# ---------------------------------------------------------------------------
# 4. The migration: owner-set chain values become each stocking shop's level
# ---------------------------------------------------------------------------


def _script():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "scripts", "migrate_reorder_levels_per_shop.py",
    )
    spec = importlib.util.spec_from_file_location("migrate_reorder_levels_per_shop", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migration_db():
    db = mongomock.MongoClient().db
    db.products.insert_many([
        {"product_id": "P-OWNER", "reorder_point": 3},       # owner typed -> migrates
        {"product_id": "P-FORM5", "reorder_point": 5},       # the form default = not set
        {"product_id": "P-UNSET", "reorder_point": -1},
        {"product_id": "P-ZERO", "reorder_point": 0},        # import stamp, not typed
        {"product_id": "P-NONE"},
        {"product_id": "P-NOSHOP", "reorder_point": 4},      # no shop stocks it
        {"product_id": "P-KEEP", "reorder_point": 2, **_levels({DHN: 7})},
    ])
    db.stock_units.insert_many(
        _units("P-OWNER", DHN, 1) + _units("P-OWNER", BOK, 2)
        + _units("P-FORM5", DHN, 1) + _units("P-UNSET", DHN, 1) + _units("P-ZERO", DHN, 1)
        + _units("P-NONE", DHN, 1) + _units("P-KEEP", DHN, 1) + _units("P-KEEP", BOK, 1)
    )
    return db


def test_migration_dry_run_plans_owner_values_and_writes_nothing():
    mod = _script()
    db = _migration_db()
    before = list(db.products.find({}, {"_id": 0}))
    plan = mod.plan(db)
    assert {r["product_id"]: r["levels"] for r in plan} == {
        "P-OWNER": {DHN: 3, BOK: 3},
        "P-KEEP": {BOK: 2},  # a level a manager already typed is never overwritten
    }
    assert list(db.products.find({}, {"_id": 0})) == before
    assert mod.parse_args([]).apply is False  # dry run unless --apply


def test_migration_apply_writes_the_levels_once():
    mod = _script()
    db = _migration_db()
    mod.apply(db, mod.plan(db))
    assert _levels_of(db, "P-OWNER") == {DHN: 3, BOK: 3}
    assert _levels_of(db, "P-KEEP") == {DHN: 7, BOK: 2}
    assert _levels_of(db, "P-FORM5") == {}
    assert mod.plan(db) == []  # idempotent
