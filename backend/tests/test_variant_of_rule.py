"""
The VARIANT-OF rule (owner ruling 2026-09-06)
=============================================
IMS keeps ONE product per SKU, so a size variant of a style (the Ray-Ban Meta
"Large") is its own spine product -- own SKU, own stock, own POS sale -- but
on Shopify it is a VARIANT on the parent's listing. The rule: a variant-of
product pushes ONLY its own price, barcode and stock, through its
catalog_variants row on the PARENT's listing, and NEVER a listing-level field
(title, description, media, tags, status, options). It never owns a listing.

Pinned here, each with MEASURED discriminating power (the named revert turns
the test red -- table in the PR body):

  1. a child push is refused with ZERO network even with the LIVE gates on;
     the route returns the same refusal and writes one audit row
  2. the parent's stock pass carries the child SKU (one tracking call on the
     parent's productId with BOTH variant gids, one quantity write with both
     inventory items, the ledger on the PARENT twin); the gid door ignores a
     child even if a repair script stamps the parent gid on its twin
  3. the child's OWN price and barcode ride the parent's price push (the row
     minted by the create door carries the child's mrp / gtin); an mrp edit
     lands on the row, the engine re-derives the online price (never the
     stale discounted_price) and the PARENT is queued
  4. deactivating a child DENIES + zeroes ITS variant on the parent's listing
     -- never productUpdate; the parent twin is untouched; the take-down
     route on a child REPORTS with zero network
  5. an inactive child lists 0 (a missing flag is active); flip off -> flip
     on with NO stock pass between -> the next pass re-sends 1 (the delist
     zeroed the parent's ledger)
  6. media and title are hands-off: a parent push never names the child's
     photo; a child rename queues nothing; a child mrp edit queues the PARENT
  7. the live sync never selects a child (dirty by hand -> not in the queue,
     awaiting_first_publish 0, pending 0 on both counters, column OFF)
  8. the ONE create door mints the child: spine variant_of / size / identity
     key, twin ecom.variant_of + born clean + no gid, row parent-linked with
     option_size / mrp / gtin, name distinct from the parent; the same
     payload again is a 409; without an explicit sku the mint ends LARGE
     (why the runbook passes the -L sku); unknown parent / other category /
     parent-is-a-child are 422s
  9. _needs_repair is True until the child row has gids (pins the runbook's
     create -> stamp order); a parent update never re-sends productOptions

Every Shopify call is a spy or a raiser. No network, no Mongo.
Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_variant_of_rule.py -q
"""

import asyncio
import copy
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402
from api import dependencies as deps  # noqa: E402
from api.routers import online_store_push as osp  # noqa: E402
from api.services import online_delist  # noqa: E402
from api.services import online_stock_writeback as wb  # noqa: E402
from api.services import policy_engine as pe  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.online_catalog import catalog_counts, product_online_state  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.catalog_variant_repository import (  # noqa: E402
    CatalogVariantRepository,
)
from database.repositories.product_repository import ProductRepository  # noqa: E402

# ---------------------------------------------------------------------------
# constants + the world
# ---------------------------------------------------------------------------

LOC = "gid://shopify/Location/77"
P_GID = "gid://shopify/Product/10153760784633"
A_GID = "gid://shopify/ProductVariant/48728353112313"
A_INV = "gid://shopify/InventoryItem/50819646095609"
B_GID = "gid://shopify/ProductVariant/48729277726969"
B_INV = "gid://shopify/InventoryItem/50820574413049"
PARENT_SKU = "SMTFRRAYBANMETAWAYFARERSHINYBLACKG15601/71"
CHILD_SKU = PARENT_SKU + "-L"
CHILD_GTIN = "8901234567890"  # valid EAN-13, not a GS1 20-29 internal code
PUNE = "4dc49c44-08a1-46e1-85fb-8b7eca55f560"
PARENT_NAME = "Ray-Ban Meta Wayfarer Shiny Black G15 Wayfarer Smart Glasses - Shiny"
CHILD_NAME = PARENT_NAME + " - Large"
PARENT_PHOTO = "https://cdn.example.com/parent.jpg"
CHILD_PHOTO = "https://cdn.example.com/child-only.jpg"
ADMIN = {"user_id": "u-admin", "roles": ["ADMIN"], "username": "admin"}


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
        self.calls.append({"query": query, "variables": copy.deepcopy(variables)})
        for marker, body in sorted(self._responses.items(), key=lambda kv: -len(kv[0])):
            if marker in query:
                return body
        return {"data": {}}

    def calls_for(self, marker):
        return [c for c in self.calls if marker in c["query"]]


def _ok(field, **extra):
    return {"data": {field: {"userErrors": [], **extra}}}


