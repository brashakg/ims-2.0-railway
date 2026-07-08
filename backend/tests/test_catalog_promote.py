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
