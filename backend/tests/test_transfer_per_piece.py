"""
IMS 2.0 - S3: transfers tracked piece by piece
==============================================
Owner-approved design 2026-10-23 (boards Transfer + TransferBox), with the CA
answers of 2026-10-23 overriding the board's "delivery challan":

  * ask another shop for stock: quantity capped at what is FREE there
    (reserved pieces cannot be asked for); the online shop is no destination
  * AREA MANAGER or above approves before packing; a reject needs a reason
  * pack = scan pieces OUT; ship marks EXACTLY the packed barcodes (it used
    to mark whichever AVAILABLE frame the database returned first)
  * courier + tracking no. at ship; cancel only before shipping
  * receive = scan pieces IN; damaged-in-the-box pieces go to QUARANTINE at
    the receiving shop
  * a piece scanned out but never in goes on "Missing on the way" until it is
    found or written off by an admin (a partial receipt never strands it)
  * between two GSTINs: the tax invoice at cost is booked in the DISPATCH
    month (its own date), and a short delivery is the value received plus a
    CREDIT NOTE for the difference -- exact to the paisa

Runs on mongomock (no local mongod needed).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

mongomock = pytest.importorskip("mongomock")

from fastapi import HTTPException  # noqa: E402

from api.utils.ist import IST  # noqa: E402

MAY_2026 = datetime(2026, 5, 1, 11, 0, tzinfo=IST)
GSTIN_Z_JH = "20AAACZ1111A1ZH"  # company Z, Jharkhand (both Dhanbad shops)
GSTIN_Z_MH = "27AAACZ1111A1Z3"  # company Z, Maharashtra (Pune)
GSTIN_Y_JH = "20AAACY2222B1ZA"  # company Y, Jharkhand (Bokaro, a separate firm)


def _shop(sid, code, name, entity, gstin, state):
    return {
        "store_id": sid, "store_name": name, "name": name, "entity_id": entity,
        "gstin": gstin, "state": state, "state_code": gstin[:2], "address": f"{name} Road",
        "city": name.split()[-1], "pincode": "826001", "store_type": "PHYSICAL",
        "invoice_code": code,
    }


SHOPS = [
    _shop("ST-DHN-1", "DHN01", "Hirapur Dhanbad", "ENT-Z", GSTIN_Z_JH, "Jharkhand"),
    _shop("ST-DHN-2", "DHN02", "Bank More Dhanbad", "ENT-Z", GSTIN_Z_JH, "Jharkhand"),
    _shop("ST-BOK-1", "BOK01", "Sector 4 Bokaro", "ENT-Y", GSTIN_Y_JH, "Jharkhand"),
    _shop("ST-PUN-1", "PUN01", "FC Road Pune", "ENT-Z", GSTIN_Z_MH, "Maharashtra"),
]
NAMES = {s["store_id"]: s["store_name"] for s in SHOPS}

ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"],
         "store_ids": [], "active_store_id": None}
AREA = {"user_id": "u-am", "username": "suresh", "roles": ["AREA_MANAGER"],
        "store_ids": [s["store_id"] for s in SHOPS], "active_store_id": "ST-DHN-1"}
MGR_DHN = {"user_id": "u-dhn", "username": "rohit", "roles": ["STORE_MANAGER"],
           "store_ids": ["ST-DHN-1"], "active_store_id": "ST-DHN-1"}
MGR_DHN2 = {"user_id": "u-dhn2", "username": "anita", "roles": ["STORE_MANAGER"],
            "store_ids": ["ST-DHN-2"], "active_store_id": "ST-DHN-2"}
MGR_BOK = {"user_id": "u-bok", "username": "vijay", "roles": ["STORE_MANAGER"],
           "store_ids": ["ST-BOK-1"], "active_store_id": "ST-BOK-1"}


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return self.db[name]


@pytest.fixture
def world(monkeypatch):
    from api import dependencies as deps
    from api.routers import reports, transfers
    from database.repositories import order_repository as repo_module

    handle = mongomock.MongoClient()["ims_s3"]
    handle["entities"].insert_many([
        {"entity_id": "ENT-Z", "legal_name": "Zed Opticals Pvt Ltd", "gstins": [
            {"gstin": GSTIN_Z_JH, "state_code": "20", "is_primary": True},
            {"gstin": GSTIN_Z_MH, "state_code": "27"}]},
        {"entity_id": "ENT-Y", "legal_name": "Wye Vision",
         "gstins": [{"gstin": GSTIN_Y_JH, "state_code": "20"}]},
    ])
    handle["stores"].insert_many([dict(s) for s in SHOPS])
    handle["products"].insert_one({
        "product_id": "P-1", "sku": "FR-1", "name": "Carrera Frame", "category": "FRAME",
        "hsn_code": "900311", "cost_price": 2000.0,
    })
    # Three priced pieces whose average is not a whole paisa (1850.00333...),
    # then seven more at 1850, and one RESERVED for a customer.
    costs = [1850.0, 1850.0, 1850.01] + [1850.0] * 7
    handle["stock_units"].insert_many([
        {"stock_id": f"SU-{n}", "product_id": "P-1", "store_id": "ST-DHN-1",
         "status": "AVAILABLE", "barcode": f"BVQX{n:03d}", "unit_cost": c}
        for n, c in enumerate(costs, start=1)
    ])
    handle["stock_units"].insert_one(
        {"stock_id": "SU-R", "product_id": "P-1", "store_id": "ST-DHN-1",
         "status": "RESERVED", "barcode": "BVQXRES", "unit_cost": 1850.0}
    )
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(handle))
    monkeypatch.setattr(reports, "_get_raw_db", lambda: handle)
    monkeypatch.setattr(transfers, "_writeback_units_left", lambda *a, **k: None)
    monkeypatch.setattr(repo_module, "now_ist", lambda: MAY_2026)
    transfers.STOCK_TRANSFERS.clear()
    return handle


def _run(coro):
    return asyncio.run(coro)


def _status(exc_info):
    return getattr(exc_info.value, "status_code", None)


def _ask(qty=3, frm="ST-DHN-1", to="ST-BOK-1", user=ADMIN):
    from api.routers import transfers

    body = transfers.TransferInput(
        transfer_type="store_to_store",
        from_location_id=frm, from_location_name=NAMES[frm],
        to_location_id=to, to_location_name=NAMES[to],
        items=[transfers.TransferItemInput(product_id="P-1", sku="FR-1",
                                           product_name="Carrera Frame",
                                           quantity_requested=qty)],
    )
    return _run(transfers.create_transfer(body, user))["transfer"]


def _pack(tid, codes, user=ADMIN):
    from api.routers import transfers

    return _run(transfers.pack_transfer(tid, transfers.TransferPack(barcodes=codes), user))["transfer"]


def _ship(tid, user=ADMIN, courier="DTDC", tracking="D41092277"):
    from api.routers import transfers

    return _run(transfers.ship_transfer(tid, tracking, None, courier, False, user))["transfer"]


def _scan_in(tid, code, user=ADMIN, damaged=False, reason=None):
    from api.routers import transfers

    body = transfers.TransferPieceReceive(barcode=code, damaged=damaged, damage_reason=reason)
    return _run(transfers.receive_transfer(tid, body, user))["transfer"]


def _complete(tid, user=ADMIN):
    from api.routers import transfers

    return _run(transfers.complete_transfer(tid, None, user))["transfer"]


def _unit(world, sid):
    return world["stock_units"].find_one({"stock_id": sid})


def _shipped_box(world, to="ST-BOK-1", codes=("BVQX001", "BVQX002", "BVQX003")):
    t = _ask(len(codes), to=to)
    _pack(t["id"], list(codes))
    return _ship(t["id"])


# ---------------------------------------------------------------------------
# Ask another shop: capped at FREE stock there
# ---------------------------------------------------------------------------


def test_ask_is_capped_at_free_stock_reserved_does_not_count(world):
    with pytest.raises(HTTPException) as exc:
        _ask(11)  # 10 AVAILABLE + 1 RESERVED: only 10 are free
    assert _status(exc) == 409
    assert "10" in str(exc.value.detail) and "reserved" in str(exc.value.detail).lower()
    assert _ask(10)["status"] == "approved"


def test_free_stock_endpoint_counts_free_and_reserved(world):
    from api.routers import transfers

    got = _run(transfers.free_stock("P-1", "ST-DHN-1", MGR_BOK))
    assert got == {"product_id": "P-1", "store_id": "ST-DHN-1", "free": 10, "reserved": 1}


def test_the_asking_shop_may_ask_and_it_waits_for_an_area_manager(world):
    from api.routers import transfers

    t = _ask(2, user=MGR_BOK)  # Bokaro asks Dhanbad for stock
    assert t["status"] == "pending_approval"
    # The asking store manager cannot approve; the area manager can.
    with pytest.raises(HTTPException) as exc:
        _run(transfers.approve_transfer(t["id"], transfers.TransferApproval(approved=True), MGR_DHN))
    assert _status(exc) == 403
    ok = _run(transfers.approve_transfer(t["id"], transfers.TransferApproval(approved=True), AREA))
    assert ok["transfer"]["status"] == "approved"


def test_a_shop_outside_both_ends_cannot_ask(world):
    with pytest.raises(HTTPException) as exc:
        _ask(1, frm="ST-DHN-1", to="ST-BOK-1", user=MGR_DHN2)
    assert _status(exc) == 403


def test_reject_needs_a_reason(world):
    from api.routers import transfers

    t = _ask(1, user=MGR_BOK)
    with pytest.raises(HTTPException) as exc:
        _run(transfers.approve_transfer(
            t["id"], transfers.TransferApproval(approved=False, rejection_reason="  "), AREA))
    assert _status(exc) == 400
    done = _run(transfers.approve_transfer(
        t["id"], transfers.TransferApproval(approved=False, rejection_reason="Pune needs it"), AREA))
    assert done["transfer"]["status"] == "rejected"
    assert done["transfer"]["rejection_reason"] == "Pune needs it"


def test_packing_waits_for_approval(world):
    t = _ask(1, user=MGR_BOK)
    with pytest.raises(HTTPException) as exc:
        _pack(t["id"], ["BVQX001"], user=MGR_DHN)
    assert _status(exc) == 400


# ---------------------------------------------------------------------------
# Pack = scan pieces OUT
# ---------------------------------------------------------------------------


def test_pack_records_the_scanned_pieces(world):
    t = _ask(2)
    got = _pack(t["id"], ["BVQX005", "bvqx007"], user=MGR_DHN)
    line = got["items"][0]
    assert line["packed_stock_ids"] == ["SU-5", "SU-7"]
    assert got["status"] == "packed"
    # Scanning again replaces the box's list; an empty list unpacks it.
    assert _pack(t["id"], [], user=MGR_DHN)["status"] == "approved"


@pytest.mark.parametrize("codes,why", [
    (["NOPE"], "not found"),
    (["BVQXRES"], "free"),            # reserved for a customer
    (["BVQX001", "BVQX001"], "twice"),
    (["BVQX001", "BVQX002", "BVQX003"], "more"),  # asked for 2
])
def test_pack_refuses_a_wrong_piece(world, codes, why):
    t = _ask(2)
    with pytest.raises(HTTPException) as exc:
        _pack(t["id"], codes)
    assert _status(exc) == 400
    assert why in str(exc.value.detail).lower()


def test_pack_refuses_a_piece_of_another_shop(world):
    world["stock_units"].insert_one({"stock_id": "SU-B", "product_id": "P-1", "store_id": "ST-BOK-1",
                                     "status": "AVAILABLE", "barcode": "BVQXBOK", "unit_cost": 1850.0})
    t = _ask(1)
    with pytest.raises(HTTPException) as exc:
        _pack(t["id"], ["BVQXBOK"])
    assert _status(exc) == 400


def test_a_piece_packed_in_one_box_cannot_go_in_another(world):
    a, b = _ask(1), _ask(1, to="ST-DHN-2")
    _pack(a["id"], ["BVQX004"])
    with pytest.raises(HTTPException) as exc:
        _pack(b["id"], ["BVQX004"])
    assert _status(exc) == 400 and "another box" in str(exc.value.detail).lower()


# ---------------------------------------------------------------------------
# Ship marks EXACTLY the packed barcodes
# ---------------------------------------------------------------------------


def test_ship_marks_the_packed_pieces_not_a_random_frame(world):
    t = _ask(2, to="ST-DHN-2")
    _pack(t["id"], ["BVQX009", "BVQX010"])
    shipped = _ship(t["id"], user=MGR_DHN)
    assert shipped["status"] == "in_transit"
    assert shipped["courier_name"] == "DTDC" and shipped["tracking_number"] == "D41092277"
    assert _unit(world, "SU-9")["status"] == "TRANSFERRED"
    assert _unit(world, "SU-10")["status"] == "TRANSFERRED"
    # The first frames in the database stay on the shelf.
    assert _unit(world, "SU-1")["status"] == "AVAILABLE"
    assert _unit(world, "SU-2")["status"] == "AVAILABLE"
    assert shipped["items"][0]["shipped_stock_ids"] == ["SU-9", "SU-10"]


def test_ship_needs_a_packed_box_and_courier_details(world):
    t = _ask(1, to="ST-DHN-2")
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409
    _pack(t["id"], ["BVQX004"])
    for courier, tracking in (("", "X1"), ("DTDC", "")):
        with pytest.raises(HTTPException) as exc:
            _ship(t["id"], courier=courier, tracking=tracking)
        assert _status(exc) == 400
    assert _unit(world, "SU-4")["status"] == "AVAILABLE"


def test_a_packed_piece_sold_before_ship_refuses_and_moves_nothing(world):
    t = _ask(2, to="ST-DHN-2")
    _pack(t["id"], ["BVQX004", "BVQX005"])
    world["stock_units"].update_one({"stock_id": "SU-5"}, {"$set": {"status": "SOLD"}})
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409 and "BVQX005" in str(exc.value.detail)
    assert _unit(world, "SU-4")["status"] == "AVAILABLE"


def test_ship_after_a_crash_mid_move_finishes_the_job(world):
    t = _ask(2, to="ST-DHN-2")
    _pack(t["id"], ["BVQX004", "BVQX005"])
    # The process died after claiming SU-4 but before saving the transfer.
    world["stock_units"].update_one(
        {"stock_id": "SU-4"},
        {"$set": {"status": "TRANSFERRED", "transfer_id": t["id"], "transfer_to_store_id": "ST-DHN-2"}},
    )
    shipped = _ship(t["id"])
    assert shipped["items"][0]["shipped_stock_ids"] == ["SU-4", "SU-5"]
    assert _unit(world, "SU-5")["status"] == "TRANSFERRED"


def test_cancel_only_before_shipping_and_the_sending_manager_may(world):
    from api.routers import transfers

    t = _ask(1, to="ST-DHN-2")
    _pack(t["id"], ["BVQX004"])
    done = _run(transfers.cancel_transfer(t["id"], "not needed", MGR_DHN))
    assert done["transfer"]["status"] == "cancelled"
    shipped = _shipped_box(world, to="ST-DHN-2", codes=("BVQX006",))
    with pytest.raises(HTTPException) as exc:
        _run(transfers.cancel_transfer(shipped["id"], "late", ADMIN))
    assert _status(exc) == 400


# ---------------------------------------------------------------------------
# Receive = scan pieces IN; damaged to quarantine; missing on the way
# ---------------------------------------------------------------------------


def test_scan_in_rehomes_each_piece_and_damaged_goes_to_quarantine(world):
    t = _shipped_box(world, to="ST-DHN-2")
    got = _scan_in(t["id"], "BVQX001", user=MGR_DHN2)
    assert got["status"] == "partially_received"
    assert _unit(world, "SU-1")["store_id"] == "ST-DHN-2"
    assert _unit(world, "SU-1")["status"] == "AVAILABLE"
    # The same scan twice counts once.
    assert _scan_in(t["id"], "BVQX001", user=MGR_DHN2)["items"][0]["quantity_received"] == 1
    with pytest.raises(HTTPException) as exc:
        _scan_in(t["id"], "BVQX002", user=MGR_DHN2, damaged=True)  # no reason
    assert _status(exc) == 400
    got = _scan_in(t["id"], "BVQX002", user=MGR_DHN2, damaged=True, reason="Broken hinge")
    unit = _unit(world, "SU-2")
    assert unit["status"] == "QUARANTINED" and unit["store_id"] == "ST-DHN-2"
    assert unit["quarantine_reason"] == "TRANSFER_DAMAGED"
    assert got["items"][0]["quantity_damaged"] == 1


def test_scan_in_refuses_a_piece_that_is_not_in_the_box(world):
    t = _shipped_box(world, to="ST-DHN-2")
    with pytest.raises(HTTPException) as exc:
        _scan_in(t["id"], "BVQX009")
    assert _status(exc) == 400 and "not in this box" in str(exc.value.detail)


def test_the_sending_shop_cannot_scan_in(world):
    t = _shipped_box(world, to="ST-DHN-2")
    with pytest.raises(HTTPException) as exc:
        _scan_in(t["id"], "BVQX001", user=MGR_DHN)
    assert _status(exc) == 403


def test_partial_receipt_puts_the_rest_on_missing_on_the_way(world):
    from api.routers import transfers

    t = _shipped_box(world, to="ST-DHN-2")
    _scan_in(t["id"], "BVQX001", user=MGR_DHN2)
    _scan_in(t["id"], "BVQX002", user=MGR_DHN2, damaged=True, reason="Scratched lens")
    done = _complete(t["id"], user=MGR_DHN2)
    assert done["status"] == "completed"
    (piece,) = done["missing_pieces"]
    assert piece["stock_id"] == "SU-3" and piece["barcode"] == "BVQX003"
    assert piece["status"] == "missing"
    # Never stranded: still tracked against this transfer, not on any shelf.
    assert _unit(world, "SU-3")["status"] == "TRANSFERRED"
    # Both managers and the admin see it.
    for who in (MGR_DHN, MGR_DHN2, ADMIN):
        rows = _run(transfers.list_missing(who))["pieces"]
        assert [r["barcode"] for r in rows] == ["BVQX003"], who["username"]
    assert _run(transfers.list_missing(MGR_BOK))["pieces"] == []


def test_found_on_the_way_lands_on_the_shelf_where_it_was_found(world):
    from api.routers import transfers

    t = _shipped_box(world, to="ST-DHN-2")
    _complete(t["id"], user=MGR_DHN2)  # nothing scanned in: three missing
    _run(transfers.found_missing_piece(t["id"], "SU-1", transfers.MissingFound(at="destination"), MGR_DHN2))
    _run(transfers.found_missing_piece(t["id"], "SU-2", transfers.MissingFound(at="source"), MGR_DHN))
    assert (_unit(world, "SU-1")["store_id"], _unit(world, "SU-1")["status"]) == ("ST-DHN-2", "AVAILABLE")
    assert (_unit(world, "SU-2")["store_id"], _unit(world, "SU-2")["status"]) == ("ST-DHN-1", "AVAILABLE")
    # The receiving manager cannot say it turned up at the sender's.
    with pytest.raises(HTTPException) as exc:
        _run(transfers.found_missing_piece(t["id"], "SU-3", transfers.MissingFound(at="source"), MGR_DHN2))
    assert _status(exc) == 403
    rows = _run(transfers.list_missing(ADMIN))["pieces"]
    assert [r["stock_id"] for r in rows] == ["SU-3"]


def test_write_off_of_a_missing_piece_is_admin_only(world):
    from api.routers import transfers

    t = _shipped_box(world, to="ST-DHN-2")
    _complete(t["id"], user=MGR_DHN2)
    with pytest.raises(HTTPException) as exc:
        _run(transfers.write_off_missing_piece(t["id"], "SU-3", MGR_DHN))
    assert _status(exc) == 403
    asked = _run(transfers.request_missing_write_off(t["id"], "SU-3", MGR_DHN2))
    assert {p["stock_id"]: p["status"] for p in asked["transfer"]["missing_pieces"]}["SU-3"] == (
        "write_off_requested"
    )
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    unit = _unit(world, "SU-3")
    assert unit["status"] == "VOID" and unit["store_id"] == "ST-DHN-1"
    (row,) = world["stock_shrinkage"].find({"transfer_id": t["id"]})
    assert row["voided_stock_ids"] == ["SU-3"] and row["shrinkage_value"] == 1850.01
    # Same-GSTIN move: no GST paper at all.
    assert world["vendor_bills"].count_documents({}) == 0
    rows = _run(transfers.list_missing(ADMIN))["pieces"]
    assert "SU-3" not in [r["stock_id"] for r in rows]


# ---------------------------------------------------------------------------
# GST between two GSTINs (CA answers 2026-10-23)
# ---------------------------------------------------------------------------


def _bills(world, tid):
    return list(world["vendor_bills"].find({"source_transfer_id": tid}))


def test_tax_invoice_is_booked_at_ship_in_the_dispatch_month(world):
    t = _shipped_box(world)  # Dhanbad (company Z) -> Bokaro (firm Y)
    (bill,) = _bills(world, t["id"])
    assert bill["invoice_number"] == t["tax_invoice_number"]
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    shipped_day = datetime.fromisoformat(stored["tax_invoice_date"].replace("Z", "+00:00"))
    if shipped_day.tzinfo is None:
        from datetime import timezone

        shipped_day = shipped_day.replace(tzinfo=timezone.utc)
    ist_day = shipped_day.astimezone(IST).date().isoformat()
    assert bill["bill_date"][:10] == bill["invoice_date"][:10] == ist_day
    # The units SHIPPED, at their own cost: 1850 + 1850 + 1850.01.
    assert bill["taxable_amount"] == 5550.01
    assert bill["cgst_total"] + bill["sgst_total"] == 277.50
    # Completing the receipt books nothing more.
    for code in ("BVQX001", "BVQX002", "BVQX003"):
        _scan_in(t["id"], code)
    _complete(t["id"])
    assert len(_bills(world, t["id"])) == 1


def test_short_delivery_is_value_received_plus_a_credit_note(world):
    from api.routers import transfers
    from api.services.purchase_invoice_engine import split_line_gst

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK)
    _complete(t["id"], user=MGR_BOK)
    # Missing, not yet lost: no credit note until it is written off.
    assert len(_bills(world, t["id"])) == 1
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    bills = _bills(world, t["id"])
    (note,) = [b for b in bills if b.get("doc_type") == "TRANSFER_CREDIT_NOTE"]
    (invoice,) = [b for b in bills if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    assert note["invoice_number"] == note["bill_number"] == "DHN01/C2627/0001"
    assert note["original_invoice_number"] == invoice["invoice_number"]
    assert note["recipient_gstin"] == GSTIN_Y_JH and note["vendor_gstin"] == GSTIN_Z_JH
    # Value actually received: two pieces at the line's own rate, to the paisa.
    unit_cost = transfers._get_transfer(t["id"])["items"][0]["unit_cost"]
    received = split_line_gst(transfers._line_value(2, unit_cost), 5.0, False)
    for head, inv_key in (("taxable", "taxable_amount"), ("cgst", "cgst_total"),
                          ("sgst", "sgst_total"), ("igst", "igst_total")):
        assert round(invoice[inv_key] + note[inv_key], 2) == received[head], head
    assert note["taxable_amount"] < 0
    # Pressing again books no second note.
    with pytest.raises(HTTPException):
        _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    assert len(_bills(world, t["id"])) == 2


def test_found_at_the_receiver_needs_no_credit_note_found_at_the_sender_does(world):
    from api.routers import transfers

    t = _shipped_box(world)
    _complete(t["id"], user=MGR_BOK)
    _run(transfers.found_missing_piece(t["id"], "SU-1", transfers.MissingFound(at="destination"), MGR_BOK))
    assert len(_bills(world, t["id"])) == 1
    _run(transfers.found_missing_piece(t["id"], "SU-2", transfers.MissingFound(at="source"), MGR_DHN))
    notes = [b for b in _bills(world, t["id"]) if b.get("doc_type") == "TRANSFER_CREDIT_NOTE"]
    assert len(notes) == 1 and notes[0]["credited_stock_ids"] == ["SU-2"]


def test_gstr1_files_the_transfer_credit_note_in_cdnr_not_b2b(world):
    from api.routers import reports, transfers

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX001")
    _scan_in(t["id"], "BVQX002")
    _complete(t["id"])
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    month = world["vendor_bills"].find_one({})["bill_date"][:7]
    rep = reports._compute_gstr1(month, "ST-DHN-1")
    (row,) = rep["b2b"]
    assert row["taxableValue"] == 5550.01
    (cn,) = [c for c in rep["cdnr"] if c.get("documentType") == "STOCK_TRANSFER"]
    assert cn["refReference"] == "DHN01/C2627/0001"
    assert cn["customerGSTIN"] == GSTIN_Y_JH
    assert cn["taxableValue"] == 1850.0
    # HSN summary is net of the note.
    (hsn,) = [h for h in rep["hsnSummary"] if h["hsnCode"] == "900311"]
    assert hsn["taxableValue"] == round(5550.01 - 1850.0, 2)
    # No "files the RECEIVED value" warning any more: the bill is the invoice.
    assert not [i for i in rep["validation"]["issues"] if "RECEIVED value" in i["issue"]]


def test_gstr3b_sender_outward_and_receiver_credit_both_net_the_note(world):
    """Dhanbad -> Pune (one company, two states: IGST). After a piece is
    written off, the sender's 3.1(a) and the receiver's Table 4 both carry the
    invoice minus the note, paisa-exact and equal to each other."""
    from api.routers import reports, transfers
    from api.services.purchase_invoice_engine import split_line_gst

    t = _shipped_box(world, to="ST-PUN-1")
    _scan_in(t["id"], "BVQX001")
    _scan_in(t["id"], "BVQX002")
    _complete(t["id"])
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    month = world["vendor_bills"].find_one({})["bill_date"][:7]
    sender = reports._compute_gstr3b(month, "ST-DHN-1")
    receiver = reports._compute_gstr3b(month, "ST-PUN-1")
    unit_cost = transfers._get_transfer(t["id"])["items"][0]["unit_cost"]
    want = split_line_gst(transfers._line_value(2, unit_cost), 5.0, True)
    assert sender["outwardTaxableSupplies"]["integratedTax"] == want["igst"]
    assert sender["outwardTaxableValue"] == want["taxable"]
    assert receiver["itcAvailable"]["integratedTax"] == want["igst"]
    assert sender["itcAvailable"]["integratedTax"] == 0.0


def test_eway_bill_and_vehicle_no_typed_on_the_box_print_on_the_invoice(world):
    from api.routers import print_documents, transfers

    t = _shipped_box(world)
    _run(transfers.update_transfer(
        t["id"], transfers.TransferUpdate(eway_bill_number="391000123456", vehicle_number="JH10AB1234"),
        MGR_DHN))
    html = _run(print_documents.tax_invoice_for_transfer(t["id"], "ORIGINAL", False, ADMIN)).body.decode()
    assert "391000123456" in html and "JH10AB1234" in html


# ---------------------------------------------------------------------------
# Cost: read through THE stock cost rule (services/stock_value), never inline
# ---------------------------------------------------------------------------


def test_transfer_value_reads_the_one_stock_cost_rule(world):
    """A late-priced invoice layer is worth its landed cost (stock_value's
    rule), so the tax invoice is valued at it -- not at the supplier base
    price a second, inline rule would read. When S4 merges, the same reader
    answers with the product's one average cost."""
    world["stock_units"].update_one(
        {"stock_id": "SU-4"},
        {"$set": {"cost_source": "PURCHASE_INVOICE", "landed_cost": 1902.5}},
    )
    t = _shipped_box(world, codes=("BVQX004",))
    assert t["items"][0]["unit_cost"] == 1902.5
    (bill,) = _bills(world, t["id"])
    assert bill["taxable_amount"] == 1902.5


