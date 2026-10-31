"""Regression probes for SKU parity and content drift; no network or DB writes."""
import asyncio
import copy

import pytest
from api.services import online_sync_health as health
from api.services import shopify_push
from strict_fakes import StrictDB

GID = "gid://shopify/Product/1"
VID = "gid://shopify/ProductVariant/1"


def catalog():
    db = StrictDB()
    db.seed("catalog_products", [{"id": "p1", "sku": "A", "title": "Frame", "mrp": 100,
        "offer_price": 80, "ecom": {"shopify_product_id": GID, "last_pushed_at": "2026-10-01T00:00:00Z",
        "shopify_tags_sent": ["owned"], "seo": {"tags": ["owned"]}}}])
    db.seed("catalog_variants", [{"sku": "A", "parent_product_id": "p1", "shopify_variant_id": VID}])
    return db


def remote():
    return {"id": GID, "title": "Frame", "status": "ACTIVE", "tags": ["OWNED", "human tag"],
            "updatedAt": "2026-10-24T00:00:00Z", "variants": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
                    {"id": VID, "sku": "A", "price": "80.00", "compareAtPrice": "100.00",
                     "selectedOptions": [{"name": "Title", "value": "Default Title"}]}]}}


def timestamp_probe_catalog():
    # The previous detector requests dotted projections. Its established fake
    # supports that read, so reverting fails on actual timestamp/content
    # behaviour, not StrictDB's deliberately unsupported dotted projection.
    from test_bvi_drift_and_parity import _FakeDb, _FakeColl
    db = catalog()
    return _FakeDb({name: _FakeColl(list(db[name].find({})))
                    for name in ("catalog_products", "catalog_variants")})


def detect(monkeypatch, db, node=None, body=None):
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda *_: True)
    async def read(_db, query, _variables):
        assert "mutation" not in query.lower()
        return copy.deepcopy(body if body is not None else {"data": {"nodes": [node]}})
    monkeypatch.setattr(shopify_push, "_graphql", read)
    return asyncio.run(health.detect_drift(db))


def test_new_timestamp_and_unmanaged_tag_are_not_drift(monkeypatch):
    result = detect(monkeypatch, timestamp_probe_catalog(), remote())
    assert result["checked"] and result["counts"]["scanned"] == 1
    assert result["drifted"] == [] and result["counts"]["unknown"] == 0


# Tags: the writer's own rule (tags.plan_product_tags) -- see the review-fix
# tests below; a ledger tag staff deleted is not drift.
@pytest.mark.parametrize("field,label", [("title", "title"), ("status", "status"),
    ("price", "price: A"), ("compareAtPrice", "compare-at price: A"),
    ("sku", "variant SKUs"), ("selectedOptions", "variant options: A")])
def test_real_owned_changes_name_the_field_even_with_old_timestamp(monkeypatch, field, label):
    node = remote()
    node["updatedAt"] = "2020-01-01T00:00:00Z"
    changes = {"title": "Changed", "status": "DRAFT", "price": "90.0",
               "compareAtPrice": None, "sku": "wrong-SKU", "selectedOptions": [{"name": "Size", "value": "99"}]}
    target = node if field in ("title", "status") else node["variants"]["nodes"][0]
    target[field] = changes[field]
    result = detect(monkeypatch, timestamp_probe_catalog(), node)
    assert result["counts"]["drifted"] == 1
    assert label in result["drifted"][0]["fields"]


def test_missing_timestamp_does_not_hide_content_change(monkeypatch):
    db = catalog()
    db["catalog_products"].update_one({"id": "p1"}, {"$unset": {"ecom.last_pushed_at": ""}})
    node = remote()
    node["title"] = "Wrong"
    result = detect(monkeypatch, db, node)
    assert result["counts"]["no_timestamp"] == 1 and result["counts"]["drifted"] == 1


@pytest.mark.parametrize("body", [{"errors": [{"message": "denied"}]}, {"data": {"nodes": [None]}},
                                   {"data": {"nodes": [{"id": GID, "updatedAt": "2026-10-24T00:00:00Z"}]}}])
def test_failed_or_partial_read_is_unknown_never_a_clean_scan(monkeypatch, body):
    result = detect(monkeypatch, catalog(), body=body)
    assert result["drifted"] == [] and result["counts"]["scanned"] == 0
    assert result["counts"]["unknown"] == 1


