"""
The design-queue press (shopify_push.push_image) and the product press share
ONE identity for a photograph on Shopify: the parent twin's ``ecom.media_map``.

THE BUG THIS LOCKS DOWN (sync audit 2026-09-06, item 10)
--------------------------------------------------------
push_image used to call productCreateMedia on every LIVE press and record the
minted gid on the image row (``product_images.shopify_image_id``) -- a second
"which Shopify media is this IMS photo" writer beside the twin's media_map.
productCreateMedia is a pure create, the press never consulted the map, so a
re-press attached the same source url AGAIN (duplicate media on the listing)
and the row overwrite dropped the earlier gid with no tombstone: 24 orphan
rows with dead media ids.

THE RULE NOW: a design-queue image is on Shopify iff the parent twin's
media_map maps its source url, and what a press does is read off that map by
ONE predicate (media.image_press_plan: noop | update | create) the press, the
sweep's skip and the pushed/pending counts all share. The LIVE press runs the
SAME photo pass the product press runs (sync_product_media, against the media
the listing carries right now) in the DESIGN LANE: photos = this row's url
alone, design_row = the row. Two lanes, one map: a row with an image_id is the
design lane, a row without is the product's; each press governs its own lane
(attach / delete / reorder) and KEEPS the other exactly where it is. The
product press (and so the 01:00/09:00 sync) never reads the design queue;
the design press never attaches an own photograph the product press has not
put up, never drops one IMS removed, never reorders. A url that is one of the
product's own photographs is the product's lane whichever door attached it.
The pass writes the map on EVERY run, so a media that left Shopify behind
IMS's back is pruned on the next press of either door.

Every Shopify call is MOCKED (shopify_push._graphql is monkeypatched to a
transcript fake); no real network request is ever made.

REVERT-PROOF (each test names the one-line revert that turns it red):
  test_a_first_press...            revert the map write (row writer)          -> red
  test_a_repress...                drop the image_press_plan no-op check      -> red
  test_a_replaced_asset...         attach without the pass (old create)       -> red
  test_a_failed_delete_keeps...    map written from owned, not owned+pending  -> red
  test_hand_uploaded...            skip the media read (pass current=[])      -> red
  test_a_listing_ims_owns...       attach blind on hands_off                  -> red
  test_pressing_one_image...       union every APPROVED row into the list     -> red
  test_the_product_press_never..   any product_images read in push_product    -> red
  test_the_product_press_neither.. delete rule without the lane (_governed)   -> red
  test_the_product_press_leaves..  reorder compared over keep, not desired    -> red
  test_door_a_then_owns...         prune the other lane in _in_ims_order      -> red
  test_dark_press...               read the media before the gate             -> red
  test_a_map_writeback_failure..   trust the pass instead of re-reading       -> red
  test_an_unfetchable_asset...     image_source_url without _photo_url        -> red
  test_the_in_app_url...           image_source_url without _photo_url        -> red
  test_a_size_variant_twin...      _listing_map without the is_variant_of     -> red
  -- round 2 --
  test_a_product_without_a_photo.. drop the product_photo_urls gate (P1)      -> red
  test_a_public_url_drift...       same gate, the config-drift shape (P1)     -> red
  test_the_design_press_never_gov. _governed without the lane split (P1 root) -> red
  test_the_rows_alt_text...        _attach_product_photos without alts (P2)   -> red
  test_a_design_press_of_an_own..  stamp image_id on an own-photo url (P3)    -> red
  test_a_replaced_asset_already..  map written only on attach/delete (P4)     -> red
  test_a_design_media_deleted...   same: the no-change product press writes   -> red
  test_the_press_the_sweep...      sweep/counts on image_media_gid alone (P5) -> red
  -- round 4 --
  test_a_design_asset_promoted...  owned_media without the by-url lane (P1)    -> red
  test_a_product_press_on_a_stale. map written from the snapshot, no merge (P2) -> red
  test_a_design_press_on_a_stale.. same revert, the other door (P2)           -> red
  test_the_refusals_are_the_plans. image_press_plan without the skip fold (P4) -> red
  -- round 5 --
  test_a_listing_ims_owns_nothing.. image_press_plan without hands_off (P3)    -> red
  test_a_row_whose_lane_flips...    _merge by lane, not by id (P1)             -> red
  test_two_presses_on_one_product.. media_lease that claims nothing (P1/P2)    -> red
  test_the_press_reads_the_queue..  press the passed copy, no re-read          -> red
  test_replacing_one_rows_asset...  heirs={} (P2 sibling)                      -> red
  test_an_attach_whose_answer...    plan_product_media without adopt           -> red
  test_a_row_delete_waits...        media_lease that claims nothing (P4)       -> red
  test_deleting_a_promoted_rows...  delete_image without the map write (P1)    -> red
  test_the_delete_gate_fails...     the gate on _resolve_product_doc (P2)      -> red
  -- round 6 --
  test_a_rows_delete_is_refused..   image_lane_media without pending_media     -> red
  test_a_map_writeback_failure..    the attach sent with no pending record     -> red
  test_a_lost_attach_of_a_row_re..  plan_product_media without the settle      -> red
  test_a_product_blocked_from_on..  image_press_plan without the block refusal -> red

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_design_queue_repress.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402
import copy  # noqa: E402

import pytest  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import media as _media  # noqa: E402

TOMB = shopify_push.TOMBSTONES_COLLECTION
GID = "gid://shopify/Product/900"
OWN = "https://cdn.example.com/rb-front.jpg"  # the product's own photograph
OWN2 = "https://cdn.example.com/rb-side.jpg"  # a second own photograph
OLD = "https://cdn.example.com/design-v1.jpg"  # a design asset pressed earlier
NEW = "https://cdn.example.com/design-v2.jpg"  # the designer's replacement
OTHER = "https://cdn.example.com/design-other.jpg"  # a sibling design row's asset
HAND = "gid://shopify/MediaImage/7"  # a human's upload -- never in the map


def _m(n):
    return "gid://shopify/MediaImage/%d" % n


def _run(coro):
    return asyncio.run(coro)


class _Coll:
    """A MockCollection read with real Mongo's semantics: find_one hands back
    a COPY, so a doc a press read is a snapshot a later write does not mutate
    (MockCollection returns the stored dict itself, which would hide every
    interleaving between two presses). Everything else is delegated."""

    def __init__(self, name):
        self._m = MockCollection(name)

    def find_one(self, *args, **kwargs):
        return copy.deepcopy(self._m.find_one(*args, **kwargs))

    def insert_one(self, doc):
        # Mongo's unique _id: a second insert of one _id is REFUSED (the media
        # lease is exactly that claim; MockCollection would overwrite).
        if doc.get("_id") is not None and self._m.find_one({"_id": doc["_id"]}) is not None:
            raise DuplicateKeyError("E11000 duplicate key error")
        return self._m.insert_one(doc)

    def __getattr__(self, name):
        return getattr(self._m, name)


class _DB:
    def __init__(self):
        self._c = {}

    def __getitem__(self, name):
        return self._c.setdefault(name, _Coll(name))


class _NoQueueDB(_DB):
    """A db whose design queue is UNREADABLE: any touch of product_images
    raises (the Mongo timeout on one collection the 01:00/09:00 sync can
    meet). The product press must never notice."""

    def __getitem__(self, name):
        if name == "product_images":
            raise RuntimeError("product_images unreadable")
        return super().__getitem__(name)


class _Shopify:
    """A fake Shopify that routes on the operation and keeps a transcript.
    ``media_nodes`` is what the listing carries before the press; a create
    mints one MediaImage per input (100, 101, ...) and a delete removes them,
    so the fake's listing is what the next read returns. ``fail_delete_once``
    answers the next productDeleteMedia with a mediaUserError and removes
    nothing. ``on_read`` (once) runs just before the next media read answers:
    another door's press landing inside this press's window; ``before[op]``
    (once) does the same for any operation. ``lose_create_once`` lands the
    next productCreateMedia on the listing and then raises, as the transport
    does when the response is lost (a timeout after Shopify accepted it).
    Every call AWAITS once, as the real transport does, so two presses
    gathered on one loop interleave exactly as two workers would."""

    def __init__(self, media_nodes=None):
        self.calls = []
        self.media_nodes = list(media_nodes or [])
        self.next_media = 100
        self.fail_delete_once = False
        self.lose_create_once = False
        self.on_read = None
        self.before = {}

    @staticmethod
    def _op(query):
        for name in (
            "imsProductCreateMedia",
            "imsProductDeleteMedia",
            "imsProductReorderMedia",
            "imsProductMedia",
            "imsProductCreate",
            "imsProductUpdate",
            "imsPublishablePublish",
            "imsVariantPricesUpdate",
            "imsVariantsBulkCreate",
            "imsVariantInventoryUpdate",
            "imsInventorySetQuantities",
            "imsLocations",
            "metafieldsSet",
        ):
            if name in query:
                return name
        return "unknown"

    def ops(self):
        return [c["op"] for c in self.calls]

    def calls_of(self, op):
        return [c for c in self.calls if c["op"] == op]

    def listing(self):
        return [n["id"] for n in self.media_nodes]

    async def __call__(self, db, query, variables):
        await asyncio.sleep(0)
        op = self._op(query)
        self.calls.append({"op": op, "variables": copy.deepcopy(variables)})
        hook = self.before.pop(op, None)
        if hook:
            hook()
        variant = {
            "id": "gid://shopify/ProductVariant/901",
            "title": "Default Title",
            "selectedOptions": [],
            "inventoryItem": {"id": "gid://shopify/InventoryItem/902"},
        }
        if op == "imsProductMedia":
            hook, self.on_read = self.on_read, None
            if hook:
                hook()
            return {
                "data": {
                    "product": {"id": variables["id"], "media": {"nodes": copy.deepcopy(self.media_nodes)}}
                }
            }
        if op == "imsProductCreateMedia":
            out = []
            for m in variables.get("media") or []:
                gid = _m(self.next_media)
                self.next_media += 1
                out.append({"id": gid, "status": "PROCESSING"})
                self.media_nodes.append(
                    {
                        "id": gid,
                        "image": {"url": "https://cdn.shopify.com/%s.jpg" % gid.rsplit("/", 1)[-1]},
                        "originalSource": {"url": m["originalSource"]},
                    }
                )
            if self.lose_create_once:
                self.lose_create_once = False
                raise RuntimeError("shopify request failed after 3 attempts (timeout)")
            return {"data": {"productCreateMedia": {"media": out, "mediaUserErrors": []}}}
        if op == "imsProductDeleteMedia":
            if self.fail_delete_once:
                self.fail_delete_once = False
                return {"data": {"productDeleteMedia": {"deletedMediaIds": [], "mediaUserErrors": [{"message": "boom"}]}}}
            gone = set(variables["mediaIds"])
            self.media_nodes = [n for n in self.media_nodes if n["id"] not in gone]
            return {"data": {"productDeleteMedia": {"deletedMediaIds": sorted(gone), "mediaUserErrors": []}}}
        if op == "imsProductReorderMedia":
            return {"data": {"productReorderMedia": {"job": {"id": "gid://shopify/Job/1"}, "mediaUserErrors": []}}}
        if op in ("imsProductCreate", "imsProductUpdate"):
            field = "productCreate" if op == "imsProductCreate" else "productUpdate"
            return {
                "data": {
                    field: {
                        "product": {
                            "id": GID,
                            "handle": "h",
                            "variants": {"nodes": [variant]},
                            "media": {"nodes": copy.deepcopy(self.media_nodes)},
                        },
                        "userErrors": [],
                    }
                }
            }
        if op == "imsPublishablePublish":
            return {"data": {"publishablePublish": {"userErrors": []}}}
        if op == "imsVariantPricesUpdate":
            return {"data": {"productVariantsBulkUpdate": {"productVariants": [{"id": variant["id"]}], "userErrors": []}}}
        if op == "imsVariantsBulkCreate":
            return {"data": {"productVariantsBulkCreate": {"productVariants": [], "userErrors": []}}}
        if op == "metafieldsSet":
            return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
        if op == "imsLocations":
            return {"data": {"locations": {"nodes": []}}}
        return {"data": {}}


@pytest.fixture
def gates(monkeypatch):
    """Open the three push gates and pin the publication id; the network is
    whatever the test installs."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "t.myshopify.com", "access_token": "shpat_t", "source": "vault"},
    )
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/1")
    shopify_push._publication_id_cache.clear()


