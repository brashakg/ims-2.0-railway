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
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

pytestmark = pytest.mark.usefixtures("brand_is_for_the_website")

ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"], "active_store_id": "S1"}
ARCHIVED_AT = "2026-10-01T00:00:00+00:00"
PHOTO = ["https://cdn.example.com/p.jpg"]


def _run(coro):
    return asyncio.run(coro)


async def _reviewed(fn, body, user=ADMIN):
    """Preview, then Apply exactly the rows that preview showed (L1-F47)."""
    pre = await fn(body.model_copy(update={"apply": False}), user)
    exp = [
        products_mod.BulkReviewedRow(
            product_id=r["product_id"], mrp=r.get("old_mrp", r.get("mrp")), offer_price=r["old_offer_price"], version=r.get("version", "")
        )
        for r in pre["rows"]
    ]
    return await fn(body.model_copy(update={"apply": True, "expected": exp}), user)


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
    monkeypatch.setattr(deps, "get_audit_repository", lambda: AuditRepository(raw["audit_logs"]))
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
        _reviewed(
            products_mod.bulk_price_update,
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True),
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
    _run(_reviewed(products_mod.bulk_offer_update, products_mod.BulkOfferRequest(action="CLEAR", apply=True)))
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
        _reviewed(
            products_mod.bulk_price_update,
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True),
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
        _reviewed(
            products_mod.bulk_price_update,
            products_mod.BulkPriceRequest(target="BOTH", amount=10, apply=True),
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


def test_a_retried_apply_carries_the_website_copy_but_never_the_price_twice(db, monkeypatch):
    """L1-F47 crash between two writes: the first Apply saved the billing
    price but not the website copy (it stopped / its reply was lost). The
    same reviewed Apply again: the billing price is NOT moved a second time
    (5500, never 6050) and the website copy now follows."""
    _seed(db)
    body = products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10)
    pre = _run(products_mod.bulk_price_update(body, ADMIN))
    exp = [
        products_mod.BulkReviewedRow(product_id=r["product_id"], mrp=r["old_mrp"], offer_price=r["old_offer_price"], version=r.get("version", ""))
        for r in pre["rows"]
    ]
    apply = body.model_copy(update={"apply": True, "expected": exp})
    real_mirror = pm.mirror_update_to_catalog_twin
    monkeypatch.setattr(pm, "mirror_update_to_catalog_twin", lambda **kw: False)
    first = _run(products_mod.bulk_price_update(apply, ADMIN))
    assert first["summary"]["committed"] == 1 and first["summary"]["website_failed"] == 1
    assert _twin(db)["mrp"] == 5000.0
    monkeypatch.setattr(pm, "mirror_update_to_catalog_twin", real_mirror)
    again = _run(products_mod.bulk_price_update(apply, ADMIN))
    assert again["summary"]["committed"] == 0
    assert again["rows"][0]["already_applied"] is True and again["rows"][0]["website_synced"] is True
    spine = db["products"].find_one({"product_id": "P1"})
    assert (spine["mrp"], spine["offer_price"]) == (5500.0, 4950.0)
    assert (_twin(db)["mrp"], _twin(db)["offer_price"]) == (5500.0, 4950.0)


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
        _reviewed(
            products_mod.bulk_price_update,
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True),
        )
    )
    by_pid = {r["product_id"]: r for r in out["rows"]}
    assert by_pid["P1"]["applied"] is True and by_pid["P1"]["website_synced"] is True
    assert by_pid["P2"]["applied"] is True and by_pid["P2"]["website_synced"] is False
    assert out["summary"]["website_failed"] == 1
    off = _run(_reviewed(products_mod.bulk_offer_update, products_mod.BulkOfferRequest(action="CLEAR", apply=True)))
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
        _reviewed(
            products_mod.bulk_price_update,
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True, brand="Ray-Ban"),
        )
    )
    row = db["catalog_variants"].find_one({"sku": "FR-RB-C"})
    assert row["mrp"] == 5500.0
    assert _queued(db, "L")


