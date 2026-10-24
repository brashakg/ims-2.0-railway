"""
Permanent delete of an ARCHIVED product (owner 2026-10-24)
==========================================================
"i will need an option to delete archived products". Through the REAL catalog
router (role gates are the routes' own) over mongomock:

  * SUPERADMIN only (ADMIN and everyone else 403, nothing deleted)
  * an active product is refused 409 "Archive it first"
  * ANY history (every collection product_purge.HISTORY names), a Shopify
    link, or a model family it leads -> 409, it stays archived
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
from api.services import product_purge, rbac_policy  # noqa: E402
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


@pytest.mark.parametrize("coll", [c for c, _ in product_purge.HISTORY])
def test_every_history_collection_refuses(env, coll):
    env["db"][coll].insert_one({"items": [{"product_id": "P1", "quantity": 1}]})
    res = _purge(env, "P1")
    assert res.status_code == 409, coll
    assert "stays archived to keep the records complete" in res.json()["detail"]
    assert _still_there(env["db"], "P1")


@pytest.mark.parametrize("field", product_purge.REF_FIELDS)
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


@pytest.mark.parametrize(
    "setup",
    [
        lambda db: db.catalog_products.update_one(
            {"id": "T-P1"}, {"$set": {"ecom.shopify_product_id": "gid://shopify/Product/1"}}),
        lambda db: db.products.update_one(
            {"product_id": "P1"}, {"$set": {"shopify_product_id": "gid://shopify/Product/1"}}),
        lambda db: db.catalog_variants.update_one(
            {"variant_id": "V1"}, {"$set": {"shopify_variant_id": "gid://shopify/ProductVariant/1"}}),
    ],
    ids=["twin-gid", "spine-gid", "variant-gid"],
)
def test_shopify_link_refuses(env, setup):
    setup(env["db"])
    res = _purge(env, "P1")
    assert res.status_code == 409 and "Shopify" in res.json()["detail"]
    assert _still_there(env["db"], "P1")


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
# The rbac rows equal the code gates
# ---------------------------------------------------------------------------


def test_rbac_rows_are_superadmin_only():
    def row(method, path):
        (r,) = [r for r in rbac_policy.POLICY if r["method"] == method and r["path"] == path]
        return set(r["allowed"])

    assert row("DELETE", "/api/v1/catalog/products/{product_id}/permanent") == {"SUPERADMIN"}
    assert row("POST", "/api/v1/catalog/products/bulk-purge") == {"SUPERADMIN"}