def _live(monkeypatch, media_nodes=None):
    fake = _Shopify(media_nodes)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    return fake


def _nodes(*ids):
    return [{"id": _m(i), "image": {"url": "https://cdn.shopify.com/%d.jpg" % i}} for i in ids]


def _hand():
    return [{"id": HAND, "image": {"url": "https://cdn.shopify.com/hand-upload.jpg"}}]


def _row_of(entry):
    """A media_map row from a (url, gid) or (url, gid, image_id) tuple: the
    third element marks a design-queue press's media."""
    row = {"url": entry[0], "id": entry[1]}
    if len(entry) > 2:
        row["image_id"] = entry[2]
    return row


def _product(photos, media_map=None, pid="P1", **over):
    """A product LIVE on Shopify with the given IMS photo list and the media
    IMS recorded as its own (None = a pre-map listing)."""
    doc = {
        "id": pid,
        "sku": "SKU-1",
        "title": "Ray-Ban RB2140",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "mrp": 5000.0,
        "offer_price": 4000.0,
        "images": list(photos),
        "ecom": {
            "status": "PUBLISHED",
            "locally_modified": True,
            "shopify_product_id": GID,
            "shopify_variant_id": "gid://shopify/ProductVariant/901",
        },
    }
    if media_map is not None:
        doc["ecom"]["media_map"] = [_row_of(e) for e in media_map]
    doc["ecom"].update(over)
    return doc


def _seed(db, doc):
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    return copy.deepcopy(doc)


def _image(db, iid, url, **over):
    """An APPROVED design-queue row on P1."""
    doc = {"image_id": iid, "product_id": "P1", "url": url, "status": "APPROVED", "position": 0, **over}
    db["product_images"].insert_one(copy.deepcopy(doc))
    return db["product_images"].find_one({"image_id": iid})


def _map_of(db, pid="P1"):
    return (db["catalog_products"].find_one({"id": pid}) or {})["ecom"].get("media_map")


def _row(db, iid):
    return db["product_images"].find_one({"image_id": iid})


def _parent(db, pid="P1"):
    return db["catalog_products"].find_one({"id": pid})


# ===========================================================================
# ONE identity, ONE writer
# ===========================================================================


def test_a_first_press_attaches_once_and_writes_only_the_map(gates, monkeypatch):
    """The press reads the listing, attaches ONLY the new asset, and records
    the gid in the parent's media_map WITH the row's image_id (the lane
    marker). The row carries no Shopify id."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.mode == "LIVE" and res.action == "create"
    assert res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"]
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [NEW], "the edited asset, once"
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert _row(db, "I1").get("shopify_image_id") is None, "the map is the ONLY writer"
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 0


def test_a_repress_is_a_noop_with_zero_calls(gates, monkeypatch):
    """The exact 09-06 finding: pressing Publish twice must not mint a second
    MediaImage. The second press reports 'already on the listing' with the
    gid, and makes no call at all -- not even the read."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)
    first = _run(shopify_push.push_image(db, img))
    assert first.ok and first.action == "create" and first.shopify_id == _m(100)
    calls_before = len(fake.calls)

    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.action == "noop" and again.mode == "LIVE"
    assert again.shopify_id == _m(100) and again.payload["media_gid"] == _m(100)
    assert again.reason == "already on the listing"
    assert len(fake.calls) == calls_before, "zero mutations, zero reads"
    assert fake.listing() == [_m(1), _m(100)], "exactly one copy on the listing"
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]


