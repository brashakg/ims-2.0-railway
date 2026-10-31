"""
Permanent delete of an ARCHIVED product (owner 2026-10-24)
==========================================================
"i will need an option to delete archived products". Through the REAL catalog
router (role gates are the routes' own) over mongomock:

  * SUPERADMIN only (ADMIN and everyone else 403, nothing deleted)
  * an active product is refused 409 "Archive it first"
  * ANY history (every collection product_purge.HISTORY names) or a model
    family it leads -> 409, it stays archived
  * on Shopify (owner ruling on #1273): its listing -- or, for a size, only
    its variant -- is deleted there FIRST (a fake Shopify, never the
    network); already gone proceeds; a Shopify failure leaves IMS untouched;
    DARK (website writes off) refuses
  * a check that cannot read -> 503, nothing deleted
  * backup first (deleted_products_backup holds every deleted doc); a failed
    backup deletes nothing
  * the identity_key is freed: the same brand|model|colour|size can be added
  * the rbac rows equal the code gates

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_product_purge.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from api import dependencies as deps  # noqa: E402
from api.routers import catalog as catalog_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import product_purge, rbac_policy, shopify_push  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402

ROOT = {"user_id": "u-root", "username": "root", "roles": ["SUPERADMIN"]}
ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}
GONE = "2026-10-20T06:00:00+00:00"
KEY = "carrera|p1|black|52"


def _spine(pid, **extra):
    doc = {"product_id": pid, "pim_product_id": f"T-{pid}", "sku": f"SKU-{pid}",
           "name": f"Frame {pid}", "brand": "Carrera", "model": pid, "category": "SUNGLASS",
           "mrp": 5000, "is_active": False, "deleted_at": GONE, "deleted_by": "u-admin"}
    doc.update(extra)
    return doc


def _twin(pid, **extra):
    doc = {"id": f"T-{pid}", "sku": f"SKU-{pid}", "title": f"Frame {pid}", "is_active": False,
           "deleted_at": GONE, "ecom": {"status": "DRAFT"}}
    doc.update(extra)
    return doc


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.db[name]


class _Broken:
    """A db whose `bad` collection raises on every call."""

    def __init__(self, db, bad):
        self._db, self._bad = db, bad

    def __getitem__(self, name):
        if name == self._bad:
            class _Boom:
                def __getattr__(self, _):
                    def _raise(*a, **k):
                        raise RuntimeError("connection reset")
                    return _raise
            return _Boom()
        return self._db[name]


@pytest.fixture
def env(monkeypatch):
    # DARK unless a test opens the gates (_live); never a real Shopify call.
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    monkeypatch.delenv("SHOPIFY_DISPATCH_MODE", raising=False)

    async def _no_network(*a, **k):
        raise AssertionError("no Shopify call expected")

    monkeypatch.setattr(shopify_push, "_graphql", _no_network)
    db = mongomock.MongoClient().db
    db.products.create_index("identity_key", unique=True, sparse=True)
    db.products.insert_many([
        _spine("P1", identity_key=KEY),
        _spine("P2"),
        _spine("P3", is_active=True, deleted_at=None),  # active
    ])
    db.catalog_products.insert_many([_twin("P1"), _twin("P2"),
                                     _twin("P3", is_active=True, deleted_at=None)])
    db.catalog_variants.insert_one({"variant_id": "V1", "sku": "SKU-P1", "parent_product_id": "T-P1"})
    db.product_images.insert_one({"image_id": "I1", "product_id": "T-P1", "url": "https://x/p1.jpg"})
    db.vendor_sku_aliases.insert_one({"vendor_id": "V", "vendor_sku": "C-1", "product_id": "P1"})
    conn = _Conn(db)
    repo = lambda: ProductRepository(db.products)  # noqa: E731
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_product_repository", repo)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: AuditRepository(db.audit_logs))
    monkeypatch.setattr(catalog_mod, "_get_db", lambda: db)
    monkeypatch.setattr(catalog_mod, "_catalog_coll", lambda: db.catalog_products)
    monkeypatch.setattr(catalog_mod, "invalidate_product_lists", lambda: None)

    app = FastAPI()
    app.include_router(catalog_mod.router, prefix="/api/v1/catalog")
    who = {"user": ROOT}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    client = TestClient(app)

    def call(user, method, path, **kw):
        who["user"] = user
        return client.request(method, path, **kw)

    return {"db": db, "call": call, "mp": monkeypatch}


def _purge(env, pid, user=ROOT):
    return env["call"](user, "DELETE", f"/api/v1/catalog/products/{pid}/permanent")


def _bulk(env, ids, apply, user=ROOT):
    return env["call"](user, "POST", "/api/v1/catalog/products/bulk-purge",
                       json={"product_ids": ids, "apply": apply})


def _still_there(db, pid):
    return db.products.find_one({"product_id": pid}) is not None and \
        db.catalog_products.find_one({"id": f"T-{pid}"}) is not None


# ---------------------------------------------------------------------------
# Who, and only archived
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("roles", [["ADMIN"], ["CATALOG_MANAGER"], ["STORE_MANAGER"], ["CASHIER"]])
def test_only_superadmin(env, roles):
    user = {"user_id": "u-x", "roles": roles}
    assert _purge(env, "P1", user).status_code == 403
    assert _bulk(env, ["P1"], True, user).status_code == 403
    assert _still_there(env["db"], "P1")


def test_active_product_is_refused_archive_it_first(env):
    res = _purge(env, "P3")
    assert res.status_code == 409 and "Archive it first" in res.json()["detail"]
    assert _still_there(env["db"], "P3")


def test_archived_spine_with_live_twin_is_refused(env):
    env["db"].catalog_products.update_one({"id": "T-P2"}, {"$set": {"deleted_at": None}})
    assert _purge(env, "P2").status_code == 409
    assert _still_there(env["db"], "P2")


def test_unknown_product_is_404(env):
    assert _purge(env, "NOPE").status_code == 404


# ---------------------------------------------------------------------------
# History keeps it archived
# ---------------------------------------------------------------------------


# Written out by hand (review 6): a collection or field dropped from the
# code's own lists must turn these red, never shrink them with it.
EXPECTED_HISTORY = [
    "stock_units", "orders", "revised_invoices", "returns", "credit_note_ledger",
    "shopify_refund_review", "online_stock_miss", "purchase_orders", "grns",
    "goods_receipt_notes", "vendor_bills", "vendor_returns", "vendor_rmas",
    "vendor_debit_notes", "debit_notes", "stock_transfers", "stock_counts",
    "stock_shrinkage", "item_events", "opening_stock_batches", "serial_numbers",
    "workshop_jobs", "product_incentive_log", "display_placements",
]
EXPECTED_REF_FIELDS = [
    "product_id", "sku", "ims_product_id", "items.product_id", "items.ims_product_id",
    "items.sku", "lines.product_id", "lines.sku", "unresolved_lines.product_id",
    "proposed_restock.product_id", "proposed_restock.sku", "lens_details.product_id",
    "lens_product_id",
]


@pytest.mark.parametrize("coll", EXPECTED_HISTORY)
def test_every_history_collection_refuses(env, coll):
    env["db"][coll].insert_one({"items": [{"product_id": "P1", "quantity": 1}]})
    res = _purge(env, "P1")
    assert res.status_code == 409, coll
    assert "stays archived to keep the records complete" in res.json()["detail"]
    assert _still_there(env["db"], "P1")


@pytest.mark.parametrize("field", EXPECTED_REF_FIELDS)
def test_every_reference_shape_refuses(env, field):
    head, _, tail = field.partition(".")
    doc = {head: [{tail: "P1"}]} if tail else {head: "P1"}
    env["db"].orders.insert_one(doc)
    assert _purge(env, "P1").status_code == 409, field


@pytest.mark.parametrize(
    "coll,doc",
    [
        ("stock_units", {"product_id": "P1", "status": "SOLD"}),  # any status, ever
        ("orders", {"items": [{"sku": "SKU-P1"}]}),  # by sku only
        ("orders", {"items": [{"product_id": "gid://Product/9", "ims_product_id": "P1"}]}),  # online
        ("grns", {"items": [], "unresolved_lines": [{"product_id": "P1"}]}),
        ("opening_stock_batches", {"lines": [{"product_id": "P1"}]}),
        ("product_images", None),  # control: an image record alone is not history
    ],
)
def test_real_shapes(env, coll, doc):
    if doc is None:
        assert _purge(env, "P1").status_code == 200
        return
    env["db"][coll].insert_one(doc)
    assert _purge(env, "P1").status_code == 409


GID1 = "gid://shopify/Product/1"
GID4 = "gid://shopify/Product/4"
VGID2 = "gid://shopify/ProductVariant/22"
U2, U4 = "https://cdn.example.com/p2.jpg", "https://cdn.example.com/p4.jpg"
M2, M4 = "gid://shopify/MediaImage/2", "gid://shopify/MediaImage/4"


VG1 = "gid://shopify/ProductVariant/11"
VG4 = "gid://shopify/ProductVariant/44"


class _FakeShopify:
    """The ONLY network call (shopify_push._graphql), faked: the set of nodes
    that exist on Shopify, and what each listing holds ({product gid:
    {variant gid: sku}}); `fail` = gids whose delete Shopify refuses.
    `calls` = the writes (and the after-failure node probe); `reads` = the
    ground-truth reads made before any delete."""

    def __init__(self, nodes=(), listings=None):
        self.nodes = set(nodes)
        self.listings = listings if listings is not None else {
            GID1: {VG1: "SKU-P1"}, GID4: {VG4: "SKU-P4", VGID2: "SKU-P2"}}
        self.fail = set()
        self.lost_answer = set()  # Shopify deletes it but the answer is an error
        self.read_fail = {}  # gid -> "raise" | "errors" for a ground-truth read
        self.paged = set()  # listing gids whose variants run past one page
        self.media_fail = False
        self.search_lag = {}  # variant gid -> (sku, product gid): still in the search index
        self.on_media = None  # called while productDeleteMedia answers
        self.calls = []
        self.reads = []

    def _broken_read(self, gid):
        how = self.read_fail.get(gid)
        if how == "raise":
            raise RuntimeError("connection reset")
        if how == "errors":
            return {"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                    "data": {"node": None}}
        return None

    async def __call__(self, db, query, variables):
        if "imsSkuSearch" in query:
            sku = variables["q"].split('"')[1]
            self.reads.append(("sku", sku))
            hits = [{"id": v, "sku": s_, "product": {"id": pg}}
                    for pg, vs in self.listings.items() if pg in self.nodes
                    for v, s_ in vs.items() if s_ == sku and v in self.nodes]
            hits += [{"id": v, "sku": s_, "product": {"id": pg}}
                     for v, (s_, pg) in self.search_lag.items() if s_ == sku]
            return {"data": {"productVariants": {"nodes": hits}}}
        if "imsListingTruth" in query:
            gid = variables["id"]
            self.reads.append(("listing", gid))
            broken = self._broken_read(gid)
            if broken is not None:
                return broken
            if gid not in self.nodes:
                return {"data": {"node": None}}
            vs = [{"id": v, "sku": sku} for v, sku in self.listings.get(gid, {}).items()]
            return {"data": {"node": {"id": gid, "variants": {
                "pageInfo": {"hasNextPage": gid in self.paged}, "nodes": vs}}}}
        if "imsVariantTruth" in query:
            vid = variables["id"]
            self.reads.append(("variant", vid))
            broken = self._broken_read(vid)
            if broken is not None:
                return broken
            if vid not in self.nodes:
                return {"data": {"node": None}}
            for pg, vs in self.listings.items():
                if vid in vs:
                    return {"data": {"node": {"id": vid, "sku": vs[vid], "product": {"id": pg}}}}
            return {"data": {"node": {"id": vid, "sku": None, "product": None}}}
        if "productDelete(" in query:
            gid = variables["input"]["id"]
            self.calls.append(("productDelete", gid))
            if gid in self.lost_answer:
                self.nodes.discard(gid)
                return {"errors": [{"message": "Internal error (the delete landed)"}]}
            if gid in self.fail or gid not in self.nodes:
                msg = "Internal error" if gid in self.fail else "Product does not exist"
                return {"data": {"productDelete": {"deletedProductId": None,
                                                   "userErrors": [{"field": ["id"], "message": msg}]}}}
            self.nodes.discard(gid)
            return {"data": {"productDelete": {"deletedProductId": gid, "userErrors": []}}}
        if "productVariantsBulkDelete(" in query:
            (vid,) = variables["variantsIds"]
            self.calls.append(("productVariantsBulkDelete", variables["productId"], vid))
            if vid in self.fail or vid not in self.nodes:
                return {"data": {"productVariantsBulkDelete": {"product": None, "userErrors": [
                    {"field": ["variantsIds"], "message": "nope", "code": "X"}]}}}
            self.nodes.discard(vid)
            self.listings.get(variables["productId"], {}).pop(vid, None)
            return {"data": {"productVariantsBulkDelete": {"product": {"id": variables["productId"]},
                                                           "userErrors": []}}}
        if "productDeleteMedia(" in query:
            self.calls.append(("productDeleteMedia", variables["productId"], tuple(variables["mediaIds"])))
            if self.on_media:
                self.on_media()
            if self.media_fail:
                return {"data": {"productDeleteMedia": {"deletedMediaIds": [], "mediaUserErrors": [
                    {"field": ["mediaIds"], "message": "Media is processing"}]}}}
            return {"data": {"productDeleteMedia": {"deletedMediaIds": variables["mediaIds"],
                                                    "mediaUserErrors": []}}}
        if "node(id" in query:
            self.calls.append(("node", variables["id"]))
            return {"data": {"node": {"id": variables["id"]} if variables["id"] in self.nodes else None}}
        raise AssertionError("unexpected Shopify call: %s" % query[:80])


def _live(env, nodes=(), listings=None):
    fake = _FakeShopify(nodes, listings)
    mp = env["mp"]
    mp.setattr(shopify_push, "ims_shopify_writes_enabled", lambda: True)
    mp.setattr(shopify_push, "shopify_dispatch_mode", lambda: "live")
    mp.setattr(shopify_push, "_has_shopify_creds", lambda db, storefront_id="BV": True)
    mp.setattr(shopify_push, "_graphql", fake)
    return fake


def _on_shopify(db):
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {"ecom.shopify_product_id": GID1}})


def _size_of_live_model(db):
    """P2 (archived) is a size of the LIVE model P4: its variant rides P4's listing."""
    db.products.insert_one(_spine("P4", is_active=True, deleted_at=None))
    db.products.update_one({"product_id": "P2"}, {"$set": {"variant_of": "P4"}})
    db.catalog_products.insert_one(_twin("P4", is_active=True, deleted_at=None, images=[U4], ecom={
        "shopify_product_id": GID4, "title_sent": "Frame P4",
        "media_map": [{"url": U4, "id": M4}, {"url": U2, "id": M2}],
        "online_stock": {"quantities": {"SKU-P2": {"S1": 1}, "SKU-P4": {"S1": 2}}}}))
    db.catalog_products.update_one(
        {"id": "T-P2"},
        {"$set": {"images": [U2],
                  "ecom.variant_of": {"product_id": "P4", "twin_id": "T-P4", "sku": "SKU-P4"}}})
    db.catalog_variants.insert_one({"variant_id": "V2", "sku": "SKU-P2", "parent_product_id": "T-P4",
                                    "shopify_variant_id": VGID2})


