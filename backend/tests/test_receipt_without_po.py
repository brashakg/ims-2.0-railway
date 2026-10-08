"""C7 / D14 -- goods bought WITHOUT a purchase order (walk-in / local dealer).

Owner audit row C7: the only way to put a cash purchase from a local dealer on
the shelf is to call it a "Delivery Challan" on the older receiving screen. It
asks for no price and no bill photo, the units land with no cost at all, and
accounts get no task. Owner ruling D14 (2026-09-29): a "Bought without PO"
receipt -- supplier or walk-in dealer name, items, quantities, each item's
cost, the bill number/date if there is one, the bill photo, expiry -- that puts
the units on the shelf through the SAME receive/accept door as a PO receipt,
and books NO input tax credit: GSTR-3B and the ITC register never count it.
The receipt shows in the receipts list and on Movements labelled "Bought
without PO". (Who may receive -- managers only, ruling 2026-09-28 -- is the
shared receiving gate, which #1165 narrows for every kind of receipt at once.)

THE CONTRACT THESE TESTS PIN:
  * POST /vendors/grn with grn_subtype "NO_PO": no po_id; vendor_id (a supplier
    on file) OR dealer_name (typed walk-in dealer); vendor_invoice_no /
    vendor_invoice_date optional; every line carries unit_price > 0 (the cost);
    expiry_date per line as on any receipt; the bill photo
    (attachment_file_id) is required, by the same document gate as a PO
    receipt. Stored as grn_subtype "NO_PO" (+ dealer_name). A cost typed on a
    PO receipt's line is dropped: that receipt is costed at the order's price.
  * POST /vendors/grn/{id}/accept is the ONE minting door: units are
    source_type GRN, carry the line's unit_price as unit_cost/cost_price and
    the line's expiry; accounts get a "book the bill" task.
  * A bill booked against it -- line-detail purchase-invoice door OR the
    header-only AP bill door -- is stored itc_eligible False whatever the client
    sent, so the ITC register, /gst/summary and GSTR-3B Table 4 never count it.
  * GET /vendors/grn?grn_subtype=NO_PO lists only these receipts, naming the
    dealer; the Movements ledger labels them "Bought without PO".

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
    who = {"user": MANAGER}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    http = TestClient(app)

    def as_(user):
        who["user"] = user
        return http

    return {"db": db, "files": files, "as_": as_}


def _bill_photo(world, content=None):
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
            "store_id": STORE,
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


def test_d14_the_itc_rule_itself():
    """One rule for both bill doors: no credit on a no-PO receipt's bill, and
    an operator's own "no credit" on any other bill still stands."""
    from api.services.org_validation import itc_claimable

    reg = "20AACCD5678E1ZX"
    assert itc_claimable(reg, False, True, {"grn_subtype": "NO_PO"}) is False
    assert itc_claimable(reg, True, True, {"grn_subtype": "NO_PO"}) is False
    assert itc_claimable(reg, False, True, {"grn_subtype": "STANDARD"}) is True
    assert itc_claimable(reg) is True
    assert itc_claimable(reg, False, False, {"grn_subtype": "STANDARD"}) is False


def test_c7_no_po_receipt_needs_the_bill_photo(world):
    body = _no_po_body(world)
    del body["attachment_file_id"]
    res = world["as_"](MANAGER).post("/vendors/grn", json=body)
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "ATTACHMENT_REQUIRED"
    assert world["db"].grns.count_documents({}) == 0


def test_c7_walk_in_dealer_by_name_and_someone_must_be_named(world):
    http = world["as_"](MANAGER)
    by_name = _no_po_body(world, vendor_id=None, dealer_name="Sharma Optical, Bank More")
    res = http.post("/vendors/grn", json=by_name)
    assert res.status_code == 201, res.text
    grn = world["db"].grns.find_one({"grn_id": res.json()["grn_id"]}, {"_id": 0})
    assert grn["dealer_name"] == "Sharma Optical, Bank More"
    assert not grn.get("vendor_id")

    nobody = _no_po_body(world, vendor_id=None)
    res = http.post("/vendors/grn", json=nobody)
    assert res.status_code == 422, res.text


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
    """The panel's probe body: a walk-in dealer by name only, no vendor_id."""
    return _no_po_body(world, vendor_id=None, dealer_name="Sharma Optical", **over)


