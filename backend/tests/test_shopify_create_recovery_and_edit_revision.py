"""SF-02 + SF-04 (audit 18 Oct 2026, owner-approved Batch 1).

SF-02: a create whose answer was lost is never repeated blindly. The transport
never re-sends a create mutation after a send with an unknown outcome; a first
publish holds a durable claim, carries an identity metafield, and the next
press finds and ADOPTS the listing on Shopify before it would ever create
again. A concurrent second press never creates a second product.

SF-04: finishing an old push never takes a newer catalogue edit off the queue,
and never loses a sibling ecom field written meanwhile.

Synthetic only: mongomock DB, a fake Shopify. No network, no production.
"""

import asyncio
import copy
from datetime import timedelta

import httpx
import mongomock
import pytest

from api.services import catalog_dictionary, shopify_push
from api.services.catalog_ecom import dirty_marks, update_ecom
from api.services.shopify_push import create_claim, transport
from api.services.shopify_push.queries import (
    _COLLECTION_CREATE,
    _MENU_CREATE,
    _PRODUCT_CREATE_MEDIA,
    _VARIANTS_BULK_CREATE,
)
from api.services.shopify_fulfillment_push import _FULFILLMENT_CREATE
from shopify_answers import tracking_answer

GID = "gid://shopify/Product/900"
VARIANT = {
    "id": "gid://shopify/ProductVariant/5001",
    "title": "Default Title",
    "selectedOptions": [{"name": "Title", "value": "Default Title"}],
    "inventoryItem": {"id": "gid://shopify/InventoryItem/6001"},
}


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# 1. TRANSPORT -- a create is never auto-repeated after an unknown outcome
# ===========================================================================


def _transport(monkeypatch, answers):
    calls = []

    async def _post(url, headers, payload):
        calls.append(copy.deepcopy(payload))
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return a

    async def _no_sleep(*_a):
        return None

    monkeypatch.setattr(transport, "resolve_shopify_credentials",
                        lambda db, sf="BV": {"shop_url": "x.invalid", "access_token": "t"})
    monkeypatch.setattr(transport, "_post_once", _post)
    monkeypatch.setattr(transport.asyncio, "sleep", _no_sleep)
    return calls


_OK = httpx.Response(200, json={"data": {"productCreate": {
    "product": {"id": "gid://shopify/Product/SECOND"}, "userErrors": []}}})


def test_audit_probe_lost_create_reply_is_not_sent_twice(monkeypatch):
    """THE AUDIT PROBE, inverted. Shopify commits the first productCreate and
    the reply is lost (read timeout). On main the transport sent an identical
    second create and returned the SECOND id -- an orphan duplicate."""
    calls = _transport(monkeypatch, [httpx.ReadTimeout("reply lost after commit"), _OK])
    with pytest.raises(shopify_push.ShopifyOutcomeUnknown) as exc:
        _run(transport._graphql(None, shopify_push._PRODUCT_CREATE, {"input": {"title": "x"}}))
    assert len(calls) == 1
    assert "NOT sent again" in str(exc.value)


@pytest.mark.parametrize("fault", [
    httpx.Response(502, text="bad gateway"),
    httpx.Response(503, text="unavailable"),
    httpx.ReadTimeout("x"),
    httpx.WriteTimeout("x"),
    httpx.RemoteProtocolError("x"),
    httpx.ReadError("x"),
])
@pytest.mark.parametrize("query", [
    shopify_push._PRODUCT_CREATE, _COLLECTION_CREATE, _MENU_CREATE,
    _PRODUCT_CREATE_MEDIA, _VARIANTS_BULK_CREATE, _FULFILLMENT_CREATE,
], ids=["product", "collection", "menu", "media", "variants", "fulfillment"])
def test_every_create_mutation_is_sent_once_on_an_unknown_outcome(monkeypatch, fault, query):
    calls = _transport(monkeypatch, [fault, _OK])
    with pytest.raises(shopify_push.ShopifyOutcomeUnknown):
        _run(transport._graphql(None, query, {}))
    assert len(calls) == 1


@pytest.mark.parametrize("fault", [
    httpx.ConnectTimeout("never reached Shopify"),
    httpx.Response(429, text="slow down"),
    httpx.Response(200, json={"errors": [{"extensions": {"code": "THROTTLED"}}]}),
])
def test_a_create_shopify_never_ran_is_still_retried(monkeypatch, fault):
    calls = _transport(monkeypatch, [fault, _OK])
    body = _run(transport._graphql(None, shopify_push._PRODUCT_CREATE, {}))
    assert len(calls) == 2 and body["data"]["productCreate"]["product"]["id"].endswith("SECOND")


