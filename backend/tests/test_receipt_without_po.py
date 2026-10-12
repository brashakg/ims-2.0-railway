"""C7 / D14 -- goods bought WITHOUT a purchase order (walk-in / local dealer).

Owner audit row C7: the only way to put a cash purchase from a local dealer on
the shelf is to call it a "Delivery Challan" on the older receiving screen. It
asks for no price and no bill photo, the units land with no cost at all, and
accounts get no task. Owner ruling D14 (2026-09-29): a "Bought without PO"
receipt -- the supplier (a walk-in dealer not on the list is quick-added as
one, owner 2026-10-12), items, quantities, each item's cost, the bill
number/date if there is one, the bill photo, expiry -- that puts
the units on the shelf through the SAME receive/accept door as a PO receipt.
Its bill claims input tax credit only from a GST-registered supplier (owner
2026-10-13, G2); where it claims none, the GST paid is part of the stock cost
(G1) and GSTR-3B reports it in Table 4(D), the purchase from an unregistered
supplier in Table 5 (G3).
The receipt shows in the receipts list and on Movements labelled "Bought
without PO". (Who may receive -- managers only, ruling 2026-09-28 -- is the
shared receiving gate, which #1165 narrows for every kind of receipt at once.)

THE CONTRACT THESE TESTS PIN:
  * POST /vendors/grn with grn_subtype "NO_PO": no po_id; vendor_id (a supplier
    on file -- a typed dealer name alone is refused); vendor_invoice_no /
    vendor_invoice_date optional; every line carries unit_price > 0 (the cost);
    expiry_date per line as on any receipt; the bill photo
    (attachment_file_id) is required, by the same document gate as a PO
    receipt. Stored as grn_subtype "NO_PO". A cost typed on a PO receipt's
    line is dropped: that receipt is costed at the order's price.
  * POST /vendors/grn/{id}/accept is the ONE minting door: units are
    source_type GRN, carry the line's unit_price -- plus its GST when the
    supplier is unregistered (no credit to claim it back, G1) -- as
    unit_cost/cost_price (cost_source GRN_WALKIN, which becomes the product's
    cost: the latest price paid wins) and the line's expiry; accounts get a
    "book the bill" task.
  * A bill booked against it -- line-detail purchase-invoice door OR the
    header-only AP bill door -- claims credit only when its supplier has a
    valid GSTIN (G2), whatever the client sent; the draft offers each line's
    GST rate. GSTR-3B puts the GST of a bill claiming none in Table 4(D) and
    an unregistered supplier's purchase in Table 5 (G3).
  * GET /vendors/grn?grn_subtype=NO_PO lists only these receipts, naming the
    supplier; the Movements ledger labels them "Bought without PO".

Engine: mongomock behind the real repositories (CI installs it, #1168).
No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

import api.dependencies as deps  # noqa: E402
import database.connection as dbconn  # noqa: E402
from api.routers import inventory as inv  # noqa: E402
from api.routers import purchase_invoices as pi  # noqa: E402
from api.routers import tasks as tasks_api  # noqa: E402
from api.routers import vendors as vd  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import gst as fin_gst  # noqa: E402
from api.routers.finance import itc as fin_itc  # noqa: E402
from api.routers.inventory import movements as mv  # noqa: E402
from api.routers.reports import gstr3b as r3b  # noqa: E402
from api.services.file_store import InMemoryFileStore  # noqa: E402

STORE = "BV-NOPO-01"
ENTITY = "ENT-NOPO"
SHOP_GSTIN = "20AABCB1234C1ZS"  # Jharkhand (20), valid checksum
DEALER = "V-DEALER"
DEALER_GSTIN = "20AACCD5678E1ZX"  # same state -> CGST+SGST on the bill
SHARMA = "V-SHARMA"  # a walk-in dealer quick-added as a supplier: no GSTIN
FRAME = "P-FR-NOPO"
LENS = "P-CL-NOPO"
MONTH = "2026-09"

MANAGER = {
    "user_id": "u-mgr",
    "username": "mgr",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
ACCOUNTANT = {
    "user_id": "u-acct",
    "username": "acct",
    "roles": ["ACCOUNTANT"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["ADMIN"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# World: one mongomock database behind every door the flow walks through
# ---------------------------------------------------------------------------


class _Conn:
    """Stands in for DatabaseConnection: `.db` for the raw handlers,
    `.is_connected` + attribute collections for the repository factories."""

    def __init__(self, db):
        self.db = db
        self.is_connected = True

    def get_collection(self, name):
        return self.db[name]

    def __getattr__(self, name):
        return self.db[name]


def _frame(pid, cost):
    return {
        "product_id": pid,
        "category": "FRAME",
        "name": "Acme Aviator 5420",
        "attributes": {"brand_name": "ACME", "model_no": "5420", "colour_code": "BLK"},
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "cost_price": cost,
        "hsn_code": "900311",
        "gst_rate": 5.0,
        "catalog_status": "ACTIVE",
        "is_active": True,
    }


def _lens(pid, cost):
    return {
        "product_id": pid,
        "category": "CONTACT_LENS",
        "name": "Acme Daily -2.00",
        "attributes": {
            "brand_name": "ACME",
            "model_name": "Daily",
            "power": "-2.00",
            "expiry_date": "2027-08-31",
        },
        "mrp": 1200.0,
        "offer_price": 1100.0,
        "cost_price": cost,
        "hsn_code": "900130",
        "gst_rate": 12.0,
        "catalog_status": "ACTIVE",
        "is_active": True,
    }


@pytest.fixture()
def world(monkeypatch):
    client = mongomock.MongoClient()
    db = client[f"ims_nopo_{uuid.uuid4().hex[:8]}"]
    conn = _Conn(db)
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(dbconn, "get_db", lambda: conn)
    monkeypatch.setattr(r3b, "_get_raw_db", lambda: db)

    files = InMemoryFileStore()
    monkeypatch.setattr(vd, "get_file_store", lambda: files)

    db.entities.insert_one(
        {
            "entity_id": ENTITY,
            "legal_name": "BV Test Optical Pvt Ltd",
            "primary_state": "20",
            "gstins": [{"state_code": "20", "gstin": SHOP_GSTIN, "is_primary": True}],
        }
    )
    db.stores.insert_one(
        {
            "store_id": STORE,
            "store_name": "BV No-PO Test Shop",
            "store_type": "RETAIL",
            "state": "Jharkhand",
            "state_code": "20",
            "entity_id": ENTITY,
            "gstin": SHOP_GSTIN,
            "is_active": True,
        }
    )
    db.vendors.insert_one(
        {
            "vendor_id": DEALER,
            "legal_name": "Bank More Optical Traders",
            "trade_name": "Bank More Optical",
            "gstin": DEALER_GSTIN,
            "state": "Jharkhand",
            "state_code": "20",
            "credit_days": 0,
            "is_active": True,
        }
    )
    db.vendors.insert_one(
        {
            "vendor_id": SHARMA,
            "legal_name": "Sharma Optical",
            "trade_name": "Sharma Optical",
            "gstin": None,
            "gstin_status": "UNREGISTERED",
            "state": "Jharkhand",
            "state_code": "20",
            "is_active": True,
        }
    )
    db.products.insert_many([_frame(FRAME, 3000.0), _lens(LENS, 400.0)])
    # The person who holds ACCOUNTANT at this shop (a task to accounts may be
    # addressed to the role or resolved to this person -- both are accepted).
    db.users.insert_one(
        {
            "user_id": ACCOUNTANT["user_id"],
            "username": "acct",
            "roles": ["ACCOUNTANT"],
            "store_ids": [STORE],
            "is_active": True,
        }
    )

    app = FastAPI()
    app.include_router(vd.router, prefix="/vendors")
    app.include_router(inv.router, prefix="/inventory")
    app.include_router(tasks_api.router, prefix="/tasks")
    who = {"user": MANAGER}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    http = TestClient(app)

    def as_(user):
        who["user"] = user
        return http

    return {"db": db, "files": files, "as_": as_}


def _bill_photo(world, content=None, store=STORE):
    """A bill photo exactly as POST /vendors/grn/upload-doc stores it, sha256
    stamped. A fresh photo each call unless the same bytes are passed again."""
    content = content or b"\x89PNG cash memo " + uuid.uuid4().bytes
    return world["files"].put(
        content=content,
        filename="cash-memo.png",
        mime_type="image/png",
        metadata={
            "kind": "grn_document",
            "uploaded_by": "u-mgr",
            "store_id": store,
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    )


def _no_po_body(world, **over):
    body = {
        "grn_subtype": "NO_PO",
        "vendor_id": DEALER,
        "vendor_invoice_no": "CASH-77",
        "vendor_invoice_date": "2026-09-14",
        "attachment_file_id": _bill_photo(world),
        "attachment_filename": "cash-memo.png",
        "attachment_mime": "image/png",
        "items": [
            {
                "product_id": FRAME,
                "received_qty": 2,
                "accepted_qty": 2,
                "rejected_qty": 0,
                "tallied": True,
                "unit_price": 3100.0,
            },
            {
                "product_id": LENS,
                "received_qty": 3,
                "accepted_qty": 3,
                "rejected_qty": 0,
                "tallied": True,
                "unit_price": 420.0,
                "expiry_date": "2027-08-31",
            },
        ],
    }
    body.update(over)
    return body


def _seed_receipt(db, *, grn_id, subtype, status="ACCEPTED", vendor_id=DEALER, **extra):
    """A receipt row as the receiving door stores it (for the readers that sit
    after it: billing, the receipts list, the Movements ledger)."""
    doc = {
        "grn_id": grn_id,
        "grn_number": f"RCPT/{STORE}/2026-27/{grn_id[-4:]}",
        "grn_subtype": subtype,
        "po_id": None,
        "po_number": None,
        "vendor_id": vendor_id,
        "store_id": STORE,
        "vendor_invoice_no": f"INV-{grn_id[-4:]}",
        "vendor_invoice_date": "2026-09-14",
        "items": [
            {
                "product_id": FRAME,
                "received_qty": 2,
                "accepted_qty": 2,
                "rejected_qty": 0,
                "unit_price": 3100.0,
            }
        ],
        "total_received": 2,
        "total_accepted": 2,
        "total_rejected": 0,
        "status": status,
        "created_at": "2026-09-14T11:00:00",
        "accepted_at": "2026-09-14T11:05:00",
    }
    doc.update(extra)
    db.grns.insert_one(doc)
    return doc


# ===========================================================================
# 1. The receipt itself (C7: "asks for no price and no bill photo")
# ===========================================================================


def test_c7_bought_without_po_receipt_keeps_dealer_bill_photo_and_cost(world):
    res = world["as_"](MANAGER).post("/vendors/grn", json=_no_po_body(world))
    assert res.status_code == 201, res.text
    grn = world["db"].grns.find_one({"grn_id": res.json()["grn_id"]}, {"_id": 0})
    assert grn["grn_subtype"] == "NO_PO"
    assert grn["po_id"] is None
    assert grn["store_id"] == STORE
    assert grn["vendor_id"] == DEALER
    assert grn["vendor_invoice_no"] == "CASH-77"
    assert grn["vendor_invoice_date"] == "2026-09-14"
    # The bill photo is kept (a DC stores attachment_file_id None).
    assert grn["attachment_file_id"]
    assert grn["attachment_filename"] == "cash-memo.png"
    # Each line keeps what the shop paid for it.
    by_pid = {ln["product_id"]: ln for ln in grn["items"]}
    assert by_pid[FRAME]["unit_price"] == 3100.0
    assert by_pid[LENS]["unit_price"] == 420.0
    assert by_pid[LENS]["expiry_date"] == "2027-08-31"


def test_c7_a_receipt_line_keeps_its_cost():
    line = vd.GRNItemCreate(
        product_id=FRAME, received_qty=1, accepted_qty=1, unit_price=3100.0
    )
    assert line.model_dump().get("unit_price") == 3100.0


def test_c7_only_a_no_po_receipt_takes_a_typed_cost():
    """A PO receipt is costed at the order's agreed price (accept reads the PO);
    a cost on its lines would override it, so it is dropped."""
    po_receipt = vd.GRNCreate(
        po_id="PO-1",
        vendor_invoice_no="INV-1",
        items=[{"product_id": FRAME, "received_qty": 1, "accepted_qty": 1, "unit_price": 1.0}],
    )
    assert po_receipt.items[0].unit_price is None


def test_g2_the_credit_rule_itself():
    """Owner ruling 2026-10-13 (G2, reversing D14's 'no credit on any no-PO
    bill'): one rule for every bill door -- a walk-in bill claims credit from
    a GST-registered supplier, never from an unregistered one (reverse charge
    or not); an operator's own "no credit" still stands; and the rule gives
    the reason the booking form shows."""
    from api.services.org_validation import itc_claimable, itc_refusal

    reg = DEALER_GSTIN
    no_po, po = {"grn_subtype": "NO_PO"}, {"grn_subtype": "STANDARD"}
    assert itc_claimable(reg, False, True, no_po) is True
    assert itc_claimable(None, False, True, no_po) is False
    assert itc_claimable(None, True, True, no_po) is False
    assert itc_refusal(None, True, True, no_po) == "the supplier has no valid GSTIN"
    # A PO receipt's reverse-charge bill from an unregistered supplier claims.
    assert itc_claimable(None, True, True, po) is True
    assert itc_claimable(reg) is True
    assert itc_refusal(reg, False, False, po) == "switched off"
    assert itc_refusal(reg, False, True, no_po) is None


def test_c7_no_po_receipt_needs_the_bill_photo(world):
    body = _no_po_body(world)
    del body["attachment_file_id"]
    res = world["as_"](MANAGER).post("/vendors/grn", json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "ATTACHMENT_REQUIRED"
    assert world["db"].grns.count_documents({}) == 0


def test_c7_a_typed_dealer_name_is_no_seller(world):
    """Owner ruling 2026-10-12: a walk-in dealer not on file is quick-added as
    a supplier; a receipt naming a typed dealer only (round 4's dealer_name)
    is refused -- its bill had no supplier to book under and its 2B row no
    GSTIN to answer. The supplier picked is the seller."""
    http = world["as_"](MANAGER)
    for nobody in (
        _no_po_body(world, vendor_id=None, dealer_name="Sharma Optical, Bank More"),
        _no_po_body(world, vendor_id=None),
    ):
        res = http.post("/vendors/grn", json=nobody)
        assert res.status_code == 422, res.text
        assert "added as a supplier" in res.text
    assert world["db"].grns.count_documents({}) == 0
    res = http.post("/vendors/grn", json=_walk_in_body(world))
    assert res.status_code == 201, res.text
    grn = world["db"].grns.find_one({"grn_id": res.json()["grn_id"]}, {"_id": 0})
    assert grn["vendor_id"] == SHARMA and "dealer_name" not in grn


def test_c7_a_dealer_on_file_is_not_added_twice(world):
    """The quick-add goes through the one vendor create door, which refuses a
    second unregistered supplier under a name already on file (folded, trade
    or legal) -- one seller as two records had each record's bill numbers
    checked only against itself. A GSTIN makes a registration of its own."""
    http = world["as_"](MANAGER)

    def add(name, gstin=None):
        return http.post(
            "/vendors",
            json={
                "legal_name": name,
                "trade_name": name,
                "gstin_status": "REGISTERED" if gstin else "UNREGISTERED",
                "gstin": gstin,
                "address": "",
                "city": "Dhanbad",
                "state": "20",
                "mobile": "9800000000",
            },
        )

    for twin in ("Bank More Optical", "bank more optical traders", "SHARMA  OPTICAL."):
        res = add(twin)
        assert res.status_code == 400, (twin, res.text)
        assert "already on file" in res.text
    assert add("Sharma Optical Pune").status_code == 201
    assert add("Bank More Optical", gstin="20AABCB9999C1ZO").status_code == 201
    assert world["db"].vendors.count_documents({}) == 4


def _add_supplier(http, name, gstin=None):
    return http.post(
        "/vendors",
        json={
            "legal_name": name,
            "trade_name": name,
            "gstin_status": "REGISTERED" if gstin else "UNREGISTERED",
            "gstin": gstin,
            "address": "",
            "city": "Dhanbad",
            "state": "20",
            "mobile": "9800000000",
        },
    )


def test_c7_one_dealer_stays_one_supplier_through_every_vendor_door(world):
    """Panel probes (round 6): the name rule ran only on a POST with no
    GSTIN. The quick-add of 'Sharma Optical' WITH a GSTIN, Sharma on file
    without one, made a second record (201) whose bill '1' booked a second
    time; a PUT renaming 'Verma Optical' to 'Sharma Optical' (200), or
    clearing a registered supplier's GSTIN beside a namesake, did the same.
    One rule (master._name_clash) on create and update: names clash when
    either supplier has no GSTIN. Two registered namesakes are two
    taxpayers."""
    http = world["as_"](MANAGER)
    res = _add_supplier(http, "Sharma Optical", gstin="20AABCB9999C1ZO")
    assert res.status_code == 400 and "already on file" in res.text, res.text
    verma = _add_supplier(http, "Verma Optical")
    assert verma.status_code == 201, verma.text
    verma_id = verma.json()["vendor_id"]
    res = http.put(f"/vendors/{verma_id}", json={"trade_name": "Sharma Optical", "legal_name": "Sharma Optical"})
    assert res.status_code == 400 and "already on file" in res.text, res.text
    assert http.put(f"/vendors/{verma_id}", json={"mobile": "9800000001"}).status_code == 200
    twin = _add_supplier(http, "Bank More Optical", gstin="20AABCB9999C1ZO")
    assert twin.status_code == 201, twin.text
    res = http.put(f"/vendors/{twin.json()['vendor_id']}", json={"gstin": None})
    assert res.status_code == 400 and "already on file" in res.text, res.text
    names = sorted(v["trade_name"] for v in world["db"].vendors.find({}, {"_id": 0}))
    assert names == ["Bank More Optical", "Bank More Optical", "Sharma Optical", "Verma Optical"]


def test_c7_every_supplier_the_quick_add_refuses_can_be_picked(world):
    """Panel probe (round 6): the receiving screen's supplier list was the
    server's first 50 active suppliers, while the quick-add refuses a name
    on file among ALL of them -- past 50 (or switched off), a walk-in dealer
    could be neither picked nor added. The list now serves up to 1000 (the
    screen asks for all, active or not)."""
    db = world["db"]
    db.vendors.insert_many(
        [{"vendor_id": f"V-{n:03d}", "trade_name": f"Dealer {n}", "legal_name": f"Dealer {n}", "is_active": True} for n in range(120)]
    )
    db.vendors.update_one({"vendor_id": SHARMA}, {"$set": {"is_active": False}})
    http = world["as_"](MANAGER)
    assert _add_supplier(http, "Sharma Optical").status_code == 400
    listed = http.get("/vendors", params={"limit": 1000})
    assert listed.status_code == 200, listed.text
    ids = {v["vendor_id"] for v in listed.json()["vendors"]}
    assert {SHARMA, DEALER, "V-119"} <= ids and len(ids) == 122


def test_c7_every_line_must_carry_its_cost(world):
    http = world["as_"](MANAGER)
    body = _no_po_body(world)
    del body["items"][0]["unit_price"]
    refused = http.post("/vendors/grn", json=body)
    assert refused.status_code == 422, refused.text
    assert any(w in refused.text.lower() for w in ("cost", "price"))
    assert world["db"].grns.count_documents({}) == 0

    ok = http.post("/vendors/grn", json=_no_po_body(world))
    assert ok.status_code == 201, ok.text


def test_c7_a_cost_must_be_a_real_price(world):
    """Panel probe: "unit_price": Infinity (json.loads accepts it) was taken
    -- accept minted units at cost inf and GET /vendors/grn then 500'd on
    every read of the store's receipts. And with no ceiling a slipped zero
    (3100000 for 3100) became the stock cost silently."""
    import json

    from pydantic import ValidationError

    for bad in (float("inf"), float("nan"), 3_100_000.0):
        with pytest.raises(ValidationError):
            vd.GRNItemCreate(product_id=FRAME, received_qty=1, accepted_qty=1, unit_price=bad)
    http = world["as_"](MANAGER)
    raw = json.dumps(_no_po_body(world)).replace("3100.0", "3100000", 1)
    res = http.post("/vendors/grn", content=raw, headers={"content-type": "application/json"})
    assert res.status_code == 422, res.text
    assert world["db"].grns.count_documents({}) == 0
    assert http.get("/vendors/grn").status_code == 200


def _walk_in_body(world, **over):
    """A walk-in dealer's cash bill: the dealer quick-added as a supplier (no
    GSTIN)."""
    return _no_po_body(world, **{"vendor_id": SHARMA, **over})


def test_c7_the_same_walk_in_bill_cannot_go_on_the_shelf_twice(world):
    """Panel probe: the identical walk-in body posted twice (a retried post
    after a lost response) gave 201 + 201, and accepting both minted 10 units
    for a 5-unit bill."""
    http = world["as_"](MANAGER)
    body = _walk_in_body(world)
    first = http.post("/vendors/grn", json=body)
    assert first.status_code == 201, first.text
    again = http.post("/vendors/grn", json=body)
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["grn_id"] == first.json()["grn_id"]
    assert world["db"].grns.count_documents({}) == 1
    assert http.post(f"/vendors/grn/{first.json()['grn_id']}/accept").status_code == 200
    assert world["db"].stock_units.count_documents({}) == 5


def test_c7_a_new_photo_of_the_same_dealer_bill_is_still_the_same_bill(world):
    http = world["as_"](MANAGER)
    assert http.post("/vendors/grn", json=_walk_in_body(world)).status_code == 201
    # Same dealer, same bill number, a fresh upload.
    assert http.post("/vendors/grn", json=_walk_in_body(world)).status_code == 409
    # A different bill from the same dealer is a different purchase.
    other = _walk_in_body(world, vendor_invoice_no="CASH-78")
    assert http.post("/vendors/grn", json=other).status_code == 201


def test_c7_two_posts_at_once_meet_the_bill_photo_index(world, monkeypatch):
    """Two posts of one bill at the same moment both pass the look-up; the
    partial unique index on the bill photo's hash makes the second insert
    fail -- even when the photo was uploaded again under a new file id -- and
    that must read as the same 409, never a second receipt."""
    from api.routers.vendors import grn_create as gc
    from database.schemas import get_all_indexes

    spec = next(i for i in get_all_indexes()["grns"] if i.get("name") == "uniq_nopo_bill_hash")
    world["db"].grns.create_index(
        spec["keys"],
        unique=True,
        name=spec["name"],
        partialFilterExpression=spec["partialFilterExpression"],
    )
    http = world["as_"](MANAGER)
    memo = b"\x89PNG the one cash memo"
    body = _walk_in_body(world, vendor_invoice_no=None, attachment_file_id=_bill_photo(world, memo))
    assert http.post("/vendors/grn", json=body).status_code == 201
    body["attachment_file_id"] = _bill_photo(world, memo)

    real = gc._find_duplicate_receipt
    calls = {"n": 0}

    def racing(*a, **kw):  # the pre-insert look-up misses the rival
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*a, **kw)

    monkeypatch.setattr(gc, "_find_duplicate_receipt", racing)
    raced = http.post("/vendors/grn", json=body)
    assert raced.status_code == 409, raced.text
    assert world["db"].grns.count_documents({}) == 1


def test_c7_two_walk_in_posts_of_one_numbered_bill_meet_the_number_index(world, monkeypatch):
    """Panel probe (round 6): two posts of one dealer's numbered bill, each
    with its OWN photo, at the same moment both passed the look-up and both
    inserted -- the number index is STANDARD-only, so the bill's stock went
    on the shelf twice. uniq_nopo_vendor_bill (supplier + the bill's year and
    number, any shop) makes the second a 409."""
    from api.routers.vendors import grn_create as gc
    from database.schemas import get_all_indexes

    spec = next(i for i in get_all_indexes()["grns"] if i.get("name") == "uniq_nopo_vendor_bill")
    world["db"].grns.create_index(
        spec["keys"], unique=True, name=spec["name"], partialFilterExpression=spec["partialFilterExpression"]
    )
    http = world["as_"](MANAGER)
    assert http.post("/vendors/grn", json=_walk_in_body(world)).status_code == 201

    real = gc._find_duplicate_receipt
    calls = {"n": 0}

    def racing(*a, **kw):  # the pre-insert look-up misses the rival
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*a, **kw)

    monkeypatch.setattr(gc, "_find_duplicate_receipt", racing)
    raced = http.post("/vendors/grn", json=_walk_in_body(world))  # its own photo
    assert raced.status_code == 409, raced.text
    assert world["db"].grns.count_documents({}) == 1


def test_c7_the_startup_index_copies_equal_the_schema(world):
    """The atomic backstops are defined twice -- schemas.py INDEXES (what the
    race tests build) and connection.ensure_indexes (what prod builds at
    startup). Re-keying the startup copy left every test green; this compares
    the two for both receipt indexes and the bills' same-bill index."""
    from database.connection import DatabaseConnection
    from database.schemas import get_all_indexes

    built = {}

    class _Coll:
        def __init__(self, name):
            self.name = name

        def create_index(self, keys, **kw):
            if self.name in ("grns", "vendor_bills") and kw.get("name"):
                built[kw["name"]] = (list(keys), kw.get("unique", False), kw.get("partialFilterExpression"))

    class _DB:
        def __getitem__(self, name):
            return _Coll(name)

    conn = DatabaseConnection()
    saved = conn._db, conn._connected
    try:
        conn._db, conn._connected = _DB(), True
        conn.ensure_indexes()
    finally:
        conn._db, conn._connected = saved
    names = ("uniq_std_vendor_invoice_store", "uniq_nopo_bill_hash", "uniq_nopo_vendor_bill", "uniq_vendor_bill_number_key")
    for spec in get_all_indexes()["grns"] + get_all_indexes()["vendor_bills"]:
        if spec.get("name") in names:
            want = (list(spec["keys"]), True, spec["partialFilterExpression"])
            assert built[spec["name"]] == want, spec["name"]
    assert set(names) <= set(built)
    # The every-year bills index it replaces is gone from the startup path.
    assert "uniq_purchase_invoice_vendor_number" not in built


def test_c7_the_same_bill_photo_uploaded_again_is_the_same_bill(world):
    """Panel probe: a dealer's cash bill with NO number, posted again after a
    page reload with the same image uploaded again (a new file id): 201, 201
    -- the guard compared file ids only, and the supplier + number check has
    no number to go on. The upload door's sha256 is the bill's identity."""
    http = world["as_"](MANAGER)

    def upload():
        res = http.post(
            "/vendors/grn/upload-doc",
            files={"file": ("cash-memo.png", b"\x89PNG one cash memo", "image/png")},
        )
        assert res.status_code == 200, res.text
        return res.json()["file_id"]

    photo, photo_again = upload(), upload()
    assert photo != photo_again
    first = http.post("/vendors/grn", json=_walk_in_body(world, vendor_invoice_no=None, attachment_file_id=photo))
    assert first.status_code == 201, first.text
    again = http.post("/vendors/grn", json=_walk_in_body(world, vendor_invoice_no=None, attachment_file_id=photo_again))
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["grn_id"] == first.json()["grn_id"]
    assert world["db"].grns.count_documents({}) == 1


SHOP_B = "BV-NOPO-02"
MANAGER_B = {**MANAGER, "user_id": "u-mgr-b", "store_ids": [SHOP_B], "active_store_id": SHOP_B}


def _at_shop_b(world, **over):
    body = _no_po_body(world, **over)
    body["attachment_file_id"] = _bill_photo(world, store=SHOP_B)
    return body


def test_c7_one_dealer_bill_is_one_receipt_in_every_shop(world):
    """Panel probe (round 3 fixed it inside one shop only): shop A receives
    bill CASH-77 from a supplier; shop B posting the same bill was 201. One
    rule, every shop: the same supplier's bill is one receipt."""
    world["db"].stores.insert_one({"store_id": SHOP_B, "store_name": "BV No-PO Shop B", "store_type": "RETAIL", "entity_id": ENTITY, "is_active": True})
    for seller in (DEALER, SHARMA):
        assert world["as_"](MANAGER).post("/vendors/grn", json=_no_po_body(world, vendor_id=seller)).status_code == 201
        res = world["as_"](MANAGER_B).post("/vendors/grn", json=_at_shop_b(world, vendor_id=seller))
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["code"] == "GRN_DUPLICATE"
    assert world["db"].grns.count_documents({}) == 2


def test_c7_a_bill_number_is_one_bill_per_financial_year(world):
    """Panel probe: walk-in dealer 'Sharma Optical', bill '1' dated 2025-09-01 -> 201;
    the same dealer's bill '1' a year on -> 409, though invoice serials
    restart every 1 April (GST rule 46). Inside one year it is the same bill."""
    http = world["as_"](MANAGER)
    first = _walk_in_body(world, vendor_invoice_no="1", vendor_invoice_date="2025-09-01")
    assert http.post("/vendors/grn", json=first).status_code == 201
    same_year = _walk_in_body(world, vendor_invoice_no="1", vendor_invoice_date="2026-03-31")
    assert http.post("/vendors/grn", json=same_year).status_code == 409
    next_year = _walk_in_body(world, vendor_invoice_no="1", vendor_invoice_date="2026-04-01")
    assert http.post("/vendors/grn", json=next_year).status_code == 201, "a new year's bill 1"
    # A supplier picked from the list, and a receipt written before the key
    # carried the year (found through the supplier, not the key): the same.
    world["db"].grns.insert_one(
        {
            "grn_id": "GRN-LEGACY-7",
            "grn_subtype": "NO_PO",
            "vendor_id": DEALER,
            "store_id": STORE,
            "status": "ACCEPTED",
            "vendor_invoice_no": "7",
            "vendor_invoice_no_norm": "7",
            "vendor_invoice_date": "2025-09-01",
            "created_at": "2025-09-01T10:00:00",
        }
    )
    picked = _no_po_body(world, vendor_invoice_no="7", vendor_invoice_date="2025-10-01")
    assert http.post("/vendors/grn", json=picked).status_code == 409
    picked = _no_po_body(world, vendor_invoice_no="7", vendor_invoice_date="2026-04-01")
    assert http.post("/vendors/grn", json=picked).status_code == 201


def test_c7_a_po_supplier_reuses_its_serial_next_year_through_the_index(world):
    """The same rule for a PO receipt, and the atomic backstop agrees: the
    uniq_std_vendor_invoice_store index is keyed on the bill's year + number,
    so next year's INV-9 on a new order is not refused by the index either
    (that refusal read as a 500 'Failed to save goods receipt'). One order's
    INV-9 is one bill whatever the dates say (panel probe: entered on 31 March
    and again on 1 April -- the screen stamped the day received -- was 201,
    stock minted twice)."""
    from database.schemas import get_all_indexes

    spec = next(i for i in get_all_indexes()["grns"] if i.get("name") == "uniq_std_vendor_invoice_store")
    world["db"].grns.create_index(spec["keys"], unique=True, name=spec["name"], partialFilterExpression=spec["partialFilterExpression"])
    for po in ("PO-FY-1", "PO-FY-2"):
        world["db"].purchase_orders.insert_one(
            {
                "po_id": po,
                "po_number": po.replace("-", "/"),
                "vendor_id": DEALER,
                "delivery_store_id": STORE,
                "status": "SENT",
                "items": [{"product_id": FRAME, "quantity": 9, "unit_price": 3000.0}],
            }
        )

    def po_receipt(date, po="PO-FY-1"):
        return {
            "po_id": po,
            "vendor_invoice_no": "INV-9",
            "vendor_invoice_date": date,
            "attachment_file_id": _bill_photo(world),
            "items": [{"product_id": FRAME, "received_qty": 1, "accepted_qty": 1, "rejected_qty": 0, "tallied": True}],
        }

    http = world["as_"](MANAGER)
    assert http.post("/vendors/grn", json=po_receipt("2026-03-31")).status_code == 201
    assert http.post("/vendors/grn", json=po_receipt("2026-03-15")).status_code == 409
    again = http.post("/vendors/grn", json=po_receipt("2026-04-01"))
    assert again.status_code == 409, ("one order, one INV-9", again.text)
    nxt = http.post("/vendors/grn", json=po_receipt("2026-04-15", po="PO-FY-2"))
    assert nxt.status_code == 201, nxt.text
    keys = sorted(g["vendor_invoice_no_norm"] for g in world["db"].grns.find({}))
    assert keys == ["2025-26|INV9", "2026-27|INV9"]


def test_c7_a_slipped_bill_year_is_refused_not_a_new_bill(world):
    """Panel probe: 'Sharma Optical' bill '5' dated 2026-09-14 received; the
    same bill posted again with a fresh photo and the year slipped
    (2027-09-14, 0202-09-14, 2062-09-14) -> 201 at the same shop or another,
    and accepting both put 10 units on the shelf for one 5-unit bill. The
    receipt door holds the bill date to THE bill-date rule of the bill doors
    (ap_engine.iso_bill_date: a real date from the start of GST to today)."""
    world["db"].stores.insert_one({"store_id": SHOP_B, "store_name": "BV No-PO Shop B", "store_type": "RETAIL", "entity_id": ENTITY, "is_active": True})
    http = world["as_"](MANAGER)
    first = http.post("/vendors/grn", json=_walk_in_body(world, vendor_invoice_no="5", vendor_invoice_date="2026-09-14"))
    assert first.status_code == 201, first.text
    for slipped in ("14/09/2026", "2027-09-14", "0202-09-14", "2062-09-14"):
        here = world["as_"](MANAGER).post("/vendors/grn", json=_walk_in_body(world, vendor_invoice_no="5", vendor_invoice_date=slipped))
        assert here.status_code == 422, (slipped, here.text)
        body_b = _at_shop_b(world, vendor_id=SHARMA, vendor_invoice_no="5", vendor_invoice_date=slipped)
        there = world["as_"](MANAGER_B).post("/vendors/grn", json=body_b)
        assert there.status_code == 422, (slipped, there.text)
    assert "start of GST" in here.text
    assert world["db"].grns.count_documents({}) == 1
    # No date on the bill is still a bill (the day received stands in).
    undated = world["as_"](MANAGER).post("/vendors/grn", json=_walk_in_body(world, vendor_invoice_no="6", vendor_invoice_date=""))
    assert undated.status_code == 201, undated.text


def test_c7_a_voided_receipt_frees_its_bill(world):
    """The sanctioned correction path: a receipt logged by mistake is voided
    (it never put stock on the shelf), and the same bill -- same number, same
    photo -- can then be received properly."""
    http = world["as_"](MANAGER)
    body = _walk_in_body(world)
    first = http.post("/vendors/grn", json=body)
    assert first.status_code == 201, first.text
    assert http.post("/vendors/grn", json=body).status_code == 409
    voided = http.post(f"/vendors/grn/{first.json()['grn_id']}/void")
    assert voided.status_code == 200, voided.text
    again = http.post("/vendors/grn", json=body)
    assert again.status_code == 201, again.text


def test_c7_the_seller_named_must_be_a_real_one(world):
    """Panel probes: vendor_id 'NO-SUCH-VENDOR' -> 201 (the receipts list
    showed no supplier and the bill could never be booked); a typed
    dealer_name '...' alone -> 201, and again with a new photo -> 201. A line
    where nothing arrived is no purchase either."""
    http = world["as_"](MANAGER)
    ghost = http.post("/vendors/grn", json=_no_po_body(world, vendor_id="NO-SUCH-VENDOR"))
    assert ghost.status_code == 422, ghost.text
    assert "not on file" in ghost.text
    dots = http.post("/vendors/grn", json=_no_po_body(world, vendor_id=None, dealer_name="..."))
    assert dots.status_code == 422, dots.text
    empty = _walk_in_body(world)
    empty["items"][0].update(received_qty=0, accepted_qty=0, rejected_qty=0)
    refused = http.post("/vendors/grn", json=empty)
    assert refused.status_code == 422 and "how many arrived" in refused.text, refused.text
    assert world["db"].grns.count_documents({}) == 0


def test_c7_receiving_is_the_managers(world):
    """Owner ruling 2026-09-28: receiving is MANAGERS ONLY. An ACCOUNTANT
    could post a walk-in receipt at a cost they typed (201), accept it (200,
    5 units minted) and then book its bill. The receiving doors and their
    policy rows are the one _RECEIVE_ROLES list."""
    from api.routers.vendors import _shared
    from api.services import rbac_policy

    http = world["as_"](ACCOUNTANT)
    assert http.post("/vendors/grn", json=_walk_in_body(world)).status_code == 403
    created = world["as_"](MANAGER).post("/vendors/grn", json=_walk_in_body(world))
    grn_id = created.json()["grn_id"]
    assert world["as_"](ACCOUNTANT).post(f"/vendors/grn/{grn_id}/accept").status_code == 403
    assert world["db"].stock_units.count_documents({}) == 0
    assert set(_shared._RECEIVE_ROLES) == {"ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
    for method, path in (
        ("POST", "/api/v1/vendors/grn"),
        ("POST", "/api/v1/vendors/grn/express"),
        ("POST", "/api/v1/vendors/grn/{grn_id}/accept"),
        ("POST", "/api/v1/vendors/grn/{grn_id}/void"),
        ("POST", "/api/v1/vendors/grn/{grn_id}/escalate"),
        ("POST", "/api/v1/vendors/grn/upload-doc"),
        ("GET", "/api/v1/vendors/goods-receipt/cockpit"),
    ):
        for role in rbac_policy.ALL_ROLES:
            want = role in _shared._RECEIVE_ROLES or role == "SUPERADMIN"
            assert rbac_policy.check_access(method, path, [role]) == want, (method, path, role)
        # The handler's own gate is the same list as its row (panel: void,
        # escalate, the receiving screen and upload-doc could drift back to
        # _VENDOR_ROLES with every test green -- the middleware's row hid it).
        assert _handler_gate(method, path) == set(_shared._RECEIVE_ROLES), (method, path)


def _handler_gate(method, path):
    """The role set a vendors-router handler's require_roles(...) closes over,
    for the route at `path` (as the policy table writes it)."""
    sub = path.removeprefix("/api/v1/vendors")
    route = next(
        r for r in vd.router.routes if getattr(r, "path", None) == sub and method in r.methods
    )
    dep = route.dependant.dependencies
    for d in dep:
        for cell in getattr(d.call, "__closure__", None) or ():
            if isinstance(cell.cell_contents, set):
                return cell.cell_contents
    raise AssertionError(f"no require_roles gate on {method} {path}")


def test_c7_a_long_walk_in_history_never_hides_a_recent_bill(world):
    """Panel probe: the guard read the shop's first 500 walk-in receipts with
    no sort -- the OLDEST -- so past 500 the same supplier + bill number
    posted twice gave 201, 201."""
    world["db"].grns.insert_many(
        [
            {
                "grn_id": f"GRN-OLD-{n:04d}",
                "grn_subtype": "NO_PO",
                "store_id": STORE,
                "status": "ACCEPTED",
                "vendor_id": SHARMA,
                "vendor_invoice_no": f"OLD-{n}",
                "vendor_invoice_no_norm": f"OLD{n}",
                "created_at": "2026-04-01T10:00:00",
            }
            for n in range(500)
        ]
    )
    http = world["as_"](MANAGER)
    assert http.post("/vendors/grn", json=_walk_in_body(world)).status_code == 201
    assert http.post("/vendors/grn", json=_walk_in_body(world)).status_code == 409


def test_c7_a_no_po_receipt_names_no_purchase_order(world):
    """A "Bought without PO" receipt posted against a real, receivable PO would
    put PO goods on a bill that claims no input credit -- refused."""
    world["db"].purchase_orders.insert_one(
        {
            "po_id": "PO-REAL-1",
            "po_number": "PO/REAL/1",
            "vendor_id": DEALER,
            "delivery_store_id": STORE,
            "status": "SENT",
            "items": [{"product_id": FRAME, "quantity": 2, "unit_price": 3000.0}],
        }
    )
    res = world["as_"](MANAGER).post("/vendors/grn", json=_no_po_body(world, po_id="PO-REAL-1"))
    assert res.status_code == 422, res.text
    assert "name no purchase order" in res.text
    assert world["db"].grns.count_documents({}) == 0


# ===========================================================================
# 2. On the shelf through the one minting door, at the real cost
# ===========================================================================


def test_c7_accept_puts_units_on_the_shelf_at_what_was_paid(world):
    http = world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_no_po_body(world))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]

    accepted = http.post(f"/vendors/grn/{grn_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["grn_status"] == "ACCEPTED"
    assert accepted.json()["units_added"] == 5

    units = list(world["db"].stock_units.find({"source_id": grn_id}, {"_id": 0}))
    assert len(units) == 5
    # One minting door: the same GRN mint every PO receipt uses.
    assert {u["source_type"] for u in units} == {"GRN"}
    assert {u["store_id"] for u in units} == {STORE}
    assert {u["status"] for u in units} == {"AVAILABLE"}
    frames = [u for u in units if u["product_id"] == FRAME]
    lenses = [u for u in units if u["product_id"] == LENS]
    # The real cost of THIS purchase, not the catalogue's 3000 / 400.
    assert [u.get("unit_cost") for u in frames] == [3100.0, 3100.0]
    assert [u.get("cost_price") for u in frames] == [3100.0, 3100.0]
    assert [u.get("unit_cost") for u in lenses] == [420.0] * 3
    assert {u.get("expiry_date") for u in lenses} == {"2027-08-31"}
    # Labelled as what it is (owner 2026-10-12): a walk-in price, not a PO's.
    assert {u.get("cost_source") for u in units} == {"GRN_WALKIN"}


def test_c7_the_latest_price_paid_is_the_product_cost(world):
    """Owner ruling 2026-10-12: a walk-in buy's price updates the product's
    cost price -- the LATEST PRICE PAID WINS -- through the same
    cost-at-acceptance promote as a PO receipt, labelled GRN_WALKIN. It used
    to fill only a missing cost (the catalogue's 3000 stood) and called
    itself GRN_PO."""
    db = world["db"]

    def cost(pid):
        prod = db.products.find_one({"product_id": pid})
        return prod["cost_price"], prod.get("cost_source")

    db.products.update_one({"product_id": LENS}, {"$set": {"cost_source": "GRN_PO"}})
    _on_the_shelf(world)
    assert cost(FRAME) == (3100.0, "GRN_WALKIN")
    assert cost(LENS) == (420.0, "GRN_WALKIN")  # over a PO receipt's cost too
    items = _no_po_body(world)["items"]
    items[0]["unit_price"] = 3050.0
    _on_the_shelf(world, vendor_invoice_no="CASH-78", items=items)
    assert cost(FRAME) == (3050.0, "GRN_WALKIN")


def test_c7_accept_sends_the_bill_to_accounts(world):
    http = world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_no_po_body(world))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    assert http.post(f"/vendors/grn/{grn_id}/accept").status_code == 200

    def _names_receipt(task):
        payload = task.get("payload") or {}
        return grn_id in str(task.get("source_ref") or "") or payload.get(
            "grn_id"
        ) == grn_id or grn_id in str(task.get("link") or "")

    tasks = [t for t in world["db"].tasks.find({}, {"_id": 0}) if _names_receipt(t)]
    assert tasks, "no task reached accounts for this receipt"
    assert any(
        t.get("assigned_to") in ("ACCOUNTANT", ACCOUNTANT["user_id"]) for t in tasks
    ), tasks
    assert {t.get("link") for t in tasks} == {f"/purchase/invoices/book?grn_id={grn_id}"}


