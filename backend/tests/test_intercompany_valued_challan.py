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

The fix: ship stamps each line with the units' barcodes, the product's HSN and
the units' own cost (transfers._stamp_shipped_value) and re-values the
transfer; the challan and the FIN-3 mirror bill share ONE "crosses a GST
registration" rule (transfers._transfer_registrations); a crossing challan is
valued, needs both GSTINs, is refused before ship and to counter roles (D7);
transfer reads hide cost from counter roles.

The flow as it was (main @ 6068802):
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

Each test names its finding (F51 / D13 / D7). The guards (printing writes
nothing; a counter role's challan carries no cost; the same-GSTIN challan stays
unvalued) held before the fix and must keep holding.

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


def test_f51_challan_number_is_the_transfer_number(db):
    t = _shipped("ST-BOK-1")
    assert t["transfer_number"].startswith("TRF-")
    assert t["transfer_number"] in _challan(t["id"])


def test_f51_challan_lines_carry_the_product_hsn(db):
    t = _shipped("ST-BOK-1")
    assert HSN in _challan(t["id"])


def test_f51_challan_values_the_units_at_their_own_cost(db):
    t = _shipped("ST-BOK-1")
    html = _challan(t["id"])
    assert _shows_amount(html, UNIT_COST), "per-unit value at the unit's own cost"
    assert _shows_amount(html, 2 * UNIT_COST), "line / challan total value"
    # The product master's average is not the value of THESE units.
    assert not _shows_amount(html, PRODUCT_COST)
    assert not _shows_amount(html, 2 * PRODUCT_COST)


def test_f51_transfer_is_saved_at_the_units_own_cost(db):
    t = _shipped("ST-BOK-1")
    stored = transfers._get_transfer(t["id"])
    assert stored["total_value"] == pytest.approx(2 * UNIT_COST)
    assert [ln.get("unit_cost") for ln in stored["items"]] == [pytest.approx(UNIT_COST)]


def test_f51_value_counts_the_units_that_left_not_the_request(db):
    t = _shipped("ST-BOK-1", qty=3)  # only 2 units on the shelf
    stored = transfers._get_transfer(t["id"])
    assert stored["items"][0]["quantity_shipped"] == 2
    html = _challan(t["id"])
    assert _shows_amount(html, 2 * UNIT_COST)
    assert not _shows_amount(html, 3 * UNIT_COST)
    assert stored["total_value"] == pytest.approx(2 * UNIT_COST)
    # The quantity printed is what left (2), not the request (3).
    assert "<strong>2</strong>" in html and "<strong>3</strong>" not in html


def test_f51_challan_names_both_gstins_and_the_consignee_address(db):
    t = _shipped("ST-BOK-1")
    html = _challan(t["id"])
    # The consignor / consignee block itself (the letterhead also prints the
    # issuing shop's GSTIN, so a page-wide search would not see it go).
    party = html[html.index('class="party-grid"'):html.index('class="lines"')]
    assert GSTIN_Z_JH in party, "consignor GSTIN (Dhanbad's own)"
    assert GSTIN_Y_JH in party, "consignee GSTIN (Bokaro's own)"
    assert "Plot 7 Ram Mandir Road" in party and "827004" in party


def test_f51_challan_serial_column_prints_the_unit_barcodes(db):
    t = _shipped("ST-BOK-1")
    html = _challan(t["id"])
    for code in BARCODES:
        assert code in html


def test_f51_ship_without_a_carrier_never_reads_none(db):
    t = _create("ST-BOK-1")
    shipped = _ship(t["id"], courier_name=None)
    notes = [h.get("notes") or "" for h in shipped.get("status_history") or []]
    assert notes and not any("None" in n for n in notes), notes


