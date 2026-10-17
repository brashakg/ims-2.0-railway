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
from database.connection import MockCollection  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from api.services import catalogue_pdf  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import PushResult  # noqa: E402
from api.services.shopify_push import product as product_mod  # noqa: E402

# The sibling harnesses, never copies of them.
from test_online_push_dirty_flag import (  # noqa: E402
    _DB,
    _edit_spine,
    _frame_payload,
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
from test_catalog_review_put import _bvi_doc, _promote, _put, env  # noqa: E402,F401
from test_online_store_push import patched_db  # noqa: E402,F401 -- fixture
from test_cataloguing_scorecard_qc import (  # noqa: E402
    FakeDB as _QcDB,
    _product as _qc_product,
    _run_generate as _qc_generate,
)
from api.routers import catalog as catalog_mod  # noqa: E402

FRONT, ANGLED, SIDE = (
    "https://cdn.example.com/rb-front.jpg",
    "https://cdn.example.com/rb-angled.jpg",
    "https://cdn.example.com/rb-side.jpg",
)
# The push mechanics for a brand that IS for the website (owner D6).
pytestmark = pytest.mark.usefixtures("brand_is_for_the_website")

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


def test_a_new_product_keeps_the_order_it_was_created_with_on_its_twin(db):
    """POST /products: the add page reordered the photos before the FIRST
    save. The twin (what the first publish sends to Shopify) is born in the
    order sent, the same as the spine (what the till and the list show)."""
    sent = [SIDE, FRONT, ANGLED]
    created = pm.create_via_door(
        _frame_payload(),
        source="FORM",
        actor="u1",
        extra_fields={"images": list(sent)},
        product_repo=ProductRepository(MockCollection("products")),
        audit_repo=AuditRepository(MockCollection("audit_logs")),
        db=db,
    )

    assert created["images"] == sent
    assert _twin(db, created["pim_product_id"])["images"] == sent


def test_the_review_editor_save_keeps_the_order_and_queues_the_row(env):
    """The imported-product review mode saves through PUT /catalog/products."""
    doc = _bvi_doc(doc_id="clx0photos01", images=[FRONT, ANGLED, SIDE])
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"images": [SIDE, ANGLED, FRONT]})

    saved = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert saved["images"] == [SIDE, ANGLED, FRONT]
    assert saved["ecom"]["locally_modified"] is True


def test_approve_after_a_review_reorder_gives_the_till_the_same_order(env):
    """Review editor: 'Move photo 3 earlier', then Approve. Approve promotes
    the twin onto a new spine row; the spine (the till, the list) must hold
    the reviewer's order, the same as the twin (the website)."""
    doc = _bvi_doc(doc_id="clx0photos02", sku="RVPH2", images=[FRONT, ANGLED, SIDE])
    catalog_mod.CATALOG_PRODUCTS[doc["id"]] = doc

    _put(doc["id"], {"images": [FRONT, SIDE, ANGLED]})
    _promote(doc["id"])

    twin = catalog_mod.CATALOG_PRODUCTS[doc["id"]]
    assert twin["images"] == [FRONT, SIDE, ANGLED]
    assert env["repo"].find_by_id(doc["id"])["images"] == [FRONT, SIDE, ANGLED]


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


