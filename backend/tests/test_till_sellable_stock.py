"""F46 (owner-approved till change, 2026-09-28): the till's per-tile stock badge
and its cart-line warning read THE count the order-create oversell guard blocks
on (owner ruling 2026-08-25: oversell BLOCKS). One rule, one implementation:
``orders/stock.sellable_units``. These pin

  * the rule: a product with no stock_units row at this store is not unit-
    tracked -> None (the guard never blocks it, so the till must never call it
    out of stock); tracked -> the AVAILABLE-and-in-date count; a failing lookup
    -> None (fail-soft, exactly as the guard);
  * parity: for every shape the guard lets ``qty == count`` through and 409s
    ``qty == count + 1``, and never blocks a None -- so the badge on the tile
    can never disagree with Complete sale;
  * GET /inventory/sellable: per-id counts for the caller's store, None for
    lines the guard does not gate (lens / service item types, virtual ids),
    SKU references resolved the way order-create resolves them, store access
    enforced, and a bounded id list.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


class _Stock:
    """rows: (product_id, store_id) -> {'total': N any status, 'available': M}."""

    def __init__(self, rows):
        self.rows = rows

    def count(self, query):
        key = (query.get("product_id"), query.get("store_id"))
        return self.rows.get(key, {}).get("total", 0)

    def find_available(self, pid, store_id):
        return self.rows.get((pid, store_id), {}).get("available", 0)

    def count_expired(self, pid, store_id):
        return 0


class _Down:
    def count(self, query):
        raise RuntimeError("stock db down")

    def find_available(self, pid, store_id):
        raise RuntimeError("stock db down")


class _TrackedButLookupFails(_Stock):
    def find_available(self, pid, store_id):
        raise RuntimeError("stock db down")


def test_the_rule():
    from api.routers.orders.stock import sellable_units

    repo = _Stock(
        {
            ("FR-HAVANA", "S1"): {"total": 1, "available": 0},
            ("FR-BLACK", "S1"): {"total": 9, "available": 8},
            ("FR-BLACK", "S2"): {"total": 2, "available": 2},
        }
    )
    assert sellable_units(repo, "FR-HAVANA", "S1") == 0
    assert sellable_units(repo, "FR-BLACK", "S1") == 8
    assert sellable_units(repo, "FR-BLACK", "S2") == 2  # per shop, not pooled
    assert sellable_units(repo, "FR-UNTRACKED", "S1") is None
    assert sellable_units(_Down(), "FR-BLACK", "S1") is None
    assert sellable_units(_TrackedButLookupFails({("P", "S1"): {"total": 1}}), "P", "S1") is None


@pytest.mark.parametrize(
    "row",
    [
        {"total": 3, "available": 0},  # tracked, nothing free (the Havana tile)
        {"total": 9, "available": 8},  # tracked, eight on the shelf
        {"total": 0, "available": 0},  # not unit-tracked here
    ],
)
def test_the_guard_blocks_exactly_past_the_count(monkeypatch, row):
    from api.routers import orders as om
    from api.routers.orders.stock import (
        _assert_serialized_stock_available,
        sellable_units,
    )

    repo = _Stock({("FR-1", "S1"): row})
    monkeypatch.setattr(om, "get_stock_repository", lambda: repo)

    def lines(qty):
        return [{"product_id": "FR-1", "item_type": "FRAME", "quantity": qty}]

    n = sellable_units(repo, "FR-1", "S1")
    if n is None:
        _assert_serialized_stock_available(lines(99), "S1")  # never blocked
        return
    if n > 0:
        _assert_serialized_stock_available(lines(n), "S1")  # the count sells
    with pytest.raises(HTTPException) as exc:
        _assert_serialized_stock_available(lines(n + 1), "S1")  # one more 409s
    assert exc.value.status_code == 409


@pytest.fixture
def till(monkeypatch):
    from api.routers import inventory as inv
    from database.repositories.product_repository import ProductRepository
    from tests.test_walkouts import FakeDB

    prod_repo = ProductRepository(FakeDB().get_collection("products"))
    prod_repo.create(
        {"product_id": "FR-BLACK", "sku": "FRCARRERA8895BLK", "name": "Frame",
         "category": "FRAME", "mrp": 6990.0, "is_active": True}
    )
    repo = _Stock(
        {
            ("FR-HAVANA", "BV-TEST-01"): {"total": 1, "available": 0},
            ("FR-BLACK", "BV-TEST-01"): {"total": 9, "available": 8},
            ("LENS-1", "BV-TEST-01"): {"total": 4, "available": 0},
            ("FR-BLACK", "BV-OTHER-02"): {"total": 5, "available": 5},
        }
    )
    monkeypatch.setattr(inv, "get_stock_repository", lambda: repo)
    monkeypatch.setattr(inv, "get_product_repository", lambda: prod_repo)
    return repo


def _get(client, headers, **params):
    return client.get("/api/v1/inventory/sellable", params=params, headers=headers)


def test_endpoint_reports_this_shops_count_per_tile(client, staff_headers, till):
    r = _get(
        client,
        staff_headers,
        product_ids="FR-HAVANA,FR-BLACK,FR-NEW,FRCARRERA8895BLK",
        item_types="FRAME,FRAME,FRAME,FRAME",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["store_id"] == "BV-TEST-01"
    assert body["sellable"] == {
        "FR-HAVANA": 0,
        "FR-BLACK": 8,
        "FR-NEW": None,  # untracked: the guard never blocks it
        "FRCARRERA8895BLK": 8,  # a SKU resolves like order-create resolves it
    }


def test_endpoint_is_none_for_lines_the_guard_does_not_gate(client, staff_headers, till):
    r = _get(
        client,
        staff_headers,
        product_ids="LENS-1,custom-frame,FR-HAVANA",
        item_types="LENS,FRAME,SERVICE",
    )
    assert r.status_code == 200, r.text
    assert r.json()["sellable"] == {"LENS-1": None, "custom-frame": None, "FR-HAVANA": None}


def test_endpoint_defaults_to_stock_taking_when_types_are_missing(client, staff_headers, till):
    r = _get(client, staff_headers, product_ids="FR-HAVANA,FR-BLACK")
    assert r.json()["sellable"] == {"FR-HAVANA": 0, "FR-BLACK": 8}


def test_endpoint_refuses_another_shops_stock_to_staff(client, staff_headers, till):
    r = _get(client, staff_headers, product_ids="FR-BLACK", store_id="BV-OTHER-02")
    assert r.status_code == 403


def test_admin_reads_the_named_shop(client, auth_headers, till):
    r = _get(client, auth_headers, product_ids="FR-BLACK", store_id="BV-OTHER-02")
    assert r.json()["sellable"] == {"FR-BLACK": 5}


def test_endpoint_bounds_the_id_list(client, staff_headers, till):
    ids = ",".join(f"P{i}" for i in range(101))
    assert _get(client, staff_headers, product_ids=ids).status_code == 400