def test_a_bulk_mrp_change_on_a_size_never_queues_an_archived_lead(db):
    _size_family(db, lead_archived=True)
    _run(
        _reviewed(
            products_mod.bulk_price_update,
            products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10, apply=True, brand="Ray-Ban"),
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


def test_a_retried_offer_apply_carries_the_website_copy_but_never_the_offer_twice(db, monkeypatch):
    """The offer twin of the retry above: the billing offer landed, the
    website copy did not. The same reviewed Apply again re-carries the copy
    (it used to skip it) and never moves the billing offer a second time."""
    _seed(db, offer=5000.0)
    body = products_mod.BulkOfferRequest(action="SET", discount_percent=10)
    pre = _run(products_mod.bulk_offer_update(body, ADMIN))
    exp = [
        products_mod.BulkReviewedRow(product_id=r["product_id"], mrp=r["mrp"], offer_price=r["old_offer_price"], version=r.get("version", ""))
        for r in pre["rows"]
    ]
    apply = body.model_copy(update={"apply": True, "expected": exp})
    real_mirror = pm.mirror_update_to_catalog_twin
    monkeypatch.setattr(pm, "mirror_update_to_catalog_twin", lambda **kw: False)
    first = _run(products_mod.bulk_offer_update(apply, ADMIN))
    assert first["summary"]["committed"] == 1 and first["summary"]["website_failed"] == 1
    assert _twin(db)["offer_price"] == 5000.0
    monkeypatch.setattr(pm, "mirror_update_to_catalog_twin", real_mirror)
    again = _run(products_mod.bulk_offer_update(apply, ADMIN))
    assert again["summary"]["committed"] == 0
    assert again["rows"][0]["already_applied"] is True and again["rows"][0]["website_synced"] is True
    assert db["products"].find_one({"product_id": "P1"})["offer_price"] == 4500.0
    assert (_twin(db)["offer_price"], _twin(db)["pricing"]["offer_price"]) == (4500.0, 4500.0)


def test_a_retried_apply_whose_website_copy_fails_again_counts_it_once(db, monkeypatch):
    """One already-saved row whose website copy fails on the retry is ONE
    product to save again, on both endpoints (the price door counted 2)."""
    _seed(db)
    monkeypatch.setattr(pm, "mirror_update_to_catalog_twin", lambda **kw: False)
    for fn, body in (
        (products_mod.bulk_price_update, products_mod.BulkPriceRequest(target="BOTH", mode="PERCENT", amount=10)),
        (products_mod.bulk_offer_update, products_mod.BulkOfferRequest(action="CLEAR")),
    ):
        pre = _run(fn(body, ADMIN))
        exp = [
            products_mod.BulkReviewedRow(
                product_id=r["product_id"], mrp=r.get("old_mrp", r.get("mrp")), offer_price=r["old_offer_price"], version=r.get("version", "")
            )
            for r in pre["rows"]
        ]
        apply = body.model_copy(update={"apply": True, "expected": exp})
        _run(fn(apply, ADMIN))
        again = _run(fn(apply, ADMIN))
        assert again["rows"][0]["already_applied"] is True
        assert again["summary"]["website_failed"] == 1, (fn.__name__, again["summary"])


# ===========================================================================
# D84 / PR-1429 round 3: every landed bulk change has exactly one audit
# entry, written before success; anything unconfirmed answers 503 so the
# page holds the preview and offers the identical retry.
# ===========================================================================

_BOTH_DOORS = (
    (products_mod.bulk_price_update, products_mod.BulkPriceRequest(target="BOTH", mode="FLAT", amount=10),
     "BULK_PRICE_UPDATE", (5010.0, 4510.0)),
    (products_mod.bulk_offer_update, products_mod.BulkOfferRequest(action="CLEAR"),
     "BULK_OFFER_UPDATE", (5000.0, 5000.0)),
)


def _reviewed_apply(db, fn, body):
    db["products"].delete_many({})
    db["catalog_products"].delete_many({})
    db["audit_logs"].delete_many({})
    db["audit_chain_head"].delete_many({})
    _seed(db)
    _seed(db, "P2")
    pre = _run(fn(body, ADMIN))
    exp = [
        products_mod.BulkReviewedRow(
            product_id=r["product_id"], mrp=r.get("old_mrp", r.get("mrp")), offer_price=r["old_offer_price"], version=r.get("version", "")
        )
        for r in pre["rows"]
    ]
    return body.model_copy(update={"apply": True, "expected": exp})


def _audits(db, action):
    return list(db["audit_logs"].find({"action": action}))


def _named(db, action):
    """D94(3): one entry per Apply per product -- every product a bulk audit
    entry names, as often as it is named (a doubled entry shows twice)."""
    return sorted(c["product_id"] for a in _audits(db, action) for c in a["details"]["changes"])


def _prices(db, pid):
    d = db["products"].find_one({"product_id": pid})
    return d["mrp"], d["offer_price"]


def _lands_then_raises(monkeypatch, repo, victim="P1"):
    real_update = type(repo).update

    def update(self, pid, data, **kw):
        done = real_update(self, pid, data, **kw)
        if pid == victim:
            raise RuntimeError("AutoReconnect after the server applied the write")
        return done

    monkeypatch.setattr(type(repo), "update", update)
    return lambda: monkeypatch.setattr(type(repo), "update", real_update)


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_lost_acknowledgement_is_committed_audited_and_reaches_the_website(db, monkeypatch, fn, body, action, want):
    """The database applied P1's write and then raised. The one Apply the
    page sends must not answer a clean 200 that leaves P1 out: P1 is read
    back, found at this Apply's write, and counted, audited and copied to
    the website like any other row."""
    apply = _reviewed_apply(db, fn, body)
    restore = _lands_then_raises(monkeypatch, products_mod.get_product_repository())
    out = _run(fn(apply, ADMIN))
    restore()
    assert out["summary"]["committed"] == 2
    assert {r["product_id"]: r["applied"] for r in out["rows"]} == {"P1": True, "P2": True}
    assert _named(db, action) == ["P1", "P2"]
    assert _prices(db, "P1") == want
    assert (_twin(db)["mrp"], _twin(db)["offer_price"]) == want


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_write_that_cannot_be_confirmed_answers_503_and_the_retry_finishes_it_once(
    db, monkeypatch, fn, body, action, want
):
    """P1's write landed and raised, and reading P1 back failed too: the
    request answers 503 (the page holds the preview and offers the retry),
    never a 200 that hides P1. The identical retry moves nothing again and
    writes ONE audit entry naming both landed rows."""
    from fastapi import HTTPException

    apply = _reviewed_apply(db, fn, body)
    restore = _lands_then_raises(monkeypatch, products_mod.get_product_repository())
    real_read = getattr(products_mod, "_stored_doc", None)

    def read(repo, pid):
        if pid == "P1":
            raise RuntimeError("read-back failed too")
        return real_read(repo, pid)

    monkeypatch.setattr(products_mod, "_stored_doc", read, raising=False)
    with pytest.raises(HTTPException) as ei:
        _run(fn(apply, ADMIN))
    assert ei.value.status_code == 503 and "Retry the same Apply" in ei.value.detail
    restore()
    monkeypatch.setattr(products_mod, "_stored_doc", real_read, raising=False)
    # D94(2): P2 was confirmed, so it is audited before the 503 for P1 --
    # and so is P1: its write landed, and the record that write left on it
    # is found by the end-of-Apply read (D84), even though its own read-back failed.
    assert _named(db, action) == ["P1", "P2"]
    again = _run(fn(apply, ADMIN))
    assert again["summary"]["committed"] == 0 and again["summary"]["counts"]["already_applied"] == 2
    assert _named(db, action) == ["P1", "P2"]
    assert _prices(db, "P1") == want and _prices(db, "P2") == want


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_an_audit_that_cannot_be_written_answers_503_and_the_retry_writes_it_once(
    db, monkeypatch, fn, body, action, want
):
    """D84: the prices landed but the audit insert failed. The request
    answers 503 (not 200 with the change unrecorded); the identical retry
    writes the entry once, and a further retry writes nothing more."""
    from fastapi import HTTPException

    apply = _reviewed_apply(db, fn, body)
    real_create = AuditRepository.create
    monkeypatch.setattr(AuditRepository, "create", lambda self, data: None)
    with pytest.raises(HTTPException) as ei:
        _run(fn(apply, ADMIN))
    assert ei.value.status_code == 503
    monkeypatch.setattr(AuditRepository, "create", real_create)
    assert _audits(db, action) == []
    assert _prices(db, "P1") == want
    for _ in range(2):
        again = _run(fn(apply, ADMIN))
        assert again["summary"]["counts"]["already_applied"] == 2
        assert _named(db, action) == ["P1", "P2"]
    assert _prices(db, "P1") == want and _prices(db, "P2") == want


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_retried_apply_never_writes_a_second_audit_entry(db, monkeypatch, fn, body, action, want):
    """The Apply landed and was audited; its reply was lost and the page
    sent the identical retry. One change, one audit entry."""
    apply = _reviewed_apply(db, fn, body)
    first = _run(fn(apply, ADMIN))
    assert first["summary"]["committed"] == 2
    again = _run(fn(apply, ADMIN))
    assert again["summary"]["committed"] == 0
    assert _named(db, action) == ["P1", "P2"]
    assert not any(c.get("recovered") for a in _audits(db, action) for c in a["details"]["changes"])


def test_a_new_change_back_to_the_same_prices_is_audited_again(db, monkeypatch):
    """The fixed id belongs to one Apply, not to the rows or prices: the
    same +10 previewed again after the prices were put back is a new change
    with its own entry."""
    fn, body, action, _want = _BOTH_DOORS[0]
    apply = _reviewed_apply(db, fn, body)
    _run(fn(apply, ADMIN))
    db["products"].update_many({}, {"$set": {"mrp": 5000.0, "offer_price": 4500.0}})
    _run(_reviewed(fn, body.model_copy(update={"apply": True})))
    assert _named(db, action) == ["P1", "P1", "P2", "P2"]


def test_a_fresh_preview_shows_rows_a_bulk_apply_changed_in_the_last_hour(db):
    """The page's hold on an unconfirmed Apply lives in the browser; a reload
    or a fresh preview loses it. The preview itself now names the rows a
    bulk Apply moved in the last hour, so a landed +10 is not re-applied
    unseen as a new change."""
    import datetime as _dt

    fn, body, _action, _want = _BOTH_DOORS[0]
    apply = _reviewed_apply(db, fn, body)
    pre = _run(fn(body, ADMIN))
    assert {r["recent_bulk_change_at"] for r in pre["rows"]} == {None}
    _run(fn(apply, ADMIN))
    again = _run(fn(body, ADMIN))
    assert all(r["recent_bulk_change_at"] for r in again["rows"])
    offer = _run(products_mod.bulk_offer_update(products_mod.BulkOfferRequest(action="CLEAR"), ADMIN))
    assert all(r["recent_bulk_change_at"] for r in offer["rows"])
    old = _dt.datetime.utcnow() - _dt.timedelta(hours=2)
    db["products"].update_many({}, {"$set": {"price_updated_at": old}})
    assert {r["recent_bulk_change_at"] for r in _run(fn(body, ADMIN))["rows"]} == {None}


# ===========================================================================
# D94 (PR-1429, head 516ad51f): an Apply never rewrites a row written since
# its preview (ABA), and a retry racing the original never hides a row.
# ===========================================================================

_WAS = (5000.0, 4500.0)


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_row_moved_back_to_its_old_price_is_never_reapplied(db, monkeypatch, fn, body, action, want):
    """D94(1): W1 lands, someone edits P1 back to its previewed price
    through the ordinary edit door, then W1's identical retry arrives. The
    price equals the preview again, but the row was written since: it is
    reported 'changed since preview' and left alone (it used to be moved a
    second time). P2, which W1 landed, is recognised as W1's own."""
    apply = _reviewed_apply(db, fn, body)
    assert _run(fn(apply, ADMIN))["summary"]["committed"] == 2
    assert products_mod.get_product_repository().update("P1", {"mrp": _WAS[0], "offer_price": _WAS[1]})
    again = _run(fn(apply, ADMIN))
    rows = {r["product_id"]: r for r in again["rows"]}
    assert rows["P1"]["reason"] == "CHANGED_SINCE_PREVIEW" and rows["P1"]["applied"] is False
    assert rows["P2"]["already_applied"] is True
    assert again["summary"]["committed"] == 0
    assert again["summary"]["counts"]["changed_since_preview"] == 1
    assert _prices(db, "P1") == _WAS and _prices(db, "P2") == want
    assert _named(db, action) == ["P1", "P2"]


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_retry_racing_the_original_never_hides_the_original_s_rows(db, monkeypatch, fn, body, action, want):
    """D94(3): the original Apply A has written P1 and is about to write P2
    when its identical retry B runs: B reads (P1 landed, P2 not yet), A's P2
    write lands, B's own P2 write is refused, and B writes its audit first.
    Every row A landed is still audited, each exactly once (it used to be
    one entry per Apply that B wrote with P1 only, so A's P2 was hidden)."""
    from concurrent.futures import ThreadPoolExecutor

    apply = _reviewed_apply(db, fn, body)
    repo_type = type(products_mod.get_product_repository())
    real_update = repo_type.update
    seen = {"a_p2": False}

    def a_update(self, pid, data, **kw):
        if pid != "P2" or seen["a_p2"]:
            return real_update(self, pid, data, **kw)
        seen["a_p2"] = True
        held = {}

        def b_update(self2, pid2, data2, **kw2):
            if pid2 == "P2" and "a" not in held:
                held["a"] = real_update(self2, pid2, data, **kw)  # A's write lands first
            return real_update(self2, pid2, data2, **kw2)

        monkeypatch.setattr(repo_type, "update", b_update)
        with ThreadPoolExecutor(1) as ex:
            ex.submit(lambda: _run(fn(apply, ADMIN))).result()
        monkeypatch.setattr(repo_type, "update", a_update)
        return held["a"]

    monkeypatch.setattr(repo_type, "update", a_update)
    _run(fn(apply, ADMIN))
    monkeypatch.setattr(repo_type, "update", real_update)
    assert seen["a_p2"]
    assert _named(db, action) == ["P1", "P2"]
    assert _prices(db, "P1") == want and _prices(db, "P2") == want



def test_a_retry_with_changed_content_never_moves_a_landed_row_again(db, monkeypatch):
    """D94: P1 landed under the first Apply and P2's write could not be
    confirmed (503). A retry that changes the operation (+20 instead of +10)
    on the same reviewed rows is a different Apply: P1, written since the
    preview, is left alone (never +10 and then +20); P2, still exactly as
    previewed, takes the new operation once."""
    from fastapi import HTTPException

    fn, body, action, _want = _BOTH_DOORS[0]
    apply = _reviewed_apply(db, fn, body)
    real_update = type(products_mod.get_product_repository()).update

    def p2_fails(self, pid, data, **kw):
        if pid == "P2":
            raise RuntimeError("write failed before reaching the server")
        return real_update(self, pid, data, **kw)

    monkeypatch.setattr(type(products_mod.get_product_repository()), "update", p2_fails)
    with pytest.raises(HTTPException) as ei:
        _run(fn(apply, ADMIN))
    assert ei.value.status_code == 503
    monkeypatch.setattr(type(products_mod.get_product_repository()), "update", real_update)
    changed = _run(fn(apply.model_copy(update={"amount": 20.0}), ADMIN))
    rows = {r["product_id"]: r for r in changed["rows"]}
    assert rows["P1"]["reason"] == "CHANGED_SINCE_PREVIEW"
    assert rows["P2"]["applied"] is True
    assert _prices(db, "P1") == (5010.0, 4510.0) and _prices(db, "P2") == (5020.0, 4520.0)
    assert _named(db, action) == ["P1", "P2"]


# ===========================================================================
# D84 / D94(2)(3) (PR-1429, head 9883bfce): every row an Apply landed keeps
# its audit entry -- when the row is edited again before the retry, when the
# retry is refused 409 for a changed scope, and when staff press Preview
# instead of the retry (read repair). The entry comes from the record the
# Apply's own write left on the product.
# ===========================================================================


def _apply_whose_audit_fails(db, monkeypatch, fn, body, action):
    from fastapi import HTTPException

    apply = _reviewed_apply(db, fn, body)
    real_create = AuditRepository.create
    monkeypatch.setattr(AuditRepository, "create", lambda self, data: None)
    with pytest.raises(HTTPException) as ei:
        _run(fn(apply, ADMIN))
    assert ei.value.status_code == 503
    monkeypatch.setattr(AuditRepository, "create", real_create)
    assert _audits(db, action) == []
    return apply


def _entry(db, action, pid):
    (a,) = [a for a in _audits(db, action) if a["entity_id"] == pid]
    return a


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_landed_row_edited_before_the_retry_is_still_audited(db, monkeypatch, fn, body, action, want):
    """X lands P1 and P2, its audit cannot be written (503 'press again'),
    then P2 is edited through the ordinary edit door. The identical retry
    leaves P2 alone (never moved again) and still writes X's entry for P2,
    with the change X made (was -> X's price), not today's price."""
    apply = _apply_whose_audit_fails(db, monkeypatch, fn, body, action)
    assert products_mod.get_product_repository().update("P2", {"mrp": 6000.0, "offer_price": 5500.0})
    again = _run(fn(apply, ADMIN))
    assert {r["product_id"]: r.get("reason") for r in again["rows"]}["P2"] == "CHANGED_SINCE_PREVIEW"
    assert _prices(db, "P2") == (6000.0, 5500.0) and _prices(db, "P1") == want
    assert _named(db, action) == ["P1", "P2"]
    p2 = _entry(db, action, "P2")
    assert p2["details"]["changes"][0]["offer_price"] == [_WAS[1], want[1]]
    assert p2["user_id"] == ADMIN["user_id"] and p2["log_id"].startswith(action + ":")


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
@pytest.mark.parametrize("drift", ["added", "gone"])
def test_a_retry_refused_for_a_changed_scope_still_audits_the_landed_rows(
    db, monkeypatch, fn, body, action, want, drift
):
    """X lands P1 and P2, its audit cannot be written; then a product joins
    the scope (or P2 leaves it). The identical retry is refused 409 and
    writes nothing -- but X's landed rows are audited before the refusal,
    the one that left the scope included."""
    from fastapi import HTTPException

    apply = _apply_whose_audit_fails(db, monkeypatch, fn, body, action)
    if drift == "added":
        _seed(db, "P3")
    else:
        db["products"].update_one({"product_id": "P2"}, {"$set": {"is_active": False}})
    with pytest.raises(HTTPException) as ei:
        _run(fn(apply, ADMIN))
    assert ei.value.status_code == 409
    assert _named(db, action) == ["P1", "P2"]
    assert _prices(db, "P1") == want and _prices(db, "P2") == want


@pytest.mark.parametrize("fn,body,action,want", _BOTH_DOORS)
def test_a_preview_instead_of_the_retry_audits_the_landed_rows_once(db, monkeypatch, fn, body, action, want):
    """D84 read repair on the D94(4) path: X lands P1 and P2, its audit
    cannot be written, and staff press Preview instead of the retry. That
    read writes X's entries (under X's id and X's user), once; a product
    that merely carries a copy of another product's record gets none."""
    _apply_whose_audit_fails(db, monkeypatch, fn, body, action)
    p1 = db["products"].find_one({"product_id": "P1"})
    _seed(db, "P3")
    db["products"].update_one(
        {"product_id": "P3"}, {"$set": {"bulk_write_id": p1["bulk_write_id"], "bulk_write": p1["bulk_write"]}}
    )
    for _ in range(2):
        _run(fn(body, ADMIN))
        assert _named(db, action) == ["P1", "P2"]
    assert {a["details"]["apply_id"] for a in _audits(db, action)} == {p1["bulk_write_id"]}
    assert {a["user_id"] for a in _audits(db, action)} == {ADMIN["user_id"]}
