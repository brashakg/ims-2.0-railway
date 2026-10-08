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
api/services/stock_value.py, seen through cost_mask.can_see_cost(user, "purchase"))
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


def test_f47_a_reserved_unit_is_still_stock_at_cost():
    """A frame reserved for a customer's order is still ours and on the shelf
    until it is sold, so the cost headline counts it -- and the Selling value
    tile counts the same units (stock + reserved, InventoryLayout.tsx), so the
    pair compares. Mutating stock_value to include_reserved=False turns this red."""
    mongomock = pytest.importorskip("mongomock")
    from api.services import stock_value

    coll = mongomock.MongoClient().db.stock_units
    coll.insert_many(
        [
            {"stock_id": "A", "product_id": "P", "store_id": "S1", "status": "AVAILABLE", "unit_cost": 3100},
            {"stock_id": "R", "product_id": "P", "store_id": "S1", "status": "RESERVED", "unit_cost": 3200},
            {"stock_id": "X", "product_id": "P", "store_id": "S1", "status": "SOLD", "unit_cost": 3000},
        ]
    )

    class Repo:
        def find_many(self, flt, limit=0):
            return list(coll.find(flt, {"_id": 0}))

    units = stock_value.shelf_units(Repo(), None, "S1")
    assert sorted(u["stock_id"] for u in units) == ["A", "R"]
    assert stock_value.total(units) == 6300.0
    # uncosted_units / unit_cost joined the shape with the "no cost" fix below.
    assert stock_value.by_product(units)["P"] == {
        "units": 2.0, "cost": 6300.0, "uncosted_units": 0.0, "unit_cost": 3150.0,
    }



# ============================================================================
# Review r1 #34: a unit with no known cost is not "Rs 0"
# ============================================================================
# A shop holding 2 units of a frame nobody priced (no unit_cost / cost_price on
# the units, no cost_price on the product) read "Cost / unit Rs 0" on the ledger
# and added 0 to the "Stock value at cost" headline, which then looked
# complete. The one rule (stock_value) now counts such units as UNCOSTED: they
# add nothing to the value, the ledger row says unit_cost None and
# uncosted_units N, and the tile can say "N units have no cost".
#
# The world (its own database, one shop):
#   P-NOCOST  2 on the shelf, no cost anywhere          -> unit_cost None, 2 uncosted
#   P-MIXED   1 at 4000 + 1 with unit_cost 0 / no price -> unit_cost 4000 (not 2000), 1 uncosted
#   P-MASTER  1 with no own cost, product cost 2500     -> 2500 from the product, costed
#   P-HELD    3 on the shelf + 1 reserved, 1000 each    -> stock 3, reserved 1, cost 4000

_UNCOSTED_PRODUCTS = {
    "P-NOCOST": None,
    "P-MIXED": None,
    "P-MASTER": 2500.0,
    "P-HELD": None,
}


def _seed_uncosted(db) -> None:
    now = datetime.utcnow()
    for pid, master_cost in _UNCOSTED_PRODUCTS.items():
        doc = {
            "_id": pid, "product_id": pid, "sku": f"FR-{pid}", "brand": "Test",
            "model": pid, "name": f"Frame {pid}", "category": "FRAME",
            "mrp": 9000.0, "offer_price": 8000.0, "is_active": True,
        }
        if master_cost is not None:
            doc["cost_price"] = master_cost
        db["products"].insert_one(doc)

    def unit(sid, pid, status="AVAILABLE", **cost):
        return {
            "_id": sid, "stock_id": sid, "product_id": pid, "store_id": STORE,
            "barcode": f"BV{sid}", "quantity": 1, "status": status,
            # Past the aging grace window (#1169, F54), so an unsold product
            # gets its slow-mover verdict rather than NEW.
            "created_at": now - timedelta(days=120), **cost,
        }

    db["stock_units"].insert_many(
        [
            unit("N1", "P-NOCOST"),
            unit("N2", "P-NOCOST", unit_cost=None, cost_price=0),
            unit("M1", "P-MIXED", unit_cost=4000.0, cost_price=4000.0),
            unit("M2", "P-MIXED", unit_cost=0, cost_price=None),
            unit("S1", "P-MASTER"),
            unit("H1", "P-HELD", unit_cost=1000.0),
            unit("H2", "P-HELD", unit_cost=1000.0),
            unit("H3", "P-HELD", unit_cost=1000.0),
            unit("H4", "P-HELD", status="RESERVED", unit_cost=1000.0),
            unit("H5", "P-HELD", status="SOLD", unit_cost=1000.0),
        ]
    )