def test_c7_the_bill_task_names_no_dealer_to_the_counter(world):
    """Panel probe: the task said 'Book the bill for RCPT/... - bought without
    PO (Sharma Optical)' and '...bill CASH-77...', and it is stamped with the
    shop -- GET /tasks and GET /tasks/{id} gave every counter role there the
    dealer and the bill number (the home screen's priority tasks too)."""
    grn_id, _barcode = _walk_in_on_the_shelf(world)
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        counter = {"user_id": f"u-{role.lower()}", "roles": [role], "store_ids": [STORE], "active_store_id": STORE}
        listed = world["as_"](counter).get("/tasks", params={"status": "OPEN"})
        assert listed.status_code == 200, listed.text
        mine = [t for t in listed.json()["tasks"] if grn_id in str(t.get("link"))]
        assert mine, (role, listed.text)  # the control: the task IS listed
        one = world["as_"](counter).get(f"/tasks/{mine[0]['task_id']}")
        for text in (listed.text, one.text):
            assert "Sharma" not in text and "CASH-77" not in text and "CASH77" not in text, (role, text)
    assert "bought without PO" in mine[0]["title"]


def test_c7_no_bill_task_until_the_receipt_put_something_on_the_shelf(world):
    """Panel probes: a receipt whose every unit was rejected (accept 200,
    0 units) still sent accounts a 'book the bill' task no bill could ever
    satisfy (over-billed); and a receipt held part-accepted (a line not yet
    catalogued) must wait until the rest is on the shelf."""
    http = world["as_"](MANAGER)
    rejected = _walk_in_body(
        world,
        items=[{"product_id": FRAME, "received_qty": 2, "accepted_qty": 0, "rejected_qty": 2, "tallied": True, "unit_price": 3100.0}],
    )
    created = http.post("/vendors/grn", json=rejected)
    assert created.status_code == 201, created.text
    done = http.post(f"/vendors/grn/{created.json()['grn_id']}/accept")
    assert done.status_code == 200 and done.json()["units_added"] == 0, done.text
    assert done.json()["grn_status"] == "ACCEPTED"
    assert world["db"].tasks.count_documents({}) == 0

    new_frame = "P-FR-NOT-YET"
    held = _walk_in_body(
        world,
        vendor_invoice_no="CASH-91",
        items=[
            {"product_id": FRAME, "received_qty": 1, "accepted_qty": 1, "rejected_qty": 0, "tallied": True, "unit_price": 3100.0},
            {"product_id": new_frame, "received_qty": 1, "accepted_qty": 1, "rejected_qty": 0, "tallied": True, "unit_price": 900.0},
        ],
    )
    created = http.post("/vendors/grn", json=held)
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    part = http.post(f"/vendors/grn/{grn_id}/accept")
    assert part.json()["grn_status"] == "PARTIALLY_ACCEPTED", part.text
    assert world["db"].tasks.count_documents({}) == 0
    world["db"].products.insert_one({**_frame(new_frame, 900.0), "name": "Acme Round 1"})
    rest = http.post(f"/vendors/grn/{grn_id}/accept")
    assert rest.json()["grn_status"] == "ACCEPTED", rest.text
    assert [t.get("link") for t in world["db"].tasks.find({})] == [f"/purchase/invoices/book?grn_id={grn_id}"]