@pytest.mark.parametrize(
    "setup",
    [
        _on_shopify,
        lambda db: db.products.update_one({"product_id": "P1"}, {"$set": {"shopify_product_id": GID1}}),
    ],
    ids=["twin-gid", "spine-gid"],
)
def test_dark_refuses_a_product_on_shopify(env, setup):
    setup(env["db"])
    res = _purge(env, "P1")
    assert res.status_code == 409
    assert "website writes are switched off" in res.json()["detail"]
    (dry,) = _bulk(env, ["P1"], False).json()["results"]
    assert dry["status"] == "refused" and dry["website"] is True
    assert "website writes are switched off" in dry["reason"]
    assert _still_there(env["db"], "P1")


def test_own_listing_is_deleted_on_shopify_then_in_ims(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    res = _purge(env, "P1")
    assert res.status_code == 200, res.text
    assert fake.calls == [("productDelete", GID1)]
    assert not db.products.find_one({"product_id": "P1"})
    (bk,) = list(db.deleted_products_backup.find())
    assert bk["shopify"]["plan"]["kind"] == "product"
    assert bk["shopify"]["plan"]["product_gid"] == GID1
    assert bk["shopify"]["result"]["action"] == "delete"
    resp = bk["shopify"]["result"]["payload"]["response"]
    assert resp["data"]["productDelete"]["deletedProductId"] == GID1
    (audit,) = list(db.audit_logs.find({"action": "product.deleted_permanently"}))
    assert audit["detail"]["shopify_removed"] == [GID1]


@pytest.mark.parametrize("how", ["size", "moved_lead"])
def test_a_size_deletes_only_its_variant_never_the_model(env, how):
    db = env["db"]
    _size_of_live_model(db)
    if how == "moved_lead":
        # A lead whose listing was handed to the next colour
        # (product_master._hand_over_listing): listing_moved_to + variant_of.
        db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"ecom.listing_moved_to": "T-P4"}})
    refreshed = []
    from api.services import product_master

    env["mp"].setattr(product_master, "refresh_family_options", lambda db_, lead: refreshed.append(lead))
    fake = _live(env, {GID4, VGID2})
    res = _purge(env, "P2")
    assert res.status_code == 200, res.text
    assert fake.calls == [("productVariantsBulkDelete", GID4, VGID2),
                          ("productDeleteMedia", GID4, (M2,))]  # never productDelete
    assert GID4 in fake.nodes  # the model's listing stays
    assert not db.catalog_variants.find_one({"variant_id": "V2"})
    lead = db.catalog_products.find_one({"id": "T-P4"})
    assert lead["ecom"]["shopify_product_id"] == GID4 and lead["ecom"]["title_sent"] == "Frame P4"
    assert lead["ecom"]["media_map"] == [{"url": U4, "id": M4}]  # only its photo pruned
    assert lead["ecom"]["online_stock"]["quantities"] == {"SKU-P4": {"S1": 2}}
    assert refreshed == ["P4"]
    (audit,) = list(db.audit_logs.find({"action": "product.deleted_permanently"}))
    assert audit["detail"]["shopify_removed"] == [VGID2]
    bk = db.deleted_products_backup.find_one()
    assert bk["updated_docs"]["catalog_products"][0]["ecom"]["media_map"][1]["id"] == M2


def test_lead_with_live_colours_is_refused_and_shopify_untouched(env):
    db = env["db"]
    _on_shopify(db)
    db.catalog_products.update_one(
        {"id": "T-P3"}, {"$set": {"ecom.variant_of": {"product_id": "P1", "twin_id": "T-P1"}}})
    fake = _live(env, {GID1})
    res = _purge(env, "P1")
    assert res.status_code == 409
    assert "Delete or move the other colours first" in res.json()["detail"]
    assert fake.calls == [] and _still_there(db, "P1")


