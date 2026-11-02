"""
Website 2 (verified review 2026-10-23)
======================================
(a) A BULK price change (Pricing & Offers: POST /products/bulk-price and
    /products/bulk-offer) wrote the billing product only. Its website twin
    kept the old price and was never queued, so bettervision.in kept selling
    at the old price. A bulk write now takes THE spine -> twin mirror rule a
    single product edit takes (product_master.mirror_update_to_catalog_twin):
    the twin's price moves, the twin is queued with a fresh edit revision,
    and an MRP that moves re-derives the discount rule's online price (the
    price the website shows when a rule owns it).

(b) An ARCHIVED product could go back on the website: every push asked the
    product gate, and the gate let anything with a Shopify id through -- and
    a push maps every status but ARCHIVED to ACTIVE, so a re-push of a
    taken-down archived listing put it back on sale. Archived products are
    now never pushed (the gate, shopify_push.product_push_refusal) and never
    queued (the sweep selector, and every door that queues a row it did not
    just edit: a collection unblock, a discount-rule re-price, a model's
    listing, an unlinked colour, a catalogue edit). ONE archived rule:
    product_master._is_archived.

mongomock; no network (every Shopify call would raise).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_website2_bulk_price_archived.py -q
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from api import dependencies as deps  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
from api.routers import online_store_push as push_router  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.services import online_discount_engine as ode  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_live_sync as live_sync  # noqa: E402
from api.services import shopify_push  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

pytestmark = pytest.mark.usefixtures("brand_is_for_the_website")

ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"], "active_store_id": "S1"}
ARCHIVED_AT = "2026-10-01T00:00:00+00:00"
PHOTO = ["https://cdn.example.com/p.jpg"]


def _run(coro):
    return asyncio.run(coro)


class _Conn:
    """The dependencies.get_db() shape over a mongomock database."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]

    def __getitem__(self, name):
        return self.db[name]


@pytest.fixture
def db(monkeypatch):
    raw = mongomock.MongoClient()["ims_website2"]
    conn = _Conn(raw)
    repo = ProductRepository(raw["products"])
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None)
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
    monkeypatch.setattr(catalog_mod, "_get_db", lambda: raw)

    # Every gate open, the network an exploding spy: a test passes only when
    # nothing is sent.
    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("an archived product reached the Shopify network")

    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "x.myshopify.com", "access_token": "y", "source": "vault"},
    )
    monkeypatch.setattr(shopify_push, "_graphql", _boom)
    return raw


def _seed(db, pid="P1", *, archived=False, live=True, mrp=5000.0, offer=4500.0, ecom=None):
    """A Ray-Ban frame: its billing spine and its website twin, the twin
    already on Shopify (live) and clean (the last push cleared the flag)."""
    spine = {
        "product_id": pid,
        "pim_product_id": "twin-" + pid,
        "sku": "FR-RB-" + pid,
        "brand": "Ray-Ban",
        "model": "RB2140",
        "category": "FRAME",
        "mrp": mrp,
        "offer_price": offer,
        "is_active": not archived,
        "catalog_status": "ACTIVE",
        "attributes": {"brand_name": "Ray-Ban", "model_no": "RB2140"},
    }
    twin = {
        "id": "twin-" + pid,
        "sku": spine["sku"],
        "title": "Ray-Ban RB2140",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "mrp": mrp,
        "offer_price": offer,
        "pricing": {"mrp": mrp, "offer_price": offer},
        "images": PHOTO,
        "is_active": not archived,
        "ecom": {"status": "PUBLISHED" if live else "DRAFT", "locally_modified": False, "edit_rev": "r0"},
    }
    if live:
        twin["ecom"]["shopify_product_id"] = "gid://shopify/Product/" + pid
    if archived:
        spine["deleted_at"] = twin["deleted_at"] = ARCHIVED_AT
        if live:
            # Taken down by the archive door: a Draft on Shopify.
            twin["ecom"]["status"] = "DRAFT"
    twin["ecom"].update(ecom or {})
    db["products"].insert_one(copy.deepcopy(spine))
    db["catalog_products"].insert_one(copy.deepcopy(twin))
    return spine, twin


def _twin(db, pid="P1"):
    return db["catalog_products"].find_one({"id": "twin-" + pid})


def _queued(db, pid="P1"):
    return bool((_twin(db, pid).get("ecom") or {}).get("locally_modified"))