@pytest.fixture(scope="module")
def uncosted_db():
    mongomock = pytest.importorskip("mongomock")
    db = mongomock.MongoClient()[f"ims_test_uncosted_{uuid.uuid4().hex[:8]}"]
    _seed_uncosted(db)
    return db


@pytest.fixture
def uncosted_world(uncosted_db, monkeypatch):
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    stock = lambda: StockRepository(uncosted_db["stock_units"])  # noqa: E731
    products = lambda: ProductRepository(uncosted_db["products"])  # noqa: E731
    monkeypatch.setattr(inv_mod, "get_stock_repository", stock)
    monkeypatch.setattr(inv_mod, "get_product_repository", products)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: _DBProxy(uncosted_db))
    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")
    return _World(TestClient(app), app)


@pytest.fixture
def uncosted_reports(uncosted_db, monkeypatch):
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    stock = lambda: StockRepository(uncosted_db["stock_units"])  # noqa: E731
    products = lambda: ProductRepository(uncosted_db["products"])  # noqa: E731
    monkeypatch.setattr(reports_mod, "get_stock_repository", stock)
    monkeypatch.setattr(reports_mod, "get_product_repository", products)
    monkeypatch.setattr(reports_mod, "get_db", lambda: _DBProxy(uncosted_db))
    app = FastAPI()
    app.include_router(reports_mod.router, prefix="/reports")
    return _World(TestClient(app), app)


def _rows_by_pid(world, role):
    resp = world.get("/inventory/stock", role)
    assert resp.status_code == 200, resp.text
    return {r["product_id"]: r for r in resp.json()["items"]}


def test_r1_34_a_frame_nobody_priced_has_no_unit_cost_not_rs_0(uncosted_world):
    row = _rows_by_pid(uncosted_world, "STORE_MANAGER")["P-NOCOST"]
    assert row["stock"] == 2
    assert row["unit_cost"] is None, f"unit_cost {row['unit_cost']!r} for 2 unpriced units"
    assert row["uncosted_units"] == 2
    assert row["cost_value"] == 0


def test_r1_34_an_unpriced_unit_does_not_halve_the_unit_cost(uncosted_world):
    """1 unit at 4000 + 1 with no price: the per-unit cost is 4000 (the costed
    unit), and the other unit is counted as having no cost -- not 2000."""
    row = _rows_by_pid(uncosted_world, "STORE_MANAGER")["P-MIXED"]
    assert row["cost_value"] == pytest.approx(4000.0)
    assert row["unit_cost"] == pytest.approx(4000.0)
    assert row["uncosted_units"] == 1


def test_r1_34_the_product_cost_still_prices_a_unit_without_its_own(uncosted_world):
    row = _rows_by_pid(uncosted_world, "STORE_MANAGER")["P-MASTER"]
    assert row["unit_cost"] == pytest.approx(2500.0)
    assert row["uncosted_units"] == 0


@pytest.mark.parametrize("role", ["STORE_MANAGER", "ACCOUNTANT", "ADMIN"])
def test_r1_34_the_rows_add_up_to_the_one_rule_with_the_uncosted_count(
    uncosted_world, uncosted_db, role
):
    """What the 'Stock value at cost' tile sums (the ledger rows) equals the one
    rule's total, and the uncosted pieces it reports equal stock_value's."""
    from api.services import stock_value
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    rows = _rows_by_pid(uncosted_world, role).values()
    units = stock_value.shelf_units(
        StockRepository(uncosted_db["stock_units"]),
        ProductRepository(uncosted_db["products"]),
        STORE,
    )
    assert sum(r["cost_value"] for r in rows) == pytest.approx(stock_value.total(units))
    assert stock_value.total(units) == pytest.approx(4000.0 + 2500.0 + 4000.0)
    assert sum(r["uncosted_units"] for r in rows) == stock_value.uncosted(units) == 3


@pytest.mark.parametrize("role", COUNTER)
def test_r1_34_the_counter_gets_no_cost_and_no_uncosted_count(uncosted_world, role):
    for pid, row in _rows_by_pid(uncosted_world, role).items():
        leaked = [k for k in (*COST_KEYS, "uncosted_units") if k in row]
        assert not leaked, f"{role} row {pid} carries {leaked}"