def test_one_save_that_adds_removes_and_reorders_reaches_shopify_in_the_new_order(
    db, gates, monkeypatch  # noqa: F811
):
    """One save of the add / edit page: a NEW photo uploaded and moved to
    photo 1, the angled shot removed, the front shot moved last. Shopify shows
    [front, angled, side] = [M/1, M/2, M/3]. The press attaches only the new
    photo, deletes only M/2, then ONE reorder puts the new photo first: the
    attach appends it LAST, so without the reorder the website would lead
    with the side shot while IMS shows the new photo as photo 1."""
    new = "https://cdn.example.com/rb-new.jpg"
    _seed_pushed(db)
    ecom = dict(
        _twin(db)["ecom"],
        shopify_product_id=GID,
        media_map=[
            {"url": FRONT, "id": _m(1)},
            {"url": ANGLED, "id": _m(2)},
            {"url": SIDE, "id": _m(3)},
        ],
    )
    db["catalog_products"].update_one(
        {"id": "P1"}, {"$set": {"images": [FRONT, ANGLED, SIDE], "ecom": ecom}}
    )
    repo = _spine_repo()
    repo.update("P1", {"images": [FRONT, ANGLED, SIDE]})

    _edit_spine(db, monkeypatch, repo=repo, images=[new, SIDE, FRONT])

    doc = copy.deepcopy(_twin(db))
    assert doc["images"] == [new, SIDE, FRONT]
    fake = _Shopify(_nodes(1, 2, 3))
    monkeypatch.setattr(shopify_push, "_graphql", fake)

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True
    assert _media_ops(fake) == [
        "imsProductCreateMedia", "imsProductDeleteMedia", "imsProductReorderMedia"
    ], _media_ops(fake)
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [new]
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)]
    (re,) = fake.calls_of("imsProductReorderMedia")
    # On Shopify after the attach and the delete: [M/1, M/3, M/100 (new)].
    assert _apply_moves([_m(1), _m(3), _m(100)], re["variables"]["moves"]) == [
        _m(100), _m(3), _m(1)
    ]
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 1
    assert res.photos["reordered"] is True and res.photos["on_shopify"] == 3
    assert _twin(db)["ecom"]["media_map"] == [
        {"url": new, "id": _m(100)},
        {"url": SIDE, "id": _m(3)},
        {"url": FRONT, "id": _m(1)},
    ]


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
# 2b. A photo step that FAILED is sent again: the product stays queued
# ===========================================================================


class _ReorderFails(_Shopify):
    """The media rig, with productReorderMedia switchable to a transport error."""

    def __init__(self, media_nodes):
        super().__init__(media_nodes)
        self.fail_reorder = True

    async def __call__(self, db, query, variables):
        if self.fail_reorder and self._op(query) == "imsProductReorderMedia":
            self.calls.append(
                {"op": "imsProductReorderMedia", "variables": copy.deepcopy(variables)}
            )
            raise RuntimeError("Shopify 503")
        return await super().__call__(db, query, variables)


def _live_reordered():
    """Live on Shopify as [front, angled, side]; IMS now wants [side, front, angled]."""
    return _product(
        [SIDE, FRONT, ANGLED],
        media_map=[(FRONT, _m(1)), (ANGLED, _m(2)), (SIDE, _m(3))],
    )


def _reorder_fails(monkeypatch):
    fake = _ReorderFails(_nodes(1, 2, 3))
    monkeypatch.setattr(shopify_push, "_graphql", fake)

    # This pins the PHOTO side channel. These rigs hold no shops, so a live
    # stock pass would stamp its own code on every result (that code has its
    # own file, test_shopify_online_stock.py).
    async def _stock_written(*_a, **_k):
        return {"ok": True, "code": None, "error": None, "set": 1, "quantities": {}, "tracking_confirmed": True}

    monkeypatch.setattr(product_mod, "sync_product_stock", _stock_written)
    return fake


def test_a_failed_reorder_keeps_the_live_product_queued_and_says_so(
    db, gates, monkeypatch  # noqa: F811
):
    fake = _reorder_fails(monkeypatch)
    doc = _seed(db, _live_reordered())

    res = _run(shopify_push.push_product(db, doc, []))

    assert _media_ops(fake) == ["imsProductReorderMedia"], _media_ops(fake)
    assert res.photos["reordered"] is False and res.photos["error"] == "Shopify 503"
    # The product IS live -- never a false "failed" -- but it says so...
    assert res.ok is True
    assert res.code == shopify_push.PHOTOS_NOT_SYNCED
    assert res.error == shopify_push._PHOTOS_NOT_SYNCED_MSG
    # ...and stays queued, so the next press / scheduled sync sends it again.
    assert _twin(db)["ecom"]["locally_modified"] is True


