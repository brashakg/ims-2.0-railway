"""S5 - one Reorder list, one buy-quantity rule (owner 2026-10-22/23).

The owner: Buy Desk, Replenishment and Low stock become ONE Reorder list at
/inventory/reorders, and "reorder = the shop's level if set, else 30-day
sales". Before this PR five places each worked out "how many to buy" their own
way (Buy Desk: velocity x 14 days; stock alerts: velocity x 2 x lead time; the
purchase report: velocity x 67 days of cover; ORACLE: burn x (lead + horizon);
the analytics forecast: 30-day forecast minus a legacy stock field). Every test
below names THE rule (reorder_policy.buy_qty) and checks a reader against it.

The world: Dhanbad (DHN) has a level of 3 for P-LVL and none for P-SALES; P-SALES
sold 5 at DHN in the last 30 days. Pune (PUN) holds 6 of P-LVL and sells none
(spare pieces: a transfer instead of a buy). A SENT order for 1 P-LVL is open to
DHN; a DRAFT for 2 more is shown, never netted (owner D11).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_one_reorder_list.py -q
"""

import asyncio
import os
import re
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
from api.routers import inventory_router, reports_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.reorder_policy import buy_qty  # noqa: E402

DHN, BOK, PUN = "BV-DHN-02", "BV-BOK-01", "BV-PUN-01"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _user(role, shop=DHN):
    return {"user_id": f"u-{role}", "username": role.lower(), "roles": [role],
            "active_store_id": shop, "store_ids": [shop]}


_ADMIN = _user("ADMIN")
_MGR = _user("STORE_MANAGER")


def _product(pid, **extra):
    """A catalogue-complete, purchasable frame with a vendor."""
    base = {
        "product_id": pid, "sku": f"SKU-{pid}", "barcode": f"SKU-{pid}",
        "name": f"Ray-Ban {pid}", "brand": "Ray-Ban", "category": "FRAME",
        "mrp": 9000.0, "offer_price": 9000.0, "cost_price": 4000.0,
        "hsn_code": "9003", "gst_rate": 5, "is_active": True,
        "attributes": {"brand_name": "Ray-Ban", "model_no": pid, "colour_code": "01", "size": "54"},
        "preferred_vendor_id": "V-LUX", "reorder_quantity": None,
    }
    base.update(extra)
    return base


def _units(pid, store, n):
    return [{"stock_id": f"U-{pid}-{store}-{i}", "product_id": pid, "store_id": store,
             "status": "AVAILABLE", "quantity": 1, "barcode": f"B{pid}{store}{i}"}
            for i in range(n)]


def _orders(pid, store, n, days_ago=2, status="DELIVERED"):
    when = datetime.utcnow() - timedelta(days=days_ago)
    return [{"order_id": f"O-{pid}-{store}-{days_ago}-{i}", "store_id": store, "status": status,
             "created_at": when,
             "items": [{"product_id": pid, "barcode": f"SKU-{pid}", "sku": f"SKU-{pid}",
                        "quantity": 1, "unit_price": 9000.0, "product_name": pid}]}
            for i in range(n)]


