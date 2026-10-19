"""Input credit is ONE rule: GSTR-3B, the Finance GST tab and the cost of
stock (owner + CA, 2026-10-18; GST review, 2026-10-19).

  * GSTR-3B is the GSTIN's return: every shop on the GSTIN, page and portal
    file; a reader who cannot see them all is refused (H2).
  * The Finance GST table AND the summary cards are those returns, one row per
    GSTIN (A04-3, M3); a GSTIN shared with shops outside the view shows no
    input-side figure (M4); a read failure is a 503, never a zero (M7).
  * F04: a bill booked without input credit adds its GST to the cost of the
    units it bought -- goods receipt or Delivery Challans (M5), multi-piece
    units billed in parts (LOW 8), unpriced receipts billed in parts (LOW 9),
    the Cash Flow door at the bill's own price (LOW 17).
  * Section 17(5): the tick on a services bill, or a goods bill bought without
    a PO when booked (LOW 13); no input credit anywhere, shown in 4(B)(1);
    refused once the month is locked or its GSTR-3B is due (M6).
  * A supplier credit note counts in the month of the SUPPLIER'S note date,
    a real day up to today in IST (LOW 18).
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.routers.finance import gst as fin_gst
from api.routers.finance import gst_crosscheck as fin_xc
from api.routers.finance import itc as fin_itc
from api.routers.reports import gst_itc
from api.routers.reports import gstr3b as r3b
from api.routers import purchase_invoices as pi
from api.routers import vendors as vd
from api.routers.vendors import ap_bills
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
SISTER = "BV-NOPO-02"  # a second shop on the Jharkhand GSTIN
SEPT_10 = datetime(2026, 9, 10, 6, 0, 0)  # 11:30 IST, a naive-UTC instant


@pytest.fixture(autouse=True)
def _before_septembers_return_is_due(monkeypatch):
    """The 17(5) tick closes when the bill's GSTR-3B falls due (M6): these
    September bills are ticked on 10 October."""
    monkeypatch.setattr(ap_bills, "now_ist_naive", lambda: datetime(2026, 10, 10, 12, 0))


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


def _cards(user=ADMIN, month=9):
    return _run(fin_gst.get_gst_summary(month=month, year=2026, current_user=user))


def _page(store=STORE, user=ADMIN, month=MONTH):
    return _run(r3b.gstr3b_report(month=month, store_id=store, current_user=user))


def _portal(store=STORE, user=ADMIN, month=MONTH):
    return _run(r3b.gstr3b_gstn_json(month=month, year=None, store_id=store, entity_id=None,
                                     current_user=user))


def _user(role, *shops):
    return {"user_id": f"u-{role}", "roles": [role], "store_ids": list(shops), "active_store_id": shops[0]}


@pytest.fixture
def two_gstins(world):
    """One company, two registrations: Jharkhand (STORE) and Maharashtra."""
    db = world["db"]
    db.entities.update_one({"entity_id": ENTITY}, {"$push": {"gstins": {"state_code": "27", "gstin": MH_GSTIN}}})
    db.stores.insert_one({"store_id": MH_STORE, "store_name": "BV Pune", "state": "Maharashtra",
                          "state_code": "27", "entity_id": ENTITY, "gstin": MH_GSTIN, "is_active": True})
    return world


@pytest.fixture
def shared_gstin(world):
    """Two shops filing one return on the Jharkhand GSTIN (as BV-PUN-01 and
    BV-ONLINE-01 share one)."""
    world["db"].stores.insert_one({"store_id": SISTER, "store_name": "BV Online", "state": "Jharkhand",
                                   "state_code": "20", "entity_id": ENTITY, "gstin": SHOP_GSTIN,
                                   "is_active": True})
    return world


# ---------------------------------------------------------------------------
# H2: GSTR-3B is the GSTIN's return
# ---------------------------------------------------------------------------


def test_gstr3b_page_and_portal_file_cover_every_shop_on_the_gstin(shared_gstin):
    db = shared_gstin["db"]
    _sale(db, STORE, 1000.0, "O-JH")
    _sale(db, SISTER, 500.0, "O-ONLINE")
    _booked(db, "B-JH", SHOP_GSTIN, cgst=150.0)
    for shop in (STORE, SISTER):  # asked through either shop: the same return
        page = _page(shop)
        assert (page["shops"], _heads(page["outwardTaxableSupplies"])) == ([STORE, SISTER], 1500.0)
        assert _heads(page["taxPaidCash"]) == 1200.0
        portal = _portal(shop)["sup_details"]["osup_det"]
        assert portal["camt"] + portal["samt"] == 1500.0


def test_a_reader_who_cannot_see_every_shop_on_the_gstin_is_refused(shared_gstin):
    _sale(shared_gstin["db"], SISTER, 500.0, "O-ONLINE")
    accountant = _user("ACCOUNTANT", STORE)
    for door in (_page, _portal):
        with pytest.raises(HTTPException) as refused:
            door(STORE, accountant)
        assert refused.value.status_code == 403
        assert refused.value.detail == "GSTR-3B covers 2 shops on this GSTIN - ask an admin."
    both = _user("ACCOUNTANT", STORE, SISTER)
    assert _heads(_page(STORE, both)["outwardTaxableSupplies"]) == 500.0


def test_a_gstr3b_read_failure_is_a_503_never_a_zero_return(world, monkeypatch):
    """M7 / LOW 12: a failed read used to return zero credit (an overstated
    cash figure) or a whole all-zero portal file."""
    def unreadable(*_a, **_k):
        raise RuntimeError("vendor_bills unavailable")

    monkeypatch.setattr(gst_itc, "_sum_heads", unreadable)
    monkeypatch.setattr(r3b, "_sum_heads", unreadable)
    for call in (_page, _portal, _table, _cards):
        with pytest.raises(HTTPException) as refused:
            call()
        assert refused.value.status_code == 503, call


# ---------------------------------------------------------------------------
# A04-3 / M3 / M4: the Finance GST table and cards are those returns
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


def test_the_cards_are_the_tables_rows_added_up_refunds_and_transfers_included(two_gstins):
    """M3: the cards kept a copy that ignored the month's refunds and a
    transfer's deemed supply (cards 1000 vs table 900). One rule now."""
    db = two_gstins["db"]
    _sale(db, STORE, 1000.0, "O-JH")
    db.returns.insert_one({"return_id": "RET-1", "store_id": STORE, "status": "COMPLETED",
                           "created_at": SEPT_10, "gst_breakup": {"tax": 100.0, "taxable": 1000.0}})
    # A stock transfer JH -> MH: the sender's outward supply (IGST 60).
    _booked(db, "MIRROR-1", MH_GSTIN, igst=60.0, store=MH_STORE, source_transfer_id="T-1",
            from_store_id=STORE, to_store_id=MH_STORE)
    _booked(db, "B-JH", SHOP_GSTIN, cgst=50.0)
    rows = _table()["registrations"]
    cards = _cards()
    assert _rows()[SHOP_GSTIN] == (960.0, 100.0, 860.0)
    assert cards["gst_collected"] == sum(r["gst_collected"] for r in rows) == 960.0
    assert cards["gst_input_credit"] == sum(r["input_credit"] for r in rows) == 160.0
    assert cards["net_gst_payable"] == sum(r["net_payable"] for r in rows) == 860.0
    assert (cards["cgst"], cards["sgst"], cards["igst"]) == (450.0, 450.0, 60.0)


