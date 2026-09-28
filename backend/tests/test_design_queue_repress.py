"""
The design-queue press (shopify_push.push_image) and the product press share
ONE identity for a photograph on Shopify: its doc in the ``online_media``
ledger (shopify_push.media).

THE BUGS THIS LOCKS DOWN
------------------------
Sync audit 2026-09-06, item 10: push_image called productCreateMedia on every
LIVE press; a re-press attached the same url again. Rounds 1-8 then kept the
media state on the twin (``ecom.media_map`` / ``ecom.media_pending``) and
recognised IMS's own attach by ``originalSource.url == the IMS url``. Three
root causes the round-5..8 panels exposed, each pinned below:

  (1) IDENTITY. In production originalSource is Shopify's OWN storage copy
      (scripts/adopt_shopify_media_map.py: measured 2026-09-06, 42 twins, 180
      media), never the url IMS sent -- so every lost-answer repair matched
      nothing and duplicated. The fake here is PRODUCTION-SHAPED: a create
      answers UPLOADED with no image, originalSource is a
      shopify-shop-assets storage url, and the CDN copy (after ``ready()``)
      keeps the source FILE NAME (+ ``_<uuid>`` on a Files collision). A
      pending attach is settled BY FILE NAME (media._settle).
  (2) RETRY. The transport replayed a timed-out productCreateMedia: two media.
      It is sent once now (test_online_store_push covers the transport; T1
      here runs the REAL _graphql through _post_once).
  (3) MANY WRITERS. Whole-doc and whole-ecom writers of the twin wrote the
      map back from stale copies. The state lives in its own collection now
      (T8, T17), so no twin writer can touch it.
  (4) A stale sweep doc re-added a photo the twin no longer had (T10); the
      delete gate normalised live rows but not pending ones (T11).

Every Shopify call is MOCKED (shopify_push._graphql, or _post_once under the
real _graphql); no real network request is ever made.

REVERT-PROOF (each test names the revert that turns it red; the new ones
were run red against it before they counted):
  T1  test_a_lost_answer_is_one_media...      transport retry-all          -> 2 media
                                              hits never match by name     -> 2nd attach
  T3  test_a_node_still_processing...         nameless counted as absent   -> attach
  T4  test_a_pending_attach_is_dropped_...    no grace                     -> attach at 5 min
  T5  test_a_generic_name_is_never_claimed    _ims_unique -> True          -> hand upload deleted
  T6  test_two_hits_hold / ..._one_node       len(ids)==1 / load check off -> claimed
  T7  test_a_naming_drift_holds_every...      canary off                   -> re-attach
  T7b test_the_canary_ignores_adopted_media   canary counts adopted docs   -> held
  T8  test_stale_whole_doc_writers...         (structural: T17)
  T9  test_a_stolen_lease_sends_nothing       renew() -> True              -> 1 send
  T10 test_a_stale_sweep_doc_never...         drop the NO_PHOTO refusal    -> deletes
  T11 test_the_delete_gate_clears_the_stamp.. normalise live docs only     -> red
  T13 test_a_failed_media_is_replaced... /    on_shopify counts FAILED     -> published
      test_a_failed_only_listing_...
  T14 test_the_dark_press_plans...            (read before the gate)       -> red
  T15 test_one_rejected_photo_...             no grace / stop-on-error     -> red
  T17 test_no_media_state_lives_on_the_twin   put a media_map constant back-> red
  old test_the_old_originalsource_identity... (documents the production shape)
  and the ported round 1-6 tests, each with the revert it names.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_design_queue_repress.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402
import copy  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from urllib.parse import urlsplit  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import media as _media  # noqa: E402
from tests.strict_fakes import media_doc  # noqa: E402

TOMB = shopify_push.TOMBSTONES_COLLECTION
LEDGER = shopify_push.MEDIA_COLLECTION
GID = "gid://shopify/Product/900"
OWN = "https://cdn.example.com/rb-front.jpg"  # the product's own photograph
OWN2 = "https://cdn.example.com/rb-side.jpg"  # a second own photograph
OLD = "https://cdn.example.com/design-v1.jpg"  # a design asset pressed earlier
NEW = "https://cdn.example.com/design-v2.jpg"  # the designer's replacement
OTHER = "https://cdn.example.com/design-other.jpg"  # a sibling design row's asset
# IMS-minted names (a uuid4 hex upload key; the in-app uploader's ObjectId):
# the only names a pending attach may be settled by.
U1 = "https://img.example.com/P1/3f2a9c1e5b7d4e6f8a0b1c2d3e4f5a6b.png"
U2 = "https://img.example.com/P1/9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d.png"
OID = "https://api.example.com/api/v1/products/image/65f1a2b3c4d5e6f7a8b9c0d1"
HAND = "gid://shopify/MediaImage/7"  # a human's upload -- never in the ledger
CDN = "https://cdn.shopify.com/s/files/1/0000/0001/files/"
STORAGE = "https://shopify-shop-assets.storage.googleapis.com/s/files/"
_EXT = re.compile(r"\.(jpe?g|png|gif|webp)$", re.I)


def _m(n):
    return "gid://shopify/MediaImage/%d" % n


def _run(coro):
    return asyncio.run(coro)


def _ago(minutes, naive=False):
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return at.replace(tzinfo=None) if naive else at


class _Coll:
    """A MockCollection read with real Mongo's semantics: find_one / find hand
    back COPIES, so a doc a press read is a snapshot a later write does not
    mutate. insert_one refuses a duplicate _id (the media lease is exactly
    that claim; MockCollection would overwrite). Everything else delegates."""

    def __init__(self, name):
        self._m = MockCollection(name)

    def find_one(self, *args, **kwargs):
        return copy.deepcopy(self._m.find_one(*args, **kwargs))

    def find(self, *args, **kwargs):
        return [copy.deepcopy(d) for d in self._m.find(*args, **kwargs)]

    def insert_one(self, doc):
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


def _cdn_name(url):
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return name if _EXT.search(name) else name + ".png"


def _node(i, url=None, status="READY", name=None):
    """A media already on the listing, as the listing query answers it: READY
    with its CDN copy named after ``url`` (a hand upload when url is None)."""
    name = name or (_cdn_name(url) if url else "hand-upload-%d.jpg" % i)
    return {
        "id": _m(i),
        "status": status,
        "image": {"url": CDN + name + "?v=1700000000"} if status == "READY" else None,
    }


class _Shopify:
    """A PRODUCTION-SHAPED fake Shopify that routes on the operation and keeps
    a transcript.
      * productCreateMedia mints one MediaImage per input (100, 101, ...):
        status UPLOADED, NO image, originalSource = Shopify's own storage
        copy (never the url IMS sent); it answers {id, status: UPLOADED}.
      * ready() makes every UPLOADED/PROCESSING node READY with its CDN url:
        the source file name (+ '.png' when it has no image extension), with
        '_<uuid4>' before the extension when that name is already in Files
        (Files keeps a name after its media is deleted).
      * fail(gid) marks a node FAILED; commit_then(exc) lets the NEXT create
        commit its node and then raises exc (the answer is lost); ``reject``
        (a set of urls) answers mediaUserErrors and creates nothing.
      * the listing read (imsProductMedia) answers id/status/image only, in
        insertion order -- what the query selects.
      * ``on_read`` (once) runs just before the next listing read answers;
        ``before[op]`` (once) before any operation.
    Every call AWAITS once, as the real transport does, so two presses
    gathered on one loop interleave exactly as two workers would."""

    def __init__(self, media_nodes=None):
        self.calls = []
        self.media_nodes = [dict(n) for n in media_nodes or []]
        self.files = {self._name(n) for n in self.media_nodes if self._name(n)}
        self.next_media = 100
        self.fail_delete_once = False
        self.reject = set()
        self._commit_then = None
        self.on_read = None
        self.before = {}

    @staticmethod
    def _name(node):
        url = ((node.get("image") or {}).get("url")) or ""
        return urlsplit(url).path.rsplit("/", 1)[-1] if url else None

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

    def commit_then(self, exc):
        self._commit_then = exc

    def ready(self):
        for n in self.media_nodes:
            if n.get("status") in ("UPLOADED", "PROCESSING"):
                name = _cdn_name(n["src"])
                if name in self.files:
                    stem, ext = os.path.splitext(name)
                    name = "%s_%s%s" % (stem, uuid.uuid4(), ext)
                self.files.add(name)
                n["status"] = "READY"
                n["image"] = {"url": CDN + name + "?v=1700000001"}

    def fail(self, gid):
        for n in self.media_nodes:
            if n["id"] == gid:
                n["status"], n["image"] = "FAILED", None

    def node(self, gid):
        return next(n for n in self.media_nodes if n["id"] == gid)

    def ops(self):
        return [c["op"] for c in self.calls]

    def calls_of(self, op):
        return [c for c in self.calls if c["op"] == op]

    def attached(self):
        return [m["originalSource"] for c in self.calls_of("imsProductCreateMedia") for m in c["variables"]["media"]]

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
            nodes = [{"id": n["id"], "status": n.get("status"), "image": copy.deepcopy(n.get("image"))} for n in self.media_nodes]
            return {"data": {"product": {"id": variables["id"], "media": {"nodes": nodes}}}}
        if op == "imsProductCreateMedia":
            out = []
            for m in variables.get("media") or []:
                src = m["originalSource"]
                if src in self.reject:
                    return {
                        "data": {
                            "productCreateMedia": {
                                "media": [],
                                "mediaUserErrors": [{"field": ["media", "0", "originalSource"], "message": "Image URL is invalid"}],
                            }
                        }
                    }
                gid = _m(self.next_media)
                self.next_media += 1
                out.append({"id": gid, "status": "UPLOADED", "mediaErrors": []})
                self.media_nodes.append(
                    {
                        "id": gid,
                        "status": "UPLOADED",
                        "image": None,
                        "src": src,
                        "alt": m.get("alt"),
                        "originalSource": {"url": STORAGE + uuid.uuid4().hex + "/" + urlsplit(src).path.rsplit("/", 1)[-1]},
                    }
                )
            if self._commit_then is not None:
                exc, self._commit_then = self._commit_then, None
                raise exc
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
                        "product": {"id": GID, "handle": "h", "variants": {"nodes": [variant]}},
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


def _wire(monkeypatch, media_nodes=None):
    """The fake behind the REAL _graphql: _post_once answers from it (a raise
    in the fake is a raise of the HTTP call), so the transport's send-once
    rule is in the path."""
    fake = _Shopify(media_nodes)

    async def _post_once(url, headers, payload):
        return httpx.Response(200, json=await fake(None, payload["query"], payload["variables"]))

    monkeypatch.setattr(shopify_push, "_post_once", _post_once)
    return fake


def _hand():
    return [_node(7)]


def _product(photos, pid="P1", **over):
    """A product LIVE on Shopify with the given IMS photo list."""
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
    doc["ecom"].update(over)
    return doc


def _seed(db, doc, *ledger, pid=None):
    """The twin, and the media IMS owns on its listing: (url, n[, image_id])
    live docs (how=minted) -- none = a listing IMS owns nothing on."""
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    for e in ledger:
        _own(db, *e, pid=pid or doc["id"])
    return copy.deepcopy(doc)


def _own(db, url, n, image_id=None, pid="P1", how=None):
    db[LEDGER].insert_one(media_doc(pid, url, _m(n), image_id=image_id, how=how))


def _pend(db, url, image_id=None, pid="P1", minutes=0, naive=False):
    db[LEDGER].insert_one(media_doc(pid, url, None, image_id=image_id, sent_at=_ago(minutes, naive)))


def _image(db, iid, url, **over):
    """An APPROVED design-queue row on P1."""
    doc = {"image_id": iid, "product_id": "P1", "url": url, "status": "APPROVED", "position": 0, **over}
    db["product_images"].insert_one(copy.deepcopy(doc))
    return db["product_images"].find_one({"image_id": iid})


def _docs(db, pid="P1"):
    return [d for d in db[LEDGER].find({}) if d.get("product_id") == pid]


def _ledger(db, pid="P1"):
    """The LIVE docs as {(url, gid, image_id)}."""
    return {(d["url"], d["gid"], d.get("image_id")) for d in _docs(db, pid) if d.get("gid")}


def _pending(db, pid="P1"):
    return {(d["url"], d.get("image_id")) for d in _docs(db, pid) if not d.get("gid")}


def _rows(db, pid="P1"):
    return shopify_push.media_rows(db, pid)


def _row(db, iid):
    return db["product_images"].find_one({"image_id": iid})


def _parent(db, pid="P1"):
    return db["catalog_products"].find_one({"id": pid})


def _plan(db, img, pid="P1"):
    return shopify_push.image_press_plan(_parent(db, pid), img, _rows(db, pid))


def _gid(db, img, pid="P1"):
    return shopify_push.image_media_gid(_parent(db, pid), img, _rows(db, pid))


def _age(db, url, minutes, naive=False, pid="P1"):
    """Push a pending doc's sent_at back -- the clock moving on."""
    db[LEDGER].update_many({"product_id": pid, "url": url}, {"$set": {"sent_at": _ago(minutes, naive)}})


