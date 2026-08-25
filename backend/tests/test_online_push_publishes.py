"""
Pressing publish must actually put the product in front of customers.
====================================================================

Owner ruling 2026-08-25: "one press, goes live". The queue fix (the sibling
test_online_push_dirty_flag.py) only got the product INTO the queue. Three more
doors were shut behind it, and a product has to pass all of them to be visible
on bettervision.in:

  1. Shopify product status -- every IMS product is born ecom.status=DRAFT and
     NOTHING in IMS ever advanced it, so build_product_input always sent
     ProductInput.status=DRAFT. A DRAFT is invisible no matter what else is set.
  2. The Online Store PUBLICATION -- an ACTIVE product published to no sales
     channel is still invisible. publishablePublish was gated behind
     SHOPIFY_PUBLISH_ON_CREATE (default OFF) and only ever ran on a CREATE.
  3. The PHOTOGRAPH -- product media pushed in a SEPARATE, LATER press, so a
     product could go live as a name, a price and an empty grey box.

These tests pin the PAYLOAD Shopify actually receives (the captured GraphQL
variables), never an internal IMS flag: an internal flag proves nothing about
what the storefront was told.

***** SAFETY-CRITICAL: every Shopify call is MOCKED (shopify_push._graphql is
monkeypatched). No real Shopify request is ever made. *****

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_online_push_publishes.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import copy  # noqa: E402

import pytest  # noqa: E402

from api.routers import online_store_push as push_router  # noqa: E402
from api.services import shopify_push  # noqa: E402

# Reuse the sibling suite's harness rather than keep a second copy of it: the
# same in-memory _DB whose _Coll nests dot-notation $set the way real Mongo does
# (the house strict double flattens it -- a weaker double than production).
from test_online_push_dirty_flag import _DB, _run  # noqa: E402


_PUB_GID = "gid://shopify/Publication/1"


# ---------------------------------------------------------------------------
# A fake Shopify that ROUTES on the mutation, so every step of one press is
# both answerable and inspectable. `calls` is the transcript we assert on.
# ---------------------------------------------------------------------------


class _Shopify:
    def __init__(self, media_on_existing=0, product_id="gid://shopify/Product/900"):
        self.calls = []
        self.media_on_existing = media_on_existing
        self.product_id = product_id

    @staticmethod
    def _op(query):
        for name in (
            "imsProductCreateMedia",
            "imsProductCreate",
            "imsProductUpdate",
            "imsPublishablePublish",
            "imsVariantPricesUpdate",
            "imsVariantsBulkCreate",
            "imsPublications",
            "metafieldsSet",
        ):
            if name in query:
                return name
        return "unknown"

    def calls_of(self, op):
        return [c for c in self.calls if c["op"] == op]

    def ops(self):
        return [c["op"] for c in self.calls]

    async def __call__(self, db, query, variables):
        op = self._op(query)
        self.calls.append({"op": op, "variables": copy.deepcopy(variables)})
        variant_node = {
            "id": "gid://shopify/ProductVariant/901",
            "title": "Default Title",
            "selectedOptions": [],
            "inventoryItem": {"id": "gid://shopify/InventoryItem/902"},
        }
        media_nodes = [
            {"id": "gid://shopify/MediaImage/%d" % i}
            for i in range(self.media_on_existing)
        ]
        if op == "imsProductCreate":
            return {
                "data": {
                    "productCreate": {
                        "product": {
                            "id": self.product_id,
                            "handle": "h",
                            "variants": {"nodes": [variant_node]},
                            "media": {"nodes": []},
                        },
                        "userErrors": [],
                    }
                }
            }
        if op == "imsProductUpdate":
            return {
                "data": {
                    "productUpdate": {
                        "product": {
                            "id": self.product_id,
                            "handle": "h",
                            "variants": {"nodes": [variant_node]},
                            "media": {"nodes": media_nodes},
                        },
                        "userErrors": [],
                    }
                }
            }
        if op == "imsProductCreateMedia":
            return {
                "data": {
                    "productCreateMedia": {
                        "media": [
                            {"id": "gid://shopify/MediaImage/77"}
                            for _ in (variables.get("media") or [])
                        ],
                        "mediaUserErrors": [],
                    }
                }
            }
        if op == "imsPublishablePublish":
            return {"data": {"publishablePublish": {"userErrors": []}}}
        if op == "imsVariantPricesUpdate":
            return {
                "data": {
                    "productVariantsBulkUpdate": {
                        "productVariants": [{"id": variant_node["id"]}],
                        "userErrors": [],
                    }
                }
            }
        if op == "imsVariantsBulkCreate":
            return {
                "data": {
                    "productVariantsBulkCreate": {
                        "productVariants": [],
                        "userErrors": [],
                    }
                }
            }
        if op == "metafieldsSet":
            return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
        return {"data": {}}


@pytest.fixture
def db():
    return _DB()


@pytest.fixture
def shopify(monkeypatch):
    """Open the three push gates, pin the publication id, replace the network."""
    fake = _Shopify()
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {
            "shop_url": "t.myshopify.com",
            "access_token": "shpat_t",
            "source": "vault",
        },
    )
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", _PUB_GID)
    return fake


def _product(pid="P1", photos=True, shopify_id=None, status="DRAFT", **extra):
    """A freshly catalogued product exactly as the create door leaves it:
    ecom.status DRAFT, no Shopify id, a photo on the product doc (where the
    Add-Product screen actually puts it) and a real price."""
    doc = {
        "id": pid,
        "sku": "SKU-%s" % pid,
        "title": "Ray-Ban RB2140",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "mrp": 5000.0,
        "offer_price": 4000.0,
        "ecom": {"status": status, "locally_modified": True},
    }
    if photos:
        doc["images"] = ["https://cdn.example.com/rb2140-front.jpg"]
    if shopify_id:
        doc["ecom"]["shopify_product_id"] = shopify_id
    doc.update(extra)
    return doc


def _seed(db, doc):
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    return copy.deepcopy(doc)


def _input_of(fake, op):
    return (fake.calls_of(op)[0]["variables"] or {}).get("input") or {}


def _admin():
    return {"user_id": "u1", "roles": ["ADMIN"], "username": "admin"}


# ===========================================================================
# A. ONE PRESS, GOES LIVE
# ===========================================================================


def test_a_pushed_product_reaches_shopify_ACTIVE_not_draft(db, shopify):
    """DOOR 1. Assert the PAYLOAD Shopify received. Every IMS product is born
    ecom.status=DRAFT and nothing advances it, so before the fix this press
    sent status=DRAFT -- 'sent 1, failed 0' and an empty brand page."""
    doc = _seed(db, _product())

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True and res.mode == "LIVE"
    assert _input_of(shopify, "imsProductCreate")["status"] == "ACTIVE", (
        "the product Shopify was told about is still a DRAFT -- invisible"
    )


def test_the_dry_run_plan_shows_the_same_ACTIVE_status(db, monkeypatch):
    """The SIMULATED plan the operator previews must be the payload the live
    press would send -- a plan that says DRAFT while the press sends ACTIVE is
    a lie about what the button does."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    res = _run(shopify_push.push_product(db, _product(), []))
    assert res.mode == "SIMULATED"
    assert (res.payload or {}).get("status") == "ACTIVE"


