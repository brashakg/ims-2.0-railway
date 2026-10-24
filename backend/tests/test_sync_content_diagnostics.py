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


@pytest.mark.parametrize("field,label", [("title", "title"), ("status", "status"),
    ("tags", "tags"), ("price", "price: A"), ("compareAtPrice", "compare-at price: A"),
    ("sku", "variant SKUs"), ("selectedOptions", "variant options: A")])
def test_real_owned_changes_name_the_field_even_with_old_timestamp(monkeypatch, field, label):
    node = remote()
    node["updatedAt"] = "2020-01-01T00:00:00Z"
    changes = {"title": "Changed", "status": "DRAFT", "tags": ["human tag"], "price": "90.0",
               "compareAtPrice": None, "sku": "wrong-SKU", "selectedOptions": [{"name": "Size", "value": "99"}]}
    target = node if field in ("title", "status", "tags") else node["variants"]["nodes"][0]
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
    assert {r["sku"]: r["reason"] for r in colours["missing_items"]} == {"C": "archived", "D": "not queued"}


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