@pytest.mark.parametrize("which", ["product", "variant"])
def test_shopify_failure_leaves_ims_untouched(env, which):
    db = env["db"]
    if which == "product":
        _on_shopify(db)
        fake = _live(env, {GID1})
        fake.fail.add(GID1)
        pid = "P1"
    else:
        _size_of_live_model(db)
        fake = _live(env, {GID4, VGID2})
        fake.fail.add(VGID2)
        pid = "P2"
    res = _purge(env, pid)
    assert res.status_code == 502
    assert "did not remove it, so it stays archived" in res.json()["detail"]
    assert _still_there(db, pid)
    # The backup was written BEFORE Shopify was asked; it is closed as aborted.
    assert [b["status"] for b in db.deleted_products_backup.find()] == ["aborted"]
    assert not db.audit_logs.find_one({"action": "product.deleted_permanently"})


def test_gate_closing_after_the_check_deletes_nothing(env):
    # The push answers SIMULATED (website writes switched off meanwhile):
    # nothing reached Shopify, so IMS must not delete either.
    db = env["db"]
    _on_shopify(db)
    _live(env, {GID1})

    async def _dark(db_, plan, target_id=None, product=None):
        return shopify_push.PushResult(mode=shopify_push.MODE_SIMULATED, entity="product",
                                       action="delete", ok=True, reason="writes_disabled")

    env["mp"].setattr(shopify_push, "push_product_remove", _dark)
    res = _purge(env, "P1")
    assert res.status_code == 409 and "website writes are switched off" in res.json()["detail"]
    assert _still_there(db, "P1")
    assert [b["status"] for b in db.deleted_products_backup.find()] == ["aborted"]


def test_already_gone_on_shopify_proceeds(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, set())  # not on Shopify any more
    res = _purge(env, "P1")
    assert res.status_code == 200, res.text
    # Gone, and its SKU sells nowhere else: nothing to delete.
    assert fake.reads == [("listing", GID1), ("sku", "SKU-P1")] and fake.calls == []
    assert not db.products.find_one({"product_id": "P1"})
    assert db.deleted_products_backup.find_one()["shopify"]["result"]["action"] == "already_gone"


def test_bulk_one_failure_never_stops_the_others(env):
    db = env["db"]
    _on_shopify(db)
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"ecom.shopify_product_id": GID4}})
    fake = _live(env, {GID1, GID4}, {GID1: {VG1: "SKU-P1"}, GID4: {VG4: "SKU-P2"}})
    fake.fail.add(GID1)
    dry = {r["product_id"]: r for r in _bulk(env, ["P1", "P2"], False).json()["results"]}
    assert dry["P1"]["website"] is True and dry["P1"]["status"] == "ready"
    assert fake.calls == []  # a dry run sends nothing
    rows = {r["product_id"]: r for r in _bulk(env, ["P1", "P2"], True).json()["results"]}
    assert rows["P1"]["status"] == "refused" and rows["P1"]["code"] == 502
    assert rows["P2"]["status"] == "done"
    assert _still_there(db, "P1") and not db.products.find_one({"product_id": "P2"})


def test_unclear_website_link_is_refused(env):
    # A website variant id with no listing id anywhere: nothing is guessed.
    env["db"].catalog_variants.update_one({"variant_id": "V1"}, {"$set": {"shopify_variant_id": VGID2}})
    fake = _live(env, {VGID2})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "website link is unclear" in res.json()["detail"]
    assert fake.calls == [] and _still_there(env["db"], "P1")


def test_dropped_from_collection_member_lists(env):
    db = env["db"]
    db.ecom_collections.insert_one({"collection_id": "C1", "collection_type": "CUSTOM",
                                    "products": [{"sku": "SKU-P1", "position": 0},
                                                 {"sku": "OTHER", "position": 1}]})
    assert _purge(env, "P1").status_code == 200
    left = db.ecom_collections.find_one({"collection_id": "C1"})["products"]
    assert left == [{"sku": "OTHER", "position": 1}]
    bk = db.deleted_products_backup.find_one()
    assert len(bk["updated_docs"]["ecom_collections"][0]["products"]) == 2


@pytest.mark.parametrize("where", ["spine", "twin"])
def test_family_lead_refuses(env, where):
    db = env["db"]
    if where == "spine":
        db.products.update_one({"product_id": "P2"}, {"$set": {"variant_of": "P1"}})
    else:
        db.catalog_products.update_one(
            {"id": "T-P2"}, {"$set": {"ecom.variant_of": {"product_id": "P1", "twin_id": "T-P1"}}})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "colours or sizes" in res.json()["detail"]
    # The variant itself (it leads nothing) may go.
    assert _purge(env, "P2").status_code == 200


@pytest.mark.parametrize("bad", ["orders", "stock_units", "catalog_variants", "products"])
def test_a_read_that_fails_refuses_503(env, bad):
    env["mp"].setattr(catalog_mod, "_get_db", lambda: _Broken(env["db"], bad))
    res = _purge(env, "P1")
    assert res.status_code == 503
    assert _still_there(env["db"], "P1")


# ---------------------------------------------------------------------------
# Backup, then delete
# ---------------------------------------------------------------------------


def test_backup_then_delete_and_audit(env):
    db = env["db"]
    res = _purge(env, "P1")
    assert res.status_code == 200, res.text
    assert not db.products.find_one({"product_id": "P1"})
    assert not db.catalog_products.find_one({"id": "T-P1"})
    assert not db.catalog_variants.find_one({"variant_id": "V1"})
    assert not db.product_images.find_one({"image_id": "I1"})
    assert not db.vendor_sku_aliases.find_one({"product_id": "P1"})
    (bk,) = list(db.deleted_products_backup.find())
    assert bk["backup_id"] == res.json()["backup_id"] and bk["actor"] == "u-root"
    assert bk["product_id"] == "P1" and bk["run_id"].startswith("PURGE-")
    assert bk["docs"]["products"][0]["identity_key"] == KEY
    assert bk["docs"]["catalog_products"][0]["id"] == "T-P1"
    assert bk["docs"]["catalog_variants"][0]["variant_id"] == "V1"
    assert bk["docs"]["product_images"][0]["image_id"] == "I1"
    (audit,) = list(db.audit_logs.find({"action": "product.deleted_permanently"}))
    assert audit["entity_id"] == "P1" and audit["detail"]["backup_id"] == bk["backup_id"]
    # Other products untouched.
    assert _still_there(db, "P2") and _still_there(db, "P3")


def test_failed_backup_deletes_nothing(env):
    env["mp"].setattr(catalog_mod, "_get_db",
                      lambda: _Broken(env["db"], product_purge.BACKUP_COLLECTION))
    res = _purge(env, "P1")
    assert res.status_code == 503
    db = env["db"]
    assert _still_there(db, "P1")
    assert db.catalog_variants.find_one({"variant_id": "V1"})
    assert db.product_images.find_one({"image_id": "I1"})


def test_identity_key_is_freed(env):
    db = env["db"]
    with pytest.raises(Exception):
        db.products.insert_one(_spine("P9", identity_key=KEY, deleted_at=None))
    assert _purge(env, "P1").status_code == 200
    db.products.insert_one(_spine("P9", identity_key=KEY, deleted_at=None))
    assert db.products.find_one({"identity_key": KEY})["product_id"] == "P9"


# ---------------------------------------------------------------------------
# The bulk door: dry run, then apply, per-product reasons
# ---------------------------------------------------------------------------


def test_bulk_dry_run_then_apply(env):
    db = env["db"]
    db.stock_units.insert_one({"product_id": "P2", "status": "AVAILABLE"})
    dry = _bulk(env, ["P1", "P2", "P3"], False).json()["results"]
    by = {r["product_id"]: r for r in dry}
    assert by["P1"]["status"] == "ready" and by["P1"]["sku"] == "SKU-P1"
    assert by["P2"]["status"] == "refused" and "stock units" in by["P2"]["reason"]
    assert by["P3"]["status"] == "refused" and "Archive it first" in by["P3"]["reason"]
    assert _still_there(db, "P1")  # a dry run deletes nothing
    applied = {r["product_id"]: r for r in _bulk(env, ["P1", "P2"], True).json()["results"]}
    assert applied["P1"]["status"] == "done" and applied["P2"]["status"] == "refused"
    assert not db.products.find_one({"product_id": "P1"}) and _still_there(db, "P2")


# ---------------------------------------------------------------------------
# Review round 1 (#1273): check and delete tied, one twin, own rows only,
# leftovers, restore after a part-delete, and the Shopify extension's edges
# ---------------------------------------------------------------------------


def test_restored_while_shopify_was_answering_deletes_nothing_in_ims(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    real = fake.__call__

    async def _restore_meanwhile(db_, query, variables):
        if "productDelete(" in query:  # someone presses Restore mid-flight
            db.products.update_one({"product_id": "P1"}, {"$set": {"deleted_at": None}})
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _restore_meanwhile)
    res = _purge(env, "P1")
    assert res.status_code == 409
    assert "Archive it first" in res.json()["detail"] and "taken off the website" in res.json()["detail"]
    assert db.products.find_one({"product_id": "P1"}) and db.catalog_products.find_one({"id": "T-P1"})
    # The dead listing id is dropped: it reads "not on the website" now.
    assert "shopify_product_id" not in db.catalog_products.find_one({"id": "T-P1"})["ecom"]
    assert db.deleted_products_backup.find_one()["status"] == "aborted"


def test_a_refused_apply_writes_no_backup_and_sends_nothing(env):
    # The delete checks again itself, after the claim and BEFORE the backup:
    # a product with history is refused with nothing written or sent.
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    db.stock_units.insert_one({"product_id": "P1", "status": "SOLD"})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "stock units" in res.json()["detail"]
    assert db.deleted_products_backup.count_documents({}) == 0
    assert fake.calls == [] and fake.reads == []
    assert db.products.find_one({"product_id": "P1"})["purge_run"] is None  # claim given back


