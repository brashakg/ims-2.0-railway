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
`cost_value` (on-hand units at cost) and `unit_cost` for the roles that see
stock at cost and never for anyone else; every stock-value report equals the
same on-hand cost; aging values stock at cost (costValue) beside its selling
value.

Owner ruling 2026-10-12 (what we pay, stock value at cost included, is ADMIN /
SUPERADMIN / ACCOUNTANT only): those roles are stock_value.COST_CONTEXT, the
accounts-only cost_mask context. Store, area and catalogue managers read the
selling value main always showed them (the aging `value` = quantity x MRP)
and no stock-at-cost figure -- section "Managers" below.

These were strict xfails while the finding was open; the fix (one rule,
api/services/stock_value.py, seen through stock_value.COST_CONTEXT) turned
them into plain tests. A regression raises FindingStillOpen.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_stock_value_at_cost.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import re
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
# Who sees stock at cost (stock_value.COST_CONTEXT) and who reads selling value.
ACCOUNTS = ("ACCOUNTANT", "ADMIN")
MANAGERS = ("STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER")
# Every key a stock-at-cost figure travels under, on any of these reads.
STOCK_COST_KEYS = ("cost_value", "unit_cost", "uncosted_units", "costValue",
                   "uncostedUnits", "slowMovingCostValue", "slowMovingUncostedUnits")


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


class _Products:
    """A product repository answering find_by_id from a dict."""

    def __init__(self, docs):
        self._docs = docs

    def find_by_id(self, pid):
        return self._docs.get(pid)


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


@pytest.mark.parametrize("role", ACCOUNTS)
def test_f47_accounts_get_the_shelf_at_cost_and_the_unit_cost(world, role):
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


@pytest.mark.parametrize("role", ACCOUNTS)
def test_f47_stock_aging_tied_capital_is_at_cost(world, role):
    body = _aging(world, role).json()
    products = {p["id"]: p for p in body["products"]}
    assert products[PID]["quantity"] == 2
    assert products[PID]["classification"] == "C"  # no sale in 90 days -> slow
    _open(
        products[PID]["costValue"] == pytest.approx(AT_COST),
        f"F47: aging values the frame at {products[PID]['costValue']}, cost {AT_COST}",
    )
    _open(
        body["summary"]["slowMovingCostValue"] == pytest.approx(AT_COST),
        f"F47: Tied Capital {body['summary']['slowMovingCostValue']}, cost {AT_COST}",
    )
    # The selling value stays beside it, labelled apart (2 x MRP 5800).
    assert products[PID]["value"] == pytest.approx(11600.0)


@pytest.mark.parametrize("role", (*COUNTER, *MANAGERS))
def test_f47_aging_is_at_selling_value_for_everyone_else(world, role):
    """Managers and the counter read what main always showed them -- quantity
    x MRP, and Tied Capital summed from it -- and no cost figure at all."""
    resp = _aging(world, role)
    if resp.status_code == 403:
        return
    body = resp.json()
    row = {p["id"]: p for p in body["products"]}[PID]
    assert row["value"] == pytest.approx(11600.0)
    assert body["summary"]["slowMovingValue"] == pytest.approx(11600.0)
    leaked = [k for k in STOCK_COST_KEYS
              if row.get(k) is not None or body["summary"].get(k) is not None]
    _open(not leaked, f"F47: {role} reads aging stock at cost {leaked}")


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

    # S4: every piece carries the product's one average cost (3150), not
    # the price it was received at.
    units = stock_value.shelf_units(Repo(), _Products({"P": {"cost_price": 3150}}), "S1")
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
# The world (its own database, one shop). S4 (owner 2026-10-23): every piece
# carries its product's ONE average cost -- a piece's own receipt price is not
# its value, and a price typed only to finish cataloguing is no cost:
#   P-NOCOST  2 on the shelf, no cost anywhere              -> unit_cost None, 2 uncosted
#   P-MIXED   pieces received at 4000 / no price, product
#             priced only to finish cataloguing (3000)      -> unit_cost None, 2 uncosted
#   P-MASTER  1 piece, product average 2500                 -> 2500, costed
#   P-HELD    3 on the shelf + 1 reserved, average 1000     -> stock 3, reserved 1, cost 4000

