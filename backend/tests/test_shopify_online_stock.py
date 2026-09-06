"""
Make the website's QUANTITIES real (owner ruling 2026-09-07, sync-audit gap #1)
==============================================================================
Measured on prod the day before: every IMS-pushed product on Shopify was
``inventoryItem.tracked = false``, quantity 0 everywhere, policy allowing the
sale -- the website sold what the shops may not have -- and the old stock
write-back was DEAD (location env unset, stale vault token, no hook).

Pinned here, each with discriminating power (revert the named piece and the
test fails):

  1. LOCATION: pinned env wins with no network; a persisted registry row wins
     with no network; ONE `locations` lookup picks the single active
     online-fulfilling location and PERSISTS it; two candidates -> the one
     named after the online store's city, else a REFUSAL with
     ONLINE_LOCATION_AMBIGUOUS; none -> ONLINE_LOCATION_UNRESOLVED. Never a guess.
  2. THE ONE QUANTITY RULE: pooled AVAILABLE units across PHYSICAL stores
     (never the online store's phantom row, never a SOLD unit) minus the
     buffer -- online_stock_writeback.online_quantities_for_skus.
  3. PUSH: a LIVE create AND a LIVE update set tracked=true + inventoryPolicy
     DENY on the variant and write the pooled quantity at the resolved
     location (mocked _graphql transcript); ecom.online_stock records what was
     sent; allow_oversell -> CONTINUE.
  4. sync_stock_levels: only gid'd products; only those whose number CHANGED
     since the last send; a clean second run is a noop with ZERO network;
     STRICT when on-hand is unknown (never writes 0).
  5. DARK: the product push's stock plan and sync_stock_levels are SIMULATED
     with zero network.
  6. INBOUND: a unit sold through the item_events ledger (the same CAS the
     online-order claim path lands on) reduces the pooled number; the ingest
     tripwire proves the online order still claims units + re-sends.
  7. The route + rbac row + the revived nexus setter.

Every Shopify call is MOCKED at shopify_push._graphql. No network, no Mongo.
Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_shopify_online_stock.py -q
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402
from api.services import item_events  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402
from api.services import shopify_auth  # noqa: E402
from agents import nexus_providers  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

LOC = "gid://shopify/Location/77"
PRODUCT_GID = "gid://shopify/Product/111"
VARIANT_GID = "gid://shopify/ProductVariant/5"
INV_GID = "gid://shopify/InventoryItem/9"
ONLINE_LOC = {"id": LOC, "name": "Ranchi Online", "isActive": True, "fulfillsOnlineOrders": True}
SHOP_LOC = {"id": "gid://shopify/Location/1", "name": "Pune Shop", "isActive": True, "fulfillsOnlineOrders": False}


def _run(coro):
    return asyncio.run(coro)


async def _explode(db, query, variables):  # noqa: ARG001
    raise AssertionError("shopify_push._graphql reached -- a DARK path made a network call")


class _Spy:
    """Per-mutation canned bodies (longest marker first) + a full transcript."""

    def __init__(self, responses):
        self.calls = []
        self._responses = responses

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append({"query": query, "variables": variables})
        for marker, body in sorted(self._responses.items(), key=lambda kv: -len(kv[0])):
            if marker in query:
                return body
        return {"data": {}}

    def calls_for(self, marker):
        return [c for c in self.calls if marker in c["query"]]


def _locations(*nodes):
    return {"data": {"locations": {"nodes": list(nodes)}}}


def _ok_body(field, **extra):
    return {"data": {field: {"userErrors": [], **extra}}}


def _product_body(field):
    return {
        "data": {
            field: {
                "product": {
                    "id": PRODUCT_GID,
                    "handle": "frame-1",
                    "variants": {
                        "nodes": [
                            {
                                "id": VARIANT_GID,
                                "title": "Default Title",
                                "selectedOptions": [],
                                "inventoryItem": {"id": INV_GID},
                            }
                        ]
                    },
                    "media": {"nodes": [{"id": "gid://shopify/MediaImage/1"}]},
                },
                "userErrors": [],
            }
        }
    }


def _responses(*locs):
    return {
        "productCreate(": _product_body("productCreate"),
        "productUpdate(": _product_body("productUpdate"),
        "productVariantsBulkUpdate": _ok_body("productVariantsBulkUpdate", productVariants=[]),
        "productVariantsBulkCreate": _ok_body("productVariantsBulkCreate", productVariants=[]),
        "locations(": _locations(*locs),
        "inventorySetQuantities": _ok_body(
            "inventorySetQuantities", inventoryAdjustmentGroup={"createdAt": "now", "reason": "correction"}
        ),
        "publications(": {"data": {"publications": {"nodes": [{"id": "gid://shopify/Publication/1", "name": "Online Store"}]}}},
        "publishablePublish": _ok_body("publishablePublish"),
        "metafieldsSet": _ok_body("metafieldsSet", metafields=[]),
    }


def _live(monkeypatch, spy):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", spy)


def _dark(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _explode)


def _catalog_row(pid, sku, *, gid=True, online_stock=None, **ecom_extra):
    ecom = {
        "status": "DRAFT",
        "shopify_variant_id": VARIANT_GID if gid else None,
        "shopify_inventory_item_id": INV_GID if gid else None,
        **ecom_extra,
    }
    if gid:
        ecom["shopify_product_id"] = PRODUCT_GID
    if online_stock is not None:
        ecom["online_stock"] = online_stock
    return {
        "id": pid,
        "sku": sku,
        "name": "Frame " + sku,
        "price": 1500,
        "mrp": 1500,
        "images": ["https://cdn.example.com/p.jpg"],
        "ecom": ecom,
    }


def _db(*, on_hand=3, sold=1, sku="SP-1", stored_location=None):
    """A StrictDB with ONE spine product: `on_hand` AVAILABLE units in a shop,
    `sold` SOLD units, and one phantom AVAILABLE unit parked on the online
    store (must never be counted)."""
    db = StrictDB()
    db.seed(
        "stores",
        [
            {"store_id": "BV-RANCHI", "name": "Better Vision Ranchi", "city": "Ranchi", "store_type": "RETAIL"},
            {"store_id": "BV-ONLINE-01", "name": "Better Vision Online", "city": "Ranchi", "store_type": "ONLINE"},
        ],
    )
    db.seed("products", [{"product_id": "spine-1", "sku": sku}])
    units = [
        {"stock_id": f"u{i}", "product_id": "spine-1", "store_id": "BV-RANCHI", "status": "AVAILABLE"}
        for i in range(on_hand)
    ]
    units += [
        {"stock_id": f"s{i}", "product_id": "spine-1", "store_id": "BV-RANCHI", "status": "SOLD"}
        for i in range(sold)
    ]
    units.append({"stock_id": "phantom", "product_id": "spine-1", "store_id": "BV-ONLINE-01", "status": "AVAILABLE"})
    db.seed("stock_units", units)
    row = {"storefront_id": "BV", "name": "Better Vision", "is_default": True}
    if stored_location:
        row["online_location_id"] = stored_location
    db.seed("storefronts", [row])
    return db


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    shopify_push._online_location_cache.clear()
    shopify_push._publication_id_cache.clear()
    for k in ("SHOPIFY_ONLINE_LOCATION_ID", "ONLINE_STOCK_SAFETY_BUFFER", "SHOPIFY_ONLINE_STORE_PUBLICATION_ID"):
        monkeypatch.delenv(k, raising=False)
    yield
    shopify_push._online_location_cache.clear()
    shopify_push._publication_id_cache.clear()


# ---------------------------------------------------------------------------
# 1. location resolution
# ---------------------------------------------------------------------------


def test_pick_online_location_is_pure_and_never_guesses():
    pick = shopify_push.pick_online_location
    assert pick([], [])[1] == shopify_push.ONLINE_LOCATION_UNRESOLVED
    assert pick([SHOP_LOC], [])[1] == shopify_push.ONLINE_LOCATION_UNRESOLVED
    inactive = {**ONLINE_LOC, "isActive": False}
    assert pick([inactive], [])[1] == shopify_push.ONLINE_LOCATION_UNRESOLVED
    node, code, _ = pick([SHOP_LOC, ONLINE_LOC], [])
    assert node is ONLINE_LOC and code is None
    other = {"id": "gid://shopify/Location/2", "name": "Pune Warehouse", "isActive": True, "fulfillsOnlineOrders": True}
    node, code, _ = pick([ONLINE_LOC, other], ["ranchi"])
    assert node is ONLINE_LOC and code is None
    node, code, err = pick([ONLINE_LOC, other], ["mumbai"])
    assert node is None and code == shopify_push.ONLINE_LOCATION_AMBIGUOUS
    assert "SHOPIFY_ONLINE_LOCATION_ID" in err


def test_pinned_env_wins_with_no_network(monkeypatch):
    monkeypatch.setenv("SHOPIFY_ONLINE_LOCATION_ID", "123")
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    out = _run(shopify_push.resolve_online_location_id(_db()))
    assert out == {"location_id": "gid://shopify/Location/123", "source": "pinned"}


def test_stored_registry_row_wins_with_no_network(monkeypatch):
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    out = _run(shopify_push.resolve_online_location_id(_db(stored_location=LOC)))
    assert out == {"location_id": LOC, "source": "stored"}


def test_lookup_picks_the_single_online_location_and_persists_it(monkeypatch):
    db = _db()
    spy = _Spy({"locations(": _locations(SHOP_LOC, ONLINE_LOC)})
    monkeypatch.setattr(shopify_push, "_graphql", spy)
    out = _run(shopify_push.resolve_online_location_id(db))
    assert out["location_id"] == LOC and out["source"] == "looked_up"
    assert len(spy.calls) == 1
    row = db.get_collection("storefronts").find_one({"storefront_id": "BV"})
    assert row["online_location_id"] == LOC and row["online_location_name"] == "Ranchi Online"
    # Second ask: the cache answers, the network is never touched again.
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    assert _run(shopify_push.resolve_online_location_id(db))["location_id"] == LOC
    # And the sync-side reader the POS write-back uses sees the same answer.
    from api.services.online_catalog import _online_location_id

    assert _online_location_id(db) == LOC


def test_two_candidates_prefer_the_online_stores_city(monkeypatch):
    db = _db()  # the online store row says city Ranchi
    pune = {"id": "gid://shopify/Location/2", "name": "Pune Warehouse", "isActive": True, "fulfillsOnlineOrders": True}
    monkeypatch.setattr(shopify_push, "_graphql", _Spy({"locations(": _locations(pune, ONLINE_LOC)}))
    assert _run(shopify_push.resolve_online_location_id(db))["location_id"] == LOC


def test_two_unhinted_candidates_are_refused_and_nothing_persisted(monkeypatch):
    db = _db()
    a = {"id": "gid://shopify/Location/2", "name": "Warehouse A", "isActive": True, "fulfillsOnlineOrders": True}
    b = {"id": "gid://shopify/Location/3", "name": "Warehouse B", "isActive": True, "fulfillsOnlineOrders": True}
    monkeypatch.setattr(shopify_push, "_graphql", _Spy({"locations(": _locations(a, b)}))
    out = _run(shopify_push.resolve_online_location_id(db))
    assert out["location_id"] is None and out["code"] == shopify_push.ONLINE_LOCATION_AMBIGUOUS
    assert "online_location_id" not in db.get_collection("storefronts").find_one({"storefront_id": "BV"})
    assert shopify_push._online_location_cache == {}


def test_no_online_location_is_refused(monkeypatch):
    monkeypatch.setattr(shopify_push, "_graphql", _Spy({"locations(": _locations(SHOP_LOC)}))
    out = _run(shopify_push.resolve_online_location_id(_db()))
    assert out["code"] == shopify_push.ONLINE_LOCATION_UNRESOLVED and out["location_id"] is None


def test_push_mode_status_reports_the_location_without_network(monkeypatch):
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    status = shopify_push.push_mode_status(_db(stored_location=LOC))
    assert status["online_location_id"] == LOC and status["online_location_source"] == "stored"
    shopify_push._online_location_cache.clear()  # a fresh worker, no registry row
    status = shopify_push.push_mode_status(_db())
    assert status["online_location_id"] is None and status["online_location_source"] == "unresolved"


# ---------------------------------------------------------------------------
# 2. the one quantity rule
# ---------------------------------------------------------------------------


def test_online_quantity_is_pooled_physical_available_minus_buffer():
    db = _db(on_hand=3, sold=1)  # + a phantom AVAILABLE unit on BV-ONLINE-01
    assert wb.online_quantities_for_skus(db, ["SP-1"]) == {"SP-1": 3}
    assert wb.online_quantities_for_skus(db, ["SP-1"], safety_buffer=1) == {"SP-1": 2}
    assert wb.online_quantities_for_skus(db, ["SP-1"], safety_buffer=9) == {"SP-1": 0}
    # UNKNOWN sku -> absent (never 0); no spine at all -> {} (STRICT).
    assert wb.online_quantities_for_skus(db, ["NOPE"]) == {}
    assert wb.online_quantities_for_skus(StrictDB(), ["SP-1"]) == {}


# ---------------------------------------------------------------------------
# 3. the product push writes stock
# ---------------------------------------------------------------------------


def _assert_stock_written(spy, expected_qty, policy="DENY"):
    tracking = [
        c for c in spy.calls_for("productVariantsBulkUpdate")
        if any("inventoryPolicy" in row for row in c["variables"]["variants"])
    ]
    assert len(tracking) == 1, "exactly one tracking/policy update"
    rows = tracking[0]["variables"]["variants"]
    assert tracking[0]["variables"]["productId"] == PRODUCT_GID
    assert {r["id"] for r in rows} == {VARIANT_GID}
    assert all(r["inventoryPolicy"] == policy and r["inventoryItem"] == {"tracked": True} for r in rows)
    sets = spy.calls_for("inventorySetQuantities")
    assert len(sets) == 1
    inp = sets[0]["variables"]["input"]
    assert inp["name"] == "available" and inp["ignoreCompareQuantity"] is True
    assert inp["quantities"] == [{"inventoryItemId": INV_GID, "locationId": LOC, "quantity": expected_qty}]


def test_live_create_tracks_denies_and_writes_pooled_quantity(monkeypatch):
    db = _db(on_hand=3)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=False)])
    spy = _Spy(_responses(ONLINE_LOC))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.mode == "LIVE" and res.action == "create", res
    _assert_stock_written(spy, 3)
    assert res.stock["ok"] is True and res.stock["quantities"] == {"SP-1": 3}
    assert res.stock["location_id"] == LOC and res.stock["tracked"] == 1
    # The stock step ran BEFORE the publish, so the listing never went visible untracked.
    order = [next(m for m in ("inventorySetQuantities", "publishablePublish") if m in c["query"])
             for c in spy.calls if "inventorySetQuantities" in c["query"] or "publishablePublish" in c["query"]]
    assert order == ["inventorySetQuantities", "publishablePublish"]
    doc = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    assert doc["ecom"]["online_stock"]["quantities"] == {"SP-1": 3}
    assert doc["ecom"]["online_stock"]["tracked"] is True
    assert doc["ecom"]["online_stock"]["location_id"] == LOC
    assert doc["ecom"]["locally_modified"] is False  # the stock write-back never re-queues


def test_live_update_also_writes_stock(monkeypatch):
    db = _db(on_hand=2)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True)])
    spy = _Spy(_responses(ONLINE_LOC))
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.action == "update" and res.mode == "LIVE"
    _assert_stock_written(spy, 2)
    assert res.stock["quantities"] == {"SP-1": 2}


def test_allow_oversell_flag_selects_continue(monkeypatch):
    assert shopify_push.inventory_policy_for({"ecom": {}}) == "DENY"
    assert shopify_push.inventory_policy_for({"ecom": {"allow_oversell": True}}) == "CONTINUE"
    db = _db(on_hand=2)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True, allow_oversell=True)])
    spy = _Spy(_responses(ONLINE_LOC))
    _live(monkeypatch, spy)
    _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    _assert_stock_written(spy, 2, policy="CONTINUE")


def test_live_push_with_unresolvable_location_writes_nothing_and_says_why(monkeypatch):
    db = _db(on_hand=2)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True)])
    spy = _Spy(_responses(SHOP_LOC))  # no online-fulfilling location
    _live(monkeypatch, spy)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert spy.calls_for("inventorySetQuantities") == []
    assert res.stock["ok"] is False and res.stock["code"] == shopify_push.ONLINE_LOCATION_UNRESOLVED
    assert "online_stock" not in db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]


def test_dark_push_plans_stock_with_zero_network(monkeypatch):
    db = _db(on_hand=3, stored_location=LOC)
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True)])
    _dark(monkeypatch)
    res = _run(shopify_push.push_product(db, db.get_collection("catalog_products").find_one({"id": "cat-1"}), []))
    assert res.mode == "SIMULATED"
    assert res.stock == {
        "tracked": True,
        "policy": "DENY",
        "quantities": {"SP-1": 3},
        "location_id": LOC,
        "location_source": "stored",
    }


# ---------------------------------------------------------------------------
# 4 + 5. sync_stock_levels
# ---------------------------------------------------------------------------


def _two_products_db():
    db = _db(on_hand=3)
    db.seed("products", [{"product_id": "spine-2", "sku": "SP-2"}, {"product_id": "spine-3", "sku": "SP-3"}])
    db.seed(
        "stock_units",
        [
            {"stock_id": "b1", "product_id": "spine-2", "store_id": "BV-RANCHI", "status": "AVAILABLE"},
            {"stock_id": "c1", "product_id": "spine-3", "store_id": "BV-RANCHI", "status": "AVAILABLE"},
        ],
    )
    db.seed(
        "catalog_products",
        [
            # A: on Shopify, never sent -> changed.
            _catalog_row("cat-1", "SP-1", gid=True),
            # B: on Shopify, last send equals today's number and tracked -> unchanged.
            _catalog_row("cat-2", "SP-2", gid=True, online_stock={"quantities": {"SP-2": 1}, "tracked": True}),
            # C: NOT on Shopify -> never a candidate.
            _catalog_row("cat-3", "SP-3", gid=False),
        ],
    )
    return db


def test_sync_stock_levels_pushes_only_changed_gid_products_then_noops(monkeypatch):
    db = _two_products_db()
    spy = _Spy(_responses(ONLINE_LOC))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.mode == "LIVE" and res.ok is True and res.action == "sync", res
    assert res.payload["candidates"] == 2 and res.payload["changed"] == 1 and res.payload["synced"] == 1
    assert res.payload["location_id"] == LOC
    _assert_stock_written(spy, 3)  # only A (SP-1 = 3), never B's SP-2 or C's SP-3
    doc = db.get_collection("catalog_products").find_one({"id": "cat-1"})
    assert doc["ecom"]["online_stock"]["quantities"] == {"SP-1": 3}
    # Clean second run: nothing changed -> noop, ZERO network.
    monkeypatch.setattr(shopify_push, "_graphql", _explode)
    again = _run(shopify_push.sync_stock_levels(db))
    assert again.action == "noop" and again.ok is True and again.payload["changed"] == 0


def test_sync_stock_levels_resends_after_a_sale_changes_the_number(monkeypatch):
    db = _two_products_db()
    spy = _Spy(_responses(ONLINE_LOC))
    _live(monkeypatch, spy)
    _run(shopify_push.sync_stock_levels(db))
    spy.calls.clear()
    # A unit of SP-1 sells -- the guarded status flip the POS claim door and
    # the online-order claim door both perform (product_repository
    # claim_one_available / mark_sold: find_one_and_update on AVAILABLE).
    flipped = db.get_collection("stock_units").find_one_and_update(
        {"stock_id": "u0", **item_events.on_hand_match()}, {"$set": {"status": "SOLD"}}
    )
    assert flipped is not None
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.payload["changed"] == 1
    inp = spy.calls_for("inventorySetQuantities")[0]["variables"]["input"]
    assert inp["quantities"][0]["quantity"] == 2


def test_sync_stock_levels_dark_is_simulated_with_zero_network(monkeypatch):
    db = _two_products_db()
    _dark(monkeypatch)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.mode == "SIMULATED" and res.ok is True and res.action == "sync"
    assert res.payload["changed"] == 1 and res.payload["plan"] == [{"product_id": "cat-1", "quantities": {"SP-1": 3}}]
    assert "online_stock" not in db.get_collection("catalog_products").find_one({"id": "cat-1"})["ecom"]


def test_sync_stock_levels_is_strict_when_on_hand_is_unknown(monkeypatch):
    db = StrictDB()
    db.seed("catalog_products", [_catalog_row("cat-1", "SP-1", gid=True)])  # no spine, no units
    spy = _Spy(_responses(ONLINE_LOC))
    _live(monkeypatch, spy)
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok is False and res.code == shopify_push.STOCK_ONHAND_UNKNOWN
    assert spy.calls == []


# ---------------------------------------------------------------------------
# 6. inbound: the pooled number follows the same units the online order claims
# ---------------------------------------------------------------------------


def test_inbound_online_order_path_claims_units_and_resends():
    """The Shopify order ingest already reduces the pool through the ONE claim
    path (orders._mark_units_sold) and re-sends via writeback_after_sale --
    this tripwire keeps both hooks in place."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "api", "services", "shopify_ingest.py"), encoding="utf-8").read()
    assert "_mark_units_sold(" in src
    assert "writeback_after_sale(" in src


