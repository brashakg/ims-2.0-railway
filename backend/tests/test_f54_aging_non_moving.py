"""Audit F54 (2026-09-29): stock received today was 'Slow Mover', aged -1 days,
and listed as non-moving.

* /inventory/aging: a unit stamped a few hours "ahead" of the reader's clock
  (created_at is naive datetime.now(); the reader used utcnow) aged -1 days,
  and a product with no sales yet was classed C "Slow Mover - consider
  discount/return" on the morning it arrived.
* /inventory/non-moving: listed every catalogue product with no sale in the
  window, including one with 0 units and ones whose units arrived today.

Contract: age is never negative; stock younger than the grace window with no
sales carries no mover verdict (NEW); non-moving counts only units that have
sat on the shelf for the whole window and skips products with none.
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
from api.routers.inventory import get_non_moving_stock, get_stock_aging_report  # noqa: E402

_MGR = {"user_id": "m1", "roles": ["STORE_MANAGER"], "active_store_id": "S1", "store_ids": ["S1"]}
_NOW = datetime.utcnow()
# datetime.now() on a box east of UTC: "today" but hours ahead of utcnow.
_TODAY_AHEAD = _NOW + timedelta(hours=5, minutes=30)

_PRODUCTS = {
    "P-NEW": {"product_id": "P-NEW", "name": "Aviator Gold", "sku": "AV", "brand": "RB", "category": "SUNGLASS", "mrp": 9000},
    "P-OLD": {"product_id": "P-OLD", "name": "Old Clubmaster", "sku": "CM", "brand": "RB", "category": "SUNGLASS", "mrp": 8000},
    "P-ZERO": {"product_id": "P-ZERO", "name": "Havana", "sku": "HV", "brand": "RB", "category": "SUNGLASS", "mrp": 7000},
}

_UNITS = (
    [{"product_id": "P-NEW", "store_id": "S1", "status": "AVAILABLE", "created_at": _TODAY_AHEAD} for _ in range(4)]
    + [{"product_id": "P-OLD", "store_id": "S1", "status": "AVAILABLE", "created_at": _NOW - timedelta(days=120)} for _ in range(2)]
)


class _StockRepo:
    def aggregate(self, pipeline):
        match = pipeline[0]["$match"]
        if match.get("status") == "SOLD":
            return []  # nothing sold yet
        out = {}
        for u in _UNITS:
            r = out.setdefault(u["product_id"], {"_id": u["product_id"], "quantity": 0, "oldest": None, "total_value": 0})
            r["quantity"] += 1
            if r["oldest"] is None or u["created_at"] < r["oldest"]:
                r["oldest"] = u["created_at"]
        return list(out.values())


class _ProductRepo:
    def find_by_id(self, pid):
        return _PRODUCTS.get(pid)


def _mongo(monkeypatch, units=_UNITS):
    """The catalogue + ``units`` on mongomock behind the REAL repositories, so
    every screen runs the repository's own arrival rule, not a copy of it."""
    import mongomock

    from database.repositories.product_repository import ProductRepository, StockRepository

    db = mongomock.MongoClient().db
    db.products.insert_many(
        [{"_id": p["product_id"], **p, "barcode": p["sku"], "cost_price": 3000} for p in _PRODUCTS.values()]
    )
    if units:
        db.stock_units.insert_many([dict(u) for u in units])
    monkeypatch.setattr(inv, "get_stock_repository", lambda: StockRepository(db.stock_units))
    monkeypatch.setattr(inv, "get_product_repository", lambda: ProductRepository(db.products))
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    return db


def _non_moving():
    return asyncio.run(get_non_moving_stock(days=90, category=None, store_id=None, current_user=_MGR))


def _aging(monkeypatch):
    monkeypatch.setattr(inv, "get_stock_repository", lambda: _StockRepo())
    monkeypatch.setattr(inv, "get_product_repository", lambda: _ProductRepo())
    res = asyncio.run(
        get_stock_aging_report(
            store_id=None, category=None, classification=None, min_days=None, current_user=_MGR
        )
    )
    return {p["id"]: p for p in res["products"]}, res["summary"]


def test_stock_received_today_is_zero_days_old(monkeypatch):
    rows, summary = _aging(monkeypatch)
    assert rows["P-NEW"]["daysInStock"] == 0
    assert summary["averageAge"] >= 0


def test_unsold_new_stock_gets_no_slow_mover_verdict(monkeypatch):
    rows, summary = _aging(monkeypatch)
    assert rows["P-NEW"]["classification"] == "NEW"
    # Stock that has had its chance and not sold is still called slow.
    assert rows["P-OLD"]["classification"] == "C"
    assert summary["classC"] == 1


def test_non_moving_counts_only_shelf_stock_older_than_the_window(monkeypatch):
    _mongo(monkeypatch)
    res = _non_moving()
    ids = [p["product_id"] for p in res["products"]]
    assert ids == ["P-OLD"]  # not today's Aviators, not the 0-stock Havana
    assert res["products"][0]["current_stock"] == 2


def test_non_moving_stock_column_is_what_is_on_the_shelf(monkeypatch):
    """Verifier round 2: the young units drop out of the VERDICT, not out of
    the displayed on-hand figure. 2 units received 120 days ago + 3 today, no
    sale in 90 days: listed (the old two have sat out the window) with
    Stock 5 -- what is on the shelf -- not 2."""
    units = _UNITS + [
        {"product_id": "P-OLD", "store_id": "S1", "status": "AVAILABLE", "created_at": _NOW}
        for _ in range(3)
    ]
    _mongo(monkeypatch, units)
    (row,) = _non_moving()["products"]
    assert row["product_id"] == "P-OLD"
    assert row["current_stock"] == 5