def test_the_claim_takes_it_off_the_sync_queue_and_gives_it_back(env):
    db = env["db"]
    _on_shopify(db)
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {"ecom.locally_modified": True}})
    fake = _live(env, {GID1})
    fake.fail.add(GID1)
    real = fake.__call__
    seen = []

    async def _watch(db_, query, variables):
        seen.append(db.catalog_products.find_one({"id": "T-P1"})["ecom"]["locally_modified"])
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _watch)
    assert _purge(env, "P1").status_code == 502
    assert seen and not any(seen)  # never queued while Shopify was asked
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert twin["ecom"]["locally_modified"] is True and "purge_run" not in twin


@pytest.mark.parametrize("live_row", [False, True])
def test_a_second_catalogue_row_refuses(env, live_row):
    extra = {"id": "P1", "sku": "SKU-P1", "deleted_at": None if live_row else GONE}
    env["db"].catalog_products.insert_one(extra)
    res = _purge(env, "P1")
    assert res.status_code == 409
    assert ("Archive it first" if live_row else "More than one catalogue row") in res.json()["detail"]
    assert _still_there(env["db"], "P1")


def test_another_skus_row_under_it_refuses_and_is_never_taken(env):
    db = env["db"]
    db.catalog_variants.insert_one({"variant_id": "VX", "sku": "SKU-X", "parent_product_id": "T-P1"})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "Other sizes" in res.json()["detail"]
    db.catalog_variants.update_one({"variant_id": "VX"}, {"$set": {"parent_product_id": "T-ELSE"}})
    assert _purge(env, "P1").status_code == 200
    assert db.catalog_variants.find_one({"variant_id": "VX"})  # another SKU's row stays
    assert not db.catalog_variants.find_one({"variant_id": "V1"})


@pytest.mark.parametrize("field", ["shopify_variant_id", "shopify_inventory_item_id"])
def test_the_twins_own_variant_ids_count_as_a_website_link(env, field):
    # With no catalog_variants row of its own, the twin's ecom ids are its link.
    env["db"].catalog_variants.delete_many({})
    env["db"].catalog_products.update_one({"id": "T-P1"}, {"$set": {f"ecom.{field}": "gid://x/1"}})
    _live(env)
    res = _purge(env, "P1")
    assert res.status_code == 409 and "website link is unclear" in res.json()["detail"]


def test_named_in_a_promotion_refuses(env):
    env["db"].promo_rules.insert_one({"promo_id": "PR-1", "name": "Diwali pair", "product_ids": ["P1"]})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "Diwali pair" in res.json()["detail"]


def test_a_website_order_not_yet_booked_refuses(env):
    db = env["db"]
    db.webhook_inbox.insert_one({"vendor": "shopify", "processed": False,
                                 "headers": {"x-shopify-topic": "orders/create"},
                                 "payload": {"id": 999, "line_items": [{"sku": "SKU-P1", "quantity": 1}]}})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "not been brought into IMS" in res.json()["detail"]
    # A fulfillment (not an order) naming it does not count.
    db.webhook_inbox.update_one({}, {"$set": {"headers.x-shopify-topic": "fulfillments/create"}})
    assert _purge(env, "P1").status_code == 200


def test_the_products_delete_webhook_is_never_imported():
    from agents.implementations.nexus import NexusAgent

    assert "products/" in NexusAgent._SHOPIFY_EDIT_ONLY_PREFIXES
    assert not any(t.startswith("products/") for t in NexusAgent._SHOPIFY_TOPIC_HANDLERS)
    assert not any(t.startswith("products/") for t in NexusAgent._SHOPIFY_ORDER_TOPICS)


def test_leftovers_are_cleaned_with_a_backup(env):
    db = env["db"]
    _on_shopify(db)
    item = "gid://shopify/InventoryItem/9"
    db.catalog_variants.update_one({"variant_id": "V1"}, {"$set": {"shopify_inventory_item_id": item}})
    db.shopify_stock_write_guard.insert_many([
        {"_id": f"{item}|gid://shopify/Location/1", "seq": 4},
        {"_id": "gid://shopify/InventoryItem/90|gid://shopify/Location/1", "seq": 5},
    ])
    db.tasks.insert_one({"task_id": "K1", "payload": {"kind": "catalog_flag", "product_ids": ["P1", "P2"]}})
    db.users.insert_one({"user_id": "u-root", "full_name": "Avinash Kumar"})
    _live(env, {GID1})
    assert _purge(env, "P1").status_code == 200
    assert [g["_id"] for g in db.shopify_stock_write_guard.find()] == [
        "gid://shopify/InventoryItem/90|gid://shopify/Location/1"]
    assert db.tasks.find_one({"task_id": "K1"})["payload"]["product_ids"] == ["P2"]
    bk = db.deleted_products_backup.find_one()
    assert bk["docs"]["shopify_stock_write_guard"][0]["seq"] == 4
    assert bk["updated_docs"]["tasks"][0]["payload"]["product_ids"] == ["P1", "P2"]
    assert db.audit_logs.find_one({"action": "product.deleted_permanently"})["actor_name"] == "Avinash Kumar"


class _SpineDeleteFails:
    """A db whose `products` delete_many raises (a stop part-way)."""

    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        coll = self._db[name]
        if name != "products":
            return coll

        class _Coll:
            def __getattr__(self, attr):
                if attr == "delete_many":
                    def _boom(*a, **k):
                        raise RuntimeError("connection reset")
                    return _boom
                return getattr(coll, attr)

        return _Coll()

    def get_collection(self, name):
        return self[name]


def test_a_part_delete_blocks_restore_and_pressing_again_finishes(env):
    db = env["db"]
    env["mp"].setattr(catalog_mod, "_get_db", lambda: _SpineDeleteFails(db))
    res = _purge(env, "P1")
    assert res.status_code == 503 and "Press Delete permanently again" in res.json()["detail"]
    assert db.products.find_one({"product_id": "P1"}) and not db.catalog_products.find_one({"id": "T-P1"})
    assert db.deleted_products_backup.find_one()["status"] == "deleting"
    env["mp"].setattr(catalog_mod, "_get_db", lambda: db)
    (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                         json={"product_ids": ["P1"], "apply": False}).json()["results"]
    assert row["status"] == "refused" and "being deleted permanently" in row["reason"]
    assert _purge(env, "P1").status_code == 200
    assert not db.products.find_one({"product_id": "P1"})
    assert {b["status"] for b in db.deleted_products_backup.find()} == {"done"}


# ---------------------------------------------------------------------------
# The rbac rows equal the code gates
# ---------------------------------------------------------------------------


def test_rbac_rows_are_superadmin_only():
    def row(method, path):
        (r,) = [r for r in rbac_policy.POLICY if r["method"] == method and r["path"] == path]
        return set(r["allowed"])

    assert row("DELETE", "/api/v1/catalog/products/{product_id}/permanent") == {"SUPERADMIN"}
    assert row("POST", "/api/v1/catalog/products/bulk-purge") == {"SUPERADMIN"}


# ---------------------------------------------------------------------------
# Re-review of ed42c746 (#1273): each repro below failed (or deleted the
# wrong thing) before its fix
# ---------------------------------------------------------------------------


def _two_colour_model(db):
    """A (archived, P2) and B (live, P5) are colours of the live model P4."""
    _size_of_live_model(db)
    db.products.insert_one(_spine("P5", is_active=True, deleted_at=None, variant_of="P4"))
    db.catalog_products.insert_one(_twin("P5", is_active=True, deleted_at=None, ecom={
        "variant_of": {"product_id": "P4", "twin_id": "T-P4", "sku": "SKU-P4"}}))


def test_review1_adopted_variant_on_the_twin_is_never_deleted(env):
    # BLOCK 1: B took over A's variant (writeback._writeback_variant): A's
    # row lost the ids (variant_taken_over_by), A's twin ecom still names
    # them. Deleting A must not touch B's live variant.
    db = env["db"]
    _two_colour_model(db)
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {
        "ecom.shopify_variant_id": VGID2, "ecom.shopify_inventory_item_id": "gid://shopify/InventoryItem/2"}})
    db.catalog_variants.update_one({"sku": "SKU-P2"}, {"$unset": {"shopify_variant_id": ""},
                                                       "$set": {"variant_taken_over_by": "SKU-P5"}})
    db.catalog_variants.insert_one({"sku": "SKU-P5", "parent_product_id": "T-P4", "shopify_variant_id": VGID2,
                                    "shopify_inventory_item_id": "gid://shopify/InventoryItem/2"})
    fake = _live(env, {GID4, VGID2}, {GID4: {VG4: "SKU-P4", VGID2: "SKU-P5"}})
    res = _purge(env, "P2")
    assert res.status_code == 200, res.text
    assert fake.calls == [] and VGID2 in fake.nodes  # B's variant untouched
    assert db.catalog_variants.find_one({"sku": "SKU-P5"})["shopify_variant_id"] == VGID2


def test_review1_a_lone_twin_variant_id_another_row_holds_is_unclear(env):
    db = env["db"]
    db.catalog_variants.delete_many({})
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {"ecom.shopify_product_id": GID1,
                                                             "ecom.shopify_variant_id": VGID2}})
    db.catalog_variants.insert_one({"sku": "SKU-OTHER", "parent_product_id": "T-X", "shopify_variant_id": "22"})
    fake = _live(env, {GID1})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "website link is unclear" in res.json()["detail"]
    assert fake.calls == [] and fake.reads == []


def test_review2_a_colour_linked_only_by_its_spine_deletes_only_its_variant(env):
    # BLOCK 2: a half-finished hand-over left the colour's twin holding the
    # model's listing id with no ecom.variant_of; only spine.variant_of says
    # it is a colour. Never productDelete the model.
    db = env["db"]
    _size_of_live_model(db)
    db.catalog_products.update_one({"id": "T-P2"}, {"$unset": {"ecom.variant_of": ""},
                                                    "$set": {"ecom.shopify_product_id": GID4}})
    fake = _live(env, {GID4, VGID2})
    res = _purge(env, "P2")
    assert res.status_code == 200, res.text
    assert [c[0] for c in fake.calls if c[0] != "productDeleteMedia"] == ["productVariantsBulkDelete"]
    assert GID4 in fake.nodes


