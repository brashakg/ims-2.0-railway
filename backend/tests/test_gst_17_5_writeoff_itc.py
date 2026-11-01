"""Section 17(5) (owner 2026-10-24 "check yourself"; CA 2026-10-23 Q11).

(a) Every expense / services bill can carry the "credit not claimable" tick;
    its DEFAULT comes from one table by expense type (ap_engine.EXPENSE_TYPES),
    and the accountant can still change it per bill.
(b) 17(5)(h): stock written off (lost, stolen, destroyed) takes back the input
    credit claimed on those exact pieces, per GSTIN, in GSTR-3B Table 4(B)(1)
    of the write-off month (services/writeoff_itc), shown on the write-off.
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.routers import item_events as iev
from api.routers import purchase_invoices as pi
from api.routers import vendors as vd
from api.routers.inventory import stock_count_reconcile as scr
from api.routers.reports import gstr3b as r3b
from api.services import ap_engine
from api.services.gstn_export import to_gstr3b_json
from tests.test_gst_input_credit_one_rule import (  # noqa: F401 -- fixtures
    MH_GSTIN,
    MH_STORE,
    SISTER,
    _before_septembers_return_is_due,
    _heads,
    _po_receipt,
    shared_gstin,
    two_gstins,
)
from tests.test_receipt_without_po import (  # noqa: F401 -- `world` is the fixture
    ACCOUNTANT,
    ADMIN,
    DEALER,
    ENTITY,
    FRAME,
    MONTH,
    SHARMA,
    SHOP_GSTIN,
    STORE,
    _invoice_body,
    _run,
    world,
)

OCTOBER = "2026-10"


# ---------------------------------------------------------------------------
# (a) the default tick by expense type
# ---------------------------------------------------------------------------

BLOCKED_BY_DEFAULT = {"FOOD", "MOTOR_VEHICLE", "GIFTS", "CLUB_PERSONAL", "CONSTRUCTION", "STAFF_BENEFITS"}
CLAIMABLE_BY_DEFAULT = {"RENT", "UTILITIES", "SUPPLIES", "MARKETING", "REPAIRS", "STOCK",
                        "BUSINESS_TRAVEL", "FREIGHT", "OTHER"}


def _expense_bill(number, expense_type=None, **over):
    bill = vd.VendorBillCreate(bill_number=number, bill_date="2026-09-20", taxable_amount=1000.0,
                               tax_amount=180.0, total_amount=1180.0, bill_kind="SERVICES",
                               expense_type=expense_type, **over)
    return _run(vd.create_vendor_bill(DEALER, bill, current_user=ACCOUNTANT))


def test_the_table_names_every_owner_listed_type_with_its_default():
    table = {key: blocked for key, _label, blocked in ap_engine.EXPENSE_TYPES}
    assert {k for k, b in table.items() if b} == BLOCKED_BY_DEFAULT
    assert {k for k, b in table.items() if not b} == CLAIMABLE_BY_DEFAULT


@pytest.mark.parametrize("expense_type", sorted(BLOCKED_BY_DEFAULT | CLAIMABLE_BY_DEFAULT))
def test_a_bill_with_no_tick_sent_takes_its_expense_types_default(world, expense_type):
    bill = _expense_bill(f"EXP-{expense_type}", expense_type.lower())
    blocked = expense_type in BLOCKED_BY_DEFAULT
    assert (bill["expense_type"], bill["itc_blocked"], bill["itc_eligible"]) == (
        expense_type, blocked, not blocked)
    ret = r3b._compute_gstr3b(MONTH, STORE)
    assert (_heads(ret["itcAvailable"]), _heads(ret["itcBlocked"])) == (
        (0.0, 180.0) if blocked else (180.0, 0.0))


def test_the_accountant_can_still_change_the_tick_on_any_type(world):
    food = _expense_bill("FOOD-CLAIM", "FOOD", itc_blocked=False)
    rent = _expense_bill("RENT-BLOCK", "RENT", itc_blocked=True)
    assert (food["itc_blocked"], rent["itc_blocked"]) == (False, True)
    # No type named: unticked, as before.
    assert _expense_bill("PLAIN")["itc_blocked"] is False


def test_an_unknown_expense_type_is_refused():
    with pytest.raises(ValidationError):
        vd.VendorBillCreate(bill_number="X", bill_date="2026-09-20", taxable_amount=1.0,
                            total_amount=1.0, bill_kind="SERVICES", expense_type="YACHT")


def test_the_form_reads_the_same_table_accounts_only(world):
    out = _run(vd.expense_types(current_user=ACCOUNTANT))
    assert [(t["value"], t["blocked_by_default"]) for t in out["types"]] == [
        (k, b) for k, _l, b in ap_engine.EXPENSE_TYPES]
    from api.services.rbac_policy import POLICY

    row = next(r for r in POLICY if r["path"] == "/api/v1/vendors/expense-types")
    assert (row["method"], sorted(row["allowed"])) == ("GET", ["ACCOUNTANT", "ADMIN"])


# ---------------------------------------------------------------------------
# (b) 17(5)(h): credit reversed on written-off stock
# ---------------------------------------------------------------------------

class _October(datetime):
    """The write-off happens on 5 October (11:30 IST)."""

    @classmethod
    def utcnow(cls):
        return datetime(2026, 10, 5, 6, 0)


@pytest.fixture
def october(monkeypatch):
    monkeypatch.setattr(scr, "datetime", _October)
    monkeypatch.setattr(iev, "datetime", _October)
    # The item ledger's own clock: a DAMAGED event is dated 5 October too.
    monkeypatch.setattr(iev.ie, "_now", lambda: _October.utcnow().replace(tzinfo=timezone.utc))


def _stocked(db, grn_id, vendor=DEALER):
    """Two frames received on a PO receipt at Rs 3,100 each."""
    _po_receipt(db, grn_id, vendor=vendor)


def _line_bill(grn_id, number, *, vendor=DEALER, credit=True):
    body = _invoice_body(grn_id, number, vendor=vendor)  # 5%: Rs 77.50 + 77.50 a piece
    body.itc_eligible = credit
    return _run(pi.create_purchase_invoice(body, current_user=ACCOUNTANT))


def _count_write_off(db, grn_id, count_id="CNT-1", n=1):
    """A completed whole-shop count that did not find `n` of the receipt's
    frames, written off by the admin."""
    units = list(db.stock_units.find({"grn_id": grn_id}).sort("stock_id", 1))[:n]
    db.stock_counts.insert_one({
        "count_id": count_id, "status": "completed", "store_id": STORE, "audit_number": count_id,
        "variances": [{
            "product_id": FRAME, "product_name": "Acme Aviator 5420", "sku": "S",
            "system_quantity": 2, "physical_quantity": 2 - n,
            "missing_units": [{"unit_id": str(u["_id"]), "barcode": u["stock_id"]} for u in units],
        }],
    })
    return _run(scr.reconcile_stock_count(count_id, None, current_user=ADMIN))


def _written_off(ret):
    return _heads(ret["itcWriteOffReversed"])


def test_a_counted_loss_reverses_the_credit_on_those_exact_pieces(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0001")
    _line_bill("GRN-WO-0001", "TAX-WO-1")
    done = _count_write_off(db, "GRN-WO-0001")
    assert done["units_voided"] == 1
    # Shown on the write-off: the count, its shrinkage row and the answer.
    assert done["itc_reversed"] == {"igst": 0.0, "cgst": 77.5, "sgst": 77.5, "total": 155.0}
    assert db.stock_shrinkage.find_one({"count_id": "CNT-1"})["itc_reversed"]["total"] == 155.0
    assert db.stock_counts.find_one({"count_id": "CNT-1"})["itc_reversed"]["total"] == 155.0

    # October's return (the write-off month) takes it back, Table 4(B)(1);
    # September's credit stands.
    october_ret = r3b._compute_gstr3b(OCTOBER, STORE)
    assert _written_off(october_ret) == 155.0
    assert _heads(october_ret["itcAvailable"]) == -155.0
    # The GSTIN-bound slice the Cross-Check and the Finance GST cards add up
    # takes it too (gst_by_registration reads the slice).
    assert _heads(october_ret["itcAvailableGstin"]) == -155.0
    from api.routers.finance import gst as fin_gst

    summary = _run(fin_gst.get_gst_summary(month=10, year=2026, current_user=ADMIN))
    assert summary["gst_input_credit"] == -155.0
    september = r3b._compute_gstr3b(MONTH, STORE)
    assert (_heads(september["itcAvailable"]), _written_off(september)) == (310.0, 0.0)
    # The portal file: 4(A)(5) carries it, 4(B)(1) reverses it, 4(C) is net.
    portal = to_gstr3b_json(october_ret, gstin=SHOP_GSTIN, period=OCTOBER)["itc_elg"]
    rul = next(r for r in portal["itc_rev"] if r["ty"] == "RUL")
    oth = next(r for r in portal["itc_avl"] if r["ty"] == "OTH")
    assert (rul["camt"], rul["samt"], oth["camt"], portal["itc_net"]["camt"]) == (77.5, 77.5, 0.0, -77.5)


def test_the_reversal_is_hidden_from_a_manager_on_the_count(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0002")
    _line_bill("GRN-WO-0002", "TAX-WO-2")
    _count_write_off(db, "GRN-WO-0002")
    manager = {"user_id": "u-mgr", "roles": ["STORE_MANAGER"], "store_ids": [STORE], "active_store_id": STORE}
    assert "itc_reversed" not in _run(scr.get_stock_count("CNT-1", current_user=manager))
    assert _run(scr.get_stock_count("CNT-1", current_user=ADMIN))["itc_reversed"]["total"] == 155.0


@pytest.mark.parametrize("case", ["no_bill_yet", "unregistered_dealer", "credit_switched_off", "blocked_17_5"])
def test_pieces_whose_purchase_claimed_no_credit_reverse_nothing(world, october, case):
    db = world["db"]
    vendor = SHARMA if case == "unregistered_dealer" else DEALER
    _stocked(db, "GRN-WO-0003", vendor=vendor)
    if case in ("unregistered_dealer", "credit_switched_off"):
        _line_bill("GRN-WO-0003", "TAX-WO-3", vendor=vendor, credit=case != "credit_switched_off")
    if case == "blocked_17_5":
        _line_bill("GRN-WO-0003", "TAX-WO-3")
        db.vendor_bills.update_many({}, {"$set": {"itc_blocked": True, "itc_eligible": False}})
    done = _count_write_off(db, "GRN-WO-0003")
    assert done["units_voided"] == 1
    assert done["itc_reversed"]["total"] == 0.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0


def test_a_header_only_bill_reverses_the_pieces_share(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0004")
    _run(vd.create_vendor_bill(DEALER, vd.VendorBillCreate(
        bill_number="HDR-4", bill_date="2026-09-14", taxable_amount=6200.0, tax_amount=310.0,
        total_amount=6510.0, grn_id="GRN-WO-0004"), current_user=ACCOUNTANT))
    assert _count_write_off(db, "GRN-WO-0004", n=2)["itc_reversed"]["total"] == 310.0


def test_a_retry_or_a_stuck_finish_never_reverses_twice(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0005")
    _line_bill("GRN-WO-0005", "TAX-WO-5")
    _count_write_off(db, "GRN-WO-0005")
    # The write-off died after the pieces were voided: Finish rebuilds the
    # record and finds the reversal already there.
    db.stock_counts.update_one({"count_id": "CNT-1"}, {"$set": {"status": "reconciling"}})
    db.stock_shrinkage.delete_many({})
    out = _run(scr.finish_stuck_stock_count_reconcile("CNT-1", None, current_user=ADMIN))
    assert out["units_voided"] == 1
    assert db.stock_counts.find_one({"count_id": "CNT-1"})["itc_reversed"]["total"] == 155.0
    assert db.itc_writeoff_reversals.count_documents({}) == 1
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0


def test_a_stuck_finish_reverses_what_the_cut_off_write_off_did_not(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0006")
    _line_bill("GRN-WO-0006", "TAX-WO-6")
    _count_write_off(db, "GRN-WO-0006")
    db.itc_writeoff_reversals.delete_many({})  # cut off before the reversal
    db.stock_counts.update_one({"count_id": "CNT-1"}, {"$set": {"status": "reconciling"}})
    _run(scr.finish_stuck_stock_count_reconcile("CNT-1", None, current_user=ADMIN))
    # In the month the piece was written off (its voided_at), not the Finish's.
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0


def test_a_piece_written_off_as_damaged_reverses_its_credit_once(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0007")
    _line_bill("GRN-WO-0007", "TAX-WO-7")
    sid = "SU-GRN-WO-0007-0"
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "QUARANTINED"}})
    out = _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="DAMAGE"),
                                      current_user=ADMIN))
    assert out["itc_reversed"]["total"] == 155.0
    # A retry (the reversal cut off, or a second click) reverses nothing more.
    again = _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="DAMAGE"),
                                        current_user=ADMIN))
    assert again["itc_reversed"]["total"] == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0
    # Restocked or sent back to the supplier: no write-off, nothing reversed.
    sid2 = "SU-GRN-WO-0007-1"
    db.stock_units.update_one({"stock_id": sid2}, {"$set": {"status": "QUARANTINED"}})
    out = _run(iev.quarantine_release(sid2, iev.QuarantineReleaseRequest(disposition="RESTOCK"),
                                      current_user=ADMIN))
    assert "itc_reversed" not in out
    assert db.itc_writeoff_reversals.count_documents({}) == 1


# ---------------------------------------------------------------------------
# Review round 1: the GSTIN, the claim, the paisa, the order of events
# ---------------------------------------------------------------------------

MH2_STORE = "BV-NOPO-MH2"


class _November(datetime):
    """A later step happens on 3 November (11:30 IST)."""

    @classmethod
    def utcnow(cls):
        return datetime(2026, 11, 3, 6, 0)


def _to_november(monkeypatch):
    from api.services import writeoff_itc as wo

    for mod in (scr, iev):
        monkeypatch.setattr(mod, "datetime", _November)
    # Only the service's clock: a datetime subclass there would fail
    # _lost_at's isinstance check on real stored stamps.
    monkeypatch.setattr(wo, "_now", _November.utcnow)
    monkeypatch.setattr(iev.ie, "_now", lambda: _November.utcnow().replace(tzinfo=timezone.utc))


def _mirror(db, tid, *, gstin=MH_GSTIN, to_store=MH_STORE, lines=None, day="2026-09-20"):
    """The transfer's mirror bill as _book_mirror_purchase stores it."""
    db.vendor_bills.insert_one({
        "bill_id": f"MB-{tid}", "bill_number": f"TI-{tid}", "source_transfer_id": tid,
        "status": "OUTSTANDING", "bill_date": day, "invoice_date": day,
        "from_store_id": STORE, "to_store_id": to_store, "store_id": to_store,
        "vendor_id": ENTITY, "vendor_gstin": SHOP_GSTIN, "entity_id": ENTITY,
        "recipient_entity_id": ENTITY, "recipient_gstin": gstin, "itc_eligible": True,
        "taxable_amount": 3100.0, "tax_amount": 155.0, "igst_total": 155.0,
        "cgst_total": 0.0, "sgst_total": 0.0,
        "lines": lines if lines is not None else [
            {"product_id": FRAME, "qty": 1, "taxable": 3100.0, "gst_rate": 5.0,
             "cgst": 0.0, "sgst": 0.0, "igst": 155.0}],
    })


