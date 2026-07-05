"""
IMS 2.0 - Batched product joins (N+1 kill) equivalence suite
============================================================
Locks the pure-refactor guarantee for the three report-side N+1 fixes:

  1. GET /inventory/sell-through-analysis -- used to run products.find_one
     PER ORDER ITEM and PER PHYSICAL STOCK UNIT; now ONE batched products
     query. Output must be byte-identical to the legacy per-item algorithm.
  2. reports._stock_category_map -- per-distinct-pid find_by_id + per-miss
     3-way $or catalog find_one; now ONE products $in + ONE catalog query.
     Batch path and the per-id fallback path (repos without a batchable
     .collection, e.g. the FakeProductRepo pattern) must agree exactly.
  3. analytics._build_product_master_map -- identical clone, same guarantee,
     plus the catalog field flattening (title -> name, pricing.* -> flat).

All tests are pure in-memory (no Mongo): a seeded fake collection mirrors the
filter semantics the code uses ($in / $or / $gte / equality), and counts
queries so the N+1 death is asserted, not assumed.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_report_perf_batch_joins.py -q
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import analytics as an  # noqa: E402
from api.routers import inventory as inv_mod  # noqa: E402
from api.routers import orders as orders_mod  # noqa: E402
from api.routers import reports as rep  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# Seeded fake collection (query-counting; mirrors MockCollection filter
# semantics for the operators these code paths use)
# ============================================================================


class _FakeColl:
    """In-memory collection: find/find_one with $in/$or/$gte/equality filters,
    projection accepted (and ignored, like connection.MockCollection). Counts
    calls so tests can assert the query volume, not just the output."""

    def __init__(self, docs: Optional[List[Dict]] = None):
        self.docs: List[Dict] = [dict(d) for d in (docs or [])]
        self.find_calls = 0
        self.find_one_calls = 0

    @staticmethod
    def _matches(doc: Dict, flt: Dict) -> bool:
        if not flt:
            return True
        for key, value in flt.items():
            if key == "$or":
                if not any(_FakeColl._matches(doc, cond) for cond in value):
                    return False
            elif isinstance(value, dict):
                doc_value = doc.get(key)
                for op, op_value in value.items():
                    if op == "$in":
                        if doc_value not in op_value:
                            return False
                    elif op == "$gte":
                        if doc_value is None or not (doc_value >= op_value):
                            return False
                    elif op == "$lte":
                        if doc_value is None or not (doc_value <= op_value):
                            return False
                    else:  # unsupported operator -> no match (fail loud-ish)
                        return False
            else:
                if doc.get(key) != value:
                    return False
        return True

    def find(self, flt: Optional[Dict] = None, projection: Optional[Dict] = None):
        self.find_calls += 1
        return [dict(d) for d in self.docs if self._matches(d, flt or {})]

    def find_one(self, flt: Optional[Dict] = None, projection: Optional[Dict] = None):
        self.find_one_calls += 1
        for d in self.docs:
            if self._matches(d, flt or {}):
                return dict(d)
        return None


class _FakeDB:
    def __init__(self, collections: Dict[str, _FakeColl]):
        self._collections = collections
        self.is_connected = True

    def get_collection(self, name: str) -> _FakeColl:
        return self._collections.setdefault(name, _FakeColl())


# ============================================================================
# 1. /inventory/sell-through-analysis
# ============================================================================

_STORE = "S-PERF"


def _sell_through_fixture() -> _FakeDB:
    now = datetime.utcnow()
    products = [
        {"_id": "P1", "product_id": "P1", "brand": "Ray-Ban"},
        {"_id": "P2", "product_id": "P2", "brand": "Ray-Ban"},
        {"_id": "P3", "product_id": "P3"},  # no brand field -> "Unknown"
        {"_id": "P4", "product_id": "P4", "brand": "Oakley"},
        {"_id": "P5", "product_id": "P5", "brand": "Vogue"},  # stocked only
    ]
    orders = [
        {  # counted: sold status, in window
            "store_id": _STORE,
            "status": "COMPLETED",
            "created_at": now - timedelta(days=2),
            "items": [
                {"product_id": "P1", "quantity": 2},
                {"product_id": "P2", "quantity": 1},
                {"product_id": "GHOST", "quantity": 9},  # no product doc -> skipped
                {"product_id": None, "quantity": 3},  # null pid -> skipped
            ],
        },
        {  # counted: another sold status + item with no quantity field (0)
            "store_id": _STORE,
            "status": "DELIVERED",
            "created_at": now - timedelta(days=5),
            "items": [{"product_id": "P3", "quantity": 4}, {"product_id": "P1"}],
        },
        {  # excluded: non-sold status
            "store_id": _STORE,
            "status": "CANCELLED",
            "created_at": now - timedelta(days=1),
            "items": [{"product_id": "P1", "quantity": 50}],
        },
        {  # excluded: outside the window
            "store_id": _STORE,
            "status": "COMPLETED",
            "created_at": now - timedelta(days=90),
            "items": [{"product_id": "P4", "quantity": 50}],
        },
        {  # other store: counted only when scope is all-stores
            "store_id": "S-OTHER",
            "status": "COMPLETED",
            "created_at": now - timedelta(days=3),
            "items": [{"product_id": "P4", "quantity": 6}],
        },
    ]
    stock = [
        {"product_id": "P1", "store_id": _STORE, "quantity": 3},
        {"product_id": "P1", "store_id": _STORE},  # no quantity -> 1 unit
        {"product_id": "P2", "store_id": _STORE, "quantity": 2},
        {"product_id": "P3", "store_id": _STORE, "quantity": 5},
        {"product_id": "P5", "store_id": _STORE, "quantity": 1},
        {"product_id": "GHOST-2", "store_id": _STORE, "quantity": 4},  # skipped
        {"product_id": None, "store_id": _STORE, "quantity": 4},  # skipped
        {"product_id": "P4", "store_id": "S-OTHER", "quantity": 8},
    ]
    return _FakeDB(
        {
            "products": _FakeColl(products),
            "orders": _FakeColl(orders),
            "stock_units": _FakeColl(stock),
        }
    )


def _legacy_sell_through(db: _FakeDB, days: int, active_store: Optional[str]) -> Dict:
    """The pre-refactor algorithm, verbatim (per-item / per-unit find_one),
    used as the equivalence oracle."""
    orders_coll = db.get_collection("orders")
    stock_coll = db.get_collection("stock_units")
    products_coll = db.get_collection("products")

    cutoff_date = datetime.utcnow() - timedelta(days=days)
    _order_q: Dict[str, Any] = {
        "created_at": {"$gte": cutoff_date},
        "status": {"$in": inv_mod._SOLD_STATUSES},
    }
    if active_store:
        _order_q["store_id"] = active_store

    sales_by_brand: Dict = {}
    for order in orders_coll.find(_order_q):
        for item in order.get("items", []):
            product = products_coll.find_one({"_id": item.get("product_id")})
            if product:
                brand = product.get("brand", "Unknown")
                qty = item.get("quantity", 0)
                sales_by_brand[brand] = sales_by_brand.get(brand, 0) + qty

    stock_by_brand: Dict = {}
    for stock in stock_coll.find({"store_id": active_store} if active_store else {}):
        product = products_coll.find_one({"_id": stock.get("product_id")})
        if product:
            brand = product.get("brand", "Unknown")
            qty = stock.get("quantity", 1)
            stock_by_brand[brand] = stock_by_brand.get(brand, 0) + qty

    brands = set(list(sales_by_brand.keys()) + list(stock_by_brand.keys()))
    results = []
    for brand in brands:
        units_sold = sales_by_brand.get(brand, 0)
        units_stocked = stock_by_brand.get(brand, 0)
        sell_through = (
            (units_sold / max(units_stocked, 1)) * 100 if units_stocked > 0 else 0
        )
        results.append(
            {
                "brand": brand,
                "units_sold": units_sold,
                "units_stocked": units_stocked,
                "sell_through_percent": round(sell_through, 2),
            }
        )
    return {
        "period_days": days,
        "brands": sorted(results, key=lambda x: x["sell_through_percent"], reverse=True),
    }


def _sell_through_client(db: _FakeDB, monkeypatch) -> TestClient:
    monkeypatch.setattr(inv_mod, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")

    async def _user():
        return {
            "user_id": "u-perf",
            "username": "perfadmin",
            "roles": ["SUPERADMIN"],
            "store_ids": [_STORE],
            "active_store_id": _STORE,
        }

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


class TestSellThroughBatched:
    def test_matches_legacy_algorithm_all_stores(self, monkeypatch):
        db = _sell_through_fixture()
        expected = _legacy_sell_through(db, days=30, active_store=None)

        client = _sell_through_client(db, monkeypatch)
        products = db.get_collection("products")
        products.find_calls = products.find_one_calls = 0

        resp = client.get("/inventory/sell-through-analysis?days=30")
        assert resp.status_code == 200, resp.text
        assert resp.json() == expected

        # Sanity on the oracle itself: known hand-computed rows.
        by_brand = {r["brand"]: r for r in resp.json()["brands"]}
        # Ray-Ban: sold 2+1+0 = 3; stocked 3+1+2 = 6 -> 50%
        assert by_brand["Ray-Ban"] == {
            "brand": "Ray-Ban",
            "units_sold": 3,
            "units_stocked": 6,
            "sell_through_percent": 50.0,
        }
        # Unknown (P3, no brand field): sold 4, stocked 5
        assert by_brand["Unknown"]["units_sold"] == 4
        assert by_brand["Unknown"]["units_stocked"] == 5
        # Oakley: other-store sale (6) + other-store stock (8) count all-stores
        assert by_brand["Oakley"] == {
            "brand": "Oakley",
            "units_sold": 6,
            "units_stocked": 8,
            "sell_through_percent": 75.0,
        }
        # Vogue: stocked only, zero sales
        assert by_brand["Vogue"]["units_sold"] == 0
        assert by_brand["Vogue"]["sell_through_percent"] == 0

    def test_matches_legacy_algorithm_store_scoped(self, monkeypatch):
        db = _sell_through_fixture()
        expected = _legacy_sell_through(db, days=30, active_store=_STORE)

        client = _sell_through_client(db, monkeypatch)
        resp = client.get(f"/inventory/sell-through-analysis?days=30&store_id={_STORE}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == expected

        by_brand = {r["brand"]: r for r in resp.json()["brands"]}
        # Oakley's sale + stock live in S-OTHER: fully excluded under scope.
        assert "Oakley" not in by_brand
        assert by_brand["Ray-Ban"]["units_sold"] == 3

    def test_products_queried_exactly_once(self, monkeypatch):
        """The N+1 is dead: 7 matched order items + 8 stock rows used to cost
        15 products.find_one calls; now the join is ONE products find."""
        db = _sell_through_fixture()
        client = _sell_through_client(db, monkeypatch)
        products = db.get_collection("products")
        orders = db.get_collection("orders")
        stock = db.get_collection("stock_units")
        products.find_calls = products.find_one_calls = 0
        orders.find_calls = stock.find_calls = 0

        resp = client.get("/inventory/sell-through-analysis?days=30")
        assert resp.status_code == 200, resp.text
        assert products.find_one_calls == 0
        assert products.find_calls == 1
        assert orders.find_calls == 1
        assert stock.find_calls == 1

    def test_empty_db_shape(self, monkeypatch):
        db = _FakeDB(
            {
                "products": _FakeColl(),
                "orders": _FakeColl(),
                "stock_units": _FakeColl(),
            }
        )
        client = _sell_through_client(db, monkeypatch)
        resp = client.get("/inventory/sell-through-analysis?days=30")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"period_days": 30, "brands": []}


# ============================================================================
# 2 + 3. _stock_category_map (reports) / _build_product_master_map (analytics)
# ============================================================================


class _BatchableProductRepo:
    """Product-repo fake WITH a batchable .collection (the real repo shape)."""

    def __init__(self, docs: List[Dict]):
        self.collection = _FakeColl(docs)

    def find_by_id(self, pid):
        return self.collection.find_one({"product_id": pid})


class _LegacyOnlyProductRepo:
    """Product-repo fake with ONLY find_by_id (the FakeProductRepo pattern of
    test_reports_analytics_correctness.py) -- forces the per-id fallback."""

    def __init__(self, docs: List[Dict]):
        self._coll = _FakeColl(docs)

    def find_by_id(self, pid):
        return self._coll.find_one({"product_id": pid})


_MASTER_DOCS = [
    {"product_id": "P-FRAME", "category": "FRAME", "sku": "F-1",
     "name": "Ray-Ban Aviator", "cost_price": 2500, "mrp": 5000},
    {"product_id": "P-NOCAT", "sku": "N-1", "model": "ModelOnly",
     "cost_price": 100},  # master hit WITHOUT category: no catalog fallback
]

_CATALOG_DOCS = [
    {"_id": "cat-1", "id": "P-CAT", "sku": "C-SKU-1", "title": "Cartier CT01",
     "category": "SUNGLASS", "pricing": {"cost_price": 9000, "mrp": 20000}},
    {"_id": "cat-2", "id": "OTHER-ID", "sku": "P-BYSKU", "title": "Gucci G1",
     "category": "FRAME", "pricing": {"mrp": 12000}},  # resolves by sku
    {"_id": "cat-3", "id": "P-CAT-NOCAT", "title": "No Category",
     "pricing": {}},  # catalog hit without category
]

_STOCK_ROWS = [
    {"product_id": "P-FRAME", "quantity": 1},
    {"product_id": "P-FRAME", "quantity": 2},  # duplicate pid: one lookup
    {"product_id": "P-NOCAT", "quantity": 1},
    {"product_id": "P-CAT", "quantity": 1},
    {"product_id": "P-BYSKU", "quantity": 1},
    {"product_id": "P-CAT-NOCAT", "quantity": 1},
    {"product_id": "P-UNKNOWN", "quantity": 1},
    {"product_id": None, "quantity": 1},  # ignored
]


def _patch_catalog(monkeypatch, docs: List[Dict]) -> _FakeColl:
    coll = _FakeColl(docs)
    # Both the batch path (imported at call time from .orders) and the
    # per-id _resolve_catalog_product_doc read this module-level getter.
    monkeypatch.setattr(orders_mod, "_get_catalog_collection", lambda: coll)
    return coll


class TestStockCategoryMapBatched:
    EXPECTED = {
        "P-FRAME": "FRAME",  # master
        "P-CAT": "SUNGLASS",  # catalog by id
        "P-BYSKU": "FRAME",  # catalog by sku
        # P-NOCAT: master hit without category -> absent (NOT catalog-resolved)
        # P-CAT-NOCAT: catalog hit without category -> absent
        # P-UNKNOWN: nowhere -> absent
    }

    def test_batch_path_output(self, monkeypatch):
        repo = _BatchableProductRepo(_MASTER_DOCS)
        catalog = _patch_catalog(monkeypatch, _CATALOG_DOCS)
        monkeypatch.setattr(rep, "get_product_repository", lambda: repo)

        assert rep._stock_category_map(_STOCK_ROWS) == self.EXPECTED
        # ONE products query + ONE catalog query; zero per-id find_ones.
        assert repo.collection.find_calls == 1
        assert repo.collection.find_one_calls == 0
        assert catalog.find_calls == 1
        assert catalog.find_one_calls == 0

    def test_fallback_path_matches_batch_path(self, monkeypatch):
        """A repo that cannot batch resolves through the original per-id loop
        and must produce the identical map."""
        repo = _LegacyOnlyProductRepo(_MASTER_DOCS)
        _patch_catalog(monkeypatch, _CATALOG_DOCS)
        monkeypatch.setattr(rep, "get_product_repository", lambda: repo)

        assert rep._stock_category_map(_STOCK_ROWS) == self.EXPECTED

    def test_master_hit_without_category_never_falls_to_catalog(self, monkeypatch):
        """Legacy semantics: a master doc WITHOUT a category blocks the catalog
        fallback for that pid (`if not product` gated it). A catalog doc that
        WOULD resolve it must stay unused."""
        repo = _BatchableProductRepo(
            [{"product_id": "P-NOCAT", "sku": "N-1"}]
        )
        _patch_catalog(
            monkeypatch,
            [{"_id": "c", "id": "P-NOCAT", "title": "X", "category": "FRAME"}],
        )
        monkeypatch.setattr(rep, "get_product_repository", lambda: repo)
        assert rep._stock_category_map([{"product_id": "P-NOCAT"}]) == {}

    def test_no_repo_no_catalog_is_failsoft_empty(self, monkeypatch):
        monkeypatch.setattr(rep, "get_product_repository", lambda: None)
        monkeypatch.setattr(orders_mod, "_get_catalog_collection", lambda: None)
        assert rep._stock_category_map([{"product_id": "X"}]) == {}

    def test_empty_rows_short_circuit(self, monkeypatch):
        repo = _BatchableProductRepo(_MASTER_DOCS)
        monkeypatch.setattr(rep, "get_product_repository", lambda: repo)
        assert rep._stock_category_map([]) == {}
        assert repo.collection.find_calls == 0


class TestBuildProductMasterMapBatched:
    EXPECTED = {
        "P-FRAME": {
            "sku": "F-1",
            "name": "Ray-Ban Aviator",
            "category": "FRAME",
            "cost_price": 2500.0,
            "mrp": 5000.0,
        },
        # master hit without category/name: category defaults, model fallback
        "P-NOCAT": {
            "sku": "N-1",
            "name": "ModelOnly",
            "category": "Other",
            "cost_price": 100.0,
            "mrp": 0.0,
        },
        # catalog by id: title -> name, nested pricing flattened
        "P-CAT": {
            "sku": "C-SKU-1",
            "name": "Cartier CT01",
            "category": "SUNGLASS",
            "cost_price": 9000.0,
            "mrp": 20000.0,
        },
        # catalog by sku: no cost_price in pricing -> 0.0
        "P-BYSKU": {
            "sku": "P-BYSKU",
            "name": "Gucci G1",
            "category": "FRAME",
            "cost_price": 0.0,
            "mrp": 12000.0,
        },
        # catalog hit WITHOUT category still resolves (category -> "Other")
        "P-CAT-NOCAT": {
            "sku": "",
            "name": "No Category",
            "category": "Other",
            "cost_price": 0.0,
            "mrp": 0.0,
        },
        # P-UNKNOWN: absent
    }

    def test_batch_path_output(self, monkeypatch):
        repo = _BatchableProductRepo(_MASTER_DOCS)
        catalog = _patch_catalog(monkeypatch, _CATALOG_DOCS)
        monkeypatch.setattr(an, "get_product_repository", lambda: repo)

        assert an._build_product_master_map(_STOCK_ROWS) == self.EXPECTED
        assert repo.collection.find_calls == 1
        assert repo.collection.find_one_calls == 0
        assert catalog.find_calls == 1
        assert catalog.find_one_calls == 0

    def test_fallback_path_matches_batch_path(self, monkeypatch):
        repo = _LegacyOnlyProductRepo(_MASTER_DOCS)
        _patch_catalog(monkeypatch, _CATALOG_DOCS)
        monkeypatch.setattr(an, "get_product_repository", lambda: repo)

        assert an._build_product_master_map(_STOCK_ROWS) == self.EXPECTED

    def test_none_repo_failsoft(self, monkeypatch):
        """Pre-existing contract (test_build_product_master_map_falls_back_softly):
        no repo + no catalog match -> empty map, no crash."""
        monkeypatch.setattr(an, "get_product_repository", lambda: None)
        monkeypatch.setattr(orders_mod, "_get_catalog_collection", lambda: None)
        out = an._build_product_master_map([{"product_id": "X", "quantity": 1}])
        assert out == {}