def test_review2_a_listing_id_another_catalogue_row_holds_is_unclear(env):
    db = env["db"]
    _on_shopify(db)
    db.catalog_products.insert_one({"id": "T-X", "sku": "SKU-X", "ecom": {"shopify_product_id": "1"}})
    fake = _live(env, {GID1})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "website link is unclear" in res.json()["detail"]
    assert fake.calls == [] and fake.reads == []


@pytest.mark.parametrize("dark", [True, False])
def test_review3_a_twin_linked_only_by_sku_refuses(env, dark):
    # BLOCK 3: no pim / id link, the SKU is the only link (as has_listing and
    # online_delist read it). Before: the spine went, the twin and its
    # listing were orphaned, the dry run said website=false.
    db = env["db"]
    db.products.update_one({"product_id": "P2"}, {"$unset": {"pim_product_id": ""}})
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"ecom.shopify_product_id": GID1}})
    fake = None if dark else _live(env, {GID1})
    (dry,) = _bulk(env, ["P2"], False).json()["results"]
    assert dry["status"] == "refused" and "only by SKU" in dry["reason"]
    res = _purge(env, "P2")
    assert res.status_code == 409 and "only by SKU" in res.json()["detail"]
    assert db.products.find_one({"product_id": "P2"}) and db.catalog_products.find_one({"id": "T-P2"})
    assert fake is None or fake.calls == []


def test_ground_truth_a_listing_holding_another_colour_deletes_nothing(env):
    # A variant IMS does not know of (Shopify admin, a merge revert, BVI).
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1}, {GID1: {VG1: "SKU-P1", "gid://shopify/ProductVariant/99": "SKU-STRAY"}})
    res = _purge(env, "P1")
    assert res.status_code == 502 and "holds other colours" in res.json()["detail"]
    assert fake.reads == [("listing", GID1)] and fake.calls == []
    assert _still_there(db, "P1") and GID1 in fake.nodes


@pytest.mark.parametrize("shopify_says", ["other_sku", "other_listing"])
def test_ground_truth_a_variant_that_is_not_this_products_is_never_deleted(env, shopify_says):
    db = env["db"]
    _size_of_live_model(db)
    if shopify_says == "other_sku":
        listing = {GID4: {VG4: "SKU-P4", VGID2: "SKU-P9"}}
    else:
        listing = {"gid://shopify/Product/7": {VGID2: "SKU-P2"}, GID4: {VG4: "SKU-P4"}}
    fake = _live(env, {GID4, VGID2}, listing)
    res = _purge(env, "P2")
    assert res.status_code == 502, res.text
    assert fake.reads == [("variant", VGID2)] and fake.calls == []
    assert _still_there(db, "P2")


def test_ground_truth_already_gone_needs_no_delete(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, set())
    assert _purge(env, "P1").status_code == 200
    assert fake.reads == [("listing", GID1), ("sku", "SKU-P1")] and fake.calls == []


def test_review4_the_plan_is_read_fresh_and_a_changed_plan_stops_it(env):
    # SHOULD-FIX: B takes over A's variant on another worker after the backup:
    # the delete must not send the old plan.
    db = env["db"]
    _two_colour_model(db)
    real = product_purge._gather
    seen = []

    def _adopt_after_first_gather(*a, **k):
        out = real(*a, **k)
        if not seen:
            seen.append(1)
            db.catalog_variants.update_one({"sku": "SKU-P2"}, {"$unset": {"shopify_variant_id": ""},
                                                               "$set": {"variant_taken_over_by": "SKU-P5"}})
            db.catalog_variants.insert_one({"sku": "SKU-P5", "parent_product_id": "T-P4",
                                            "shopify_variant_id": VGID2})
        return out

    env["mp"].setattr(product_purge, "_gather", _adopt_after_first_gather)
    fake = _live(env, {GID4, VGID2})
    res = _purge(env, "P2")
    assert res.status_code == 409 and "changed meanwhile" in res.json()["detail"]
    assert fake.calls == [] and VGID2 in fake.nodes
    assert [b["status"] for b in db.deleted_products_backup.find()] == ["aborted"]


def test_review5_two_runs_never_share_the_claim(env):
    # SHOULD-FIX: a re-press while the first run waits on Shopify. One run
    # gets "already running"; the queue flag is given back exactly once.
    import asyncio

    db = env["db"]
    _on_shopify(db)
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {"ecom.locally_modified": True}})
    fake = _live(env, {GID1})
    fake.fail.add(GID1)
    real = fake.__call__

    async def _slow(db_, query, variables):
        await asyncio.sleep(0.3)
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _slow)

    async def _both():
        return await asyncio.gather(catalog_mod._purge_rows(["P1"], ROOT, True),
                                    catalog_mod._purge_rows(["P1"], ROOT, True))

    (a,), (b,) = asyncio.run(_both())
    assert sorted([a["code"], b["code"]]) == [409, 502]
    assert "already running" in (a if a["code"] == 409 else b)["reason"]
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert twin["ecom"]["locally_modified"] is True and "purge_run" not in twin
    assert [c[0] for c in fake.calls].count("productDelete") == 1


@pytest.mark.parametrize("minutes_ago,expect", [(60, 200), (5, 409)])
def test_review5_only_a_dead_claim_is_taken_over(env, minutes_ago, expect):
    from datetime import timedelta, timezone

    at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    env["db"].products.update_one({"product_id": "P1"}, {"$set": {"purge_run": "PURGE-OLD", "purge_run_at": at}})
    assert _purge(env, "P1").status_code == expect


def test_review5_restore_cannot_take_a_product_a_delete_holds(env):
    from datetime import timezone

    db = env["db"]
    db.products.update_one({"product_id": "P1"},
                           {"$set": {"purge_run": "PURGE-LIVE", "purge_run_at": datetime.now(timezone.utc)}})
    for apply in (False, True):
        (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                             json={"product_ids": ["P1"], "apply": apply}).json()["results"]
        assert row["status"] == "refused" and "running" in row["reason"]
    assert db.products.find_one({"product_id": "P1"})["deleted_at"] == GONE


def test_review5_the_restore_write_itself_is_fenced_by_the_claim(env):
    # The race across workers: Restore read "free", then the purge claimed.
    from datetime import timezone

    db = env["db"]
    env["mp"].setattr(catalog_mod, "_purge_open", lambda spine: None)  # read before the claim
    db.products.update_one({"product_id": "P1"},
                           {"$set": {"purge_run": "PURGE-LIVE", "purge_run_at": datetime.now(timezone.utc)}})
    (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                         json={"product_ids": ["P1"], "apply": True}).json()["results"]
    assert row["status"] == "refused"
    assert db.products.find_one({"product_id": "P1"})["deleted_at"] == GONE
    assert db.catalog_products.find_one({"id": "T-P1"})["deleted_at"] == GONE


def test_nit6_a_stale_removing_backup_lets_restore_through(env):
    from datetime import timedelta, timezone

    db = env["db"]
    db.deleted_products_backup.insert_one({"backup_id": "DPB-OLD", "product_id": "P1", "status": "removing",
                                           "deleted_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                         json={"product_ids": ["P1"], "apply": False}).json()["results"]
    assert row["status"] == "ready"


def test_review6_the_colours_design_row_photos_come_off_the_model_listing(env):
    db = env["db"]
    _size_of_live_model(db)
    db.product_images.insert_one({"image_id": "I2", "product_id": "T-P2", "url": "https://cdn.example.com/d2.jpg",
                                  "status": "APPROVED", "shopify_image_id": "gid://shopify/MediaImage/77"})
    fake = _live(env, {GID4, VGID2})
    assert _purge(env, "P2").status_code == 200
    (media,) = [c for c in fake.calls if c[0] == "productDeleteMedia"]
    assert set(media[2]) == {M2, "gid://shopify/MediaImage/77"}


def test_review14_a_photo_another_colour_still_uses_stays(env):
    db = env["db"]
    _two_colour_model(db)
    db.catalog_products.update_one({"id": "T-P5"}, {"$set": {"images": [U2]}})
    fake = _live(env, {GID4, VGID2})
    assert _purge(env, "P2").status_code == 200
    assert fake.calls == [("productVariantsBulkDelete", GID4, VGID2)]
    assert {r["id"] for r in db.catalog_products.find_one({"id": "T-P4"})["ecom"]["media_map"]} == {M4, M2}


def test_review7_an_unsettled_create_claim_is_unclear(env):
    db = env["db"]
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {"ecom.create_claim": {"state": "UNCERTAIN"}}})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "may have created it" in res.json()["detail"]


class _DeleteFails:
    """A db whose `name` delete_many raises (a stop part-way)."""

    def __init__(self, db, name):
        self._db, self._name = db, name

    def __getitem__(self, name):
        coll = self._db[name]
        if name != self._name:
            return coll
        bad = self._name

        class _Coll:
            def __getattr__(self, attr):
                if attr == "delete_many":
                    def _boom(*a, **k):
                        raise RuntimeError(bad + ": connection reset")
                    return _boom
                return getattr(coll, attr)

        return _Coll()

    def get_collection(self, name):
        return self[name]