def _rehomed(db, sid, tid, store):
    """The unit exactly as transfers._rehome leaves it, and the transfer's
    line listing it as received."""
    db.stock_units.update_one({"stock_id": sid}, {"$set": {
        "status": "AVAILABLE", "store_id": store, "source_type": "TRANSFER", "source_id": tid,
        "transfer_id": None, "transfer_to_store_id": None}})
    db.stock_transfers.insert_one({"id": tid, "items": [{"product_id": FRAME, "received_stock_ids": [sid]}]})


def _damage(db, sid, user=ADMIN):
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "QUARANTINED"}})
    return _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="DAMAGE"),
                                       current_user=user))


MH_ADMIN = {**ADMIN, "store_ids": [STORE, MH_STORE, MH2_STORE]}


def test_a_transferred_piece_reverses_on_the_gstin_that_claimed_it(two_gstins, october):
    """Moved to Maharashtra on a transfer invoice, then on to a second
    Maharashtra shop (no invoice: one GSTIN): the credit is the Maharashtra
    GSTIN's, so the reversal is too."""
    db = two_gstins["db"]
    db.stores.insert_one({"store_id": MH2_STORE, "store_name": "BV Pune 2", "state": "Maharashtra",
                          "state_code": "27", "entity_id": ENTITY, "gstin": MH_GSTIN, "is_active": True})
    _stocked(db, "GRN-WO-0008")
    _line_bill("GRN-WO-0008", "TAX-WO-8")
    sid = "SU-GRN-WO-0008-0"
    _mirror(db, "TRF-8")
    _rehomed(db, sid, "TRF-8", MH_STORE)
    _rehomed(db, sid, "TRF-9", MH2_STORE)  # within the one GSTIN: no mirror bill
    out = _damage(db, sid, MH_ADMIN)
    assert out["itc_reversed"] == {"igst": 155.0, "cgst": 0.0, "sgst": 0.0, "total": 155.0}
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0