def test_an_archived_product_is_never_resurrected_by_a_press(db, shopify):
    """ARCHIVED is a deliberate retirement, not the un-advanced create-time
    default -- the publish intent must not override it."""
    doc = _seed(db, _product(status="ARCHIVED"))
    _run(shopify_push.push_product(db, doc, []))
    assert _input_of(shopify, "imsProductCreate")["status"] == "ARCHIVED"


def test_a_pushed_product_is_published_to_the_online_store_channel(db, shopify):
    """DOOR 3 (the sales channel). An ACTIVE product published to NO channel is
    still invisible on bettervision.in. Before the fix publishablePublish was
    behind SHOPIFY_PUBLISH_ON_CREATE (default OFF), so it never ran."""
    doc = _seed(db, _product())

    _run(shopify_push.push_product(db, doc, []))

    pubs = shopify.calls_of("imsPublishablePublish")
    assert len(pubs) == 1, "the product was never attached to the Online Store"
    assert pubs[0]["variables"]["id"] == shopify.product_id
    assert pubs[0]["variables"]["input"] == [{"publicationId": _PUB_GID}]


def test_an_already_mapped_product_is_published_too(db, shopify):
    """The rows already carrying a Shopify id got there as DRAFTs. Publish only
    ever ran on CREATE, so re-pressing them could never make them visible."""
    doc = _seed(db, _product(shopify_id="gid://shopify/Product/900"))
    shopify.media_on_existing = 1  # already has its photo on Shopify

    _run(shopify_push.push_product(db, doc, []))

    assert _input_of(shopify, "imsProductUpdate")["status"] == "ACTIVE"
    assert len(shopify.calls_of("imsPublishablePublish")) == 1