def test_a_replaced_asset_tombstones_deletes_the_old_and_attaches_the_new_once(gates, monkeypatch):
    """The designer replaced the asset on the same row (edited_url OLD -> NEW).
    The press attaches NEW first, tombstones OLD, deletes it -- the SAME
    attach -> delete order and primitives the product press uses -- and the
    map swaps the row. Nothing is attached twice, nothing is lost silently."""
    fake = _live(monkeypatch, _nodes(1, 2))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (OLD, _m(2), "I1")]))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "create" and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia", "imsProductDeleteMedia"]
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [NEW]
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)]
    stones = list(db[TOMB].find({}))
    assert [(s["product_id"], s["media_gid"], s["url"]) for s in stones] == [("P1", _m(2), OLD)]
    assert stones[0]["deleted_at"].tzinfo is not None, "tz-aware UTC"
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 1


def test_a_failed_delete_keeps_the_old_asset_mapped_until_a_re_press_takes_it_down(gates, monkeypatch):
    """Shopify refuses the delete of the replaced asset. The press is loud
    (ok=False) and the map STILL holds OLD -- a row leaves the map only after
    its media is off Shopify -- so the re-press is not a no-op over an
    orphan: it runs the pass again, deletes OLD, attaches nothing. In
    between, the product press keeps its hands off both design rows."""
    fake = _live(monkeypatch, _nodes(1, 2))
    fake.fail_delete_once = True
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (OLD, _m(2), "I1")]))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    first = _run(shopify_push.push_image(db, img))

    assert first.ok is False and "mediaUserErrors" in (first.error or "")
    assert first.shopify_id == _m(100), "the minted gid rides the audit row"
    assert fake.listing() == [_m(1), _m(2), _m(100)]
    assert _map_of(db) == [
        {"url": OWN, "id": _m(1)},
        {"url": OLD, "id": _m(2), "image_id": "I1"},
        {"url": NEW, "id": _m(100), "image_id": "I1"},
    ], "OLD stays mapped: its delete has not happened"

    n = len(fake.calls)
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and not [o for o in fake.ops()[n:] if o.endswith("Media")]
    assert fake.listing() == [_m(1), _m(2), _m(100)] and len(_map_of(db)) == 3

    n = len(fake.calls)
    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.action == "update" and again.shopify_id == _m(100)
    assert fake.ops()[n:] == ["imsProductMedia", "imsProductDeleteMedia"], "no second attach"
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"


def test_hand_uploaded_media_is_never_touched_and_own_photos_never_re_attached(gates, monkeypatch):
    """A listing carrying a human's upload (not in the map) and the product's
    own mapped photo: the press attaches ONLY the design asset -- the hand
    upload is neither deleted nor moved, and the own photo is not attached a
    second time (the pass diffs against what the listing carries NOW)."""
    fake = _live(monkeypatch, _nodes(1) + _hand())
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"], "no delete, no reorder"
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [NEW]
    assert fake.listing() == [_m(1), HAND, _m(100)]
    assert list(db[TOMB].find({})) == []
    assert res.photos["unmanaged"] == 1
    assert HAND not in str(_map_of(db))


def test_a_listing_ims_owns_nothing_on_is_refused_not_attached_to_blind(gates, monkeypatch):
    """A pre-map listing (hand uploads, no media_map): the product press keeps
    its hands off, and so does this one -- a loud refusal pointing at the
    adoption runbook, ZERO mutations. Attaching blind here is exactly how the
    24 orphan rows were minted.
    Round 5 (P3): the refusal is read off the TWIN, so it is part of the one
    predicate -- the plan says skip/hands_off, the counts do not call the row
    pending, and the press refuses with ZERO calls (it used to read the
    listing first, answer 'create' everywhere else, and so stay pending and
    be re-pressed by every images sweep forever).
    REVERT-PROOF: drop the hands_off branch of image_press_plan -> red."""
    from api.routers import online_store_push as router

    fake = _live(monkeypatch, _hand())
    db = _DB()
    _seed(db, _product([OWN]))  # no map
    img = _image(db, "I1", NEW)

    plan = shopify_push.image_press_plan(_parent(db), img)
    assert (plan["action"], plan["reason"]) == ("skip", "hands_off")
    assert router._image_counts(db) == {"approved": 1, "pushed": 0, "pending": 0}
    res = _run(shopify_push.push_image(db, img))

    assert (res.ok, res.action, res.reason) == (False, "skip", "hands_off")
    assert "adopt" in (res.error or "") and "hands off" in (res.error or "")
    assert fake.calls == [], "zero calls, not even the read"
    assert fake.listing() == [HAND]
    assert _map_of(db) is None


# ===========================================================================
# The press is exactly the image the human pressed
# ===========================================================================


def test_pressing_one_image_never_attaches_a_sibling_row(gates, monkeypatch):
    """Two APPROVED rows on the product; the human presses I1. Only I1's asset
    reaches Shopify and the map; I2 stays pending -- its own press, its own
    audit row."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)
    _image(db, "I2", OTHER)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [NEW]
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert shopify_push.image_media_gid(_parent(db), _row(db, "I2")) is None, "I2 still pending"


# ===========================================================================
# The product press (and so the scheduled sync) keeps its hands off the lane
# ===========================================================================


def test_the_product_press_never_reads_the_design_queue(gates, monkeypatch):
    """A Mongo timeout on product_images during the 01:00/09:00 sync must not
    be able to turn into a delete plan: the product press does not consult
    the queue at all. Here the queue RAISES on any touch, and the press runs
    clean, keeps the design media and touches no media."""
    fake = _live(monkeypatch, _nodes(1, 100))
    db = _NoQueueDB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(100), "I1")]))

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True and res.mode == "LIVE", res.error
    assert not [o for o in fake.ops() if o.endswith("Media")], fake.ops()
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert res.photos["deleted"] == 0 and res.photos["on_shopify"] == 2


def test_the_product_press_neither_attaches_nor_drops_design_media(gates, monkeypatch):
    """IMS dropped its own side shot (OWN2) -- that one comes down, as before.
    The design media NEW stays although its queue row no longer exists, and
    the APPROVED-but-unpressed sibling OTHER is NOT attached: a design image
    reaches or leaves Shopify only through a press of its row."""
    fake = _live(monkeypatch, _nodes(1, 2, 100))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (OWN2, _m(2)), (NEW, _m(100), "I1")]))
    _image(db, "I2", OTHER)  # APPROVED, never pressed; I1's row is gone

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True, res.error
    assert fake.calls_of("imsProductCreateMedia") == [], "no attach without a press"
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)], "only the dropped OWN photo"
    assert [(s["media_gid"], s["url"]) for s in db[TOMB].find({})] == [(_m(2), OWN2)]
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert res.photos == {
        "attached": 0, "deleted": 1, "reordered": False,
        "unmanaged": 0, "adopted": 0, "hands_off": False, "on_shopify": 2,
    }


def test_the_product_press_leaves_a_design_media_in_its_slot(gates, monkeypatch):
    """The design media leads the listing (a human put it first in the admin).
    The product press wants its own photos in IMS order -- among themselves:
    the design row keeps slot 0, no reorder is sent."""
    fake = _live(monkeypatch, _nodes(100, 1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(100), "I1")]))
    _image(db, "I1", NEW)

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True and not [o for o in fake.ops() if o.endswith("Media")], fake.ops()
    assert res.photos["reordered"] is False and fake.listing() == [_m(100), _m(1)]
    plan = shopify_push.plan_product_media(_parent(db), [OWN], fake.media_nodes)
    assert plan == {
        "attach": [], "delete": [], "reorder": [], "unmanaged": 0, "hands_off": False,
        "owned": [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}],
        "adopt": [],
    }
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"


def test_door_a_then_owns_the_design_media(gates, monkeypatch):
    """After a design press the PRODUCT press sees that media as its own: its
    diff keeps it (no delete, no re-attach), the map survives the press with
    the image_id row intact, and the design press behind it is a no-op."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)
    assert _run(shopify_push.push_image(db, img)).ok
    n = len(fake.calls)

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True and res.mode == "LIVE"
    media_ops = [o for o in fake.ops()[n:] if o.endswith("Media")]
    assert media_ops == [], media_ops
    assert res.photos["attached"] == 0 and res.photos["deleted"] == 0 and res.photos["on_shopify"] == 2
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    # The pure diff door A runs, over the product's OWN photographs:
    parent = _parent(db)
    plan = shopify_push.plan_product_media(parent, shopify_push.product_photo_urls(parent), fake.media_nodes)
    assert plan["attach"] == [] and plan["delete"] == []
    assert {"url": NEW, "id": _m(100), "image_id": "I1"} in plan["owned"]
    # ... and the map writer keeps the row even when a write names own photos only:
    assert _media._in_ims_order(plan["owned"], [OWN]) == plan["owned"]
    again = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert again.action == "noop" and again.shopify_id == _m(100)