def test_a_mirror_bill_naming_no_gstin_reverses_on_its_receiving_shops(two_gstins, october):
    """A mirror with no recipient GSTIN and one aggregate line naming no
    product: GSTR-3B places it on the receiving shop's GSTIN, so the reversal
    lands there, the piece's share of the bill by its cost."""
    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0009")
    _line_bill("GRN-WO-0009", "TAX-WO-9")
    sid = "SU-GRN-WO-0009-0"
    _mirror(db, "TRF-10", gstin="", lines=[
        {"product_id": None, "qty": 0.0, "taxable": 3100.0, "cgst": 0.0, "sgst": 0.0, "igst": 155.0}])
    _rehomed(db, sid, "TRF-10", MH_STORE)
    assert _damage(db, sid, MH_ADMIN)["itc_reversed"]["total"] == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0


def test_credit_no_return_places_is_held_unallocated_never_shown_as_reversed(two_gstins, october):
    """A mirror into a shop with no GSTIN: no return carries its credit, so
    the reversal has no GSTIN -- held, listed on GSTR-3B, never on the
    write-off as reversed -- until the bill's GSTIN is settled."""
    db = two_gstins["db"]
    db.stores.insert_one({"store_id": "BV-NOGST", "store_name": "BV Kiosk", "state": "Karnataka",
                          "state_code": "29", "entity_id": ENTITY, "is_active": True})
    _stocked(db, "GRN-WO-0010")
    _line_bill("GRN-WO-0010", "TAX-WO-10")
    sid = "SU-GRN-WO-0010-0"
    _mirror(db, "TRF-11", gstin="", to_store="BV-NOGST")
    _rehomed(db, sid, "TRF-11", "BV-NOGST")
    out = _damage(db, sid, {**ADMIN, "store_ids": [STORE, "BV-NOGST"]})
    assert out["itc_reversed"] == {"igst": 0.0, "cgst": 0.0, "sgst": 0.0, "total": 0.0,
                                   "unallocated_total": 155.0}
    row = db.itc_writeoff_reversals.find_one({"_id": sid})
    assert (row["gstin"], row["unallocated"]) == (None, True)
    ret = r3b._compute_gstr3b(OCTOBER, STORE)
    assert _written_off(ret) == 0.0
    assert (ret["unallocatedWriteOffs"]["count"], ret["unallocatedWriteOffs"]["tax"]) == (1, 155.0)
    # The accountant settles the bill on Maharashtra: the reversal follows.
    db.vendor_bills.update_one({"bill_id": "MB-TRF-11"}, {"$set": {"recipient_gstin": MH_GSTIN}})
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 155.0
    assert r3b._compute_gstr3b(OCTOBER, STORE)["unallocatedWriteOffs"]["count"] == 0


@pytest.mark.parametrize("case", ["pending", "invalid_supplier_gstin"])
def test_claimed_is_whatever_table_4_counted(world, october, case):
    """A bill Table 4 counts (any live status) reverses; one it never counts
    (a legacy bill whose recorded supplier GSTIN is invalid) reverses nothing."""
    db = world["db"]
    _stocked(db, "GRN-WO-0011")
    _line_bill("GRN-WO-0011", "TAX-WO-11")
    if case == "pending":
        db.vendor_bills.update_many({}, {"$set": {"status": "PENDING"}})
    else:
        db.vendor_bills.update_many({}, {"$set": {"vendor_gstin": "20BADGSTIN0000"},
                                         "$unset": {"itc_eligible": ""}})
    table4 = _heads(r3b._compute_gstr3b(MONTH, STORE)["itcAvailable"])
    done = _count_write_off(db, "GRN-WO-0011")
    assert (table4, done["itc_reversed"]["total"]) == ((310.0, 155.0) if case == "pending" else (0.0, 0.0))


def test_a_whole_line_written_off_reverses_exactly_its_heads(world, october):
    """Three pieces on one line, Rs 25.00 + 25.00 of GST: piece by piece the
    shares are 8.33 / 8.34 / 8.33, so all three reverse exactly 50.00."""
    db = world["db"]
    _po_receipt(db, "GRN-WO-0012")
    db.stock_units.insert_one({"stock_id": "SU-GRN-WO-0012-2", "product_id": FRAME, "store_id": STORE,
                               "status": "AVAILABLE", "source_type": "GRN", "source_id": "GRN-WO-0012",
                               "grn_id": "GRN-WO-0012"})
    db.vendor_bills.insert_one({
        "bill_id": "B-12", "bill_number": "B-12", "vendor_id": DEALER, "store_id": STORE,
        "grn_id": "GRN-WO-0012", "bill_date": "2026-09-14", "status": "OUTSTANDING",
        "recipient_entity_id": ENTITY, "recipient_gstin": SHOP_GSTIN, "itc_eligible": True,
        "taxable_amount": 999.99, "cgst_total": 25.0, "sgst_total": 25.0, "igst_total": 0.0,
        "tax_amount": 50.0, "lines": [{"product_id": FRAME, "qty": 3, "taxable": 999.99,
                                       "cgst": 25.0, "sgst": 25.0, "igst": 0.0}],
    })
    for i in range(3):  # one at a time, as three separate write-offs would
        _damage(db, f"SU-GRN-WO-0012-{i}")
    ret = r3b._compute_gstr3b(OCTOBER, STORE)
    assert ret["itcWriteOffReversed"]["centralTax"] == 25.0
    assert ret["itcWriteOffReversed"]["stateTax"] == 25.0