def test_c7_the_same_walk_in_bill_cannot_go_on_the_shelf_twice(world):
    """Panel probe: the identical dealer-only body posted twice (a retried
    post after a lost response) gave 201 + 201, and accepting both minted 10
    units for a 5-unit bill -- the vendor-keyed guard has no vendor to key on."""
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
    # Same dealer typed differently, same bill number, a fresh upload.
    twin = _no_po_body(world, vendor_id=None, dealer_name="sharma  optical.")
    assert http.post("/vendors/grn", json=twin).status_code == 409
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

    real = gc._find_duplicate_no_po_grn
    calls = {"n": 0}

    def racing(*a, **kw):  # the pre-insert look-up misses the rival
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*a, **kw)

    monkeypatch.setattr(gc, "_find_duplicate_no_po_grn", racing)
    raced = http.post("/vendors/grn", json=body)
    assert raced.status_code == 409, raced.text
    assert world["db"].grns.count_documents({}) == 1


def test_c7_the_same_bill_photo_uploaded_again_is_the_same_bill(world):
    """Panel probe: a dealer's cash bill with NO number, posted again after a
    page reload with the same image uploaded again (a new file id): 201, 201
    -- the guard compared file ids only, and the dealer + number check has no
    number to go on. The upload door's sha256 is the bill's identity."""
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


def test_c7_a_supplier_picked_or_typed_by_name_is_the_same_seller(world):
    """Panel probe: receipt 1 picked the supplier from the list, receipt 2
    typed the same supplier's name; same bill number, fresh photos: 201, 201
    in either order -- 10 units on the shelf for a 5-unit bill."""
    http = world["as_"](MANAGER)
    assert http.post("/vendors/grn", json=_no_po_body(world)).status_code == 201
    typed = _no_po_body(world, vendor_id=None, dealer_name="Bank More Optical")
    assert http.post("/vendors/grn", json=typed).status_code == 409
    # The other order, with the legal name typed.
    typed = _no_po_body(world, vendor_id=None, dealer_name="bank more optical traders", vendor_invoice_no="CASH-88")
    assert http.post("/vendors/grn", json=typed).status_code == 201
    picked = _no_po_body(world, vendor_invoice_no="CASH-88")
    assert http.post("/vendors/grn", json=picked).status_code == 409
    # Another dealer's bill that happens to carry the same number is another purchase.
    other = _no_po_body(world, vendor_id=None, dealer_name="Sharma Optical")
    assert http.post("/vendors/grn", json=other).status_code == 201
    assert world["db"].grns.count_documents({}) == 3