def test_a_catalogue_only_price_is_no_cost_for_the_invoice(world):
    """A price typed only to finish cataloguing is not a cost (stock_value):
    a cost-less piece of such a product refuses a crossing ship."""
    world["products"].update_one({"product_id": "P-1"}, {"$set": {"cost_price_is_catalogue_only": True}})
    world["stock_units"].update_one({"stock_id": "SU-4"}, {"$unset": {"unit_cost": ""}})
    t = _ask(1)
    _pack(t["id"], ["BVQX004"])
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409
    assert _unit(world, "SU-4")["status"] == "AVAILABLE"


def test_an_invoice_booked_late_still_files_in_the_dispatch_month(world, monkeypatch):
    """If ship's booking did not land, Complete books it -- dated the tax
    invoice's own day (IST), never the day it was completed."""
    from api.routers import transfers

    real = transfers._book_mirror_purchase
    monkeypatch.setattr(transfers, "_book_mirror_purchase", lambda t: None)
    t = _shipped_box(world)
    assert _bills(world, t["id"]) == []
    # Invoiced 30 Apr 20:00 UTC = 1 May 01:30 IST: the May return.
    world["stock_transfers"].update_one(
        {"id": t["id"]}, {"$set": {"tax_invoice_date": "2026-04-30T20:00:00"}}
    )
    transfers.STOCK_TRANSFERS.clear()
    monkeypatch.setattr(transfers, "_book_mirror_purchase", real)
    _complete(t["id"], user=MGR_BOK)
    (bill,) = _bills(world, t["id"])
    assert bill["bill_date"][:10] == bill["invoice_date"][:10] == "2026-05-01"


def test_a_piece_already_credited_is_never_credited_twice(world):
    """Notes over overlapping pieces: each piece is credited once, and the
    invoice plus every note still equals the tax on what was delivered."""
    from api.routers import transfers
    from api.services.purchase_invoice_engine import split_line_gst

    t = _shipped_box(world)
    stored = transfers._get_transfer(t["id"])
    transfers._short_credit_note(stored, ["SU-1"], "test")
    transfers._short_credit_note(stored, ["SU-1", "SU-2"], "test")
    bills = _bills(world, t["id"])
    notes = [b for b in bills if b.get("doc_type") == "TRANSFER_CREDIT_NOTE"]
    assert sorted(s for n in notes for s in n["credited_stock_ids"]) == ["SU-1", "SU-2"]
    (invoice,) = [b for b in bills if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    left = split_line_gst(transfers._line_value(1, stored["items"][0]["unit_cost"]), 5.0, False)
    assert round(invoice["taxable_amount"] + sum(n["taxable_amount"] for n in notes), 2) == left["taxable"]
    assert round(invoice["cgst_total"] + sum(n["cgst_total"] for n in notes), 2) == left["cgst"]