def _product_body(field):
    return {
        "data": {
            field: {
                "product": {
                    "id": P_GID,
                    "handle": "meta-wayfarer",
                    "tags": [],
                    "variants": {
                        "nodes": [
                            {"id": A_GID, "title": "Standard", "selectedOptions": [{"name": "Size", "value": "Standard"}], "inventoryItem": {"id": A_INV}},
                            {"id": B_GID, "title": "Large", "selectedOptions": [{"name": "Size", "value": "Large"}], "inventoryItem": {"id": B_INV}},
                        ]
                    },
                    "media": {"nodes": [{"id": "gid://shopify/MediaImage/1"}]},
                },
                "userErrors": [],
            }
        }
    }


def _responses():
    return {
        "productCreate(": _product_body("productCreate"),
        "productUpdate(": _product_body("productUpdate"),
        "productVariantsBulkUpdate": _ok("productVariantsBulkUpdate", productVariants=[]),
        "productVariantsBulkCreate": _ok("productVariantsBulkCreate", productVariants=[]),
        "inventorySetQuantities": _ok("inventorySetQuantities", inventoryAdjustmentGroup={"createdAt": "now"}),
        "publications(": {"data": {"publications": {"nodes": [{"id": "gid://shopify/Publication/1", "name": "Online Store"}]}}},
        "publishablePublish": _ok("publishablePublish"),
        "metafieldsSet": _ok("metafieldsSet", metafields=[]),
    }


def _live(monkeypatch, spy):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", spy)


def _dark(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "_graphql", _explode)