def test_c7_one_book_the_bill_task_for_every_receipt():
    """Express receive and a no-PO accept raise the same accounts task; it is
    built in ONE place (grn_accept._raise_book_bill_task), so who books and
    where the link points cannot drift between the two."""
    from pathlib import Path

    pkg = Path(vd.__file__).parent
    hits = [
        f.name
        for f in pkg.glob("*.py")
        for _ in range(f.read_text(encoding="utf-8").count("/purchase/invoices/book?grn_id="))
    ]
    assert hits == ["grn_accept.py"], hits


# ===========================================================================
# 3. No input tax credit, on either bill door (D14)
# ===========================================================================


def _invoice_body(grn_id, number, *, rate=5.0, vendor=DEALER):
    return pi.PurchaseInvoiceCreate(
        vendor_id=vendor,
        invoice_number=number,
        invoice_date="2026-09-14",
        grn_id=grn_id,
        recipient_entity_id=ENTITY,
        lines=[
            pi.PurchaseInvoiceLine(
                product_id=FRAME,
                description="Acme Aviator 5420",
                hsn="900311",
                qty=2,
                unit_price=3100.0,
                gst_rate=rate,
            )
        ],
    )


def _itc_everywhere():
    """Input credit for MONTH as each reader counts it: (register, summary,
    GSTR-3B Table 4 total)."""
    reg = _run(fin_itc.itc_register(period=MONTH, entity_id=None, current_user=ADMIN))
    summary = _run(fin_gst.get_gst_summary(month=9, year=2026, current_user=ADMIN))
    table4 = r3b._compute_gstr3b(MONTH, STORE)
    avail = (table4.get("itc") or table4).get("itcAvailable") or {}
    return (
        round(float(reg.get("total_itc") or 0), 2),
        round(float(summary.get("gst_input_credit") or 0), 2),
        round(sum(float(avail.get(k) or 0) for k in ("integratedTax", "centralTax", "stateTax")), 2),
    )


