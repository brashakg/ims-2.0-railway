"""F51 / D13 -- a stock transfer between two GST registrations prints a VALUED
delivery challan.

Audit row F51 (Transfer > Delivery Challan, Dhanbad -> Bokaro): the challan is
numbered by an internal code (DC/TRF/<last 8 of the doc id>) instead of the
transfer's TRF-YYYYMM-NNNN; it has no HSN, no value, no consignee GSTIN or
address; the serial column is empty; the carrier reads "None"; the transfer is
saved at Rs 0. Dhanbad and Bokaro are different companies, so this is a Rs 0
paper between two registrations.

Owner ruling D13 (2026-09-29): such a transfer carries a valued delivery
challan -- value at cost, HSN, both GSTINs, the SAME number as the transfer --
until the CA confirms otherwise; keep that decision in one place so it can
later become a tax invoice. Same-registration transfers keep today's challan.
The value is the units' OWN cost (stamped on each unit at its goods receipt),
never the product master's average; the challan is never a sale on GSTR-1.

The flow traced (main @ 6068802):
  modal  StockTransferModal.tsx:236 sends no cost -> inventory.ts:73 unit_cost 0
  create transfers.py:1103-1106 total_value = sum(client unit_cost * qty) = 0;
         lines keep only the client's fields (no hsn_code, no cost) :1112
  ship   transfers.py:1376 _apply_ship_stock_move :524 claims the AVAILABLE
         units (shipped_stock_ids) but never reads their cost; the history note
         is f"Shipped via {courier_name}" with courier_name None :1440
  print  print_documents.py:176 numbers it _challan_number("TRF", id) :222,
         hsn from the line (never stored), qty = quantity_requested :211,
         serial = serial_number or notes :215, no consignee GSTIN/address :226;
         print_render.render_delivery_challan :397 has no value column at all
  close  transfers.py:1713 _book_mirror_purchase -> the FIN-3 mirror bill reads
         the same Rs 0 line cost (:2167) and books taxable 0

Every finding test is xfail(strict=True) with its id: an unexpected pass fails
the suite, so the build must remove the marker in the commit that fixes it.
The unmarked tests are guards that already hold and must keep holding.

Runs on mongomock behind the real repositories (StockRepository's atomic
claim, the transfers persistence, print_identity's store/entity loads). No
Shopify call: the ship write-back is stubbed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api.routers import print_documents, transfers  # noqa: E402

mongomock = pytest.importorskip("mongomock")


def _xfail(finding: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"{finding}: {why}")


# Checksum-valid synthetic registrations.
GSTIN_Z_JH = "20AAACZ1111A1ZH"  # company Z, Jharkhand (Dhanbad shops)
GSTIN_Z_MH = "27AAACZ1111A1Z3"  # company Z, Maharashtra (Pune shop)
GSTIN_Y_JH = "20AAACY2222B1ZA"  # company Y, Jharkhand (Bokaro shop)

UNIT_COST = 1850.0  # each unit's own cost, stamped at its goods receipt
PRODUCT_COST = 2000.0  # the product master's average -- NOT the units' own
HSN = "900311"
BARCODES = ("BVQXKDNA01", "BVQXKDNA02")

ENTITIES = [
    {
        "entity_id": "ENT-Z",
        "legal_name": "Zed Opticals Pvt Ltd",
        "gstins": [
            {"gstin": GSTIN_Z_JH, "state_code": "20", "state_name": "Jharkhand", "is_primary": True},
            {"gstin": GSTIN_Z_MH, "state_code": "27", "state_name": "Maharashtra"},
        ],
    },
    {
        "entity_id": "ENT-Y",
        "legal_name": "Wye Vision LLP",
        "gstins": [{"gstin": GSTIN_Y_JH, "state_code": "20", "state_name": "Jharkhand"}],
    },
]


def _store(sid, name, entity, gstin, state, code, address, city, pin):
    return {
        "store_id": sid, "store_name": name, "name": name, "entity_id": entity,
        "gstin": gstin, "state": state, "state_code": code, "address": address,
        "city": city, "pincode": pin, "phone": "9000000001", "store_type": "PHYSICAL",
    }


STORES = [
    _store("ST-DHN-1", "Hirapur Dhanbad", "ENT-Z", GSTIN_Z_JH, "Jharkhand", "20",
           "Shop 33 Park Market", "Dhanbad", "826001"),
    _store("ST-DHN-2", "Bank More Dhanbad", "ENT-Z", GSTIN_Z_JH, "Jharkhand", "20",
           "Bank More Chowk", "Dhanbad", "826009"),
    _store("ST-BOK-1", "Sector 4 Bokaro", "ENT-Y", GSTIN_Y_JH, "Jharkhand", "20",
           "Plot 7 Ram Mandir Road", "Bokaro", "827004"),
    _store("ST-PUN-1", "FC Road Pune", "ENT-Z", GSTIN_Z_MH, "Maharashtra", "27",
           "FC Road", "Pune", "411004"),
]

PRODUCT = {
    "product_id": "P-CA8895", "sku": "FR-CARRERA-CA8895-807-54",
    "name": "Carrera CA 8895 Rectangle Black", "category": "FRAME",
    "hsn_code": HSN, "cost_price": PRODUCT_COST, "mrp": 6500.0,
}


def _user(role, store="ST-DHN-1"):
    return {
        "user_id": f"u-{role.lower()}", "username": role.lower(), "roles": [role],
        "store_ids": [store] if store else [], "active_store_id": store,
    }


ADMIN = _user("ADMIN", None)
SOURCE_MANAGER = _user("STORE_MANAGER", "ST-DHN-1")


class _Conn:
    """What api.dependencies.get_db hands out: .is_connected, .db, attr access."""

    is_connected = True

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return self.db[name]


@pytest.fixture
def db(monkeypatch):
    client = mongomock.MongoClient()
    handle = client[f"ims_f51_{uuid.uuid4().hex[:8]}"]
    handle["entities"].insert_many([dict(e) for e in ENTITIES])
    handle["stores"].insert_many([dict(s) for s in STORES])
    handle["products"].insert_one(dict(PRODUCT))
    handle["stock_units"].insert_many(
        [
            {
                "stock_id": f"SU-{n}", "product_id": PRODUCT["product_id"],
                "store_id": "ST-DHN-1", "status": "AVAILABLE", "barcode": code,
                "unit_cost": UNIT_COST, "cost_price": UNIT_COST, "cost_source": "GRN_PO",
            }
            for n, code in enumerate(BARCODES, start=1)
        ]
    )
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(handle))
    # The ship write-back is the online (Shopify) stock path: never reached here.
    monkeypatch.setattr(transfers, "_writeback_units_left", lambda *a, **k: None)
    transfers.STOCK_TRANSFERS.clear()
    yield handle
    client.close()


def _run(coro):
    return asyncio.run(coro)


def _create(to_store, qty=2, unit_cost=0.0, by=ADMIN):
    """The transfer modal's exact payload: inventory.ts sends unit_cost ?? 0."""
    body = transfers.TransferInput(
        transfer_type="store_to_store",
        from_location_id="ST-DHN-1", from_location_name="Hirapur Dhanbad",
        to_location_id=to_store,
        to_location_name=next(s["store_name"] for s in STORES if s["store_id"] == to_store),
        notes="Audit transfer for a customer",
        items=[
            transfers.TransferItemInput(
                product_id=PRODUCT["product_id"], sku=PRODUCT["sku"],
                product_name=PRODUCT["name"], quantity_requested=qty, unit_cost=unit_cost,
            )
        ],
    )
    return _run(transfers.create_transfer(body, by))["transfer"]