def _world():
    db = mongomock.MongoClient().db
    db.stores.insert_many([{"store_id": DHN, "store_name": "Dhanbad"},
                           {"store_id": BOK, "store_name": "Bokaro"},
                           {"store_id": PUN, "store_name": "Pune"}])
    db.vendors.insert_one({"vendor_id": "V-LUX", "trade_name": "Luxottica"})
    db.products.insert_many([
        _product("P-LVL", reorder_levels={DHN: 3}),
        _product("P-SALES"),
        # Each of these is short at DHN (sold 4, holds 0) but kept off the list.
        _product("P-DISC", is_active=False),
        _product("P-OFF", reorder_quantity=-1),
        _product("P-NOVEND", preferred_vendor_id=None),
        _product("P-NOHSN", hsn_code=None),
    ])
    db.stock_units.insert_many(_units("P-SALES", DHN, 1) + _units("P-LVL", PUN, 6)
                               + _units("P-DISC", DHN, 1))
    db.orders.insert_many(
        _orders("P-SALES", DHN, 5)
        + _orders("P-SALES", DHN, 9, days_ago=45)  # outside 30 days: never counted
        + _orders("P-SALES", DHN, 3, status="CANCELLED")  # never a sale
        + _orders("P-LVL", DHN, 2)
        + [o for pid in ("P-DISC", "P-OFF", "P-NOVEND", "P-NOHSN") for o in _orders(pid, DHN, 4)]
    )
    db.purchase_orders.insert_many([
        {"po_id": "PO-SENT", "status": "SENT", "delivery_store_id": DHN,
         "items": [{"product_id": "P-LVL", "quantity": 1}]},
        {"po_id": "PO-DRAFT", "status": "DRAFT", "delivery_store_id": DHN,
         "items": [{"product_id": "P-LVL", "quantity": 2}]},
        {"po_id": "PO-DONE", "status": "SENT", "delivery_store_id": DHN,
         "items": [{"product_id": "P-SALES", "quantity": 2}],
         "received_qty_by_product": {"P-SALES": 2}},  # fully received: not on order
    ])
    return db


class _Conn:
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
    for name, mod in list(sys.modules.items()):
        if (name.startswith(("api.", "agents.")) and mod is not None
                and getattr(mod, "get_db", None) in originals):
            monkeypatch.setattr(mod, "get_db", lambda: conn)
    from api.routers import jarvis
    monkeypatch.setattr(jarvis, "get_db_collection", lambda n: db[n])
    return db


def _client(user):
    from api.routers.analytics_v2 import router as analytics_v2_router

    app = FastAPI()
    app.include_router(inventory_router, prefix="/api/v1/inventory")
    app.include_router(reports_router, prefix="/api/v1/reports")
    app.include_router(analytics_v2_router, prefix="/api/v1/analytics-v2")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _list(user=_ADMIN, **params):
    res = _client(user).get("/api/v1/inventory/reorder-list", params=params)
    assert res.status_code == 200, res.text[:300]
    return res.json()


def _row(body, pid, shop=DHN):
    got = [r for r in body["rows"] if r["product_id"] == pid and r["store_id"] == shop]
    assert len(got) == 1, (pid, shop, body["rows"])
    return got[0]


# ---------------------------------------------------------------------------
# 1. THE rule
# ---------------------------------------------------------------------------


def test_level_basis_buys_back_above_the_level():
    p = _product("P", reorder_levels={DHN: 3})
    assert buy_qty(p, store_id=DHN, on_hand=0) == (4, "level")
    # the level wins over sales
    assert buy_qty(p, store_id=DHN, on_hand=0, sold_30d=50) == (4, "level")
    assert buy_qty(p, store_id=DHN, on_hand=4) == (0, "level")


def test_no_level_buys_thirty_days_of_sales():
    p = _product("P")
    assert buy_qty(p, store_id=DHN, on_hand=1, sold_30d=5) == (4, "sales")
    assert buy_qty(p, store_id=DHN, on_hand=0, sold_30d=0) == (0, None)


def test_sent_orders_are_taken_off():
    p = _product("P", reorder_levels={DHN: 3})
    assert buy_qty(p, store_id=DHN, on_hand=0, on_order=1) == (3, "level")
    assert buy_qty(p, store_id=DHN, on_hand=0, on_order=9) == (0, "level")


def test_rounded_up_to_the_order_quantity_never_past_max_stock():
    p = _product("P", reorder_levels={DHN: 3}, reorder_quantity=6)
    assert buy_qty(p, store_id=DHN, on_hand=0) == (6, "level")
    capped = _product("P", reorder_levels={DHN: 3}, reorder_quantity=6, max_stock=5)
    assert buy_qty(capped, store_id=DHN, on_hand=1) == (4, "level")


def test_auto_reorder_off_or_discontinued_buys_nothing():
    for p in (_product("P", reorder_quantity=-1, reorder_levels={DHN: 3}),
              _product("P", is_active=False, reorder_levels={DHN: 3})):
        assert buy_qty(p, store_id=DHN, on_hand=0, sold_30d=9)[0] == 0