def test_g2_a_walk_in_bill_claims_credit_only_from_a_registered_dealer(world):
    """G2: the line-detail door stores a walk-in bill's credit by the one rule
    -- V-DEALER's (valid GSTIN) claims Rs 310 like its PO bill; unregistered
    Sharma's claims none, whatever the client sent (credit on, reverse
    charge). The register, /gst/summary and GSTR-3B Table 4 count only the
    claiming bills (round-6 D14 stored every walk-in bill with no credit)."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0001", subtype="STANDARD", po_id="PO-1")
    _seed_receipt(db, grn_id="GRN-NOPO-0002", subtype="NO_PO")
    _seed_receipt(db, grn_id="GRN-NOPO-0003", subtype="NO_PO", vendor_id=SHARMA)

    _run(pi.create_purchase_invoice(_invoice_body("GRN-STD-0001", "TAX-1"), current_user=ACCOUNTANT))
    # The client says nothing about ITC (the body default is True).
    _run(pi.create_purchase_invoice(_invoice_body("GRN-NOPO-0002", "CASH-2"), current_user=ACCOUNTANT))
    sharma = _invoice_body("GRN-NOPO-0003", "1", vendor=SHARMA)
    sharma.itc_eligible = sharma.reverse_charge = True
    _run(pi.create_purchase_invoice(sharma, current_user=ACCOUNTANT))

    bills = {b["grn_id"]: b for b in db.vendor_bills.find({}, {"_id": 0})}
    assert [bills[g]["tax_amount"] for g in ("GRN-STD-0001", "GRN-NOPO-0002", "GRN-NOPO-0003")] == [310.0] * 3
    assert bills["GRN-NOPO-0002"]["itc_eligible"] is True
    assert bills["GRN-NOPO-0003"]["itc_eligible"] is False
    assert _itc_everywhere() == (620.0, 620.0, 620.0)


def test_g2_the_form_preview_says_what_the_booking_stores_and_why(world):
    """The Purchase Invoices form shows POST /preview's itc_eligible and its
    reason: the same one credit rule (org_validation.itc_refusal) with the
    same receipt, so the form never promises credit the booking will not
    store -- nor explains a refusal by a rule of its own (round 6: it told a
    registered dealer's walk-in bill 'the supplier has no valid GSTIN')."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0009", subtype="NO_PO")
    _seed_receipt(db, grn_id="GRN-NOPO-0011", subtype="NO_PO", vendor_id=SHARMA)

    def preview(grn_id, vendor=DEALER, credit=True):
        body = _invoice_body(grn_id, "PREVIEW", vendor=vendor)
        form = pi.PurchaseInvoicePreview(vendor_id=vendor, lines=body.lines, grn_id=grn_id, itc_eligible=credit)
        out = _run(pi.preview_purchase_invoice(form, current_user=ACCOUNTANT))
        return out["itc_eligible"], out["itc_reason"]

    assert preview("GRN-NOPO-0009") == (True, None)
    assert preview("GRN-NOPO-0011", SHARMA) == (False, "the supplier has no valid GSTIN")
    assert preview("GRN-NOPO-0009", credit=False) == (False, "switched off")


def test_g2_the_header_only_bill_door_follows_the_same_rule(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0004", subtype="NO_PO")
    _seed_receipt(db, grn_id="GRN-NOPO-0005", subtype="NO_PO", vendor_id=SHARMA)
    for vendor, grn_id in ((DEALER, "GRN-NOPO-0004"), (SHARMA, "GRN-NOPO-0005")):
        bill = vd.VendorBillCreate(
            bill_number=f"CASH-{grn_id[-1]}",
            bill_date="2026-09-14",
            taxable_amount=6200.0,
            tax_amount=310.0,
            total_amount=6510.0,
            grn_id=grn_id,
        )
        _run(vd.create_vendor_bill(vendor, bill, current_user=ACCOUNTANT))
    stored = {b["grn_id"]: b["itc_eligible"] for b in db.vendor_bills.find({}, {"_id": 0})}
    assert stored == {"GRN-NOPO-0004": True, "GRN-NOPO-0005": False}
    reg = _run(fin_itc.itc_register(period=MONTH, entity_id=None, current_user=ADMIN))
    summary = _run(fin_gst.get_gst_summary(month=9, year=2026, current_user=ADMIN))
    assert float(reg.get("total_itc") or 0) == 310.0
    assert float(summary.get("gst_input_credit") or 0) == 310.0


def _bill_index(world):
    """The bills' atomic same-bill twin, exactly as schemas.py declares it."""
    from database.schemas import get_all_indexes

    spec = next(i for i in get_all_indexes()["vendor_bills"] if i.get("name") == "uniq_vendor_bill_number_key")
    world["db"].vendor_bills.create_index(
        spec["keys"], unique=True, name=spec["name"], partialFilterExpression=spec["partialFilterExpression"]
    )


def _on_the_shelf(world, **over):
    http = world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_no_po_body(world, **over))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    assert http.post(f"/vendors/grn/{grn_id}/accept").status_code == 200
    return grn_id


def _line_bill(grn_id, number, date, vendor=DEALER):
    body = _invoice_body(grn_id, number)
    body.invoice_date, body.vendor_id = date, vendor
    return _run(pi.create_purchase_invoice(body, current_user=ACCOUNTANT))


def _header_bill(grn_id, number, date, vendor=DEALER, **over):
    bill = vd.VendorBillCreate(
        bill_number=number,
        bill_date=date,
        taxable_amount=6200.0,
        tax_amount=310.0,
        total_amount=6510.0,
        grn_id=grn_id,
        **over,
    )
    return _run(vd.create_vendor_bill(vendor, bill, current_user=ACCOUNTANT))


def test_c7_a_bill_on_the_shelf_books_under_its_own_number(world):
    """Panel probe: supplier V-DEALER's receipt INV-9 dated 2025-09-01 was
    received, accepted and billed; INV-9 dated 2026-09-14 (a new financial
    year, GST rule 46) was received (201) and its stock minted -- and booking
    its bill then failed 409 on BOTH bill doors, which still refused any
    number the supplier had used in any year. One same-bill rule on every
    door (purchase_invoice_engine.same_bill), with its atomic twin."""
    from fastapi import HTTPException

    _bill_index(world)
    last_year = _on_the_shelf(world, vendor_invoice_no="INV-9", vendor_invoice_date="2025-09-01")
    _line_bill(last_year, "INV-9", "2025-09-01")
    this_year = _on_the_shelf(world, vendor_invoice_no="INV-9", vendor_invoice_date="2026-09-14")
    _line_bill(this_year, "INV-9", "2026-09-14")
    # The header-only door, the same way round.
    old = _on_the_shelf(world, vendor_invoice_no="INV-11", vendor_invoice_date="2024-09-01")
    _header_bill(old, "INV-11", "2024-09-01")
    new = _on_the_shelf(world, vendor_invoice_no="INV-11", vendor_invoice_date="2025-09-01")
    _header_bill(new, "INV-11", "2025-09-01")
    assert world["db"].vendor_bills.count_documents({}) == 4
    keys = sorted(b["bill_number_key"] for b in world["db"].vendor_bills.find({}))
    assert keys == ["2024-25|INV11", "2025-26|INV11", "2025-26|INV9", "2026-27|INV9"]
    # Inside one year it is still one bill, on either door -- refused by the
    # rule itself, not only by the index (dropped, so the rule is what is read).
    world["db"].vendor_bills.drop_index("uniq_vendor_bill_number_key")
    _seed_receipt(world["db"], grn_id="GRN-SEED-0901", subtype="STANDARD", po_id="PO-SEED-1")
    with pytest.raises(HTTPException) as line:
        _line_bill("GRN-SEED-0901", "inv/9", "2026-09-20")
    assert line.value.status_code == 409 and "already recorded" in str(line.value.detail)
    with pytest.raises(HTTPException) as header:
        _header_bill(None, "INV 11", "2026-03-01", bill_kind="SERVICES")
    assert header.value.status_code == 409 and "already recorded" in str(header.value.detail)
    keys = sorted(b["bill_number_key"] for b in world["db"].vendor_bills.find({}))
    assert keys == ["2024-25|INV11", "2025-26|INV11", "2025-26|INV9", "2026-27|INV9"]


def test_c7_two_walk_in_dealers_bill_one_are_two_bills(world):
    """'Sharma Optical' bill 1 and 'Gupta Optical' bill 1 are two bills: each
    dealer is its own supplier (quick-added at receiving), and each bill books
    under the supplier its receipt names -- on either bill door."""
    _bill_index(world)
    world["db"].vendors.insert_one({**world["db"].vendors.find_one({"vendor_id": SHARMA}, {"_id": 0}), "vendor_id": "V-GUPTA", "legal_name": "Gupta Optical", "trade_name": "Gupta Optical"})
    sharma = _on_the_shelf(world, vendor_id=SHARMA, vendor_invoice_no="1")
    gupta = _on_the_shelf(world, vendor_id="V-GUPTA", vendor_invoice_no="1")
    _line_bill(sharma, "1", "2026-09-14", vendor=SHARMA)
    _header_bill(gupta, "1", "2026-09-14", vendor="V-GUPTA")
    bills = {b["vendor_id"]: b for b in world["db"].vendor_bills.find({}, {"_id": 0})}
    assert set(bills) == {SHARMA, "V-GUPTA"}
    assert {b["bill_number_key"] for b in bills.values()} == {"2026-27|1"}


def test_c7_a_walk_in_bill_is_booked_once_under_its_supplier(world, monkeypatch):
    """Panel probes 1 and 2 (round 5): supplier V-DEALER's INV-9 was billed;
    a walk-in receipt from the same dealer with the number left blank then
    booked INV-9 again under a stand-in supplier (accepted), and one dealer's
    bill '1' booked under two stand-ins (a double payable of Rs 6510). The
    seller is the receipt's supplier: its bill books under that supplier
    only (any other is a vendor mismatch), and the same-bill rule reads that
    supplier's bills by number and year on both bill doors -- refused by the
    rule itself (index dropped), and by the index when the rule is raced."""
    from fastapi import HTTPException

    def refused(book):
        with pytest.raises(HTTPException) as exc:
            book()
        return exc.value

    # Probe 1: the supplier's own INV-9, then its walk-in receipt, no number.
    po_bill = _on_the_shelf(world, vendor_invoice_no="INV-9")
    _line_bill(po_bill, "INV-9", "2026-09-14")
    walk_in = _on_the_shelf(world, vendor_invoice_no=None)
    for book in (
        lambda: _line_bill(walk_in, "INV-9", "2026-09-14"),
        lambda: _header_bill(walk_in, "inv/9", "2026-09-14"),
    ):
        err = refused(book)
        assert err.status_code == 409 and "already recorded" in str(err.detail), err.detail
    for book in (
        lambda: _line_bill(walk_in, "INV-9", "2026-09-14", vendor=SHARMA),
        lambda: _header_bill(walk_in, "INV-9", "2026-09-14", vendor=SHARMA),
    ):
        err = refused(book)
        assert err.status_code == 409 and err.detail["code"] == "grn_vendor_mismatch", err.detail
    # Probe 2: one dealer's bill '1' on two receipts (the second unnumbered).
    first = _on_the_shelf(world, vendor_id=SHARMA, vendor_invoice_no="1")
    second = _on_the_shelf(world, vendor_id=SHARMA, vendor_invoice_no=None)
    _header_bill(first, "1", "2026-09-14", vendor=SHARMA)
    err = refused(lambda: _line_bill(second, "1", "2026-09-20", vendor=SHARMA))
    assert err.status_code == 409, err.detail
    assert world["db"].vendor_bills.count_documents({}) == 2
    # Raced (the look-up misses the rival booking): the atomic twin refuses
    # the same supplier's key on either door, as the same 409.
    _bill_index(world)
    monkeypatch.setattr(pi, "find_duplicate_bill", lambda *a, **k: None)
    for book in (
        lambda: _line_bill(second, "1", "2026-09-20", vendor=SHARMA),
        lambda: _header_bill(second, "1", "2026-09-20", vendor=SHARMA),
    ):
        err = refused(book)
        assert err.status_code == 409 and "already recorded" in str(err.detail), err.detail
    assert world["db"].vendor_bills.count_documents({}) == 2


def test_c7_the_same_bill_rule_is_one_function():
    """The rule's cases, through the one implementation every door calls."""
    from api.services.purchase_invoice_engine import same_bill

    def bill(no="INV-9", fy=2026, po=None, vid="V1"):
        return {"no": no, "fy": fy, "po_id": po, "vendor_id": vid}

    assert same_bill(bill(), bill(no="inv/9"))
    assert not same_bill(bill(), bill(fy=2025))
    assert same_bill(bill(fy=None), bill(fy=2025))  # an unknown year matches
    assert same_bill(bill(po="PO-B", fy=2025), bill(po="PO-B", fy=2026))
    assert not same_bill(bill(), bill(vid="V2"))
    assert not same_bill(bill(vid=None), bill(vid=None))  # no seller, no match
    assert not same_bill(bill(no=""), bill(no=""))


def test_d14_naming_a_challan_too_cannot_smuggle_the_credit_back(world):
    """Panel probe: grn_id = a no-PO receipt PLUS linked_dc_ids = an unrelated
    open challan of the same dealer and shop. The receipt was never read on
    that path, so the bill stored itc_eligible True and every ITC reader
    counted 310. A bill is one receipt or a set of challans, never both."""
    from fastapi import HTTPException

    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0101", subtype="NO_PO")
    _seed_receipt(
        db,
        grn_id="GRN-DC-0102",
        subtype="DELIVERY_CHALLAN",
        dc_number="DC-0102",
        dc_matched=False,
    )
    body = _invoice_body("GRN-NOPO-0101", "CASH-101")
    body.linked_dc_ids = ["GRN-DC-0102"]
    with pytest.raises(HTTPException) as refused:
        _run(pi.create_purchase_invoice(body, current_user=ACCOUNTANT))
    assert refused.value.status_code == 422
    assert db.vendor_bills.count_documents({}) == 0
    assert db.grns.find_one({"grn_id": "GRN-DC-0102"})["dc_matched"] is False
    assert _itc_everywhere() == (0.0, 0.0, 0.0)


def test_d14_the_gstr2b_screen_counts_what_the_register_counts(world):
    """Panel probes: the GSTR-2B reconcile fed EVERY vendor bill to the
    matcher, so a bill stored with no credit whose supplier filed it read
    "Matched (claim)" -- and with no 2B row, "ITC at risk" -- while the
    register, /gst/summary and GSTR-3B all said 0. Dropping it instead put the
    filed row under "In 2B, not booked - book then claim": a booked bill that
    claims no credit is its own line, and it answers its 2B row. A
    registered dealer's walk-in bill claims credit like any bill (G2): in 2B,
    it is safe to claim. A 17(5)-blocked bill reads as no credit."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0301", subtype="STANDARD", po_id="PO-3")
    _seed_receipt(db, grn_id="GRN-NOPO-0302", subtype="NO_PO")
    _seed_receipt(db, grn_id="GRN-NOPO-0303", subtype="NO_PO")
    _run(pi.create_purchase_invoice(_invoice_body("GRN-STD-0301", "TAX-301"), current_user=ACCOUNTANT))
    _run(pi.create_purchase_invoice(_invoice_body("GRN-NOPO-0302", "CASH-302"), current_user=ACCOUNTANT))
    off = _invoice_body("GRN-NOPO-0303", "CASH-303")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    portal = [
        fin_itc.Gstr2bRow(gstin=DEALER_GSTIN, invoice_no=n, taxable=6200.0, tax=310.0)
        for n in ("TAX-301", "CASH-302", "CASH-303")
    ]
    filed = _run(fin_itc.gstr2b_reconcile(fin_itc.Gstr2bReconcileBody(rows=portal), current_user=ADMIN))
    assert sorted(r["invoice_no"] for r in filed["matched"]) == ["CASH-302", "TAX-301"]
    assert filed["summary"]["itc_safe_to_claim"] == 620.0
    assert filed["summary"]["total_book_itc"] == _itc_everywhere()[0] == 620.0
    assert filed["only_in_2b"] == [] and filed["summary"]["only_in_2b"] == 0
    assert [r["invoice_no"] for r in filed["booked_no_credit"]] == ["CASH-303"]
    assert filed["summary"]["booked_no_credit"] == 1
    unfiled = _run(fin_itc.gstr2b_reconcile(fin_itc.Gstr2bReconcileBody(rows=[]), current_user=ADMIN))
    assert sorted(r["invoice_no"] for r in unfiled["only_in_books"]) == ["CASH-302", "TAX-301"]
    assert unfiled["summary"]["itc_at_risk"] == 620.0
    assert unfiled["booked_no_credit"] == []

    # A 17(5)-blocked bill its supplier filed: booked, no credit -- never
    # "book then claim". A cancelled bill is not booked: its row stays unbooked.
    db.vendor_bills.update_one({"bill_number": "TAX-301"}, {"$set": {"itc_blocked": True}})
    blocked = _run(fin_itc.gstr2b_reconcile(fin_itc.Gstr2bReconcileBody(rows=portal), current_user=ADMIN))
    assert sorted(r["invoice_no"] for r in blocked["booked_no_credit"]) == ["CASH-303", "TAX-301"]
    assert blocked["only_in_2b"] == [] and blocked["summary"]["total_book_itc"] == 310.0
    db.vendor_bills.update_one({"bill_number": "TAX-301"}, {"$set": {"status": "CANCELLED"}})
    cancelled = _run(fin_itc.gstr2b_reconcile(fin_itc.Gstr2bReconcileBody(rows=portal), current_user=ADMIN))
    assert [r["invoice_no"] for r in cancelled["only_in_2b"]] == ["TAX-301"]


def _walk_in_lines():
    return [
        {"product_id": FRAME, "received_qty": 2, "accepted_qty": 2, "rejected_qty": 0, "unit_price": 3100.0},
        {"product_id": LENS, "received_qty": 3, "accepted_qty": 3, "rejected_qty": 0, "unit_price": 420.0},
    ]


def test_c7_the_bill_draft_carries_the_price_paid(world):
    """Panel probe: the book-the-bill task opens /from-grn, whose lines took a
    price only from the PO -- a receipt of 2 x 3100 + 3 x 420 drafted every
    line at 0 (total 0.0), so the price paid had to be typed again from
    nothing."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0201", subtype="NO_PO", items=_walk_in_lines())
    draft = _run(pi.draft_invoice_from_grn("GRN-NOPO-0201", current_user=ACCOUNTANT))
    # The booking form's supplier is the receipt's (owner 2026-10-12): the
    # draft carries it, so the form opens filled (round 5: vendor_id None
    # opened a locked, empty supplier box and Book stopped there).
    assert draft["vendor_id"] == DEALER
    assert {ln["product_id"]: ln["unit_price"] for ln in draft["lines"]} == {
        FRAME: 3100.0,
        LENS: 420.0,
    }
    assert draft["taxable_total"] == 2 * 3100.0 + 3 * 420.0
    # Round 6 (HIGH, the root of 'in 2B, not booked'): the draft read a GST
    # rate only off a PO line, so every walk-in line drafted at 0% and a
    # registered dealer's bill booked with no tax against its 2B row. Each
    # line now takes the PO-line rate rule (the HSN's rate, before the
    # catalogued one): frame 900311 and contact lens 900130, both 5%.
    assert {ln["product_id"]: ln["gst_rate"] for ln in draft["lines"]} == {FRAME: 5.0, LENS: 5.0}
    assert draft["tax_total"] == 373.0