@pytest.mark.parametrize("fault", [httpx.ReadTimeout("x"), httpx.Response(503, text="x")])
def test_reads_and_updates_keep_the_retry(monkeypatch, fault):
    calls = _transport(monkeypatch, [fault, _OK])
    _run(transport._graphql(None, shopify_push._PRODUCT_UPDATE, {}))
    assert len(calls) == 2


# ===========================================================================
# 2. FIRST PUBLISH -- claim, identity, resolve before any repeat
# ===========================================================================


class _Shopify:
    """Fake Shopify keyed on the operation; `create` decides what productCreate
    does; `existing` is what a products search finds."""

    def __init__(self):
        self.calls = []
        self.existing = []  # [(gid, ims_product_id)]
        self.create = "ok"
        self.hold = None  # an asyncio.Event a create waits on

    def ops(self, marker):
        return [c for c in self.calls if marker in c["query"]]

    async def __call__(self, db, query, variables):
        self.calls.append({"query": query, "variables": copy.deepcopy(variables)})
        t = tracking_answer(query, variables)
        if t:
            return t
        if "imsFindCreated" in query:
            return {"data": {"products": {"nodes": [
                {"id": g, "identity": {"value": p}} for g, p in reversed(self.existing)]}}}
        if "imsProductCreateMedia" in query:
            return {"data": {"productCreateMedia": {
                "media": [{"id": "gid://shopify/MediaImage/1"}], "mediaUserErrors": []}}}
        if "imsProductCreate" in query:
            if self.hold is not None:
                await self.hold.wait()
            if self.create == "lost":
                # Shopify made it; the answer never came back.
                ident = [m["value"] for m in variables["input"].get("metafields") or []]
                self.existing.append((GID, ident[0] if ident else None))
                raise shopify_push.ShopifyOutcomeUnknown("Shopify did not answer (ReadTimeout)")
            if self.create == "refused":
                return {"data": {"productCreate": {"product": None,
                                                   "userErrors": [{"message": "bad"}]}}}
            ident = [m["value"] for m in variables["input"].get("metafields") or []]
            self.existing.append((GID, ident[0] if ident else None))
            return {"data": {"productCreate": {"product": {
                "id": GID, "handle": "h", "tags": [], "variants": {"nodes": [VARIANT]},
                "media": {"nodes": []}}, "userErrors": []}}}
        if "imsProductUpdate" in query:
            return {"data": {"productUpdate": {"product": {
                "id": GID, "handle": "h", "tags": [], "variants": {"nodes": [VARIANT]},
                "media": {"nodes": []}}, "userErrors": []}}}
        if "imsPublishablePublish" in query:
            return {"data": {"publishablePublish": {"userErrors": []}}}
        if "imsVariantPricesUpdate" in query:
            return {"data": {"productVariantsBulkUpdate": {"productVariants": [VARIANT],
                                                           "userErrors": []}}}
        if "metafieldsSet" in query:
            return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
        return {"data": {}}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "resolve_shopify_credentials",
                        lambda db, storefront_id="BV": {"shop_url": "t", "access_token": "t",
                                                        "source": "vault"})
    monkeypatch.setattr(catalog_dictionary, "brand_website_refusal", lambda db, brand: None)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    fake = _Shopify()
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    db = mongomock.MongoClient()["synthetic"]
    db.catalog_products.insert_one({
        "id": "P1", "sku": "BV-1", "title": "OLD", "brand": "Ray-Ban", "mrp": 5000,
        "offer_price": 4000, "images": ["https://cdn.example.com/a.jpg"],
        "ecom": {"status": "DRAFT", "locally_modified": True, "edit_rev": "r1"},
    })
    return db, fake


def _doc(db):
    return db.catalog_products.find_one({"id": "P1"}, {"_id": 0})


def _press(db):
    return _run(shopify_push.push_product(db, _doc(db), [], blocked=False))


def _age_claim(db, minutes):
    def _older(ecom):
        ecom["create_claim"]["at"] = ecom["create_claim"]["at"] - timedelta(minutes=minutes)
        return ecom
    update_ecom(db, "P1", _older)