# ---------------------------------------------------------------------------
# 2. The list
# ---------------------------------------------------------------------------


def test_the_list_shows_both_bases_with_sent_netted_and_drafts_beside(world):
    body = _list(store_id=DHN)
    lvl = _row(body, "P-LVL")
    assert (lvl["basis"], lvl["level"], lvl["on_hand"]) == ("level", 3, 0)
    assert (lvl["on_order"], lvl["in_draft"], lvl["buy"]) == (1, 2, 3)
    assert lvl["band"] == "out-of-stock" and lvl["runs_out_days"] == 0
    sales = _row(body, "P-SALES")
    # 5 sold in 30 days (not the 45-day-old or cancelled ones), 1 held, the
    # received order is not on order.
    assert (sales["basis"], sales["sold_30d"], sales["on_hand"], sales["on_order"]) == ("sales", 5, 1, 0)
    assert sales["buy"] == 4 and sales["runs_out_days"] == 6
    assert sales["vendor_id"] == "V-LUX" and sales["vendor_name"] == "Luxottica"


def test_every_number_on_the_list_is_the_rule(world):
    body = _list(store_id=DHN)
    docs = {d["product_id"]: d for d in world.products.find({})}
    for r in body["rows"]:
        assert r["buy"] == buy_qty(docs[r["product_id"]], store_id=r["store_id"],
                                   on_hand=r["on_hand"], on_order=r["on_order"],
                                   sold_30d=r["sold_30d"])[0], r


def test_skipped_products_say_why(world):
    skipped = {s["product_id"]: s for s in _list(store_id=DHN)["skipped"]}
    assert skipped["P-DISC"]["reason"] == "discontinued"
    assert skipped["P-OFF"]["reason"] == "auto-reorder off"
    assert skipped["P-NOHSN"]["reason"] == "catalogue incomplete"
    assert "hsn_code" in skipped["P-NOHSN"]["missing"]
    rows = {r["product_id"] for r in _list(store_id=DHN)["rows"]}
    assert not rows & set(skipped)


def test_another_shops_spare_pieces_suggest_a_transfer(world):
    lvl = _row(_list(store_id=DHN), "P-LVL")
    assert lvl["transfer_from"] == [{"store_id": PUN, "store_name": "Pune", "qty": 3}]
    assert lvl["transfer_instead"] is True  # Pune's spares cover the buy of 3
    # Pune sells it fast: its pieces are its own, nothing to spare.
    world.orders.insert_many(_orders("P-LVL", PUN, 6))
    lvl = _row(_list(store_id=DHN), "P-LVL")
    assert lvl["transfer_from"] == [] and lvl["transfer_instead"] is False


def test_one_donors_spares_are_shared_out_never_offered_twice(world):
    """Review: Pune's 6 spare were offered whole to EVERY short shop. Two short
    shops, one donor: worst row first, each share comes off the donor."""
    world.products.update_one({"product_id": "P-LVL"},
                              {"$set": {"reorder_levels": {DHN: 3, BOK: 4}}})
    body = _list()
    bok, dhn = _row(body, "P-LVL", BOK), _row(body, "P-LVL", DHN)
    assert (bok["buy"], dhn["buy"]) == (5, 3)
    # both hold none: the bigger buy (Bokaro) is served first
    assert bok["transfer_from"] == [{"store_id": PUN, "store_name": "Pune", "qty": 5}]
    assert bok["transfer_qty"] == 5 and bok["transfer_instead"] is True
    assert dhn["transfer_from"] == [{"store_id": PUN, "store_name": "Pune", "qty": 1}]
    assert dhn["transfer_qty"] == 1 and dhn["transfer_instead"] is False  # 1 does not cover 3
    assert bok["transfer_qty"] + dhn["transfer_qty"] == 6  # Pune's spare, once