def test_g2_a_dealers_walk_in_bill_booked_as_drafted_meets_its_2b_row(world):
    """Panel probe (round 6, HIGH): V-DEALER's walk-in receipt CASH-77,
    accepted, its bill booked exactly as /from-grn offers it, the dealer's 2B
    row CASH-77 carrying the bill's tax: the screen said 'in 2B, not booked -
    book then claim' (and booking again 409s). Drafted at each line's rate,
    the bill claims its credit (G2) and matches its row."""
    db = world["db"]
    grn_id = _on_the_shelf(world)
    draft = _run(pi.draft_invoice_from_grn(grn_id, current_user=ACCOUNTANT))
    body = pi.PurchaseInvoiceCreate(
        vendor_id=draft["vendor_id"],
        invoice_number=draft["invoice_number"],
        invoice_date=draft["invoice_date"],
        grn_id=grn_id,
        recipient_entity_id=ENTITY,
        lines=[
            pi.PurchaseInvoiceLine(**{k: ln[k] for k in ("product_id", "description", "hsn", "qty", "unit_price", "gst_rate")})
            for ln in draft["lines"]
        ],
    )
    _run(pi.create_purchase_invoice(body, current_user=ACCOUNTANT))
    bill = db.vendor_bills.find_one({"grn_id": grn_id}, {"_id": 0})
    assert bill["itc_eligible"] is True and bill["tax_amount"] == 373.0
    row = {"gstin": DEALER_GSTIN, "invoice_no": "CASH-77", "taxable": 7460.0, "tax": 373.0, "invoice_date": "2026-09-14"}
    out = _reconcile([row], "2026-09")
    assert [m["invoice_no"] for m in out["matched"]] == ["CASH-77"], out
    assert out["only_in_2b"] == [] and out["summary"]["itc_safe_to_claim"] == 373.0