def test_the_create_carries_the_identity_and_drops_its_claim_on_success(world):
    db, fake = world
    res = _press(db)
    sent = fake.ops("imsProductCreate(")[0]["variables"]["input"]["metafields"]
    assert sent == [create_claim.identity_metafield("P1")]
    ecom = _doc(db)["ecom"]
    assert ecom["shopify_product_id"] == GID and "create_claim" not in ecom
    assert res.ok is True


def test_a_lost_create_answer_is_adopted_on_the_next_press_never_recreated(world):
    db, fake = world
    fake.create = "lost"
    first = _press(db)
    assert first.ok is False and first.code == shopify_push.CREATE_UNCERTAIN
    ecom = _doc(db)["ecom"]
    assert "shopify_product_id" not in ecom
    assert ecom["create_claim"]["state"] == "UNCERTAIN" and ecom["locally_modified"] is True

    fake.create = "ok"  # would make a SECOND product if sent
    second = _press(db)
    assert len(fake.ops("imsProductCreate(")) == 1, "a lost create was sent twice"
    assert fake.ops("imsFindCreated"), "IMS created again without looking first"
    assert _doc(db)["ecom"]["shopify_product_id"] == GID
    assert "create_claim" not in _doc(db)["ecom"]
    assert second.action == "update" and second.ok is True


def test_an_uncertain_create_not_yet_found_is_not_resent_until_settled(world):
    db, fake = world
    fake.create = "lost"
    _press(db)
    fake.existing.clear()  # Shopify's search does not show it (yet)
    fake.create = "ok"
    again = _press(db)
    assert again.code == shopify_push.CREATE_UNCERTAIN
    assert len(fake.ops("imsProductCreate(")) == 1

    _age_claim(db, 20)  # past SETTLE and still not on Shopify: safe to create
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 2
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_a_concurrent_second_press_never_creates_a_second_product(world):
    db, fake = world

    async def scenario():
        fake.hold = asyncio.Event()
        first = asyncio.create_task(shopify_push.push_product(db, _doc(db), [], blocked=False))
        while not fake.ops("imsProductCreate("):
            await asyncio.sleep(0)
        try:
            # A second press that sent its own create would block on `hold`.
            second = await asyncio.wait_for(
                shopify_push.push_product(db, _doc(db), [], blocked=False), timeout=5
            )
        except asyncio.TimeoutError:
            second = None
        fake.hold.set()
        return await first, second

    first, second = _run(scenario())
    assert second is not None, "the second press sent a create of its own"
    assert len(fake.ops("imsProductCreate(")) == 1
    assert second.ok is False and second.code == shopify_push.CREATE_IN_PROGRESS
    assert first.ok is True and _doc(db)["ecom"]["shopify_product_id"] == GID


def test_a_failed_id_save_is_a_visible_state_and_is_adopted_not_recreated(world, monkeypatch):
    db, fake = world
    from api.services.shopify_push import product as product_mod

    monkeypatch.setattr(product_mod, "_writeback_product", lambda *a, **k: False)
    first = _press(db)
    assert first.ok is False and first.code == shopify_push.CREATE_ID_NOT_SAVED
    assert first.shopify_id == GID and "Shopify" in first.error
    assert _doc(db)["ecom"]["create_claim"]["state"] == "SENDING"
    monkeypatch.undo()
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "resolve_shopify_credentials",
                        lambda db, storefront_id="BV": {"shop_url": "t", "access_token": "t"})
    monkeypatch.setattr(catalog_dictionary, "brand_website_refusal", lambda db, brand: None)

    in_flight = _press(db)  # the claim is fresh: nobody creates
    assert in_flight.code == shopify_push.CREATE_IN_PROGRESS
    _age_claim(db, 20)
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 1
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_a_refused_create_releases_its_claim(world):
    db, fake = world
    fake.create = "refused"
    res = _press(db)
    assert res.ok is False and "create_claim" not in _doc(db)["ecom"]
    fake.create = "ok"
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 2
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_an_adoption_never_takes_a_stranger(world):
    """A product created meanwhile that carries ANOTHER IMS id is not ours."""
    db, fake = world
    fake.create = "lost"
    _press(db)
    fake.existing[:] = [("gid://shopify/Product/OTHER", "P-SOMEONE-ELSE")]
    res = _press(db)
    assert res.code == shopify_push.CREATE_UNCERTAIN
    assert "shopify_product_id" not in _doc(db)["ecom"]


