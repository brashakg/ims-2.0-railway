"""
IMS 2.0 — catalog product persistence
=====================================
/catalog/products used an in-memory CATALOG_PRODUCTS dict (lost on restart;
dead — the frontend uses /admin/catalog/* + /products/*). Now backed by the
`catalog_products` collection via _save/_get/_all helpers, fail-soft to the
in-memory dict. These tests cover the collection round-trip + the fallback.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import catalog  # noqa: E402


class _FakeColl:
    """Minimal collection double that models BOTH real pymongo and the
    in-memory MockDatabase contract used by _save_catalog_product:
      - update_one(filter, {"$set": ...}) updates ONLY an existing doc
        (no upsert) -- matches pymongo's 2-arg form and MockCollection.
      - insert_one(doc) inserts a new doc.
    _save_catalog_product checks existence first, then update_one OR insert_one,
    so it never relies on the upsert kwarg (which MockCollection lacks)."""

    def __init__(self):
        self.docs = {}

    def update_one(self, flt, update):
        _id = flt["id"]
        if _id in self.docs:
            self.docs[_id].update(update["$set"])

    def insert_one(self, doc):
        self.docs[doc["id"]] = dict(doc)

    def find_one(self, flt):
        d = self.docs.get(flt["id"])
        return dict(d) if d else None

    def find(self, flt=None, projection=None):
        return [dict(d) for d in self.docs.values()]


class TestCatalogCollectionPersistence:
    def test_save_get_round_trip(self, monkeypatch):
        fake = _FakeColl()
        monkeypatch.setattr(catalog, "_catalog_coll", lambda: fake)
        catalog._save_catalog_product({"id": "prod_1", "title": "Ray-Ban X", "is_active": True})
        assert "prod_1" in fake.docs
        got = catalog._get_catalog_product("prod_1")
        assert got["title"] == "Ray-Ban X"

    def test_all_reads_collection(self, monkeypatch):
        fake = _FakeColl()
        monkeypatch.setattr(catalog, "_catalog_coll", lambda: fake)
        catalog._save_catalog_product({"id": "a", "title": "A"})
        catalog._save_catalog_product({"id": "b", "title": "B"})
        assert {p["id"] for p in catalog._all_catalog_products()} == {"a", "b"}


class TestInMemoryFallback:
    def test_fallback_when_no_db(self, monkeypatch):
        monkeypatch.setattr(catalog, "_catalog_coll", lambda: None)
        catalog.CATALOG_PRODUCTS.clear()
        catalog._save_catalog_product({"id": "mem", "title": "M"})
        assert catalog._get_catalog_product("mem")["title"] == "M"
        assert any(p["id"] == "mem" for p in catalog._all_catalog_products())

    def test_get_missing_none(self, monkeypatch):
        monkeypatch.setattr(catalog, "_catalog_coll", lambda: None)
        catalog.CATALOG_PRODUCTS.clear()
        assert catalog._get_catalog_product("nope") is None