@pytest.mark.parametrize("door", ["line_bill", "header_bill"])
def test_a_bill_booked_after_the_write_off_reverses_it(world, october, door):
    """Counted missing (5 October) before the September bill came: the bill
    claims credit on the lost piece, so booking it reverses that credit, in
    the month of the loss -- never before the loss happened."""
    db = world["db"]
    _stocked(db, "GRN-WO-0013")
    assert _count_write_off(db, "GRN-WO-0013")["itc_reversed"]["total"] == 0.0
    if door == "line_bill":
        _line_bill("GRN-WO-0013", "TAX-WO-13")
    else:
        _run(vd.create_vendor_bill(DEALER, vd.VendorBillCreate(
            bill_number="HDR-13", bill_date="2026-09-14", taxable_amount=6200.0, tax_amount=310.0,
            total_amount=6510.0, grn_id="GRN-WO-0013"), current_user=ACCOUNTANT))
    row = db.itc_writeoff_reversals.find_one({})
    assert (row["cgst"] + row["sgst"], row["period"], row["source_type"]) == (155.0, OCTOBER, "STOCK_COUNT")
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0
    assert _written_off(r3b._compute_gstr3b(MONTH, STORE)) == 0.0


def test_a_header_only_bill_shares_equally_when_the_pieces_carry_no_price(world, october):
    db = world["db"]
    _po_receipt(db, "GRN-WO-0014", cost=0)
    _run(vd.create_vendor_bill(DEALER, vd.VendorBillCreate(
        bill_number="HDR-14", bill_date="2026-09-14", taxable_amount=6200.0, tax_amount=310.0,
        total_amount=6510.0, grn_id="GRN-WO-0014"), current_user=ACCOUNTANT))
    assert _count_write_off(db, "GRN-WO-0014")["itc_reversed"]["total"] == 155.0


def test_a_stuck_finish_never_dates_a_reversal_into_a_locked_month(world, october, monkeypatch):
    db = world["db"]
    _stocked(db, "GRN-WO-0015")
    _line_bill("GRN-WO-0015", "TAX-WO-15")
    _count_write_off(db, "GRN-WO-0015")
    db.itc_writeoff_reversals.delete_many({})  # cut off before the reversal
    db.stock_counts.update_one({"count_id": "CNT-1"}, {"$set": {"status": "reconciling"}})
    db.period_locks.insert_one({"month": 10, "year": 2026})  # October filed
    _to_november(monkeypatch)
    _run(scr.finish_stuck_stock_count_reconcile("CNT-1", None, current_user=ADMIN))
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0
    assert _written_off(r3b._compute_gstr3b("2026-11", STORE)) == 155.0
    assert db.itc_writeoff_reversals.find_one({})["writeoff_period"] == OCTOBER


def test_a_damage_cut_off_after_its_reversal_is_finished_by_a_retry(world, october, monkeypatch):
    """The reversal is written before the piece moves: a crash between the
    two keeps it, and the retry moves the piece without reversing twice. A
    move that loses its race drops the row it wrote."""
    db = world["db"]
    _stocked(db, "GRN-WO-0016")
    _line_bill("GRN-WO-0016", "TAX-WO-16")
    sid = "SU-GRN-WO-0016-0"
    real = iev.ie.record_event_atomic

    def crash(*a, **kw):
        raise RuntimeError("process killed")

    monkeypatch.setattr(iev.ie, "record_event_atomic", crash)
    with pytest.raises(RuntimeError):
        _damage(db, sid)
    assert db.itc_writeoff_reversals.count_documents({"_id": sid}) == 1
    monkeypatch.setattr(iev.ie, "record_event_atomic", real)
    assert _damage(db, sid)["itc_reversed"]["total"] == 155.0
    assert db.itc_writeoff_reversals.count_documents({}) == 1
    # Lost race: the piece was not quarantined after all.
    sid2 = "SU-GRN-WO-0016-1"
    monkeypatch.setattr(iev.ie, "record_event_atomic", lambda *a, **kw: (False, None))
    with pytest.raises(Exception):
        _damage(db, sid2)
    assert db.itc_writeoff_reversals.count_documents({"_id": sid2}) == 0


def test_a_retried_damage_is_dated_when_the_piece_was_damaged(world, october):
    db = world["db"]
    _stocked(db, "GRN-WO-0017")
    _line_bill("GRN-WO-0017", "TAX-WO-17")
    sid = "SU-GRN-WO-0017-0"
    # Written off on 30 September by a build that recorded no reversal.
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "DAMAGED"}})
    db.item_events.insert_one({"stock_id": sid, "event_type": "QUARANTINE_OUT", "to_state": "DAMAGED",
                               "at": datetime(2026, 9, 30, 6, 0)})
    out = _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="DAMAGE"),
                                      current_user=ADMIN))
    assert out["itc_reversed"]["total"] == 155.0
    assert db.itc_writeoff_reversals.find_one({"_id": sid})["period"] == MONTH


def _requarantine(sid):
    from api.routers.inventory import QuarantineRequest, quarantine_stock_unit

    return _run(quarantine_stock_unit(sid, QuarantineRequest(reason="DEFECTIVE"), ADMIN))


def test_a_damaged_piece_brought_back_drops_its_reversal(world, october):
    """Re-quarantined in the same open month: the reversal row goes; written
    off again, it reverses again."""
    db = world["db"]
    _stocked(db, "GRN-WO-0018")
    _line_bill("GRN-WO-0018", "TAX-WO-18")
    sid = "SU-GRN-WO-0018-0"
    _damage(db, sid)
    _requarantine(sid)
    assert db.itc_writeoff_reversals.count_documents({}) == 0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0
    assert _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="DAMAGE"),
                                       current_user=ADMIN))["itc_reversed"]["total"] == 155.0


def test_a_damaged_piece_brought_back_after_its_month_is_filed_is_reclaimed(world, october, monkeypatch):
    db = world["db"]
    _stocked(db, "GRN-WO-0019")
    _line_bill("GRN-WO-0019", "TAX-WO-19")
    sid = "SU-GRN-WO-0019-0"
    _damage(db, sid)
    db.period_locks.insert_one({"month": 10, "year": 2026})
    _to_november(monkeypatch)
    _requarantine(sid)
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0  # the filed return stands
    # Re-claimed in November: 4(A)(5) takes the credit back, 4(B)(1) never
    # goes negative.
    nov = r3b._compute_gstr3b("2026-11", STORE)
    assert (_written_off(nov), _heads(nov["itcWriteOffReclaimed"]), _heads(nov["itcAvailable"])) == (
        0.0, 155.0, 155.0)
    portal = to_gstr3b_json(nov, gstin=SHOP_GSTIN, period="2026-11")["itc_elg"]
    rul = next(r for r in portal["itc_rev"] if r["ty"] == "RUL")
    oth = next(r for r in portal["itc_avl"] if r["ty"] == "OTH")
    assert (rul["camt"], rul["samt"], oth["camt"], oth["samt"], portal["itc_net"]["camt"]) == (
        0.0, 0.0, 77.5, 77.5, 77.5)
    # The re-claim is the GSTIN's own credit (the slice bound to it), never
    # the company-wide remainder.
    assert (_heads(nov["itcAvailableGstin"]), _heads(nov["itcAvailableRegular"])) == (155.0, 0.0)
    # A month with both: the November loss files whole in 4(B)(1), the
    # re-claim whole in 4(A)(5), net nil.
    _damage(db, "SU-GRN-WO-0019-1")
    nov = r3b._compute_gstr3b("2026-11", STORE)
    portal = to_gstr3b_json(nov, gstin=SHOP_GSTIN, period="2026-11")["itc_elg"]
    rul = next(r for r in portal["itc_rev"] if r["ty"] == "RUL")
    oth = next(r for r in portal["itc_avl"] if r["ty"] == "OTH")
    assert (rul["camt"], oth["camt"], portal["itc_net"]["camt"]) == (77.5, 77.5, 0.0)


