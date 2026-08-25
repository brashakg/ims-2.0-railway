"""
Catalogued products must QUEUE for the manual Online Store push.
================================================================

The bug (owner, 2026-08-25): "why are catalogued products not showing on the
Shopify website". `ecom.locally_modified` is the ONE dirty flag the push reads
-- both to select the rows the operator's "push all pending" sweep sends and to
compute the `pending` NUMBER shown on the Online Store screen. Nothing on any
product create / edit path ever set it, so a freshly catalogued product was
never queued AND the screen reported "pending: 0". It failed silently.

What these tests pin (the SET the sweep walks *and* the COUNT the owner reads):

  * a product created through the real create door is queued, and pending == 1
  * a catalogue edit through the catalog door re-queues an already-pushed row
    (both the plain save and the compare-and-swap save)
  * the SHOPIFY SYNC WRITE-BACK never re-queues -- the ping-pong guard. This is
    the most important test here: if the push's own write-back marked the row
    dirty, every successful push would queue itself again forever against a
    LIVE storefront.
  * a stock movement, the retired sync-shopify stamp and the soft-delete stamp
    do NOT queue (nothing they write is in any pushed Shopify payload)
  * a successful push clears the flag and pending returns to 0

***** SAFETY-CRITICAL: every Shopify call is MOCKED (shopify_push._graphql is
monkeypatched). No real Shopify request is ever made. *****

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_online_push_dirty_flag.py -q
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
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from api.routers import catalog  # noqa: E402
from api.routers import online_store_push as push_router  # noqa: E402
from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402


# ---------------------------------------------------------------------------
# Harness -- one in-memory store, reachable BOTH ways
# ---------------------------------------------------------------------------


class _Coll(MockCollection):
    """MockCollection with the two real-pymongo behaviours it lacks and the
    writers under test rely on. Infrastructure only -- the subject (the flag
    stamp) is never faked.

      * `upsert=True` -- the create door stages its PIM doc with
        update_one(..., upsert=True).
      * DOT-NOTATION $set ("ecom.locally_modified" -> nested) -- the spine ->
        catalog mirror patches that way. MockCollection would store the literal
        dotted key, i.e. a double WEAKER than production: a test on it would
        pass while the real write nested nothing readable by the push.
    """

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
            if not upsert:
                return type(
                    "obj", (object,), {"modified_count": 0, "matched_count": 0}
                )()
            doc = {k: v for k, v in filter.items() if not str(k).startswith("$")}
            self._apply_set(doc, update.get("$set") or {})
            self.insert_one(doc)
            return type("obj", (object,), {"modified_count": 1, "matched_count": 0})()
        if "$set" in update:
            self._apply_set(doc, update["$set"])
            return type("obj", (object,), {"modified_count": 1, "matched_count": 1})()
        return super().update_one(filter, update)


class _DB:
    """An in-memory db that supports db[name] (what the push router's
    _all_docs / _product_counts use) AND db.get_collection(name) (what
    product_master._stage_catalog_draft uses), over the SAME collections."""

    def __init__(self):
        self._colls = {}

    def __getitem__(self, name):
        return self._colls.setdefault(name, _Coll(name))

    def get_collection(self, name):
        return self[name]


def _run(coro):
    return asyncio.run(coro)


def _load(db, product_id):
    """Read a doc the way a route does -- but DEEP-COPIED. MockCollection.find_one
    hands back the live stored dict, so a test that mutated it would prove
    nothing about whether the save actually persisted anything."""
    return copy.deepcopy(db["catalog_products"].find_one({"id": product_id}))


def _pending(db):
    """The number the Online Store screen shows -- the REAL counter."""
    return push_router._product_counts(db)["pending"]


def _dirty_ids(db):
    """The SET the 'push all pending' sweep walks, selected with the sweep's
    own predicate (online_store_push: ecom and ecom.get('locally_modified'))."""
    out = []
    for doc in push_router._all_docs(db, "catalog_products"):
        ecom = doc.get("ecom")
        if ecom and ecom.get("locally_modified"):
            out.append(doc.get("id"))
    return out


def _frame_payload(sku="FR-QUEUE-001", model="RB-2140"):
    return {
        "category": "FRAME",
        "sku": sku,
        "brand": "Ray-Ban",
        "model": model,
        "color": "BLK",
        "mrp": 5000.0,
        "offer_price": 4000.0,
        "hsn_code": "9003",
        "gst_rate": 12.0,
        "attributes": {"frame_material": "Acetate", "frame_shape": "Round"},
    }


def _force_live(monkeypatch, graphql_response):
    """Open the three push gates and replace the network boundary with a fake."""
    calls = []

    async def _fake_graphql(db, query, variables):
        calls.append({"query": query, "variables": variables})
        return graphql_response

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
    monkeypatch.setattr(shopify_push, "_graphql", _fake_graphql)
    return calls


def _product_writes(calls):
    """The productUpdate/productCreate calls only. One press now makes several
    GraphQL calls (the product, its photograph, the sales-channel publish), so
    "how many pushes happened" has to count the PRODUCT write, not the traffic."""
    return [
        c
        for c in calls
        if "imsProductUpdate" in c["query"] or "imsProductCreate(" in c["query"]
    ]


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """The flag-gated internal mirror stays OFF: _stage_catalog_draft (which
    always runs) is the door under test, not the mirror."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    yield