def test_ims_records_the_product_as_published(db, shopify):
    """ecom.status is READ in six places (the Online Store screen's PUBLISHED
    card, the storefront-visibility helpers) and was written 'PUBLISHED' in
    ZERO. After a real publish IMS must agree with the storefront."""
    doc = _seed(db, _product())

    _run(shopify_push.push_product(db, doc, []))

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["status"] == "PUBLISHED"
    assert saved["ecom"]["shopify_product_id"] == shopify.product_id


def test_publish_is_withheld_when_the_variant_could_not_be_priced(db, shopify):
    """A 0.00 listing is worse than no listing. The product may be created, but
    it must not become visible."""
    doc = _product()
    doc.pop("mrp", None)
    doc.pop("offer_price", None)
    _seed(db, doc)

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True
    assert shopify.calls_of("imsPublishablePublish") == []
    assert (res.publication or {}).get("published") is not True


# ===========================================================================
# B. NO PHOTO, NO PUBLISH
# ===========================================================================


def test_a_product_with_no_photograph_is_refused(db, shopify):
    """Owner ruling: 'Refuse -- no photo, no publish.' A name, a price and an
    empty grey box is worse for the brand than absence. The refusal must stop
    at Shopify's door: NO network call at all."""
    doc = _seed(db, _product(pid="P2", photos=False))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is False, "a photo-less product was pushed"
    assert res.reason == "no_photo"
    assert "photograph" in (res.error or "").lower()
    assert shopify.calls == [], "a photo-less product reached the Shopify API"


