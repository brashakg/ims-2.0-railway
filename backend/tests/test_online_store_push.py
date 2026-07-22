"""
Tests for the BVI Phase 5 Shopify PUSH module (IMS -> Shopify).

***** SAFETY-CRITICAL: every Shopify call is MOCKED. *****
The real network boundary `shopify_push._graphql` is monkeypatched to a fake in
every test that exercises the LIVE branch, so NO real Shopify request is ever
made. The DARK-by-default tests assert the engine returns a SIMULATED dry-run
WITHOUT even reaching that boundary (a spy proves _graphql was never called).

Four layers:
  1. Gating / mode -- push_mode_status + the three gate components; the default
     posture is DARK (SIMULATED) with no creds / gate off.
  2. Engine SIMULATED-by-default -- push_product/collection/menu/image each return
     mode=SIMULATED with the dry-run payload and DO NOT touch the network, for
     every reason a gate can be closed.
  3. Engine LIVE (gates+creds mocked on, _graphql mocked) -- the LIVE path calls
     the mock, returns mode=LIVE, and WRITES BACK the Shopify gid (idempotent
     re-push UPDATES instead of duplicating). Payload builders are checked too.
  4. Router wiring over a TestClient + monkeypatched DB + audit repo: every push
     route is catalogued in rbac_policy.POLICY with EXACTLY {ADMIN, SUPERADMIN}
     (narrower than the rest of the module -- CATALOG_MANAGER/DESIGN_MANAGER are
     denied), check_access allow/deny, a 403 for CATALOG_MANAGER, the live
     SIMULATED push flow over HTTP, an audit_logs row PER push, and GET /status.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_online_store_push.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================

def _run(coro):
    """Run one coroutine to completion (the engine functions are async).

    Uses asyncio.run() (a fresh loop each call) -- get_event_loop() raises
    'no current event loop' on Python 3.12+ outside a running loop."""
    return asyncio.run(coro)


class _SpyGraphQL:
    """A fake shopify_push._graphql: records every call and returns a canned
    GraphQL response. Used to prove (a) the LIVE branch calls it and (b) the DARK
    branch never does."""

    def __init__(self, response):
        self.calls = []
        self._response = response

    async def __call__(self, db, query, variables):
        self.calls.append({"query": query, "variables": variables})
        return self._response


def _force_live(monkeypatch, graphql_response):
    """Open all three gates ON shopify_push's OWN namespace (it imported the
    symbols by value) and replace the network boundary with a spy. Returns the
    spy so a test can assert calls + inspect variables.

    Creds now come from resolve_shopify_credentials (OAuth-preferred resolver),
    so we stub THAT symbol in shopify_push's namespace -- no real mint."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push, "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "test.myshopify.com",
                    "access_token": "shpat_test", "source": "vault"},
    )
    spy = _SpyGraphQL(graphql_response)
    monkeypatch.setattr(shopify_push, "_graphql", spy)
    return spy


