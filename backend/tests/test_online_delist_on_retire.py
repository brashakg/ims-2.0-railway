"""
Retiring a product in IMS takes it OFF Shopify (sync audit gap #2)
==================================================================
Owner-ordered 2026-09-06. Before this, soft-deleting a product or setting
is_active=false wrote the IMS row and did NOTHING on Shopify -- the product
stayed on sale forever (the 07-29 purge had to be cleaned on Shopify by hand).

What these tests pin, door by door, through the REAL routers:
  * DELETE /catalog/products/{id}  -> push_product_delist called ONCE with the
    gid; the delete still lands; the gid is kept (reversible, never a delete)
  * the delete still SUCCEEDS when the take-down fails or raises (fail-soft),
    and the failure is recorded: audit code DELIST_FAILED + the Online column
    says DELIST_FAILED ("still live on Shopify -- take-down failed")
  * is_active=false through the catalog drawer, the spine PUT /products and
    the /products/master door -> the twin is taken down, trigger "deactivated"
  * a never-pushed product (no gid) triggers nothing; an already-inactive
    product does not get taken down again
  * DARK gates -> SIMULATED and ZERO network (the intent is still audited)
  * reactivation queues the twin for the next live sync and lifts the
    take-down marker; a never-pushed product is NOT queued (first publish
    stays a human press)
  * a later successful publish clears the delist marks

***** SAFETY-CRITICAL: every Shopify call is a spy or a raiser. No real
Shopify request is ever made. *****

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \
        backend/tests/test_online_delist_on_retire.py -q
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
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from api import dependencies as deps  # noqa: E402
from api.routers import catalog  # noqa: E402
from api.routers import products as products_mod  # noqa: E402
from api.routers import product_master as pm_router  # noqa: E402
from api.services import online_delist  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.online_catalog import product_online_state  # noqa: E402
from api.services.shopify_push.writeback import _writeback_product  # noqa: E402

GID = "gid://shopify/Product/111"
ADMIN = {"user_id": "u-admin", "roles": ["ADMIN"], "username": "admin"}


# ---------------------------------------------------------------------------
# Harness -- one in-memory store reachable as db[name] AND conn.get_collection
# ---------------------------------------------------------------------------
class _Coll(MockCollection):
    """MockCollection + dot-notation $set (the spine -> twin mirror patches
    "ecom.locally_modified" that way; the stock double would store the literal
    dotted key, a double WEAKER than production)."""

    @staticmethod
    def _apply_set(doc, changes):
        for key, value in changes.items():
            parts = str(key).split(".")
            cur = doc
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value

    def update_one(self, filter, update, upsert=False):  # noqa: A002
        doc = self.find_one(filter)
        if doc is None:
            return type("obj", (object,), {"modified_count": 0, "matched_count": 0})()
        if "$set" in update:
            self._apply_set(doc, update["$set"])
            return type("obj", (object,), {"modified_count": 1, "matched_count": 1})()
        return super().update_one(filter, update)


class _DB:
    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _Coll(name))

    def get_collection(self, name):
        return self[name]


class _Conn:
    """The dependencies.get_db() shape: is_connected + .db + get_collection."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db[name]


class _DelistSpy:
    """Stands in for shopify_push.push_product_delist. Records every call
    (deep-copied, so a later write cannot rewrite what it saw)."""

    def __init__(self, ok=True, raises=False):
        self.calls = []
        self.ok = ok
        self.raises = raises

    async def __call__(self, db, product):
        self.calls.append(copy.deepcopy(product))
        if self.raises:
            raise RuntimeError("shopify exploded")
        return shopify_push.PushResult(
            mode=shopify_push.MODE_LIVE,
            entity="product",
            action="delist",
            target_id=product.get("id"),
            ok=self.ok,
            shopify_id=(product.get("ecom") or {}).get("shopify_product_id"),
            error=None if self.ok else "productUpdate: 502 from Shopify",
        )


def _run(coro):
    return asyncio.run(coro)


def _twin(db, pid="P1"):
    return copy.deepcopy(db["catalog_products"].find_one({"id": pid}))


def _audit_rows(db):
    return [r for r in db["audit_logs"].find({}) if r.get("action") == "ONLINE_STORE_PUSH"]