# ===========================================================================
# Dark, loud, and the edges of identity
# ===========================================================================


def test_dark_press_makes_zero_network_calls(monkeypatch):
    """Gates closed: an unmapped image is a SIMULATED create carrying the media
    input; a mapped one is a SIMULATED no-op carrying its gid; a mapped one
    whose row still maps a replaced asset is a SIMULATED update naming the
    drop. None reads or writes Shopify, none touches the map."""
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {"shop_url": "x", "access_token": "y", "source": "vault"},
    )

    async def _boom(db, query, variables):  # pragma: no cover - must never run
        raise AssertionError("DARK press must not hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)
    db = _DB()
    before = [(OWN, _m(1)), (OLD, _m(2), "I2"), (OTHER, _m(3), "I3"), (NEW, _m(4), "I3")]
    _seed(db, _product([OWN], media_map=before))

    fresh = _run(shopify_push.push_image(db, _image(db, "I1", "https://cdn.example.com/fresh.jpg")))
    mapped = _run(shopify_push.push_image(db, _image(db, "I2", OLD)))
    pending = _run(shopify_push.push_image(db, _image(db, "I3", NEW)))

    assert fresh.mode == "SIMULATED" and fresh.ok and fresh.action == "create"
    assert fresh.payload["media"][0]["originalSource"] == "https://cdn.example.com/fresh.jpg"
    assert fresh.shopify_id is None
    assert mapped.mode == "SIMULATED" and mapped.ok and mapped.action == "noop"
    assert mapped.shopify_id == _m(2) and mapped.payload["media_gid"] == _m(2)
    assert pending.mode == "SIMULATED" and pending.ok and pending.action == "update"
    assert pending.shopify_id == _m(4) and pending.payload["drop"] == [OTHER]
    assert _map_of(db) == [_row_of(e) for e in before]


def _die_after_the_record(monkeypatch):
    """The pass's record of its attach (media_pending, written BEFORE
    productCreateMedia) lands; every media write after it fails -- the twin
    stops taking writes, or the worker dies right after the attach."""
    real = _media._writeback_media_map
    state = {"recorded": False}

    def _write(db, pid, rows, pending=None):
        if state["recorded"] or not pending:
            return False
        state["recorded"] = True
        return real(db, pid, rows, pending=pending)

    monkeypatch.setattr(_media, "_writeback_media_map", _write)
    return lambda: monkeypatch.setattr(_media, "_writeback_media_map", real)


def _pending_of(db, pid="P1"):
    return (db["catalog_products"].find_one({"id": pid}) or {})["ecom"].get("media_pending") or []


def test_a_map_writeback_failure_is_loud_and_keeps_the_gid(gates, monkeypatch):
    """The media attached on Shopify but the twin could not record it: ok=False
    (a silent ok=True on an un-recorded create is exactly what let a re-run
    duplicate media), the minted gid kept on the result for reconcile -- and
    the attach's pending row, written before the call, stays for the next
    press to settle. When even that record cannot be written, nothing is
    sent: no record, no attach."""
    fake = _live(monkeypatch, _nodes(1))
    _die_after_the_record(monkeypatch)
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and "write-back failed" in (res.error or "")
    assert res.shopify_id == _m(100), "the orphaned gid rides the audit row"
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}], "the map did not change"
    assert _pending_of(db) == [{"url": NEW, "image_id": "I1"}], "the attach stays on record"
    # ... so the row it was made for is not deletable (a dead worker's lease
    # has expired by now; the gate reads the record, not the lease)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _run(_delete_route(monkeypatch, db)("I1"))
    assert refused.value.status_code == 409 and _row(db, "I1") is not None

    fake = _live(monkeypatch, _nodes(1))
    monkeypatch.setattr(_media, "_writeback_media_map", lambda db, pid, rows, pending=None: False)
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and "nothing was sent" in (res.error or ""), res.error
    assert fake.ops() == ["imsProductMedia"] and fake.listing() == [_m(1)]


def test_an_unfetchable_asset_is_refused_before_the_network(gates, monkeypatch):
    """A relative /uploads path is not a photograph Shopify can fetch (the same
    rule product_photo_urls applies): a skip with the reason, zero calls,
    dark or live."""
    monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", "/uploads/design.jpg")

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and res.action == "skip"
    assert "http(s)" in (res.error or "")
    assert res.payload["media"] == [] and fake.calls == []
    assert shopify_push.image_source_url(img) is None


def test_the_in_app_url_is_one_identity_in_every_helper(gates, monkeypatch):
    """A row stored with the in-app serve path. The product press maps it under
    the PUBLIC_API_BASE_URL-rewritten absolute url (product_photo_urls); the
    design press, the sweep's skip and the pushed/pending counts must key on
    that SAME spelling -- or a media that IS on the listing reads as pending
    and the sweep re-presses it every run forever."""
    from api.routers import online_store_push as router

    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.example.com")
    path = "/api/v1/products/image/abc123"
    absolute = "https://api.example.com" + path
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([path], media_map=[(absolute, _m(1))]))
    img = _image(db, "I1", path)

    assert shopify_push.product_photo_urls(_parent(db)) == [absolute]
    assert shopify_push.image_source_url(img) == absolute
    assert shopify_push.image_media_gid(_parent(db), img) == _m(1)
    assert router._press_plan(db, img) == {"gid": _m(1), "drop": [], "action": "noop"}
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 0}
    res = _run(shopify_push.push_image(db, img))
    assert res.action == "noop" and res.shopify_id == _m(1) and fake.calls == []


def test_a_size_variant_twin_never_answers_noop_off_a_copied_map(gates, monkeypatch):
    """A size-variant twin owns no listing. A twin repair that copied the
    parent's ecom copied its media_map too: the child's press must not answer
    'already on the listing' with the PARENT's gid -- it is refused, zero
    calls, exactly as _resolve_product_gid refuses the twin."""
    fake = _live(monkeypatch, _nodes(1, 5))
    db = _DB()
    twin = _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(5), "I0")], variant_of={"product_id": "P0"})
    _seed(db, twin)
    img = _image(db, "I1", NEW)

    assert shopify_push.is_variant_of(_parent(db)) is True
    assert shopify_push.image_media_gid(_parent(db), img) is None
    res = _run(shopify_push.push_image(db, img))

    assert (res.action, res.ok) == ("skip", False) and fake.calls == []
    assert "not on Shopify" in (res.error or "") and res.shopify_id is None


