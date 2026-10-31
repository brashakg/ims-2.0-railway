"""Section 17(5) (owner 2026-10-24 "check yourself"; CA 2026-10-23 Q11).

(a) Every expense / services bill can carry the "credit not claimable" tick;
    its DEFAULT comes from one table by expense type (ap_engine.EXPENSE_TYPES),
    and the accountant can still change it per bill.
(b) 17(5)(h): stock written off (lost, stolen, destroyed) takes back the input
    credit claimed on those exact pieces, per GSTIN, in GSTR-3B Table 4(B)(1)
    of the write-off month (services/writeoff_itc), shown on the write-off.
"""
from datetime import datetime

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
    _before_septembers_return_is_due,
    _heads,
    _po_receipt,
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


def test_a_transferred_piece_reverses_on_the_gstin_that_claimed_it(two_gstins, october):
    """A frame moved to the Maharashtra registration on a transfer invoice:
    its credit is the Maharashtra GSTIN's, so the reversal is too."""
    db = two_gstins["db"]
    _stocked(db, "GRN-WO-0008")
    _line_bill("GRN-WO-0008", "TAX-WO-8")
    db.vendor_bills.insert_one({
        "bill_id": "TRF-BILL-8", "source_transfer_id": "TRF-8", "status": "OUTSTANDING",
        "bill_date": "2026-09-20", "recipient_entity_id": ENTITY, "recipient_gstin": MH_GSTIN,
        "itc_eligible": True, "lines": [{"product_id": FRAME, "qty": 1, "taxable": 3100.0,
                                         "gst_rate": 5.0, "cgst": 0.0, "sgst": 0.0, "igst": 155.0}],
    })
    sid = "SU-GRN-WO-0008-0"
    db.stock_units.update_one({"stock_id": sid}, {"$set": {"status": "QUARANTINED", "store_id": MH_STORE,
                                                            "transfer_id": "TRF-8"}})
    mh_admin = {**ADMIN, "store_ids": [STORE, MH_STORE]}
    out = _run(iev.quarantine_release(sid, iev.QuarantineReleaseRequest(disposition="DAMAGE"),
                                      current_user=mh_admin))
    assert out["itc_reversed"] == {"igst": 155.0, "cgst": 0.0, "sgst": 0.0, "total": 155.0}
    assert _written_off(r3b._compute_gstr3b(OCTOBER, MH_STORE)) == 155.0
    assert _written_off(r3b._compute_gstr3b(OCTOBER, STORE)) == 0.0