def test_the_refusal_is_visible_and_never_counted_as_pushed(db, shopify, monkeypatch):
    """Not silently skipped, not tallied under `pushed`. The sweep must report
    the refusal on its own line so the operator sees WHY nothing went live."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _seed(db, _product(pid="P-ok"))
    _seed(db, _product(pid="P-nophoto", photos=False))

    out = _run(push_router.push_all_pending(entities="products", current_user=_admin()))

    bucket = out["summary"]["products"]
    assert bucket["pushed"] == 1
    assert bucket.get("refused_no_photo") == 1
    assert bucket["failed"] == 0, "a refusal must not be filed as a failure"


def test_a_refused_product_stays_in_the_queue(db, shopify):
    """A failed/refused push must NEVER de-queue the row -- silently dropping a
    product forever is the exact class of failure this whole effort is about."""
    _seed(db, _product(pid="P2", photos=False))
    doc = copy.deepcopy(db["catalog_products"].find_one({"id": "P2"}))

    _run(shopify_push.push_product(db, doc, []))

    saved = db["catalog_products"].find_one({"id": "P2"})
    assert saved["ecom"]["locally_modified"] is True
    assert push_router._product_counts(db)["pending"] == 1


def test_the_photograph_rides_the_SAME_press_as_the_product(db, shopify):
    """POSITIVE CONTROL. "Has a photo in IMS" and "has a photo on Shopify" are
    different questions; the one that protects the storefront is the second.
    So the press that creates the product also attaches its photo, and the
    publish happens after."""
    doc = _seed(db, _product())

    res = _run(shopify_push.push_product(db, doc, []))

    media = shopify.calls_of("imsProductCreateMedia")
    assert len(media) == 1, "the product went to Shopify without its photograph"
    sent = media[0]["variables"]["media"]
    assert sent[0]["originalSource"] == "https://cdn.example.com/rb2140-front.jpg"
    assert sent[0]["mediaContentType"] == "IMAGE"
    assert (res.photos or {}).get("attached") == 1
    # ...and the publish came AFTER the photo, never before it.
    ops = shopify.ops()
    assert ops.index("imsProductCreateMedia") < ops.index("imsPublishablePublish")


def test_publish_is_withheld_when_the_photograph_could_not_be_attached(db, shopify):
    """The media attach is a fail-soft side channel. If it fails the product is
    still created -- but it must NOT become visible, or the storefront shows the
    grey box the rule exists to prevent."""
    doc = _seed(db, _product())
    real = shopify.__call__

    async def _media_fails(dbx, query, variables):
        if "imsProductCreateMedia" in query:
            shopify.calls.append(
                {"op": "imsProductCreateMedia", "variables": variables}
            )
            return {
                "data": {
                    "productCreateMedia": {
                        "media": [],
                        "mediaUserErrors": [
                            {"field": "originalSource", "message": "boom"}
                        ],
                    }
                }
            }
        return await real(dbx, query, variables)

    shopify_push._graphql = _media_fails
    try:
        res = _run(shopify_push.push_product(db, doc, []))
    finally:
        shopify_push._graphql = shopify

    assert res.ok is True
    assert shopify.calls_of("imsPublishablePublish") == []


def test_a_local_upload_path_is_not_a_photograph(db, shopify):
    """Shopify pulls the image over the internet; it cannot fetch a private
    /uploads/... path. Counting one as a photo would publish a grey box."""
    doc = _seed(db, _product(pid="P3", photos=False, images=["/uploads/x.jpg"]))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is False and res.reason == "no_photo"
    assert shopify.calls == []


def test_an_already_photographed_shopify_product_is_not_re_attached(db, shopify):
    """Re-pressing a live product must not pile a duplicate copy of the same
    photo onto the storefront listing."""
    doc = _seed(db, _product(shopify_id="gid://shopify/Product/900"))
    shopify.media_on_existing = 2

    _run(shopify_push.push_product(db, doc, []))

    assert shopify.calls_of("imsProductCreateMedia") == []
    assert len(shopify.calls_of("imsPublishablePublish")) == 1


def test_a_live_product_missing_its_photo_gets_one_on_the_next_press(db, shopify):
    """The gap a create-time-only attach would leave: a product put on Shopify
    by an OLDER press carries no media. The update press must repair it before
    it publishes."""
    doc = _seed(db, _product(shopify_id="gid://shopify/Product/900"))
    shopify.media_on_existing = 0

    _run(shopify_push.push_product(db, doc, []))

    assert len(shopify.calls_of("imsProductCreateMedia")) == 1
    assert len(shopify.calls_of("imsPublishablePublish")) == 1


# ===========================================================================
# C. A PRESS THAT DID NOT ACHIEVE VISIBILITY STAYS QUEUED
# ===========================================================================
# The publish can be WITHHELD after the product write succeeded (unpriced,
# the photograph did not attach, the publication could not be resolved). The
# product write-back clears the dirty flag, so without this the row would be
# on Shopify, invisible, and OUT of the queue -- pending 0 and an empty brand
# page, the exact lie this whole change exists to end.


def _withheld(db, shopify, pid="P1"):
    """A press that creates the product but cannot publish it: no price."""
    doc = _product(pid=pid)
    doc.pop("mrp", None)
    doc.pop("offer_price", None)
    _seed(db, doc)
    return _run(shopify_push.push_product(db, doc, []))


def test_a_press_whose_publish_was_withheld_leaves_the_row_queued(db, shopify):
    res = _withheld(db, shopify)

    assert (res.publication or {}).get("published") is not True
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["locally_modified"] is True, (
        "the product is on Shopify, invisible, and no longer in the queue"
    )
    assert push_router._product_counts(db)["pending"] == 1


def test_the_withheld_row_keeps_its_shopify_id_so_a_retry_never_duplicates(db, shopify):
    """It stays queued so a re-press retries it -- and the retry must land on
    the SAME Shopify product, not mint a second one."""
    res = _withheld(db, shopify)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_product_id"] == shopify.product_id
    assert res.shopify_id == shopify.product_id


def test_a_press_that_DID_publish_still_drains_the_queue(db, shopify):
    """CONTROL: the requeue must not fire on the happy path, or the sweep would
    push the same product forever against a LIVE storefront."""
    _seed(db, _product())

    _run(shopify_push.push_product(db, _load_doc(db, "P1"), []))

    assert push_router._product_counts(db)["pending"] == 0


def _load_doc(db, pid):
    return copy.deepcopy(db["catalog_products"].find_one({"id": pid}))


# ===========================================================================
# D. THE BATCH CAP -- one wrong press is bounded
# ===========================================================================


def test_one_press_sends_at_most_the_batch_cap(db, shopify, monkeypatch):
    """N+10 queued, one press sends exactly N. Pressing publish now puts
    products in front of customers immediately, so a single wrong press must
    not be able to take the whole catalogue live."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    cap = push_router.PRODUCT_BATCH_CAP
    for i in range(cap + 10):
        _seed(db, _product(pid="P%03d" % i))

    out = _run(push_router.push_all_pending(entities="products", current_user=_admin()))

    assert out["summary"]["products"]["pushed"] == cap
    assert len(shopify.calls_of("imsProductCreate")) == cap
    assert out["batch_cap"] == cap


