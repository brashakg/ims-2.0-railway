"""S7 (owner approved 2026-10-23): the Stock today page -- one shop's jobs.

GET /api/v1/inventory/today is READ-ONLY. It writes nothing and decides
nothing of its own: each section asks the rule that already exists on main.

    incoming     -- stock_transfers TO this shop that left the other shop
                    (in_transit / partially_received), pieces still to scan in
                    (routers/transfers._line_expected_qty minus received);
    requests     -- transfers FROM this shop not yet shipped (the pre-ship
                    statuses transfers.CANCELLABLE_TRANSFER_STATUSES lists,
                    minus draft and rejected);
    count        -- this shop's stock counts (/inventory/audit): any still in
                    progress, and how many finished today (IST);
    quarantine   -- QUARANTINED pieces here, and how many still have no red
                    label (the Quarantine queue's label_printed=false filter);
    deliveries   -- open purchase orders delivering here with pieces still
                    due (purchase_recon._stock_yet_to_receive), vendor named;
    stock_value  -- stock_value.shelf_units total + uncosted pieces, for the
                    roles that see stock at cost (stock_value.COST_CONTEXT)
                    only; None for everyone else;
    running_low  -- reorder_policy.low_stock_rows for this shop, with the
                    pieces at every OTHER shop (reorder_policy.on_hand) and
                    units sold here in 30 days (collection_insights.
                    movement_summary), fastest sellers first.

Gate: the purchase roles (managers + accountant), cost_mask.PURCHASE_ROLES.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.dependencies as deps  # noqa: E402
from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import collection_insights, stock_value  # noqa: E402
from api.services.rbac_policy import POLICY  # noqa: E402
from api.services.rbac_policy._core import PURCHASE  # noqa: E402

from test_on_hand_is_one_rule import _DBProxy  # noqa: E402

S1, S2 = "S1-DHANBAD", "S2-BOKARO"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(scope="module")
def _engine():
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:  # noqa: BLE001
        import mongomock

        client = mongomock.MongoClient()
    yield client
    client.close()


@pytest.fixture
def mongo_db(_engine):
    name = f"ims_test_today_{uuid.uuid4().hex[:12]}"
    yield _engine[name]
    try:
        _engine.drop_database(name)
    except Exception:  # noqa: BLE001
        pass


class _Conn:
    def __init__(self, proxy):
        self.db = proxy
        self.is_connected = True

    def get_collection(self, name):
        return self.db.get_collection(name)


def _user(role: str, store: str = S1) -> Dict[str, Any]:
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": [store] if store else [],
        "active_store_id": store,
    }


@pytest.fixture
def sold(monkeypatch):
    """Units sold in 30 days, per (shop, product), as movement_summary answers
    them; records every call so a test can see which shop was asked."""
    table: Dict[tuple, int] = {}
    calls = []

    def fake(db, members, store_id=None, days=90, by_store=False):
        calls.append({"store_id": store_id, "days": days, "pids": list(members["product_ids"])})
        per = {
            pid: {"units_d30": table.get((store_id, pid), 0)}
            for pid in members["product_ids"]
            if (store_id, pid) in table
        }
        return {"per_product": per, "totals": {}, "per_store": {}}

    monkeypatch.setattr(collection_insights, "movement_summary", fake)
    return table, calls


@pytest.fixture
def get(mongo_db, monkeypatch):
    from database.repositories.product_repository import ProductRepository, StockRepository
    import database.connection as db_conn

    proxy = _DBProxy(mongo_db)
    conn = _Conn(proxy)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(inv_mod, "get_stock_repository", lambda: StockRepository(mongo_db["stock_units"]))
    monkeypatch.setattr(inv_mod, "get_product_repository", lambda: ProductRepository(mongo_db["products"]))
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(db_conn, "get_db", lambda: conn)

    def _get(user, **params):
        app = FastAPI()
        app.include_router(inv_mod.router, prefix="/inventory")
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app).get("/inventory/today", params=params)

    return _get


def _ok(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()


def _transfer(db, tid, frm, to, status, lines, **extra):
    db["stock_transfers"].insert_one({
        "id": tid,
        "transfer_number": f"TRF-{tid}",
        "transfer_type": "store_to_store",
        "from_location_id": frm,
        "from_location_name": {S1: "Dhanbad", S2: "Bokaro"}.get(frm, frm),
        "to_location_id": to,
        "to_location_name": {S1: "Dhanbad", S2: "Bokaro"}.get(to, to),
        "status": status,
        "items": lines,
        "created_at": "2026-10-30T10:00:00+00:00",
        **extra,
    })


def _line(name, requested, shipped=None, received=0):
    line = {"product_id": f"P-{name}", "product_name": name,
            "quantity_requested": requested, "quantity_received": received}
    if shipped is not None:
        line["quantity_shipped"] = shipped
    return line


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_the_policy_row_is_the_purchase_roles():
    rows = [r for r in POLICY if r["method"] == "GET" and r["path"] == "/api/v1/inventory/today"]
    assert len(rows) == 1
    assert sorted(rows[0]["allowed"]) == sorted(PURCHASE)
    assert rows[0].get("store_scoped") is True


@pytest.mark.parametrize("role", ["SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "CATALOG_MANAGER"])
def test_roles_outside_the_purchase_roles_are_refused(get, role):
    assert get(_user(role)).status_code == 403


def test_a_manager_cannot_read_another_shop(get):
    assert get(_user("STORE_MANAGER"), store_id=S2).status_code == 403


def test_no_database_is_an_empty_page_not_a_500(get, monkeypatch):
    monkeypatch.setattr(inv_mod, "_get_db", lambda: None)
    body = _ok(get(_user("STORE_MANAGER")))
    assert body["incoming"] == [] and body["requests"] == [] and body["deliveries"] == []


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------


def test_pieces_coming_in_are_the_ones_shipped_to_this_shop_and_not_yet_scanned(get, mongo_db):
    _transfer(mongo_db, "a", S2, S1, "in_transit", [_line("RB3025", 4, shipped=3), _line("VO5286", 2, shipped=2)],
              shipped_at="2026-10-30T12:00:00+00:00")
    _transfer(mongo_db, "b", S2, S1, "partially_received", [_line("CA1234", 5, shipped=5, received=3)])
    _transfer(mongo_db, "c", S2, S1, "approved", [_line("X", 1)])  # not shipped yet
    _transfer(mongo_db, "d", S2, S1, "completed", [_line("Y", 1, shipped=1, received=1)])
    _transfer(mongo_db, "e", S1, S2, "in_transit", [_line("Z", 1, shipped=1)])  # going OUT
    mongo_db["stores"].insert_one({"store_id": S1, "store_name": "Dhanbad - Bank More"})
    body = _ok(get(_user("STORE_MANAGER")))
    assert body["store_id"] == S1 and body["store_name"] == "Dhanbad - Bank More"
    got = {t["transfer_id"]: t for t in body["incoming"]}
    assert set(got) == {"a", "b"}
    assert got["a"]["pieces"] == 5 and got["a"]["from_store_name"] == "Bokaro"
    assert got["a"]["shipped_at"] == "2026-10-30T12:00:00+00:00"
    assert got["b"]["pieces"] == 2  # 5 shipped, 3 already scanned in


def test_requests_are_this_shops_transfers_out_that_have_not_shipped(get, mongo_db):
    for tid, status in [("p1", "pending_approval"), ("p2", "approved"), ("p3", "picking"),
                        ("p4", "packed"), ("p5", "pending"), ("x1", "draft"), ("x2", "rejected"),
                        ("x3", "cancelled"), ("x4", "in_transit")]:
        _transfer(mongo_db, tid, S1, S2, status, [_line("RB3025", 2)])
    _transfer(mongo_db, "x5", S2, S1, "approved", [_line("RB3025", 2)])  # asked OF Bokaro
    body = _ok(get(_user("STORE_MANAGER")))
    got = {t["transfer_id"]: t for t in body["requests"]}
    assert set(got) == {"p1", "p2", "p3", "p4", "p5"}
    assert got["p1"]["to_store_name"] == "Bokaro"
    assert got["p1"]["pieces"] == 2
    assert got["p1"]["lines"] == [{"product_name": "RB3025", "quantity": 2}]


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------


def test_the_count_card_reads_this_shops_counts(get, mongo_db):
    now = datetime.now(timezone.utc)
    mongo_db["stock_counts"].insert_many([
        {"count_id": "c-open", "audit_number": "AUDIT-1", "store_id": S1, "zone": "Sunglasses wall",
         "status": "in_progress", "created_at": now.isoformat(), "completed_at": None},
        {"count_id": "c-today", "store_id": S1, "status": "completed",
         "created_at": now.isoformat(), "completed_at": now.isoformat()},
        {"count_id": "c-old", "store_id": S1, "status": "reconciled",
         "created_at": (now - timedelta(days=2)).isoformat(),
         "completed_at": (now - timedelta(days=2)).isoformat()},
        {"count_id": "c-other", "store_id": S2, "status": "in_progress",
         "created_at": now.isoformat(), "completed_at": None},
    ])
    count = _ok(get(_user("STORE_MANAGER")))["count"]
    assert [c["count_id"] for c in count["in_progress"]] == ["c-open"]
    assert count["in_progress"][0]["zone"] == "Sunglasses wall"
    assert count["counted_today"] == 1


def test_a_count_finished_before_ist_midnight_is_not_today(get, mongo_db):
    ist_midnight = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    late_last_night = (ist_midnight - timedelta(minutes=5)).astimezone(timezone.utc)
    mongo_db["stock_counts"].insert_one(
        {"count_id": "c-late", "store_id": S1, "status": "completed",
         "created_at": late_last_night.isoformat(), "completed_at": late_last_night.isoformat()})
    assert _ok(get(_user("STORE_MANAGER")))["count"]["counted_today"] == 0


def test_a_count_finished_just_after_ist_midnight_is_today(get, mongo_db):
    # 00:01 IST is still the previous day in UTC: a UTC day would miss it.
    ist_midnight = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    just_after = (ist_midnight + timedelta(minutes=1)).astimezone(timezone.utc)
    mongo_db["stock_counts"].insert_one(
        {"count_id": "c-early", "store_id": S1, "status": "completed",
         "created_at": just_after.isoformat(), "completed_at": just_after.isoformat()})
    assert _ok(get(_user("STORE_MANAGER")))["count"]["counted_today"] == 1


# ---------------------------------------------------------------------------
# Quarantine
# ---------------------------------------------------------------------------


def test_quarantine_counts_this_shops_pieces_and_the_unlabelled_ones(get, mongo_db):
    mongo_db["stock_units"].insert_many([
        {"_id": "q1", "product_id": "P1", "store_id": S1, "status": "QUARANTINED"},
        {"_id": "q2", "product_id": "P1", "store_id": S1, "status": "QUARANTINED",
         "quarantine_label_printed": False},
        {"_id": "q3", "product_id": "P1", "store_id": S1, "status": "QUARANTINED",
         "quarantine_label_printed": True},
        {"_id": "q4", "product_id": "P1", "store_id": S2, "status": "QUARANTINED"},
        {"_id": "a1", "product_id": "P1", "store_id": S1, "status": "AVAILABLE"},
    ])
    assert _ok(get(_user("STORE_MANAGER")))["quarantine"] == {"pieces": 3, "unlabeled": 2}


# ---------------------------------------------------------------------------
# Deliveries
# ---------------------------------------------------------------------------


def test_deliveries_due_are_open_orders_for_this_shop_with_pieces_still_due(get, mongo_db):
    mongo_db["vendors"].insert_one({"vendor_id": "V1", "trade_name": "Safilo India"})
    mongo_db["purchase_orders"].insert_many([
        {"po_id": "po1", "po_number": "PO-0412", "vendor_id": "V1", "status": "SENT",
         "delivery_store_id": S1, "expected_date": "2026-11-02", "created_at": "2026-10-20",
         "items": [{"product_id": "P1", "quantity": 8, "received_qty": 0},
                   {"product_id": "P2", "quantity": 5, "received_qty": 2}]},
        {"po_id": "po2", "po_number": "PO-0413", "vendor_id": "V1", "status": "SENT",
         "delivery_store_id": S1, "expected_date": "2026-11-01", "created_at": "2026-10-21",
         "items": [{"product_id": "P1", "quantity": 1, "received_qty": 1}]},  # nothing due
        {"po_id": "po3", "po_number": "PO-0414", "vendor_id": "V1", "status": "SENT",
         "delivery_store_id": S2, "items": [{"product_id": "P1", "quantity": 1}]},
        {"po_id": "po4", "po_number": "PO-0415", "vendor_id": "V1", "status": "DRAFT",
         "delivery_store_id": S1, "items": [{"product_id": "P1", "quantity": 1}]},
    ])
    rows = _ok(get(_user("STORE_MANAGER")))["deliveries"]
    assert rows == [{"po_id": "po1", "po_number": "PO-0412", "vendor_id": "V1",
                     "vendor_name": "Safilo India", "pieces_due": 11,
                     "expected_date": "2026-11-02"}]


# ---------------------------------------------------------------------------
# Stock value (accounts roles only)
# ---------------------------------------------------------------------------


def _priced_shelf(db):
    db["products"].insert_one({"product_id": "P1", "name": "Frame", "cost_price": 0})
    db["stock_units"].insert_many([
        {"_id": "u1", "product_id": "P1", "store_id": S1, "status": "AVAILABLE", "unit_cost": 1000},
        {"_id": "u2", "product_id": "P1", "store_id": S1, "status": "RESERVED", "unit_cost": 1500},
        {"_id": "u3", "product_id": "P1", "store_id": S1, "status": "AVAILABLE"},  # no cost
        {"_id": "u4", "product_id": "P1", "store_id": S1, "status": "SOLD", "unit_cost": 9000},
        {"_id": "u5", "product_id": "P1", "store_id": S2, "status": "AVAILABLE", "unit_cost": 7000},
    ])


@pytest.mark.parametrize("role", ["ADMIN", "ACCOUNTANT", "SUPERADMIN"])
def test_the_accounts_roles_see_stock_value_by_the_one_formula(get, mongo_db, role):
    _priced_shelf(mongo_db)
    from database.repositories.product_repository import ProductRepository, StockRepository

    units = stock_value.shelf_units(
        StockRepository(mongo_db["stock_units"]), ProductRepository(mongo_db["products"]), S1)
    body = _ok(get(_user(role), store_id=S1))
    assert body["stock_value"] == {"total": stock_value.total(units), "uncosted_pieces": 1}
    assert body["stock_value"]["total"] == 2500


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER"])
def test_managers_get_no_stock_value(get, mongo_db, role):
    _priced_shelf(mongo_db)
    body = _ok(get(_user(role)))
    assert body["stock_value"] is None
    assert "2500" not in str(body) and "1000" not in str(body)


# ---------------------------------------------------------------------------
# Running low
# ---------------------------------------------------------------------------


def _low_shelf(db):
    db["products"].insert_many([
        {"product_id": "RB", "sku": "RB3025", "name": "Ray-Ban RB3025", "is_active": True,
         "reorder_levels": {S1: 1, S2: 1}},
        {"product_id": "VO", "sku": "VO5286", "name": "Vogue VO5286", "is_active": True,
         "reorder_levels": {S1: 2}},
        {"product_id": "OK", "sku": "OK1", "name": "Plenty here", "is_active": True,
         "reorder_levels": {S1: 1}},
        {"product_id": "NS", "sku": "NS1", "name": "No level here", "is_active": True,
         "reorder_levels": {S2: 5}},
    ])
    units = [("RB", S2), ("RB", S2), ("RB", S2), ("VO", S1), ("OK", S1), ("OK", S1), ("OK", S1)]
    db["stock_units"].insert_many(
        [{"_id": f"lu{i}", "product_id": p, "store_id": s, "status": "AVAILABLE"} for i, (p, s) in enumerate(units)])


def test_running_low_is_the_shops_low_stock_list_with_other_shops_and_30_day_sales(get, mongo_db, sold):
    _low_shelf(mongo_db)
    table, calls = sold
    table[(S1, "RB")] = 7
    table[(S1, "VO")] = 5
    body = _ok(get(_user("STORE_MANAGER")))
    rows = body["running_low"]
    assert [(r["product_id"], r["here"], r["other_shops"], r["sold_30d"]) for r in rows] == [
        ("RB", 0, 3, 7),
        ("VO", 1, 0, 5),
    ]
    assert rows[0]["name"] == "Ray-Ban RB3025" and rows[0]["reorder_level"] == 1
    assert body["running_low_total"] == 2
    # Sales are this shop's, over 30 days.
    assert calls and all(c["store_id"] == S1 and c["days"] == 30 for c in calls)


def test_running_low_puts_the_fastest_sellers_first(get, mongo_db, sold):
    _low_shelf(mongo_db)
    table, _ = sold
    table[(S1, "RB")] = 1
    table[(S1, "VO")] = 9
    rows = _ok(get(_user("STORE_MANAGER")))["running_low"]
    assert [r["product_id"] for r in rows] == ["VO", "RB"]


# ---------------------------------------------------------------------------
# An admin with no shop picked sees every shop (owner ruling 2026-10-18)
# ---------------------------------------------------------------------------


def test_an_admin_with_no_shop_sees_every_shop(get, mongo_db, sold):
    _transfer(mongo_db, "a", S2, S1, "in_transit", [_line("RB3025", 1, shipped=1)])
    _transfer(mongo_db, "b", S1, S2, "in_transit", [_line("VO5286", 1, shipped=1)])
    _low_shelf(mongo_db)
    body = _ok(get(_user("ADMIN", store=None)))
    assert body["store_id"] is None and body["store_name"] is None
    assert {t["transfer_id"] for t in body["incoming"]} == {"a", "b"}
    low = {(r["product_id"], r["store_id"]) for r in body["running_low"]}
    assert ("NS", S2) in low and ("RB", S1) in low