def test_the_next_sync_sends_the_failed_reorder_again_then_the_queue_drains(
    world, monkeypatch  # noqa: F811
):
    db, audit = world
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/1")
    shopify_push._publication_id_cache.clear()
    fake = _reorder_fails(monkeypatch)

    async def _no_stock(_db):
        return PushResult(mode="LIVE", entity="stock", action="sync", ok=True, payload={})

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _no_stock)
    db.seed("catalog_products", [_live_reordered()])

    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))

    assert run["pushed_ok"] == 1 and run["failed"] == 0
    assert run["photos_not_synced"] == 1
    (failure,) = run["failures"]
    assert failure["product_id"] == "P1"
    assert failure["code"] == shopify_push.PHOTOS_NOT_SYNCED
    assert failure["error"] == shopify_push._PHOTOS_NOT_SYNCED_MSG
    (row,) = [r for r in audit.rows if r["entity_id"] == "P1"]
    assert row["severity"] == "WARNING"
    assert row["details"]["photos"]["error"] == "Shopify 503"
    assert _twin(db)["ecom"]["locally_modified"] is True

    # Shopify is back: the next run selects the product again and sends the
    # SAME reorder; it lands and the queue drains.
    fake.fail_reorder = False
    run2 = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))

    assert run2["selected"] == 1
    assert run2["photos_not_synced"] == 0 and run2["failures"] == []
    first, again = fake.calls_of("imsProductReorderMedia")
    assert again["variables"] == first["variables"]
    assert _apply_moves([_m(1), _m(2), _m(3)], again["variables"]["moves"]) == [
        _m(3), _m(1), _m(2)
    ]
    assert _twin(db)["ecom"]["locally_modified"] is False
    assert _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))["selected"] == 0


def test_the_push_all_press_gives_it_its_own_line_never_a_stock_line(
    client, auth_headers, patched_db, monkeypatch  # noqa: F811
):
    conn, _ = patched_db
    conn.db["catalog_products"].insert_one(_live_reordered())

    async def _live_with_old_photos(db, product, variants, blocked=None):
        return PushResult(
            mode="LIVE", entity="product", action="update", target_id=product["id"],
            ok=True, shopify_id=GID, code=shopify_push.PHOTOS_NOT_SYNCED,
            error=shopify_push._PHOTOS_NOT_SYNCED_MSG,
        )

    async def _no_stock(db):
        return PushResult(mode="LIVE", entity="stock", action="noop", ok=True)

    monkeypatch.setattr(shopify_push, "push_product", _live_with_old_photos)
    monkeypatch.setattr(shopify_push, "sync_stock_levels", _no_stock)

    r = client.post(
        "/api/v1/online-store/push/all-pending?entities=products", headers=auth_headers
    )

    assert r.status_code == 200, r.text
    products = r.json()["summary"]["products"]
    assert products["pushed"] == 1 and products["failed"] == 0
    assert products["photos_not_synced"] == 1
    assert "stock_not_written" not in products and "stock_warning" not in products


class _Website(_Shopify):
    """The media rig with a MEMORY: an attach appends, a delete removes, a
    reorder moves -- so the second press sees what the first one left on the
    website. productDeleteMedia is switchable to a transport error."""

    def __init__(self, media_nodes):
        super().__init__(media_nodes)
        self.fail_delete = True

    def order(self):
        return [n["id"] for n in self.media_nodes]

    async def __call__(self, db, query, variables):
        op = self._op(query)
        if op == "imsProductDeleteMedia" and self.fail_delete:
            self.calls.append({"op": op, "variables": copy.deepcopy(variables)})
            raise RuntimeError("Shopify 503")
        body = await super().__call__(db, query, variables)
        if op == "imsProductCreateMedia":
            new = body["data"]["productCreateMedia"]["media"]
            self.media_nodes = self.media_nodes + [
                {"id": n["id"], "image": {"url": "https://cdn.shopify.com/new.jpg"}} for n in new
            ]
        elif op == "imsProductDeleteMedia":
            gone = set(variables["mediaIds"])
            self.media_nodes = [n for n in self.media_nodes if n["id"] not in gone]
        elif op == "imsProductReorderMedia":
            by_id = {n["id"]: n for n in self.media_nodes}
            self.media_nodes = [by_id[i] for i in _apply_moves(self.order(), variables["moves"])]
        return body