def _ship(tid, courier_name=None):
    return _run(
        transfers.ship_transfer(tid, None, None, courier_name, False, SOURCE_MANAGER)
    )["transfer"]


def _challan(tid, user=SOURCE_MANAGER):
    resp = _run(print_documents.delivery_challan_for_transfer(tid, "ORIGINAL", False, user))
    return resp.body.decode("utf-8")


def _shipped(to_store, qty=2):
    t = _create(to_store, qty=qty)
    _ship(t["id"])
    return t


def _shows_amount(text: str, amount: float) -> bool:
    """True when `amount` is printed as a figure (1850, 1850.00, 1,850.00)."""
    flat = text.replace(",", "")
    whole = f"{amount:.2f}".rstrip("0").rstrip(".")
    return re.search(rf"(?<![\d.]){re.escape(whole)}(?:\.0+)?(?![\d.])", flat) is not None


# ===========================================================================
# F51 -- the Dhanbad -> Bokaro challan (two companies, two GSTINs)
# ===========================================================================


@_xfail("F51", "the challan is numbered DC/TRF/<last 8 of the doc id> "
        "(print_documents.py:222), not the transfer's TRF-YYYYMM-NNNN")
def test_f51_challan_number_is_the_transfer_number(db):
    t = _shipped("ST-BOK-1")
    assert t["transfer_number"].startswith("TRF-")
    assert t["transfer_number"] in _challan(t["id"])