def test_c7_the_bill_is_held_to_the_price_paid(world):
    """The receipt's cost is what the units went on the shelf at; the bill's
    price drives the product's moving-average cost. With no PO the match read
    every line as "not on purchase order", so a fair bill and an inflated one
    looked the same. Now the receipt is the order it is matched against."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0202", subtype="NO_PO")
    _seed_receipt(db, grn_id="GRN-NOPO-0203", subtype="NO_PO")
    _run(pi.create_purchase_invoice(_invoice_body("GRN-NOPO-0202", "CASH-202"), current_user=ACCOUNTANT))
    dear = _invoice_body("GRN-NOPO-0203", "CASH-203")
    dear.lines[0].unit_price = 4000.0
    _run(pi.create_purchase_invoice(dear, current_user=ACCOUNTANT))

    fair = db.vendor_bills.find_one({"grn_id": "GRN-NOPO-0202"}, {"_id": 0})
    inflated = db.vendor_bills.find_one({"grn_id": "GRN-NOPO-0203"}, {"_id": 0})
    assert fair["match_status"] == "MATCHED", fair["match_detail"]
    assert inflated["match_status"] == "ON_HOLD_EXCEPTION"
    reasons = " ".join(inflated["match_detail"]["exceptions"])
    assert "4000" in reasons and "3100" in reasons, reasons


# ===========================================================================
# 4. Seen for what it is: receipts list + Movements
# ===========================================================================


def test_c7_receipts_list_finds_and_names_bought_without_po(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0005", subtype="STANDARD", po_id="PO-5")
    _seed_receipt(db, grn_id="GRN-NOPO-0006", subtype="NO_PO", vendor_id=SHARMA)
    res = world["as_"](MANAGER).get("/vendors/grn", params={"grn_subtype": "NO_PO"})
    assert res.status_code == 200, res.text
    rows = res.json()["grns"]
    assert [r["grn_id"] for r in rows] == ["GRN-NOPO-0006"]
    assert rows[0]["vendor_name"] == "Sharma Optical"


def test_c7_counter_staff_never_read_what_was_paid(world):
    """Panel probe: a no-PO receipt stores the cost per line and the dealer,
    and both GRN reads were open to any logged-in user -- SALES_STAFF
    and CASHIER read unit_price [3100, 420] and 'Sharma Optical'. The reads
    are the purchase roles' (the same gate as #1161), store-scoped."""
    http = world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_walk_in_body(world))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    for role in ("SALES_STAFF", "CASHIER"):
        counter = {**MANAGER, "user_id": f"u-{role.lower()}", "roles": [role]}
        http = world["as_"](counter)
        listed = http.get("/vendors/grn", params={"grn_subtype": "NO_PO"})
        detail = http.get(f"/vendors/grn/{grn_id}")
        assert listed.status_code == 403, (role, listed.text)
        assert detail.status_code == 403, (role, detail.text)
        assert "3100" not in listed.text + detail.text
    other_shop = {**MANAGER, "store_ids": ["BV-OTHER-01"], "active_store_id": "BV-OTHER-01"}
    assert world["as_"](other_shop).get(f"/vendors/grn/{grn_id}").status_code == 404


def test_c7_what_was_paid_has_one_visibility_rule(world):
    """Panel probe: as STORE_MANAGER the barcode trace showed line prices
    [None, None] (cost_mask) while GET /vendors/grn/{id} and the list returned
    [3100, 420]; AREA_MANAGER the same. The price paid on a receipt line is
    cost: every read that returns a receipt goes through the one cost rule
    (cost_mask.mask_receipt) -- the managers who receive read the receipt,
    not what was paid; the roles that see cost read both."""
    grn_id, barcode = _walk_in_on_the_shelf(world)
    reads = (
        lambda http: http.get(f"/vendors/grn/{grn_id}").json(),
        lambda http: http.get("/vendors/grn", params={"grn_subtype": "NO_PO"}).json()["grns"][0],
        lambda http: http.get(f"/inventory/barcode/{barcode}/trace").json()["purchase"][0],
    )
    for role in ("STORE_MANAGER", "AREA_MANAGER"):
        user = {**MANAGER, "user_id": f"u-{role.lower()}", "roles": [role]}
        for read in reads:
            grn = read(world["as_"](user))
            assert grn["grn_id"] == grn_id, (role, grn)
            assert [it.get("unit_price") for it in grn["items"]] == [None, None], (role, grn)
            assert [it["accepted_qty"] for it in grn["items"]] == [2, 3], (role, grn)
    for role in ("ADMIN", "ACCOUNTANT"):
        user = {**MANAGER, "user_id": f"u-{role.lower()}", "roles": [role]}
        for read in reads:
            grn = read(world["as_"](user))
            assert [it["unit_price"] for it in grn["items"]] == [3100.0, 420.0], (role, grn)


def _walk_in_on_the_shelf(world):
    """A walk-in dealer's bill received and accepted; returns the receipt id
    and one minted frame unit's barcode."""
    http = world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_walk_in_body(world))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    assert http.post(f"/vendors/grn/{grn_id}/accept").status_code == 200
    return grn_id, world["db"].stock_units.find_one({"product_id": FRAME})["barcode"]