@pytest.mark.parametrize("door", ["restock", "lift"])
def test_a_quarantined_piece_put_back_on_sale_keeps_no_reversal(world, october, door):
    """A reversal left on a quarantined piece (a damage cut off after its
    reversal) goes when the piece is restocked or its quarantine lifted."""
    from api.routers.inventory import LiftQuarantineRequest, lift_quarantine_stock_unit
    from api.services import writeoff_itc as wo

    db = world["db"]
    _stocked(db, "GRN-WO-0020")
    _line_bill("GRN-WO-0020", "TAX-WO-20")
    sid = "SU-GRN-WO-0020-0"
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "QUARANTINED"}})
    wo.record_writeoff(db, [db.stock_units.find_one({"stock_id": sid})], source_type="QUARANTINE_DAMAGE",
                       source_id=sid, when=datetime(2026, 10, 5, 6, 0))
    if door == "restock":
        _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="RESTOCK"),
                                    current_user=ADMIN))
    else:
        _run(lift_quarantine_stock_unit(sid, LiftQuarantineRequest(lift_reason="put back by mistake"), ADMIN))
    assert db.itc_writeoff_reversals.count_documents({}) == 0


# ---------------------------------------------------------------------------
# Review round 2: exact to the paisa on every bill shape; a supplier return
# ---------------------------------------------------------------------------

def _three_frames(db, grn_id, cost):
    """Three frames on one receipt, each at `cost` (0: no price)."""
    _po_receipt(db, grn_id, cost=cost)
    priced = {"unit_cost": cost, "cost_price": cost} if cost else {}
    db.stock_units.insert_one({"stock_id": f"SU-{grn_id}-2", "product_id": FRAME, "store_id": STORE,
                               "status": "AVAILABLE", "source_type": "GRN", "source_id": grn_id,
                               "grn_id": grn_id, **priced})


@pytest.mark.parametrize("cost", [0, 333.33], ids=["unpriced", "priced"])
def test_a_header_only_bill_written_off_piece_by_piece_reverses_exactly_its_heads(world, october, cost):
    """A walk-in receipt booked on the AP door (no lines): Rs 25.00 + 25.00
    of GST over three pieces. Rounded piece by piece that was 8.33 x 3 =
    24.99 a head; shared cumulatively, all three reverse exactly 50.00."""
    db = world["db"]
    _three_frames(db, "GRN-WO-0021", cost)
    db.vendor_bills.insert_one({
        "bill_id": "HB-21", "bill_number": "HB-21", "vendor_id": DEALER, "store_id": STORE,
        "grn_id": "GRN-WO-0021", "bill_date": "2026-09-14", "status": "OUTSTANDING",
        "recipient_entity_id": ENTITY, "recipient_gstin": SHOP_GSTIN, "itc_eligible": True,
        "taxable_amount": 999.99, "cgst_total": 25.0, "sgst_total": 25.0, "igst_total": 0.0,
        "tax_amount": 50.0,
    })
    for i in range(3):
        _damage(db, f"SU-GRN-WO-0021-{i}")
    ret = r3b._compute_gstr3b(OCTOBER, STORE)
    assert (ret["itcWriteOffReversed"]["centralTax"], ret["itcWriteOffReversed"]["stateTax"]) == (25.0, 25.0)


def test_a_transfers_aggregate_line_written_off_piece_by_piece_reverses_exactly(two_gstins, october):
    """Three unpriced pieces moved on one transfer invoice whose only line
    names no product: Rs 50.00 IGST, all three written off, exactly 50.00."""
    db = two_gstins["db"]
    _three_frames(db, "GRN-WO-0022", 0)
    sids = [f"SU-GRN-WO-0022-{i}" for i in range(3)]
    _mirror(db, "TRF-22", gstin="", lines=[
        {"product_id": None, "qty": 0.0, "taxable": 999.99, "cgst": 0.0, "sgst": 0.0, "igst": 50.0}])
    db.vendor_bills.update_one({"bill_id": "MB-TRF-22"}, {"$set": {
        "taxable_amount": 999.99, "tax_amount": 50.0, "igst_total": 50.0}})
    db.stock_units.update_many({"stock_id": {"$in": sids}}, {"$set": {
        "status": "AVAILABLE", "store_id": MH_STORE, "source_type": "TRANSFER", "source_id": "TRF-22",
        "transfer_id": None}})
    db.stock_transfers.insert_one({"id": "TRF-22", "items": [{"product_id": FRAME, "received_stock_ids": sids}]})
    for sid in sids:
        _damage(db, sid, MH_ADMIN)
    assert r3b._compute_gstr3b(OCTOBER, MH_STORE)["itcWriteOffReversed"]["integratedTax"] == 50.0


def test_a_piece_shipped_back_to_the_supplier_drops_a_leftover_reversal(world, october):
    """A damage cut off after its reversal leaves the piece quarantined with
    a reversal row; shipped back on a supplier return, the supplier's credit
    note takes the credit back, so the write-off reversal must go."""
    from api.routers import vendor_returns as vr
    from api.services import writeoff_itc as wo

    db = world["db"]
    _stocked(db, "GRN-WO-0023")
    _line_bill("GRN-WO-0023", "TAX-WO-23")
    sid = "SU-GRN-WO-0023-0"
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "QUARANTINED", "rtv_vendor_id": "VR-23"}})
    wo.record_writeoff(db, [db.stock_units.find_one({"stock_id": sid})], source_type="QUARANTINE_DAMAGE",
                       source_id=sid, when=datetime(2026, 10, 5, 6, 0))
    assert db.itc_writeoff_reversals.count_documents({}) == 1
    assert vr._ship_pieces(db, {"return_id": "VR-23", "store_id": STORE, "stock_ids": [sid]}, "u-admin") == []
    assert db.itc_writeoff_reversals.count_documents({}) == 0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0


# ---------------------------------------------------------------------------
# Review round 3: a header-only bill shares its GST by the receipt's own
# costs; unticking 17(5) after a write-off reverses the lost pieces
# ---------------------------------------------------------------------------

def _frames_costing(db, grn_id, costs, taxable):
    """Three frames on one receipt at `costs` (0: no price), billed on a
    header-only bill: `taxable`, Rs 25.00 CGST + 25.00 SGST."""
    _three_frames(db, grn_id, 1.0)
    for i, cost in enumerate(costs):
        priced = {"unit_cost": cost, "cost_price": cost} if cost else {}
        db.stock_units.update_one({"stock_id": f"SU-{grn_id}-{i}"},
                                  {"$unset": {"unit_cost": "", "cost_price": ""}})
        if priced:
            db.stock_units.update_one({"stock_id": f"SU-{grn_id}-{i}"}, {"$set": priced})
    db.vendor_bills.insert_one({
        "bill_id": f"HB-{grn_id}", "bill_number": f"HB-{grn_id}", "vendor_id": DEALER,
        "store_id": STORE, "grn_id": grn_id, "bill_date": "2026-09-14", "status": "OUTSTANDING",
        "recipient_entity_id": ENTITY, "recipient_gstin": SHOP_GSTIN, "itc_eligible": True,
        "taxable_amount": taxable, "cgst_total": 25.0, "sgst_total": 25.0, "igst_total": 0.0,
        "tax_amount": 50.0,
    })


def _rows_of(db, grn_id):
    return [(r["cgst"], r["sgst"]) for r in db.itc_writeoff_reversals.find(
        {"stock_id": {"$regex": f"^SU-{grn_id}-"}}).sort("stock_id", 1)]


