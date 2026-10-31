"""S2b (owner 2026-10-23): the server side of the one Receive screen.

1. A delivery challan is received only with its PHOTO (no paper, no stock),
   and the photo is kept with the receipt.
2. "Bought without PO": the bill NUMBER is optional (cash memos), the photo
   is not.
3. A damaged piece the supplier CREDITS closes its order line short (D9); a
   replacement keeps it open.
4. A damaged piece may carry its own photo, kept on the receipt line, the
   quarantined piece and the supplier-return draft.
5. The receipt's quarantined pieces can be listed for their red DO NOT SHELVE
   label.
"""
from api.routers import vendors as vd
from tests.test_receipt_without_po import (  # noqa: F401 - the world fixture
    ACCOUNTANT,
    DEALER,
    FRAME,
    MANAGER,
    STORE,
    _bill_photo,
    _no_po_body,
    world,
)


def _dc_body(world, photo=True, **over):
    body = {
        "grn_subtype": "DELIVERY_CHALLAN",
        "vendor_id": DEALER,
        "dc_number": "DC/S2B/1",
        "dc_date": "2026-09-14",
        "items": [{"product_id": FRAME, "received_qty": 1, "accepted_qty": 1, "rejected_qty": 0, "tallied": True}],
    }
    if photo:
        body.update(
            attachment_file_id=_bill_photo(world),
            attachment_filename="challan.jpg",
            attachment_mime="image/jpeg",
        )
    body.update(over)
    return body


# --- 1. challan photo ---------------------------------------------------------


def test_a_challan_without_its_photo_is_refused(world):
    res = world["as_"](MANAGER).post("/vendors/grn", json=_dc_body(world, photo=False))
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "ATTACHMENT_REQUIRED"
    assert "challan" in res.json()["detail"]["message"].lower()
    assert world["db"].grns.count_documents({}) == 0


def test_a_challan_photo_is_kept_with_the_receipt(world):
    body = _dc_body(world)
    res = world["as_"](MANAGER).post("/vendors/grn", json=body)
    assert res.status_code == 201, res.text
    row = world["db"].grns.find_one({})
    assert row["attachment_file_id"] == body["attachment_file_id"]
    assert row["attachment_filename"] == "challan.jpg"
    assert row["attachment_mime"] == "image/jpeg"
    doc = world["as_"](ACCOUNTANT).get(f"/vendors/grn/{row['grn_id']}/document")
    assert doc.status_code == 200 and doc.content.startswith(b"\x89PNG")


def test_a_challan_photo_of_another_kind_is_refused(world):
    other = world["files"].put(
        content=b"aadhaar", filename="id.png", mime_type="image/png",
        metadata={"kind": "employee_document", "store_id": STORE},
    )
    res = world["as_"](MANAGER).post("/vendors/grn", json=_dc_body(world, attachment_file_id=other))
    assert res.status_code == 400, res.text
    assert world["db"].grns.count_documents({}) == 0


# --- 2. cash memo: number optional, photo required ------------------------------


def test_bought_without_po_needs_no_bill_number(world):
    res = world["as_"](MANAGER).post("/vendors/grn", json=_no_po_body(world, vendor_invoice_no=None))
    assert res.status_code == 201, res.text


def test_bought_without_po_still_needs_the_bill_photo(world):
    res = world["as_"](MANAGER).post(
        "/vendors/grn", json=_no_po_body(world, vendor_invoice_no=None, attachment_file_id=None)
    )
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "ATTACHMENT_REQUIRED"


# --- 3. credit closes the order line short (D9) ---------------------------------


def _order(world, qty=4):
    world["db"].purchase_orders.insert_one(
        {
            "po_id": "PO-S2B",
            "po_number": "PO/S2B/1",
            "vendor_id": DEALER,
            "vendor_gstin": None,
            "store_gstin": None,
            "supply_place_recipient": "20",
            "delivery_store_id": STORE,
            "status": "SENT",
            "updated_at": "2026-09-14T10:00:00+00:00",
            "items": [
                {
                    "po_item_id": "L1",
                    "product_id": FRAME,
                    "product_name": "Test frame",
                    "quantity": qty,
                    "ordered_qty": qty,
                    "unit_price": 3000.0,
                    "tax_rate": 12,
                }
            ],
        }
    )


def _receive_damaged(world, choice, arrived=3, damaged=1, photo=None):
    item = {
        "po_item_id": "L1",
        "product_id": FRAME,
        "received_qty": arrived,
        "accepted_qty": arrived - damaged,
        "rejected_qty": damaged,
        "rejection_reason": "Scratched lens",
        "rejection_disposition": choice,
        "tallied": True,
    }
    if photo:
        item["rejection_photo_file_id"] = photo
    http = world["as_"](MANAGER)
    made = http.post(
        "/vendors/grn",
        json={
            "po_id": "PO-S2B",
            "vendor_invoice_no": f"INV-{choice}",
            "vendor_invoice_date": "2026-09-14",
            "attachment_file_id": _bill_photo(world),
            "items": [item],
        },
    )
    assert made.status_code == 201, made.text
    gid = made.json()["grn_id"]
    acc = http.post(f"/vendors/grn/{gid}/accept")
    assert acc.status_code == 200, acc.text
    return gid, acc.json()


