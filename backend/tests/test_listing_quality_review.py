"""Review of #1257 (listing quality): collection SEO a human wrote survives,
New Arrivals never freezes, counts read the listing's tags, the doubled-brand
strip stays on whole words, and the live-listing requeue reverts safely."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from api.services.catalog_ecom import dirty_marks  # noqa: E402
from api.services.product_naming import drop_doubled_brand, model_number_line  # noqa: E402
from api.services.shopify_push import collection_registry as reg  # noqa: E402
from api.services.shopify_push.media import photo_alt  # noqa: E402
from api.services.shopify_push.product_input import LISTING_TAGS  # noqa: E402
from api.services.shopify_tag_gen import listing_tags  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import requeue_live_listing_copy as rq  # noqa: E402


def _world(rows=(), products=()):
    db = StrictDB()
    db.seed(reg.REBUILD_STATE, [{"_id": "rebuild", "old_ids": []}])
    db.seed("ecom_collections", list(rows))
    db.seed("catalog_products", list(products))
    db.seed("catalog_variants", [])
    db.seed("products", [])
    db.seed("brand_masters", [])
    return db


_RULES = [{"field": "tag", "relation": "EQUALS", "value": "polarization_yes"}]
POL = {"key": "polarized:sunglasses", "title": "Polarized Sunglasses", "handle": "polarized-sunglasses",
       "rules": _RULES, "sort_order": "BEST_SELLING", "disjunctive": False,
       "seo_title": "Polarized Sunglasses | Better Vision", "seo_description": "Shop it.", "count": 1}


def _row(**kw):
    base = {"collection_id": "c1", "handle": "polarized-sunglasses", "title": "Polarized Sunglasses",
            "collection_type": "SMART", "rules": _RULES, "disjunctive": False, "sort_order": "BEST_SELLING",
            "published": True, "ims_managed": True, "ims_key": POL["key"]}
    return {**base, **kw}


def _stored(db, key=POL["key"]):
    return db["ecom_collections"].find_one({"ims_key": key})


# 1. Hand-written SEO -----------------------------------------------------------


def test_a_human_written_seo_title_survives_and_an_empty_description_is_filled():
    db = _world([_row(seo_title="Shades that cut glare", seo_description="",
                      seo_generated={"seo_title": "old generated", "seo_description": ""})])
    reg.apply_collection_registry(db, [POL])
    row = _stored(db)
    assert row["seo_title"] == "Shades that cut glare", "a human's SEO title is never overwritten"
    assert row["seo_description"] == "Shop it.", "an empty one is filled"


def test_a_stale_generated_seo_is_updated_then_left_alone():
    db = _world([_row(seo_title="Old | Better Vision", seo_description="Old.",
                      seo_generated={"seo_title": "Old | Better Vision", "seo_description": "Old."})])
    assert reg.apply_collection_registry(db, [POL])["updated"] == ["polarized-sunglasses"]
    row = _stored(db)
    assert (row["seo_title"], row["seo_description"]) == (POL["seo_title"], POL["seo_description"])
    assert reg.apply_collection_registry(db, [POL])["unchanged"] == 1, "nothing re-sent when nothing changed"


# 2. The registry writes the SEO it generates ----------------------------------------


def test_a_new_registry_row_carries_the_generated_seo():
    db = _world()
    reg.apply_collection_registry(db, [POL])
    row = _stored(db)
    assert (row["seo_title"], row["seo_description"]) == (POL["seo_title"], POL["seo_description"])
    assert row[reg.SEO_GENERATED] == {"seo_title": POL["seo_title"], "seo_description": POL["seo_description"]}


# 4. New Arrivals never freezes ------------------------------------------------------


def _fact(*tags):
    return {"type": "Sunglass", "prices": [1.0], "brand": "", "tags": set(tags)}


def test_new_arrivals_replaces_the_price_rule_on_the_first_run_with_the_newest_weeks():
    # Today's 23 live listings all arrived in w37; it is w43.
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    w37 = _fact("arrived_2026w37")
    na = next(d for d in reg.derive_definitions([w37], {}, now) if d["key"] == "new-arrivals")
    assert na["count"] == 1
    db = _world([_row(collection_id="na", handle="new-arrivals", title="New Arrivals", ims_key="new-arrivals",
                      rules=[{"field": "variant_price", "relation": "GREATER_THAN", "value": "0"}])])
    reg.apply_collection_registry(db, [na])
    row = _stored(db, "new-arrivals")
    assert row["rules"] == [{"field": "tag", "relation": "EQUALS", "value": "arrived_2026w37"}]
    assert row["disjunctive"] is True


def test_new_arrivals_window_prefers_recent_weeks_and_is_never_empty():
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    weeks = [_fact(f"arrived_2026w{w}") for w in (43, 42, 40, 37, 30)]
    assert reg.new_arrival_tags(weeks, now) == ["arrived_2026w43", "arrived_2026w42", "arrived_2026w40",
                                                 "arrived_2026w37"]
    old = _fact("arrived_2026w10")  # beyond the 12-week look-back
    assert reg.new_arrival_tags([_fact("arrived_2026w37"), old], now) == ["arrived_2026w37"]
    assert reg.new_arrival_tags([old], now) == ["arrived_2026w10"], "never empty while listings exist"
    assert reg.new_arrival_tags([], now) == []


# 5. Counts read the listing's tags -----------------------------------------------------


def test_registry_counts_by_the_listing_tags_the_push_sends(monkeypatch):
    from api.services.shopify_push import listing as ls

    lead = {"id": "L", "sku": "L", "category": "SUNGLASS", "attributes": {"polarization": "Yes"},
            "ecom": {"shopify_product_id": "gid://shopify/Product/1"}}
    mixed = listing_tags([["polarization_yes"], ["polarization_no"]])
    monkeypatch.setattr(ls, "model_listing", lambda db, doc, rows: {"refusal": None, "tags": mixed})
    monkeypatch.setattr(ls, "listing_view", lambda doc, lst: {**doc, LISTING_TAGS: lst["tags"]})
    fact = reg._facts(_world(products=[lead]), lead)
    assert "polarization_yes" not in fact["tags"], "one colour is not polarized: not in Polarized"


# 6. Whole words only ------------------------------------------------------------------


def test_the_doubled_brand_strip_never_reaches_into_urls_or_hyphenated_words():
    href = '<a href="/collections/carrera-carrera-804">Carrera Carrera 3016/S</a>'
    assert drop_doubled_brand(href, "Carrera") == '<a href="/collections/carrera-carrera-804">Carrera 3016/S</a>'
    assert drop_doubled_brand('<img src="/carrera carrera.jpg">', "Carrera") == '<img src="/carrera carrera.jpg">'
    tag = '<a href="https://x.in/p?q=Carrera Carrera" title="Carrera Carrera">x</a>'
    assert drop_doubled_brand(tag, "Carrera") == tag, "never inside a tag"
    assert drop_doubled_brand("Police police-line", "Police") == "Police police-line"
    assert drop_doubled_brand("Carrera Carrera 3016/S", "Carrera") == "Carrera 3016/S"


# 7. Cosmetics ---------------------------------------------------------------------------


def test_only_lowercase_model_codes_are_capitalised():
    line = model_number_line({"brand": "X", "attributes": {"model_no": "2nd Gen2 gen2 rb5279 3016/s"}})
    assert line == "X 2nd Gen2 gen2 RB5279 3016/S"


def test_alt_drops_the_colour_only_when_the_title_ends_with_it():
    assert photo_alt("Frame Blackwood", "Black", 1) == "Frame Blackwood - Black - view 1"
    assert photo_alt("Frame - Black", "Black", 1) == "Frame - Black - view 1"


# 3. The live-listing requeue ---------------------------------------------------------------

LIVE = {"shopify_product_id": "gid://shopify/Product/1", "status": "PUBLISHED"}


def _ecom(db, pid="A"):
    return db["catalog_products"].find_one({"id": pid})["ecom"]


def test_requeue_queues_live_listings_only_and_reverts():
    db = _world(products=[
        {"id": "A", "sku": "A", "ecom": dict(LIVE)},
        {"id": "B", "sku": "B", "ecom": {**LIVE, "locally_modified": True}},
        {"id": "C", "sku": "C", "ecom": {**LIVE, "status": "DRAFT"}},
        {"id": "D", "sku": "D", "ecom": {"locally_modified": True}},  # not live
        {"id": "E", "sku": "E", "ecom": {**LIVE, "variant_of": {"sku": "A"}}},  # a colour
    ])
    rows = rq.live_listings(db)
    assert [(r["sku"], r["queued"]) for r in rows] == [("A", False), ("B", True)]
    assert rq.apply(db, rows, "run1") == 1
    assert _ecom(db)["locally_modified"] is True and _ecom(db)["edit_rev"]
    assert rq.revert(db, "run1") == 1
    assert _ecom(db)["locally_modified"] is False
    assert _ecom(db, "B")["locally_modified"] is True


def test_revert_never_unqueues_a_later_human_edit():
    db = _world(products=[{"id": "A", "sku": "A", "ecom": dict(LIVE)}])
    rq.apply(db, rq.live_listings(db), "run1")
    db["catalog_products"].update_one({"id": "A"}, {"$set": {"ecom.locally_modified": False}})  # pushed
    db["catalog_products"].update_one(  # a human edit queues it again
        {"id": "A"}, {"$set": {f"ecom.{k}": v for k, v in dirty_marks().items()}})
    assert rq.revert(db, "run1") == 0
    assert _ecom(db)["locally_modified"] is True


def test_a_row_queued_concurrently_is_neither_touched_nor_recorded():
    db = _world(products=[{"id": "A", "sku": "A", "ecom": dict(LIVE)}])
    rows = rq.live_listings(db)  # read while not queued
    db["catalog_products"].update_one({"id": "A"}, {"$set": {"ecom.locally_modified": True, "ecom.edit_rev": "h"}})
    assert rq.apply(db, rows, "run1") == 0
    assert list(db[rq.BACKUPS].find({"run_id": "run1"})) == []
    assert rq.revert(db, "run1") == 0
    assert _ecom(db) == {**LIVE, "locally_modified": True, "edit_rev": "h"}
