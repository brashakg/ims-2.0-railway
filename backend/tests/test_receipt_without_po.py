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
    who = {"user": MANAGER}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    http = TestClient(app)

    def as_(user):
        who["user"] = user
        return http

    return {"db": db, "files": files, "as_": as_}


def _bill_photo(world):
    """A bill photo exactly as POST /vendors/grn/upload-doc stores it."""
    return world["files"].put(
        content=b"\x89PNG cash memo",
        filename="cash-memo.png",
        mime_type="image/png",
        metadata={"kind": "grn_document", "uploaded_by": "u-mgr", "store_id": STORE},
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
    from api.services import ap_engine

    assert ap_engine.itc_eligible({"grn_subtype": "NO_PO"}, True) is False
    assert ap_engine.itc_eligible({"grn_subtype": "STANDARD"}, True) is True
    assert ap_engine.itc_eligible(None) is True
    assert ap_engine.itc_eligible({"grn_subtype": "STANDARD"}, False) is False


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


def test_c7_movements_label_bought_without_po(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0007", subtype="STANDARD", po_id="PO-7", po_number="PO/7")
    _seed_receipt(db, grn_id="GRN-NOPO-0008", subtype="NO_PO")
    events = mv._collect_received_events(db, STORE, "2026-09-01T00:00:00", None)
    by_ref = {e["ref_id"]: e for e in events}
    assert set(by_ref) == {"GRN-STD-0007", "GRN-NOPO-0008"}
    assert "Bought without PO" in by_ref["GRN-NOPO-0008"]["detail"]
    assert "Bought without PO" not in by_ref["GRN-STD-0007"]["detail"]