def test_nit2_pressing_again_after_shopify_finished_skips_shopify_and_the_dark_gate(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    env["mp"].setattr(catalog_mod, "_get_db", lambda: _DeleteFails(db, "catalog_products"))
    assert _purge(env, "P1").status_code == 503
    assert fake.calls == [("productDelete", GID1)] and db.catalog_products.find_one({"id": "T-P1"})
    # Website writes switched off now: the second press still finishes.
    env["mp"].setattr(shopify_push, "ims_shopify_writes_enabled", lambda: False)
    env["mp"].setattr(catalog_mod, "_get_db", lambda: db)
    res = _purge(env, "P1")
    assert res.status_code == 200, res.text
    assert fake.calls == [("productDelete", GID1)]  # no second Shopify call
    assert not db.products.find_one({"product_id": "P1"}) and not db.catalog_products.find_one({"id": "T-P1"})


def test_review15_a_restore_after_the_last_check_keeps_the_spine(env):
    # The "only while still archived" delete filter (review 15, mutation A).
    db = env["db"]
    real = product_purge._gather
    calls = []

    def _restore_on_second_gather(*a, **k):
        calls.append(1)
        out = real(*a, **k)
        if len(calls) == 2:  # inside _after_shopify, after its re-check
            db.products.update_one({"product_id": "P1"}, {"$set": {"deleted_at": None}})
        return out

    env["mp"].setattr(product_purge, "_gather", _restore_on_second_gather)
    res = _purge(env, "P1")
    assert res.status_code == 503 and "stopped part-way" in res.json()["detail"]
    assert db.products.find_one({"product_id": "P1"})
    assert db.deleted_products_backup.find_one()["status"] == "deleting"


def test_review15_a_twin_restored_before_the_claim_is_never_claimed(env):
    # The claim's "still archived" filter (review 15, mutation B).
    db = env["db"]
    real = product_purge._claim

    def _restore_then_claim(db_, pid, twin_id, run_id):
        db.catalog_products.update_one({"id": twin_id}, {"$set": {"deleted_at": None,
                                                                  "ecom.locally_modified": True}})
        return real(db_, pid, twin_id, run_id)

    env["mp"].setattr(product_purge, "_claim", _restore_then_claim)
    res = _purge(env, "P1")
    assert res.status_code == 409 and "changed meanwhile" in res.json()["detail"]
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert "purge_run" not in twin and twin["ecom"]["locally_modified"] is True
    assert db.deleted_products_backup.count_documents({}) == 0


def test_nit7_a_family_row_deletes_its_colours_then_the_lead(env):
    db = env["db"]
    db.products.update_one({"product_id": "P2"}, {"$set": {"variant_of": "P1"}})
    dry = {r["product_id"]: r for r in _bulk(env, ["P1", "P2"], False).json()["results"]}
    assert dry["P2"]["status"] == "ready"
    assert dry["P1"]["status"] == "ready" and "after its colours" in dry["P1"]["note"]
    rows = {r["product_id"]: r for r in _bulk(env, ["P1", "P2"], True).json()["results"]}
    assert rows["P1"]["status"] == "done" and rows["P2"]["status"] == "done"
    # Alone, a lead still refuses while a colour is linked.
    db.products.insert_one(_spine("P7", variant_of="P3"))
    db.products.update_one({"product_id": "P3"}, {"$set": {"deleted_at": GONE}})
    db.catalog_products.update_one({"id": "T-P3"}, {"$set": {"deleted_at": GONE}})
    assert "colours" in _purge(env, "P3").json()["detail"]


def test_nit8_the_permanent_delete_is_its_own_ungrantable_capability():
    from api.services.capabilities import capability_for, is_ungrantable

    for method, path in (("DELETE", "/api/v1/catalog/products/{product_id}/permanent"),
                         ("POST", "/api/v1/catalog/products/bulk-purge")):
        assert capability_for(method, path) == "catalog:purge"
    assert is_ungrantable("catalog:purge")
    assert capability_for("DELETE", "/api/v1/catalog/products/{product_id}") == "catalog:write"


def test_nit5_rows_added_while_shopify_answered_are_backed_up_and_deleted(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    real = fake.__call__

    async def _late_image(db_, query, variables):
        if "productDelete(" in query:
            db.product_images.insert_one({"image_id": "I-LATE", "product_id": "T-P1",
                                          "url": "https://x/late.jpg"})
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _late_image)
    assert _purge(env, "P1").status_code == 200
    assert not db.product_images.find_one({"image_id": "I-LATE"})
    bk = db.deleted_products_backup.find_one()
    assert {r["image_id"] for r in bk["docs"]["product_images"]} == {"I1", "I-LATE"}


def test_review11_a_take_over_never_gives_back_a_stale_queue_flag(env):
    # A run died holding the claim (its False on the twin, the real flag kept
    # as purge_prior_queue). The next press takes the dead claim over and,
    # when Shopify then refuses, gives back the REAL flag, not False.
    from datetime import timedelta, timezone

    db = env["db"]
    _on_shopify(db)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    db.products.update_one({"product_id": "P1"}, {"$set": {"purge_run": "PURGE-DEAD", "purge_run_at": old}})
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {
        "purge_run": "PURGE-DEAD", "purge_run_at": old, "purge_prior_queue": True,
        "ecom.locally_modified": False}})
    fake = _live(env, {GID1})
    fake.fail.add(GID1)
    assert _purge(env, "P1").status_code == 502
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert twin["ecom"]["locally_modified"] is True and "purge_run" not in twin


# ---------------------------------------------------------------------------
# Guards the first mutation pass found untested (reverting them stayed green)
# ---------------------------------------------------------------------------


def test_a_claim_taken_over_before_shopify_sends_nothing(env):
    # Another run took the claim over between the backup and the Shopify
    # call (the product is still archived): nothing goes to Shopify.
    db = env["db"]
    _on_shopify(db)
    real = product_purge._gather
    seen = []

    def _take_over_after_backup_gather(*a, **k):
        out = real(*a, **k)
        if not seen:
            seen.append(1)
            db.products.update_one({"product_id": "P1"}, {"$set": {"purge_run": "PURGE-OTHER"}})
        return out

    env["mp"].setattr(product_purge, "_gather", _take_over_after_backup_gather)
    fake = _live(env, {GID1})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "changed meanwhile" in res.json()["detail"]
    assert fake.calls == [] and fake.reads == []
    assert _still_there(db, "P1")


def test_the_spine_is_deleted_only_while_this_run_holds_it(env):
    # Another run took the claim over after the last check: this run's
    # delete leaves the spine to it.
    db = env["db"]
    real = product_purge._gather
    calls = []

    def _take_over_on_second_gather(*a, **k):
        calls.append(1)
        out = real(*a, **k)
        if len(calls) == 2:
            db.products.update_one({"product_id": "P1"}, {"$set": {"purge_run": "PURGE-OTHER"}})
        return out

    env["mp"].setattr(product_purge, "_gather", _take_over_on_second_gather)
    res = _purge(env, "P1")
    assert res.status_code == 503 and "stopped part-way" in res.json()["detail"]
    assert db.products.find_one({"product_id": "P1"})


def test_review2_only_the_spine_says_it_is_a_colour(env):
    # No catalog_variants row of its own and no ecom.variant_of: the spine's
    # variant_of alone makes it a colour of the model. Only its variant goes.
    db = env["db"]
    _size_of_live_model(db)
    db.catalog_variants.delete_many({"sku": "SKU-P2"})
    db.catalog_products.update_one({"id": "T-P2"}, {"$unset": {"ecom.variant_of": ""}, "$set": {
        "ecom.shopify_product_id": GID4, "ecom.shopify_variant_id": VGID2}})
    fake = _live(env, {GID4, VGID2})
    res = _purge(env, "P2")
    assert res.status_code == 200, res.text
    assert [c[:3] for c in fake.calls if c[0] != "productDeleteMedia"] == [
        ("productVariantsBulkDelete", GID4, VGID2)]
    assert GID4 in fake.nodes


def test_a_delete_whose_answer_was_lost_but_landed_counts_as_done(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    fake.lost_answer.add(GID1)
    res = _purge(env, "P1")
    assert res.status_code == 200, res.text
    assert fake.calls == [("productDelete", GID1), ("node", GID1)]
    assert db.deleted_products_backup.find_one()["shopify"]["result"]["action"] == "already_gone"


# ---------------------------------------------------------------------------
# Review 3 of #1273 (54fda8dc): each repro below failed before its fix
# ---------------------------------------------------------------------------


def test_r3_1_a_colour_holding_the_models_listing_id_can_be_deleted_after_a_refusal(env):
    # The variant went, then a promotion naming it landed: refused, kept. The
    # colour's twin held the MODEL's listing id; left there, every later
    # press said "website link unclear". Now the second press goes through.
    db = env["db"]
    _size_of_live_model(db)
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"ecom.shopify_product_id": GID4}})
    fake = _live(env, {GID4, VGID2})
    real = fake.__call__

    async def _promo_meanwhile(db_, query, variables):
        if "productVariantsBulkDelete(" in query:
            db.promo_rules.insert_one({"promo_id": "PR-1", "name": "Diwali", "product_ids": ["P2"]})
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _promo_meanwhile)
    res = _purge(env, "P2")
    assert res.status_code == 409 and "already taken off the website" in res.json()["detail"]
    assert "shopify_product_id" not in db.catalog_products.find_one({"id": "T-P2"})["ecom"]
    db.promo_rules.delete_many({})
    assert _purge(env, "P2").status_code == 200


@pytest.mark.parametrize("kind", ["variant", "product"])
def test_r3_2_gone_is_not_trusted_while_its_sku_sells_under_another_id(env, kind):
    db = env["db"]
    if kind == "variant":
        _size_of_live_model(db)
        stray = "gid://shopify/ProductVariant/555"
        fake = _live(env, {GID4, stray}, {GID4: {VG4: "SKU-P4", stray: "SKU-P2"}})  # VGID2 gone
        pid = "P2"
    else:
        _on_shopify(db)
        other, stray = "gid://shopify/Product/8", "gid://shopify/ProductVariant/81"
        fake = _live(env, {other, stray}, {other: {stray: "SKU-P1"}})  # GID1 gone
        pid = "P1"
    res = _purge(env, pid)
    assert res.status_code == 502 and "still on the website" in res.json()["detail"]
    assert fake.calls == [] and _still_there(db, pid)


