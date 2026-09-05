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
    # The press PUBLISHES now, and a press that could not publish leaves the
    # row queued on purpose (_requeue_unpublished). Pin the Online Store
    # publication so these fixtures exercise a press that actually went live;
    # the withholding cases pin their own conditions.
    monkeypatch.setenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID", "77")
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
            # A product already live on Shopify necessarily has a photograph
            # and a price: the publish rule ("no photo, no publish") refuses
            # one without a photo, and an unpriced product is withheld from
            # publishing -- and a press that could not publish deliberately
            # leaves the row QUEUED, which would mask the flag lifecycle
            # this file is about.
            "images": ["https://cdn.example.com/rb2140.jpg"],
            "mrp": 5000.0,
            "offer_price": 4000.0,

            "inventory": {"locations": {"BV-01": 3}, "total_quantity": 3},
            "ecom": {
                "status": "PUBLISHED",
                "shopify_product_id": "gid://shopify/Product/111",
                # already seeded by an earlier successful push, so this press
                # neither re-seeds nor is withheld for an unpriced variant.
                "shopify_variant_id": "gid://shopify/ProductVariant/222",
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
                },
                # One press now also attaches the photograph and publishes, and
                # publish is WITHHELD unless the photo provably landed -- a
                # withheld press deliberately leaves the row QUEUED, so this
                # fixture has to answer the media call too or the flag
                # lifecycle under test never happens.
                "productCreateMedia": {
                    "media": [{"id": "gid://shopify/MediaImage/1"}],
                    "mediaUserErrors": [],
                },
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
                },
                # One press now also attaches the photograph and publishes, and
                # publish is WITHHELD unless the photo provably landed -- a
                # withheld press deliberately leaves the row QUEUED, so this
                # fixture has to answer the media call too or the flag
                # lifecycle under test never happens.
                "productCreateMedia": {
                    "media": [{"id": "gid://shopify/MediaImage/1"}],
                    "mediaUserErrors": [],
                },
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


