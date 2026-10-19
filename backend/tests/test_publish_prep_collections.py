"""Publish prep (owner 2026-10-19): the collections fill and keep their rules.

- the tags a product carries include the storefront's own spellings the
  collection rules use (polarized, cat eye, product_sunglass ...);
- one brand collection per brand with a listing on the website: a brand's
  existing collection (in IMS, or on Shopify only) is marked and published,
  never overwritten; one another brand claims is never taken; a new one is
  made only when none exists anywhere (review of #1241);
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


LIVE = {"shopify_product_id": "gid://shopify/Product/1"}


def _db():
    db = StrictDB()
    db.seed("brand_masters", [{"name": n, "is_active": True} for n in (
        "Ray-Ban", "Polaroid", "Idee", "Dolce & Gabbana", "Oakley", "Maui Jim")])
    db.seed("catalog_products", [
        {"id": "a", "sku": "A", "brand": "RAY-BAN", "ecom": dict(LIVE)},
        {"id": "b", "sku": "B", "brand": "Polaroid", "ecom": dict(LIVE)},
        {"id": "c", "sku": "C", "brand": "Idee", "ecom": {}},  # not on the website: no collection
        {"id": "d", "sku": "D", "brand": "Idee", "deleted_at": "x", "ecom": dict(LIVE)},
        {"id": "e", "sku": "E", "brand": "Dolce & Gabbana", "ecom": dict(LIVE)},
        {"id": "f", "sku": "F", "brand": "Oakley", "ecom": dict(LIVE)},
        {"id": "g", "sku": "G", "brand": "Maui Jim", "ecom": dict(LIVE)},
    ])
    db.seed("ecom_collections", [
        {"title": "D&G", "handle": "dg", "collection_type": "SMART", "locally_modified": False,
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_dolce-gabbana"}],
         "shopify_collection_id": "gid://shopify/Collection/8"},
        {"title": "rayban", "handle": "rayban", "collection_type": "SMART",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}],
         "shopify_collection_id": "gid://shopify/Collection/9", "locally_modified": False},
        {"title": "Ray Ban Sunglass", "handle": "ray-ban-sunglass", "collection_type": "SMART",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"},
                   {"field": "type", "relation": "EQUALS", "value": "Sunglass"}]},
    ])
    return db


# What Shopify holds: a 2-rule Oakley collection (vendor OR tag) and a Maui
# Jim one made in the admin after the import (in IMS neither exists), and a
# 'Polaroid AND sunglass' one that is NOT the brand's.
SHOP = [
    {"id": "gid://shopify/Collection/30", "handle": "oakley-all", "title": "Oakley",
     "ruleSet": {"appliedDisjunctively": True, "rules": [
         {"column": "VENDOR", "relation": "EQUALS", "condition": "Oakley"},
         {"column": "TAG", "relation": "EQUALS", "condition": "brand_oakley"}]}},
    {"id": "gid://shopify/Collection/31", "handle": "maui-jim", "title": "Maui Jim", "ruleSet": None},
    {"id": "gid://shopify/Collection/32", "handle": "polaroid-sunglasses", "title": "Polaroid Sunglasses",
     "ruleSet": {"appliedDisjunctively": False, "rules": [
         {"column": "TAG", "relation": "EQUALS", "condition": "brand_polaroid"},
         {"column": "TAG", "relation": "EQUALS", "condition": "product_sunglass"}]}},
]


class _Fake:
    def __init__(self, shop=None, fail_publish=()):
        self.calls = []
        self.shop = SHOP if shop is None else shop
        self.fail_publish = set(fail_publish)

    async def __call__(self, db, query, variables):  # noqa: ARG002
        self.calls.append((query, variables))
        if "imsCollectionsRead" in query:
            return {"data": {"collections": {"pageInfo": {"hasNextPage": False}, "nodes": self.shop}}}
        if "collectionCreate" in query:
            return {"data": {"collectionCreate": {"collection": {"id": "gid://shopify/Collection/50"}, "userErrors": []}}}
        if "collectionUpdate" in query:
            return {"data": {"collectionUpdate": {"collection": {"id": variables["input"]["id"]}, "userErrors": []}}}
        if "publishablePublish" in query:
            errs = [{"field": "id", "message": "boom"}] if variables["id"] in self.fail_publish else []
            return {"data": {"publishablePublish": {"userErrors": errs}}}
        raise AssertionError(query)

    def published(self):
        return [v["id"] for q, v in self.calls if "publishablePublish" in q]

    def writes(self):
        return [q for q, _ in self.calls if "collectionCreate" in q or "collectionUpdate" in q]


def _live(monkeypatch, fake=None):
    fake = fake or _Fake()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    return fake


def _rows(db):
    return {d["handle"]: d for d in db["ecom_collections"].find({})}


def test_existing_brand_collections_are_marked_and_published_never_overwritten(monkeypatch):
    db = _db()
    fake = _live(monkeypatch)
    out = asyncio.run(shopify_push.ensure_brand_collections(db))
    assert out == {"created": ["polaroid"], "reused": ["dg", "rayban"],
                   "adopted": ["maui-jim", "oakley-all"], "errors": []}
    assert fake.writes() == [], "nothing a brand already has is ever sent"
    assert set(fake.published()) == {"gid://shopify/Collection/8", "gid://shopify/Collection/9",
                                      "gid://shopify/Collection/30", "gid://shopify/Collection/31"}
    rows = _rows(db)
    # Reused: marked, NOT queued; adopted from Shopify: recorded, not queued.
    assert rows["rayban"]["auto_brand"] == "brand_rayban" and rows["rayban"]["locally_modified"] is False
    assert rows["dg"]["auto_brand"] == "brand_dolcegabbana" and rows["dg"]["locally_modified"] is False
    oak = rows["oakley-all"]
    assert oak["auto_brand"] == "brand_oakley" and oak["locally_modified"] is False
    assert oak["shopify_collection_id"] == "gid://shopify/Collection/30" and len(oak["rules"]) == 2
    assert rows["maui-jim"]["shopify_collection_id"] == "gid://shopify/Collection/31"
    # 'brand AND sunglass' is not the brand's: Polaroid gets its own, queued.
    pol = rows["polaroid"]
    assert pol["title"] == "Polaroid" and pol["collection_type"] == "SMART" and pol["locally_modified"] is True
    assert pol["rules"] == [{"field": "tag", "relation": "EQUALS", "value": "brand_polaroid"}]
    assert "idee" not in rows, "a brand with nothing live gets none"
    assert "auto_brand" not in rows["ray-ban-sunglass"]
    fake.calls.clear()
    again = asyncio.run(shopify_push.ensure_brand_collections(db))
    assert again == {"created": [], "reused": [], "adopted": [], "errors": []}
    assert fake.published() == [] and fake.writes() == []


def test_a_collection_another_brand_claims_is_never_taken(monkeypatch):
    """'Hugo Boss' and 'Hugo' both read brand_hugo: the one marked for one of
    them stays that brand's; the other gets its own, and no mark flips."""
    db = StrictDB()
    db.seed("brand_masters", [{"name": "Hugo Boss", "is_active": True}, {"name": "Hugo", "is_active": True}])
    db.seed("catalog_products", [{"id": "a", "sku": "A", "brand": "Hugo Boss", "ecom": dict(LIVE)},
                                 {"id": "b", "sku": "B", "brand": "Hugo", "ecom": dict(LIVE)}])
    db.seed("ecom_collections", [{"title": "HUGO", "handle": "hugo-x", "collection_type": "SMART",
                                  "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_hugo"}],
                                  "shopify_collection_id": "gid://shopify/Collection/7", "locally_modified": False}])
    hugo_shop = [{"id": "gid://shopify/Collection/7", "handle": "hugo-x", "title": "HUGO",
                  "ruleSet": {"rules": [{"column": "TAG", "relation": "EQUALS", "condition": "brand_hugo"}]}}]
    _live(monkeypatch, _Fake(shop=hugo_shop))
    out = asyncio.run(shopify_push.ensure_brand_collections(db))
    assert out["errors"] == []
    rows = _rows(db)
    assert sorted(d["auto_brand"] for d in rows.values()) == ["brand_hugo", "brand_hugoboss"]
    before = {h: d["auto_brand"] for h, d in rows.items()}
    assert asyncio.run(shopify_push.ensure_brand_collections(db))["reused"] == []
    assert {h: d["auto_brand"] for h, d in _rows(db).items()} == before, "the mark never flips"
    assert [d["handle"] for d in db["ecom_collections"].find({"locally_modified": True})] != ["hugo-x"]