def test_c7_a_long_walk_in_history_never_hides_a_recent_bill(world):
    """Panel probe: the guard read the shop's first 500 walk-in receipts with
    no sort -- the OLDEST -- so past 500 the same dealer + bill number posted
    twice gave 201, 201."""
    world["db"].grns.insert_many(
        [
            {
                "grn_id": f"GRN-OLD-{n:04d}",
                "grn_subtype": "NO_PO",
                "store_id": STORE,
                "status": "ACCEPTED",
                "dealer_name": f"Old Dealer {n}",
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


def _invoice_body(grn_id, number, *, rate=5.0):
    return pi.PurchaseInvoiceCreate(
        vendor_id=DEALER,
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


def test_d14_line_bill_for_a_no_po_receipt_claims_no_itc(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0001", subtype="STANDARD", po_id="PO-1")
    _seed_receipt(db, grn_id="GRN-NOPO-0002", subtype="NO_PO")

    _run(pi.create_purchase_invoice(_invoice_body("GRN-STD-0001", "TAX-1"), current_user=ACCOUNTANT))
    # The client says nothing about ITC (the body default is True).
    _run(pi.create_purchase_invoice(_invoice_body("GRN-NOPO-0002", "CASH-2"), current_user=ACCOUNTANT))

    std = db.vendor_bills.find_one({"grn_id": "GRN-STD-0001"}, {"_id": 0})
    walk_in = db.vendor_bills.find_one({"grn_id": "GRN-NOPO-0002"}, {"_id": 0})
    assert std["tax_amount"] == 310.0 and walk_in["tax_amount"] == 310.0
    assert walk_in["itc_eligible"] is False
    # Control: the PO receipt's bill IS counted, so a zero is not a blind reader.
    assert _itc_everywhere() == (310.0, 310.0, 310.0)


def test_d14_client_cannot_switch_itc_back_on(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0003", subtype="NO_PO")
    body = _invoice_body("GRN-NOPO-0003", "CASH-3")
    body.itc_eligible = True
    _run(pi.create_purchase_invoice(body, current_user=ACCOUNTANT))
    bill = db.vendor_bills.find_one({"grn_id": "GRN-NOPO-0003"}, {"_id": 0})
    assert bill["itc_eligible"] is False
    assert _itc_everywhere() == (0.0, 0.0, 0.0)


def test_d14_header_only_bill_door_claims_no_itc_either(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0004", subtype="NO_PO")
    bill = vd.VendorBillCreate(
        bill_number="CASH-4",
        bill_date="2026-09-14",
        taxable_amount=6200.0,
        tax_amount=310.0,
        total_amount=6510.0,
        grn_id="GRN-NOPO-0004",
    )
    _run(vd.create_vendor_bill(DEALER, bill, current_user=ACCOUNTANT))
    stored = db.vendor_bills.find_one({"grn_id": "GRN-NOPO-0004"}, {"_id": 0})
    assert stored is not None
    assert stored["itc_eligible"] is False
    reg = _run(fin_itc.itc_register(period=MONTH, entity_id=None, current_user=ADMIN))
    summary = _run(fin_gst.get_gst_summary(month=9, year=2026, current_user=ADMIN))
    assert float(reg.get("total_itc") or 0) == 0.0
    assert float(summary.get("gst_input_credit") or 0) == 0.0


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
    """Panel probe: the GSTR-2B reconcile fed EVERY vendor bill to the matcher,
    so a walk-in bill (stored itc_eligible False, tax 310) whose dealer filed it
    read "Matched (claim) Rs 310" -- and with no 2B row, "ITC at risk Rs 310"
    -- while the register, /gst/summary and GSTR-3B all said 0."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0301", subtype="STANDARD", po_id="PO-3")
    _seed_receipt(db, grn_id="GRN-NOPO-0302", subtype="NO_PO")
    _run(pi.create_purchase_invoice(_invoice_body("GRN-STD-0301", "TAX-301"), current_user=ACCOUNTANT))
    _run(pi.create_purchase_invoice(_invoice_body("GRN-NOPO-0302", "CASH-302"), current_user=ACCOUNTANT))
    portal = [
        fin_itc.Gstr2bRow(gstin=DEALER_GSTIN, invoice_no=n, taxable=6200.0, tax=310.0)
        for n in ("TAX-301", "CASH-302")
    ]
    filed = _run(fin_itc.gstr2b_reconcile(fin_itc.Gstr2bReconcileBody(rows=portal), current_user=ADMIN))
    # Control: the PO receipt's bill matches, so the walk-in's absence is the rule.
    assert [r["invoice_no"] for r in filed["matched"]] == ["TAX-301"]
    assert filed["summary"]["itc_safe_to_claim"] == 310.0
    assert filed["summary"]["total_book_itc"] == _itc_everywhere()[0] == 310.0
    unfiled = _run(fin_itc.gstr2b_reconcile(fin_itc.Gstr2bReconcileBody(rows=[]), current_user=ADMIN))
    assert [r["invoice_no"] for r in unfiled["only_in_books"]] == ["TAX-301"]
    assert unfiled["summary"]["itc_at_risk"] == 310.0


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
    assert {ln["product_id"]: ln["unit_price"] for ln in draft["lines"]} == {
        FRAME: 3100.0,
        LENS: 420.0,
    }
    assert draft["taxable_total"] == 2 * 3100.0 + 3 * 420.0


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
    _seed_receipt(
        db,
        grn_id="GRN-NOPO-0006",
        subtype="NO_PO",
        vendor_id=None,
        dealer_name="Sharma Optical, Bank More",
    )
    res = world["as_"](MANAGER).get("/vendors/grn", params={"grn_subtype": "NO_PO"})
    assert res.status_code == 200, res.text
    rows = res.json()["grns"]
    assert [r["grn_id"] for r in rows] == ["GRN-NOPO-0006"]
    assert rows[0]["vendor_name"] == "Sharma Optical, Bank More"


def test_c7_counter_staff_never_read_what_was_paid(world):
    """Panel probe: a no-PO receipt stores the cost per line and the dealer's
    name, and both GRN reads were open to any logged-in user -- SALES_STAFF
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
    mine = world["as_"](MANAGER).get(f"/vendors/grn/{grn_id}")
    assert mine.status_code == 200
    assert [ln["unit_price"] for ln in mine.json()["items"]] == [3100.0, 420.0]
    other_shop = {**MANAGER, "store_ids": ["BV-OTHER-01"], "active_store_id": "BV-OTHER-01"}
    assert world["as_"](other_shop).get(f"/vendors/grn/{grn_id}").status_code == 404


def _walk_in_on_the_shelf(world):
    """The panel's walk-in body (dealer typed by name) received and accepted;
    returns the receipt id and one minted frame unit's barcode."""
    http = world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_walk_in_body(world))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    assert http.post(f"/vendors/grn/{grn_id}/accept").status_code == 200
    return grn_id, world["db"].stock_units.find_one({"product_id": FRAME})["barcode"]


def test_c7_the_barcode_trace_shows_no_counter_what_was_paid(world):
    """Panel probe: GET /inventory/barcode/{barcode}/trace was open to any
    logged-in user with no store check and returned the raw receipt -- a
    SALES_STAFF of another shop read the dealer 'Sharma Optical', bill
    CASH-77, unit_price [3100, 420] and the unit's cost 3100."""
    grn_id, barcode = _walk_in_on_the_shelf(world)
    url = f"/inventory/barcode/{barcode}/trace"
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF"):
        for shop in (STORE, "BV-OTHER-99"):
            counter = {"user_id": "u-counter", "roles": [role], "store_ids": [shop], "active_store_id": shop}
            res = world["as_"](counter).get(url)
            assert res.status_code == 403, (role, shop, res.text)
            assert "3100" not in res.text and "Sharma" not in res.text
    other_shop = {**MANAGER, "store_ids": ["BV-OTHER-99"], "active_store_id": "BV-OTHER-99"}
    hidden = world["as_"](other_shop).get(url)
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["stock_unit"] is None and hidden.json()["purchase"] == []
    assert "3100" not in hidden.text and "Sharma" not in hidden.text
    mine = world["as_"](MANAGER).get(url).json()
    assert mine["purchase"][0]["grn_id"] == grn_id
    assert mine["stock_unit"]["unit_cost"] == 3100.0


def test_c7_the_policy_table_answers_as_the_receipt_readers_do(world):
    """Panel finding: the two GRN reads were gated to the purchase roles while
    their rbac_policy rows still said AUTHENTICATED, and no test compared the
    two. Every role asks each route that returns a receipt; the table must
    give the handler's answer and mark the route store-scoped."""
    from api.services import rbac_policy

    grn_id, barcode = _walk_in_on_the_shelf(world)
    for url in ("/vendors/grn", f"/vendors/grn/{grn_id}", f"/inventory/barcode/{barcode}/trace"):
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