def test_r1_39_stock_is_the_shelf_reserved_is_apart_and_cost_covers_both(uncosted_world):
    """Review r1 #39, the server half: a row's `stock` is the AVAILABLE units
    only and `reserved` is counted apart (never inside stock), while its
    cost_value covers both -- so the screen shows stock as available (no
    second subtraction) and stock + reserved is what the value tiles count."""
    row = _rows_by_pid(uncosted_world, "STORE_MANAGER")["P-HELD"]
    assert (row["stock"], row["reserved"]) == (3, 1)
    assert row["cost_value"] == pytest.approx(4000.0)  # 4 units x 1000, sold H5 not stock
    assert row["unit_cost"] == pytest.approx(1000.0)


def test_f47_stock_aging_counts_the_units_with_no_cost(uncosted_world):
    """Stock aging values at cost like the ledger: a unit with no cost adds
    nothing, and is COUNTED (per product and beside Tied capital), never a
    silent Rs 0 row."""
    body = uncosted_world.get("/inventory/aging", "STORE_MANAGER").json()
    rows = {p["id"]: p for p in body["products"]}
    assert (rows["P-NOCOST"]["value"], rows["P-NOCOST"]["uncostedUnits"]) == (0.0, 2)
    assert (rows["P-MIXED"]["value"], rows["P-MIXED"]["uncostedUnits"]) == (4000.0, 1)
    assert rows["P-MASTER"]["uncostedUnits"] == 0
    slow = [p for p in body["products"] if p["classification"] == "C"]
    assert body["summary"]["slowMovingUncostedUnits"] == sum(
        p["uncostedUnits"] for p in slow
    ) == 3


@pytest.mark.parametrize("role", COUNTER)
def test_f47_the_counter_gets_no_uncosted_count_on_aging(uncosted_world, role):
    body = uncosted_world.get("/inventory/aging", role).json()
    assert body["summary"]["slowMovingUncostedUnits"] is None
    assert all(p["uncostedUnits"] is None for p in body["products"])


@pytest.mark.parametrize(
    "path,params,count",
    [
        ("/reports/inventory", {}, lambda b: b["uncostedUnits"]),
        ("/reports/inventory/summary", {}, lambda b: b["summary"]["uncosted_units"]),
        ("/reports/inventory/valuation", {}, lambda b: b["valuation"]["uncosted_units"]),
        (
            "/reports/stock/count",
            {"from_date": "2026-09-01", "to_date": "2026-09-29"},
            lambda b: b["summary"]["uncosted_units"],
        ),
    ],
)
def test_f47_every_stock_value_report_counts_the_units_with_no_cost(
    uncosted_reports, path, params, count
):
    resp = uncosted_reports.get(path, "ADMIN", **params)
    assert resp.status_code == 200, resp.text
    assert count(resp.json()) == 3


def test_r1_34_the_one_rule_counts_uncosted_pieces():
    """stock_value itself: a zero / blank / junk cost is no price; such a unit
    adds 0, is flagged cost_known False and counted by uncosted()."""
    mongomock = pytest.importorskip("mongomock")
    from api.services import stock_value

    coll = mongomock.MongoClient().db.stock_units
    coll.insert_many(
        [
            {"stock_id": "A", "product_id": "P", "store_id": "S1", "status": "AVAILABLE", "unit_cost": 3100},
            {"stock_id": "Z", "product_id": "P", "store_id": "S1", "status": "AVAILABLE", "unit_cost": 0},
            {"stock_id": "J", "product_id": "P", "store_id": "S1", "status": "RESERVED", "unit_cost": "n/a", "quantity": 2},
        ]
    )

    class Repo:
        def find_many(self, flt, limit=0):
            return list(coll.find(flt, {"_id": 0}))

    units = stock_value.shelf_units(Repo(), None, "S1")
    assert {u["stock_id"]: u["cost_known"] for u in units} == {"A": True, "Z": False, "J": False}
    assert stock_value.total(units) == 3100.0
    assert stock_value.uncosted(units) == 3
    assert stock_value.by_product(units)["P"] == {
        "units": 4.0, "cost": 3100.0, "uncosted_units": 3.0, "unit_cost": 3100.0,
    }


# ============================================================================
# Review r3 #6: a product held ONLY as reserved stock still has its ledger row
# ============================================================================
# The "Stock value at cost . shelf + reserved" tile (InventoryLayout.tsx) sums
# the /inventory/stock ledger rows' cost_value; /reports/inventory sums every
# unit the one rule (stock_value.shelf_units) counts, reserved ones included.
# The ledger listed the active catalogue plus stranded products that had units
# ON THE SHELF -- so a discontinued frame whose only unit here is set aside for
# a customer's order got no row, and the tile read less than the report.
#
# The world (its own database):
#   P-ACT   active,      1 on the shelf + 1 reserved, 800 each   -> 1600
#   P-OLD   deactivated, 1 reserved at 1200 (Dhanbad)             -> 1200
#           + 1 reserved at 5000 at ANOTHER shop                   -> not Dhanbad's
#   P-GONE  deactivated, its only unit here SOLD                   -> no row
#   Dhanbad's stock at cost = 1600 + 1200 = 2800

