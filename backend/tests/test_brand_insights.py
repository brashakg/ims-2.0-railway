"""
IMS 2.0 - Brand insights (Inventory > Insights > Brands) tests
==============================================================
Locks the contract of api/services/brand_insights.py + the
GET /inventory/brand-insights endpoint:

  * fold_brand_rows: on-hand + sold + revenue roll product -> brand; blank /
    missing / unresolvable brands fold into "Unknown"; stock value prices
    on-hand units at offer_price with mrp fallback (positive coercion, junk
    0/negative prices read as absent); rows sort by revenue desc.
  * KPI math is SHARED with collection_insights: sell_through_percent is
    sell_through()*100 and days_cover is days_of_cover() (999 cap, None when
    no signal), with non-30-day windows normalised to a 30-day rate that
    never rounds real sales down to zero.
  * endpoint: single-pass composition (no per-item product lookups), store
    scoping via resolve_store_scope, {"period_days", "store_id", "brands"}
    envelope. Endpoint tests reuse the throwaway-real-mongo pattern from
    test_inventory_quantity.py and skip fail-soft when Mongo is unreachable.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_brand_insights.py -q
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import brand_insights as bi  # noqa: E402


STORE = "BV-BRAND-01"


# ============================================================================
# Pure fold tests (no DB)
# ============================================================================


def _doc(pid: str, brand: Any, offer=None, mrp=None) -> Dict[str, Any]:
    return {
        "_id": pid,
        "product_id": pid,
        "brand": brand,
        "offer_price": offer,
        "mrp": mrp,
    }


class TestNormalizeBrand:
    def test_strips_whitespace(self):
        assert bi.normalize_brand("  Ray-Ban  ") == "Ray-Ban"

    @pytest.mark.parametrize("raw", ["", "   ", None, 42, {"x": 1}])
    def test_blank_or_junk_is_unknown(self, raw):
        assert bi.normalize_brand(raw) == "Unknown"


class TestFoldBrandRows:
    def _rows(self):
        products = [
            _doc("P1", "Ray-Ban ", offer=4500, mrp=5000),
            _doc("P2", "Ray-Ban", offer=0, mrp=2000),  # junk offer -> mrp
            _doc("P3", "", mrp=None),  # blank brand -> Unknown, no price -> 0
            _doc("P4", "Oakley", offer=8000),
        ]
        on_hand = {"P1": 3, "P2": 2, "P3": 5, "P4": 0}
        sales = {
            "P1": {"units": 2, "revenue": 9000.0},
            "P4": {"units": 4, "revenue": 32000.0},
            # pid with no product doc at all -> folds to Unknown, not dropped
            "GHOST": {"units": 1, "revenue": 100.0},
        }
        return bi.fold_brand_rows(products, on_hand, sales, period_days=30)

    def test_brand_fold_and_kpis(self):
        rows = {r["brand"]: r for r in self._rows()}
        assert set(rows) == {"Ray-Ban", "Oakley", "Unknown"}

        rb = rows["Ray-Ban"]
        assert rb["units_on_hand"] == 5  # 3 + 2 (P4 qty 0 excluded)
        # 3 x offer 4500 + 2 x mrp 2000 (offer=0 is junk -> mrp fallback)
        assert rb["stock_value"] == 17500.0
        assert rb["units_sold"] == 2
        assert rb["revenue"] == 9000.0
        # sell_through(2, 5) = 2/7 -> 28.6%
        assert rb["sell_through_percent"] == 28.6
        # days_of_cover(5, 2) = 5 / (2/30) = 75.0
        assert rb["days_cover"] == 75.0

        oak = rows["Oakley"]
        assert oak["units_on_hand"] == 0
        assert oak["stock_value"] == 0.0
        assert oak["units_sold"] == 4
        assert oak["sell_through_percent"] == 100.0
        assert oak["days_cover"] == 0.0  # stock gone, still selling

        unk = rows["Unknown"]
        assert unk["units_on_hand"] == 5  # P3 (blank brand)
        assert unk["stock_value"] == 0.0  # no usable price
        assert unk["units_sold"] == 1  # GHOST line folded, not dropped
        assert unk["revenue"] == 100.0

    def test_sorted_by_revenue_desc(self):
        rows = self._rows()
        assert [r["brand"] for r in rows] == ["Oakley", "Ray-Ban", "Unknown"]

    def test_stocked_but_unsold_brand(self):
        rows = bi.fold_brand_rows(
            [_doc("P9", "Fossil", offer=1000)], {"P9": 6}, {}, period_days=30
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["sell_through_percent"] == 0.0
        assert r["days_cover"] == 999.0  # stock, zero sales -> capped cover

    def test_no_signal_pids_produce_no_rows(self):
        # A product with neither stock nor sales must not appear at all.
        rows = bi.fold_brand_rows([_doc("P9", "Fossil")], {"P9": 0}, {})
        assert rows == []


class TestWindowNormalisation:
    def test_30_day_window_is_exact(self):
        assert bi.days_cover(5, 2, 30) == 75.0

    def test_90_day_window_scales_to_monthly_rate(self):
        # 9 sold over 90d = 3/month -> cover = 10 / (3/30) = 100 days.
        assert bi.days_cover(10, 9, 90) == 100.0

    def test_small_sales_never_round_to_not_moving(self):
        # 1 sold over 90d rounds to a 30d-equivalent of >= 1, NOT 0 -- so the
        # brand reads "slow" (finite cover), never "not moving" (999).
        assert bi.days_cover(10, 1, 90) == 300.0

    def test_no_signal_is_none(self):
        assert bi.days_cover(0, 0, 30) is None


# ============================================================================
# Endpoint tests -- throwaway real mongo (pattern: test_inventory_quantity.py)
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip(f"Mongo unavailable at {uri}; skipping integration tests")
        return None

    db_name = f"ims_test_brand_ins_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


@pytest.fixture
def brand_client(mongo_db, monkeypatch):
    """Bare app mounting the inventory router against the throwaway db, with
    an authenticated SUPERADMIN (passes every gate; store scope resolves to
    None = all stores)."""
    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(inventory, "_get_db", lambda: proxy)

    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")

    async def _fake_user():
        return {
            "user_id": "u-brand",
            "username": "brandadmin",
            "roles": ["SUPERADMIN"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _seed_product(mongo_db, brand: str, offer: float, **over: Any) -> str:
    pid = over.pop("product_id", f"PRD-{uuid.uuid4().hex[:8]}")
    doc: Dict[str, Any] = {
        "_id": pid,
        "product_id": pid,
        "sku": f"SKU-{pid[-6:]}",
        "brand": brand,
        "model": "M1",
        "category": "SUNGLASS",
        "mrp": offer + 500,
        "offer_price": offer,
        "is_active": True,
    }
    doc.update(over)
    mongo_db["products"].insert_one(doc)
    return pid


def _seed_units(mongo_db, product_id: str, n: int, store_id: str = STORE):
    for _ in range(n):
        mongo_db["stock_units"].insert_one(
            {
                "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
                "product_id": product_id,
                "store_id": store_id,
                "status": "AVAILABLE",
            }
        )


def _seed_order(
    mongo_db, product_id: str, qty: int, item_total: float, store_id: str = STORE
):
    mongo_db["orders"].insert_one(
        {
            "order_id": f"ORD-{uuid.uuid4().hex[:8]}",
            "store_id": store_id,
            "status": "COMPLETED",
            "created_at": datetime.utcnow() - timedelta(days=2),
            "items": [
                {"product_id": product_id, "quantity": qty, "item_total": item_total}
            ],
        }
    )


class TestBrandInsightsEndpoint:
    def test_envelope_and_brand_rollup(self, brand_client, mongo_db):
        p_rb = _seed_product(mongo_db, "Ray-Ban", 4500.0)
        p_ok = _seed_product(mongo_db, "Oakley", 8000.0)
        _seed_units(mongo_db, p_rb, 4)
        _seed_units(mongo_db, p_ok, 1)
        _seed_order(mongo_db, p_rb, qty=2, item_total=9000.0)

        resp = brand_client.get("/inventory/brand-insights?days=30")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["period_days"] == 30
        by_brand = {r["brand"]: r for r in body["brands"]}

        rb = by_brand["Ray-Ban"]
        assert rb["units_on_hand"] == 4
        assert rb["stock_value"] == 4 * 4500.0
        assert rb["units_sold"] == 2
        assert rb["revenue"] == 9000.0
        assert rb["sell_through_percent"] == round(2 / 6 * 100, 1)
        assert rb["days_cover"] == 60.0  # 4 / (2/30)

        oak = by_brand["Oakley"]
        assert oak["units_sold"] == 0
        assert oak["units_on_hand"] == 1
        assert oak["days_cover"] == 999.0

        # revenue desc: the selling brand ranks first
        assert body["brands"][0]["brand"] == "Ray-Ban"

    def test_store_scoping_excludes_other_stores(self, brand_client, mongo_db):
        pid = _seed_product(mongo_db, "Vogue", 3000.0)
        _seed_units(mongo_db, pid, 2, store_id=STORE)
        _seed_units(mongo_db, pid, 7, store_id="BV-OTHER-01")
        _seed_order(mongo_db, pid, qty=1, item_total=3000.0, store_id="BV-OTHER-01")

        resp = brand_client.get(f"/inventory/brand-insights?days=30&store_id={STORE}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["store_id"] == STORE
        row = {r["brand"]: r for r in body["brands"]}["Vogue"]
        assert row["units_on_hand"] == 2  # other store's 7 excluded
        assert row["units_sold"] == 0  # other store's sale excluded

    def test_blank_brand_folds_to_unknown(self, brand_client, mongo_db):
        pid = _seed_product(mongo_db, "   ", 1000.0)
        _seed_units(mongo_db, pid, 3)

        resp = brand_client.get("/inventory/brand-insights?days=30")
        assert resp.status_code == 200, resp.text
        brands = {r["brand"] for r in resp.json()["brands"]}
        assert "Unknown" in brands
        assert "   " not in brands

    def test_days_validation(self, brand_client, mongo_db):
        assert brand_client.get("/inventory/brand-insights?days=0").status_code == 422
        assert brand_client.get("/inventory/brand-insights?days=400").status_code == 422
