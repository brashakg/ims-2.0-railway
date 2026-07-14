"""
Tests for the SUPERADMIN "block a collection from online sale" feature
(BVI-retirement). Covers:

  1. online_block.is_blocked_from_online -- True when ANY membership is blocked
     (the HARD multi-collection rule), False otherwise; CUSTOM + SMART.
  2. online_block.blocked_skus -- the batch classifier used by the availability
     paths.
  3. shopify_push.push_product -- SKIPS a blocked product (MODE_BLOCKED / skip,
     no network) and push_product_delist plans a delist (dark SIMULATED).
  4. Router POST /{id}/block sets the flag + stamps + plans a delist for an
     already-synced member; POST /{id}/unblock reverses + re-queues.
  5. online_stock_writeback -- a blocked SKU's online availability is forced to 0.
  6. rbac_policy -- block/unblock are SUPERADMIN-ONLY (ADMIN et al denied).

SAFETY: no real Shopify call is ever made -- pushes run DARK (SIMULATED) or the
network boundary is never reached. No emoji (Windows cp1252). Patched globals are
restored via monkeypatch.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_collection_online_block.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import online_block  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _EngineDB:
    """Minimal in-memory db (db["x"] subscript -> shared MockCollection)."""

    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


class _FakeConn:
    """DatabaseConnection stand-in for the router's _get_db()."""

    def __init__(self):
        self._colls = {}
        self.is_connected = True

    class _DB:
        def __init__(self, outer):
            self._outer = outer

        def __getitem__(self, name):
            return self._outer._colls.setdefault(name, MockCollection(name))

    @property
    def db(self):
        return _FakeConn._DB(self)