def test_the_rest_stay_queued_and_the_press_does_not_read_as_finished(db, shopify, monkeypatch):
    """The 10 that did not go must still be waiting -- a capped press that
    silently dropped them would be the same silent failure as before -- and the
    result must say it stopped early so the operator presses again."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    cap = push_router.PRODUCT_BATCH_CAP
    for i in range(cap + 10):
        _seed(db, _product(pid="P%03d" % i))

    out = _run(push_router.push_all_pending(entities="products", current_user=_admin()))

    assert push_router._product_counts(db)["pending"] == 10
    assert out["limit_reached"] is True


def test_a_caller_cannot_raise_the_cap(db, shopify, monkeypatch):
    """The frontend passes limit=100 explicitly, so a cap that is only a
    DEFAULT caps nothing. It is a hard server-side clamp."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    cap = push_router.PRODUCT_BATCH_CAP
    for i in range(cap + 10):
        _seed(db, _product(pid="P%03d" % i))

    out = _run(
        push_router.push_all_pending(
            entities="products", limit=5000, current_user=_admin()
        )
    )

    assert out["summary"]["products"]["pushed"] == cap


def test_a_photo_less_refusal_does_not_burn_a_cap_slot(db, shopify, monkeypatch):
    """A refusal never reached Shopify and nothing went live. If refusals ate
    the cap, a catalogue that is mostly un-photographed would publish nothing
    at all and the owner would press forever."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    cap = push_router.PRODUCT_BATCH_CAP
    for i in range(cap):
        _seed(db, _product(pid="N%03d" % i, photos=False))
    for i in range(3):
        _seed(db, _product(pid="P%03d" % i))

    out = _run(push_router.push_all_pending(entities="products", current_user=_admin()))

    bucket = out["summary"]["products"]
    assert bucket["refused_no_photo"] == cap
    assert bucket["pushed"] == 3


# ===========================================================================
# E. TAKE-DOWN -- pulling ONE product back off the storefront
# ===========================================================================


def _live_product(db, pid="P1"):
    """A product that IS on the storefront: mapped, PUBLISHED, and dirty (the
    worst case -- a take-down has to survive the next sweep)."""
    doc = _product(pid=pid, shopify_id="gid://shopify/Product/900", status="PUBLISHED")
    doc["ecom"]["locally_modified"] = True
    return _seed(db, doc)


def test_take_down_unpublishes_the_product_on_shopify(db, shopify, monkeypatch):
    """The engine could already delist; nothing could ask it to. The button
    is the reversibility that makes one-press publishing survivable."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _live_product(db)

    out = _run(push_router.take_down_product("P1", current_user=_admin()))

    assert out["result"]["ok"] is True
    assert out["result"]["action"] == "delist"
    sent = _input_of(shopify, "imsProductUpdate")
    assert sent["status"] == "DRAFT", "the product is still visible on the storefront"
    assert sent["id"] == "gid://shopify/Product/900"