# ===========================================================================
# Round 2: the gate, the lanes, the alt, the converging map, one predicate
# ===========================================================================


def test_a_product_without_a_photograph_refuses_the_design_press_like_the_product_press(gates, monkeypatch):
    """P1. IMS removed the product's own photos (images=[]); two are still
    mapped and up on the listing. The product press refuses (no_photo) and
    never reaches its delete step -- so must the design press: a refusal with
    the SAME reason, zero calls, the listing and the map untouched. Without
    this gate one design press stripped both own photographs off the listing."""
    fake = _live(monkeypatch, _nodes(1, 2))
    db = _DB()
    _seed(db, _product([], media_map=[(OWN, _m(1)), (OWN2, _m(2))]))
    img = _image(db, "I1", NEW)

    prod = _run(shopify_push.push_product(db, _parent(db), []))
    res = _run(shopify_push.push_image(db, img))

    assert (prod.reason, prod.ok) == ("no_photo", False)
    assert (res.reason, res.ok, res.action, res.mode) == ("no_photo", False, "skip", "BLOCKED")
    assert fake.calls == [], "zero calls"
    assert fake.listing() == [_m(1), _m(2)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": OWN2, "id": _m(2)}]
    assert list(db[TOMB].find({})) == []


def test_a_public_url_drift_refuses_the_design_press_instead_of_stripping_the_listing(gates, monkeypatch):
    """P1, the config-drift shape: the own photo is the in-app serve path,
    mapped under the absolute PUBLIC_API_BASE_URL spelling; the env var is
    unset on this deploy, so product_photo_urls() is empty. The product press
    refuses; so does the design press -- and the images sweep, which presses
    every APPROVED row a press would act on, therefore strips nothing."""
    monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
    path = "/api/v1/products/image/abc123"
    absolute = "https://api.example.com" + path
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([path], media_map=[(absolute, _m(1))]))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert (res.action, res.reason, res.ok) == ("skip", "no_photo", False)
    assert fake.calls == [] and fake.listing() == [_m(1)]
    assert _map_of(db) == [{"url": absolute, "id": _m(1)}]


def test_the_design_press_never_governs_the_products_own_photographs(gates, monkeypatch):
    """The lanes (P1's root). The product has a second photo the product press
    has not put up yet (OWN2, unmapped) and a photo IMS removed whose media is
    still up (OWN3, mapped, off the product). The design press attaches ONLY
    its own asset: OWN2 is not attached and OWN3 is not dropped -- both are
    the product press's calls, under its own gate -- and no reorder is sent.
    The map keeps every row it had and gains the design row. The product
    press then does exactly its own lane's work and keeps the design row."""
    own3 = "https://cdn.example.com/rb-removed.jpg"
    fake = _live(monkeypatch, _nodes(1, 3))
    db = _DB()
    _seed(db, _product([OWN, OWN2], media_map=[(OWN, _m(1)), (own3, _m(3))]))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "create"
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"], "no delete, no reorder"
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [NEW], "OWN2 is not this press's"
    assert fake.listing() == [_m(1), _m(3), _m(100)], "OWN3 stays up: the product press drops it"
    assert list(db[TOMB].find({})) == []
    assert _map_of(db) == [
        {"url": OWN, "id": _m(1)},
        {"url": own3, "id": _m(3)},
        {"url": NEW, "id": _m(100), "image_id": "I1"},
    ]

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True, prod.error
    assert prod.photos["attached"] == 1 and prod.photos["deleted"] == 1 and prod.photos["reordered"] is False
    assert fake.listing() == [_m(1), _m(100), _m(101)]
    assert [(s["media_gid"], s["url"]) for s in db[TOMB].find({})] == [(_m(3), own3)]
    assert _map_of(db) == [
        {"url": OWN, "id": _m(1)},
        {"url": OWN2, "id": _m(101)},
        {"url": NEW, "id": _m(100), "image_id": "I1"},
    ]


def test_the_rows_alt_text_reaches_shopify(gates, monkeypatch):
    """P2. The designer's alt text on the row is what productCreateMedia
    carries -- the same alt the audit payload records. (The product's own
    photographs keep alt '': match_media_to_photos relies on that.)"""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW, alt_text="Ray-Ban RB2140 front view")

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert att["variables"]["media"] == [
        {"originalSource": NEW, "alt": "Ray-Ban RB2140 front view", "mediaContentType": "IMAGE"}
    ]
    assert res.payload["media"][0]["alt"] == "Ray-Ban RB2140 front view"


def test_a_design_press_of_an_own_photograph_leaves_it_in_the_products_lane(gates, monkeypatch):
    """P3. The design row's url IS one of the product's own photographs (B),
    pressed before the product press put B up. The press attaches B but
    stamps NO image_id: B is the product's lane whichever door attached it,
    so when the operator removes B from the product the product press takes
    it down -- 'removing a photo updates Shopify' (sync-audit gap #3) does
    not depend on which door pressed first."""
    b = "https://cdn.example.com/rb-b.jpg"
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN, b], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", b)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": b, "id": _m(100)}], "no image_id: the product's lane"
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok and prod.photos["attached"] == 0 and prod.photos["deleted"] == 0, "its own, already up"

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN]}})
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok and prod.photos["deleted"] == 1
    assert fake.listing() == [_m(1)] and _map_of(db) == [{"url": OWN, "id": _m(1)}]
    assert [(s["media_gid"], s["url"]) for s in db[TOMB].find({})] == [(_m(100), b)]


def test_a_replaced_asset_already_gone_from_shopify_is_pruned_by_the_press(gates, monkeypatch):
    """P4. The row's old asset is still mapped for deletion but its media is
    already off the listing (the delete landed and the response was lost, or
    an admin removed it). The press reads, finds nothing to attach or drop,
    and STILL writes the map -- the dead row is pruned -- so the next press
    is a no-op with zero calls and the counts stop calling the row pending."""
    from api.routers import online_store_push as router

    fake = _live(monkeypatch, _nodes(1, 100))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(100), "I1"), (OLD, _m(2), "I1")]))
    img = _image(db, "I1", NEW)
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 1}

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "update" and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia"], "a read, no mutation"
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    n = len(fake.calls)
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop" and len(fake.calls) == n
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 0}