def test_c7_the_barcode_trace_shows_no_counter_what_was_paid(world):
    """Panel probe: GET /inventory/barcode/{barcode}/trace returned the raw
    receipt and unit to any signed-in user -- a SALES_STAFF read the unit's
    cost 3100 and each line's price paid [3100, 420]. The route stays open to
    every signed-in role (the sale, transfer and return history carries no
    cost); what was paid goes through the one cost rule (cost_mask): the
    unit's cost through its "product" context (#1161: the managers read a
    product's per-unit cost, the counter never), each receipt line's price
    paid through mask_receipt.

    Owner ruling 2026-10-12: nor does a counter role read who sold it, the
    bill number, the bill photo's file id / hash or the accept lock there
    (SALES_STAFF read 'V-SHARMA', 'CASH-77' and the photo's sha256); the
    managers and accounts still do, and the managers never what was paid."""
    from api.services.cost_mask import can_see_cost

    grn_id, barcode = _walk_in_on_the_shelf(world)
    url = f"/inventory/barcode/{barcode}/trace"
    world["db"].grns.update_one({"grn_id": grn_id}, {"$set": {"accept_lock_token": "tok-1", "accept_lock_by": "u-mgr"}})
    stored = world["db"].grns.find_one({"grn_id": grn_id})
    secrets = (SHARMA, "CASH-77", "CASH77", stored["attachment_file_id"], stored["attachment_sha256"], "tok-1")
    hidden = {"vendor_id", "vendor_name", "po_id", "po_number", "vendor_invoice_no", "vendor_invoice_no_norm", "attachment_file_id", "attachment_sha256", "attachment_filename", "accept_lock_token", "accept_lock_by"}
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "CATALOG_MANAGER"):
        counter = {"user_id": "u-counter", "roles": [role], "store_ids": [STORE], "active_store_id": STORE}
        res = world["as_"](counter).get(url)
        receipt = res.json()["purchase"][0]
        assert receipt["grn_id"] == grn_id and receipt["grn_subtype"] == "NO_PO", (role, receipt)
        assert [it["accepted_qty"] for it in receipt["items"]] == [2, 3], (role, receipt)
        assert not hidden & set(receipt), (role, sorted(hidden & set(receipt)))
        assert not [x for x in secrets if x in res.text], (role, res.text)
    for role in ("STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT", "ADMIN"):
        user = {"user_id": "u-buyer", "roles": [role], "store_ids": [STORE], "active_store_id": STORE}
        receipt = world["as_"](user).get(url).json()["purchase"][0]
        assert (receipt["vendor_id"], receipt["vendor_invoice_no"]) == (SHARMA, "CASH-77"), role
        assert receipt["attachment_file_id"] == stored["attachment_file_id"], role
        assert receipt["accept_lock_token"] == "tok-1", role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "STORE_MANAGER", "AREA_MANAGER"):
        counter = {"user_id": "u-counter", "roles": [role], "store_ids": [STORE], "active_store_id": STORE}
        res = world["as_"](counter).get(url)
        assert res.status_code == 200, (role, res.text)
        body = res.json()
        assert body["stock_unit"]["barcode"] == barcode
        assert body["purchase"][0]["grn_id"] == grn_id
        sees_unit_cost = can_see_cost(counter, "product")
        assert sees_unit_cost == role.endswith("_MANAGER"), role  # owner ruling D7
        assert bool({"unit_cost", "cost_price"} & set(body["stock_unit"])) == sees_unit_cost, (role, body)
        assert [sorted(it) for it in body["purchase"][0]["items"]] and all(
            "unit_price" not in it for it in body["purchase"][0]["items"]
        ), (role, body)
    # The control: a role the cost rule admits reads both figures (unregistered
    # Sharma's frame went on the shelf at 3100 + its 5% GST, G1).
    mine = world["as_"](ADMIN).get(url).json()
    assert mine["stock_unit"]["unit_cost"] == 3255.0
    assert [ln["unit_price"] for ln in mine["purchase"][0]["items"]] == [3100.0, 420.0]


def test_c7_the_policy_table_answers_as_the_receipt_readers_do(world):
    """Panel finding: the two GRN reads were gated to the purchase roles while
    their rbac_policy rows still said AUTHENTICATED, and no test compared the
    two. Every role asks each route that returns a receipt; the table must
    give the handler's answer and mark the route store-scoped."""
    from api.services import rbac_policy

    grn_id, _barcode = _walk_in_on_the_shelf(world)
    for url in ("/vendors/grn", f"/vendors/grn/{grn_id}"):
        path = f"/api/v1{url}"
        assert rbac_policy.is_store_scoped("GET", path), path
        for role in rbac_policy.ALL_ROLES:
            user = {"user_id": "u-any", "roles": [role], "store_ids": [STORE], "active_store_id": STORE}
            served = world["as_"](user).get(url).status_code != 403
            assert served == rbac_policy.check_access("GET", path, [role]), (path, role)


