"""
IMS 2.0 - Bulk pricing / offers test suite
===========================================
Covers the v2 Pricing & Offers slice:

  POST /api/v1/products/bulk-price  -- % / flat delta to a filtered set
  POST /api/v1/products/bulk-offer  -- set / clear offer_price on a set

Two layers:

  1. PURE cap-resolver tests (no DB) -- api/services/pricing_caps.py is a pure
     module, so the cap math (category caps, luxury brand caps, MRP > offer
     block, effective cap = min(category, brand)) is exercised exhaustively
     and deterministically with zero infrastructure.

  2. ENDPOINT tests against a REAL mongo:7.0 (CI provides one; local dev may
     fall back to localhost). Skipped fail-soft when Mongo is unreachable so
     the unit-test sweep still passes on a laptop without Mongo. These prove
     the revenue-critical contract:
       - dry-run mutates NOTHING
       - cap-violation rows are rejected with a reason (never clamped)
       - valid rows actually apply on apply=True
       - MRP > offer_price is blocked
       - committed changes are audit-logged
       - the operation is idempotent (re-apply changes nothing)

Business rules enforced (CLAUDE.md "Non-negotiable business rules" -> Pricing):
  Category caps: MASS 15% / PREMIUM 20% / LUXURY 5% / SERVICE 10% /
                 NON_DISCOUNTABLE 0%
  Luxury brand caps: Cartier/Chopard/Bvlgari 2% ; Gucci/Prada/Versace/
                     Burberry 5% (override the category cap when lower).
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = pytest.mark.asyncio


# ============================================================================
# Layer 1 -- pure cap-resolver tests (no DB)
# ============================================================================


class TestPricingCapsPure:
    """Exhaustive, deterministic tests of the pure cap resolver."""

    def test_category_caps_match_business_rules(self):
        from api.services.pricing_caps import CATEGORY_DISCOUNT_CAPS

        assert CATEGORY_DISCOUNT_CAPS["MASS"] == 15.0
        assert CATEGORY_DISCOUNT_CAPS["PREMIUM"] == 20.0
        assert CATEGORY_DISCOUNT_CAPS["LUXURY"] == 5.0
        assert CATEGORY_DISCOUNT_CAPS["SERVICE"] == 10.0
        assert CATEGORY_DISCOUNT_CAPS["NON_DISCOUNTABLE"] == 0.0

    def test_luxury_brand_caps_match_business_rules(self):
        from api.services.pricing_caps import LUXURY_BRAND_CAPS

        for b in ("Cartier", "Chopard", "Bvlgari"):
            assert LUXURY_BRAND_CAPS[b.upper()] == 2.0
        for b in ("Gucci", "Prada", "Versace", "Burberry"):
            assert LUXURY_BRAND_CAPS[b.upper()] == 5.0

    def test_unknown_category_defaults_to_mass(self):
        from api.services.pricing_caps import effective_discount_cap

        assert effective_discount_cap(None) == 15.0
        assert effective_discount_cap("") == 15.0
        assert effective_discount_cap("WHATEVER") == 15.0

    def test_brand_cap_overrides_category_when_lower(self):
        from api.services.pricing_caps import effective_discount_cap

        # Cartier is a PREMIUM-tier product (20%) but the brand cap (2%) wins.
        assert effective_discount_cap("PREMIUM", "Cartier") == 2.0
        # Gucci under MASS (15%) -> brand cap 5% wins.
        assert effective_discount_cap("MASS", "Gucci") == 5.0
        # Case / whitespace insensitive.
        assert effective_discount_cap("PREMIUM", "  cartier ") == 2.0

    def test_category_cap_wins_when_lower_than_brand(self):
        from api.services.pricing_caps import effective_discount_cap

        # NON_DISCOUNTABLE (0%) beats even a luxury brand's 5%.
        assert effective_discount_cap("NON_DISCOUNTABLE", "Gucci") == 0.0

    def test_non_luxury_brand_has_no_brand_constraint(self):
        from api.services.pricing_caps import brand_cap_for, effective_discount_cap

        assert brand_cap_for("Ray-Ban") is None
        assert effective_discount_cap("LUXURY", "Ray-Ban") == 5.0  # category only

    def test_evaluate_within_cap_is_ok(self):
        from api.services.pricing_caps import evaluate_offer_price

        # MASS 15% cap: 10% discount is fine.
        v = evaluate_offer_price(mrp=1000, offer_price=900, discount_category="MASS")
        assert v["ok"] is True
        assert v["reason"] is None
        assert v["effective_cap_pct"] == 15.0
        assert v["implied_discount_pct"] == 10.0

    def test_evaluate_exactly_at_cap_is_ok(self):
        from api.services.pricing_caps import evaluate_offer_price

        # Exactly 15% off under MASS -> allowed (float dust must not reject).
        v = evaluate_offer_price(mrp=1000, offer_price=850, discount_category="MASS")
        assert v["ok"] is True
        assert v["implied_discount_pct"] == 15.0

    def test_evaluate_over_cap_is_violation(self):
        from api.services.pricing_caps import evaluate_offer_price

        # 20% off under LUXURY (5% cap) -> violation.
        v = evaluate_offer_price(mrp=1000, offer_price=800, discount_category="LUXURY")
        assert v["ok"] is False
        assert v["reason"] == "CAP_EXCEEDED"
        assert "exceeds" in v["message"].lower()
        assert v["effective_cap_pct"] == 5.0

    def test_evaluate_luxury_brand_violation(self):
        from api.services.pricing_caps import evaluate_offer_price

        # 3% off a Cartier (2% brand cap) -> violation even though the
        # category (PREMIUM) would allow 20%.
        v = evaluate_offer_price(
            mrp=100000, offer_price=97000, discount_category="PREMIUM", brand="Cartier"
        )
        assert v["ok"] is False
        assert v["reason"] == "CAP_EXCEEDED"
        assert v["effective_cap_pct"] == 2.0

    def test_evaluate_mrp_below_offer_is_blocked(self):
        from api.services.pricing_caps import evaluate_offer_price

        # offer_price > mrp -> the "MRP > offer_price blocked at DB" rule.
        v = evaluate_offer_price(mrp=1000, offer_price=1200, discount_category="MASS")
        assert v["ok"] is False
        assert v["reason"] == "MRP_BELOW_OFFER"

    def test_evaluate_invalid_mrp(self):
        from api.services.pricing_caps import evaluate_offer_price

        assert evaluate_offer_price(0, 0, "MASS")["reason"] == "INVALID_MRP"
        assert evaluate_offer_price(-5, 10, "MASS")["reason"] == "INVALID_MRP"

    def test_implied_discount_pct_never_negative(self):
        from api.services.pricing_caps import implied_discount_pct

        assert implied_discount_pct(1000, 1100) == 0.0  # offer above MRP -> 0
        assert implied_discount_pct(0, 100) == 0.0  # no MRP -> 0
        assert implied_discount_pct(1000, 900) == 10.0


# ============================================================================
# Layer 2 -- endpoint tests against a real mongo:7.0 (skip if absent)
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    """Real mongo:7.0 connection. Skip the module fail-soft if absent."""
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

    db_name = f"ims_test_bulk_pricing_{uuid.uuid4().hex[:8]}"
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
    """Minimal get_db() shape exposing mongo collections by name + attr.

    `db.products` (attribute access) and `db.get_collection("stock_units")`
    both resolve to the underlying test mongo db, so the repository getters
    and the router's stock-scope / audit helpers all hit the test DB.
    """

    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


@pytest.fixture
def patch_db(mongo_db, monkeypatch):
    """Point both get_db() entrypoints (dependencies + database.connection) at
    the test mongo db, and force DATABASE_AVAILABLE on so get_db() returns it.

    Also clears the product list cache between tests so cache invalidation in
    the endpoint doesn't leak stale rows across assertions.
    """
    proxy = _DBProxy(mongo_db)
    import api.dependencies as deps
    from database import connection as conn

    monkeypatch.setattr(deps, "DATABASE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps, "get_db", lambda: proxy)
    monkeypatch.setattr(conn, "get_db", lambda: proxy, raising=False)

    try:
        from api.services.cache import cache

        cache.clear() if hasattr(cache, "clear") else None
    except Exception:  # noqa: BLE001
        pass
    return proxy


ADMIN_USER = {
    "user_id": "test-admin-bulk",
    "username": "bulkadmin",
    "roles": ["SUPERADMIN"],
    "active_store_id": "BV-TEST-01",
}


def _seed_products(mongo_db, products: List[Dict[str, Any]]) -> List[str]:
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(mongo_db["products"])
    pids: List[str] = []
    for p in products:
        created = repo.create(p)
        assert created is not None
        pids.append(created["product_id"])
    return pids


def _get_product(mongo_db, pid: str) -> Dict[str, Any]:
    return mongo_db["products"].find_one({"product_id": pid})


# A small catalog spanning tiers + a luxury brand for cap testing.
def _sample_catalog() -> List[Dict[str, Any]]:
    return [
        {
            "sku": "BLK-MASS-1",
            "category": "FRAME",
            "discount_category": "MASS",
            "brand": "Ray-Ban",
            "model": "Mass Frame",
            "mrp": 1000.0,
            "offer_price": 1000.0,
            "is_active": True,
        },
        {
            "sku": "BLK-LUX-1",
            "category": "SUNGLASS",
            "discount_category": "LUXURY",
            "brand": "Cartier",
            "model": "Lux Shade",
            "mrp": 100000.0,
            "offer_price": 100000.0,
            "is_active": True,
        },
        {
            "sku": "BLK-PREM-1",
            "category": "FRAME",
            "discount_category": "PREMIUM",
            "brand": "Vogue",
            "model": "Prem Frame",
            "mrp": 5000.0,
            "offer_price": 5000.0,
            "is_active": True,
        },
    ]


class TestBulkOfferEndpoint:
    """POST /products/bulk-offer behaviour against a real DB."""

    async def test_dry_run_mutates_nothing(self, mongo_db, patch_db):
        """A dry-run (apply omitted) returns per-row preview but writes NOTHING
        back to the products collection."""
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        pids = _seed_products(mongo_db, _sample_catalog())
        body = BulkOfferRequest(action="SET", discount_percent=5.0, apply=False)
        res = await bulk_offer_update(body, ADMIN_USER)

        assert res["dry_run"] is True
        assert res["summary"]["counts"]["total"] == 3
        # Nothing changed in the DB.
        for pid in pids:
            doc = _get_product(mongo_db, pid)
            assert doc["offer_price"] == doc["mrp"]  # untouched

    async def test_cap_violation_rejected_with_reason(self, mongo_db, patch_db):
        """A 5% offer on a Cartier (2% brand cap) is reported as a violation
        with a reason, and -- even with apply=True -- is NOT written."""
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        pids = _seed_products(mongo_db, _sample_catalog())
        # 5% off everything: fine for MASS(15)/PREMIUM(20), violates Cartier(2).
        body = BulkOfferRequest(action="SET", discount_percent=5.0, apply=True)
        res = await bulk_offer_update(body, ADMIN_USER)

        rows = {r["sku"]: r for r in res["rows"]}
        lux = rows["BLK-LUX-1"]
        assert lux["ok"] is False
        assert lux["reason"] == "CAP_EXCEEDED"
        assert lux["effective_cap_pct"] == 2.0
        assert lux["message"]  # human explanation present

        # The luxury product's offer_price stays at MRP (NOT clamped to 2%).
        lux_doc = _get_product(mongo_db, pids[1])
        assert lux_doc["offer_price"] == 100000.0

        # The valid MASS + PREMIUM rows DID apply (5% off).
        assert rows["BLK-MASS-1"]["ok"] is True
        assert _get_product(mongo_db, pids[0])["offer_price"] == 950.0
        assert _get_product(mongo_db, pids[2])["offer_price"] == 4750.0

        assert res["summary"]["counts"]["violations"] == 1
        assert res["summary"]["counts"]["valid"] == 2
        assert res["summary"]["committed"] == 2

    async def test_within_cap_applies(self, mongo_db, patch_db):
        """A 2% offer is within Cartier's brand cap -> it applies."""
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        pids = _seed_products(mongo_db, _sample_catalog())
        body = BulkOfferRequest(
            action="SET", discount_percent=2.0, brand="Cartier", apply=True
        )
        res = await bulk_offer_update(body, ADMIN_USER)

        assert res["summary"]["counts"]["total"] == 1  # brand filter
        lux_doc = _get_product(mongo_db, pids[1])
        assert lux_doc["offer_price"] == 98000.0  # 2% off 100000

    async def test_clear_offer_resets_to_mrp(self, mongo_db, patch_db):
        """CLEAR resets offer_price back up to MRP (removes the discount)."""
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        cat = _sample_catalog()
        cat[0]["offer_price"] = 850.0  # an existing 15% offer
        pids = _seed_products(mongo_db, cat)

        body = BulkOfferRequest(action="CLEAR", brand="Ray-Ban", apply=True)
        res = await bulk_offer_update(body, ADMIN_USER)

        doc = _get_product(mongo_db, pids[0])
        assert doc["offer_price"] == 1000.0  # reset to MRP
        assert res["summary"]["committed"] == 1

    async def test_idempotent_reapply_changes_nothing(self, mongo_db, patch_db):
        """Re-running the same SET when rows already sit at target -> all rows
        reported 'unchanged' and committed count is 0."""
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        _seed_products(mongo_db, _sample_catalog())
        body = BulkOfferRequest(
            action="SET", discount_percent=5.0, brand="Ray-Ban", apply=True
        )
        first = await bulk_offer_update(body, ADMIN_USER)
        assert first["summary"]["committed"] == 1

        second = await bulk_offer_update(body, ADMIN_USER)
        assert second["summary"]["committed"] == 0
        assert second["summary"]["counts"]["unchanged"] == 1

    async def test_committed_change_is_audit_logged(self, mongo_db, patch_db):
        """An applied bulk offer writes a BULK_OFFER_UPDATE row to audit_logs."""
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        _seed_products(mongo_db, _sample_catalog())
        body = BulkOfferRequest(
            action="SET", discount_percent=5.0, brand="Ray-Ban", apply=True
        )
        await bulk_offer_update(body, ADMIN_USER)

        log = mongo_db["audit_logs"].find_one({"action": "BULK_OFFER_UPDATE"})
        assert log is not None
        assert log["user_id"] == ADMIN_USER["user_id"]
        assert log["details"]["summary"]["committed"] == 1
        assert len(log["details"]["changes"]) == 1