def _delete_route(monkeypatch, db):
    """DELETE /online-store/images/{id} bound to this test's db."""
    from api.routers import online_store_images as images

    monkeypatch.setattr(images, "_get_db", lambda: db)
    return lambda iid: images.delete_image(iid, current_user={})


# ===========================================================================
# ONE identity, ONE writer
# ===========================================================================


def test_a_first_press_attaches_once_and_writes_only_the_ledger(gates, monkeypatch):
    """The press reads the listing, attaches ONLY the new asset and records it
    live in the ledger WITH the row's image_id (the lane marker), minted from
    Shopify's own answer. The row carries no Shopify id; the twin carries no
    media state at all."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.mode == "LIVE" and res.action == "create", res.error
    assert res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"]
    assert fake.attached() == [NEW], "the edited asset, once"
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    assert [d["how"] for d in _docs(db) if d["gid"] == _m(100)] == ["minted"]
    assert _pending(db) == set()
    assert _row(db, "I1").get("shopify_image_id") is None, "the ledger is the ONLY writer"
    assert set(_parent(db)["ecom"]) == {"status", "locally_modified", "shopify_product_id", "shopify_variant_id"}
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 0


def test_a_repress_is_a_noop_with_zero_calls(gates, monkeypatch):
    """The exact 09-06 finding: pressing Publish twice must not mint a second
    MediaImage. The second press reports 'already on the listing' with the
    gid and makes no call at all -- not even the read."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
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


def test_a_replaced_asset_tombstones_deletes_the_old_and_attaches_the_new_once(gates, monkeypatch):
    """The designer replaced the asset on the same row (edited_url OLD -> NEW).
    The press attaches NEW first, tombstones OLD, deletes it, and the ledger
    swaps the doc. Nothing is attached twice, nothing is lost silently."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(2, OLD)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (OLD, 2, "I1"))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "create" and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia", "imsProductDeleteMedia"]
    assert fake.attached() == [NEW]
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)]
    stones = list(db[TOMB].find({}))
    assert [(s["product_id"], s["media_gid"], s["url"]) for s in stones] == [("P1", _m(2), OLD)]
    assert stones[0]["shopify_url"].startswith(CDN) and stones[0]["deleted_at"].tzinfo is not None
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 1


def test_a_failed_delete_keeps_the_old_asset_on_record_until_a_re_press_takes_it_down(gates, monkeypatch):
    """Shopify refuses the delete of the replaced asset. The press is loud
    (ok=False) and the ledger STILL holds OLD -- a doc leaves only after its
    media is off Shopify -- so the re-press runs the pass again, deletes OLD,
    attaches nothing. In between, the product press keeps its hands off both
    design docs.
    REVERT-PROOF: prune the ledger before the delete call -> red."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(2, OLD)])
    fake.fail_delete_once = True
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (OLD, 2, "I1"))
    img = _image(db, "I1", "https://cdn.example.com/raw.jpg", edited_url=NEW)

    first = _run(shopify_push.push_image(db, img))

    assert first.ok is False and "mediaUserErrors" in (first.error or "")
    assert first.shopify_id == _m(100), "the minted gid rides the audit row"
    assert fake.listing() == [_m(1), _m(2), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (OLD, _m(2), "I1"), (NEW, _m(100), "I1")}

    n = len(fake.calls)
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and not [o for o in fake.ops()[n:] if o.endswith("Media") and o != "imsProductMedia"]
    assert fake.listing() == [_m(1), _m(2), _m(100)] and len(_ledger(db)) == 3

    n = len(fake.calls)
    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.action == "update" and again.shopify_id == _m(100)
    assert fake.ops()[n:] == ["imsProductMedia", "imsProductDeleteMedia"], "no second attach"
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"


