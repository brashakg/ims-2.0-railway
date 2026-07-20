"""
Tests for the storefront-keyed Shopify reference accessor (api.services.shopify_ref).

The invariant under test:
  * BV (the default storefront) reads/writes the EXISTING FLAT field, in place,
    and NEVER creates a `storefronts` sub-map -> byte-identical to today.
  * Any other storefront reads/writes a namespaced `storefronts.<sid>.<field>`
    block on the same container, isolated from BV's flat field.

Pure functions, no DB.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_ref.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services.shopify_ref import get_shopify_ref, set_shopify_ref  # noqa: E402


# ---------------------------------------------------------------------------
# BV -> flat fields (byte-identical)
# ---------------------------------------------------------------------------


def test_bv_get_reads_flat_field():
    ecom = {"shopify_product_id": "gid://shopify/Product/1"}
    assert (
        get_shopify_ref(ecom, "BV", "shopify_product_id")
        == "gid://shopify/Product/1"
    )


def test_bv_set_writes_flat_field_and_creates_no_submap():
    ecom = {"status": "PUBLISHED"}
    out = set_shopify_ref(ecom, "BV", "shopify_product_id", "gid://shopify/Product/9")
    assert out is ecom  # mutated in place
    assert ecom["shopify_product_id"] == "gid://shopify/Product/9"
    # The byte-identical-BV guarantee: NO storefronts sub-map is created for BV.
    assert "storefronts" not in ecom
    # Sibling fields untouched.
    assert ecom["status"] == "PUBLISHED"


def test_bv_set_on_variant_doc_is_flat():
    variant = {"sku": "SKU1"}
    set_shopify_ref(variant, "BV", "shopify_variant_id", "gid://shopify/ProductVariant/5")
    assert variant["shopify_variant_id"] == "gid://shopify/ProductVariant/5"
    assert "storefronts" not in variant


def test_blank_storefront_id_falls_back_to_flat():
    """An empty / None storefront_id resolves to the default (flat) path so an
    unkeyed caller keeps today's behaviour."""
    ecom = {}
    set_shopify_ref(ecom, "", "shopify_product_id", "X")
    assert ecom["shopify_product_id"] == "X"
    assert "storefronts" not in ecom


# ---------------------------------------------------------------------------
# Other storefront -> nested storefronts.<sid>.<field>
# ---------------------------------------------------------------------------


def test_other_storefront_set_is_nested():
    ecom = {"shopify_product_id": "gid://shopify/Product/BV"}
    set_shopify_ref(ecom, "WZ", "shopify_product_id", "gid://shopify/Product/WZ")
    assert ecom["storefronts"]["WZ"]["shopify_product_id"] == "gid://shopify/Product/WZ"
    # BV's flat field is untouched by a WZ write.
    assert ecom["shopify_product_id"] == "gid://shopify/Product/BV"


def test_other_storefront_get_reads_nested():
    ecom = {"storefronts": {"WZ": {"shopify_product_id": "gid://shopify/Product/WZ"}}}
    assert (
        get_shopify_ref(ecom, "WZ", "shopify_product_id")
        == "gid://shopify/Product/WZ"
    )


def test_bv_and_other_are_isolated():
    """A doc with only a WZ nested ref returns None for BV, and vice-versa."""
    ecom = {"storefronts": {"WZ": {"shopify_product_id": "gid://wz"}}}
    assert get_shopify_ref(ecom, "BV", "shopify_product_id") is None
    flat = {"shopify_product_id": "gid://bv"}
    assert get_shopify_ref(flat, "WZ", "shopify_product_id") is None


def test_get_is_failsoft_on_bad_input():
    assert get_shopify_ref(None, "BV", "shopify_product_id") is None
    assert get_shopify_ref({}, "WZ", "shopify_product_id") is None
    assert get_shopify_ref({"storefronts": "notadict"}, "WZ", "x") is None