def test_take_down_keeps_the_shopify_id(db, shopify, monkeypatch):
    """Losing the id would make the next push CREATE A DUPLICATE product on the
    live storefront. The Shopify product is never deleted either."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _live_product(db)

    out = _run(push_router.take_down_product("P1", current_user=_admin()))

    assert out["result"]["shopify_id"] == "gid://shopify/Product/900"
    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["shopify_product_id"] == "gid://shopify/Product/900"
    assert shopify.calls_of("productDelete") == []


def test_a_taken_down_product_does_not_resurrect_on_the_next_sweep(db, shopify, monkeypatch):
    """The row was DIRTY when it was taken down. If the take-down left it
    queued, the very next sweep would put it back in front of customers
    seconds later and the button would be worthless."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _live_product(db)

    _run(push_router.take_down_product("P1", current_user=_admin()))
    before = len(shopify.calls_of("imsPublishablePublish"))
    out = _run(push_router.push_all_pending(entities="products", current_user=_admin()))

    assert push_router._product_counts(db)["pending"] == 0
    assert out["pushed_count"] == 0, "the sweep re-pushed a product just taken down"
    assert len(shopify.calls_of("imsPublishablePublish")) == before


def test_ims_stops_claiming_a_taken_down_product_is_published(db, shopify, monkeypatch):
    """ecom.status is read by the DRAFT/PUBLISHED cards and every
    storefront-visibility helper. Leaving it PUBLISHED would have IMS insisting
    a product is on a storefront it was just pulled from."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _live_product(db)

    _run(push_router.take_down_product("P1", current_user=_admin()))

    assert db["catalog_products"].find_one({"id": "P1"})["ecom"]["status"] == "DRAFT"


def test_a_taken_down_product_goes_straight_back_when_pressed_again(db, shopify, monkeypatch):
    """Take-down must not re-shut the publish door: DRAFT is the create-time
    default the whole change exists to get past, so pressing publish again puts
    the product straight back on the same Shopify listing."""
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _live_product(db)
    _run(push_router.take_down_product("P1", current_user=_admin()))
    shopify.media_on_existing = 1

    doc = copy.deepcopy(db["catalog_products"].find_one({"id": "P1"}))
    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True
    assert (res.payload or {})["status"] == "ACTIVE"
    assert (res.publication or {}).get("published") is True
    assert res.shopify_id == "gid://shopify/Product/900", "a DUPLICATE listing"


def test_taking_down_something_never_on_shopify_is_a_clean_noop(db, shopify, monkeypatch):
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    _seed(db, _product(pid="P2"))

    out = _run(push_router.take_down_product("P2", current_user=_admin()))

    assert out["result"]["ok"] is True and out["result"]["action"] == "noop"
    assert shopify.calls == []


def test_taking_down_an_unknown_product_is_a_404(db, shopify, monkeypatch):
    monkeypatch.setattr(push_router, "_get_db", lambda: db)
    with pytest.raises(Exception) as exc:
        _run(push_router.take_down_product("nope", current_user=_admin()))
    assert getattr(exc.value, "status_code", None) == 404


# ===========================================================================
# THE PING-PONG GUARD, re-checked against the NEW publish path
# ===========================================================================


def test_publishing_writeback_never_re_queues_the_row(db, shopify):
    """The publish now writes ecom.status back. That is a SYNC write. If it
    queued the row, every press would queue itself again -- forever, against a
    LIVE storefront."""
    _seed(db, _product())

    for _ in range(2):
        dirty = [
            d["id"]
            for d in push_router._all_docs(db, "catalog_products")
            if (d.get("ecom") or {}).get("locally_modified")
        ]
        for pid in dirty:
            _run(
                shopify_push.push_product(
                    db,
                    copy.deepcopy(db["catalog_products"].find_one({"id": pid})),
                    [],
                )
            )
        assert push_router._product_counts(db)["pending"] == 0

    assert len(shopify.calls_of("imsProductCreate")) == 1
