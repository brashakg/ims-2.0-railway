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
    """An IMS-only field the push makes no tag for (hsn_code) or an IN list
    is never sent to Shopify as written: IMS reports it, counts 0, never
    guesses."""
    db = _db()
    for rules in ([{"field": "hsn_code", "relation": "EQUALS", "value": "9004"}],
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
    # The colour's catalogue doc points at A's twin (G1): THE push rule.
    db["catalog_products"].insert_one({"id": "A-BLUE", "sku": "A-BLUE", "ecom": {"variant_of": {"twin_id": "A", "sku": "A"}}})
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

    def _down(db, include_ready=False, strict=False):
        raise RuntimeError("Mongo down")

    monkeypatch.setattr(reg, "listing_facts", _down)
    assert cm.online_facts(db) == []
    with pytest.raises(RuntimeError):
        cm.online_facts(db, strict=True)
    with pytest.raises(RuntimeError):
        mat.materialize_all(db)
    assert mat.materialize_collection(db, rows[0]) is None  # unreadable, never "0 products"
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


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_colour_is_in_a_website_collection_only_when_it_is_online():
    """#1275's THE colour/size online rule (online_catalog.skus_on_shopify)
    decides which of a live listing's colour SKUs are on the website: a
    colour with no Shopify variant mapped is in no website collection
    (Store Health, the view) until its variant is."""
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    pids = ml._family(db, [("Gold", "001"), ("Black", "002/58", "58")])
    lead_sku = db["products"].find_one({"product_id": pids[0]})["sku"]
    colour = db["products"].find_one({"product_id": pids[1]})["sku"]
    db["catalog_products"].update_one({"sku": lead_sku}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    fact = next(f for f in cm.online_facts(db, strict=True) if f["sku"] == lead_sku)
    assert fact["skus"] == [lead_sku]
    db["catalog_variants"].update_one({"sku": colour}, {"$set": {"shopify_variant_id": "gid://shopify/ProductVariant/9"}})
    fact = next(f for f in cm.online_facts(db, strict=True) if f["sku"] == lead_sku)
    assert fact["skus"] == [lead_sku, colour]


def test_builder_chip_rules_read_as_the_push_sends_them():
    """The New Collection builder's chips (category, shape, gender,
    polarization) are carried as the push's own productType and filter
    tags (shape_aviator, gender_men, polarization_yes), so the one rule --
    and Shopify -- can match them (review round 2)."""
    from api.services.shopify_push.collections import convert_rule_set

    db = _db()

    def skus(rule):
        found, why = cm.members(db, {"collection_type": "SMART", "rules": [rule]})
        assert why is None, why
        return sorted(f["sku"] for f in found)

    assert convert_rule_set([{"field": "shape", "relation": "EQUALS", "value": "Aviator"}])[0] == [
        {"column": "TAG", "relation": "EQUALS", "condition": "shape_aviator"}]
    assert convert_rule_set([{"field": "category", "relation": "EQUALS", "value": "SUNGLASS"}])[0] == [
        {"column": "TYPE", "relation": "EQUALS", "condition": "Sunglass"}]
    assert skus({"field": "shape", "relation": "EQUALS", "value": "Aviator"}) == ["A"]
    assert skus({"field": "gender", "relation": "EQUALS", "value": "Men"}) == ["A", "D", "F"]
    assert skus({"field": "polarization", "relation": "EQUALS", "value": "Yes"}) == ["B"]
    assert skus({"field": "category", "relation": "EQUALS", "value": "SUNGLASS"}) == ["A", "B", "C", "D"]


def test_the_view_counts_listings_and_browses_by_listing():
    """products_count and the browse total are LISTINGS (the number the
    menu and the website show); the view keeps a row per colour SKU for the
    insights' stock joins."""
    db = _db()
    coll = {"collection_id": "c-rb", "handle": "ray-ban", "collection_type": "SMART",
            "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}]}
    db["ecom_collections"].insert_one(dict(coll))
    facts = cm.online_facts(db, strict=True)
    next(f for f in facts if f["sku"] == "A")["skus"] = ["A", "A-BLUE"]  # an online colour of A
    assert mat.materialize_collection(db, coll, facts) == 2
    assert len(list(db["collection_products"].find({"collection_id": "c-rb"}))) == 3
    assert db["ecom_collections"].find_one({"collection_id": "c-rb"})["products_count"] == 2
    page = mat.browse(db, coll)
    assert page["total"] == 2 and [r["sku"] for r in page["products"]] == ["A", "B"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_colour_not_online_does_not_put_its_price_in_a_band():
    """online_facts cuts a listing's colours to the ones on the website --
    and their prices with them: a colour at Rs 9,000 with no Shopify
    variant does not put a Rs 12,990 listing in the Rs 5,000-10,000 band
    until it is online."""
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    pids = ml._family(db, [("Gold", "001"), ("Black", "002/58", "58", {"offer": 9000.0})])
    lead_sku = db["products"].find_one({"product_id": pids[0]})["sku"]
    colour = db["products"].find_one({"product_id": pids[1]})["sku"]
    db["catalog_products"].update_one({"sku": lead_sku}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    band = {"collection_type": "SMART", "rules": [
        {"field": "variant_price", "relation": "GREATER_THAN", "value": "4999.99"},
        {"field": "variant_price", "relation": "LESS_THAN", "value": "10000"}]}
    assert cm.member_skus(db, band, cm.online_facts(db, strict=True)) == []
    db["catalog_variants"].update_one({"sku": colour}, {"$set": {"shopify_variant_id": "gid://shopify/ProductVariant/9"}})
    assert cm.member_skus(db, band, cm.online_facts(db, strict=True)) == [lead_sku]


# ---------------------------------------------------------------------------
# Review round 3
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_blocked_custom_colour_blocks_its_listing_with_or_without_the_view():
    """A blocked CUSTOM collection that names a COLOUR SKU: the view holds
    the colour's whole listing, so the lead is blocked by the view; the
    direct path must say the same before the view exists (and the colour
    itself is blocked either way)."""
    from api.services import online_block as ob
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    pids = ml._family(db, [("Gold", "001"), ("Black", "002/58", "58")])
    lead = ml._twin(db, pids[0])
    colour = db["products"].find_one({"product_id": pids[1]})["sku"]
    db["catalog_products"].update_one({"sku": lead["sku"]}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    db["catalog_variants"].update_one({"sku": colour}, {"$set": {"shopify_variant_id": "gid://shopify/ProductVariant/9"}})
    db.seed("ecom_collections", [{"collection_id": "ban", "handle": "ban", "collection_type": "CUSTOM",
                                  "online_sync_blocked": True, "products": [{"sku": colour, "position": 0}]}])
    db.seed("collection_products", [])
    colour_doc = db["catalog_products"].find_one({"sku": colour})
    before = (ob.is_blocked_from_online(lead, db), ob.is_blocked_from_online(colour_doc, db),
              ob.blocked_skus(db, [lead["sku"], colour], strict=True))
    mat.materialize_all(db)
    assert {r["sku"] for r in db["collection_products"].find({"collection_id": "ban"})} == {lead["sku"], colour}
    after = (ob.is_blocked_from_online(lead, db), ob.is_blocked_from_online(colour_doc, db),
             ob.blocked_skus(db, [lead["sku"], colour], strict=True))
    assert before == after == (True, True, {lead["sku"], colour})


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_the_registry_makes_a_collection_only_where_the_one_rule_counts_a_product():
    """A Rs 4,000 listing with an UNMAPPED Rs 12,000 colour: the website
    has no Rs 12,000 variant, so the registry must not create Over Rs
    10,000 (it read listing_facts, which still carried that price) -- every
    collection it makes has the count counts_by_handle gives it."""
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    db.seed(reg.REBUILD_STATE, [{"_id": "rebuild", "old_ids": []}])
    db.seed("brand_masters", [{"name": "Ray-Ban", "is_active": True}])
    db.seed("ecom_collections", [])
    pids = ml._family(db, [("Gold", "001", "58", {"mrp": 4000.0}),
                           ("Black", "002/58", "58", {"mrp": 12000.0})])
    lead_sku = db["products"].find_one({"product_id": pids[0]})["sku"]
    db["catalog_products"].update_one({"sku": lead_sku}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    out = reg.apply_collection_registry(db)
    rows = list(db["ecom_collections"].find({"ims_managed": True}))
    assert rows and "over-10000" not in {r["handle"] for r in rows}, out
    counts = cm.counts_by_handle(db)
    assert all(counts[r["handle"]]["count"] > 0 for r in rows), {r["handle"]: counts[r["handle"]] for r in rows}


def test_materialize_all_leaves_a_quick_share_set_alone():
    """A temporary Quick Share set is a hand-picked list (E is not on the
    website): materialize_all must not overwrite its products_count with
    the website count, nor write it into the view; the share page reads its
    own length."""
    from api.routers.catalogue_pdf import _temp_public

    db = _db()
    temp = {"collection_id": "qs", "handle": "qs-1", "collection_type": "CUSTOM", "is_temporary": True,
            "products": [{"sku": "E", "position": 0}, {"sku": "A", "position": 1}], "products_count": 2}
    db["ecom_collections"].insert_one(dict(temp))
    mat.materialize_all(db)
    assert db["ecom_collections"].find_one({"collection_id": "qs"})["products_count"] == 2
    assert list(db["collection_products"].find({"collection_id": "qs"})) == []
    assert _temp_public(dict(temp, products_count=0))["products_count"] == 2


def test_collection_insights_members_count_listings_like_products_count():
    """The Collections list / detail 'Products' figure is LISTINGS: a
    two-colour listing is one product there, as products_count, browse and
    the menu say -- the stock join still sees both colour SKUs."""
    from api.services import collection_insights as ci

    db = _db()
    coll = {"collection_id": "c-rb", "handle": "ray-ban", "collection_type": "SMART",
            "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}]}
    db["ecom_collections"].insert_one(dict(coll))
    facts = cm.online_facts(db, strict=True)
    next(f for f in facts if f["sku"] == "A")["skus"] = ["A", "A-BLUE"]
    mat.materialize_collection(db, coll, facts)
    count = db["ecom_collections"].find_one({"collection_id": "c-rb"})["products_count"]
    m = ci.member_ids(db, "c-rb")
    assert m["listings"] == count == 2 and set(m["skus"]) == {"A", "A-BLUE", "B"}
    assert ci.members_by_collection(db, ["c-rb"])["c-rb"]["listings"] == count
    assert ci.kpis(db, db["ecom_collections"].find_one({"collection_id": "c-rb"}))["members"] == count
    row = next(r for r in ci.summary(db, list(db["ecom_collections"].find({}))) if r["collection_id"] == "c-rb")
    assert row["members"] == count


def test_the_catalogue_pdf_lists_the_range_and_says_so_when_it_cannot_read(monkeypatch):
    """A SMART collection's PDF asks the one rule of EVERY listing IMS holds
    (E is not on the website, G is a Shopify Draft -- both are still the
    range), with their colours; one unreadable listing is an error the
    router reports, never a shorter or empty PDF."""
    from api.services import catalogue_pdf as pdf

    db = _db()
    db["ecom_collections"].insert_one({"collection_id": "c-idee", "handle": "idee", "collection_type": "SMART",
                                       "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_idee"}]})
    assert [d["sku"] for d in pdf.resolve_collection_products(db, "c-idee")] == ["E", "F", "G"]
    real = reg._facts

    def _flaky(db, doc, strict=False):
        if doc.get("sku") == "F":
            raise RuntimeError("model unreadable")
        return real(db, doc, strict)

    monkeypatch.setattr(reg, "_facts", _flaky)
    with pytest.raises(RuntimeError):
        pdf.resolve_collection_products(db, "c-idee")


def test_the_rule_preview_says_unreadable_not_zero(monkeypatch):
    """The New Collection preview over an unreadable catalogue reports
    'unreadable' (the page keeps Create off), never 0 matches."""
    from api.services import collection_insights as ci

    db = _db()

    def _down(db, include_ready=False, strict=False):
        raise RuntimeError("Mongo down")

    monkeypatch.setattr(reg, "listing_facts", _down)
    out = ci.preview(db, [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}])
    assert out["unreadable"] and out["match_count"] == 0


def test_a_strict_facts_read_raises_when_a_model_is_unreadable(monkeypatch):
    """_facts counts an unreadable model by the doc's own tags for a page,
    but a STRICT read (every writer) raises."""
    from api.services.shopify_push import listing

    db = _db()
    doc = db["catalog_products"].find_one({"sku": "A"})

    def _boom(db, product, variants):
        raise RuntimeError("model unreadable")

    monkeypatch.setattr(listing, "model_listing", _boom)
    assert reg._facts(db, doc)["sku"] == "A"
    with pytest.raises(RuntimeError):
        reg._facts(db, doc, strict=True)
    with pytest.raises(RuntimeError):
        cm.online_facts(db, strict=True)


# ---------------------------------------------------------------------------
# Review round 4
# ---------------------------------------------------------------------------


def _colour_world():
    """A live Gold lead (Shopify product 5) and its Black colour, whose
    variant is NOT mapped on Shopify yet; the colour has no product id."""
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    pids = ml._family(db, [("Gold", "001"), ("Black", "002/58", "58")])
    lead = ml._twin(db, pids[0])
    colour = db["products"].find_one({"product_id": pids[1]})["sku"]
    db["catalog_products"].update_one({"sku": lead["sku"]}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    return db, db["catalog_products"].find_one({"sku": lead["sku"]}), colour


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_custom_collection_has_one_membership_rule_push_view_and_menu():
    """A CUSTOM collection naming a colour whose variant is not mapped yet:
    the push puts the lead's product in the website collection, so the
    membership view, the count the menu reads and the push agree -- one
    listing, the lead."""
    from api.services.shopify_push.collections import _member_product_gids

    db, lead, colour = _colour_world()
    coll = {"collection_id": "pick", "handle": "pick", "collection_type": "CUSTOM",
            "products": [{"sku": colour, "position": 0}]}
    db.seed("ecom_collections", [dict(coll)])
    gids, _skipped = _member_product_gids(db, coll)
    assert gids == ["gid://shopify/Product/5"]
    assert [f["sku"] for f in cm.members(db, coll, cm.online_facts(db, strict=True))[0]] == [lead["sku"]]
    assert cm.counts_by_handle(db)["pick"]["count"] == len(gids) == 1


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_blocking_a_custom_collection_that_names_a_colour_delists_its_lead(monkeypatch):
    """The classifier blocks the colour's listing; the block sweep must take
    THAT listing down (the lead carries the Shopify product), and the
    unblock sweep must requeue it."""
    from api.routers import online_store_collections as osc
    from api.services import online_block as ob

    db, lead, colour = _colour_world()
    coll = {"collection_id": "ban", "handle": "ban", "collection_type": "CUSTOM",
            "online_sync_blocked": True, "products": [{"sku": colour, "position": 0}]}
    db.seed("ecom_collections", [dict(coll)])
    assert ob.is_blocked_from_online(lead, db) is True
    monkeypatch.setattr(osc, "_get_db", lambda: db)
    skus, truncated = osc._resolve_block_members(coll)
    assert [d["sku"] for d in osc._synced_member_products(db, skus)] == [lead["sku"]] and not truncated


def test_a_block_sweep_over_an_unreadable_catalogue_is_a_503_not_a_partial_sweep(monkeypatch):
    """catalogue_facts is read strictly for a sweep: one listing whose facts
    cannot be read is a 503, never silently left live."""
    from fastapi import HTTPException

    from api.routers import online_store_collections as osc

    def _facts(db, strict=False):
        if strict:
            raise RuntimeError("model listing unreadable")
        return []

    monkeypatch.setattr(osc, "_get_db", lambda: object())
    monkeypatch.setattr(cm, "catalogue_facts", _facts)
    doc = {"collection_id": "C", "collection_type": "SMART",
           "rules": [{"field": "brand", "relation": "EQUALS", "value": "Gucci"}]}
    with pytest.raises(HTTPException) as e:
        osc._resolve_block_members(doc)
    assert e.value.status_code == 503


def test_an_unreadable_catalogue_is_never_reported_as_0_products(monkeypatch):
    """A forced refresh over unreadable listings answers 503 ("the last
    membership stands"), never {materialized: True, products_count: 0};
    the lazy first browse says unreadable, never an empty page."""
    import asyncio

    from fastapi import HTTPException

    from api.routers import collections_browse as cb

    db = _db()
    coll = {"collection_id": "c-rb", "handle": "ray-ban", "collection_type": "SMART",
            "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}]}
    db["ecom_collections"].insert_one(dict(coll))

    def _down(db, include_ready=False, strict=False):
        raise RuntimeError("Mongo down")

    monkeypatch.setattr(reg, "listing_facts", _down)
    page = mat.browse(db, coll)
    assert page["unreadable"] is True and page["total"] is None, page
    monkeypatch.setattr(cb, "_get_db", lambda: db)
    with pytest.raises(HTTPException) as e:
        asyncio.run(cb.refresh_collection_membership("ray-ban", current_user={"roles": ["ADMIN"]}))
    assert e.value.status_code == 503


def test_a_failed_view_read_on_a_materialised_collection_is_unreadable_not_empty(monkeypatch):
    """Round 5: a Mongo blip reading the view of an ALREADY materialised
    collection (count_documents or find raises) is unreadable, never
    {products: [], total: 0}; the router's no-db branch says the same."""
    import asyncio

    from api.routers import collections_browse as cb

    coll = {"collection_id": "c1", "handle": "h", "materialized_at": NOW}

    class _BlipView:
        def __init__(self, fail_on):
            self.fail_on = fail_on

        def count_documents(self, *_a, **_k):
            if self.fail_on == "count":
                raise RuntimeError("mongo blip")
            return 3

        def find(self, *_a, **_k):
            raise RuntimeError("mongo blip")

    for fail_on in ("count", "find"):
        db = {mat._VIEW: _BlipView(fail_on)}
        page = mat.browse(db, coll)
        assert page["unreadable"] is True and page["total"] is None, (fail_on, page)
        assert page["products"] == []

    monkeypatch.setattr(cb, "_get_db", lambda: None)
    page = asyncio.run(cb.browse_collection_products("h", skip=0, limit=24, current_user={}))
    assert page["unreadable"] is True and page["total"] is None, page


# ---------------------------------------------------------------------------
# Review round 6
# ---------------------------------------------------------------------------


def test_a_view_built_before_the_lead_field_still_browses():
    """A view row written before this PR has no "lead" field: browse counts
    it as before, never "No products in this collection" until the next sync."""
    db = _db()
    coll = {"collection_id": "old", "handle": "old", "materialized_at": NOW}
    db.seed("collection_products", [{"collection_id": "old", "handle": "old", "sku": s, "position": i}
                                    for i, s in enumerate(["A", "B"])])
    page = mat.browse(db, coll)
    assert page["total"] == 2 and [r["sku"] for r in page["products"]] == ["A", "B"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_custom_collection_naming_a_colour_is_never_one_short_on_a_blip(monkeypatch):
    """catalog_products.find_one raising for a CUSTOM collection naming a
    colour SKU: counts_by_handle raises and materialize_collection returns
    None (the last view stands) -- never a count of 0 or a smaller view."""
    db, lead, colour = _colour_world()
    coll = {"collection_id": "pick", "handle": "pick", "collection_type": "CUSTOM",
            "products": [{"sku": colour, "position": 0}]}
    db.seed("ecom_collections", [dict(coll)])
    facts = cm.online_facts(db, strict=True)
    assert mat.materialize_collection(db, coll, facts) == 1
    before = list(db["collection_products"].find({"collection_id": "pick"}))

    cp = db["catalog_products"]

    def _blip(*_a, **_k):
        raise RuntimeError("mongo blip")

    monkeypatch.setattr(cp, "find_one", _blip)
    with pytest.raises(RuntimeError):
        cm.counts_by_handle(db, facts)
    assert mat.materialize_collection(db, coll, facts) is None
    assert list(db["collection_products"].find({"collection_id": "pick"})) == before


def test_a_strict_facts_read_raises_when_the_variant_rows_are_unreadable(monkeypatch):
    """_facts passes strict to variant_rows_for_product: a dead
    catalog_variants read raises, never lead-only SKUs."""
    from api.services import online_catalog

    db = _db()
    doc = db["catalog_products"].find_one({"sku": "A"})
    real = online_catalog.variant_rows_for_product

    def _flaky(db, product, *, strict=False):
        if strict:
            raise RuntimeError("catalog_variants unreadable")
        return real(db, product)

    monkeypatch.setattr(online_catalog, "variant_rows_for_product", _flaky)
    assert reg._facts(db, doc)["sku"] == "A"
    with pytest.raises(RuntimeError):
        reg._facts(db, doc, strict=True)
