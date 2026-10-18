"""SF-02 + SF-04 (audit 18 Oct 2026, owner-approved Batch 1; review round 2).

SF-02: a create whose answer was lost is never repeated blindly. The transport
never re-sends a create mutation after a send with an unknown outcome; a first
publish holds a durable claim, carries an identity metafield whose definition
has unique values, and a later press finds and ADOPTS the listing on Shopify
(direct read; or the claim-window search, every page) before it would ever
create again. A concurrent second press never creates a second product, and
a catalogue save never wipes the Shopify id or the claim.

SF-04: finishing an old push never takes a newer catalogue edit off the queue
(products, collections, menus), and never loses a sibling ecom field.

Synthetic only: mongomock DB, a fake Shopify. No network, no production.
"""

import asyncio
import copy
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
UTC = timezone.utc


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# 1. TRANSPORT -- a create is never auto-repeated after an unknown outcome
# ===========================================================================


def _transport(monkeypatch, answers, urls=None):
    calls = []

    async def _post(url, headers, payload):
        calls.append(copy.deepcopy(payload))
        if urls is not None:
            urls.append(url)
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
    create_claim._IDENTITY_DEFINITION_CREATE,
], ids=["product", "collection", "menu", "media", "variants", "fulfillment", "definition"])
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


@pytest.mark.skipif(bool(os.getenv("SHOPIFY_API_VERSION")), reason="version overridden")
def test_the_api_version_is_pinned_to_2026_01(monkeypatch):
    """SF-06 start: the code says what Shopify actually answers in."""
    urls = []
    _transport(monkeypatch, [_OK], urls)
    _run(transport._graphql(None, shopify_push._PRODUCT_UPDATE, {}))
    assert "/admin/api/2026-01/graphql.json" in urls[0]


# ===========================================================================
# 2. A fake Shopify: products with created_at, the identity read, the paged
#    window search, the identity definition
# ===========================================================================


def _iso(dt):
    return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


class _Shopify:
    def __init__(self):
        self.calls = []
        self.products = []  # {"id", "ident", "at"} -- what Shopify holds
        self.create = "ok"
        self.hold = None  # an asyncio.Event a create waits on
        self.on_create = None  # a hook run inside productCreate
        self.definition = None  # None | {"unique": bool, "count": int}
        self.titles = {}  # gid -> (title, ims value) for the live-values read

    def ops(self, marker):
        return [c for c in self.calls if marker in c["query"]]

    def add(self, gid, ident, at=None):
        self.products.append({"id": gid, "ident": ident, "at": at or datetime.now(UTC)})

    def _made(self, variables):
        ident = [m["value"] for m in variables["input"].get("metafields") or []]
        self.add(GID, ident[0] if ident else None)

    def _search(self, query, variables):
        q = variables["q"]
        lo = _iso(re.search(r"created_at:>='([^']+)'", q).group(1))
        hi = _iso(re.search(r"created_at:<='([^']+)'", q).group(1))
        first = int(re.search(r"first:\s*(\d+)", query).group(1))
        rows = sorted((p for p in self.products if lo <= p["at"].replace(microsecond=0) <= hi),
                      key=lambda p: p["at"])
        start = int(variables.get("after") or 0)
        page = rows[start:start + first]
        more = start + first < len(rows)
        return {"data": {"products": {
            "nodes": [{"id": p["id"], "identity": {"value": p["ident"]} if p["ident"] else None}
                      for p in page],
            "pageInfo": {"hasNextPage": more, "endCursor": str(start + first) if more else None},
        }}}

    async def __call__(self, db, query, variables):
        self.calls.append({"query": query, "variables": copy.deepcopy(variables)})
        t = tracking_answer(query, variables)
        if t:
            return t
        if "imsProductByIdentity" in query:
            hit = next((p for p in self.products if p["ident"] == variables["value"]), None)
            return {"data": {"productByIdentifier": {"id": hit["id"]} if hit else None}}
        if "imsFindCreated" in query:
            return self._search(query, variables)
        if "imsIdentityDefinitionCreate" in query:
            self.definition = {"unique": True, "count": 0}
            return {"data": {"metafieldDefinitionCreate": {
                "createdDefinition": {"id": "gid://shopify/MetafieldDefinition/1",
                                      "capabilities": {"uniqueValues": {"enabled": True}}},
                "userErrors": []}}}
        if "imsIdentityDefinitionUpdate" in query:
            self.definition["unique"] = True
            return {"data": {"metafieldDefinitionUpdate": {
                "updatedDefinition": {"id": "gid://shopify/MetafieldDefinition/1",
                                      "capabilities": {"uniqueValues": {"enabled": True}}},
                "userErrors": []}}}
        if "imsIdentityDefinition" in query:
            d = self.definition
            nodes = [] if d is None else [{"id": "gid://shopify/MetafieldDefinition/1",
                                           "metafieldsCount": d["count"],
                                           "capabilities": {"uniqueValues": {"enabled": d["unique"]}}}]
            return {"data": {"metafieldDefinitions": {"nodes": nodes}}}
        if "imsIdentityValues" in query:
            return {"data": {"nodes": [
                {"id": g, "title": self.titles[g][0],
                 "identity": {"value": self.titles[g][1]} if self.titles[g][1] else None}
                for g in variables["ids"] if g in self.titles]}}
        if "imsProductCreateMedia" in query:
            return {"data": {"productCreateMedia": {
                "media": [{"id": "gid://shopify/MediaImage/1"}], "mediaUserErrors": []}}}
        if "imsProductCreate" in query:
            if self.hold is not None:
                await self.hold.wait()
            if self.on_create:
                self.on_create()
            if self.create == "lost":
                self._made(variables)  # Shopify made it; the answer never came back
                raise shopify_push.ShopifyOutcomeUnknown("Shopify did not answer (ReadTimeout)")
            if self.create == "errors":
                self._made(variables)  # ...a 200 with top-level errors, no product
                return {"errors": [{"message": "Internal error"}], "data": {"productCreate": None}}
            if self.create == "refused":
                return {"data": {"productCreate": {"product": None,
                                                   "userErrors": [{"message": "bad"}]}}}
            self._made(variables)
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
        if "imsCollectionUpdate" in query or "imsCollectionCreate" in query:
            field = "collectionUpdate" if "Update" in query else "collectionCreate"
            return {"data": {field: {"collection": {"id": "gid://shopify/Collection/7", "handle": "c"},
                                     "userErrors": []}}}
        if "imsMenuUpdate" in query or "imsMenuCreate" in query:
            field = "menuUpdate" if "Update" in query else "menuCreate"
            return {"data": {field: {"menu": {"id": "gid://shopify/Menu/7", "handle": "m"},
                                     "userErrors": []}}}
        return {"data": {}}