def test_pagination_reads_content_beyond_first_page(monkeypatch):
    node = remote()
    node["variants"]["nodes"] = []
    node["variants"]["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
    final = remote()
    final["variants"]["nodes"][0]["price"] = "999.00"
    calls = []
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda *_: True)
    async def read(_db, query, variables):
        assert "mutation" not in query
        calls.append(variables)
        return {"data": {"nodes": [copy.deepcopy(final if variables["after"] else node)]}}
    monkeypatch.setattr(shopify_push, "_graphql", read)
    result = asyncio.run(health.detect_drift(catalog()))
    assert len(calls) == 2 and calls[1]["after"] == "next"
    assert result["drifted"][0]["fields"] == ["price: A"]


def test_mapped_colours_count_per_catalog_sku_and_keep_legacy_counts():
    db = catalog()
    db["catalog_products"].insert_one({"id": "p2", "sku": "B", "ecom": {"variant_of": {"twin_id": "p1", "product_id": "sp1"}}})
    db["catalog_variants"].insert_one({"sku": "B", "parent_product_id": "p1", "shopify_variant_id": "v2"})
    db["catalog_variants"].insert_one({"sku": "B", "shopify_variant_id": "duplicate"})
    db["catalog_variants"].insert_one({"sku": "orphan", "shopify_variant_id": "orphan"})
    db["catalog_products"].insert_one({"id": "p3", "sku": "C"})
    db["products"].insert_one({"sku": "C", "product_id": "sp3", "deleted_at": "2026-10-01"})
    db["catalog_products"].insert_one({"id": "p4", "sku": "D"})
    result = health.parity_summary(db)
    assert result["entities"]["catalog_products"] == {"total": 4, "pushed": 1, "missing": 3}
    colours = result["colours_sizes"]
    assert (colours["total"], colours["online"], colours["website_pages"], colours["missing"]) == (4, 2, 1, 2)
    # D has no brand: the writer's own gate refuses it (never "not queued").
    assert {r["sku"]: (r["reason"], r["code"]) for r in colours["missing_items"]} == {
        "C": ("archived", None), "D": ("not for the website - the product has no brand", "NOT_FOR_WEBSITE")}


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_family_refusal_reason_is_current_and_shared_by_missing_colours():
    from test_shopify_model_listings import _world, _family, _twin
    db = _world()
    # An incomplete lead remains a refusal under Prompt A as well, so this
    # diagnostics regression remains valid when both independent PRs land.
    ids = _family(db, [("Gold", "001", ""), ("Black", "002", "58")])
    result = health.parity_summary(db)["colours_sizes"]
    assert result["checked"] and result["online"] == 0
    assert {r["code"] for r in result["missing_items"]} == {"OPTION_MISSING"}
    assert all(r["reason"] == "family push refused - OPTION_MISSING" for r in result["missing_items"])
    # Fix the actual catalogue input: no old run log should leave a false refusal.
    db["products"].update_one({"product_id": ids[0]}, {"$set": {"attributes.lens_size": "58"}})
    db["catalog_products"].update_one({"id": _twin(db, ids[0])["id"]}, {"$set": {"attributes.lens_size": "58"}})
    result = health.parity_summary(db)["colours_sizes"]
    assert all(r["code"] is None and r["reason"].startswith("queued") for r in result["missing_items"])