@_xfail("F51", "the HSN is read from the transfer line (print_documents.py:210), "
        "which create_transfer never stamps; the product master is not read")
def test_f51_challan_lines_carry_the_product_hsn(db):
    t = _shipped("ST-BOK-1")
    assert HSN in _challan(t["id"])


@_xfail("F51", "render_delivery_challan (print_render.py:397) has no value column "
        "and the transfer line carries the modal's Rs 0, never the units' own cost")
def test_f51_challan_values_the_units_at_their_own_cost(db):
    t = _shipped("ST-BOK-1")
    html = _challan(t["id"])
    assert _shows_amount(html, UNIT_COST), "per-unit value at the unit's own cost"
    assert _shows_amount(html, 2 * UNIT_COST), "line / challan total value"
    # The product master's average is not the value of THESE units.
    assert not _shows_amount(html, PRODUCT_COST)
    assert not _shows_amount(html, 2 * PRODUCT_COST)


@_xfail("F51", "create_transfer saves total_value = client unit_cost * qty "
        "(transfers.py:1104) = Rs 0 from the modal, and ship never re-values the "
        "units it claimed")
def test_f51_transfer_is_saved_at_the_units_own_cost(db):
    t = _shipped("ST-BOK-1")
    stored = transfers._get_transfer(t["id"])
    assert stored["total_value"] == pytest.approx(2 * UNIT_COST)
    assert [ln.get("unit_cost") for ln in stored["items"]] == [pytest.approx(UNIT_COST)]


@_xfail("F51", "the value must count what LEFT the shop: the challan prints "
        "quantity_requested (print_documents.py:211), not the units ship claimed")
def test_f51_value_counts_the_units_that_left_not_the_request(db):
    t = _shipped("ST-BOK-1", qty=3)  # only 2 units on the shelf
    stored = transfers._get_transfer(t["id"])
    assert stored["items"][0]["quantity_shipped"] == 2
    html = _challan(t["id"])
    assert _shows_amount(html, 2 * UNIT_COST)
    assert not _shows_amount(html, 3 * UNIT_COST)
    assert stored["total_value"] == pytest.approx(2 * UNIT_COST)


@_xfail("F51", "the transfer challan passes no consignee GSTIN or address "
        "(print_documents.py:219-232); only the consignor's header GSTIN prints")
def test_f51_challan_names_both_gstins_and_the_consignee_address(db):
    t = _shipped("ST-BOK-1")
    html = _challan(t["id"])
    assert GSTIN_Z_JH in html, "consignor GSTIN (Dhanbad's own)"
    assert GSTIN_Y_JH in html, "consignee GSTIN (Bokaro's own)"
    assert "Plot 7 Ram Mandir Road" in html and "827004" in html


@_xfail("F51", "the serial column prints serial_number or the line notes "
        "(print_documents.py:215); the shipped units' barcodes are never read")
def test_f51_challan_serial_column_prints_the_unit_barcodes(db):
    t = _shipped("ST-BOK-1")
    html = _challan(t["id"])
    for code in BARCODES:
        assert code in html


@_xfail("F51", "ship writes f\"Shipped via {courier_name}\" with courier_name None "
        "(transfers.py:1440), so the timeline reads 'Shipped via None'")
def test_f51_ship_without_a_carrier_never_reads_none(db):
    t = _create("ST-BOK-1")
    shipped = _ship(t["id"], courier_name=None)
    notes = [h.get("notes") or "" for h in shipped.get("status_history") or []]
    assert notes and not any("None" in n for n in notes), notes


@_xfail("F51", "an inter-company challan printed before ship renders with no "
        "value (the units are not chosen yet) instead of being refused")
def test_f51_challan_before_ship_is_refused_or_valued(db):
    t = _create("ST-BOK-1")  # APPROVED, not shipped
    try:
        html = _challan(t["id"])
    except HTTPException as exc:
        assert 400 <= exc.status_code < 500
        return
    assert _shows_amount(html, UNIT_COST)


@_xfail("F51", "the transfer challan never requires a GSTIN on either side "
        "(assert_issuing_identity without require_gstin, print_documents.py:198) "
        "and never looks for the consignee's: a GST paper between two "
        "registrations prints with one missing")