@pytest.mark.parametrize("costs,taxable,written_off,expected", [
    # Freight on the bill: the pieces cost Rs 900 of a Rs 1,000 bill. The
    # whole receipt written off still reverses exactly the bill's heads.
    ([300.0, 300.0, 300.0], 1000.0, 3, [(8.33, 8.33), (8.34, 8.34), (8.33, 8.33)]),
    # A bill discount: the pieces kept their Rs 400 PO price. One piece is a
    # third of the receipt, not 400/1000 of the bill.
    ([400.0, 400.0, 400.0], 1000.0, 1, [(8.33, 8.33)]),
    # Unequal costs: each piece's share follows its cost.
    ([666.66, 222.22, 111.11], 999.99, 3, [(16.67, 16.67), (5.55, 5.55), (2.78, 2.78)]),
    # One piece unpriced: no cost base, so every piece shares equally.
    ([900.0, 0, 0], 1000.0, 1, [(8.33, 8.33)]),
], ids=["costs-below-taxable", "costs-above-taxable", "unequal-costs", "one-unpriced"])
def test_a_header_only_bill_shares_its_gst_by_the_receipts_own_costs(
        world, october, costs, taxable, written_off, expected):
    db = world["db"]
    _frames_costing(db, "GRN-WO-0031", costs, taxable)
    for i in range(written_off):
        _damage(db, f"SU-GRN-WO-0031-{i}")
    assert _rows_of(db, "GRN-WO-0031") == expected
    ret = r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]
    assert (ret["centralTax"], ret["stateTax"]) == tuple(round(sum(r[k] for r in expected), 2)
                                                          for k in (0, 1))


def test_unticking_17_5_after_a_write_off_reverses_the_lost_piece(world, october):
    """A walk-in bill booked with the 17(5) tick claims no credit, so a piece
    damaged meanwhile (5 October) reverses nothing. Unticked while September
    is open, the bill claims Rs 373 in September: the lost piece's share of
    it is reversed in October, the month of the loss."""
    from tests.test_gst_input_credit_one_rule import _tick
    from tests.test_receipt_without_po import _on_the_shelf

    db = world["db"]
    walk_in = _on_the_shelf(world, vendor_invoice_no="GIFT-1")
    gift = vd.VendorBillCreate(bill_number="GIFT-1", bill_date="2026-09-14", taxable_amount=7460.0,
                               tax_amount=373.0, total_amount=7833.0, grn_id=walk_in, itc_blocked=True)
    booked = _run(vd.create_vendor_bill(DEALER, gift, current_user=ACCOUNTANT))
    unit = db.stock_units.find_one({"grn_id": walk_in, "product_id": FRAME})
    assert _damage(db, unit["stock_id"])["itc_reversed"]["total"] == 0.0
    assert _tick(booked, False)["itc_eligible"] is True
    sept = r3b._compute_gstr3b(MONTH, STORE)
    # Rs 373 claimed in September; the lost Rs 3,100 frame's share
    # (3100/7460) reversed in October.
    assert (_written_off(sept), _heads(sept["itcAvailable"])) == (0.0, 373.0)
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0
    assert db.itc_writeoff_reversals.count_documents({"period": OCTOBER}) == 1


# ---------------------------------------------------------------------------
# Review round 4: the cost base is the pieces the bill paid for (rejected
# custody excluded); a piece that moved on stays in its transfer's base
# ---------------------------------------------------------------------------

def _rejected_piece(db, grn_id):
    """One rejected piece exactly as grn_rejections.handoff_rejected_units
    mints it: same receipt, no price."""
    sid = f"GRN-REJ-{grn_id}-0-0"
    db.stock_units.insert_one({
        "_id": sid, "stock_id": sid, "store_id": STORE, "product_id": FRAME, "quantity": 1,
        "status": "QUARANTINED", "source_type": "GRN", "source_id": grn_id, "grn_id": grn_id,
        "grn_line_index": 0, "line_unit_seq": -1, "receipt_disposition": "REJECTED",
        "rtv_vendor_id": f"VR-GRN-{grn_id}-0"})


@pytest.mark.parametrize("costs,written_off,expected", [
    # The whole accepted receipt written off reverses exactly the bill's heads.
    ([300.0, 300.0, 300.0], 3, (25.0, 25.0)),
    # One piece takes its cost share: 600 / 900 of the heads.
    ([600.0, 200.0, 100.0], 1, (16.67, 16.67)),
], ids=["whole-receipt", "cost-share"])
def test_a_rejected_piece_on_the_receipt_is_not_in_the_bills_cost_base(
        world, october, costs, written_off, expected):
    db = world["db"]
    _frames_costing(db, "GRN-WO-0041", costs, 900.0)
    _rejected_piece(db, "GRN-WO-0041")
    for i in range(written_off):
        _damage(db, f"SU-GRN-WO-0041-{i}")
    ret = r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]
    assert (ret["centralTax"], ret["stateTax"]) == expected


def test_a_piece_moved_on_by_a_later_transfer_stays_in_its_transfers_cost_base(two_gstins, october):
    """Three priced pieces (600 / 200 / 100) received at Pune on TRF-42's
    aggregate-line mirror bill (Rs 50 IGST). One moves on by TRF-43, which
    restamps its source_id; it is still listed on TRF-42. The Rs 200 piece
    written off reverses 200/900 of 50, not 200/300."""
    db = two_gstins["db"]
    _frames_costing(db, "GRN-WO-0042", [600.0, 200.0, 100.0], 900.0)
    sids = [f"SU-GRN-WO-0042-{i}" for i in range(3)]
    _mirror(db, "TRF-42", gstin="", lines=[
        {"product_id": None, "qty": 0.0, "taxable": 900.0, "cgst": 0.0, "sgst": 0.0, "igst": 50.0}])
    db.vendor_bills.update_one({"bill_id": "MB-TRF-42"}, {"$set": {
        "taxable_amount": 900.0, "tax_amount": 50.0, "igst_total": 50.0}})
    db.stock_units.update_many({"stock_id": {"$in": sids}}, {"$set": {
        "status": "AVAILABLE", "store_id": MH_STORE, "source_type": "TRANSFER", "source_id": "TRF-42",
        "transfer_id": None}})
    db.stock_transfers.insert_one({"id": "TRF-42", "items": [{"product_id": FRAME, "received_stock_ids": sids}]})
    _rehomed(db, sids[0], "TRF-43", MH2_STORE)
    _damage(db, sids[1], MH_ADMIN)
    assert r3b._compute_gstr3b(OCTOBER, MH_STORE)["itcWriteOffReversed"]["integratedTax"] == 11.11


# ---------------------------------------------------------------------------
# Review round 5: a transfer's mirror not booked yet; the loss month; partial
# bills of one receipt
# ---------------------------------------------------------------------------

def test_a_piece_lost_before_its_transfers_mirror_is_booked_reverses_on_the_receiving_gstin(
        two_gstins, october):
    """Received at Pune (TRF-P3), damaged there before Complete books the
    mirror bill: Jharkhand's credit already left with the piece, so nothing is
    reversed on Jharkhand. Booking the mirror reverses it on Maharashtra, in
    the month of the loss."""
    from api.services import writeoff_itc as wo

    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0051")
    _line_bill("GRN-WO-0051", "TAX-WO-51")
    sid = "SU-GRN-WO-0051-0"
    _rehomed(db, sid, "TRF-P3", MH_STORE)
    assert _damage(db, sid, MH_ADMIN)["itc_reversed"]["total"] == 0.0
    assert db.itc_writeoff_reversals.count_documents({}) == 0
    _mirror(db, "TRF-P3")
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "MB-TRF-P3"}))
    row = db.itc_writeoff_reversals.find_one({"_id": sid})
    assert (row["gstin"], row["bill_id"], row["igst"], row["period"]) == (MH_GSTIN, "MB-TRF-P3", 155.0, OCTOBER)
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0