def test_pos_writeback_uses_the_one_quantity_rule(monkeypatch):
    """writeback_skus (POS sale / return / ingest hook) sends the SAME number
    the sync sends: revert it to its own math and this fails."""
    db = _db(on_hand=3)
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "1")
    monkeypatch.setattr(nexus_providers, "dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_auth, "resolve_shopify_credentials",
                        lambda db, storefront_id="BV": {"shop_url": "t.myshopify.com", "access_token": "tok"})
    spy = _Spy(_responses(ONLINE_LOC))
    monkeypatch.setattr(shopify_push, "_graphql", spy)
    from api.services import online_catalog

    monkeypatch.setattr(
        online_catalog,
        "online_variant_targets_for_skus",
        lambda db, skus: {"SP-1": {"inventory_item_id": INV_GID, "location_id": LOC}},
    )
    summary = _run(wb.writeback_skus(db, ["SP-1"], "BV-RANCHI", safety_buffer=1))
    assert summary["pushed"] == 1 and summary["failed"] == 0, summary
    inp = spy.calls_for("inventorySetQuantities")[0]["variables"]["input"]
    assert inp["quantities"] == [{"inventoryItemId": INV_GID, "locationId": LOC, "quantity": 2}]


# ---------------------------------------------------------------------------
# 7. route, rbac row, package surface
# ---------------------------------------------------------------------------


def test_stock_route_is_catalogued_admin_superadmin_only():
    entry = rbac.policy_for("POST", "/api/v1/online-store/push/stock")
    assert entry is not None
    assert set(entry["allowed"]) == {"ADMIN", "SUPERADMIN"}


def test_stock_route_is_mounted_and_sweep_carries_stock():
    from api.main import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/v1/online-store/push/stock" in paths
    src = open(os.path.join(os.path.dirname(__file__), "..", "api", "routers", "online_store_push.py"), encoding="utf-8").read()
    assert "sync_stock_levels(db)" in src and '"stock": stock' in src


def test_package_exports_and_patch_forwarding():
    assert shopify_push.inventory in shopify_push._SUBMODULES
    for name in ("sync_stock_levels", "sync_product_stock", "resolve_online_location_id", "set_inventory_quantities"):
        assert callable(getattr(shopify_push, name))
