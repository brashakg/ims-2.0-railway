"""
Product photos in order  (owner 2026-10-12, mockup artboard "Photos")
=====================================================================
"Photo 1 is the main photo on the website and at the till." The add / edit
page reorders a product's photos; THE ORDER IS THE images ARRAY. These tests
pin the backend half end to end:

  * SAVE: a reordered images array is stored in that order by every product
    save door (PUT /products, the product-master door, the catalog review
    door) and mirrored to the catalog twin in that order -- and a reorder
    queues the twin (ecom.locally_modified) like any other photo change.
  * THE WEBSITE: a reorder of a LIVE product reaches Shopify as ONE
    productReorderMedia call in the new order -- nothing re-uploaded, nothing
    deleted -- at the next press AND at the scheduled live sync; a product
    never published is still left for a human's first press.
  * ONE MAIN-PHOTO RULE: every reader that shows a single photo shows
    images[0]; a stale singular image_url never outranks photo 1.

***** SAFETY-CRITICAL: every Shopify call is MOCKED (shopify_push._graphql is
monkeypatched with a transcript fake). *****

No emoji (Windows cp1252).
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.routers import products as products_mod  # noqa: E402
from api.services import catalogue_pdf  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import PushResult  # noqa: E402

# The sibling harnesses, never copies of them.
from test_online_push_dirty_flag import (  # noqa: E402
    _DB,
    _edit_spine,
    _pm_edit,
    _pm_spine_repo,
    _run,
    _seed_pushed,
    _spine_repo,
)
from test_shopify_media_title_sync import (  # noqa: E402
    GID,
    _Shopify,
    _apply_moves,
    _m,
    _nodes,
    _product,
    _seed,
    gates,  # noqa: F401 -- fixture
)
from test_shopify_live_sync import world  # noqa: E402,F401 -- fixture
from test_catalog_review_put import _bvi_doc, _put, env  # noqa: E402,F401
from api.routers import catalog as catalog_mod  # noqa: E402

FRONT, ANGLED, SIDE = (
    "https://cdn.example.com/rb-front.jpg",
    "https://cdn.example.com/rb-angled.jpg",
    "https://cdn.example.com/rb-side.jpg",
)
USER = {"user_id": "u1", "username": "t", "roles": ["ADMIN"], "active_store_id": "S1"}


@pytest.fixture
def db():
    return _DB()


def _twin_photos(db, photos, pid="P1"):
    db["catalog_products"].update_one({"id": pid}, {"$set": {"images": list(photos)}})


def _twin(db, pid="P1"):
    return db["catalog_products"].find_one({"id": pid})


# ===========================================================================
# 1. SAVE: every door stores the new order and mirrors it to the twin
# ===========================================================================


def test_the_product_page_save_keeps_the_order_on_the_product_and_its_twin(db, monkeypatch):
    """PUT /products/{id} -- the door the add / edit page saves through."""
    _seed_pushed(db)
    _twin_photos(db, [FRONT, ANGLED, SIDE])
    repo = _spine_repo()
    repo.update("P1", {"images": [FRONT, ANGLED, SIDE]})

    _edit_spine(db, monkeypatch, repo=repo, images=[SIDE, FRONT, ANGLED])

    assert repo.find_by_id("P1")["images"] == [SIDE, FRONT, ANGLED]
    twin = _twin(db)
    assert twin["images"] == [SIDE, FRONT, ANGLED]
    assert twin["ecom"]["locally_modified"] is True, "a reorder queues the twin"
    # The round trip: the page reloads the product in the saved order, and
    # the till's single-photo alias is the new photo 1.
    back = _run(products_mod.get_product("P1", USER))
    assert back["images"] == [SIDE, FRONT, ANGLED]
    assert back["image_url"] == SIDE


def test_the_product_master_door_keeps_the_order_and_queues_the_twin(db):
    _seed_pushed(db)
    _twin_photos(db, [FRONT, ANGLED, SIDE])
    repo = _pm_spine_repo()
    repo.update("PM1", {"images": [FRONT, ANGLED, SIDE]})

    _pm_edit(db, {"images": [ANGLED, SIDE, FRONT]}, repo=repo)

    assert repo.find_by_id("PM1")["images"] == [ANGLED, SIDE, FRONT]
    twin = _twin(db)
    assert twin["images"] == [ANGLED, SIDE, FRONT]
    assert twin["ecom"]["locally_modified"] is True


def test_the_review_editor_save_keeps_the_order_and_queues_the_row(env):
    """The imported-product review mode saves through PUT /catalog/products."""
    doc = _bvi_doc(doc_id="clx0photos01", images=[FRONT, ANGLED, SIDE])
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"images": [SIDE, ANGLED, FRONT]})

    saved = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert saved["images"] == [SIDE, ANGLED, FRONT]
    assert saved["ecom"]["locally_modified"] is True


# ===========================================================================
# 2. THE WEBSITE follows: one reorder call, nothing re-uploaded or deleted
# ===========================================================================


def _media_ops(fake):
    return [o for o in fake.ops() if o.endswith("Media")]


def test_a_live_products_reorder_is_one_reorder_call_and_nothing_is_reuploaded(
    db, gates, monkeypatch  # noqa: F811
):
    """Shopify shows [front, angled, side]; IMS now wants [side, front, angled]."""
    fake = _Shopify(_nodes(1, 2, 3))
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    doc = _seed(
        db,
        _product(
            [SIDE, FRONT, ANGLED],
            media_map=[(FRONT, _m(1)), (ANGLED, _m(2)), (SIDE, _m(3))],
        ),
    )

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True
    assert _media_ops(fake) == ["imsProductReorderMedia"], _media_ops(fake)
    (re,) = fake.calls_of("imsProductReorderMedia")
    assert re["variables"]["id"] == GID
    moves = re["variables"]["moves"]
    assert _apply_moves([_m(1), _m(2), _m(3)], moves) == [_m(3), _m(1), _m(2)]
    assert res.photos["reordered"] is True
    assert res.photos["attached"] == 0 and res.photos["deleted"] == 0


def test_a_stale_singular_image_url_never_outranks_photo_1(db, gates, monkeypatch):  # noqa: F811
    """A twin that still carries a legacy singular image_url (the old photo 1)
    after the photos were reordered: the website must follow images[], so the
    new photo 1 leads on Shopify too."""
    fake = _Shopify(_nodes(1, 2))
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    doc = _product([ANGLED, FRONT], media_map=[(FRONT, _m(1)), (ANGLED, _m(2))])
    doc["image_url"] = FRONT
    doc = _seed(db, doc)

    res = _run(shopify_push.push_product(db, doc, []))

    assert shopify_push.product_photo_urls(doc) == [ANGLED, FRONT]
    assert _media_ops(fake) == ["imsProductReorderMedia"], _media_ops(fake)
    (re,) = fake.calls_of("imsProductReorderMedia")
    assert _apply_moves([_m(1), _m(2)], re["variables"]["moves"]) == [_m(2), _m(1)]
    assert res.photos["reordered"] is True


def test_the_scheduled_sync_carries_a_reorder_and_never_first_publishes(world, monkeypatch):  # noqa: F811
    """The 01:00 / 09:00 IST live sync: the live product's reorder goes out as
    one reorder call; a reordered product that was never published stays for
    a human's first press (owner rule)."""
    db, _audit = world
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/1")
    shopify_push._publication_id_cache.clear()
    fake = _Shopify(_nodes(1, 2, 3))
    monkeypatch.setattr(shopify_push, "_graphql", fake)

    async def _no_stock(_db):
        return PushResult(mode="LIVE", entity="stock", action="sync", ok=True, payload={})

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _no_stock)
    live = _product(
        [SIDE, FRONT, ANGLED],
        media_map=[(FRONT, _m(1)), (ANGLED, _m(2)), (SIDE, _m(3))],
    )
    never = _product([SIDE, FRONT], shopify_id=None, pid="P-new")
    never["sku"] = "SKU-NEW"
    db.seed("catalog_products", [copy.deepcopy(live), copy.deepcopy(never)])

    run = _run(ls.sync_live_products(db, trigger="scheduled", actor="u-super"))

    assert run["awaiting_first_publish"] == 1
    assert fake.calls_of("imsProductCreate") == [], "a first publish stays a human press"
    assert _media_ops(fake) == ["imsProductReorderMedia"], _media_ops(fake)
    (re,) = fake.calls_of("imsProductReorderMedia")
    assert re["variables"]["id"] == GID
    assert _apply_moves([_m(1), _m(2), _m(3)], re["variables"]["moves"]) == [
        _m(3), _m(1), _m(2)
    ]


