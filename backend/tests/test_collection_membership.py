"""THE website collection membership rule (owner 2026-10-29).

"Collection C has products" and "product P is in collection C" come from ONE
implementation (services/collection_membership): Shopify's reading of the
collection's rules over what the product push SENDS (collection_registry
._facts), among the listings ON the website. The materialised
collection_products view is a cache of it, so it must agree with the
registry's own count for every rule shape the registry writes (tag,
product_type, variant_price bands, the disjunctive New Arrivals) -- the
"0 members for all 49 collections" bug was a second evaluator reading raw
catalogue fields the push never sends.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from api.services import collection_materializer as mat  # noqa: E402
from api.services import collection_membership as cm  # noqa: E402
from api.services.shopify_push import collection_registry as reg  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

LIVE = {"shopify_product_id": "gid://shopify/Product/1"}
NOW = datetime.now(timezone.utc)


def _sun(sku, brand, mrp, live=True, category="SUNGLASS", **attrs):
    return {"id": sku, "sku": sku, "brand": brand, "category": category, "mrp": mrp, "created_at": NOW,
            "attributes": attrs, "ecom": dict(LIVE) if live else {}}


def _db(products=None):
    db = StrictDB()
    db.seed(reg.REBUILD_STATE, [{"_id": "rebuild", "old_ids": []}])
    db.seed("brand_masters", [{"name": n, "is_active": True} for n in ("Ray-Ban", "Boss", "Hugo Boss", "Idee")])
    db.seed("catalog_variants", [])
    db.seed("products", [])
    db.seed("catalog_products", products if products is not None else [
        _sun("A", "Ray-Ban", 2500, gender="Men", shape="Aviator"),
        _sun("B", "Ray-Ban", 7000, gender="Women", shape="Cat Eye", polarization="Yes"),
        _sun("C", "Boss", 12000, gender="Unisex", shape="Square"),
        _sun("D", "Hugo Boss", 4000, gender="Men", shape="Square"),
        _sun("E", "Idee", 1500, live=False, gender="Kids"),  # not on the website
        _sun("F", "Idee", 3500, category="FRAME", gender="Men"),
        {**_sun("G", "Idee", 1500), "ecom": {**LIVE, "status": "DRAFT"}},  # a Shopify Draft
    ])
    db.seed("ecom_collections", [])
    return db


def _registry(db):
    reg.apply_collection_registry(db)
    return list(db["ecom_collections"].find({"ims_managed": True}))


def test_materialised_view_agrees_with_the_registry_for_every_rule_shape():
    """The parity lock: for EVERY collection the registry makes (tag,
    product_type, variant_price band, disjunctive New Arrivals), the
    registry's own count == the one rule's count == the materialised view."""
    db = _db()
    defs = {d["handle"]: d for d in reg.derive_definitions(reg.listing_facts(db), reg.brand_names(db))}
    rows = _registry(db)
    assert {r["handle"] for r in rows} == set(defs)
    shapes = set()
    for r in rows:
        for rule in r["rules"]:
            shapes.add(rule["field"])
        if r.get("disjunctive"):
            shapes.add("disjunctive")
    assert {"tag", "product_type", "variant_price", "disjunctive"} <= shapes
    out = mat.materialize_all(db)
    counts = cm.counts_by_handle(db)
    for r in rows:
        want = defs[r["handle"]]["count"]
        assert counts[r["handle"]]["count"] == want, r["handle"]
        assert out[r["collection_id"]] == want, r["handle"]
        view = {v["sku"] for v in db["collection_products"].find({"collection_id": r["collection_id"]})}
        direct = {f["sku"] for f in reg.listing_facts(db) if reg._matches(f, r["rules"], r.get("disjunctive"))}
        assert view == direct, r["handle"]


def test_the_live_shopify_rule_shapes_count_their_products():
    """The 49 live rows carry Shopify-style rules (tag EQUALS brand_carrera,
    product_type EQUALS Sunglass, variant_price ...); each now counts."""
    db = _db()
    c = cm.counts_by_handle(db, collections=[
        {"handle": "ray-ban", "collection_type": "SMART",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}]},
        {"handle": "sunglasses", "collection_type": "SMART",
         "rules": [{"field": "product_type", "relation": "EQUALS", "value": "Sunglass"}]},
        {"handle": "5000-10000", "collection_type": "SMART",
         "rules": [{"field": "variant_price", "relation": "GREATER_THAN", "value": "4999.99"},
                   {"field": "variant_price", "relation": "LESS_THAN", "value": "10000"}]},
        # the migrated Shopify shape reads the same
        {"handle": "boss", "collection_type": "SMART",
         "rules": [{"column": "TAG", "relation": "EQUALS", "condition": "brand_boss"}]},
    ])
    assert {h: v["count"] for h, v in c.items()} == {"ray-ban": 2, "sunglasses": 4, "5000-10000": 1, "boss": 1}


def test_only_listings_on_the_website_count():
    """E (no Shopify id) and G (a Shopify Draft) are not on the website: they
    never count, so a menu never links a collection showing 0 products."""
    db = _db()
    idee = {"handle": "idee", "collection_type": "SMART",
            "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_idee"}]}
    found, why = cm.members(db, idee)
    assert why is None and [f["sku"] for f in found] == ["F"]


def test_boss_and_hugo_boss_are_separate_collections():
    db = _db()
    c = {r["handle"]: r for r in _registry(db)}
    assert "boss" in c and "hugo-boss" in c
    counts = cm.counts_by_handle(db)
    assert counts["boss"]["count"] == 1 and counts["hugo-boss"]["count"] == 1


def test_a_rule_the_website_cannot_read_is_reported_never_guessed():
    """An IMS-only field (shape on the raw doc) or an IN list is never sent
    to Shopify as written: IMS reports it, counts 0, never guesses."""
    db = _db()
    for rules in ([{"field": "shape", "relation": "EQUALS", "value": "Aviator"}],
                  [{"field": "brand", "relation": "IN", "value": ["Ray-Ban", "Boss"]}]):
        found, why = cm.members(db, {"collection_type": "SMART", "rules": rules})
        assert found == [] and why and "cannot evaluate" in why
    # Sendable to Shopify, but not over a fact IMS pushes (weight) or not a
    # relation Shopify allows on that column (a tag CONTAINS): never guessed.
    for rules in ([{"column": "VARIANT_WEIGHT", "relation": "EQUALS", "condition": "32"}],
                  [{"field": "tag", "relation": "CONTAINS", "value": "brand_"}]):
        found, why = cm.members(db, {"collection_type": "SMART", "rules": rules})
        assert found == [] and why and "cannot evaluate" in why, rules


def test_relations_follow_shopify():
    f = {"tags": {"brand_rayban", "gender_men"}, "type": "Sunglass", "brand": "Ray-Ban", "prices": [2500.0, 9000.0]}
    r = cm.rule_set
    assert cm.matches(f, r({"rules": [{"field": "tag", "relation": "NOT_EQUALS", "value": "gender_men"}]})[0]) is False
    assert cm.matches(f, r({"rules": [{"field": "brand", "relation": "CONTAINS", "value": "ray"}]})[0]) is True
    assert cm.matches(f, r({"rules": [{"field": "price", "relation": "GREATER_THAN", "value": "8000"}]})[0]) is True
    assert cm.matches(f, r({"rules": [{"field": "category", "relation": "EQUALS", "value": "FRAME"}]})[0]) is False
    assert cm.matches(f, []) is False  # no rules never matches everything


def test_custom_membership_resolves_a_colour_to_its_listing():
    db = _db()
    facts = cm.online_facts(db)
    facts[0]["skus"] = {"A", "A-BLUE"}  # a colour on A's listing
    found, _ = cm.members(db, {"collection_type": "CUSTOM", "products": [{"sku": "A-BLUE", "position": 0}]}, facts)
    assert [f["sku"] for f in found] == ["A"]


def test_store_health_counts_variant_skus_of_a_listing_in_a_collection():
    db = _db()
    _registry(db)
    skus = cm.skus_in_any_collection(db)
    assert {"A", "B", "C", "D", "F"} <= skus and "E" not in skus and "G" not in skus


# ---------------------------------------------------------------------------
# Review round 1: strict writers, CUSTOM parity, every SKU, push-sent facts
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


def test_writers_read_strictly_and_keep_the_last_view(monkeypatch):
    """An unreadable catalogue is NEVER "no products" for a writer: the
    strict read raises, materialize_all rewrites nothing and one
    collection's refresh keeps its rows (a count stays fail-soft)."""
    db = _db()
    rows = _registry(db)
    mat.materialize_all(db)
    before = sorted((r["collection_id"], r["sku"]) for r in db["collection_products"].find({}))
    assert before

    def _down(db, include_ready=False):
        raise RuntimeError("Mongo down")

    monkeypatch.setattr(reg, "listing_facts", _down)
    assert cm.online_facts(db) == []
    with pytest.raises(RuntimeError):
        cm.online_facts(db, strict=True)
    with pytest.raises(RuntimeError):
        mat.materialize_all(db)
    assert mat.materialize_collection(db, rows[0]) == 0
    assert sorted((r["collection_id"], r["sku"]) for r in db["collection_products"].find({})) == before