def _edit_spine(db, monkeypatch, repo=None, spine_id="P1", **fields):
    from api.routers import products as products_mod
    import api.dependencies as deps_mod

    repo = repo or _spine_repo()
    monkeypatch.setattr(products_mod, "get_product_repository", lambda: repo)
    monkeypatch.setattr(deps_mod, "get_db", lambda: _SpineConn(db["catalog_products"]))
    _run(
        products_mod.update_product(
            spine_id,
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


def _door_created_spine_repo():
    """A spine row as the CREATE DOOR writes it: `pim_product_id` is a SEPARATE
    uuid the door minted, and the catalog twin is keyed on THAT id -- not the
    spine's own. 71 of the 77 live products are shaped this way (prod census
    2026-08-30); only 6 legacy/convergence twins share the spine id."""
    coll = MockCollection("products")
    coll.insert_one(
        {
            "_id": "SPINE-77",
            "product_id": "SPINE-77",
            "id": "SPINE-77",
            "sku": "FR-RB-0077",
            "brand": "Ray-Ban",
            "model": "RB1077",
            "category": "FRAME",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "cost_price": 2000.0,
            "hsn_code": "900311",
            "gst_rate": 5.0,
            "is_active": True,
            "pim_product_id": "P1",  # the twin's key -- a DIFFERENT uuid
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1077"},
            "catalog_status": "ACTIVE",
        }
    )
    return ProductRepository(coll)


def test_a_door_created_products_price_edit_reaches_its_pim_keyed_twin(
    db, monkeypatch
):
    """THE 71-of-77 BUG. A door-created product's catalog twin is keyed on
    pim_product_id (a different uuid), but the PUT /products mirror filtered on
    the SPINE id -- so the write matched nothing: the POS price moved, the
    WEBSITE price stayed stale, and ecom.locally_modified landed nowhere, so
    the edit never even queued. Silently. The mirror must resolve the twin the
    way the service door does: pim_product_id first, spine id as the legacy
    fallback (which test_price_edit_on_the_billing_spine_queues_the_twin pins)."""
    _seed_pushed(db)  # the twin lives at id "P1" == the spine's pim_product_id

    _edit_spine(
        db,
        monkeypatch,
        repo=_door_created_spine_repo(),
        spine_id="SPINE-77",
        mrp=6000.0,
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["pricing"]["mrp"] == 6000.0, (
        "the price edit must reach the pim-keyed twin, not silently miss it"
    )
    assert saved["ecom"]["locally_modified"] is True, (
        "the edit must QUEUE the twin for the manual Online Store push"
    )
    assert _pending(db) == 1
    # ...and nothing was upserted/written under the spine's own id.
    assert db["catalog_products"].find_one({"id": "SPINE-77"}) is None


def test_internal_cost_edit_on_the_billing_spine_does_not_queue(db, monkeypatch):
    """cost_price is in NO pushed Shopify payload. Queuing on it would send a
    push that changes nothing on the storefront."""
    _seed_pushed(db)
    _edit_spine(db, monkeypatch, cost_price=2500.0)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["pricing"]["cost_price"] == 2500.0, "the mirror write must still land"
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0


def test_the_spine_edit_leaves_a_queued_row_in_a_status_bucket(db, monkeypatch):
    """The 6eede9b bug, one door over. This door sets the flag by DOT-NOTATION
    with no status default, so on a twin with no `ecom` sub-doc Mongo creates
    {ecom: {locally_modified: true}} -- a row the Online Store screen counts as
    pending while BOTH status cards (DRAFT and PUBLISHED) count it as neither."""
    db["catalog_products"].insert_one(
        {
            "id": "P1",
            "sku": "SKU-1",
            "title": "Ray-Ban RB2140",
            "images": ["https://cdn.example.com/rb2140.jpg"],
            "mrp": 5000.0,
        }
    )

    _edit_spine(db, monkeypatch, mrp=6000.0)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["locally_modified"] is True
    assert saved["ecom"]["status"] == "DRAFT", "a queued row in no status bucket"
    assert _pending(db) == 1


def test_the_spine_edit_never_demotes_a_live_products_status(db, monkeypatch):
    """The other direction: the default is a DEFAULT. An edit to a product that
    IS on the storefront must not tell the screen it went back to draft."""
    _seed_pushed(db)

    _edit_spine(db, monkeypatch, mrp=6000.0)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["ecom"]["status"] == "PUBLISHED"
    assert saved["ecom"]["locally_modified"] is True


# ===========================================================================
# 6. The product-master edit door (PUT /product-master/master/{id})
# ===========================================================================


def _pm_spine_repo():
    coll = MockCollection("products")
    coll.insert_one(
        {
            "_id": "PM1",
            "product_id": "PM1",
            "id": "PM1",
            "sku": "FR-RB-0002",
            "brand": "Ray-Ban",
            "model": "RB1002",
            "category": "FRAME",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "is_active": True,
            "pim_product_id": "P1",
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1002"},
        }
    )
    return ProductRepository(coll)


def test_a_price_change_on_the_product_master_door_queues_the_twin(db, monkeypatch):
    """The THIRD price door. It mirrors mrp / offer_price onto the catalog twin
    -- the very fields the push sends as the variant price -- but never set the
    one flag the push selects on, so an MRP changed here moved in IMS and never
    reached the storefront."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    _seed_pushed(db)

    pm.update_product(
        product_id="PM1",
        patch={"mrp": 6000.0},
        actor="u1",
        product_repo=_pm_spine_repo(),
        audit_repo=AuditRepository(MockCollection("audit_logs")),
        db=db,
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["mrp"] == 6000.0, "the mirror write must still land"
    assert saved["ecom"]["locally_modified"] is True
    assert _pending(db) == 1


def test_an_internal_field_on_the_product_master_door_does_not_queue(db, monkeypatch):
    """gst_rate is in NO pushed payload -- queuing on it would send a push that
    changes nothing on the storefront."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")
    _seed_pushed(db)

    pm.update_product(
        product_id="PM1",
        patch={"gst_rate": 12.0},
        actor="u1",
        product_repo=_pm_spine_repo(),
        audit_repo=AuditRepository(MockCollection("audit_logs")),
        db=db,
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["gst_rate"] == 12.0, "the mirror write must still land"
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0


# ===========================================================================
# 7. ONE mirror rule, not two (PR #1029 follow-up). The spine PUT and the
# /products/master door used to carry SEPARATE copies of the edit->twin
# mirror, and the copies had drifted: different field sets, different price
# spellings (pricing.* vs top-level), a description-only edit queued on one
# door and not the other, a flag gate on one door only, and no legacy-key
# fallback on the service door. Both doors now call
# product_master.mirror_update_to_catalog_twin; these tests pin each decided
# behaviour AND the identity of the two doors' output.
# ===========================================================================


def _pm_edit(db, patch, repo=None):
    pm.update_product(
        product_id="PM1",
        patch=patch,
        actor="u1",
        product_repo=repo or _pm_spine_repo(),
        audit_repo=AuditRepository(MockCollection("audit_logs")),
        db=db,
    )


def test_spine_price_edit_updates_the_top_level_price_the_push_reads(db, monkeypatch):
    """THE stale-price hazard the dedup fixed on the ROUTER side. A PM-born
    twin carries its price TOP-LEVEL (_build_pim_doc), and the push resolves
    the top-level value FIRST (shopify_push._resolve_variant_pricing) -- so a
    mirror that moved only pricing.mrp left the stale top-level 5000 winning:
    the queued push would have shipped the OLD price. The mirror must write
    BOTH spellings."""
    _seed_pushed(db)  # twin has top-level mrp 5000.0 and NO pricing sub-doc
    _edit_spine(db, monkeypatch, mrp=6000.0)

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["mrp"] == 6000.0, (
        "top-level mrp is what the push resolves first; leaving it stale "
        "ships the OLD price"
    )
    assert saved["pricing"]["mrp"] == 6000.0
    # The push-side resolution actually lands on the new price.
    price, mrp = shopify_push._resolve_variant_pricing(saved, {})
    assert mrp == 6000.0


def test_master_door_price_edit_lands_in_both_price_spellings(db):
    """Same rule, other door: the service used to write ONLY the top-level
    spelling, so a catalog-door twin's pricing.mrp went stale."""
    _seed_pushed(db)
    _pm_edit(db, {"mrp": 6000.0})

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["mrp"] == 6000.0
    assert saved["pricing"]["mrp"] == 6000.0
    assert saved["ecom"]["locally_modified"] is True


def test_master_door_mirrors_with_the_mirror_flag_off(db):
    """The mirror flag guards EXTERNAL (Postgres/Shopify) writes, not the
    local Mongo twin (_stage_catalog_draft precedent). The service door used
    to gate the twin mirror on it, so on a normal deploy (flag off) a price
    edited through /products/master moved in IMS and NEVER reached the twin
    or the queue. The autouse _mirror_off fixture keeps the flag OFF here."""
    _seed_pushed(db)
    _pm_edit(db, {"mrp": 6000.0})

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["mrp"] == 6000.0, "flag OFF must not silence the local twin mirror"
    assert saved["ecom"]["locally_modified"] is True
    assert _pending(db) == 1


def test_master_door_reaches_a_legacy_spine_keyed_twin(db):
    """The service door used to REQUIRE pim_product_id -- a legacy /
    convergence twin (keyed on the spine's own id, 6 of the 77 live products)
    was silently never mirrored from /products/master. The shared rule falls
    back to the spine id, exactly as the spine PUT always did."""
    coll = MockCollection("products")
    coll.insert_one(
        {
            "_id": "P1",  # legacy: twin shares the spine's own id
            "product_id": "P1",
            "id": "P1",
            "sku": "FR-RB-0003",
            "brand": "Ray-Ban",
            "model": "RB1003",
            "category": "FRAME",
            "mrp": 5000.0,
            "offer_price": 4500.0,
            "is_active": True,
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RB1003"},
        }
    )
    _seed_pushed(db)  # twin at id P1
    pm.update_product(
        product_id="P1",
        patch={"mrp": 6000.0},
        actor="u1",
        product_repo=ProductRepository(coll),
        audit_repo=AuditRepository(MockCollection("audit_logs")),
        db=db,
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["mrp"] == 6000.0, "a legacy-keyed twin must not be silently missed"
    assert saved["ecom"]["locally_modified"] is True


def test_a_missing_twin_is_never_upserted_into_a_fragment(db, monkeypatch):
    """A twin is born only at the create door. The service's old repo branch
    UPSERTED the patch, minting a fragment doc (no sku, no name, no ecom
    identity) invisible to every sku-joined consumer. Both doors: a missing
    twin is a NO-OP."""
    _edit_spine(db, monkeypatch, mrp=6000.0)  # no twin seeded
    _pm_edit(db, {"mrp": 6500.0})

    assert db["catalog_products"].find_one({"id": "P1"}) is None
    assert len(db["catalog_products"]._data) == 0, "no fragment twin may be minted"


def test_both_doors_write_the_identical_twin(db, monkeypatch):
    """THE anti-drift tripwire. Drive the SAME price edit through BOTH doors
    over identically seeded twins: the resulting twin docs must be EQUAL. If
    someone re-inlines a copy of the mirror in either door and it drifts,
    this is the test that reddens."""
    _seed_pushed(db, "P1")
    db2 = _DB()
    _run_seed = _seed_pushed(db2, "P1")  # noqa: F841 -- same starting twin

    _edit_spine(db, monkeypatch, offer_price=4200.0)
    _pm_edit(db2, {"offer_price": 4200.0})

    spine_twin = _load(db, "P1")
    master_twin = copy.deepcopy(db2["catalog_products"].find_one({"id": "P1"}))
    # The doors stamp no door-specific keys on the twin; compare whole docs.
    assert spine_twin == master_twin, (
        "the two edit doors produced DIFFERENT twins -- the mirror rule has "
        "been duplicated and drifted again"
    )


# ===========================================================================
# 8. The customer-facing fields the edit mirror used to DROP (2026-09-06).
# Prod measurement on the 6 IMS-pushed (non-smartglass) products: every field
# IMS holds reached Shopify equal -- title / vendor / productType / tags /
# price / images / ims.* metafields -- so build_product_input's mapping is
# complete. The open door was the EDIT: brand / category / attributes / tags
# stopped at the spine, so the twin (the doc the push reads) and therefore
# Shopify went stale after an edit; one prod twin had already drifted on
# attributes. Each test here reddens if its line of the mirror is reverted.
# ===========================================================================


def test_spine_put_carries_brand_attributes_and_tags_onto_the_twin_and_queues(
    db, monkeypatch
):
    _seed_pushed(db)
    _edit_spine(
        db,
        monkeypatch,
        brand="Oakley",
        attributes={"frame_color": "Matte Black", "gtin": "8901234567893"},
        tags=["Polarised"],
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["brand"] == "Oakley", "vendor is read off the twin"
    assert saved["attributes"]["frame_color"].lower() == "matte black", (
        "metafields + filter tags read the twin's attributes"
    )
    assert saved["attributes"]["brand_name"] == "Ray-Ban", (
        "the route's MERGED bag lands, not a partial overwrite"
    )
    assert saved["gtin"] == "8901234567893", (
        "the public barcode the pseudo-variant reads top-level"
    )
    assert saved["ecom"]["seo"]["tags"] == ["polarised"], (
        "build_product_input reads ONLY ecom.seo.tags"
    )
    assert saved["ecom"]["locally_modified"] is True
    assert _pending(db) == 1
    # ...and the payload the next press sends now carries them.
    inp = shopify_push.build_product_input(saved, [])
    assert inp["vendor"] == "Oakley"
    assert "polarised" in inp["tags"]
    mf = {m["key"]: m["value"] for m in shopify_push.build_product_metafields(saved)}
    assert mf["frame_color"].lower() == "matte black"


def test_master_door_carries_category_brand_attributes_and_tags(db):
    from api.services.ecom_category_map import ims_to_shopify_type

    _seed_pushed(db)
    _pm_edit(
        db,
        {
            "category": "SUNGLASS",
            "brand": "Oakley",
            "attributes": {"lens_color": "Grey"},
            "tags": ["Summer"],
        },
    )

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["category"] == "SUNGLASS"
    assert saved["brand"] == "Oakley"
    assert saved["attributes"] == {"lens_color": "Grey"}, (
        "verbatim what the spine now holds (the service door writes the "
        "patch's attributes wholesale to the spine too)"
    )
    assert saved["ecom"]["seo"]["tags"] == ["summer"]
    assert saved["ecom"]["locally_modified"] is True
    assert shopify_push.build_product_input(saved, [])["productType"] == (
        ims_to_shopify_type("SUNGLASS")
    )


def test_model_edit_mirrors_but_does_not_queue(db):
    """model is in NO pushed payload (the spine never recomputes `name` on
    edit, so the Shopify title cannot move): mirror for the PIM screens,
    queue nothing."""
    _seed_pushed(db)
    _pm_edit(db, {"model": "RB1002X"})

    saved = db["catalog_products"].find_one({"id": "P1"})
    assert saved["model"] == "RB1002X"
    assert saved["ecom"]["locally_modified"] is False
    assert _pending(db) == 0


def test_build_pim_doc_projects_the_attribute_gtin_as_the_public_barcode():
    """The spine captures GTIN as an ATTRIBUTE; the push's pseudo-variant
    reads product.gtin TOP-LEVEL. Without the projection a catalogued GTIN
    only ever reached Shopify as an ims.gtin metafield, never as the variant
    barcode the shopping feeds republish."""
    doc = pm._build_pim_doc(
        {"pim_product_id": "PIM-9", "sku": "X", "attributes": {"gtin": "8901234567893"}}
    )
    assert doc["gtin"] == "8901234567893"
    doc["ecom"]["shopify_variant_id"] = "gid://shopify/ProductVariant/1"
    pseudo = shopify_push._variants_for_price_push(doc, [])
    assert pseudo and pseudo[0]["gtin"] == "8901234567893"
    # tolerant of a spine with no gtin at all
    assert pm._build_pim_doc({"pim_product_id": "PIM-10"})["gtin"] is None
