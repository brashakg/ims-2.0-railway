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
media_map maps its source url (media.image_media_gid). The LIVE press runs
the SAME photo pass the product press runs (sync_product_media over
listing_photo_urls = own photos + APPROVED design images, against the media
the listing carries right now). The map is the one writer; the row carries no
Shopify id at all.

Every Shopify call is MOCKED (shopify_push._graphql is monkeypatched to a
transcript fake); no real network request is ever made.

REVERT-PROOF (each test names the one-line revert that turns it red):
  test_a_first_press...          revert the map write (row writer)      -> red
  test_a_repress...              drop the image_media_gid no-op check   -> red
  test_a_replaced_asset...       attach without the pass (old create)   -> red
  test_hand_uploaded...          skip the media read (pass current=[])  -> red
  test_a_listing_ims_owns...     attach blind on hands_off              -> red
  test_door_a_then_owns...       revert the product.py listing union    -> red
  test_dark_press...             read the media before the gate         -> red
  test_a_map_writeback_failure.. trust the pass instead of re-reading   -> red
  test_an_unfetchable_asset...   drop the `src not in photos` guard     -> red

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

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import media as _media  # noqa: E402

TOMB = shopify_push.TOMBSTONES_COLLECTION
GID = "gid://shopify/Product/900"
OWN = "https://cdn.example.com/rb-front.jpg"  # the product's own photograph
OLD = "https://cdn.example.com/design-v1.jpg"  # a design asset pressed earlier
NEW = "https://cdn.example.com/design-v2.jpg"  # the designer's replacement
HAND = "gid://shopify/MediaImage/7"  # a human's upload -- never in the map


def _m(n):
    return "gid://shopify/MediaImage/%d" % n


def _run(coro):
    return asyncio.run(coro)


class _DB:
    def __init__(self):
        self._c = {}

    def __getitem__(self, name):
        return self._c.setdefault(name, MockCollection(name))


class _Shopify:
    """A fake Shopify that routes on the operation and keeps a transcript.
    ``media_nodes`` is what the listing carries before the press; a create
    mints one MediaImage per input (100, 101, ...) and a delete removes them,
    so the fake's listing is what the next read returns."""

    def __init__(self, media_nodes=None):
        self.calls = []
        self.media_nodes = list(media_nodes or [])
        self.next_media = 100

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
        op = self._op(query)
        self.calls.append({"op": op, "variables": copy.deepcopy(variables)})
        variant = {
            "id": "gid://shopify/ProductVariant/901",
            "title": "Default Title",
            "selectedOptions": [],
            "inventoryItem": {"id": "gid://shopify/InventoryItem/902"},
        }
        if op == "imsProductMedia":
            return {
                "data": {
                    "product": {"id": variables["id"], "media": {"nodes": copy.deepcopy(self.media_nodes)}}
                }
            }
        if op == "imsProductCreateMedia":
            out = []
            for _ in variables.get("media") or []:
                gid = _m(self.next_media)
                self.next_media += 1
                out.append({"id": gid, "status": "PROCESSING"})
                self.media_nodes.append({"id": gid, "image": {"url": "https://cdn.shopify.com/%s.jpg" % gid.rsplit("/", 1)[-1]}})
            return {"data": {"productCreateMedia": {"media": out, "mediaUserErrors": []}}}
        if op == "imsProductDeleteMedia":
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


def _product(photos, media_map=None, pid="P1"):
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
        doc["ecom"]["media_map"] = [{"url": u, "id": i} for u, i in media_map]
    return doc


def _seed(db, doc):
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    return copy.deepcopy(doc)


def _image(db, iid, url, **over):
    """An APPROVED design-queue row on P1, stored so listing_photo_urls sees it."""
    doc = {"image_id": iid, "product_id": "P1", "url": url, "status": "APPROVED", "position": 0, **over}
    db["product_images"].insert_one(copy.deepcopy(doc))
    return db["product_images"].find_one({"image_id": iid})


def _map_of(db, pid="P1"):
    return (db["catalog_products"].find_one({"id": pid}) or {})["ecom"].get("media_map")


def _row(db, iid):
    return db["product_images"].find_one({"image_id": iid})


# ===========================================================================
# ONE identity, ONE writer
# ===========================================================================


def test_a_first_press_attaches_once_and_writes_only_the_map(gates, monkeypatch):
    """The press reads the listing, attaches ONLY the new asset, and records
    the gid in the parent's media_map. The row carries no Shopify id."""
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
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100)}]
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
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100)}]