def _seed_live(db, pid="P1", gid=GID):
    """A product already LIVE on Shopify, active in IMS, clean."""
    db["catalog_products"].insert_one(
        {
            "id": pid,
            "sku": "SKU-1",
            "title": "Ray-Ban RB2140",
            "is_active": True,
            "images": ["https://cdn.example.com/rb2140.jpg"],
            "mrp": 5000.0,
            "offer_price": 4000.0,
            "ecom": {
                "status": "PUBLISHED",
                "shopify_product_id": gid,
                "shopify_variant_id": "gid://shopify/ProductVariant/222",
                "locally_modified": False,
            },
        }
    )


def _seed_never_pushed(db, pid="P2"):
    db["catalog_products"].insert_one(
        {
            "id": pid,
            "sku": "SKU-2",
            "title": "Never pushed",
            "is_active": True,
            "images": ["https://cdn.example.com/np.jpg"],
            "ecom": {"status": "DRAFT", "locally_modified": True},
        }
    )


@pytest.fixture(autouse=True)
def _dark(monkeypatch):
    """The three gates stay DARK; nothing here may ever reach Shopify."""
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    monkeypatch.delenv("SHOPIFY_DISPATCH_MODE", raising=False)
    monkeypatch.setenv("IMS_SHOPIFY_WRITES", "")
    yield


@pytest.fixture
def db(monkeypatch):
    db = _DB()
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    monkeypatch.setattr(catalog, "_get_db", lambda: db)
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(
        deps, "get_audit_repository", lambda: AuditRepository(db["audit_logs"])
    )
    return db


@pytest.fixture
def spy(monkeypatch):
    s = _DelistSpy()
    monkeypatch.setattr(shopify_push, "push_product_delist", s)
    return s


# ===========================================================================
# 1. DELETE /catalog/products/{id}
# ===========================================================================
def test_soft_delete_of_a_live_product_takes_it_down_once_with_its_gid(db, spy):
    _seed_live(db)

    res = _run(catalog.delete_catalog_product("P1", current_user=ADMIN))

    assert res == {"message": "Product deleted successfully"}
    assert len(spy.calls) == 1, "exactly ONE take-down per delete"
    assert spy.calls[0]["ecom"]["shopify_product_id"] == GID
    saved = _twin(db)
    assert saved["is_active"] is False, "the delete write must still land"
    assert saved["ecom"]["shopify_product_id"] == GID, "gid KEPT -- reversible, never a delete"
    assert saved["ecom"]["online_state"] == "DELISTED"
    assert saved["ecom"]["delisted_at"] is not None
    assert saved["ecom"]["delist_reason"] == "deleted"
    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0]["entity_type"] == "product"
    assert rows[0]["entity_id"] == "P1"
    assert rows[0]["details"]["trigger"] == "deleted"
    assert rows[0]["details"]["push_action"] == "delist"
    assert rows[0]["details"]["shopify_id"] == GID
    assert rows[0]["severity"] == "INFO"


def test_delete_still_succeeds_when_the_take_down_fails(db, monkeypatch):
    """FAIL-SOFT: Shopify said no -> the IMS delete stands, and the failure is
    recorded with a stable code so the Online column can tell the truth."""
    spy = _DelistSpy(ok=False)
    monkeypatch.setattr(shopify_push, "push_product_delist", spy)
    _seed_live(db)

    res = _run(catalog.delete_catalog_product("P1", current_user=ADMIN))

    assert res == {"message": "Product deleted successfully"}
    saved = _twin(db)
    assert saved["is_active"] is False
    assert saved["ecom"]["online_state"] == "DELIST_FAILED"
    assert "502" in saved["ecom"]["delist_error"]
    assert "delisted_at" not in saved["ecom"]
    assert saved["ecom"]["status"] == "PUBLISHED", "still live -- nothing pretends otherwise"
    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0]["severity"] == "WARNING"
    assert rows[0]["details"]["code"] == "DELIST_FAILED"
    assert rows[0]["details"]["trigger"] == "deleted"
    assert product_online_state(saved)["online"] == "DELIST_FAILED"


def test_delete_still_succeeds_when_the_take_down_raises(db, monkeypatch):
    spy = _DelistSpy(raises=True)
    monkeypatch.setattr(shopify_push, "push_product_delist", spy)
    _seed_live(db)

    res = _run(catalog.delete_catalog_product("P1", current_user=ADMIN))

    assert res == {"message": "Product deleted successfully"}
    assert _twin(db)["is_active"] is False
    assert len(spy.calls) == 1


