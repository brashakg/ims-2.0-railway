"""IMS 2.0 - items ordered before they were catalogued (audit C1, C2, C3).

The owner's procurement audit (2026-09-28) walked this in the real screens:

  C1 (blocker) The manager types an item onto a PO through "Not in the
     catalogue?". The box arrives, Receive Goods holds those units "waiting to
     be catalogued" -- and nobody can release them: the draft is an inactive
     row the cataloguer cannot find (Needs review shows 0, no task), the
     manager is refused the catalogue, "Add to stock" does nothing visible,
     and the receipt row wears a green "On shelf" chip for 0 units (that chip
     is a frontend test: PurchaseStatusChip heldReceipt test).
  C2 (major)   The manager's search failed, so he typed in a Carrera CA 8895
     807 that ALREADY exists. The order silently made a second, hidden product.
  C3 (major)   The cataloguer then catalogued the Boss 1700 C2 the manager had
     already ordered. No duplicate warning; now two Boss 1700 C2 exist and the
     held box still points at the hidden one.

This file is the WORLD those findings live in, driven through the real code
path end to end: POST /vendors/purchase-orders (create_po) -> send_po ->
POST /vendors/grn (create) -> POST /vendors/grn/{id}/accept -> the catalogue's
own doors (POST /products, PUT /products/{id}, the Needs-review tally), over
the REAL repositories on a strict in-memory Mongo (tests/strict_fakes.py) so a
filter the fake does not understand fails loudly instead of matching
everything.

The findings were pinned here as strict xfails and are now fixed; each test is
the regression guard for its rule (a finding check raises FindingStillOpen).

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \
        backend/tests/test_off_catalogue_items_release.py -q

No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import database.connection as _dbconn  # noqa: E402
from api import dependencies as _deps  # noqa: E402
from api.routers import catalog as _catalog  # noqa: E402
from api.routers import products as _products  # noqa: E402
from api.routers import tasks as _tasks  # noqa: E402
from api.routers import vendors as vd  # noqa: E402
from api.services import online_catalog as _online  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE = "BV-DHN-02"
VENDOR = "V-JOT"

MANAGER = {
    "user_id": "u-mgr-dhn2",
    "username": "mgr.dhn2",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
CATALOGUER = {
    "user_id": "u-cat-hq",
    "username": "catalog.hq",
    "roles": ["CATALOG_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}

# What the manager typed on the audit's PO (notes/critic/s2_po_new_item.mjs).
BOSS_TYPED = {
    "category": "FR",
    "brand": "Boss",
    "model": "BOSS 1700",
    "colour": "C2",
    "size": "52",
    "mrp": 2990,
}
CARRERA_TYPED = {
    "category": "FR",
    "brand": "Carrera",
    "model": "CA 8895",
    "colour": "807",
    "size": "54",
    "mrp": 6990,
}


class FindingStillOpen(AssertionError):
    """The audit finding this test pins is still reproducible."""


def finding(ok: bool, message: str) -> None:
    if not ok:
        raise FindingStillOpen(message)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


class _Conn:
    """The DatabaseConnection shape (conn.db, conn.<collection>,
    conn.get_collection) over one StrictDB, so every door -- the dependency
    repositories, the vendors package's _get_db, database.connection.get_db --
    lands in the SAME in-memory database and nothing reaches a real Mongo."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def get_collection(self, name):
        return self.db.get_collection(name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self.db.get_collection(name)


class _FileStore:
    """The receipt photo the manager uploads first (F-S3 gate)."""

    def get_metadata(self, file_id):
        return {"kind": vd._GRN_DOCUMENT_KIND, "store_id": STORE}


class World:
    def __init__(self, db):
        self.db = db

    # -- reads ---------------------------------------------------------------
    def products_named(self, brand, model):
        want = (brand.lower(), model.lower())
        return [
            p
            for p in self.db.products.find({})
            if (str(p.get("brand", "")).lower(), str(p.get("model", "")).lower())
            == want
        ]

    def product(self, pid):
        return self.db.products.find_one({"product_id": pid})

    def units(self, pid):
        return list(
            self.db.stock_units.find(
                {"product_id": pid, "store_id": STORE, "status": "AVAILABLE"}
            )
        )

    def grn(self, grn_id):
        return self.db.grns.find_one({"grn_id": grn_id})

    # -- the manager's day ---------------------------------------------------
    def raise_po(self, lines):
        body = vd.POCreate(
            vendor_id=VENDOR,
            delivery_store_id=STORE,
            items=[vd.POItemCreate(**ln) for ln in lines],
        )
        out = _run(vd.create_po(body, MANAGER))
        po = self.db.purchase_orders.find_one({"po_id": out["po_id"]})
        assert po is not None, "the PO was not stored"
        _run(vd.send_po(po["po_id"], MANAGER))
        return self.db.purchase_orders.find_one({"po_id": po["po_id"]})

    def receive_everything(self, po, invoice_no="JOT/26-27/0701"):
        """Receive Goods, two-step: count every line in full, tick it, create
        the receipt, then accept it ("Add to stock")."""
        items = [
            vd.GRNItemCreate(
                product_id=it["product_id"],
                received_qty=int(it.get("ordered_qty") or it.get("quantity")),
                accepted_qty=int(it.get("ordered_qty") or it.get("quantity")),
                rejected_qty=0,
                tallied=True,
            )
            for it in po["items"]
        ]
        created = _run(
            vd.create_grn(
                vd.GRNCreate(
                    po_id=po["po_id"],
                    vendor_invoice_no=invoice_no,
                    vendor_invoice_date="2026-09-28",
                    items=items,
                    attachment_file_id="F-RECEIPT-PHOTO",
                    attachment_filename="bill.jpg",
                    attachment_mime="image/jpeg",
                ),
                MANAGER,
            )
        )
        accepted = _run(vd.accept_grn(created["grn_id"], MANAGER))
        return created, accepted

    def order_and_receive(self, typed, qty, cost):
        po = self.raise_po(
            [{"new_product": dict(typed), "quantity": qty, "unit_price": cost}]
        )
        draft_id = po["items"][0]["product_id"]
        grn, accepted = self.receive_everything(po)
        # The C1 hold, as the audit saw it (s2_release.out): the receipt is
        # PARTIALLY_ACCEPTED, the line is held, not one unit is on the shelf.
        assert accepted["grn_status"] == "PARTIALLY_ACCEPTED", accepted
        assert accepted["units_added"] == 0, accepted
        assert [u["product_id"] for u in accepted["unresolved_lines"]] == [draft_id]
        assert self.units(draft_id) == []
        return po, grn, draft_id

    # -- the cataloguer's day ------------------------------------------------
    def catalogue_frame(self, brand, model, colour, lens_size, mrp, offer, cost):
        """Catalog > Add product, Frame -- the payload the Add-Product form
        builds (domain/catalog/productAdd/formModel.buildProductPayload): flat
        brand/model, the identity in `attributes`, eye size as lens_size."""
        body = _products.ProductCreate(
            category="FR",
            brand=brand,
            model=model,
            attributes={
                "brand_name": brand,
                "model_no": model,
                "colour_code": colour,
                "lens_size": lens_size,
            },
            mrp=mrp,
            offer_price=offer,
            cost_price=cost,
        )
        return _run(_products.create_product(body, CATALOGUER, as_draft=False))

    def finish_draft(self, pid, offer):
        """The cataloguer finishes the manager's draft in the Edit screen
        (useQuickAddForm spine edit: one PUT /products/{id}; it never sends
        is_active)."""
        cur = self.product(pid)
        body = _products.ProductUpdate(
            brand=cur["brand"],
            model=cur["model"],
            attributes=dict(cur.get("attributes") or {}),
            mrp=cur["mrp"],
            offer_price=offer,
            cost_price=cur.get("cost_price"),
        )
        _run(_products.update_product(pid, body, CATALOGUER))
        return self.product(pid)


@pytest.fixture
def world(monkeypatch):
    db = StrictDB()
    db.seed(
        "stores",
        [
            {
                "store_id": STORE,
                "store_name": "Hirapur Dhanbad",
                "store_code": STORE,
                "store_type": "RETAIL",
                "state": "Jharkhand",
                "state_code": "20",
                "is_active": True,
            }
        ],
    )
    db.seed(
        "vendors",
        [
            {
                "vendor_id": VENDOR,
                "trade_name": "Jharkhand Optical Traders",
                "legal_name": "Jharkhand Optical Traders",
                "state": "Jharkhand",
                "state_code": "20",
                "is_active": True,
            }
        ],
    )
    db.seed(
        "users",
        [
            {
                **{k: v for k, v in MANAGER.items() if k != "active_store_id"},
                "full_name": "Mgr Dhn2",
                "is_active": True,
            },
            {
                **{k: v for k, v in CATALOGUER.items() if k != "active_store_id"},
                "full_name": "Catalog HQ",
                "is_active": True,
            },
        ],
    )
    conn = _Conn(db)
    monkeypatch.setattr(_deps, "get_db", lambda: conn)
    monkeypatch.setattr(_dbconn, "get_db", lambda: conn)
    monkeypatch.setattr(vd, "_get_db", lambda: db)
    monkeypatch.setattr(vd, "get_file_store", lambda: _FileStore())
    return World(db)


# ---------------------------------------------------------------------------
# The trace itself holds: if any of these break, the world no longer
# reproduces the audit and every rule below would be meaningless.
# ---------------------------------------------------------------------------


def _open_tasks(world):
    return [
        t
        for t in world.db.tasks.find({})
        if str(t.get("status", "")).upper() in {"OPEN", "IN_PROGRESS", "ESCALATED"}
    ]


def _needs_review_list(world):
    """GET /catalog/products?needs_review=true&is_active=all -- the list the
    sidebar badge opens."""
    listed = _run(
        _catalog.list_catalog_products(
            category=None,
            brand=None,
            search=None,
            is_active="all",
            needs_review=True,
            source=None,
            photo=None,
            limit=250,
            page=1,
            current_user=CATALOGUER,
        )
    )
    return [p.get("sku") for p in listed.get("products", [])]


def test_the_world_reproduces_the_hold(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    draft = world.product(draft_id)
    # purchase_orders.create_po -> product_master.create_via_door(provisional)
    assert draft["provisional"] is True
    assert draft["is_active"] is False
    assert draft["catalog_status"] == "DRAFT"
    assert draft["done_gaps"] == ["offer_price"]
    # A frame's typed size is its eye size -- the registry's lens_size, the
    # key the Add-Product form writes -- so the PO draft carries the SAME
    # identity_key a catalogued Boss 1700 C2 52 does (C2/C3 root cause), and
    # the eye size is part of it (owner 09-28: each eye size is its own item).
    assert draft["attributes"]["lens_size"] == "52"
    assert draft["identity_key"] == "boss|boss1700|c2|52"
    # grn_accept: the line is held with reason incomplete_catalog, 0 minted.
    stored = world.grn(grn["grn_id"])
    assert stored["status"] == "PARTIALLY_ACCEPTED"
    assert stored["unresolved_lines"][0]["reason"] == "incomplete_catalog"


# ---------------------------------------------------------------------------
# C1 -- held units with no release path
# ---------------------------------------------------------------------------


def test_c1_finishing_the_draft_puts_the_held_units_on_the_shelf(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    assert len(_open_tasks(world)) == 1

    finished = world.finish_draft(draft_id, offer=2790)
    # Precondition: the Edit screen really did complete the catalogue entry.
    assert finished["catalog_status"] == "ACTIVE", finished.get("done_gaps")

    units = world.units(draft_id)
    finding(
        len(units) == 2,
        f"C1: the draft is finished but {len(units)} of 2 held units are on "
        "the shelf -- someone still has to find the receipt and press "
        "'Add to stock' again",
    )
    finding(
        world.grn(grn["grn_id"])["status"] == "ACCEPTED",
        "C1: the receipt still says units are waiting to be catalogued",
    )
    finding(
        world.product(draft_id).get("is_active") is True
        and world.product(draft_id).get("provisional") is False,
        "C1: the finished product is still inactive (provisional), so the "
        "counter cannot sell the units even once they are on the shelf",
    )
    finding(
        world.db.purchase_orders.find_one({"po_id": po["po_id"]})["status"]
        == "RECEIVED",
        "C1: the order still reads partly received after its units went on the shelf",
    )
    # The job is done: the cataloguer's task closes, the draft leaves the queue.
    finding(not _open_tasks(world), "C1: the task stays open after the release")
    finding(
        finished["sku"] not in _needs_review_list(world),
        "C1: the finished product is still in Needs review",
    )


def test_c1_finishing_never_reactivates_what_the_cataloguer_switched_off(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    cur = world.product(draft_id)
    body = _products.ProductUpdate(
        brand=cur["brand"],
        model=cur["model"],
        attributes=dict(cur.get("attributes") or {}),
        mrp=cur["mrp"],
        offer_price=2790,
        cost_price=cur.get("cost_price"),
        is_active=False,
    )
    _run(_products.update_product(draft_id, body, CATALOGUER))
    finding(
        world.product(draft_id).get("is_active") is False,
        "C1: finishing the draft switched back on a product the cataloguer "
        "switched off in the same save",
    )


def test_c1_the_held_draft_is_in_needs_review_at_the_top(world):
    # An import awaiting review that is NEWER than the manager's draft: the
    # plain newest-first order would put it above the draft.
    world.db.seed(
        "catalog_products",
        [
            {
                "id": "bvi-newer",
                "sku": "BVI-NEWER",
                "category": "FRAME",
                "needs_review": True,
                "is_active": False,
                "created_at": "2099-01-01T00:00:00",
            }
        ],
    )
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    sku = world.product(draft_id)["sku"]

    # The sidebar badge and the Catalog counts row: GET /catalog/online-summary.
    count = _online.catalog_counts(world.db)["needs_review"]
    skus = _needs_review_list(world)
    finding(
        count == 2,
        f"C1: Needs review shows {count} while a receipt waits on this draft",
    )
    finding(
        sku in skus,
        f"C1: the Needs-review list does not contain the held draft {sku}",
    )
    finding(
        skus[0] == sku,
        f"C1: the held draft is not at the top of Needs review ({skus})",
    )


def test_c1_a_held_receipt_raises_a_task_for_a_person(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    # "Add to stock" pressed again while the line is still held: no second task.
    again = _run(vd.accept_grn(grn["grn_id"], MANAGER))
    assert again["grn_status"] == "PARTIALLY_ACCEPTED"

    open_tasks = _open_tasks(world)
    finding(
        bool(open_tasks),
        "C1: nobody is told -- no task exists after units were held",
    )
    # Owner 2026-09-03: tasks go to PEOPLE, never a title. The person who can
    # finish the product is the catalogue manager (the store manager is
    # refused the catalogue: routes/catalogRoutes.tsx, products._CATALOG_ROLES).
    mine = [t for t in open_tasks if t.get("assigned_to") == CATALOGUER["user_id"]]
    finding(
        len(mine) == 1 and len(open_tasks) == 1,
        "C1: not exactly one task, assigned to the catalogue manager by person "
        f"(assignees: {[t.get('assigned_to') for t in open_tasks]})",
    )
    text = " ".join(str(mine[0].get(k) or "") for k in ("title", "description"))
    for must in (grn["grn_number"], "BOSS 1700", STORE):
        finding(must in text, f"C1: the task does not name {must!r}: {text!r}")


def test_c1_the_task_goes_to_the_catalogue_manager_of_that_entity(world):
    # Better Vision's shops are one legal entity; WizOpt's is another.
    world.db.stores.update_one({"store_id": STORE}, {"$set": {"entity_id": "E-BV"}})
    world.db.seed(
        "stores",
        [
            {"store_id": "BV-HQ", "entity_id": "E-BV", "is_active": True},
            {"store_id": "WO-PUN-01", "entity_id": "E-WIZ", "is_active": True},
        ],
    )
    world.db.users.update_one(
        {"user_id": CATALOGUER["user_id"]}, {"$set": {"store_ids": ["WO-PUN-01"]}}
    )
    world.db.seed(
        "users",
        [
            {
                "user_id": "u-cat-bv",
                "username": "catalog.bv",
                "roles": ["CATALOG_MANAGER"],
                "store_ids": ["BV-HQ"],
                "is_active": True,
            }
        ],
    )
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    tasks = _open_tasks(world)
    assignees = [t.get("assigned_to") for t in tasks]
    finding(
        assignees == ["u-cat-bv"],
        f"C1: the task did not go to the entity's catalogue manager ({assignees})",
    )
    # The task list and task page are store-scoped: the task sits in a store
    # its assignee can open, and still names the receiving shop.
    finding(
        tasks[0].get("store_id") == "BV-HQ" and STORE in tasks[0]["title"],
        f"C1: the task is outside its assignee's stores ({tasks[0].get('store_id')})",
    )


def test_c1_no_catalogue_manager_fails_loud_to_the_admins(world, caplog):
    world.db.users.update_one(
        {"user_id": CATALOGUER["user_id"]}, {"$set": {"is_active": False}}
    )
    world.db.seed(
        "users",
        [
            {
                "user_id": "u-admin",
                "username": "admin",
                "roles": ["ADMIN"],
                "store_ids": [],
                "is_active": True,
            }
        ],
    )
    with caplog.at_level("ERROR"):
        po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    tasks = _open_tasks(world)
    finding(
        [t.get("assigned_to") for t in tasks] == ["u-admin"]
        and "No catalogue manager" in tasks[0]["title"]
        and grn["grn_number"] in tasks[0]["title"],
        f"C1: a shop with no catalogue manager is not raised to the admins ({tasks})",
    )
    finding(
        any(
            r.levelname == "ERROR" and "NO catalogue manager" in r.getMessage()
            for r in caplog.records
        ),
        "No ERROR is logged when no catalogue manager covers the shop",
    )


def test_c1_each_catalogue_manager_gets_their_own_task(world):
    # Two catalogue managers of the same entity: one task EACH, by name -- a
    # task one of them closes never silences the other.
    world.db.seed(
        "users",
        [
            {
                "user_id": "u-cat-2",
                "username": "catalog.two",
                "roles": ["CATALOG_MANAGER"],
                "store_ids": [STORE],
                "is_active": True,
            }
        ],
    )
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    tasks = _open_tasks(world)
    finding(
        sorted(t.get("assigned_to") for t in tasks)
        == sorted([CATALOGUER["user_id"], "u-cat-2"]),
        "C1: not one task per catalogue manager "
        f"({[(t.get('assigned_to'), t.get('source_ref')) for t in tasks]})",
    )


def _task_list(user, store_id):
    out = _run(
        _tasks.list_tasks(
            status="OPEN",
            priority=None,
            assigned_to=None,
            task_type=None,
            store_id=store_id,
            skip=0,
            limit=50,
            current_user=user,
        )
    )
    return [(t.get("assigned_to"), t.get("title")) for t in out["tasks"]]


def test_c1_asking_for_cataloguing_for_a_bill_reaches_the_catalogue_manager(world):
    # Purchase Invoices: the accountant is stopped by an unfinished product and
    # asks for it (InvoiceFormDrawer does this when a booking is refused). The
    # ask goes through the SAME named-person door as a held receipt's task.
    from api.routers import purchase_invoices as _pi

    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _run(
        _pi.request_cataloguing(
            _pi.CataloguingRequest(product_ids=[draft_id]), ACCOUNTANT
        )
    )
    want = "Finish cataloguing 1 item(s) - a vendor bill is waiting"
    for store_id in (STORE, None):
        finding(
            (CATALOGUER["user_id"], want) in _task_list(CATALOGUER, store_id),
            "The catalogue manager cannot see the ask for cataloguing "
            f"(store_id={store_id}: {_task_list(CATALOGUER, store_id)})",
        )


# ---------------------------------------------------------------------------
# C2 -- typing an item we already have makes a hidden twin
# ---------------------------------------------------------------------------


def _refused_po(world, lines):
    try:
        world.raise_po(lines)
    except HTTPException as exc:
        return exc
    return None


def test_c2_typing_an_item_we_already_have_uses_it(world):
    existing = world.catalogue_frame(
        "Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3155.76
    )
    assert len(world.products_named("Carrera", "CA 8895")) == 1

    refused = _refused_po(
        world, [{"new_product": dict(CARRERA_TYPED), "quantity": 2, "unit_price": 3200}]
    )

    twins = world.products_named("Carrera", "CA 8895")
    finding(
        len(twins) == 1,
        f"C2: the PO silently made a second Carrera CA 8895 807 "
        f"({[(p['sku'], p.get('is_active')) for p in twins]})",
    )
    detail = (refused.detail if refused else None) or {}
    finding(
        refused is not None
        and refused.status_code == 409
        and detail.get("code") == "ALREADY_IN_CATALOGUE",
        "C2: the server did not answer 'already in the catalogue' for the typed line",
    )
    finding(
        [m["existing"]["product_id"] for m in detail.get("matches", [])]
        == [existing["product_id"]],
        "C2: the answer does not point at the catalogued Carrera CA 8895 807",
    )
    # The name alone does not carry the eye size; the manager must see WHICH.
    finding(
        "size 54" in detail.get("message", ""),
        f"C2: the answer does not name the eye size ({detail.get('message')!r})",
    )
    assert world.db.purchase_orders.count_documents({}) == 0

    # The composer's "use it?" -> yes: the line is resent naming that product.
    match = detail["matches"][0]["existing"]
    po = world.raise_po(
        [
            {
                "product_id": match["product_id"],
                "product_name": match["name"],
                "sku": match["sku"],
                "quantity": 2,
                "unit_price": 3200,
            }
        ]
    )
    assert po["items"][0]["product_id"] == existing["product_id"]


def test_c2_typing_an_item_already_on_order_uses_the_draft(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

    refused = _refused_po(
        world, [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    detail = (refused.detail if refused else None) or {}
    finding(
        [m["existing"]["product_id"] for m in detail.get("matches", [])] == [draft_id],
        "C2: a second order for the same typed-in item did not point at the draft",
    )
    finding(
        len(world.products_named("Boss", "BOSS 1700")) == 1,
        "C2: the second order made a second Boss 1700 C2",
    )


# ---------------------------------------------------------------------------
# C3 -- the cataloguer gets no warning against the manager's draft
# ---------------------------------------------------------------------------


def test_c3_cataloguing_an_ordered_item_warns_against_the_draft(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

    refused = None
    try:
        world.catalogue_frame(
            "Boss", "BOSS 1700", "C2", "52", mrp=2990, offer=2790, cost=1200
        )
    except HTTPException as exc:
        refused = exc

    twins = world.products_named("Boss", "BOSS 1700")
    finding(
        len(twins) == 1,
        f"C3: two Boss 1700 C2 products now exist "
        f"({[(p['sku'], p.get('is_active')) for p in twins]})",
    )
    finding(
        refused is not None and refused.status_code == 409,
        "C3: the Add-product door gave no duplicate warning",
    )
    existing = (refused.detail or {}).get("existing") if refused else None
    finding(
        (existing or {}).get("product_id") == draft_id,
        "C3: the warning does not point at the manager's draft that the held "
        "receipt is waiting on",
    )
    # What the popup keys on to lead the cataloguer to FINISH that draft.
    finding(
        (existing or {}).get("provisional") is True,
        "C3: the warning does not say the existing product is an ordered draft",
    )


# ---------------------------------------------------------------------------
# Panel round 2 (2026-09-29)
# ---------------------------------------------------------------------------

SALES = {
    "user_id": "u-sales-dhn2",
    "username": "sales.dhn2",
    "roles": ["SALES_STAFF"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}


def _receive_again(world, po, invoice_no):
    """A second receipt against the SAME order (the PO still reads receivable
    after a held receipt)."""
    fresh = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    return world.receive_everything(fresh, invoice_no=invoice_no)


def _tasks_of(world, **flt):
    return [
        (t.get("status"), t.get("assigned_to"), t.get("category"), t.get("title"))
        for t in world.db.tasks.find(flt)
    ]


def test_c1_a_second_receipt_of_the_same_box_waits_for_the_store_manager(world):
    po, grn1, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    grn2, again = _receive_again(world, po, "JOT/26-27/0701-DUP")
    assert again["grn_status"] == "PARTIALLY_ACCEPTED"

    world.finish_draft(draft_id, offer=2790)

    units = world.units(draft_id)
    finding(
        len(units) == 2,
        f"C1: finishing the draft put {len(units)} units on the shelf for a "
        "2-unit order -- the duplicate receipt was released with no human check",
    )
    assert world.grn(grn1["grn_id"])["status"] == "ACCEPTED"
    finding(
        world.grn(grn2["grn_id"])["status"] == "PARTIALLY_ACCEPTED",
        "C1: the receipt beyond the order did not stay held",
    )
    # Owner 2026-09-29: a receipt problem is the shop's store manager's task.
    mgr = [
        t
        for t in _open_tasks(world)
        if t.get("assigned_to") == MANAGER["user_id"]
        and t.get("grn_id") == grn2["grn_id"]
    ]
    finding(
        len(mgr) == 1 and grn2["grn_number"] in mgr[0]["title"],
        f"C1: the store manager was not told about {grn2['grn_number']} "
        f"({_tasks_of(world)})",
    )
    # The task opens that vendor's "Receipts still waiting", where it is voided.
    finding(
        mgr[0].get("link") == f"/purchase/receive?vendor_id={po['vendor_id']}",
        f"C1: the store manager's task does not open the receipt ({mgr[0].get('link')})",
    )
    finding(
        not [t for t in _open_tasks(world) if t.get("category") == "Catalogue"],
        "C1: the cataloguer still holds a task for an item already finished",
    )

    # The manager voids the duplicate: it put nothing on the shelf.
    voided = _run(vd.void_grn(grn2["grn_id"], MANAGER))
    assert voided["grn_status"] == "VOID"
    assert len(world.units(draft_id)) == 2
    finding(not _open_tasks(world), "C1: the task outlived the voided receipt")


def test_c1_two_orders_of_one_draft_both_go_on_the_shelf(world):
    po1, grn1, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    # A second order for the same item names the draft (the C2 answer).
    draft = world.product(draft_id)
    po2 = world.raise_po(
        [
            {
                "product_id": draft_id,
                "product_name": draft.get("name") or draft["sku"],
                "sku": draft["sku"],
                "quantity": 1,
                "unit_price": 1200,
            }
        ]
    )
    grn2, held = world.receive_everything(po2, invoice_no="JOT/26-27/0702")
    assert held["grn_status"] == "PARTIALLY_ACCEPTED"

    world.finish_draft(draft_id, offer=2790)

    finding(
        len(world.units(draft_id)) == 3,
        f"C1: {len(world.units(draft_id))} of 3 ordered units reached the shelf",
    )
    for g in (grn1, grn2):
        finding(
            world.grn(g["grn_id"])["status"] == "ACCEPTED",
            f"C1: receipt {g['grn_number']} is still holding its units",
        )


def test_c1_the_release_is_the_manager_who_accepted_the_receipt(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    world.finish_draft(draft_id, offer=2790)

    units = world.units(draft_id)
    assert len(units) == 2
    who = MANAGER["user_id"]
    finding(
        {u.get("created_by") for u in units} == {who},
        "C1: the released units are not stamped with the receiving manager "
        f"({[u.get('created_by') for u in units]})",
    )
    ids = {str(u.get("stock_id")) for u in units}
    audit = [a for a in world.db.stock_audit.find({}) if a.get("stock_id") in ids]
    finding(
        len(audit) == 2 and {a.get("by_user") for a in audit} == {who},
        f"C1: the stock audit does not name the receiving manager ({audit})",
    )
    mints = [e for e in world.db.item_events.find({}) if e.get("stock_id") in ids]
    finding(
        len(mints) == 2 and {e.get("actor_id") for e in mints} == {who},
        f"C1: the MINT ledger does not name the receiving manager ({mints})",
    )


def test_c1_a_closed_task_is_never_raised_again(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    (task,) = _open_tasks(world)
    _run(
        _tasks.complete_task(
            task["task_id"], _tasks.TaskComplete(completion_notes="Seen it"), CATALOGUER
        )
    )
    # "Add to stock" pressed again while the line is still held.
    _run(vd.accept_grn(grn["grn_id"], MANAGER))
    finding(
        not _open_tasks(world),
        f"C1: a task a person closed was raised again ({_tasks_of(world)})",
    )


def test_c1_finishing_one_of_two_held_items_never_reopens_a_closed_task(world):
    other = dict(BOSS_TYPED, model="BOSS 1701", colour="C3")
    po = world.raise_po(
        [
            {"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200},
            {"new_product": other, "quantity": 1, "unit_price": 1300},
        ]
    )
    grn, accepted = world.receive_everything(po)
    assert len(accepted["unresolved_lines"]) == 2
    (task,) = _open_tasks(world)
    _run(
        _tasks.complete_task(
            task["task_id"], _tasks.TaskComplete(completion_notes="On it"), CATALOGUER
        )
    )
    world.finish_draft(po["items"][0]["product_id"], offer=2790)
    finding(
        not _open_tasks(world),
        f"C1: finishing item 1 re-raised a closed task ({_tasks_of(world)})",
    )


def test_c1_po_timeline_never_says_on_shelf_for_held_units(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

    def shelf_events():
        tl = _run(vd.get_po_timeline(po["po_id"], MANAGER))
        return [e for e in tl["events"] if e.get("kind") == "on_shelf"]

    finding(
        shelf_events() == [],
        f"C1: the PO timeline says 'On shelf' for 0 units ({shelf_events()})",
    )
    world.finish_draft(draft_id, offer=2790)
    ev = shelf_events()
    finding(
        len(ev) == 1 and ev[0]["detail"].startswith("2 units"),
        f"C1: the PO timeline does not show the released units ({ev})",
    )


def test_counter_staff_never_see_the_cataloguers_task(world):
    world.db.seed(
        "users",
        [
            {
                **{k: v for k, v in SALES.items() if k != "active_store_id"},
                "is_active": True,
            }
        ],
    )
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

    def listed(user, store_id):
        out = _run(
            _tasks.list_tasks(
                status="OPEN",
                priority=None,
                assigned_to=None,
                task_type=None,
                store_id=store_id,
                skip=0,
                limit=50,
                current_user=user,
            )
        )
        return [t.get("assigned_to") for t in out["tasks"]]

    # The Hub's "Priority tasks": the whole shop's open tasks.
    finding(
        listed(SALES, STORE) == [],
        "Owner 09-03: a salesperson sees a task assigned to someone else",
    )
    finding(
        listed(CATALOGUER, None) == [CATALOGUER["user_id"]],
        "C1: the catalogue manager cannot list the task assigned to them",
    )
    finding(
        listed(MANAGER, STORE) == [CATALOGUER["user_id"]],
        "Owner 09-03: the store manager no longer sees the shop's tasks",
    )


# ---------------------------------------------------------------------------
# Eye size is part of a frame's identity (owner 09-28: each eye size is its
# own item); a category with no size records none.
# ---------------------------------------------------------------------------


def test_c2_another_eye_size_of_a_catalogued_frame_is_its_own_item(world):
    fifty_two = world.catalogue_frame(
        "Boss", "BOSS 1700", "C2", "52", mrp=2990, offer=2790, cost=1200
    )
    fifty_four = dict(BOSS_TYPED, size="54")
    po = world.raise_po(
        [{"new_product": fifty_four, "quantity": 2, "unit_price": 1200}]
    )
    draft = world.product(po["items"][0]["product_id"])
    finding(
        draft["product_id"] != fifty_two["product_id"]
        and draft["attributes"].get("lens_size") == "54",
        "C2: a 54 typed on the PO was ordered as the catalogued 52",
    )
    # And the catalogue form takes a 54 beside the 52.
    world.catalogue_frame("Boss", "BOSS 1701", "C2", "52", mrp=2990, offer=2790, cost=1200)
    world.catalogue_frame("Boss", "BOSS 1701", "C2", "54", mrp=2990, offer=2790, cost=1200)
    assert len(world.products_named("Boss", "BOSS 1701")) == 2

    # The SAME eye size is still the catalogued product.
    refused = _refused_po(
        world, [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    detail = (refused.detail if refused else None) or {}
    finding(
        [m["existing"]["product_id"] for m in detail.get("matches", [])]
        == [fifty_two["product_id"]],
        "C2: the typed 52 is not answered with the catalogued 52",
    )


def test_c2_a_frame_typed_without_its_eye_size_is_asked_for_it(world):
    world.catalogue_frame("Boss", "BOSS 1700", "C2", "52", mrp=2990, offer=2790, cost=1200)
    sizeless = {k: v for k, v in BOSS_TYPED.items() if k != "size"}
    refused = _refused_po(
        world, [{"new_product": sizeless, "quantity": 1, "unit_price": 1200}]
    )
    detail = (refused.detail if refused else None) or {}
    finding(
        refused is not None
        and refused.status_code == 422
        and detail.get("code") == "EYE_SIZE_NEEDED"
        and "52" in detail.get("message", ""),
        f"C2: a sizeless Boss 1700 C2 was not sent back for its eye size ({refused})",
    )
    assert len(world.products_named("Boss", "BOSS 1700")) == 1
    assert world.db.purchase_orders.count_documents({}) == 0


@pytest.mark.parametrize(
    "second, code",
    [
        # The same frame again without its eye size: the 52 is on this order.
        ({"new_product": dict(BOSS_TYPED, size=None)}, "EYE_SIZE_NEEDED"),
        # A line the product door refuses outright.
        (
            {"new_product": dict(BOSS_TYPED, model="BOSS 1701", category="NOPE")},
            "NEW_PRODUCT_INVALID",
        ),
        # A picked product gone from the catalogue since (or any API client).
        ({"product_id": "P-GONE", "product_name": "gone"}, "UNKNOWN_PRODUCT"),
    ],
)
def test_c2_a_refused_order_leaves_no_draft_behind(world, second, code):
    refused = _refused_po(
        world,
        [
            {"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200},
            {**second, "quantity": 1, "unit_price": 1200},
        ],
    )
    assert refused is not None and refused.status_code == 422, refused
    assert refused.detail.get("code") == code, refused.detail
    assert world.db.purchase_orders.count_documents({}) == 0
    finding(
        world.products_named("Boss", "BOSS 1700") == [] and _needs_review_list(world) == [],
        "C2: a refused order left its first line's draft behind, 'ordered' in "
        f"Needs review ({_needs_review_list(world)})",
    )


def test_c2_a_size_typed_for_a_watch_is_not_a_second_watch(world):
    body = _products.ProductCreate(
        category="WT",
        brand="Titan",
        model="NR1805",
        attributes={"brand_name": "Titan", "model_no": "NR1805", "colour_code": "SL01"},
        mrp=4995,
        offer_price=4495,
        cost_price=2600,
    )
    watch = _run(_products.create_product(body, CATALOGUER, as_draft=False))
    typed = {
        "category": "WT",
        "brand": "Titan",
        "model": "NR1805",
        "colour": "SL01",
        "size": "42",
        "mrp": 4995,
    }
    refused = _refused_po(
        world, [{"new_product": typed, "quantity": 1, "unit_price": 2600}]
    )
    detail = (refused.detail if refused else None) or {}
    finding(
        [m["existing"]["product_id"] for m in detail.get("matches", [])]
        == [watch["product_id"]],
        "C2: a size typed for a watch made a second, hidden watch",
    )
    assert len(world.products_named("Titan", "NR1805")) == 1


# ---------------------------------------------------------------------------
# An ordered draft is finished in the product editor, never the import review
# ---------------------------------------------------------------------------


def _twin_of(world, draft_id):
    return world.db.catalog_products.find_one({"spine_product_id": draft_id})


def test_c1_the_import_review_never_saves_or_approves_an_ordered_draft(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    twin = _twin_of(world, draft_id)
    assert twin is not None

    calls = (
        lambda: _catalog.update_catalog_product(
            twin["id"], _catalog.ProductUpdateInput(offer_price=2790), CATALOGUER
        ),
        lambda: _catalog.promote_catalog_product(
            twin["id"], dry_run=True, current_user=CATALOGUER
        ),
    )
    for call in calls:
        try:
            _run(call())
            refused = None
        except HTTPException as exc:
            refused = exc
        finding(
            refused is not None
            and refused.status_code == 409
            and "product editor" in str(refused.detail),
            f"C1: the import review wrote around the product door ({refused})",
        )
    assert not world.product(draft_id).get("offer_price")


def _listed(world, **kw):
    args = dict(
        category=None,
        brand=None,
        search=None,
        is_active="all",
        needs_review=None,
        source=None,
        ordered_draft=None,
        photo=None,
        limit=250,
        page=1,
        current_user=CATALOGUER,
    )
    args.update(kw)
    listed = _run(_catalog.list_catalog_products(**args))
    return [p.get("sku") for p in listed["products"]]


def test_c1_the_import_review_queue_leaves_ordered_drafts_out(world):
    world.db.seed(
        "catalog_products",
        [
            {
                "id": "bvi-old",
                "sku": "BVI-OLD",
                "category": "FRAME",
                "needs_review": True,
                "is_active": False,
                "created_at": "2001-01-01T00:00:00",
            }
        ],
    )
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    sku = world.product(draft_id)["sku"]
    assert _listed(world, needs_review=True)[0] == sku
    finding(
        _listed(world, needs_review=True, ordered_draft=False) == ["BVI-OLD"],
        "C1: the import-review 'Next' fallback still lands on the ordered draft",
    )


def test_c1_ordered_first_is_only_the_needs_review_order(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    boss_sku = world.product(draft_id)["sku"]
    world.finish_draft(draft_id, offer=2790)
    newer = world.catalogue_frame(
        "Ray-Ban", "RB4350", "710", "58", mrp=9990, offer=8990, cost=5000
    )
    # Dated as on production: the Boss was catalogued first.
    for sku, day in ((boss_sku, "2026-09-01"), (newer["sku"], "2026-09-02")):
        world.db.catalog_products.update_one(
            {"sku": sku}, {"$set": {"created_at": f"{day}T10:00:00"}}
        )
    everything = _listed(world)
    finding(
        everything.index(newer["sku"]) < everything.index(boss_sku),
        f"C1: a once-ordered product permanently leads the product list ({everything})",
    )


# ---------------------------------------------------------------------------
# Panel round 3 (2026-09-30)
# ---------------------------------------------------------------------------

ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["ADMIN"],
    "store_ids": [],
    "active_store_id": STORE,
}
ACCOUNTANT = {
    "user_id": "u-acc-dhn2",
    "username": "acc.dhn2",
    "roles": ["ACCOUNTANT"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
BOSS_1701 = dict(BOSS_TYPED, model="BOSS 1701", colour="C3")


def _seed_user(world, user):
    world.db.seed(
        "users",
        [{**{k: v for k, v in user.items() if k != "active_store_id"}, "is_active": True}],
    )
    return user


def _receive(world, po, qtys, invoice_no):
    """Receive Goods with the counts the vendor really sent (one per PO line),
    then "Add to stock"."""
    items = [
        vd.GRNItemCreate(
            product_id=it["product_id"],
            received_qty=q,
            accepted_qty=q,
            rejected_qty=0,
            tallied=True,
        )
        for it, q in zip(po["items"], qtys)
    ]
    created = _run(
        vd.create_grn(
            vd.GRNCreate(
                po_id=po["po_id"],
                vendor_invoice_no=invoice_no,
                vendor_invoice_date="2026-09-28",
                items=items,
                attachment_file_id="F-RECEIPT-PHOTO",
                attachment_filename="bill.jpg",
                attachment_mime="image/jpeg",
            ),
            MANAGER,
        )
    )
    _run(vd.accept_grn(created["grn_id"], MANAGER))
    return created


def _mgr_tasks(world, grn_id):
    return [
        t
        for t in _open_tasks(world)
        if t.get("assigned_to") == MANAGER["user_id"] and t.get("grn_id") == grn_id
    ]


def _any_status_units(world, pid):
    return list(world.db.stock_units.find({"product_id": pid}))


def _two_drafts_po(world):
    po = world.raise_po(
        [
            {"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200},
            {"new_product": dict(BOSS_1701), "quantity": 1, "unit_price": 1300},
        ]
    )
    return po, po["items"][0]["product_id"], po["items"][1]["product_id"]


def test_c1_a_second_item_over_the_order_on_a_receipt_is_told_too(world):
    # P1: the store manager's task is per item, so closing the first item's
    # task never silences the second.
    po, d_id, e_id = _two_drafts_po(world)
    grn = _receive(world, po, [2, 2], "JOT/26-27/0801")

    world.finish_draft(d_id, offer=2790)
    (t1,) = _mgr_tasks(world, grn["grn_id"])
    assert "BOSS 1700" in t1["description"]
    # The manager does what T1 says: "Add to stock" (the vendor really sent
    # 2). Nothing on the receipt is beyond its order now, so T1 closes.
    _run(vd.accept_grn(grn["grn_id"], MANAGER))
    assert len(world.units(d_id)) == 2
    assert world.db.tasks.find_one({"task_id": t1["task_id"]})["status"] == "COMPLETED"

    world.finish_draft(e_id, offer=2890)
    held = world.grn(grn["grn_id"])
    assert held["status"] == "PARTIALLY_ACCEPTED"
    assert [ln["product_id"] for ln in held["unresolved_lines"]] == [e_id]
    finding(
        any("BOSS 1701" in t["description"] for t in _mgr_tasks(world, grn["grn_id"])),
        f"P1: the second item held beyond the order told nobody ({_tasks_of(world)})",
    )


def test_c1_a_line_within_its_order_is_never_held_behind_one_over_it(world):
    # P3: D arrived exactly as ordered, E over-shipped. Finishing D shelves D.
    po, d_id, e_id = _two_drafts_po(world)
    grn = _receive(world, po, [1, 2], "JOT/26-27/0802")

    world.finish_draft(d_id, offer=2790)
    finding(
        len(world.units(d_id)) == 1,
        f"P3: D is finished and within its order, yet {len(world.units(d_id))} "
        "of 1 unit reached the shelf",
    )
    # E is still a draft: the cataloguer's job, not yet the store manager's.
    assert not _mgr_tasks(world, grn["grn_id"])

    world.finish_draft(e_id, offer=2890)
    assert world.units(e_id) == []
    (task,) = _mgr_tasks(world, grn["grn_id"])
    # D's unit is on the shelf, so the receipt cannot be voided: the task must
    # not send the manager to a void the server refuses.
    finding(
        "BOSS 1701" in task["description"] and "void" not in task["description"].lower(),
        "P3: the task sends the manager to void a receipt with stock on the shelf "
        f"({task['description']!r})",
    )
    assert len(world.units(d_id)) == 1


def test_c1_a_receipt_of_the_same_order_going_into_stock_holds_the_other(world):
    # P4: request A holds GRN1's accept claim and has not minted yet when
    # request B's catalogue save reaches GRN2 (the same box again).
    po, grn1, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    grn2, _ = _receive_again(world, po, "JOT/26-27/0701-DUP")
    world.db.grns.update_one(
        {"grn_id": grn1["grn_id"]},
        {
            "$set": {
                "accept_lock_at": vd.datetime.now().isoformat(),
                "accept_lock_token": "GACC-request-a",
                "accept_lock_by": MANAGER["user_id"],
            }
        },
    )

    world.finish_draft(draft_id, offer=2790)

    finding(
        world.units(draft_id) == [],
        "P4: GRN2 went on the shelf while GRN1 of the same 2-unit order was being "
        f"added ({len(world.units(draft_id))} units, and request A still mints 2)",
    )
    held = world.grn(grn2["grn_id"])
    assert held["status"] == "PARTIALLY_ACCEPTED"
    assert held["unresolved_lines"][0]["reason"] == "over_order"
    assert _mgr_tasks(world, grn2["grn_id"])


def test_c1_units_sold_since_still_fill_the_order(world):
    # P5: the order cap counts units in ANY status -- once the released units
    # are sold, the next catalogue save must not release the second receipt.
    po, grn1, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    grn2, _ = _receive_again(world, po, "JOT/26-27/0701-DUP")
    world.finish_draft(draft_id, offer=2790)
    assert len(world.units(draft_id)) == 2
    world.db.stock_units.update_many(
        {"product_id": draft_id}, {"$set": {"status": "SOLD"}}
    )

    world.finish_draft(draft_id, offer=2690)  # another catalogue edit

    finding(
        len(_any_status_units(world, draft_id)) == 2,
        f"P5: {len(_any_status_units(world, draft_id))} units exist for a 2-unit "
        "order -- the second receipt of the box was released once the first "
        "units were sold",
    )
    assert world.grn(grn2["grn_id"])["status"] == "PARTIALLY_ACCEPTED"


def test_c1_a_held_draft_is_never_discarded_behind_its_receipt(world):
    # P2: an admin deleting the draft a receipt is holding units for.
    _seed_user(world, ADMIN)
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    twin = _twin_of(world, draft_id)
    try:
        _run(_catalog.delete_catalog_product(twin["id"], ADMIN))
        refused = None
    except HTTPException as exc:
        refused = exc
    finding(
        refused is not None
        and refused.status_code == 409
        and grn["grn_number"] in str(refused.detail),
        f"P2: the draft was deleted while {grn['grn_number']} holds its units ({refused})",
    )
    assert world.product(draft_id)["provisional"] is True

    # The units go back: the manager voids the receipt and cancels the order
    # (round 5: a draft an open order names is not discarded either), then the
    # admin deletes.
    _run(vd.void_grn(grn["grn_id"], MANAGER))
    _run(vd.cancel_po(po["po_id"], "box went back", MANAGER))
    _run(_catalog.delete_catalog_product(twin["id"], ADMIN))
    sku = world.product(draft_id)["sku"]
    finding(
        sku not in _needs_review_list(world),
        "P2: the deleted draft is still in Needs review",
    )
    assert not _open_tasks(world)

    # Following a stale lead to "finish" it never brings it back.
    world.finish_draft(draft_id, offer=2790)
    finding(
        world.product(draft_id).get("is_active") is False,
        "P2: finishing a deleted draft switched it back on",
    )
    assert _any_status_units(world, draft_id) == []


def test_c1_the_catalogue_task_escalates_to_someone_who_can_do_it(world):
    # An SLA breach climbs the ladder. Above a catalogue manager is the admin:
    # the shop's store manager can neither open Needs review nor save a product.
    _seed_user(world, ADMIN)
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    (task,) = _open_tasks(world)
    assert task["assigned_to"] == CATALOGUER["user_id"]
    _tasks._escalate_and_reassign(
        _deps.get_task_repository(),
        task,
        reason="ack SLA breached",
        by="TASKMASTER",
        now=vd.datetime.now(),
    )
    after = world.db.tasks.find_one({"task_id": task["task_id"]})
    finding(
        after["assigned_to"] == ADMIN["user_id"],
        f"The catalogue task escalated to {after['assigned_to']}, who cannot do it",
    )


def _escalate_after(world, monkeypatch, engine, hours):
    """Run one SLA escalation pass `hours` after the tasks were raised, through
    either real engine: the TASKMASTER tick or POST /tasks/auto-escalate-overdue."""
    real = vd.datetime

    class _Later(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + vd.timedelta(hours=hours)

    if engine == "taskmaster":
        from agents.implementations import taskmaster as _tm

        monkeypatch.setattr(_tm, "datetime", _Later)
        _run(_tm.TaskmasterAgent(db=world.db)._escalate_overdue_tasks())
    else:
        monkeypatch.setattr(_tasks, "datetime", _Later)
        _run(_tasks.auto_escalate_overdue_tasks(store_id=None, current_user=ADMIN))


ENGINES = pytest.mark.parametrize("engine", ["taskmaster", "endpoint"])


@ENGINES
def test_c1_the_catalogue_task_escalates_after_a_day_not_before(world, monkeypatch, engine):
    # Owner 2026-09-30: the catalogue manager's task escalates after a DAY.
    _seed_user(world, ADMIN)
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _escalate_after(world, monkeypatch, engine, hours=23)
    finding(
        [t.get("assigned_to") for t in _open_tasks(world)] == [CATALOGUER["user_id"]]
        and _task_list(CATALOGUER, None),
        "C1: the catalogue task escalated within a day "
        f"({[(t.get('assigned_to'), t.get('escalation_reason')) for t in _open_tasks(world)]})",
    )
    _escalate_after(world, monkeypatch, engine, hours=25)
    finding(
        [t.get("assigned_to") for t in _open_tasks(world)] == [ADMIN["user_id"]],
        "C1: the catalogue task did not escalate after a day "
        f"({[t.get('assigned_to') for t in _open_tasks(world)]})",
    )


@ENGINES
def test_c1_two_catalogue_managers_escalate_as_one_task(world, monkeypatch, engine):
    # One task each, by name -- but the receipt reaches the next rung ONCE.
    _seed_user(world, ADMIN)
    _seed_user(world, dict(CATALOGUER, user_id="u-cat-2", username="catalog.two"))
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    assert len(_open_tasks(world)) == 2
    _escalate_after(world, monkeypatch, engine, hours=25)
    held = [t for t in _open_tasks(world) if t.get("assigned_to") == ADMIN["user_id"]]
    finding(
        len(_open_tasks(world)) == 1 and len(held) == 1,
        "C1: one held receipt escalated as "
        f"{[(t.get('assigned_to'), t.get('title')) for t in _open_tasks(world)]}",
    )


def test_c1_asking_again_after_the_ask_was_closed_asks_again(world):
    # The accountant asks; the catalogue manager closes the task without
    # finishing the item; the next refused booking asks again -- and must reach
    # someone (an ask is not a receipt's once-ever task).
    from api.routers import purchase_invoices as _pi

    _seed_user(world, ACCOUNTANT)
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

    def ask():
        _run(
            _pi.request_cataloguing(
                _pi.CataloguingRequest(product_ids=[draft_id]), ACCOUNTANT
            )
        )
        return [t for t in _open_tasks(world) if "vendor bill" in t["title"]]

    (first,) = ask()
    _run(
        _tasks.complete_task(
            first["task_id"], _tasks.TaskComplete(completion_notes="seen it"), CATALOGUER
        )
    )
    assert world.product(draft_id).get("is_active") is False  # still unfinished
    again = ask()
    finding(
        [t.get("assigned_to") for t in again] == [CATALOGUER["user_id"]],
        f"Asking again after the ask was closed reached nobody ({again})",
    )


def test_an_accountant_sees_and_closes_the_task_addressed_to_accountants(world):
    # Express receive's "Book purchase invoice" is addressed to the ACCOUNTANT
    # title (grn_express). Below manager the list is your own -- and a task
    # addressed to your title is yours.
    _seed_user(world, ACCOUNTANT)
    _seed_user(world, SALES)
    from api.services.task_triggers import create_system_task

    task = create_system_task(
        _deps.get_task_repository(),
        title="Book purchase invoice for GRN RCPT/1 (Jharkhand Optical Traders)",
        description="Express receive completed.",
        priority="P2",
        category="Purchase",
        store_id=STORE,
        dedupe_ref="express_invoice:g-1",
        assigned_to="ACCOUNTANT",
    )

    def listed(user, store_id):
        out = _run(
            _tasks.list_tasks(
                status="OPEN",
                priority=None,
                assigned_to=None,
                task_type=None,
                store_id=store_id,
                skip=0,
                limit=50,
                current_user=user,
            )
        )
        return [t.get("task_id") for t in out["tasks"]]

    for store_id in (STORE, None):
        finding(
            listed(ACCOUNTANT, store_id) == [task["task_id"]],
            f"No accountant can see the express-receive task (store_id={store_id})",
        )
    assert listed(SALES, STORE) == []
    _run(
        _tasks.complete_task(
            task["task_id"], _tasks.TaskComplete(completion_notes="Booked"), ACCOUNTANT
        )
    )
    assert world.db.tasks.find_one({"task_id": task["task_id"]})["status"] == "COMPLETED"


def test_c1_no_store_manager_fails_loud_to_the_admins(world, caplog):
    world.db.users.update_one(
        {"user_id": MANAGER["user_id"]}, {"$set": {"is_active": False}}
    )
    _seed_user(world, ADMIN)
    po, grn1, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    grn2, _ = _receive_again(world, po, "JOT/26-27/0701-DUP")
    with caplog.at_level("ERROR"):
        world.finish_draft(draft_id, offer=2790)
    admin = [
        t
        for t in _open_tasks(world)
        if t.get("assigned_to") == ADMIN["user_id"] and t.get("grn_id") == grn2["grn_id"]
    ]
    finding(
        len(admin) == 1 and "No store manager" in admin[0]["title"],
        f"A shop with no store manager went to the admins silently ({admin})",
    )
    finding(
        any(
            r.levelname == "ERROR" and "NO store manager" in r.getMessage()
            for r in caplog.records
        ),
        "No ERROR is logged when no store manager covers the shop",
    )


# -- A unit's ORIGIN is recorded once; a transfer never rewrites it -------------
# (owner 2026-10-01). Every "already received from this receipt / line" count
# reads the origin, so a unit moved to another shop is still received.

OTHER = "BV-DHN-01"


def _transfer_out(world, monkeypatch, pid, n):
    """Send n units of pid to another shop, through the transfer module's own
    stock moves (ship, then receive): the receive re-homes each unit and
    rewrites its source_type / source_id to the transfer."""
    from api.routers import transfers as _tr

    monkeypatch.setattr(_tr, "_writeback_units_left", lambda *a, **k: None)  # no Shopify
    t = {
        "id": f"T-{pid}",
        "transfer_number": "TRF/1",
        "from_location_id": STORE,
        "to_location_id": OTHER,
        "items": [{"product_id": pid, "quantity_requested": n}],
    }
    _tr._apply_ship_stock_move(t)
    t["items"][0]["quantity_received"] = n
    _tr._apply_receive_stock_move(t)
    moved = list(world.db.stock_units.find({"product_id": pid, "store_id": OTHER}))
    assert len(moved) == n and {u["source_type"] for u in moved} == {"TRANSFER"}, moved


def _carrera_and_boss(world, qtys, invoice_no="JOT/26-27/0901"):
    """One order: a catalogued Carrera x1 and the typed Boss draft x2."""
    car = world.catalogue_frame(
        "Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3155.76
    )
    po = world.raise_po(
        [
            {
                "product_id": car["product_id"],
                "product_name": car.get("name") or car["sku"],
                "sku": car["sku"],
                "quantity": 1,
                "unit_price": 3200,
            },
            {"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200},
        ]
    )
    grn = _receive(world, po, qtys, invoice_no)
    assert len(world.units(car["product_id"])) == 1
    return po, grn, car["product_id"], po["items"][1]["product_id"]


def test_c1_units_transferred_since_still_fill_the_order(world, monkeypatch):
    po, grn1, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    grn2, _ = _receive_again(world, po, "JOT/26-27/0701-DUP")
    world.finish_draft(draft_id, offer=2790)
    assert len(world.units(draft_id)) == 2
    _transfer_out(world, monkeypatch, draft_id, 2)

    world.finish_draft(draft_id, offer=2690)  # any later catalogue edit

    finding(
        len(_any_status_units(world, draft_id)) == 2,
        f"Origin: {len(_any_status_units(world, draft_id))} units exist for a 2-unit "
        "order -- the duplicate receipt went on the shelf once the first units "
        "were transferred",
    )
    assert world.grn(grn2["grn_id"])["status"] == "PARTIALLY_ACCEPTED"


def test_c1_a_transferred_line_is_never_received_twice(world, monkeypatch):
    po, grn, car_id, boss_id = _carrera_and_boss(world, [1, 2])
    _transfer_out(world, monkeypatch, car_id, 1)

    # The manager presses "Add to stock" again (no order cap: a person) ...
    _run(vd.accept_grn(grn["grn_id"], MANAGER))
    finding(
        len(_any_status_units(world, car_id)) == 1,
        f"Origin: {len(_any_status_units(world, car_id))} Carrera units for 1 "
        "received -- 'Add to stock' re-received the transferred Carrera line",
    )
    # ... and the cataloguer finishes the Boss, which releases the receipt.
    world.finish_draft(boss_id, offer=2790)

    assert len(world.units(boss_id)) == 2
    finding(
        len(_any_status_units(world, car_id)) == 1,
        f"Origin: {len(_any_status_units(world, car_id))} Carrera units for 1 "
        "received -- finishing the Boss re-received the transferred Carrera line",
    )
    finding(
        world.grn(grn["grn_id"])["status"] == "ACCEPTED" and not _mgr_tasks(world, grn["grn_id"]),
        "Origin: the transferred Carrera line reads as not yet received, so the "
        f"receipt is held for the store manager ({world.grn(grn['grn_id'])['unresolved_lines']})",
    )


def test_c1_a_receipt_whose_units_moved_shop_is_never_voided(world, monkeypatch):
    # The Boss over-shipped (3 for 2), so finishing it leaves the receipt held
    # for the store manager -- while its Carrera unit lives at another shop.
    po, grn, car_id, boss_id = _carrera_and_boss(world, [1, 3])
    _transfer_out(world, monkeypatch, car_id, 1)
    world.finish_draft(boss_id, offer=2790)

    (task,) = _mgr_tasks(world, grn["grn_id"])
    finding(
        "void" not in task["description"].lower(),
        "Origin: the task sends the manager to void a receipt whose unit is stock "
        f"at another shop ({task['description']!r})",
    )
    try:
        _run(vd.void_grn(grn["grn_id"], MANAGER))
        refused = None
    except HTTPException as exc:
        refused = exc
    finding(
        refused is not None and refused.status_code == 409,
        f"Origin: a receipt with a live unit at another shop was voided ({refused})",
    )
    assert world.grn(grn["grn_id"])["status"] == "PARTIALLY_ACCEPTED"


def test_c1_a_unit_received_before_this_deploy_and_moved_since_is_still_received(
    world, monkeypatch
):
    """A receipt accepted on the old code (its units carry no grn_id) whose
    Carrera unit is transferred AFTER the deploy: the transfer rewrites
    source_id, so only the grn_number stamped at mint still names the receipt."""
    po, grn, car_id, boss_id = _carrera_and_boss(world, [1, 2])
    world.db.stock_units.update_one({"product_id": car_id}, {"$unset": {"grn_id": ""}})
    _transfer_out(world, monkeypatch, car_id, 1)

    _run(vd.accept_grn(grn["grn_id"], MANAGER))  # "Add to stock" again
    finding(
        len(_any_status_units(world, car_id)) == 1,
        f"Origin: {len(_any_status_units(world, car_id))} Carrera units for 1 "
        "received -- an older unit moved shop since was received a second time",
    )
    try:
        _run(vd.void_grn(grn["grn_id"], MANAGER))
        refused = None
    except HTTPException as exc:
        refused = exc
    finding(
        refused is not None and refused.status_code == 409,
        f"Origin: a receipt whose older unit lives at another shop was voided ({refused})",
    )
    assert world.grn(grn["grn_id"])["status"] == "PARTIALLY_ACCEPTED"


# -- Reading glasses record an eye size, as their Add-Product form does ---------

RG_TYPED = {"category": "RG", "brand": "Titan", "model": "RG77", "colour": "BLK", "mrp": 1490}


def _catalogue_rg(world, lens_size):
    attrs = {"brand_name": "Titan", "model_no": "RG77", "colour_code": "BLK"}
    if lens_size:
        attrs["lens_size"] = lens_size
    body = _products.ProductCreate(
        category="RG",
        brand="Titan",
        model="RG77",
        attributes=attrs,
        mrp=1490,
        offer_price=1290,
        cost_price=600,
    )
    return _run(_products.create_product(body, CATALOGUER, as_draft=False))


def test_c2_reading_glasses_typed_with_their_eye_size_use_the_catalogued_one(world):
    rg = _catalogue_rg(world, "50")
    refused = _refused_po(
        world, [{"new_product": dict(RG_TYPED, size="50"), "quantity": 1, "unit_price": 600}]
    )
    detail = (refused.detail if refused else None) or {}
    finding(
        [m["existing"]["product_id"] for m in detail.get("matches", [])]
        == [rg["product_id"]],
        f"C2: an RG typed with its eye size made a twin ({refused})",
    )
    assert len(world.products_named("Titan", "RG77")) == 1


def test_c2_reading_glasses_typed_without_an_eye_size_are_asked_for_it(world):
    _catalogue_rg(world, "50")
    refused = _refused_po(
        world, [{"new_product": dict(RG_TYPED), "quantity": 1, "unit_price": 600}]
    )
    finding(
        refused is not None and (refused.detail or {}).get("code") == "EYE_SIZE_NEEDED",
        f"C2: a sizeless RG against a catalogued RG 50 was not asked its size ({refused})",
    )
    assert len(world.products_named("Titan", "RG77")) == 1


def test_c3_cataloguing_reading_glasses_already_ordered_warns(world):
    po = world.raise_po(
        [{"new_product": dict(RG_TYPED, size="50"), "quantity": 1, "unit_price": 600}]
    )
    try:
        _catalogue_rg(world, "50")
        refused = None
    except HTTPException as exc:
        refused = exc
    finding(
        refused is not None
        and refused.status_code == 409
        and refused.detail["existing"]["product_id"] == po["items"][0]["product_id"],
        f"C3: an RG catalogued after it was ordered made a twin ({refused})",
    )


def test_c3_a_frame_catalogued_without_its_eye_size_is_asked_for_it(world):
    # One "already exists" rule for both doors: Add product asks for the eye
    # size exactly as the PO line does.
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    try:
        world.catalogue_frame("Boss", "BOSS 1700", "C2", "", mrp=2990, offer=2790, cost=1200)
        refused = None
    except HTTPException as exc:
        refused = exc
    finding(
        refused is not None and refused.status_code == 422 and "52" in str(refused.detail),
        f"C3: a sizeless Boss 1700 C2 was catalogued beside the ordered 52 ({refused})",
    )
    assert len(world.products_named("Boss", "BOSS 1700")) == 1


def test_the_identity_migration_keys_rows_as_the_door_does(world):
    from scripts.migrate_identity_key_tighten import _identity_of

    frame = world.catalogue_frame(
        "Boss", "BOSS 1700", "C2", "52", mrp=2990, offer=2790, cost=1200
    )
    stored = world.product(frame["product_id"])
    assert _identity_of(stored) == stored["identity_key"] == "boss|boss1700|c2|52"
    # A top-level size that differs from the eye size never wins over it.
    assert _identity_of({**stored, "size": "M"}) == "boss|boss1700|c2|52"


def test_c2_a_frame_still_carrying_an_old_size_is_found_by_its_eye_size(world):
    # `size` left the frame registry, but an API or clone caller can still
    # store a legacy "52-18-140" beside the eye size. The eye size (the
    # registry's lens_size) is the identity, so the frame typed with eye size
    # 52 on a PO is that frame -- never a hidden second one.
    from scripts.migrate_identity_key_tighten import _identity_of

    body = _products.ProductCreate(
        category="FR",
        brand="Boss",
        model="BOSS 1700",
        attributes={
            "brand_name": "Boss",
            "model_no": "BOSS 1700",
            "colour_code": "C2",
            "lens_size": "52",
            "size": "52-18-140",
        },
        mrp=2990,
        offer_price=2790,
        cost_price=1200,
    )
    made = _run(_products.create_product(body, CATALOGUER, as_draft=False))
    stored = world.product(made["product_id"])
    finding(
        stored["identity_key"] == "boss|boss1700|c2|52",
        f"C2: the frame is keyed by its old size ({stored['identity_key']})",
    )
    assert _identity_of(stored) == "boss|boss1700|c2|52"
    # A row stored before this fix carries the old size top-level too.
    world.db.products.update_one(
        {"product_id": made["product_id"]}, {"$set": {"size": "52-18-140"}}
    )
    # As a size variant, its Size option is the eye size too.
    from api.services.product_master import _variant_row_for

    row = _variant_row_for(world.product(made["product_id"]), {"product_id": "P-PARENT"})
    assert row["option_size"] == "52", row

    refused = _refused_po(
        world, [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    finding(
        refused is not None
        and refused.status_code == 409
        and len(world.products_named("Boss", "BOSS 1700")) == 1,
        "C2: the Boss 1700 C2 typed with eye size 52 made a second, hidden one "
        f"({[p.get('identity_key') for p in world.products_named('Boss', 'BOSS 1700')]})",
    )
    finding(
        "size 52 (SKU" in refused.detail["message"],
        f"C2: the answer names the old size, not the eye size ({refused.detail['message']!r})",
    )


# ---------------------------------------------------------------------------
# Round 5 -- a discarded draft: never behind an open order or a receipt, and
# ordering the item again gets the draft back
# ---------------------------------------------------------------------------


def _delete_refusal(world, draft_id):
    try:
        _run(_catalog.delete_catalog_product(_twin_of(world, draft_id)["id"], ADMIN))
    except HTTPException as exc:
        return exc
    return None


def test_r5_a_draft_on_an_open_order_is_never_discarded(world):
    # Review round 4, problem 1: the sent order is not received yet. Deleting
    # the draft then receiving the box held it behind a deleted product, with
    # a task pointing at a Needs-review queue it had left, and finishing it
    # minted AVAILABLE units of a product that stayed deleted.
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]

    refused = _delete_refusal(world, draft_id)
    finding(
        refused is not None
        and refused.status_code == 409
        and po["po_number"] in str(refused.detail),
        f"R5: the draft was deleted while {po['po_number']} is still open ({refused})",
    )
    assert world.product(draft_id)["provisional"] is True
    assert world.product(draft_id).get("discarded_draft") is not True
    assert _twin_of(world, draft_id)["needs_review"] is True

    # The box still arrives: it is held behind a draft that is in Needs review.
    grn, accepted = world.receive_everything(po)
    assert accepted["grn_status"] == "PARTIALLY_ACCEPTED"
    assert world.product(draft_id)["sku"] in _needs_review_list(world)

    # Once the order is cancelled (nothing received yet), the draft can go.
    po2 = world.raise_po(
        [{"new_product": dict(CARRERA_TYPED), "quantity": 1, "unit_price": 3200}]
    )
    carrera = po2["items"][0]["product_id"]
    assert _delete_refusal(world, carrera) is not None
    _run(vd.cancel_po(po2["po_id"], "ordered by mistake", MANAGER))
    assert _delete_refusal(world, carrera) is None
    assert world.product(carrera).get("discarded_draft") is True
    assert world.product(carrera)["provisional"] is False


def test_r5_a_draft_a_receipt_names_is_never_discarded(world):
    # A receipt with no order at all (a delivery challan) naming the draft:
    # the order is cancelled, but the box is here.
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]
    _run(vd.cancel_po(po["po_id"], "came on a challan instead", MANAGER))
    created = _run(
        vd.create_grn(
            vd.GRNCreate(
                grn_subtype="DELIVERY_CHALLAN",
                vendor_id=VENDOR,
                dc_number="DC-0901",
                dc_date="2026-09-28",
                items=[
                    vd.GRNItemCreate(
                        product_id=draft_id,
                        received_qty=2,
                        accepted_qty=2,
                        rejected_qty=0,
                        tallied=True,
                    )
                ],
                attachment_file_id="F-RECEIPT-PHOTO",
                attachment_filename="dc.jpg",
                attachment_mime="image/jpeg",
            ),
            MANAGER,
        )
    )
    refused = _delete_refusal(world, draft_id)
    finding(
        refused is not None
        and refused.status_code == 409
        and created["grn_number"] in str(refused.detail),
        f"R5: the draft was deleted while receipt {created['grn_number']} names it",
    )
    _run(vd.void_grn(created["grn_id"], MANAGER))
    assert _delete_refusal(world, draft_id) is None


def test_r5_voiding_the_only_receipt_reopens_the_order(world):
    # The held accept moved the order to PARTIALLY_RECEIVED with nothing on
    # the shelf; cancel refuses a part-received order. Voided, nothing was
    # received: the order is SENT again and can be cancelled.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    assert world.db.purchase_orders.find_one({"po_id": po["po_id"]})["status"] == (
        "PARTIALLY_RECEIVED"
    )
    _run(vd.void_grn(grn["grn_id"], MANAGER))
    stored = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    finding(
        stored["status"] == "SENT",
        f"R5: an order whose only receipt was voided still reads {stored['status']}",
    )
    assert [it["received_qty"] for it in stored["items"]] == [0]
    _run(vd.cancel_po(po["po_id"], "box went back", MANAGER))


def test_r5_voiding_one_of_two_receipts_keeps_the_order_part_received(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    dup, _ = _receive_again(world, po, "JOT/26-27/0702")
    _run(vd.void_grn(dup["grn_id"], MANAGER))
    stored = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    assert stored["status"] == "PARTIALLY_RECEIVED"


def test_r5_a_discarded_draft_is_ordered_again_by_typing_it(world):
    # Review round 4, problem 2: after a discard, typing the item again was
    # refused ALREADY_IN_CATALOGUE naming the deleted row; picking that row was
    # refused at send. The identity key is unique, so the draft itself comes
    # back -- to Needs review, still off -- and the item is ordered.
    _seed_user(world, ADMIN)
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _run(vd.void_grn(grn["grn_id"], MANAGER))
    _run(vd.cancel_po(po["po_id"], "box went back", MANAGER))
    assert _delete_refusal(world, draft_id) is None
    sku = world.product(draft_id)["sku"]
    assert sku not in _needs_review_list(world)

    refused = _refused_po(
        world,
        [
            {"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200},
            {"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200},
        ],
    )
    finding(
        refused is None,
        f"R5: ordering a discarded item again was refused ({getattr(refused, 'detail', None)})",
    )
    again = world.db.purchase_orders.find_one({"status": "SENT"})
    assert [it["product_id"] for it in again["items"]] == [draft_id, draft_id]
    assert len(world.products_named("Boss", "BOSS 1700")) == 1
    spine = world.product(draft_id)
    finding(
        spine["provisional"] is True and spine.get("discarded_draft") is False,
        f"R5: the draft did not come back as an ordered draft ({spine})",
    )
    assert spine["is_active"] is False
    twin = _twin_of(world, draft_id)
    assert twin["needs_review"] is True
    assert "is_active" not in twin and "deleted_at" not in twin
    finding(
        sku in _needs_review_list(world),
        "R5: the re-ordered draft is not in Needs review",
    )

    # And it lands like any ordered draft: held, a task, finished, on the shelf.
    grn2, accepted = world.receive_everything(again, invoice_no="JOT/26-27/0801")
    assert accepted["grn_status"] == "PARTIALLY_ACCEPTED"
    assert any("Finish" in t.get("title", "") for t in _open_tasks(world))
    world.finish_draft(draft_id, offer=2790)
    assert len(world.units(draft_id)) == 3
    assert world.product(draft_id)["is_active"] is True


def test_r5_a_deleted_catalogued_item_is_still_answered_with_it(world):
    # Only a DISCARDED DRAFT is given back. A catalogued product the admin
    # deleted is not silently revived by a manager's typed line.
    _seed_user(world, ADMIN)
    existing = world.catalogue_frame(
        "Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3155.76
    )
    _run(_catalog.delete_catalog_product(_twin_of_sku(world, existing["sku"])["id"], ADMIN))
    refused = _refused_po(
        world, [{"new_product": dict(CARRERA_TYPED), "quantity": 1, "unit_price": 3200}]
    )
    assert refused is not None and refused.status_code == 409
    # Never "already in the catalogue -- use it?": that would order stock of a
    # product nobody can sell (review round 5).
    finding(
        refused.detail.get("code") == "SWITCHED_OFF_IN_CATALOGUE",
        f"R5: a switched-off product was offered as 'use it' ({refused.detail})",
    )
    assert "switched off" in refused.detail["message"]
    assert world.product(existing["product_id"])["is_active"] is False
    assert world.db.purchase_orders.count_documents({}) == 0


def _twin_of_sku(world, sku):
    return world.db.catalog_products.find_one({"sku": sku})


# ---------------------------------------------------------------------------
# Round 5, review pass 1 -- the revive, the discard guard and the reopen
# ---------------------------------------------------------------------------

from api.routers.vendors import grn_accept as _grn_accept  # noqa: E402
from api.routers.vendors import purchase_orders as _po_mod  # noqa: E402


def _discard(world, draft_id, po, grn=None):
    """The box went back: void the receipt (if any), cancel the order, then
    the admin discards the draft."""
    _seed_user(world, ADMIN)
    if grn is not None:
        _run(vd.void_grn(grn["grn_id"], MANAGER))
    _run(vd.cancel_po(po["po_id"], "box went back", MANAGER))
    assert _delete_refusal(world, draft_id) is None
    assert world.product(draft_id)["discarded_draft"] is True


class _Truthless:
    """What a pymongo Database hands back for an unknown attribute: a
    Collection, whose truth value raises."""

    def __init__(self, coll):
        self._coll = coll

    def __bool__(self):
        raise NotImplementedError("Collection objects do not implement truth value testing")

    def __getattr__(self, name):
        return getattr(self._coll, name)


class _PymongoShaped:
    """The production `_get_db()` (vendors._shared): a raw pymongo Database.
    StrictDB answers `is_connected = True`; a real Database answers any
    attribute with a Collection."""

    def __init__(self, db):
        self._db = db

    def get_collection(self, name):
        return self._db.get_collection(name)

    def __getitem__(self, name):
        return self._db.get_collection(name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _Truthless(self._db.get_collection(name))


def test_r5_revive_works_on_the_production_db_shape(world, monkeypatch):
    # Review pass 1 (HIGH): getattr(db, "is_connected", True) on a pymongo
    # Database is a Collection; bool() of it raised after the spine was half
    # revived, the order 500'd, and the draft was left out of Needs review.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    monkeypatch.setattr(_po_mod, "_get_db", lambda: _PymongoShaped(world.db))
    again = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    assert again["items"][0]["product_id"] == draft_id
    finding(
        world.product(draft_id)["provisional"] is True
        and world.product(draft_id)["sku"] in _needs_review_list(world),
        "R5: on the production db shape the draft did not come back to Needs review",
    )


def test_r5_a_discarded_draft_switched_on_since_is_never_switched_off(world):
    # Review pass 1 (HIGH): the discard mark outlived a later switch-on, so a
    # manager's typed line turned a live product back into an inactive draft.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    world.finish_draft(draft_id, offer=2790)
    _run(
        _products.update_product(
            draft_id, _products.ProductUpdate(is_active=True), CATALOGUER
        )
    )
    assert world.product(draft_id)["is_active"] is True

    refused = _refused_po(
        world, [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    finding(
        world.product(draft_id)["is_active"] is True
        and not world.product(draft_id).get("provisional"),
        "R5: a typed line switched a live product back into a draft",
    )
    assert refused is not None and refused.detail.get("code") == "ALREADY_IN_CATALOGUE"


def test_r5_a_discarded_draft_finished_since_is_never_revived(world):
    # Review pass 1: finished while discarded (it stays off), then typed again
    # -- revived with catalog_status ACTIVE, its box went straight on the shelf
    # for a product that is off. It is an ordinary switched-off product now.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    world.finish_draft(draft_id, offer=2790)
    assert world.product(draft_id)["catalog_status"] != "DRAFT"

    refused = _refused_po(
        world, [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    finding(
        refused is not None and refused.detail.get("code") == "SWITCHED_OFF_IN_CATALOGUE",
        f"R5: a finished, switched-off draft was revived ({getattr(refused, 'detail', None)})",
    )
    assert world.product(draft_id).get("provisional") is False


def test_r5_a_discarded_frame_is_not_revived_by_a_typed_sunglass(world):
    # Review pass 3: the revive dropped what the buyer typed -- a sunglass
    # (18% GST) would be ordered as the discarded frame (5%).
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    refused = _refused_po(
        world,
        [{"new_product": {**BOSS_TYPED, "category": "SG"}, "quantity": 1, "unit_price": 1200}],
    )
    finding(
        world.product(draft_id).get("discarded_draft") is True,
        "R5: a typed sunglass revived the discarded frame",
    )
    assert refused is not None and refused.status_code == 409


def test_r5_a_revived_draft_carries_the_mrp_typed_this_time(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    world.raise_po(
        [{"new_product": {**BOSS_TYPED, "mrp": 3190}, "quantity": 1, "unit_price": 1200}]
    )
    finding(
        world.product(draft_id)["mrp"] == 3190,
        f"R5: the revive kept the discarded MRP ({world.product(draft_id)['mrp']})",
    )
    assert _twin_of(world, draft_id)["mrp"] == 3190
    actions = [a.get("action") for a in world.db.audit_logs.find({})]
    assert "product.draft_discarded" in actions
    assert "product.discarded_draft_revived" in actions


class _NeverStores:
    def __init__(self, repo):
        self._repo = repo

    def __getattr__(self, name):
        return getattr(self._repo, name)

    def create(self, doc, *a, **k):
        return None


def test_r5_an_order_that_is_not_stored_never_revives_the_draft(world, monkeypatch):
    # Review pass 1: the revive ran in the create loop; an order that then
    # failed to store left the discarded draft back in Needs review, ordered by
    # nothing -- and finishing it from there would switch it on.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    real = _po_mod.get_purchase_order_repository
    monkeypatch.setattr(_po_mod, "get_purchase_order_repository", lambda: _NeverStores(real()))
    body = vd.POCreate(
        vendor_id=VENDOR,
        delivery_store_id=STORE,
        items=[vd.POItemCreate(new_product=dict(BOSS_TYPED), quantity=1, unit_price=1200)],
    )
    _run(vd.create_po(body, MANAGER))
    finding(
        world.product(draft_id).get("discarded_draft") is True
        and world.product(draft_id).get("provisional") is False,
        "R5: an order that was never stored brought the discarded draft back",
    )
    assert world.product(draft_id)["sku"] not in _needs_review_list(world)


def test_r5_a_receipt_that_accepted_none_of_the_draft_does_not_hold_it(world):
    # Review pass 1: a broken box (every unit rejected) left an ACCEPTED
    # receipt naming the draft, and the refusal told the admin to void it and
    # to cancel the order -- both refused by the server.
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]
    created = _run(
        vd.create_grn(
            vd.GRNCreate(
                po_id=po["po_id"],
                vendor_invoice_no="JOT/26-27/0991",
                vendor_invoice_date="2026-09-28",
                items=[
                    vd.GRNItemCreate(
                        product_id=draft_id,
                        received_qty=2,
                        accepted_qty=0,
                        rejected_qty=2,
                        tallied=True,
                    )
                ],
                attachment_file_id="F-RECEIPT-PHOTO",
                attachment_filename="bill.jpg",
                attachment_mime="image/jpeg",
            ),
            MANAGER,
        )
    )
    _run(vd.accept_grn(created["grn_id"], MANAGER))
    refused = _delete_refusal(world, draft_id)
    assert refused is not None and refused.status_code == 409
    detail = str(refused.detail)
    finding(
        created["grn_number"] not in detail,
        f"R5: a receipt that accepted none of the draft blocks its discard ({detail})",
    )
    stored = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    if stored["status"] in ("DRAFT", "SENT", "ACKNOWLEDGED"):
        assert "cancel" in detail.lower()
    else:
        # The order cannot be cancelled; the refusal never says to.
        finding(
            "cancel that order" not in detail and "cannot be cancelled" in detail,
            f"R5: the refusal tells the admin to cancel an order cancel refuses ({detail})",
        )


def test_r5_the_discard_refuses_loudly_when_orders_cannot_be_read(world, monkeypatch):
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]
    monkeypatch.setattr(_grn_accept, "draft_discard_blockers", lambda pid: None)
    refused = _delete_refusal(world, draft_id)
    finding(
        refused is not None and refused.status_code == 503,
        f"R5: the draft was discarded although its orders could not be read ({refused})",
    )
    assert world.product(draft_id)["provisional"] is True


def test_r5_a_draft_whose_copy_predates_the_needs_review_mark_is_still_a_draft(world):
    # Drafts typed before this deploy have a catalogue copy with no
    # needs_review / spine_product_id: the spine's provisional flag decides.
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]
    world.db.catalog_products.update_one(
        {"spine_product_id": draft_id},
        {"$unset": {"needs_review": ""}},
    )
    refused = _delete_refusal(world, draft_id)
    assert refused is not None and refused.status_code == 409


def test_r5_a_void_never_reopens_a_cancelled_order(world):
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]
    created = _run(
        vd.create_grn(
            vd.GRNCreate(
                po_id=po["po_id"],
                vendor_invoice_no="JOT/26-27/0992",
                vendor_invoice_date="2026-09-28",
                items=[
                    vd.GRNItemCreate(
                        product_id=draft_id, received_qty=2, accepted_qty=2,
                        rejected_qty=0, tallied=True,
                    )
                ],
                attachment_file_id="F-RECEIPT-PHOTO",
                attachment_filename="bill.jpg",
                attachment_mime="image/jpeg",
            ),
            MANAGER,
        )
    )
    _run(vd.cancel_po(po["po_id"], "cancelled before the box was counted", MANAGER))
    _run(vd.void_grn(created["grn_id"], MANAGER))
    assert world.db.purchase_orders.find_one({"po_id": po["po_id"]})["status"] == "CANCELLED"


def test_r5_a_legacy_partial_order_is_reopened_too(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    world.db.purchase_orders.update_one({"po_id": po["po_id"]}, {"$set": {"status": "PARTIAL"}})
    _run(vd.void_grn(grn["grn_id"], MANAGER))
    assert world.db.purchase_orders.find_one({"po_id": po["po_id"]})["status"] == "SENT"
    assert "purchase.po_reopened_after_void" in [
        a.get("action") for a in world.db.audit_logs.find({})
    ]


def test_r5_the_reopen_never_undoes_a_receipt_counted_meanwhile(world):
    # Review pass 1: the reopen read "no live receipt" and then wrote SENT and
    # zero received unguarded; an accept of another receipt committing in
    # between was undone. The write lands only while nothing is counted.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    world.db.purchase_orders.update_one(
        {"po_id": po["po_id"]}, {"$set": {"total_received_qty": 1}}
    )
    _run(vd.void_grn(grn["grn_id"], MANAGER))
    stored = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    finding(
        stored["status"] == "PARTIALLY_RECEIVED" and stored["total_received_qty"] == 1,
        f"R5: the reopen overwrote a counted receipt ({stored['status']})",
    )


def test_r5_a_discarded_draft_is_never_received(world):
    # Review pass 2: a delivery challan could still receive a discarded draft;
    # held, its task pointed at a queue it had left.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    try:
        _run(
            vd.create_grn(
                vd.GRNCreate(
                    grn_subtype="DELIVERY_CHALLAN",
                    vendor_id=VENDOR,
                    dc_number="DC-0902",
                    dc_date="2026-09-28",
                    items=[
                        vd.GRNItemCreate(
                            product_id=draft_id, received_qty=2, accepted_qty=2,
                            rejected_qty=0, tallied=True,
                        )
                    ],
                ),
                MANAGER,
            )
        )
        refused = None
    except HTTPException as exc:
        refused = exc
    finding(
        refused is not None
        and refused.status_code == 409
        and refused.detail.get("code") == "DISCARDED_DRAFT",
        f"R5: a discarded draft was received ({refused})",
    )


def test_r5_a_discarded_draft_switched_on_unfinished_is_never_switched_off(world):
    # Switched on by hand while still a draft (catalog_status DRAFT): it is a
    # live product now, never turned back into an inactive draft by a typed line.
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    _discard(world, draft_id, po, grn)
    world.db.products.update_one({"product_id": draft_id}, {"$set": {"is_active": True}})
    assert world.product(draft_id)["catalog_status"] == "DRAFT"
    _refused_po(
        world, [{"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200}]
    )
    finding(
        world.product(draft_id)["is_active"] is True,
        "R5: a typed line switched a live (unfinished) product off",
    )


def test_r5_a_waiting_receipt_that_accepts_none_of_the_draft_does_not_block(world):
    _seed_user(world, ADMIN)
    po = world.raise_po(
        [{"new_product": dict(BOSS_TYPED), "quantity": 2, "unit_price": 1200}]
    )
    draft_id = po["items"][0]["product_id"]
    _run(
        vd.create_grn(
            vd.GRNCreate(
                po_id=po["po_id"],
                vendor_invoice_no="JOT/26-27/0993",
                vendor_invoice_date="2026-09-28",
                items=[
                    vd.GRNItemCreate(
                        product_id=draft_id, received_qty=2, accepted_qty=0,
                        rejected_qty=2, tallied=True,
                    )
                ],
                attachment_file_id="F-RECEIPT-PHOTO",
                attachment_filename="bill.jpg",
                attachment_mime="image/jpeg",
            ),
            MANAGER,
        )
    )
    _run(vd.cancel_po(po["po_id"], "box broken, not re-sent", MANAGER))
    refused = _delete_refusal(world, draft_id)
    finding(
        refused is None,
        f"R5: a receipt accepting none of the draft blocks its discard ({refused})",
    )


# ---------------------------------------------------------------------------
# Review round 5, pass 3 (HIGH): frames catalogued before eye size joined the
# key keep a 3-part key until the migration runs
# ---------------------------------------------------------------------------


def _pre_deploy_frame(world, lens_size="54"):
    existing = world.catalogue_frame(
        "Carrera", "CA 8895", "807", lens_size, mrp=6990, offer=6490, cost=3155.76
    )
    world.db.products.update_one(
        {"product_id": existing["product_id"]},
        {"$set": {"identity_key": "carrera|ca8895|807"}},
    )
    return existing


def test_r5_a_frame_keyed_before_this_deploy_is_still_found_by_its_eye_size(world):
    existing = _pre_deploy_frame(world)
    refused = _refused_po(
        world, [{"new_product": dict(CARRERA_TYPED), "quantity": 1, "unit_price": 3200}]
    )
    finding(
        refused is not None
        and refused.detail.get("code") == "ALREADY_IN_CATALOGUE"
        and refused.detail["matches"][0]["existing"]["product_id"] == existing["product_id"],
        f"R5: a typed line twinned a frame keyed before this deploy ({getattr(refused, 'detail', None)})",
    )
    try:
        world.catalogue_frame("Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3100)
        added = None
    except HTTPException as exc:
        added = exc
    finding(
        added is not None and added.status_code == 409,
        "R5: Add product twinned a frame keyed before this deploy",
    )
    assert len(world.products_named("Carrera", "CA 8895")) == 1


def test_r5_another_eye_size_of_a_frame_keyed_before_this_deploy_is_its_own_item(world):
    _pre_deploy_frame(world, lens_size="54")
    po = world.raise_po(
        [{"new_product": {**CARRERA_TYPED, "size": "52"}, "quantity": 1, "unit_price": 3200}]
    )
    assert po["items"][0]["product_id"]
    assert len(world.products_named("Carrera", "CA 8895")) == 2


def test_r5_the_identity_migration_rekeys_everything_but_the_collisions(world):
    from scripts.migrate_identity_key_tighten import run

    a = world.catalogue_frame("Boss", "BOSS 1701", "C2", "50", mrp=2990, offer=2790, cost=1200)
    b = world.catalogue_frame("Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3100)
    c = world.catalogue_frame("Carrera", "CA 8895", "807", "56", mrp=6990, offer=6490, cost=3100)
    for pid, old in (
        (a["product_id"], "boss|boss1701|c2"),
        (b["product_id"], "carrera|ca8895|807-b"),
        (c["product_id"], "carrera|ca8895|807-c"),
    ):
        world.db.products.update_one({"product_id": pid}, {"$set": {"identity_key": old}})
    # c is made to collide with b: the same eye size.
    world.db.products.update_one(
        {"product_id": c["product_id"]}, {"$set": {"attributes.lens_size": "54"}}
    )
    stats = run(world.db.products, apply=True)
    finding(
        world.product(a["product_id"])["identity_key"] == "boss|boss1701|c2|50",
        "R5: one collision stopped the migration re-keying an unrelated frame",
    )
    assert stats["collisions"] == 1
    assert world.product(b["product_id"])["identity_key"] == "carrera|ca8895|807-b"
    assert world.product(c["product_id"])["identity_key"] == "carrera|ca8895|807-c"


@ENGINES
def test_r5_an_acknowledged_catalogue_task_still_escalates_after_a_day(world, monkeypatch, engine):
    # Review round 5, pass 2: pressing Acknowledge stopped the P3 ack clock,
    # and the overdue clock added the P3 grace (3 days) to the day it was due:
    # one click bought four days.
    _seed_user(world, ADMIN)
    world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    (task,) = _open_tasks(world)
    _run(_tasks.acknowledge_task(task["task_id"], CATALOGUER))
    _escalate_after(world, monkeypatch, engine, hours=23)
    assert [t.get("assigned_to") for t in _open_tasks(world)] == [CATALOGUER["user_id"]]
    _escalate_after(world, monkeypatch, engine, hours=25)
    finding(
        [t.get("assigned_to") for t in _open_tasks(world)] == [ADMIN["user_id"]],
        "R5: an acknowledged catalogue task did not escalate after a day "
        f"({[(t.get('assigned_to'), t.get('status')) for t in _open_tasks(world)]})",
    )


def test_r5_a_line_for_no_product_at_all_is_never_the_cataloguers_task(world, caplog):
    # Review round 5, pass 3: a receipt line whose product is not on the spine
    # (a lens-catalogue id on a contact-lens auto-PO) was put on the catalogue
    # manager's list as "finish it in Needs review" -- there is nothing there.
    existing = world.catalogue_frame(
        "Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3155.76
    )
    po = world.raise_po(
        [
            {
                "product_id": existing["product_id"],
                "product_name": "Carrera CA 8895 807",
                "sku": existing["sku"],
                "quantity": 1,
                "unit_price": 3200,
            }
        ]
    )
    items = [dict(po["items"][0], product_id="LENSCAT-ACUVUE-OASYS--2.50")]
    world.db.purchase_orders.update_one({"po_id": po["po_id"]}, {"$set": {"items": items}})
    po = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    with caplog.at_level("ERROR"):
        world.receive_everything(po)
    finding(
        not [t for t in _open_tasks(world) if t.get("category") == "Catalogue"],
        "R5: the catalogue manager got a task for a product that does not exist "
        f"({[t.get('title') for t in _open_tasks(world)]})",
    )
    assert "do not exist on the catalogue spine" in caplog.text


def test_r5_the_eye_size_rule_does_not_depend_on_the_order_of_lines(world):
    # Review round 5, pass 3: [sizeless, 52] made two Boss 1700 C2 (a
    # sizeless twin) while [52, sizeless] was refused.
    refused = _refused_po(
        world,
        [
            {"new_product": dict(BOSS_TYPED, size=None), "quantity": 1, "unit_price": 1200},
            {"new_product": dict(BOSS_TYPED), "quantity": 1, "unit_price": 1200},
        ],
    )
    finding(
        refused is not None
        and refused.status_code == 422
        and refused.detail.get("code") == "EYE_SIZE_NEEDED"
        and [ln["line"] for ln in refused.detail["lines"]] == [0],
        f"R5: a sizeless line typed before its sized twin was accepted ({getattr(refused, 'detail', None)})",
    )
    assert world.products_named("Boss", "BOSS 1700") == []
    assert world.db.purchase_orders.count_documents({}) == 0