def test_one_brand_failing_never_stops_the_rest(monkeypatch):
    db = _db()
    _live(monkeypatch, _Fake(fail_publish={"gid://shopify/Collection/8"}))
    out = asyncio.run(shopify_push.ensure_brand_collections(db))
    assert len(out["errors"]) == 1 and "Dolce" in out["errors"][0]
    assert out["created"] == ["polaroid"] and "rayban" in out["reused"]
    assert "auto_brand" not in _rows(db)["dg"], "not published -> not marked, tried again next press"


def test_a_dark_press_matches_and_makes_nothing():
    db = _db()
    out = asyncio.run(shopify_push.ensure_brand_collections(db))
    assert out["dark"] is True and len(_rows(db)) == 3


def test_a_new_brand_collection_is_created_and_published_and_an_imported_one_leaves_the_queue(monkeypatch):
    db = _db()
    fake = _live(monkeypatch)
    asyncio.run(shopify_push.ensure_brand_collections(db))
    db["ecom_collections"].update_one({"handle": "rayban"}, {"$set": {"locally_modified": True}})  # a human edit
    fake.calls.clear()
    for doc in db["ecom_collections"].find({"locally_modified": True}):
        doc.pop("_id", None)  # what the sweep hands over
        res = asyncio.run(shopify_push.push_collection(db, doc))
        assert res.ok, res.error
        assert (res.collection_publication or {}).get("published") is True
    sent = [v["input"] for q, v in fake.calls if "collectionCreate" in q]
    assert sent[0]["ruleSet"]["rules"] == [{"column": "TAG", "relation": "EQUALS", "condition": "brand_polaroid"}]
    # The imported 'rayban' (no collection_id) and the new one both left the queue.
    assert list(db["ecom_collections"].find({"locally_modified": True})) == []


def test_a_rule_set_with_a_malformed_rule_is_never_sent_in_part():
    out, lost = shopify_push.convert_rule_set([{"field": "tag", "relation": "EQUALS", "value": "x"}, "junk"])
    assert lost and out == [{"column": "TAG", "relation": "EQUALS", "condition": "x"}]
    assert "ruleSet" not in shopify_push.build_collection_input(
        {"collection_type": "SMART", "title": "t", "handle": "t",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "x"}, "junk"]})


def test_a_new_smart_collection_whose_rules_cannot_be_sent_is_refused(monkeypatch):
    db = StrictDB()
    fake = _live(monkeypatch)
    doc = {"collection_id": "x", "title": "Odd", "handle": "odd", "collection_type": "SMART",
           "rules": [{"field": "nonsense", "relation": "EQUALS", "value": "1"}]}
    res = asyncio.run(shopify_push.push_collection(db, doc))
    assert res.ok is False and fake.calls == [] and "nonsense" in res.rules_note
