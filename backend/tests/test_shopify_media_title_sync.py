"""
Photos and the title follow IMS onto Shopify  (sync audit gap #3, owner 2026-09-06)
===================================================================================
"Replacing or removing a photo, and renaming, update Shopify instead of
silently doing nothing."

Before this: the push ATTACHED media only onto a bare Shopify product, so
once a product was live a replaced / removed / reordered photo in IMS changed
nothing on the storefront while IMS reported "synced"; and the spine->twin
mirror never recomputed the twin's title, so a rename never reached Shopify.

THE DESIGN under test
  * ecom.media_map = [{url, id}] -- the Shopify media IMS attached, in IMS
    order. IMS manages ONLY those: attach what is missing, delete what IMS
    dropped (tombstone first), reorder to IMS order. Media not in the map
    (hand-uploaded, design-queue, admin) is never touched; a product IMS owns
    nothing on is left entirely alone (no duplicate attach).
  * ONE title formula (product_master.pim_display_name) at create AND on a
    rename; the rename queues the twin so the next press / sync sends it.
  * The scheduled live sync runs the stock pass after the product pass.

***** SAFETY-CRITICAL: every Shopify call is MOCKED (shopify_push._graphql is
monkeypatched); the dark tests use a spy that EXPLODES on any call. *****

Discriminating power (measured by reverting each rule; see the PR): the
delete, the reorder, the tombstone-before-delete, the hands-off rule, the
250 cap, the dark plan, the title recompute, the same-name no-queue and the
stock pass each have a test that goes red when that one rule is removed.

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

from api.services import product_master as pm  # noqa: E402
from api.services import shopify_live_sync as ls  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_push import PushResult  # noqa: E402

# The sibling harnesses, not copies of them: the in-memory _DB whose $set nests
# dot-notation the way real Mongo does, the spine repo + the two edit doors,
# and the live-sync `world` fixture (a StrictDB wired into the service).
from test_online_push_dirty_flag import (  # noqa: E402
    _DB,
    _edit_spine,
    _pm_edit,
    _run,
    _seed_pushed,
)
from test_shopify_live_sync import _seed_products, world  # noqa: E402,F401

TOMB = shopify_push.TOMBSTONES_COLLECTION
GID = "gid://shopify/Product/900"
U1, U2, U3 = (
    "https://cdn.example.com/rb-front.jpg",
    "https://cdn.example.com/rb-side.jpg",
    "https://cdn.example.com/rb-top.jpg",
)


def _m(n):
    return "gid://shopify/MediaImage/%d" % n


# ---------------------------------------------------------------------------
# A fake Shopify that routes on the operation and keeps a transcript. The
# media on the (existing) product is injectable so each test states exactly
# what is on Shopify before the press.
# ---------------------------------------------------------------------------


class _Shopify:
    def __init__(self, media_nodes=None):
        self.calls = []
        self.media_nodes = list(media_nodes or [])
        self.next_media = 100
        self.tombstones_at_delete = None
        self.fail_attach = False

    @staticmethod
    def _op(query):
        for name in (
            "imsProductCreateMedia",
            "imsProductDeleteMedia",
            "imsProductReorderMedia",
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

    def calls_of(self, op):
        return [c for c in self.calls if c["op"] == op]

    def ops(self):
        return [c["op"] for c in self.calls]

    async def __call__(self, db, query, variables):
        op = self._op(query)
        self.calls.append({"op": op, "variables": copy.deepcopy(variables)})
        variant = {
            "id": "gid://shopify/ProductVariant/901",
            "title": "Default Title",
            "selectedOptions": [],
            "inventoryItem": {"id": "gid://shopify/InventoryItem/902"},
        }
        if op in ("imsProductCreate", "imsProductUpdate"):
            field = "productCreate" if op == "imsProductCreate" else "productUpdate"
            nodes = [] if op == "imsProductCreate" else self.media_nodes
            return {
                "data": {
                    field: {
                        "product": {
                            "id": GID,
                            "handle": "h",
                            "variants": {"nodes": [variant]},
                            "media": {"nodes": nodes},
                        },
                        "userErrors": [],
                    }
                }
            }
        if op == "imsProductCreateMedia":
            if self.fail_attach:
                return {
                    "data": {
                        "productCreateMedia": {
                            "media": [],
                            "mediaUserErrors": [{"field": ["media"], "message": "boom"}],
                        }
                    }
                }
            out = []
            for _ in variables.get("media") or []:
                out.append({"id": _m(self.next_media), "status": "PROCESSING"})
                self.next_media += 1
            return {"data": {"productCreateMedia": {"media": out, "mediaUserErrors": []}}}
        if op == "imsProductDeleteMedia":
            self.tombstones_at_delete = len(list(db[TOMB].find({})))
            return {
                "data": {
                    "productDeleteMedia": {
                        "deletedMediaIds": list(variables["mediaIds"]),
                        "mediaUserErrors": [],
                    }
                }
            }
        if op == "imsProductReorderMedia":
            return {
                "data": {
                    "productReorderMedia": {
                        "job": {"id": "gid://shopify/Job/1"},
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
                        "productVariants": [{"id": variant["id"]}],
                        "userErrors": [],
                    }
                }
            }
        if op == "imsVariantsBulkCreate":
            return {
                "data": {"productVariantsBulkCreate": {"productVariants": [], "userErrors": []}}
            }
        if op == "metafieldsSet":
            return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
        if op == "imsLocations":
            return {"data": {"locations": {"nodes": []}}}
        return {"data": {}}


@pytest.fixture
def db():
    return _DB()


@pytest.fixture
def gates(monkeypatch):
    """Open the three push gates and pin the publication id; the network is
    whatever the test installs."""
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
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "gid://shopify/Publication/1")
    shopify_push._publication_id_cache.clear()


def _live(monkeypatch, media_nodes=None):
    fake = _Shopify(media_nodes)
    monkeypatch.setattr(shopify_push, "_graphql", fake)
    return fake


def _product(photos, media_map=None, shopify_id=GID, pid="P1"):
    """A product already on Shopify (or not, shopify_id=None) with the given
    IMS photo list and the media IMS recorded as its own."""
    doc = {
        "id": pid,
        "sku": "SKU-1",
        "title": "Ray-Ban RB2140",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "mrp": 5000.0,
        "offer_price": 4000.0,
        "images": list(photos),
        "ecom": {"status": "PUBLISHED", "locally_modified": True},
    }
    if shopify_id:
        doc["ecom"]["shopify_product_id"] = shopify_id
        doc["ecom"]["shopify_variant_id"] = "gid://shopify/ProductVariant/901"
    if media_map is not None:
        doc["ecom"]["media_map"] = [{"url": u, "id": i} for u, i in media_map]
    return doc


def _seed(db, doc):
    db["catalog_products"].insert_one(copy.deepcopy(doc))
    return copy.deepcopy(doc)


def _map_of(db, pid="P1"):
    return (db["catalog_products"].find_one({"id": pid}) or {})["ecom"].get("media_map")


def _nodes(*ids):
    return [{"id": _m(i), "image": {"url": "https://cdn.shopify.com/%d.jpg" % i}} for i in ids]


# ===========================================================================
# A. THE MEDIA DIFF, through push_product's live transcript
# ===========================================================================


def test_create_attaches_every_photo_in_order_and_records_the_map(db, gates, monkeypatch):
    fake = _live(monkeypatch)
    doc = _seed(db, _product([U1, U2], shopify_id=None))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True and res.mode == "LIVE"
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [U1, U2]
    assert _map_of(db) == [{"url": U1, "id": _m(100)}, {"url": U2, "id": _m(101)}]
    assert res.photos["attached"] == 2 and res.photos["on_shopify"] == 2


def test_a_repress_with_nothing_changed_touches_no_media(db, gates, monkeypatch):
    """The map is what stops a re-press from piling a duplicate copy of every
    photograph onto a live listing (the July '250 media' wall)."""
    fake = _live(monkeypatch, _nodes(1, 2))
    doc = _seed(db, _product([U1, U2], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is True
    assert not [o for o in fake.ops() if o.endswith("Media")], fake.ops()
    assert res.photos == {
        "attached": 0, "deleted": 0, "reordered": False,
        "unmanaged": 0, "hands_off": False, "on_shopify": 2,
    }


def test_replacing_a_photo_attaches_the_new_then_deletes_the_old(db, gates, monkeypatch):
    """IMS swapped the side shot (U2) for a top shot (U3). Shopify gets the new
    one FIRST, then the old one comes down -- and only the old one."""
    fake = _live(monkeypatch, _nodes(1, 2))
    doc = _seed(db, _product([U1, U3], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    media_ops = [o for o in fake.ops() if o.endswith("Media")]
    assert media_ops == ["imsProductCreateMedia", "imsProductDeleteMedia"], media_ops
    (att,) = fake.calls_of("imsProductCreateMedia")
    assert [m["originalSource"] for m in att["variables"]["media"]] == [U3]
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"] == {"productId": GID, "mediaIds": [_m(2)]}
    assert res.photos["attached"] == 1 and res.photos["deleted"] == 1
    assert res.photos["on_shopify"] == 2
    assert _map_of(db) == [{"url": U1, "id": _m(1)}, {"url": U3, "id": _m(100)}]
    assert res.ok is True and res.publication["published"] is True


def test_removing_a_photo_deletes_exactly_that_media(db, gates, monkeypatch):
    fake = _live(monkeypatch, _nodes(1, 2))
    doc = _seed(db, _product([U2], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    assert fake.calls_of("imsProductCreateMedia") == []
    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(1)]
    assert fake.calls_of("imsProductReorderMedia") == []
    assert res.photos["deleted"] == 1 and res.photos["on_shopify"] == 1
    assert _map_of(db) == [{"url": U2, "id": _m(2)}]


def test_the_tombstone_is_written_before_the_delete(db, gates, monkeypatch):
    """The never-lose-bytes lesson: the record of what came down exists
    BEFORE the call that takes it down."""
    fake = _live(monkeypatch, _nodes(1, 2))
    doc = _seed(db, _product([U1], media_map=[(U1, _m(1)), (U2, _m(2))]))

    _run(shopify_push.push_product(db, doc, []))

    assert fake.tombstones_at_delete == 1, "delete ran with no tombstone on record"
    (row,) = list(db[TOMB].find({}))
    assert row["product_id"] == "P1" and row["media_gid"] == _m(2)
    assert row["url"] == U2 and row["shopify_url"] == "https://cdn.shopify.com/2.jpg"
    assert row["deleted_at"] is not None


def test_a_failed_tombstone_skips_the_delete(db, gates, monkeypatch):
    fake = _live(monkeypatch, _nodes(1, 2))
    doc = _seed(db, _product([U1], media_map=[(U1, _m(1)), (U2, _m(2))]))

    def _no_record(*_a, **_k):
        raise RuntimeError("tombstones unavailable")

    monkeypatch.setattr(db[TOMB], "insert_many", _no_record)

    res = _run(shopify_push.push_product(db, doc, []))

    assert fake.calls_of("imsProductDeleteMedia") == []
    assert res.photos["deleted"] == 0 and "tombstones unavailable" in res.photos["error"]
    # The old photograph is still up, so the product stays visible.
    assert res.photos["on_shopify"] == 2 and res.publication["published"] is True


def test_reordering_photos_reorders_the_shopify_media(db, gates, monkeypatch):
    fake = _live(monkeypatch, _nodes(1, 2))
    doc = _seed(db, _product([U2, U1], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    assert fake.calls_of("imsProductCreateMedia") == []
    assert fake.calls_of("imsProductDeleteMedia") == []
    (re,) = fake.calls_of("imsProductReorderMedia")
    assert re["variables"] == {
        "id": GID,
        "moves": [{"id": _m(2), "newPosition": "0"}, {"id": _m(1), "newPosition": "1"}],
    }
    assert res.photos["reordered"] is True


def test_unmapped_media_is_never_deleted_and_keeps_its_place(db, gates, monkeypatch):
    """A hand-uploaded hero shot (M/9, not in the map) leads the product. IMS
    drops U2: only M/2 comes down; M/9 is counted, never touched, and NOT
    displaced -- the owned media was already in IMS order among itself."""
    fake = _live(monkeypatch, _nodes(9, 1, 2))
    doc = _seed(db, _product([U1], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    (dele,) = fake.calls_of("imsProductDeleteMedia")
    assert dele["variables"]["mediaIds"] == [_m(2)]
    assert fake.calls_of("imsProductReorderMedia") == []
    assert res.photos["unmanaged"] == 1 and res.photos["on_shopify"] == 2


def _apply_moves(order, moves):
    """Shopify applies MoveInput rows one after another."""
    order = list(order)
    for mv in moves:
        order.remove(mv["id"])
        order.insert(int(mv["newPosition"]), mv["id"])
    return order


def test_a_reorder_uses_the_slots_ims_owns_and_leaves_unmanaged_media_put(db, gates, monkeypatch):
    """Shopify shows [hero M/9, M/2, M/1]; IMS wants its own two as [U1, U2].
    The moves sort the IMS-owned media inside slots 1 and 2; the hero stays
    at 0 whichever way Shopify applies the moves."""
    fake = _live(monkeypatch, _nodes(9, 2, 1))
    doc = _seed(db, _product([U1, U2], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    (re,) = fake.calls_of("imsProductReorderMedia")
    moves = re["variables"]["moves"]
    assert moves == [{"id": _m(1), "newPosition": "1"}, {"id": _m(2), "newPosition": "2"}]
    assert _apply_moves([_m(9), _m(2), _m(1)], moves) == [_m(9), _m(1), _m(2)]
    assert res.photos["reordered"] is True and res.photos["unmanaged"] == 1


def test_a_product_ims_owns_nothing_on_is_left_alone(db, gates, monkeypatch):
    """The 36 connector-created Ray-Ban Meta products and the six that went
    live before the map existed: media on Shopify, no map. Nothing is
    attached (no duplicates), nothing deleted; the product still publishes
    because it HAS photographs."""
    fake = _live(monkeypatch, _nodes(7, 8))
    doc = _seed(db, _product([U1, U2]))  # no media_map at all

    res = _run(shopify_push.push_product(db, doc, []))

    assert not [o for o in fake.ops() if o.endswith("Media")], fake.ops()
    assert res.photos["hands_off"] is True and res.photos["unmanaged"] == 2
    assert res.photos["on_shopify"] == 2 and res.publication["published"] is True
    assert _map_of(db) is None


def test_a_failed_attach_never_deletes_the_photo_it_was_replacing(db, gates, monkeypatch):
    fake = _live(monkeypatch, _nodes(1, 2))
    fake.fail_attach = True
    doc = _seed(db, _product([U1, U3], media_map=[(U1, _m(1)), (U2, _m(2))]))

    res = _run(shopify_push.push_product(db, doc, []))

    assert fake.calls_of("imsProductDeleteMedia") == []
    assert res.photos["attached"] == 0 and "mediaUserErrors" in res.photos["error"]
    assert res.photos["on_shopify"] == 2  # the old side shot is still up
    assert _map_of(db) == [{"url": U1, "id": _m(1)}, {"url": U2, "id": _m(2)}]


def test_the_250_media_limit_is_refused_with_a_code_before_any_call(db, gates, monkeypatch):
    urls = ["https://cdn.example.com/%d.jpg" % i for i in range(250)]
    fake = _live(monkeypatch, _nodes(*range(250)))
    doc = _seed(db, _product(urls + [U3], media_map=list(zip(urls, (_m(i) for i in range(250))))))

    res = _run(shopify_push.push_product(db, doc, []))

    assert not [o for o in fake.ops() if o.endswith("Media")], fake.ops()
    assert res.photos["code"] == shopify_push.MEDIA_LIMIT_CODE
    assert "250" in res.photos["error"] and res.photos["attached"] == 0


def test_a_bare_create_that_cannot_attach_withholds_the_publish(db, gates, monkeypatch):
    """The grey-box guard survives the diff: no media on Shopify after the
    pass -> not published, row stays queued."""
    fake = _live(monkeypatch)
    fake.fail_attach = True
    doc = _seed(db, _product([U1], shopify_id=None))

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.ok is False and res.reason == "publish_withheld"
    assert res.photos["on_shopify"] == 0
    assert fake.calls_of("imsPublishablePublish") == []


def test_dark_press_returns_the_media_plan_and_makes_no_call(db, monkeypatch):
    monkeypatch.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)

    async def _boom(db, query, variables):  # pragma: no cover
        raise AssertionError("DARK press must never hit the Shopify network")

    monkeypatch.setattr(shopify_push, "_graphql", _boom)
    doc = _product([U3, U1], media_map=[(U1, _m(1)), (U2, _m(2))])

    res = _run(shopify_push.push_product(db, doc, []))

    assert res.mode == "SIMULATED" and res.ok is True
    assert res.photos == {
        "attach": [U3],
        "delete": [{"url": U2, "id": _m(2), "shopify_url": None}],
        "reorder": [],
        "unmanaged": 0,
        "hands_off": False,
        "owned": [{"url": U1, "id": _m(1)}],
    }


def test_plan_reports_a_reorder_when_shopify_order_differs():
    """The pure diff (what the live pass acts on) sees a reorder when the
    IMS order and the Shopify order of the owned media disagree."""
    doc = _product([U2, U1], media_map=[(U1, _m(1)), (U2, _m(2))])
    plan = shopify_push.plan_product_media(doc, [U2, U1], _nodes(1, 2))
    assert plan["reorder"] == [_m(2), _m(1)] and plan["attach"] == [] and plan["delete"] == []
    same = shopify_push.plan_product_media(doc, [U2, U1], _nodes(2, 1))
    assert same["reorder"] == []


# ===========================================================================
# B. THE TITLE: one formula, rename queues, same name does not
# ===========================================================================


def test_create_and_mirror_share_one_title_formula():
    spine = {"pim_product_id": "X", "sku": "S", "brand": "Ray-Ban", "model": "RB3025",
             "category": "SUNGLASS"}
    assert pm._build_pim_doc(spine)["title"] == pm.pim_display_name(spine)
    named = dict(spine, name="Ray-Ban Aviator Classic")
    assert pm._build_pim_doc(named)["title"] == "Ray-Ban Aviator Classic"
    assert pm.pim_display_name(named) == "Ray-Ban Aviator Classic"


def test_a_rename_on_the_master_door_moves_the_twin_title_and_queues_it(db):
    """The twin's name/title IS the Shopify title (build_product_input); the
    mirror never recomputed it, so a rename stayed in IMS."""
    _seed_pushed(db)  # twin P1 "Ray-Ban RB2140", clean, live
    _pm_edit(db, {"name": "Ray-Ban Wayfarer Classic"})

    twin = db["catalog_products"].find_one({"id": "P1"})
    assert twin["name"] == "Ray-Ban Wayfarer Classic"
    assert twin["title"] == "Ray-Ban Wayfarer Classic"
    assert twin["ecom"]["locally_modified"] is True
    # ... and the update payload the next press / sync sends carries it.
    assert shopify_push.build_product_input(twin, [])["title"] == "Ray-Ban Wayfarer Classic"


def test_a_rename_to_the_same_title_does_not_queue(db):
    _seed_pushed(db)
    twin_before = db["catalog_products"].find_one({"id": "P1"})
    _pm_edit(db, {"name": twin_before["title"]})

    twin = db["catalog_products"].find_one({"id": "P1"})
    assert twin["ecom"]["locally_modified"] is False


def test_put_products_route_accepts_the_rename_and_it_reaches_the_twin(db, monkeypatch):
    """The validated edit door (PUT /products/{id}) now carries `name`; the
    ONE mirror rule takes it to the twin the same way the master door does."""
    _seed_pushed(db)
    _edit_spine(db, monkeypatch, name="Ray-Ban Clubmaster")

    twin = db["catalog_products"].find_one({"id": "P1"})
    assert twin["title"] == "Ray-Ban Clubmaster" and twin["ecom"]["locally_modified"] is True


# ===========================================================================
# C. THE LIVE SYNC runs the stock pass after the product pass
# ===========================================================================


def test_live_sync_runs_the_stock_pass_once_after_the_products(world, monkeypatch):
    db, audit = world
    _seed_products(db)
    seen = []

    async def _stock(_db):
        seen.append(len(audit.rows))  # how many product rows existed when called
        return PushResult(
            mode="SIMULATED", entity="stock", action="sync", ok=True,
            payload={"candidates": 4, "changed": 3, "synced": 2, "failed": 1},
        )

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _stock)

    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))

    assert seen == [1], "the stock pass must run exactly once, after the product pass"
    assert run["stock"] == {
        "ok": True, "changed": 3, "synced": 2, "failed": 1, "code": None, "error": None,
    }
    stored = db["online_sync_runs"].docs[0]
    assert stored["stock"]["synced"] == 2


def test_live_sync_records_a_stock_pass_failure_and_still_finishes(world, monkeypatch):
    db, _audit = world
    _seed_products(db)

    async def _stock(_db):
        raise RuntimeError("inventory read exploded")

    monkeypatch.setattr(shopify_push, "sync_stock_levels", _stock)

    run = _run(ls.sync_live_products(db, trigger="manual", actor="u-super"))

    assert run["status"] == "done" and run["pushed_ok"] == 1
    assert run["stock"]["ok"] is False and "exploded" in run["stock"]["error"]