def _force_dark(monkeypatch, reason="writes_off"):
    """Close a gate so the engine is SIMULATED, and install a spy that EXPLODES if
    called (the dark branch must never reach the network)."""
    _creds = {"shop_url": "x", "access_token": "y", "source": "vault"}
    if reason == "writes_off":
        monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
        monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
        monkeypatch.setattr(shopify_push, "resolve_shopify_credentials",
                            lambda db, storefront_id="BV": _creds)
    elif reason == "dispatch_off":
        monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
        monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "off")
        monkeypatch.setattr(shopify_push, "resolve_shopify_credentials",
                            lambda db, storefront_id="BV": _creds)
    elif reason == "no_creds":
        monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
        monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
        monkeypatch.setattr(shopify_push, "resolve_shopify_credentials", lambda db, storefront_id="BV": None)

    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("DARK push must not hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)


class _FakeConn:
    """Stand-in for the DatabaseConnection the push router's _get_db() expects:
    `.is_connected` True + `.db[name]` returns a shared MockCollection so the
    router + engine hit the same in-memory store."""

    def __init__(self):
        self._colls = {}
        self.is_connected = True

    class _DB:
        def __init__(self, outer):
            self._outer = outer

        def __getitem__(self, name):
            return self._outer._colls.setdefault(name, MockCollection(name))

        # The engine's write-backs use db["name"].update_one(...); MockCollection
        # provides that. find_one likewise. Subscript is the only access used.

    @property
    def db(self):
        return _FakeConn._DB(self)


# A minimal in-memory db that the ENGINE can use directly (db["x"] subscript).
class _EngineDB:
    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


# ===========================================================================
# Layer 1 -- gating / mode posture
# ===========================================================================

def test_mode_is_dark_by_default(monkeypatch):
    """With writes off (the default per #262), the posture is SIMULATED and the
    three gate components are reported."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "off")
    status = shopify_push.push_mode_status(None)
    assert status["mode"] == "SIMULATED"
    assert status["is_live"] is False
    assert status["writes_enabled"] is False
    assert status["creds_present"] is False
    assert "single_writer_note" in status


def test_mode_is_live_only_when_all_three_align(monkeypatch):
    _force_live(monkeypatch, {})
    status = shopify_push.push_mode_status(object())
    assert status["mode"] == "LIVE"
    assert status["is_live"] is True
    assert status["writes_enabled"] and status["creds_present"]
    assert status["dispatch_mode"] == "live"


def test_mode_dark_when_creds_missing_even_if_gates_on(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "resolve_shopify_credentials", lambda db, storefront_id="BV": None)
    status = shopify_push.push_mode_status(object())
    assert status["mode"] == "SIMULATED" and status["creds_present"] is False


# ===========================================================================
# Layer 2 -- engine is SIMULATED by default + NEVER touches the network
# ===========================================================================

@pytest.mark.parametrize("reason", ["writes_off", "dispatch_off", "no_creds"])
def test_push_product_simulated_no_network(monkeypatch, reason):
    _force_dark(monkeypatch, reason)
    product = {"id": "P1", "title": "Ray-Ban Aviator", "brand": "Ray-Ban",
               "ecom": {"status": "PUBLISHED", "handle": "rayban-aviator"}}
    res = _run(shopify_push.push_product(_EngineDB(), product, []))
    assert res.mode == "SIMULATED"
    assert res.ok is True            # a dry-run is a success
    assert res.action == "create"    # no stored gid yet
    assert res.entity == "product"
    assert res.target_id == "P1"
    # The dry-run carries the would-be Shopify ProductInput.
    assert res.payload["title"] == "Ray-Ban Aviator"
    assert res.payload["status"] == "ACTIVE"   # PUBLISHED -> ACTIVE
    assert res.payload["handle"] == "rayban-aviator"
    assert res.reason  # explains WHY we are dark


def test_push_collection_menu_image_simulated_no_network(monkeypatch):
    _force_dark(monkeypatch, "writes_off")
    db = _EngineDB()
    coll = {"collection_id": "C1", "title": "Sunglasses", "handle": "sunglasses",
            "collection_type": "SMART", "disjunctive": False,
            "rules": [{"field": "category", "relation": "EQUALS", "value": "SUNGLASSES"}]}
    menu = {"menu_id": "M1", "title": "Main", "handle": "main-menu",
            "items": [{"id": "n1", "title": "Shop", "item_type": "COLLECTION",
                       "resource_id": "gid://shopify/Collection/9", "children": []}]}
    img = {"image_id": "I1", "product_id": "P1", "url": "http://x/raw.jpg",
           "status": "APPROVED"}

    rc = _run(shopify_push.push_collection(db, coll))
    rm = _run(shopify_push.push_menu(db, menu))
    ri = _run(shopify_push.push_image(db, img))
    for r in (rc, rm, ri):
        assert r.mode == "SIMULATED" and r.ok is True
    # Collection dry-run carries the smart ruleSet.
    assert rc.payload["ruleSet"]["rules"][0]["column"] == "TYPE"
    # Menu dry-run carries the mapped item tree.
    assert rm.payload["items"][0]["type"] == "COLLECTION"
    # Image dry-run carries the media input.
    assert ri.payload["media"][0]["originalSource"] == "http://x/raw.jpg"


def test_push_image_non_approved_is_skipped_even_dark(monkeypatch):
    """A non-APPROVED image is push-INELIGIBLE: ok=False action=skip, regardless of
    the gate (the design-queue go-live gate). No network either."""
    _force_dark(monkeypatch, "writes_off")
    img = {"image_id": "I2", "product_id": "P1", "url": "u", "status": "REVIEW"}
    res = _run(shopify_push.push_image(_EngineDB(), img))
    assert res.action == "skip" and res.ok is False
    assert "APPROVED" in (res.error or "")


# ===========================================================================
# Layer 3 -- engine LIVE (mocked client) + idempotent gid write-back
# ===========================================================================

def test_push_product_live_creates_and_writes_back_gid(monkeypatch):
    """LIVE create: calls the mocked _graphql, returns the new gid, and persists
    ecom.shopify_product_id back onto the catalog_products doc + clears dirty."""
    spy = _force_live(monkeypatch, {
        "data": {"productCreate": {
            "product": {"id": "gid://shopify/Product/111", "handle": "rb"},
            "userErrors": [],
        }}
    })
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P1", "title": "RB", "ecom": {"status": "PUBLISHED", "locally_modified": True}}
    )
    product = db["catalog_products"].find_one({"id": "P1"})

    res = _run(shopify_push.push_product(db, product, []))
    assert res.mode == "LIVE" and res.ok is True
    assert res.action == "create"
    assert res.shopify_id == "gid://shopify/Product/111"
    assert len(spy.calls) == 1  # the network boundary WAS hit (once)

    # Idempotency write-back: the gid is now on the doc + dirty cleared.
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_product_id"] == "gid://shopify/Product/111"
    assert saved["ecom"]["locally_modified"] is False
    assert "last_pushed_at" in saved["ecom"]


def test_push_product_live_repush_updates_not_duplicates(monkeypatch):
    """A second push of a product that ALREADY has a gid uses productUpdate (not
    create) and includes the gid in the input -> Shopify updates the same object."""
    spy = _force_live(monkeypatch, {
        "data": {"productUpdate": {
            "product": {"id": "gid://shopify/Product/111", "handle": "rb"},
            "userErrors": [],
        }}
    })
    db = _EngineDB()
    product = {"id": "P1", "title": "RB",
               "ecom": {"status": "PUBLISHED", "shopify_product_id": "gid://shopify/Product/111"}}

    res = _run(shopify_push.push_product(db, product, []))
    assert res.action == "update"
    assert res.shopify_id == "gid://shopify/Product/111"
    # The mutation was the UPDATE one and carried the existing id in the input.
    assert "productUpdate" in spy.calls[0]["query"]
    assert spy.calls[0]["variables"]["input"]["id"] == "gid://shopify/Product/111"


def test_push_collection_live_writes_back_and_handles_user_errors(monkeypatch):
    # First: a clean create writes back the collection gid.
    spy = _force_live(monkeypatch, {
        "data": {"collectionCreate": {
            "collection": {"id": "gid://shopify/Collection/55", "handle": "sg"},
            "userErrors": [],
        }}
    })
    db = _EngineDB()
    db["ecom_collections"].insert_one(
        {"collection_id": "C1", "title": "SG", "handle": "sg", "collection_type": "CUSTOM",
         "locally_modified": True}
    )
    coll = db["ecom_collections"].find_one({"collection_id": "C1"})
    res = _run(shopify_push.push_collection(db, coll))
    assert res.ok is True and res.shopify_id == "gid://shopify/Collection/55"
    saved = db["ecom_collections"].find_one({"collection_id": "C1"})
    assert saved["shopify_collection_id"] == "gid://shopify/Collection/55"
    assert saved["locally_modified"] is False

    # Now: a userErrors response -> ok=False, NO write-back of a bad gid.
    monkeypatch.setattr(shopify_push, "_graphql", _SpyGraphQL({
        "data": {"collectionCreate": {
            "collection": None,
            "userErrors": [{"field": "handle", "message": "is invalid"}],
        }}
    }))
    db2 = _EngineDB()
    db2["ecom_collections"].insert_one(
        {"collection_id": "C2", "title": "Bad", "handle": "bad", "collection_type": "CUSTOM"}
    )
    bad = db2["ecom_collections"].find_one({"collection_id": "C2"})
    res2 = _run(shopify_push.push_collection(db2, bad))
    assert res2.ok is False and "userErrors" in (res2.error or "")
    assert db2["ecom_collections"].find_one({"collection_id": "C2"}).get("shopify_collection_id") is None


def test_push_menu_live_writes_back_gid(monkeypatch):
    spy = _force_live(monkeypatch, {
        "data": {"menuCreate": {
            "menu": {"id": "gid://shopify/Menu/7", "handle": "main-menu"},
            "userErrors": [],
        }}
    })
    db = _EngineDB()
    db["ecom_menus"].insert_one(
        {"menu_id": "M1", "title": "Main", "handle": "main-menu",
         "items": [{"id": "n1", "title": "Shop", "item_type": "HTTP", "url": "/shop", "children": []}],
         "locally_modified": True}
    )
    menu = db["ecom_menus"].find_one({"menu_id": "M1"})
    res = _run(shopify_push.push_menu(db, menu))
    assert res.ok is True and res.shopify_id == "gid://shopify/Menu/7"
    assert db["ecom_menus"].find_one({"menu_id": "M1"})["shopify_menu_id"] == "gid://shopify/Menu/7"
    # menuCreate carried the title/handle/items variables.
    assert spy.calls[0]["variables"]["handle"] == "main-menu"


def test_push_image_live_attaches_media_and_writes_back(monkeypatch):
    """An APPROVED image whose parent product is already on Shopify pushes via
    productCreateMedia and writes back the MediaImage gid."""
    spy = _force_live(monkeypatch, {
        "data": {"productCreateMedia": {
            "media": [{"id": "gid://shopify/MediaImage/900"}],
            "mediaUserErrors": [],
        }}
    })
    db = _EngineDB()
    # Parent product must already carry a Shopify gid (media attaches to a product).
    db["catalog_products"].insert_one(
        {"id": "P1", "ecom": {"shopify_product_id": "gid://shopify/Product/111"}}
    )
    db["product_images"].insert_one(
        {"image_id": "I1", "product_id": "P1", "url": "http://x/raw.jpg",
         "edited_url": "http://x/edited.jpg", "status": "APPROVED", "shopify_image_id": None}
    )
    img = db["product_images"].find_one({"image_id": "I1"})
    res = _run(shopify_push.push_image(db, img))
    assert res.ok is True and res.shopify_id == "gid://shopify/MediaImage/900"
    assert db["product_images"].find_one({"image_id": "I1"})["shopify_image_id"] == "gid://shopify/MediaImage/900"
    # Prefer the EDITED asset as the source.
    assert spy.calls[0]["variables"]["media"][0]["originalSource"] == "http://x/edited.jpg"


def test_push_image_live_skips_when_parent_not_on_shopify(monkeypatch):
    """LIVE but the parent product has no Shopify gid yet -> skip (ok=False), no
    media call (you must push the product first)."""
    spy = _force_live(monkeypatch, {"data": {"productCreateMedia": {"media": [], "mediaUserErrors": []}}})
    db = _EngineDB()
    db["catalog_products"].insert_one({"id": "P1", "ecom": {}})  # no shopify_product_id
    db["product_images"].insert_one(
        {"image_id": "I1", "product_id": "P1", "url": "u", "status": "APPROVED"}
    )
    img = db["product_images"].find_one({"image_id": "I1"})
    res = _run(shopify_push.push_image(db, img))
    assert res.action == "skip" and res.ok is False
    assert spy.calls == []  # never reached the network


def test_push_product_live_failsoft_on_transport_error(monkeypatch):
    """A transport exception from _graphql becomes a fail-soft ok=False result,
    never a raise."""
    _force_live(monkeypatch, {})

    async def _raise(db, query, variables):
        raise ValueError("status 500: boom")

    monkeypatch.setattr(shopify_push, "_graphql", _raise)
    res = _run(shopify_push.push_product(_EngineDB(),
                                         {"id": "P1", "title": "X", "ecom": {"status": "DRAFT"}}, []))
    assert res.mode == "LIVE" and res.ok is False
    assert "boom" in (res.error or "")


# ===========================================================================
# Layer 3b -- payload builders (pure)
# ===========================================================================

def test_build_product_input_maps_status_and_options():
    product = {"id": "P1", "title": "RB", "brand": "Ray-Ban", "category": "SUNGLASS",
               "ecom": {"status": "DRAFT", "handle": "rb",
                        "seo": {"title": "RB SEO", "tags": ["new", "summer"]}}}
    variants = [{"sku": "S-1", "option_color": "Black", "option_size": "M"},
                {"sku": "S-2", "option_color": "Gold", "option_size": "M"}]
    inp = shopify_push.build_product_input(product, variants)
    assert inp["status"] == "DRAFT"
    assert inp["vendor"] == "Ray-Ban"
    assert inp["productType"] == "SUNGLASS"
    assert inp["seo"]["title"] == "RB SEO"
    # Tags = manual seo.tags UNION the BVI attribute->tag tokens (here a SUNGLASS
    # with top-level brand Ray-Ban -> brand_ray-ban), lower-cased + de-duped.
    assert inp["tags"] == ["new", "summer", "brand_ray-ban"]
    # Options derived + de-duped (Color: Black, Gold ; Size: M).
    opts = {o["name"]: [v["name"] for v in o["values"]] for o in inp["productOptions"]}
    assert opts["Color"] == ["Black", "Gold"]
    assert opts["Size"] == ["M"]


def test_build_rule_set_skips_unknown_columns():
    rules = [
        {"field": "brand", "relation": "EQUALS", "value": "Ray-Ban"},
        {"field": "nonsense", "relation": "EQUALS", "value": "x"},  # dropped
        {"field": "tag", "relation": "CONTAINS", "value": "sale"},
    ]
    out = shopify_push._build_rule_set(rules)
    cols = [r["column"] for r in out]
    assert cols == ["VENDOR", "TAG"]  # nonsense skipped, never pushed


# ===========================================================================
# Layer 4 -- router RBAC catalogue + role gate + live flow + audit + status
# ===========================================================================

_PUSH_ROUTES = [
    ("GET", "/api/v1/online-store/push/status"),
    ("POST", "/api/v1/online-store/push/product/{product_id}"),
    ("POST", "/api/v1/online-store/push/collection/{collection_id}"),
    ("POST", "/api/v1/online-store/push/menu/{menu_id}"),
    ("POST", "/api/v1/online-store/push/image/{image_id}"),
    ("POST", "/api/v1/online-store/push/all-pending"),
]

_PUSH_SET = {"ADMIN", "SUPERADMIN"}


def test_every_push_route_catalogued_admin_superadmin_only():
    """Push is integration-critical -> EXACTLY {ADMIN, SUPERADMIN}; the broader
    ecom roles (CATALOG_MANAGER/DESIGN_MANAGER) are NOT admitted here."""
    for method, path in _PUSH_ROUTES:
        entry = rbac.policy_for(method, path)
        assert entry is not None, f"{method} {path} not catalogued in rbac_policy"
        assert set(entry["allowed"]) == _PUSH_SET, f"{method} {path} -> {entry['allowed']}"


def test_status_route_beats_entity_param_routes():
    """The literal /push/status resolves to its own row, not an entity push."""
    hit = rbac.policy_for("GET", "/api/v1/online-store/push/status")
    assert hit is not None and hit["path"].endswith("/push/status")
    # And an entity push resolves to its own templated row.
    hit2 = rbac.policy_for("POST", "/api/v1/online-store/push/product/P1")
    assert hit2 is not None and hit2["path"].endswith("/push/product/{product_id}")


def test_check_access_admin_superadmin_only():
    path = "/api/v1/online-store/push/product/{product_id}"
    for role in ("SUPERADMIN", "ADMIN"):
        assert rbac.check_access("POST", path, [role]) is True, role
    # Crucially: the OTHER ecom roles are denied on the push surface.
    for role in ("CATALOG_MANAGER", "DESIGN_MANAGER", "SALES_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("POST", path, [role]) is False, role


# --- live HTTP flow over a monkeypatched DB + audit repo (no live Mongo) -----

@pytest.fixture
def patched_db(monkeypatch):
    """Point dependencies.get_db at a fresh _FakeConn + get_audit_repository at a
    real AuditRepository bound to that conn's audit_logs MockCollection, so the
    push router's _get_db() + _write_audit() resolve without live Mongo. Returns
    (conn, audit_repo)."""
    from api import dependencies as deps
    from database.repositories.audit_repository import AuditRepository

    conn = _FakeConn()
    audit_repo = AuditRepository(conn.db["audit_logs"])
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)
    return conn, audit_repo


@pytest.fixture
def catalog_headers(client):
    """A CATALOG_MANAGER JWT -- allowed elsewhere in the module, but MUST be 403
    on the push surface."""
    from api.routers.auth import create_access_token

    token = create_access_token({
        "user_id": "test-catalog-001", "username": "catmgr",
        "roles": ["CATALOG_MANAGER"], "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


def test_live_role_gate_forbids_catalog_manager(client, catalog_headers, patched_db):
    """CATALOG_MANAGER is inside the ecom set but OUTSIDE the push set -> 403."""
    r = client.get("/api/v1/online-store/push/status", headers=catalog_headers)
    assert r.status_code == 403, r.text


def test_live_role_gate_forbids_sales_staff(client, staff_headers, patched_db):
    r = client.post("/api/v1/online-store/push/product/P1", headers=staff_headers)
    assert r.status_code == 403, r.text


def test_status_endpoint_reports_dark_and_counts(client, auth_headers, patched_db, monkeypatch):
    """GET /status: DARK by default + per-entity counts from the seeded docs."""
    conn, _ = patched_db
    # Force DARK explicitly (default), regardless of process env.
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "off")
    # Seed a staged + pushed product and a pending collection.
    conn.db["catalog_products"].insert_one({"id": "P1", "ecom": {"locally_modified": True}})
    conn.db["catalog_products"].insert_one(
        {"id": "P2", "ecom": {"shopify_product_id": "gid://shopify/Product/1"}})
    conn.db["ecom_collections"].insert_one(
        {"collection_id": "C1", "handle": "sg", "title": "SG", "locally_modified": True})

    r = client.get("/api/v1/online-store/push/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"]["mode"] == "SIMULATED"
    assert body["mode"]["is_live"] is False
    assert body["db_connected"] is True
    counts = body["counts"]
    assert counts["products"]["staged"] == 2
    assert counts["products"]["pushed"] == 1
    assert counts["products"]["pending"] == 1
    assert counts["collections"]["pending"] == 1


def test_live_push_product_simulated_over_http_writes_audit(client, auth_headers, patched_db, monkeypatch):
    """End-to-end over HTTP: a DARK product push returns mode=SIMULATED with the
    dry-run payload AND writes a chained ONLINE_STORE_PUSH audit row."""
    conn, audit_repo = patched_db
    # DARK posture; install a boom-spy so a network call would fail the test.
    _force_dark(monkeypatch, "writes_off")
    conn.db["catalog_products"].insert_one(
        {"id": "P1", "title": "Ray-Ban", "brand": "Ray-Ban",
         "ecom": {"status": "PUBLISHED", "handle": "rb"}}
    )

    r = client.post("/api/v1/online-store/push/product/P1", headers=auth_headers)
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["mode"] == "SIMULATED"
    assert result["ok"] is True
    assert result["entity"] == "product"
    assert result["payload"]["title"] == "Ray-Ban"

    rows = audit_repo.find_many({"action": "ONLINE_STORE_PUSH"})
    assert len(rows) == 1, "every push must write exactly one audit row"
    row = rows[0]
    assert row["entity_type"] == "product"
    assert row["entity_id"] == "P1"
    assert row["details"]["mode"] == "SIMULATED"
    assert row["user_id"] == "test-admin-001"


def test_live_push_product_404_when_missing(client, auth_headers, patched_db):
    r = client.post("/api/v1/online-store/push/product/NOPE", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_push_product_400_without_ecom_subdoc(client, auth_headers, patched_db):
    """A product never staged for the online store (no ecom sub-doc) is a 400."""
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one({"id": "P9", "title": "Offline only"})
    r = client.post("/api/v1/online-store/push/product/P9", headers=auth_headers)
    assert r.status_code == 400, r.text


def test_get_catalog_product_falls_back_to_sku_then_parent_sku():
    """Audit OS-004: the resolver retries by `sku` (BVI-imported docs) and then
    `parent_sku` (door-created spine mirrors, whose catalog `id` is the
    pim_product_id uuid) when the primary id lookup misses -- a caller holding
    a sku no longer gets a misleading 404."""
    from api.routers.online_store_push import _get_catalog_product

    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "PIM-1", "sku": "BV-SKU-1", "title": "BVI import"})
    db["catalog_products"].insert_one(
        {"id": "PIM-2", "parent_sku": "FR-SKU-2", "title": "Door mirror"})
    # Primary id hit still wins.
    assert _get_catalog_product(db, "PIM-1")["title"] == "BVI import"
    # sku fallback (BVI docs carry `sku`).
    assert _get_catalog_product(db, "BV-SKU-1")["title"] == "BVI import"
    # parent_sku fallback (door mirrors carry `parent_sku`, not `sku`).
    assert _get_catalog_product(db, "FR-SKU-2")["title"] == "Door mirror"
    # A genuine miss is still None (-> the route's 404).
    assert _get_catalog_product(db, "NOPE") is None


def test_live_push_collection_and_menu_simulated_over_http(client, auth_headers, patched_db, monkeypatch):
    conn, audit_repo = patched_db
    _force_dark(monkeypatch, "writes_off")
    conn.db["ecom_collections"].insert_one(
        {"collection_id": "C1", "title": "SG", "handle": "sg", "collection_type": "CUSTOM"})
    conn.db["ecom_menus"].insert_one(
        {"menu_id": "M1", "title": "Main", "handle": "main-menu", "items": []})

    rc = client.post("/api/v1/online-store/push/collection/C1", headers=auth_headers)
    rm = client.post("/api/v1/online-store/push/menu/M1", headers=auth_headers)
    assert rc.status_code == 200 and rc.json()["result"]["mode"] == "SIMULATED"
    assert rm.status_code == 200 and rm.json()["result"]["mode"] == "SIMULATED"
    # Two pushes -> two audit rows.
    assert len(audit_repo.find_many({"action": "ONLINE_STORE_PUSH"})) == 2


def test_push_unknown_collection_and_menu_are_404(client, auth_headers, patched_db):
    assert client.post("/api/v1/online-store/push/collection/NOPE", headers=auth_headers).status_code == 404
    assert client.post("/api/v1/online-store/push/menu/NOPE", headers=auth_headers).status_code == 404


def test_db_down_push_is_503_not_false_200(client, auth_headers, monkeypatch):
    """No DB -> the push surface 503s (Fail Loudly, not a false 200)."""
    from api import dependencies as deps
    monkeypatch.setattr(deps, "get_db", lambda: None)
    r = client.post("/api/v1/online-store/push/product/P1", headers=auth_headers)
    assert r.status_code == 503, r.text


# ===========================================================================
# Batch "push all pending" sweep (the Phase-6 cutover queue-drain)
# ===========================================================================


def _seed_pending(conn):
    """Seed a mix of pending + already-pushed/clean docs across all four entities."""
    conn.db["catalog_products"].insert_one(
        {"id": "P1", "title": "RB", "brand": "RB",
         "ecom": {"status": "PUBLISHED", "handle": "rb", "locally_modified": True}})
    conn.db["catalog_products"].insert_one(  # clean -> NOT swept
        {"id": "P2", "ecom": {"shopify_product_id": "gid://shopify/Product/2"}})
    conn.db["ecom_collections"].insert_one(
        {"collection_id": "C1", "title": "SG", "handle": "sg",
         "collection_type": "CUSTOM", "locally_modified": True})
    conn.db["ecom_menus"].insert_one(
        {"menu_id": "M1", "title": "Main", "handle": "main-menu",
         "items": [], "locally_modified": True})
    conn.db["product_images"].insert_one(  # APPROVED + unpushed -> swept
        {"image_id": "I1", "product_id": "P1", "url": "http://x/a.jpg",
         "status": "APPROVED"})
    conn.db["product_images"].insert_one(  # already pushed -> NOT swept
        {"image_id": "I2", "product_id": "P1", "url": "http://x/b.jpg",
         "status": "APPROVED", "shopify_image_id": "gid://shopify/MediaImage/9"})


def test_push_all_pending_dark_sweeps_every_dirty_doc(client, auth_headers, patched_db, monkeypatch):
    """The batch sweep pushes exactly the pending docs (dirty product/collection/
    menu + APPROVED-unpushed image), all SIMULATED, one audit row each."""
    conn, audit_repo = patched_db
    _force_dark(monkeypatch, "writes_off")  # boom-spy: a network call fails the test
    _seed_pending(conn)

    r = client.post("/api/v1/online-store/push/all-pending", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"]["mode"] == "SIMULATED"
    assert body["db_connected"] is True
    # P1 + C1 + M1 + I1 = 4 (P2 clean, I2 already pushed -> skipped).
    assert body["pushed_count"] == 4, body
    assert body["summary"]["products"]["pushed"] == 1
    assert body["summary"]["collections"]["pushed"] == 1
    assert body["summary"]["menus"]["pushed"] == 1
    assert body["summary"]["images"]["pushed"] == 1
    # Every push is a dry-run + every push wrote an audit row.
    assert all(res["mode"] == "SIMULATED" for res in body["results"])
    assert len(audit_repo.find_many({"action": "ONLINE_STORE_PUSH"})) == 4


def test_push_all_pending_entities_filter(client, auth_headers, patched_db, monkeypatch):
    """The entities CSV filter restricts the sweep to the named entity only."""
    conn, _ = patched_db
    _force_dark(monkeypatch, "writes_off")
    _seed_pending(conn)

    r = client.post(
        "/api/v1/online-store/push/all-pending?entities=collections",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pushed_count"] == 1
    assert body["summary"].get("collections", {}).get("pushed") == 1
    assert "products" not in body["summary"]


def test_push_all_pending_limit_caps_the_sweep(client, auth_headers, patched_db, monkeypatch):
    """`limit` caps the total pushes and flags limit_reached."""
    conn, _ = patched_db
    _force_dark(monkeypatch, "writes_off")
    _seed_pending(conn)

    r = client.post(
        "/api/v1/online-store/push/all-pending?limit=1", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pushed_count"] == 1
    assert body["limit_reached"] is True


def test_push_all_pending_403_for_catalog_manager(client, catalog_headers, patched_db):
    """The batch sweep is on the narrowed push surface -> CATALOG_MANAGER 403."""
    r = client.post("/api/v1/online-store/push/all-pending", headers=catalog_headers)
    assert r.status_code == 403, r.text


def test_push_all_pending_503_no_db(client, auth_headers, monkeypatch):
    from api import dependencies as deps
    monkeypatch.setattr(deps, "get_db", lambda: None)
    r = client.post("/api/v1/online-store/push/all-pending", headers=auth_headers)
    assert r.status_code == 503, r.text


# ---------------------------------------------------------------------------
# SHOPIFY_DISPATCH_MODE decouple (owner 2026-07-05): Shopify can go live
# WITHOUT arming the global DISPATCH_MODE (which also arms WhatsApp/SMS and
# the other NEXUS writes). Unset -> identical to the old behaviour.
# ---------------------------------------------------------------------------


def test_shopify_dispatch_mode_override_and_fallback(monkeypatch):
    from agents import nexus_providers as nx

    # No override -> falls back to the global mode (off).
    monkeypatch.setattr(nx, "dispatch_mode", lambda: "off")
    monkeypatch.delenv("SHOPIFY_DISPATCH_MODE", raising=False)
    assert nx.shopify_dispatch_mode() == "off"
    assert nx._is_shopify_write_allowed() is False

    # Override LIVE while global stays off -> Shopify (and ONLY Shopify) live.
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "live")
    assert nx.shopify_dispatch_mode() == "live"
    assert nx._is_shopify_write_allowed() is True
    assert nx._is_destructive_allowed() is False  # global gate untouched

    # Garbage override -> ignored, falls back to the global mode.
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "banana")
    assert nx.shopify_dispatch_mode() == "off"

    # Override can also force Shopify OFF while the global mode is live.
    monkeypatch.setattr(nx, "dispatch_mode", lambda: "live")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "off")
    assert nx.shopify_dispatch_mode() == "off"
    monkeypatch.delenv("SHOPIFY_DISPATCH_MODE")
    assert nx.shopify_dispatch_mode() == "live"


def test_push_mode_live_via_shopify_override_only(monkeypatch):
    """Full gate: writes on + creds + SHOPIFY_DISPATCH_MODE=live -> LIVE even
    though the global DISPATCH_MODE stays off."""
    from agents import nexus_providers as nx

    monkeypatch.setattr(nx, "dispatch_mode", lambda: "off")
    monkeypatch.setenv("SHOPIFY_DISPATCH_MODE", "live")
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "x.myshopify.com", "access_token": "tok",
                    "source": "oauth"},
    )
    st = shopify_push.push_mode_status(None)
    assert st["is_live"] is True
    assert st["dispatch_mode"] == "live"
    assert st["mode"] == "LIVE"


# ---------------------------------------------------------------------------
# Attribute -> Shopify metafields (owner 2026-07-05 "CREATE WITH METAFIELDS"):
# category attributes push as structured `ims`-namespace metafields --
# planned in the dry-run, metafieldsSet after a LIVE product write.
# ---------------------------------------------------------------------------


def test_build_product_metafields_pure():
    product = {
        "attributes": {
            "frame_material": "Acetate",
            "Temple Length": 145,
            "uv_protection": "UV400",
            "empty": "   ",
            "nested": {"skip": "me"},
            "listy": ["skip"],
            "none": None,
        }
    }
    rows = shopify_push.build_product_metafields(product)
    keys = [r["key"] for r in rows]
    # scalars only, lowercased snake_case, deterministic sorted order
    assert keys == ["frame_material", "temple_length", "uv_protection"]
    assert all(r["namespace"] == "ims" for r in rows)
    assert all(r["type"] == "single_line_text_field" for r in rows)
    assert {r["key"]: r["value"] for r in rows}["temple_length"] == "145"
    # no attributes -> no rows, and non-dict attributes are safe
    assert shopify_push.build_product_metafields({}) == []
    assert shopify_push.build_product_metafields({"attributes": "junk"}) == []


def test_dark_push_plans_metafields_no_network(monkeypatch):
    _force_dark(monkeypatch, "writes_off")
    product = {
        "id": "P1",
        "title": "RB",
        "attributes": {"frame_material": "Metal"},
        "ecom": {"status": "PUBLISHED"},
    }
    res = _run(shopify_push.push_product(_EngineDB(), product, []))
    assert res.mode == "SIMULATED"
    assert res.metafields == [
        {
            "namespace": "ims",
            "key": "frame_material",
            "type": "single_line_text_field",
            "value": "Metal",
        }
    ]


def test_live_push_sets_metafields_after_create(monkeypatch):
    spy = _force_live(
        monkeypatch,
        {
            "data": {
                "productCreate": {
                    "product": {"id": "gid://shopify/Product/222"},
                    "userErrors": [],
                },
                "metafieldsSet": {
                    "metafields": [{"id": "gid://shopify/Metafield/1", "key": "frame_material"},
                                    {"id": "gid://shopify/Metafield/2", "key": "uv_protection"}],
                    "userErrors": [],
                },
            }
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {
            "id": "P1",
            "title": "RB",
            "attributes": {"frame_material": "Metal", "uv_protection": "UV400"},
            "ecom": {"status": "PUBLISHED", "locally_modified": True},
        }
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.mode == "LIVE" and res.ok is True
    # Two network calls: productCreate, then ONE metafieldsSet chunk.
    assert len(spy.calls) == 2
    assert "metafieldsSet" in spy.calls[1]["query"]
    mfs = spy.calls[1]["variables"]["metafields"]
    assert all(m["ownerId"] == "gid://shopify/Product/222" for m in mfs)
    assert sorted(m["key"] for m in mfs) == ["frame_material", "uv_protection"]
    assert res.metafields == {"set": 2, "errors": []}


def test_live_metafield_errors_do_not_fail_the_push(monkeypatch):
    spy = _force_live(
        monkeypatch,
        {
            "data": {
                "productCreate": {
                    "product": {"id": "gid://shopify/Product/333"},
                    "userErrors": [],
                },
                "metafieldsSet": {
                    "metafields": [],
                    "userErrors": [{"field": ["metafields"], "message": "boom"}],
                },
            }
        },
    )
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {
            "id": "P1",
            "title": "RB",
            "attributes": {"frame_material": "Metal"},
            "ecom": {"status": "PUBLISHED"},
        }
    )
    product = db["catalog_products"].find_one({"id": "P1"})
    res = _run(shopify_push.push_product(db, product, []))
    assert res.ok is True  # the product write itself succeeded
    assert res.mode == "LIVE"
    assert res.metafields["set"] == 0
    assert any("boom" in e for e in res.metafields["errors"])
    assert len(spy.calls) == 2


# ---------------------------------------------------------------------------
# Shopify connection hardening (owner 2026-07-05 "make the connection
# stronger"): variant price/barcode push, throttle retries, webhook
# registration. Same gate + fail-soft contracts as the rest of the engine.
# ---------------------------------------------------------------------------


def test_build_variant_price_inputs_pure():
    product = {"mrp": 12990, "offer_price": 10990}
    variants = [
        # mapped + priced: uses its own pricing, mrp > price -> compareAt set
        {"shopify_variant_id": "111", "discounted_price": 8000,
         "compare_at_price": 9000, "gtin": "8056597857239"},
        # mapped, falls back to product pricing (offer 10990 / mrp 12990)
        {"shopify_variant_id": "222"},
        # no gid -> SKIPPED (update-only by design)
        {"discounted_price": 5000},
    ]
    rows, skipped = shopify_push.build_variant_price_inputs(product, variants)
    assert skipped == 1
    assert [r["id"] for r in rows] == [
        "gid://shopify/ProductVariant/111",
        "gid://shopify/ProductVariant/222",
    ]
    assert rows[0]["price"] == "8000.00"
    assert rows[0]["compareAtPrice"] == "9000.00"
    assert rows[0]["barcode"] == "8056597857239"
    assert rows[1]["price"] == "10990.00"
    assert rows[1]["compareAtPrice"] == "12990.00"
    # price == mrp -> compareAtPrice EXPLICIT None (clears stale strikethrough)
    rows2, _ = shopify_push.build_variant_price_inputs(
        {"mrp": 5000}, [{"shopify_variant_id": "9", "discounted_price": 5000,
                         "compare_at_price": 5000}]
    )
    assert rows2[0]["compareAtPrice"] is None
    # nothing priced at all -> skipped, no row
    rows3, skipped3 = shopify_push.build_variant_price_inputs(
        {}, [{"shopify_variant_id": "10"}]
    )
    assert rows3 == [] and skipped3 == 1


def test_push_variant_prices_simulated_plans_no_network(monkeypatch):
    _force_dark(monkeypatch, "writes_off")
    product = {"id": "P1", "mrp": 100, "offer_price": 90,
               "ecom": {"shopify_product_id": "77"}}
    variants = [{"shopify_variant_id": "111"}]
    res = _run(shopify_push.push_variant_prices(_EngineDB(), product, variants))
    assert res.mode == "SIMULATED" and res.ok is True
    assert res.entity == "variant-prices" and res.action == "update"
    assert res.payload["productId"] == "gid://shopify/Product/77"
    assert len(res.payload["variants"]) == 1


def test_push_variant_prices_live_bulk_update(monkeypatch):
    spy = _force_live(monkeypatch, {
        "data": {"productVariantsBulkUpdate": {
            "productVariants": [{"id": "gid://shopify/ProductVariant/111"}],
            "userErrors": [],
        }}
    })
    product = {"id": "P1", "mrp": 100, "offer_price": 90,
               "ecom": {"shopify_product_id": "77"}}
    variants = [{"shopify_variant_id": "111", "gtin": "123"}]
    res = _run(shopify_push.push_variant_prices(_EngineDB(), product, variants))
    assert res.mode == "LIVE" and res.ok is True
    assert len(spy.calls) == 1
    v = spy.calls[0]["variables"]
    assert v["productId"] == "gid://shopify/Product/77"
    assert v["variants"][0]["barcode"] == "123"


def test_push_variant_prices_live_requires_parent_gid(monkeypatch):
    spy = _force_live(monkeypatch, {"data": {}})
    product = {"id": "P1", "mrp": 100, "ecom": {}}  # never pushed yet
    variants = [{"shopify_variant_id": "111", "discounted_price": 90}]
    res = _run(shopify_push.push_variant_prices(_EngineDB(), product, variants))
    assert res.ok is False
    assert "push the product first" in (res.error or "")
    assert spy.calls == []  # no mutation without the parent gid


# --- _graphql retry behaviour (via the _post_once seam) --------------------


class _FakeResp:
    def __init__(self, status, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._body


def _wire_graphql(monkeypatch, responses):
    """Point _graphql at canned _post_once responses; kill real sleeps."""
    calls = {"n": 0}

    async def fake_post_once(url, headers, payload):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        r = responses[i]
        if isinstance(r, Exception):
            raise r
        return r

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(shopify_push, "_post_once", fake_post_once)
    monkeypatch.setattr(shopify_push.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(
        shopify_push, "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "x.myshopify.com", "access_token": "t",
                    "source": "vault"},
    )
    return calls


def test_graphql_retries_429_then_succeeds(monkeypatch):
    ok_body = {"data": {"ok": True}}
    calls = _wire_graphql(monkeypatch, [
        _FakeResp(429, headers={"Retry-After": "0"}),
        _FakeResp(200, body=ok_body),
    ])
    body = _run(shopify_push._graphql(None, "query { x }", {}))
    assert body == ok_body
    assert calls["n"] == 2


def test_graphql_does_not_retry_plain_4xx(monkeypatch):
    calls = _wire_graphql(monkeypatch, [_FakeResp(401, text="bad token")])
    with pytest.raises(ValueError):
        _run(shopify_push._graphql(None, "query { x }", {}))
    assert calls["n"] == 1  # immediate failure, no retry


def test_graphql_gives_up_after_max_retries(monkeypatch):
    calls = _wire_graphql(monkeypatch, [_FakeResp(429)])
    with pytest.raises(ValueError):
        _run(shopify_push._graphql(None, "query { x }", {}))
    assert calls["n"] == shopify_push._MAX_RETRIES


def test_graphql_retries_graphql_throttled_body(monkeypatch):
    throttled = {"errors": [{"extensions": {"code": "THROTTLED"}}]}
    ok_body = {"data": {"ok": True}}
    calls = _wire_graphql(monkeypatch, [
        _FakeResp(200, body=throttled),
        _FakeResp(200, body=ok_body),
    ])
    body = _run(shopify_push._graphql(None, "query { x }", {}))
    assert body == ok_body and calls["n"] == 2


# --- register_webhooks ------------------------------------------------------


def test_register_webhooks_dark_makes_no_network_call(monkeypatch):
    _force_dark(monkeypatch, "writes_off")  # _graphql explodes if touched
    res = _run(shopify_push.register_webhooks(
        _EngineDB(), "https://api.example.com", topics=["orders/create"]
    ))
    assert res["mode"] == "SIMULATED" and res["ok"] is True
    assert res["callback_url"] == "https://api.example.com/api/v1/webhooks/shopify"
    assert res["missing"] == ["ORDERS_CREATE"]
    assert res["applied"] is False


def test_register_webhooks_rejects_non_https(monkeypatch):
    _force_dark(monkeypatch, "writes_off")
    res = _run(shopify_push.register_webhooks(_EngineDB(), "http://insecure"))
    assert res["ok"] is False
    assert any("https" in e for e in res["errors"])


def test_register_webhooks_apply_creates_only_missing(monkeypatch):
    cb = "https://api.example.com/api/v1/webhooks/shopify"
    spy = _force_live(monkeypatch, {
        "data": {
            "webhookSubscriptions": {"edges": [
                # orders/create ALREADY at our URL -> not recreated
                {"node": {"id": "gid://shopify/WebhookSubscription/1",
                          "topic": "ORDERS_CREATE",
                          "endpoint": {"__typename": "WebhookHttpEndpoint",
                                       "callbackUrl": cb}}},
                # same family topic still pointed at BVI -> conflict surfaced
                {"node": {"id": "gid://shopify/WebhookSubscription/2",
                          "topic": "ORDERS_UPDATED",
                          "endpoint": {"__typename": "WebhookHttpEndpoint",
                                       "callbackUrl": "https://old-bvi.app/hook"}}},
            ]},
            "webhookSubscriptionCreate": {
                "webhookSubscription": {"id": "gid://shopify/WebhookSubscription/9",
                                        "topic": "ORDERS_UPDATED"},
                "userErrors": [],
            },
        }
    })
    res = _run(shopify_push.register_webhooks(
        _EngineDB(), "https://api.example.com",
        topics=["orders/create", "orders/updated"], apply=True,
    ))
    assert res["ok"] is True and res["applied"] is True
    assert res["already_registered"] == ["ORDERS_CREATE"]
    assert res["missing"] == ["ORDERS_UPDATED"]
    assert [c["topic"] for c in res["created"]] == ["ORDERS_UPDATED"]
    assert len(res["conflicts"]) == 1  # the BVI-pointed sub is surfaced
    # 1 query + 1 create (only the missing topic)
    assert len(spy.calls) == 2
