"""Audit F48 (2026-09-29): Low stock said 'Unknown Product', Alerts said nothing.

GET /inventory/low-stock returned only {_id, quantity}, so the screen printed
"Unknown Product - 4 left". GET /inventory/alerts counted stock from the legacy
products.stock_quantity field and filtered the catalogue by products.store_id,
so with real stock living in stock_units it answered "No Alerts" while the strip
said LOW STOCK 1.

Contract: the low-stock row names the product; Alerts reads the same ledger,
flags exactly the low-stock products as LOW_STOCK (one rule: find_low_stock),
and never calls stock that arrived today dead.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv  # noqa: E402
from api.routers.inventory import get_low_stock_alerts, get_stock_alerts  # noqa: E402

_NOW = datetime.utcnow()
_MGR = {"user_id": "m1", "roles": ["STORE_MANAGER"], "active_store_id": "S1", "store_ids": ["S1"]}

_PRODUCTS = [
    {
        "product_id": "P-AV",
        "name": "Ray-Ban RB3025 Aviator - Gold",
        "brand": "Ray-Ban",
        "sku": "RB3025-GLD",
        "category": "SUNGLASS",
        "cost_price": 5000,
    },
    {
        "product_id": "P-WAY",
        "name": "Ray-Ban Wayfarer",
        "brand": "Ray-Ban",
        "sku": "RB2140",
        "category": "SUNGLASS",
        "cost_price": 4000,
    },
]


def _units(pid, n, days_old=0, store="S1", **over):
    unit = {
        "product_id": pid,
        "store_id": store,
        "status": "AVAILABLE",
        "created_at": _NOW - timedelta(days=days_old),
    }
    unit.update(over)
    return [dict(unit) for _ in range(n)]


# Received today: 4 Aviators (low), 17 Wayfarers (healthy).
_UNITS = _units("P-AV", 4) + _units("P-WAY", 17)


def _wire(mp, units=_UNITS, products=_PRODUCTS):
    """A mongomock database behind the REAL StockRepository / ProductRepository,
    so both screens run the repository's own count, not a copy of it."""
    import mongomock

    from database.repositories.product_repository import ProductRepository, StockRepository

    db = mongomock.MongoClient().db
    db.products.insert_many([dict(p) for p in products])
    if units:
        db.stock_units.insert_many([dict(u) for u in units])
    mp.setattr(inv, "get_stock_repository", lambda: StockRepository(db.stock_units))
    mp.setattr(inv, "get_product_repository", lambda: ProductRepository(db.products))
    mp.setattr(inv, "_get_db", lambda: db)
    return db


def _alerts():
    return asyncio.run(
        get_stock_alerts(
            store_id=None, dead_days=90, lead_time_days=14, limit=200, current_user=_MGR
        )
    )


def _low():
    return asyncio.run(get_low_stock_alerts(store_id=None, current_user=_MGR))


def test_low_stock_row_names_the_product(monkeypatch):
    _wire(monkeypatch)
    res = _low()
    (row,) = res["items"]
    assert row["name"] == "Ray-Ban RB3025 Aviator - Gold"
    assert row["sku"] == "RB3025-GLD"
    assert row["brand"] == "Ray-Ban"
    assert row["id"] == row["product_id"] == "P-AV"
    assert row["quantity"] == 4


def test_alerts_agree_with_low_stock(monkeypatch):
    _wire(monkeypatch)
    low = _low()
    res = _alerts()
    low_names = {r["name"] for r in low["items"]}
    alert_low = {a["productName"] for a in res["alerts"] if a["alertType"] == "LOW_STOCK"}
    assert alert_low == low_names == {"Ray-Ban RB3025 Aviator - Gold"}
    av = next(a for a in res["alerts"] if a["productName"].startswith("Ray-Ban RB3025"))
    assert av["currentStock"] == 4


def test_stock_received_today_is_never_dead(monkeypatch):
    """17 Wayfarers that arrived this morning have not had 90 days to sell."""
    _wire(monkeypatch)
    res = _alerts()
    assert not [a for a in res["alerts"] if a["alertType"] == "DEAD_STOCK"]


# ---------------------------------------------------------------------------
# Verifier round 2
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize(
    "legacy", [{"status": "available"}, {"status": None}], ids=["lowercase", "no-status"]
)
def test_a_legacy_unit_does_not_split_the_count(monkeypatch, legacy):
    """5 units marked AVAILABLE plus 1 legacy unit: both screens count the
    same units, so Alerts can never say 'Only 6 left - at or below' beside
    Low stock's '5 left, Min 5'."""
    units = _units("P-AV", 5) + _units("P-AV", 1, **legacy)
    if legacy["status"] is None:
        units[-1].pop("status")
    _wire(monkeypatch, units=units)
    (row,) = _low()["items"]
    (alert,) = [a for a in _alerts()["alerts"] if a["productName"].startswith("Ray-Ban RB3025")]
    assert alert["currentStock"] == row["quantity"] == 5
    assert alert["alertType"] == "LOW_STOCK"
    assert alert["actionRequired"] == "Only 5 left - at or below the low-stock level"


def test_dead_stock_outranks_the_low_list(monkeypatch):
    """2 frames on the shelf 200 days, never sold: that is dead stock, not
    'running low'. Being on the low list (5 or fewer units) must not hide it."""
    products = [{**_PRODUCTS[0], "cost_price": 3000}]
    _wire(monkeypatch, units=_units("P-AV", 2, days_old=200), products=products)
    assert [r["_id"] for r in _low()["items"]] == ["P-AV"]  # it IS on the low list
    res = _alerts()
    (alert,) = res["alerts"]
    assert alert["alertType"] == "DEAD_STOCK"
    assert alert["costImpact"] == 6000
    assert res["stats"]["deadStockValue"] == 6000