def test_a_gstin_shared_with_a_hidden_shop_shows_no_input_side_figure(shared_gstin):
    """M4: the accountant sees his own shop. His GSTIN also files for a shop he
    cannot see, so credit, reverse charge and net are the whole return's --
    withheld on the table and the cards; his own output tax is shown."""
    db = shared_gstin["db"]
    _sale(db, STORE, 1000.0, "O-JH")
    _booked(db, "B-JH", SHOP_GSTIN, cgst=150.0)
    [row] = _table(ACCOUNTANT)["registrations"]
    assert (row["gst_collected"], row["shops_outside_view"]) == (1000.0, 1)
    assert (row["input_credit"], row["reverse_charge"], row["net_payable"]) == (None, None, None)
    cards = _cards(ACCOUNTANT)
    assert cards["gst_collected"] == 1000.0
    assert (cards["gst_input_credit"], cards["reverse_charge_tax"], cards["net_gst_payable"]) == (None,) * 3
    assert _rows()[SHOP_GSTIN] == (1000.0, 300.0, 700.0)  # an admin sees the whole return


@pytest.mark.parametrize("month", [0, 13])
def test_a_month_that_is_not_one_is_a_400(world, month):
    for call in (_table, _cards):
        with pytest.raises(HTTPException) as refused:
            call(month=month)
        assert refused.value.status_code == 400


# ---------------------------------------------------------------------------
# Section 17(5): credit not claimable
# ---------------------------------------------------------------------------