def test_a_failed_delete_keeps_the_removed_photo_owned_until_the_retry_deletes_it(
    db, gates, monkeypatch  # noqa: F811
):
    """One save: a NEW photo uploaded as photo 1 and the front shot (photo 1
    on the website) removed. Press 1 attaches the new photo, then
    productDeleteMedia fails. The front shot must STAY in the map, marked
    pending_delete -- dropped there, press 2 would see it as media IMS does
    not own, never delete it, and the website would keep leading with the
    photo the user removed. Press 2 sends the delete and the reorder; the
    website ends [NEW, ANGLED, SIDE] and only then is the map clean."""
    new = "https://cdn.example.com/rb-new.jpg"
    fake = _Website(_nodes(1, 2, 3))
    monkeypatch.setattr(shopify_push, "_graphql", fake)

    async def _stock_written(*_a, **_k):
        return {"ok": True, "code": None, "error": None, "set": 1, "quantities": {}, "tracking_confirmed": True}

    monkeypatch.setattr(product_mod, "sync_product_stock", _stock_written)
    _seed(
        db,
        _product(
            [new, ANGLED, SIDE],
            media_map=[(FRONT, _m(1)), (ANGLED, _m(2)), (SIDE, _m(3))],
        ),
    )

    # PRESS 1: the attach lands, the delete fails.
    res = _run(shopify_push.push_product(db, copy.deepcopy(_twin(db)), []))

    assert _media_ops(fake) == ["imsProductCreateMedia", "imsProductDeleteMedia"]
    assert fake.order() == [_m(1), _m(2), _m(3), _m(100)]
    assert res.ok is True and res.code == shopify_push.PHOTOS_NOT_SYNCED
    assert res.photos["error"] == "Shopify 503"
    assert _twin(db)["ecom"]["locally_modified"] is True
    assert _twin(db)["ecom"]["media_map"] == [
        {"url": new, "id": _m(100)},
        {"url": ANGLED, "id": _m(2)},
        {"url": SIDE, "id": _m(3)},
        {"url": FRONT, "id": _m(1), "pending_delete": True},
    ]

    # PRESS 2: Shopify is back. The removed photo is deleted, then the order.
    fake.fail_delete = False
    fake.calls.clear()
    res = _run(shopify_push.push_product(db, copy.deepcopy(_twin(db)), []))

    assert _media_ops(fake) == ["imsProductDeleteMedia", "imsProductReorderMedia"]
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(1)]
    assert fake.order() == [_m(100), _m(2), _m(3)], "the website: NEW, ANGLED, SIDE"
    assert res.ok is True and res.code is None
    assert _twin(db)["ecom"]["locally_modified"] is False
    assert _twin(db)["ecom"]["media_map"] == [
        {"url": new, "id": _m(100)},
        {"url": ANGLED, "id": _m(2)},
        {"url": SIDE, "id": _m(3)},
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


def test_the_cataloguing_scorecard_sample_shows_photo_1(monkeypatch):
    """Cataloguing Scorecard > QC sample (generate_qc_samples): the sample's
    photo is photo 1, never a stale stored image_url."""
    db = _QcDB(
        products=[
            _qc_product(
                "P9", "u-a", name="asha", created_days_ago=1,
                images=[SIDE, FRONT], image_url=FRONT,
            )
        ]
    )

    _qc_generate(monkeypatch, db, per_user=1)

    (item,) = db.get_collection("qc_samples").docs
    assert item["image_url"] == SIDE