@pytest.fixture
def db():
    return _DB()


@pytest.fixture
def admin():
    return {"user_id": "u-admin", "roles": ["ADMIN"], "username": "admin"}


# ===========================================================================
# 1. Catalogue a product -> it is queued AND the pending count is truthful
# ===========================================================================


def test_catalogued_product_is_queued_and_pending_count_is_non_zero(db):
    """THE owner-visible symptom. A product created through the real create
    door (POST /products -> create_via_door -> _stage_catalog_draft) must land
    in the pending queue, and the COUNT the screen shows must say so."""
    product_repo = ProductRepository(MockCollection("products"))
    audit_repo = AuditRepository(MockCollection("audit_logs"))

    assert _pending(db) == 0

    created = pm.create_via_door(
        _frame_payload(),
        source="FORM",
        actor="u1",
        product_repo=product_repo,
        audit_repo=audit_repo,
        db=db,
    )

    pim_id = created["pim_product_id"]
    doc = db["catalog_products"].find_one({"id": pim_id})
    assert doc is not None, "the create door staged no catalog_products doc"
    assert doc["ecom"]["locally_modified"] is True

    # The SET the sweep walks, and the NUMBER the owner reads.
    assert _dirty_ids(db) == [pim_id]
    assert _pending(db) == 1


def test_a_second_catalogued_product_raises_the_pending_count_to_two(db):
    """The count is a real count, not a boolean dressed up as one."""
    product_repo = ProductRepository(MockCollection("products"))
    audit_repo = AuditRepository(MockCollection("audit_logs"))
    for sku, model in (("FR-QUEUE-001", "RB-2140"), ("FR-QUEUE-002", "RB-3025")):
        pm.create_via_door(
            _frame_payload(sku, model),
            source="FORM",
            actor="u1",
            product_repo=product_repo,
            audit_repo=audit_repo,
            db=db,
        )
    assert _pending(db) == 2


# ===========================================================================
# 2. Edit a catalogue field -> queued again
# ===========================================================================


def _seed_pushed(db, product_id="P1"):
    """A product already live on Shopify and CLEAN (the last push cleared it)."""
    db["catalog_products"].insert_one(
        {
            "id": product_id,
            "sku": "SKU-1",
            "title": "Ray-Ban RB2140",
            "description": "old copy",
            # A product already live on Shopify necessarily has a photograph --
            # the publish rule ("no photo, no publish") refuses one without.
            "images": ["https://cdn.example.com/rb2140.jpg"],
            "inventory": {"locations": {"BV-01": 3}, "total_quantity": 3},
            "ecom": {
                "status": "PUBLISHED",
                "shopify_product_id": "gid://shopify/Product/111",
                "locally_modified": False,
            },
        }
    )
    assert _pending(db) == 0
    return _load(db, product_id)