def _services_bill(number="FOOD-1", day="2026-09-20", blocked=True, vendor=DEALER, user=ACCOUNTANT):
    bill = vd.VendorBillCreate(bill_number=number, bill_date=day, taxable_amount=1000.0,
                               tax_amount=180.0, total_amount=1180.0, bill_kind="SERVICES",
                               itc_blocked=blocked)
    return _run(vd.create_vendor_bill(vendor, bill, current_user=user))


def _tick(bill, blocked, user=ACCOUNTANT):
    return _run(vd.set_vendor_bill_itc_blocked(
        bill["vendor_id"], bill["bill_id"], vd.VendorBillItcBlocked(itc_blocked=blocked),
        current_user=user))


def _credit_everywhere():
    """(GSTR-3B Table 4, Table 4(B)(1), GST tab table, GST cards, ITC register)."""
    ret = r3b._compute_gstr3b(MONTH, STORE)
    register = _run(fin_itc.itc_register(period=MONTH, entity_id=None, current_user=ADMIN))
    return (_heads(ret["itcAvailable"]), _heads(ret["itcBlocked"]), _rows()[SHOP_GSTIN][1],
            _cards()["gst_input_credit"], float(register.get("total_itc") or 0))


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
    assert _cards()["gst_input_credit_excluded"] == 180.0

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


def test_the_17_5_tick_closes_when_the_months_gstr3b_falls_due(world, monkeypatch):
    """M6: September's GSTR-3B is due on 20 October (IST). On the 20th the
    tick still changes; on the 21st it is the CA's, in the current return."""
    bill = _services_bill(day="2026-09-20", blocked=False)
    monkeypatch.setattr(ap_bills, "now_ist_naive", lambda: datetime(2026, 10, 20, 23, 0))
    assert _tick(bill, True)["itc_blocked"] is True
    monkeypatch.setattr(ap_bills, "now_ist_naive", lambda: datetime(2026, 10, 21, 0, 30))
    with pytest.raises(HTTPException) as refused:
        _tick(bill, False)
    assert refused.value.status_code == 422
    assert refused.value.detail["message"] == (
        "GSTR-3B for September 2026 is due/filed - record the change in the current return with your CA.")
    assert world["db"].vendor_bills.find_one({"bill_id": bill["bill_id"]})["itc_blocked"] is True


def test_the_17_5_tick_reaches_only_the_callers_own_shops(world):
    """LOW 12: another shop's bill is not there for the caller (404)."""
    bill = _services_bill(blocked=False)
    other = _user("ACCOUNTANT", "BV-ELSEWHERE")
    with pytest.raises(HTTPException) as refused:
        _tick(bill, True, other)
    assert refused.value.status_code == 404
    from api.services.rbac_policy import POLICY

    row = next(r for r in POLICY if r["path"].endswith("/bills/{bill_id}/itc-blocked"))
    assert (row["method"], sorted(row["allowed"])) == ("PUT", ["ACCOUNTANT", "ADMIN"])


def test_goods_bought_without_a_po_may_carry_the_tick_a_po_stock_bill_may_not(world):
    """LOW 13: a walk-in goods bill ticked when booked claims no credit, its
    GST in 4(B)(1) and in the goods' cost; a PO receipt's stock bill keeps its
    credit switch. A goods bill's tick is not changed afterwards (its stock
    is costed by then)."""
    db = world["db"]
    from tests.test_receipt_without_po import _on_the_shelf

    walk_in = _on_the_shelf(world, vendor_invoice_no="GIFT-1")
    gift = vd.VendorBillCreate(bill_number="GIFT-1", bill_date="2026-09-14", taxable_amount=7460.0,
                               tax_amount=373.0, total_amount=7833.0, grn_id=walk_in, itc_blocked=True)
    booked = _run(vd.create_vendor_bill(DEALER, gift, current_user=ACCOUNTANT))
    assert (booked["itc_blocked"], booked["itc_eligible"]) == (True, False)
    ret = r3b._compute_gstr3b(MONTH, STORE)
    assert (_heads(ret["itcAvailable"]), _heads(ret["itcBlocked"])) == (0.0, 373.0)
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3255.0
    with pytest.raises(HTTPException) as refused:
        _tick(booked, False)
    assert refused.value.status_code == 422

    _po_receipt(db, "GRN-STD-0170")
    stock = vd.VendorBillCreate(bill_number="PO-1", bill_date="2026-09-14", taxable_amount=6200.0,
                                tax_amount=310.0, total_amount=6510.0, grn_id="GRN-STD-0170",
                                itc_blocked=True)
    with pytest.raises(HTTPException) as refused:
        _run(vd.create_vendor_bill(DEALER, stock, current_user=ACCOUNTANT))
    assert refused.value.detail["code"] == "ITC_BLOCKED_SERVICES_ONLY"


