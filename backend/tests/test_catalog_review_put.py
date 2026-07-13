"""PUT /catalog/products/{id} full-page review editor extensions (PR: review
full editor).

The review editor saves DIFF-ONLY payloads through the lenient catalog PUT;
promote stays the single strict validation door. Covered here:

* PricingPatchInput -- the create-path PricingInput REQUIRED mrp and defaulted
  discount_category to 'MASS', so a partial pricing patch was impossible AND
  silently stamped MASS onto the merged block (widening a LUXURY item's 5% POS
  discount cap to 15% via the fail-soft spine mirror). All-optional now; the
  MRP >= offer rule still runs on the EFFECTIVE post-merge values.
* name/tags on the PUT (explicit name wins over the title regen for that save)
  + the attributes.brand_name/model_no -> top-level brand/model mirror.
* expected_updated_at -- additive optimistic concurrency (absent = today's
  last-write-wins, so the existing drawer keeps working unchanged).
* name/tags survive promote to the spine row.

Runs without a DB: catalog falls back to the in-memory CATALOG_PRODUCTS dict
(catalog._get_db monkeypatched to None) while the spine repo is the REAL
ProductRepository over the in-repo MockCollection (same rig as
test_catalog_promote.py; pytest's monkeypatch restores every patched global).
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
from pydantic import ValidationError  # noqa: E402

import api.dependencies as deps_mod  # noqa: E402
import api.services.cache as cache_mod  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
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


def _bvi_doc(doc_id="clx0review001", sku="RVSKU1", **over):
    """A BVI-import-shaped catalog_products doc (long-form category, top-level
    AND nested pricing, needs_review=True) -- see test_catalog_promote._bvi_doc."""
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
        "pricing": {
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "discount_category": "LUXURY",
        },
        "images": ["https://cdn.shopify.com/s/files/1/vo5051.jpg"],
        "attributes": {
            "brand_name": "Vogue",
            "model_no": "VO5051",
            "colour_code": "BLK",
        },
        "tags": ["eyewear"],
        "is_active": True,
        "pos_ready": False,
        "needs_review": True,
        "source": "bvi_import",
        "migrated_at": datetime.now(timezone.utc),
        "updated_at": "2026-07-10T10:00:00",
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
    MockCollection + audit recorder + cache spy. monkeypatch restores every
    patched module global on teardown (a leaked fake _get_db has broken other
    test files in CI before)."""
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


def _put(pid, payload):
    inp = catalog_mod.ProductUpdateInput(**payload)
    return asyncio.run(catalog_mod.update_catalog_product(pid, inp, _user()))


def _promote(pid, dry_run=False):
    return asyncio.run(
        catalog_mod.promote_catalog_product(pid, dry_run=dry_run, current_user=_user())
    )


# ---------------------------------------------------------------------------
# Pricing patch: all-optional, no MASS stamp, effective-value MRP rule
# ---------------------------------------------------------------------------


def test_offer_only_patch_preserves_tier_and_needs_no_mrp(env):
    # The load-bearing money fix: an offer-only patch must neither 422 on the
    # missing mrp nor stamp discount_category='MASS' over the stored LUXURY
    # tier (that widened the POS discount cap from 5% to 15%).
    doc = _bvi_doc()
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"pricing": {"offer_price": 4200.0}})

    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["pricing"]["offer_price"] == 4200.0
    assert updated["pricing"]["mrp"] == 5000.0  # untouched
    assert updated["pricing"]["discount_category"] == "LUXURY"  # NOT 'MASS'
    # Top-level mirror stays in sync for the promote payload.
    assert updated["offer_price"] == 4200.0


def test_offer_only_patch_spine_mirror_keeps_luxury_tier(env):
    # End-to-end tier assertion: an already-promoted product's spine row must
    # keep LUXURY after a catalog offer tweak (the fail-soft mirror used to
    # $set the stamped MASS onto it).
    doc = _bvi_doc(doc_id="clx0rvspine1", sku="RVSPINE1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    env["repo"].create(
        {
            "product_id": doc["id"],
            "id": doc["id"],
            "sku": "RVSPINE1",
            "discount_category": "LUXURY",
            "mrp": 5000.0,
            "offer_price": 4500.0,
        }
    )

    _put(doc["id"], {"pricing": {"offer_price": 4200.0}})

    spine = env["repo"].find_by_id(doc["id"])
    assert spine["discount_category"] == "LUXURY"
    assert spine["offer_price"] == 4200.0