def test_custom_collections_are_a_cache_of_the_one_rule_too():
    """CUSTOM membership is materialised by the SAME rule as SMART: the
    manual members that are listings on the website, in position order --
    E is not on the website, so it is in no website collection."""
    db = _db()
    coll = {"collection_id": "cur", "handle": "curated", "collection_type": "CUSTOM",
            "products": [{"sku": "C", "position": 0}, {"sku": "E", "position": 1}, {"sku": "A", "position": 2}]}
    db["ecom_collections"].insert_one(dict(coll))
    mat.materialize_all(db)
    view = [r["sku"] for r in sorted(db["collection_products"].find({"collection_id": "cur"}),
                                     key=lambda r: r["position"])]
    assert view == cm.member_skus(db, coll, every_sku=True) == ["C", "A"]


def test_vendor_is_the_field_the_push_sends():
    """product_input sends vendor = the listing view's own ``brand``: a doc
    whose brand lives only in attributes has no vendor on Shopify, so a
    VENDOR rule never counts it."""
    db = _db([{**_sun("A", "Ray-Ban", 2500), "brand": None, "attributes": {"brand_name": "Ray-Ban"}},
              _sun("B", "Ray-Ban", 2500)])
    found, _ = cm.members(db, {"collection_type": "SMART",
                               "rules": [{"column": "VENDOR", "relation": "EQUALS", "condition": "Ray-Ban"}]})
    assert [f["sku"] for f in found] == ["B"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_price_bands_read_the_prices_the_push_sends_per_colour():
    """A model listing prices every colour by its OWN offer (the push's
    listing rows): a colour at Rs 9,000 under a Rs 12,990 MRP puts the
    listing in the Rs 5,000-10,000 band on Shopify, so IMS counts it there
    too; the listing carries every colour SKU, lead first."""
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    pids = ml._family(db, [("Gold", "001"), ("Black", "002/58", "58", {"offer": 9000.0})])
    db["catalog_products"].update_one({"sku": db["products"].find_one({"product_id": pids[0]})["sku"]},
                                      {"$set": {"ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    lead = ml._twin(db, pids[0])
    colour = db["products"].find_one({"product_id": pids[1]})["sku"]
    fact = cm.facts_for(db, lead)
    assert 9000.0 in fact["prices"] and 12990.0 in fact["prices"]
    assert fact["skus"][0] == lead["sku"] and colour in fact["skus"]
    band = {"collection_type": "SMART", "rules": [
        {"field": "variant_price", "relation": "GREATER_THAN", "value": "4999.99"},
        {"field": "variant_price", "relation": "LESS_THAN", "value": "10000"}]}
    assert cm.member_skus(db, band, [fact]) == [lead["sku"]]
    assert cm.member_skus(db, band, [fact], every_sku=True) == fact["skus"]