def test_parity_unreadable_join_is_not_a_fake_zero():
    class Dead:
        def __getitem__(self, key):
            raise RuntimeError("offline")
    result = health.parity_summary(Dead())
    assert result["colours_sizes"]["checked"] is False
    assert "online" not in result["colours_sizes"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_clean_multi_colour_writer_output_compares_clean(monkeypatch):
    from test_shopify_model_listings import _world, _family, _live, _push, FakeShopify
    db = _world()
    ids = _family(db, [("Gold", "001", "58"), ("Black", "002", "62")])
    fake = FakeShopify()
    _live(monkeypatch, fake)
    result = _push(db, ids[0])
    assert result.ok
    product = next(iter(fake.products.values()))
    node = {"id": product["id"], "title": product["title"], "status": product["status"],
            "tags": product["tags"] + ["manual-tag"], "updatedAt": "2030-01-01T00:00:00Z",
            "variants": {"pageInfo": {"hasNextPage": False}, "nodes": [
                {"id": v["id"], "sku": v["sku"], "price": v["price"], "compareAtPrice": v["compareAtPrice"],
                 "selectedOptions": [{"name": name, "value": value} for name, value in v["opts"].items()]}
                for v in product["variants"]]}}
    result = detect(monkeypatch, db, node)
    assert result["counts"]["scanned"] == 1 and not result["drifted"] and not result["unknown"], result
    colours = health.parity_summary(db)["colours_sizes"]
    assert colours["online"] == 2 and colours["website_pages"] == 1


def test_cleared_compare_at_and_case_order_of_options(monkeypatch):
    db = catalog()
    db["catalog_products"].update_one({"id": "p1"}, {"$set": {"offer_price": 100}})
    db["catalog_variants"].update_one({"sku": "A"}, {"$set": {"option_color": "Black", "option_size": "58"}})
    node = remote()
    node["variants"]["nodes"][0].update(price="100", compareAtPrice=None, selectedOptions=[
        {"name": "Size", "value": "58"}, {"name": "Colour", "value": "BLACK"}])
    result = detect(monkeypatch, db, node)
    assert result["counts"]["scanned"] == 1 and not result["drifted"]
    node["variants"]["nodes"][0]["compareAtPrice"] = "120.00"
    assert detect(monkeypatch, db, node)["drifted"][0]["fields"] == ["compare-at price: A"]


def test_taken_down_product_status_is_draft(monkeypatch):
    db = catalog()
    db["catalog_products"].update_one({"id": "p1"}, {"$set": {"ecom.taken_down_at": "2026-10-01"}})
    node = remote()
    node["status"] = "DRAFT"
    assert detect(monkeypatch, db, node)["drifted"] == []


def test_stale_owned_tag_is_drift_but_unmanaged_tags_are_preserved(monkeypatch):
    db = catalog()
    db["catalog_products"].update_one({"id": "p1"}, {"$set": {"ecom.seo.tags": []}})
    assert detect(monkeypatch, db, remote())["drifted"][0]["fields"] == ["tags"]


def test_incomplete_pagination_does_not_report_false_missing_skus(monkeypatch):
    node = remote()
    node["variants"]["pageInfo"] = {"hasNextPage": True, "endCursor": "repeated"}
    result = detect(monkeypatch, catalog(), node)
    assert not result["drifted"] and result["counts"]["unknown"] == 1


def test_archived_mapped_variant_prices_remain_owned(monkeypatch):
    db = catalog()
    db["catalog_variants"].update_one({"sku": "A"}, {"$set": {"_archived": True}})
    node = remote()
    node["variants"]["nodes"][0]["price"] = "999"
    assert detect(monkeypatch, db, node)["drifted"][0]["fields"] == ["price: A"]


def test_family_diagnostics_do_not_mutate_catalogue():
    from test_shopify_model_listings import _world, _family
    db = _world()
    _family(db, [("Gold", "001", "58"), ("Black", "002", "")])
    names = ("products", "catalog_products", "catalog_variants", "audit_log")
    before = {name: list(db[name].find({})) for name in names}
    assert health.parity_summary(db)["colours_sizes"]["missing"] == 2
    assert {name: list(db[name].find({})) for name in names} == before


# ---------------------------------------------------------------------------
# Review fixes (independent review of #1275)
# ---------------------------------------------------------------------------


def _missing(db):
    return {r["sku"]: r for r in health.parity_summary(db)["colours_sizes"]["missing_items"]}


@pytest.mark.usefixtures("brand_is_for_the_website")
@pytest.mark.parametrize("row", ["no_row", "row_without_ids"])
def test_a_live_product_mapped_only_on_its_ecom_counts_online(row):
    """BLOCK 1: the product-level pseudo-variant path (no variant row, or a
    self-row with no ids next to the ecom ids) is online -- THE rule
    online_catalog.skus_on_shopify, the same one Store Health reads."""
    from test_shopify_model_listings import _world, _live_single, FakeShopify
    db = _world()
    lead, _p, _var = _live_single(db, FakeShopify())
    sku = db["products"].find_one({"product_id": lead})["sku"]
    if row == "no_row":
        db["catalog_variants"].delete_one({"sku": sku})
    else:
        db["catalog_variants"].update_one({"sku": sku}, {"$unset": {"shopify_variant_id": "", "shopify_inventory_item_id": ""}})
    colours = health.parity_summary(db)["colours_sizes"]
    assert (colours["total"], colours["online"], colours["missing"], colours["website_pages"]) == (1, 1, 0, 1)


def test_an_ecom_variant_id_another_sku_owns_is_not_this_skus_mapping():
    from api.services.online_catalog import skus_on_shopify
    db = StrictDB()
    db.seed("catalog_products", [{"id": "p1", "sku": "A", "ecom": {"shopify_variant_id": "gid://shopify/ProductVariant/9"}}])
    db.seed("catalog_variants", [{"sku": "B", "shopify_variant_id": "gid://shopify/ProductVariant/9"}])
    assert skus_on_shopify(db, ["A", "B"]) == {"B"}


def test_the_online_rule_raises_on_a_dead_read_and_parity_says_unknown():
    from api.services.online_catalog import skus_on_shopify
    db = catalog()

    def dead(*_a, **_k):
        raise RuntimeError("blip")
    db["catalog_variants"].find = dead
    with pytest.raises(RuntimeError):
        skus_on_shopify(db, ["A"])
    assert health.parity_summary(db)["colours_sizes"]["checked"] is False


def test_a_dirty_product_with_no_photo_is_never_called_queued(monkeypatch):
    """SHOULD-FIX 2 (probe 1): the push refuses no_photo, so the reason says so."""
    from test_shopify_model_listings import _world, _item
    from api.services import catalog_dictionary
    monkeypatch.setattr(catalog_dictionary, "brand_website_refusal", lambda db, brand: None)
    db = _world()
    _item(db, 1, "Gold", "001", photos=0)
    (row,) = _missing(db).values()
    assert row["code"] == "no_photo" and row["reason"].startswith("no photo"), row


def test_a_brand_off_the_website_is_never_called_queued():
    """SHOULD-FIX 2 (probe 2): no Brand Master row -> the gate refuses."""
    from test_shopify_model_listings import _world, _item
    db = _world()
    _item(db, 1, "Gold", "001")
    (row,) = _missing(db).values()
    assert row["code"] == "NOT_FOR_WEBSITE" and row["reason"].startswith("not for the website"), row


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_an_unpriced_first_publish_is_never_called_queued():
    from test_shopify_model_listings import _world, _item
    db = _world()
    _item(db, 1, "Gold", "001", mrp=0.0)
    (row,) = _missing(db).values()
    assert row["code"] == "UNPRICED_SIZE", row


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_product_in_a_blocked_collection_is_never_called_queued(monkeypatch):
    from test_shopify_model_listings import _world, _item
    from api.services import online_block
    db = _world()
    _item(db, 1, "Gold", "001")
    monkeypatch.setattr(online_block, "is_blocked_from_online_strict", lambda product, db: True)
    (row,) = _missing(db).values()
    assert row["code"] == "online_sync_blocked", row


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_held_colour_on_a_live_listing_says_held():
    """SHOULD-FIX 5 (M10): a new unpriced colour of a live listing is held."""
    from test_shopify_model_listings import _world, _live_single, _item, FakeShopify
    from api.services import product_master as pm
    db = _world()
    lead, _p, _var = _live_single(db, FakeShopify())
    black = _item(db, 2, "Black", "002/58", mrp=0.0)
    pm.link_to_family(db, black, lead, actor="u-admin")
    sku = db["products"].find_one({"product_id": black})["sku"]
    row = _missing(db)[sku]
    assert row["code"] == "UNPRICED_SIZE" and row["reason"].startswith("colour/size held - UNPRICED_SIZE"), row


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_switched_off_colour_of_a_live_listing_says_not_ready():
    """SHOULD-FIX 5 (M9)."""
    from test_shopify_model_listings import _world, _live_single, _item, FakeShopify
    from api.services import product_master as pm
    db = _world()
    lead, _p, _var = _live_single(db, FakeShopify())
    black = _item(db, 2, "Black", "002/58")
    db["products"].update_one({"product_id": black}, {"$set": {"is_active": False}})
    pm.link_to_family(db, black, lead, actor="u-admin")
    sku = db["products"].find_one({"product_id": black})["sku"]
    assert _missing(db)[sku]["reason"].startswith("not ready - "), _missing(db)[sku]


def test_colours_of_a_taken_down_lead_say_taken_down():
    """SHOULD-FIX 5 (M8), with the lead found only through ecom.variant_of.twin_id (M12)."""
    db = StrictDB()
    db.seed("catalog_products", [
        {"id": "lead", "sku": "L", "ecom": {"taken_down_at": "2026-10-01"}},
        {"id": "kid", "sku": "K", "ecom": {"variant_of": {"twin_id": "lead"}}}])
    rows = _missing(db)
    assert {sku: r["reason"] for sku, r in rows.items()} == {
        "L": "taken down - not queued for automatic publish",
        "K": "taken down - not queued for automatic publish"}


def test_a_tag_ims_sent_and_staff_deleted_is_not_drift_the_writer_cannot_clear(monkeypatch):
    """SHOULD-FIX 3: tags.plan_product_tags decides; the writer never re-adds a
    ledger tag, so the diagnostic must not alarm on it either."""
    node = remote()
    node["tags"] = ["human tag"]
    assert detect(monkeypatch, timestamp_probe_catalog(), node)["drifted"] == []


def test_a_wanted_tag_ims_never_sent_is_drift(monkeypatch):
    db = catalog()
    db["catalog_products"].update_one({"id": "p1"}, {"$set": {"ecom.seo.tags": ["owned", "new"]}})
    assert detect(monkeypatch, db, remote())["drifted"][0]["fields"] == ["tags"]


def _many(n):
    db = StrictDB()
    db.seed("catalog_products", [{"id": f"p{i}", "sku": f"S{i}", "title": "Frame", "mrp": 100, "offer_price": 80,
        "ecom": {"shopify_product_id": f"gid://shopify/Product/{i}", "last_pushed_at": f"2026-10-{1 + i % 28:02d}T00:00:00Z"}}
        for i in range(n)])
    return db


def test_fifty_listings_are_read_in_five_calls_newest_push_first(monkeypatch):
    """SHOULD-FIX 4 + nit 1: ten listings a read (variants first:25), the sample is
    the most recently pushed, and the denominator is reported."""
    calls = []
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", lambda *_: True)

    async def read(_db, query, variables):
        assert "variants(first: 25" in query
        calls.append(list(variables["ids"]))
        return {"data": {"nodes": []}}
    monkeypatch.setattr(shopify_push, "_graphql", read)
    result = asyncio.run(health.detect_drift(_many(60), limit=50))
    assert len(calls) == 5 and all(len(c) == 10 for c in calls)
    assert result["counts"]["candidates"] == 60 and result["counts"]["unknown"] == 50
    newest = {"gid://shopify/Product/%d" % i for i in (27, 55)}  # pushed 2026-10-28
    assert newest <= set(calls[0])


def test_a_stray_colour_listing_is_named_never_a_generic_read_failure(monkeypatch):
    """nit 2 + M19: a colour twin carrying its own page id is unknown, with why."""
    db = catalog()
    db["catalog_products"].insert_one({"id": "p2", "sku": "B", "ecom": {
        "shopify_product_id": "gid://shopify/Product/2", "variant_of": {"twin_id": "p1"}}})
    node = remote()
    node["id"] = "gid://shopify/Product/2"
    result = detect(monkeypatch, db, body={"data": {"nodes": [remote(), node]}})
    (row,) = result["unknown"]
    assert row["sku"] == "B" and "belongs to another lead" in row["reason"], row


def test_two_twins_on_one_page_are_flagged_not_collapsed(monkeypatch):
    db = catalog()
    db["catalog_products"].insert_one({"id": "p9", "sku": "Z", "ecom": {"shopify_product_id": "1"}})
    result = detect(monkeypatch, db, remote())
    (flag,) = [u for u in result["unknown"] if "same Shopify page as" in u["reason"]]
    other = flag["reason"].split("same Shopify page as ")[1].split(" ")[0]
    assert {flag["sku"], other} == {"A", "Z"}, result


def test_drift_never_raises_on_a_broken_gate(monkeypatch):
    """nit 3: the documented never-raises contract."""
    def boom(*_):
        raise RuntimeError("creds store offline")
    monkeypatch.setattr(shopify_push, "_has_shopify_creds", boom)
    result = asyncio.run(health.detect_drift(catalog()))
    assert result["checked"] is False and "creds store offline" in result["reason"]


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_live_listing_the_push_refuses_unpriced_is_never_called_queued(monkeypatch):
    """Review of #1275: the unpriced check was skipped for live listings, but
    push_product still refuses a press that seeds an unpriced size (here a
    live page whose size row was never seeded). The diagnostics and the push
    must give the SAME answer (one rule)."""
    from test_shopify_model_listings import _world, _item, _twin, _push, _live, FakeShopify
    db = _world()
    first = _item(db, 1, "Gold", "001", mrp=0.0)
    db["catalog_products"].update_one({"id": _twin(db, first)["id"]}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/77", "ecom.status": "PUBLISHED"}})
    (row,) = _missing(db).values()
    _live(monkeypatch, FakeShopify())
    res = _push(db, first)
    assert not res.ok and res.code == "UNPRICED_SIZE", (res.code, res.error)
    assert row["code"] == "UNPRICED_SIZE", row


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_a_listing_whose_spine_is_a_colour_of_another_says_push_the_model():
    """Review of #1275: push_product refuses a plan variant_of; the verdict did not."""
    from api.services import online_content_diagnostics as diag
    from test_shopify_model_listings import _world, _item, _twin
    db = _world()
    first = _item(db, 1, "Gold", "001")
    db["products"].update_one({"product_id": first}, {"$set": {"variant_of": "sp-99"}})
    code, reason, _plan = diag._press_verdict(db, _twin(db, first))
    assert code == "variant_of", (code, reason)