def test_hand_uploaded_media_is_never_touched_and_own_photos_never_re_attached(gates, monkeypatch):
    """T16. A listing carrying a human's upload (not in the ledger) and the
    product's own photo: the press attaches ONLY the design asset -- the hand
    upload is neither deleted nor moved nor claimed, and the own photo is not
    attached a second time."""
    fake = _live(monkeypatch, [_node(1, OWN)] + _hand())
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"], "no delete, no reorder"
    assert fake.attached() == [NEW]
    assert fake.listing() == [_m(1), HAND, _m(100)]
    assert list(db[TOMB].find({})) == []
    assert res.photos["unmanaged"] == 1
    assert HAND not in {g for _u, g, _i in _ledger(db)}


def test_a_listing_ims_owns_nothing_on_is_refused_not_attached_to_blind(gates, monkeypatch):
    """T16. A pre-ledger listing (hand uploads only): the product press keeps
    its hands off, and so does this one -- a loud refusal pointing at the
    adoption runbook, ZERO calls. The refusal is part of the one predicate:
    the counts do not call the row pending.
    REVERT-PROOF: drop the hands_off branch of image_press_plan -> red."""
    from api.routers import online_store_push as router

    fake = _live(monkeypatch, _hand())
    db = _DB()
    _seed(db, _product([OWN]))  # IMS owns nothing
    img = _image(db, "I1", NEW)

    plan = _plan(db, img)
    assert (plan["action"], plan["reason"]) == ("skip", "hands_off")
    assert router._image_counts(db) == {"approved": 1, "pushed": 0, "pending": 0}
    res = _run(shopify_push.push_image(db, img))

    assert (res.ok, res.action, res.reason) == (False, "skip", "hands_off")
    assert "adopt" in (res.error or "") and "hands off" in (res.error or "")
    assert fake.calls == [], "zero calls, not even the read"
    assert fake.listing() == [HAND] and _docs(db) == []

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.photos["hands_off"] is True and prod.photos["unmanaged"] == 1
    assert fake.attached() == [] and fake.listing() == [HAND]


# ===========================================================================
# The press is exactly the image the human pressed
# ===========================================================================


def test_pressing_one_image_never_attaches_a_sibling_row(gates, monkeypatch):
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", NEW)
    _image(db, "I2", OTHER)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    assert fake.attached() == [NEW]
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    assert _gid(db, _row(db, "I2")) is None, "I2 still pending"


# ===========================================================================
# The product press (and so the scheduled sync) keeps its hands off the lane
# ===========================================================================


def test_the_product_press_never_reads_the_design_queue(gates, monkeypatch):
    """A Mongo timeout on product_images during the 01:00/09:00 sync must not
    be able to turn into a delete plan: the product press does not consult
    the queue at all."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(100, NEW)])
    db = _NoQueueDB()
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 100, "I1"))

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True and res.mode == "LIVE", res.error
    assert fake.attached() == [] and fake.calls_of("imsProductDeleteMedia") == []
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    assert res.photos["deleted"] == 0 and res.photos["on_shopify"] == 2


def test_the_product_press_neither_attaches_nor_drops_design_media(gates, monkeypatch):
    """IMS dropped its own side shot (OWN2) -- that one comes down. The design
    media NEW stays although its queue row no longer exists, and the
    APPROVED-but-unpressed sibling OTHER is NOT attached."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(2, OWN2), _node(100, NEW)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (OWN2, 2), (NEW, 100, "I1"))
    _image(db, "I2", OTHER)

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True, res.error
    assert fake.attached() == [], "no attach without a press"
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)], "only the dropped OWN photo"
    assert [(s["media_gid"], s["url"]) for s in db[TOMB].find({})] == [(_m(2), OWN2)]
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    assert res.photos == {
        "attached": 0, "deleted": 1, "reordered": False, "unmanaged": 0, "adopted": 0,
        "dropped": 0, "held": [], "hands_off": False, "on_shopify": 2, "attached_map": [],
    }


def test_the_product_press_leaves_a_design_media_in_its_slot(gates, monkeypatch):
    """The design media leads the listing (a human put it first in the admin).
    The product press wants its own photos in IMS order -- among themselves:
    the design doc keeps slot 0, no reorder is sent."""
    fake = _live(monkeypatch, [_node(100, NEW), _node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 100, "I1"))
    _image(db, "I1", NEW)

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True and fake.calls_of("imsProductReorderMedia") == [], fake.ops()
    assert res.photos["reordered"] is False and fake.listing() == [_m(100), _m(1)]
    plan = shopify_push.plan_product_media(_rows(db), [OWN], [OWN], fake.media_nodes)
    assert (plan["attach"], plan["delete"], plan["reorder"], plan["hands_off"]) == ([], [], [], False)
    assert {(r["url"], r["id"], r["image_id"]) for r in plan["owned"]} == _ledger(db)
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"


def test_door_a_then_owns_the_design_media(gates, monkeypatch):
    """After a design press the PRODUCT press keeps that media (no delete, no
    re-attach), and the design press behind it is a no-op."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    assert _run(shopify_push.push_image(db, _image(db, "I1", NEW))).ok
    fake.ready()
    n = len(fake.calls)

    res = _run(shopify_push.push_product(db, _parent(db), []))

    assert res.ok is True and res.mode == "LIVE"
    assert [o for o in fake.ops()[n:] if o.endswith("Media")] == ["imsProductMedia"]
    assert res.photos["attached"] == 0 and res.photos["deleted"] == 0 and res.photos["on_shopify"] == 2
    assert res.photos.get("code") is None, "the canary is quiet: the CDN copy carries the url's name"
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    again = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert again.action == "noop" and again.shopify_id == _m(100)


# ===========================================================================
# Dark, loud, and the edges of identity
# ===========================================================================


def test_the_dark_press_plans_with_zero_network_calls(monkeypatch):
    """T14. Gates closed: an unmapped image is a SIMULATED create carrying the
    media input; a mapped one a SIMULATED no-op with its gid; one whose lane
    still holds a replaced asset a SIMULATED update naming the drop. The
    product press's dry run carries its photo plan. None reads or writes
    Shopify (_graphql raises on any call), none touches the ledger."""
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
    _seed(db, _product([OWN, OWN2]), (OWN, 1), (OLD, 2, "I2"), (OTHER, 3, "I3"), (NEW, 4, "I3"))
    before = copy.deepcopy(_docs(db))

    fresh = _run(shopify_push.push_image(db, _image(db, "I1", "https://cdn.example.com/fresh.jpg")))
    mapped = _run(shopify_push.push_image(db, _image(db, "I2", OLD)))
    pending = _run(shopify_push.push_image(db, _image(db, "I3", NEW)))
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert fresh.mode == "SIMULATED" and fresh.ok and fresh.action == "create"
    assert fresh.payload["media"][0]["originalSource"] == "https://cdn.example.com/fresh.jpg"
    assert fresh.shopify_id is None
    assert mapped.mode == "SIMULATED" and mapped.ok and mapped.action == "noop"
    assert mapped.shopify_id == _m(2) and mapped.payload["media_gid"] == _m(2)
    assert pending.mode == "SIMULATED" and pending.ok and pending.action == "update"
    assert pending.shopify_id == _m(4) and pending.payload["drop"] == [OTHER]
    assert prod.mode == "SIMULATED" and prod.ok, prod.error
    assert prod.photos["attach"] == [OWN2] and prod.photos["delete"] == []
    assert _docs(db) == before


def test_a_ledger_write_failure_after_the_attach_is_loud_and_keeps_the_gid(gates, monkeypatch):
    """The media attached on Shopify but its ledger doc could not be turned
    live: ok=False, the minted gid kept on the result -- and the doc,
    recorded BEFORE the call, stays pending for the next press to settle
    (the row's delete is refused meanwhile). When even that record cannot
    be written, nothing is sent: no record, no attach.
    REVERT-PROOF: attach before the pending insert -> the second half red."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", U1)

    def _no_live(*args, **kwargs):
        raise RuntimeError("online_media write timed out")

    db[LEDGER].update_one = _no_live
    res = _run(shopify_push.push_image(db, img))
    del db[LEDGER].update_one

    assert res.ok is False and "write-back failed" in (res.error or ""), res.error
    assert res.shopify_id == _m(100), "the gid rides the audit row"
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None)} and _pending(db) == {(U1, "I1")}
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as refused:
        _run(_delete_route(monkeypatch, db)("I1"))
    assert refused.value.status_code == 409 and _row(db, "I1") is not None
    fake.ready()
    settled = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert settled.ok and settled.shopify_id == _m(100) and settled.photos["adopted"] == 1
    assert len(fake.calls_of("imsProductCreateMedia")) == 1

    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", NEW)
    monkeypatch.setattr(db[LEDGER], "insert_one", _no_live)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and "nothing was sent" in (res.error or ""), res.error
    assert fake.ops() == ["imsProductMedia"] and fake.listing() == [_m(1)]


