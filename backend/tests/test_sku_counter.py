"""
IMS 2.0 — SKU counter must be atomic + persistent (cross-cutting)
=================================================================
generate_sku minted its numeric tail from a MODULE-GLOBAL in-memory dict
(SKU_COUNTERS), which (a) reset to 1000 on every server restart -> reissued
already-used SKUs, and (b) was per-worker -> two Railway workers minted the SAME
counter -> duplicate SKUs. The counter now comes from an atomic find_one_and_update
on the shared `counters` collection when a DB is available (fail-soft to the
in-memory dict offline).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-sku")


class FakeCounters:
    """Minimal stand-in for the `counters` collection supporting the atomic
    $inc upsert generate_sku uses."""

    def __init__(self):
        self.docs = {}

    def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        _id = flt["_id"]
        cur = self.docs.get(_id, {"_id": _id, "seq": 0})
        cur["seq"] += update["$inc"]["seq"]
        self.docs[_id] = cur
        return dict(cur)


class FakeDB:
    def __init__(self):
        self._counters = FakeCounters()

    def get_collection(self, name):
        assert name == "counters"
        return self._counters


def test_db_counter_is_monotonic_and_persists():
    from api.routers.catalog import ProductCategory, _next_sku_counter

    db = FakeDB()
    cat = next(iter(ProductCategory)).value
    a = _next_sku_counter(cat, db=db)
    b = _next_sku_counter(cat, db=db)
    c = _next_sku_counter(cat, db=db)
    assert (a, b, c) == (1001, 1002, 1003)  # seeded at 1000, first issued 1001

    # "Restart": a brand-new process-global dict would reset to 1000, but the DB
    # doc survives, so the next value continues the series (no SKU reuse).
    d = _next_sku_counter(cat, db=db)
    assert d == 1004


def test_two_workers_never_collide_on_the_db_counter():
    """Two independent generate_sku calls against the SAME shared counters doc
    (simulating two workers) must get DISTINCT values."""
    from api.routers.catalog import ProductCategory, _next_sku_counter

    db = FakeDB()  # one shared counters collection
    cat = next(iter(ProductCategory)).value
    seen = {_next_sku_counter(cat, db=db) for _ in range(50)}
    assert len(seen) == 50  # all unique


def test_per_category_counters_are_independent():
    from api.routers.catalog import ProductCategory, _next_sku_counter

    db = FakeDB()
    cats = list(ProductCategory)[:2]
    a1 = _next_sku_counter(cats[0].value, db=db)
    b1 = _next_sku_counter(cats[1].value, db=db)
    a2 = _next_sku_counter(cats[0].value, db=db)
    assert a1 == 1001 and b1 == 1001 and a2 == 1002  # separate series per prefix


def test_fallback_without_db_still_works():
    from api.routers.catalog import ProductCategory, _next_sku_counter

    cat = next(iter(ProductCategory)).value
    x = _next_sku_counter(cat, db=None)
    y = _next_sku_counter(cat, db=None)
    assert isinstance(x, int) and y == x + 1  # in-memory fallback monotonic


def test_generate_sku_shape_and_uses_db_counter():
    from api.routers.catalog import ProductCategory, generate_sku

    db = FakeDB()
    cat = next(iter(ProductCategory))
    sku = generate_sku(cat, {"brand_name": "Ray-Ban", "model_no": "Wayfarer", "colour_name": "Black"}, db=db)
    # prefix-BR-WAYFBLA-1001 shape; ends with the DB counter value.
    assert sku.endswith("-1001")
    assert sku.startswith(cat.value)
