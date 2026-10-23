"""Counter roles read no cost, and no figure worked out from cost, anywhere.

Owner rulings: D7 (2026-09-29) -- SALES_STAFF, SALES_CASHIER, CASHIER,
OPTOMETRIST and WORKSHOP_STAFF see no cost price, landed cost or money worked
out from cost, behind the screens too. 2026-10-12 -- the till's order lines,
barcode scans and below-cost error hide it from them; workshop staff may log a
vendor return but never see a price; the cash-flow supplier totals are ADMIN +
ACCOUNTANT only. 2026-10-12 (later) -- what we pay, product cost included, is the
owner's and the accounts roles' only: the managers join the counter roles on
every cost read here (tests/test_what_we_pay_accounts_only.py walks them).

Every read below is driven over HTTP with a token per role against one shop
whose every cost figure is COST (or COST_PAISE). A counter role's raw JSON must
carry no cost-like key (``_cost_keys`` walks the whole body) and no cost figure
under any name; an accounts role's must still carry them (the positive control
that keeps the counter-role assertion from passing on an empty shop).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

COUNTER_ROLES = ("SALES_STAFF", "SALES_CASHIER", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF")
# Who reads cost (cost_mask.COST_VISIBLE_ROLES, owner ruling 2026-10-12).
COST_READERS = ("ADMIN", "ACCOUNTANT")
STORE = "BV-TEST-01"
OTHER = "BV-TEST-02"
COST = 4321.77
COST_PAISE = 432177

# A key is cost-like when it names cost, landed cost, margin or COGS, or a
# figure the business works out from cost. Deliberately NOT read from
# services/cost_mask: a key dropped from the mask must still be caught here.
_COSTLIKE = re.compile(
    r"cost|landed|margin|cogs|purchase_price|shrinkage_value|overage_value"
    r"|variance_value|deadstockvalue|restockvalue",
    re.I,
)
# Keys that say "cost" but carry no money: a count of lines and a source tag.
_NOT_MONEY = {"lines_without_cost", "cost_source"}


def _cost_keys(body, extra=(), path="$"):
    """Every cost-like key in `body`, at any depth, as a JSON path. `extra`
    names keys that are a value at cost on that one response (e.g. a stock
    report's totalValue)."""
    found = []
    if isinstance(body, dict):
        for k, v in body.items():
            if k not in _NOT_MONEY and (k in extra or _COSTLIKE.search(str(k))):
                found.append(f"{path}.{k}")
            found += _cost_keys(v, extra, f"{path}.{k}")
    elif isinstance(body, list):
        for i, v in enumerate(body):
            found += _cost_keys(v, extra, f"{path}[{i}]")
    return found


def _figures_in(body):
    """The cost figures that appear anywhere in the body, under any key."""
    text = json.dumps(body, default=str)
    return [f for f in (str(COST), str(COST_PAISE), str(round(COST * 2, 2))) if f in text]


def _user(role, store=STORE):
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": [store],
        "active_store_id": store,
    }


def _headers(role):
    from api.routers.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(_user(role))}"}


# ---------------------------------------------------------------------------
# One shop, every cost figure COST
# ---------------------------------------------------------------------------