def test_an_unfetchable_asset_is_refused_before_the_network(gates, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", "/uploads/design.jpg")

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False and res.action == "skip"
    assert "http(s)" in (res.error or "")
    assert res.payload["media"] == [] and fake.calls == []
    assert shopify_push.image_source_url(img) is None


def test_the_in_app_url_is_one_identity_in_every_helper(gates, monkeypatch):
    """A row stored with the in-app serve path. The product press records it
    under the PUBLIC_API_BASE_URL-rewritten absolute url; the design press,
    the sweep's skip and the pushed/pending counts key on that SAME spelling."""
    from api.routers import online_store_push as router

    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.example.com")
    path = "/api/v1/products/image/65f1a2b3c4d5e6f7a8b9c0d1"
    fake = _live(monkeypatch, [_node(1, OID)])
    db = _DB()
    _seed(db, _product([path]), (OID, 1))
    img = _image(db, "I1", path)

    assert shopify_push.product_photo_urls(_parent(db)) == [OID]
    assert shopify_push.image_source_url(img) == OID
    assert _gid(db, img) == _m(1)
    assert router._press_plan(db, img) == {"gid": _m(1), "drop": [], "action": "noop"}
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 0}
    res = _run(shopify_push.push_image(db, img))
    assert res.action == "noop" and res.shopify_id == _m(1) and fake.calls == []


def test_a_size_variant_twin_never_answers_noop_off_a_copied_record(gates, monkeypatch):
    """A size-variant twin owns no listing: even with docs under its id, its
    press is refused with zero calls and no gid."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(5, NEW)])
    db = _DB()
    _seed(db, _product([OWN], variant_of={"product_id": "P0"}), (OWN, 1), (NEW, 5, "I0"))
    img = _image(db, "I1", NEW)

    assert shopify_push.is_variant_of(_parent(db)) is True
    assert _gid(db, img) is None
    res = _run(shopify_push.push_image(db, img))

    assert (res.action, res.ok) == ("skip", False) and fake.calls == []
    assert "not on Shopify" in (res.error or "") and res.shopify_id is None


# ===========================================================================
# Round 2: the gate, the lanes, the alt, one predicate
# ===========================================================================


def test_a_product_without_a_photograph_refuses_the_design_press_like_the_product_press(gates, monkeypatch):
    fake = _live(monkeypatch, [_node(1, OWN), _node(2, OWN2)])
    db = _DB()
    _seed(db, _product([]), (OWN, 1), (OWN2, 2))
    img = _image(db, "I1", NEW)

    prod = _run(shopify_push.push_product(db, _parent(db), []))
    res = _run(shopify_push.push_image(db, img))

    assert (prod.reason, prod.ok) == ("no_photo", False)
    assert (res.reason, res.ok, res.action, res.mode) == ("no_photo", False, "skip", "BLOCKED")
    assert fake.calls == [], "zero calls"
    assert fake.listing() == [_m(1), _m(2)]
    assert _ledger(db) == {(OWN, _m(1), None), (OWN2, _m(2), None)}
    assert list(db[TOMB].find({})) == []


def test_a_public_url_drift_refuses_the_design_press_instead_of_stripping_the_listing(gates, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_BASE_URL", raising=False)
    path = "/api/v1/products/image/65f1a2b3c4d5e6f7a8b9c0d1"
    fake = _live(monkeypatch, [_node(1, OID)])
    db = _DB()
    _seed(db, _product([path]), (OID, 1))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert (res.action, res.reason, res.ok) == ("skip", "no_photo", False)
    assert fake.calls == [] and fake.listing() == [_m(1)]
    assert _ledger(db) == {(OID, _m(1), None)}


def test_the_design_press_never_governs_the_products_own_photographs(gates, monkeypatch):
    """The product has a second photo the product press has not put up yet
    (OWN2) and a photo IMS removed whose media is still up (OWN3). The design
    press attaches ONLY its own asset; the product press then does exactly
    its own lane's work and keeps the design doc."""
    own3 = "https://cdn.example.com/rb-removed.jpg"
    fake = _live(monkeypatch, [_node(1, OWN), _node(3, own3)])
    db = _DB()
    _seed(db, _product([OWN, OWN2]), (OWN, 1), (own3, 3))
    img = _image(db, "I1", NEW)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "create"
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"], "no delete, no reorder"
    assert fake.attached() == [NEW], "OWN2 is not this press's"
    assert fake.listing() == [_m(1), _m(3), _m(100)], "OWN3 stays up: the product press drops it"
    assert list(db[TOMB].find({})) == []
    assert _ledger(db) == {(OWN, _m(1), None), (own3, _m(3), None), (NEW, _m(100), "I1")}

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True, prod.error
    assert prod.photos["attached"] == 1 and prod.photos["deleted"] == 1 and prod.photos["reordered"] is False
    assert fake.listing() == [_m(1), _m(100), _m(101)]
    assert [(s["media_gid"], s["url"]) for s in db[TOMB].find({})] == [(_m(3), own3)]
    assert _ledger(db) == {(OWN, _m(1), None), (OWN2, _m(101), None), (NEW, _m(100), "I1")}


def test_the_rows_alt_text_reaches_shopify(gates, monkeypatch):
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", NEW, alt_text="Ray-Ban RB2140 front view")

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert att["variables"]["media"] == [
        {"originalSource": NEW, "alt": "Ray-Ban RB2140 front view", "mediaContentType": "IMAGE"}
    ]
    assert res.payload["media"][0]["alt"] == "Ray-Ban RB2140 front view"


def test_a_design_press_of_an_own_photograph_leaves_it_in_the_products_lane(gates, monkeypatch):
    """The design row's url IS one of the product's own photographs (B). The
    press attaches B but stamps NO image_id, so when the operator removes B
    from the product the product press takes it down."""
    b = "https://cdn.example.com/rb-b.jpg"
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN, b]), (OWN, 1))
    img = _image(db, "I1", b)

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    assert _ledger(db) == {(OWN, _m(1), None), (b, _m(100), None)}, "no image_id: the product's lane"
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok and prod.photos["attached"] == 0 and prod.photos["deleted"] == 0, "its own, already up"

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN]}})
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok and prod.photos["deleted"] == 1
    assert fake.listing() == [_m(1)] and _ledger(db) == {(OWN, _m(1), None)}
    assert [(s["media_gid"], s["url"]) for s in db[TOMB].find({})] == [(_m(100), b)]


def test_a_replaced_asset_already_gone_from_shopify_is_pruned_by_the_press(gates, monkeypatch):
    """The row's old asset is still on record for deletion but its media is
    already off the listing. The press reads, finds nothing to attach or
    drop, and PRUNES the dead doc -- so the next press is a no-op."""
    from api.routers import online_store_push as router

    fake = _live(monkeypatch, [_node(1, OWN), _node(100, NEW)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 100, "I1"), (OLD, 2, "I1"))
    img = _image(db, "I1", NEW)
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 1}

    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "update" and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia"], "a read, no mutation"
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    n = len(fake.calls)
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop" and len(fake.calls) == n
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 0}


