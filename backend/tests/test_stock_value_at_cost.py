"""
IMS 2.0 -- WHAT MY STOCK COST ME (audit F47)
============================================
Owner audit 2026-09-29, row F47: "Stock value - total landed inventory" is
really the SELLING price (Rs 5.0L against an actual cost of 2.8L); Stock aging's
"Tied capital" uses MRP (5.8L) -- three rupee figures for the same 83 units,
none of them the purchase cost, and no cost per unit anywhere.

Owner ruling 2026-09-28 (second set, Reports / scope): the stock value headline
is at COST (it matches the bills); selling value is shown separately and
labelled; managers see per-unit cost, sales staff never.

The world, written out by hand -- one frame at Dhanbad:
  product  MRP 5800, offer 5000, master cost 3150
  unit U1  on the shelf, received at cost 3100
  unit U2  on the shelf, received at cost 3200
  unit U3  SOLD 100 days ago (cost 3000) -- no longer stock
  unit U4  on the shelf at ANOTHER shop (cost 9999) -- not Dhanbad's stock

  Dhanbad's stock AT COST = 3100 + 3200 = 6300 (per unit 3150)
  at offer price 2 x 5000 = 10000, at MRP 2 x 5800 = 11600  (selling values)
  what the report copies sum today = every Dhanbad unit ever = 9300

Where the numbers come from today (file:line on main 381390d):
  * the headline tile  frontend/src/pages/inventory/InventoryLayout.tsx:123
      sum((offerPrice || mrp) * stock), labelled "total landed inventory"
      -- the ledger rows it sums carry NO cost (inventory/stock.py _ledger_row)
  * Stock aging        backend/api/routers/inventory/aging.py:176
      value = qty * product.mrp; summary.slowMovingValue ("Tied Capital",
      StockAgingReport.tsx:330) sums it
  * four report copies of "stock value", each quantity * cost_price over EVERY
    unit at the store, sold ones included:
      reports/overview.py:156 /reports/inventory       (totalValue)
      reports/inventory.py:25 /reports/inventory/summary (summary.total_value)
      reports/inventory.py:66 /reports/inventory/valuation (valuation.total)
      reports/workshop.py:479 /reports/stock/count      (summary.total_value)
    three of them open to ANY login, so the counter reads cost today.

Contract pinned (the build follows it): the stock ledger rows carry
`cost_value` (on-hand units at cost) and `unit_cost` for the cost readers
(managers, accounts, admins) and never for the counter; every stock-value
report equals the same on-hand cost; aging values stock at cost.

These were strict xfails while the finding was open; the fix (one rule,
api/services/stock_value.py, seen through cost_mask.can_see_cost(user, "stock"))
turned them into plain tests. A regression raises FindingStillOpen.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_stock_value_at_cost.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv_mod  # noqa: E402
from api.routers import reports as reports_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "BV-DHN-01"
OTHER = "WO-PUN-01"
PID = "P-CARRERA-8895"
AT_COST = 6300.0
COST_KEYS = ("cost_price", "cost_value", "unit_cost")
COUNTER = ("SALES_STAFF", "SALES_CASHIER", "CASHIER")


class FindingStillOpen(AssertionError):
    """The audited behaviour is still there (not a broken fixture)."""


def _open(ok: bool, message: str) -> None:
    if not ok:
        raise FindingStillOpen(message)


def _user(role: str) -> dict:
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": [STORE],
        "active_store_id": STORE,
    }


@pytest.fixture(scope="module")
def mongo_db():
    from pymongo import MongoClient

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    db_name = f"ims_test_stockcost_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return
        client = mongomock.MongoClient()
    db = client[db_name]
    _seed(db)
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:
            pass
        client.close()


def _seed(db) -> None:
    now = datetime.utcnow()
    db["products"].insert_one(
        {
            "_id": PID,
            "product_id": PID,
            "sku": "FR-CARRERA-CA8895-807-54",
            "brand": "Carrera",
            "model": "CA 8895",
            "name": "Carrera CA 8895",
            "category": "FRAME",
            "mrp": 5800.0,
            "offer_price": 5000.0,
            "cost_price": 3150.0,
            "is_active": True,
        }
    )

    def unit(sid, store, status, cost, **extra):
        return {
            "_id": sid,
            "stock_id": sid,
            "product_id": PID,
            "store_id": store,
            "barcode": f"BV{sid}",
            "quantity": 1,
            "status": status,
            "unit_cost": cost,
            "cost_price": cost,
            "cost_source": "GRN_PO",
            "created_at": now - timedelta(days=120),
            **extra,
        }

    db["stock_units"].insert_many(
        [
            unit("U1", STORE, "AVAILABLE", 3100.0),
            unit("U2", STORE, "AVAILABLE", 3200.0),
            unit("U3", STORE, "SOLD", 3000.0, sold_at=now - timedelta(days=100)),
            unit("U4", OTHER, "AVAILABLE", 9999.0),
        ]
    )


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


class _World:
    def __init__(self, client: TestClient, app: FastAPI):
        self._client = client
        self._app = app

    def get(self, path: str, role: str, **params):
        user = _user(role)

        async def _as_user():
            return dict(user)

        self._app.dependency_overrides[get_current_user] = _as_user
        return self._client.get(path, params={"store_id": STORE, **params})


@pytest.fixture
def world(mongo_db, monkeypatch):
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    stock = lambda: StockRepository(mongo_db["stock_units"])  # noqa: E731
    products = lambda: ProductRepository(mongo_db["products"])  # noqa: E731
    for mod in (inv_mod, reports_mod):
        monkeypatch.setattr(mod, "get_stock_repository", stock)
        monkeypatch.setattr(mod, "get_product_repository", products)
    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(reports_mod, "get_db", lambda: proxy)

    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")
    app.include_router(reports_mod.router, prefix="/reports")
    return _World(TestClient(app), app)


def _ledger_row(world, role):
    resp = world.get("/inventory/stock", role)
    assert resp.status_code == 200, resp.text
    rows = [r for r in resp.json()["items"] if r.get("product_id") == PID]
    assert len(rows) == 1, rows
    return rows[0]


# ============================================================================
# The anchor
# ============================================================================


def test_the_shelf_holds_two_units_worth_6300_at_cost(world):
    """Not a finding: the ledger sees exactly the two Dhanbad units on the shelf,
    so every value below has one right answer (2 x the unit costs = 6300)."""
    row = _ledger_row(world, "STORE_MANAGER")
    assert row["stock"] == 2
    assert row["offerPrice"] == pytest.approx(5000.0)
    assert row["mrp"] == pytest.approx(5800.0)


# ============================================================================
# The headline: the ledger rows the tile sums carry the cost
# ============================================================================


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT", "ADMIN"])
def test_f47_managers_get_the_shelf_at_cost_and_the_unit_cost(world, role):
    row = _ledger_row(world, role)
    _open(
        row.get("cost_value") == pytest.approx(AT_COST),
        f"F47: {role} ledger row cost_value={row.get('cost_value')!r}, the shelf cost {AT_COST}",
    )
    _open(
        row.get("unit_cost") == pytest.approx(3150.0),
        f"F47: {role} sees no per-unit cost (unit_cost={row.get('unit_cost')!r})",
    )


@pytest.mark.parametrize("role", COUNTER)
def test_the_counter_never_gets_cost_on_the_ledger(world, role):
    """Guard for the fix above (holds today only because there is no cost at all):
    sales staff never see cost, per unit or in total."""
    row = _ledger_row(world, role)
    leaked = [k for k in COST_KEYS if row.get(k) is not None]
    assert not leaked, f"{role} ledger row carries {leaked}"


# ============================================================================
# Stock aging's "Tied capital"
# ============================================================================


def _aging(world, role):
    resp = world.get("/inventory/aging", role)
    assert resp.status_code in (200, 403), resp.text
    return resp


def test_f47_stock_aging_tied_capital_is_at_cost(world):
    body = _aging(world, "STORE_MANAGER").json()
    products = {p["id"]: p for p in body["products"]}
    assert products[PID]["quantity"] == 2
    assert products[PID]["classification"] == "C"  # no sale in 90 days -> slow
    _open(
        products[PID]["value"] == pytest.approx(AT_COST),
        f"F47: aging values the frame at {products[PID]['value']}, cost {AT_COST}",
    )
    _open(
        body["summary"]["slowMovingValue"] == pytest.approx(AT_COST),
        f"F47: Tied Capital {body['summary']['slowMovingValue']}, cost {AT_COST}",
    )


@pytest.mark.parametrize("role", COUNTER)
def test_f47_the_counter_never_gets_aging_values(world, role):
    resp = _aging(world, role)
    if resp.status_code == 403:
        return
    body = resp.json()
    values = [p.get("value") for p in body["products"]] + [
        (body.get("summary") or {}).get("slowMovingValue")
    ]
    _open(
        all(v is None for v in values),
        f"F47: {role} reads aging rupee values {values}",
    )


# ============================================================================
# The report copies of "stock value": one rule, on-hand units at cost
# ============================================================================

_REPORTS = {
    "/reports/inventory": ({}, lambda b: [b.get("totalValue")] + [c.get("value") for c in b.get("categories") or []]),
    "/reports/inventory/summary": ({}, lambda b: [(b.get("summary") or {}).get("total_value")]),
    "/reports/inventory/valuation": (
        {},
        lambda b: [(b.get("valuation") or {}).get("total")]
        + [c.get("value") for c in (b.get("valuation") or {}).get("by_category") or []],
    ),
    "/reports/stock/count": (
        {"from_date": "2026-09-01", "to_date": "2026-09-29"},
        lambda b: [(b.get("summary") or {}).get("total_value")]
        + [c.get("total_value") for c in b.get("data") or []],
    ),
}


@pytest.mark.parametrize("path", list(_REPORTS))
def test_f47_every_stock_value_report_is_the_shelf_at_cost(world, path):
    params, _values = _REPORTS[path]
    resp = world.get(path, "ADMIN", **params)
    assert resp.status_code == 200, resp.text
    headline = _values(resp.json())[0]
    _open(
        headline == pytest.approx(AT_COST),
        f"F47: {path} says the stock is worth {headline}, the shelf cost {AT_COST}",
    )


@pytest.mark.parametrize(
    "path",
    [
        "/reports/inventory",
        "/reports/inventory/summary",
        "/reports/stock/count",
        # Gated to the finance-report roles.
        "/reports/inventory/valuation",
    ],
)
def test_f47_the_counter_never_reads_stock_at_cost(world, path):
    params, values = _REPORTS[path]
    resp = world.get(path, "SALES_STAFF", **params)
    assert resp.status_code in (200, 403), resp.text
    if resp.status_code == 403:
        return
    found = [v for v in values(resp.json()) if v is not None]
    _open(not found, f"F47: SALES_STAFF reads cost figures {found} from {path}")