def test_a_replaced_asset_tombstones_deletes_the_old_and_attaches_the_new_once(gates, monkeypatch):
    """The designer replaced the asset on the same row (edited_url OLD -> NEW).
    The press attaches NEW first, tombstones OLD, deletes it -- the SAME
    attach -> delete order and primitives the product press uses -- and the
    map swaps the row. Nothing is attached twice, nothing is lost silently."""
    fake = _live(monkeypatch, _nodes(1, 2))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (OLD, _m(2))]))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia", "imsProductDeleteMedia"]
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [NEW]
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)]
    stones = list(db[TOMB].find({}))
    assert [(s["product_id"], s["media_gid"], s["url"]) for s in stones] == [("P1", _m(2), OLD)]
    assert stones[0]["deleted_at"].tzinfo is not None, "tz-aware UTC"
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100)}]
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 1


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
    24 orphan rows were minted."""
    fake = _live(monkeypatch, _hand())
    db = _DB()
    _seed(db, _product([OWN]))  # no map
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and res.action == "create"
    assert "adopt" in (res.error or "") and "hands off" in (res.error or "")
    assert fake.ops() == ["imsProductMedia"], "a read, never a mutation"
    assert res.photos["hands_off"] is True
    assert fake.listing() == [HAND]
    assert _map_of(db) is None


def test_door_a_then_owns_the_design_media(gates, monkeypatch):
    """After a design press the PRODUCT press sees that media as its own: its
    diff keeps it (no delete, no re-attach), the map survives the press, and
    the design press behind it is a no-op. This is the listing union in
    product.py: without it the product press would tombstone + delete the
    design media and prune its map row on the very next press."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)
    assert _run(shopify_push.push_image(db, img)).ok
    parent = db["catalog_products"].find_one({"id": "P1"})
    n = len(fake.calls)

    res = _run(shopify_push.push_product(db, parent, []))

    assert res.ok is True and res.mode == "LIVE"
    media_ops = [o for o in fake.ops()[n:] if o.endswith("Media")]
    assert media_ops == [], media_ops
    assert res.photos["attached"] == 0 and res.photos["deleted"] == 0 and res.photos["on_shopify"] == 2
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": NEW, "id": _m(100)}]
    # The pure diff door A runs, over the list both doors share:
    plan = shopify_push.plan_product_media(parent, shopify_push.listing_photo_urls(db, parent), fake.media_nodes)
    assert plan["attach"] == [] and plan["delete"] == []
    assert {"url": NEW, "id": _m(100)} in plan["owned"]
    again = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert again.action == "noop" and again.shopify_id == _m(100)


def test_dark_press_makes_zero_network_calls(monkeypatch):
    """Gates closed: an unmapped image is a SIMULATED create carrying the media
    input; a mapped one is a SIMULATED no-op carrying its gid. Neither reads
    nor writes Shopify, and neither touches the map."""
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
    _seed(db, _product([OWN], media_map=[(OWN, _m(1)), (OLD, _m(2))]))

    fresh = _run(shopify_push.push_image(db, _image(db, "I1", NEW)))
    mapped = _run(shopify_push.push_image(db, _image(db, "I2", OLD)))

    assert fresh.mode == "SIMULATED" and fresh.ok and fresh.action == "create"
    assert fresh.payload["media"][0]["originalSource"] == NEW and fresh.shopify_id is None
    assert mapped.mode == "SIMULATED" and mapped.ok and mapped.action == "noop"
    assert mapped.shopify_id == _m(2) and mapped.payload["media_gid"] == _m(2)
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}, {"url": OLD, "id": _m(2)}]


def test_a_map_writeback_failure_is_loud_and_keeps_the_gid(gates, monkeypatch):
    """The media attached on Shopify but the twin could not record it: ok=False
    (a silent ok=True on an un-recorded create is exactly what let a re-run
    duplicate media), the minted gid kept on the result for reconcile."""
    fake = _live(monkeypatch, _nodes(1))
    monkeypatch.setattr(_media, "_writeback_media_map", lambda db, pid, rows: False)
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and "write-back failed" in (res.error or "")
    assert res.shopify_id == _m(100), "the orphaned gid rides the audit row"
    assert fake.listing() == [_m(1), _m(100)]
    assert _map_of(db) == [{"url": OWN, "id": _m(1)}], "the map did not change"


def test_an_unfetchable_asset_is_refused_before_the_network(gates, monkeypatch):
    """A relative /uploads path is not a photograph Shopify can fetch (the same
    rule product_photo_urls applies): a skip with the reason, zero calls."""
    fake = _live(monkeypatch, _nodes(1))
    db = _DB()
    _seed(db, _product([OWN], media_map=[(OWN, _m(1))]))
    img = _image(db, "I1", "/uploads/design.jpg")

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and res.action == "skip"
    assert "http(s)" in (res.error or "")
    assert fake.calls == []