def test_a_design_media_deleted_in_the_admin_comes_back_after_the_product_press(gates, monkeypatch):
    """An admin deleted the design media in the Shopify admin. The design
    press answers no-op off the ledger (the documented ceiling); the product
    press prunes the dead doc from what the listing carries, and the next
    design press puts the image back."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 50, "I1"))  # 50 is gone
    img = _image(db, "I1", NEW)

    assert _run(shopify_push.push_image(db, img)).action == "noop" and fake.calls == []
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and fake.attached() == []
    assert _ledger(db) == {(OWN, _m(1), None)}, "the dead doc is pruned by a no-change press"

    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.action == "create" and again.shopify_id == _m(100)
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}


def test_the_press_the_sweep_and_the_counts_read_one_predicate(gates, monkeypatch):
    from api.routers import online_store_push as router

    _live(monkeypatch, [_node(1, OWN), _node(2, OLD), _node(100, NEW)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 100, "I1"), (OLD, 2, "I1"))
    img = _image(db, "I1", NEW)

    plan = _plan(db, img)
    assert plan == {"gid": _m(100), "drop": [OLD], "action": "update"}
    assert router._press_plan(db, img) == plan
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 1}
    res = _run(shopify_push.push_image(db, img))
    assert res.ok and res.action == "update" and res.payload["drop"] == [OLD]
    assert router._press_plan(db, _row(db, "I1")) == {"gid": _m(100), "drop": [], "action": "noop"}
    assert router._image_counts(db) == {"approved": 1, "pushed": 1, "pending": 0}


# ===========================================================================
# Round 4-6: lane by url, stale copies, the lease, hand-over, the delete gate
# ===========================================================================


def test_a_design_asset_promoted_to_a_product_photo_is_never_deleted_by_its_old_rows_press(gates, monkeypatch):
    """OLD was pressed through row I1, then the operator made it one of the
    product's own photographs, and the designer replaced I1's asset with
    NEW. The lane is decided by url: I1's press attaches NEW and deletes
    NOTHING, and the pass STORES OLD in the product's lane.
    REVERT-PROOF: owned_media without the by-url lane -> red."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(2, OLD)])
    db = _DB()
    _seed(db, _product([OWN, OLD]), (OWN, 1), (OLD, 2, "I1"))
    img = _image(db, "I1", OLD, edited_url=NEW)

    assert _plan(db, img) == {"gid": None, "drop": [], "action": "create"}
    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.action == "create" and res.shopify_id == _m(100)
    assert fake.ops() == ["imsProductMedia", "imsProductCreateMedia"], "no delete"
    assert list(db[TOMB].find({})) == []
    assert _ledger(db) == {(OWN, _m(1), None), (OLD, _m(2), None), (NEW, _m(100), "I1")}
    n = len(fake.calls)
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and fake.ops()[n:].count("imsProductCreateMedia") == 0
    assert fake.calls_of("imsProductDeleteMedia") == []


def test_a_product_press_on_a_stale_snapshot_keeps_the_design_doc_written_since(gates, monkeypatch):
    """The 01:00/09:00 sweep loads its docs up front; a human presses a design
    image inside that window. The product press on the old doc must keep
    that media, or the next design press attaches it a second time."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    stale = _parent(db)
    assert _run(shopify_push.push_image(db, _image(db, "I1", NEW))).shopify_id == _m(100)

    prod = _run(shopify_push.push_product(db, stale, []))

    assert prod.ok is True, prod.error
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}
    n = len(fake.calls)
    again = _run(shopify_push.push_image(db, _row(db, "I1")))
    assert again.action == "noop" and len(fake.calls) == n
    assert fake.listing() == [_m(1), _m(100)], "one copy"


def test_a_design_press_on_a_stale_parent_keeps_the_product_photo_attached_since(gates, monkeypatch):
    """The design press read the parent; before its pass ends a product press
    puts OWN2 up and records it. The design press must not lose that doc, or
    the next product press attaches OWN2 a second time."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", NEW)

    def _product_press_lands():
        fake.media_nodes.append(_node(50, OWN2))
        _own(db, OWN2, 50)
        db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2]}})

    fake.on_read = _product_press_lands
    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True and res.shopify_id == _m(100)
    assert _ledger(db) == {(OWN, _m(1), None), (OWN2, _m(50), None), (NEW, _m(100), "I1")}
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and prod.photos["attached"] == 0, prod.photos
    assert fake.listing() == [_m(1), _m(50), _m(100)], "OWN2 once"


def test_the_refusals_are_the_plans_answer_and_the_press_sends_nothing(gates, monkeypatch):
    from api.services import policy_engine

    monkeypatch.setattr(
        policy_engine, "get_policy",
        lambda key, default=None: {"brands": ["Cartier"]} if key == "ecom.shopify_push_locks" else default,
    )
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
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
        plan = shopify_push.image_press_plan(_parent(db, pid), img, _rows(db, pid), lock=lock)
        assert (plan["action"], plan["reason"]) == ("skip", rows[iid][2]), iid
        res = _run(shopify_push.push_image(db, img))
        assert (res.action, res.ok) == ("skip", False), (iid, res)
    assert fake.calls == [], "zero network"


def test_an_unreadable_ledger_refuses_every_door(gates, monkeypatch):
    """Fail closed: a ledger read error is never 'IMS owns nothing' -- the
    design press refuses (map_unreadable) with zero calls, and the product
    press's pass sends no media mutation and withholds the publish.
    REVERT-PROOF: media_rows swallowing the error (-> []) -> red."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    img = _image(db, "I1", NEW)

    def _down(*args, **kwargs):
        raise RuntimeError("online_media read timed out")

    monkeypatch.setattr(db[LEDGER], "find", _down)
    res = _run(shopify_push.push_image(db, img))
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert (res.action, res.reason) == ("skip", "map_unreadable") and res.ok is False
    assert prod.photos["code"] == "MAP_UNREADABLE" and prod.photos["on_shopify"] == 0
    assert prod.ok is False and prod.reason == "publish_withheld"
    assert fake.attached() == [] and fake.calls_of("imsPublishablePublish") == []


def test_a_row_whose_lane_flips_mid_pass_is_never_dropped(gates, monkeypatch):
    """The product press plans while OLD is a design media (stamped I1);
    while its attach is in flight the operator makes OLD one of the product's
    photographs. OLD's doc stays IMS's, and the next press attaches nothing."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(2, OLD)])
    db = _DB()
    _seed(db, _product([OWN, OWN2]), (OWN, 1), (OLD, 2, "I1"))

    def _promote():
        db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2, OLD]}})

    fake.before["imsProductCreateMedia"] = _promote
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True, prod.error
    assert fake.listing() == [_m(1), _m(2), _m(100)]
    assert {g for _u, g, _i in _ledger(db)} == {_m(1), _m(2), _m(100)}, "OLD stays IMS's"
    again = _run(shopify_push.push_product(db, _parent(db), []))
    assert again.ok is True and again.photos["attached"] == 0, again.photos
    assert fake.listing() == [_m(1), _m(2), _m(100)], "OLD once"


def test_two_presses_on_one_product_run_one_at_a_time(gates, monkeypatch):
    """Under the product's media lease the second press waits and then reads
    a listing and a ledger that agree.
    REVERT-PROOF: media_lease that yields without claiming -> red."""
    # (H) two presses of ONE row
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    _image(db, "I1", NEW)

    async def _twice():
        return await asyncio.gather(
            shopify_push.push_image(db, _row(db, "I1")), shopify_push.push_image(db, _row(db, "I1"))
        )

    a, b = _run(_twice())
    assert sorted([a.action, b.action]) == ["create", "noop"] and a.ok and b.ok
    assert len(fake.calls_of("imsProductCreateMedia")) == 1
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}

    # (C) the sweep's product press (a doc loaded before OWN2 was added) and a
    # human design press of OWN2 -- an own photograph -- on the same product
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    stale = _parent(db)
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, OWN2]}})
    _image(db, "I1", OWN2)

    async def _both():
        return await asyncio.gather(
            shopify_push.push_product(db, stale, []), shopify_push.push_image(db, _row(db, "I1"))
        )

    prod, img = _run(_both())
    assert prod.ok is True and img.ok is True, (prod.error, img.error)
    assert fake.attached() == [OWN2], "OWN2 attached once"
    assert fake.listing() == [_m(1), _m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (OWN2, _m(100), None)}


