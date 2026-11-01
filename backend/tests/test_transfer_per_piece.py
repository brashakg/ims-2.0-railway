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
from tests.ist_business_day import business_day  # noqa: E402

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
    # The IST day of the invoice, by the tests' own hand-rolled +05:30
    # (tests/ist_business_day), never the code under test's helper.
    ist_day = business_day(stored["tax_invoice_date"].replace("Z", "+00:00"))
    assert bill["bill_date"].split("T")[0] == bill["invoice_date"].split("T")[0] == ist_day
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
    # Value actually received: the two pieces received, each at its own
    # cost (1850 + 1850), to the paisa -- not two at the line's average.
    received = split_line_gst(3700.0, 5.0, False)
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
    # SU-3's own cost, the figure its shrinkage row books.
    assert cn["taxableValue"] == 1850.01
    assert world["stock_shrinkage"].find_one({"transfer_id": t["id"]})["shrinkage_value"] == 1850.01
    # HSN summary is net of the note.
    (hsn,) = [h for h in rep["hsnSummary"] if h["hsnCode"] == "900311"]
    assert hsn["taxableValue"] == round(5550.01 - 1850.01, 2)
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
    want = split_line_gst(3700.0, 5.0, True)  # SU-1 + SU-2, each at its own cost
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
    assert bill["bill_date"].split("T")[0] == bill["invoice_date"].split("T")[0] == "2026-05-01"


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
    left = split_line_gst(1850.01, 5.0, False)  # SU-3, at its own cost
    assert round(invoice["taxable_amount"] + sum(n["taxable_amount"] for n in notes), 2) == left["taxable"]
    assert round(invoice["cgst_total"] + sum(n["cgst_total"] for n in notes), 2) == left["cgst"]


# ---------------------------------------------------------------------------
# Fix round 1 (S3 review): per-piece cost, unit checked before a note,
# compare-and-set saves, a ship that died part-way, either-end gates
# ---------------------------------------------------------------------------


def _notes(world, tid):
    return [b for b in _bills(world, tid) if b.get("doc_type") == "TRANSFER_CREDIT_NOTE"]


def _stale_once(monkeypatch, transfers, snapshot, at=1):
    """Transfer read number `at` answers `snapshot` (a press on another
    worker that read the box before the save that landed meanwhile); at=2 is
    the read a retrying door saves from (_update_transfer)."""
    real = transfers._get_transfer
    calls = {"n": 0}

    def _read(tid):
        calls["n"] += 1
        return dict(snapshot) if calls["n"] == at else real(tid)

    monkeypatch.setattr(transfers, "_get_transfer", _read)