def _gates(monkeypatch, fake):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(shopify_push, "resolve_shopify_credentials",
                        lambda db, storefront_id="BV": {"shop_url": "t", "access_token": "t",
                                                        "source": "vault"})
    monkeypatch.setattr(catalog_dictionary, "brand_website_refusal", lambda db, brand: None)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
    monkeypatch.setattr(shopify_push, "_graphql", fake)


@pytest.fixture
def world(monkeypatch):
    fake = _Shopify()
    _gates(monkeypatch, fake)
    db = mongomock.MongoClient()["synthetic"]
    db.catalog_products.insert_one({
        "id": "P1", "sku": "BV-1", "title": "OLD", "brand": "Ray-Ban", "mrp": 5000,
        "offer_price": 4000, "images": ["https://cdn.example.com/a.jpg"],
        "inventory": {"locations": {"S1": 1}, "total_quantity": 1},
        "updated_at": "2026-10-18T00:00:00",
        "ecom": {"status": "DRAFT", "locally_modified": True, "edit_rev": "r1"},
    })
    return db, fake


@pytest.fixture
def search_only(monkeypatch):
    """The identity definition is NOT in place (and its ensure is not due):
    only the window search can answer."""
    monkeypatch.setitem(create_claim._identity, "ready", False)
    monkeypatch.setitem(create_claim._identity, "retry_at", datetime.now(UTC) + timedelta(days=1))


def _doc(db):
    return db.catalog_products.find_one({"id": "P1"}, {"_id": 0})


def _press(db):
    return _run(shopify_push.push_product(db, _doc(db), [], blocked=False))


def _age_world(db, fake, minutes):
    """Move the claim AND everything Shopify made back `minutes` in time."""
    def _older(ecom):
        ecom["create_claim"]["at"] = ecom["create_claim"]["at"] - timedelta(minutes=minutes)
        return ecom
    update_ecom(db, "P1", _older)
    for p in fake.products:
        p["at"] -= timedelta(minutes=minutes)