# ---------------------------------------------------------------------------
# Verifier round 3: one rule for an unknown stock age
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.parametrize("stamp", [None, "30/09/2026"], ids=["missing", "unreadable"])
def test_unknown_stock_age_is_old_on_every_screen(monkeypatch, stamp):
    """A never-sold unit whose created_at is missing or unreadable. Aging
    read it as 0 days old and said NEW while Non-moving (and Alerts) read it
    as legacy stock and listed it. One rule now (helpers._had_the_window):
    unknown age = legacy = old, so Aging gives it its mover verdict too."""
    unit = {"product_id": "P-OLD", "store_id": "S1", "status": "AVAILABLE"}
    if stamp:
        unit["created_at"] = stamp

    class _LegacyStockRepo:
        def aggregate(self, pipeline):
            if pipeline[0]["$match"].get("status") == "SOLD":
                return []
            # group_with_oldest_arrival: None when undated, else the lone string.
            return [{"_id": "P-OLD", "quantity": 1, "oldest": stamp, "total_value": 0}]

    _mongo(monkeypatch, [unit])
    monkeypatch.setattr(inv, "get_stock_repository", lambda: _LegacyStockRepo())
    monkeypatch.setattr(inv, "get_product_repository", lambda: _ProductRepo())
    aging = asyncio.run(
        get_stock_aging_report(
            store_id=None, category=None, classification=None, min_days=None, current_user=_MGR
        )
    )
    (row,) = aging["products"]
    non_moving = _non_moving()
    assert [p["product_id"] for p in non_moving["products"]] == ["P-OLD"]
    assert row["classification"] == "C"  # not NEW
    # ...and the age in the same row says old too: no made-up 0 days, the
    # oldest bucket, counted as old stock (verifier round 4).
    assert row["daysInStock"] is None
    assert row["ageCategory"] == "180+"
    assert aging["summary"]["oldStockCount"] == 1
    assert aging["summary"]["averageAge"] == 0  # no known age to average


# ---------------------------------------------------------------------------
# Verifier round 4: legacy units beside a unit received today
# ---------------------------------------------------------------------------


def test_legacy_units_beside_todays_receipt_are_old_on_every_screen(monkeypatch):
    """5 never-sold legacy units with no created_at and 1 unit received today,
    run through the REAL repositories on mongomock. Mongo's $min skips the
    missing dates, so Aging said NEW / 0 days and Alerts saw stock 'since
    today' (no DEAD_STOCK) while Non-moving, judging unit by unit, listed the
    same 5 as old. One rule: an undated unit makes the product's age unknown,
    and unknown is old -- on all three screens, and min_days keeps it."""
    _mongo(
        monkeypatch,
        [{"product_id": "P-OLD", "store_id": "S1", "status": "AVAILABLE"} for _ in range(5)]
        + [{"product_id": "P-OLD", "store_id": "S1", "status": "AVAILABLE", "created_at": _NOW}],
    )

    aging = asyncio.run(
        get_stock_aging_report(
            store_id=None, category=None, classification=None, min_days=90, current_user=_MGR
        )
    )
    (row,) = aging["products"]
    assert row["classification"] == "C" and row["daysInStock"] is None

    alerts = asyncio.run(
        inv.get_stock_alerts(
            store_id=None, dead_days=90, lead_time_days=14, limit=200, current_user=_MGR
        )
    )
    assert [a["alertType"] for a in alerts["alerts"]] == ["DEAD_STOCK"]

    non_moving = _non_moving()
    assert [(p["product_id"], p["current_stock"]) for p in non_moving["products"]] == [("P-OLD", 6)]


# ---------------------------------------------------------------------------
# Verifier round 5: opening stock is undated, not "arrived the day typed in"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("today_too", [False, True], ids=["opening-only", "beside-a-receipt"])
def test_opening_stock_is_old_on_every_screen(monkeypatch, today_too):
    """8 never-sold frames entered as opening stock 10 days ago (pre-IMS stock:
    nobody knows when they reached the shelf). Read as an arrival, the entry
    day made them NEW / 0-30 on Aging, never DEAD_STOCK and never non-moving
    for 90 days -- every shop's whole go-live stock. They are undated, so old,
    exactly like the same 8 with no date; a unit received today beside them
    does not make them young."""
    units = [
        {
            "product_id": "P-OLD",
            "store_id": "S1",
            "status": "AVAILABLE",
            "source": "OPENING_STOCK",
            "created_at": _NOW - timedelta(days=10),
        }
        for _ in range(8)
    ]
    if today_too:
        units.append({"product_id": "P-OLD", "store_id": "S1", "status": "AVAILABLE", "created_at": _NOW})
    _mongo(monkeypatch, units)

    aging = asyncio.run(
        get_stock_aging_report(
            store_id=None, category=None, classification=None, min_days=None, current_user=_MGR
        )
    )
    (row,) = aging["products"]
    assert (row["classification"], row["daysInStock"], row["ageCategory"]) == ("C", None, "180+")

    alerts = asyncio.run(
        inv.get_stock_alerts(
            store_id=None, dead_days=90, lead_time_days=14, limit=200, current_user=_MGR
        )
    )
    assert [a["alertType"] for a in alerts["alerts"]] == ["DEAD_STOCK"]

    assert [(p["product_id"], p["current_stock"]) for p in _non_moving()["products"]] == [
        ("P-OLD", 9 if today_too else 8)
    ]