def test_catalogue_edit_requeues_an_already_pushed_product(db, monkeypatch):
    """A human edit through the catalog door re-queues a row whose flag the
    last push had cleared. (A `setdefault`-style stamp would silently fail
    here -- the key already exists, set to False.)"""
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    doc = _seed_pushed(db)

    doc["description"] = "new copy the storefront must show"
    catalog._save_catalog_product(doc)

    assert _dirty_ids(db) == ["P1"]
    assert _pending(db) == 1


def test_catalogue_edit_through_the_concurrency_safe_door_requeues(db, monkeypatch):
    """The review-drawer PUT uses the compare-and-swap save; it queues too."""
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    db["catalog_products"].insert_one(
        {
            "id": "P2",
            "title": "Old title",
            "updated_at": "2026-08-01T00:00:00",
            "ecom": {"status": "PUBLISHED", "locally_modified": False},
        }
    )
    assert _pending(db) == 0

    doc = _load(db, "P2")
    doc["title"] = "New title"
    doc["updated_at"] = "2026-08-25T00:00:00"
    assert catalog._save_catalog_product_cas(doc, "2026-08-01T00:00:00") is True

    assert _pending(db) == 1


def test_a_product_with_no_ecom_subdoc_still_lands_in_the_queue(db, monkeypatch):
    """The catalog create door builds a doc with no `ecom` key at all. Without
    an ecom sub-doc the row is not even counted as staged, so the flag would
    have nowhere to live and the count would stay blind."""
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    catalog._save_catalog_product({"id": "P3", "title": "Fresh"})
    assert _pending(db) == 1
    assert _dirty_ids(db) == ["P3"]


# ===========================================================================
# 3. THE PING-PONG GUARD -- a Shopify sync write-back must NOT queue
# ===========================================================================


def test_successful_push_clears_the_flag_and_pending_returns_to_zero(db, monkeypatch):
    """A real push (network boundary mocked) write-back must CLEAR the flag, or
    the queue never drains."""
    calls = _force_live(
        monkeypatch,
        {
            "data": {
                "productUpdate": {
                    "product": {"id": "gid://shopify/Product/111", "handle": "rb"},
                    "userErrors": [],
                }
            }
        },
    )
    doc = _seed_pushed(db)
    doc["ecom"]["locally_modified"] = True
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"ecom": doc["ecom"]}})
    assert _pending(db) == 1

    res = _run(shopify_push.push_product(db, _load(db, "P1"), []))
    assert res.ok is True and res.mode == "LIVE"
    assert len(_product_writes(calls)) == 1

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0
    assert _dirty_ids(db) == []


def test_shopify_writeback_never_requeues_the_row(db, monkeypatch):
    """PING-PONG GUARD. The push's own write-back is a SYNC write, not a
    catalogue edit. If it queued the row, every successful push would queue
    itself again and the sweep would push forever against a LIVE storefront.
    Sweep twice: the queue must be empty after each, and the second sweep must
    find nothing left to send."""
    calls = _force_live(
        monkeypatch,
        {
            "data": {
                "productUpdate": {
                    "product": {"id": "gid://shopify/Product/111", "handle": "rb"},
                    "userErrors": [],
                }
            }
        },
    )
    doc = _seed_pushed(db)
    doc["ecom"]["locally_modified"] = True
    db["catalog_products"].update_one({"id": "P1"}, {"$set": {"ecom": doc["ecom"]}})

    for _ in range(2):
        for pid in list(_dirty_ids(db)):
            _run(shopify_push.push_product(db, _load(db, pid), []))
        assert _dirty_ids(db) == [], "the write-back re-queued the row -- ping-pong"
        assert _pending(db) == 0

    # Exactly ONE push happened: the second sweep found nothing to send.
    assert len(_product_writes(calls)) == 1