def test_a_credit_closes_the_order_line_short(world):
    _order(world, qty=4)
    gid, acc = _receive_damaged(world, "credit_note")
    po = world["db"].purchase_orders.find_one({"po_id": "PO-S2B"})
    line = po["items"][0]
    # 4 ordered, 2 good, 1 credited: 1 still due, the credited one is not.
    assert line["cancelled_qty"] == 1
    assert line["quantity"] == 3 and line["ordered_qty"] == 3
    assert line["received_qty"] == 2
    assert po["status"] == "PARTIALLY_RECEIVED" == acc["po_status"]
    assert po["credit_closed_grns"] == [gid]
    assert any(ev.get("kind") == "line_cancelled" and "credit" in ev["detail"].lower() for ev in po["history"])
    # The money follows the quantity (the cancel's own re-pricing).
    assert po["subtotal"] == 9000.0


def test_a_credit_that_covers_the_rest_receives_the_order(world):
    _order(world, qty=3)
    _receive_damaged(world, "credit_note")
    po = world["db"].purchase_orders.find_one({"po_id": "PO-S2B"})
    assert po["status"] == "RECEIVED"
    assert po["items"][0]["quantity"] == 2


def test_a_replacement_keeps_the_line_open(world):
    _order(world, qty=3)
    _receive_damaged(world, "replacement")
    po = world["db"].purchase_orders.find_one({"po_id": "PO-S2B"})
    assert po["status"] == "PARTIALLY_RECEIVED"
    assert po["items"][0]["quantity"] == 3
    assert not po["items"][0].get("cancelled_qty")


def test_a_credit_closes_the_line_once(world):
    _order(world, qty=4)
    gid, _ = _receive_damaged(world, "credit_note")
    # Every later re-derivation (another receipt, a retry) finds it done.
    for _ in range(2):
        vd.refresh_po_received(vd.get_purchase_order_repository(), "PO-S2B", gid)
    po = world["db"].purchase_orders.find_one({"po_id": "PO-S2B"})
    assert po["items"][0]["cancelled_qty"] == 1
    assert po["items"][0]["quantity"] == 3


def test_a_credit_missed_by_a_failed_write_closes_on_the_next_refresh(world):
    _order(world, qty=4)
    gid, _ = _receive_damaged(world, "credit_note")
    db = world["db"]
    # As if the accept's order write had been lost: the receipt is accepted,
    # the order still asks for 4.
    db.purchase_orders.update_one(
        {"po_id": "PO-S2B"},
        {"$set": {"items.0.quantity": 4, "items.0.ordered_qty": 4, "items.0.cancelled_qty": 0, "credit_closed_grns": []}},
    )
    vd.refresh_po_received(vd.get_purchase_order_repository(), "PO-S2B", None)
    po = db.purchase_orders.find_one({"po_id": "PO-S2B"})
    assert po["items"][0]["quantity"] == 3
    assert po["credit_closed_grns"] == [gid]


# --- 4. damaged-piece photo -------------------------------------------------------


def test_a_damaged_piece_keeps_its_photo(world):
    _order(world, qty=3)
    photo = _bill_photo(world)
    gid, _ = _receive_damaged(world, "credit_note", photo=photo)
    db = world["db"]
    assert db.grns.find_one({"grn_id": gid})["items"][0]["rejection_photo_file_id"] == photo
    unit = db.stock_units.find_one({"status": "QUARANTINED"})
    assert unit["quarantine_photo_file_id"] == photo
    draft = db.vendor_returns.find_one({})
    assert draft["items"][0]["photo_file_id"] == photo
    doc = world["as_"](ACCOUNTANT).get(f"/vendors/grn/{gid}/document", params={"line": 0})
    assert doc.status_code == 200 and doc.content.startswith(b"\x89PNG")


def test_a_forged_damaged_photo_is_refused(world):
    _order(world, qty=3)
    res = world["as_"](MANAGER).post(
        "/vendors/grn",
        json={
            "po_id": "PO-S2B",
            "vendor_invoice_no": "INV-F",
            "attachment_file_id": _bill_photo(world),
            "items": [
                {
                    "product_id": FRAME, "received_qty": 1, "accepted_qty": 0, "rejected_qty": 1,
                    "rejection_reason": "Broken hinge", "rejection_disposition": "credit_note",
                    "tallied": True, "rejection_photo_file_id": "forged-id",
                }
            ],
        },
    )
    assert res.status_code == 400, res.text
    assert res.json()["detail"]["code"] == "ATTACHMENT_INVALID"
    assert world["db"].grns.count_documents({}) == 0


# --- 5. DO NOT SHELVE labels ---------------------------------------------------------


def test_the_receipts_quarantined_pieces_are_listed_for_their_red_label(world):
    _order(world, qty=3)
    gid, _ = _receive_damaged(world, "credit_note")
    http = world["as_"](MANAGER)
    red = http.get("/inventory/units", params={"grn_id": gid, "rejected": "true"}).json()
    assert red["total"] == 1
    unit = red["units"][0]
    assert unit["status"] == "QUARANTINED" and unit["barcode"]
    assert unit["quarantine_label_printed"] is False
    # The ordinary receipt list still shows only the pieces put on the shelf.
    shelf = http.get("/inventory/units", params={"grn_id": gid}).json()
    assert unit["stock_id"] not in [u["stock_id"] for u in shelf["units"]]