# ===========================================================================
# 2. is_active=false through every door that writes it
# ===========================================================================
def test_deactivating_through_the_catalog_drawer_takes_it_down(db, spy):
    _seed_live(db)

    _run(
        catalog.update_catalog_product(
            "P1", catalog.ProductUpdateInput(is_active=False), current_user=ADMIN
        )
    )

    assert len(spy.calls) == 1
    assert spy.calls[0]["ecom"]["shopify_product_id"] == GID
    saved = _twin(db)
    assert saved["is_active"] is False
    assert saved["ecom"]["online_state"] == "DELISTED"
    assert saved["ecom"]["delist_reason"] == "deactivated"
    assert _audit_rows(db)[0]["details"]["trigger"] == "deactivated"


def _spine_repo(pid="SPINE-77", pim_id="P1", active=True):
    coll = MockCollection("products")
    coll.insert_one(
        {
            "_id": pid,
            "product_id": pid,
            "id": pid,
            "sku": "FR-RB-0077",
            "brand": "Ray-Ban",
            "model": "RB1077",
            "category": "FRAME",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "cost_price": 2000.0,
            "hsn_code": "900311",
            "gst_rate": 5.0,
            "is_active": active,
            "pim_product_id": pim_id,  # the twin's key -- a DIFFERENT uuid
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1077"},
            "catalog_status": "ACTIVE",
        }
    )
    return ProductRepository(coll)


def test_deactivating_through_the_spine_put_takes_the_twin_down(db, spy, monkeypatch):
    """PUT /products/{id} -- the twin is keyed by pim_product_id, not the
    spine id (71 of 77 live products are shaped this way)."""
    _seed_live(db)
    repo = _spine_repo()
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)

    _run(
        products_mod.update_product(
            "SPINE-77",
            products_mod.ProductUpdate(is_active=False),
            {**ADMIN, "active_store_id": "S1"},
        )
    )

    assert repo.find_by_id("SPINE-77")["is_active"] is False, "the spine write must land"
    assert len(spy.calls) == 1
    assert spy.calls[0]["id"] == "P1"
    assert spy.calls[0]["ecom"]["shopify_product_id"] == GID
    saved = _twin(db)
    assert saved["ecom"]["online_state"] == "DELISTED"
    assert _audit_rows(db)[0]["details"]["trigger"] == "deactivated"


def test_deactivating_through_the_product_master_door_takes_the_twin_down(
    db, spy, monkeypatch
):
    _seed_live(db)
    repo = _spine_repo(pid="PM1")
    monkeypatch.setattr(pm_router, "get_product_repository", lambda: repo)
    monkeypatch.setattr(pm_router, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(
        pm_router, "get_audit_repository", lambda: AuditRepository(db["audit_logs"])
    )

    _run(
        pm_router.update_master_product(
            "PM1", pm_router.ProductMasterUpdate(is_active=False), ADMIN
        )
    )

    assert repo.find_by_id("PM1")["is_active"] is False
    assert len(spy.calls) == 1
    assert spy.calls[0]["ecom"]["shopify_product_id"] == GID
    assert _twin(db)["ecom"]["online_state"] == "DELISTED"


def test_a_never_pushed_product_triggers_nothing(db, spy):
    _seed_never_pushed(db)

    _run(catalog.delete_catalog_product("P2", current_user=ADMIN))
    _run(
        catalog.update_catalog_product(
            "P2", catalog.ProductUpdateInput(is_active=False), current_user=ADMIN
        )
    )

    assert spy.calls == []
    assert _audit_rows(db) == []
    assert "online_state" not in _twin(db, "P2")["ecom"]


def test_an_already_inactive_product_is_not_taken_down_again(db, spy):
    _seed_live(db)
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"is_active": False}})

    _run(
        catalog.update_catalog_product(
            "P1", catalog.ProductUpdateInput(is_active=False, weight=12.0), current_user=ADMIN
        )
    )

    assert spy.calls == [], "inactive -> inactive is not a transition"


def test_an_unrelated_edit_does_not_take_down(db, spy):
    _seed_live(db)

    _run(
        catalog.update_catalog_product(
            "P1", catalog.ProductUpdateInput(weight=12.0), current_user=ADMIN
        )
    )

    assert spy.calls == []


