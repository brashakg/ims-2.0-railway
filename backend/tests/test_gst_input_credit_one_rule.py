"""Input credit is ONE rule: GSTR-3B Table 4, the Finance GST tab and the
cost of stock (owner + CA, 2026-10-18).

  * A04-3: the Finance GST table reads each registration's GSTR-3B -- booked
    bills per GSTIN, never purchase-order tax pooled per company.
  * F04: a PO bill booked with credit off adds its GST to the cost of the
    units it bought (as walk-in bills already did); with credit, no change.
  * Section 17(5): a services bill ticked 'credit not claimable' is no input
    credit anywhere and shows as blocked credit (Table 4(B)(1)); the tick is
    refused once the month is locked.
  * A supplier credit note reduces input credit in the month of the
    SUPPLIER'S note date, read on the IST calendar.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.routers.finance import gst as fin_gst
from api.routers.finance import gst_crosscheck as fin_xc
from api.routers.finance import itc as fin_itc
from api.routers.reports import gstr3b as r3b
from api.routers import purchase_invoices as pi
from api.routers import vendors as vd
from api.services import stock_value
from api.services.gstn_export import to_gstr3b_json
from api.services.supplier_credit_confirmation import _input as confirmation_input
from api.services.supplier_credit_evidence import SupplierCreditError
from api.utils.ist import now_ist_naive
from database.repositories import ProductRepository, StockRepository
from tests.test_receipt_without_po import (  # noqa: F401 -- `world` is the fixture
    ACCOUNTANT,
    ADMIN,
    DEALER,
    DEALER_GSTIN,
    ENTITY,
    FRAME,
    MONTH,
    SHARMA,
    SHOP_GSTIN,
    STORE,
    _invoice_body,
    _minted,
    _run,
    _seed_receipt,
    world,
)

MH_STORE = "BV-NOPO-MH"
MH_GSTIN = "27AABCB1234C1ZQ"  # the same company's Maharashtra registration
SEPT_10 = datetime(2026, 9, 10, 6, 0, 0)  # 11:30 IST, a naive-UTC instant


def _heads(block):
    return round(sum(float((block or {}).get(k) or 0) for k in ("integratedTax", "centralTax", "stateTax")), 2)


def _sale(db, store, tax, order_id):
    db.orders.insert_one({"order_id": order_id, "store_id": store, "status": "DELIVERED",
                          "created_at": SEPT_10, "tax_amount": tax, "grand_total": tax * 10})


def _booked(db, bill_id, gstin, *, cgst=0.0, igst=0.0, store=STORE, day="2026-09-12", **extra):
    """A booked bill as the bill doors store it (received on `gstin`)."""
    doc = {"bill_id": bill_id, "bill_number": bill_id, "vendor_id": DEALER, "store_id": store,
           "bill_date": day, "status": "OUTSTANDING", "recipient_entity_id": ENTITY,
           "recipient_gstin": gstin, "vendor_gstin": DEALER_GSTIN, "itc_eligible": True,
           "taxable_amount": 1000.0, "cgst_total": cgst, "sgst_total": cgst, "igst_total": igst,
           "tax_amount": 2 * cgst + igst, "total_amount": 1000.0 + 2 * cgst + igst, **extra}
    db.vendor_bills.insert_one(doc)
    return doc


def _table(user=ADMIN, month=9):
    return _run(fin_xc.get_gst_reconciliation(month=month, year=2026, entity_id=None, current_user=user))


def _rows(user=ADMIN, month=9):
    return {r["gstin"]: (r["gst_collected"], r["input_credit"], r["net_payable"])
            for r in _table(user, month)["registrations"]}


@pytest.fixture
def two_gstins(world):
    """One company, two registrations: Jharkhand (STORE) and Maharashtra."""
    db = world["db"]
    db.entities.update_one({"entity_id": ENTITY}, {"$push": {"gstins": {"state_code": "27", "gstin": MH_GSTIN}}})
    db.stores.insert_one({"store_id": MH_STORE, "store_name": "BV Pune", "state": "Maharashtra",
                          "state_code": "27", "entity_id": ENTITY, "gstin": MH_GSTIN, "is_active": True})
    return world


# ---------------------------------------------------------------------------
# A04-3: the Finance GST table is GSTR-3B, per GSTIN
# ---------------------------------------------------------------------------


def test_the_gst_table_is_one_row_per_gstin_and_never_reads_purchase_orders(two_gstins):
    db = two_gstins["db"]
    _sale(db, STORE, 1000.0, "O-JH")
    _sale(db, MH_STORE, 500.0, "O-MH")
    _booked(db, "B-JH", SHOP_GSTIN, cgst=150.0)
    _booked(db, "B-MH", MH_GSTIN, igst=100.0, store=MH_STORE)
    # A purchase ORDER's tax is not input credit: the old table pooled 5000
    # of it per company and read net payable -3500.
    db.purchase_orders.insert_one({"po_id": "PO-1", "delivery_store_id": STORE, "status": "SENT",
                                   "created_at": SEPT_10, "tax_amount": 5000.0})

    assert _rows() == {SHOP_GSTIN: (1000.0, 300.0, 700.0), MH_GSTIN: (500.0, 100.0, 400.0)}

    # ...and each row is that GSTIN's GSTR-3B, to the paisa.
    for store, gstin in ((STORE, SHOP_GSTIN), (MH_STORE, MH_GSTIN)):
        ret = r3b._compute_gstr3b(MONTH, store)
        _out, credit, cash = _rows()[gstin]
        assert (_heads(ret["itcAvailable"]), _heads(ret["taxPaidCash"])) == (credit, cash)


def test_one_gstins_spare_credit_never_pays_anothers_tax(two_gstins):
    """Pooled per company, Pune's spare credit paid Jharkhand's tax."""
    db = two_gstins["db"]
    _sale(db, STORE, 1000.0, "O-JH")
    _booked(db, "B-MH", MH_GSTIN, igst=800.0, store=MH_STORE)
    assert _rows() == {SHOP_GSTIN: (1000.0, 0.0, 1000.0), MH_GSTIN: (0.0, 800.0, 0.0)}


def test_a_gstin_shared_with_a_hidden_shop_shows_no_cash_figure(two_gstins):
    """An accountant sees his own shop. When his GSTIN also files for a shop
    he cannot see, his rows' output tax is only part of the return, so the
    table names no net payable rather than a wrong one."""
    db = two_gstins["db"]
    db.stores.insert_one({"store_id": "BV-NOPO-02", "state": "Jharkhand", "state_code": "20",
                          "entity_id": ENTITY, "gstin": SHOP_GSTIN, "is_active": True})
    _sale(db, STORE, 1000.0, "O-JH")
    _booked(db, "B-JH", SHOP_GSTIN, cgst=150.0)
    [row] = _table(ACCOUNTANT)["registrations"]
    assert (row["gstin"], row["input_credit"], row["net_payable"], row["shops_outside_view"]) == (
        SHOP_GSTIN, 300.0, None, 1)
    assert _rows()[SHOP_GSTIN] == (1000.0, 300.0, 700.0)  # an admin sees the whole return


# ---------------------------------------------------------------------------
# Section 17(5): credit not claimable on a services bill
# ---------------------------------------------------------------------------


def _services_bill(number="FOOD-1", day="2026-09-20", blocked=True, vendor=DEALER):
    bill = vd.VendorBillCreate(bill_number=number, bill_date=day, taxable_amount=1000.0,
                               tax_amount=180.0, total_amount=1180.0, bill_kind="SERVICES",
                               itc_blocked=blocked)
    return _run(vd.create_vendor_bill(vendor, bill, current_user=ACCOUNTANT))


def _tick(bill, blocked):
    return _run(vd.set_vendor_bill_itc_blocked(
        bill["vendor_id"], bill["bill_id"], vd.VendorBillItcBlocked(itc_blocked=blocked),
        current_user=ACCOUNTANT))


def _credit_everywhere():
    """(GSTR-3B Table 4, Table 4(B)(1), GST tab table, GST cards, ITC register)."""
    ret = r3b._compute_gstr3b(MONTH, STORE)
    cards = _run(fin_gst.get_gst_summary(month=9, year=2026, current_user=ADMIN))
    register = _run(fin_itc.itc_register(period=MONTH, entity_id=None, current_user=ADMIN))
    return (_heads(ret["itcAvailable"]), _heads(ret["itcBlocked"]), _rows()[SHOP_GSTIN][1],
            cards["gst_input_credit"], float(register.get("total_itc") or 0))


def test_a_17_5_bill_is_no_input_credit_anywhere_and_shows_as_blocked(world):
    bill = _services_bill()
    assert (bill["itc_blocked"], bill["itc_eligible"]) == (True, False)
    assert _credit_everywhere() == (0.0, 180.0, 0.0, 0.0, 0.0)
    ret = r3b._compute_gstr3b(MONTH, STORE)
    # Blocked, not "ineligible for another reason" (Table 4(D)).
    assert _heads(ret["itcIneligible"]) == 0.0
    # The portal file: 4(A)(5) as GSTR-2B shows it, reversed in 4(B)(1);
    # the net credit 4(C) is unchanged.
    portal = to_gstr3b_json(ret, gstin=SHOP_GSTIN, period=MONTH)["itc_elg"]
    rul = next(r for r in portal["itc_rev"] if r["ty"] == "RUL")
    oth = next(r for r in portal["itc_avl"] if r["ty"] == "OTH")
    assert (rul["camt"], rul["samt"], oth["camt"], portal["itc_net"]["camt"]) == (90.0, 90.0, 90.0, 0.0)

    # Unticked: the credit comes back on every reader.
    assert _tick(bill, False)["itc_eligible"] is True
    assert _credit_everywhere() == (180.0, 0.0, 180.0, 180.0, 180.0)
    # Ticked again later: out again.
    assert _tick(bill, True)["itc_blocked"] is True
    assert _credit_everywhere() == (0.0, 180.0, 0.0, 0.0, 0.0)


def test_unticking_gives_back_the_credit_decision_the_bill_had(world):
    """An unregistered supplier's bill never had credit: unticking 17(5)
    does not hand it any."""
    bill = _services_bill("SH-1", vendor=SHARMA)
    assert _tick(bill, False)["itc_eligible"] is False
    plain = _services_bill("FOOD-2", blocked=False)
    _tick(plain, True)
    assert _tick(plain, False)["itc_eligible"] is True


def test_the_17_5_tick_is_refused_once_the_month_is_locked(world):
    db = world["db"]
    bill = _services_bill(day="2026-09-20", blocked=False)
    db.period_locks.insert_one({"year": 2026, "month": 9, "locked_by": "u-admin"})
    with pytest.raises(HTTPException) as refused:
        _tick(bill, True)
    assert refused.value.status_code == 423
    assert db.vendor_bills.find_one({"bill_id": bill["bill_id"]})["itc_eligible"] is True
    # An open month's bill can still be changed.
    october = _services_bill("FOOD-OCT", day="2026-10-02", blocked=False)
    assert _tick(october, True)["itc_blocked"] is True


def test_the_17_5_tick_is_for_services_bills_and_accounts_only(world):
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-NOPO-0170", subtype="NO_PO")
    goods = vd.VendorBillCreate(bill_number="G-1", bill_date="2026-09-14", taxable_amount=6200.0,
                                tax_amount=310.0, total_amount=6510.0, grn_id="GRN-NOPO-0170",
                                itc_blocked=True)
    with pytest.raises(HTTPException) as refused:
        _run(vd.create_vendor_bill(DEALER, goods, current_user=ACCOUNTANT))
    assert refused.value.status_code == 422
    assert refused.value.detail["code"] == "ITC_BLOCKED_SERVICES_ONLY"
    from api.services.rbac_policy import POLICY

    row = next(r for r in POLICY if r["path"].endswith("/bills/{bill_id}/itc-blocked"))
    assert (row["method"], sorted(row["allowed"])) == ("PUT", ["ACCOUNTANT", "ADMIN"])


# ---------------------------------------------------------------------------
# F04: a PO bill booked without credit adds its GST to the units' cost
# ---------------------------------------------------------------------------


def _po_receipt(db, grn_id, vendor=DEALER):
    _seed_receipt(db, grn_id=grn_id, subtype="STANDARD", po_id=f"PO-{grn_id[-4:]}", vendor_id=vendor)
    _minted(db, grn_id, 3100.0)


def _units(db, grn_id):
    return [(u["unit_cost"], u.get("landed_cost"), stock_value._own_cost(u))
            for u in db.stock_units.find({"grn_id": grn_id}).sort("stock_id", 1)]


def _shelf_value(db):
    return stock_value.by_product(stock_value.shelf_units(
        StockRepository(db.stock_units), ProductRepository(db.products), STORE))[FRAME]["cost"]


def test_a_po_bill_booked_without_credit_puts_its_gst_into_the_units_cost(world):
    db = world["db"]
    _po_receipt(db, "GRN-STD-0401")
    off = _invoice_body("GRN-STD-0401", "TAX-401")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    # The supplier's price stays the unit's price (the last-paid quote); the
    # stock is worth price + the 5% GST that cannot be claimed back.
    assert _units(db, "GRN-STD-0401") == [(3100.0, 3255.0, 3255.0)] * 2
    assert _shelf_value(db) == 6510.0
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3255.0
    # Booking it again cannot add the GST twice.
    pi._add_unclaimed_gst_to_units(db, db.grns.find_one({"grn_id": "GRN-STD-0401"}),
                                   {"itc_eligible": False, "bill_id": "again"},
                                   [{"product_id": FRAME, "qty": 2, "unit_price": 3100.0, "gst_rate": 5.0}])
    assert _units(db, "GRN-STD-0401") == [(3100.0, 3255.0, 3255.0)] * 2


def test_a_po_bill_claiming_credit_leaves_the_cost_before_gst(world):
    db = world["db"]
    _po_receipt(db, "GRN-STD-0402")
    _run(pi.create_purchase_invoice(_invoice_body("GRN-STD-0402", "TAX-402"), current_user=ACCOUNTANT))
    assert _units(db, "GRN-STD-0402") == [(3100.0, None, 3100.0)] * 2
    assert _shelf_value(db) == 6200.0
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3100.0


def test_the_cash_flow_bill_door_costs_an_unregistered_suppliers_po_receipt_the_same(world):
    db = world["db"]
    _po_receipt(db, "GRN-STD-0403", vendor=SHARMA)
    bill = vd.VendorBillCreate(bill_number="SH-403", bill_date="2026-09-14", taxable_amount=6200.0,
                               tax_amount=310.0, total_amount=6510.0, grn_id="GRN-STD-0403")
    assert _run(vd.create_vendor_bill(SHARMA, bill, current_user=ACCOUNTANT))["itc_eligible"] is False
    assert _units(db, "GRN-STD-0403") == [(3100.0, 3255.0, 3255.0)] * 2


@pytest.mark.parametrize("credit, landed", [(False, 330500), (True, 315000)])
def test_landed_cost_lands_on_the_same_cost(world, credit, landed):
    """Freight of Rs 100 over the two frames: on top of price + GST when the
    bill claims no credit, on top of the price when it does."""
    db = world["db"]
    _po_receipt(db, "GRN-STD-0404")
    body = _invoice_body("GRN-STD-0404", "TAX-404")
    body.itc_eligible = credit
    saved = _run(pi.create_purchase_invoice(body, current_user=ACCOUNTANT))
    comps = pi.LandedCostsSet(components=[pi.LandedCostComponent(type="FREIGHT", amount_paise=10000)])
    _run(pi.set_invoice_landed_costs(saved["bill_id"], comps, current_user=ACCOUNTANT))
    preview = _run(pi.preview_invoice_landed_costs(saved["bill_id"], current_user=ACCOUNTANT))
    assert preview["landed_unit_cost_by_product_paise"] == {FRAME: landed}
    _run(pi.allocate_invoice_landed_costs(saved["bill_id"], current_user=ACCOUNTANT))
    assert db.products.find_one({"product_id": FRAME})["landed_cost_paise"] == landed


# ---------------------------------------------------------------------------
# Supplier credit notes: the month of the supplier's note date
# ---------------------------------------------------------------------------


def _supplier_note(db, number, note_date, posted_at, cgst_paise):
    db.supplier_credit_confirmations.insert_one({
        "_id": number, "confirmation_id": number, "return_id": "RET-1", "vendor_id": DEALER,
        "posting_status": "POSTED", "credit_note_number": number, "credit_note_date": note_date,
        "posted_at": posted_at, "posted_by": "u-acct",
        "allocations": [{"bill_id": "B-AUG", "store_id": STORE, "recipient_entity_id": ENTITY,
                         "recipient_gstin": SHOP_GSTIN, "vendor_gstin": DEALER_GSTIN, "claims_credit": True,
                         "reverse_charge": False, "taxable_paise": 10 * cgst_paise,
                         "supplier_amount_paise": 12 * cgst_paise,
                         "itc_reversal_paise": {"cgst": cgst_paise, "sgst": cgst_paise, "igst": 0}}],
    })


def test_a_supplier_credit_note_counts_in_its_note_dates_month(world):
    db = world["db"]
    _booked(db, "B-AUG", SHOP_GSTIN, cgst=100.0, day="2026-08-10")
    # Dated 31 Aug, entered 2 Sep: August's return, never September's.
    _supplier_note(db, "CN-A", "2026-08-31", "2026-09-02T10:00:00+00:00", 2000)
    # Dated 1 Sep, entered 00:30 IST on 1 Sep (still 31 Aug in UTC):
    # September's, never August's.
    _supplier_note(db, "CN-B", "2026-09-01", "2026-08-31T19:00:00+00:00", 500)
    aug = r3b._compute_gstr3b("2026-08", STORE)
    sep = r3b._compute_gstr3b("2026-09", STORE)
    assert (_heads(aug["itcReversed"]), _heads(aug["itcAvailable"])) == (40.0, 160.0)
    assert (_heads(sep["itcReversed"]), _heads(sep["itcAvailable"])) == (10.0, -10.0)
    # The GST tab reads the same months.
    assert _rows(month=8)[SHOP_GSTIN][1] == 160.0
    assert _rows(month=9)[SHOP_GSTIN][1] == -10.0


def test_the_supplier_note_date_is_a_real_day_up_to_today_in_ist():
    def confirm(day):
        return confirmation_input({"idempotency_key": str(uuid4()), "credit_note_number": "CN-9",
                                   "credit_note_date": day, "received_amount": 10})

    today = now_ist_naive().date()
    assert confirm(today.isoformat())["credit_note_date"] == today.isoformat()
    for wrong in ((today + timedelta(days=1)).isoformat(), "2017-06-30", "2026-02-30", "31/08/2026", ""):
        with pytest.raises(SupplierCreditError) as refused:
            confirm(wrong)
        assert refused.value.code == "SUPPLIER_NOTE_DATE_REQUIRED", wrong
    assert confirm(date(2017, 7, 1))["credit_note_date"] == "2017-07-01"