# ===========================================================================
# (a) Bulk price changes reach the website
# ===========================================================================


def test_bulk_price_moves_the_website_twin_and_queues_it(db):
    _seed(db)
    out = _run(
        products_mod.bulk_price_update(
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True), ADMIN
        )
    )
    assert out["summary"]["committed"] == 1, out
    twin = _twin(db)
    # Both spellings: the push reads the top-level price first.
    assert (twin["mrp"], twin["pricing"]["mrp"]) == (5500.0, 5500.0)
    assert (twin["offer_price"], twin["pricing"]["offer_price"]) == (4950.0, 4950.0)
    assert twin["ecom"]["locally_modified"] is True
    assert twin["ecom"]["edit_rev"] != "r0", "a fresh edit revision, so a push in flight cannot un-queue it"
    assert twin["ecom"]["shopify_product_id"] == "gid://shopify/Product/P1"


def test_bulk_offer_moves_the_website_twin_and_queues_it(db):
    _seed(db)
    _run(products_mod.bulk_offer_update(products_mod.BulkOfferRequest(action="CLEAR", apply=True), ADMIN))
    twin = _twin(db)
    assert (twin["offer_price"], twin["pricing"]["offer_price"]) == (5000.0, 5000.0)
    assert twin["ecom"]["locally_modified"] is True


def test_a_bulk_dry_run_touches_neither_copy(db):
    _seed(db)
    _run(products_mod.bulk_price_update(products_mod.BulkPriceRequest(target="BOTH", amount=10), ADMIN))
    twin = _twin(db)
    assert twin["mrp"] == 5000.0 and twin["ecom"]["locally_modified"] is False


def test_a_bulk_mrp_change_re_derives_the_rule_price_the_website_shows(db):
    """A discount rule owns this frame's website price (MRP less 10%). The
    push sends THAT price, so an MRP raised in bulk must move it too, or the
    website keeps the old price under the new MRP."""
    db["ecom_discount_rules"].insert_one({"id": "R1", "category": "FRAME", "discount_percentage": 10})
    _seed(db, ecom={"online_offer_price": 4500.0, "online_compare_at_price": 5000.0, "online_price_source": "rule"})
    _run(
        products_mod.bulk_price_update(
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True), ADMIN
        )
    )
    ecom = _twin(db)["ecom"]
    assert (ecom["online_offer_price"], ecom["online_compare_at_price"]) == (4950.0, 5500.0)
    assert ecom["locally_modified"] is True


def test_the_single_spine_edit_re_derives_the_rule_price_too(db):
    """One rule: the same mirror, so the product editor's MRP change moves the
    rule price as the bulk tool does."""
    db["ecom_discount_rules"].insert_one({"id": "R1", "category": "FRAME", "discount_percentage": 10})
    spine, _ = _seed(
        db, ecom={"online_offer_price": 4500.0, "online_compare_at_price": 5000.0, "online_price_source": "rule"}
    )
    pm.mirror_update_to_catalog_twin(product_id="P1", current=spine, patch={"mrp": 6000.0}, db=_Conn(db))
    assert _twin(db)["ecom"]["online_offer_price"] == 5400.0


def test_bulk_tools_never_touch_an_archived_product(db):
    _seed(db, archived=True)
    _run(
        products_mod.bulk_price_update(
            products_mod.BulkPriceRequest(target="BOTH", amount=10, apply=True), ADMIN
        )
    )
    assert _twin(db)["mrp"] == 5000.0 and not _queued(db)


# ===========================================================================
# (b) An archived product is never pushed
# ===========================================================================


def test_the_publish_door_refuses_an_archived_listing(db):
    """Online Store > Push (one product): a taken-down archived listing would
    go back as ACTIVE. Refused by the gate, nothing sent."""
    _seed(db, archived=True, ecom={"locally_modified": True})
    out = _run(push_router.push_product("twin-P1", ADMIN))["result"]
    assert out["ok"] is False and out["mode"] == shopify_push.MODE_BLOCKED
    assert "archived" in out["error"]


def test_the_gate_names_an_archived_listing_for_every_push(db):
    """push_product, the price push, the photo push and the sweep all ask
    THE gate; it answers 'archived' for a listed product too."""
    _, twin = _seed(db, archived=True)
    assert "archived" in (shopify_push.product_push_refusal(db, twin) or "")
    _, live = _seed(db, "P2")
    assert shopify_push.product_push_refusal(db, live) is None