def test_retired_shopify_sync_stamp_does_not_queue(db, monkeypatch, admin):
    """POST /catalog/products/{id}/sync-shopify writes only sync book-keeping
    (the retired marker). It is on the sync side -- it must never queue."""
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    _seed_pushed(db)

    _run(
        catalog.sync_product_to_shopify(
            "P1", catalog.ShopifySyncInput(sync_to_shopify=True), current_user=admin
        )
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0


def test_bulk_shopify_sync_stamp_does_not_queue(db, monkeypatch, admin):
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    _seed_pushed(db)

    _run(
        catalog.bulk_sync_products_to_shopify(
            ["P1"], catalog.ShopifySyncInput(sync_to_shopify=True), current_user=admin
        )
    )

    assert _pending(db) == 0


# ===========================================================================
# 4. Internal / derived writes must NOT queue
# ===========================================================================


def test_stock_movement_does_not_queue(db, monkeypatch, admin):
    """Quantity is never pushed from the product payload. Flagging on a stock
    movement would queue a no-change push on every sale and every GRN."""
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    _seed_pushed(db)

    _run(
        catalog.adjust_product_inventory(
            "P1", location_id="BV-01", adjustment=-1, current_user=admin
        )
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["inventory"]["total_quantity"] == 2, "the stock write must still land"
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0


def test_soft_delete_does_not_queue(db, monkeypatch, admin):
    """is_active / deleted_at / deleted_by are in no pushed payload -- queuing
    would send a push that changes nothing on the storefront."""
    monkeypatch.setattr(catalog, "_catalog_coll", lambda: db["catalog_products"])
    _seed_pushed(db)

    _run(catalog.delete_catalog_product("P1", current_user=admin))

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["is_active"] is False, "the delete write must still land"
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0


# ===========================================================================
# 5. The billing-spine edit door (PUT /products) mirrors onto the same twin
# ===========================================================================


class _SpineConn:
    """Minimal connection stub for the spine -> catalog twin mirror block
    (same shape as test_product_update_description_weight._ConnStub), but over
    _Coll so dot-notation $set nests like real Mongo."""

    is_connected = True

    def __init__(self, coll):
        self._catalog = coll

    def get_collection(self, name):
        return self._catalog if name == "catalog_products" else MockCollection(name)


def _spine_repo():
    coll = MockCollection("products")
    coll.insert_one(
        {
            "_id": "P1",
            "product_id": "P1",
            "id": "P1",
            "sku": "FR-RB-0001",
            "brand": "Ray-Ban",
            "model": "RB1001",
            "category": "FRAME",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "cost_price": 2000.0,
            "hsn_code": "900311",
            "gst_rate": 5.0,
            "is_active": True,
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1001"},
            "catalog_status": "ACTIVE",
        }
    )
    return ProductRepository(coll)


def _edit_spine(db, monkeypatch, **fields):
    from api.routers import products as products_mod
    import api.dependencies as deps_mod

    repo = _spine_repo()
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps_mod, "get_db", lambda: _SpineConn(db["catalog_products"]))
    _run(
        products_mod.update_product(
            "P1",
            products_mod.ProductUpdate(**fields),
            {
                "user_id": "u1",
                "username": "t",
                "roles": ["ADMIN"],
                "active_store_id": "S1",
            },
        )
    )


def test_price_edit_on_the_billing_spine_queues_the_twin(db, monkeypatch):
    """Owner priority: change the MRP in IMS and the website follows. The spine
    edit mirrors the price onto the catalog twin -- so it must queue it too."""
    _seed_pushed(db)
    _edit_spine(db, monkeypatch, mrp=6000.0)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["pricing"]["mrp"] == 6000.0, "the mirror write must still land"
    assert saved["ecom"]["locally_modified"] is True
    assert _pending(db) == 1


def test_internal_cost_edit_on_the_billing_spine_does_not_queue(db, monkeypatch):
    """cost_price is in NO pushed Shopify payload. Queuing on it would send a
    push that changes nothing on the storefront."""
    _seed_pushed(db)
    _edit_spine(db, monkeypatch, cost_price=2500.0)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["pricing"]["cost_price"] == 2500.0, "the mirror write must still land"
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0