def test_a_bill_booked_after_an_earlier_loss_reverses_in_the_bills_month(world, october):
    """Damaged in August, billed in September: credit cannot be reversed
    before it is claimed, so the reversal is September's."""
    db = world["db"]
    _stocked(db, "GRN-WO-0052")
    sid = "SU-GRN-WO-0052-0"
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "DAMAGED"}})
    db.item_events.insert_one({"stock_id": sid, "event_type": "QUARANTINE_OUT", "to_state": "DAMAGED",
                               "at": datetime(2026, 8, 20, 6, 0)})
    _line_bill("GRN-WO-0052", "TAX-WO-52")
    assert db.itc_writeoff_reversals.find_one({"_id": sid})["period"] == MONTH
    assert _written_off(r3b._compute_gstr3b(MONTH, STORE)) == 155.0


def _part_bill(db, grn_id, bill_id, qty, head):
    db.vendor_bills.insert_one({
        "bill_id": bill_id, "bill_number": bill_id, "vendor_id": DEALER, "store_id": STORE,
        "grn_id": grn_id, "bill_date": "2026-09-14", "status": "OUTSTANDING",
        "recipient_entity_id": ENTITY, "recipient_gstin": SHOP_GSTIN, "itc_eligible": True,
        "taxable_amount": 100.0 * qty, "cgst_total": head, "sgst_total": head, "igst_total": 0.0,
        "tax_amount": 2 * head, "lines": [{"product_id": FRAME, "qty": qty, "taxable": 100.0 * qty,
                                           "cgst": head, "sgst": head, "igst": 0.0}],
    })


@pytest.mark.parametrize("a_first", [True, False], ids=["two-piece-bill-first", "one-piece-bill-first"])
def test_a_receipt_billed_in_parts_at_different_gst_reverses_exactly_what_was_claimed(
        world, october, a_first):
    """Three pieces: bill A covers two (Rs 10 + 10), bill B the third
    (Rs 20 + 20). All three written off, in either order of the bills, take
    back exactly the Rs 30 + 30 claimed."""
    db = world["db"]
    _three_frames(db, "GRN-WO-0053", 0)
    first, second = (("BA-53", 2, 10.0), ("BB-53", 1, 20.0)) if a_first else (("BB-53", 1, 20.0), ("BA-53", 2, 10.0))
    _part_bill(db, "GRN-WO-0053", *first)
    _damage(db, "SU-GRN-WO-0053-0")
    _damage(db, "SU-GRN-WO-0053-1")
    _part_bill(db, "GRN-WO-0053", *second)
    from api.services import writeoff_itc as wo

    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": second[0]}))
    _damage(db, "SU-GRN-WO-0053-2")
    ret = r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]
    assert (ret["centralTax"], ret["stateTax"]) == (30.0, 30.0)


# ---------------------------------------------------------------------------
# Fix round 5 (round-4 review): a crossing whose mirror is not booked yet, on
# the way back too; a part bill after every piece is lost; a piece moved on
# before its transfer completes; a same-GSTIN transfer keeps the receipt bill
# ---------------------------------------------------------------------------

def test_a_piece_sent_back_reverses_on_the_gstin_it_came_back_to(two_gstins, october):
    """JH -> MH (mirror booked) -> back to JH (received, its mirror not booked
    yet), damaged at JH: the Maharashtra crossing no longer holds it, so
    nothing is reversed until the return mirror lands -- then on Jharkhand,
    against that mirror."""
    from api.services import writeoff_itc as wo

    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0061")
    _line_bill("GRN-WO-0061", "TAX-WO-61")
    sid = "SU-GRN-WO-0061-0"
    _mirror(db, "TRF-RA")
    _rehomed(db, sid, "TRF-RA", MH_STORE)
    _rehomed(db, sid, "TRF-RB", STORE)
    assert _damage(db, sid, MH_ADMIN)["itc_reversed"]["total"] == 0.0
    assert db.itc_writeoff_reversals.count_documents({}) == 0
    _mirror(db, "TRF-RB", gstin=SHOP_GSTIN, to_store=STORE, day="2026-10-05")
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "MB-TRF-RB"}))
    row = db.itc_writeoff_reversals.find_one({"_id": sid})
    assert (row["gstin"], row["bill_id"], row["igst"], row["period"]) == (SHOP_GSTIN, "MB-TRF-RB", 155.0, OCTOBER)
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 0.0


@pytest.mark.parametrize("a_first", [True, False], ids=["two-piece-bill-first", "one-piece-bill-first"])
def test_every_piece_lost_before_the_receipts_second_part_bill_reverses_both_bills(
        world, october, a_first):
    """All three pieces lost while only one part bill is booked: the pieces
    past its quantity reverse nothing yet. The second part bill then takes
    back the rest, so exactly the Rs 30 + 30 claimed is reversed."""
    from api.services import writeoff_itc as wo

    db = world["db"]
    _three_frames(db, "GRN-WO-0062", 0)
    first, second = (("BA-62", 2, 10.0), ("BB-62", 1, 20.0)) if a_first else (("BB-62", 1, 20.0), ("BA-62", 2, 10.0))
    _part_bill(db, "GRN-WO-0062", *first)
    for i in range(3):
        _damage(db, f"SU-GRN-WO-0062-{i}")
    _part_bill(db, "GRN-WO-0062", *second)
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": second[0]}))
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": second[0]}))  # a re-run adds nothing
    ret = r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]
    assert (ret["centralTax"], ret["stateTax"]) == (30.0, 30.0)


def test_a_piece_found_after_a_part_bill_top_up_gives_its_share_back(world, october):
    """Two of three pieces lost under bill A (Rs 10 + 10 for two), then bill
    B (Rs 20 + 20 for one): the two lost pieces' slice of Rs 30 is Rs 20.
    One is then found: Rs 10 stays reversed, never the whole top-up."""
    from api.services import writeoff_itc as wo

    db = world["db"]
    _three_frames(db, "GRN-WO-0065", 0)
    _part_bill(db, "GRN-WO-0065", "BA-65", 2, 10.0)
    _damage(db, "SU-GRN-WO-0065-0")
    _damage(db, "SU-GRN-WO-0065-1")
    _part_bill(db, "GRN-WO-0065", "BB-65", 1, 20.0)
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "BB-65"}))
    assert r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]["centralTax"] == 20.0
    _requarantine("SU-GRN-WO-0065-0")
    assert r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]["centralTax"] == 10.0


def test_a_piece_moved_on_before_its_transfer_completes_reverses_when_the_mirror_lands(
        two_gstins, october):
    """Received at Pune on TRF-M1 (not completed), moved on to a second Pune
    shop on TRF-M2 (one GSTIN, which restamps its source), damaged there.
    Completing TRF-M1 books its mirror: the piece is still listed on its
    lines, so Maharashtra's credit on it is reversed then."""
    from api.services import writeoff_itc as wo

    db = two_gstins["db"]
    db.stores.insert_one({"store_id": MH2_STORE, "store_name": "BV Pune 2", "state": "Maharashtra",
                          "state_code": "27", "entity_id": ENTITY, "gstin": MH_GSTIN, "is_active": True})
    _stocked(db, "GRN-WO-0063")
    _line_bill("GRN-WO-0063", "TAX-WO-63")
    sid = "SU-GRN-WO-0063-0"
    _rehomed(db, sid, "TRF-M1", MH_STORE)
    _rehomed(db, sid, "TRF-M2", MH2_STORE)
    assert _damage(db, sid, MH_ADMIN)["itc_reversed"]["total"] == 0.0
    _mirror(db, "TRF-M1")
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "MB-TRF-M1"}))
    row = db.itc_writeoff_reversals.find_one({"_id": sid})
    assert (row["gstin"], row["bill_id"], row["igst"]) == (MH_GSTIN, "MB-TRF-M1", 155.0)
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 155.0


