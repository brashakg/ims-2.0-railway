"""The IMS collection registry and the rebuild (owner 2026-10-19).

- ONE collection set, derived from the listings on the website: category,
  gender / shape / polarized per category, brand (Brand Master title), price
  bands, New Arrivals. Rules use IMS's own tags / productType / prices; a
  definition with no products is never made, and appears with its first one.
- scripts/rebuild_collections.py deletes every Shopify collection that is not
  IMS's FIRST, retires the old rows, then creates the IMS set; a re-run does
  nothing; an IMS collection is never deleted.
- smart rule sets are sent all-or-nothing; a retired row is never pushed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts"))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import collection_registry as reg  # noqa: E402
from api.services.shopify_tag_gen import generate_attribute_tags  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

LIVE = {"shopify_product_id": "gid://shopify/Product/1"}


NOW = datetime.now(timezone.utc)


def _sun(sku, brand, mrp, live=True, created_at=NOW, **attrs):
    return {"id": sku, "sku": sku, "brand": brand, "category": "SUNGLASS", "mrp": mrp, "created_at": created_at,
            "attributes": attrs, "ecom": dict(LIVE) if live else {}}


def _db(products=None, collections=(), armed=True):
    db = StrictDB()
    if armed:  # the rebuild has run once (registry writes allowed)
        db.seed(reg.REBUILD_STATE, [{"_id": "rebuild", "old_ids": []}])
    db.seed("brand_masters", [{"name": n, "is_active": True} for n in ("Ray-Ban", "Dolce & Gabbana", "Idee")])
    db.seed("catalog_variants", [])
    db.seed("catalog_products", products if products is not None else [
        _sun("A", "RAY-BAN", 2500, gender="Men", shape="Aviator"),
        _sun("B", "Ray-Ban", 7000, gender="Women", shape="Cat Eye"),
        # catalogued two months ago: not a New Arrival
        _sun("C", "Dolce & Gabbana", 12000, created_at=NOW - timedelta(days=60), gender="Women", shape="Cat Eye"),
        _sun("D", "Idee", 1500, live=False, gender="Unisex", polarization="Yes"),  # not on the website
        {**_sun("E", "Idee", 1500, gender="Kids"), "ecom": {**LIVE, "variant_of": {"sku": "A"}}},  # a colour
    ])
    db.seed("ecom_collections", list(collections))
    return db


def _plan(db):
    return {d["handle"]: d for d in reg.derive_definitions(reg.listing_facts(db), reg.brand_names(db))}


def test_the_set_is_derived_from_the_listings_on_the_website_and_empty_ones_are_skipped():
    plan = _plan(_db())
    assert {h: (d["title"], d["count"]) for h, d in plan.items()} == {
        "sunglasses": ("Sunglasses", 3),
        "mens-sunglasses": ("Men's Sunglasses", 1),
        "womens-sunglasses": ("Women's Sunglasses", 2),
        "aviator-sunglasses": ("Aviator Sunglasses", 1),
        "cat-eye-sunglasses": ("Cat Eye Sunglasses", 2),
        "dolce-gabbana": ("Dolce & Gabbana", 1),
        "ray-ban": ("Ray-Ban", 2),  # 'RAY-BAN' and 'Ray-Ban' are one brand, titled from Brand Master
        "under-3000": ("Under Rs 3,000", 1),
        "5000-10000": ("Rs 5,000-10,000", 1),
        "over-10000": ("Over Rs 10,000", 1),
        "new-arrivals": ("New Arrivals", 2),  # catalogued in the last NEW_ARRIVALS_WEEKS weeks only
    }  # no Idee / unisex / kids / polarized (not live or a colour), no empty 3000-5000 band
    assert plan["mens-sunglasses"]["rules"] == [
        {"field": "product_type", "relation": "EQUALS", "value": "Sunglass"},
        {"field": "tag", "relation": "EQUALS", "value": "gender_men"}]
    assert plan["ray-ban"]["rules"] == [{"field": "tag", "relation": "EQUALS", "value": "brand_rayban"}]
    assert plan["new-arrivals"]["sort_order"] == "CREATED_DESC"
    # Every rule is one the push can send whole.
    for d in plan.values():
        rules, lost = shopify_push.convert_rule_set(d["rules"])
        assert rules and not lost, d["handle"]


def test_rules_read_what_the_product_push_sends():
    from api.services.shopify_push.product_input import build_product_input, ims_product_tags

    p = _sun("A", "RAY-BAN", 2500, gender="Men", shape="Aviator", polarization="Polarized")
    tags = ims_product_tags(p)
    assert {"brand_rayban", "gender_men", "shape_aviator", "polarization_yes"} <= set(tags)
    assert build_product_input(p, [])["productType"] == "Sunglass"
    # One spelling per meaning, so one rule finds every polarized product.
    for v in ("Yes", "Polarized", "polarised", True):
        assert "polarization_yes" in generate_attribute_tags("SUNGLASS", {"polarization": v})
    assert "polarization_no" in generate_attribute_tags("SUNGLASS", {"polarization": "Non-Polarized"})


def test_registry_rows_are_ims_managed_queued_once_and_idempotent():
    db = _db()
    out = reg.apply_collection_registry(db)
    assert len(out["created"]) == 11 and out["skipped"] == []
    row = db["ecom_collections"].find_one({"handle": "ray-ban"})
    assert row["ims_managed"] is True and row["ims_key"] == "brand:rayban"
    assert row["collection_type"] == "SMART" and row["locally_modified"] is True
    again = reg.apply_collection_registry(db)
    assert again == {"created": [], "updated": [], "unchanged": 11, "skipped": []}


def test_a_collection_appears_when_its_first_product_goes_live():
    db = _db()
    reg.apply_collection_registry(db)
    assert db["ecom_collections"].find_one({"handle": "polarized-sunglasses"}) is None
    db["catalog_products"].update_one({"id": "D"}, {"$set": {"ecom": dict(LIVE)}})
    out = reg.apply_collection_registry(db)
    assert {"polarized-sunglasses", "idee", "unisex-sunglasses"} <= set(out["created"])
    assert "sunglasses" in out["updated"] or out["unchanged"]  # same definition, not re-made


def test_a_handle_held_by_an_old_collection_is_skipped_until_retired_then_reused():
    old = {"_id": "x1", "collection_id": "old1", "title": "Ray Ban", "handle": "ray-ban", "collection_type": "SMART",
           "shopify_collection_id": "gid://shopify/Collection/9", "locally_modified": True}
    db = _db(collections=[old])
    out = reg.apply_collection_registry(db)
    assert any(s.startswith("ray-ban:") for s in out["skipped"])
    assert reg.retire_old_collections(db) == 1
    gone = db["ecom_collections"].find_one({"collection_id": "old1"})
    assert gone["retired"] is True and gone["locally_modified"] is False and "handle" not in gone
    assert gone["retired_handle"] == "ray-ban" and "shopify_collection_id" not in gone
    assert "ray-ban" in reg.apply_collection_registry(db)["created"]


# ---------------------------------------------------------------------------
# The rebuild script against a fake shop.
# ---------------------------------------------------------------------------


class _Shop:
    """A shop with two old collections and one IMS collection on it.

    page_size     collections per read page (pagination)
    fail_delete   gids whose delete Shopify refuses
    fail_read_at  the read page (0-based) that comes back as an error
    taken_suffix  a create whose handle is taken gets '-1' (Shopify's way)
    publish_error a userError on every Online Store publish
    """

    def __init__(self, **kw):
        self.calls = []
        self.colls = {
            "gid://shopify/Collection/1": {"handle": "ray-ban", "title": "Ray Ban", "ims_key": None},
            "gid://shopify/Collection/2": {"handle": "summer", "title": "Summer", "ims_key": None},
            "gid://shopify/Collection/3": {"handle": "sunglasses", "title": "Sunglasses", "ims_key": "type:sunglasses"},
        }
        self.n = 100
        self.page_size = kw.get("page_size", 250)
        self.fail_delete = set(kw.get("fail_delete", ()))
        self.fail_read_at = kw.get("fail_read_at")
        self.taken_suffix = kw.get("taken_suffix", False)
        self.publish_error = kw.get("publish_error")
        self.menus = kw.get("menus", [])
        self.home = kw.get("home", "{}")

    async def __call__(self, db, query, variables):  # noqa: ARG002
        import re

        self.calls.append(re.search(r"(?:query|mutation)\s+(\w+)", query).group(1))
        if "imsCollectionsRead" in query:
            page = int(variables.get("after") or 0)
            if page == self.fail_read_at:
                return {"errors": [{"message": "Throttled"}]}
            items = list(self.colls.items())[page * self.page_size:(page + 1) * self.page_size]
            nodes = [{"id": g, "handle": c["handle"], "title": c["title"], "productsCount": {"count": 0},
                      "imsKey": {"value": c["ims_key"]} if c["ims_key"] else None} for g, c in items]
            more = (page + 1) * self.page_size < len(self.colls)
            return {"data": {"collections": {"pageInfo": {"hasNextPage": more, "endCursor": str(page + 1)},
                                             "nodes": nodes}}}
        if "imsMenusRead" in query:
            return {"data": {"menus": {"nodes": self.menus}}}
        if "imsThemeHomeRead" in query:
            return {"data": {"themes": {"nodes": [{"name": "Dawn", "files": {"nodes": [
                {"filename": "templates/index.json", "body": {"content": self.home}}]}}]}}}
        if "collectionDelete" in query:
            gid = variables["input"]["id"]
            if gid in self.fail_delete:
                return {"data": {"collectionDelete": {"deletedCollectionId": None,
                                                      "userErrors": [{"field": "id", "message": "locked"}]}}}
            self.colls.pop(gid)
            return {"data": {"collectionDelete": {"deletedCollectionId": gid, "userErrors": []}}}
        if "collectionCreate" in query:
            inp = variables["input"]
            handle = inp["handle"]
            if handle in {c["handle"] for c in self.colls.values()}:
                assert self.taken_suffix, "handle still taken"
                handle += "-1"
            self.n += 1
            gid = f"gid://shopify/Collection/{self.n}"
            self.colls[gid] = {"handle": handle, "title": inp["title"],
                               "ims_key": inp["metafields"][0]["value"]}
            return {"data": {"collectionCreate": {"collection": {"id": gid, "handle": handle}, "userErrors": []}}}
        if "collectionUpdate" in query:
            gid = variables["input"]["id"]
            return {"data": {"collectionUpdate": {"collection": {"id": gid, "handle": self.colls.get(gid, {}).get(
                "handle", variables["input"]["handle"])}, "userErrors": []}}}
        if "publishablePublish" in query:
            errs = [{"field": "id", "message": self.publish_error}] if self.publish_error else []
            return {"data": {"publishablePublish": {"userErrors": errs}}}
        raise AssertionError(query)


def _live(monkeypatch, fake):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")


def test_rebuild_deletes_the_old_first_keeps_ims_collections_and_is_idempotent(monkeypatch):
    import rebuild_collections

    shop = _Shop()
    _live(monkeypatch, shop)
    old = [{"_id": "x1", "collection_id": "o1", "title": "Ray Ban", "handle": "ray-ban", "collection_type": "SMART",
            "shopify_collection_id": "gid://shopify/Collection/1", "locally_modified": True},
           {"_id": "x2", "collection_id": "o2", "title": "Summer", "handle": "summer", "collection_type": "CUSTOM",
            "shopify_collection_id": "gid://shopify/Collection/2", "locally_modified": False},
           {"_id": "x3", "collection_id": "t1", "title": "PDF", "handle": "pdf-x", "is_temporary": True}]
    db = _db(collections=old, armed=False)

    dry = asyncio.run(rebuild_collections.run(db, apply=False))
    assert dry == {"delete": 2, "collections": 11, "references": []}
    assert "imsCollectionDelete" not in shop.calls and "imsCollectionCreate" not in shop.calls

    out = asyncio.run(rebuild_collections.run(db, apply=True))
    assert out["deleted"] == 2 and out["retired"] == 2 and out["failed"] == [] and out["linked"] == 1
    assert len(out["created"]) == 11 and len(out["pushed"]) == 11
    assert shop.calls.count("imsCollectionCreate") == 10, "the linked IMS collection is updated, not made twice"
    # Every delete went out before the first create (the handles were freed).
    first_create = next(i for i, c in enumerate(shop.calls) if c == "imsCollectionCreate")
    assert max(i for i, c in enumerate(shop.calls) if c == "imsCollectionDelete") < first_create
    assert "gid://shopify/Collection/3" in shop.colls, "an IMS collection is never deleted"
    assert "ray-ban" in {c["handle"] for c in shop.colls.values()}, "the freed handle is reused"
    assert db["ecom_collections"].find_one({"collection_id": "t1"}).get("retired") is None
    assert {d["collection_id"] for d in db["ecom_collections"].find({"retired": True})} == {"o1", "o2"}

    shop.calls.clear()
    again = asyncio.run(rebuild_collections.run(db, apply=True))
    assert again["deleted"] == 0 and again["retired"] == 0 and again["created"] == [] and again["pushed"] == []
    assert shop.calls == ["imsCollectionsRead"]


def test_rebuild_apply_is_refused_when_pushes_are_dark():
    import pytest
    import rebuild_collections

    with pytest.raises(SystemExit):
        asyncio.run(rebuild_collections.run(_db(), apply=True))


def test_a_retired_collection_is_never_pushed(monkeypatch):
    shop = _Shop()
    _live(monkeypatch, shop)
    res = asyncio.run(shopify_push.push_collection(StrictDB(), {
        "collection_id": "o1", "title": "x", "handle": "x", "retired": True, "locally_modified": True}))
    assert res.ok is False and res.reason == "retired" and shop.calls == []


def test_a_rule_set_with_a_malformed_rule_is_never_sent_in_part():
    out, lost = shopify_push.convert_rule_set([{"field": "tag", "relation": "EQUALS", "value": "x"}, "junk"])
    assert lost and out == [{"column": "TAG", "relation": "EQUALS", "condition": "x"}]
    assert "ruleSet" not in shopify_push.build_collection_input(
        {"collection_type": "SMART", "title": "t", "handle": "t",
         "rules": [{"field": "tag", "relation": "EQUALS", "value": "x"}, "junk"]})


def test_a_new_smart_collection_whose_rules_cannot_be_sent_is_refused(monkeypatch):
    shop = _Shop()
    _live(monkeypatch, shop)
    doc = {"collection_id": "x", "title": "Odd", "handle": "odd", "collection_type": "SMART",
           "rules": [{"field": "nonsense", "relation": "EQUALS", "value": "1"}]}
    res = asyncio.run(shopify_push.push_collection(StrictDB(), doc))
    assert res.ok is False and shop.calls == [] and "nonsense" in res.rules_note


# ---------------------------------------------------------------------------
# Review of ba538066: every guard on the live-store delete has a test that
# fails when the guard is removed.
# ---------------------------------------------------------------------------

_OLD = [{"_id": "x1", "collection_id": "o1", "title": "Ray Ban", "handle": "ray-ban", "collection_type": "SMART",
         "shopify_collection_id": "gid://shopify/Collection/1", "locally_modified": True},
        {"_id": "x2", "collection_id": "o2", "title": "Summer", "handle": "summer", "collection_type": "CUSTOM",
         "shopify_collection_id": "gid://shopify/Collection/2", "locally_modified": False}]


def _fresh(**shop_kw):
    return _Shop(**shop_kw), _db(collections=[dict(c) for c in _OLD], armed=False)


def test_an_ims_collection_whose_key_is_missing_on_shopify_is_never_deleted():
    # Its ims.collection_key metafield is gone, but IMS knows its Shopify id.
    db = _db(collections=[{"_id": "m1", "collection_id": "m1", "handle": "sunglasses", "ims_managed": True,
                           "ims_key": "type:sunglasses", "shopify_collection_id": "3"}])
    shop = [{"id": "gid://shopify/Collection/3", "handle": "sunglasses", "ims_key": None},
            {"id": "gid://shopify/Collection/2", "handle": "summer", "ims_key": None}]
    assert [c["handle"] for c in reg.collections_to_delete(db, shop)] == ["summer"]


def test_a_failed_delete_retires_nothing_and_creates_nothing(monkeypatch):
    import pytest
    import rebuild_collections

    shop, db = _fresh(fail_delete={"gid://shopify/Collection/2"})
    _live(monkeypatch, shop)
    with pytest.raises(RuntimeError):
        asyncio.run(rebuild_collections.run(db, apply=True))
    assert list(db["ecom_collections"].find({"retired": True})) == []
    assert db["ecom_collections"].find_one({"collection_id": "o2"})["handle"] == "summer"
    assert "imsCollectionCreate" not in shop.calls and "imsCollectionUpdate" not in shop.calls
    assert reg.rebuild_state(db) is None, "the registry stays off: the old set is still there"


def test_the_shop_is_read_across_every_page(monkeypatch):
    import rebuild_collections

    shop, db = _fresh(page_size=1)  # 3 collections -> 3 pages; 'summer' is on page 2
    _live(monkeypatch, shop)
    assert [c["handle"] for c in asyncio.run(reg.shop_collections(db))] == ["ray-ban", "summer", "sunglasses"]
    out = asyncio.run(rebuild_collections.run(db, apply=True))
    assert out["deleted"] == 2 and "gid://shopify/Collection/2" not in shop.colls


def test_a_failed_read_deletes_nothing(monkeypatch):
    import pytest
    import rebuild_collections

    shop, db = _fresh(page_size=1, fail_read_at=1)  # page 1 read, page 2 fails
    _live(monkeypatch, shop)
    with pytest.raises(RuntimeError):
        asyncio.run(rebuild_collections.run(db, apply=True))
    assert "imsCollectionDelete" not in shop.calls and len(shop.colls) == 3


def test_the_scheduled_collection_pass_is_live_only():
    db = _db()  # gates closed
    out = asyncio.run(shopify_push.sync_collections(db))
    assert "dark" in out and list(db["ecom_collections"].find({})) == []


def test_the_registry_waits_for_the_first_rebuild(monkeypatch):
    shop, db = _fresh()
    _live(monkeypatch, shop)
    assert "waiting" in reg.apply_collection_registry(db)
    assert "waiting" in asyncio.run(shopify_push.sync_collections(db))
    assert db["ecom_collections"].find_one({"ims_managed": True}) is None and shop.calls == []


def test_a_handle_shopify_changed_keeps_the_row_queued(monkeypatch):
    shop = _Shop(taken_suffix=True)  # 'summer' is still taken on Shopify
    _live(monkeypatch, shop)
    db = _db(collections=[{"_id": "n1", "collection_id": "n1", "title": "Summer", "handle": "summer",
                           "collection_type": "SMART", "ims_managed": True, "ims_key": "k",
                           "rules": [{"field": "tag", "relation": "EQUALS", "value": "x"}],
                           "locally_modified": True, "edit_rev": "r1"}])
    doc = db["ecom_collections"].find_one({"collection_id": "n1"})
    res = asyncio.run(shopify_push.push_collection(db, doc))
    saved = db["ecom_collections"].find_one({"collection_id": "n1"})
    assert res.ok is False and "summer-1" in res.error
    assert saved["locally_modified"] is True and "summer-1" in saved["push_problem"]
    assert saved["shopify_collection_id"], "the gid is kept, so the retry updates and never makes a second"


def test_a_failed_online_store_publish_keeps_the_row_queued_until_it_works(monkeypatch):
    shop = _Shop(publish_error="Access denied")
    _live(monkeypatch, shop)
    db = _db(collections=[{"_id": "n1", "collection_id": "n1", "title": "Idee", "handle": "idee",
                           "collection_type": "SMART", "ims_managed": True, "ims_key": "brand:idee",
                           "rules": [{"field": "tag", "relation": "EQUALS", "value": "brand_idee"}],
                           "locally_modified": True, "edit_rev": "r1"}])
    res = asyncio.run(shopify_push.push_collection(db, db["ecom_collections"].find_one({"collection_id": "n1"})))
    saved = db["ecom_collections"].find_one({"collection_id": "n1"})
    assert res.ok is False and saved["locally_modified"] is True and "publish" in saved["push_problem"]
    shop.publish_error = None
    res = asyncio.run(shopify_push.push_collection(db, saved))
    saved = db["ecom_collections"].find_one({"collection_id": "n1"})
    assert res.ok is True and saved["locally_modified"] is False and "push_problem" not in saved


def test_the_dry_run_names_menus_and_home_sections_that_point_at_a_doomed_collection(monkeypatch):
    import rebuild_collections

    menus = [{"handle": "main-menu", "title": "Main", "items": [{"title": "Shop", "url": "/", "resourceId": None,
              "items": [{"title": "Ray-Ban", "url": "/collections/x", "resourceId": "gid://shopify/Collection/1",
                         "items": []}]}]}]
    shop, db = _fresh(menus=menus, home='{"sections": {"f": {"settings": {"collection": "summer"}}}}')
    db.seed("ecom_menus", [{"handle": "footer", "items": [{"title": "Summer", "url": "/collections/summer/",
                                                           "children": []}]}])
    _live(monkeypatch, shop)
    refs = asyncio.run(rebuild_collections.run(db, apply=False))["references"]
    assert any(r.startswith("IMS menu footer > Summer") for r in refs)
    assert any(r.startswith("Shopify menu main-menu > Ray-Ban") for r in refs)
    assert any("templates/index.json uses collection summer" in r for r in refs)
    assert "imsCollectionDelete" not in shop.calls


def test_a_rerun_keeps_collections_made_after_the_first_rebuild(monkeypatch):
    import rebuild_collections

    shop, db = _fresh()
    _live(monkeypatch, shop)
    asyncio.run(rebuild_collections.run(db, apply=True))
    # Staff make a collection in IMS afterwards; it is pushed (no IMS key).
    shop.colls["gid://shopify/Collection/500"] = {"handle": "diwali", "title": "Diwali", "ims_key": None}
    db.seed("ecom_collections", [{"_id": "s1", "collection_id": "s1", "title": "Diwali", "handle": "diwali",
                                  "shopify_collection_id": "gid://shopify/Collection/500"}])
    again = asyncio.run(rebuild_collections.run(db, apply=True))
    assert again["deleted"] == 0 and again["retired"] == 0 and "gid://shopify/Collection/500" in shop.colls
    assert not db["ecom_collections"].find_one({"collection_id": "s1"}).get("retired")
    forced = asyncio.run(rebuild_collections.run(db, apply=True, include_new=True))
    assert forced["deleted"] == 1 and "gid://shopify/Collection/500" not in shop.colls


def test_tags_one_spelling_per_meaning():
    for v in ("Male", "Men", "M", "Men's"):
        assert "gender_men" in generate_attribute_tags("SUNGLASS", {"gender": v}), v
    for v in ("Female", "Women", "F", "Women's"):
        assert "gender_women" in generate_attribute_tags("SUNGLASS", {"gender": v}), v
    assert "gender_unisex" in generate_attribute_tags("SUNGLASS", {"gender": "Unisex"})
    assert "polarization_yes" in generate_attribute_tags("SUNGLASS", {"uv_protection": "Polarized"})
    both = generate_attribute_tags("SUNGLASS", {"polarization": "No", "uv_protection": "Polarized"})
    assert "polarization_no" in both and "polarization_yes" not in both


def test_a_listing_held_as_a_shopify_draft_is_not_counted():
    db = _db(products=[_sun("A", "Ray-Ban", 2500), {**_sun("B", "Idee", 2500), "ecom": {**LIVE, "status": "DRAFT"}}])
    plan = _plan(db)
    assert plan["sunglasses"]["count"] == 1 and "idee" not in plan