# ---------------------------------------------------------------------------
# F04: a bill booked without input credit adds its GST to the units' cost
# ---------------------------------------------------------------------------


def _po_receipt(db, grn_id, vendor=DEALER, cost=3100.0, subtype="STANDARD", **extra):
    _seed_receipt(db, grn_id=grn_id, subtype=subtype, po_id=extra.pop("po_id", f"PO-{grn_id[-4:]}"),
                  vendor_id=vendor, **extra)
    _minted(db, grn_id, cost)


def _units(db, grn_id):
    return [(u.get("unit_cost"), u.get("landed_cost"), stock_value._own_cost(u))
            for u in db.stock_units.find({"grn_id": grn_id}).sort("stock_id", 1)]


def _shelf_value(db):
    return stock_value.by_product(stock_value.shelf_units(
        StockRepository(db.stock_units), ProductRepository(db.products), STORE))[FRAME]["cost"]


def test_a_po_bill_booked_without_credit_puts_its_gst_into_the_units_cost(world):
    db = world["db"]
    _po_receipt(db, "GRN-STD-0401")
    # A unit of the receipt sold before the bill: it keeps its receipt cost
    # (its GST reaches later units through the moving average, LOW 10).
    db.stock_units.update_one({"stock_id": "SU-GRN-STD-0401-1"}, {"$set": {"status": "SOLD"}})
    off = _invoice_body("GRN-STD-0401", "TAX-401")
    off.itc_eligible = False
    _run(pi.create_purchase_invoice(off, current_user=ACCOUNTANT))
    # The supplier's price stays the unit's price (the last-paid quote); the
    # stock is worth price + the 5% GST that cannot be claimed back.
    assert _units(db, "GRN-STD-0401") == [(3100.0, 3255.0, 3255.0), (3100.0, None, 3100.0)]
    assert _shelf_value(db) == 3255.0
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3255.0
    # The same bill again adds nothing -- even if the unit's cost source were
    # reset (the guard is the bill's own mark, LOW 12).
    db.stock_units.update_one({"stock_id": "SU-GRN-STD-0401-0"}, {"$set": {"cost_source": "GRN_PO"}})
    stored = db.vendor_bills.find_one({"grn_id": "GRN-STD-0401"})
    pi._add_unclaimed_gst_to_units(db, [db.grns.find_one({"grn_id": "GRN-STD-0401"})], stored,
                                   stored["lines"])
    assert _units(db, "GRN-STD-0401")[0][:2] == (3100.0, 3255.0)


def test_a_po_bill_claiming_credit_leaves_the_cost_before_gst(world):
    db = world["db"]
    _po_receipt(db, "GRN-STD-0402")
    _run(pi.create_purchase_invoice(_invoice_body("GRN-STD-0402", "TAX-402"), current_user=ACCOUNTANT))
    assert _units(db, "GRN-STD-0402") == [(3100.0, None, 3100.0)] * 2
    assert _shelf_value(db) == 6200.0
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3100.0


def test_the_cash_flow_door_costs_a_po_receipt_at_the_bills_own_price(world):
    """LOW 17: Sharma (no GSTIN) bills the two frames at Rs 3000, not the
    order's 3100: the product costs the bill's price + 5%; each unit its
    receipt price + the GST on the bill's price."""
    db = world["db"]
    _po_receipt(db, "GRN-STD-0403", vendor=SHARMA)
    bill = vd.VendorBillCreate(bill_number="SH-403", bill_date="2026-09-14", taxable_amount=6000.0,
                               tax_amount=300.0, total_amount=6300.0, grn_id="GRN-STD-0403")
    assert _run(vd.create_vendor_bill(SHARMA, bill, current_user=ACCOUNTANT))["itc_eligible"] is False
    assert db.products.find_one({"product_id": FRAME})["cost_price"] == 3150.0
    assert _units(db, "GRN-STD-0403") == [(3100.0, 3250.0, 3250.0)] * 2


