"""A saved catalogue change is visible on the next listing in every shop."""

import asyncio
from fnmatch import fnmatchcase

import pytest

from api.routers import catalog, products
from api.services import cache as cache_module
from test_catalog_flags_archive import (
    ADMIN, _archive, _fix, _flag, _restore, _rows, env,
)


class FakeRedis:
    def __init__(self):
        self.values = {}

    def setex(self, key, ttl, value):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def scan(self, cursor, *, match, count):
        return 0, [key for key in self.values if fnmatchcase(key, match)]

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


@pytest.fixture(params=["memory", "redis"])
def cached(env, monkeypatch, request):
    redis = FakeRedis() if request.param == "redis" else None
    memory = cache_module._MemoryStore()
    monkeypatch.setattr(cache_module, "_redis_client", redis)
    monkeypatch.setattr(cache_module, "_memory", memory)
    service = cache_module.CacheService()
    monkeypatch.setattr(cache_module, "cache", service)
    return service, redis.values if redis is not None else memory._store


SHOP_A = {**ADMIN, "active_store_id": "S1"}
SHOP_B = {**ADMIN, "active_store_id": "S2"}


def _prime(cached):
    service, keys = cached
    before = _rows(SHOP_A, is_active="true")
    _rows(SHOP_B, is_active="true")
    service.set("customers:S1:all", {"keep": True})
    assert any("products:S1:" in key for key in keys)
    assert any("products:S2:" in key for key in keys)
    return before


def _assert_cleared(cached):
    service, keys = cached
    assert not any("products:" in key for key in keys)
    assert service.get("customers:S1:all") == {"keep": True}


def test_create_in_another_shop_is_visible_on_the_next_cached_list(env, cached):
    before = _prime(cached)
    created = asyncio.run(products.create_product(
        products.ProductCreate(
            category="FR", brand="Carrera", model="CACHE NEW", mrp=5000,
            offer_price=4500, cost_price=2000,
            attributes={"brand_name": "Carrera", "model_no": "CACHE NEW",
                        "colour_code": "C1", "lens_size": "52"},
        ), SHOP_B, as_draft=False,
    ))
    _assert_cleared(cached)
    after = _rows(SHOP_A, is_active="true")
    assert created["product_id"] not in before
    assert created["product_id"] in after




@pytest.mark.parametrize("door", ["edit", "photo_order", "review", "archive", "restore", "flag", "fixed"])
def test_every_list_visible_edit_clears_both_shops(env, cached, door):
    if door == "restore":
        assert _archive(env, ["P1"]).status_code == 200
    if door == "fixed":
        assert _flag(env, ADMIN, ["P1"]).status_code == 200
    before = _prime(cached)

    if door in ("edit", "photo_order"):
        patch = {"mrp": 6400} if door == "edit" else {
            "images": ["https://cdn.example.com/new-first.jpg", "https://cdn.example.com/p1.jpg"]
        }
        asyncio.run(products.update_product("P1", products.ProductUpdate(**patch), SHOP_B))
    elif door == "review":
        asyncio.run(catalog.update_catalog_product(
            "T-P1", catalog.ProductUpdateInput(pricing={"mrp": 6400}), SHOP_B,
        ))
    elif door == "archive":
        assert _archive(env, ["P1"], user=SHOP_B).status_code == 200
    elif door == "restore":
        assert _restore(env, ["P1"], user=SHOP_B).status_code == 200
    elif door == "flag":
        assert _flag(env, SHOP_B, ["P1"]).status_code == 200
    else:
        assert _fix(env, "P1").status_code == 200

    _assert_cleared(cached)
    after = _rows(SHOP_A, is_active="true")
    if door in ("edit", "review"):
        assert before["P1"]["mrp"] == 5000 and after["P1"]["mrp"] == 6400
    elif door == "photo_order":
        assert after["P1"]["image_url"] == "https://cdn.example.com/new-first.jpg"
    elif door == "archive":
        assert "P1" in before and "P1" not in after
    elif door == "restore":
        assert "P1" not in before and "P1" in after
    else:
        expected = "open" if door == "flag" else "fixed"
        assert after["P1"]["flags"][0]["status"] == expected