def test_push_all_pending_skips_an_archived_listing_quietly(db):
    _seed(db, archived=True, ecom={"locally_modified": True})
    dirty, _, archived_skipped = live_sync.select_dirty_products(db)
    assert dirty == [] and archived_skipped == 1
    body = _run(push_router.push_all_pending(entities="products", current_user=ADMIN))
    assert body["summary"]["products"]["archived_skipped"] == 1
    assert body["summary"]["products"]["pushed"] == 0
    assert all(r.get("target_id") != "twin-P1" for r in body["results"])


def test_an_archived_listing_counts_as_nothing_pending(db):
    _seed(db, archived=True, ecom={"locally_modified": True})
    assert push_router._product_counts(db)["pending"] == 0


# ===========================================================================
# (b) An archived product is never queued
# ===========================================================================


def test_a_rule_change_re_prices_but_never_queues_an_archived_product(db):
    """The rule went from 5% to 10% off (recompute_all after the rule edit)."""
    db["ecom_discount_rules"].insert_one({"id": "R1", "category": "FRAME", "discount_percentage": 10})
    was = {"online_offer_price": 4750.0, "online_compare_at_price": 5000.0, "online_price_source": "rule"}
    _seed(db, archived=True, ecom=was)
    _seed(db, "P2", ecom=was)
    res = ode.recompute_all(db, category="FRAME")
    assert res["ok"] and res["products"] == 2
    assert _twin(db)["ecom"]["online_offer_price"] == 4500.0, "the stored price still follows the rule"
    assert not _queued(db), "an archived product never enters the website queue"
    assert _queued(db, "P2"), "a live one does (OS-016)"


def test_a_catalogue_edit_of_an_archived_product_does_not_queue_it(db):
    _seed(db, archived=True)
    doc = _twin(db)
    doc.pop("_id", None)
    doc["description"] = "new copy"
    catalog_mod._save_catalog_product(doc)
    assert _twin(db)["description"] == "new copy"
    assert not _queued(db)


def test_a_billing_product_edit_of_an_archived_product_mirrors_but_does_not_queue(db):
    spine, _ = _seed(db, archived=True)
    pm.mirror_update_to_catalog_twin(product_id="P1", current=spine, patch={"mrp": 5200.0}, db=_Conn(db))
    assert _twin(db)["mrp"] == 5200.0
    assert not _queued(db)


def _family(db, *, lead_archived=False, child_archived=False):
    """Lead L and colour C of one model, C linked to L."""
    lead, _ = _seed(db, "L", archived=lead_archived)
    child, _ = _seed(db, "C", archived=child_archived, live=False)
    link = {"product_id": "L", "twin_id": "twin-L", "sku": lead["sku"]}
    db["products"].update_one({"product_id": "C"}, {"$set": {"variant_of": "L"}})
    db["catalog_products"].update_one({"id": "twin-C"}, {"$set": {"ecom.variant_of": link}})
    db["catalog_variants"].insert_many([
        {"sku": lead["sku"], "parent_product_id": "twin-L", "parent_sku": lead["sku"]},
        {"sku": child["sku"], "parent_product_id": "twin-L", "parent_sku": lead["sku"]},
    ])


def test_a_colour_edit_never_queues_an_archived_lead_s_listing(db):
    _family(db, lead_archived=True)
    pm.queue_model_listing_of(_Conn(db), db["products"].find_one({"product_id": "C"}))
    assert not _queued(db, "L")


def test_a_colour_edit_queues_a_live_lead_s_listing(db):
    _family(db)
    pm.queue_model_listing_of(_Conn(db), db["products"].find_one({"product_id": "C"}))
    assert _queued(db, "L")


def test_unlinking_an_archived_colour_does_not_queue_it(db):
    _family(db, child_archived=True)
    pm.unlink_from_family(_Conn(db), "C", actor="u1")
    assert "variant_of" not in _twin(db, "C")["ecom"]
    assert not _queued(db, "C")
    assert _queued(db, "L"), "the model's listing still drops the colour"


# ===========================================================================
# Review of #1301: the website copy result, sizes, the remaining doors
# ===========================================================================


