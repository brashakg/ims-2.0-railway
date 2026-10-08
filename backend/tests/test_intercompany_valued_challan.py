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


def _create(to_store, qty=2, unit_cost=0.0, by=ADMIN, extra=()):
    """The transfer modal's exact payload: inventory.ts sends unit_cost ?? 0.
    `extra`: more TransferItemInput lines after the frame's."""
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
            ),
            *extra,
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
def test_d13_a_shipped_move_keeps_the_registrations_it_left_on(db, side):
    """r5: ship stamps its answer (transfers._transfer_registrations). A
    GSTIN lost after Dhanbad -> Bokaro shipped changes neither the paper nor
    the books: the challan still prints valued with the two GSTINs the goods
    left on, and the mirror bill books between those two. (A move with a
    GSTIN missing is refused at ship instead --
    test_d13_ship_is_refused_when_a_side_has_no_gstin.)"""
    store_id, entity_id = ("ST-DHN-1", "ENT-Z") if side == "consignor" else ("ST-BOK-1", "ENT-Y")
    t = _shipped("ST-BOK-1")
    db["stores"].update_one({"store_id": store_id}, {"$set": {"gstin": ""}})
    db["entities"].update_one({"entity_id": entity_id}, {"$set": {"gstins": []}})
    html = _challan(t["id"])
    party = html[html.index('class="party-grid"'):html.index('class="lines"')]
    assert GSTIN_Z_JH in party and GSTIN_Y_JH in party
    assert _shows_amount(html, 2 * UNIT_COST)
    _receive_and_complete(t)
    (bill,) = db["vendor_bills"].find({"source_transfer_id": t["id"]})
    assert (bill["vendor_gstin"], bill["recipient_gstin"]) == (GSTIN_Z_JH, GSTIN_Y_JH)


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


def _receive_and_complete(t, received=2):
    line_id = transfers._get_transfer(t["id"])["items"][0]["id"]
    _run(transfers.receive_transfer(
        t["id"], [transfers.TransferItemReceive(transfer_item_id=line_id, quantity_received=received)],
        ADMIN,
    ))
    _run(transfers.complete_transfer(t["id"], None, ADMIN))


def test_d13_any_mirror_bill_carries_the_challan_value(db):
    t = _shipped("ST-BOK-1")
    _receive_and_complete(t)
    bills = list(db["vendor_bills"].find({"source_transfer_id": t["id"]}))
    assert [b.get("taxable_amount") for b in bills] == [pytest.approx(2 * UNIT_COST)] * len(bills)
    assert bills, "the mirror bill is still booked at complete today"
    assert bills[0]["lines"][0]["hsn"] == HSN, "the bill's HSN is the challan's"


def test_d13_short_receipt_bill_states_its_basis_and_the_challan_value(db):
    """Request 3, ship the 2 on the shelf at 1850, receive 1: the challan and
    total_value stay at the 2 that LEFT (3700, never the 3 requested = 5550);
    the mirror bill books the 1 that ARRIVED (1850 -- no input credit on goods
    never received) and says so on the bill, next to the challan's value, so
    the gap is visible instead of two silent figures. Which one the sender's
    GSTR-1 reports is the CA's call (owner ruling D13)."""
    t = _shipped("ST-BOK-1", qty=3)
    _receive_and_complete(t, received=1)
    assert transfers._get_transfer(t["id"])["total_value"] == pytest.approx(2 * UNIT_COST)
    assert _shows_amount(_challan(t["id"]), 2 * UNIT_COST)
    (bill,) = db["vendor_bills"].find({"source_transfer_id": t["id"]})
    assert bill["taxable_amount"] == pytest.approx(UNIT_COST)
    assert (bill["qty_basis"], bill["challan_value"]) == ("received", pytest.approx(2 * UNIT_COST))


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


def test_d13_same_company_shop_without_a_gstin_is_a_data_gap(db):
    """Hirapur -> Bank More, both company Z, Bank More with no GSTIN or state:
    IMS cannot place the shop on a registration, so ship is refused as a data
    gap (never guessed either way) -- and the refusal says so, instead of
    calling a one-company move 'between two GST registrations'."""
    db["stores"].update_one(
        {"store_id": "ST-DHN-2"}, {"$set": {"gstin": "", "state": "", "state_code": ""}}
    )
    t = _create("ST-DHN-2")
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert exc.value.status_code == 400
    assert "Bank More Dhanbad" in exc.value.detail and "cannot tell" in exc.value.detail
    assert "between two GST registrations" not in exc.value.detail
    assert db["stock_units"].count_documents({"status": "AVAILABLE"}) == 2


