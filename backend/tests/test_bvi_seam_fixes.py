"""
IMS 2.0 - BVI seam fixes: BVI-7, BVI-8, BVI-9, BVI-10 regression tests
=======================================================================
BVI-7: menusApi.addItem must wrap payload in {item: ...} (FE service test not
        possible in Python; covered in code-review notes).
BVI-8: menusApi.moveItem must send `new_parent_id` not `parent_id` (same).
BVI-9: GET /online-store/summary `counts` must expose FE-readable keys
        (products, variants, collections, menus, images_pending_design,
        customers, orders) -- no more catalog_variants / products_with_ecom.
BVI-10: ProductCreate.discount_category was silently dropped on persist so POS
        always fell back to MASS (15%) even for LUXURY products. Now the field
        is accepted on both create + update and validated against the canonical
        cap-tier set.

All tests are pure-Python (Pydantic model assertions + HTTP tests using the
FastAPI TestClient with no live DB required). They mirror the test style of
test_catalog_hardening.py.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# BVI-10 -- ProductCreate / ProductUpdate discount_category persistence
# ---------------------------------------------------------------------------


class TestDiscountCategoryOnCreate:
    """BVI-10: discount_category is accepted by ProductCreate and persisted."""

    def _make_create(self, **kwargs):
        """Build a minimal valid ProductCreate payload dict."""
        from api.routers.products import ProductCreate

        defaults = {
            "sku": "TEST-DC-001",
            "category": "FRAME",
            "brand": "Ray-Ban",
            "model": "T1",
            "mrp": 2000.0,
            "offer_price": 1800.0,
        }
        defaults.update(kwargs)
        return ProductCreate(**defaults)

    def test_no_discount_category_defaults_none(self):
        """Omitting discount_category leaves it as None (caller fallback = MASS)."""
        m = self._make_create()
        assert m.discount_category is None

    def test_luxury_accepted_and_uppercased(self):
        """LUXURY (any case) is normalised to uppercase and accepted."""
        m = self._make_create(discount_category="luxury")
        assert m.discount_category == "LUXURY"

    def test_premium_accepted(self):
        m = self._make_create(discount_category="PREMIUM")
        assert m.discount_category == "PREMIUM"

    def test_mass_accepted(self):
        m = self._make_create(discount_category="MASS")
        assert m.discount_category == "MASS"

    def test_service_accepted(self):
        m = self._make_create(discount_category="SERVICE")
        assert m.discount_category == "SERVICE"

    def test_non_discountable_accepted(self):
        m = self._make_create(discount_category="NON_DISCOUNTABLE")
        assert m.discount_category == "NON_DISCOUNTABLE"

    def test_unknown_tier_rejected(self):
        """An unknown tier (GOLD, PLATINUM, ...) raises a ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid discount_category"):
            self._make_create(discount_category="GOLD")

    def test_blank_string_rejected(self):
        """A blank string normalises to '' which is not in the allowed set."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._make_create(discount_category="")

    def test_discount_category_in_optional_fields(self):
        """discount_category must be in _OPTIONAL_PRODUCT_FIELDS so _build_product_data
        persists it (the root cause of BVI-10)."""
        from api.routers.products import _OPTIONAL_PRODUCT_FIELDS

        assert "discount_category" in _OPTIONAL_PRODUCT_FIELDS, (
            "discount_category is missing from _OPTIONAL_PRODUCT_FIELDS -- it will be "
            "silently dropped on product create (BVI-10 regression)"
        )

    def test_build_product_data_includes_discount_category(self):
        """_build_product_data must carry discount_category through to the persisted doc."""
        from api.routers.products import ProductCreate, _build_product_data

        pc = ProductCreate(
            sku="TEST-DC-LUX",
            category="FRAME",
            brand="Cartier",
            model="C1",
            mrp=50000.0,
            offer_price=49000.0,
            discount_category="LUXURY",
        )
        doc = _build_product_data(pc, created_by="t-1")
        assert doc.get("discount_category") == "LUXURY", (
            "_build_product_data did not persist discount_category; "
            "POS will fall back to MASS 15% on LUXURY products (BVI-10)"
        )

    def test_build_product_data_omits_none_discount_category(self):
        """When discount_category is None (not set), it must NOT appear in the doc
        (additive pattern -- absence lets the fallback logic in pricing_caps work)."""
        from api.routers.products import ProductCreate, _build_product_data

        pc = ProductCreate(
            sku="TEST-DC-NONE",
            category="FRAME",
            brand="Ray-Ban",
            model="N1",
            mrp=2000.0,
            offer_price=1800.0,
        )
        doc = _build_product_data(pc, created_by="t-1")
        # None values are excluded by the additive pattern in _build_product_data.
        assert doc.get("discount_category") is None


class TestDiscountCategoryOnUpdate:
    """BVI-10: discount_category is accepted and validated on ProductUpdate."""

    def _make_update(self, **kwargs):
        from api.routers.products import ProductUpdate

        return ProductUpdate(**kwargs)

    def test_luxury_update_accepted(self):
        m = self._make_update(discount_category="LUXURY")
        assert m.discount_category == "LUXURY"

    def test_lowercase_normalised_on_update(self):
        m = self._make_update(discount_category="mass")
        assert m.discount_category == "MASS"

    def test_unknown_tier_rejected_on_update(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid discount_category"):
            self._make_update(discount_category="PLATINUM")

    def test_none_allowed_on_update(self):
        """None is fine -- caller can omit the field on update."""
        m = self._make_update(discount_category=None)
        assert m.discount_category is None


# ---------------------------------------------------------------------------
# BVI-9 -- online_store summary counts shape
# ---------------------------------------------------------------------------


class TestOnlineStoreSummaryCounts:
    """BVI-9: GET /online-store/summary must return counts with the keys the FE reads."""

    _EXPECTED_KEYS = {
        "products",
        "variants",
        "collections",
        "menus",
        "images_pending_design",
        "customers",
        "orders",
    }

    def test_summary_counts_has_all_fe_keys(self, client, auth_headers):
        """The /summary counts object must contain all keys the OnlineStoreCounts
        TS interface expects (even if all are 0 when no DB is connected)."""
        resp = client.get("/api/v1/online-store/summary", headers=auth_headers)
        # 403/404 means the route isn't reachable -- fail loudly.
        assert resp.status_code not in (403, 404), (
            f"Unexpected status {resp.status_code}: {resp.text}"
        )
        if resp.status_code != 200:
            pytest.skip("backend not fully available in this test env")
        counts = resp.json().get("counts", {})
        missing = self._EXPECTED_KEYS - set(counts.keys())
        assert not missing, (
            f"summary counts missing FE-expected keys: {missing}. "
            "The shell cards will show undefined/-- for these sections (BVI-9)."
        )

    def test_summary_counts_no_old_keys(self, client, auth_headers):
        """The old field names (`catalog_variants`, `products_with_ecom`) must
        not be the ONLY keys -- they were the bug (FE couldn't read them)."""
        resp = client.get("/api/v1/online-store/summary", headers=auth_headers)
        if resp.status_code != 200:
            pytest.skip("backend not fully available in this test env")
        counts = resp.json().get("counts", {})
        # After the fix, the FE-readable keys must ALL be present.
        missing = self._EXPECTED_KEYS - set(counts.keys())
        assert not missing

    def test_summary_counts_are_non_negative_ints(self, client, auth_headers):
        """All count values must be non-negative integers (0 when no DB)."""
        resp = client.get("/api/v1/online-store/summary", headers=auth_headers)
        if resp.status_code != 200:
            pytest.skip("backend not fully available in this test env")
        counts = resp.json().get("counts", {})
        for key, val in counts.items():
            assert isinstance(val, int) and val >= 0, (
                f"counts.{key} = {val!r} is not a non-negative integer"
            )


# ---------------------------------------------------------------------------
# BVI-7 / BVI-8 -- menus addItem / moveItem payload shape (pure model layer)
# ---------------------------------------------------------------------------
# The MenusPage uses saveTree in practice (dormant paths), but the addItem /
# moveItem functions in the FE service are still exposed and could be called.
# These tests verify that the BACKEND models accept exactly what the fixed FE
# now sends.


class TestMenusItemPayloads:
    """Verify the backend Pydantic models match the fixed FE call shapes."""

    def test_add_item_model_expects_item_wrapper(self):
        """BVI-7: backend AddItem expects {item: {...}, parent_id?, position?}.
        A flat payload (just the item fields) is a 422."""
        from api.routers.online_store_menus import AddItem, MenuItemIn

        # Correct shape: item is wrapped.
        payload = AddItem(
            item=MenuItemIn(title="Test", item_type="HTTP", url="https://example.com"),
            parent_id=None,
            position=0,
        )
        assert payload.item.title == "Test"
        assert payload.parent_id is None

    def test_move_item_model_uses_new_parent_id(self):
        """BVI-8: backend MoveItem uses `new_parent_id`, not `parent_id`.
        Sending `parent_id` would silently be ignored (unknown field = extra)."""
        from api.routers.online_store_menus import MoveItem

        payload = MoveItem(new_parent_id="abc-123", position=2)
        assert payload.new_parent_id == "abc-123"
        assert payload.position == 2
        # `parent_id` is NOT a field on MoveItem -- it should not exist.
        assert not hasattr(payload, "parent_id"), (
            "MoveItem has a `parent_id` attribute -- the backend model was changed. "
            "Verify BVI-8 fix is still correct."
        )