def test_a_failed_website_copy_is_reported_per_row_and_in_the_summary(db, monkeypatch):
    """The billing price saved but the website copy did not: the operator is
    told which row, so it can be saved again (the price change is not lost
    in a green 'applied')."""
    _seed(db)
    _seed(db, "P2")
    monkeypatch.setattr(
        pm, "mirror_update_to_catalog_twin", lambda **kw: kw["product_id"] != "P2"
    )
    out = _run(
        products_mod.bulk_price_update(
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True), ADMIN
        )
    )
    by_pid = {r["product_id"]: r for r in out["rows"]}
    assert by_pid["P1"]["applied"] is True and by_pid["P1"]["website_synced"] is True
    assert by_pid["P2"]["applied"] is True and by_pid["P2"]["website_synced"] is False
    assert out["summary"]["website_failed"] == 1
    off = _run(products_mod.bulk_offer_update(products_mod.BulkOfferRequest(action="CLEAR", apply=True), ADMIN))
    assert off["summary"]["website_failed"] == 1
    assert {r["product_id"]: r["website_synced"] for r in off["rows"]} == {"P1": True, "P2": False}


def _size_family(db, *, lead_archived):
    """Lead L and a LIVE size C of one model; C's price lives on its
    catalog_variants row, which rides L's listing."""
    _family(db, lead_archived=lead_archived)
    # Only the size is in the bulk run (the archived lead is out of scope anyway).
    db["products"].update_one({"product_id": "C"}, {"$set": {"brand": "Ray-Ban", "category": "FRAME"}})


def test_a_bulk_mrp_change_on_a_size_moves_its_row_and_queues_a_live_lead(db):
    _size_family(db, lead_archived=False)
    _run(
        products_mod.bulk_price_update(
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True, brand="Ray-Ban"),
            ADMIN,
        )
    )
    row = db["catalog_variants"].find_one({"sku": "FR-RB-C"})
    assert row["mrp"] == 5500.0
    assert _queued(db, "L")


def test_a_bulk_mrp_change_on_a_size_never_queues_an_archived_lead(db):
    _size_family(db, lead_archived=True)
    _run(
        products_mod.bulk_price_update(
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True, brand="Ray-Ban"),
            ADMIN,
        )
    )
    row = db["catalog_variants"].find_one({"sku": "FR-RB-C"})
    assert row["mrp"] == 5500.0, "the size's own price still moves"
    assert not _queued(db, "L"), "an archived lead's listing never enters the website queue"


def test_the_price_push_refuses_an_archived_listing(db):
    _, twin = _seed(db, archived=True)
    out = _run(shopify_push.push_variant_prices(db, twin))
    assert out.ok is False and out.mode == shopify_push.MODE_BLOCKED and "archived" in out.error


def test_the_photo_push_refuses_an_archived_listing(db):
    _seed(db, archived=True)
    out = _run(shopify_push.push_image(db, {"image_id": "I1", "product_id": "twin-P1", "status": "APPROVED"}))
    assert out.ok is False and out.mode == shopify_push.MODE_BLOCKED and "archived" in out.error


def test_a_listed_copy_whose_billing_product_is_archived_is_refused(db):
    _, twin = _seed(db)
    db["products"].update_one({"product_id": "P1"}, {"$set": {"deleted_at": ARCHIVED_AT}})
    assert "archived" in (shopify_push.product_push_refusal(db, twin) or "")


def test_a_listed_copy_is_refused_when_its_billing_product_cannot_be_read(db, monkeypatch):
    _, twin = _seed(db)
    from api.services.shopify_push import listing

    def _dead(db, product):
        raise RuntimeError("read failed")

    monkeypatch.setattr(listing, "lead_spine", _dead)
    assert "could not be read" in (shopify_push.product_push_refusal(db, twin) or "")


def test_archiving_a_listed_product_takes_it_off_the_queue(db, monkeypatch):
    """A queued listing that is archived: every later sync would only count it
    as skipped. The archive door clears its flag (the edit_rev rule)."""
    _seed(db, ecom={"locally_modified": True, "edit_rev": "r1"})

    async def _no_takedown(*a, **kw):
        return {"ok": True}

    monkeypatch.setattr(catalog_mod._delist, "delist_if_live", _no_takedown)
    monkeypatch.setattr(catalog_mod, "_catalog_coll", lambda: db["catalog_products"])
    monkeypatch.setattr(catalog_mod, "invalidate_product_lists", lambda: None)
    _run(catalog_mod._archive_product(_twin(db), ADMIN, spine_id="P1"))
    assert _twin(db).get("deleted_at")
    assert not _queued(db)
