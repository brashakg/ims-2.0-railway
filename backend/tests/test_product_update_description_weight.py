"""ProductUpdate gained description + weight (Catalog Manager edit-in-place):
they round-trip via PUT /products/{id}, and description mirrors onto the
catalog_products twin inside the existing fail-soft mirror block.

Direct-router-call style (test_product_image_alias.py pattern); the repo is
the REAL ProductRepository over the in-repo MockCollection.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import products as products_mod  # noqa: E402
from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402


def _user():
    return {
        "user_id": "u1",
        "username": "t",
        "roles": ["ADMIN"],
        "active_store_id": "S1",
    }


def _seeded_repo():
    coll = MockCollection("products")
    coll.insert_one(
        {
            "_id": "P1",
            "product_id": "P1",
            "id": "P1",
            "sku": "FR-RB-0001",
            "brand": "Ray-Ban",
            "model": "RB1001",
            "category": "FRAME",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "hsn_code": "900311",
            "gst_rate": 5.0,
            "is_active": True,
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1001"},
            "catalog_status": "ACTIVE",
        }
    )
    return ProductRepository(coll)


class _ConnStub:
    """Minimal connection stub for the catalog-twin mirror block."""

    is_connected = True

    def __init__(self):
        self._catalog = MockCollection("catalog_products")
        self._catalog.insert_one(
            {"_id": "P1", "id": "P1", "sku": "FR-RB-0001", "description": "old copy"}
        )

    def get_collection(self, name):
        return self._catalog if name == "catalog_products" else MockCollection(name)


def test_description_and_weight_round_trip_and_twin_mirror(monkeypatch):
    repo = _seeded_repo()
    conn = _ConnStub()
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)

    import api.dependencies as deps_mod

    monkeypatch.setattr(deps_mod, "get_db", lambda: conn)

    payload = products_mod.ProductUpdate(
        description="Acetate square frame, spring hinges.",
        weight=42.5,
    )
    out = asyncio.run(products_mod.update_product("P1", payload, _user()))
    assert out["product_id"] == "P1"

    # Round-trip on the spine.
    doc = repo.find_by_id("P1")
    assert doc["description"] == "Acetate square frame, spring hinges."
    assert doc["weight"] == 42.5
    # Existing fields untouched.
    assert doc["mrp"] == 5000.0 and doc["hsn_code"] == "900311"

    # Description mirrored onto the catalog twin (shared id).
    twin = conn.get_collection("catalog_products").find_one({"id": "P1"})
    assert twin["description"] == "Acetate square frame, spring hinges."


def test_weight_rejects_negative(monkeypatch):
    import pytest

    with pytest.raises(Exception):
        products_mod.ProductUpdate(weight=-5)
