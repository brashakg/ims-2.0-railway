"""
Per-store Shopify locations (owner ruling 2026-09-06) -- PR 1: the read
=======================================================================
GET /api/v1/online-store/push/locations feeds the Organization page's
per-store "Shopify location" dropdown. Pinned here, each REVERT-PROOF:

  1. DARK (a push gate off) -> locations == [] plus the gate reason and ZERO
     network (a counting spy that raises proves the query was never sent).
  2. LIVE -> every node mapped to {id, name, isActive, fulfillsOnlineOrders,
     shipsInventory, city, province}; a bare numeric id is promoted; a Shopify
     error is [] plus the error, never a raise.
  3. The route joins each location to the shop already holding it through the
     ONE store reader (mapped_store_id / mapped_store_code).
  4. The rbac row is {ADMIN, SUPERADMIN} and the module :read union is
     unchanged; BOTH locations queries page 50 wide; the dropdown's own
     query (_LOCATIONS_LIST_QUERY) carries shipsInventory/address while the
     picker's (_LOCATIONS_QUERY, the live Push-stock path) keeps #1125's
     shape -- the read used here is the list query.

Every Shopify call is MOCKED at shopify_push._graphql. No Mongo.
Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_shopify_locations_read.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push.queries import _LOCATIONS_LIST_QUERY, _LOCATIONS_QUERY  # noqa: E402

BOKARO = "gid://shopify/Location/58793230523"
PUNE = "gid://shopify/Location/76684427513"
PUNE_UUID = "4dc49c44-1111-2222-3333-444444444444"

NODES = [
    {"id": BOKARO, "name": "Better Vision Sector 4", "isActive": True, "fulfillsOnlineOrders": True,
     "shipsInventory": True, "address": {"city": "Bokaro", "province": "Jharkhand"}},
    {"id": "76684427513", "name": "Gangadham Pune", "isActive": True, "fulfillsOnlineOrders": True,
     "shipsInventory": True, "address": {"city": "Pune", "province": "Maharashtra"}},
    {"id": "gid://shopify/Location/3", "name": "Old warehouse", "isActive": False,
     "fulfillsOnlineOrders": False},
]


def _run(coro):
    return asyncio.run(coro)


class _CountingBoom:
    calls = 0

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls += 1
        raise AssertionError("shopify_push._graphql reached while DARK")


class _Spy:
    def __init__(self, body=None, exc=None):
        self.calls = []
        self.body = body
        self.exc = exc

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append(query)
        if self.exc:
            raise self.exc
        return self.body


def _live(monkeypatch, spy):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", spy)


# ---------------------------------------------------------------------------
# 1: DARK is [] with zero network
# ---------------------------------------------------------------------------


def test_dark_returns_empty_list_and_never_touches_the_network(monkeypatch):
    boom = _CountingBoom()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", boom)
    out = _run(shopify_push.list_locations(StrictDB()))
    assert out["locations"] == []
    assert out["mode"] == "SIMULATED"
    assert out["reason"].startswith("writes_disabled")
    assert boom.calls == 0


# ---------------------------------------------------------------------------
# 2: LIVE shape
# ---------------------------------------------------------------------------


def test_live_maps_every_node_and_promotes_a_bare_id(monkeypatch):
    spy = _Spy({"data": {"locations": {"nodes": NODES}}})
    _live(monkeypatch, spy)
    out = _run(shopify_push.list_locations(StrictDB()))
    assert out["mode"] == "LIVE" and out["reason"] is None
    assert spy.calls == [_LOCATIONS_LIST_QUERY]  # the dropdown's query, not the picker's
    assert out["locations"] == [
        {"id": BOKARO, "name": "Better Vision Sector 4", "isActive": True, "fulfillsOnlineOrders": True,
         "shipsInventory": True, "city": "Bokaro", "province": "Jharkhand"},
        {"id": PUNE, "name": "Gangadham Pune", "isActive": True, "fulfillsOnlineOrders": True,
         "shipsInventory": True, "city": "Pune", "province": "Maharashtra"},
        {"id": "gid://shopify/Location/3", "name": "Old warehouse", "isActive": False,
         "fulfillsOnlineOrders": False, "shipsInventory": False, "city": None, "province": None},
    ]


def test_live_shopify_error_is_empty_plus_reason_never_a_raise(monkeypatch):
    _live(monkeypatch, _Spy(exc=RuntimeError("429 throttled")))
    out = _run(shopify_push.list_locations(StrictDB()))
    assert out["locations"] == [] and out["mode"] == "LIVE"
    assert "429 throttled" in out["reason"]


# ---------------------------------------------------------------------------
# 3: the route joins against the ONE store reader
# ---------------------------------------------------------------------------


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token({
        "user_id": f"test-{roles[0].lower()}", "username": roles[0].lower(),
        "roles": roles, "store_ids": ["BV-TEST-01"], "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(monkeypatch):
    from api import dependencies as deps

    db = StrictDB()
    db.seed("stores", [
        {"store_id": "BV-BOK-02", "store_code": "BV-BOK-02", "store_name": "Sec 4 Bokaro",
         "is_active": True, "store_type": "RETAIL", "shopify_location_id": BOKARO},
        {"store_id": PUNE_UUID, "store_code": "BV-PUN-01", "store_name": "GANGADHAM- PUNE",
         "is_active": True, "store_type": "RETAIL"},
        # An ONLINE store carrying a gid by hand must NEVER count as a holder.
        {"store_id": "BV-ONLINE-01", "store_code": "BV-ONLINE-01", "store_name": "Web",
         "is_active": True, "store_type": "ONLINE", "shopify_location_id": PUNE},
    ])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    return db


def test_route_joins_mapped_store_through_physical_stores(client, world, monkeypatch):
    _live(monkeypatch, _Spy({"data": {"locations": {"nodes": NODES}}}))
    r = client.get("/api/v1/online-store/push/locations", headers=_headers(["ADMIN"]))
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()["locations"]}
    assert rows[BOKARO]["mapped_store_id"] == "BV-BOK-02"
    assert rows[BOKARO]["mapped_store_code"] == "BV-BOK-02"
    assert rows[PUNE]["mapped_store_id"] is None  # the ONLINE row is not a shop
    assert rows["gid://shopify/Location/3"]["mapped_store_id"] is None


def test_route_dark_is_empty_with_reason_and_zero_network(client, world, monkeypatch):
    boom = _CountingBoom()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", boom)
    r = client.get("/api/v1/online-store/push/locations", headers=_headers(["SUPERADMIN"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locations"] == [] and body["mode"] == "SIMULATED"
    assert body["reason"].startswith("writes_disabled")
    assert boom.calls == 0


@pytest.mark.parametrize("roles, status", [
    (["CATALOG_MANAGER"], 403),
    (["STORE_MANAGER"], 403),
    (["SALES_STAFF"], 403),
])
def test_route_role_gate(client, world, monkeypatch, roles, status):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _CountingBoom())
    r = client.get("/api/v1/online-store/push/locations", headers=_headers(roles))
    assert r.status_code == status, r.text


# ---------------------------------------------------------------------------
# 4: rbac row, read union, query text
# ---------------------------------------------------------------------------


def test_rbac_row_is_admin_superadmin_and_the_read_union_is_unchanged():
    row = rbac.policy_for("GET", "/api/v1/online-store/push/locations")
    assert row is not None and set(row["allowed"]) == {"ADMIN", "SUPERADMIN"}
    from api.services.capabilities import capability_for, capability_roles

    assert capability_for("GET", "/api/v1/online-store/push/locations") == "online-store:read"
    assert set(capability_roles("online-store:read")) == {
        "ACCOUNTANT", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN",
    }


def test_locations_queries_page_fifty_wide_and_the_picker_shape_is_untouched():
    for q in (_LOCATIONS_QUERY, _LOCATIONS_LIST_QUERY):
        assert "first: 50" in q and "first: 10" not in q
    for field in ("shipsInventory", "address", "city", "province"):
        assert field in _LOCATIONS_LIST_QUERY
        assert field not in _LOCATIONS_QUERY  # the live stock path's read shape is #1125's
    assert "{ nodes { id name isActive fulfillsOnlineOrders } }" in _LOCATIONS_QUERY