def test_f51_letterhead_and_consignor_block_print_one_gstin(db):
    """Dhanbad declares state code 'JH' (not its registration's '20') and company
    Z's PRIMARY is the Maharashtra GSTIN: print_legal's own state match misses
    and falls back to that primary, while the one shop rule answers Dhanbad's
    own GSTIN. The page must carry one consignor GSTIN, not both."""
    db["stores"].update_one({"store_id": "ST-DHN-1"}, {"$set": {"state_code": "JH"}})
    db["entities"].update_one({"entity_id": "ENT-Z"}, {"$set": {"gstins": [
        {"gstin": GSTIN_Z_JH, "state_code": "20", "state_name": "Jharkhand"},
        {"gstin": GSTIN_Z_MH, "state_code": "27", "state_name": "Maharashtra", "is_primary": True},
    ]}})
    html = _challan(_shipped("ST-BOK-1")["id"])
    head = html[: html.index('class="party-grid"')]
    assert GSTIN_Z_JH in head, "the letterhead's GSTIN"
    assert GSTIN_Z_MH not in html
    # ...and the state printed beside it is that registration's, not the
    # primary's (was 'Maharashtra / JH' next to a Jharkhand GSTIN).
    assert '<td class="k">State / Code</td><td>Jharkhand / 20</td>' in head
    assert "Maharashtra" not in html


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
    # The receiving shop's counter: its INCOMING list holds the transfer.
    there = _run(transfers.get_location_transfer_analytics(
        "ST-BOK-1", _user("SALES_STAFF", "ST-BOK-1")
    ))
    assert many["transfers"], "the counter role can list the transfer"
    assert pending["ready_to_ship"], "the counter role sees it pending"
    assert (where["outgoing"]["total"], there["incoming"]["total"]) == (1, 1)
    for payload in (one, many, pending, summary, where, there):
        text = json.dumps(payload, default=str)
        assert not _shows_amount(text, UNIT_COST)
        assert not _shows_amount(text, 2 * UNIT_COST)
    # The stored doc is untouched, and a manager still sees per-unit cost
    # (owner ruling 2026-09-28: managers yes, counter staff never).
    assert transfers._get_transfer(t["id"])["total_value"] == pytest.approx(2 * UNIT_COST)
    seen = json.dumps(_run(transfers.get_transfer(t["id"], SOURCE_MANAGER)), default=str)
    assert _shows_amount(seen, UNIT_COST) and _shows_amount(seen, 2 * UNIT_COST)


def test_d7_counter_role_cannot_read_the_mirror_bill(app, db, monkeypatch):
    """The FIN-3 mirror bill now carries each unit's own cost, filed under the
    sending company's entity_id as its vendor_id. The vendor bill / ledger /
    payment / debit-note reads are the accounts roles' -- at the route gate AND
    in the rbac_policy row -- so a counter role at the receiving shop is
    refused both ways (D7; owner 09-29: vendor payments = admin + accountant).
    The vendor scorecard (/performance) is the purchase roles', and its
    month-to-date spend -- the sum of those bills -- the accounts roles' only."""
    from api.routers.vendors import performance
    from api.services import rbac_policy as rbac

    t = _shipped("ST-BOK-1")
    _receive_and_complete(t)
    (bill,) = db["vendor_bills"].find({"source_transfer_id": t["id"]})
    assert bill["vendor_id"] == "ENT-Z"
    assert bill["lines"][0]["unit_price"] == pytest.approx(UNIT_COST)
    staff, accountant = _user("SALES_STAFF", "ST-BOK-1"), _user("ACCOUNTANT")
    for read in ("bills", "ledger", "payments", "debit-notes", "performance"):
        route = next(
            r for r in app.routes
            if getattr(r, "path", None) == f"/api/v1/vendors/{{vendor_id}}/{read}"
            and "GET" in r.methods
        )
        gate = next(d.call for d in route.dependant.dependencies if d.name == "current_user")
        with pytest.raises(HTTPException) as exc:
            _run(gate(current_user=staff))
        assert exc.value.status_code == 403, read
        assert _run(gate(current_user=accountant)) is accountant, read
        url = f"/api/v1/vendors/{bill['vendor_id']}/{read}"
        assert not rbac.check_access("GET", url, staff["roles"]), read
        assert rbac.check_access("GET", url, accountant["roles"]), read

    monkeypatch.setattr(performance, "_get_db", lambda: db)
    monkeypatch.setattr(performance, "get_vendor_repository", lambda: None)
    manager = _user("STORE_MANAGER", "ST-BOK-1")
    assert "mtd_spend" not in _run(performance.vendor_performance("ENT-Z", 6, manager))
    books = _run(performance.vendor_performance("ENT-Z", 6, accountant))
    assert books["mtd_spend"] == pytest.approx(bill["total_amount"])


