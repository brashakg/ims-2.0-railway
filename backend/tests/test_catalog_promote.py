"""Catalog Manager: POST /catalog/products/{id}/promote + the catalog PUT
extensions (PR: catalog manager).

Promote is the ONLY thing that clears needs_review/pos_ready: it validates the
imported doc through the canonical door (build_canonical_product -- no
validation fork) and inserts a `products` spine row PRESERVING the BVI CUID id
(catalog_variants.parent_product_id + ecom.* hang off it) and the existing sku.

Runs without a DB: catalog falls back to the in-memory CATALOG_PRODUCTS dict
(catalog._get_db monkeypatched to None) while the spine repo is the REAL
ProductRepository over the in-repo MockCollection.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import api.dependencies as deps_mod  # noqa: E402
import api.services.cache as cache_mod  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
from api.routers import orders as orders_mod  # noqa: E402
from api.services.gst_rates import gst_rate_for_category, hsn_for_category  # noqa: E402
from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402


def _user():
    return {
        "user_id": "reviewer-1",
        "username": "reviewer",
        "roles": ["ADMIN"],
        "active_store_id": "S1",
    }


def _bvi_doc(doc_id="clx0catmgr001", sku="BVISKU1", complete=True, **over):
    """A BVI-import-shaped catalog_products doc (see scripts/migrate_bvi_pim.py
    map_product): CUID id, canonical long-form category, top-level AND nested
    pricing, needs_review=True / pos_ready=False."""
    attrs = {"brand_name": "Vogue", "model_no": "VO5051"}
    if complete:
        attrs["colour_code"] = "BLK"
    doc = {
        "id": doc_id,
        "bvi_product_id": doc_id,
        "title": "Vogue VO5051",
        "name": "Vogue VO5051",
        "brand": "Vogue",
        "category": "FRAME",
        "hsn_code": "900311",
        "gst_rate": 5.0,
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "pricing": {"mrp": 5000.0, "offer_price": 4500.0},
        "images": ["https://cdn.shopify.com/s/files/1/vo5051.jpg"],
        "attributes": attrs,
        "tags": ["eyewear"],
        "is_active": True,
        "pos_ready": False,
        "needs_review": True,
        "source": "bvi_import",
        "migrated_at": datetime.now(timezone.utc),
    }
    if sku:
        doc["sku"] = sku
    doc.update(over)
    return doc


class _AuditRecorder:
    def __init__(self):
        self.rows = []

    def create(self, row):
        self.rows.append(row)
        return row


class _CacheSpy:
    TTL_MEDIUM = 300

    def __init__(self):
        self.deleted_patterns = []

    def get(self, k):
        return None

    def set(self, k, v, ttl=0):
        pass

    def delete_pattern(self, pattern):
        self.deleted_patterns.append(pattern)


@pytest.fixture()
def env(monkeypatch):
    """No-DB catalog (in-memory CATALOG_PRODUCTS) + a real spine repo over a
    MockCollection + audit recorder + cache spy."""
    catalog_mod.CATALOG_PRODUCTS.clear()
    repo = ProductRepository(MockCollection("products"))
    audit = _AuditRecorder()
    cache = _CacheSpy()
    monkeypatch.setattr(catalog_mod, "_get_db", lambda: None)
    monkeypatch.setattr(deps_mod, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps_mod, "get_audit_repository", lambda: audit)
    monkeypatch.setattr(cache_mod, "cache", cache)
    yield {"repo": repo, "audit": audit, "cache": cache}
    catalog_mod.CATALOG_PRODUCTS.clear()


def _promote(pid, dry_run=False):
    return asyncio.run(
        catalog_mod.promote_catalog_product(pid, dry_run=dry_run, current_user=_user())
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_promote_happy_path_preserves_id_and_sku(env):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    res = _promote(doc["id"])

    assert res["pos_ready"] is True and res["needs_review"] is False
    assert res["product_id"] == doc["id"]
    assert res["sku"] == "BVISKU1"

    # Spine row shares the SAME id + sku (BVI CUID preserved).
    spine = env["repo"].find_by_id(doc["id"])
    assert spine is not None
    assert spine["id"] == doc["id"]
    assert spine["sku"] == "BVISKU1"
    assert spine["category"] == "FRAME"
    assert spine["is_active"] is True
    # Additive columns carried over from the catalog doc.
    assert spine.get("images") == doc["images"]

    # Catalog doc stamped -- promote is the ONLY writer of these flags.
    stamped = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert stamped["needs_review"] is False
    assert stamped["pos_ready"] is True
    assert stamped["promoted_by"] == "reviewer-1"
    assert stamped.get("promoted_at")

    # Activity log written.
    actions = [r.get("action") for r in env["audit"].rows]
    assert "catalog_product.promoted" in actions

    # GET /products TTL cache busted so the item is immediately searchable.
    assert "products:*" in env["cache"].deleted_patterns


def test_promote_sku_absent_mints_and_writes_back(env):
    doc = _bvi_doc(doc_id="clx0nosku0001", sku=None)
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    res = _promote(doc["id"])

    minted = res["sku"]
    assert minted and minted != "DRYRUN-PLACEHOLDER"
    spine = env["repo"].find_by_id(doc["id"])
    assert spine["sku"] == minted
    # Door-minted SKU written back to the catalog doc (shared identity).
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["sku"] == minted


# ---------------------------------------------------------------------------
# Hard collisions (plain-English 409s)
# ---------------------------------------------------------------------------


def test_promote_409_on_existing_spine_id(env):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    env["repo"].create({"product_id": doc["id"], "id": doc["id"], "sku": "OTHER-1"})

    with pytest.raises(HTTPException) as exc:
        _promote(doc["id"])
    assert exc.value.status_code == 409
    assert "already" in str(exc.value.detail)


def test_promote_409_on_sku_collision_names_the_owner(env):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    env["repo"].create(
        {
            "product_id": "spine-999",
            "id": "spine-999",
            "sku": "BVISKU1",
            "brand": "Ray-Ban",
            "model": "RB9999",
        }
    )

    with pytest.raises(HTTPException) as exc:
        _promote(doc["id"])
    assert exc.value.status_code == 409
    detail = str(exc.value.detail)
    assert "BVISKU1" in detail
    assert "Ray-Ban" in detail  # the colliding product is NAMED


def test_promote_404_unknown_doc(env):
    with pytest.raises(HTTPException) as exc:
        _promote("no-such-doc")
    assert exc.value.status_code == 404


def test_promote_503_without_product_repo(env, monkeypatch):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    monkeypatch.setattr(deps_mod, "get_product_repository", lambda: None)
    with pytest.raises(HTTPException) as exc:
        _promote(doc["id"])
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Validation: the door's gates, never a fork
# ---------------------------------------------------------------------------


def test_promote_422_gap_shape_matches_create_door(env):
    doc = _bvi_doc(complete=False)  # missing colour_code (FRAME required)
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    with pytest.raises(HTTPException) as exc:
        _promote(doc["id"])
    assert exc.value.status_code == 422
    assert "missing required" in str(exc.value.detail).lower()
    assert "colour_code" in str(exc.value.detail)
    # Hard-fail semantics: nothing was written.
    assert env["repo"].find_by_id(doc["id"]) is None
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["needs_review"] is True


def test_promote_400_on_offer_above_mrp(env):
    doc = _bvi_doc(
        mrp=1000.0,
        offer_price=1500.0,
        pricing={"mrp": 1000.0, "offer_price": 1500.0},
    )
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    with pytest.raises(HTTPException) as exc:
        _promote(doc["id"])
    assert exc.value.status_code == 400
    assert "Offer price cannot exceed MRP" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# Dry-run: {ok, gaps, duplicate_warnings} with ZERO writes
# ---------------------------------------------------------------------------


def test_dry_run_reports_gaps_with_zero_writes(env):
    doc = _bvi_doc(doc_id="clx0dry00001", sku=None, complete=False)
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    before = dict(catalog_mod.CATALOG_PRODUCTS[doc["id"]])

    res = _promote(doc["id"], dry_run=True)

    assert res["ok"] is False
    gap_fields = {g["field"] for g in res["gaps"]}
    assert "colour_code" in gap_fields
    # Zero writes: no spine row, doc byte-identical (no minted sku, no stamp).
    assert env["repo"].find_by_id(doc["id"]) is None
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]] == before
    assert env["audit"].rows == []
    assert env["cache"].deleted_patterns == []


def test_dry_run_ok_when_complete(env):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    res = _promote(doc["id"], dry_run=True)
    assert res == {"ok": True, "gaps": [], "duplicate_warnings": []}
    assert env["repo"].find_by_id(doc["id"]) is None  # still no write


def test_dry_run_soft_duplicate_warning_on_brand_model(env):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    # An EXISTING manually-catalogued spine product with the same brand+model
    # but a different sku/id: soft warning, never a block.
    env["repo"].create(
        {
            "product_id": "spine-777",
            "id": "spine-777",
            "sku": "FR-VO-0777",
            "brand": "Vogue",
            "model": "VO5051",
        }
    )
    res = _promote(doc["id"], dry_run=True)
    assert res["ok"] is True
    assert len(res["duplicate_warnings"]) == 1
    warn = res["duplicate_warnings"][0]
    assert warn["sku"] == "FR-VO-0777"
    assert warn["reason"] == "same brand + model"


# ---------------------------------------------------------------------------
# Structural POS gate (unit-level -- NO POS files touched): before promote the
# orders resolver only finds the doc via the catalog fallback (guard 3 400s
# that); after promote it resolves from the spine.
# ---------------------------------------------------------------------------


def test_promote_satisfies_orders_structural_gate(env, monkeypatch):
    doc = _bvi_doc(doc_id="clx0gate0001", sku="BVIGATE1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    # The orders catalog fallback reads catalog_products; back it with the doc.
    cat_coll = MockCollection("catalog_products")
    cat_coll.insert_one({**doc, "_id": doc["id"]})
    monkeypatch.setattr(orders_mod, "_get_catalog_collection", lambda: cat_coll)

    before = orders_mod._resolve_product_doc(env["repo"], doc["id"])
    assert before is not None
    assert before.get("_resolved_from") == "catalog_products"  # guard 3 would 400

    _promote(doc["id"])

    after = orders_mod._resolve_product_doc(env["repo"], doc["id"])
    assert after is not None
    assert after.get("_resolved_from") != "catalog_products"  # spine row wins
    assert after.get("sku") == "BVIGATE1"


# ---------------------------------------------------------------------------
# PUT /catalog/products/{id} extensions (review mini-form save)
# ---------------------------------------------------------------------------


def _put(pid, payload):
    inp = catalog_mod.ProductUpdateInput(**payload)
    return asyncio.run(catalog_mod.update_catalog_product(pid, inp, _user()))


def test_put_category_change_rederives_hsn_gst(env):
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    # Alias input canonicalises; HSN/GST re-derived from the NEW category
    # because neither was explicitly sent.
    _put(doc["id"], {"category": "Sunglasses"})
    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["category"] == "SUNGLASS"
    assert updated["hsn_code"] == hsn_for_category("SUNGLASS")
    assert updated["gst_rate"] == gst_rate_for_category("SUNGLASS")
    assert updated["category_unmapped"] is False


def test_put_category_change_respects_explicit_hsn_gst(env):
    doc = _bvi_doc(doc_id="clx0puttax01")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    _put(doc["id"], {"category": "SUNGLASS", "hsn_code": "90041000", "gst_rate": 12.0})
    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["hsn_code"] == "90041000"
    assert updated["gst_rate"] == 12.0


def test_put_unknown_category_422(env):
    doc = _bvi_doc(doc_id="clx0putbad01")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    with pytest.raises(HTTPException) as exc:
        _put(doc["id"], {"category": "NOT_A_THING"})
    assert exc.value.status_code == 422


def test_put_attributes_patch_on_imported_doc_does_not_500(env):
    # BVI docs store the canonical LONG-form category ("FRAME"), which is not a
    # ProductCategory short code -- the title regen used to raise ValueError.
    doc = _bvi_doc(doc_id="clx0putattr1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    res = _put(doc["id"], {"attributes": {"gender": "Men"}})
    assert res["product"]["attributes"]["gender"] == "Men"
    # Merge, not replace: existing keys survive.
    assert res["product"]["attributes"]["brand_name"] == "Vogue"


def test_put_dictionary_enforcement_fires_on_attributes(env, monkeypatch):
    doc = _bvi_doc(doc_id="clx0putdict1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    def _reject(category, attrs, db=None):
        raise catalog_mod._pm.ProductMasterError(
            "'Neon' is not an allowed value for Frame Color.",
            status=422,
            field="frame_color",
        )

    monkeypatch.setattr(catalog_mod._pm, "enforce_dictionary_values", _reject)
    with pytest.raises(HTTPException) as exc:
        _put(doc["id"], {"attributes": {"frame_color": "Neon"}})
    assert exc.value.status_code == 422
    assert "not an allowed value" in str(exc.value.detail)


def test_put_pricing_edit_mirrors_top_level(env):
    # Imported docs carry the price BOTH top-level and nested; the review-form
    # save must keep them in sync so promote never reads a stale value.
    doc = _bvi_doc(doc_id="clx0putprice")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    _put(doc["id"], {"pricing": {"mrp": 6000.0, "offer_price": 5500.0}})
    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["pricing"]["mrp"] == 6000.0
    assert updated["mrp"] == 6000.0
    assert updated["offer_price"] == 5500.0


def test_put_never_touches_review_flags(env):
    doc = _bvi_doc(doc_id="clx0putflags")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    _put(
        doc["id"],
        {
            "category": "SUNGLASS",
            "attributes": {"gender": "Men"},
            "pricing": {"mrp": 9000.0},
            "description": "New copy",
            "is_active": True,
        },
    )
    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["needs_review"] is True  # provably untouched
    assert updated["pos_ready"] is False  # promote stays the only door


# ---------------------------------------------------------------------------
# One maker code, one product: Approve and the bulk import are create doors too
# (product_master.assert_gtin_free, normalised compare via find_by_barcode).
# ---------------------------------------------------------------------------

_HELD_UPC = "036000291452"  # the holder stores the 13-digit spelling


def _gtin_holder_repo(monkeypatch):
    """A real ProductRepository over mongomock (dotted $or/$in queries) where
    another product already holds the GTIN, as 0036000291452."""
    import mongomock

    repo = ProductRepository(mongomock.MongoClient().db.products)
    repo.collection.insert_one(
        {"product_id": "spine-holder", "sku": "HOLD1", "attributes": {"gtin": "0" + _HELD_UPC}}
    )
    monkeypatch.setattr(deps_mod, "get_product_repository", lambda: repo)
    return repo


def _attrs_with_gtin(gtin):
    return {"brand_name": "Vogue", "model_no": "VO5051", "colour_code": "BLK", "gtin": gtin}


def test_promote_refuses_a_gtin_another_product_holds(env, monkeypatch):
    repo = _gtin_holder_repo(monkeypatch)
    doc = _bvi_doc(doc_id="clx0gtintwin", attributes=_attrs_with_gtin(_HELD_UPC))
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    dry = _promote(doc["id"], dry_run=True)
    assert dry["ok"] is False and dry["gaps"][0]["field"] == "gtin"
    with pytest.raises(HTTPException) as exc:
        _promote(doc["id"])
    assert exc.value.status_code == 409
    assert repo.find_one({"product_id": doc["id"]}) is None  # no spine
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["needs_review"] is True


def test_promote_with_a_free_gtin_still_approves(env, monkeypatch):
    repo = _gtin_holder_repo(monkeypatch)
    doc = _bvi_doc(doc_id="clx0gtinfree", attributes=_attrs_with_gtin("4006381333931"))
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    assert _promote(doc["id"])["pos_ready"] is True
    assert repo.find_one({"product_id": doc["id"]}) is not None


def _import(gtin):
    row = catalog_mod.ProductCreateInput(
        category="FR",
        attributes=_attrs_with_gtin(gtin),
        pricing={"mrp": 5000.0, "offer_price": 4500.0},
    )
    return asyncio.run(catalog_mod.import_products([row], current_user=_user()))


def test_import_refuses_a_row_whose_gtin_another_product_holds(env, monkeypatch):
    _gtin_holder_repo(monkeypatch)
    res = _import(_HELD_UPC)
    assert res["created_count"] == 0
    assert "already assigned" in res["errors"][0]["error"]
    assert not catalog_mod.CATALOG_PRODUCTS


def test_import_with_a_free_gtin_still_creates(env, monkeypatch):
    _gtin_holder_repo(monkeypatch)
    res = _import("4006381333931")
    assert res["created_count"] == 1, res["errors"]


def test_an_imported_then_approved_gtin_reaches_the_price_push(env, monkeypatch):
    """Import and Approve write the GTIN as the gtin ATTRIBUTE only (no
    top-level projection on the doc): the push reads the product's own GTIN
    from that home, so it still ships as the variant barcode."""
    from api.services.shopify_push.product_input import (
        _variants_for_price_push,
        build_variant_price_inputs,
    )

    _gtin_holder_repo(monkeypatch)
    assert _import("4006381333931")["created_count"] == 1
    (pid,) = catalog_mod.CATALOG_PRODUCTS
    _promote(pid)
    twin = dict(catalog_mod.CATALOG_PRODUCTS[pid])
    assert not twin.get("gtin")
    twin["ecom"] = {"shopify_variant_id": "gid://shopify/ProductVariant/1"}
    rows, _ = build_variant_price_inputs(twin, _variants_for_price_push(twin, []))
    assert rows[0]["barcode"] == "4006381333931"


@pytest.fixture()
def shared_db(env, monkeypatch):
    """`env` on ONE mongomock db, as in production: the catalog door's twins
    and the spine repo read and write the same database."""
    import mongomock

    db = mongomock.MongoClient().db
    repo = ProductRepository(db.products)
    monkeypatch.setattr(catalog_mod, "_get_db", lambda: db)
    monkeypatch.setattr(deps_mod, "get_product_repository", lambda: repo)
    return db


def _import_rows(*gtins):
    rows = [
        catalog_mod.ProductCreateInput(
            category="FR",
            attributes={**_attrs_with_gtin(g), "model_no": f"VO{i}"},
            pricing={"mrp": 5000.0, "offer_price": 4500.0},
        )
        for i, g in enumerate(gtins)
    ]
    return asyncio.run(catalog_mod.import_products(rows, current_user=_user()))


def test_import_keeps_one_product_per_gtin_across_rows_and_batches(shared_db):
    """The import writes only a catalog_products twin (no spine) and queues it
    for the push, which sends the twin's gtin as its variant barcode: two rows
    with one GTIN were two Shopify listings carrying the same barcode."""
    res = _import_rows("4006381333931", "04006381333931")
    assert res["created_count"] == 1
    assert [e["index"] for e in res["errors"]] == [1]
    assert "already assigned" in res["errors"][0]["error"]
    again = _import_rows("4006381333931")
    assert again["created_count"] == 0 and "already assigned" in again["errors"][0]["error"]
    assert shared_db.catalog_products.count_documents({}) == 1


def test_a_create_door_sees_an_imported_twins_gtin(shared_db):
    """Quick Add / the catalog create door read only spines, so an imported
    twin's GTIN was free to take."""
    assert _import_rows("4006381333931")["created_count"] == 1
    row = catalog_mod.ProductCreateInput(
        category="FR",
        attributes={**_attrs_with_gtin("4006381333931"), "model_no": "VO-NEW"},
        pricing={"mrp": 5000.0, "offer_price": 4500.0},
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(catalog_mod.create_catalog_product(row, current_user=_user()))
    assert exc.value.status_code == 409
    assert shared_db.products.count_documents({}) == 0


def test_an_imported_twin_approves_and_re_saves_its_own_gtin(shared_db):
    """The twin itself is never the clash: Approve keys the check on its id,
    and the review editor on a spineless twin passes the twin's id."""
    assert _import_rows("4006381333931")["created_count"] == 1
    (pid,) = [d["id"] for d in shared_db.catalog_products.find({})]
    inp = catalog_mod.ProductUpdateInput(
        attributes={"gtin": "4006381333931", "colour_code": "RED"}
    )
    asyncio.run(catalog_mod.update_catalog_product(pid, inp, _user()))
    assert shared_db.catalog_products.find_one({"id": pid})["attributes"]["colour_code"] == "RED"
    assert _promote(pid)["pos_ready"] is True
