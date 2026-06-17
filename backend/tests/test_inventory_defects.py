"""
IMS 2.0 - Inventory defect regression tests
============================================
INV-1: discount_category persisted on product create (prevents LUXURY over-discount).
INV-6: category-scoped stock-count start resolves product_ids via the products
       collection rather than filtering stock_units on a non-existent `category`
       field.

All tests are pure-unit (no live DB required): they exercise the helper
functions directly, mirroring the style of test_inventory_stock_add_bound.py
and test_inventory_correctness.py.
"""

from __future__ import annotations

import pytest


# ===========================================================================
# INV-1 — discount_category persisted on product create
# ===========================================================================


class TestDiscountCategoryOnCreate:
    """_build_product_data must include discount_category in the returned
    dict so it is actually persisted when a product is created."""

    def _make_product(self, **kwargs):
        """Return a minimal ProductCreate-like namespace object."""
        import types

        defaults = {
            "sku": "SKU-DC-1",
            "category": "FRAME",
            "brand": "Acme",
            "model": "M1",
            "variant": None,
            "color": None,
            "size": None,
            "mrp": 1000.0,
            "offer_price": 900.0,
            "hsn_code": None,
            "gst_rate": None,
            "attributes": None,
            "discount_category": "MASS",
            # CL / spectacle-lens fields
            "cl_series": None,
            "modality": None,
            "base_curve": None,
            "diameter": None,
            "cl_power": None,
            "cl_cyl": None,
            "cl_axis": None,
            "cl_add": None,
            "pack_size": None,
            "sph": None,
            "cyl": None,
            "axis": None,
            "add": None,
        }
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def test_discount_category_present_in_built_data(self):
        """discount_category must appear in the data dict returned by
        _build_product_data — the field was silently dropped before INV-1."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category="LUXURY")
        data = _build_product_data(product, created_by="test-user")

        assert "discount_category" in data, (
            "discount_category must be persisted on product create (INV-1)"
        )
        assert data["discount_category"] == "LUXURY"

    def test_default_discount_category_is_mass(self):
        """When not explicitly set, discount_category defaults to MASS."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category="MASS")
        data = _build_product_data(product, created_by="test-user")

        assert data["discount_category"] == "MASS"

    def test_premium_discount_category_round_trips(self):
        """PREMIUM tier must round-trip correctly."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category="PREMIUM")
        data = _build_product_data(product, created_by="test-user")

        assert data["discount_category"] == "PREMIUM"

    def test_discount_category_persisted_when_provided(self):
        """INV-1/BVI-10: a provided discount_category is PERSISTED (the bug was
        it got silently dropped on create), so the cap resolver reads the tier."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category="LUXURY")
        data = _build_product_data(product, created_by="test-user")

        assert data["discount_category"] == "LUXURY"

    def test_discount_category_omitted_when_none(self):
        """None means 'unset' -> omitted from the doc (additive); the cap
        resolver then falls back to the product's `category`."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category=None)
        data = _build_product_data(product, created_by="test-user")

        assert "discount_category" not in data

    def test_validator_uppercases_valid_and_rejects_invalid(self):
        """The ProductCreate field validator normalises a valid tier to upper
        case and REJECTS an unknown tier (fail loudly, never silently mis-cap)."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        import pytest
        from pydantic import ValidationError
        from api.routers.products import ProductCreate

        p = ProductCreate(
            sku="SKU-DC-1", category="FRAME", brand="Acme", model="M1",
            mrp=1000.0, offer_price=900.0, discount_category="luxury",
        )
        assert p.discount_category == "LUXURY"
        with pytest.raises(ValidationError):
            ProductCreate(
                sku="SKU-DC-2", category="FRAME", brand="Acme", model="M1",
                mrp=1000.0, offer_price=900.0, discount_category="BANANA",
            )

    def test_service_tier_round_trips(self):
        """SERVICE tier (eye-test lines) must survive."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category="SERVICE")
        data = _build_product_data(product, created_by="test-user")

        assert data["discount_category"] == "SERVICE"

    def test_non_discountable_tier_round_trips(self):
        """NON_DISCOUNTABLE tier must survive."""
        import sys, os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
        from api.routers.products import _build_product_data

        product = self._make_product(discount_category="NON_DISCOUNTABLE")
        data = _build_product_data(product, created_by="test-user")

        assert data["discount_category"] == "NON_DISCOUNTABLE"


# ===========================================================================
# INV-6 — category-scoped stock count must NOT filter on stock_units.category
# ===========================================================================


class TestStockCountCategoryFilter:
    """The start_stock_count endpoint must resolve product_ids from the
    products collection (which has a `category` field) and scope the
    stock_units aggregation to those ids.

    We test the pure logic: the aggregation pipeline must include a
    product_id filter when a category is given, not a raw `category` filter
    (which would always miss because stock_units lack that field).
    """

    def test_no_category_filter_uses_plain_match(self):
        """When no category is requested, the pipeline must NOT add a
        product_id or category constraint (it returns all stock at the
        store)."""
        # This is the baseline — the pre-fix AND post-fix behavior.
        # We just verify the fix didn't break the no-category path.
        # (Full pipeline construction is inside the endpoint; here we
        #  just verify the helper _on_hand_by_product accepts an empty set.)
        from api.routers.inventory import _on_hand_by_product

        result = _on_hand_by_product(db=None, product_ids=[], store_id="STORE1")
        assert result == {}, "Empty input must return empty dict (fail-soft)"

    def test_stock_count_category_resolves_product_ids(self):
        """The fix resolves category -> product_ids before filtering stock.
        Verify the logic path: when category_product_ids is empty (no products
        in category), system_quantities must stay empty rather than returning
        all-store stock (the old broken behavior was to add `category` to the
        match against stock_units, which matched nothing but didn't error --
        and could be confused for a successful empty-category result)."""
        # We can't run the full endpoint without a live DB, but we can verify
        # the _on_hand_by_product helper respects an empty product_ids list.
        from api.routers.inventory import _on_hand_by_product

        # An empty product_ids list is the signal: category has no products.
        result = _on_hand_by_product(db=None, product_ids=[], store_id="STORE1")
        assert result == {}

    def test_on_hand_helper_returns_empty_without_db(self):
        """_on_hand_by_product must return {} (not raise) when db is None."""
        from api.routers.inventory import _on_hand_by_product

        result = _on_hand_by_product(db=None, product_ids=["p1", "p2"])
        assert result == {}

    def test_stock_units_category_field_absent_from_pipeline(self):
        """Regression: the OLD code added `category: req.category` directly
        to the stock_units $match, which never matched anything.  Verify
        that no such key appears in the aggregation pipeline the fix builds
        when a category IS provided.

        We mock the stock_repo.aggregate call and inspect the pipeline
        passed to it. The `category` key must NOT appear in $match; a
        `product_id` key (from the resolved-product-ids list) MUST appear
        when products exist for the category.
        """
        import types

        # Build a minimal mock product_repo that returns two products.
        fake_products = [
            {"product_id": "p1", "_id": "p1"},
            {"product_id": "p2", "_id": "p2"},
        ]

        captured_pipelines: list = []

        class FakeStockRepo:
            def aggregate(self, pipeline):
                captured_pipelines.append(pipeline)
                return []

        class FakeProductRepo:
            def find_many(self, filt, limit=5000):
                # Only return products when category filter matches.
                if filt.get("category") == "FRAME":
                    return fake_products
                return []

        # Simulate the fix's category->product_ids resolution + pipeline build.
        # We replicate the logic from the fixed start_stock_count to check it.
        category = "FRAME"
        product_repo = FakeProductRepo()
        try:
            cat_products = product_repo.find_many(
                {"category": category, "is_active": True}, limit=5000
            )
            category_product_ids = [
                str(p.get("product_id") or p.get("_id") or "")
                for p in (cat_products or [])
                if p.get("product_id") or p.get("_id")
            ]
        except Exception:
            category_product_ids = []

        # Build the match clause as the fix does.
        match_clause: dict = {
            "store_id": "STORE1",
            "status": {"$in": ["AVAILABLE", "RESERVED"]},
        }
        if category_product_ids:
            match_clause["product_id"] = {"$in": category_product_ids}

        pipeline = [
            {"$match": match_clause},
            {"$group": {"_id": "$product_id", "qty": {"$sum": 1}}},
        ]

        # Assertions
        assert "category" not in pipeline[0]["$match"], (
            "The $match clause must NOT contain a raw 'category' filter "
            "(stock_units don't have that field) -- INV-6 regression"
        )
        assert "product_id" in pipeline[0]["$match"], (
            "The $match clause MUST contain a product_id filter when "
            "category resolves to known products -- INV-6"
        )
        assert set(pipeline[0]["$match"]["product_id"]["$in"]) == {"p1", "p2"}


# ===========================================================================
# INV-6 — END-TO-END: drive the REAL start_stock_count endpoint.
#
# TestStockCountCategoryFilter above replicates the resolution logic in the
# test body, so it would still pass if someone reverted the real handler.
# These tests mount the inventory router and POST to the actual endpoint with
# a stock_repo that captures the aggregation pipeline -- a regression in
# start_stock_count itself is what fails them.
# ===========================================================================


class TestStartStockCountEndpointCategory:
    def _client(self, monkeypatch, product_repo, stock_repo):
        import os
        import sys

        os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
        os.environ.setdefault("MONGODB_URI", "")
        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers import inventory as inv
        from api.routers.auth import get_current_user

        app = FastAPI()
        app.include_router(inv.router, prefix="/api/v1/inventory")

        async def _fake_user():
            return {
                "user_id": "u1",
                "full_name": "Tester",
                "active_store_id": "BV-TEST-01",
                "roles": ["STORE_MANAGER"],
            }

        app.dependency_overrides[get_current_user] = _fake_user
        monkeypatch.setattr(inv, "get_product_repository", lambda: product_repo)
        monkeypatch.setattr(inv, "get_stock_repository", lambda: stock_repo)
        # No raw DB persistence needed -- the endpoint returns the count_doc.
        monkeypatch.setattr(inv, "_get_db", lambda: None)
        # validate_store_access(None, user) -> caller's active store.
        monkeypatch.setattr(
            inv, "validate_store_access", lambda store, user: "BV-TEST-01"
        )
        return TestClient(app)

    def test_endpoint_scopes_aggregation_to_resolved_product_ids(self, monkeypatch):
        """POST /stock-count/start?category=FRAME must build an aggregation
        whose $match filters on the RESOLVED product_ids (never a raw
        `category` field, which stock_units do not carry) -- INV-6 end-to-end."""
        captured: list = []

        class FakeStockRepo:
            def aggregate(self, pipeline):
                captured.append(pipeline)
                return [{"_id": "p1", "qty": 4}, {"_id": "p2", "qty": 1}]

        class FakeProductRepo:
            def find_many(self, filt, limit=5000):
                if filt.get("category") == "FRAME":
                    return [{"product_id": "p1"}, {"product_id": "p2"}]
                return []

        client = self._client(monkeypatch, FakeProductRepo(), FakeStockRepo())
        resp = client.post(
            "/api/v1/inventory/stock-count/start", json={"category": "FRAME"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The handler counted only the FRAME products.
        assert body["system_quantities"] == {"p1": 4, "p2": 1}
        # And it did so by scoping on product_id, NOT a raw category filter.
        assert captured, "the real handler must run the aggregation"
        match = captured[0][0]["$match"]
        assert "category" not in match
        assert set(match["product_id"]["$in"]) == {"p1", "p2"}

    def test_endpoint_empty_category_yields_no_system_quantities(self, monkeypatch):
        """A category with no products must NOT fall back to counting all store
        stock -- system_quantities stays empty and the aggregation is skipped."""
        aggregate_calls: list = []

        class FakeStockRepo:
            def aggregate(self, pipeline):
                aggregate_calls.append(pipeline)
                return [{"_id": "SHOULD-NOT-APPEAR", "qty": 99}]

        class FakeProductRepo:
            def find_many(self, filt, limit=5000):
                return []  # no products in this category

        client = self._client(monkeypatch, FakeProductRepo(), FakeStockRepo())
        resp = client.post(
            "/api/v1/inventory/stock-count/start", json={"category": "NOPE"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["system_quantities"] == {}
        assert aggregate_calls == []  # aggregation skipped, not run store-wide

    def test_endpoint_no_category_counts_whole_store(self, monkeypatch):
        """Without a category, the aggregation runs with NO product_id / category
        constraint -- the whole-store baseline path is preserved."""
        captured: list = []

        class FakeStockRepo:
            def aggregate(self, pipeline):
                captured.append(pipeline)
                return [{"_id": "p9", "qty": 7}]

        class FakeProductRepo:
            def find_many(self, filt, limit=5000):  # pragma: no cover - unused
                raise AssertionError("product lookup must not run without category")

        client = self._client(monkeypatch, FakeProductRepo(), FakeStockRepo())
        resp = client.post("/api/v1/inventory/stock-count/start", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["system_quantities"] == {"p9": 7}
        match = captured[0][0]["$match"]
        assert "product_id" not in match
        assert "category" not in match
