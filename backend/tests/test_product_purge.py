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


class _FakeShopify:
    """The ONLY network call (shopify_push._graphql), faked: the set of nodes
    that exist on Shopify; `fail` = gids whose delete Shopify refuses."""

    def __init__(self, nodes=()):
        self.nodes = set(nodes)
        self.fail = set()
        self.calls = []

    async def __call__(self, db, query, variables):
        if "productDelete(" in query:
            gid = variables["input"]["id"]
            self.calls.append(("productDelete", gid))
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
            return {"data": {"productVariantsBulkDelete": {"product": {"id": variables["productId"]},
                                                           "userErrors": []}}}
        if "productDeleteMedia(" in query:
            self.calls.append(("productDeleteMedia", variables["productId"], tuple(variables["mediaIds"])))
            return {"data": {"productDeleteMedia": {"deletedMediaIds": variables["mediaIds"],
                                                    "mediaUserErrors": []}}}
        if "node(id" in query:
            self.calls.append(("node", variables["id"]))
            return {"data": {"node": {"id": variables["id"]} if variables["id"] in self.nodes else None}}
        raise AssertionError("unexpected Shopify call: %s" % query[:80])


def _live(env, nodes=()):
    fake = _FakeShopify(nodes)
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
    assert fake.calls == [("productDelete", GID1), ("node", GID1)]
    assert not db.products.find_one({"product_id": "P1"})
    assert db.deleted_products_backup.find_one()["shopify"]["result"]["action"] == "already_gone"


def test_bulk_one_failure_never_stops_the_others(env):
    db = env["db"]
    _on_shopify(db)
    db.catalog_products.update_one({"id": "T-P2"}, {"$set": {"ecom.shopify_product_id": GID4}})
    fake = _live(env, {GID1, GID4})
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


def test_history_that_lands_after_the_first_check_stops_it_before_the_backup(env):
    # A sale lands between the door's check and the delete: the re-check
    # inside purge refuses BEFORE anything is backed up or sent to Shopify.
    db = env["db"]
    real = product_purge.purge_check
    calls = []

    def _stale_first(db_, spine, twin, **kw):
        calls.append(1)
        if len(calls) == 1:
            db.stock_units.insert_one({"product_id": "P1", "status": "SOLD"})
            return None, None  # the door's check saw nothing yet
        return real(db_, spine, twin, **kw)

    env["mp"].setattr(product_purge, "purge_check", _stale_first)
    res = _purge(env, "P1")
    assert res.status_code == 409 and "stock units" in res.json()["detail"]
    assert _still_there(db, "P1")
    assert db.deleted_products_backup.count_documents({}) == 0


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