def test_f51_challan_before_ship_is_refused(db):
    """The value is what leaves the shop, chosen at ship: before that the
    crossing challan is refused with "ship first", not printed unvalued."""
    t = _create("ST-BOK-1")  # APPROVED, not shipped
    with pytest.raises(HTTPException) as exc:
        _challan(t["id"])
    assert exc.value.status_code == 409
    assert "Ship the transfer first" in str(exc.value.detail)


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
        # A different company.
        "ST-BOK-1",
        # Same company, different GSTIN (Jharkhand -> Maharashtra): the same
        # registration boundary the FIN-3 mirror bill books on -> valued.
        "ST-PUN-1",
    ],
)
def test_d13_challan_is_valued_exactly_when_the_gstins_differ(db, to_store):
    t = _shipped(to_store)
    html = _challan(t["id"])
    crosses = to_store != "ST-DHN-2"
    assert _shows_amount(html, 2 * UNIT_COST) is crosses


def _receive_and_complete(t):
    line_id = transfers._get_transfer(t["id"])["items"][0]["id"]
    _run(transfers.receive_transfer(
        t["id"], [transfers.TransferItemReceive(transfer_item_id=line_id, quantity_received=2)], ADMIN,
    ))
    _run(transfers.complete_transfer(t["id"], None, ADMIN))


def test_d13_any_mirror_bill_carries_the_challan_value(db):
    t = _shipped("ST-BOK-1")
    _receive_and_complete(t)
    bills = list(db["vendor_bills"].find({"source_transfer_id": t["id"]}))
    assert [b.get("taxable_amount") for b in bills] == [pytest.approx(2 * UNIT_COST)] * len(bills)
    assert bills, "the mirror bill is still booked at complete today"
    assert bills[0]["lines"][0]["hsn"] == HSN, "the bill's HSN is the challan's"


def test_d13_the_paper_and_the_books_ask_one_rule(db, monkeypatch):
    """One "crosses a GST registration" rule: flip it and BOTH the challan and
    the FIN-3 mirror bill follow -- neither keeps a private copy."""
    real = transfers._transfer_registrations
    monkeypatch.setattr(
        transfers, "_transfer_registrations", lambda d, tr: (*real(d, tr)[:2], False)
    )
    t = _shipped("ST-BOK-1")
    assert not _shows_amount(_challan(t["id"]), 2 * UNIT_COST)
    _receive_and_complete(t)
    assert db["vendor_bills"].count_documents({"source_transfer_id": t["id"]}) == 0


def test_d13_printing_the_challan_writes_nothing(db):
    """Guard: the challan is a paper, never a sale -- printing it books no
    invoice, order, bill or GSTR-1 row."""
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
    """A valued challan carries cost, so a counter role is refused it (403);
    the same role still prints a same-registration (unvalued) challan."""
    staff = _user("SALES_STAFF")
    t = _shipped("ST-BOK-1")
    with pytest.raises(HTTPException) as exc:
        _challan(t["id"], staff)
    assert exc.value.status_code == 403

    db["stock_units"].insert_many([
        {"stock_id": f"SU-X{n}", "product_id": PRODUCT["product_id"], "store_id": "ST-DHN-1",
         "status": "AVAILABLE", "barcode": f"BVQXKDNX0{n}", "unit_cost": UNIT_COST}
        for n in (1, 2)
    ])
    same = _shipped("ST-DHN-2")
    assert transfers._get_transfer(same["id"])["items"][0]["quantity_shipped"] == 2
    html = _challan(same["id"], staff)
    assert same["transfer_number"] in html and "BVQXKDNX01" in html
    assert not _shows_amount(html, UNIT_COST)
    assert not _shows_amount(html, 2 * UNIT_COST)