def test_a_design_media_deleted_in_the_admin_comes_back_after_the_product_press(gates, monkeypatch):
    """P6. An admin deleted the design media in the Shopify admin. The map
    still holds it, so the design press answers no-op (it reads nothing: the
    documented ceiling). The product press ALWAYS writes the map from what
    the listing carries, even when it attached and dropped nothing, so it
    prunes the dead row -- and the next design press puts the image back."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(50), "I1")]))  # 50 is gone
    img = _image(db, "I1", NEW)

    assert _run(shopify_push.push_image(db, img)).action == "noop" and fake.calls == []
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and not [o for o in fake.ops() if o.endswith("Media")]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}], "the dead row is pruned by a no-change press"

    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.action == "create" and again.shopify_id == _m(100)
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]


def test_the_press_the_sweep_and_the_counts_read_one_predicate(gates, monkeypatch):
    """P5. The failed-delete state: NEW is mapped and up, OLD still mapped for
    deletion. One predicate (image_press_plan) answers everyone: the press
    runs ('update', drop OLD), the sweep's skip (router._press_plan) says
    the same, the counts call the row pending -- and pushed, since NEW IS on
    the listing. After the press all three say no-op / not pending."""
    from api.routers import online_store_push as router

    _live(monkeypatch, _nodes(1, 2, 100))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(100), "I1"), (OLD, _m(2), "I1")]))
    img = _image(db, "I1", NEW)

    plan = shopify_push.image_press_plan(_parent(db), img)
    assert plan == {"gid": _m(100), "drop": [OLD], "action": "update"}
    assert router._press_plan(db, img) == plan
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 1}
    res = _run(shopify_push.push_image(db, img))
    assert res.ok and res.action == "update" and res.payload["drop"] == [OLD]
    assert router._press_plan(db, _row(db, "I1")) == {"gid": _m(100), "drop": [], "action": "noop"}
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 0}


# ===========================================================================
# Round 4: the lane is decided by url, the map merges by lane, refusals are
# part of the one predicate
# ===========================================================================


def test_a_design_asset_promoted_to_a_product_photo_is_never_deleted_by_its_old_rows_press(gates, monkeypatch):
    """P1. OLD was pressed through row I1 (stamped I1 on the map), then the
    operator made it one of the product's own photographs, and the designer
    replaced I1's asset with NEW. A url that is one of the product's own
    photographs is the product's lane whichever door attached it -- decided
    by url on every read of the map -- so I1's press attaches NEW and deletes
    NOTHING: OLD is a photograph the product lists and IMS never removed. The
    map stores OLD in the product's lane from then on, and the product press
    behind it has nothing to do."""
    fake = _live(monkeypatch, _nodes(1, 2))
    db = _DB()
    _seed(db, _product([OWN, OLD], media_map=[(OWN, _m(1)), (OLD, _m(2), "I1")]))
    img = _image(db, "I1", OLD, edited_url=NEW)

    assert shopify_push.image_press_plan(_parent(db), img) == {"gid": None, "drop": [], "action": "create"}
    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "create" and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"], "no delete"
    assert list(db[TOMB].find({})) == []
    assert fake.listing() == [_m(1), _m(2), _m(100)]
    assert _map_of(db) == [
        {"url": OWN, "id": _m(1)},
        {"url": OLD, "id": _m(2)},
        {"url": NEW, "id": _m(100), "image_id": "I1"},
    ]
    n = len(fake.calls)
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and not [o for o in fake.ops()[n:] if o.endswith("Media")], fake.ops()[n:]
    assert fake.listing() == [_m(1), _m(2), _m(100)]


def test_a_product_press_on_a_stale_snapshot_keeps_the_design_row_written_since(gates, monkeypatch):
    """P2, door A over door B. The 01:00/09:00 sweep loads its docs up front;
    a human presses a design image inside that window. The product press
    then writes the map from a snapshot that never held the design row: it
    must keep that row (the other lane is taken from the twin at write time),
    or the next design press attaches the image a second time and the first
    copy is unmanaged forever."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    stale = _parent(db)
    assert _run(shopify_push.push_image(db, _image(db, "I1", NEW))).shopify_id == _m(100)

    prod = _run(shopify_push.push_product(db, stale, []))

    assert prod.ok is True, prod.error
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    n = len(fake.calls)
    again = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert again.action == "noop" and len(fake.calls) == n
    assert fake.listing() == [_m(1), _m(100)], "one copy"


def test_a_design_press_on_a_stale_parent_keeps_the_product_photo_attached_since(gates, monkeypatch):
    """P2, door B over door A. The design press read the parent; before it
    writes the map a product press puts OWN2 up and maps it. The design
    press's write must keep OWN2's row, or the next product press attaches
    OWN2 a second time."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)

    def _product_press_lands():
        fake.media_nodes += _nodes(50)
        ecom = dict(_parent(db)["ecom"], media_map=[{"url": OWN, "id": _m(1)}, {"url": OWN2, "id": _m(50)}])
        db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2], "ecom": ecom}})

    fake.on_read = _product_press_lands
    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    assert _map_of(db) == [
        {"url": OWN, "id": _m(1)},
        {"url": OWN2, "id": _m(50)},
        {"url": NEW, "id": _m(100), "image_id": "I1"},
    ]
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and prod.photos["attached"] == 0, prod.photos
    assert fake.listing() == [_m(1), _m(50), _m(100)], "OWN2 once"


def test_the_refusals_are_the_plans_answer_and_the_press_sends_nothing(gates, monkeypatch):
    """P4. Every refusal the press makes before it sends anything is part of
    image_press_plan's answer ('skip' + reason), so the press, the sweep's
    skip and the counts agree: none of these rows reads 'create' while the
    LIVE press refuses it with zero network."""
    from api.services import policy_engine

    monkeypatch.setattr(
        policy_engine, "get_policy",
        lambda key, default=None: {"brands": ["Cartier"]} if key == "ecom.shopify_push_locks" else default,
    )
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _seed(db, _product([OWN], pid="P2", shopify_product_id=None))
    _seed(db, _product([], pid="P3"))
    _seed(db, dict(_product([OWN], pid="P4"), brand="Cartier"))
    rows = {
        "I1": ("P1", "/uploads/design.jpg", "no_url"),
        "I2": ("P2", NEW, "not_on_shopify"),
        "I3": ("P3", NEW, "no_photo"),
        "I4": ("P4", NEW, "push_locked"),
    }
    for iid, (pid, url, _r) in rows.items():
        img = _image(db, iid, url, product_id=pid)
        lock = shopify_push.push_lock_reason(db, "product", _parent(db, pid))
        plan = shopify_push.image_press_plan(_parent(db, pid), img, lock=lock)
        assert (plan["action"], plan["reason"]) == ("skip", rows[iid][2]), iid
        res = _run(shopify_push.push_image(db, img))
        assert (res.action, res.ok) == ("skip", False), (iid, res)
    assert fake.calls == [], "zero network"


# ===========================================================================
# Round 5: one pass per product (the lease), the map merged by id, the row
# pressed as it is now, a shared image handed over, a lost attach adopted,
# the delete gate closed
# ===========================================================================


def _delete_route(monkeypatch, db):
    """DELETE /online-store/images/{id} bound to this test's db."""
    from api.routers import online_store_images as images

    monkeypatch.setattr(images, "_get_db", lambda: db)
    return lambda iid: images.delete_image(iid, current_user={})


def test_a_row_whose_lane_flips_mid_pass_is_never_dropped_from_the_map(gates, monkeypatch):
    """P1 (lane re-derived at write time). The product press plans while OLD
    is a design media (stamped I1); while its attach is in flight the
    operator makes OLD one of the product's photographs. The write keeps
    every stored row BY ID -- a lane worked out again at write time put OLD
    in neither list and dropped it, and the next press attached OLD a
    second time over a media no door governs any more.
    REVERT-PROOF: _merge by lane (mine from the plan, theirs from the write
    read) -> red."""
    fake = _live(monkeypatch, _nodes(1, 2))
    db = _DB()
    _seed(db, _product([OWN, OWN2], media_map=[(OWN, _m(1)), (OLD, _m(2), "I1")]))

    def _promote():
        db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2, OLD]}})

    fake.before["imsProductCreateMedia"] = _promote
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True, prod.error
    assert fake.listing() == [_m(1), _m(2), _m(100)]
    assert sorted(r["id"] for r in _map_of(db)) == [_m(1), _m(100), _m(2)], "OLD stays IMS's"
    again = _run(shopify_push.push_product(db, _parent(db), []))
    assert again.ok is True and again.photos["attached"] == 0, again.photos
    assert fake.listing() == [_m(1), _m(2), _m(100)], "OLD once"