def test_no_preferred_vendor_falls_back_to_the_last_po_vendor(world):
    """Review BLOCK: nothing in the app sets preferred_vendor_id, so the list
    must not drop a product without one. A past PO from Safilo names it."""
    world.vendors.insert_one({"vendor_id": "V-SAF", "trade_name": "Safilo"})
    world.purchase_orders.insert_one(
        {"po_id": "PO-OLD", "status": "RECEIVED", "vendor_id": "V-SAF", "delivery_store_id": BOK,
         "created_at": "2026-09-01T10:00:00", "items": [{"product_id": "P-NOVEND", "quantity": 2}],
         "received_qty_by_product": {"P-NOVEND": 2}})
    row = _row(_list(store_id=DHN), "P-NOVEND")
    assert (row["vendor_id"], row["vendor_name"], row["vendor_source"]) == ("V-SAF", "Safilo", "last order")
    assert row["buy"] == 4
    assert "P-NOVEND" not in {s["product_id"] for s in _list(store_id=DHN)["skipped"]}


def test_no_vendor_anywhere_is_still_a_row_to_choose_a_vendor_for(world):
    row = _row(_list(store_id=DHN), "P-NOVEND")
    assert (row["vendor_id"], row["vendor_source"], row["buy"]) == (None, None, 4)


def test_a_failed_stock_read_is_an_error_never_a_full_buy_list(world, monkeypatch):
    """Review: a failed stock / open-order read read as zero, so the list
    suggested buying everything. It answers 503 with no rows."""
    import mongomock.collection as mc

    real = mc.Collection.find

    def boom(self, *a, **k):
        if self.name in ("stock_units", "purchase_orders"):
            raise RuntimeError("mongo down")
        return real(self, *a, **k)

    monkeypatch.setattr(mc.Collection, "find", boom)
    res = _client(_ADMIN).get("/api/v1/inventory/reorder-list", params={"store_id": DHN})
    assert res.status_code == 503, res.text[:300]
    assert "rows" not in res.json()
    assert res.json()["detail"] == "Could not load the reorder list"


def test_thirty_day_sales_chip_bands_by_running_out_the_boards_vogue(world):
    """Board: Vogue VO5286, no level, sold 5, holds 1, runs out in 6 days ->
    Low (not a made-up level's Critical). Ray-Ban, level 3, holds 0 -> Critical."""
    body = _list(store_id=DHN)
    vogue = _row(body, "P-SALES")
    assert (vogue["sold_30d"], vogue["on_hand"], vogue["runs_out_days"]) == (5, 1, 6)
    assert vogue["band"] == "low"
    assert _row(body, "P-LVL")["band"] == "out-of-stock"
    world.stock_units.delete_many({"product_id": "P-SALES"})
    assert _row(_list(store_id=DHN), "P-SALES")["band"] == "out-of-stock"


def test_a_healthy_tracked_line_shows_under_all_with_nothing_to_buy(world):
    """Board: Carrera 1234, level 2, 5 in stock, buy 0, shown under All."""
    world.products.insert_one(_product("P-OK", reorder_levels={DHN: 2}))
    world.stock_units.insert_many(_units("P-OK", DHN, 5))
    ok = _row(_list(store_id=DHN), "P-OK")
    assert (ok["buy"], ok["band"], ok["transfer_from"]) == (0, "healthy", [])
    # a healthy line of a skipped product is not news
    assert "P-DISC" not in {r["product_id"] for r in _list(store_id=DHN)["rows"]}


def test_the_header_counts_products_with_auto_reorder_off(world):
    assert _list(store_id=DHN)["auto_reorder_off"] == 1  # P-OFF (P-DISC is discontinued)