def test_a_row_delete_waits_for_a_press_of_its_product(gates, monkeypatch):
    """REVERT-PROOF: media_lease that yields without claiming -> red."""
    from fastapi import HTTPException

    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN]), (OWN, 1))
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
    from fastapi import HTTPException

    monkeypatch.setattr(_media, "_LEASE_WAIT_SECONDS", 0.05)
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN]), (OWN, 1))
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


def test_a_stolen_lease_sends_nothing(gates, monkeypatch):
    """T9. Another worker took the lease over while this pass was reading
    the listing (its lease had run out). The pass renews before every
    attach, finds it lost, and sends NOTHING.
    REVERT-PROOF: renew() -> True -> 1 send."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    _image(db, "I1", NEW)

    def _thief():
        db[_media.LEASES_COLLECTION].update_one({"_id": "P1"}, {"$set": {"token": "another-worker"}})

    fake.on_read = _thief
    res = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert res.ok is False and "lost the media lease" in (res.error or ""), res.error
    assert fake.calls_of("imsProductCreateMedia") == [] and _docs(db) and _pending(db) == set()


def test_the_press_reads_the_queue_row_as_it_is_now_not_the_sweeps_copy(gates, monkeypatch):
    """REVERT-PROOF: _press_image on the passed copy (no re-read) -> red."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
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


def test_replacing_one_rows_asset_hands_a_shared_image_to_its_sibling(gates, monkeypatch):
    """I1 and I2 both source OLD; I1's press put it up (stamped I1). Replacing
    I1's asset hands the media to I2's lane instead of deleting it.
    REVERT-PROOF: push_image with heirs={} -> red."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    _image(db, "I1", OLD)
    _image(db, "I2", OLD)
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).shopify_id == _m(100)
    assert _run(shopify_push.push_image(db, _row(db, "I2"))).action == "noop"
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"edited_url": NEW}})

    res = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert res.ok is True and res.shopify_id == _m(101)
    assert fake.calls_of("imsProductDeleteMedia") == [] and list(db[TOMB].find({})) == []
    assert fake.listing() == [_m(1), _m(100), _m(101)]
    assert _ledger(db) == {(OWN, _m(1), None), (OLD, _m(100), "I2"), (NEW, _m(101), "I1")}
    for iid, gid in (("I1", _m(101)), ("I2", _m(100))):
        plan = _plan(db, _row(db, iid))
        assert (plan["action"], plan["gid"]) == ("noop", gid), iid


def test_the_delete_gate_fails_closed_on_a_parent_or_ledger_read_error(gates, monkeypatch):
    """A transient read error is a 503 and the row stays -- never an empty
    lane. REVERT-PROOF: the gate on _resolve_product_doc (swallows) -> red."""
    from fastapi import HTTPException

    _live(monkeypatch, [_node(1, OWN), _node(100, NEW)])
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 100, "I1"))
    _image(db, "I1", NEW)
    for coll, method in (("catalog_products", "find_one"), (LEDGER, "find")):
        real = getattr(db[coll], method)
        fails = {"left": 1}

        def _flaky(*args, _real=real, _fails=fails, **kwargs):
            if _fails["left"]:
                _fails["left"] -= 1
                raise RuntimeError("read timed out")
            return _real(*args, **kwargs)

        monkeypatch.setattr(db[coll], method, _flaky)
        with pytest.raises(HTTPException) as failed:
            _run(delete("I1"))
        assert failed.value.status_code == 503, coll
        assert _row(db, "I1") is not None
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}


def test_the_delete_gate_refuses_while_the_lane_holds_media_and_clears_the_stamp_after(gates, monkeypatch):
    """T11. (a) A PENDING doc in the row's lane (an attach that never heard
    back) refuses the delete (409), as does a live one. (b) A row whose url
    became one of the product's photographs holds nothing in its lane and
    deletes -- and EVERY doc still stamped with its image_id, pending AND
    live, is stored in the product's lane first, so no later read can put
    that media back in a lane whose row is gone.
    REVERT-PROOF: clear the stamp on live docs only -> red."""
    from fastapi import HTTPException

    _live(monkeypatch, [_node(1, OWN), _node(100, NEW)])
    db = _DB()
    delete = _delete_route(monkeypatch, db)
    _seed(db, _product([OWN]), (OWN, 1), (NEW, 100, "I1"))
    _pend(db, U1, "I1")
    _image(db, "I1", NEW)

    with pytest.raises(HTTPException) as refused:
        _run(delete("I1"))
    assert refused.value.status_code == 409 and "never heard back" in refused.value.detail
    db[LEDGER].delete_many({"gid": None})
    with pytest.raises(HTTPException) as on_listing:
        _run(delete("I1"))
    assert on_listing.value.status_code == 409 and _m(100) in on_listing.value.detail

    _pend(db, U1, "I1")
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN, NEW, U1]}})
    assert _run(delete("I1"))["deleted"] is True
    assert {(d["url"], d.get("image_id")) for d in _docs(db)} == {(OWN, None), (NEW, None), (U1, None)}

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN]}})
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    assert prod.ok is True and prod.photos["deleted"] == 1, prod.photos


def test_a_product_blocked_from_online_takes_no_design_image(gates, monkeypatch):
    """REVERT-PROOF: image_press_plan without the online-block refusal -> red."""
    from api.routers import online_store_push as router

    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN], status="DRAFT"), (OWN, 1))
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
    assert fake.calls == [] and _ledger(db) == {(OWN, _m(1), None)}


# ===========================================================================
# ROOT CAUSE 1+2: identity by file name, one send -- the lost answer
# ===========================================================================


def test_the_old_originalsource_identity_matches_nothing_on_a_production_shaped_listing(gates, monkeypatch):
    """What production Shopify does with an attach (measured 2026-09-06): the
    media's originalSource is Shopify's OWN storage copy, never the url IMS
    sent, and the listing query does not even carry it now. An identity keyed
    on originalSource == the IMS url (the round 1-8 settle) recognises 0 of
    IMS's own attaches; the CDN copy's FILE NAME recognises all of them."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    _image(db, "I1", U1)
    _image(db, "I2", OID)
    for iid in ("I1", "I2"):
        assert _run(shopify_push.push_image(db, _row(db, iid))).ok
    fake.ready()

    mine = [fake.node(_m(100)), fake.node(_m(101))]
    assert [n["originalSource"]["url"].startswith(STORAGE) for n in mine] == [True, True]
    by_source = [n for n in mine if n["originalSource"]["url"] in (U1, OID)]
    assert by_source == [], "the old IMS-url assumption: nothing matches"
    listing = _run(_media._product_media(db, GID))
    assert all("originalSource" not in n for n in listing)
    by_name = shopify_push.match_media_to_photos([U1, OID], listing)
    assert by_name["map"] == [{"url": U1, "id": _m(100)}, {"url": OID, "id": _m(101)}]
    for q in (shopify_push.queries._PRODUCT_CREATE, shopify_push.queries._PRODUCT_UPDATE,
              shopify_push.queries._PRODUCT_MEDIA_QUERY):
        assert "originalSource" not in q