class _Conn:
    """The dependencies.get_db() shape: is_connected + .db + get_collection."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]


def _wire(monkeypatch, db):
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(deps, "get_audit_repository", lambda: AuditRepository(db["audit_logs"]))


def _parent_spine():
    return {
        "product_id": "sp-parent",
        "sku": PARENT_SKU,
        "pim_product_id": "tw-parent",
        "category": "SMARTGLASSES",
        "brand": "Ray-Ban",
        "model": "Meta Wayfarer Shiny Black G15",
        "name": PARENT_NAME,
        "mrp": 39900.0,
        "offer_price": 39900.0,
        "is_active": True,
        "attributes": {"brand_name": "Ray-Ban", "model_name": "Meta Wayfarer Shiny Black G15", "colour_code": "601/71"},
        "identity_key": "rayban|meta wayfarer shiny black g15|601 71",
    }


def _child_spine(active=True):
    return {
        "product_id": "sp-child",
        "sku": CHILD_SKU,
        "pim_product_id": "tw-child",
        "category": "SMARTGLASSES",
        "brand": "Ray-Ban",
        "model": "Meta Wayfarer Shiny Black G15",
        "name": CHILD_NAME,
        "size": "Large",
        "mrp": 45700.0,
        "offer_price": 45700.0,
        "is_active": active,
        "variant_of": "sp-parent",
        "attributes": {"brand_name": "Ray-Ban", "model_name": "Meta Wayfarer Shiny Black G15", "colour_code": "601/71", "size": "Large", "gtin": CHILD_GTIN},
    }


def _child_link():
    return {"product_id": "sp-parent", "twin_id": "tw-parent", "sku": PARENT_SKU}


def _world(*, seed_child=True, child_active=True, ledger=None, child_gids=True):
    """Parent LIVE on Shopify (gid P, its own row A / inv A), child spine +
    twin + parent-linked row (gid B / inv B, mrp 45700 vs parent 39900), one
    AVAILABLE unit each at a RETAIL store, a phantom unit on the online
    store (never counted), the online location pinned by env."""
    db = StrictDB()
    db.seed(
        "stores",
        [
            {"store_id": PUNE, "store_code": "BV-PUN-01", "name": "Better Vision Pune", "city": "Pune", "store_type": "RETAIL"},
            {"store_id": "BV-ONLINE-01", "name": "Better Vision Online", "city": "Ranchi", "store_type": "ONLINE"},
        ],
    )
    db.seed("storefronts", [{"storefront_id": "BV", "name": "Better Vision", "is_default": True}])
    products = [_parent_spine()]
    twins = [
        {
            "id": "tw-parent",
            "sku": PARENT_SKU,
            "parent_sku": PARENT_SKU,
            "name": PARENT_NAME,
            "title": PARENT_NAME,
            "category": "SMARTGLASSES",
            "brand": "Ray-Ban",
            "mrp": 39900.0,
            "offer_price": 39900.0,
            "images": [PARENT_PHOTO],
            "attributes": {"brand_name": "Ray-Ban"},
            "ecom": {
                "status": "PUBLISHED",
                "shopify_product_id": P_GID,
                "shopify_variant_id": A_GID,
                "shopify_inventory_item_id": A_INV,
                "locally_modified": False,
                **({"online_stock": ledger} if ledger is not None else {}),
            },
        }
    ]
    rows = [
        {"variant_id": "v-a", "sku": PARENT_SKU, "parent_product_id": "tw-parent", "parent_sku": PARENT_SKU,
         "shopify_variant_id": A_GID, "shopify_inventory_item_id": A_INV},
    ]
    units = [
        {"stock_id": "u-p", "product_id": "sp-parent", "store_id": PUNE, "status": "AVAILABLE"},
        {"stock_id": "u-phantom", "product_id": "sp-parent", "store_id": "BV-ONLINE-01", "status": "AVAILABLE"},
    ]
    if seed_child:
        products.append(_child_spine(child_active))
        twins.append(
            {
                "id": "tw-child",
                "sku": CHILD_SKU,
                "parent_sku": CHILD_SKU,
                "name": CHILD_NAME,
                "title": CHILD_NAME,
                "category": "SMARTGLASSES",
                "brand": "Ray-Ban",
                "mrp": 45700.0,
                "offer_price": 45700.0,
                "images": [CHILD_PHOTO],
                "attributes": {"brand_name": "Ray-Ban", "size": "Large"},
                "ecom": {"status": "DRAFT", "locally_modified": False, "variant_of": _child_link()},
            }
        )
        row = {"variant_id": "v-b", "sku": CHILD_SKU, "parent_product_id": "tw-parent", "parent_sku": PARENT_SKU,
               "option_size": "Large", "mrp": 45700.0, "gtin": CHILD_GTIN}
        if child_gids:
            row.update({"shopify_variant_id": B_GID, "shopify_inventory_item_id": B_INV})
        rows.append(row)
        units.append({"stock_id": "u-c", "product_id": "sp-child", "store_id": PUNE, "status": "AVAILABLE"})
    db.seed("products", products)
    db.seed("catalog_products", twins)
    db.seed("catalog_variants", rows)
    db.seed("stock_units", units)
    return db


def _twin(db, tid):
    return copy.deepcopy(db["catalog_products"].find_one({"id": tid}))


def _row(db, sku):
    return copy.deepcopy(db["catalog_variants"].find_one({"sku": sku}))


def _spine(db, pid):
    return copy.deepcopy(db["products"].find_one({"product_id": pid}))


def _push_audit_rows(db):
    return [r for r in db["audit_logs"].find({}) if r.get("action") == "ONLINE_STORE_PUSH"]


def _child_payload(**over):
    payload = {
        "category": "SMARTGLASSES",
        "sku": CHILD_SKU,
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_name": "Meta Wayfarer Shiny Black G15",
            "model_no": "Meta Wayfarer Shiny Black G15",
            "colour_code": "601/71",
            "subbrand": "Meta",
            "shape": "Wayfarer",
            "gender": "unisex",
            "size": "Large",
            "gtin": CHILD_GTIN,
        },
        "mrp": 45700.0,
        "offer_price": 45700.0,
        "hsn_code": "852580",
        "gst_rate": 18.0,
        "discount_category": "PREMIUM",
        "tags": ["brand_rayban", "product_smartglass"],
        "variant_of": "sp-parent",
    }
    payload.update(over)
    return payload


def _create_via_door(db, payload, name=CHILD_NAME):
    """The runbook's create step: the ONE product door with the parent link,
    the name_hint and the base photo riding as extra_fields."""
    return pm.create_via_door(
        payload,
        source="MASTER",
        actor="u-admin",
        actor_name="admin",
        extra_fields={"name": name, "images": [PARENT_PHOTO], "sync_to_shopify": True},
        product_repo=ProductRepository(db["products"]),
        variant_repo=CatalogVariantRepository(db["catalog_variants"]),
        audit_repo=AuditRepository(db["audit_logs"]),
        db=db,
    )


def _stamp_child_gids(db):
    """The runbook's step 2: the Large variant's Shopify ids onto the row."""
    CatalogVariantRepository(db["catalog_variants"]).upsert(
        {"sku": CHILD_SKU, "shopify_variant_id": B_GID, "shopify_inventory_item_id": B_INV}
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    shopify_push._online_location_cache.clear()
    shopify_push._publication_id_cache.clear()
    monkeypatch.setenv("SHOPIFY_ONLINE_LOCATION_ID", "77")
    monkeypatch.delenv("ONLINE_STOCK_SAFETY_BUFFER", raising=False)
    monkeypatch.delenv("SHOPIFY_PUSH_PRICE_ON_UPDATE", raising=False)
    # The mirror (catalog_variants row) is ON on prod (policy default True).
    monkeypatch.setattr(pm, "mirror_enabled", lambda: True)
    yield
    shopify_push._online_location_cache.clear()
    shopify_push._publication_id_cache.clear()


# ---------------------------------------------------------------------------
# 1. a child push is refused, zero network
# ---------------------------------------------------------------------------


def test_child_push_is_refused_with_zero_network_even_live(monkeypatch):
    db = _world()
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    _wire(monkeypatch, db)

    res = _run(shopify_push.push_product(db, _twin(db, "tw-child"), []))
    assert (res.reason, res.action, res.ok, res.mode) == ("variant_of", "skip", False, "BLOCKED")
    assert PARENT_SKU in res.error and "push the parent" in res.error
    assert spy.calls == [], "no productCreate / anything for a size variant"

    # the product-page door ("Send to website") by sku: same refusal, one audit row
    out = _run(osp.push_product(CHILD_SKU, current_user=ADMIN))
    assert out["result"]["reason"] == "variant_of" and out["result"]["ok"] is False
    assert spy.calls == []
    rows = _push_audit_rows(db)
    assert len(rows) == 1 and rows[0]["details"]["reason"] == "variant_of"
    assert rows[0]["entity_id"] == "tw-child"
    # and the child twin was not written: still no gid, still clean
    child = _twin(db, "tw-child")
    assert "shopify_product_id" not in child["ecom"] and child["ecom"]["locally_modified"] is False


# ---------------------------------------------------------------------------
# 2. the parent's stock pass carries the child SKU
# ---------------------------------------------------------------------------


def test_parent_stock_pass_carries_the_child_sku(monkeypatch):
    db = _world()
    spy = _Spy(_responses())
    _live(monkeypatch, spy)

    res = _run(shopify_push.sync_stock_levels(db))
    assert res.ok and res.payload["candidates"] == 1 and res.payload["changed"] == 1

    tracking = spy.calls_for("productVariantsBulkUpdate")
    assert len(tracking) == 1
    assert tracking[0]["variables"]["productId"] == P_GID
    assert {r["id"] for r in tracking[0]["variables"]["variants"]} == {A_GID, B_GID}
    assert all(r["inventoryPolicy"] == "DENY" for r in tracking[0]["variables"]["variants"])
    setq = spy.calls_for("inventorySetQuantities")
    assert len(setq) == 1
    written = {q["inventoryItemId"]: q["quantity"] for q in setq[0]["variables"]["input"]["quantities"]}
    assert written == {A_INV: 1, B_INV: 1}
    assert all(q["locationId"] == LOC for q in setq[0]["variables"]["input"]["quantities"])
    # the ledger lives on the PARENT twin, keyed by SKU; the child twin has none
    assert _twin(db, "tw-parent")["ecom"]["online_stock"]["quantities"] == {PARENT_SKU: 1, CHILD_SKU: 1}
    assert "online_stock" not in _twin(db, "tw-child")["ecom"]
    # a clean second pass is a noop with zero network
    n = len(spy.calls)
    assert _run(shopify_push.sync_stock_levels(db)).action == "noop" and len(spy.calls) == n


def test_gid_door_ignores_a_child_even_if_a_repair_stamps_the_parent_gid_on_it():
    db = _world()
    child = db["catalog_products"].find_one({"id": "tw-child"})
    child["ecom"]["shopify_product_id"] = P_GID  # what a twin-repair-by-SKU script would do
    pairs = shopify_push.inventory._gid_products_with_variants(db)
    assert [p["id"] for p, _rows in pairs] == ["tw-parent"]
    assert sorted(r["sku"] for r in pairs[0][1]) == sorted([PARENT_SKU, CHILD_SKU])


# ---------------------------------------------------------------------------
# 3. the child's own price + barcode ride the parent's price push
# ---------------------------------------------------------------------------


def _bulk_price_rows(spy):
    rows = {}
    for c in spy.calls_for("productVariantsBulkUpdate"):
        for r in c["variables"]["variants"]:
            if "price" in r:
                rows[r["id"]] = r
    return rows


def test_child_price_and_barcode_ride_the_parents_price_push(monkeypatch):
    db = _world(seed_child=False)
    child_id = _create_via_door(db, _child_payload())["product_id"]
    _stamp_child_gids(db)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)

    parent = _twin(db, "tw-parent")
    rows = ls.variants_for_product(db, parent)
    assert sorted(r["sku"] for r in rows) == sorted([PARENT_SKU, CHILD_SKU])
    res = _run(shopify_push.push_variant_prices(db, parent, rows))
    assert res.ok
    sent = _bulk_price_rows(spy)
    assert sent[B_GID]["price"] == "45700.00" and sent[B_GID]["barcode"] == CHILD_GTIN
    assert sent[A_GID]["price"] == "39900.00", "the parent's row keeps its own price"
    assert "productOptions" not in json.dumps(spy.calls)

    # an mrp EDIT on the child: the row moves, the engine re-derives the online
    # price (a stale discounted_price must never ship), the PARENT is queued
    db["catalog_variants"].update_one(
        {"sku": CHILD_SKU},
        {"$set": {"discounted_price": 45700.0, "compare_at_price": 45700.0,
                  "online_price_meta": {"source": "rule", "pct": 0}}},
    )
    pm.mirror_update_to_catalog_twin(
        product_id=child_id, current=_spine(db, child_id), patch={"mrp": 47000.0}, db=db
    )
    row = _row(db, CHILD_SKU)
    assert row["mrp"] == 47000.0 and row["discounted_price"] == 47000.0
    assert _twin(db, "tw-parent")["ecom"]["locally_modified"] is True
    assert _twin(db, _spine(db, child_id)["pim_product_id"])["ecom"]["locally_modified"] is False
    spy.calls.clear()
    res = _run(shopify_push.push_variant_prices(db, _twin(db, "tw-parent"), ls.variants_for_product(db, parent)))
    assert res.ok and _bulk_price_rows(spy)[B_GID]["price"] == "47000.00"


# ---------------------------------------------------------------------------
# 4. delist of a child never drafts the parent
# ---------------------------------------------------------------------------


def test_deactivating_a_child_denies_its_variant_and_never_drafts_the_parent(monkeypatch):
    ledger = {"tracked": True, "quantities": {PARENT_SKU: 1, CHILD_SKU: 1}, "location_id": LOC, "policy": "DENY"}
    db = _world(ledger=ledger)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    _wire(monkeypatch, db)

    out = _run(online_delist.on_active_flip(
        _Conn(db), _spine(db, "sp-child"), was_active=True, now_active=False, actor=ADMIN
    ))
    assert out and out["ok"] and (out["entity"], out["action"], out["trigger"]) == ("variant", "delist", "deactivated")
    assert out["shopify_id"] == B_GID
    tracking = spy.calls_for("productVariantsBulkUpdate")
    assert len(tracking) == 1 and tracking[0]["variables"]["productId"] == P_GID
    assert tracking[0]["variables"]["variants"] == [
        {"id": B_GID, "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True}}
    ]
    setq = spy.calls_for("inventorySetQuantities")
    assert len(setq) == 1
    assert [(q["inventoryItemId"], q["quantity"]) for q in setq[0]["variables"]["input"]["quantities"]] == [(B_INV, 0)]
    assert not spy.calls_for("productUpdate("), "NEVER productUpdate on the parent"
    assert len(spy.calls) == 2

    child = _twin(db, "tw-child")
    assert child["ecom"]["online_state"] == "DELISTED" and child["ecom"]["delist_reason"] == "deactivated"
    parent = _twin(db, "tw-parent")
    assert parent["ecom"]["status"] == "PUBLISHED" and "taken_down_at" not in parent["ecom"]
    assert parent["ecom"]["locally_modified"] is False and parent["ecom"]["shopify_product_id"] == P_GID
    assert parent["ecom"]["online_stock"]["quantities"] == {PARENT_SKU: 1, CHILD_SKU: 0}, "the parent ledger records the 0"
    rows = _push_audit_rows(db)
    assert len(rows) == 1 and rows[0]["entity_type"] == "variant" and rows[0]["details"]["trigger"] == "deactivated"

    # the take-down route on a child REPORTS: zero network, never productUpdate
    n = len(spy.calls)
    res = _run(osp.take_down_product(CHILD_SKU, current_user=ADMIN))["result"]
    assert (res["reason"], res["action"], res["ok"], res["mode"]) == ("variant_of", "noop", True, "SIMULATED")
    assert "deactivate" in res["error"]
    assert len(spy.calls) == n


def test_child_delist_is_dark_by_default_and_a_noop_when_unmapped(monkeypatch):
    db = _world()
    _dark(monkeypatch)
    _wire(monkeypatch, db)
    out = _run(online_delist.on_active_flip(
        _Conn(db), _spine(db, "sp-child"), was_active=True, now_active=False, actor=ADMIN
    ))
    assert out["mode"] == "SIMULATED" and out["entity"] == "variant" and out["payload"]["variantId"] == B_GID
    assert out["payload"]["productId"] == P_GID and out["payload"]["quantity"] == 0
    # never mapped (no gids on the row): nothing on Shopify, nothing recorded
    db2 = _world(child_gids=False)
    _wire(monkeypatch, db2)
    assert _run(online_delist.on_active_flip(
        _Conn(db2), _spine(db2, "sp-child"), was_active=True, now_active=False, actor=ADMIN
    )) is None
    assert _push_audit_rows(db2) == [] and "online_state" not in _twin(db2, "tw-child")["ecom"]


# ---------------------------------------------------------------------------
# 5. an inactive child lists 0 and comes back on reactivation
# ---------------------------------------------------------------------------


def test_inactive_child_lists_zero_and_a_missing_flag_is_active():
    db = _world(child_active=False)
    assert wb.online_quantities_for_skus(db, [PARENT_SKU, CHILD_SKU]) == {PARENT_SKU: 1, CHILD_SKU: 0}
    db["products"].update_one({"product_id": "sp-child"}, {"$unset": {"is_active": ""}})
    assert wb.online_quantities_for_skus(db, [CHILD_SKU]) == {CHILD_SKU: 1}, "a MISSING flag is active"


def test_flip_off_then_on_with_no_pass_between_resends_the_child(monkeypatch):
    ledger = {"tracked": True, "quantities": {PARENT_SKU: 1, CHILD_SKU: 1}, "location_id": LOC, "policy": "DENY"}
    db = _world(ledger=ledger)
    spy = _Spy(_responses())
    _live(monkeypatch, spy)
    _wire(monkeypatch, db)
    conn = _Conn(db)

    # off: the delist writes 0 on Shopify (and 0 into the parent's ledger)
    db["products"].update_one({"product_id": "sp-child"}, {"$set": {"is_active": False}})
    _run(online_delist.on_active_flip(conn, _spine(db, "sp-child"), was_active=True, now_active=False, actor=ADMIN))
    # while off, the quantity rule agrees with the delist: a pass re-sends nothing
    spy.calls.clear()
    assert _run(shopify_push.sync_stock_levels(db)).action == "noop" and spy.calls == []

    # on again, NO pass in between: the next pass must carry the child's 1
    db["products"].update_one({"product_id": "sp-child"}, {"$set": {"is_active": True}})
    _run(online_delist.on_active_flip(conn, _spine(db, "sp-child"), was_active=False, now_active=True, actor=ADMIN))
    child = _twin(db, "tw-child")
    assert "online_state" not in child["ecom"] and child["ecom"]["locally_modified"] is False, "lifted, not queued"
    res = _run(shopify_push.sync_stock_levels(db))
    assert res.action == "sync" and res.ok
    setq = spy.calls_for("inventorySetQuantities")
    assert len(setq) == 1
    written = {q["inventoryItemId"]: q["quantity"] for q in setq[0]["variables"]["input"]["quantities"]}
    assert written[B_INV] == 1
    assert _twin(db, "tw-parent")["ecom"]["online_stock"]["quantities"][CHILD_SKU] == 1


# ---------------------------------------------------------------------------
# 6. media and title are hands-off
# ---------------------------------------------------------------------------


def test_media_and_title_are_hands_off_for_a_child(monkeypatch):
    db = _world()
    spy = _Spy(_responses())
    _live(monkeypatch, spy)

    parent = _twin(db, "tw-parent")
    res = _run(shopify_push.push_product(db, parent, ls.variants_for_product(db, parent), blocked=False))
    assert res.action == "update" and spy.calls_for("productUpdate(")
    transcript = json.dumps(spy.calls)
    assert CHILD_PHOTO not in transcript, "the child's photo never reaches a media mutation"
    assert CHILD_NAME not in transcript, "the child's name never reaches the title"
    assert spy.calls_for("productUpdate(")[0]["variables"]["input"]["title"] == PARENT_NAME
    # the child's SKU still rode the parent's stock write
    setq = spy.calls_for("inventorySetQuantities")
    assert B_INV in {q["inventoryItemId"] for c in setq for q in c["variables"]["input"]["quantities"]}

    # a rename of the child queues NOTHING (no listing to retitle)
    pm.mirror_update_to_catalog_twin(
        product_id="sp-child", current=_spine(db, "sp-child"), patch={"name": "Large Renamed"}, db=db
    )
    assert _twin(db, "tw-child")["ecom"]["locally_modified"] is False
    assert _twin(db, "tw-parent")["ecom"]["locally_modified"] is False
    # an mrp edit lands on the row and queues the PARENT, never the child
    pm.mirror_update_to_catalog_twin(
        product_id="sp-child", current=_spine(db, "sp-child"), patch={"mrp": 47000.0}, db=db
    )
    assert _row(db, CHILD_SKU)["mrp"] == 47000.0
    assert _twin(db, "tw-parent")["ecom"]["locally_modified"] is True
    assert _twin(db, "tw-child")["ecom"]["locally_modified"] is False


# ---------------------------------------------------------------------------
# 7. the live sync never selects a child as a listing
# ---------------------------------------------------------------------------


class _Cache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ttl=None):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


def test_live_sync_never_selects_a_child_as_a_listing(monkeypatch):
    db = _world()
    _dark(monkeypatch)
    _wire(monkeypatch, db)
    monkeypatch.setattr(pe, "cache", _Cache())
    monkeypatch.setattr(pe, "_coll", lambda name="policy_settings": db[name])
    # dirtied by hand (a drawer edit / the discount engine's product pass)
    db["catalog_products"].update_one({"id": "tw-child"}, {"$set": {"ecom.locally_modified": True}})

    dirty, skipped = ls.select_dirty_products(db)
    assert [d["id"] for d in dirty] == [] and skipped == 0
    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-admin"))
    assert run["selected"] == 0 and run["awaiting_first_publish"] == 0 and run["attempted"] == 0
    assert osp._product_counts(db) == {"staged": 1, "pushed": 1, "pending": 0}
    assert catalog_counts(db)["pending"] == 0
    state = product_online_state(_twin(db, "tw-child"))
    assert state["queued"] is False and state["online"] == "OFF"


# ---------------------------------------------------------------------------
# 8. the door mints the child
# ---------------------------------------------------------------------------


def test_the_one_create_door_mints_a_size_variant():
    db = _world(seed_child=False)
    created = _create_via_door(db, _child_payload())

    spine = _spine(db, created["product_id"])
    assert spine["variant_of"] == "sp-parent" and spine["sku"] == CHILD_SKU
    assert spine["size"] == "Large" and spine["identity_key"].endswith("|large")
    assert spine["name"] == CHILD_NAME and spine["name"] != _spine(db, "sp-parent")["name"]
    assert spine["category"] == "SMARTGLASSES" and spine["hsn_code"] == "852580" and spine["gst_rate"] == 18.0
    assert spine["images"] == [PARENT_PHOTO] and spine["sync_to_shopify"] is True

    twin = _twin(db, spine["pim_product_id"])
    assert twin["ecom"]["variant_of"] == _child_link()
    assert twin["ecom"]["locally_modified"] is False, "born CLEAN -- never queued as a listing"
    assert "shopify_product_id" not in twin["ecom"]
    assert shopify_push.is_variant_of(twin) and not shopify_push.is_variant_of(_twin(db, "tw-parent"))
    assert twin["sku"] == CHILD_SKU and twin["name"] == CHILD_NAME

    row = _row(db, CHILD_SKU)
    assert row["parent_product_id"] == "tw-parent" and row["parent_sku"] == PARENT_SKU
    assert row["option_size"] == "Large" and row["mrp"] == 45700.0 and row["gtin"] == CHILD_GTIN
    assert "discounted_price" not in row and "shopify_variant_id" not in row
    parent = _twin(db, "tw-parent")
    assert sorted(r["sku"] for r in ls.variants_for_product(db, parent)) == sorted([PARENT_SKU, CHILD_SKU])
    assert shopify_push.product_skus(parent, ls.variants_for_product(db, parent)) == [PARENT_SKU, CHILD_SKU]
    # the parent was NOT queued by the create (first publish stays a human press)
    assert parent["ecom"]["locally_modified"] is False

    # the same payload again is a duplicate (sku)
    with pytest.raises(pm.ProductMasterError) as exc:
        _create_via_door(db, _child_payload())
    assert exc.value.status == 409


def test_without_an_explicit_sku_the_mint_ends_large():
    """Documents WHY the runbook passes the -L sku: build_sku appends the size
    with no joiner, but Shopify holds '...601/71-L' and a reseed writes
    inventoryItem.sku = row.sku, so IMS must equal Shopify."""
    db = _world(seed_child=False)
    created = _create_via_door(db, _child_payload(sku=None))
    assert created["sku"].endswith("LARGE") and created["sku"] != CHILD_SKU
    assert _row(db, created["sku"])["parent_product_id"] == "tw-parent"


@pytest.mark.parametrize(
    "parent_id, status, needle",
    [
        ("sp-nope", 422, "no product"),
        ("sp-sun", 422, "same category"),
        ("sp-child", 422, "no chains"),
    ],
)
def test_variant_of_is_validated_at_the_door(parent_id, status, needle):
    db = _world()  # sp-child exists (a child); add a SUNGLASS parent
    db["products"].insert_one({"product_id": "sp-sun", "sku": "SGX", "category": "SUNGLASS", "is_active": True})
    with pytest.raises(pm.ProductMasterError) as exc:
        _create_via_door(db, _child_payload(sku=CHILD_SKU + "2", variant_of=parent_id))
    assert exc.value.status == status and exc.value.field == "variant_of"
    assert needle in str(exc.value)
    assert db["products"].find_one({"sku": CHILD_SKU + "2"}) is None, "refused BEFORE any write"


def test_variant_of_needs_the_product_store():
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.create_product(
            category="SMARTGLASSES", attributes=_child_payload()["attributes"],
            mrp=1, offer_price=1, actor="u", variant_of="sp-parent", product_repo=None,
        )
    assert exc.value.status == 503


# ---------------------------------------------------------------------------
# 9. the runbook's step order + create-only options
# ---------------------------------------------------------------------------


def test_needs_repair_is_true_until_the_child_row_has_gids_and_options_are_create_only():
    db = _world(child_gids=False)
    parent = _twin(db, "tw-parent")
    rows = ls.variants_for_product(db, parent)
    assert shopify_push._needs_repair(parent, rows) is True, "a gid-less child row would trigger repair seeding"
    _stamp_child_gids(db)
    rows = ls.variants_for_product(db, parent)
    assert shopify_push._needs_repair(parent, rows) is False
    # an UPDATE of the live parent never re-sends productOptions (create-only)
    assert "productOptions" not in shopify_push.build_product_input(parent, rows)
    assert shopify_push.build_product_input(parent, rows)["id"] == P_GID


# ---------------------------------------------------------------------------
# 10. the runbook (scripts/create_large_variants.py) end to end, no prod
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
import create_large_variants as runbook  # noqa: E402


def _large_row():
    """One row shaped exactly like large_11.json, against the fixture parent."""
    return {
        "large_sku": CHILD_SKU,
        "parent": {"product_id": "sp-parent", "sku": PARENT_SKU, "pim_product_id": "tw-parent",
                   "shopify_product_id": P_GID, "name": PARENT_NAME},
        "create_payload_base_fields": {**_child_payload(), "cost_price": None, "images": [PARENT_PHOTO],
                                       "sync_to_shopify": True, "name_hint": CHILD_NAME},
        "shopify_large_variant": {"sku": CHILD_SKU, "variant_id": B_GID, "inventory_item_id": B_INV,
                                  "product_id": P_GID, "tracked": True, "policy": "DENY"},
        "opening_stock": {"store_id": PUNE, "store_code": "BV-PUN-01", "qty": 1},
    }


class _DoorConn(_Conn):
    """dependencies.get_db() for the opening-stock door: repositories are
    built off attributes (db.products / db.stock_units / db.audit_logs)."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.db[name]