_UNCOSTED_PRODUCTS = {
    "P-NOCOST": None,
    "P-MIXED": 3000.0,
    "P-MASTER": 2500.0,
    "P-HELD": 1000.0,
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
        if pid == "P-MIXED":
            doc["cost_price_is_catalogue_only"] = True
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


def _no_cost_keys(role):
    """The keys `role` never gets on a ledger row: the counter no cost at all;
    a manager no stock at cost (the product's own cost_price is cost_mask
    "product"'s, pinned in test_one_cost_answer_per_rule_on_every_stock_read)."""
    return (*COST_KEYS, "uncosted_units") if role in COUNTER else STOCK_COST_KEYS


def _rows_by_pid(world, role):
    resp = world.get("/inventory/stock", role)
    assert resp.status_code == 200, resp.text
    return {r["product_id"]: r for r in resp.json()["items"]}


def test_r1_34_a_frame_nobody_priced_has_no_unit_cost_not_rs_0(uncosted_world):
    row = _rows_by_pid(uncosted_world, "ACCOUNTANT")["P-NOCOST"]
    assert row["stock"] == 2
    assert row["unit_cost"] is None, f"unit_cost {row['unit_cost']!r} for 2 unpriced units"
    assert row["uncosted_units"] == 2
    assert row["cost_value"] == 0


def test_s4_a_pieces_own_receipt_price_is_not_its_value(uncosted_world):
    """S4: a piece received at 4000 is worth its product's average -- and a
    product with only a catalogue-finishing price has none, so both pieces are
    counted as having no cost (never valued at what one piece came in at)."""
    row = _rows_by_pid(uncosted_world, "ACCOUNTANT")["P-MIXED"]
    assert row["cost_value"] == 0
    assert row["unit_cost"] is None
    assert row["uncosted_units"] == 2


def test_r1_34_the_product_cost_still_prices_a_unit_without_its_own(uncosted_world):
    row = _rows_by_pid(uncosted_world, "ACCOUNTANT")["P-MASTER"]
    assert row["unit_cost"] == pytest.approx(2500.0)
    assert row["uncosted_units"] == 0


@pytest.mark.parametrize("role", ACCOUNTS)
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
    assert stock_value.total(units) == pytest.approx(2500.0 + 4000.0)
    assert sum(r["uncosted_units"] for r in rows) == stock_value.uncosted(units) == 4


@pytest.mark.parametrize("role", (*COUNTER, *MANAGERS))
def test_r1_34_the_counter_gets_no_cost_and_no_uncosted_count(uncosted_world, role):
    for pid, row in _rows_by_pid(uncosted_world, role).items():
        leaked = [k for k in _no_cost_keys(role) if k in row]
        assert not leaked, f"{role} row {pid} carries {leaked}"


def test_r1_39_stock_is_the_shelf_reserved_is_apart_and_cost_covers_both(uncosted_world):
    """Review r1 #39, the server half: a row's `stock` is the AVAILABLE units
    only and `reserved` is counted apart (never inside stock), while its
    cost_value covers both -- so the screen shows stock as available (no
    second subtraction) and stock + reserved is what the value tiles count."""
    row = _rows_by_pid(uncosted_world, "ACCOUNTANT")["P-HELD"]
    assert (row["stock"], row["reserved"]) == (3, 1)
    assert row["cost_value"] == pytest.approx(4000.0)  # 4 units x 1000, sold H5 not stock
    assert row["unit_cost"] == pytest.approx(1000.0)


def test_f47_stock_aging_counts_the_units_with_no_cost(uncosted_world):
    """Stock aging values at cost like the ledger: a unit with no cost adds
    nothing, and is COUNTED (per product and beside Tied capital), never a
    silent Rs 0 row."""
    body = uncosted_world.get("/inventory/aging", "ACCOUNTANT").json()
    rows = {p["id"]: p for p in body["products"]}
    assert (rows["P-NOCOST"]["costValue"], rows["P-NOCOST"]["uncostedUnits"]) == (0.0, 2)
    assert (rows["P-MIXED"]["costValue"], rows["P-MIXED"]["uncostedUnits"]) == (0.0, 2)
    assert rows["P-MASTER"]["uncostedUnits"] == 0
    slow = [p for p in body["products"] if p["classification"] == "C"]
    assert body["summary"]["slowMovingUncostedUnits"] == sum(
        p["uncostedUnits"] for p in slow
    ) == 4


@pytest.mark.parametrize("role", (*COUNTER, *MANAGERS))
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
    assert count(resp.json()) == 4


def test_r1_34_the_one_rule_counts_uncosted_pieces():
    """stock_value itself: a zero / blank / junk average is no price; such a
    product's pieces add 0, are flagged cost_known False and counted by
    uncosted(). A piece's own price never stands in (S4)."""
    mongomock = pytest.importorskip("mongomock")
    from api.services import stock_value

    coll = mongomock.MongoClient().db.stock_units
    coll.insert_many(
        [
            {"stock_id": "A", "product_id": "P", "store_id": "S1", "status": "AVAILABLE", "unit_cost": 99},
            {"stock_id": "Z", "product_id": "Q", "store_id": "S1", "status": "AVAILABLE", "unit_cost": 3100},
            {"stock_id": "J", "product_id": "R", "store_id": "S1", "status": "RESERVED", "unit_cost": 3100, "quantity": 2},
        ]
    )

    class Repo:
        def find_many(self, flt, limit=0):
            return list(coll.find(flt, {"_id": 0}))

    products = _Products({"P": {"cost_price": 3100}, "Q": {"cost_price": 0}, "R": {"cost_price": "n/a"}})
    units = stock_value.shelf_units(Repo(), products, "S1")
    assert {u["stock_id"]: u["cost_known"] for u in units} == {"A": True, "Z": False, "J": False}
    assert stock_value.total(units) == 3100.0
    assert stock_value.uncosted(units) == 3
    assert stock_value.by_product(units)["P"] == {
        "units": 1.0, "cost": 3100.0, "uncosted_units": 0.0, "unit_cost": 3100.0,
    }
    assert stock_value.by_product(units)["R"]["unit_cost"] is None


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
#           + 1 reserved at ANOTHER shop                           -> not Dhanbad's
#   P-GONE  deactivated, its only unit here SOLD                   -> no row
#   Dhanbad's stock at cost = 1600 + 1200 = 2800

_R3_HELD_AT_COST = 2800.0


def _seed_reserved_only(db) -> None:
    now = datetime.utcnow()
    for pid, active, cost in (("P-ACT", True, 800.0), ("P-OLD", False, 1200.0), ("P-GONE", False, 700.0)):
        db["products"].insert_one(
            {
                "_id": pid, "product_id": pid, "sku": f"FR-{pid}", "brand": "Test",
                "model": pid, "name": f"Frame {pid}", "category": "FRAME",
                "mrp": 3000.0, "offer_price": 2500.0, "is_active": active,
                "cost_price": cost,
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
    rows = _rows_by_pid(reserved_only_world, "ACCOUNTANT")
    assert "P-OLD" in rows, f"no ledger row for the reserved-only frame: {sorted(rows)}"
    row = rows["P-OLD"]
    assert (row["stock"], row["reserved"]) == (0, 1)
    # Only Dhanbad's reserved unit -- the one at another shop is not ours here.
    assert row["cost_value"] == pytest.approx(1200.0)
    assert row["unit_cost"] == pytest.approx(1200.0)
    # A discontinued product with nothing held here (its unit was sold) is
    # still not listed: the row is for held stock, not every unit ever.
    assert "P-GONE" not in rows


@pytest.mark.parametrize("role", ACCOUNTS)
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


@pytest.mark.parametrize("role", (*COUNTER, *MANAGERS))
def test_r3_6_the_counter_sees_the_reserved_row_without_cost(reserved_only_world, role):
    """The new row is masked exactly like every other: the counter sees the
    held frame (stock 0, reserved 1) and no cost figure on it."""
    row = _rows_by_pid(reserved_only_world, role)["P-OLD"]
    assert row["reserved"] == 1
    leaked = [k for k in _no_cost_keys(role) if k in row]
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
# (Dhanbad 3 + Pune 5 pieces x the average 1000 = 8000). The one shop rule
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
         "category": "FRAME", "mrp": 9000.0, "is_active": True, "cost_price": 1000.0}
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


# Owner ruling 2026-10-12 (R4): a non-admin login with no shop sees NO shop --
# the one rule (resolve_store_scope) scopes it to the reserved NO_SHOP_SCOPE,
# which no unit carries, so these reads answer empty (never every shop's
# Rs 13,000, never a refusal for the implicit read); naming a shop is a 403
# that says why.


@pytest.mark.parametrize("path,params", _R3_READS)
@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"])
def test_r4_a_login_with_no_shop_reads_no_shops_stock(two_shop_reports, path, params, role):
    from api.dependencies import NO_SHOP_DETAIL

    resp = two_shop_reports(path, _shopless(role), **params)
    assert resp.status_code == 200, resp.text
    assert not _stock_value(resp.json()), resp.json()
    assert "8000" not in resp.text and "3000" not in resp.text
    asked = two_shop_reports(path, _shopless(role), store_id=STORE, **params)
    assert asked.status_code == 403, asked.text
    assert asked.json()["detail"] == NO_SHOP_DETAIL


@pytest.mark.parametrize("path,params", _R3_READS[:2])
def test_r4_the_counter_with_no_shop_gets_no_counts_either(two_shop_reports, path, params):
    resp = two_shop_reports(path, _shopless("SALES_STAFF"), **params)
    assert resp.status_code == 200, resp.text
    assert '"total_units":8' not in resp.text.replace(" ", ""), resp.text
    assert "8000" not in resp.text


@pytest.mark.parametrize("path,params", _R3_READS)
def test_r3_shops_and_admins_still_read_their_stock(two_shop_reports, path, params):
    dhn = {"user_id": "u-ac", "roles": ["ACCOUNTANT"], "store_ids": [STORE], "active_store_id": STORE}
    assert _stock_value(two_shop_reports(path, dhn, **params).json()) == 3000.0
    # An admin with no shop asked reads every shop (admins see all shops).
    assert _stock_value(two_shop_reports(path, _shopless("ADMIN"), **params).json()) == 8000.0


# Stock aging on the same shop rule (panel 2026-10-08). It took its shop from
# validate_store_access -- None for a login with no shop -- so it COUNTED only
# the units with no store_id (one legacy unit) while it VALUED every shop's
# (shelf_units(None)): quantity 1 at Rs 9,993, Dhanbad's cost leaking into a
# row that should read Rs 1,111, for managers and accounts alike.


@pytest.fixture
def aging_two_shops(monkeypatch):
    mongomock = pytest.importorskip("mongomock")
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    db = mongomock.MongoClient()[f"ims_test_aging_{uuid.uuid4().hex[:8]}"]
    db["products"].insert_one(
        {"_id": "P1", "product_id": "P1", "sku": "FR-P1", "name": "Frame P1",
         "category": "FRAME", "mrp": 9000.0, "is_active": True, "cost_price": 4441.0}
    )
    old = datetime.utcnow() - timedelta(days=120)
    db["stock_units"].insert_many(
        [
            {"_id": f"D{i}", "product_id": "P1", "store_id": STORE, "quantity": 1,
             "status": "AVAILABLE", "unit_cost": 4441.0, "created_at": old}
            for i in range(2)
        ]
        + [{"_id": "L1", "product_id": "P1", "quantity": 1, "status": "AVAILABLE",
            "unit_cost": 1111.0, "created_at": old}]
    )
    monkeypatch.setattr(inv_mod, "get_stock_repository", lambda: StockRepository(db["stock_units"]))
    monkeypatch.setattr(inv_mod, "get_product_repository", lambda: ProductRepository(db["products"]))
    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")
    client = TestClient(app)

    def get(user):
        app.dependency_overrides[get_current_user] = lambda: user
        return client.get("/inventory/aging")

    return get


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"])
def test_r4_stock_aging_gives_a_login_with_no_shop_no_shops_stock(aging_two_shops, role):
    """R4: an empty report (no shop's units counted or valued), not a 403 --
    and never the legacy unit at every shop's cost."""
    resp = aging_two_shops(_shopless(role))
    assert resp.status_code == 200, resp.text
    assert resp.json()["products"] == [], resp.json()
    assert "13323" not in resp.text and "8882" not in resp.text and "4441" not in resp.text


def test_stock_aging_values_exactly_the_units_it_counts(aging_two_shops):
    dhn = {"user_id": "u-ac", "roles": ["ACCOUNTANT"], "store_ids": [STORE], "active_store_id": STORE}
    (row,) = aging_two_shops(dhn).json()["products"]
    assert (row["quantity"], row["costValue"]) == (2, 8882.0)
    # An admin with no shop asked reads every shop -- every unit counted is
    # every unit valued, never one unit at three units' cost.
    (row,) = aging_two_shops(_shopless("ADMIN")).json()["products"]
    assert (row["quantity"], row["costValue"]) == (3, 13323.0)


# ============================================================================
# One answer per role across the stock router (panel 2026-10-08, 2026-10-12)
# ============================================================================
# The ledger rows asked cost_mask "purchase" (no catalogue manager) through
# their own gate, the per-unit rows (?product_id=) and /inventory/units the
# accounts-only default, the catalogue's product reads "product": a store
# manager saw unit_cost on the row and lost cost_price one click deeper, and
# the ledger kept its own list of WHICH fields are cost. Now two rules, each
# asked in ONE place: a product's / a unit's own cost answers to cost_mask
# "product" on every read; stock AT COST (the ledger's cost_value / unit_cost
# / uncosted_units, aging's costValue) answers to stock_value.COST_CONTEXT,
# the accounts roles (owner ruling 2026-10-12).


def _cost_answers(world, role):
    from api.services.cost_mask import _ALL_MASKED

    row = _ledger_row(world, role)
    units = world.get("/inventory/stock", role, product_id=PID).json()["items"]
    labels = world.get("/inventory/units", role, product_id=PID).json()["units"]
    aging = {p["id"]: p for p in _aging(world, role).json()["products"]}
    return {
        "product": {
            "ledger row": row.get("cost_price") is not None,
            "per-unit rows": any(_ALL_MASKED & set(u) for u in units),
            "units": any("cost_price" in u for u in labels),
        },
        "stock at cost": {
            "ledger row": any(k in row for k in STOCK_COST_KEYS),
            "aging": aging[PID]["costValue"] is not None,
        },
    }


@pytest.mark.parametrize("role", [*MANAGERS, *ACCOUNTS, *COUNTER])
def test_one_cost_answer_per_rule_on_every_stock_read(world, role):
    from api.services import stock_value
    from api.services.cost_mask import can_see_cost

    answers = _cost_answers(world, role)
    for rule, context in (("product", "product"), ("stock at cost", stock_value.COST_CONTEXT)):
        want = can_see_cost(_user(role), context)
        assert answers[rule] == dict.fromkeys(answers[rule], want), (role, rule, want, answers)


def test_which_ledger_fields_are_cost_is_cost_mask_s(world, monkeypatch):
    """#1161's unhiding check, on the ledger: take a field off cost_mask's
    list and the counter's row carries exactly it -- the ledger keeps no list
    of its own."""
    from api.services import cost_mask

    full = cost_mask._ALL_MASKED
    # The product's own cost ("product") and the row's stock at cost
    # (stock_value.COST_CONTEXT) alike: WHICH keys are cost is cost_mask's.
    for field, want in (("cost_price", 3150.0), ("cost_value", AT_COST)):
        monkeypatch.setattr(cost_mask, "_ALL_MASKED", full - {field})
        row = _ledger_row(world, "SALES_STAFF")
        assert row.get(field) == pytest.approx(want), field
        assert "unit_cost" not in row, field


# ============================================================================
# Managers: what we pay, stock value at cost included, is the accounts roles'
# ============================================================================
# Owner ruling 2026-10-12 (recorded on main: what we pay is ADMIN / SUPERADMIN /
# ACCOUNTANT only). Panel 2026-10-12 #8: this branch had given STORE_MANAGER,
# AREA_MANAGER and CATALOG_MANAGER three new at-cost figures through cost_mask
# "product" -- the Inventory 'Stock value' headline (ledger cost_value), Stock
# aging 'Tied capital (at cost)' and Reports > Inventory's Stock Summary. As a
# STORE_MANAGER (and the other managers), no read carries a stock-at-cost key
# or the shelf's cost (Rs 6,300; units at 3,100 and 3,200).

_MANAGER_READS = (
    ("/inventory/stock", {}),
    ("/inventory/aging", {}),
    *[(path, params) for path, (params, _values) in _REPORTS.items()],
)


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k, x
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


@pytest.mark.parametrize("path,params", _MANAGER_READS, ids=[p for p, _ in _MANAGER_READS])
@pytest.mark.parametrize("role", MANAGERS)
def test_a_manager_reads_no_stock_at_cost_anywhere(world, path, params, role):
    resp = world.get(path, role, **params)
    if resp.status_code == 403:  # a report outside the role's reach
        return
    assert resp.status_code == 200, resp.text
    body = resp.json()
    leaked = sorted({k for k, v in _keys(body) if k in STOCK_COST_KEYS and v is not None})
    _open(not leaked, f"{role} reads stock at cost on {path}: {leaked}")
    if path in _REPORTS:
        values = [v for v in _REPORTS[path][1](body) if v is not None]
        _open(not values, f"{role} reads stock value at cost {values} on {path}")
    # As a JSON number, never inside a timestamp's digits.
    figures = re.findall(r"(?<![\d.])(6300|3100|3200)(?:\.0+)?(?![\d])", resp.text)
    assert not figures, (role, path, figures)


def test_the_store_manager_probe_reads_selling_value_only(world):
    """The panel's probe, as a STORE_MANAGER at Dhanbad: the shop is reached
    (the reads are not simply refused) and every figure is selling value."""
    row = _ledger_row(world, "STORE_MANAGER")
    assert (row["stock"], row["offerPrice"]) == (2, pytest.approx(5000.0))
    aging = _aging(world, "STORE_MANAGER").json()
    assert aging["summary"]["slowMovingValue"] == pytest.approx(11600.0)
    count = world.get("/reports/stock/count", "STORE_MANAGER",
                      from_date="2026-09-01", to_date="2026-09-29").json()
    assert count["summary"]["total_quantity"] == 2
    assert "total_value" not in count["summary"]