def test_c7_movements_label_bought_without_po(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0007", subtype="STANDARD", po_id="PO-7", po_number="PO/7")
    _seed_receipt(db, grn_id="GRN-NOPO-0008", subtype="NO_PO")
    events = mv._collect_received_events(db, STORE, "2026-09-01T00:00:00", None)
    by_ref = {e["ref_id"]: e for e in events}
    assert set(by_ref) == {"GRN-STD-0007", "GRN-NOPO-0008"}
    assert "Bought without PO" in by_ref["GRN-NOPO-0008"]["detail"]
    assert "Bought without PO" not in by_ref["GRN-STD-0007"]["detail"]


def test_c7_the_barcode_trace_is_every_signed_in_roles(client):
    """Decided rule (a): the trace is open to every signed-in role -- the
    counter's barcode look-up (#1181) reads it, through the real RBAC
    middleware (this file's own app has none). Narrowing its policy row to
    the purchase roles 403'd every counter role with every other test green."""
    import jwt
    from datetime import timedelta

    from api.routers import auth as auth_mod
    from api.services import rbac_policy

    template = "/api/v1/inventory/barcode/{barcode}/trace"
    for role in rbac_policy.ALL_ROLES:
        assert rbac_policy.check_access("GET", template, [role]), role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        token = jwt.encode(
            {
                "sub": "u-trace",
                "user_id": "u-trace",
                "username": "trace",
                "roles": [role],
                "store_ids": [STORE],
                "active_store_id": STORE,
                "exp": datetime.utcnow() + timedelta(hours=1),
            },
            auth_mod.SECRET_KEY,
            algorithm=auth_mod.ALGORITHM,
        )
        res = client.get(
            "/api/v1/inventory/barcode/BV-NO-SUCH-UNIT/trace",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, (role, res.text)
        assert res.json()["barcode"] == "BV-NO-SUCH-UNIT"


def _return_of(world, return_id, stock_id, vendor=DEALER):
    """A vendor return whose unit `stock_id` went back (rtv_vendor_id linked,
    as POST /vendor-returns links a quarantined unit)."""
    db = world["db"]
    db.stock_units.update_one({"stock_id": stock_id}, {"$set": {"status": "QUARANTINED", "rtv_vendor_id": return_id}})
    unit = db.stock_units.find_one({"stock_id": stock_id})
    return {"return_id": return_id, "vendor_id": vendor, "store_id": STORE}, unit["product_id"]


def test_r5_a_walk_in_return_reverses_only_the_credit_its_bill_claimed(world):
    """Owner rulings 2026-10-12 (R5) and 2026-10-13 (G2): a vendor return
    reverses GST only where the returned goods' bill claimed it -- the debit
    note reversing Input CGST / SGST that was never claimed credits a ledger
    never debited. Walk-in goods from unregistered Sharma: amount only. From
    registered V-DEALER they claim credit like a PO receipt's (G2) and reverse
    it -- unless the bill was booked with credit switched off. Each line by
    the receipt its unit came in on."""
    from api.services.rtv_debit_note import DebitNoteEngine, tally_build_debit_note_xml

    db = world["db"]
    seller = {"entity_id": ENTITY, "name": "BV Test Optical Pvt Ltd", "gstin": SHOP_GSTIN, "state_code": "20"}
    frame = [{"product_id": FRAME, "product_name": "Acme Aviator", "quantity": 1, "unit_price": 3100.0}]

    def note(rtv, lines):
        vendor = db.vendors.find_one({"vendor_id": rtv["vendor_id"]}, {"_id": 0})
        res = DebitNoteEngine(db).issue(rtv, vendor, actor="u-acct", seller=seller, lines=lines)
        assert res["ok"], res
        return res["debit_note"]

    def frame_unit(grn_id):
        return db.stock_units.find_one({"source_id": grn_id, "product_id": FRAME})["stock_id"]

    sharma = _on_the_shelf(world, vendor_id=SHARMA)
    walked = note(_return_of(world, "VR-SHARMA", frame_unit(sharma), SHARMA)[0], frame)
    assert walked["totals"]["tax_paise"] == 0
    assert walked["totals"]["grand_total_paise"] == walked["totals"]["taxable_paise"] == 310000
    assert "Input CGST" not in tally_build_debit_note_xml(walked)

    dealer = _on_the_shelf(world, vendor_invoice_no="CASH-51")
    claimed = note(_return_of(world, "VR-DEALER", frame_unit(dealer))[0], frame)
    assert claimed["totals"]["tax_paise"] == 15500  # 5% of Rs 3100, reversed
    assert "Input CGST" in tally_build_debit_note_xml(claimed)

    off_grn = _on_the_shelf(world, vendor_invoice_no="CASH-52")
    off = _invoice_body(off_grn, "CASH-52")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    _seed_receipt(db, grn_id="GRN-STD-0501", subtype="STANDARD", po_id="PO-501")
    db.stock_units.insert_one({"stock_id": "SU-PO-1", "product_id": LENS, "source_type": "GRN", "source_id": "GRN-STD-0501", "store_id": STORE})
    # One return of both: each line by its own receipt.
    db.stock_units.update_many({"stock_id": {"$in": [frame_unit(off_grn), "SU-PO-1"]}}, {"$set": {"rtv_vendor_id": "VR-MIXED"}})
    mixed = note(
        {"return_id": "VR-MIXED", "vendor_id": DEALER, "store_id": STORE},
        frame + [{"product_id": LENS, "product_name": "Acme Daily", "quantity": 1, "unit_price": 400.0}],
    )
    assert [ln["tax_paise"] for ln in mixed["lines"]] == [0, 4800]


_SELLER = {"entity_id": ENTITY, "name": "BV Test Optical Pvt Ltd", "gstin": SHOP_GSTIN, "state_code": "20"}
_ONE_LENS = [{"product_id": LENS, "product_name": "Acme Daily", "quantity": 1, "unit_price": 400.0}]


def _lens_return(db, rid, vendor_id, grn_id, *, bill=None, linked=True):
    """One CONTACT_LENS (12%) at Rs 400 back to `vendor_id`, received on PO
    receipt `grn_id`, with `bill` (stored fields) booked against it; the
    debit note's tax in paise."""
    from api.services.rtv_debit_note import DebitNoteEngine

    lens_line = {"product_id": LENS, "received_qty": 1, "accepted_qty": 1, "rejected_qty": 0, "unit_price": 400.0}
    _seed_receipt(db, grn_id=grn_id, subtype="STANDARD", vendor_id=vendor_id, po_id=f"PO-{grn_id}", items=[lens_line])
    if bill is not None:
        db.vendor_bills.insert_one({"bill_id": f"B-{grn_id}", "vendor_id": vendor_id, "grn_id": grn_id, "status": "OUTSTANDING", **bill})
    if linked:
        db.stock_units.insert_one(
            {"stock_id": f"SU-{grn_id}", "product_id": LENS, "source_type": "GRN", "source_id": grn_id, "store_id": STORE, "rtv_vendor_id": rid}
        )
    vendor = db.vendors.find_one({"vendor_id": vendor_id}, {"_id": 0})
    rtv = {"return_id": rid, "vendor_id": vendor_id, "store_id": STORE}
    res = DebitNoteEngine(db).issue(rtv, vendor, actor="u-acct", seller=_SELLER, lines=_ONE_LENS)
    assert res["ok"], res
    return res["debit_note"]["totals"]["tax_paise"]


def test_r5_the_debit_note_reverses_what_the_bill_booked(world):
    """Panel probe (round 6): the note re-asked the credit rule on TODAY's
    supplier master and the receipt, leaving out what the bill stored. A
    dealer's PO bill booked with credit, the dealer's GSTIN cleared later:
    tax 0 where Rs 48 credit was claimed and never reversed. An unregistered
    supplier's reverse-charge bill (credit claimed): tax 0. And a bill booked
    with credit off or 17(5)-blocked still had 'credit' reversed. The bill
    against the receipt answers from its stored flag (_itc_eligible_bill, the
    register's reader); only with no bill booked does the credit rule decide."""
    db = world["db"]
    # Unregistered Sharma: reverse charge claimed the credit -> reversed.
    assert _lens_return(db, "VR-RCM", SHARMA, "GRN-STD-0610", bill={"itc_eligible": True, "reverse_charge": True}) == 4800
    # Unregistered Sharma, no bill booked yet: the credit rule -> nothing claimable.
    assert _lens_return(db, "VR-URD", SHARMA, "GRN-STD-0611") == 0
    # Registered dealer, credit switched off on the bill / 17(5)-blocked: none to reverse.
    assert _lens_return(db, "VR-OFF", DEALER, "GRN-STD-0612", bill={"itc_eligible": False, "vendor_gstin": DEALER_GSTIN}) == 0
    blocked = {"itc_eligible": True, "itc_blocked": True, "vendor_gstin": DEALER_GSTIN}
    assert _lens_return(db, "VR-BLK", DEALER, "GRN-STD-0613", bill=blocked) == 0
    # A cancelled bill booked nothing: the credit rule decides (a registered dealer's PO receipt).
    assert _lens_return(db, "VR-CXL", DEALER, "GRN-STD-0614", bill={"itc_eligible": False, "status": "CANCELLED"}) == 4800
    # The dealer's registration cancelled after the bill claimed credit: still reversed.
    db.vendors.update_one({"vendor_id": DEALER}, {"$set": {"gstin": None}})
    assert _lens_return(db, "VR-GONE", DEALER, "GRN-STD-0601", bill={"itc_eligible": True, "vendor_gstin": DEALER_GSTIN}) == 4800


def test_r5_a_return_naming_no_units_finds_their_receipt(world):
    """Panel probe (round 6): the New-return form links no units unless it
    was opened from one, and the note then decided by the supplier's GSTIN
    alone -- V-DEALER's walk-in frame, its bill booked with no credit,
    reversed Rs 155 'Input CGST' never claimed (R5). With no unit linked the
    supplier's latest receipt of that product stands in, and its bill
    answers."""
    from api.services.rtv_debit_note import DebitNoteEngine, tally_build_debit_note_xml

    db = world["db"]
    frame_only = [it for it in _no_po_body(world)["items"] if it["product_id"] == FRAME]
    grn_id = _on_the_shelf(world, items=frame_only)
    off = _invoice_body(grn_id, "CASH-77")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    vendor = db.vendors.find_one({"vendor_id": DEALER}, {"_id": 0})
    rtv = {"return_id": "VR-NOLINK", "vendor_id": DEALER, "store_id": STORE}
    line = [{"product_id": FRAME, "product_name": "Acme Aviator", "quantity": 1, "unit_price": 3100.0}]
    res = DebitNoteEngine(db).issue(rtv, vendor, actor="u-acct", seller=_SELLER, lines=line)
    assert res["ok"], res
    assert res["debit_note"]["totals"]["tax_paise"] == 0
    assert "Input CGST" not in tally_build_debit_note_xml(res["debit_note"])
    # The dealer's PO lens, returned with no unit linked: its credit is reversed.
    assert _lens_return(db, "VR-NOLINK-PO", DEALER, "GRN-STD-0620", linked=False) == 4800


def _booked(db, bill_id, number, date, *, vendor=DEALER, gstin=DEALER_GSTIN, tax=310.0, credit=True):
    """A booked bill as both bill doors store it."""
    db.vendor_bills.insert_one(
        {
            "bill_id": bill_id,
            "vendor_id": vendor,
            "vendor_gstin": gstin,
            "bill_number": number,
            "bill_date": date,
            "taxable_amount": 6200.0,
            "tax_amount": tax,
            "itc_eligible": credit,
            "status": "OUTSTANDING",
        }
    )


def _reconcile(rows, period=None):
    body = fin_itc.Gstr2bReconcileBody(rows=[fin_itc.Gstr2bRow(**r) for r in rows], period=period)
    return _run(fin_itc.gstr2b_reconcile(body, current_user=ADMIN))


def test_c7_a_number_reused_next_year_never_answers_last_years_2b_row(world):
    """Panel probe (round 5, problem 3): V-DEALER's INV-10 dated 2025-09-01
    and INV-10 dated 2026-09-01 (Rs 310 credit each) against September 2026's
    2B row INV-10 (Rs 310): matched=2, safe to claim 620 against the portal's
    310. Last year's PO bill INV-9 (credit) and this year's walk-in INV-9 (no
    credit) against one 2B row INV-9: reported both "safe to claim Rs 310" and
    "booked, no credit". A bill is keyed by its financial year, the 2B row by
    its own date's (else its return period's), and one row answers one bill;
    a bill of an earlier period not in this 2B is not this return's risk."""
    db = world["db"]
    _booked(db, "B-10-OLD", "INV-10", "2025-09-01")
    _booked(db, "B-10-NEW", "INV-10", "2026-09-01")
    row = {"gstin": DEALER_GSTIN, "invoice_no": "INV-10", "taxable": 6200.0, "tax": 310.0}
    for rows, period in (([row], "2026-09"), ([{**row, "invoice_date": "2026-09-01"}], None)):
        out = _reconcile(rows, period)
        assert [m["bill_id"] for m in out["matched"]] == ["B-10-NEW"], (period, out)
        assert out["summary"]["itc_safe_to_claim"] == 310.0, out["summary"]
    assert _reconcile([row], "2026-09")["only_in_books"] == []  # last year's: its own 2B
    # No year on either side: still one row, one bill.
    assert _reconcile([row])["summary"]["matched"] == 1
    # Filed late: September 2026's 2B carries last year's INV-10 by its own date.
    late = _reconcile([{**row, "invoice_date": "2025-09-01"}], "2026-09")
    assert [m["bill_id"] for m in late["matched"]] == ["B-10-OLD"], late
    assert [b["bill_id"] for b in late["only_in_books"]] == ["B-10-NEW"]

    _booked(db, "B-9-PO", "INV-9", "2025-09-01")
    _booked(db, "B-9-WALKIN", "INV-9", "2026-09-14", credit=False)
    out = _reconcile([{**row, "invoice_no": "INV-9"}], "2026-09")
    assert [b["bill_id"] for b in out["booked_no_credit"]] == ["B-9-WALKIN"], out
    assert out["matched"] == [] and out["summary"]["itc_safe_to_claim"] == 0.0, out
    assert out["only_in_2b"] == []
    assert [b["bill_id"] for b in out["only_in_books"]] == ["B-10-NEW"]  # this period's, not filed


def test_c7_a_walk_in_bill_answers_only_its_own_2b_row(world):
    """Panel probe (round 5, problem 4): a walk-in bill '1' (Rs 310, no
    credit) booked under a stand-in supplier took that supplier's GSTIN, and
    the no-credit branch marked the supplier's OWN 2B row '1' (Rs 1200, not
    booked) as seen -- the Rs 1200 credit vanished from 'In 2B, not booked -
    book then claim'. A bill reaches the reconciler under the GSTIN it was
    booked with (its own supplier's, or none).

    Round 6 (HIGH): a bill claiming no credit then answered its own 2B row
    only when the tax agreed, so any gap read 'in 2B, not booked - book then
    claim' for a bill already booked (booking it again 409s). The supplier,
    number and year ARE the bill: it answers its row whatever the tax, the
    gap shown beside it."""
    db = world["db"]
    sharma = _on_the_shelf(world, vendor_id=SHARMA, vendor_invoice_no="1")
    _line_bill(sharma, "1", "2026-09-14", vendor=SHARMA)
    assert db.vendor_bills.find_one({"vendor_id": SHARMA})["vendor_gstin"] is None
    theirs = {"gstin": DEALER_GSTIN, "invoice_no": "1", "taxable": 24000.0, "tax": 1200.0}
    out = _reconcile([theirs], "2026-09")
    assert [(r["gstin"], r["tax"]) for r in out["only_in_2b"]] == [(DEALER_GSTIN, 1200.0)]
    assert out["booked_no_credit"] == []
    # Sharma registers later: the bill was booked with no GSTIN and stays so.
    db.vendors.update_one({"vendor_id": SHARMA}, {"$set": {"gstin": "20AABCB9999C1ZO"}})
    out = _reconcile([{**theirs, "gstin": "20AABCB9999C1ZO", "tax": 310.0}], "2026-09")
    assert out["booked_no_credit"] == [] and len(out["only_in_2b"]) == 1
    # A registered dealer's walk-in bill '1', booked with credit switched off,
    # against the dealer's row '1' of another tax: its own line, gap beside it.
    walk_in = _on_the_shelf(world, vendor_invoice_no="1")
    off = _invoice_body(walk_in, "1")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    out = _reconcile([theirs], "2026-09")
    assert [(b["invoice_no"], b["book_tax"], b["portal_tax"]) for b in out["booked_no_credit"]] == [("1", 310.0, 1200.0)]
    assert out["only_in_2b"] == [] and out["summary"]["only_in_2b"] == 0


# ===========================================================================
# G1 / G3 (owner 2026-10-13): GST that is never claimed is cost, and is filed
# ===========================================================================


def test_g1_an_unregistered_dealers_walk_in_is_costed_with_its_gst(world):
    """G1: a walk-in buy whose bill will claim no input credit (THE credit
    rule on its supplier: unregistered Sharma) goes on the shelf at the price
    paid plus its GST at the line's rate (the PO-line rule, HSN first) --
    frame 3100 + 5% = 3255, lens 420 + 5% = 441 --
    and, the latest price paid winning (R3), that full cost is the product's.
    A registered dealer's walk-in claims the GST back: cost before GST."""
    db = world["db"]
    sharma = _on_the_shelf(world, vendor_id=SHARMA)
    cost = {u["product_id"]: u["unit_cost"] for u in db.stock_units.find({"source_id": sharma})}
    assert cost == {FRAME: 3255.0, LENS: 441.0}
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3255.0
    # The receipt keeps the price paid: the bill is matched against it.
    assert [it["unit_price"] for it in db.grns.find_one({"grn_id": sharma})["items"]] == [3100.0, 420.0]

    dealer = _on_the_shelf(world, vendor_invoice_no="CASH-99")
    cost = {u["product_id"]: u["unit_cost"] for u in db.stock_units.find({"source_id": dealer})}
    assert cost == {FRAME: 3100.0, LENS: 420.0}
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3100.0


def test_g1_a_walk_in_bill_booked_without_credit_costs_its_gst(world):
    """G1 at the bill: booking trues the product's cost up to what the bill
    says (the valuation true-up). A registered dealer's walk-in bill booked
    with credit switched off claims none, so its GST is cost: 3100 + 5%.
    Booked with credit, the cost stays before GST."""
    db = world["db"]
    grn_id = _on_the_shelf(world)
    off = _invoice_body(grn_id, "CASH-77")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    frame = db.products.find_one({"product_id": FRAME})
    assert (frame["cost_price"], frame["cost_source"]) == (3255.0, "PURCHASE_INVOICE")

    other = _on_the_shelf(world, vendor_invoice_no="CASH-78")
    _run(pi.create_purchase_invoice(_invoice_body(other, "CASH-78"), current_user=ACCOUNTANT))
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3100.0


def _three_b(world):
    from api.services.gstn_export import to_gstr3b_json

    data = r3b._compute_gstr3b(MONTH, STORE)
    return data, to_gstr3b_json(data)


def test_g3_table_5_reports_the_unregistered_and_no_gst_buys(world):
    """G3: GSTR-3B Table 5 (exempt, nil, non-GST and composition inward
    supplies) carries the purchase from an unregistered supplier (Sharma's
    walk-in bill, Rs 6200) and a bill that charged no GST (exempt, nil-rated
    or a composition dealer's: Rs 6200 at 0%), intra-state here; a
    registered dealer's taxed bill is not there. The portal JSON's inward_sup says the same."""
    db = world["db"]
    sharma = _on_the_shelf(world, vendor_id=SHARMA, vendor_invoice_no="1")
    _line_bill(sharma, "1", "2026-09-14", vendor=SHARMA)
    dealer = _on_the_shelf(world, vendor_invoice_no="CASH-91")
    _line_bill(dealer, "CASH-91", "2026-09-14")
    nil = _on_the_shelf(world, vendor_invoice_no="CASH-92")
    _run(pi.create_purchase_invoice(_invoice_body(nil, "CASH-92", rate=0.0), current_user=ACCOUNTANT))
    data, portal = _three_b(world)
    assert data["inwardExemptNilNonGst"] == {"interState": 0.0, "intraState": 12400.0}
    gst_row = portal["inward_sup"]["isup_details"][0]
    assert (gst_row["ty"], gst_row["inter"], gst_row["intra"]) == ("GST", 0.0, 12400.0)


def test_g3_table_4d_reports_the_gst_paid_and_never_claimed(world):
    """G3: GSTR-3B Table 4(D) (ineligible ITC) carries the GST on every bill
    that claims no input credit -- unregistered Sharma's walk-in bill (Rs 310)
    and a registered dealer's booked with credit switched off (Rs 310) --
    exactly the bills Table 4 leaves out (its Rs 310: the claiming bill). The
    portal JSON's itc_elg.itc_inelg 'others' row says the same."""
    sharma = _on_the_shelf(world, vendor_id=SHARMA, vendor_invoice_no="1")
    _line_bill(sharma, "1", "2026-09-14", vendor=SHARMA)
    off_grn = _on_the_shelf(world, vendor_invoice_no="CASH-93")
    off = _invoice_body(off_grn, "CASH-93")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    claimed = _on_the_shelf(world, vendor_invoice_no="CASH-94")
    _line_bill(claimed, "CASH-94", "2026-09-14")
    data, portal = _three_b(world)
    assert data["itcIneligible"] == {"integratedTax": 0.0, "centralTax": 310.0, "stateTax": 310.0, "cess": 0.0}
    assert data["itcAvailable"]["centralTax"] + data["itcAvailable"]["stateTax"] == 310.0
    others = portal["itc_elg"]["itc_inelg"][1]
    assert (others["ty"], others["camt"], others["samt"]) == ("OTH", 310.0, 310.0)