def test_an_online_sale_counts_at_the_shop_that_fulfilled_it(world):
    from api.services.reorder_policy import sold_by_shop

    world.stores.insert_one({"store_id": "BV-WEB", "store_name": "Website", "store_type": "ONLINE"})
    when = datetime.utcnow() - timedelta(days=1)
    world.orders.insert_many([
        {"order_id": "SH-1", "store_id": "BV-WEB", "status": "SHIPPED", "created_at": when,
         "items": [{"product_id": "P-LVL", "quantity": 2}],
         "fulfillment_breakdown": [{"product_id": "P-LVL", "store_id": BOK, "qty": 2}],
         "fulfillment_stores": [BOK]},
        {"order_id": "SH-2", "store_id": "BV-WEB", "status": "DELIVERED", "created_at": when,
         "items": [{"product_id": "P-LVL", "quantity": 1}], "fulfillment_stores": [PUN]},
    ])
    sold = sold_by_shop(world.orders)
    assert sold[("P-LVL", BOK)] == 2 and sold[("P-LVL", PUN)] == 1
    assert ("P-LVL", "BV-WEB") not in sold
    assert sold_by_shop(world.orders, store_id=BOK) == {("P-LVL", BOK): 2}


def test_only_booked_orders_are_sales(world):
    from api.services.reorder_policy import sold_by_shop

    world.orders.insert_many(_orders("P-LVL", BOK, 3, status="ON_HOLD")
                             + _orders("P-LVL", BOK, 2, status="CONFIRMED"))
    assert sold_by_shop(world.orders, store_id=BOK) == {("P-LVL", BOK): 2}


def test_rebalance_suggests_the_lists_shared_out_transfers(world):
    """Review: one rule for spare pieces. Rebalance shows exactly the Reorder
    list's shared-out transfers for the shop."""
    world.products.update_one({"product_id": "P-LVL"},
                              {"$set": {"reorder_levels": {DHN: 3, BOK: 4}}})
    for shop in (DHN, BOK):
        res = _client(_ADMIN).get("/api/v1/inventory/transfer-recommendations", params={"store_id": shop})
        assert res.status_code == 200, res.text[:300]
        got = {(r["product_id"], r["from_store"], r["quantity"]) for r in res.json()["recommendations"]}
        want = {(r["product_id"], t["store_id"], t["qty"]) for r in _list(store_id=shop)["rows"]
                for t in r["transfer_from"]}
        assert got == want and got, shop


def test_all_shops_for_an_admin_one_shop_for_a_manager(world):
    world.products.update_one({"product_id": "P-SALES"}, {"$set": {"reorder_levels": {BOK: 1}}})
    shops = {r["store_id"] for r in _list()["rows"]}
    assert shops == {DHN, BOK}
    assert {r["store_id"] for r in _list(_MGR)["rows"]} == {DHN}
    assert _client(_MGR).get("/api/v1/inventory/reorder-list",
                             params={"store_id": BOK}).status_code == 403


def test_value_is_at_cost_and_only_for_the_cost_roles(world):
    lvl = _row(_list(store_id=DHN), "P-LVL")
    assert (lvl["unit_cost"], lvl["est_value"]) == (4000.0, 12000.0)  # never the MRP
    mgr = _row(_list(_MGR), "P-LVL")
    assert "unit_cost" not in mgr and "est_value" not in mgr


@pytest.mark.parametrize("role", ["SALES_STAFF", "CASHIER", "WORKSHOP_STAFF", "OPTOMETRIST"])
def test_counter_roles_cannot_read_the_list(world, role):
    assert _client(_user(role)).get("/api/v1/inventory/reorder-list").status_code == 403


def test_the_route_has_a_policy_row_matching_the_screen_gate():
    from api.services.rbac_policy import POLICY

    row = next(r for r in POLICY if r["method"] == "GET"
               and r["path"] == "/api/v1/inventory/reorder-list")
    src = open(os.path.join(ROOT, "frontend", "src", "pages", "inventory", "inventoryRoles.ts")).read()
    m = re.search(r"REORDER_LIST_ROLES[^=]*=\s*\[([^\]]*)\]", src)
    assert m, "REORDER_LIST_ROLES is not in inventoryRoles.ts"
    assert set(re.findall(r"'([A-Z_]+)'", m.group(1))) == set(row["allowed"])


# ---------------------------------------------------------------------------
# 3. Every other reader suggests the same number
# ---------------------------------------------------------------------------


