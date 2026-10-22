"""A product's weight, end to end (owner 2026-10-22: "she filled weight in a
lot of products, now she can't see it").

ONE copy: weight (grams) lives on the billing SPINE (`products`).
  * the catalogue door's edit (the review editor, the drawer) writes it THERE,
    never onto the twin alone -- a twin with no spine (an import awaiting
    promote) keeps it until promote carries it across;
  * the catalogue door's create writes it on the spine;
  * the catalogue door's reads (the drawer / review editor's GET, the list)
    show the spine's;
  * a spine weight edit queues the listing without copying weight to the twin;
  * the Shopify push sends each variant's spine weight as
    inventoryItem.measurement.weight in GRAMS, on create AND update, only
    when one is known (never 0);
  * the backfill script queues the live listings that have a weight.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))

import pytest  # noqa: E402

import api.dependencies as deps_mod  # noqa: E402
import api.services.cache as cache_mod  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services.shopify_push.product_input import (  # noqa: E402
    build_variant_price_inputs,
    with_spine_weights,
)
from api.services.shopify_push.variants import build_variant_seed_rows  # noqa: E402
from api.services.web_page_type import STANDARD  # noqa: E402
from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402
from test_catalog_review_put import _AuditRecorder, _bvi_doc, _CacheSpy, _user  # noqa: E402

import queue_weight_listings as backfill  # noqa: E402


@pytest.fixture()
def env(monkeypatch):
    catalog_mod.CATALOG_PRODUCTS.clear()
    repo = ProductRepository(MockCollection("products"))
    monkeypatch.setattr(catalog_mod, "_get_db", lambda: None)
    monkeypatch.setattr(deps_mod, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps_mod, "get_audit_repository", lambda: _AuditRecorder())
    monkeypatch.setattr(cache_mod, "cache", _CacheSpy())
    yield repo
    catalog_mod.CATALOG_PRODUCTS.clear()


def _put(pid, payload):
    inp = catalog_mod.ProductUpdateInput(**payload)
    return asyncio.run(catalog_mod.update_catalog_product(pid, inp, _user()))


def _twin_with_spine(repo, weight=None):
    doc = _bvi_doc(doc_id="T-1", sku="SKU-1", needs_review=False)
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    spine = {"product_id": "P-1", "pim_product_id": "T-1", "sku": "SKU-1", "mrp": 5000.0}
    if weight is not None:
        spine["weight"] = weight
    repo.create(spine)
    return doc


# --------------------------------------------------------------------------
# Entry: the catalogue door (review editor, drawer) and its create
# --------------------------------------------------------------------------


def test_review_save_writes_weight_on_the_spine_not_the_twin(env):
    _twin_with_spine(env)
    res = _put("T-1", {"weight": 40.0})
    assert env.find_by_id("P-1")["weight"] == 40.0
    assert "weight" not in catalog_mod.CATALOG_PRODUCTS["T-1"]
    assert res["product"]["weight"] == 40.0


@pytest.mark.parametrize("cleared", [None, 0])
def test_null_or_zero_clears_the_spine_weight(env, cleared):
    _twin_with_spine(env, weight=40.0)
    res = _put("T-1", {"weight": cleared})
    assert "weight" not in env.find_by_id("P-1")
    assert res["product"]["weight"] is None


def test_an_absent_weight_leaves_it_alone(env):
    _twin_with_spine(env, weight=40.0)
    _put("T-1", {"description": "x"})
    assert env.find_by_id("P-1")["weight"] == 40.0


def test_a_negative_weight_is_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        catalog_mod.ProductUpdateInput(weight=-1)
    with pytest.raises(ValidationError):
        catalog_mod.ProductCreateInput(
            category="FR", attributes={}, weight=-1, pricing={"mrp": 1.0}
        )


def test_an_import_with_no_spine_keeps_its_weight_and_promote_carries_it(env):
    doc = _bvi_doc(doc_id="T-IMP", sku="IMP-1")
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc
    _put("T-IMP", {"weight": 28.0})
    assert catalog_mod.CATALOG_PRODUCTS["T-IMP"]["weight"] == 28.0
    asyncio.run(catalog_mod.promote_catalog_product("T-IMP", dry_run=False, current_user=_user()))
    assert env.find_by_id("T-IMP")["weight"] == 28.0


def test_catalog_create_door_puts_weight_on_the_spine(env):
    body = catalog_mod.ProductCreateInput(
        category="FR",
        attributes={"brand_name": "Vogue", "model_no": "VO5051", "colour_code": "BLK"},
        weight=26.0,
        pricing={"mrp": 5000.0, "offer_price": 4800.0, "discount_category": "LUXURY"},
    )
    out = asyncio.run(catalog_mod.create_catalog_product(body, _user()))
    pid = out["product"]["id"]
    assert env.find_by_id(pid)["weight"] == 26.0
    assert out["product"]["weight"] == 26.0


# --------------------------------------------------------------------------
# Display: what the drawer and the review editor read
# --------------------------------------------------------------------------


def test_the_catalogue_reads_show_the_spine_weight(env):
    _twin_with_spine(env, weight=40.0)
    one = asyncio.run(catalog_mod.get_catalog_product("T-1", current_user=_user()))
    assert one["product"]["weight"] == 40.0
    listed = asyncio.run(
        catalog_mod.list_catalog_products(
            category=None, brand=None, search=None, is_active="all", needs_review=None, source=None,
            page=1, limit=50, sort=None, current_user=_user(),
        )
    )
    assert [p.get("weight") for p in listed["products"] if p["id"] == "T-1"] == [40.0]
    # The read never writes the spine's value onto the twin.
    assert "weight" not in catalog_mod.CATALOG_PRODUCTS["T-1"]


# --------------------------------------------------------------------------
# One copy: a spine weight edit queues the listing, copies nothing
# --------------------------------------------------------------------------


def test_spine_weight_edit_queues_the_twin_without_copying_weight():
    db = StrictDB()
    db.get_collection("catalog_products").insert_one(
        {"id": "T-1", "sku": "SKU-1", "ecom": {"status": "ACTIVE", "locally_modified": False}}
    )
    pm.mirror_update_to_catalog_twin(
        product_id="P-1", current={"pim_product_id": "T-1"}, patch={"weight": 40.0}, db=db
    )
    twin = db.get_collection("catalog_products").find_one({"id": "T-1"})
    assert twin["ecom"]["locally_modified"] is True
    assert "weight" not in twin


# --------------------------------------------------------------------------
# Shopify: grams on the variant, create and update, never 0
# --------------------------------------------------------------------------


def _push_db(weights):
    """Spines LINKED to the twin T-1: the first SKU is its lead (pim_product_id
    = the twin's id), every other one a colour/size of it (variant_of)."""
    db = StrictDB()
    lead = None
    for sku, w in weights.items():
        doc = {"product_id": "P-" + sku, "sku": sku}
        if lead is None:
            doc["pim_product_id"] = "T-1"
            lead = doc["product_id"]
        else:
            doc["variant_of"] = lead
        if w is not None:
            doc["weight"] = w
        db["products"].insert_one(doc)
    return db


TWIN = {"id": "T-1", "sku": "SKU-1", "mrp": 5000.0, "offer_price": 4800.0,
        "ecom": {"shopify_product_id": "gid://shopify/Product/1", "shopify_variant_id": "gid://shopify/ProductVariant/9"}}
GRAMS = {"weight": {"value": 40.0, "unit": "GRAMS"}}


def test_create_seed_rows_carry_the_spine_weight_in_grams():
    product = with_spine_weights(_push_db({"SKU-1": 40.0}), dict(TWIN), [])
    rows = build_variant_seed_rows(product, [])
    assert rows[0]["row"]["inventoryItem"] == {"sku": "SKU-1", "measurement": GRAMS}


def test_update_price_rows_carry_each_variants_own_weight():
    variants = [
        {"sku": "SKU-1", "shopify_variant_id": "gid://shopify/ProductVariant/9"},
        {"sku": "SKU-2", "shopify_variant_id": "gid://shopify/ProductVariant/10", "mrp": 5000.0},
    ]
    product = with_spine_weights(_push_db({"SKU-1": 40.0, "SKU-2": 33.0}), dict(TWIN), variants)
    rows, _ = build_variant_price_inputs(product, variants)
    assert [r["inventoryItem"]["measurement"]["weight"]["value"] for r in rows] == [40.0, 33.0]
    assert {r["inventoryItem"]["measurement"]["weight"]["unit"] for r in rows} == {"GRAMS"}


@pytest.mark.parametrize("stored", [None, 0])
def test_no_known_weight_sends_no_weight(stored):
    product = with_spine_weights(_push_db({"SKU-1": stored}), dict(TWIN), [])
    rows, _ = build_variant_price_inputs(product, [{"sku": "SKU-1", "shopify_variant_id": "gid://shopify/ProductVariant/9"}])
    assert "inventoryItem" not in rows[0]
    assert "measurement" not in build_variant_seed_rows(product, [])[0]["row"]["inventoryItem"]


def test_a_spine_that_only_shares_the_sku_gives_no_weight():
    """Never by SKU alone (catalog._spine_ids): an unrelated product's spine
    with the same SKU is not this twin's."""
    db = StrictDB()
    db["products"].insert_one({"product_id": "P-OTHER", "sku": "SKU-1", "weight": 99.0})
    product = with_spine_weights(db, dict(TWIN), [])
    rows, _ = build_variant_price_inputs(product, [{"sku": "SKU-1", "shopify_variant_id": "gid://shopify/ProductVariant/9"}])
    assert "inventoryItem" not in rows[0]


def test_push_variant_prices_reads_the_weight_itself(monkeypatch):
    """The standalone price push (not via push_product) looks the weight up."""
    from api.services.shopify_push import variants as variants_mod

    monkeypatch.setattr(variants_mod, "product_push_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(variants_mod, "_live_or_reason", lambda *_a, **_k: (False, "dark"))
    res = asyncio.run(variants_mod.push_variant_prices(_push_db({"SKU-1": 40.0}), dict(TWIN), []))
    assert res.payload["variants"][0]["inventoryItem"] == {"measurement": GRAMS}


def test_dry_run_push_plan_carries_the_weight(monkeypatch):
    """The whole press, DARK (simulated): the plan's variant rows send grams."""
    from api.services.shopify_push import product as push_mod

    db = _push_db({"SKU-1": 40.0})
    monkeypatch.setattr(push_mod, "product_push_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(push_mod, "model_listing", lambda *_a, **_k: None)
    monkeypatch.setattr(push_mod, "product_photo_urls", lambda *_a, **_k: ["https://x/1.jpg"])
    monkeypatch.setattr(push_mod, "effective_page_type", lambda *_a, **_k: STANDARD)
    monkeypatch.setattr(push_mod, "_live_or_reason", lambda *_a, **_k: (False, "dark"))

    async def _no_stock(*_a, **_k):
        return None

    monkeypatch.setattr(push_mod, "plan_product_stock", _no_stock)
    res = asyncio.run(push_mod.push_product(db, dict(TWIN), [], blocked=False))
    assert res.mode == "SIMULATED"
    assert res.variant_prices["variants"][0]["inventoryItem"] == {"measurement": GRAMS}


# --------------------------------------------------------------------------
# Backfill: queue the live listings that have a weight
# --------------------------------------------------------------------------


def test_backfill_plan_queues_only_live_listings_with_a_weight():
    spines = [
        {"product_id": "P-LIVE", "pim_product_id": "T-LIVE", "sku": "LIVE", "weight": 40.0},
        {"product_id": "P-DRAFT", "pim_product_id": "T-DRAFT", "sku": "DRAFT", "weight": 30.0},
        {"product_id": "P-NOW", "pim_product_id": "T-NOW", "sku": "NOW"},
        # A colour of the live model: its MODEL's listing is the one queued.
        {"product_id": "P-COL", "pim_product_id": "T-COL", "sku": "COL", "weight": 41.0, "variant_of": "P-LIVE"},
    ]
    live = {"shopify_product_id": "gid://shopify/Product/1"}
    twins = [
        {"id": "T-LIVE", "sku": "LIVE", "ecom": live},
        {"id": "T-DRAFT", "sku": "DRAFT", "ecom": {}},
        {"id": "T-NOW", "sku": "NOW", "ecom": live},
        {"id": "T-COL", "sku": "COL", "ecom": {"variant_of": {"product_id": "P-LIVE"}}},
    ]
    p = backfill.plan(spines, twins)
    assert p["weighted"] == 3
    assert p["queue"] == [{"twin_id": "T-LIVE", "sku": "LIVE", "weighted_skus": ["LIVE", "COL"]}]
    assert p["not_live"] == ["DRAFT"]


def test_backfill_skips_listings_that_are_off():
    spines = [{"product_id": f"P-{i}", "pim_product_id": f"T-{i}", "sku": f"S{i}", "weight": 30.0} for i in range(5)]
    live = {"shopify_product_id": "gid://shopify/Product/1"}
    twins = [
        {"id": "T-0", "sku": "S0", "ecom": live, "is_active": False},
        {"id": "T-1", "sku": "S1", "ecom": live, "deleted_at": "2026-10-01"},
        {"id": "T-2", "sku": "S2", "ecom": {**live, "status": "ARCHIVED"}},
        {"id": "T-3", "sku": "S3", "ecom": {**live, "taken_down_at": "2026-10-01"}},
        {"id": "T-4", "sku": "S4", "ecom": live},
    ]
    p = backfill.plan(spines, twins)
    assert [r["sku"] for r in p["queue"]] == ["S4"]
    assert sorted(p["not_live"]) == ["S0", "S1", "S2", "S3"]


def test_backfill_copies_a_stranded_twin_weight_onto_its_linked_spine():
    spines = [
        {"product_id": "P-A", "pim_product_id": "T-A", "sku": "A"},
        {"product_id": "P-B", "pim_product_id": "T-B", "sku": "B", "weight": 20.0},
        # Only the SKU matches T-C: not its spine, nothing copied.
        {"product_id": "P-X", "sku": "C"},
    ]
    twins = [
        {"id": "T-A", "sku": "A", "weight": 35.0},
        {"id": "T-B", "sku": "B", "weight": 99.0},  # spine has its own: kept
        {"id": "T-C", "sku": "C", "weight": 12.0},
    ]
    rows = backfill.plan_copies(spines, twins)
    assert rows == [{"product_id": "P-A", "sku": "A", "twin_id": "T-A", "weight": 35.0}]
    db = StrictDB()
    for sp in spines:
        db["products"].insert_one(dict(sp))
    assert backfill.apply_copies(db, rows, "run2") == 1
    assert db["products"].find_one({"product_id": "P-A"})["weight"] == 35.0
    assert backfill.revert(db, "run2") == 1
    assert "weight" not in db["products"].find_one({"product_id": "P-A"})


def test_backfill_apply_and_revert():
    db = StrictDB()
    db["catalog_products"].insert_one(
        {"id": "T-LIVE", "sku": "LIVE", "ecom": {"locally_modified": False, "edit_rev": "old"}}
    )
    rows = [{"twin_id": "T-LIVE", "sku": "LIVE", "weighted_skus": ["LIVE"]}]
    assert backfill.apply(db, rows, "run1") == 1
    ecom = db["catalog_products"].find_one({"id": "T-LIVE"})["ecom"]
    assert ecom["locally_modified"] is True and ecom["edit_rev"] != "old"
    assert backfill.revert(db, "run1") == 1
    assert db["catalog_products"].find_one({"id": "T-LIVE"})["ecom"] == {"locally_modified": False, "edit_rev": "old"}