def test_d7_counter_role_reading_a_valued_transfer_sees_no_cost(db):
    t = _create("ST-BOK-1", unit_cost=UNIT_COST)  # a costed line (the API allows it)
    staff = _user("SALES_STAFF")
    one = _run(transfers.get_transfer(t["id"], staff))
    many = _run(transfers.list_transfers(
        status=None, transfer_type=None, from_location_id=None, to_location_id=None,
        store_id=None, priority=None, created_after=None, created_before=None,
        limit=50, page=1, current_user=staff,
    ))
    pending = _run(transfers.get_pending_transfers(None, staff))
    summary = _run(transfers.get_transfer_analytics(None, None, None, staff))
    where = _run(transfers.get_location_transfer_analytics("ST-DHN-1", staff))
    assert many["transfers"], "the counter role can list the transfer"
    assert pending["ready_to_ship"], "the counter role sees it pending"
    for payload in (one, many, pending, summary, where):
        text = json.dumps(payload, default=str)
        assert not _shows_amount(text, UNIT_COST)
        assert not _shows_amount(text, 2 * UNIT_COST)
    # The stored doc is untouched, and a manager still sees per-unit cost
    # (owner ruling 2026-09-28: managers yes, counter staff never).
    assert transfers._get_transfer(t["id"])["total_value"] == pytest.approx(2 * UNIT_COST)
    seen = json.dumps(_run(transfers.get_transfer(t["id"], SOURCE_MANAGER)), default=str)
    assert _shows_amount(seen, UNIT_COST) and _shows_amount(seen, 2 * UNIT_COST)


# ===========================================================================
# Where the value comes from when a unit carries no cost of its own
# ===========================================================================


def test_f51_unit_without_its_own_cost_is_valued_at_the_product_cost(db):
    """A unit with no cost of its own (an opening-stock row without one) is
    valued at the product master's cost -- shelf units carry the product's cost
    (owner ruling 2026-09-28)."""
    db["stock_units"].update_many({}, {"$unset": {"unit_cost": "", "cost_price": ""}})
    t = _shipped("ST-BOK-1")
    stored = transfers._get_transfer(t["id"])
    assert stored["items"][0]["unit_cost"] == pytest.approx(PRODUCT_COST)
    assert stored["total_value"] == pytest.approx(2 * PRODUCT_COST)
    assert _shows_amount(_challan(t["id"]), 2 * PRODUCT_COST)


def test_d13_a_unit_with_no_cost_anywhere_stops_the_ship(db):
    """No unit cost and no product cost: the move is refused BEFORE any unit
    leaves (the value is fixed at ship), and ships once the product has one."""
    db["stock_units"].update_one({"stock_id": "SU-2"}, {"$unset": {"unit_cost": "", "cost_price": ""}})
    db["products"].update_one({}, {"$set": {"cost_price": 0}})
    t = _create("ST-BOK-1")
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert exc.value.status_code == 409
    assert db["stock_units"].count_documents({"status": "AVAILABLE"}) == 2
    db["products"].update_one({}, {"$set": {"cost_price": PRODUCT_COST}})
    _ship(t["id"])
    assert _shows_amount(_challan(t["id"]), UNIT_COST + PRODUCT_COST)


@pytest.mark.parametrize("side", ["consignor", "consignee"])
def test_d13_ship_is_refused_when_a_side_has_no_gstin(db, side):
    """The paper must be printable before the goods leave: no GSTIN on one
    side of a registration-crossing move -> refused, nothing moved."""
    store_id, entity_id = ("ST-DHN-1", "ENT-Z") if side == "consignor" else ("ST-BOK-1", "ENT-Y")
    db["stores"].update_one({"store_id": store_id}, {"$set": {"gstin": ""}})
    db["entities"].update_one({"entity_id": entity_id}, {"$set": {"gstins": []}})
    t = _create("ST-BOK-1")
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert exc.value.status_code == 400
    assert db["stock_units"].count_documents({"status": "AVAILABLE"}) == 2


def test_f51_a_transfer_shipped_without_a_value_never_prints_at_rs_0(db):
    """A crossing transfer shipped before ship stamped values (or racing a
    cost-less unit) is refused, never printed at Rs 0 or short."""
    t = _shipped("ST-BOK-1")
    db["stock_transfers"].update_one({"id": t["id"]}, {"$set": {"items.0.unit_cost": 0}})
    with pytest.raises(HTTPException) as exc:
        _challan(t["id"])
    assert exc.value.status_code == 409