class _Conn:
    """The app's database connection, backed by mongomock."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        return self.db[name]


def _seed(db):
    now = datetime.now()
    db.stores.insert_one({"store_id": STORE, "store_name": "Test shop", "entity_id": "entity-test",
                          "gstin": "20AAAAA0000A1Z5", "state_code": "20"})
    db.entities.insert_one({"entity_id": "entity-test", "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
    db.products.insert_one(
        {
            "product_id": "P1",
            "sku": "SKU-P1",
            "name": "Aviator",
            "category": "FRAME",
            "brand": "Ray-Ban",
            "mrp": 9999.0,
            "offer_price": 9999.0,
            "gst_rate": 12,
            "cost_price": COST,
            "landed_cost": COST,
            "is_active": True,
            "reorder_levels": {STORE: 5},
        }
    )
    db.stock_units.insert_one(
        {
            "stock_id": "U1",
            "product_id": "P1",
            "store_id": STORE,
            "barcode": "2900000000017",
            "status": "AVAILABLE",
            "quantity": 2,
            "category": "FRAME",
            "cost_price": COST,
            "unit_cost": COST,
            "created_at": now,
            # Inside /inventory/expiring's window (an ISO date, as add_stock writes it).
            "expiry_date": (now + timedelta(days=5)).date().isoformat(),
        }
    )
    db.customers.insert_one(
        {"customer_id": "C1", "name": "Asha", "phone": "9876500001", "store_id": STORE}
    )
    db.lens_catalog.insert_one(
        {
            "lens_line_id": "LL1",
            "brand": "Essilor",
            "series": "Crizal",
            "index": 1.5,
            "material": "CR39",
            "lens_type": "SV",
            "coating": "HC",
            "cost_price": COST,
            "is_active": True,
        }
    )
    db.orders.insert_one(
        {
            "order_id": "O1",
            "order_number": "BV-O1",
            "tracking_token": "TRACK-O1",  # else the first read backfills one
            "store_id": STORE,
            "customer_id": "C1",
            "customer_phone": "9876500001",
            "status": "CONFIRMED",
            "payment_status": "PAID",
            "grand_total": 9999.0,
            "created_at": now,
            "items": [
                {
                    "item_id": "I1",
                    "product_id": "P1",
                    "product_name": "Aviator",
                    "quantity": 1,
                    "unit_price": 9999.0,
                    "item_total": 9999.0,
                    "cost_at_sale": COST,
                }
            ],
        }
    )
    # Ready to collect, part-paid and past its promised day: on the delivery
    # queue, the unpaid list and the overdue list.
    db.orders.insert_one(
        {
            "order_id": "O3",
            "order_number": "BV-O3",
            "store_id": STORE,
            "customer_id": "C1",
            "customer_name": "Asha",
            "status": "READY",
            "payment_status": "PARTIAL",
            "expected_delivery": "2000-01-01",
            "grand_total": 9999.0,
            "created_at": now,
            "items": [
                {
                    "item_id": "I3",
                    "product_id": "P1",
                    "product_name": "Aviator",
                    "quantity": 1,
                    "unit_price": 9999.0,
                    "item_total": 9999.0,
                    "cost_at_sale": COST,
                }
            ],
        }
    )
    # A legacy order whose lines sit under `order_items` (read as-is).
    db.orders.insert_one(
        {
            "order_id": "O2",
            "order_number": "BV-O2",
            "store_id": STORE,
            "customer_id": "C1",
            "status": "DELIVERED",
            "created_at": now,
            "order_items": [{"product_id": "P1", "quantity": 1, "cost_at_sale": COST}],
        }
    )
    db.stock_counts.insert_one(
        {
            "count_id": "SC1",
            "store_id": STORE,
            "status": "reconciled",
            "audit_number": "AUDIT-1",
            "created_at": now.isoformat(),
            "items": [{"product_id": "P1", "counted_quantity": 1}],
            "variances": [
                {
                    "product_id": "P1",
                    "variance": -1,
                    "unit_cost": COST,
                    "variance_value": -COST,
                }
            ],
            "shrinkage_value": COST,
            "overage_value": COST,
            "shrinkage_value_written_off": COST,
            "reconciled_items": [{"product_id": "P1", "unit_cost": COST}],
        }
    )
    remake = {"reason_code": "BREAK", "category": "LAB", "cost_paise": COST_PAISE, "at": now.isoformat()}
    db.workshop_jobs.insert_one(
        {
            "job_id": "J1",
            "job_number": "WJ-1",
            "store_id": STORE,
            "status": "PENDING",
            "vendor_id": "V1",
            "created_at": now.isoformat(),
            "expected_date": "2000-01-01",
            "remake_reasons": [remake],
        }
    )
    db.stock_transfers.insert_one(
        {
            "id": "T1",
            "from_location_id": STORE,
            "to_location_id": OTHER,
            "status": "pending_approval",
            "created_at": now.isoformat(),
            "items": [{"product_id": "P1", "quantity_requested": 1, "unit_cost": COST}],
            "total_value": COST,
        }
    )
    revealed = [
        {
            "product_id": "P1",
            "counted_qty": 0,
            "expected": 1,
            "cost_paise": COST_PAISE,
            "variance_units": -1,
            "verdict": "SHORT",
            "variance_value_paise": -COST_PAISE,
        }
    ]
    db.blind_stock_takes.insert_one(
        {
            "_id": "BST-1",
            "session_id": "BST-1",
            "store_id": STORE,
            "status": "LOCKED",
            "items": revealed,
            "items_revealed": revealed,
            "summary": {"net_variance_value_paise": -COST_PAISE, "total_skus": 1},
        }
    )


@pytest.fixture
def shop(monkeypatch):
    import mongomock

    import database.connection as conn_mod
    from api import dependencies as deps

    db = mongomock.MongoClient().db
    _seed(db)
    conn = _Conn(db)
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(conn_mod, "get_db", lambda: conn)
    return db


# (method, path, value-at-cost keys particular to the response)
READS = [
    ("GET", "/api/v1/orders", ()),
    ("GET", f"/api/v1/orders?store_id={STORE}", ()),
    ("GET", "/api/v1/orders/O1", ()),
    ("GET", "/api/v1/orders/O2", ()),
    ("GET", "/api/v1/orders/O1/invoice", ()),
    ("GET", "/api/v1/orders/pending/delivery", ()),
    ("GET", "/api/v1/orders/unpaid/list", ()),
    ("GET", "/api/v1/orders/overdue/list", ()),
    ("GET", "/api/v1/orders/search?q=BV-O", ()),
    ("GET", "/api/v1/customers/C1/orders", ()),
    ("GET", "/api/v1/inventory/barcode/2900000000017", ()),
    ("GET", "/api/v1/inventory/stock/barcode/2900000000017", ()),
    ("GET", "/api/v1/inventory/expiring", ()),
    ("GET", "/api/v1/inventory/barcode/2900000000017/trace", ()),
    ("GET", "/api/v1/lens-catalog", ()),
    ("GET", "/api/v1/lens-catalog/", ()),
    ("GET", "/api/v1/lens-catalog/LL1", ()),
    ("GET", "/api/v1/reports/purchase/recommendations", ()),
    ("GET", f"/api/v1/inventory/stock-count?store_id={STORE}", ()),
    ("GET", "/api/v1/inventory/stock-count/SC1", ()),
    ("GET", "/api/v1/reports/inventory", ("totalValue", "value")),
    ("GET", "/api/v1/reports/inventory/summary", ("total_value",)),
    (
        "GET",
        "/api/v1/reports/stock/count?from_date=2026-01-01&to_date=2026-12-31",
        ("total_value",),
    ),
    ("GET", "/api/v1/inventory/alerts", ()),
    ("GET", "/api/v1/workshop/jobs", ()),
    ("GET", "/api/v1/workshop/jobs/J1", ()),
    ("GET", "/api/v1/workshop/pending", ()),
    ("GET", "/api/v1/workshop/overdue", ()),
    ("GET", "/api/v1/workshop/jobs/by-vendor/V1", ()),
    ("GET", "/api/v1/workshop/dashboard-kpis", ()),
    ("GET", "/api/v1/transfers", ("total_value",)),
    ("GET", "/api/v1/transfers/pending", ("total_value",)),
    ("GET", "/api/v1/transfers/T1", ("total_value",)),
    ("GET", "/api/v1/transfers/analytics/summary", ("total_value",)),
    ("GET", f"/api/v1/transfers/analytics/location/{STORE}", ("total_value", "value")),
]

# The /api/v1/blind-count routes were RETIRED in S1 (review M5): unmounted.
BLIND = ("GET", "/api/v1/blind-count/BST-1", ())


def _get(client, role, method, path):
    r = client.request(method, path, headers=_headers(role))
    assert r.status_code == 200, (role, path, r.status_code, r.text[:300])
    return r.json()


@pytest.mark.parametrize("role", COUNTER_ROLES)
@pytest.mark.parametrize("method,path,extra", READS)
def test_counter_role_reads_no_cost(client, shop, role, method, path, extra):
    body = _get(client, role, method, path)
    assert not _cost_keys(body, extra), (role, path, _cost_keys(body, extra))
    assert not _figures_in(body), (role, path, body)


# Stock value AT COST (services/stock_value.COST_CONTEXT) is narrower than the
# product's own cost: the accounts roles alone (owner ruling 2026-10-12, what we
# pay) -- a store, area or catalogue manager reads these without the value.
_STOCK_AT_COST = (
    "/api/v1/reports/inventory",
    "/api/v1/reports/inventory/summary",
    "/api/v1/reports/stock/count",
)
_ACCOUNTS = ("ADMIN", "ACCOUNTANT")


@pytest.mark.parametrize("role", COST_READERS)
@pytest.mark.parametrize("method,path,extra", READS)
def test_accounts_still_read_the_cost(client, shop, role, method, path, extra):
    body = _get(client, role, method, path)
    if path.split("?")[0] in _STOCK_AT_COST and role not in _ACCOUNTS:
        assert not _cost_keys(body, extra), (role, path, _cost_keys(body, extra))
        return
    assert _cost_keys(body, extra), (role, path, body)


def _without_cost_keys(body):
    """`body` without the keys _cost_keys names, at any depth (this file's rule)."""
    if isinstance(body, dict):
        return {k: _without_cost_keys(v) for k, v in body.items() if not _cost_keys({k: 0})}
    if isinstance(body, list):
        return [_without_cost_keys(v) for v in body]
    return body


# The till bills from the scan reply: a counter role's must be the manager's
# with only the cost keys gone -- the joined product (name, MRP, offer price,
# GST rate), the barcode, the status and the other-shop flag all kept. (A scan
# scoped to a shop the login does not hold is a 403 since 2026-10-18, F11:
# tests/test_visibility_sweep_1018.py.)
_SCANS = (
    ("/api/v1/inventory/barcode/2900000000017", False),
    (f"/api/v1/inventory/barcode/2900000000017?store_id={STORE}", False),
    ("/api/v1/inventory/stock/barcode/2900000000017", None),  # no product join
)


@pytest.mark.parametrize("role", COUNTER_ROLES)
@pytest.mark.parametrize("path,foreign", _SCANS)
def test_the_till_scans_for_counter_roles_as_for_managers(client, shop, role, path, foreign):
    got = _get(client, role, "GET", path)
    assert got == _without_cost_keys(_get(client, "ADMIN", "GET", path)), got
    assert (got["barcode"], got["status"]) == ("2900000000017", "AVAILABLE"), got
    if foreign is not None:
        assert got["cross_store"] is foreign, got
        p = got["product"]
        assert (p["name"], p["mrp"], p["offer_price"], p["gst_rate"]) == (
            "Aviator", 9999.0, 9999.0, 12
        ), p


# The counter still gets the order lines it sells and hands over from (the
# delivery surface, the sale-complete screen, the customer panel's history):
# its reply is the manager's with only the cost keys gone.
_ORDER_READS = (
    ("/api/v1/orders/O1", lambda b: [b], "camel"),
    ("/api/v1/orders/pending/delivery", lambda b: b["orders"], "camel"),
    ("/api/v1/orders", lambda b: b["orders"], "camel"),
    ("/api/v1/customers/C1/orders", lambda b: b["orders"], "snake"),
)
_LINE_KEYS = {
    "camel": ("productName", "unitPrice", "quantity", "finalPrice"),
    "snake": ("product_name", "unit_price", "quantity", "item_total"),
}


@pytest.mark.parametrize("role", COUNTER_ROLES)
@pytest.mark.parametrize("path,orders_of,case", _ORDER_READS)
def test_counter_roles_keep_the_order_lines(client, shop, role, path, orders_of, case):
    got = _get(client, role, "GET", path)
    assert got == _without_cost_keys(_get(client, "ADMIN", "GET", path)), got
    lines = [ln for o in orders_of(got) for ln in o.get("items") or []]
    assert lines, got
    for ln in lines:
        assert [ln[k] for k in _LINE_KEYS[case]] == ["Aviator", 9999.0, 1, 9999.0], ln


def _widen_product_rule(monkeypatch, role):
    """cost_mask's "product" context WITH `role`: the one rule changed."""
    from api.services import cost_mask

    monkeypatch.setitem(
        cost_mask._CONTEXT_ROLES,
        "product",
        cost_mask._CONTEXT_ROLES["product"] | {role},
    )


# Product reads answer cost_mask's "product" context. Stock valuation has
# the narrower default context: widening product access must not widen it.
@pytest.mark.parametrize("widened", ("STORE_MANAGER", "CATALOG_MANAGER"))
def test_widening_the_product_rule_moves_every_shop_cost_read(
    client, shop, monkeypatch, widened
):
    for method, path, extra in READS:
        assert not _cost_keys(_get(client, widened, method, path), extra), path
    _widen_product_rule(monkeypatch, widened)
    for method, path, extra in READS:
        body = _get(client, widened, method, path)
        if path.split("?")[0] in _STOCK_AT_COST:
            assert not _cost_keys(body, extra), (widened, path, body)
            for account in _ACCOUNTS:
                assert _cost_keys(_get(client, account, method, path), extra), (account, path)
        else:
            assert _cost_keys(body, extra), (widened, path, body)
        other = _get(client, "AREA_MANAGER", method, path)
        assert not _cost_keys(other, extra), (path, _cost_keys(other, extra))


# ---------------------------------------------------------------------------
# Serials: the unit, its warranty lookup (unit + the sold order) and the
# manager-only sell / recall / return replies
# ---------------------------------------------------------------------------
_SERIAL_READERS = ("SALES_STAFF", "SALES_CASHIER", "CASHIER", "OPTOMETRIST")
_SERIAL_READS = ("/api/v1/serials/SN12345", "/api/v1/serials/warranty/SN12345")


def _serial_unit(db, serial, status, **over):
    db.stock_units.insert_one(
        {
            "stock_id": f"U-{serial}",
            "serial": serial,
            "product_id": "P1",
            "store_id": STORE,
            "status": status,
            "quantity": 1,
            "cost_price": COST,
            "unit_cost": COST,
            "warranty_months": 12,
            **over,
        }
    )


@pytest.fixture
def serials(shop, monkeypatch):
    from api.routers import serial_tracking as st

    # No Shopify write-back from a test (the per-shop stock sync).
    monkeypatch.setattr(st, "_sync_online_stock", lambda *a, **k: None)
    _serial_unit(shop, "SN12345", "SOLD", order_id="O1", sold_to_customer_id="C1",
                 sold_at=datetime.now().isoformat())
    return shop


@pytest.mark.parametrize("role", _SERIAL_READERS)
@pytest.mark.parametrize("path", _SERIAL_READS)
def test_counter_roles_read_a_serial_without_cost(client, serials, role, path):
    body = _get(client, role, "GET", path)
    assert body["serial"] == "SN12345" and body["status"] == "SOLD", body
    assert not _cost_keys(body), (role, path, _cost_keys(body))
    assert not _figures_in(body), body
    if "warranty" in path:  # the sold order still names what was sold
        assert body["order"]["items"][0]["product_name"] == "Aviator", body


@pytest.mark.parametrize("role", COST_READERS)
@pytest.mark.parametrize("path", _SERIAL_READS)
def test_accounts_read_a_serial_with_its_cost(client, serials, role, path):
    keys = _cost_keys(_get(client, role, "GET", path))
    assert keys, (role, path)
    if "warranty" in path:
        assert "$.order.items[0].cost_at_sale" in keys, keys


# (path, the unit's state before, body) -- STORE_MANAGER reaches both. A
# recall is a write-off since S1 (ADMIN / SUPERADMIN only):
# test_write_off_admin_only.py.
_SERIAL_WRITES = (
    ("/api/v1/serials/SN-W/mark-sold", "IN_STOCK", {"order_id": "O-W"}),
    ("/api/v1/serials/SN-W/return", "SOLD", None),
)


@pytest.mark.parametrize("widened", (False, True))
@pytest.mark.parametrize("path,before,body", _SERIAL_WRITES)
def test_a_serial_write_answers_cost_by_the_product_rule(
    client, serials, monkeypatch, path, before, body, widened
):
    if widened:
        _widen_product_rule(monkeypatch, "STORE_MANAGER")
    _serial_unit(serials, "SN-W", before)
    if path.endswith("/mark-sold"):
        # A hand mark-sold needs a live order line with a piece left to hand
        # over (S1 review round 2); O1's one piece is already sold (SN12345).
        serials.orders.insert_one({"order_id": "O-W", "store_id": STORE, "status": "CONFIRMED",
                                   "items": [{"product_id": "P1", "quantity": 1}]})
    r = client.post(path, json=body, headers=_headers("STORE_MANAGER"))
    assert r.status_code == 200, r.text
    assert r.json()["serial"] == "SN-W" and r.json()["status"] != before, r.json()
    assert bool(_cost_keys(r.json())) is widened, r.json()
    assert serials.stock_units.find_one({"serial": "SN-W"})["cost_price"] == COST


@pytest.mark.parametrize("role", ("SALES_STAFF", "ADMIN"))
def test_the_retired_blind_count_answers_nobody(client, shop, role):
    """S1 review M5: the unused second count system is unmounted -- it showed
    expected figures to managers on an open count."""
    r = client.get(BLIND[1], headers=_headers(role))
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# The walker itself: it must see every shape it is trusted with
# ---------------------------------------------------------------------------


def test_the_walker_finds_cost_at_any_depth():
    body = {
        "a": [{"b": {"cost_at_sale": 1}}],
        "landed_cost_paise": 1,
        "x": {"gross_margin": 1, "costImpact": 2, "deadStockValue": 3},
        "rows": [{"shrinkage_value": 1, "variance_value_paise": 2, "value": 9}],
        "lines_without_cost": 4,
    }
    assert set(_cost_keys(body, ("value",))) == {
        "$.a[0].b.cost_at_sale",
        "$.landed_cost_paise",
        "$.x.gross_margin",
        "$.x.costImpact",
        "$.x.deadStockValue",
        "$.rows[0].shrinkage_value",
        "$.rows[0].variance_value_paise",
        "$.rows[0].value",
    }


# ---------------------------------------------------------------------------
# Workshop staff writes: a count they complete, a rework they send
# ---------------------------------------------------------------------------


def _open_count(db, count_id):
    db.stock_counts.insert_one(
        {
            "count_id": count_id,
            "store_id": STORE,
            "status": "in_progress",
            "audit_number": f"AUDIT-{count_id}",
            "system_quantities": {"P1": 1},  # the one unit row on the shelf
            "items": [{"product_id": "P1", "product_name": "Aviator", "counted_quantity": 0}],
        }
    )


# (role, sees cost, the product rule widened to take that role in)
_WRITE_CASES = [
    ("WORKSHOP_STAFF", False, False),
    ("STORE_MANAGER", False, False),
    ("STORE_MANAGER", True, True),
]


@pytest.mark.parametrize("role,sees,widened", _WRITE_CASES)
def test_completing_a_count_answers_cost_to_managers_only(
    client, shop, monkeypatch, role, sees, widened
):
    if widened:
        _widen_product_rule(monkeypatch, role)
    _open_count(shop, f"SC-{role}")
    r = client.post(
        f"/api/v1/inventory/stock-count/SC-{role}/complete", headers=_headers(role)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shrinkage_units"] == 1 and body["variances"], body
    assert bool(_cost_keys(body)) is sees, _cost_keys(body)
    assert bool(_figures_in(body)) is sees, body
    # The variance task is read by every store role: units, never rupees.
    (task,) = list(shop.tasks.find({}))
    assert "2 units short" not in task["description"]  # one unit row was counted
    assert "1 units short" in task["description"], task
    assert "Rs" not in task["description"] and "4,321" not in task["description"], task


def _qc_failed_job(db, job_id):
    db.workshop_jobs.insert_one(
        {
            "job_id": job_id,
            "job_number": f"WJ-{job_id}",
            "store_id": STORE,
            "status": "QC_FAILED",
            "lens_details": {"product_id": "P1"},
            "created_at": datetime.now().isoformat(),
        }
    )


@pytest.mark.parametrize("role,sees,widened", _WRITE_CASES)
def test_a_rework_answers_its_spoilage_cost_to_managers_only(
    client, shop, monkeypatch, role, sees, widened
):
    from api.routers.workshop import qc as qc_mod

    if widened:
        _widen_product_rule(monkeypatch, role)
    monkeypatch.setattr(qc_mod, "get_db", lambda: _Conn(shop))
    _qc_failed_job(shop, f"J-{role}")
    r = client.post(
        f"/api/v1/workshop/jobs/J-{role}/rework?remake_reason_code=AXIS_ERROR",
        headers=_headers(role),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "IN_PROGRESS", body
    assert bool(_cost_keys(body)) is sees, body
    # The cost is still recorded on the job for the managers' reports.
    stored = shop.workshop_jobs.find_one({"job_id": f"J-{role}"})
    assert stored["remake_reasons"][0]["cost_paise"] == COST_PAISE


# ---------------------------------------------------------------------------
# (c) The below-cost price error: no figure for counter roles, sale still blocked
# ---------------------------------------------------------------------------
from tests.test_discount_cap_door_parity import (  # noqa: E402,F401
    COST as _DOOR_COST,
    PID as _DOOR_PID,
    _line,
    _seed_draft,
    doors,
)

_BELOW_COST_TO_COUNTER = "This price is below cost - ask a manager"


def _below_cost_on_both_doors(client, doors, role):
    """POST /orders and POST /orders/{id}/items at Rs 100 for a Rs 12,000-cost
    frame; both must refuse. Returns the two details."""
    headers = _headers(role)
    created = client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": [_line(_DOOR_PID, 100.0)]},
        headers=headers,
    )
    oid = _seed_draft(doors, f"ORD-BC-{role}")
    added = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(_DOOR_PID, 100.0), headers=headers
    )
    for r in (created, added):
        assert r.status_code == 400, (role, r.status_code, r.text)
    # The sale is still blocked exactly as before: nothing was appended.
    assert doors["orders"].find_by_id(oid)["items"] == []
    return [created.json()["detail"], added.json()["detail"]]


@pytest.mark.parametrize("role", ("SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "AREA_MANAGER"))
def test_below_cost_error_names_no_figure_to_the_counter(client, doors, role):
    figure = str(int(_DOOR_COST))  # "12000"
    for detail in _below_cost_on_both_doors(client, doors, role):
        assert detail == _BELOW_COST_TO_COUNTER, detail
        assert figure not in detail and "Rs" not in detail, detail


@pytest.mark.parametrize("role", ("ADMIN",))
def test_below_cost_error_still_names_the_cost_to_accounts(client, doors, role):
    for detail in _below_cost_on_both_doors(client, doors, role):
        assert "below cost" in detail.lower(), detail
        assert str(_DOOR_COST) in detail, detail


def test_the_below_cost_error_follows_the_product_rule(client, doors, monkeypatch):
    # The figure goes to whoever cost_mask's "product" context admits, not a
    # copy of it or another context: widen it and the manager is told the
    # cost (the sale is still blocked, _below_cost_on_both_doors).
    _widen_product_rule(monkeypatch, "STORE_MANAGER")
    for detail in _below_cost_on_both_doors(client, doors, "STORE_MANAGER"):
        assert str(_DOOR_COST) in detail, detail


# ---------------------------------------------------------------------------
# (3) Cash flow: what was paid to suppliers is ADMIN + ACCOUNTANT only
# ---------------------------------------------------------------------------
_PAID_POS = 6543.21
_PAID_VENDORS = 1234.56


class _CashColl:
    def __init__(self, rows, found=()):
        self._rows = rows
        self._found = found

    def aggregate(self, _pipeline):
        return list(self._rows)

    def find(self, *_a, **_k):
        return list(self._found)


class _CashDb:
    def get_collection(self, name):
        from api.utils.ist import ist_today

        rows = {"purchase_orders": [{"_id": None, "total": _PAID_POS}]}.get(name, [])
        # Cash paid to suppliers is the supplier ledger's payment rows
        # (cash_flow._ap_rows): one payment at this shop, today.
        found = {
            "vendor_payments": [
                {"payment_id": "VP1", "vendor_id": "V1", "amount": _PAID_VENDORS,
                 "payment_date": ist_today().isoformat(), "store_id": STORE},
            ],
        }.get(name, [])
        return _CashColl(rows, found)


def _cash_flow(monkeypatch, role, store_id=None):
    from api.routers.finance import cash_flow as cash_flow_mod

    monkeypatch.setattr(cash_flow_mod, "_get_db", lambda: _CashDb())
    user = _user(role)
    # An admin with no shop picked reads the org view; a non-admin keeps its
    # shop (one with NO shop reads none: R4, test_finance_supplier_money_*).
    if store_id is None and role in ("ADMIN", "SUPERADMIN"):
        user = {**user, "active_store_id": None, "store_ids": []}
    return asyncio.run(
        cash_flow_mod.get_cash_flow(period="month", store_id=store_id, current_user=user)
    )


_SUPPLIER_KEYS = ("purchase_outflow", "vendor_payment_outflow")


@pytest.mark.parametrize("store_id", (None, STORE))
@pytest.mark.parametrize("role", ("ADMIN", "ACCOUNTANT"))
def test_accounts_read_what_was_paid_to_suppliers(monkeypatch, role, store_id):
    body = _cash_flow(monkeypatch, role, store_id)
    assert body["purchase_outflow"] == _PAID_POS
    assert "vendor_payments_restricted" not in body
    # The org view and the shop's own view both count the shop's payment
    # (every payment carries its shop, F63).
    assert body["vendor_payment_outflow"] == _PAID_VENDORS
    assert body["outflows"] == round(_PAID_POS + _PAID_VENDORS, 2)


@pytest.mark.parametrize("store_id", (None, STORE))
@pytest.mark.parametrize("role", ("STORE_MANAGER", "AREA_MANAGER"))
def test_managers_cash_flow_carries_no_supplier_total(monkeypatch, role, store_id):
    body = _cash_flow(monkeypatch, role, store_id)
    for key in _SUPPLIER_KEYS:
        assert key not in body, body
    # Not folded into the totals either: outflows - expenses would hand it back.
    assert body["outflows"] == 0 and body["net_cash_flow"] == 0, body
    assert body["vendor_payments_restricted"] is True, body
    text = json.dumps(body)
    assert str(_PAID_POS) not in text and str(_PAID_VENDORS) not in text, body


@pytest.mark.parametrize("role", ("ADMIN", "ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER"))
def test_the_supplier_total_answers_the_payables_rule(monkeypatch, role):
    from api.services.cost_mask import can_see_cost

    body = _cash_flow(monkeypatch, role, STORE)
    assert ("purchase_outflow" in body) is can_see_cost(_user(role), "payables")


# ---------------------------------------------------------------------------
# (2) Workshop staff log a vendor return, and never see a price on it
# ---------------------------------------------------------------------------


@pytest.fixture
def returns_db(monkeypatch):
    import mongomock

    from api.routers import vendor_returns as vr

    db = mongomock.MongoClient().db
    monkeypatch.setattr(vr, "_get_db", lambda: db)
    return db


def _return_body(**item_over):
    item = {"product_id": "P1", "product_name": "Aviator", "quantity": 1, "reason": "defective"}
    item.update(item_over)
    return {
        "vendor_id": "V1",
        "vendor_name": "Luxottica",
        "store_id": STORE,
        "items": [item],
        "return_type": "credit_note",
        # No piece of P1 at the shop: a typed line, with its note (PR #1234).
        "notes": "Made-to-order lens, never held in stock",
    }


_PRICE_KEYS = ("unit_price", "total_value", "credit_note_amount")


@pytest.mark.parametrize("path", ("/api/v1/vendor-returns", "/api/v1/vendor-returns/"))
def test_workshop_staff_log_a_vendor_return_without_a_price(client, returns_db, path):
    r = client.post(path, json=_return_body(), headers=_headers("WORKSHOP_STAFF"))
    assert r.status_code == 201, r.text
    assert not _cost_keys(r.json(), _PRICE_KEYS), r.json()
    stored = returns_db.vendor_returns.find_one({"return_id": r.json()["return_id"]})
    assert stored["items"][0]["unit_price"] is None
    assert stored["total_value"] is None  # unknown, never a made-up 0
    assert stored["created_by"] == "u-workshop_staff"


def test_a_price_sent_by_workshop_staff_is_refused(client, returns_db):
    # A line's price is the accounts roles' (owner rulings 2026-10-12 R2 + R3):
    # anyone else sending one is a 403 and nothing is written.
    r = client.post(
        "/api/v1/vendor-returns",
        json=_return_body(unit_price=999.0),
        headers=_headers("WORKSHOP_STAFF"),
    )
    assert r.status_code == 403, r.text
    assert returns_db.vendor_returns.count_documents({}) == 0


def test_workshop_staff_read_their_return_without_a_price(client, returns_db):
    h = _headers("WORKSHOP_STAFF")
    rid = client.post("/api/v1/vendor-returns", json=_return_body(), headers=h).json()[
        "return_id"
    ]
    for path in ("/api/v1/vendor-returns", f"/api/v1/vendor-returns/{rid}"):
        r = client.get(path, headers=h)
        assert r.status_code == 200, r.text
        assert not _cost_keys(r.json(), _PRICE_KEYS), r.json()
    # Still no status change for them: approve / ship / credit are the buyers'.
    r = client.patch(
        f"/api/v1/vendor-returns/{rid}/status", json={"status": "approved"}, headers=h
    )
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("role", ("STORE_MANAGER", "ADMIN"))
def test_only_the_accounts_roles_price_the_return(client, returns_db, role):
    h = _headers(role)
    r = client.post("/api/v1/vendor-returns", json=_return_body(), headers=h)
    assert r.status_code == 201, r.text  # logged unpriced; accounts price it later
    stored = returns_db.vendor_returns.find_one({"return_id": r.json()["return_id"]})
    assert stored["items"][0]["unit_price"] is None and stored["total_value"] is None
    r = client.post("/api/v1/vendor-returns", json=_return_body(unit_price=COST), headers=h)
    if role == "ADMIN":
        assert r.status_code == 201, r.text
        stored = returns_db.vendor_returns.find_one({"return_id": r.json()["return_id"]})
        assert stored["items"][0]["unit_price"] == COST and stored["total_value"] == COST
    else:
        assert r.status_code == 403, r.text


# Whose price the create door takes is cost_mask's "payables" rule, the one
# every return read answers: widen it there and the added role's price is
# taken. A written-out role set at the door would keep refusing it.
def test_widening_the_payables_rule_moves_the_return_pricing_door(
    client, returns_db, monkeypatch
):
    from api.services import cost_mask

    monkeypatch.setitem(cost_mask._CONTEXT_ROLES, "payables", {"STORE_MANAGER"})
    r = client.post(
        "/api/v1/vendor-returns", json=_return_body(unit_price=COST),
        headers=_headers("STORE_MANAGER"),
    )
    assert r.status_code == 201, r.text
    stored = returns_db.vendor_returns.find_one({"return_id": r.json()["return_id"]})
    assert stored["items"][0]["unit_price"] == COST


@pytest.mark.parametrize("role", COUNTER_ROLES[:4])
def test_other_counter_roles_still_cannot_log_a_return(client, returns_db, role):
    r = client.post("/api/v1/vendor-returns", json=_return_body(), headers=_headers(role))
    assert r.status_code == 403, r.text


def test_the_return_create_door_admits_the_readers_and_its_row_says_so():
    from api.routers import vendor_returns as vr
    from api.services import rbac_policy as rbac
    from api.services.capabilities import capability_for, capability_roles
    from api.services.cost_mask import PURCHASE_ROLES, RETURN_READERS

    for path in ("/api/v1/vendor-returns", "/api/v1/vendor-returns/"):
        row = rbac.policy_for("POST", path)
        assert set(row["allowed"]) == set(RETURN_READERS), row
        # Its own capability key, so workshop staff never join the
        # approve / ship / credit union (vendor-returns:write).
        assert capability_for("POST", path) == "vendor-returns:create"
    assert set(capability_roles("vendor-returns:write")) == set(PURCHASE_ROLES)
    assert set(vr._VENDOR_RETURN_ROLES) == set(PURCHASE_ROLES)


def _issue_debit_note(**price):
    import mongomock

    from api.services.rtv_debit_note import DebitNoteEngine

    line = {"product_id": "P1", "product_name": "Aviator", "quantity": 2,
            "reason": "defective", "gst_rate": 5, "hsn": "9003", **price}
    db = mongomock.MongoClient().db
    db.grns.insert_one({"grn_id": "G1", "vendor_id": "V1", "store_id": STORE,
                        "status": "ACCEPTED", "items": [{"product_id": "P1", "accepted_qty": 2}]})
    return DebitNoteEngine(db).issue(
        {"return_id": "VR-1", "vendor_id": "V1", "store_id": STORE, "grn_id": "G1", "items": [line]},
        {"vendor_id": "V1", "name": "Luxottica"},
        actor="u-1",
        seller={"gstin": "20AAAAA0000A1Z5", "name": "Better Vision"},
    )


def test_a_debit_note_is_refused_for_a_return_without_prices():
    res = _issue_debit_note(unit_price=None)
    assert res["ok"] is False and res["http"] == 422, res
    assert res["error"] == "price_required", res
    assert "price" in res["message"].lower()


# Every key a return line is priced by -- a vendor RMA line carries only
# unit_cost (routers/vendor_rma.py) -- issues a note at that price: the
# no-price refusal and the line's price read one list.
@pytest.mark.parametrize(
    "price",
    (
        {"rate_paise": 12345},
        {"unit_cost_paise": 12345},
        {"unit_cost": 123.45},
        {"rate": 123.45},
        {"unit_price": 123.45},
    ),
)
def test_a_priced_return_line_issues_its_debit_note_at_that_price(price):
    res = _issue_debit_note(**price)
    assert res["ok"] is True, res
    (line,) = res["debit_note"]["lines"]
    assert line["rate_paise"] == 12345 and line["taxable_paise"] == 24690, line


# ---------------------------------------------------------------------------
# The screens hide what the server hides: the frontend lists ARE cost_mask's
# ---------------------------------------------------------------------------
from tests.test_counter_roles_no_purchase_reads import (  # noqa: E402
    _COST_CELL,
    _assert_screens_read,
)

# The doors under test number their GST documents (a return's credit note,
# a debit note): these fakes hold no store records, so a stub mint numbers
# them (conftest.gst_numbers). The mint is tested in test_gst_document_numbers.
pytestmark = pytest.mark.usefixtures("gst_numbers")


@pytest.mark.skipif(not os.path.exists(_COST_CELL), reason="frontend/ not shipped here")
def test_frontend_cost_roles_are_the_purchase_context():
    from api.services import rbac_policy as rbac
    from api.services.cost_mask import can_see_cost

    with open(_COST_CELL, encoding="utf-8") as fh:
        m = re.search(r"COST_ROLES[^=]*=\s*\[([^\]]*)\]", fh.read())
    assert m, "CostCell.tsx no longer declares COST_ROLES"
    want = {r for r in rbac.ALL_ROLES if can_see_cost({"roles": [r]}, "purchase")}
    assert set(re.findall(r"'([A-Z_]+)'", m.group(1))) == want
    _assert_screens_read(
        "COST_ROLES",
        ("pages/purchase/VendorReturns.tsx", "components/inventory/StockAlertsOverview.tsx"),
    )


# ---------------------------------------------------------------------------
# A return the workshop logged: a manager approves the goods, accounts price it
# ---------------------------------------------------------------------------


def _workshop_return(client):
    r = client.post(
        "/api/v1/vendor-returns",
        json=_return_body(quantity=2),
        headers=_headers("WORKSHOP_STAFF"),
    )
    assert r.status_code == 201, r.text
    return r.json()["return_id"]


def test_a_manager_approves_an_unpriced_return_and_accounts_price_it(client, returns_db):
    rid = _workshop_return(client)
    r = client.patch(
        f"/api/v1/vendor-returns/{rid}/status",
        json={"status": "approved"},
        headers=_headers("STORE_MANAGER"),
    )
    assert r.status_code == 200, r.text
    r = client.put(
        f"/api/v1/vendor-returns/{rid}/prices",
        json={"unit_prices": [COST]},
        headers=_headers("STORE_MANAGER"),
    )
    assert r.status_code == 403, r.text
    r = client.put(
        f"/api/v1/vendor-returns/{rid}/prices",
        json={"unit_prices": [COST]},
        headers=_headers("ACCOUNTANT"),
    )
    assert r.status_code == 200, r.text
    stored = returns_db.vendor_returns.find_one({"return_id": rid})
    assert stored["status"] == "approved"
    assert stored["items"][0]["unit_price"] == COST
    assert stored["total_value"] == round(2 * COST, 2)
    # Workshop staff and the manager still read it without the price.
    for role in ("WORKSHOP_STAFF", "STORE_MANAGER"):
        r = client.get(f"/api/v1/vendor-returns/{rid}", headers=_headers(role))
        assert not _cost_keys(r.json(), _PRICE_KEYS) and not _figures_in(r.json()), r.json()


# Both pricing doors (create and PUT /prices) take a price by one type: a crore
# at most, never NaN / Infinity -- either would be stored and then break every
# read of the Vendor Returns list. Raw JSON, since NaN / Infinity are not JSON
# a client library will write.
_BAD_PRICES = ("20000000", "NaN", "Infinity", "-1")


@pytest.mark.parametrize("bad", _BAD_PRICES)
def test_both_return_doors_refuse_a_price_outside_the_one_rule(client, returns_db, bad):
    h = {**_headers("ACCOUNTANT"), "Content-Type": "application/json"}
    rid = _workshop_return(client)
    r = client.put(
        f"/api/v1/vendor-returns/{rid}/prices",
        content=f'{{"unit_prices": [{bad}]}}',
        headers=h,
    )
    assert r.status_code == 422, (bad, r.text)
    body = json.dumps(_return_body(unit_price=0.0)).replace("0.0", bad)
    r = client.post("/api/v1/vendor-returns", content=body, headers=h)
    assert r.status_code == 422, (bad, r.text)
    stored = returns_db.vendor_returns.find_one({"return_id": rid})
    assert stored["status"] == "created" and stored["items"][0]["unit_price"] is None
    # The list still loads for the accounts roles.
    r = client.get("/api/v1/vendor-returns", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 1, r.text


def test_the_top_price_is_taken_at_both_doors(client, returns_db):
    h = _headers("ACCOUNTANT")
    rid = _workshop_return(client)  # 2 pieces
    r = client.put(
        f"/api/v1/vendor-returns/{rid}/prices",
        json={"unit_prices": [10_000_000]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert returns_db.vendor_returns.find_one({"return_id": rid})["total_value"] == 20_000_000
    r = client.post(
        "/api/v1/vendor-returns", json=_return_body(unit_price=10_000_000), headers=h
    )
    assert r.status_code == 201, r.text