def _others(fake, n, start=1):
    """n OTHER products created after ours (a big publish running on)."""
    base = datetime.now(UTC)
    for i in range(n):
        fake.add(f"gid://shopify/Product/O{i}", f"OTHER-{i}", base + timedelta(seconds=start + i))


# ===========================================================================
# 3. FIRST PUBLISH -- claim, identity, resolve before any repeat
# ===========================================================================


def test_the_create_carries_the_identity_and_drops_its_claim_on_success(world):
    db, fake = world
    res = _press(db)
    sent = fake.ops("imsProductCreate(")[0]["variables"]["input"]["metafields"]
    assert sent == [create_claim.identity_metafield("P1")]
    ecom = _doc(db)["ecom"]
    assert ecom["shopify_product_id"] == GID and "create_claim" not in ecom
    assert res.ok is True


def test_a_lost_create_answer_is_adopted_by_the_direct_read(world):
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
    assert fake.ops("imsProductByIdentity"), "IMS created again without looking first"
    assert not fake.ops("imsFindCreated"), "the direct read answered; no search needed"
    assert _doc(db)["ecom"]["shopify_product_id"] == GID
    assert "create_claim" not in _doc(db)["ecom"]
    assert second.action == "update" and second.ok is True


@pytest.mark.parametrize("later", [60, 300])
def test_the_search_finds_it_after_hundreds_of_later_products(world, search_only, later):
    """REVIEW BLOCK 1. A 450-product publish keeps creating after our lost
    create; the old search read only the NEWEST 50 and missed ours, so a
    later press created it again. The window search reads OLDEST first,
    every page."""
    db, fake = world
    fake.create = "lost"
    _press(db)
    _others(fake, later)
    fake.create = "ok"
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 1, "missed among later products -> duplicate"
    assert _doc(db)["ecom"]["shopify_product_id"] == GID
    pages = fake.ops("imsFindCreated")
    assert pages and "first: 250" in pages[0]["query"]


def test_the_search_follows_every_page(world, search_only):
    """Ours is on page 3: 600 products were created in its window BEFORE it."""
    db, fake = world
    base = datetime.now(UTC) - timedelta(minutes=4)
    for i in range(600):
        fake.add(f"gid://shopify/Product/E{i}", f"EARLIER-{i}", base + timedelta(milliseconds=i))
    fake.create = "lost"
    _press(db)
    fake.create = "ok"
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 1
    assert [c["variables"]["after"] for c in fake.ops("imsFindCreated")] == [None, "250", "500"]
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_not_found_by_the_direct_read_creates_once_past_in_flight(world):
    db, fake = world
    fake.create = "lost"
    _press(db)
    fake.products.clear()  # it really was not made
    fake.create = "ok"
    assert _press(db).code == shopify_push.CREATE_UNCERTAIN  # under 5 minutes: wait
    assert len(fake.ops("imsProductCreate(")) == 1
    _age_world(db, fake, 6)  # past IN_FLIGHT; the direct read is definitive
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 2
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_not_found_by_the_search_waits_for_settle(world, search_only):
    db, fake = world
    fake.create = "lost"
    _press(db)
    fake.products.clear()  # the search does not show it (lag, or never made)
    fake.create = "ok"
    _age_world(db, fake, 6)
    assert _press(db).code == shopify_push.CREATE_UNCERTAIN  # a search "none" may be lag
    assert len(fake.ops("imsProductCreate(")) == 1
    _age_world(db, fake, 14)  # 20 minutes: past SETTLE
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 2


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


def test_a_failed_id_save_is_adopted_once_the_claim_is_past_in_flight(world, monkeypatch):
    """A SENDING claim older than IN_FLIGHT is LOOKED UP -- never refused
    blind for 15 minutes."""
    db, fake = world
    from api.services.shopify_push import product as product_mod

    real = product_mod._writeback_product
    monkeypatch.setattr(product_mod, "_writeback_product", lambda *a, **k: False)
    first = _press(db)
    monkeypatch.setattr(product_mod, "_writeback_product", real)
    assert first.ok is False and first.code == shopify_push.CREATE_ID_NOT_SAVED
    assert first.shopify_id == GID and "Shopify" in first.error
    assert _doc(db)["ecom"]["create_claim"]["state"] == "SENDING"

    assert _press(db).code == shopify_push.CREATE_IN_PROGRESS  # fresh: another press
    _age_world(db, fake, 6)
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 1
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_a_200_with_top_level_errors_and_no_product_is_uncertain(world):
    """REVIEW 3: Shopify may have run it -- keep the claim, look first."""
    db, fake = world
    fake.create = "errors"
    res = _press(db)
    assert res.ok is False and res.code == shopify_push.CREATE_UNCERTAIN
    assert _doc(db)["ecom"]["create_claim"]["state"] == "UNCERTAIN"
    fake.create = "ok"
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