def _wire_door(monkeypatch, db):
    monkeypatch.setattr(deps, "get_db", lambda: _DoorConn(db))


def test_runbook_creates_links_and_seeds_opening_stock_through_the_doors(monkeypatch):
    db = _world(seed_child=False)
    _wire_door(monkeypatch, db)
    user = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"], "active_store_id": PUNE}
    product_repo = ProductRepository(db["products"])
    variant_repo = CatalogVariantRepository(db["catalog_variants"])
    audit_repo = AuditRepository(db["audit_logs"])
    rows = [_large_row()]

    assert runbook.row_fences(db, rows, product_repo) == []
    assert rows[0]["_preview"]["sku"] == CHILD_SKU and rows[0]["_preview"]["identity_key"].endswith("|large")

    made = runbook.create_variant_of(db, rows[0], user, product_repo=product_repo,
                                     variant_repo=variant_repo, audit_repo=audit_repo)
    assert made["sku"] == CHILD_SKU and made["parent_twin_id"] == "tw-parent"
    row = _row(db, CHILD_SKU)
    assert (row["shopify_variant_id"], row["shopify_inventory_item_id"], row["parent_product_id"]) == (B_GID, B_INV, "tw-parent")
    assert _spine(db, made["product_id"])["variant_of"] == "sp-parent"
    assert _twin(db, "tw-parent")["ecom"]["locally_modified"] is False, "the parent is left CLEAN"

    out = runbook.opening_stock(rows, user, apply=True)
    assert out["commit"]["summary"]["units_added"] == 1 and out["commit"]["summary"]["batch_id"]
    units = list(db["stock_units"].find({"product_id": made["product_id"]}))
    assert len(units) == 1 and units[0]["store_id"] == PUNE and units[0]["status"] == "AVAILABLE"
    assert units[0]["source"] == "OPENING_STOCK" and units[0]["created_by"] == "u-admin"
    assert db["opening_stock_batches"].find_one({"batch_id": out["commit"]["summary"]["batch_id"]})["lines"][0]["sku"] == CHILD_SKU
    assert wb.online_quantities_for_skus(db, [PARENT_SKU, CHILD_SKU]) == {PARENT_SKU: 1, CHILD_SKU: 1}
    rev = runbook.reversal_list(db, [made], out["commit"]["summary"]["batch_id"])
    assert rev["products_product_id"] == [made["product_id"]] and rev["catalog_variants_sku"] == [CHILD_SKU]
    assert len(rev["stock_units"]) == 1 and rev["opening_stock_batches"] == [out["commit"]["summary"]["batch_id"]]

    # a second press is REFUSED by the fences (sku + gid + batch line)
    problems = runbook.row_fences(db, [_large_row()], product_repo)
    assert any("already holds this sku" in p for p in problems)
    assert any("Large variant gid" in p for p in problems)
    assert any("opening_stock_batches" in p for p in problems)


def test_runbook_fences_refuse_a_dirty_parent_and_a_wrong_gid():
    db = _world(seed_child=False)
    product_repo = ProductRepository(db["products"])
    db["catalog_products"].update_one({"id": "tw-parent"}, {"$set": {"ecom.locally_modified": True}})
    assert any("DIRTY" in p for p in runbook.row_fences(db, [_large_row()], product_repo))
    db["catalog_products"].update_one({"id": "tw-parent"}, {"$set": {"ecom.locally_modified": False}})
    wrong = _large_row()
    wrong["shopify_large_variant"]["product_id"] = "gid://shopify/Product/999"
    assert any("!= the Large variant's product" in p for p in runbook.row_fences(db, [wrong], product_repo))
    assert runbook.in_sync_window(datetime(2026, 9, 6, 19, 40, tzinfo=timezone.utc))  # 01:10 IST
    assert not runbook.in_sync_window(datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc))  # 17:30 IST