def test_purchase_report_suggests_the_rules_number(world):
    body = _list(store_id=DHN)
    res = _client(_ADMIN).get("/api/v1/reports/purchase/recommendations",
                              params={"store_id": DHN, "min_velocity": 1})
    assert res.status_code == 200, res.text[:300]
    recs = {r["product_id"]: r["suggested_order_qty"] for r in res.json()["recommendations"]}
    for pid in ("P-LVL", "P-SALES"):
        assert recs[pid] == _row(body, pid)["buy"], pid


def test_stock_alerts_recommend_the_rules_number(world):
    res = _client(_ADMIN).get("/api/v1/inventory/alerts", params={"store_id": DHN})
    alert = next(a for a in res.json()["alerts"] if a["sku"] == "SKU-P-LVL")
    assert alert["recommendedOrder"] == _row(_list(store_id=DHN), "P-LVL")["buy"]


def test_jarvis_recommends_the_rules_number(world):
    from api.routers import jarvis

    inv = jarvis.JarvisAnalyticsEngine._compute_inventory_live() or {}
    recs = {(r["sku"], r["store_id"]): r["recommended_order"]
            for r in inv.get("reorder_recommendations") or []}
    assert recs[("SKU-P-LVL", DHN)] == _row(_list(store_id=DHN), "P-LVL")["buy"]


def test_oracle_proposes_the_lists_rows_and_nothing_else(world):
    from agents.implementations.oracle import OracleAgent

    # max stock caps the buy; an online-store sale and a catalogue-incomplete
    # product are never proposed (review BLOCK: ORACLE kept its own count).
    world.products.update_one({"product_id": "P-SALES"}, {"$set": {"max_stock": 3}})
    world.stores.insert_one({"store_id": "BV-WEB", "store_name": "Website", "store_type": "ONLINE"})
    world.orders.insert_many(_orders("P-LVL", "BV-WEB", 4))
    asyncio.run(OracleAgent(db=world)._propose_reorders())
    got = {(p["payload"]["product_id"], p["payload"]["store_id"]): p["payload"]["quantity"]
           for p in world.ai_proposals.find({"type": "draft_po"})}
    want = {(r["product_id"], r["store_id"]): r["buy"] for r in _list()["rows"] if r["buy"] > 0}
    assert want[("P-SALES", DHN)] == 2  # capped: max 3, holds 1
    assert got == want
    assert ("P-OFF", DHN) not in got and ("P-NOHSN", DHN) not in got
    assert not [k for k in got if k[1] == "BV-WEB"]


def test_the_forecast_suggests_what_the_report_suggests(world):
    """Review: the forecast read only list rows, so a product the list skips
    (catalogue incomplete) read 0 there and a buy in the report. Both now ask
    buy_qty with the same inputs; stock is the shop's own units."""
    report = _client(_ADMIN).get("/api/v1/reports/purchase/recommendations",
                                 params={"store_id": DHN, "min_velocity": 1}).json()
    recs = {r["product_id"]: r["suggested_order_qty"] for r in report["recommendations"]}
    res = _client(_user("SUPERADMIN")).get("/api/v1/analytics-v2/demand-forecast", params={"store_id": DHN})
    assert res.status_code == 200, res.text[:300]
    fc = {f["product_id"]: f for f in res.json()["forecasts"]}
    for pid in ("P-NOHSN", "P-NOVEND", "P-SALES", "P-LVL"):
        assert fc[pid]["reorder_recommended"] == recs.get(pid, 0), pid
    assert fc["P-NOHSN"]["reorder_recommended"] == 4
    assert fc["P-SALES"]["current_stock"] == 1  # the shop's units, not a legacy field
    assert fc["P-SALES"]["reason"] == "Buy 4: the last 30 days' sales."


def test_buy_desk_is_gone():
    assert not os.path.exists(os.path.join(ROOT, "backend", "api", "routers", "buy_desk.py"))
    assert not os.path.exists(os.path.join(ROOT, "backend", "api", "services", "buy_desk.py"))
    from api.services.rbac_policy import POLICY

    assert not [r for r in POLICY if r["path"].startswith("/api/v1/buy-desk")]