def test_d7_workshop_staff_pick_ship_and_receive_replies_carry_no_cost(db):
    """WORKSHOP_STAFF picks, ships and receives (the only non-cost role on those
    four routes, owner 09-29: workshop staff never see prices paid). The line
    is costed from the start, as a BOPIS line is; ship stamps the units' own
    cost. None of the four replies may carry it."""
    t = _create("ST-BOK-1", unit_cost=UNIT_COST)
    line_id = t["items"][0]["id"]
    picker = _user("WORKSHOP_STAFF")
    receiver = _user("WORKSHOP_STAFF", "ST-BOK-1")
    replies = {
        "start-picking": _run(transfers.start_picking(t["id"], picker)),
        "complete-picking": _run(transfers.complete_picking(
            t["id"], [{"item_id": line_id, "quantity_picked": 2}], picker
        )),
        "ship": _run(transfers.ship_transfer(t["id"], None, None, None, False, picker)),
        "receive": _run(transfers.receive_transfer(
            t["id"],
            [transfers.TransferItemReceive(transfer_item_id=line_id, quantity_received=2)],
            receiver,
        )),
    }
    for route, reply in replies.items():
        text = json.dumps(reply, default=str)
        assert not _shows_amount(text, UNIT_COST), route
        assert not _shows_amount(text, 2 * UNIT_COST), route
    assert transfers._get_transfer(t["id"])["total_value"] == pytest.approx(2 * UNIT_COST)


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


def test_f51_a_line_that_shipped_nothing_prints_a_cost_not_the_client_figure(db):
    """Line 2 has no unit on the shelf and a client-typed unit_cost (a BOPIS
    line carries the SALE price): after ship its rate is the product's cost,
    never the typed figure under 'Rate (at cost)'."""
    db["products"].insert_one({
        "product_id": "P-LENS", "sku": "LN-1", "name": "Lens pair", "category": "LENS",
        "hsn_code": "900150", "cost_price": 1200.0,
    })
    lens = transfers.TransferItemInput(
        product_id="P-LENS", sku="LN-1", product_name="Lens pair",
        quantity_requested=1, unit_cost=9999,
    )
    t = _create("ST-BOK-1", extra=[lens])
    _ship(t["id"])
    line = transfers._get_transfer(t["id"])["items"][1]
    assert (line["quantity_shipped"], line["unit_cost"]) == (0, pytest.approx(1200.0))
    assert not _shows_amount(_challan(t["id"]), 9999)


# ===========================================================================
# Panel round 3 -- one HSN, one consignor state, the place of supply, no Rs 0
# paper, and "cannot tell" refused on both sides
# ===========================================================================


def test_d13_challan_and_mirror_bill_answer_one_hsn_when_the_ship_lookup_fails(db, monkeypatch):
    """The ship-time product read is fail-soft. When only it fails, the line is
    stamped with no HSN; the valued challan and the FIN-3 mirror bill must then
    still print / book the SAME HSN (one rule, transfers._line_hsn), never a
    blank paper beside a 900311 GSTR-1 row. (Only the read AFTER the units
    moved fails: the ship guard's read before them is a refusal door.)"""
    real = mongomock.collection.Collection.find_one

    def flaky(self, filter=None, *args, **kwargs):
        projection = args[0] if args else kwargs.get("projection")
        moved = self.database["stock_units"].count_documents({"status": "TRANSFERRED"})
        if self.name == "products" and projection and "hsn_code" in projection and moved:
            raise RuntimeError("products read failed")
        return real(self, filter, *args, **kwargs)

    t = _create("ST-BOK-1")
    with monkeypatch.context() as m:
        m.setattr(mongomock.collection.Collection, "find_one", flaky)
        _ship(t["id"])
    assert transfers._get_transfer(t["id"])["items"][0]["hsn_code"] == "", "the read failed"
    assert f"<td>{HSN}</td>" in _challan(t["id"])
    _receive_and_complete(t)
    (bill,) = db["vendor_bills"].find({"source_transfer_id": t["id"]})
    assert bill["lines"][0]["hsn"] == HSN