def test_a_delivery_challan_bill_costs_the_challans_units_too(world):
    """M5: a consolidated bill names its challans (linked_dc_ids), no goods
    receipt; its unclaimed GST still lands on the challans' units."""
    db = world["db"]
    for dc in ("DC-0001", "DC-0002"):
        _po_receipt(db, dc, subtype="DELIVERY_CHALLAN", po_id=None,
                    items=[{"product_id": FRAME, "received_qty": 1, "accepted_qty": 1,
                            "rejected_qty": 0, "unit_price": 3100.0}])
        db.stock_units.delete_one({"stock_id": f"SU-{dc}-1"})
    doc = {"bill_id": "DC-BILL", "invoice_id": "DC-BILL", "itc_eligible": False,
           "linked_dc_ids": ["DC-0001", "DC-0002"]}
    pi._apply_valuation_trueup(db, doc, {"lines": [
        {"product_id": FRAME, "qty": 2, "unit_price": 3100.0, "gst_rate": 5.0}]}, {})
    assert [u[1] for u in _units(db, "DC-0001") + _units(db, "DC-0002")] == [3255.0, 3255.0]


def test_a_multi_piece_unit_billed_in_parts_takes_each_parts_share(world):
    """LOW 8: one stock row of 10 boxes, billed 4 + 6 without credit at
    Rs 100 + 5%: 4/10 of the GST, then all of it; a unit of another receipt
    listed first is passed over, never a reason to stop."""
    db = world["db"]
    _seed_receipt(db, grn_id="GRN-STD-0808", subtype="STANDARD", po_id="PO-0808",
                  items=[{"product_id": FRAME, "received_qty": 10, "accepted_qty": 10,
                          "rejected_qty": 0, "unit_price": 100.0}])
    db.stock_units.insert_many([
        {"stock_id": "A-OTHER", "product_id": FRAME, "store_id": STORE, "status": "AVAILABLE",
         "quantity": 50, "unit_cost": 90.0, "grn_id": "GRN-ELSE"},
        {"stock_id": "B-BOXES", "product_id": FRAME, "store_id": STORE, "status": "AVAILABLE",
         "quantity": 10, "unit_cost": 100.0, "grn_id": "GRN-STD-0808"},
    ])
    grn = db.grns.find_one({"grn_id": "GRN-STD-0808"})

    def part(bill_id, qty):
        pi._add_unclaimed_gst_to_units(db, [grn], {"bill_id": bill_id, "itc_eligible": False},
                                       [{"product_id": FRAME, "qty": qty, "unit_price": 100.0, "gst_rate": 5.0}])
        return db.stock_units.find_one({"stock_id": "B-BOXES"})

    assert part("P1", 4)["landed_cost"] == 102.0
    boxes = part("P2", 6)
    assert (boxes["landed_cost"], boxes["unclaimed_gst"]["pieces"]) == (105.0, 10)
    assert part("P2", 6)["landed_cost"] == 105.0  # the same bill again: nothing
    assert "landed_cost" not in db.stock_units.find_one({"stock_id": "A-OTHER"})


def test_an_unpriced_receipt_billed_in_two_parts_prices_every_unit(world):
    """LOW 9: one part with credit, one without, at one price: each part
    prices its own pieces (3100, and 3100 + 5%) -- none left pending."""
    db = world["db"]
    grn = _seed_receipt(db, grn_id="GRN-STD-0909", subtype="STANDARD", po_id="PO-0909")
    _minted(db, "GRN-STD-0909", 0)
    for n, credit in ((1, True), (2, False)):
        db.vendor_bills.insert_one({
            "bill_id": f"PART-{n}", "grn_id": "GRN-STD-0909", "doc_type": "PURCHASE_INVOICE",
            "status": "OUTSTANDING", "vendor_id": DEALER, "itc_eligible": credit,
            "created_at": f"2026-09-1{n}T10:00:00",
            "lines": [{"product_id": FRAME, "qty": 1, "unit_price": 3100.0, "gst_rate": 5.0}]})
    pi._resolve_billed_receipt_costs(db, grn)
    assert sorted(u[1] for u in _units(db, "GRN-STD-0909")) == [3100.0, 3255.0]


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
    for wrong in ((today + timedelta(days=1)).isoformat(), "2017-06-30", "2026-02-30", "31/08/2026", "",
                  "2026-08-31junk", "2026-08-31T10:00:00"):
        with pytest.raises(SupplierCreditError) as refused:
            confirm(wrong)
        assert refused.value.code == "SUPPLIER_NOTE_DATE_REQUIRED", wrong
    assert confirm(date(2017, 7, 1))["credit_note_date"] == "2017-07-01"