def test_a_lost_piece_is_credited_at_its_own_cost_not_the_line_average(world):
    """SU-1 costs 1500, SU-2 costs 2000; SU-1 arrives, SU-2 is lost. The note
    credits 2000 (the lost piece's own cost, the figure its shrinkage books),
    so invoice + note = the tax on the 1500 actually received."""
    from api.routers import transfers
    from api.services.purchase_invoice_engine import split_line_gst

    world["stock_units"].update_one({"stock_id": "SU-1"}, {"$set": {"unit_cost": 1500.0}})
    world["stock_units"].update_one({"stock_id": "SU-2"}, {"$set": {"unit_cost": 2000.0}})
    t = _shipped_box(world, codes=("BVQX001", "BVQX002"))
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _complete(t["id"], user=MGR_BOK)
    _run(transfers.write_off_missing_piece(t["id"], "SU-2", ADMIN))
    (note,) = _notes(world, t["id"])
    (invoice,) = [b for b in _bills(world, t["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    assert invoice["taxable_amount"] == 3500.0
    received = split_line_gst(1500.0, 5.0, False)
    for head, key in (("taxable", "taxable_amount"), ("cgst", "cgst_total"),
                      ("sgst", "sgst_total"), ("igst", "igst_total")):
        assert round(invoice[key] + note[key], 2) == received[head], head
    shrink = world["stock_shrinkage"].find_one({"transfer_id": t["id"]})
    assert shrink["shrinkage_value"] == 2000.0 == -note["taxable_amount"]


def test_a_piece_that_landed_gets_no_credit_note_from_write_off_or_found(world):
    """BVQX001's scan in at Bokaro landed on the unit (on its shelf) only
    after Complete listed it missing (the scan raced Accept). Write-off and
    found-at-the-sender refuse BEFORE any note; found-at-the-receiver
    resolves it, still with no note."""
    from api.routers import transfers

    t = _shipped_box(world)
    stored = transfers._get_transfer(t["id"])
    done = _complete(t["id"], user=MGR_BOK)
    assert "SU-1" in [p["stock_id"] for p in done["missing_pieces"]]
    world["stock_units"].update_one(
        {"stock_id": "SU-1"}, {"$set": transfers._rehome_patch(stored, "AVAILABLE")}
    )
    with pytest.raises(HTTPException) as exc:
        _run(transfers.write_off_missing_piece(t["id"], "SU-1", ADMIN))
    assert _status(exc) == 409
    with pytest.raises(HTTPException) as exc:
        _run(transfers.found_missing_piece(t["id"], "SU-1", transfers.MissingFound(at="source"), MGR_DHN))
    assert _status(exc) == 409
    assert _notes(world, t["id"]) == []
    _run(transfers.found_missing_piece(t["id"], "SU-1", transfers.MissingFound(at="destination"), MGR_BOK))
    assert _notes(world, t["id"]) == []
    assert (_unit(world, "SU-1")["store_id"], _unit(world, "SU-1")["status"]) == ("ST-BOK-1", "AVAILABLE")


def test_an_eway_bill_saved_from_a_stale_read_keeps_the_scan(world, monkeypatch):
    """The sending manager's e-way PUT read the box before Bokaro scanned
    BVQX001 in and saves after it: the scan stays, and so does the e-way no."""
    from api.routers import transfers

    t = _shipped_box(world)
    snapshot = transfers._get_transfer(t["id"])
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _stale_once(monkeypatch, transfers, snapshot, at=2)
    _run(transfers.update_transfer(
        t["id"], transfers.TransferUpdate(eway_bill_number="391000123456"), MGR_DHN))
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["items"][0]["received_stock_ids"] == ["SU-1"]
    assert stored["status"] == "partially_received"
    assert stored["eway_bill_number"] == "391000123456"


def test_two_tablets_scanning_one_box_both_land(world, monkeypatch):
    """Two receiving tablets read the box, then each scans a different piece:
    the second save is retried on a fresh read, so neither scan is lost."""
    from api.routers import transfers

    t = _shipped_box(world)
    snapshot = transfers._get_transfer(t["id"])
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _stale_once(monkeypatch, transfers, snapshot, at=2)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK)
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["items"][0]["received_stock_ids"] == ["SU-1", "SU-2"]


@pytest.mark.parametrize("door", ["pack", "cancel"])
def test_a_stale_pack_or_cancel_never_undoes_a_ship(world, monkeypatch, door):
    from api.routers import transfers

    t = _ask(2, to="ST-DHN-2")
    _pack(t["id"], ["BVQX004", "BVQX005"])
    snapshot = transfers._get_transfer(t["id"])
    _ship(t["id"])
    _stale_once(monkeypatch, transfers, snapshot)
    with pytest.raises(HTTPException) as exc:
        if door == "pack":
            _pack(t["id"], ["BVQX006"])
        else:
            # Cancel checked the units before ship claimed them.
            monkeypatch.setattr(transfers, "_transfer_has_moved_stock", lambda t: False)
            _run(transfers.cancel_transfer(t["id"], "not needed", ADMIN))
    assert _status(exc) == 409
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["status"] == "in_transit"
    assert stored["items"][0]["shipped_stock_ids"] == ["SU-4", "SU-5"]
    assert _unit(world, "SU-4")["status"] == "TRANSFERRED"
    assert _unit(world, "SU-6")["status"] == "AVAILABLE"


def test_a_ship_that_loses_to_a_repack_puts_its_pieces_back(world, monkeypatch):
    """Ship read the box (004, 005); a re-pack (006, 007) saved before ship
    did. Ship's save is refused and the pieces it claimed go back."""
    from api.routers import transfers

    t = _ask(2, to="ST-DHN-2")
    _pack(t["id"], ["BVQX004", "BVQX005"])
    snapshot = transfers._get_transfer(t["id"])
    _pack(t["id"], ["BVQX006", "BVQX007"])
    _stale_once(monkeypatch, transfers, snapshot)
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409
    for sid in ("SU-4", "SU-5", "SU-6", "SU-7"):
        assert (_unit(world, sid)["status"], _unit(world, sid).get("transfer_id")) == ("AVAILABLE", None), sid
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["status"] == "packed" and stored["items"][0]["packed_stock_ids"] == ["SU-6", "SU-7"]


def test_a_repack_after_a_ship_that_died_part_way_strands_nothing(world):
    """A ship died after claiming SU-1 and SU-2. The packer re-scans the box
    as 001, 004, 005: SU-1 may go in again, SU-2 (left out) goes back on
    the shelf, and the next ship sends exactly 001, 004, 005."""
    from api.routers import transfers

    t = _ask(3, to="ST-DHN-2")
    _pack(t["id"], ["BVQX001", "BVQX002", "BVQX003"])
    world["stock_units"].update_many(
        {"stock_id": {"$in": ["SU-1", "SU-2"]}},
        {"$set": {"status": "TRANSFERRED", "transfer_id": t["id"], "transfer_to_store_id": "ST-DHN-2"}},
    )
    _pack(t["id"], ["BVQX001", "BVQX004", "BVQX005"])
    assert (_unit(world, "SU-2")["status"], _unit(world, "SU-2").get("transfer_id")) == ("AVAILABLE", None)
    shipped = _ship(t["id"])
    assert shipped["items"][0]["shipped_stock_ids"] == ["SU-1", "SU-4", "SU-5"]
    for code in ("BVQX001", "BVQX004", "BVQX005"):
        _scan_in(t["id"], code, user=MGR_DHN2)
    _complete(t["id"], user=MGR_DHN2)
    assert list(world["stock_units"].find({"status": "TRANSFERRED"})) == []
    assert _run(transfers.list_missing(ADMIN))["pieces"] == []


def test_a_sale_between_the_check_and_the_claim_ships_nothing_and_takes_no_invoice_number(
    world, monkeypatch
):
    """Dhanbad -> Bokaro (two GSTINs). SU-5 is sold after ship's re-check but
    before its claim: 409, SU-4 (claimed first) is back on the shelf, the box
    is still packed, and no tax invoice number was taken."""
    from api.routers import transfers

    real = transfers._packed_units

    def _then_sold(transfer):
        found = real(transfer)
        world["stock_units"].update_one({"stock_id": "SU-5"}, {"$set": {"status": "SOLD"}})
        return found

    t = _ask(2)
    _pack(t["id"], ["BVQX004", "BVQX005"])
    monkeypatch.setattr(transfers, "_packed_units", _then_sold)
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409 and "BVQX005" in str(exc.value.detail)
    assert (_unit(world, "SU-4")["status"], _unit(world, "SU-4").get("transfer_id")) == ("AVAILABLE", None)
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["status"] == "packed"
    assert not stored.get("tax_invoice_number") and _bills(world, t["id"]) == []


def test_a_crossing_box_with_sold_pieces_names_them_all_and_takes_no_number(world):
    t = _ask(3)
    _pack(t["id"], ["BVQX004", "BVQX005", "BVQX006"])
    world["stock_units"].update_many({"stock_id": {"$in": ["SU-5", "SU-6"]}}, {"$set": {"status": "SOLD"}})
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409
    assert "since it was packed: BVQX005, BVQX006" in str(exc.value.detail)
    assert _unit(world, "SU-4")["status"] == "AVAILABLE"
    assert not world["stock_transfers"].find_one({"id": t["id"]}).get("tax_invoice_number")


def test_the_asking_shop_may_cancel_its_request_with_a_reason(world):
    from api.routers import transfers

    t = _ask(1, user=MGR_BOK)  # Bokaro asks Dhanbad: pending approval
    for blank in ("", "   "):
        with pytest.raises(HTTPException) as exc:
            _run(transfers.cancel_transfer(t["id"], blank, MGR_BOK))
        assert _status(exc) == 400
    assert transfers._get_transfer(t["id"])["status"] == "pending_approval"
    with pytest.raises(HTTPException) as exc:
        _run(transfers.cancel_transfer(t["id"], "not needed", MGR_DHN2))  # at neither end
    assert _status(exc) == 403
    done = _run(transfers.cancel_transfer(t["id"], "not needed", MGR_BOK))
    assert done["transfer"]["status"] == "cancelled"


def test_the_receiving_manager_cannot_pack_the_senders_box(world):
    from api.routers import transfers

    t = _ask(1)  # Dhanbad -> Bokaro, approved
    with pytest.raises(HTTPException) as exc:
        _pack(t["id"], ["BVQX001"], user=MGR_BOK)
    assert _status(exc) == 403
    assert not transfers._get_transfer(t["id"])["items"][0].get("packed_stock_ids")


@pytest.mark.parametrize("to", ["   ", "	"])
def test_a_transfer_to_no_shop_is_refused(world, to):
    from api.routers import transfers

    body = transfers.TransferInput(
        transfer_type="store_to_store", from_location_id="ST-BOK-1", from_location_name="Bokaro",
        to_location_id=to, to_location_name="",
        items=[transfers.TransferItemInput(product_id="P-1", sku="FR-1", product_name="Carrera Frame",
                                           quantity_requested=1)],
    )
    with pytest.raises(HTTPException) as exc:
        _run(transfers.create_transfer(body, ADMIN))
    assert _status(exc) == 400
    assert world["stock_transfers"].count_documents({}) == 0


def test_a_blank_destination_fails_validation(world):
    from pydantic import ValidationError

    from api.routers import transfers

    with pytest.raises(ValidationError):
        transfers.TransferInput(
            transfer_type="store_to_store", from_location_id="ST-BOK-1", from_location_name="Bokaro",
            to_location_id="", to_location_name="",
            items=[transfers.TransferItemInput(product_id="P-1", sku="FR-1", product_name="Carrera Frame",
                                           quantity_requested=1)],
        )


# ---------------------------------------------------------------------------
# Fix round 2 (S3 review): two presses on two workers, a note only after the
# unit moved, a scan racing Accept, a stale pack list, BOPIS line keys
# ---------------------------------------------------------------------------


def _sync(coro):
    """Run a door to the end INSIDE another door (a press on another worker
    landing mid-way): the doors make no awaits, so one send finishes them."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("the door awaited")


class _UnitsHook:
    """stock_units, with `hook(method, args)` run once before the first call
    it picks (returns True) -- the moment another worker's press lands."""

    def __init__(self, coll, hook):
        self._coll, self._hook, self._fired = coll, hook, False

    def __getattr__(self, name):
        real = getattr(self._coll, name)
        if name not in ("find_one_and_update", "update_one"):
            return real

        def call(*args, **kwargs):
            if not self._fired and self._hook(name, args):
                self._fired = True
                self._hook(None, None)
            return real(*args, **kwargs)

        return call


def _hook_units(monkeypatch, transfers, pick, then):
    real = transfers._units_coll
    state = {"hook": None}

    def hook(method, args):
        if method is None:
            then()
            return True
        return pick(method, args)

    def coll():
        if state["hook"] is None:
            state["hook"] = _UnitsHook(real(), hook)
        return state["hook"]

    monkeypatch.setattr(transfers, "_units_coll", coll)


def _ship_events(monkeypatch, transfers):
    """The stock_ids each TRANSFER_SHIP ledger row is written for."""
    rows: list = []
    monkeypatch.setattr(transfers, "_ledger_transfer_event",
                        lambda kind, sid, *a, **k: rows.append(sid) if kind == "TRANSFER_SHIP" else None)
    return rows


def test_a_second_ship_during_the_first_ships_mint_ships_the_box_once(world, monkeypatch):
    """Ship A has claimed SU-1..3 and is minting the T number when ship B (a
    second press on another worker) runs to the end. A loses the save and
    answers B's ship: every piece stays on the way under this transfer, one
    invoice, one ship ledger row per piece, and Bokaro can scan them in."""
    from api.routers import transfers

    t = _ask(3)  # Dhanbad (company Z) -> Bokaro (firm Y)
    _pack(t["id"], ["BVQX001", "BVQX002", "BVQX003"])
    real = transfers._transfer_tax_invoice
    fired: dict = {"b": None}
    ship_rows = _ship_events(monkeypatch, transfers)

    def _mint(transfer):
        if fired["b"] is None:
            fired["b"] = "running"
            fired["b"] = _sync(transfers.ship_transfer(t["id"], "D41092277", None, "DTDC", False, ADMIN))
        return real(transfer)

    monkeypatch.setattr(transfers, "_transfer_tax_invoice", _mint)
    a = _ship(t["id"])
    b = fired["b"] or {}
    assert b["transfer"]["status"] == "in_transit"
    assert a["status"] == "in_transit"
    for sid in ("SU-1", "SU-2", "SU-3"):
        assert (_unit(world, sid)["status"], _unit(world, sid)["transfer_id"]) == ("TRANSFERRED", t["id"]), sid
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["status"] == "in_transit"
    assert stored["items"][0]["shipped_stock_ids"] == ["SU-1", "SU-2", "SU-3"]
    (bill,) = _bills(world, t["id"])
    assert bill["invoice_number"] == stored["tax_invoice_number"]
    assert sorted(ship_rows) == ["SU-1", "SU-2", "SU-3"]
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    assert _unit(world, "SU-1")["store_id"] == "ST-BOK-1"


def test_a_ship_that_loses_a_claim_never_puts_back_a_piece_another_press_ships(world, monkeypatch):
    """Ship A has claimed SU-1; before it claims SU-2, ship B runs to the end
    (it takes SU-1 over and claims SU-2). A's claim of SU-2 fails: A
    refuses, and SU-1 stays on the way -- it is on B's invoice."""
    from api.routers import transfers

    t = _ask(2)
    _pack(t["id"], ["BVQX001", "BVQX002"])
    claims = {"n": 0}

    def pick(method, args):
        if method == "find_one_and_update" and args[0].get("status") == "AVAILABLE":
            claims["n"] += 1
            return claims["n"] == 2  # A, about to claim SU-2
        return False

    _hook_units(monkeypatch, transfers, pick,
                lambda: _sync(transfers.ship_transfer(t["id"], "D1", None, "DTDC", False, ADMIN)))
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["status"] == "in_transit"
    assert stored["items"][0]["shipped_stock_ids"] == ["SU-1", "SU-2"]
    for sid in ("SU-1", "SU-2"):
        assert (_unit(world, sid)["status"], _unit(world, sid)["transfer_id"]) == ("TRANSFERRED", t["id"]), sid
    (bill,) = _bills(world, t["id"])
    assert bill["taxable_amount"] == 3700.0


def test_a_ship_pressed_again_after_it_landed_answers_the_ship(world, monkeypatch):
    """A second press that read the box before the first one saved gets the
    shipped box back, never a 409 and never a second ship."""
    from api.routers import transfers

    t = _ask(2)
    _pack(t["id"], ["BVQX001", "BVQX002"])
    snapshot = transfers._get_transfer(t["id"])
    ship_rows = _ship_events(monkeypatch, transfers)
    _ship(t["id"])
    _stale_once(monkeypatch, transfers, snapshot)
    again = _ship(t["id"])
    assert again["status"] == "in_transit"
    assert len(_bills(world, t["id"])) == 1
    assert sorted(ship_rows) == ["SU-1", "SU-2"]


@pytest.mark.parametrize("door", ["write_off", "found_at_source"])
def test_a_piece_found_at_the_receiver_mid_write_off_gets_no_credit_note(world, monkeypatch, door):
    """Write-off (or found at the sender) checked SU-1 is on the way; the
    Bokaro manager's Found at Bokaro lands on another worker before the
    door moves the unit. The door refuses and no credit note is booked."""
    from api.routers import transfers

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK)
    _scan_in(t["id"], "BVQX003", user=MGR_BOK)
    _complete(t["id"], user=MGR_BOK)

    def pick(method, args):
        if method != "update_one" or args[0].get("stock_id") != "SU-1":
            return False
        patch = args[1].get("$set", {})
        return patch.get("status") == "VOID" or (
            patch.get("status") == "AVAILABLE" and "store_id" not in patch)

    _hook_units(monkeypatch, transfers, pick, lambda: _sync(transfers.found_missing_piece(
        t["id"], "SU-1", transfers.MissingFound(at="destination"), MGR_BOK)))
    with pytest.raises(HTTPException) as exc:
        if door == "write_off":
            _run(transfers.write_off_missing_piece(t["id"], "SU-1", ADMIN))
        else:
            _run(transfers.found_missing_piece(t["id"], "SU-1", transfers.MissingFound(at="source"), MGR_DHN))
    assert _status(exc) == 409
    assert _notes(world, t["id"]) == []
    assert (_unit(world, "SU-1")["store_id"], _unit(world, "SU-1")["status"]) == ("ST-BOK-1", "AVAILABLE")


def test_a_damaged_scan_racing_accept_is_recorded_damaged_not_missing(world, monkeypatch):
    """Bokaro scans BVQX001 damaged in the box; the unit lands in
    quarantine, then Accept saves before the scan does. Accept reads the
    unit: SU-1 is recorded damaged, never put on Missing on the way."""
    from api.routers import transfers

    t = _shipped_box(world)
    real = transfers._update_transfer
    fired = {"done": False}

    def _update(tid, apply):
        if not fired["done"]:
            fired["done"] = True
            _sync(transfers.complete_transfer(t["id"], None, MGR_BOK))
        return real(tid, apply)

    monkeypatch.setattr(transfers, "_update_transfer", _update)
    _scan_in(t["id"], "BVQX001", user=MGR_BOK, damaged=True, reason="Broken hinge")
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["status"] == "completed"
    assert stored["items"][0]["damaged_stock_ids"] == ["SU-1"]
    assert sorted(p["stock_id"] for p in stored["missing_pieces"]) == ["SU-2", "SU-3"]
    assert "SU-1" not in [r["stock_id"] for r in _run(transfers.list_missing(ADMIN))["pieces"]]
    assert _unit(world, "SU-1")["status"] == "QUARANTINED"


def test_a_damaged_piece_listed_missing_is_found_at_the_receiver_still_damaged(world):
    """The other order: Accept listed SU-1 missing, then its damaged scan
    landed on the unit (quarantine at Bokaro). Found at Bokaro resolves it
    as damaged and leaves it in quarantine."""
    from api.routers import transfers

    t = _shipped_box(world)
    stored = transfers._get_transfer(t["id"])
    _complete(t["id"], user=MGR_BOK)
    world["stock_units"].update_one(
        {"stock_id": "SU-1"},
        {"$set": transfers._rehome_patch(stored, "QUARANTINED", "Broken hinge")},
    )
    _run(transfers.found_missing_piece(t["id"], "SU-1", transfers.MissingFound(at="destination"), MGR_BOK))
    after = world["stock_transfers"].find_one({"id": t["id"]})
    assert after["items"][0]["damaged_stock_ids"] == ["SU-1"]
    assert after["items"][0]["damage_reasons"]["SU-1"] == "Broken hinge"
    assert {p["stock_id"]: p["status"] for p in after["missing_pieces"]}["SU-1"] == "found"
    assert (_unit(world, "SU-1")["store_id"], _unit(world, "SU-1")["status"]) == ("ST-BOK-1", "QUARANTINED")
    assert _notes(world, t["id"]) == []


def test_a_pack_list_built_from_an_older_box_is_refused(world):
    """Tablet B loaded the empty box; tablet A then packed 005 and 007. B's
    next scan sends its stale list with the box revision it saw: refused,
    and A's pieces stay in the box."""
    from api.routers import transfers

    t = _ask(3)
    seen_by_b = t.get("rev")
    a = _run(transfers.pack_transfer(
        t["id"], transfers.TransferPack(barcodes=["BVQX005", "BVQX007"], rev=seen_by_b), ADMIN))
    assert a["transfer"]["rev"] != seen_by_b
    with pytest.raises(HTTPException) as exc:
        _run(transfers.pack_transfer(t["id"], transfers.TransferPack(barcodes=["BVQX009"], rev=seen_by_b), ADMIN))
    assert _status(exc) == 409
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["items"][0]["packed_barcodes"] == ["BVQX005", "BVQX007"]


def test_a_two_line_bopis_transfer_packs_ships_and_lands(world):
    """orders/bopis.py writes lines with transfer_item_id and no id: each
    line still keeps its own pieces, invoice line and receipt."""
    world["products"].insert_one({"product_id": "P-2", "sku": "SG-2", "name": "Vogue Sunglass",
                                  "category": "SUNGLASS", "hsn_code": "900410", "cost_price": 1000.0})
    world["stock_units"].insert_one({"stock_id": "SU-V1", "product_id": "P-2", "store_id": "ST-DHN-1",
                                     "status": "AVAILABLE", "barcode": "BVV001", "unit_cost": 1000.0})
    tid = "trf_bopis1"
    world["stock_transfers"].insert_one({
        "id": tid, "transfer_number": "TRF-BOPIS-1", "order_id": "ORD-1",
        "transfer_type": "store_to_store", "source": "bopis",
        "from_location_id": "ST-DHN-1", "from_location_name": NAMES["ST-DHN-1"],
        "to_location_id": "ST-BOK-1", "to_location_name": NAMES["ST-BOK-1"],
        "status": "approved", "created_at": "2026-05-01T05:30:00",
        "items": [
            {"transfer_item_id": "ti-1", "product_id": "P-1", "sku": "FR-1", "product_name": "Carrera Frame",
             "quantity_requested": 1, "quantity_shipped": 0, "quantity_received": 0},
            {"transfer_item_id": "ti-2", "product_id": "P-2", "sku": "SG-2", "product_name": "Vogue Sunglass",
             "quantity_requested": 1, "quantity_shipped": 0, "quantity_received": 0},
        ],
    })
    packed = _pack(tid, ["BVQX001", "BVV001"])
    assert [ln["packed_barcodes"] for ln in packed["items"]] == [["BVQX001"], ["BVV001"]]
    shipped = _ship(tid)
    assert [ln["shipped_stock_ids"] for ln in shipped["items"]] == [["SU-1"], ["SU-V1"]]
    stored = world["stock_transfers"].find_one({"id": tid})
    assert [ln["serials"] for ln in stored["tax_invoice_lines"]] == [["BVQX001"], ["BVV001"]]
    _scan_in(tid, "BVQX001", user=MGR_BOK)
    _scan_in(tid, "BVV001", user=MGR_BOK)
    done = _complete(tid, user=MGR_BOK)
    assert done["status"] == "completed" and not done.get("missing_pieces")
    assert [ln["received_stock_ids"] for ln in done["items"]] == [["SU-1"], ["SU-V1"]]


def _ship_loses_to_a_repack_during_its_mint(monkeypatch, tid, repack):
    """Ship took the box and claimed its pieces; while it mints the T number
    a re-pack saves on another worker. Ship's save is refused (409) after
    the number, its date and the frozen lines already landed on the doc."""
    from api.routers import transfers

    real = transfers._transfer_tax_invoice
    fired = {"done": False}

    def _mint(transfer):
        if not fired["done"]:
            fired["done"] = True
            _sync(transfers.pack_transfer(tid, transfers.TransferPack(barcodes=repack), ADMIN))
        return real(transfer)

    monkeypatch.setattr(transfers, "_transfer_tax_invoice", _mint)
    with pytest.raises(HTTPException) as exc:
        _ship(tid)
    assert _status(exc) == 409
    monkeypatch.setattr(transfers, "_transfer_tax_invoice", real)


def test_frozen_invoice_lines_are_rebuilt_at_the_ship_that_lands(world, monkeypatch):
    t = _ask(2)  # Dhanbad -> Bokaro: a tax invoice
    _pack(t["id"], ["BVQX003", "BVQX004"])  # 1850.01 + 1850
    _ship_loses_to_a_repack_during_its_mint(monkeypatch, t["id"], ["BVQX005", "BVQX006"])
    failed = world["stock_transfers"].find_one({"id": t["id"]})
    assert failed["status"] == "packed" and failed["tax_invoice_number"]
    assert [ln["serials"] for ln in failed["tax_invoice_lines"]] == [["BVQX003", "BVQX004"]]
    for sid in ("SU-3", "SU-4"):
        assert (_unit(world, sid)["status"], _unit(world, sid).get("transfer_id")) == ("AVAILABLE", None), sid
    _ship(t["id"])
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert [ln["serials"] for ln in stored["tax_invoice_lines"]] == [["BVQX005", "BVQX006"]]
    assert stored["tax_invoice_lines"][0]["taxable"] == 3700.0
    (bill,) = _bills(world, t["id"])
    assert bill["invoice_number"] == failed["tax_invoice_number"]
    assert bill["taxable_amount"] == 3700.0


def test_a_number_from_a_failed_ship_is_redated_at_the_ship_that_lands(world, monkeypatch):
    t = _ask(2)
    _pack(t["id"], ["BVQX003", "BVQX004"])
    _ship_loses_to_a_repack_during_its_mint(monkeypatch, t["id"], ["BVQX005", "BVQX006"])
    # The failed press's day falls in an earlier month (April).
    world["stock_transfers"].update_one(
        {"id": t["id"]}, {"$set": {"tax_invoice_date": "2026-04-15T06:00:00"}}
    )
    _ship(t["id"])
    stored = world["stock_transfers"].find_one({"id": t["id"]})
    assert stored["tax_invoice_date"] != "2026-04-15T06:00:00"
    ist_day = business_day(stored["tax_invoice_date"].replace("Z", "+00:00"))
    (bill,) = _bills(world, t["id"])
    assert bill["invoice_date"].split("T")[0] == ist_day
    assert not bill["invoice_date"].startswith("2026-04")


def test_a_ship_that_read_the_box_before_another_press_took_it_touches_no_piece(world, monkeypatch):
    """Ship B read the box; ship A then took it (and is still running). B is
    refused before it claims or puts back any piece, so it can never undo
    A's claims; A alone can save the ship."""
    from api.routers import transfers

    t = _ask(2)
    _pack(t["id"], ["BVQX001", "BVQX002"])
    snapshot = transfers._get_transfer(t["id"])
    transfers._take_box(transfers._get_transfer(t["id"]))  # ship A took the box
    writes = []
    _hook_units(monkeypatch, transfers, lambda m, a: writes.append(m) and False, lambda: None)
    _stale_once(monkeypatch, transfers, snapshot)
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert _status(exc) == 409
    assert writes == []
    assert world["stock_transfers"].find_one({"id": t["id"]})["status"] == "packed"


# ---------------------------------------------------------------------------
# Fix round 3 (S3 review): a piece lost on the way takes back the SENDER's
# input credit (section 17(5)(h), #1302's one write-off rule); a piece that
# landed and then changed state is never stuck on "Missing on the way"
# ---------------------------------------------------------------------------


def _purchase_bill(world, bill_id="PB-1", grn_id="GRN-1"):
    """The ten pieces came in on GRN-1; its bill was billed to company Z's
    Jharkhand GSTIN at 5%: Rs 46.25 CGST + 46.25 SGST a piece."""
    world["stock_units"].update_many({"stock_id": {"$regex": "^SU-[0-9]"}}, {"$set": {"grn_id": grn_id}})
    bill = {
        "bill_id": bill_id, "bill_number": f"INV-{bill_id}", "grn_id": grn_id, "status": "OUTSTANDING",
        "bill_date": "2026-05-02", "invoice_date": "2026-05-02", "vendor_id": "V-1",
        "vendor_gstin": "20AAACV3333C1ZQ", "recipient_gstin": GSTIN_Z_JH, "recipient_entity_id": "ENT-Z",
        "entity_id": "ENT-Z", "store_id": "ST-DHN-1", "itc_eligible": True,
        "taxable_amount": 18500.0, "tax_amount": 925.0, "igst_total": 0.0,
        "cgst_total": 462.5, "sgst_total": 462.5,
        "lines": [{"product_id": "P-1", "qty": 10, "taxable": 18500.0, "gst_rate": 5.0,
                   "cgst": 462.5, "sgst": 462.5, "igst": 0.0}],
    }
    world["vendor_bills"].insert_one(bill)
    return bill


def _reversals(world):
    return list(world["itc_writeoff_reversals"].find({}))


def _lost_one(world, to):
    t = _shipped_box(world, to=to)
    who = MGR_DHN2 if to == "ST-DHN-2" else MGR_BOK
    _scan_in(t["id"], "BVQX001", user=who)
    _scan_in(t["id"], "BVQX002", user=who)
    _complete(t["id"], user=who)
    return t


@pytest.mark.parametrize("to", ["ST-DHN-2", "ST-BOK-1"])  # same GSTIN / crossing
def test_a_piece_lost_on_the_way_takes_back_the_senders_input_credit(world, to):
    from api.routers import transfers
    from api.services import writeoff_itc

    _purchase_bill(world)
    t = _lost_one(world, to)
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    # Exactly one reversal, on the SENDER's GSTIN, against the bill that put
    # the piece there -- never this box's own tax invoice (the credit note
    # already nets that for the receiver).
    (row,) = _reversals(world)
    assert (row["_id"], row["gstin"], row["bill_id"]) == ("SU-3", GSTIN_Z_JH, "PB-1")
    assert (row["cgst"], row["sgst"], row["igst"]) == (46.25, 46.25, 0.0)
    assert (row["source_type"], row["source_id"]) == ("TRANSFER_MISSING", t["id"])
    # GSTR-3B 4(B)(1) of the write-off month takes it back.
    period = writeoff_itc._period(_unit(world, "SU-3")["voided_at"])
    assert writeoff_itc.month_reversal(world, GSTIN_Z_JH, period)["total"] == 92.5
    assert writeoff_itc.month_reversal(world, GSTIN_Y_JH, period)["total"] == 0.0
    shrink = world["stock_shrinkage"].find_one({"transfer_id": t["id"]})
    assert shrink["itc_reversed"]["total"] == 92.5
    # Crossing: the credit note is booked as well; same GSTIN: no GST paper.
    assert len(_notes(world, t["id"])) == (1 if to == "ST-BOK-1" else 0)
    # Found after the write-off is refused: the reversal stands, once.
    with pytest.raises(HTTPException) as exc:
        _run(transfers.found_missing_piece(t["id"], "SU-3", transfers.MissingFound(at="source"), MGR_DHN))
    assert _status(exc) == 409
    assert len(_reversals(world)) == 1


@pytest.mark.parametrize("at", ["source", "destination"])
def test_a_missing_piece_found_reverses_no_credit(world, at):
    from api.routers import transfers

    _purchase_bill(world)
    t = _lost_one(world, "ST-BOK-1")
    who = MGR_DHN if at == "source" else MGR_BOK
    _run(transfers.found_missing_piece(t["id"], "SU-3", transfers.MissingFound(at=at), who))
    assert _reversals(world) == []


def test_a_bill_booked_after_the_piece_was_lost_on_the_way_reverses_it(world):
    from api.routers import transfers
    from api.services import writeoff_itc

    t = _lost_one(world, "ST-DHN-2")
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    assert _reversals(world) == []  # no bill yet: nothing claimed
    bill = _purchase_bill(world)
    writeoff_itc.reverse_on_new_bill(world, world["vendor_bills"].find_one({"bill_id": bill["bill_id"]}))
    (row,) = _reversals(world)
    assert (row["_id"], row["gstin"], row["cgst"], row["sgst"]) == ("SU-3", GSTIN_Z_JH, 46.25, 46.25)
    assert (row["source_type"], row["source_id"]) == ("TRANSFER_MISSING", t["id"])


def test_a_piece_that_landed_then_was_sold_or_reserved_is_never_stuck_missing(world):
    """Bokaro's scans of BVQX001 and BVQX002 landed on the units but not on
    the box (a scan racing Accept, or a worker that died between the two
    writes); the counter then sold BVQX001 and reserved BVQX002. Accept
    records the sold one received; Found at Bokaro resolves the reserved one."""
    from api.routers import transfers

    t = _shipped_box(world)
    stored = transfers._get_transfer(t["id"])
    for sid, then in (("SU-1", "SOLD"), ("SU-2", "RESERVED")):
        world["stock_units"].update_one({"stock_id": sid}, {"$set": transfers._rehome_patch(stored, "AVAILABLE")})
        world["stock_units"].update_one({"stock_id": sid}, {"$set": {"status": then}})
        if sid == "SU-1":
            done = _complete(t["id"], user=MGR_BOK)
    assert sorted(p["stock_id"] for p in done["missing_pieces"]) == ["SU-2", "SU-3"]
    _run(transfers.found_missing_piece(t["id"], "SU-2", transfers.MissingFound(at="destination"), MGR_BOK))
    after = world["stock_transfers"].find_one({"id": t["id"]})
    assert after["items"][0]["received_stock_ids"] == ["SU-1", "SU-2"]
    assert [r["stock_id"] for r in _run(transfers.list_missing(ADMIN))["pieces"]] == ["SU-3"]
    assert _notes(world, t["id"]) == []
    assert (_unit(world, "SU-1")["status"], _unit(world, "SU-2")["status"]) == ("SOLD", "RESERVED")
    # Its arrival is audited as it landed (the shelf), not as it is now.
    row = world["stock_audit"].find_one({"stock_id": "SU-2", "reason": "MISSING_FOUND"})
    assert row and row["new_status"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# Review round 4: a credit note on the box is never the bill a write-off reads
# ---------------------------------------------------------------------------


def test_a_damaged_write_off_on_a_box_with_a_credit_note_reverses_the_invoice_credit(world):
    """Dhanbad (Z) ships BVQX001-003 to Bokaro (Y): 001 in good, 002 damaged,
    003 lost and written off (credit note DHN01/C2627/0001). Writing 002 off
    as damaged takes back Bokaro's credit on the tax invoice line -- 92.50 --
    never the credit note's negative heads clamped to 0."""
    from datetime import datetime as _dt

    from api.routers import transfers
    from api.services import writeoff_itc

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _complete(t["id"], user=MGR_BOK)
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    assert len(_notes(world, t["id"])) == 1
    (invoice,) = [b for b in _bills(world, t["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    out = writeoff_itc.record_writeoff(world, [_unit(world, "SU-2")], source_type="QUARANTINE_DAMAGE",
                                       source_id="SU-2", when=_dt.utcnow(), actor_id="u-admin")
    row = world["itc_writeoff_reversals"].find_one({"_id": "SU-2"})
    assert (row["bill_id"], row["gstin"]) == (invoice["bill_id"], GSTIN_Y_JH)
    assert (row["cgst"], row["sgst"], row["igst"]) == (46.25, 46.25, 0.0)
    assert out["total"] == 92.5


def test_a_piece_lost_on_its_second_hop_reverses_the_first_hops_invoice_not_its_credit_note(world):
    """T0 Dhanbad -> Bokaro lands BVQX001 and BVQX002; BVQX003 is lost
    (credit note on T0). T1 Bokaro -> Dhanbad then loses BVQX001: Bokaro
    takes back the credit T0's tax invoice gave it -- 92.50 -- and the
    shrinkage record says so."""
    from api.routers import transfers

    t0 = _lost_one(world, "ST-BOK-1")
    _run(transfers.write_off_missing_piece(t0["id"], "SU-3", ADMIN))
    (invoice,) = [b for b in _bills(world, t0["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    t1 = _ask(1, frm="ST-BOK-1", to="ST-DHN-1")
    _pack(t1["id"], ["BVQX001"])
    _ship(t1["id"])
    _complete(t1["id"], user=MGR_DHN)
    _run(transfers.write_off_missing_piece(t1["id"], "SU-1", ADMIN))
    row = world["itc_writeoff_reversals"].find_one({"_id": "SU-1"})
    assert (row["bill_id"], row["gstin"]) == (invoice["bill_id"], GSTIN_Y_JH)
    assert (row["cgst"], row["sgst"], row["igst"]) == (46.25, 46.25, 0.0)
    shrink = world["stock_shrinkage"].find_one({"transfer_id": t1["id"]})
    assert shrink["itc_reversed"]["total"] == 92.5


def test_a_credit_note_is_never_read_as_a_late_transfer_bill(world):
    """reverse_on_new_bill on a transfer credit note books nothing -- not
    even for a piece of the box written off as damaged: the note only covers
    pieces that never arrived, and a row it wrote would stand for good."""
    from api.routers import transfers
    from api.services import writeoff_itc

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _complete(t["id"], user=MGR_BOK)
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    world["stock_units"].update_one({"stock_id": "SU-2"}, {"$set": {"status": "DAMAGED"}})
    (note,) = _notes(world, t["id"])
    assert writeoff_itc.reverse_on_new_bill(world, note)["total"] == 0.0
    assert _reversals(world) == []


def test_found_at_the_sender_after_a_failed_note_and_a_sale_still_books_the_note(world, monkeypatch):
    """Dhanbad -> Bokaro, BVQX002 missing. Found at Dhanbad: the unit moves
    back, then the credit note fails once; the counter sells the piece. The
    second press books exactly one note and resolves the piece."""
    from api.routers import transfers

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _scan_in(t["id"], "BVQX003", user=MGR_BOK)
    _complete(t["id"], user=MGR_BOK)
    real = transfers._short_credit_note
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HTTPException(status_code=503, detail="database blip")
        return real(*a, **k)

    monkeypatch.setattr(transfers, "_short_credit_note", _flaky)
    with pytest.raises(HTTPException) as exc:
        _run(transfers.found_missing_piece(t["id"], "SU-2", transfers.MissingFound(at="source"), MGR_DHN))
    assert _status(exc) == 503
    world["stock_units"].update_one({"stock_id": "SU-2"}, {"$set": {"status": "SOLD"}})
    _run(transfers.found_missing_piece(t["id"], "SU-2", transfers.MissingFound(at="source"), MGR_DHN))
    (note,) = _notes(world, t["id"])
    assert note["credited_stock_ids"] == ["SU-2"]
    assert _run(transfers.list_missing(ADMIN))["pieces"] == []
    assert _unit(world, "SU-2")["status"] == "SOLD"


# ---------------------------------------------------------------------------
# Review round 5: a kept piece shares the invoice line NET of its credit note
# ---------------------------------------------------------------------------


def _unequal_box(world, lost):
    """Dhanbad (Z) ships BVQX001 (3000) and BVQX002 (1000) to Bokaro (Y):
    the invoice line is 4000 + 100/100. The kept piece lands damaged; the
    other (`lost`) is written off on the way, its credit note at its own
    cost."""
    from api.routers import transfers

    world["stock_units"].update_one({"stock_id": "SU-1"}, {"$set": {"unit_cost": 3000.0}})
    world["stock_units"].update_one({"stock_id": "SU-2"}, {"$set": {"unit_cost": 1000.0}})
    t = _shipped_box(world, codes=("BVQX001", "BVQX002"))
    kept = "SU-2" if lost == "SU-1" else "SU-1"
    _scan_in(t["id"], f"BVQX00{kept[-1]}", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _complete(t["id"], user=MGR_BOK)
    _run(transfers.write_off_missing_piece(t["id"], lost, ADMIN))
    (invoice,) = [b for b in _bills(world, t["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    assert (invoice["taxable_amount"], invoice["cgst_total"], invoice["sgst_total"]) == (4000.0, 100.0, 100.0)
    assert len(_notes(world, t["id"])) == 1
    return kept, invoice


def _write_off(world, sid):
    from datetime import datetime as _dt

    from api.services import writeoff_itc

    return writeoff_itc.record_writeoff(world, [_unit(world, sid)], source_type="QUARANTINE_DAMAGE",
                                        source_id=sid, when=_dt.utcnow(), actor_id="u-admin")


@pytest.mark.parametrize("lost, share", [("SU-1", 25.0), ("SU-2", 75.0)])  # expensive / cheap lost
def test_a_kept_piece_reverses_what_the_receiver_kept_net_of_the_credit_note(world, lost, share):
    """Expensive piece (3000) lost: Bokaro keeps 25/25 of credit, so writing
    the 1000 piece off takes back 25/25 -- not half the gross line (50/50).
    Cheap piece lost: Bokaro keeps 75/75, and that is what goes back."""
    kept, invoice = _unequal_box(world, lost)
    out = _write_off(world, kept)
    row = world["itc_writeoff_reversals"].find_one({"_id": kept})
    assert (row["bill_id"], row["gstin"]) == (invoice["bill_id"], GSTIN_Y_JH)
    assert (row["cgst"], row["sgst"], row["igst"]) == (share, share, 0.0)
    assert out["total"] == 2 * share


def test_a_kept_piece_back_after_its_write_off_reclaims_exactly_what_it_reversed(world, monkeypatch):
    """Cheap piece lost, the 3000 piece written off (75/75), its month filed,
    then it comes back: the re-claim is exactly 75/75, and writing it off
    again reverses 75/75 once more."""
    from api.services import writeoff_itc

    kept, _invoice = _unequal_box(world, "SU-2")
    _write_off(world, kept)
    filed = world["itc_writeoff_reversals"].find_one({"_id": kept})["period"]
    monkeypatch.setattr(writeoff_itc, "_locked", lambda db, period: period == filed)
    writeoff_itc.undo_writeoff(world, _unit(world, kept))
    (back,) = world["itc_writeoff_reversals"].find({"kind": "RECLAIM"})
    assert (back["cgst"], back["sgst"], back["igst"]) == (-75.0, -75.0, 0.0)
    monkeypatch.setattr(writeoff_itc, "_locked", lambda db, period: False)
    _write_off(world, kept)
    row = world["itc_writeoff_reversals"].find_one({"_id": kept})
    assert (row["cgst"], row["sgst"]) == (75.0, 75.0)


# ---------------------------------------------------------------------------
# Review round 6: a kept piece written off BEFORE the credit note is trued up
# ---------------------------------------------------------------------------


def _kept_first_box(world, lost):
    """_unequal_box, but the kept (damaged) piece is written off from
    quarantine BEFORE the admin writes the missing one off: its row reverses
    the gross 50/50, the credit note comes after."""
    world["stock_units"].update_one({"stock_id": "SU-1"}, {"$set": {"unit_cost": 3000.0}})
    world["stock_units"].update_one({"stock_id": "SU-2"}, {"$set": {"unit_cost": 1000.0}})
    t = _shipped_box(world, codes=("BVQX001", "BVQX002"))
    kept = "SU-2" if lost == "SU-1" else "SU-1"
    _scan_in(t["id"], f"BVQX00{kept[-1]}", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _complete(t["id"], user=MGR_BOK)
    _write_off(world, kept)
    assert world["itc_writeoff_reversals"].find_one({"_id": kept})["cgst"] == 50.0
    (invoice,) = [b for b in _bills(world, t["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    return t, kept, invoice


def _on_invoice(world, invoice, kind="REVERSAL"):
    rows = list(world["itc_writeoff_reversals"].find({"bill_id": invoice["bill_id"], "kind": kind}))
    return tuple(round(sum(r.get(h) or 0 for r in rows), 2) for h in ("cgst", "sgst", "igst"))


@pytest.mark.parametrize("lost, share", [("SU-1", 25.0), ("SU-2", 75.0)])  # expensive / cheap lost
def test_a_kept_piece_written_off_before_the_credit_note_is_trued_up_to_it(world, lost, share):
    """Bokaro writes the damaged piece off first (50/50 of the gross line),
    then the admin writes the missing one off: the credit note trues the
    invoice's reversal up to what Bokaro kept -- 25/25 when the 3000 piece is
    lost, 75/75 when the 1000 piece is -- and the month's 4(B)(1) says so."""
    from api.routers import transfers
    from api.services import writeoff_itc

    t, _kept, invoice = _kept_first_box(world, lost)
    _run(transfers.write_off_missing_piece(t["id"], lost, ADMIN))
    assert _on_invoice(world, invoice) == (share, share, 0.0)
    period = writeoff_itc._period(datetime.utcnow())
    assert writeoff_itc.month_reversal(world, GSTIN_Y_JH, period)["total"] == 2 * share
    # The true-up run again (a retried hook) books nothing more.
    (note,) = _notes(world, t["id"])
    writeoff_itc.reverse_on_new_bill(world, note)
    assert _on_invoice(world, invoice) == (share, share, 0.0)


@pytest.mark.parametrize("lost, share", [("SU-1", 25.0), ("SU-2", 75.0)])
def test_found_at_the_sender_after_a_kept_write_off_trues_it_up(world, lost, share):
    """The missing piece turns up back at Dhanbad after Bokaro wrote the
    damaged one off: the credit note for it trues the reversal up the same
    way."""
    from api.routers import transfers

    t, _kept, invoice = _kept_first_box(world, lost)
    _run(transfers.found_missing_piece(t["id"], lost, transfers.MissingFound(at="source"), MGR_DHN))
    assert len(_notes(world, t["id"])) == 1
    assert _on_invoice(world, invoice) == (share, share, 0.0)


def test_an_equal_cost_kept_piece_is_the_same_in_either_order(world):
    """Three equal pieces: the damaged one written off before the lost one's
    credit note still reverses 46.25/46.25 -- the note leaves it alone."""
    from api.routers import transfers

    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX001", user=MGR_BOK)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _complete(t["id"], user=MGR_BOK)
    _write_off(world, "SU-2")
    _run(transfers.write_off_missing_piece(t["id"], "SU-3", ADMIN))
    (invoice,) = [b for b in _bills(world, t["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    assert _on_invoice(world, invoice) == (46.25, 46.25, 0.0)


def test_a_filed_kept_write_off_gives_the_excess_back_and_its_undo_nets_it(world, monkeypatch):
    """The 3000 piece is lost; the 1000 piece was written off at 50/50 and
    that month FILED. The note re-claims the 25/25 excess in the first open
    month (the filed row stands). The piece then comes back: only the 25/25
    still reversed is re-claimed -- 50/50 in all, never 75/75."""
    from api.routers import transfers
    from api.services import writeoff_itc

    t, kept, invoice = _kept_first_box(world, "SU-1")
    filed = world["itc_writeoff_reversals"].find_one({"_id": kept})["period"]
    monkeypatch.setattr(writeoff_itc, "_locked", lambda db, period: period == filed)
    _run(transfers.write_off_missing_piece(t["id"], "SU-1", ADMIN))
    assert world["itc_writeoff_reversals"].find_one({"_id": kept})["cgst"] == 50.0
    nxt = writeoff_itc._next_month(filed)
    assert writeoff_itc.month_reclaim(world, GSTIN_Y_JH, nxt)["cgst"] == 25.0
    done, heads = writeoff_itc._recorded(world, {"bill_id": invoice["bill_id"]}, "pieces")
    assert (done, heads["cgst"]) == (1.0, 25.0)
    writeoff_itc.undo_writeoff(world, _unit(world, kept))
    assert writeoff_itc.month_reclaim(world, GSTIN_Y_JH, nxt)["cgst"] == 50.0
    done, heads = writeoff_itc._recorded(world, {"bill_id": invoice["bill_id"]}, "pieces")
    assert (done, heads["cgst"]) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Review round 7: an undo holds the line at the pieces still lost, both sides
# ---------------------------------------------------------------------------


def _three_cost_box(world):
    """Three pieces 3000/1000/2000 (invoice 150/150); SU-2 and SU-3 land
    damaged and are written off (50/50 each), then SU-1 -- missing -- is
    written off: its credit note (75/75) leaves 75/75 on the two kept pieces,
    given back unevenly (SU-2 25, SU-3 50)."""
    from api.routers import transfers

    for sid, cost in (("SU-1", 3000.0), ("SU-2", 1000.0), ("SU-3", 2000.0)):
        world["stock_units"].update_one({"stock_id": sid}, {"$set": {"unit_cost": cost}})
    t = _shipped_box(world)
    _scan_in(t["id"], "BVQX002", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _scan_in(t["id"], "BVQX003", user=MGR_BOK, damaged=True, reason="Cracked rim")
    _complete(t["id"], user=MGR_BOK)
    (invoice,) = [b for b in _bills(world, t["id"]) if b.get("doc_type") != "TRANSFER_CREDIT_NOTE"]
    assert invoice["cgst_total"] == 150.0
    _write_off(world, "SU-2")
    _write_off(world, "SU-3")
    _run(transfers.write_off_missing_piece(t["id"], "SU-1", ADMIN))
    assert _on_invoice(world, invoice) == (75.0, 75.0, 0.0)
    return invoice


@pytest.mark.parametrize("back", ["SU-2", "SU-3"])
def test_one_of_two_kept_pieces_back_after_the_note_holds_the_other_at_its_slice(world, back):
    """Either piece back, the other still reverses 37.5/37.5 -- half of what
    Bokaro kept -- never the 25/25 the uneven give-back left on SU-2. Written
    off again, the line is back at 75/75."""
    from api.services import writeoff_itc

    invoice = _three_cost_box(world)
    writeoff_itc.undo_writeoff(world, _unit(world, back))
    assert _on_invoice(world, invoice) == (37.5, 37.5, 0.0)
    _write_off(world, back)
    assert _on_invoice(world, invoice) == (75.0, 75.0, 0.0)


def test_a_kept_piece_back_into_a_filed_month_tops_up_in_the_open_month(world, monkeypatch):
    """The write-offs' month is filed when SU-3 comes back: the filed return
    stands (75/75), SU-3's 50/50 is re-claimed and the 12.5/12.5 SU-2 is short
    is topped up, both in the first open month -- 37.5/37.5 held in all."""
    from api.services import writeoff_itc

    invoice = _three_cost_box(world)
    filed = world["itc_writeoff_reversals"].find_one({"_id": "SU-2"})["period"]
    monkeypatch.setattr(writeoff_itc, "_locked", lambda db, period: period == filed)
    writeoff_itc.undo_writeoff(world, _unit(world, "SU-3"))
    nxt = writeoff_itc._next_month(filed)
    assert world["itc_writeoff_reversals"].find_one({"_id": "SU-2"})["cgst"] == 25.0
    assert writeoff_itc.month_reversal(world, GSTIN_Y_JH, filed)["cgst"] == 75.0
    assert writeoff_itc.month_reversal(world, GSTIN_Y_JH, nxt)["cgst"] == 12.5
    assert writeoff_itc.month_reclaim(world, GSTIN_Y_JH, nxt)["cgst"] == 50.0
    _done, heads = writeoff_itc._recorded(world, {"bill_id": invoice["bill_id"]}, "pieces")
    assert heads["cgst"] == 37.5
