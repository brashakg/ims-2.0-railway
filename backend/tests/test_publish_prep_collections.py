"""Publish prep (owner 2026-10-19): the collections fill and keep their rules.

- the tags a product carries include the storefront's own spellings the
  collection rules use (polarized, cat eye, product_sunglass ...);
- one brand collection per brand with a listing on the website, reusing the
  brand's existing collection, never an empty one;
- a collection imported without a collection_id leaves the queue after a clean
  push; a new collection IMS creates (and a brand one) goes on the Online Store.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from api.services import shopify_push  # noqa: E402
from api.services.shopify_tag_gen import generate_attribute_tags  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402


def test_products_carry_the_tags_the_collection_rules_use():
    from api.services.shopify_push.product_input import ims_product_tags

    tags = ims_product_tags({"category": "SUNGLASS", "brand": "Prada", "attributes": {
        "polarization": "Yes", "shape": "Cat Eye", "uv_protection": "100"}})
    for t in ("brand_prada", "polarization_yes", "shape_cat-eye", "uvprotection_100",  # IMS's own
              "polarization_polarized", "polarized", "shape_cat eye", "cat eye",
              "uvprotection_100-uv-protection", "product_sunglass", "prada-sunglasses"):
        assert t in tags, t
    frame = ims_product_tags({"category": "FRAME", "attributes": {"brand_name": "Dolce & Gabbana", "shape": "Wayfarer"}})
    assert {"product_spectacles", "brand_dolcegabbana", "brand_dolce-gabbana", "wayfarer"} <= set(frame)
    assert "prada-sunglasses" not in generate_attribute_tags("FRAME", {"brand_name": "Prada"})
    assert "brand_hugo" in generate_attribute_tags("FRAME", {"brand_name": "Hugo Boss"})
    assert generate_attribute_tags("FRAME", {}) == []


def _db():
    db = StrictDB()
    db.seed("brand_masters", [{"name": "Ray-Ban", "is_active": True}, {"name": "Polaroid", "is_active": True},
                              {"name": "Idee", "is_active": True}])
    live = {"shopify_product_id": "gid://shopify/Product/1"}
    db.seed("catalog_products", [
        {"id": "a", "sku": "A", "brand": "RAY-BAN", "ecom": dict(live)},
        {"id": "b", "sku": "B", "brand": "Polaroid", "ecom": dict(live)},
        {"id": "c", "sku": "C", "brand": "Idee", "ecom": {}},  # not on the website: no collection
        {"id": "d", "sku": "D", "brand": "Idee", "deleted_at": "x", "ecom": dict(live)},
    ])
    db.seed("ecom_collections", [
        {"title": "rayban", "handle": "rayban", "collection_type": "SMART",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}],
         "shopify_collection_id": "gid://shopify/Collection/9", "locally_modified": False},
        {"title": "Ray Ban Sunglass", "handle": "ray-ban-sunglass", "collection_type": "SMART",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"},
                   {"field": "type", "relation": "EQUALS", "value": "Sunglass"}]},
    ])
    return db


def test_one_brand_collection_per_brand_on_the_website_reusing_the_existing_one():
    db = _db()
    out = shopify_push.ensure_brand_collections(db)
    assert out == {"created": ["polaroid"], "reused": ["rayban"]}
    rows = {d["handle"]: d for d in db["ecom_collections"].find({})}
    assert len(rows) == 3, "Ray-Ban reused, never duplicated; Idee (nothing live) gets none"
    pol = rows["polaroid"]
    assert pol["title"] == "Polaroid" and pol["collection_type"] == "SMART" and pol["collection_id"]
    assert pol["rules"] == [{"field": "tag", "relation": "EQUALS", "value": "brand_polaroid"}]
    assert pol["locally_modified"] is True and rows["rayban"]["locally_modified"] is True
    assert shopify_push.ensure_brand_collections(db) == {"created": [], "reused": []}, "re-runnable"


class _Fake:
    def __init__(self):
        self.calls = []

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append((query, variables))
        if "collectionCreate" in query:
            return {"data": {"collectionCreate": {"collection": {"id": "gid://shopify/Collection/50"}, "userErrors": []}}}
        if "collectionUpdate" in query:
            return {"data": {"collectionUpdate": {"collection": {"id": variables["input"]["id"]}, "userErrors": []}}}
        if "publishablePublish" in query:
            return {"data": {"publishablePublish": {"userErrors": []}}}
        raise AssertionError(query)


def _live(monkeypatch):
    fake = _Fake()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    return fake


def test_brand_collection_is_created_and_published_and_an_imported_one_leaves_the_queue(monkeypatch):
    db = _db()
    shopify_push.ensure_brand_collections(db)
    fake = _live(monkeypatch)
    for doc in db["ecom_collections"].find({"locally_modified": True}):
        doc.pop("_id", None)  # what the sweep hands over
        res = asyncio.run(shopify_push.push_collection(db, doc))
        assert res.ok, res.error
        assert (res.collection_publication or {}).get("published") is True
    sent = [v["input"] for q, v in fake.calls if "collectionCreate" in q]
    assert sent[0]["ruleSet"]["rules"] == [{"column": "TAG", "relation": "EQUALS", "condition": "brand_polaroid"}]
    # The imported 'rayban' (no collection_id) and the new one both left the queue.
    assert list(db["ecom_collections"].find({"locally_modified": True})) == []


def test_a_new_smart_collection_whose_rules_cannot_be_sent_is_refused(monkeypatch):
    db = StrictDB()
    fake = _live(monkeypatch)
    doc = {"collection_id": "x", "title": "Odd", "handle": "odd", "collection_type": "SMART",
           "rules": [{"field": "nonsense", "relation": "EQUALS", "value": "1"}]}
    res = asyncio.run(shopify_push.push_collection(db, doc))
    assert res.ok is False and fake.calls == [] and "nonsense" in res.rules_note