@pytest.mark.parametrize("how", ["errors", "raise"])
@pytest.mark.parametrize("kind", ["listing", "variant"])
def test_r3_3_6_a_ground_truth_read_that_fails_deletes_nothing(env, how, kind):
    db = env["db"]
    if kind == "listing":
        _on_shopify(db)
        fake = _live(env, {GID1})
        fake.read_fail[GID1] = how
        pid = "P1"
    else:
        _size_of_live_model(db)
        fake = _live(env, {GID4, VGID2})
        fake.read_fail[VGID2] = how
        pid = "P2"
    res = _purge(env, pid)
    assert res.status_code == 502 and "could not be read" in res.json()["detail"]
    assert fake.calls == [] and _still_there(db, pid)


def test_r3_7_a_listing_with_more_variants_than_one_page_deletes_nothing(env):
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    fake.paged.add(GID1)
    res = _purge(env, "P1")
    assert res.status_code == 502 and "holds other colours" in res.json()["detail"]
    assert fake.calls == [] and _still_there(db, "P1")


@pytest.mark.parametrize("blank", ["", None, "  "])
@pytest.mark.parametrize("kind", ["listing", "variant"])
def test_r3_8_a_blank_sku_on_shopify_is_never_this_products(env, blank, kind):
    db = env["db"]
    if kind == "listing":
        _on_shopify(db)
        fake = _live(env, {GID1}, {GID1: {VG1: "SKU-P1", "gid://shopify/ProductVariant/98": blank}})
        pid = "P1"
    else:
        _size_of_live_model(db)
        fake = _live(env, {GID4, VGID2}, {GID4: {VG4: "SKU-P4", VGID2: blank}})
        pid = "P2"
    res = _purge(env, pid)
    assert res.status_code == 502, res.text
    assert fake.calls == [] and _still_there(db, pid)


def test_r3_4_an_unrecorded_removal_deletes_nothing_in_ims(env):
    # The 'removed' mark after Shopify is mandatory: if it cannot be
    # written, nothing in IMS is deleted (so a part-delete can never look
    # unstarted to Restore later).
    db = env["db"]
    _on_shopify(db)
    fake = _live(env, {GID1})
    real = product_purge._mark

    def _mark(db_, backup_id, status, **extra):
        return False if status == "removed" else real(db_, backup_id, status, **extra)

    env["mp"].setattr(product_purge, "_mark", _mark)
    res = _purge(env, "P1")
    assert res.status_code == 503 and "Could not record the delete" in res.json()["detail"]
    assert fake.calls == [("productDelete", GID1)]
    assert _still_there(db, "P1") and db.catalog_variants.find_one({"variant_id": "V1"})
    assert "shopify_product_id" not in db.catalog_products.find_one({"id": "T-P1"})["ecom"]  # dead id dropped


def test_r3_4_an_unrecorded_deleting_mark_deletes_nothing(env):
    db = env["db"]
    real = product_purge._mark
    env["mp"].setattr(product_purge, "_mark",
                      lambda db_, b, status, **x: False if status == "deleting" else real(db_, b, status, **x))
    res = _purge(env, "P1")
    assert res.status_code == 503 and "Could not record the delete" in res.json()["detail"]
    assert _still_there(db, "P1") and db.product_images.find_one({"image_id": "I1"})


def test_r3_5_a_read_error_before_the_delete_leaves_it_restorable(env):
    # The backup starts as 'prepared'; a read failing before the first IMS
    # change aborts it (UNREADABLE), so the intact product can be Restored.
    db = env["db"]
    real = product_purge._gather
    calls = []

    def _gather_fails_second_time(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("connection reset")
        return real(*a, **k)

    env["mp"].setattr(product_purge, "_gather", _gather_fails_second_time)
    res = _purge(env, "P1")
    assert res.status_code == 503 and "Could not check" in res.json()["detail"]
    assert _still_there(db, "P1")
    assert [b["status"] for b in db.deleted_products_backup.find()] == ["aborted"]
    (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                         json={"product_ids": ["P1"], "apply": False}).json()["results"]
    assert row["status"] == "ready"


def test_r3_5_the_backup_is_prepared_then_deleting_only_at_the_first_change(env):
    db = env["db"]
    seen = []
    real = product_purge._apply_delete

    def _watch(db_, pid, run_id, fresh):
        seen.append(db.deleted_products_backup.find_one()["status"])
        return real(db_, pid, run_id, fresh)

    real_after = product_purge._after_shopify

    def _watch_after(*a, **k):
        seen.append(db.deleted_products_backup.find_one()["status"])
        return real_after(*a, **k)

    env["mp"].setattr(product_purge, "_apply_delete", _watch)
    env["mp"].setattr(product_purge, "_after_shopify", _watch_after)
    assert _purge(env, "P1").status_code == 200
    assert seen == ["prepared", "deleting"]


def test_r3_n2_a_colours_row_under_its_own_twin_is_still_a_colour_of_its_model(env):
    db = env["db"]
    _size_of_live_model(db)
    db.catalog_variants.update_one({"sku": "SKU-P2"}, {"$set": {"parent_product_id": "T-P2"}})
    fake = _live(env, {GID4, VGID2})
    res = _purge(env, "P2")
    assert res.status_code == 200, res.text
    assert ("productVariantsBulkDelete", GID4, VGID2) in fake.calls


def test_r3_n4_the_photo_prune_keeps_a_media_row_written_meanwhile(env):
    db = env["db"]
    _size_of_live_model(db)
    fake = _live(env, {GID4, VGID2})
    fake.on_media = lambda: db.catalog_products.update_one(
        {"id": "T-P4"}, {"$push": {"ecom.media_map": {"url": "https://x/p9.jpg", "id": "M9"}}})
    assert _purge(env, "P2").status_code == 200
    assert [r["id"] for r in db.catalog_products.find_one({"id": "T-P4"})["ecom"]["media_map"]] == [M4, "M9"]


def test_r3_n5_a_failed_photo_prune_queues_the_model_and_says_so(env):
    db = env["db"]
    _size_of_live_model(db)
    fake = _live(env, {GID4, VGID2})
    fake.media_fail = True
    res = _purge(env, "P2")
    assert res.status_code == 200, res.text
    assert "photos could not all be taken off" in res.json()["note"]
    assert db.catalog_products.find_one({"id": "T-P4"})["ecom"]["locally_modified"] is True


def test_r3_n6_a_twin_with_another_sku_needs_sorting_by_hand(env):
    env["db"].catalog_products.update_one({"id": "T-P1"}, {"$set": {"sku": "SKU-OLD"}})
    res = _purge(env, "P1")
    assert res.status_code == 409 and "different SKU" in res.json()["detail"]


def test_r3_n7_a_discarded_draft_being_deleted_is_not_revived(env):
    from datetime import timezone

    from api.services import product_master

    db = env["db"]
    db.products.update_one({"product_id": "P1"}, {"$set": {
        "discarded_draft": True, "catalog_status": "DRAFT", "is_active": False,
        "purge_run": "PURGE-LIVE", "purge_run_at": datetime.now(timezone.utc)}})
    assert product_master.revive_discarded_draft("P1", ProductRepository(db.products), db=db) is False
    assert db.products.find_one({"product_id": "P1"})["deleted_at"] == GONE
    assert db.catalog_products.find_one({"id": "T-P1"})["deleted_at"] == GONE


def test_r3_n8_restore_after_a_dead_claim_clears_it_and_gives_the_queue_back(env):
    from datetime import timedelta, timezone

    db = env["db"]
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    db.products.update_one({"product_id": "P1"}, {"$set": {"purge_run": "PURGE-DEAD", "purge_run_at": old}})
    db.catalog_products.update_one({"id": "T-P1"}, {"$set": {
        "purge_run": "PURGE-DEAD", "purge_run_at": old, "purge_prior_queue": True,
        "ecom.locally_modified": False}})
    env["mp"].setattr(catalog_mod, "_live_skus", lambda skus: set())
    (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                         json={"product_ids": ["P1"], "apply": True}).json()["results"]
    assert row["status"] == "done", row
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert not any(k in twin for k in ("purge_run", "purge_run_at", "purge_prior_queue"))
    assert twin["ecom"]["locally_modified"] is True
    assert db.products.find_one({"product_id": "P1"})["purge_run"] is None


def test_r3_n9_a_long_run_refreshes_its_claim(env):
    from datetime import timedelta, timezone

    db = env["db"]
    _on_shopify(db)
    ticks = iter(range(1000))
    base = datetime(2026, 10, 29, tzinfo=timezone.utc)
    env["mp"].setattr(product_purge, "_now", lambda: base + timedelta(minutes=next(ticks)))
    fake = _live(env, {GID1})
    real = fake.__call__
    at = []

    async def _watch(db_, query, variables):
        at.append(db.products.find_one({"product_id": "P1"})["purge_run_at"])
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _watch)
    claimed = []
    real_claim = product_purge._claim

    def _claim(db_, pid, twin_id, run_id):
        out = real_claim(db_, pid, twin_id, run_id)
        claimed.append(db.products.find_one({"product_id": "P1"})["purge_run_at"])
        return out

    env["mp"].setattr(product_purge, "_claim", _claim)
    assert _purge(env, "P1").status_code == 200
    assert at and min(at) > claimed[0]


