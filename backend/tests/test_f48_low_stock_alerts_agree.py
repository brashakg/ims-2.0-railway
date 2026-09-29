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


def _units(pid, n, days_old=0, store="S1"):
    return [
        {
            "product_id": pid,
            "store_id": store,
            "status": "AVAILABLE",
            "created_at": _NOW - timedelta(days=days_old),
        }
        for _ in range(n)
    ]


# Received today: 4 Aviators (low), 17 Wayfarers (healthy).
_UNITS = _units("P-AV", 4) + _units("P-WAY", 17)


class _StockRepo:
    """find_low_stock exactly as StockRepository computes it (AVAILABLE units
    per product at the store, qty <= 5)."""

    def find_low_stock(self, store_id, threshold=5):
        counts = {}
        for u in _UNITS:
            if u["store_id"] == store_id and u["status"] == "AVAILABLE":
                counts[u["product_id"]] = counts.get(u["product_id"], 0) + 1
        return [{"_id": p, "quantity": q} for p, q in counts.items() if q <= threshold]


class _ProductRepo:
    def find_many(self, flt, limit=None, **_):
        ids = set(flt["product_id"]["$in"])
        return [dict(p) for p in _PRODUCTS if p["product_id"] in ids]


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs]

    def aggregate(self, pipeline):
        match = pipeline[0].get("$match", {})
        group = next((s["$group"] for s in pipeline if "$group" in s), None)
        rows = [d for d in self.docs if all(d.get(k) == v for k, v in match.items() if not isinstance(v, dict) and not k.startswith("$"))]
        if group is None or group.get("_id") != "$product_id":
            return []  # orders: no sales in this scenario
        out = {}
        for d in rows:
            r = out.setdefault(d["product_id"], {"_id": d["product_id"], "n": 0, "oldest": None})
            r["n"] += 1
            if r["oldest"] is None or d["created_at"] < r["oldest"]:
                r["oldest"] = d["created_at"]
        return list(out.values())


class _Db:
    def get_collection(self, name):
        return {
            "products": _Coll(_PRODUCTS),
            "stock_units": _Coll(_UNITS),
            "orders": _Coll([]),
        }[name]


def _wire(mp):
    mp.setattr(inv, "get_stock_repository", lambda: _StockRepo())
    mp.setattr(inv, "get_product_repository", lambda: _ProductRepo())
    mp.setattr(inv, "_get_db", lambda: _Db())


def test_low_stock_row_names_the_product(monkeypatch):
    _wire(monkeypatch)
    res = asyncio.run(get_low_stock_alerts(store_id=None, current_user=_MGR))
    (row,) = res["items"]
    assert row["name"] == "Ray-Ban RB3025 Aviator - Gold"
    assert row["sku"] == "RB3025-GLD"
    assert row["brand"] == "Ray-Ban"
    assert row["id"] == row["product_id"] == "P-AV"
    assert row["quantity"] == 4


def test_alerts_agree_with_low_stock(monkeypatch):
    _wire(monkeypatch)
    low = asyncio.run(get_low_stock_alerts(store_id=None, current_user=_MGR))
    res = asyncio.run(
        get_stock_alerts(
            store_id=None, dead_days=90, lead_time_days=14, limit=200, current_user=_MGR
        )
    )
    low_names = {r["name"] for r in low["items"]}
    alert_low = {a["productName"] for a in res["alerts"] if a["alertType"] == "LOW_STOCK"}
    assert alert_low == low_names == {"Ray-Ban RB3025 Aviator - Gold"}
    av = next(a for a in res["alerts"] if a["productName"].startswith("Ray-Ban RB3025"))
    assert av["currentStock"] == 4


def test_stock_received_today_is_never_dead(monkeypatch):
    """17 Wayfarers that arrived this morning have not had 90 days to sell."""
    _wire(monkeypatch)
    res = asyncio.run(
        get_stock_alerts(
            store_id=None, dead_days=90, lead_time_days=14, limit=200, current_user=_MGR
        )
    )
    assert not [a for a in res["alerts"] if a["alertType"] == "DEAD_STOCK"]