class TestBulkPriceEndpoint:
    """POST /products/bulk-price behaviour against a real DB."""

    async def test_dry_run_mutates_nothing(self, mongo_db, patch_db):
        from api.routers.products import bulk_price_update, BulkPriceRequest

        pids = _seed_products(mongo_db, _sample_catalog())
        # +10% to MRP, dry run.
        body = BulkPriceRequest(mode="PERCENT", target="MRP", amount=10.0, apply=False)
        res = await bulk_price_update(body, ADMIN_USER)

        assert res["dry_run"] is True
        for pid in pids:
            doc = _get_product(mongo_db, pid)
            # MRP unchanged on disk.
            assert doc["mrp"] in (1000.0, 100000.0, 5000.0)

    async def test_percent_raise_both_applies_and_keeps_offer_le_mrp(
        self, mongo_db, patch_db
    ):
        """+10% to BOTH on a product selling at MRP keeps offer <= MRP and is
        valid (raising prices never trips a discount cap)."""
        from api.routers.products import bulk_price_update, BulkPriceRequest

        pids = _seed_products(mongo_db, _sample_catalog())
        body = BulkPriceRequest(
            mode="PERCENT", target="BOTH", amount=10.0, brand="Ray-Ban", apply=True
        )
        res = await bulk_price_update(body, ADMIN_USER)

        doc = _get_product(mongo_db, pids[0])
        assert doc["mrp"] == 1100.0
        assert doc["offer_price"] == 1100.0
        assert res["summary"]["committed"] == 1

    async def test_offer_cut_violating_cap_is_rejected(self, mongo_db, patch_db):
        """A flat -Rs cut to OFFER only that pushes a Cartier below its 2% cap
        is rejected (offer stays at MRP), while a within-cap row applies."""
        from api.routers.products import bulk_price_update, BulkPriceRequest

        pids = _seed_products(mongo_db, _sample_catalog())
        # -Rs 6000 off OFFER. On Cartier MRP 100000 -> 94000 = 6% > 2% cap.
        body = BulkPriceRequest(
            mode="FLAT", target="OFFER", amount=-6000.0, brand="Cartier", apply=True
        )
        res = await bulk_price_update(body, ADMIN_USER)

        row = res["rows"][0]
        assert row["ok"] is False
        assert row["reason"] == "CAP_EXCEEDED"
        # Not written.
        assert _get_product(mongo_db, pids[1])["offer_price"] == 100000.0

    async def test_mrp_below_offer_is_blocked(self, mongo_db, patch_db):
        """Dropping MRP (OFFER only target) such that the existing offer would
        exceed the new MRP is blocked with MRP_BELOW_OFFER."""
        from api.routers.products import bulk_price_update, BulkPriceRequest

        cat = _sample_catalog()
        cat[0]["offer_price"] = 1000.0  # at MRP
        pids = _seed_products(mongo_db, cat)

        # Cut MRP by Rs 200 -> 800, but offer stays 1000 > 800 -> blocked.
        body = BulkPriceRequest(
            mode="FLAT", target="MRP", amount=-200.0, brand="Ray-Ban", apply=True
        )
        res = await bulk_price_update(body, ADMIN_USER)

        row = res["rows"][0]
        assert row["ok"] is False
        assert row["reason"] == "MRP_BELOW_OFFER"
        # Unchanged on disk.
        doc = _get_product(mongo_db, pids[0])
        assert doc["mrp"] == 1000.0

    async def test_committed_change_is_audit_logged(self, mongo_db, patch_db):
        from api.routers.products import bulk_price_update, BulkPriceRequest

        _seed_products(mongo_db, _sample_catalog())
        body = BulkPriceRequest(
            mode="PERCENT", target="MRP", amount=10.0, brand="Ray-Ban", apply=True
        )
        await bulk_price_update(body, ADMIN_USER)

        log = mongo_db["audit_logs"].find_one({"action": "BULK_PRICE_UPDATE"})
        assert log is not None
        assert log["details"]["summary"]["committed"] == 1

    async def test_store_filter_scopes_to_store_stock(self, mongo_db, patch_db):
        """store_id restricts to products with stock at that store."""
        from api.routers.products import bulk_price_update, BulkPriceRequest
        from database.repositories.product_repository import StockRepository

        pids = _seed_products(mongo_db, _sample_catalog())
        # Give ONLY the MASS product stock at BV-STORE-A.
        StockRepository(mongo_db["stock_units"]).create(
            {
                "stock_id": f"STK-{uuid.uuid4().hex[:8]}",
                "product_id": pids[0],
                "store_id": "BV-STORE-A",
                "barcode": f"BC-{uuid.uuid4().hex[:8]}",
                "quantity": 1,
                "status": "AVAILABLE",
            }
        )

        body = BulkPriceRequest(
            mode="PERCENT",
            target="MRP",
            amount=5.0,
            store_id="BV-STORE-A",
            apply=False,
        )
        res = await bulk_price_update(body, ADMIN_USER)
        assert res["summary"]["counts"]["total"] == 1
        assert res["rows"][0]["sku"] == "BLK-MASS-1"


class TestBulkValidation:
    """Request-shape validation (no DB needed for the 400s, but the role gate
    runs first; we call the function directly so validation is what trips)."""

    async def test_bad_mode_rejected(self, patch_db, mongo_db):
        from fastapi import HTTPException
        from api.routers.products import bulk_price_update, BulkPriceRequest

        body = BulkPriceRequest(mode="WRONG", target="OFFER", amount=1.0)
        with pytest.raises(HTTPException) as ei:
            await bulk_price_update(body, ADMIN_USER)
        assert ei.value.status_code == 400

    async def test_set_without_value_rejected(self, patch_db, mongo_db):
        from fastapi import HTTPException
        from api.routers.products import bulk_offer_update, BulkOfferRequest

        body = BulkOfferRequest(action="SET")  # neither pct nor price
        with pytest.raises(HTTPException) as ei:
            await bulk_offer_update(body, ADMIN_USER)
        assert ei.value.status_code == 400
