"""F46 (owner-approved till change, 2026-09-28): the till's per-tile stock badge
and its cart-line warning read THE count the order-create oversell guard blocks
on (owner ruling 2026-08-25: oversell BLOCKS).

One rule, one implementation: GET /inventory/sellable holds no copy of the
rule. It ASKS ``orders/stock._assert_serialized_stock_available`` for the most
one line may sell: find_available only picks the first question (n + 1, then
n); any other answer is searched for, so the figure is always a quantity the
guard let through with one more refused -- or None when it never gates the
line. The guard is not touched (orders/ is byte-identical to main); only its
log is muted, for the ask alone. These pin

  * the figure is the guard's under ANY monotone rule, not just today's (a
    re-read of find_available fails ``test_the_tile_follows_any_guard_rule``),
    and stays the guard's when stock moves between the hint and the asks;
  * parity: for every shape, the guard lets ``qty == count`` through and 409s
    ``qty == count + 1``, and never blocks a None -- on fakes and on a real
    StockRepository (every non-sellable status and an expired unit left out);
  * the store is the one in the sign-in token, where create_order binds the
    guard; a named ``?store_id`` never moves it;
  * per-id counts, None for lines the guard does not gate (lens / service item
    types, virtual ids, a failing stock lookup), SKU references resolved the way
    order-create resolves them (and the canonical id returned, so the cart adds
    lines up the way the guard does), a quiet ask, and a bounded id list;
  * the grid stays fast: the hint makes a tile two asks, never a search.
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
        self.expired_asks = 0

    def count(self, query):
        key = (query.get("product_id"), query.get("store_id"))
        return self.rows.get(key, {}).get("total", 0)

    def find_available(self, pid, store_id):
        return self.rows.get((pid, store_id), {}).get("available", 0)

    def count_expired(self, pid, store_id):
        self.expired_asks += 1
        return self.rows.get((pid, store_id), {}).get("expired", 0)


class _Moving(_Stock):
    """The first find_available (the endpoint's hint) sees `first`; every read
    after it -- the guard's -- sees `then`: a receipt or a sale landed between."""

    def __init__(self, first, then):
        super().__init__({("FR-1", STORE): {"total": 5}})
        self.reads = [first]
        self.then = then

    def find_available(self, pid, store_id):
        return self.reads.pop() if self.reads else self.then


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


def _guard_selling(limit):
    """A stand-in sale guard that lets a line of up to `limit` through (None:
    never gates) -- any monotone rule the real guard might grow into."""

    def guard(items, store_id):
        if limit is not None and items[0]["quantity"] > limit:
            raise HTTPException(status_code=409, detail="Insufficient stock")

    return guard


def test_the_endpoint_asks_the_guard(client, staff_headers, till, monkeypatch):
    """Structural pin: whatever the sale guard decides, the tile follows. A
    copy of the rule inside the endpoint ignores these fakes and fails here."""
    from api.routers.orders import stock as guard_mod

    ids = dict(product_ids="FR-HAVANA,FR-BLACK,LENS-1,FR-NEW", item_types="FRAME,FRAME,LENS,FRAME")

    monkeypatch.setattr(guard_mod, "_assert_serialized_stock_available", _guard_selling(0))
    assert _get(client, staff_headers, **ids).json()["sellable"] == {
        "FR-HAVANA": 0, "FR-BLACK": 0, "LENS-1": 0, "FR-NEW": 0,
    }

    monkeypatch.setattr(guard_mod, "_assert_serialized_stock_available", _guard_selling(None))
    assert _get(client, staff_headers, **ids).json()["sellable"] == {
        "FR-HAVANA": None, "FR-BLACK": None, "LENS-1": None, "FR-NEW": None,
    }


@pytest.mark.parametrize(
    "limit",
    [
        8,  # today's rule: find_available (8) is what it sells
        7,  # a floor unit held back, or `avail <= qty`: one less than the read
        3,  # a stricter rule
        10,  # a looser rule (sells past find_available)
        0,  # refuses every line
        None,  # never gates
    ],
)
def test_the_tile_follows_any_guard_rule(client, staff_headers, till, monkeypatch, limit):
    """The NUMBER is the guard's, not a second read of its input: with
    find_available saying 8, the tile says whatever the guard would sell."""
    from api.routers.orders import stock as guard_mod

    monkeypatch.setattr(guard_mod, "_assert_serialized_stock_available", _guard_selling(limit))
    r = _get(client, staff_headers, product_ids="FR-BLACK", item_types="FRAME")
    assert r.json()["sellable"] == {"FR-BLACK": limit}


@pytest.mark.parametrize(
    "first,then",
    [
        (2, 3),  # a unit was received between the hint and the guard's read
        (2, 1),  # a unit was sold between them
        (2, 0),  # the last two went
        (0, 1),  # the first unit arrived
    ],
)
def test_stock_moving_mid_call_still_gets_the_guards_number(client, staff_headers, till, first, then):
    till["repo"] = _Moving(first, then)
    r = _get(client, staff_headers, product_ids="FR-1", item_types="FRAME")
    assert r.json()["sellable"] == {"FR-1": then}


def test_asking_the_guard_logs_nothing(client, staff_headers, till, caplog):
    """A refused ask is not a refused sale: a contact lens with expired boxes
    and none sellable, polled all day by a till, logs no '[STOCK] expired'
    WARNING. A real sale refusal still logs it and still names the expiry."""
    import logging

    from api.routers.orders.stock import _assert_serialized_stock_available

    repo = till["repo"] = _Stock({("CL-1", STORE): {"total": 2, "available": 0, "expired": 2}})
    with caplog.at_level(logging.WARNING):
        r = _get(client, staff_headers, product_ids="CL-1", item_types="CONTACT_LENS")
    assert r.json()["sellable"] == {"CL-1": 0}
    assert repo.expired_asks  # the guard ran as it is; only its log was muted
    assert not [rec for rec in caplog.records if "expired" in rec.getMessage()]

    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as exc:
        _assert_serialized_stock_available(
            [{"product_id": "CL-1", "item_type": "CONTACT_LENS", "quantity": 1}], STORE
        )
    assert "PAST THEIR EXPIRY" in exc.value.detail
    assert [rec for rec in caplog.records if "expired unit(s) held back" in rec.getMessage()]


@pytest.mark.parametrize("available", [8, 1, 0])
def test_a_tile_is_two_asks_of_the_guard_not_a_search(client, staff_headers, till, monkeypatch, available):
    """The grid stays fast: find_available's hint makes the usual answer n + 1
    refused and n let through -- two asks, ~4 reads. Ignore the hint and a tile
    with 8 on the shelf costs ~22 asks (~44 reads): ~1,000 reads per 24-tile
    call, every 30 s, per till."""
    from api.routers.orders import stock as guard_mod

    real = guard_mod._assert_serialized_stock_available
    asks = []

    def counting(items, store_id):
        asks.append(items[0]["quantity"])
        return real(items, store_id)

    monkeypatch.setattr(guard_mod, "_assert_serialized_stock_available", counting)
    till["repo"] = _Stock({("FR-1", STORE): {"total": 9, "available": available}})
    r = _get(client, staff_headers, product_ids="FR-1", item_types="FRAME")
    assert r.json()["sellable"] == {"FR-1": available}
    assert len(asks) <= 2, asks


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


@pytest.mark.parametrize("who", ["staff_headers", "auth_headers"])
def test_the_store_is_the_one_in_the_sign_in_token(client, till, request, who):
    """create_order binds the guard to the token's active store and nothing
    else. A screen whose store switch never reached the server (AuthContext's
    fire-and-forget switchStore) may name another shop; the figure must still
    be the one Complete sale checks -- for staff and for a SUPERADMIN who could
    read any shop."""
    r = _get(client, request.getfixturevalue(who), product_ids="FR-BLACK", store_id="BV-OTHER-02")
    assert r.status_code == 200, r.text
    assert r.json()["store_id"] == STORE
    assert r.json()["sellable"] == {"FR-BLACK": 8}  # BV-OTHER-02 holds 5


def test_no_store_in_the_token_gates_nothing(client, till):
    """With no active store the guard never blocks a sale, so no tile may."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {"user_id": "hq-1", "username": "hq", "roles": ["SUPERADMIN"], "store_ids": []}
    )
    r = _get(client, {"Authorization": f"Bearer {token}"}, product_ids="FR-BLACK,FR-HAVANA")
    assert r.status_code == 200, r.text
    assert r.json()["sellable"] == {"FR-BLACK": None, "FR-HAVANA": None}


def test_a_real_stock_repository_counts_only_sellable_units(client, staff_headers, till):
    """End to end on a real StockRepository: reserved, transferred, quarantined,
    damaged, sold, expired and another shop's units are not on the tile, and
    the guard sells exactly the tile's figure."""
    mongomock = pytest.importorskip("mongomock")
    from api.routers.orders.stock import _assert_serialized_stock_available
    from database.repositories.product_repository import StockRepository

    coll = mongomock.MongoClient().db.stock_units
    units = [("AVAILABLE", STORE, None)] * 2 + [
        ("AVAILABLE", STORE, "2020-01-01"),  # expired
        ("RESERVED", STORE, None),
        ("TRANSFERRED", STORE, None),
        ("QUARANTINED", STORE, None),
        ("DAMAGED", STORE, None),
        ("SOLD", STORE, None),
        ("AVAILABLE", "BV-OTHER-02", None),
    ]
    for i, (status, store, expiry) in enumerate(units):
        coll.insert_one(
            {"stock_id": f"U{i}", "product_id": "FR-1", "store_id": store, "status": status,
             "expiry_date": expiry, "barcode": f"BC{i}", "quantity": 1}
        )
    till["repo"] = StockRepository(coll)

    r = _get(client, staff_headers, product_ids="FR-1", item_types="FRAME")
    assert r.json()["sellable"] == {"FR-1": 2}
    line = {"product_id": "FR-1", "item_type": "FRAME"}
    _assert_serialized_stock_available([{**line, "quantity": 2}], STORE)
    with pytest.raises(HTTPException):
        _assert_serialized_stock_available([{**line, "quantity": 3}], STORE)


def test_endpoint_bounds_the_id_list(client, staff_headers, till):
    ids = ",".join(f"P{i}" for i in range(101))
    assert _get(client, staff_headers, product_ids=ids).status_code == 400
