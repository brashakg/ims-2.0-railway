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
            r = out.setdefault(u["product_id"], {"_id": u["product_id"], "quantity": 0, "oldest_date": None, "total_value": 0})
            r["quantity"] += 1
            if r["oldest_date"] is None or u["created_at"] < r["oldest_date"]:
                r["oldest_date"] = u["created_at"]
        return list(out.values())


class _ProductRepo:
    def find_by_id(self, pid):
        return _PRODUCTS.get(pid)


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find(self, flt=None, projection=None):
        flt = flt or {}
        return [
            dict(d)
            for d in self.docs
            if all(d.get(k) == v for k, v in flt.items() if not k.startswith("$") and not isinstance(v, dict))
        ]

    def find_one(self, *a, **k):
        return None


class _Db:
    def get_collection(self, name):
        return {
            "products": _Coll([{"_id": p["product_id"], **p} for p in _PRODUCTS.values()]),
            "orders": _Coll([]),
            "stock_units": _Coll(_UNITS),
        }[name]


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
    monkeypatch.setattr(inv, "_get_db", lambda: _Db())
    res = asyncio.run(get_non_moving_stock(days=90, category=None, store_id=None, current_user=_MGR))
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

    class _MixedDb(_Db):
        def get_collection(self, name):
            if name == "stock_units":
                return _Coll(units)
            return super().get_collection(name)

    monkeypatch.setattr(inv, "_get_db", lambda: _MixedDb())
    res = asyncio.run(get_non_moving_stock(days=90, category=None, store_id=None, current_user=_MGR))
    (row,) = res["products"]
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
            # $min ignores a missing field and returns the lone string.
            return [{"_id": "P-OLD", "quantity": 1, "oldest_date": stamp, "total_value": 0}]

    class _LegacyDb(_Db):
        def get_collection(self, name):
            if name == "stock_units":
                return _Coll([unit])
            return super().get_collection(name)

    monkeypatch.setattr(inv, "get_stock_repository", lambda: _LegacyStockRepo())
    monkeypatch.setattr(inv, "get_product_repository", lambda: _ProductRepo())
    monkeypatch.setattr(inv, "_get_db", lambda: _LegacyDb())
    aging = asyncio.run(
        get_stock_aging_report(
            store_id=None, category=None, classification=None, min_days=None, current_user=_MGR
        )
    )
    (row,) = aging["products"]
    non_moving = asyncio.run(get_non_moving_stock(days=90, category=None, store_id=None, current_user=_MGR))
    assert [p["product_id"] for p in non_moving["products"]] == ["P-OLD"]
    assert row["classification"] == "C"  # not NEW