def test_a_create_refused_because_the_id_is_taken_links_the_existing_listing(world):
    """The unique definition makes Shopify refuse a second product with the
    same IMS id. That refusal must not repeat on every press: IMS reads the
    listing that holds the id and carries on as an update."""
    db, fake = world
    fake.add(GID, "P1")  # already on Shopify; IMS lost the link and the claim
    fake.create = "refused"  # "ims_product_id: value is already taken"
    res = _press(db)
    assert _doc(db)["ecom"]["shopify_product_id"] == GID
    assert res.action == "update" and len(fake.ops("imsProductCreate(")) == 1


@pytest.mark.parametrize("mode", ["direct", "search"])
def test_an_adoption_never_takes_a_stranger(world, monkeypatch, mode):
    """A product carrying ANOTHER IMS id is not ours."""
    db, fake = world
    if mode == "search":
        monkeypatch.setitem(create_claim._identity, "ready", False)
        monkeypatch.setitem(create_claim._identity, "retry_at", datetime.now(UTC) + timedelta(days=1))
    fake.create = "lost"
    _press(db)
    fake.products[:] = [{"id": "gid://shopify/Product/OTHER", "ident": "P-SOMEONE-ELSE",
                         "at": datetime.now(UTC)}]
    res = _press(db)
    assert res.code == shopify_push.CREATE_UNCERTAIN
    assert "shopify_product_id" not in _doc(db)["ecom"]


# ===========================================================================
# 4. The identity definition -- ensured before the first create
# ===========================================================================


@pytest.fixture
def not_ensured(monkeypatch):
    monkeypatch.setitem(create_claim._identity, "ready", False)
    monkeypatch.setitem(create_claim._identity, "retry_at", None)


def _live_products(db, fake, values):
    for i, value in enumerate(values):
        gid = f"gid://shopify/Product/L{i}"
        db.catalog_products.insert_one({"id": f"L{i}", "ecom": {"shopify_product_id": gid}})
        fake.titles[gid] = (f"Live {i}", value)


def test_ensure_creates_the_unique_definition_before_the_first_create(world, not_ensured):
    db, fake = world
    _live_products(db, fake, [None] * 25)  # today's 25 live products carry no value
    _press(db)
    order = [c["query"] for c in fake.calls]
    first_create = next(i for i, q in enumerate(order) if "imsProductCreate(" in q)
    made = next(i for i, q in enumerate(order) if "imsIdentityDefinitionCreate" in q)
    assert made < first_create, "a value went out before its unique definition"
    sent = fake.ops("imsIdentityDefinitionCreate")[0]["variables"]["definition"]
    assert sent["capabilities"] == {"uniqueValues": {"enabled": True}}
    assert sent["namespace"] == "ims" and sent["key"] == "ims_product_id"
    assert sent["ownerType"] == "PRODUCT"
    assert create_claim._identity["ready"] is True
    fake.calls.clear()
    db.catalog_products.update_one({"id": "P1"}, {"$unset": {"ecom.shopify_product_id": ""}})
    _press(db)
    assert not fake.ops("imsIdentityDefinition"), "confirmed once per process"


def test_ensure_dry_run_reports_and_writes_nothing(world, not_ensured):
    db, fake = world
    _live_products(db, fake, [None, "L1"])
    out = _run(create_claim.ensure_identity_definition(db, apply=False))
    assert out["ready"] is False and out["action"] == "would_create"
    assert "1 live products already carry" in out["message"]
    assert not [c for c in fake.calls if c["query"].lstrip().startswith("mutation")]


def test_ensure_does_nothing_when_the_definition_is_in_place(world, not_ensured):
    db, fake = world
    fake.definition = {"unique": True, "count": 25}
    out = _run(create_claim.ensure_identity_definition(db))
    assert out["action"] == "exists" and out["ready"] is True
    assert not [c for c in fake.calls if c["query"].lstrip().startswith("mutation")]


def test_ensure_switches_unique_values_on_for_an_existing_definition(world, not_ensured):
    db, fake = world
    fake.definition = {"unique": False, "count": 3}
    out = _run(create_claim.ensure_identity_definition(db))
    assert out["action"] == "enabled" and fake.definition["unique"] is True


