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
    # Creds resolve via shopify_auth.resolve_shopify_credentials since #916
    # (the old _load_integration_config seam no longer exists on shopify_push).
    monkeypatch.setattr(
        shopify_push, "resolve_shopify_credentials", lambda db, storefront_id="BV": None
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
    # HONEST DARK REPORTING (finding #14): the gates are DARK, so the delist was
    # only SIMULATED -- report it as PLANNED, not done. delisted (real LIVE
    # writes) MUST be 0; planned MUST be 1.
    assert body["delisted"] == 0
    assert body["planned"] == 1
    assert body["truncated"] is False
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


# ===========================================================================
# 7. ADVERSARIAL-REVIEW FIXES (#14-#20)
# ===========================================================================

from api.routers import online_store_collections as osc  # noqa: E402
from api.routers import online_store_push as osp  # noqa: E402


def _force_live(monkeypatch, graphql_fn):
    """Open all three push gates on shopify_push's namespace + install a fake
    _graphql (no real network). Restored by monkeypatch."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "t.myshopify.com", "access_token": "shpat_x"},
    )
    monkeypatch.setattr(shopify_push, "_graphql", graphql_fn)


def _dispatch_graphql(handlers):
    """Build an async _graphql replacement: dispatch on a query substring to a
    handler(variables)->response. Records every call on `.calls`."""
    calls = []

    async def _fake(db, query, variables):
        calls.append({"query": query, "variables": variables})
        for marker, fn in handlers:
            if marker in query:
                return fn(variables)
        return {"data": {}}

    _fake.calls = calls
    return _fake


# --- #14: honest dark reporting -- LIVE counts delisted, dark counts planned ---

def test_block_live_counts_delisted_not_planned(
    client, auth_headers, patched_db, monkeypatch
):
    """When the gates are LIVE a real productUpdate fires -> delisted increments,
    planned stays 0 (the mirror of the dark test above which asserts the opposite)."""
    conn = patched_db
    _force_live(
        monkeypatch,
        _dispatch_graphql(
            [(
                "productUpdate",
                lambda v: {"data": {"productUpdate": {"product": {"id": "gid://shopify/Product/1"}, "userErrors": []}}},
            )]
        ),
    )
    conn.db["ecom_collections"].insert_one(
        {"collection_id": "CL", "title": "Gucci", "handle": "gucci-live",
         "collection_type": "CUSTOM", "online_sync_blocked": False,
         "products": [{"sku": "SKU-A", "position": 0}]}
    )
    conn.db["catalog_products"].insert_one(
        {"id": "P-A", "sku": "SKU-A",
         "ecom": {"shopify_product_id": "gid://shopify/Product/1", "status": "PUBLISHED"}}
    )
    r = client.post("/api/v1/online-store/collections/CL/block", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delisted"] == 1  # a REAL LIVE write happened
    assert body["planned"] == 0
    assert body["delist_results"][0]["mode"] == "LIVE"


# --- #15: SMART >1000-member resolution is not silently truncated ---

def test_block_members_smart_over_1000_not_truncated_at_preview_cap(monkeypatch):
    """A SMART block resolves the FULL banned set, not the 1000 preview cap: 1200
    matching products all come back and truncated stays False (finding #15)."""
    products = [{"sku": f"S{i}", "brand": "Gucci"} for i in range(1200)]
    monkeypatch.setattr(osc, "_catalog_products", lambda: products)
    doc = {"collection_id": "C-SMART", "collection_type": "SMART",
           "rules": [{"field": "brand", "relation": "EQUALS", "value": "Gucci"}]}
    skus, truncated = osc._resolve_block_members(doc)
    assert len(skus) == 1200  # NOT capped at the old _RESOLVE_MAX (1000)
    assert truncated is False


def test_block_members_truncation_is_surfaced_not_silent(monkeypatch):
    """When even the higher block cap is hit, `truncated` is True (surfaced, so a
    partial delist/requeue is never silent)."""
    monkeypatch.setattr(osc, "_BLOCK_RESOLVE_MAX", 3)
    products = [{"sku": f"S{i}", "brand": "Gucci"} for i in range(10)]
    monkeypatch.setattr(osc, "_catalog_products", lambda: products)
    doc = {"collection_id": "C-SMART", "collection_type": "SMART",
           "rules": [{"field": "brand", "relation": "EQUALS", "value": "Gucci"}]}
    skus, truncated = osc._resolve_block_members(doc)
    assert len(skus) == 3
    assert truncated is True


# --- #16: delete_conflicts never deletes on a FAILED replacement create ---

def test_register_webhooks_conflict_delete_only_after_successful_create(monkeypatch):
    """Two conflicting topics: one create SUCCEEDS (its BVI conflict may be
    deleted), one create FAILS (its BVI conflict is KEPT -- deleting it would
    leave the topic delivering nowhere). Finding #16."""
    cb = "https://api.example.com/api/v1/webhooks/shopify"

    def _query(_v):
        return {"data": {"webhookSubscriptions": {"edges": [
            {"node": {"id": "gid://bvi/1", "topic": "ORDERS_CREATE",
                      "endpoint": {"__typename": "WebhookHttpEndpoint",
                                   "callbackUrl": "https://old-bvi.app/hook"}}},
            {"node": {"id": "gid://bvi/2", "topic": "ORDERS_UPDATED",
                      "endpoint": {"__typename": "WebhookHttpEndpoint",
                                   "callbackUrl": "https://old-bvi.app/hook"}}},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}

    def _create(v):
        if v.get("topic") == "ORDERS_CREATE":
            return {"data": {"webhookSubscriptionCreate": {
                "webhookSubscription": {"id": "gid://ims/9", "topic": "ORDERS_CREATE"},
                "userErrors": []}}}
        # ORDERS_UPDATED create FAILS (e.g. missing scope)
        return {"data": {"webhookSubscriptionCreate": {
            "webhookSubscription": None,
            "userErrors": [{"field": ["topic"], "message": "scope missing"}]}}}

    _force_live(monkeypatch, _dispatch_graphql([
        ("webhookSubscriptions(first", _query),
        ("webhookSubscriptionCreate(", _create),
    ]))

    async def _del_spy(db, sub_id):
        _del_spy.calls.append(sub_id)
        return {"ok": True, "deleted": "gid://deleted", "errors": []}

    _del_spy.calls = []
    monkeypatch.setattr(shopify_push, "delete_webhook_subscription", _del_spy)

    res = _run(shopify_push.register_webhooks(
        _EngineDB(), "https://api.example.com",
        topics=["orders/create", "orders/updated"], apply=True, delete_conflicts=True,
    ))
    # ORDERS_CREATE created -> its conflict deleted. ORDERS_UPDATED create failed
    # -> its conflict KEPT (delete never called for it).
    assert _del_spy.calls == ["gid://bvi/1"]
    assert res["deleted_conflicts"] == [{"topic": "ORDERS_CREATE", "id": "gid://bvi/1"}]
    assert any("skipped delete for ORDERS_UPDATED" in e for e in res["errors"])
    assert res["ok"] is False  # a create failed


# --- #17: a blocked product is excluded from the /all-pending sweep selection ---

def test_all_pending_sweep_excludes_blocked_products(monkeypatch):
    """A blocked dirty product is skipped BEFORE it consumes a limit slot or
    writes a junk audit row; the clean dirty product is pushed (finding #17)."""
    _force_dark(monkeypatch)
    from api import dependencies as deps

    conn = _FakeConn()
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)

    conn.db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
         "products": [{"sku": "SKU-BAD", "position": 0}]}
    )
    conn.db["catalog_products"].insert_one(
        {"id": "P-BAD", "sku": "SKU-BAD", "ecom": {"status": "PUBLISHED", "locally_modified": True}}
    )
    conn.db["catalog_products"].insert_one(
        {"id": "P-OK", "sku": "SKU-OK", "ecom": {"status": "PUBLISHED", "locally_modified": True}}
    )

    out = _run(osp.push_all_pending(entities="products", limit=500,
                                    current_user={"user_id": "u"}))
    assert out["summary"]["products"]["blocked_skipped"] == 1
    assert out["summary"]["products"]["pushed"] == 1
    target_ids = [r.get("target_id") for r in out["results"]]
    assert "P-OK" in target_ids
    assert "P-BAD" not in target_ids  # never pushed, never audited


# --- #18: the push path FAILS CLOSED when the block config can't be read ---

class _RaisingColl:
    def find(self, *a, **k):
        raise RuntimeError("primary stepdown")

    def find_one(self, *a, **k):
        raise RuntimeError("primary stepdown")


class _RaisingDB:
    """db whose ecom_collections read raises (a transient Mongo error)."""

    def __getitem__(self, name):
        if name == "ecom_collections":
            return _RaisingColl()
        return MockCollection(name)


def test_strict_classifier_returns_unknown_on_db_error():
    db = _RaisingDB()
    # STRICT (push path): a config read error is UNKNOWN (None), NOT a false clean.
    assert online_block.is_blocked_from_online_strict({"sku": "X"}, db) is None
    # FAIL-OPEN (availability): the same error still degrades to False (unchanged).
    assert online_block.is_blocked_from_online({"sku": "X"}, db) is False


def test_push_product_fails_closed_on_block_config_error(monkeypatch):
    """A block-config read error must SKIP the push (fail closed), never fall
    through to a SIMULATED/LIVE create that could ship a banned product (#18)."""
    _force_dark(monkeypatch)  # boom-spy: also proves no network is reached
    product = {"id": "P1", "sku": "X", "title": "T", "ecom": {"status": "PUBLISHED"}}
    res = _run(shopify_push.push_product(_RaisingDB(), product, []))
    assert res.mode == shopify_push.MODE_BLOCKED
    assert res.action == "skip"
    assert res.ok is False
    assert res.reason == "block_status_unverifiable"


# --- #19: the webhook registrar paginates past the first 100 subscriptions ---

def test_register_webhooks_paginates_past_first_page(monkeypatch):
    """A subscription already at IMS's URL on PAGE 2 is detected (already
    registered), so it is not treated as missing / re-created (finding #19)."""
    cb = "https://api.example.com/api/v1/webhooks/shopify"

    def _query(v):
        if v.get("after") is None:
            # Page 1: 100 unrelated subs (elided) + hasNextPage -> page 2 exists.
            return {"data": {"webhookSubscriptions": {"edges": [
                {"node": {"id": "gid://x/1", "topic": "PRODUCTS_CREATE",
                          "endpoint": {"__typename": "WebhookHttpEndpoint",
                                       "callbackUrl": "https://other.app/h"}}},
            ], "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"}}}}
        # Page 2: the ORDERS_CREATE sub already at OUR url.
        return {"data": {"webhookSubscriptions": {"edges": [
            {"node": {"id": "gid://x/2", "topic": "ORDERS_CREATE",
                      "endpoint": {"__typename": "WebhookHttpEndpoint",
                                   "callbackUrl": cb}}},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}

    fake = _dispatch_graphql([("webhookSubscriptions(first", _query)])
    _force_live(monkeypatch, fake)

    res = _run(shopify_push.register_webhooks(
        _EngineDB(), "https://api.example.com", topics=["orders/create"], apply=False,
    ))
    # Page 2 was read -> ORDERS_CREATE is seen as already registered, not missing.
    assert res["already_registered"] == ["ORDERS_CREATE"]
    assert res["missing"] == []
    assert len(fake.calls) == 2  # two query pages fetched