def test_d13_a_line_with_no_hsn_never_ships_and_never_prints(db):
    """D13: the valued challan carries an HSN. A product with none (and no
    category to give one) is refused at SHIP with the challan's own reason --
    nothing moves, so goods never leave on a paper that cannot print (r4 #2)
    -- and a transfer that shipped before its HSN went missing is refused at
    print, never printed blank."""
    db["products"].update_one({}, {"$set": {"hsn_code": "", "category": "MISC"}})
    t = _create("ST-BOK-1")
    with pytest.raises(HTTPException) as ship:
        _ship(t["id"])
    assert ship.value.status_code == 409 and "HSN" in ship.value.detail
    assert db["stock_units"].count_documents({"status": "AVAILABLE"}) == 2
    db["products"].update_one({}, {"$set": {"hsn_code": HSN}})
    _ship(t["id"])
    db["products"].update_one({}, {"$set": {"hsn_code": ""}})
    db["stock_transfers"].update_one({"id": t["id"]}, {"$set": {"items.0.hsn_code": ""}})
    with pytest.raises(HTTPException) as paper:
        _challan(t["id"])
    assert (paper.value.status_code, paper.value.detail) == (409, ship.value.detail)


def test_d13_inter_state_challan_names_the_destination_as_place_of_supply(db):
    """Dhanbad (Jharkhand GSTIN) -> Pune (Maharashtra GSTIN): Rule 55 wants the
    place of supply of an inter-state move -- the consignee's state, not the
    consignor's the letterhead defaults to."""
    html = _challan(_shipped("ST-PUN-1")["id"])
    assert '<td class="k">Place of Supply</td><td>Maharashtra (27)</td>' in html


def test_f51_a_crossing_move_with_nothing_on_the_shelf_never_ships(db):
    """Dhanbad -> Bokaro with both units SOLD: shipping would move 0 units and
    the valued challan would read 'Total Quantity 0 / Rs 0.00' between two
    GSTINs -- the F51 paper. Refused before ship; nothing changes."""
    db["stock_units"].update_many({}, {"$set": {"status": "SOLD"}})
    t = _create("ST-BOK-1")
    with pytest.raises(HTTPException) as exc:
        _ship(t["id"])
    assert exc.value.status_code == 409 and "in stock" in exc.value.detail
    assert transfers._get_transfer(t["id"])["status"] == transfers.TransferStatus.APPROVED


def test_f51_a_crossing_transfer_that_shipped_nothing_prints_no_rs_0_challan(db):
    """A crossing transfer already shipped with 0 units (before the ship guard,
    or a lost race for the units) is refused at print, never a Rs 0 paper."""
    t = _shipped("ST-BOK-1")
    db["stock_transfers"].update_one({"id": t["id"]}, {"$set": {"items.0.quantity_shipped": 0}})
    with pytest.raises(HTTPException) as exc:
        _challan(t["id"])
    assert exc.value.status_code == 409 and "Nothing left the shop" in exc.value.detail


_CANNOT_PLACE = {
    # company Z's GSTINs not entered yet: Dhanbad -> Pune, both blank
    "one_company_no_gstins": "ST-PUN-1",
    # a shop with no company: Bokaro, entity_id unset
    "shop_without_a_company": "ST-BOK-1",
    # r4 #1: ONE shop of one company without its GSTIN (Bank More blank)
    "one_shop_without_a_gstin": "ST-DHN-2",
    # ...and of two companies (Bokaro's GSTIN not entered)
    "two_companies_one_gstin": "ST-BOK-1",
}


def _blank(db, gap):
    if gap == "one_company_no_gstins":
        db["entities"].update_one({"entity_id": "ENT-Z"}, {"$set": {"gstins": []}})
    elif gap == "shop_without_a_company":
        db["stores"].update_one({"store_id": "ST-BOK-1"}, {"$unset": {"entity_id": ""}})
    elif gap == "one_shop_without_a_gstin":
        db["stores"].update_one(
            {"store_id": "ST-DHN-2"}, {"$set": {"gstin": "", "state": "", "state_code": ""}}
        )
    else:
        db["stores"].update_one({"store_id": "ST-BOK-1"}, {"$set": {"gstin": ""}})
        db["entities"].update_one({"entity_id": "ENT-Y"}, {"$set": {"gstins": []}})