def test_a_lost_answer_is_one_media_and_the_next_press_settles_it_by_name(gates, monkeypatch):
    """T1, END TO END through the REAL transport: productCreateMedia commits on
    Shopify, then the answer is lost (ReadTimeout after the send). Exactly ONE
    POST and ONE media -- the transport never replays a create -- and the
    doc, recorded before the send, stays pending. Once the media is READY
    (and even long after the 15-minute grace) the next press recognises it by
    its CDN file name and records it live (how=settled): no second attach.
    REVERT-PROOF: (a) _replay_safe -> True: 2 POSTs, 2 media. (b) the settle
    never matching a name (the originalSource-era identity): the old pending
    doc is dropped and the url attached a second time."""
    fake = _wire(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    _image(db, "I1", U1)
    fake.commit_then(httpx.ReadTimeout("read timed out"))

    first = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert first.ok is False and "not retried" in (first.error or ""), first.error
    assert len(fake.calls_of("imsProductCreateMedia")) == 1, "one POST"
    assert fake.listing() == [_m(1), _m(100)], "one media"
    assert _pending(db) == {(U1, "I1")} and _ledger(db) == {(OWN, _m(1), None)}

    fake.ready()
    _age(db, U1, 60)
    again = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert again.ok is True and again.shopify_id == _m(100), again.error
    assert again.photos["adopted"] == 1 and again.photos["attached"] == 0
    assert len(fake.calls_of("imsProductCreateMedia")) == 1, "never attached twice"
    assert _ledger(db) == {(OWN, _m(1), None), (U1, _m(100), "I1")} and _pending(db) == set()
    assert [d["how"] for d in _docs(db) if d["gid"] == _m(100)] == ["settled"]
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).action == "noop"


def test_a_lost_attach_of_the_product_lane_settles_on_the_product_press(gates, monkeypatch):
    """The product door: a lost answer on the product press, a READY media
    carrying the uploader's ObjectId name (Shopify added the .png), settled
    by the next product press -- attached once, published."""
    monkeypatch.setenv("PUBLIC_API_BASE_URL", "https://api.example.com")
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN, "/api/v1/products/image/65f1a2b3c4d5e6f7a8b9c0d1"]), (OWN, 1))
    fake.commit_then(RuntimeError("shopify imsProductCreateMedia timed out after sending; not retried"))

    first = _run(shopify_push.push_product(db, _parent(db), []))
    assert first.photos["error"] and _pending(db) == {(OID, None)}

    fake.ready()
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True and prod.photos["adopted"] == 1 and prod.photos["attached"] == 0, prod.photos
    assert fake.attached() == [OID] and fake.node(_m(100))["image"]["url"].split("/")[-1].startswith(
        "65f1a2b3c4d5e6f7a8b9c0d1.png"
    )
    assert _ledger(db) == {(OWN, _m(1), None), (OID, _m(100), None)}


def test_a_lost_attach_of_a_row_re_pointed_since_is_taken_down_by_its_press(gates, monkeypatch):
    """The lost attach's row is re-pointed before anything settles it. The
    pending doc names the old url: the press settles it in I1's lane by name
    and, since I1 no longer sources it, tombstones and deletes it after the
    new asset lands -- one media on the listing for the row."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    _image(db, "I1", U1)
    fake.commit_then(RuntimeError("lost"))
    assert _run(shopify_push.push_image(db, _row(db, "I1"))).ok is False
    fake.ready()
    db["product_images"].update_one({"image_id": "I1"}, {"$set": {"url": U2}})
    plan = _plan(db, _row(db, "I1"))
    assert (plan["action"], plan["drop"]) == ("create", [U1])

    res = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert res.ok is True and res.shopify_id == _m(101), res.error
    assert fake.listing() == [_m(1), _m(101)], "the lost attach taken down"
    assert [t["media_gid"] for t in db[TOMB].find({})] == [_m(100)]
    assert _ledger(db) == {(OWN, _m(1), None), (U2, _m(101), "I1")} and _pending(db) == set()


def test_a_node_still_processing_holds_an_old_pending_attach(gates, monkeypatch):
    """T3. A pending doc 20 minutes old, no READY node carries its name -- but
    an image-less node is still PROCESSING on the listing: it may be that
    very attach. Held: nothing attached, reported MEDIA_SETTLING.
    REVERT-PROOF: count an image-less node as absent -> the url is attached."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(9, status="PROCESSING")])
    db = _DB()
    _seed(db, _product([OWN, U1]), (OWN, 1))
    _pend(db, U1, minutes=20)

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert fake.attached() == [], "held, not attached again"
    assert prod.photos["held"] == [U1] and prod.photos["code"] == "MEDIA_SETTLING"
    assert _pending(db) == {(U1, None)}