# ===========================================================================
# 3. ONE MAIN-PHOTO RULE: images[0] wherever one photo is shown
# ===========================================================================

_LEGACY = {
    "product_id": "P9",
    "sku": "SG9",
    "brand": "Ray-Ban",
    "model": "RB9",
    # a stale singular field left behind by an old import: the OLD photo 1
    "image_url": FRONT,
    "images": [SIDE, FRONT],
}


def test_main_photo_is_images_0_then_the_legacy_fields():
    assert pm.main_photo(_LEGACY) == SIDE
    assert pm.main_photo({"images": ["  ", {"url": ANGLED}], "image_url": FRONT}) == ANGLED
    assert pm.main_photo({"images": [], "image_url": FRONT}) == FRONT
    assert pm.main_photo({"image": SIDE}) == SIDE
    assert pm.main_photo({}) is None


class _Repo:
    def __init__(self, docs):
        self.docs = docs

    def find_many(self, flt=None, skip=0, limit=100):
        return [dict(d) for d in self.docs]

    def count(self, flt=None):
        return len(self.docs)

    def find_by_id(self, pid):
        return next((dict(d) for d in self.docs if d["product_id"] == pid), None)


def test_the_till_and_catalog_list_alias_is_photo_1(monkeypatch):
    import api.services.cache as cache_mod

    class _NoCache:
        TTL_MEDIUM = 0

        def get(self, k):
            return None

        def set(self, k, v, ttl=0):
            pass

    monkeypatch.setattr(cache_mod, "cache", _NoCache())
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: _Repo([_LEGACY]))
    out = _run(
        products_mod.list_products(
            category=None, brand=None, search=None, tag=None, store_id=None,
            skip=0, limit=50, current_user=USER,
        )
    )
    (row,) = out["products"]
    assert row["image_url"] == SIDE and row["images"][0] == SIDE
    assert _run(products_mod.get_product("P9", USER))["image_url"] == SIDE


def test_the_stock_screen_catalogue_pdf_and_duplicate_popup_show_photo_1():
    from api.routers.inventory import stock

    assert stock._ledger_row(_LEGACY, 1, 0, {}, "S1")["image_url"] == SIDE
    (row,) = catalogue_pdf.build_product_rows([_LEGACY])
    assert row["image"] == SIDE
    assert pm.existing_product_summary(_LEGACY)["image_url"] == SIDE