@pytest.mark.parametrize("gap", list(_CANNOT_PLACE))
def test_d13_a_move_ims_cannot_place_is_refused_on_both_sides(db, gap):
    """r2 'cannot tell = refuse', applied to every case (r4 #1: one blank
    GSTIN is a 'cannot tell' too, not 'two registrations'). Ship is refused
    with nothing moved, and the challan gives ship's own answer to every role
    -- never a counter role's 403, a manager's 'ship first' (ship would then
    refuse), or the unvalued paper with no GSTINs."""
    _blank(db, gap)
    t = _create(_CANNOT_PLACE[gap])
    with pytest.raises(HTTPException) as ship:
        _ship(t["id"])
    assert ship.value.status_code == 400 and "cannot tell" in ship.value.detail
    assert db["stock_units"].count_documents({"status": "AVAILABLE"}) == 2
    for user in (_user("SALES_STAFF"), SOURCE_MANAGER):
        with pytest.raises(HTTPException) as paper:
            _challan(t["id"], user)
        assert (paper.value.status_code, paper.value.detail) == (400, ship.value.detail)


# ===========================================================================
# Panel round 4
# ===========================================================================


@pytest.mark.parametrize("blank", ["one_shop", "both_shops"])
def test_d13_a_move_shipped_on_one_registration_books_nothing_later(db, blank):
    """r5 (was r4 #1 (c)): Hirapur -> Bank More ships on ONE registration
    (the unvalued challan prints); a GSTIN is then lost before complete --
    Bank More's alone, or the company's (both shops). The mirror bill reads
    ship's answer, so both cases answer the same: no supply to itself (it
    was: ENT-Z -> ENT-Z booked at 3700 with CGST 92.50 + SGST 92.50 on blank
    GSTINs, Rs 185 on Hirapur's GSTR-3B 3.1(a)), and the paper stays the one
    that travelled."""
    t = _shipped("ST-DHN-2")
    if blank == "one_shop":
        _blank(db, "one_shop_without_a_gstin")
    else:
        db["entities"].update_one({"entity_id": "ENT-Z"}, {"$set": {"gstins": []}})
    _receive_and_complete(t)
    assert db["vendor_bills"].count_documents({"source_transfer_id": t["id"]}) == 0
    html = _challan(t["id"], _user("SALES_STAFF"))
    assert t["transfer_number"] in html and not _shows_amount(html, 2 * UNIT_COST)


def test_d13_challan_total_value_is_the_sum_of_its_lines(db):
    """r4 #3: the tfoot's Value is the sum of the line values (frame 2 x 1850
    + lens 1 x 1100 = 4,800.00 on 3 units), never Rs 0 -- the F51 defect --
    or one line's figure."""
    db["products"].insert_one({
        "product_id": "P-LENS", "sku": "LN-1", "name": "Lens pair", "category": "LENS",
        "hsn_code": "900150", "cost_price": 1200.0,
    })
    db["stock_units"].insert_one({
        "stock_id": "SU-L1", "product_id": "P-LENS", "store_id": "ST-DHN-1",
        "status": "AVAILABLE", "barcode": "BVLENS0001", "unit_cost": 1100.0,
    })
    lens = transfers.TransferItemInput(
        product_id="P-LENS", sku="LN-1", product_name="Lens pair", quantity_requested=1,
    )
    t = _create("ST-BOK-1", extra=[lens])
    _ship(t["id"])
    html = _challan(t["id"])
    foot = html[html.index("<tfoot>"): html.index("</tfoot>")]
    assert _shows_amount(foot, 2 * UNIT_COST + 1100.0), foot
    assert "<strong>3</strong>" in foot, foot


def test_f51_a_line_with_a_cost_less_unit_is_valued_at_0_not_short(db):
    """r4 #3: one of the two units carries no cost and the product has none
    (a one-registration move, which the ship guard does not stop): the line's
    rate is 0, so a valued paper would refuse -- never the costed unit's 1850
    alone, nor 925 averaged with a phantom 0."""
    db["stock_units"].update_one({"stock_id": "SU-2"}, {"$unset": {"unit_cost": "", "cost_price": ""}})
    db["products"].update_one({}, {"$set": {"cost_price": 0}})
    t = _shipped("ST-DHN-2")
    stored = transfers._get_transfer(t["id"])
    assert (stored["items"][0]["quantity_shipped"], stored["items"][0]["unit_cost"]) == (2, 0.0)
    assert stored["total_value"] == 0.0