# ===========================================================================
# 3. SF-04 -- an edit made after the payload snapshot stays queued
# ===========================================================================


def _seed_live(db):
    db.catalog_products.update_one({"id": "P1"}, {"$set": {
        "ecom.shopify_product_id": GID, "ecom.shopify_variant_id": VARIANT["id"],
        "ecom.status": "PUBLISHED"}})


def _edit_during_push(fake, db, change):
    """Staff save `change` (the real queueing rule: flag + fresh revision)
    while the push is talking to Shopify -- after its payload snapshot."""
    inner = fake.__call__

    async def _with_edit(dbx, query, variables):
        if "imsProductUpdate" in query and not getattr(fake, "edited", False):
            fake.edited = True
            db.catalog_products.update_one({"id": "P1"}, {"$set": change})
            update_ecom(db, "P1", lambda ecom: {**ecom, **dirty_marks()})
        return await inner(dbx, query, variables)
    return _with_edit


@pytest.mark.parametrize("change", [
    {"title": "NEW UNSENT"},
    {"mrp": 6000, "offer_price": 5500},
    {"images": ["https://cdn.example.com/b.jpg"]},
], ids=["title", "price", "photo"])
def test_audit_probe_an_edit_after_the_snapshot_stays_queued(world, monkeypatch, change):
    db, fake = world
    _seed_live(db)
    monkeypatch.setattr(shopify_push, "_graphql", _edit_during_push(fake, db, change))
    snapshot = _doc(db)
    _run(shopify_push.push_product(db, snapshot, [], blocked=False))
    saved = _doc(db)
    for k, v in change.items():
        assert saved[k] == v
    assert saved["ecom"]["locally_modified"] is True, "the newer edit left the queue unsent"
    # ...and it IS what the next run sends.
    fake.calls.clear()
    _run(shopify_push.push_product(db, _doc(db), [], blocked=False))
    sent = fake.ops("imsProductUpdate")[0]["variables"]["input"]
    if "title" in change:
        assert sent["title"] == "NEW UNSENT"
    if "mrp" in change:
        prices = fake.ops("imsVariantPricesUpdate")[0]["variables"]["variants"]
        assert prices[0]["price"] == "5500.00"
    if "images" in change:
        media = fake.ops("imsProductCreateMedia")[0]["variables"]["media"]
        assert media[0]["originalSource"].endswith("b.jpg")
    assert _doc(db)["ecom"]["locally_modified"] is False


def test_control_no_edit_meanwhile_drains_the_queue(world):
    db, fake = world
    _seed_live(db)
    _press(db)
    assert _doc(db)["ecom"]["locally_modified"] is False


def test_a_sibling_ecom_field_written_meanwhile_is_not_lost(world, monkeypatch):
    db, fake = world
    _seed_live(db)
    inner = fake.__call__

    async def _sibling(dbx, query, variables):
        if "imsProductUpdate" in query:
            update_ecom(db, "P1", lambda ecom: {**ecom, "online_offer_price": 3999})
        return await inner(dbx, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", _sibling)
    _press(db)
    ecom = _doc(db)["ecom"]
    assert ecom["online_offer_price"] == 3999 and ecom["shopify_product_id"] == GID


def test_compare_and_set_retries_on_a_fresh_read():
    db = mongomock.MongoClient()["synthetic"]
    db.catalog_products.insert_one({"id": "P", "ecom": {"a": 1}})
    raced = []

    def _mutate(ecom):
        if not raced:  # another writer lands between this read and the write
            raced.append(1)
            db.catalog_products.update_one({"id": "P"}, {"$set": {"ecom": {"a": 1, "b": 2}}})
        return {**ecom, "c": 3}

    assert update_ecom(db, "P", _mutate) == {"a": 1, "b": 2, "c": 3}
    assert db.catalog_products.find_one({"id": "P"})["ecom"] == {"a": 1, "b": 2, "c": 3}


def test_every_queueing_write_gets_a_fresh_revision():
    from api.routers.catalog import _mark_online_dirty

    a, b = {"ecom": {"edit_rev": "old"}}, {}
    _mark_online_dirty(a)
    _mark_online_dirty(b)
    assert a["ecom"]["locally_modified"] and a["ecom"]["edit_rev"] not in ("old", None)
    assert b["ecom"]["edit_rev"] and b["ecom"]["edit_rev"] != a["ecom"]["edit_rev"]
