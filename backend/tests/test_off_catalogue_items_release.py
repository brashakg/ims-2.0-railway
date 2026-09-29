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

Every finding test is xfail(strict=True) and fails ONLY on its finding check
(FindingStillOpen). Any other failure -- a precondition that no longer holds,
a crash, an unsupported fake feature -- is reported as a real failure, so an
xfail here can never pass hollow. When a fix lands the test XPASSes, strict
turns that red, and the fixer deletes the marker.

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


def open_finding(fid: str, what: str):
    return pytest.mark.xfail(
        strict=True, raises=FindingStillOpen, reason=f"{fid}: {what}"
    )


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
# The trace itself holds (NOT xfail): if any of these break, the world no
# longer reproduces the audit and every xfail below would be meaningless.
# ---------------------------------------------------------------------------


def test_the_world_reproduces_the_hold(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    draft = world.product(draft_id)
    # purchase_orders.create_po -> product_master.create_via_door(provisional)
    assert draft["provisional"] is True
    assert draft["is_active"] is False
    assert draft["catalog_status"] == "DRAFT"
    assert draft["done_gaps"] == ["offer_price"]
    assert draft["identity_key"] == "boss|boss1700|c2|52"
    # grn_accept: the line is held with reason incomplete_catalog, 0 minted.
    stored = world.grn(grn["grn_id"])
    assert stored["status"] == "PARTIALLY_ACCEPTED"
    assert stored["unresolved_lines"][0]["reason"] == "incomplete_catalog"


# ---------------------------------------------------------------------------
# C1 -- held units with no release path
# ---------------------------------------------------------------------------


@open_finding(
    "C1",
    "finishing the manager's draft does not put the held units on the shelf; "
    "the receipt stays PARTIALLY_ACCEPTED and the product stays inactive",
)
def test_c1_finishing_the_draft_puts_the_held_units_on_the_shelf(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

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
        world.product(draft_id).get("is_active") is True,
        "C1: the finished product is still inactive (provisional), so the "
        "counter cannot sell the units even once they are on the shelf",
    )


@open_finding(
    "C1",
    "the manager's draft is not in the cataloguer's Needs-review count or list",
)
def test_c1_the_held_draft_is_in_needs_review(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)
    sku = world.product(draft_id)["sku"]

    # The sidebar badge and the Catalog counts row: GET /catalog/online-summary.
    count = _online.catalog_counts(world.db)["needs_review"]
    # The list the badge opens: GET /catalog/products?needs_review=true&is_active=all.
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
    skus = [p.get("sku") for p in listed.get("products", [])]
    finding(
        count >= 1,
        f"C1: Needs review shows {count} while a receipt waits on this draft",
    )
    finding(
        sku in skus,
        f"C1: the Needs-review list does not contain the held draft {sku}",
    )


@open_finding(
    "C1",
    "no task is raised when a receipt holds units waiting to be catalogued",
)
def test_c1_a_held_receipt_raises_a_task_for_a_person(world):
    po, grn, draft_id = world.order_and_receive(BOSS_TYPED, qty=2, cost=1200)

    tasks = list(world.db.tasks.find({}))
    open_tasks = [
        t
        for t in tasks
        if str(t.get("status", "")).upper() in {"OPEN", "IN_PROGRESS", "ESCALATED"}
    ]
    finding(
        bool(open_tasks),
        "C1: nobody is told -- no task exists after units were held",
    )
    # Owner 2026-09-03: tasks go to PEOPLE, never a title. The person who can
    # finish the product is the catalogue manager (the store manager is
    # refused the catalogue: routes/catalogRoutes.tsx, products._CATALOG_ROLES).
    mine = [t for t in open_tasks if t.get("assigned_to") == CATALOGUER["user_id"]]
    finding(
        bool(mine),
        "C1: the task is not assigned to the catalogue manager by person "
        f"(assignees: {[t.get('assigned_to') for t in open_tasks]})",
    )
    text = " ".join(str(mine[0].get(k) or "") for k in ("title", "description"))
    for must in (grn["grn_number"], "BOSS 1700", STORE):
        finding(must in text, f"C1: the task does not name {must!r}: {text!r}")


# ---------------------------------------------------------------------------
# C2 -- typing an item we already have makes a hidden twin
# ---------------------------------------------------------------------------


@open_finding(
    "C2",
    "an off-catalogue PO line naming an existing brand+model+colour+size "
    "mints a second product instead of using the catalogued one",
)
def test_c2_typing_an_item_we_already_have_uses_it(world):
    existing = world.catalogue_frame(
        "Carrera", "CA 8895", "807", "54", mrp=6990, offer=6490, cost=3155.76
    )
    assert len(world.products_named("Carrera", "CA 8895")) == 1

    po = world.raise_po(
        [{"new_product": dict(CARRERA_TYPED), "quantity": 2, "unit_price": 3200}]
    )

    twins = world.products_named("Carrera", "CA 8895")
    finding(
        len(twins) == 1,
        f"C2: the PO silently made a second Carrera CA 8895 807 "
        f"({[(p['sku'], p.get('is_active')) for p in twins]})",
    )
    finding(
        po["items"][0]["product_id"] == existing["product_id"],
        "C2: the PO line does not point at the catalogued Carrera CA 8895 807",
    )


# ---------------------------------------------------------------------------
# C3 -- the cataloguer gets no warning against the manager's draft
# ---------------------------------------------------------------------------


@open_finding(
    "C3",
    "cataloguing a frame the manager already ordered gives no duplicate "
    "warning against his draft and makes a second product",
)
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