def test_ensure_stops_on_duplicates_and_names_them(world, not_ensured):
    db, fake = world
    _live_products(db, fake, ["P9", "P9", "P8"])
    out = _run(create_claim.ensure_identity_definition(db))
    assert out["ready"] is False and out["action"] == "blocked_duplicates"
    assert "Live 0" in out["message"] and "Live 1" in out["message"]
    assert not fake.ops("imsIdentityDefinitionCreate")


def test_a_failed_ensure_is_not_retried_on_every_create(world, not_ensured, monkeypatch):
    db, fake = world
    real = fake.__call__

    async def _refuse(dbx, query, variables):
        if "imsIdentityDefinitionCreate" in query:
            fake.calls.append({"query": query, "variables": variables})
            return {"data": {"metafieldDefinitionCreate": {
                "createdDefinition": None, "userErrors": [{"message": "Access denied"}]}}}
        return await real(dbx, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", _refuse)
    res = _press(db)
    assert res.ok is True, "a first publish still goes out (claim + search guard it)"
    assert len(fake.ops("imsIdentityDefinitionCreate")) == 1
    db.catalog_products.update_one({"id": "P1"}, {"$unset": {"ecom.shopify_product_id": ""}})
    _press(db)
    assert len(fake.ops("imsIdentityDefinitionCreate")) == 1, "retried within 10 minutes"


# ===========================================================================
# 5. REVIEW BLOCK 2 -- a catalogue save never wipes the Shopify id or claim
# ===========================================================================


@pytest.fixture
def catalog_door(world, monkeypatch):
    from api.routers import catalog

    db, fake = world
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db.catalog_products)
    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    return catalog, db, fake


def test_a_page_read_before_the_press_saved_after_keeps_the_shopify_id(catalog_door):
    catalog, db, fake = catalog_door
    page = _doc(db)  # the editor opened the product: no Shopify id yet
    _press(db)
    assert _doc(db)["ecom"]["shopify_product_id"] == GID
    page["title"] = "EDITED"
    catalog._save_catalog_product(page)
    saved = _doc(db)
    assert saved["title"] == "EDITED"
    assert saved["ecom"]["shopify_product_id"] == GID, "the save wiped the link -> duplicate"
    assert saved["ecom"]["locally_modified"] is True
    _press(db)
    assert len(fake.ops("imsProductCreate(")) == 1


def test_the_concurrency_safe_door_keeps_the_shopify_id_too(catalog_door):
    catalog, db, fake = catalog_door
    page = _doc(db)
    _press(db)
    page["title"] = "EDITED"
    assert catalog._save_catalog_product_cas(page, "2026-10-18T00:00:00") is True
    assert _doc(db)["ecom"]["shopify_product_id"] == GID


def test_a_stock_adjust_keeps_the_shopify_id_and_writes_no_ecom(catalog_door, monkeypatch):
    catalog, db, fake = catalog_door
    page = _doc(db)  # read before the press
    _press(db)
    before = copy.deepcopy(_doc(db)["ecom"])
    monkeypatch.setattr(catalog, "_get_catalog_product", lambda pid: copy.deepcopy(page))
    _run(catalog.adjust_product_inventory("P1", "S1", 2, current_user={"roles": ["ADMIN"]}))
    saved = _doc(db)
    assert saved["inventory"]["locations"]["S1"] == 3
    assert saved["ecom"] == before, "a stock movement rewrote ecom"


def test_a_page_read_during_the_create_never_resurrects_the_claim(catalog_door):
    catalog, db, fake = catalog_door
    seen = {}
    fake.on_create = lambda: seen.setdefault("page", _doc(db))  # claim SENDING, no id
    _press(db)
    assert seen["page"]["ecom"]["create_claim"]["state"] == "SENDING"
    seen["page"]["title"] = "EDITED"
    catalog._save_catalog_product(seen["page"])
    ecom = _doc(db)["ecom"]
    assert ecom["shopify_product_id"] == GID and "create_claim" not in ecom


# ===========================================================================
# 6. SF-04 -- an edit made after the payload snapshot stays queued
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
    _run(shopify_push.push_product(db, _doc(db), [], blocked=False))
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