# ===========================================================================
# 3. DARK gates -> SIMULATED, zero network, intent still audited
# ===========================================================================
def test_dark_gates_simulate_and_never_touch_the_network(db, monkeypatch):
    async def _no_network(db, query, variables):
        raise AssertionError("a DARK take-down reached the network")

    monkeypatch.setattr(shopify_push, "_graphql", _no_network)
    _seed_live(db)

    _run(
        catalog.update_catalog_product(
            "P1", catalog.ProductUpdateInput(is_active=False), current_user=ADMIN
        )
    )

    rows = _audit_rows(db)
    assert len(rows) == 1
    assert rows[0]["details"]["mode"] == shopify_push.MODE_SIMULATED
    assert rows[0]["details"]["push_action"] == "delist"
    assert rows[0]["details"]["trigger"] == "deactivated"
    assert rows[0]["details"]["shopify_id"] == GID
    saved = _twin(db)
    assert saved["is_active"] is False
    assert saved["ecom"]["online_state"] == "DELISTED", "the intent is recorded"


# ===========================================================================
# 4. Reactivation -> queued for the next live sync (no automatic republish)
# ===========================================================================
def test_reactivation_queues_the_twin_for_the_next_sync(db, spy):
    _seed_live(db)
    _run(
        catalog.update_catalog_product(
            "P1", catalog.ProductUpdateInput(is_active=False), current_user=ADMIN
        )
    )
    # What a LIVE take-down leaves behind (writeback: DRAFT + taken_down_at,
    # the marker the sweep skips).
    db["catalog_products"].update_one(
        {"id": "P1"},
        {"$set": {"ecom.status": "DRAFT", "ecom.taken_down_at": "2026-09-06T00:00:00"}},
    )

    _run(
        catalog.update_catalog_product(
            "P1", catalog.ProductUpdateInput(is_active=True), current_user=ADMIN
        )
    )

    saved = _twin(db)
    assert saved["is_active"] is True
    assert saved["ecom"]["locally_modified"] is True, "queued for the next sync"
    assert "taken_down_at" not in saved["ecom"], "the sweep must not skip it"
    assert "online_state" not in saved["ecom"]
    assert "delisted_at" not in saved["ecom"]
    assert saved["ecom"]["shopify_product_id"] == GID, "republish is an UPDATE, never a duplicate"
    assert len(spy.calls) == 1, "reactivation never pushes by itself"
    # The Online column's queued flag (the row still carries its gid, so the
    # pre-existing display rule keeps calling it LIVE until the sweep lands).
    assert product_online_state(saved)["queued"] is True


def test_reactivation_of_a_never_pushed_product_does_not_queue_it(db, spy):
    """First publish STAYS a human press (owner ruling 08-25)."""
    _seed_never_pushed(db)
    db["catalog_products"].update_one(
        {"id": "P2"}, {"$set": {"is_active": False, "ecom.locally_modified": False}}
    )

    assert online_delist.mark_for_republish(db, _twin(db, "P2")) is False
    assert _twin(db, "P2")["ecom"]["locally_modified"] is False


# ===========================================================================
# 5. The Online column rule + the publish-clears-marks rule
# ===========================================================================
def test_online_state_rule_reports_a_failed_take_down_and_hides_a_delisted_row():
    live = {"images": ["https://x/p.jpg"], "ecom": {"shopify_product_id": GID, "status": "PUBLISHED"}}
    assert product_online_state(live)["online"] == "LIVE"
    failed = copy.deepcopy(live)
    failed["ecom"]["online_state"] = "DELIST_FAILED"
    assert product_online_state(failed)["online"] == "DELIST_FAILED"
    delisted = copy.deepcopy(live)
    delisted["ecom"].update({"online_state": "DELISTED", "status": "DRAFT"})
    assert product_online_state(delisted)["online"] == "OFF"


def test_a_successful_publish_clears_the_delist_marks(db):
    _seed_live(db)
    db["catalog_products"].update_one(
        {"id": "P1"},
        {"$set": {"ecom.online_state": "DELIST_FAILED", "ecom.delist_error": "x",
                  "ecom.delist_reason": "deleted"}},
    )

    _writeback_product(db, "P1", GID, status="PUBLISHED")

    ecom = _twin(db)["ecom"]
    for key in online_delist.DELIST_KEYS:
        assert key not in ecom
    assert ecom["status"] == "PUBLISHED"