def _force_dark(monkeypatch):
    """Close the writes gate so every push is SIMULATED; install a boom-spy so a
    network call would fail the test."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "off")
    monkeypatch.setattr(
        shopify_push, "_load_integration_config", lambda db, t: {}
    )

    async def _boom(db, query, variables):  # pragma: no cover
        raise AssertionError("DARK push must not hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)


# ===========================================================================
# 1. is_blocked_from_online
# ===========================================================================

def test_is_blocked_true_when_a_custom_membership_is_blocked():
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM",
         "online_sync_blocked": True, "products": [{"sku": "SKU-A", "position": 0}]}
    )
    assert online_block.is_blocked_from_online({"sku": "SKU-A"}, db) is True
    # A SKU that is NOT a member of any blocked collection is clean.
    assert online_block.is_blocked_from_online({"sku": "SKU-B"}, db) is False


def test_is_blocked_false_when_only_unblocked_memberships():
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-OK", "collection_type": "CUSTOM",
         "online_sync_blocked": False, "products": [{"sku": "SKU-A", "position": 0}]}
    )
    assert online_block.is_blocked_from_online({"sku": "SKU-A"}, db) is False


def test_is_blocked_hard_rule_one_banned_membership_wins():
    """Multi-collection rule: membership in a single blocked collection blocks the
    product even if it is ALSO in an unblocked one (a brand ban is a hard block)."""
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-OK", "collection_type": "CUSTOM",
         "online_sync_blocked": False, "products": [{"sku": "SKU-A", "position": 0}]}
    )
    db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM",
         "online_sync_blocked": True, "products": [{"sku": "SKU-A", "position": 0}]}
    )
    assert online_block.is_blocked_from_online({"sku": "SKU-A"}, db) is True


def test_is_blocked_smart_rule_membership():
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-GUCCI", "collection_type": "SMART",
         "online_sync_blocked": True, "disjunctive": False,
         "rules": [{"field": "brand", "relation": "EQUALS", "value": "Gucci"}]}
    )
    assert online_block.is_blocked_from_online({"sku": "S1", "brand": "Gucci"}, db) is True
    assert online_block.is_blocked_from_online({"sku": "S2", "brand": "Ray-Ban"}, db) is False


def test_is_blocked_false_when_no_blocked_collections_or_no_db():
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C1", "collection_type": "CUSTOM", "online_sync_blocked": False,
         "products": [{"sku": "SKU-A"}]}
    )
    assert online_block.is_blocked_from_online({"sku": "SKU-A"}, db) is False
    assert online_block.is_blocked_from_online({"sku": "SKU-A"}, None) is False


# ===========================================================================
# 2. blocked_skus (batch)
# ===========================================================================

def test_blocked_skus_returns_blocked_subset():
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
         "products": [{"sku": "SKU-A", "position": 0}]}
    )
    db["ecom_collections"].insert_one(
        {"collection_id": "C-GUCCI", "collection_type": "SMART", "online_sync_blocked": True,
         "rules": [{"field": "brand", "relation": "EQUALS", "value": "Gucci"}]}
    )
    db["products"].insert_one({"sku": "SKU-C", "brand": "Gucci"})
    db["products"].insert_one({"sku": "SKU-B", "brand": "Ray-Ban"})
    got = online_block.blocked_skus(db, ["SKU-A", "SKU-B", "SKU-C"])
    assert got == {"SKU-A", "SKU-C"}


def test_blocked_skus_empty_when_none_blocked():
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C1", "collection_type": "CUSTOM", "online_sync_blocked": False,
         "products": [{"sku": "SKU-A"}]}
    )
    assert online_block.blocked_skus(db, ["SKU-A", "SKU-B"]) == set()


# ===========================================================================
# 3. push_product skip + push_product_delist plan
# ===========================================================================

def test_push_product_skips_a_blocked_product(monkeypatch):
    _force_dark(monkeypatch)  # boom-spy: any network call fails the test
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
         "products": [{"sku": "SKU-X", "position": 0}]}
    )
    product = {"id": "P1", "sku": "SKU-X", "title": "Banned",
               "ecom": {"status": "PUBLISHED"}}
    res = _run(shopify_push.push_product(db, product, []))
    assert res.mode == shopify_push.MODE_BLOCKED
    assert res.action == "skip"
    assert res.ok is False
    assert res.reason == "online_sync_blocked"


def test_push_product_not_skipped_when_unblocked(monkeypatch):
    _force_dark(monkeypatch)
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-OK", "collection_type": "CUSTOM", "online_sync_blocked": False,
         "products": [{"sku": "SKU-Y", "position": 0}]}
    )
    product = {"id": "P2", "sku": "SKU-Y", "title": "Fine", "ecom": {"status": "PUBLISHED"}}
    res = _run(shopify_push.push_product(db, product, []))
    assert res.mode == shopify_push.MODE_SIMULATED  # normal dry-run, not blocked
    assert res.ok is True


def test_push_product_delist_plans_draft_when_dark(monkeypatch):
    _force_dark(monkeypatch)
    db = _EngineDB()
    product = {"id": "P1", "ecom": {"shopify_product_id": "gid://shopify/Product/1",
                                    "status": "PUBLISHED"}}
    res = _run(shopify_push.push_product_delist(db, product))
    assert res.mode == shopify_push.MODE_SIMULATED
    assert res.action == "delist"
    assert res.ok is True
    assert res.payload["status"] == "DRAFT"
    assert res.payload["id"] == "gid://shopify/Product/1"


def test_push_product_delist_noop_when_not_on_shopify(monkeypatch):
    _force_dark(monkeypatch)
    res = _run(shopify_push.push_product_delist(_EngineDB(), {"id": "P9", "ecom": {}}))
    assert res.action == "noop"
    assert res.ok is True


# ===========================================================================
# 4. Router block / unblock over HTTP (SUPERADMIN)
# ===========================================================================

@pytest.fixture
def patched_db(monkeypatch):
    """Point dependencies.get_db + get_audit_repository at a FakeConn so the
    collections router resolves without live Mongo. Returns the conn."""
    from api import dependencies as deps
    from database.repositories.audit_repository import AuditRepository

    conn = _FakeConn()
    audit_repo = AuditRepository(conn.db["audit_logs"])
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)
    return conn


def test_block_endpoint_sets_flag_and_plans_delist(
    client, auth_headers, patched_db, monkeypatch
):
    conn = patched_db
    _force_dark(monkeypatch)
    conn.db["ecom_collections"].insert_one(
        {"collection_id": "C1", "title": "Gucci", "handle": "gucci",
         "collection_type": "CUSTOM", "online_sync_blocked": False,
         "products": [{"sku": "SKU-A", "position": 0}]}
    )
    # An already-synced member (carries a Shopify gid) -> a block must delist it.
    conn.db["catalog_products"].insert_one(
        {"id": "P-A", "sku": "SKU-A",
         "ecom": {"shopify_product_id": "gid://shopify/Product/1", "status": "PUBLISHED"}}
    )

    r = client.post("/api/v1/online-store/collections/C1/block", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blocked"] is True
    assert body["member_count"] == 1
    assert body["delisted"] == 1
    assert body["delist_results"][0]["action"] == "delist"
    assert body["delist_results"][0]["mode"] == "SIMULATED"

    saved = conn.db["ecom_collections"].find_one({"collection_id": "C1"})
    assert saved["online_sync_blocked"] is True
    assert saved["online_sync_blocked_by"] == "test-admin-001"
    assert saved.get("online_sync_blocked_at") is not None


def test_unblock_endpoint_reverses_flag_and_requeues(
    client, auth_headers, patched_db, monkeypatch
):
    conn = patched_db
    _force_dark(monkeypatch)
    conn.db["ecom_collections"].insert_one(
        {"collection_id": "C2", "title": "Gucci", "handle": "gucci2",
         "collection_type": "CUSTOM", "online_sync_blocked": True,
         "products": [{"sku": "SKU-A", "position": 0}]}
    )
    conn.db["catalog_products"].insert_one(
        {"id": "P-A", "sku": "SKU-A",
         "ecom": {"shopify_product_id": "gid://shopify/Product/1", "locally_modified": False}}
    )

    r = client.post("/api/v1/online-store/collections/C2/unblock", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blocked"] is False
    assert body["requeued"] == 1

    saved = conn.db["ecom_collections"].find_one({"collection_id": "C2"})
    assert saved["online_sync_blocked"] is False
    # The synced member is re-queued (dirty) so a later push re-publishes it.
    prod = conn.db["catalog_products"].find_one({"id": "P-A"})
    assert prod["ecom"]["locally_modified"] is True


def test_block_unknown_collection_is_404(client, auth_headers, patched_db):
    r = client.post("/api/v1/online-store/collections/NOPE/block", headers=auth_headers)
    assert r.status_code == 404, r.text


# ===========================================================================
# 5. availability -- writeback forces 0 for a blocked SKU
# ===========================================================================

def test_writeback_forces_zero_available_for_blocked(monkeypatch):
    from api.services import online_stock_writeback as wb
    from api.services import online_catalog, stock_allocation
    from agents import nexus_providers

    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
         "products": [{"sku": "SKU-A", "position": 0}]}
    )

    captured = {}

    class _Res:
        ok = True
        items_synced = 1

    async def _setter(db_, inv, loc, qty):
        captured[inv] = qty
        return _Res()

    monkeypatch.setattr(nexus_providers, "shopify_set_inventory_available", _setter)
    monkeypatch.setattr(online_catalog, "ecommerce_db_configured", lambda: True)
    monkeypatch.setattr(
        online_catalog, "online_variant_targets_for_skus",
        lambda skus: {
            "SKU-A": {"inventory_item_id": "iiA", "location_id": "L"},
            "SKU-B": {"inventory_item_id": "iiB", "location_id": "L"},
        },
    )
    monkeypatch.setattr(wb, "_on_hand_for_skus", lambda db_, skus, store: {"SKU-A": 5, "SKU-B": 7})
    monkeypatch.setattr(stock_allocation, "recommend_allocation", lambda oh, buf: oh)

    summary = _run(wb.writeback_skus(db, ["SKU-A", "SKU-B"], store_id="S1"))
    assert captured["iiA"] == 0   # blocked -> forced 0 (never sellable online)
    assert captured["iiB"] == 7   # not blocked -> its on-hand
    assert summary["blocked_online"] == 1


# ===========================================================================
# 6. rbac -- block/unblock are SUPERADMIN ONLY
# ===========================================================================

_BLOCK_ROUTES = [
    "/api/v1/online-store/collections/{collection_id}/block",
    "/api/v1/online-store/collections/{collection_id}/unblock",
]


def test_block_routes_catalogued_superadmin_only():
    for path in _BLOCK_ROUTES:
        entry = rbac.policy_for("POST", path)
        assert entry is not None, f"{path} not catalogued in rbac_policy"
        assert entry["allowed"] == ["SUPERADMIN"], f"{path} -> {entry['allowed']}"


def test_block_check_access_superadmin_only():
    concrete = "/api/v1/online-store/collections/C1/block"
    assert rbac.check_access("POST", concrete, ["SUPERADMIN"]) is True
    for role in ("ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SALES_STAFF"):
        assert rbac.check_access("POST", concrete, [role]) is False, role


def test_block_route_resolves_not_shadowed_by_bare_collection_id():
    hit = rbac.policy_for("POST", "/api/v1/online-store/collections/C1/block")
    assert hit is not None and hit["path"].endswith("/block")
    hit2 = rbac.policy_for("POST", "/api/v1/online-store/collections/C1/unblock")
    assert hit2 is not None and hit2["path"].endswith("/unblock")