def test_two_presses_on_one_product_run_one_at_a_time(gates, monkeypatch):
    """P1/P2 (interleaved presses). A double click reaching two workers, the
    images sweep beside a human press, the 01:00/09:00 sweep beside a human
    press: every pair planned on one listing and read the other's half-done
    attach as foreign media -> a duplicate on the storefront and an orphan
    no door governs. Under the product's media lease the second press waits
    and then reads a listing and a map that agree.
    REVERT-PROOF: media_lease that yields without claiming -> red."""
    # (H) two presses of ONE row
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _image(db, "I1", NEW)

    async def _twice():
        return await asyncio.gather(
            shopify_push.push_image(db, _row(db, "I1")), shopify_push.push_image(db, _row(db, "I1"))
        )

    a, b = _run(_twice())
    assert sorted([a.action, b.action]) == ["create", "noop"] and a.ok and b.ok
    assert len(fake.calls_of("imsProductCreateMedia")) == 1
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]

    # (C) the sweep's product press (a doc loaded before OWN2 was added) and a
    # human design press of OWN2 -- an own photograph -- on the same product
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    stale = _parent(db)
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2]}})
    _image(db, "I1", OWN2)

    async def _both():
        return await asyncio.gather(
            shopify_push.push_product(db, stale, []), shopify_push.push_image(db, _row(db, "I1"))
        )

    prod, img = _run(_both())
    assert prod.ok is True and img.ok is True, (prod.error, img.error)
    attached = [m["originalSource"] for c in fake.calls_of("imsProductCreateMedia") for m in c["variables"]["media"]]
    assert attached == [OWN2], "OWN2 attached once"
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": OWN2, "id": _m(100)}]


def test_a_row_delete_waits_for_a_press_of_its_product(gates, monkeypatch):
    """P4 (a delete inside a press's window). The press read the listing and
    is attaching; the delete's lane check still read empty, so the row went
    and the media it pressed stayed up with no door able to take it down.
    The delete runs under the same lease: it waits, sees the lane, 409.
    REVERT-PROOF: media_lease that yields without claiming -> red."""
    from fastapi import HTTPException

    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _image(db, "I1", NEW)

    async def _both():
        return await asyncio.gather(
            shopify_push.push_image(db, _row(db, "I1")), delete("I1"), return_exceptions=True
        )

    press, deleted = _run(_both())
    assert press.ok is True and press.shopify_id == _m(100)
    assert isinstance(deleted, HTTPException) and deleted.status_code == 409, deleted
    assert _row(db, "I1") is not None, "the row that governs m100 is kept"
    assert fake.listing() == [_m(1), _m(100)]


def test_a_held_lease_refuses_every_door_and_an_expired_one_is_taken_over(gates, monkeypatch):
    """The lease itself: while another worker holds the product's lease the
    design press, the product press and the row delete all refuse with
    'press again' and send nothing; a lease a dead worker left behind
    expires and the next press takes it over, then releases it."""
    from datetime import datetime, timedelta, timezone
    from fastapi import HTTPException

    monkeypatch.setattr(_media, "_LEASE_WAIT_SECONDS", 0.05)
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _image(db, "I1", NEW)
    leases = db[_media.LEASES_COLLECTION]
    leases.insert_one({"_id": "P1", "token": "other", "until": datetime.now(timezone.utc) + timedelta(minutes=5)})

    img = _run(shopify_push.push_image(db, _row(db, "I1")))
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    with pytest.raises(HTTPException) as busy:
        _run(delete("I1"))

    assert (img.ok, img.action) == (False, "skip") and "press again" in (img.error or "")
    assert prod.ok is False and "press again" in (prod.error or "")
    assert busy.value.status_code == 409 and "press again" in busy.value.detail
    assert fake.calls == [], "nothing sent under another worker's lease"

    leases.update_one({"_id": "P1"}, {"$set": {"until": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    res = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert res.ok is True and res.shopify_id == _m(100)
    assert leases.find_one({"_id": "P1"}) is None, "released"


def test_the_press_reads_the_queue_row_as_it_is_now_not_the_sweeps_copy(gates, monkeypatch):
    """The images sweep loads every row up front and presses each copy. (a) A
    row deleted since (its lane was empty, so the delete was allowed) must
    not be pressed from the copy: that minted a media for a row that no
    longer exists. (b) A row re-pointed and pressed since must not be pressed
    from its old url: that took the new asset down and put the replaced one
    back up.
    REVERT-PROOF: _press_image on the passed copy (no re-read) -> red."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    gone = _image(db, "I2", OLD)
    db["product_images"].delete_one({"image_id": "I2"})

    res = _run(shopify_push.push_image(db, gone))

    assert (res.action, res.ok, res.reason) == ("skip", False, "row_gone") and fake.calls == []

    stale = _image(db, "I1", OLD)
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": NEW}})
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).shopify_id == _m(100)
    n = len(fake.calls)

    res = _run(shopify_push.push_image(db, stale))

    assert res.action == "noop" and res.shopify_id == _m(100) and len(fake.calls) == n
    assert fake.listing() == [_m(1), _m(100)] and list(db[TOMB].find({})) == []
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]


def test_replacing_one_rows_asset_hands_a_shared_image_to_its_sibling(gates, monkeypatch):
    """P2 (two identities for one media). I1 and I2 both source OLD; I1's
    press put it up (m100, stamped I1) and I2's press read it by url (noop,
    Synced). Replacing I1's asset used to delete m100 -- I2's image too. The
    media is HANDED to I2's lane instead, and both rows read no-op after.
    REVERT-PROOF: push_image with heirs={} -> red."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _image(db, "I1", OLD)
    _image(db, "I2", OLD)
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).shopify_id == _m(100)
    assert _run(shopify_push.push_image(db, _row(db, "I2"))).action == "noop"
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"edited_url": NEW}})

    res = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert res.ok is True and res.shopify_id == _m(101)
    assert fake.calls_of("imsProductDeleteMedia") == [] and list(db[TOMB].find({})) == []
    assert fake.listing() == [_m(1), _m(100), _m(101)]
    assert _map_of(db) == [
        {"url": OWN, "id": _m(1)},
        {"url": OLD, "id": _m(100), "image_id": "I2"},
        {"url": NEW, "id": _m(101), "image_id": "I1"},
    ]
    for iid, gid in (("I1", _m(101)), ("I2", _m(100))):
        plan = shopify_push.image_press_plan(_parent(db), _row(db, iid))
        assert (plan["action"], plan["gid"]) == ("noop", gid), iid


def test_an_attach_whose_answer_was_lost_is_adopted_not_attached_again(gates, monkeypatch):
    """A crash or a lost response between the Shopify call and the map write:
    the media is on the listing, the map never heard of it. The next press
    of either door recognises its own attach by the originalSource IMS
    handed over (match_media_to_photos' R1) and MAPS it -- it used to attach
    the url a second time and leave the first copy unmanaged forever.
    REVERT-PROOF: plan_product_media without the adopt pass -> red."""
    from api.services.shopify_push import queries

    fake = _live(monkeypatch, _nodes(1))
    fake.lose_create_once = True
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _image(db, "I1", NEW)

    first = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert first.ok is False and "timeout" in (first.error or "")
    assert fake.listing() == [_m(1), _m(100)] and _map_of(db) == [{"url": OWN, "id": _m(1)}]
    assert _pending_of(db) == [{"url": NEW, "image_id": "I1"}]

    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.shopify_id == _m(100) and again.photos["adopted"] == 1
    assert len(fake.calls_of("imsProductCreateMedia")) == 1, "never attached twice"
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert _pending_of(db) == [], "settled"

    # the product door: the worker dies after the attach, before the map write
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2]}})
    restore = _die_after_the_record(monkeypatch)
    _run(shopify_push.push_product(db, _parent(db), []))
    restore()
    assert fake.listing() == [_m(1), _m(100), _m(101)] and len(_map_of(db)) == 2
    assert _pending_of(db) == [{"url": OWN2}]

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.photos["attached"] == 0 and prod.photos["adopted"] == 1, prod.photos
    assert fake.listing() == [_m(1), _m(100), _m(101)]
    assert {"url": OWN2, "id": _m(101)} in _map_of(db) and _pending_of(db) == []
    # ... which needs the source url on the product press's own read of the listing
    for mutation in (queries._PRODUCT_CREATE, queries._PRODUCT_UPDATE):
        assert "originalSource { url }" in mutation