_R3_HELD_AT_COST = 2800.0


def _seed_reserved_only(db) -> None:
    now = datetime.utcnow()
    for pid, active in (("P-ACT", True), ("P-OLD", False), ("P-GONE", False)):
        db["products"].insert_one(
            {
                "_id": pid, "product_id": pid, "sku": f"FR-{pid}", "brand": "Test",
                "model": pid, "name": f"Frame {pid}", "category": "FRAME",
                "mrp": 3000.0, "offer_price": 2500.0, "is_active": active,
            }
        )

    def unit(sid, pid, status, cost, store=STORE):
        return {
            "_id": sid, "stock_id": sid, "product_id": pid, "store_id": store,
            "barcode": f"BV{sid}", "quantity": 1, "status": status,
            "unit_cost": cost, "cost_price": cost,
            "created_at": now - timedelta(days=10),
        }

    db["stock_units"].insert_many(
        [
            unit("A1", "P-ACT", "AVAILABLE", 800.0),
            unit("A2", "P-ACT", "RESERVED", 800.0),
            unit("O1", "P-OLD", "RESERVED", 1200.0),
            unit("O2", "P-OLD", "RESERVED", 5000.0, store=OTHER),
            unit("G1", "P-GONE", "SOLD", 700.0),
        ]
    )


@pytest.fixture(scope="module")
def reserved_only_db():
    mongomock = pytest.importorskip("mongomock")
    db = mongomock.MongoClient()[f"ims_test_reserved_only_{uuid.uuid4().hex[:8]}"]
    _seed_reserved_only(db)
    return db