def test_a_piece_moved_between_two_shops_on_one_gstin_keeps_its_receipt_bill(
        two_gstins, shared_gstin, october):
    """No GSTIN crossed, so no mirror bill: the receipt bill still carries
    the piece's credit, reversed on Jharkhand."""
    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0064")
    _line_bill("GRN-WO-0064", "TAX-WO-64")
    sid = "SU-GRN-WO-0064-0"
    _rehomed(db, sid, "TRF-J1", SISTER)
    out = _damage(db, sid, {**ADMIN, "store_ids": [STORE, SISTER]})
    assert out["itc_reversed"] == {"igst": 0.0, "cgst": 77.5, "sgst": 77.5, "total": 155.0}
    assert db.itc_writeoff_reversals.find_one({"_id": sid})["gstin"] == SHOP_GSTIN


def test_a_same_gstin_transfer_of_a_piece_whose_bill_no_return_places_is_held(
        two_gstins, shared_gstin, october):
    """The receipt bill carries credit no return places yet (no recipient
    GSTIN, a linked receipt that cannot be found): moved within the one
    GSTIN, the piece's reversal is held unallocated, as it is unmoved --
    never dropped."""
    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0066")
    _line_bill("GRN-WO-0066", "TAX-WO-66")
    db.vendor_bills.update_one({"grn_id": "GRN-WO-0066"}, {"$set": {
        "recipient_gstin": "", "linked_dc_ids": ["GRN-WO-GONE"]}})
    sid = "SU-GRN-WO-0066-0"
    _rehomed(db, sid, "TRF-J2", SISTER)
    out = _damage(db, sid, {**ADMIN, "store_ids": [STORE, SISTER]})
    assert out["itc_reversed"]["unallocated_total"] == 155.0
    row = db.itc_writeoff_reversals.find_one({"_id": sid})
    assert (row["gstin"], row["unallocated"]) == (None, True)


def test_a_backdated_bill_booked_after_a_later_loss_reverses_in_the_loss_month(world, october):
    """Damaged on 5 October, then a bill dated September is booked: the
    credit was claimed before the loss, so the reversal is October's (the
    loss month), never back-dated into September."""
    db = world["db"]
    _stocked(db, "GRN-WO-0067")
    sid = "SU-GRN-WO-0067-0"
    assert _damage(db, sid)["itc_reversed"]["total"] == 0.0
    _line_bill("GRN-WO-0067", "TAX-WO-67")
    assert db.vendor_bills.find_one({"grn_id": "GRN-WO-0067"})["bill_date"][:7] == MONTH
    assert db.itc_writeoff_reversals.find_one({"_id": sid})["period"] == OCTOBER
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 155.0


# ---------------------------------------------------------------------------
# Fix round 6 (round-5 review): the newest crossing by when the piece moved,
# not by mirror date; a top-up in a filed month gives a found piece's share
# back as a re-claim; a top-up never lands in a filed month
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("order", ["next_day", "same_day"])
def test_two_transfers_completed_out_of_order_reverse_against_the_one_that_delivered_it(
        two_gstins, october, order):
    """JH -> MH on TRF-A (received, not completed), back MH -> JH on TRF-B
    (received and completed: its mirror on JH). TRF-A completes after that,
    so its mirror on MH is the later bill. Damaged at JH: TRF-B delivered
    the piece here, so Jharkhand reverses against MB-TRF-B."""
    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0071")
    _line_bill("GRN-WO-0071", "TAX-WO-71")
    sid = "SU-GRN-WO-0071-0"
    _rehomed(db, sid, "TRF-A", MH_STORE)
    db.stock_transfers.update_one({"id": "TRF-A"}, {"$set": {"received_at": "2026-10-01T05:00:00"}})
    _rehomed(db, sid, "TRF-B", STORE)
    db.stock_transfers.update_one({"id": "TRF-B"}, {"$set": {"received_at": "2026-10-02T05:00:00"}})
    _mirror(db, "TRF-B", gstin=SHOP_GSTIN, to_store=STORE, day="2026-10-03")
    _mirror(db, "TRF-A", day="2026-10-04" if order == "next_day" else "2026-10-03")
    db.vendor_bills.update_one({"bill_id": "MB-TRF-B"}, {"$set": {"created_at": "2026-10-03T01:00:00"}})
    db.vendor_bills.update_one({"bill_id": "MB-TRF-A"}, {"$set": {"created_at": "2026-10-03T02:00:00"}})
    assert _damage(db, sid, MH_ADMIN)["itc_reversed"]["total"] == 155.0
    row = db.itc_writeoff_reversals.find_one({"_id": sid})
    assert (row["gstin"], row["bill_id"], row["igst"]) == (SHOP_GSTIN, "MB-TRF-B", 155.0)
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 0.0


@pytest.mark.parametrize("lost,found,net", [
    (3, 0, 20.0),   # all three lost, a piece with a slice found
    (3, 2, 20.0),   # all three lost, the nil-row piece found
    (2, 0, 10.0),   # two lost, one found
])
def test_a_piece_found_after_its_top_up_month_is_filed_reclaims_its_share(
        world, october, monkeypatch, lost, found, net):
    """Bill A (two pieces, Rs 10 + 10) then bill B (one piece, Rs 20 + 20)
    tops up the lost pieces' rows in October. October is filed; in November
    a piece is found: November re-claims its whole slice of Rs 30 -- its own
    row and its share of the filed top-up -- so the net reversal is exactly
    the still-lost pieces' slice."""
    from api.services import writeoff_itc as wo

    db = world["db"]
    _three_frames(db, "GRN-WO-0072", 0)
    _part_bill(db, "GRN-WO-0072", "BA-72", 2, 10.0)
    for i in range(lost):
        _damage(db, f"SU-GRN-WO-0072-{i}")
    _part_bill(db, "GRN-WO-0072", "BB-72", 1, 20.0)
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "BB-72"}))
    assert r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]["centralTax"] == 10.0 * lost
    db.period_locks.insert_one({"month": 10, "year": 2026})
    _to_november(monkeypatch)
    _requarantine(f"SU-GRN-WO-0072-{found}")
    wo._trim_top_up(db, db.stock_units.find_one({"stock_id": f"SU-GRN-WO-0072-{found}"}),
                    {"product_id": FRAME})  # a repeat re-claims nothing more
    assert r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]["centralTax"] == 10.0 * lost
    nov = r3b._compute_gstr3b("2026-11", STORE)
    back = nov["itcWriteOffReclaimed"]
    assert (back["centralTax"], back["stateTax"]) == (10.0 * lost - net, 10.0 * lost - net)
    # The next bill door sees the re-claim: no fresh top-up for what came back.
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "BB-72"}))
    assert r3b._compute_gstr3b("2026-11", STORE)["itcWriteOffReversed"]["centralTax"] == 0.0


def test_a_part_bill_after_the_loss_month_is_filed_tops_up_in_the_open_month(
        world, october, monkeypatch):
    """Two pieces lost under bill A in October, October filed, bill B booked
    in November: the top-up is November's -- a filed return is never
    re-read."""
    from api.services import writeoff_itc as wo

    db = world["db"]
    _three_frames(db, "GRN-WO-0073", 0)
    _part_bill(db, "GRN-WO-0073", "BA-73", 2, 10.0)
    _damage(db, "SU-GRN-WO-0073-0")
    _damage(db, "SU-GRN-WO-0073-1")
    db.period_locks.insert_one({"month": 10, "year": 2026})
    _to_november(monkeypatch)
    _part_bill(db, "GRN-WO-0073", "BB-73", 1, 20.0)
    wo.after_bill_booked(db, db.vendor_bills.find_one({"bill_id": "BB-73"}))
    top = db.itc_writeoff_reversals.find_one({"topup": True})
    assert (top["period"], top["cgst"], top["sgst"]) == ("2026-11", 10.0, 10.0)
    assert r3b._compute_gstr3b(OCTOBER, STORE)["itcWriteOffReversed"]["centralTax"] == 10.0
    assert r3b._compute_gstr3b("2026-11", STORE)["itcWriteOffReversed"]["centralTax"] == 10.0