def _analytics(user, location="ST-BOK-1"):
    return (
        _run(transfers.get_transfer_analytics(None, None, location, user))["summary"],
        _run(transfers.get_location_transfer_analytics(location, user))["incoming"],
    )


def _listed(user):
    return _run(transfers.list_transfers(
        status=None, transfer_type=None, from_location_id=None, to_location_id=None,
        store_id=None, priority=None, created_after=None, created_before=None,
        limit=50, page=1, current_user=user,
    ))["transfers"]


def test_d7_transfer_figures_are_the_callers_stores_only(db):
    """r4 #4-5: Dhanbad (Z) ships Bokaro (Y) 3700 at cost. A manager of
    another shop (Bank More), and an area manager of another region (Pune),
    see neither the transfer nor its value -- on the list, /pending and both
    analytics (user_store_scope, like /pending). The sending shop's manager
    sees the value on both analytics (D7, the 'product' cost context)."""
    _shipped("ST-BOK-1")
    for outsider in (_user("STORE_MANAGER", "ST-DHN-2"), _user("AREA_MANAGER", "ST-PUN-1")):
        summary, incoming = _analytics(outsider)
        assert (summary["total_transfers"], summary["total_value"]) == (0, 0)
        assert (incoming["total"], incoming["value"]) == (0, 0)
        assert _listed(outsider) == []
        pending = _run(transfers.get_pending_transfers(None, outsider))
        assert not any(pending.values())
    summary, incoming = _analytics(SOURCE_MANAGER)
    assert summary["total_value"] == incoming["value"] == pytest.approx(2 * UNIT_COST)
    assert [t["to_location_id"] for t in _listed(_user("AREA_MANAGER", "ST-DHN-1"))] == ["ST-BOK-1"]


_ROLES = (
    "SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT", "SALES_STAFF",
    "SALES_CASHIER", "WORKSHOP_STAFF", "OPTOMETRIST", "CATALOG_MANAGER",
)