def test_r3_n11_the_restore_script_puts_a_deleted_product_back(env):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "scripts"))
    import restore_deleted_product as script

    db = env["db"]
    _on_shopify(db)
    db.ecom_collections.insert_one({"collection_id": "C1", "products": [{"sku": "SKU-P1", "position": 0}]})
    db.tasks.insert_one({"task_id": "K1", "payload": {"product_ids": ["P1"]}})
    _live(env, {GID1})
    assert _purge(env, "P1").status_code == 200
    backup_id = db.deleted_products_backup.find_one()["backup_id"]
    dry = script.restore(db, backup_id)
    assert dry["inserts"]["products"] == 1 and not dry["refused"] and "removed" in dry["website"]
    assert not db.products.find_one({"product_id": "P1"})  # a dry run writes nothing
    done = script.restore(db, backup_id, commit=True)
    assert done.get("committed")
    spine = db.products.find_one({"product_id": "P1"})
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert spine["deleted_at"] == GONE and "purge_run" not in spine  # back archived, unclaimed
    assert "shopify_product_id" not in twin["ecom"]  # the listing is gone: no dead id
    assert db.catalog_variants.find_one({"variant_id": "V1"}) and db.product_images.find_one({"image_id": "I1"})
    assert db.ecom_collections.find_one({"collection_id": "C1"})["products"] == [{"sku": "SKU-P1", "position": 0}]
    assert db.tasks.find_one({"task_id": "K1"})["payload"]["product_ids"] == ["P1"]
    assert db.deleted_products_backup.find_one()["status"] == "restored"
    assert script.restore(db, backup_id)["refused"]  # never twice


def test_r3_n11_the_restore_script_never_overwrites(env):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "scripts"))
    import restore_deleted_product as script

    db = env["db"]
    assert _purge(env, "P1").status_code == 200
    backup_id = db.deleted_products_backup.find_one()["backup_id"]
    db.products.insert_one(_spine("P1", deleted_at=None))  # catalogued again meanwhile
    out = script.restore(db, backup_id, commit=True)
    assert out["refused"] and not out.get("committed")
    assert db.products.count_documents({"product_id": "P1"}) == 1


def test_r3_a_run_that_died_after_recording_the_removal_is_finished_and_never_restored(env):
    # The worker died right after the 'removed' mark: Shopify is done, IMS is
    # whole, the twin still names the dead listing. Restore refuses it, and
    # the next press finishes it with no Shopify call -- even with website
    # writes switched off.
    from datetime import timedelta, timezone

    db = env["db"]
    _on_shopify(db)
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    db.deleted_products_backup.insert_one({
        "backup_id": "DPB-DEAD", "product_id": "P1", "status": "removed", "deleted_at": long_ago,
        "shopify": {"plan": {"kind": "product", "product_gid": GID1},
                    "result": {"ok": True, "action": "delete", "mode": "LIVE"}}})
    (row,) = env["call"](ROOT, "POST", "/api/v1/catalog/products/bulk-restore",
                         json={"product_ids": ["P1"], "apply": False}).json()["results"]
    assert row["status"] == "refused" and "being deleted permanently" in row["reason"]
    res = _purge(env, "P1")  # DARK: the env default; _graphql raises if called
    assert res.status_code == 200, res.text
    assert not db.products.find_one({"product_id": "P1"})


# ---------------------------------------------------------------------------
# Closure check of #1273 (33df7091)
# ---------------------------------------------------------------------------


def test_final_1_only_the_spine_holds_the_models_listing_id(env):
    # test_r3_1 with the model's listing id on the SPINE alone: the spine
    # half of _forget_shopify drops it, so the next press goes through.
    db = env["db"]
    _size_of_live_model(db)
    db.products.update_one({"product_id": "P2"}, {"$set": {"shopify_product_id": GID4}})
    fake = _live(env, {GID4, VGID2})
    real = fake.__call__

    async def _promo_meanwhile(db_, query, variables):
        if "productVariantsBulkDelete(" in query:
            db.promo_rules.insert_one({"promo_id": "PR-1", "name": "Diwali", "product_ids": ["P2"]})
        return await real(db_, query, variables)

    env["mp"].setattr(shopify_push, "_graphql", _promo_meanwhile)
    res = _purge(env, "P2")
    assert res.status_code == 409 and "already taken off the website" in res.json()["detail"]
    assert "shopify_product_id" not in db.products.find_one({"product_id": "P2"})
    db.promo_rules.delete_many({})
    assert _purge(env, "P2").status_code == 200


def test_final_2_a_revive_that_loses_the_fence_writes_nothing(env):
    # The revive read "no claim", then a delete claimed it: the fenced spine
    # write loses, and the twin (written after it) is never touched.
    from datetime import timezone

    from api.services import product_master

    db = env["db"]
    db.products.update_one({"product_id": "P1"}, {"$set": {
        "discarded_draft": True, "catalog_status": "DRAFT", "is_active": False,
        "purge_run": "PURGE-LIVE", "purge_run_at": datetime.now(timezone.utc)}})
    env["mp"].setattr(product_purge, "claim_is_live", lambda doc: False)  # read before the claim
    assert product_master.revive_discarded_draft("P1", ProductRepository(db.products), db=db) is False
    assert db.products.find_one({"product_id": "P1"})["deleted_at"] == GONE
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert twin["deleted_at"] == GONE and "needs_review" not in twin


@pytest.mark.parametrize("kind", ["variant", "product"])
def test_final_3_the_search_index_still_showing_our_own_delete_is_not_a_hit(env, kind):
    db = env["db"]
    if kind == "variant":
        _size_of_live_model(db)
        fake = _live(env, {GID4})  # VGID2 already gone
        fake.search_lag[VGID2] = ("SKU-P2", GID4)
        pid = "P2"
    else:
        _on_shopify(db)
        fake = _live(env, set())  # GID1 already gone
        fake.search_lag[VG1] = ("SKU-P1", GID1)
        pid = "P1"
    res = _purge(env, pid)
    assert res.status_code == 200, res.text


def test_final_4_the_restore_script_refuses_a_backup_that_deleted_nothing(env):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "scripts"))
    import restore_deleted_product as script

    db = env["db"]
    db.deleted_products_backup.insert_one({"backup_id": "DPB-AB", "product_id": "P1", "status": "aborted",
                                           "docs": {}, "updated_docs": {}})
    out = script.restore(db, "DPB-AB", commit=True)
    assert out["refused"] and "nothing in IMS was deleted" in out["refused"][0]


def test_final_4_the_restore_script_never_overwrites_a_newer_stock_baseline(env):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "scripts"))
    import restore_deleted_product as script

    db = env["db"]
    _size_of_live_model(db)
    _live(env, {GID4, VGID2})
    assert _purge(env, "P2").status_code == 200
    backup_id = db.deleted_products_backup.find_one()["backup_id"]
    db.catalog_products.update_one({"id": "T-P4"}, {"$set": {"ecom.online_stock.quantities.SKU-P2": {"S1": 7}}})
    assert script.restore(db, backup_id, commit=True).get("committed")
    assert db.catalog_products.find_one({"id": "T-P4"})["ecom"]["online_stock"]["quantities"]["SKU-P2"] == {"S1": 7}


# ---------------------------------------------------------------------------
# Follow-up to #1273: the revive writes the spine first; a twin write that
# fails must not leave a revived spine behind a deleted twin
# ---------------------------------------------------------------------------


class _TwinWriteFails:
    """A db whose catalog_products.update_one raises `times` times."""

    def __init__(self, db, times=1):
        self._db, self.left = db, times

    def get_collection(self, name):
        coll = self._db[name]
        if name != "catalog_products":
            return coll
        outer = self

        class _Coll:
            def __getattr__(self, attr):
                if attr == "update_one" and outer.left > 0:
                    def _boom(*a, **k):
                        outer.left -= 1
                        raise RuntimeError("connection reset")
                    return _boom
                return getattr(coll, attr)

        return _Coll()

    def __getitem__(self, name):
        return self.get_collection(name)


def _discarded_draft(db):
    db.products.update_one({"product_id": "P1"}, {"$set": {
        "discarded_draft": True, "catalog_status": "DRAFT", "is_active": False, "provisional": False,
        "mrp": 5000}})


def test_followup_a_failed_twin_write_puts_the_spine_back_so_the_next_order_repairs_it(env):
    from api.services import product_master

    db = env["db"]
    _discarded_draft(db)
    repo = ProductRepository(db.products)
    with pytest.raises(RuntimeError):
        product_master.revive_discarded_draft("P1", repo, db=_TwinWriteFails(db), mrp=6000)
    spine = db.products.find_one({"product_id": "P1"})
    # Back exactly as it was: still a discarded draft, so it can be revived.
    assert spine["discarded_draft"] is True and spine["deleted_at"] == GONE
    assert spine["provisional"] is False and spine["mrp"] == 5000
    assert not spine.get("revived_at") and not spine.get("revived_by")
    assert product_master.revivable_discarded_draft(spine)
    assert db.catalog_products.find_one({"id": "T-P1"})["deleted_at"] == GONE
    # The next order revives it, twin and all.
    assert product_master.revive_discarded_draft("P1", repo, db=db, mrp=6000) is True
    spine = db.products.find_one({"product_id": "P1"})
    twin = db.catalog_products.find_one({"id": "T-P1"})
    assert spine["discarded_draft"] is False and spine["provisional"] is True and spine["mrp"] == 6000
    assert twin["needs_review"] is True and "deleted_at" not in twin


def test_followup_the_undo_never_overwrites_a_later_write(env):
    # Something else wrote the spine after the revive (say another revive):
    # the undo is fenced on this revive's own stamp and leaves it alone.
    from api.services import product_master

    db = env["db"]
    _discarded_draft(db)
    real = ProductRepository(db.products)

    class _Repo:
        def __getattr__(self, attr):
            return getattr(real, attr)

        def update(self, pid, data, expected=None):
            ok = real.update(pid, data, expected=expected)
            if data.get("revived_at"):
                db.products.update_one({"product_id": pid}, {"$set": {"revived_at": "LATER"}})
            return ok

    with pytest.raises(RuntimeError):
        product_master.revive_discarded_draft("P1", _Repo(), db=_TwinWriteFails(db))
    assert db.products.find_one({"product_id": "P1"})["revived_at"] == "LATER"