@pytest.mark.parametrize("side", ["consignor", "consignee"])
def test_f51_challan_is_refused_when_a_side_has_no_gstin(db, side):
    store_id, entity_id = ("ST-DHN-1", "ENT-Z") if side == "consignor" else ("ST-BOK-1", "ENT-Y")
    t = _shipped("ST-BOK-1")
    db["stores"].update_one({"store_id": store_id}, {"$set": {"gstin": ""}})
    db["entities"].update_one({"entity_id": entity_id}, {"$set": {"gstins": []}})
    with pytest.raises(HTTPException) as exc:
        _challan(t["id"])
    assert 400 <= exc.value.status_code < 500


# ===========================================================================
# D13 -- one rule: valued exactly when the move crosses a GST registration
# ===========================================================================


@pytest.mark.parametrize(
    "to_store",
    [
        # Same company, same GSTIN: today's challan, never a value (guard).
        "ST-DHN-2",
        pytest.param("ST-BOK-1", marks=_xfail(
            "D13", "a different company is never valued (no value column at all)")),
        pytest.param("ST-PUN-1", marks=_xfail(
            "D13", "same company, different GSTIN (Jharkhand -> Maharashtra) is the "
            "same registration boundary the FIN-3 mirror bill already books "
            "(transfers.py:2247); read as 'different GSTINs' -> valued")),
    ],
)
def test_d13_challan_is_valued_exactly_when_the_gstins_differ(db, to_store):
    t = _shipped(to_store)
    html = _challan(t["id"])
    crosses = to_store != "ST-DHN-2"
    assert _shows_amount(html, 2 * UNIT_COST) is crosses


@_xfail("D13", "one value per transfer: the FIN-3 mirror bill (transfers.py:2247) "
        "reads the same Rs 0 line cost and books taxable 0 for units that cost "
        "3700; whatever the CA decides about that bill, it never disagrees with "
        "the challan")
def test_d13_any_mirror_bill_carries_the_challan_value(db):
    t = _shipped("ST-BOK-1")
    line_id = transfers._get_transfer(t["id"])["items"][0]["id"]
    _run(transfers.receive_transfer(
        t["id"], [transfers.TransferItemReceive(transfer_item_id=line_id, quantity_received=2)], ADMIN,
    ))
    _run(transfers.complete_transfer(t["id"], None, ADMIN))
    bills = list(db["vendor_bills"].find({"source_transfer_id": t["id"]}))
    assert [b.get("taxable_amount") for b in bills] == [pytest.approx(2 * UNIT_COST)] * len(bills)
    assert bills, "the mirror bill is still booked at complete today"


def test_d13_printing_the_challan_writes_nothing(db):
    """Guard: the challan is a paper, never a sale -- printing it books no
    invoice, order, bill or GSTR-1 row. Holds today; must keep holding."""
    t = _shipped("ST-BOK-1")

    def snapshot():
        return {
            name: sorted(json.dumps(d, default=str, sort_keys=True) for d in db[name].find({}, {"_id": 0}))
            for name in db.list_collection_names()
        }

    before = snapshot()
    _challan(t["id"])
    assert snapshot() == before


# ===========================================================================
# D7 -- counter roles never see cost, including the transfer's new value
# ===========================================================================


def test_d7_counter_role_challan_carries_no_cost(db):
    """Guard: a SALES_STAFF may print a transfer challan today; once it is
    valued, cost must not reach a counter role (refused, or printed without
    the figures). Holds today because nothing is valued; must keep holding."""
    t = _shipped("ST-BOK-1")
    try:
        html = _challan(t["id"], _user("SALES_STAFF"))
    except HTTPException as exc:
        assert exc.status_code == 403
        return
    assert not _shows_amount(html, UNIT_COST)
    assert not _shows_amount(html, 2 * UNIT_COST)


@_xfail("D7", "GET /transfers and /transfers/{id} are AUTHENTICATED and return "
        "each line's unit_cost and the total_value unmasked (transfers.py:905, "
        ":1034) -- a valued transfer would hand counter staff the cost")
def test_d7_counter_role_reading_a_valued_transfer_sees_no_cost(db):
    t = _create("ST-BOK-1", unit_cost=UNIT_COST)  # a costed line (the API allows it)
    staff = _user("SALES_STAFF")
    one = _run(transfers.get_transfer(t["id"], staff))
    many = _run(transfers.list_transfers(
        status=None, transfer_type=None, from_location_id=None, to_location_id=None,
        store_id=None, priority=None, created_after=None, created_before=None,
        limit=50, page=1, current_user=staff,
    ))
    assert many["transfers"], "the counter role can list the transfer"
    for payload in (one, many):
        text = json.dumps(payload, default=str)
        assert not _shows_amount(text, UNIT_COST)
        assert not _shows_amount(text, 2 * UNIT_COST)
