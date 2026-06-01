"""
Tests for the catalog_variants repository (BVI Phase 1 foundation).

CatalogVariantRepository is the variant IDENTITY + Shopify-mapping tier. The
headline contract is the idempotent `upsert` keyed on `sku` (never Mongo `_id`)
and the `get_by_sku` / `list_by_parent` reads. These run against the in-memory
MockCollection so they need no live Mongo (the repo uses the 2-arg
insert_one/update_one signatures that work on both backends).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_catalog_variants.py -q
"""

import os
import sys

# Backend package root importable + JWT secret present (auth imports at app import).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.catalog_variant_repository import (  # noqa: E402
    CatalogVariantRepository,
)


@pytest.fixture
def repo():
    return CatalogVariantRepository(MockCollection("catalog_variants"))


def test_upsert_then_get_by_sku_roundtrip(repo):
    """A variant inserted via upsert is read back identically by get_by_sku."""
    stored = repo.upsert(
        {
            "sku": "RB-AVTR-BLK-52",
            "parent_product_id": "prod-rayban-aviator",
            "parent_sku": "RB-AVTR",
            "option_color": "Black",
            "option_size": "52",
            "shopify_variant_id": "gid://shopify/ProductVariant/111",
            "store_barcode": "BV-PHYS-0001",
            "gtin": "8056597000001",
        }
    )
    assert stored is not None
    assert stored["sku"] == "RB-AVTR-BLK-52"
    # variant_id auto-assigned; created/updated stamped.
    assert stored.get("variant_id")
    assert "created_at" in stored and "updated_at" in stored

    fetched = repo.get_by_sku("RB-AVTR-BLK-52")
    assert fetched is not None
    assert fetched["parent_product_id"] == "prod-rayban-aviator"
    assert fetched["option_color"] == "Black"
    assert fetched["shopify_variant_id"] == "gid://shopify/ProductVariant/111"
    assert fetched["store_barcode"] == "BV-PHYS-0001"
    # The bridge invariant: NO stored quantity on the variant row.
    assert "quantity" not in fetched


def test_upsert_is_idempotent_and_updates_in_place(repo):
    """Calling upsert twice for the same sku updates the row, not duplicates it,
    and keyed on sku (not _id)."""
    repo.upsert({"sku": "CL-ACU-130", "option_color": "Clear"})
    first = repo.get_by_sku("CL-ACU-130")
    assert first is not None
    first_id = first["variant_id"]

    # Second upsert: change a field + add the Shopify mapping.
    repo.upsert(
        {
            "sku": "CL-ACU-130",
            "option_color": "Clear",
            "shopify_variant_id": "gid://shopify/ProductVariant/222",
        }
    )
    assert repo.count({"sku": "CL-ACU-130"}) == 1  # no duplicate row
    updated = repo.get_by_sku("CL-ACU-130")
    assert updated["variant_id"] == first_id  # identity preserved
    assert updated["shopify_variant_id"] == "gid://shopify/ProductVariant/222"


def test_upsert_without_sku_is_rejected(repo):
    """sku is the identity; a row without it must not be minted (it would later
    collide on the unique-sparse sku index)."""
    assert repo.upsert({"option_color": "Red"}) is None
    assert repo.upsert({}) is None
    assert repo.count() == 0


def test_get_by_sku_missing_returns_none(repo):
    assert repo.get_by_sku("DOES-NOT-EXIST") is None
    assert repo.get_by_sku("") is None
    assert repo.get_by_sku(None) is None


def test_list_by_parent_groups_variants(repo):
    """All variants of one parent come back together, ordered by sku; a foreign
    parent's variant is excluded."""
    repo.upsert({"sku": "RB-AVTR-BLK-52", "parent_product_id": "p-aviator"})
    repo.upsert({"sku": "RB-AVTR-GLD-58", "parent_product_id": "p-aviator"})
    repo.upsert({"sku": "RB-WAY-BLK-50", "parent_product_id": "p-wayfarer"})

    aviators = repo.list_by_parent("p-aviator")
    skus = sorted(v["sku"] for v in aviators)
    assert skus == ["RB-AVTR-BLK-52", "RB-AVTR-GLD-58"]

    assert repo.list_by_parent("p-wayfarer")[0]["sku"] == "RB-WAY-BLK-50"
    assert repo.list_by_parent("no-such-parent") == []
    assert repo.list_by_parent("") == []
