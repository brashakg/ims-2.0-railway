"""Catalog Manager backend pins: GET /products pagination + is_active tri-state
+ the repo search barcode field (PR: catalog manager).

Style mirrors test_product_image_alias.py: the router function is called
DIRECTLY with a monkeypatched repository, so the pins hold with or without a
live Mongo. The repository itself is the REAL ProductRepository running over
the in-repo MockCollection (database/connection.py), so skip/limit/count and
the tokenized search query execute the real code paths.

Pinned contracts (ruling: legacy behaviour byte-preserved):
  * `total` stays the PAGE length; `total_count` is the additive pre-slice
    count of the applied filter.
  * skip/limit are honored on the category, brand and search paths (they
    previously fell into find_many's silent 100-cap).
  * is_active ABSENT reproduces the legacy per-path behaviour exactly:
    default path returns inactive products too; filtered paths active-only.
  * is_active=all surfaces inactive products on every path.
  * search_products now tokenizes over barcode as well (scanner passthrough)
    -- ADDITIVE: brand/model/sku searches return supersets, never fewer.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

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


_DOCS = [
    {
        "product_id": "P1",
        "sku": "FR-RB-0001",
        "brand": "Ray-Ban",
        "model": "RB1001",
        "category": "FRAME",
        "barcode": "2000000000017",
        "is_active": True,
    },
    {
        "product_id": "P2",
        "sku": "FR-RB-0002",
        "brand": "Ray-Ban",
        "model": "RB1002",
        "category": "FRAME",
        "is_active": True,
    },
    {
        "product_id": "P3",
        "sku": "FR-RB-0003",
        "brand": "Ray-Ban",
        "model": "RB1003",
        "category": "FRAME",
        "is_active": False,  # inactive frame
    },
    {
        "product_id": "P4",
        "sku": "SG-OK-0001",
        "brand": "Oakley",
        "model": "OK2001",
        "category": "SUNGLASS",
        "is_active": True,
    },
]


def _repo() -> ProductRepository:
    coll = MockCollection("products")
    for d in _DOCS:
        doc = dict(d)
        doc["_id"] = doc["product_id"]
        coll.insert_one(doc)
    return ProductRepository(coll)


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    class _NoCache:
        TTL_MEDIUM = 0

        def get(self, k):
            return None

        def set(self, k, v, ttl=0):
            pass

    import api.services.cache as cache_mod

    monkeypatch.setattr(cache_mod, "cache", _NoCache())
    yield


def _list(**kwargs):
    """Call the router function directly with defaults for every param."""
    params = {
        "category": None,
        "brand": None,
        "search": None,
        "tag": None,
        "store_id": None,
        "skip": 0,
        "limit": 50,
        "is_active": None,
        "current_user": _user(),
    }
    params.update(kwargs)
    return asyncio.run(products_mod.list_products(**params))


# ---------------------------------------------------------------------------
# Legacy behaviour byte-preserved (is_active absent)
# ---------------------------------------------------------------------------


def test_default_path_absent_is_active_returns_inactive_too(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list()
    ids = {p["product_id"] for p in out["products"]}
    assert ids == {"P1", "P2", "P3", "P4"}  # inactive P3 included (legacy)
    assert out["total"] == 4  # legacy total == page length
    assert out["total_count"] == 4


def test_category_path_absent_is_active_is_active_only(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(category="FRAME")
    ids = {p["product_id"] for p in out["products"]}
    assert ids == {"P1", "P2"}  # legacy: filtered paths force active-only
    assert out["total"] == 2
    assert out["total_count"] == 2


def test_brand_path_absent_is_active_is_active_only(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(brand="Ray-Ban")
    ids = {p["product_id"] for p in out["products"]}
    assert ids == {"P1", "P2"}
    assert out["total_count"] == 2


def test_search_path_absent_is_active_is_active_only(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(search="RB100")
    ids = {p["product_id"] for p in out["products"]}
    assert ids == {"P1", "P2"}  # P3 matches the token but is inactive
    assert out["total_count"] == 2


# ---------------------------------------------------------------------------
# is_active tri-state
# ---------------------------------------------------------------------------


def test_is_active_all_surfaces_inactive_on_filtered_paths(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(category="FRAME", is_active="all")
    ids = {p["product_id"] for p in out["products"]}
    assert ids == {"P1", "P2", "P3"}
    assert out["total_count"] == 3


def test_is_active_false_returns_inactive_only(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(category="FRAME", is_active="false")
    assert {p["product_id"] for p in out["products"]} == {"P3"}


def test_is_active_true_on_default_path_filters(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(is_active="true")
    assert {p["product_id"] for p in out["products"]} == {"P1", "P2", "P4"}
    assert out["total_count"] == 3


# ---------------------------------------------------------------------------
# skip/limit honored + total_count correct on the filtered paths
# ---------------------------------------------------------------------------


def test_category_path_skip_limit_and_total_count(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    page1 = _list(category="FRAME", is_active="all", skip=0, limit=2)
    page2 = _list(category="FRAME", is_active="all", skip=2, limit=2)
    assert len(page1["products"]) == 2
    assert len(page2["products"]) == 1
    # No overlap between pages; union is the whole filtered set.
    ids1 = {p["product_id"] for p in page1["products"]}
    ids2 = {p["product_id"] for p in page2["products"]}
    assert ids1.isdisjoint(ids2)
    assert ids1 | ids2 == {"P1", "P2", "P3"}
    # legacy total = page length; total_count = pre-slice count on BOTH pages.
    assert page1["total"] == 2 and page2["total"] == 1
    assert page1["total_count"] == 3 and page2["total_count"] == 3


def test_search_path_skip_limit_and_total_count(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    page1 = _list(search="Ray-Ban", skip=0, limit=1)
    page2 = _list(search="Ray-Ban", skip=1, limit=1)
    assert len(page1["products"]) == 1 and len(page2["products"]) == 1
    assert page1["products"][0]["product_id"] != page2["products"][0]["product_id"]
    assert page1["total_count"] == 2
    assert page1["total"] == 1  # legacy page-length total


def test_brand_path_skip_limit(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(brand="Ray-Ban", skip=1, limit=10)
    assert len(out["products"]) == 1
    assert out["total_count"] == 2


# ---------------------------------------------------------------------------
# search_products: barcode field is ADDITIVE
# ---------------------------------------------------------------------------


def test_barcode_prefix_resolves_product(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", _repo)
    out = _list(search="2000000000017")
    assert {p["product_id"] for p in out["products"]} == {"P1"}


def test_barcode_field_in_repo_search_fields():
    # The tokenized field list gained `barcode` and kept every legacy field.
    assert set(ProductRepository.SEARCH_FIELDS) >= {
        "brand",
        "model",
        "sku",
        "variant",
        "barcode",
    }


def test_existing_searches_return_supersets():
    """Adding barcode to the field list can only ADD matches: a brand/model/sku
    query over the same docs returns at least the legacy result set."""
    repo = _repo()
    legacy_fields = ["brand", "model", "sku", "variant"]
    for q in ("Ray-Ban", "RB1001", "FR-RB-0002"):
        legacy = {
            d["product_id"] for d in repo.search(q, legacy_fields, {"is_active": True})
        }
        current = {d["product_id"] for d in repo.search_products(q)}
        assert current >= legacy, q


def test_repo_search_count_matches_search():
    repo = _repo()
    docs = repo.search_products("Ray-Ban", is_active=None)
    assert repo.count_search_products("Ray-Ban", is_active=None) == len(docs) == 3