def test_deleting_a_promoted_rows_queue_entry_stores_the_map_as_it_reads(gates, monkeypatch):
    """P1 (the delete gate read the by-url lane; the stored stamp outlived it).
    I1's media m100 is stamped I1; the operator makes NEW one of the
    product's photos, so the lane reads empty and the delete is allowed --
    but the map still stored the I1 stamp, so when NEW was later removed
    from the product the stamp put m100 back in the (deleted) row's lane and
    no press could take it down. The delete now stores the map as it reads.
    REVERT-PROOF: drop the normalising write in delete_image -> red."""
    from fastapi import HTTPException

    fake = _live(monkeypatch, _nodes(1, 100))
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(100), "I1")]))
    _image(db, "I1", NEW)
    with pytest.raises(HTTPException) as refused:
        _run(delete("I1"))
    assert refused.value.status_code == 409

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, NEW]}})
    assert _run(delete("I1"))["deleted"] is True
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100)}], "the product's lane, stored"

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN]}})
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True and prod.photos["deleted"] == 1, prod.photos
    assert fake.listing() == [_m(1)] and _map_of(db) == [{"url": OWN, "id": _m(1)}]


def test_the_delete_gate_fails_closed_on_a_parent_read_error(gates, monkeypatch):
    """P2 (the gate failed OPEN). A transient error reading the parent read as
    'no parent, empty lane' and the row was deleted while its media was on
    the listing. It is a 503 now and the row stays.
    REVERT-PROOF: the gate on _resolve_product_doc (swallows) -> red."""
    from fastapi import HTTPException

    _live(monkeypatch, _nodes(1, 100))
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (NEW, _m(100), "I1")]))
    _image(db, "I1", NEW)
    parents = db["catalog_products"]
    real_find_one = parents.find_one
    fails = {"left": 1}

    def _flaky(*args, **kwargs):
        if fails["left"]:
            fails["left"] -= 1
            raise RuntimeError("catalog_products read timed out")
        return real_find_one(*args, **kwargs)

    monkeypatch.setattr(parents, "find_one", _flaky)
    with pytest.raises(HTTPException) as failed:
        _run(delete("I1"))

    assert failed.value.status_code == 503
    assert _row(db, "I1") is not None
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]


# ===========================================================================
# Round 6: a lost attach is on record until a press settles it (the delete
# gate honours it); a product blocked from online takes no design image
# ===========================================================================


def _lost_attach(monkeypatch, db):
    """I1 (url NEW) pressed on P1 (listing [m1], map [{OWN, m1}]); the
    productCreateMedia lands as m100 and its answer is lost."""
    fake = _live(monkeypatch, _nodes(1))
    fake.lose_create_once = True
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    _image(db, "I1", NEW)
    first = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert first.ok is False and "timeout" in (first.error or "")
    assert fake.listing() == [_m(1), _m(100)] and _map_of(db) == [{"url": OWN, "id": _m(1)}]
    return fake


def test_a_rows_delete_is_refused_while_its_lost_attach_is_unsettled(gates, monkeypatch):
    """R2 P1 (the delete orphaned a lost attach). The press's productCreateMedia
    reached Shopify but its answer was lost, so the map never heard of m100;
    the delete gate read the map alone, deleted the row -- the only repair
    path -- and m100 stayed on the storefront as unmanaged forever. The
    attach is recorded (media_pending, in I1's lane) BEFORE the call: the
    delete is refused, and the next press of either door maps m100 in I1's
    lane, where the gate sees it as on the listing.
    REVERT-PROOF: image_lane_media without pending_media -> red."""
    from fastapi import HTTPException

    db = _DB()
    delete = _delete_route(monkeypatch, db)
    fake = _lost_attach(monkeypatch, db)
    assert shopify_push.image_press_plan(_parent(db), _row(db, "I1"))["action"] == "create"

    with pytest.raises(HTTPException) as refused:
        _run(delete("I1"))

    assert refused.value.status_code == 409 and "never heard back" in refused.value.detail
    assert _row(db, "I1") is not None

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True, prod.error
    assert (prod.photos["unmanaged"], prod.photos["adopted"], prod.photos["attached"]) == (0, 1, 0)
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100), "image_id": "I1"}]
    assert _pending_of(db) == []
    with pytest.raises(HTTPException) as on_listing:
        _run(delete("I1"))
    assert on_listing.value.status_code == 409 and _m(100) in on_listing.value.detail
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"


def test_a_lost_attach_of_a_row_re_pointed_since_is_taken_down_by_its_press(gates, monkeypatch):
    """The lost attach's row is re-pointed before anything settles it: R1
    adoption only knows the row's url NOW, so m100 (the old url) would never
    be claimed. The pending row names it: the press maps it in I1's lane and,
    since I1 no longer sources it, tombstones and deletes it after the new
    asset lands -- one media on the listing for the row, nothing orphaned.
    REVERT-PROOF: plan_product_media without the pending settle -> red."""
    db = _DB()
    fake = _lost_attach(monkeypatch, db)
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": OTHER}})
    plan = shopify_push.image_press_plan(_parent(db), _row(db, "I1"))
    assert (plan["action"], plan["drop"]) == ("create", [NEW])

    res = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert res.ok is True and res.shopify_id == _m(101), res.error
    assert fake.listing() == [_m(1), _m(101)], "the lost attach taken down"
    assert [t["media_gid"] for t in db[TOMB].find({})] == [_m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": OTHER, "id": _m(101), "image_id": "I1"}]
    assert _pending_of(db) == []


def test_a_product_blocked_from_online_takes_no_design_image(gates, monkeypatch):
    """R2 (the design press wrote to a product the product press refuses). P1
    is on Shopify (delisted to DRAFT, gid kept) and a member of an
    online_sync_blocked collection: push_product refuses it with zero calls,
    and so must the design press -- the one predicate refuses it the same
    way, so the sweep skips it and the counts never call it pending. An
    unreadable block config refuses too (fail closed).
    REVERT-PROOF: image_press_plan without the online-block refusal -> red."""
    from api.routers import online_store_push as router

    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))], status="DRAFT"))
    _image(db, "I1", NEW)
    db["ecom_collections"].insert_one(
        {"collection_id": "C-BAN", "collection_type": "CUSTOM", "online_sync_blocked": True,
         "products": [{"sku": "SKU-1", "position": 0}]}
    )

    prod = _run(shopify_push.push_product(db, _parent(db), []))
    img = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert (prod.action, prod.ok, prod.reason) == ("skip", False, "online_sync_blocked")
    assert (img.action, img.ok, img.reason, img.mode) == ("skip", False, "online_sync_blocked", "BLOCKED")
    assert router._press_plan(db, _row(db, "I1"))["action"] == "skip", "the sweep skips it"
    assert router._image_counts(db) == {"approved": 1, "pushed": 0, "pending": 0}
    assert fake.calls == [], "nothing written on a banned product"

    def _unreadable(*args, **kwargs):
        raise RuntimeError("ecom_collections read timed out")

    monkeypatch.setattr(db["ecom_collections"], "find", _unreadable)
    img = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert (img.action, img.ok, img.reason) == ("skip", False, "block_status_unverifiable")
    assert fake.calls == [] and _map_of(db) == [{"url": OWN, "id": _m(1)}]