def test_offer_above_existing_mrp_still_400s(env):
    doc = _bvi_doc(doc_id="clx0rvoffhi1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    with pytest.raises(HTTPException) as exc:
        _put(doc["id"], {"pricing": {"offer_price": 6000.0}})
    assert exc.value.status_code == 400
    assert "Offer price cannot exceed MRP" in str(exc.value.detail)


def test_mrp_below_existing_offer_still_400s(env):
    doc = _bvi_doc(doc_id="clx0rvmrplo1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    with pytest.raises(HTTPException) as exc:
        _put(doc["id"], {"pricing": {"mrp": 4000.0}})  # stored offer is 4500
    assert exc.value.status_code == 400


def test_pricing_patch_tier_still_validated_when_provided(env):
    with pytest.raises(ValidationError):
        catalog_mod.ProductUpdateInput(pricing={"discount_category": "BOGUS"})
    # Case-normalised via the same canonical-tier gate as the create path.
    inp = catalog_mod.ProductUpdateInput(pricing={"discount_category": "luxury"})
    assert inp.pricing.discount_category == "LUXURY"
    # And exclude_none drops everything the caller did not send.
    diff = catalog_mod.ProductUpdateInput(
        pricing={"offer_price": 100.0}
    ).pricing.model_dump(exclude_none=True)
    assert diff == {"offer_price": 100.0}


def test_create_path_pricing_still_requires_mrp():
    # PricingInput (the CREATE path) is untouched: mrp stays required.
    with pytest.raises(ValidationError):
        catalog_mod.PricingInput(offer_price=100.0)


# ---------------------------------------------------------------------------
# name / tags / brand-model mirror
# ---------------------------------------------------------------------------


def test_name_and_tags_roundtrip(env):
    doc = _bvi_doc(doc_id="clx0rvname01")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    res = _put(
        doc["id"],
        {"name": "  Vogue VO5051 Midnight  ", "tags": [" Aviator ", "", "   ", "New Arrival"]},
    )

    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["name"] == "Vogue VO5051 Midnight"  # trimmed
    assert updated["title"] == "Vogue VO5051 Midnight"  # BOTH set
    assert updated["tags"] == ["Aviator", "New Arrival"]  # empties dropped
    assert res["product"]["name"] == "Vogue VO5051 Midnight"


def test_explicit_name_wins_over_title_regen_for_that_save(env):
    # A SHORT-code category ("FR") makes the best-effort title regen actually
    # fire on an attributes patch; an explicit name in the same save must win.
    doc = _bvi_doc(doc_id="clx0rvregen1", category="FR")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(
        doc["id"],
        {"attributes": {"colour_code": "RED"}, "name": "Operator Name"},
    )
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["title"] == "Operator Name"

    # Control (per-save only, no persistent flag): the NEXT attributes-only
    # save regenerates the title again.
    _put(doc["id"], {"attributes": {"colour_code": "BLU"}})
    assert "BLU" in catalog_mod.CATALOG_PRODUCTS[doc["id"]]["title"]


def test_attributes_patch_mirrors_brand_and_model_top_level(env):
    # Courtesy mirror: the review-queue brand filter reads top-level brand.
    doc = _bvi_doc(doc_id="clx0rvmirr01")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"attributes": {"brand_name": "Prada", "model_no": "PR17"}})

    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["brand"] == "Prada"
    assert updated["model"] == "PR17"


# ---------------------------------------------------------------------------
# Diff-only category change + dictionary enforcement (unchanged semantics)
# ---------------------------------------------------------------------------


def test_diff_only_category_change_rederives_hsn_gst(env):
    doc = _bvi_doc(doc_id="clx0rvcat001", category_unmapped=True)
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    # Diff-only payload: NO hsn_code/gst_rate sent -> both re-derived.
    _put(doc["id"], {"category": "Sunglasses"})

    updated = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert updated["category"] == "SUNGLASS"
    assert updated["hsn_code"] == hsn_for_category("SUNGLASS")
    assert updated["gst_rate"] == gst_rate_for_category("SUNGLASS")
    assert updated["category_unmapped"] is False


def test_dictionary_enforcement_still_422s(env, monkeypatch):
    doc = _bvi_doc(doc_id="clx0rvdict01")
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


# ---------------------------------------------------------------------------
# expected_updated_at: additive optimistic concurrency
# ---------------------------------------------------------------------------


def test_stale_expected_updated_at_409s_without_writing(env):
    doc = _bvi_doc(doc_id="clx0rvcc0001")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    with pytest.raises(HTTPException) as exc:
        _put(
            doc["id"],
            {
                "expected_updated_at": "2026-07-09T09:00:00",  # != stored
                "description": "clobber attempt",
            },
        )
    assert exc.value.status_code == 409
    assert "changed by someone else" in str(exc.value.detail)
    # Nothing was written.
    assert "description" not in catalog_mod.CATALOG_PRODUCTS[doc["id"]]


def test_matching_expected_updated_at_saves(env):
    doc = _bvi_doc(doc_id="clx0rvcc0002")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(
        doc["id"],
        {"expected_updated_at": "2026-07-10T10:00:00", "description": "fixed copy"},
    )
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["description"] == "fixed copy"


def test_expected_updated_at_handles_datetime_typed_stored_value(env):
    ts = datetime(2026, 7, 10, 10, 0, 0)
    doc = _bvi_doc(doc_id="clx0rvcc0003", updated_at=ts)  # datetime, not str
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(
        doc["id"],
        {"expected_updated_at": ts.isoformat(), "description": "fixed copy"},
    )
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["description"] == "fixed copy"


def test_absent_expected_updated_at_keeps_last_write_wins(env):
    # The existing drawer sends nothing -> byte-identical behaviour (no 409).
    doc = _bvi_doc(doc_id="clx0rvcc0004")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    _put(doc["id"], {"description": "drawer save"})
    assert catalog_mod.CATALOG_PRODUCTS[doc["id"]]["description"] == "drawer save"


# ---------------------------------------------------------------------------
# name/tags survive promote to the spine row
# ---------------------------------------------------------------------------


def test_name_and_tags_survive_promote_to_spine(env):
    doc = _bvi_doc(doc_id="clx0rvpromo1", sku="RVPROMO1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"name": "Vogue VO5051 Midnight", "tags": ["Aviator", "New Arrival"]})
    _promote(doc["id"])

    spine = env["repo"].find_by_id(doc["id"])
    assert spine is not None
    assert spine["name"] == "Vogue VO5051 Midnight"
    # The door normalises tags (trim + lower-case, first-seen order).
    assert spine["tags"] == ["aviator", "new arrival"]
