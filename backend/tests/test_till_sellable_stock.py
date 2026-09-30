"""F46 (owner-approved till change, 2026-09-28): the till's per-tile stock badge
and its cart-line warning read THE count the order-create oversell guard blocks
on (owner ruling 2026-08-25: oversell BLOCKS).

One rule, one implementation, and the sale guard is NOT touched: GET
/inventory/sellable asks ``orders/stock._assert_serialized_stock_available``
itself. It reads the available count ``n`` and has the guard judge a line of
``n + 1``: a 409 means the guard gates the line and sells exactly ``n``; a pass
means the guard never blocks it -> None. These pin

  * the endpoint follows the guard, whatever the guard decides (a copy of the
    rule inside the endpoint fails ``test_the_endpoint_asks_the_guard``);
  * parity: for every shape, the guard lets ``qty == count`` through and 409s
    ``qty == count + 1``, and never blocks a None;
  * per-id counts for the caller's store, None for lines the guard does not gate
    (lens / service item types, virtual ids, a failing stock lookup), SKU
    references resolved the way order-create resolves them (and the canonical
    id returned, so the cart adds lines up the way the guard does), store access
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


class _Down(_Stock):
    def count(self, query):
        raise RuntimeError("stock db down")

    def find_available(self, pid, store_id):
        raise RuntimeError("stock db down")


class _TrackedButLookupFails(_Stock):
    def find_available(self, pid, store_id):
        raise RuntimeError("stock db down")


STORE = "BV-TEST-01"


@pytest.fixture
def till(monkeypatch):
    """One stock repo behind BOTH the endpoint and the sale guard (in the app
    they are the same get_stock_repository)."""
    from api.routers import inventory as inv
    from api.routers import orders as om
    from database.repositories.product_repository import ProductRepository
    from tests.test_walkouts import FakeDB

    prod_repo = ProductRepository(FakeDB().get_collection("products"))
    prod_repo.create(
        {"product_id": "FR-BLACK", "sku": "FRCARRERA8895BLK", "name": "Frame",
         "category": "FRAME", "mrp": 6990.0, "is_active": True}
    )
    state = {
        "repo": _Stock(
            {
                ("FR-HAVANA", STORE): {"total": 1, "available": 0},
                ("FR-BLACK", STORE): {"total": 9, "available": 8},
                ("LENS-1", STORE): {"total": 4, "available": 0},
                ("FR-BLACK", "BV-OTHER-02"): {"total": 5, "available": 5},
            }
        )
    }
    monkeypatch.setattr(inv, "get_stock_repository", lambda: state["repo"])
    monkeypatch.setattr(om, "get_stock_repository", lambda: state["repo"])
    monkeypatch.setattr(inv, "get_product_repository", lambda: prod_repo)
    return state


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
    assert body["store_id"] == STORE
    assert body["sellable"] == {
        "FR-HAVANA": 0,
        "FR-BLACK": 8,
        "FR-NEW": None,  # untracked: the guard never blocks it
        "FRCARRERA8895BLK": 8,  # a SKU resolves like order-create resolves it
    }
    # The id the guard sums the line under, so the cart can add up a SKU line
    # and a product_id line of one product the same way.
    assert body["canonical"]["FRCARRERA8895BLK"] == "FR-BLACK"
    assert body["canonical"]["FR-HAVANA"] == "FR-HAVANA"


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


@pytest.mark.parametrize("repo", [_Down({}), _TrackedButLookupFails({("FR-BLACK", STORE): {"total": 3}})])
def test_a_failing_stock_lookup_is_never_out_of_stock(client, staff_headers, till, repo):
    till["repo"] = repo  # the guard fails soft here, so the tile must too
    r = _get(client, staff_headers, product_ids="FR-BLACK", item_types="FRAME")
    assert r.status_code == 200, r.text
    assert r.json()["sellable"] == {"FR-BLACK": None}


def test_the_endpoint_asks_the_guard(client, staff_headers, till, monkeypatch):
    """Structural pin: whatever the sale guard decides, the tile follows. A
    copy of the rule inside the endpoint ignores these fakes and fails here."""
    from api.routers.orders import stock as guard_mod

    ids = dict(product_ids="FR-HAVANA,FR-BLACK,LENS-1,FR-NEW", item_types="FRAME,FRAME,LENS,FRAME")

    def refuses_everything(items, store_id):
        raise HTTPException(status_code=409, detail="Insufficient stock")

    monkeypatch.setattr(guard_mod, "_assert_serialized_stock_available", refuses_everything)
    assert _get(client, staff_headers, **ids).json()["sellable"] == {
        "FR-HAVANA": 0, "FR-BLACK": 8, "LENS-1": 0, "FR-NEW": 0,
    }

    monkeypatch.setattr(guard_mod, "_assert_serialized_stock_available", lambda items, store_id: None)
    assert _get(client, staff_headers, **ids).json()["sellable"] == {
        "FR-HAVANA": None, "FR-BLACK": None, "LENS-1": None, "FR-NEW": None,
    }


@pytest.mark.parametrize(
    "row",
    [
        {"total": 3, "available": 0},  # tracked, nothing free (the Havana tile)
        {"total": 9, "available": 8},  # tracked, eight on the shelf
        {"total": 1, "available": 1},  # the last unit
        {"total": 0, "available": 0},  # not unit-tracked here
    ],
)
@pytest.mark.parametrize("item_type", ["FRAME", "LENS", "SERVICE", ""])
def test_the_guard_blocks_exactly_past_the_tile_count(client, staff_headers, till, row, item_type):
    from api.routers.orders.stock import _assert_serialized_stock_available

    till["repo"] = _Stock({("FR-1", STORE): row})
    n = _get(client, staff_headers, product_ids="FR-1", item_types=item_type).json()["sellable"]["FR-1"]

    def lines(qty):
        return [{"product_id": "FR-1", "item_type": item_type, "quantity": qty}]

    if n is None:
        _assert_serialized_stock_available(lines(99), STORE)  # never blocked
        return
    if n > 0:
        _assert_serialized_stock_available(lines(n), STORE)  # the count sells
    with pytest.raises(HTTPException) as exc:
        _assert_serialized_stock_available(lines(n + 1), STORE)  # one more 409s
    assert exc.value.status_code == 409


def test_endpoint_refuses_another_shops_stock_to_staff(client, staff_headers, till):
    r = _get(client, staff_headers, product_ids="FR-BLACK", store_id="BV-OTHER-02")
    assert r.status_code == 403


def test_admin_reads_the_named_shop(client, auth_headers, till):
    r = _get(client, auth_headers, product_ids="FR-BLACK", store_id="BV-OTHER-02")
    assert r.json()["sellable"] == {"FR-BLACK": 5}


def test_endpoint_bounds_the_id_list(client, staff_headers, till):
    ids = ",".join(f"P{i}" for i in range(101))
    assert _get(client, staff_headers, product_ids=ids).status_code == 400
