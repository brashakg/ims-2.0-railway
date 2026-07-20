"""
Regression tests for the Shopify image-push write-back (shopify_push.push_image).

THE BUG THIS LOCKS DOWN
-----------------------
The BVI-migrated product_images are stored with image_id=None (the collection's
primary key / id_field). Before this fix, push_image guarded its write-back with
`if new_gid and iid:` and _writeback_image matched on {"image_id": image_id};
with image_id None the write-back was SKIPPED even though productCreateMedia had
already attached the media on Shopify, and push_image still returned ok=True. On
a re-run the doc (still without shopify_image_id) was re-pushed -> DUPLICATE media
(productCreateMedia is a pure create, never an upsert).

The fix: _writeback_image takes the whole image doc and locates the row by the
natural key (product_id + url) when image_id is null, and push_image FAILS LOUDLY
(ok=False, gid preserved for audit) if the gid still cannot be persisted -- never
a silent success on an un-recorded Shopify create.

Every Shopify call is MOCKED (shopify_push._graphql is monkeypatched); no real
network request is ever made.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_shopify_push_image_writeback.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import shopify_push  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _EngineDB:
    """Minimal in-memory db the engine uses directly (db["x"] subscript)."""

    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, MockCollection(name))


class _SpyGraphQL:
    def __init__(self, response):
        self.calls = []
        self._response = response

    async def __call__(self, db, query, variables):
        self.calls.append({"query": query, "variables": variables})
        return self._response


def _force_live(monkeypatch, graphql_response):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    monkeypatch.setattr(
        shopify_push,
        "resolve_shopify_credentials",
        lambda db, storefront_id="BV": {
            "shop_url": "test.myshopify.com",
            "access_token": "shpat_test",
            "source": "vault",
        },
    )
    spy = _SpyGraphQL(graphql_response)
    monkeypatch.setattr(shopify_push, "_graphql", spy)
    return spy


_MEDIA_OK = {
    "data": {
        "productCreateMedia": {
            "media": [{"id": "gid://shopify/MediaImage/900"}],
            "mediaUserErrors": [],
        }
    }
}


# ---------------------------------------------------------------------------
# Unit: _writeback_image key selection + return contract
# ---------------------------------------------------------------------------


def test_writeback_uses_primary_key_when_image_id_present():
    db = _EngineDB()
    db["product_images"].insert_one(
        {"image_id": "I1", "product_id": "P1", "url": "u", "shopify_image_id": None}
    )
    doc = db["product_images"].find_one({"image_id": "I1"})
    ok = shopify_push._writeback_image(db, doc, "gid://shopify/MediaImage/1")
    assert ok is True
    saved = db["product_images"].find_one({"image_id": "I1"})
    assert saved["shopify_image_id"] == "gid://shopify/MediaImage/1"


def test_writeback_falls_back_to_natural_key_when_image_id_null():
    """The BVI-migrated shape: image_id is None but product_id + url identify the
    row. The write-back must locate + persist via the natural key."""
    db = _EngineDB()
    db["product_images"].insert_one(
        {
            "image_id": None,
            "product_id": "P1",
            "url": "http://cdn/x/rayban.jpg",
            "shopify_image_id": None,
        }
    )
    doc = db["product_images"].find_one({"product_id": "P1"})
    ok = shopify_push._writeback_image(db, doc, "gid://shopify/MediaImage/2")
    assert ok is True
    saved = db["product_images"].find_one({"product_id": "P1"})
    assert saved["shopify_image_id"] == "gid://shopify/MediaImage/2"


def test_writeback_returns_false_when_no_stable_key():
    """No image_id AND no product_id/url -> nothing safe to target -> False (the
    caller then fails loudly instead of writing blindly)."""
    db = _EngineDB()
    ok = shopify_push._writeback_image(
        db, {"image_id": None, "product_id": None, "url": None}, "gid://x/1"
    )
    assert ok is False


def test_writeback_filter_prefers_image_id():
    assert shopify_push._image_writeback_filter(
        {"image_id": "I1", "product_id": "P1", "url": "u"}
    ) == {"image_id": "I1"}
    assert shopify_push._image_writeback_filter(
        {"image_id": None, "product_id": "P1", "url": "u"}
    ) == {"product_id": "P1", "url": "u"}
    assert (
        shopify_push._image_writeback_filter({"image_id": None, "product_id": "P1"})
        is None
    )


# ---------------------------------------------------------------------------
# Integration: push_image LIVE path over the mocked network boundary
# ---------------------------------------------------------------------------


def test_push_image_live_persists_gid_for_migrated_doc_without_image_id(monkeypatch):
    """The exact incident: an APPROVED migrated image with image_id=None whose
    parent is on Shopify. The media attaches AND the gid is now written back via
    the natural key -> a re-run would see shopify_image_id set and NOT re-push."""
    spy = _force_live(monkeypatch, _MEDIA_OK)
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P1", "ecom": {"shopify_product_id": "gid://shopify/Product/111"}}
    )
    db["product_images"].insert_one(
        {
            "image_id": None,  # BVI-migrated: primary key is null
            "product_id": "P1",
            "url": "http://cdn/x/rayban.jpg",
            "status": "APPROVED",
            "shopify_image_id": None,
        }
    )
    img = db["product_images"].find_one({"product_id": "P1"})
    res = _run(shopify_push.push_image(db, img))

    assert res.ok is True
    assert res.shopify_id == "gid://shopify/MediaImage/900"
    assert len(spy.calls) == 1
    saved = db["product_images"].find_one({"product_id": "P1"})
    assert saved["shopify_image_id"] == "gid://shopify/MediaImage/900"


def test_push_image_live_fails_loud_when_writeback_cannot_persist(monkeypatch):
    """If the media attached on Shopify but the gid cannot be persisted, the push
    reports ok=False (not a silent ok=True) with the gid preserved for audit --
    the safeguard that stops an un-recorded create from being re-pushed."""
    _force_live(monkeypatch, _MEDIA_OK)
    monkeypatch.setattr(shopify_push, "_writeback_image", lambda db, image, gid: False)
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P1", "ecom": {"shopify_product_id": "gid://shopify/Product/111"}}
    )
    db["product_images"].insert_one(
        {"image_id": None, "product_id": "P1", "url": "u", "status": "APPROVED"}
    )
    img = db["product_images"].find_one({"product_id": "P1"})
    res = _run(shopify_push.push_image(db, img))

    assert res.ok is False
    assert res.shopify_id == "gid://shopify/MediaImage/900"  # gid kept for reconcile
    assert "write-back failed" in (res.error or "")


def test_push_image_live_writeback_true_keeps_ok(monkeypatch):
    """Sanity: the normal keyed doc still returns ok=True and persists (no
    regression to the pre-existing happy path)."""
    _force_live(monkeypatch, _MEDIA_OK)
    db = _EngineDB()
    db["catalog_products"].insert_one(
        {"id": "P1", "ecom": {"shopify_product_id": "gid://shopify/Product/111"}}
    )
    db["product_images"].insert_one(
        {
            "image_id": "I1",
            "product_id": "P1",
            "url": "http://x/raw.jpg",
            "status": "APPROVED",
            "shopify_image_id": None,
        }
    )
    img = db["product_images"].find_one({"image_id": "I1"})
    res = _run(shopify_push.push_image(db, img))
    assert res.ok is True and res.shopify_id == "gid://shopify/MediaImage/900"
    assert (
        db["product_images"].find_one({"image_id": "I1"})["shopify_image_id"]
        == "gid://shopify/MediaImage/900"
    )
