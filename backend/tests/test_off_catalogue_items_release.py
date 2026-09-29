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
    # identity_key a catalogued Boss 1700 C2 does (C2/C3 root cause).
    assert draft["attributes"]["lens_size"] == "52"
    assert draft["identity_key"] == "boss|boss1700|c2"
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


def test_c1_no_catalogue_manager_fails_loud_to_the_admins(world):
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
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    tasks = _open_tasks(world)
    finding(
        [t.get("assigned_to") for t in tasks] == ["u-admin"]
        and "No catalogue manager" in tasks[0]["title"]
        and grn["grn_number"] in tasks[0]["title"],
        f"C1: a shop with no catalogue manager is not raised to the admins ({tasks})",
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