@pytest.fixture
def reserved_only_world(reserved_only_db, monkeypatch):
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    stock = lambda: StockRepository(reserved_only_db["stock_units"])  # noqa: E731
    products = lambda: ProductRepository(reserved_only_db["products"])  # noqa: E731
    for mod in (inv_mod, reports_mod):
        monkeypatch.setattr(mod, "get_stock_repository", stock)
        monkeypatch.setattr(mod, "get_product_repository", products)
    proxy = _DBProxy(reserved_only_db)
    monkeypatch.setattr(inv_mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(reports_mod, "get_db", lambda: proxy)
    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")
    app.include_router(reports_mod.router, prefix="/reports")
    return _World(TestClient(app), app)


def test_r3_6_a_frame_held_only_as_reserved_has_its_ledger_row(reserved_only_world):
    rows = _rows_by_pid(reserved_only_world, "STORE_MANAGER")
    assert "P-OLD" in rows, f"no ledger row for the reserved-only frame: {sorted(rows)}"
    row = rows["P-OLD"]
    assert (row["stock"], row["reserved"]) == (0, 1)
    # Only Dhanbad's reserved unit -- the one at another shop is not ours here.
    assert row["cost_value"] == pytest.approx(1200.0)
    assert row["unit_cost"] == pytest.approx(1200.0)
    # A discontinued product with nothing held here (its unit was sold) is
    # still not listed: the row is for held stock, not every unit ever.
    assert "P-GONE" not in rows


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT", "ADMIN"])
def test_r3_6_the_cost_tile_equals_the_stock_value_report(reserved_only_world, role):
    """The tile's sum of the ledger rows == /reports/inventory totalValue: one
    'shelf + reserved at cost' figure, the reserved-only frame in both."""
    rows = _rows_by_pid(reserved_only_world, role).values()
    tile = sum(r.get("cost_value") or 0 for r in rows)
    resp = reserved_only_world.get("/reports/inventory", role)
    assert resp.status_code == 200, resp.text
    report = resp.json()["totalValue"]
    assert report == pytest.approx(_R3_HELD_AT_COST)
    assert tile == pytest.approx(report), (
        f"{role}: the cost tile reads {tile}, /reports/inventory {report}"
    )


@pytest.mark.parametrize("role", COUNTER)
def test_r3_6_the_counter_sees_the_reserved_row_without_cost(reserved_only_world, role):
    """The new row is masked exactly like every other: the counter sees the
    held frame (stock 0, reserved 1) and no cost figure on it."""
    row = _rows_by_pid(reserved_only_world, role)["P-OLD"]
    assert row["reserved"] == 1
    leaked = [k for k in (*COST_KEYS, "uncosted_units") if k in row]
    assert not leaked, f"{role} reserved-only row carries {leaked}"


def test_r3_6_the_reserved_only_row_obeys_the_category_filter(reserved_only_world):
    """The stranded-row filters still apply to it: a FRAME held as reserved is
    listed under FRAME and left out of a SUNGLASS view."""
    frames = reserved_only_world.get("/inventory/stock", "STORE_MANAGER", category="FRAME")
    sunglasses = reserved_only_world.get("/inventory/stock", "STORE_MANAGER", category="SUNGLASS")
    assert frames.status_code == sunglasses.status_code == 200
    assert "P-OLD" in {r["product_id"] for r in frames.json()["items"]}
    assert "P-OLD" not in {r["product_id"] for r in sunglasses.json()["items"]}


# ============================================================================
# R3 on the stock-value reads: a login with no shop reads no shop's stock
# ============================================================================
# stock_value.shelf_units(None) is EVERY shop (the admins' all-shops view).
# /reports/stock/count, /reports/inventory/summary and /valuation took their
# shop from `validate_store_access(...) or active_store_id` -- None for a
# non-admin with no shop -- so that login read every shop's stock at cost
# (Dhanbad 3 x 1000 + Pune 5 x 2000 = 13000). The one shop rule
# (resolve_store_scope) refuses it.

_R3_READS = (
    ("/reports/stock/count", {"from_date": "2026-09-01", "to_date": "2026-09-29"}),
    ("/reports/inventory/summary", {}),
    ("/reports/inventory/valuation", {}),
)


@pytest.fixture(scope="module")
def two_shop_db():
    mongomock = pytest.importorskip("mongomock")
    db = mongomock.MongoClient()[f"ims_test_two_shop_{uuid.uuid4().hex[:8]}"]
    db["products"].insert_one(
        {"_id": "P", "product_id": "P", "sku": "FR-P", "name": "Frame P",
         "category": "FRAME", "mrp": 9000.0, "is_active": True}
    )
    db["stock_units"].insert_many(
        [
            {"_id": f"{store}-{i}", "stock_id": f"{store}-{i}", "product_id": "P",
             "store_id": store, "quantity": 1, "status": "AVAILABLE", "unit_cost": cost}
            for store, n, cost in ((STORE, 3, 1000.0), (OTHER, 5, 2000.0))
            for i in range(n)
        ]
    )
    return db


@pytest.fixture
def two_shop_reports(two_shop_db, monkeypatch):
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    monkeypatch.setattr(reports_mod, "get_stock_repository", lambda: StockRepository(two_shop_db["stock_units"]))
    monkeypatch.setattr(reports_mod, "get_product_repository", lambda: ProductRepository(two_shop_db["products"]))
    monkeypatch.setattr(reports_mod, "get_db", lambda: _DBProxy(two_shop_db))
    app = FastAPI()
    app.include_router(reports_mod.router, prefix="/reports")
    client = TestClient(app)

    def get(path, user, **params):
        app.dependency_overrides[get_current_user] = lambda: user
        return client.get(path, params=params)

    return get


def _shopless(role: str) -> dict:
    return {"user_id": f"u-{role.lower()}", "roles": [role], "store_ids": [], "active_store_id": None}


def _stock_value(body: dict) -> float:
    return (body.get("summary") or {}).get("total_value") or (body.get("valuation") or {}).get("total")


@pytest.mark.parametrize("path,params", _R3_READS)
@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"])
def test_r3_a_login_with_no_shop_reads_no_shops_stock(two_shop_reports, path, params, role):
    resp = two_shop_reports(path, _shopless(role), **params)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Your login has no shop assigned - ask an admin to assign one."
    assert "13000" not in resp.text


@pytest.mark.parametrize("path,params", _R3_READS[:2])
def test_r3_the_counter_with_no_shop_gets_no_counts_either(two_shop_reports, path, params):
    resp = two_shop_reports(path, _shopless("SALES_STAFF"), **params)
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("path,params", _R3_READS)
def test_r3_shops_and_admins_still_read_their_stock(two_shop_reports, path, params):
    dhn = {"user_id": "u-sm", "roles": ["STORE_MANAGER"], "store_ids": [STORE], "active_store_id": STORE}
    assert _stock_value(two_shop_reports(path, dhn, **params).json()) == 3000.0
    # An admin with no shop asked reads every shop (admins see all shops).
    assert _stock_value(two_shop_reports(path, _shopless("ADMIN"), **params).json()) == 13000.0