def test_no_bare_whole_ecom_write_is_left():
    """Every catalog_products `ecom` read-merge-write goes through
    catalog_ecom.update_ecom (compare-and-set). A bare one reappearing is the
    lost-sibling race again."""
    api = Path(__file__).resolve().parent.parent / "api"
    bare = re.compile(r"""\{\s*["']\$set["']\s*:\s*\{\s*["']ecom["']\s*:""")
    hits = [
        f"{p.relative_to(api)}:{n}"
        for p in api.rglob("*.py")
        if p.name != "catalog_ecom.py"
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if bare.search(line)
    ]
    assert hits == []


# ---- the queueing writers each stamp a FRESH revision (pinned)


def test_every_queueing_write_gets_a_fresh_revision():
    from api.routers.catalog import _mark_online_dirty

    a, b = {"ecom": {"edit_rev": "old"}}, {}
    _mark_online_dirty(a)
    _mark_online_dirty(b)
    assert a["ecom"]["locally_modified"] and a["ecom"]["edit_rev"] not in ("old", None)
    assert b["ecom"]["edit_rev"] and b["ecom"]["edit_rev"] != a["ecom"]["edit_rev"]


def test_the_size_row_mirror_stamps_the_parent(monkeypatch):
    import test_variant_of_rule as vr
    from api.services import online_discount_engine
    from api.services import product_master as pm

    # Isolate the mirror's own stamp from the price engine's.
    monkeypatch.setattr(online_discount_engine, "recompute_online_price", lambda *a, **k: None)
    db = vr._world()
    before = vr._twin(db, "tw-parent")["ecom"].get("edit_rev")
    pm.mirror_update_to_catalog_twin(
        product_id="sp-child", current=vr._spine(db, "sp-child"), patch={"mrp": 47000.0}, db=db
    )
    after = vr._twin(db, "tw-parent")["ecom"]
    assert after["locally_modified"] is True
    assert after.get("edit_rev") not in (None, before)


def test_a_republish_stamps_a_fresh_revision():
    from api.services import online_delist

    db = mongomock.MongoClient()["synthetic"]
    db.catalog_products.insert_one({"id": "P1", "sku": "BV-1", "ecom": {
        "shopify_product_id": GID, "locally_modified": False, "edit_rev": "sent"}})
    assert online_delist.mark_for_republish(db, {"id": "P1", "sku": "BV-1"}) is True
    ecom = db.catalog_products.find_one({"id": "P1"})["ecom"]
    assert ecom["locally_modified"] is True and ecom["edit_rev"] not in (None, "sent")


# ---- collections and menus: the same revision-guarded clear


def _repo_push(monkeypatch, kind, edit_during):
    from database.repositories.ecom_collection_repository import EcomCollectionRepository
    from database.repositories.ecom_menu_repository import EcomMenuRepository

    fake = _Shopify()
    _gates(monkeypatch, fake)
    db = mongomock.MongoClient()["synthetic"]
    if kind == "collection":
        repo = EcomCollectionRepository(db.ecom_collections)
        doc = repo.create({"title": "Old", "handle": "c", "collection_type": "CUSTOM"})
        key, oid, push, coll = "collection_id", doc["collection_id"], shopify_push.push_collection, db.ecom_collections
        marker = "imsCollectionCreate"
    else:
        repo = EcomMenuRepository(db.ecom_menus)
        doc = repo.create({"title": "Old", "handle": "m"})
        key, oid, push, coll = "menu_id", doc["menu_id"], shopify_push.push_menu, db.ecom_menus
        marker = "imsMenuCreate"
    inner = fake.__call__

    async def _maybe_edit(dbx, query, variables):
        if edit_during and marker in query:
            repo.update(oid, {"title": "NEW UNSENT"})
        return await inner(dbx, query, variables)

    monkeypatch.setattr(shopify_push, "_graphql", _maybe_edit)
    snapshot = coll.find_one({key: oid}, {"_id": 0})
    res = _run(push(db, snapshot))
    assert res.ok, res.error
    return coll.find_one({key: oid})


@pytest.mark.parametrize("kind", ["collection", "menu"])
def test_a_collection_or_menu_edit_during_its_push_stays_queued(monkeypatch, kind):
    saved = _repo_push(monkeypatch, kind, edit_during=True)
    assert saved["title"] == "NEW UNSENT" and saved["locally_modified"] is True
    assert saved.get("shopify_collection_id") or saved.get("shopify_menu_id")


@pytest.mark.parametrize("kind", ["collection", "menu"])
def test_control_a_collection_or_menu_push_drains_its_flag(monkeypatch, kind):
    saved = _repo_push(monkeypatch, kind, edit_during=False)
    assert saved["locally_modified"] is False