@pytest.mark.parametrize("to_store", ["ST-BOK-1", "ST-DHN-2"])
def test_owner_the_challan_button_is_offered_exactly_to_whom_the_server_prints_it(db, to_store):
    """Owner 2026-10-08: the valued challan is printed by managers and
    accounts only (today's challan roles), so the Delivery Challan button is
    hidden from every role the server would refuse. Every transfer reply
    carries the server's own answer (can_print_challan, from the route's rule
    print_documents.may_print_challan), and it matches the print route, role
    by role, on a valued (Dhanbad -> Bokaro) and an unvalued (Hirapur -> Bank
    More) transfer."""
    t = _shipped(to_store)
    printers = set()
    for role in _ROLES:
        user = _user(role)
        offered = _run(transfers.get_transfer(t["id"], user))["transfer"]["can_print_challan"]
        listed = [x["can_print_challan"] for x in _listed(user) if x["id"] == t["id"]]
        try:
            _challan(t["id"], user)
            printed = True
        except HTTPException as exc:
            assert exc.status_code == 403, (role, exc.detail)
            printed = False
        assert offered is printed and listed == [printed], (role, to_store)
        if printed:
            printers.add(role)
    managers_and_accounts = {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"}
    assert printers == (
        managers_and_accounts if to_store == "ST-BOK-1"
        else managers_and_accounts | {"SALES_STAFF", "SALES_CASHIER"}
    )


# ===========================================================================
# Panel round 5
# ===========================================================================

_MANAGERS_AND_ACCOUNTS = {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"}


def test_d13_inter_state_challan_prints_one_place_of_supply(db):
    """r5: Dhanbad -> Pune printed two places of supply -- the meta row's
    'Maharashtra (27)' and the letterhead's 'Place of supply' row with
    Dhanbad's shop address. One answer per page; the shop address stays, as
    where the goods leave from."""
    html = _challan(_shipped("ST-PUN-1")["id"])
    answers = re.findall(r'<td class="k">place of supply</td><td>([^<]*)</td>', html, re.I)
    assert answers == ["Maharashtra (27)"], answers
    assert '<td class="k">Dispatched from</td><td>Shop 33 Park Market' in html


def test_d7_finance_reconciliation_lists_the_callers_stores_only(db, monkeypatch):
    """r5: GET /finance/reconciliation listed every shop's in-transit
    transfers with their lines -- now the units' own cost -- to any manager.
    It reads through the one transfer store reach (_in_callers_stores)."""
    from api.routers.finance import budget

    monkeypatch.setattr(budget, "_get_db", lambda: db)
    _shipped("ST-BOK-1")
    outsider = _run(budget.get_reconciliation(_user("STORE_MANAGER", "ST-PUN-1")))
    assert (outsider["pending_transfers"], outsider["transfers"]) == (0, [])
    mine = _run(budget.get_reconciliation(SOURCE_MANAGER))
    assert mine["pending_transfers"] == 1
    assert mine["transfers"][0]["items"][0]["unit_cost"] == pytest.approx(UNIT_COST)


@pytest.mark.parametrize(
    "own",
    [{"unit_cost": "1850"}, {"cost_price": UNIT_COST}, {"unit_cost": 0, "cost_price": UNIT_COST}],
    ids=["unit_cost_as_text", "cost_price_only", "zero_unit_cost_with_cost_price"],
)
def test_d13_the_ship_guard_and_the_ship_stamp_ask_one_unit_cost(db, own):
    """r5: 'this unit has a cost' had two copies -- the ship guard's Mongo
    predicate and the stamp's _first_cost. The product has no cost, so only
    the unit's own can value it: a unit the guard lets leave is valued at
    that cost (never stamped 0, its challan then refused for good), and a
    cost the stamp reads is never refused at ship (text '1850' was)."""
    db["stock_units"].update_many({}, {"$unset": {"unit_cost": "", "cost_price": ""}})
    db["stock_units"].update_many({}, {"$set": own})
    db["products"].update_one({}, {"$set": {"cost_price": 0}})
    t = _shipped("ST-BOK-1")
    assert transfers._get_transfer(t["id"])["items"][0]["unit_cost"] == pytest.approx(UNIT_COST)
    assert _shows_amount(_challan(t["id"]), 2 * UNIT_COST)


def test_d7_the_scorecard_spend_follows_the_payables_gate_role_by_role(app, db, monkeypatch):
    """r5: the scorecard's month-to-date spend sums supplier bills, so it is
    shown exactly to whom the bill read admits (_AP_ROLES via require_roles)
    -- one list, not cost_mask's fallback."""
    from api.routers.vendors import performance

    monkeypatch.setattr(performance, "_get_db", lambda: db)
    monkeypatch.setattr(performance, "get_vendor_repository", lambda: None)
    route = next(
        r for r in app.routes
        if getattr(r, "path", None) == "/api/v1/vendors/{vendor_id}/bills" and "GET" in r.methods
    )
    gate = next(d.call for d in route.dependant.dependencies if d.name == "current_user")
    shown = set()
    for role in _ROLES:
        user = _user(role)
        try:
            _run(gate(current_user=user))
            admitted = True
        except HTTPException:
            admitted = False
        has_spend = "mtd_spend" in _run(performance.vendor_performance("ENT-Z", 6, user))
        assert has_spend is admitted, role
        if has_spend:
            shown.add(role)
    assert shown == {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}


def test_d13_ship_refuses_when_it_cannot_read_the_shops(db, monkeypatch):
    """r5: the ship guard failed open on a read error -- a failed shop lookup
    read as 'no company', so Dhanbad -> Bokaro with a cost-less unit shipped
    at Rs 0 and its valued challan then refused for good (and cancel refuses
    an in-transit move). A refusal door refuses when it cannot read the
    shops: 503, nothing moved."""
    db["stock_units"].update_one({"stock_id": "SU-2"}, {"$unset": {"unit_cost": "", "cost_price": ""}})
    db["products"].update_one({}, {"$set": {"cost_price": 0}})
    t = _create("ST-BOK-1")
    real = mongomock.collection.Collection.find_one

    def flaky(self, *args, **kwargs):
        if self.name == "stores":
            raise RuntimeError("stores read failed")
        return real(self, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(mongomock.collection.Collection, "find_one", flaky)
        with pytest.raises(HTTPException) as exc:
            _ship(t["id"])
    assert exc.value.status_code == 503
    assert db["stock_units"].count_documents({"status": "AVAILABLE"}) == 2
    assert transfers._get_transfer(t["id"])["status"] == transfers.TransferStatus.APPROVED
