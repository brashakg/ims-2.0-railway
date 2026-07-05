"""Owner bug 2026-07-05: "unable to see images of the products."

Spine docs carry an `images` ARRAY; the POS grid (and other consumers) read
the SINGULAR `image_url` -- so images silently rendered as placeholder icons
while the files themselves loaded fine. The list + single-product endpoints
now stamp image_url = images[0] (never overwriting an explicit image_url).
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


class _Repo:
    def __init__(self, docs):
        self.docs = docs

    def find_many(self, flt=None, skip=0, limit=100):
        return [dict(d) for d in self.docs]

    def find_by_id(self, pid):
        for d in self.docs:
            if d.get("product_id") == pid:
                return dict(d)
        return None


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
        "sku": "SG1",
        "images": ["https://api.example/img/a.jpg", "https://api.example/img/b.jpg"],
    },
    {
        "product_id": "P2",
        "sku": "SG2",
        "images": [],
    },
    {
        "product_id": "P3",
        "sku": "SG3",
        "image_url": "https://api.example/explicit.jpg",
        "images": ["https://api.example/other.jpg"],
    },
    {
        "product_id": "P4",
        "sku": "SG4",
        # no images at all
    },
]


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    # Neutralise the list cache so each test sees fresh data.
    class _NoCache:
        TTL_MEDIUM = 0

        def get(self, k):
            return None

        def set(self, k, v, ttl=0):
            pass

    import api.services.cache as cache_mod

    monkeypatch.setattr(cache_mod, "cache", _NoCache())
    yield


def test_list_stamps_first_image_as_alias(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: _Repo(_DOCS))
    out = asyncio.run(
        products_mod.list_products(
            category=None,
            brand=None,
            search=None,
            tag=None,
            store_id=None,
            skip=0,
            limit=50,
            current_user=_user(),
        )
    )
    by_id = {p["product_id"]: p for p in out["products"]}
    assert by_id["P1"]["image_url"] == "https://api.example/img/a.jpg"
    assert "image_url" not in by_id["P2"] or not by_id["P2"]["image_url"]
    # explicit image_url is never overwritten
    assert by_id["P3"]["image_url"] == "https://api.example/explicit.jpg"
    assert "image_url" not in by_id["P4"] or not by_id["P4"]["image_url"]


def test_get_product_stamps_alias(monkeypatch):
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: _Repo(_DOCS))
    p = asyncio.run(products_mod.get_product("P1", _user()))
    assert p["image_url"] == "https://api.example/img/a.jpg"
    p3 = asyncio.run(products_mod.get_product("P3", _user()))
    assert p3["image_url"] == "https://api.example/explicit.jpg"