def test_a_pending_attach_is_dropped_only_after_the_grace(gates, monkeypatch):
    """T4. Nothing on the listing carries the name and nothing is processing:
    at 5 minutes the attach may still land -- held; at 20 minutes (a NAIVE
    sent_at, as pymongo hands it back) it never landed -- dropped, and the
    url attached exactly once.
    REVERT-PROOF: no grace (young always False) -> an attach at 5 minutes."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN, U1]), (OWN, 1))
    _pend(db, U1, minutes=5)

    early = _run(shopify_push.push_product(db, _parent(db), []))

    assert fake.attached() == [] and early.photos["held"] == [U1]

    _age(db, U1, 20, naive=True)
    late = _run(shopify_push.push_product(db, _parent(db), []))

    assert late.photos["dropped"] == 1 and late.photos["attached"] == 1, late.photos
    assert fake.attached() == [U1]
    assert _ledger(db) == {(OWN, _m(1), None), (U1, _m(100), None)} and _pending(db) == set()


def test_a_generic_name_is_never_claimed(gates, monkeypatch):
    """T5. A pending attach of '.../front.jpg' (a name anyone's upload can
    carry) and a READY hand upload 'front.jpg' on the listing: NOT claimed
    (it may be the human's), nothing attached, MEDIA_SETTLING. When IMS then
    removes that photograph, the hand upload survives -- it was never IMS's.
    REVERT-PROOF: _ims_unique -> True: claimed, then DELETED."""
    front = "https://cdn.example.com/p/front.jpg"
    fake = _live(monkeypatch, [_node(1, OWN), _node(7, name="front.jpg")])
    db = _DB()
    _seed(db, _product([OWN, front]), (OWN, 1))
    _pend(db, front, minutes=60)

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert fake.attached() == [] and prod.photos["code"] == "MEDIA_SETTLING"
    assert prod.photos["held"] == [front] and prod.photos["unmanaged"] == 1

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [OWN]}})
    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert HAND in fake.listing() and fake.calls_of("imsProductDeleteMedia") == []
    assert list(db[TOMB].find({})) == []


def test_two_hits_hold(gates, monkeypatch):
    """T6a. Two READY nodes carry the pending url's name (the second a Files
    collision copy): which is IMS's is a guess, and a guess is never a claim.
    REVERT-PROOF: claim the first hit (drop len(ids) == 1) -> claimed."""
    fake = _live(monkeypatch, [_node(1, OWN), _node(8, U1), _node(9, name="3f2a9c1e5b7d4e6f8a0b1c2d3e4f5a6b_%s.png" % uuid.uuid4())])
    db = _DB()
    _seed(db, _product([OWN, U1]), (OWN, 1))
    _pend(db, U1, minutes=60)

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.photos["held"] == [U1] and prod.photos["adopted"] == 0
    assert fake.attached() == [] and _pending(db) == {(U1, None)}


def test_two_pending_docs_on_one_node_hold(gates, monkeypatch):
    """T6b. Two pending attaches (two hosts, one IMS file name) and ONE READY
    node carrying that name: it can only be one of them -- both held.
    REVERT-PROOF: drop the load check -> the first is claimed."""
    twin = "https://mirror.example.com/x/3f2a9c1e5b7d4e6f8a0b1c2d3e4f5a6b.png"
    fake = _live(monkeypatch, [_node(1, OWN), _node(8, U1)])
    db = _DB()
    _seed(db, _product([OWN, U1, twin]), (OWN, 1))
    _pend(db, U1, minutes=60)
    _pend(db, twin, minutes=60)

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert sorted(prod.photos["held"]) == sorted([U1, twin]) and prod.photos["adopted"] == 0
    assert fake.attached() == [] and len(_pending(db)) == 2


def test_a_naming_drift_holds_every_pending_attach(gates, monkeypatch):
    """T7, the canary. A READY media IMS minted whose CDN name no longer
    carries its url's name means Shopify's naming changed -- a name match is
    then evidence of nothing. An old pending doc with 0 hits is NOT dropped
    (its media may be on the listing under a name IMS cannot recognise):
    held, nothing attached, MEDIA_NAMING_DRIFT.
    REVERT-PROOF: canary off -> dropped and attached again."""
    fake = _live(monkeypatch, [_node(1, name="IMG_0001.jpg"), _node(2, name="IMG_0002.jpg")])
    db = _DB()
    _seed(db, _product([OWN, U1]), (OWN, 1))
    _pend(db, U1, minutes=60)

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.photos["code"] == "MEDIA_NAMING_DRIFT" and prod.photos["held"] == [U1]
    assert fake.attached() == [] and _pending(db) == {(U1, None)}


def test_the_canary_ignores_adopted_media(gates, monkeypatch):
    """T7b. An ADOPTED media legitimately carries another CDN name (the
    connector's '<id>__<nn>__<name>', a replace-mode screenshot): it is no
    evidence that Shopify's naming changed. An old pending doc with no hit
    beside it is dropped and attached once, as usual -- no drift.
    REVERT-PROOF: the canary counting adopted docs -> MEDIA_NAMING_DRIFT, held."""
    fake = _live(monkeypatch, [_node(1, name="900__01__4_1.png")])
    db = _DB()
    _seed(db, _product([OWN, U1]))
    _own(db, OWN, 1, how="adopted")
    _pend(db, U1, minutes=60)

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.photos.get("code") is None and prod.photos["dropped"] == 1, prod.photos
    assert fake.attached() == [U1] and _pending(db) == set()
    assert _ledger(db) == {(OWN, _m(1), None), (U1, _m(100), None)}


# ===========================================================================
# ROOT CAUSE 3 + 4: the twin's writers, the stale sweep doc, FAILED media
# ===========================================================================


def test_stale_whole_doc_writers_cannot_touch_the_media_record(gates, monkeypatch):
    """T8. A catalog save of a doc loaded BEFORE the press, and a whole-ecom
    $set from a stale copy (the stock / discount / delist write-back idiom),
    land after the press. Neither can erase what IMS owns: the record is not
    on the twin. The next press sends no productCreateMedia.
    (The structural guard is T17: no media state constant on the twin.)"""
    from api.routers import catalog

    fake = _live(monkeypatch, [_node(1, OWN)])
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    stale = _parent(db)
    assert _run(shopify_push.push_image(db, _image(db, "I1", NEW))).ok
    assert _run(shopify_push.push_product(db, _parent(db), [])).ok

    catalog._save_catalog_product(copy.deepcopy(stale))
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"ecom": copy.deepcopy(stale["ecom"])}})
    n = len(fake.calls_of("imsProductCreateMedia"))
    prod = _run(shopify_push.push_product(db, _parent(db), []))
    img = _run(shopify_push.push_image(db, _row(db, "I1")))

    assert prod.ok and img.action == "noop"
    assert len(fake.calls_of("imsProductCreateMedia")) == n == 1
    assert _ledger(db) == {(OWN, _m(1), None), (NEW, _m(100), "I1")}


def test_a_stale_sweep_doc_never_re_adds_or_strips_a_photo(gates, monkeypatch):
    """T10. The sweep's doc lists [A, B]; the twin NOW lists nothing: the pass
    plans only on the twin -- zero media mutations, NO_PHOTO, the publish
    withheld (it used to fall back to the sweep's list and re-attach B, or
    with an empty list strip A). With the twin at [A] (A on record) the
    stale B is not attached either.
    REVERT-PROOF: drop the NO_PHOTO refusal -> A deleted off the listing."""
    a, b = OWN, OWN2
    fake = _live(monkeypatch, [_node(1, a)])
    db = _DB()
    _seed(db, _product([a, b]), (a, 1))
    sweep = _parent(db)
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": []}})

    prod = _run(shopify_push.push_product(db, sweep, []))

    assert prod.photos["code"] == "NO_PHOTO" and prod.photos["on_shopify"] == 0
    assert prod.ok is False and prod.reason == "publish_withheld"
    assert [o for o in fake.ops() if o.startswith("imsProduct") and "Media" in o] == []
    assert fake.calls_of("imsPublishablePublish") == [] and fake.listing() == [_m(1)]

    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"images": [a]}})
    prod = _run(shopify_push.push_product(db, sweep, []))

    assert prod.ok is True and fake.attached() == [], "B is not the twin's"
    assert fake.listing() == [_m(1)]


def test_a_failed_media_is_replaced_before_it_is_deleted(gates, monkeypatch):
    """T13a. The media IMS owns for OWN went FAILED (Shopify could not fetch
    the bytes). It is not a photograph: the url is attached again FIRST, then
    the FAILED media is tombstoned and deleted."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    fake.fail(_m(1))
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.ok is True, prod.error
    ops = [o for o in fake.ops() if o in ("imsProductCreateMedia", "imsProductDeleteMedia")]
    assert ops == ["imsProductCreateMedia", "imsProductDeleteMedia"]
    assert fake.calls_of("imsProductDeleteMedia")[0]["variables"]["mediaIds"] == [_m(1)]
    assert fake.listing() == [_m(100)] and _ledger(db) == {(OWN, _m(100), None)}
    assert prod.photos["on_shopify"] == 1


def test_a_failed_only_listing_withholds_the_publish(gates, monkeypatch):
    """T13b. The only media is FAILED and the re-attach is rejected: nothing
    on the listing is a photograph, so the publish is withheld.
    REVERT-PROOF: on_shopify counting FAILED media -> published."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    fake.fail(_m(1))
    fake.reject = {OWN}
    db = _DB()
    _seed(db, _product([OWN]), (OWN, 1))

    prod = _run(shopify_push.push_product(db, _parent(db), []))

    assert prod.photos["on_shopify"] == 0 and "mediaUserErrors" in prod.photos["error"]
    assert prod.ok is False and prod.reason == "publish_withheld"
    assert fake.calls_of("imsPublishablePublish") == []


def test_one_rejected_photo_stays_on_record_and_is_attached_once_after_the_grace(gates, monkeypatch):
    """T15. Two new photographs; Shopify rejects the second (mediaUserErrors).
    The first is live; the second stays pending (recorded before the send)
    and is NOT re-sent on an immediate re-press; after the grace it is
    dropped and attached exactly once.
    REVERT-PROOF: no grace -> re-sent on the immediate re-press."""
    fake = _live(monkeypatch, [_node(1, OWN)])
    fake.reject = {U2}
    db = _DB()
    _seed(db, _product([OWN, U1, U2]), (OWN, 1))

    first = _run(shopify_push.push_product(db, _parent(db), []))

    assert "mediaUserErrors" in first.photos["error"]
    assert _ledger(db) == {(OWN, _m(1), None), (U1, _m(100), None)} and _pending(db) == {(U2, None)}
    fake.reject = set()
    fake.ready()
    again = _run(shopify_push.push_product(db, _parent(db), []))
    assert fake.attached() == [U1, U2], "U2 once (the rejected send), none more yet"
    assert again.photos["held"] == [U2]

    _age(db, U2, 20)
    late = _run(shopify_push.push_product(db, _parent(db), []))

    assert late.photos["dropped"] == 1 and late.photos["attached"] == 1
    assert fake.attached() == [U1, U2, U2]
    assert _ledger(db) == {(OWN, _m(1), None), (U1, _m(100), None), (U2, _m(101), None)}


def test_no_media_state_lives_on_the_twin():
    """T17, structural. The media record has ONE home: the string constant
    'online_media' appears only in shopify_push/media.py, and no code under
    backend/api or scripts names ``media_map`` / ``media_pending`` (the twin
    fields every whole-ecom writer clobbered).
    REVERT-PROOF: any constant 'ecom.media_map' or 'media_pending' -> red."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    bad = re.compile(r"(^|\.)media_(map|pending)$")
    homes, names = set(), []
    for base in (root / "backend" / "api", root / "scripts"):
        for f in base.rglob("*.py"):
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value == "online_media":
                        homes.add(f.relative_to(root).as_posix())
                    if bad.search(node.value):
                        names.append((f.relative_to(root).as_posix(), node.value))
    assert homes == {"backend/api/services/shopify_push/media.py"}, homes
    assert names == [], names
