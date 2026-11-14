"""Batch b11 (PR #1423): what reaches the P&L, the bank matcher and the
supplier-credit reference.

  L3-03  An approved expense stays a cost after it is sent to the accountant
         and entered (utils/expense_status, one rule for every reader).
  L3-04  A supplier SERVICES bill (e.g. REPAIRS) is a P&L cost when booked.
  RUN-04 A COMPLETED customer return comes off the P&L in the return's own
         period: its taxable value and GST off sales, and the cost of a GOOD
         piece back on sale off the cost of goods.
  L3-22  The bank matcher matches the money an order actually received (the
         payment leg's amount and day), BSON-dated orders included.
  L3-27  Supplier credits get a consecutive reference; a retried request
         answers the credit it already recorded.

mongomock here; the same five were reproduced and re-verified against a real
local Mongo through the real ASGI app (PR #1423 handover).
No emoji (Windows cp1252).
"""
from __future__ import annotations

import asyncio
import io
import os
import pathlib
import re
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.routers.finance import bank_statement as bank  # noqa: E402
from api.routers.finance import pnl as pnl_mod  # noqa: E402
from api.routers.finance import pnl_breakdown as pb  # noqa: E402
from api.utils.expense_status import COMMITTED_EXPENSE_STATUSES, is_committed_expense  # noqa: E402

SU = {"user_id": "su", "roles": ["SUPERADMIN"], "store_ids": ["S1", "S2"], "active_store_id": "S1"}
RANGE = {"from_date": "2026-11-01", "to_date": "2026-11-30"}


@pytest.fixture
def db(monkeypatch):
    d = mongomock.MongoClient()[f"b11_{uuid.uuid4().hex[:8]}"]
    for mod in (pnl_mod, pb):
        monkeypatch.setattr(mod, "_get_db", lambda: d)
    monkeypatch.setattr(pnl_mod, "_payroll_cost", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pnl_mod.je_service, "pnl_adjustments", lambda *_a, **_k: {})
    monkeypatch.setattr(pb, "_store_maps", lambda _db: ({"S1": "E1", "S2": "E1"}, {}))
    monkeypatch.setattr(pb, "_payroll_by_store", lambda *_a, **_k: {})
    monkeypatch.setattr(pb, "_store_name_map", lambda _db: {"S1": "Shop 1", "S2": "Shop 2"})
    return d


def _pnl(store_id=None):
    return asyncio.run(pnl_mod.get_pnl(store_id=store_id, current_user=SU, **RANGE))


def _by_store():
    rows = asyncio.run(pb.get_pnl_by_store(entity_id=None, current_user=SU, **RANGE))["stores"]
    return {r["store_id"]: r for r in rows}


# --------------------------------------------------------------------------
# L3-03
# --------------------------------------------------------------------------


def test_l3_03_an_approved_expense_stays_a_cost_through_the_accountant(db):
    for i, status in enumerate(["APPROVED", "SENT_TO_ACCOUNTANT", "ENTERED", "PENDING", "APPROVING", "REJECTED"]):
        db.expenses.insert_one({"expense_id": f"E{i}", "store_id": "S1", "category": "supplies",
                                "amount": 100.0 * (i + 1), "status": status, "expense_date": "2026-11-05"})
    # APPROVED 100 + SENT_TO_ACCOUNTANT 200 + ENTERED 300; never pending/rejected.
    assert _pnl()["total_expenses"] == 600.0
    assert _by_store()["S1"]["expenses"] == 600.0


def test_l3_03_every_reader_uses_the_one_rule():
    assert set(COMMITTED_EXPENSE_STATUSES) >= {"APPROVED", "SENT_TO_ACCOUNTANT", "ENTERED"}
    assert is_committed_expense("entered") and not is_committed_expense("PENDING")
    assert not is_committed_expense("REJECTED") and not is_committed_expense(None)
    # No reader keeps its own (APPROVED / PAID only) copy of the list.
    api = pathlib.Path(__file__).resolve().parents[1] / "api"
    stale = re.compile(r'\[\s*"APPROVED",\s*"PAID",\s*"approved",\s*"paid"\s*\]'
                       r'|\("APPROVED",\s*"PAID",\s*"SENT_TO_ACCOUNTANT",\s*"REIMBURSED"\)'
                       r'|_ACTUAL_EXPENSE_STATUSES = \("APPROVED",\)')
    hits = [str(p) for p in api.rglob("*.py") if stale.search(p.read_text(encoding="utf-8"))]
    assert hits == [], hits


def test_l3_03_the_cash_drawer_still_counts_an_entered_cash_payout():
    from api.routers.finance import cash_drawer_window as cdw

    class _C:
        def find(self, *_a, **_k):
            return [{"amount": 120.0, "payment_mode": "CASH", "status": "ENTERED",
                     "expense_date": "2026-11-05", "category": "supplies"}]

    class _D:
        def get_collection(self, _n):
            return _C()

    total, _skipped = cdw._cash_expenses_for_window(_D(), "S1", "2026-11-05", "2026-11-05")
    assert total == 120.0


# --------------------------------------------------------------------------
# L3-04
# --------------------------------------------------------------------------


def _bill(bill_id, **kw):
    doc = {"bill_id": bill_id, "vendor_id": "V", "store_id": "S1", "bill_date": "2026-11-04",
           "bill_kind": "SERVICES", "expense_type": "REPAIRS", "taxable_amount": 1000.0,
           "tax_amount": 0.0, "total_amount": 1000.0, "itc_eligible": False, "status": "PAID"}
    doc.update(kw)
    return doc


def test_l3_04_a_services_bill_is_a_pnl_cost_when_booked(db):
    db.vendor_bills.insert_many([
        _bill("REPAIRS"),  # 1000, paid in full
        # GST that is input credit is not a cost; blocked / unclaimable GST is.
        _bill("RENT", expense_type="RENT", taxable_amount=500.0, tax_amount=90.0, itc_eligible=True,
              status="OUTSTANDING", store_id="S2"),
        _bill("FOOD", expense_type="FOOD", taxable_amount=200.0, tax_amount=10.0, itc_eligible=False,
              itc_blocked=True, status="PARTIAL"),
        # Never a P&L expense: stock, transfer mirrors, other months, drafts.
        _bill("GOODS", bill_kind="GOODS", grn_id="G1"),
        _bill("NOKIND", bill_kind=None),
        _bill("MIRROR", source_transfer_id="T1"),
        _bill("OCT", bill_date="2026-10-31"),
        _bill("DRAFT", status="DRAFT"),
    ])
    pnl = _pnl()
    assert pnl["total_expenses"] == 1000.0 + 500.0 + 210.0
    assert pnl["expenses"]["Supplier bills: Repairs and maintenance"] == 1000.0
    rows = _by_store()
    assert (rows["S1"]["expenses"], rows["S2"]["expenses"]) == (1210.0, 500.0)
    assert _pnl(store_id="S2")["total_expenses"] == 500.0


# --------------------------------------------------------------------------
# RUN-04 (the refund and its GOOD-return cost; the stock write-off is a
# separate policy question, not built here)
# --------------------------------------------------------------------------


def _sale(db):
    db.orders.insert_one({
        "order_id": "O1", "store_id": "S1", "status": "DELIVERED", "payment_status": "PAID",
        "created_at": datetime(2026, 11, 4, 5, 0), "grand_total": 3000.0, "tax_amount": 142.86,
        "items": [{"item_id": "L1", "product_id": "P1", "quantity": 3, "unit_price": 1000.0,
                   "item_total": 3000.0, "taxable_value": 2857.14, "gst_rate": 5.0, "cost_at_sale": 500.0}],
    })


def _ret(rid, created_at, condition="GOOD", restock=True, status="COMPLETED", applied=True):
    # A restock that put a GOOD piece back names its unit (returns.
    # _restock_good_items: every reactivated or minted unit).
    landed = [f"U-{rid}"] if applied and restock and condition == "GOOD" else []
    return {"return_id": rid, "order_id": "O1", "store_id": "S1", "status": status,
            "created_at": created_at, "restock_applied": applied, "restock_stock_ids": landed,
            "items": [{"order_item_id": "L1", "product_id": "P1", "return_qty": 1.0,
                       "condition": condition, "restock": restock}],
            "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62}}


def test_run_04_a_completed_good_return_comes_off_sales_tax_and_cost(db):
    _sale(db)
    before = _pnl()
    assert (before["revenue"], before["cogs"]) == (2857.14, 1500.0)
    db.returns.insert_one(_ret("R1", "2026-11-04T06:00:00+00:00"))
    after = _pnl()
    assert after["returns_taxable"] == 952.38 and after["returns_tax"] == 47.62
    assert after["revenue"] == 1904.76
    assert after["tax_collected"] == round(142.86 - 47.62, 2)
    assert after["gross_sales_incl_gst"] == 2000.0
    assert after["cogs"] == 1000.0 and after["returns_cogs_reversal"] == 500.0
    # Return-only overstatement was 952.38 - 500 = 452.38.
    assert round(before["gross_profit"] - after["gross_profit"], 2) == 452.38
    row = _by_store()["S1"]
    assert (row["revenue"], row["cogs"]) == (1904.76, 1000.0)


def test_run_04_dated_by_the_return_and_only_good_restocked_pieces_give_cost_back(db):
    _sale(db)
    db.returns.insert_many([
        _ret("DAMAGED", datetime(2026, 11, 5, 5, 0), condition="DAMAGED"),  # BSON date
        _ret("NOT-BACK", "2026-11-06T05:00:00+00:00", restock=False),
        _ret("PENDING", "2026-11-06T05:00:00+00:00", status="PENDING"),
        _ret("DECEMBER", "2026-12-02T05:00:00+00:00"),  # a later period's return
        {**_ret("ADVANCE", "2026-11-06T05:00:00+00:00"), "items": [],
         "gst_breakup": {"taxable": 0.0, "tax": 0.0}},  # advance refund: never revenue
    ])
    pnl = _pnl()
    # Two completed credit notes in November (DAMAGED, NOT-BACK); no cost back.
    assert pnl["returns_taxable"] == round(2 * 952.38, 2)
    assert pnl["returns_cogs_reversal"] == 0.0 and pnl["cogs"] == 1500.0
    # The cost lines stay cost-only: a store manager sees no reversal figure.
    mgr = {"user_id": "m", "roles": ["STORE_MANAGER"], "store_ids": ["S1"], "active_store_id": "S1"}
    masked = asyncio.run(pnl_mod.get_pnl(store_id="S1", current_user=mgr, **RANGE))
    assert "returns_cogs_reversal" not in masked and "cogs" not in masked


# --------------------------------------------------------------------------
# L3-22
# --------------------------------------------------------------------------


def _import(d, amount, monkeypatch):
    from starlette.datastructures import UploadFile

    monkeypatch.setattr(bank, "_get_db", lambda: d)
    csv = f"Date,Description,Debit,Credit,Balance\n2026-11-04,NEFT IN,0,{amount},5000\n"
    out = asyncio.run(bank.import_bank_statement(
        file=UploadFile(filename="s.csv", file=io.BytesIO(csv.encode())),
        store_id="S1", account_name="B", current_user=SU))
    return out["rows"][0]


@pytest.mark.parametrize("created_at", [datetime(2026, 11, 4, 4, 33), "2026-11-04T10:03:26"])
def test_l3_22_bank_credit_matches_the_money_received_not_the_invoice(monkeypatch, created_at):
    d = mongomock.MongoClient()[f"b11_{uuid.uuid4().hex[:8]}"]
    d.orders.insert_one({
        "order_id": "O-PART", "order_number": "ORD-1", "store_id": "S1", "created_at": created_at,
        "grand_total": 1000.0, "amount_paid": 100.0, "payment_status": "PARTIAL",
        "payments": [
            {"payment_id": "PAY-BANK", "method": "BANK_TRANSFER", "amount": 100.0,
             "received_at": "2026-11-04T06:00:00+00:00", "reference": "UTR1"},
            # Cash goes to the drawer, never the bank as this credit.
            {"payment_id": "PAY-CASH", "method": "CASH", "amount": 50.0,
             "received_at": "2026-11-04T06:00:00+00:00"},
        ],
    })
    got = _import(d, 100, monkeypatch)
    assert got["match_type"] == "RECEIPT" and got["match"]["id"] == "PAY-BANK"
    assert got["match"]["amount"] == 100.0
    # The Rs 1,000 invoice total was never received: no false match.
    assert _import(d, 1000, monkeypatch)["match_type"] == "UNMATCHED"
    assert _import(d, 50, monkeypatch)["match_type"] == "UNMATCHED"


def test_l3_22_a_leg_dated_outside_the_window_or_another_shops_does_not_match(monkeypatch):
    d = mongomock.MongoClient()[f"b11_{uuid.uuid4().hex[:8]}"]
    d.orders.insert_many([
        {"order_id": "OLD", "store_id": "S1", "created_at": datetime(2026, 11, 4),
         "payments": [{"payment_id": "P-OLD", "method": "UPI", "amount": 100.0,
                       "received_at": datetime(2026, 10, 20, 6, 0)}]},
        {"order_id": "OTHER", "store_id": "S2", "created_at": datetime(2026, 11, 4),
         "payments": [{"payment_id": "P-S2", "method": "UPI", "amount": 100.0,
                       "received_at": datetime(2026, 11, 4, 6, 0)}]},
    ])
    assert _import(d, 100, monkeypatch)["match_type"] == "UNMATCHED"


# --------------------------------------------------------------------------
# L3-27
# --------------------------------------------------------------------------


@pytest.fixture
def ap(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import vendors as vendors_pkg
    from api.routers.auth import get_current_user
    from database.repositories.vendor_repository import VendorRepository

    d = mongomock.MongoClient()[f"b11_{uuid.uuid4().hex[:8]}"]
    d.vendors.insert_one({"vendor_id": "V1", "legal_name": "Lens Co", "trade_name": "Lens Co", "is_active": True})
    d.stores.insert_one({"store_id": "S1", "store_code": "BV-S1"})
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: d)
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository", lambda: VendorRepository(d["vendors"]))
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")

    async def _admin():
        return {"user_id": "adm", "roles": ["ADMIN"], "store_ids": ["S1"], "active_store_id": "S1"}

    app.dependency_overrides[get_current_user] = _admin
    return d, TestClient(app)


def _credit(cli, request_id=None, amount=10.0):
    body = {"amount": amount, "date": "2026-11-04", "reason": "scheme", "cn_type": "SCHEME_CN", "store_id": "S1"}
    if request_id:
        body["request_id"] = request_id
    return cli.post("/vendors/V1/debit-notes?store_id=S1", json=body)


def test_l3_27_same_minute_supplier_credits_get_distinct_references(ap):
    d, cli = ap
    a, b = _credit(cli), _credit(cli)
    assert a.status_code == b.status_code == 201, (a.text, b.text)
    numbers = [a.json()["debit_note_number"], b.json()["debit_note_number"]]
    assert numbers == ["DN/BV-S1/26-27/0001", "DN/BV-S1/26-27/0002"]


def test_l3_27_a_retried_request_answers_the_credit_it_recorded(ap):
    d, cli = ap
    first = _credit(cli, "req-b11-0001")
    again = _credit(cli, "req-b11-0001")
    assert first.status_code == 201 and again.status_code in (200, 201)
    assert again.json()["debit_note_id"] == first.json()["debit_note_id"]
    assert d.vendor_debit_notes.count_documents({}) == 1
    # The same id with a different amount is a stale form: refused, nothing written.
    changed = _credit(cli, "req-b11-0001", amount=99.0)
    assert changed.status_code == 409
    assert d.vendor_debit_notes.count_documents({}) == 1


# --------------------------------------------------------------------------
# Round 2 (review of a7979233)
# --------------------------------------------------------------------------

MGR = {"user_id": "m", "roles": ["STORE_MANAGER"], "store_ids": ["S1"], "active_store_id": "S1"}
AREA = {"user_id": "a", "roles": ["AREA_MANAGER"], "store_ids": ["S1", "S2"], "active_store_id": "S1"}
ACCT = {"user_id": "c", "roles": ["ACCOUNTANT"], "store_ids": ["S1", "S2"], "active_store_id": "S1"}


def _order(oid, **kw):
    doc = {"order_id": oid, "store_id": "S1", "status": "DELIVERED", "payment_status": "PAID",
           "created_at": datetime(2026, 10, 4, 5, 0), "grand_total": 1000.0, "tax_amount": 47.62,
           "items": [{"item_id": "L1", "product_id": "P1", "quantity": 1, "unit_price": 1000.0,
                      "item_total": 1000.0, "taxable_value": 952.38, "gst_rate": 5.0, "cost_at_sale": 500.0}]}
    doc.update(kw)
    return doc


def test_run_04_a_return_on_an_order_the_books_already_dropped_is_not_reversed_twice(db):
    db.orders.insert_many([
        # Invoiced counter order, advance refunded through the whole-invoice
        # credit note, then cancelled (returns.py _finish_advance_refund).
        _order("ADV", status="CANCELLED", invoice_number="INV-1", advance_refund_status="COMPLETED",
               advance_refund_credit_note="CN-1", created_at=datetime(2026, 11, 3, 5, 0)),
        # The same with the cancel still pending.
        _order("ADV-PENDING", status="CONFIRMED", invoice_number="INV-2", advance_refund_status="COMPLETED",
               advance_refund_credit_note="CN-2", created_at=datetime(2026, 11, 3, 5, 0)),
        # Shopify order imported cancelled + refunded (RET-HIST credit note).
        _order("SHOP-CANCELLED", status="CANCELLED", created_at=datetime(2026, 11, 3, 5, 0)),
        # A pre-IMS historical order: never in the IMS books.
        _order("HIST", status="HISTORICAL"),
    ])
    for oid in ("ADV", "ADV-PENDING", "SHOP-CANCELLED", "HIST", "MISSING-ORDER"):
        db.returns.insert_one({**_ret(f"R-{oid}", "2026-11-06T05:00:00+00:00"), "order_id": oid,
                               "advance_refund": oid.startswith("ADV")})
    pnl = _pnl()
    assert (pnl["returns_taxable"], pnl["returns_tax"], pnl["returns_gross"]) == (0.0, 0.0, 0.0)
    assert (pnl["revenue"], pnl["tax_collected"], pnl["gross_sales_incl_gst"]) == (0, 0, 0)
    assert pnl["cogs"] == 0
    assert _by_store().get("S1", {}).get("revenue", 0) == 0


def test_run_04_a_damaged_piece_put_back_on_sale_gives_its_cost_back_when_released(db):
    db.orders.insert_one(_order("O9"))
    db.returns.insert_one({**_ret("R9", "2026-10-20T05:00:00+00:00", condition="DAMAGED", applied=False),
                           "order_id": "O9", "quarantined_stock_ids": ["U9", "U10"]})
    db.item_events.insert_many([
        # Released to sale in November: the cost comes back then (once).
        {"stock_id": "U9", "event_type": "quarantine.out", "to_state": "AVAILABLE", "product_id": "P1",
         "at": datetime(2026, 11, 8, 5, 0)},
        {"stock_id": "U9", "event_type": "quarantine.out", "to_state": "AVAILABLE", "product_id": "P1",
         "at": datetime(2026, 11, 20, 5, 0)},
        # Written off / sent back to the supplier: stays a cost.
        {"stock_id": "U10", "event_type": "quarantine.out", "to_state": "DAMAGED", "product_id": "P1",
         "at": datetime(2026, 11, 8, 5, 0)},
    ])
    pnl = _pnl()
    assert pnl["returns_taxable"] == 0.0  # the credit note was October's
    assert pnl["returns_cogs_reversal"] == 500.0 and pnl["cogs"] == -500.0
    assert _by_store()["S1"]["cogs"] == -500.0


def test_l3_04_a_supplier_credit_on_a_services_bill_lowers_its_cost(db):
    db.vendor_bills.insert_many([
        _bill("RENT", expense_type="RENT", taxable_amount=500.0, tax_amount=90.0, total_amount=590.0,
              itc_eligible=True),
        _bill("GOODS", bill_kind="GOODS", grn_id="G1"),
    ])
    db.vendor_debit_notes.insert_many([
        {"debit_note_id": "DN1", "bill_id": "RENT", "vendor_id": "V", "store_id": "S1", "amount": 118.0,
         "date": "2026-11-10", "cn_type": "DISCOUNT_CN"},
        {"debit_note_id": "DN-DEC", "bill_id": "RENT", "vendor_id": "V", "store_id": "S1", "amount": 59.0,
         "date": "2026-12-01", "cn_type": "DISCOUNT_CN"},
        {"debit_note_id": "DN-GOODS", "bill_id": "GOODS", "vendor_id": "V", "store_id": "S1", "amount": 50.0,
         "date": "2026-11-10", "cn_type": "QUALITY_CN"},
    ])
    pnl = _pnl()
    # 500 cost, less the credit's cost share 118 * 500 / 590 = 100.
    assert pnl["expenses"]["Supplier bills: Rent / lease"] == 400.0
    assert pnl["total_expenses"] == 400.0
    assert _by_store()["S1"]["expenses"] == 400.0


@pytest.mark.parametrize("user", [MGR, AREA])
def test_l3_04_supplier_bill_money_stays_with_the_accounts_roles(db, user):
    db.vendor_bills.insert_one(_bill("RENT", expense_type="RENT", taxable_amount=50000.0, total_amount=50000.0))
    db.expenses.insert_one({"expense_id": "E1", "store_id": "S1", "category": "supplies", "amount": 100.0,
                            "status": "APPROVED", "expense_date": "2026-11-05"})
    seen = asyncio.run(pnl_mod.get_pnl(store_id="S1", current_user=user, **RANGE))
    assert not any(h.startswith("Supplier bills") for h in seen["expenses"])
    assert seen["total_expenses"] == 100.0
    assert seen["expenses_partially_restricted"] is True
    acct = asyncio.run(pnl_mod.get_pnl(store_id="S1", current_user=ACCT, **RANGE))
    assert acct["expenses"]["Supplier bills: Rent / lease"] == 50000.0
    assert acct["total_expenses"] == 50100.0


# --------------------------------------------------------------------------
# Review round 2 (PR #1423): RUN-04 on the category P&L, the Goods-back door,
# a retried restock's month; L3-04 a STOCK-typed services bill
# --------------------------------------------------------------------------


def _pnl_range(frm, to):
    return asyncio.run(pnl_mod.get_pnl(store_id=None, current_user=SU, from_date=frm, to_date=to))


def _by_category():
    return asyncio.run(pb.get_pnl_by_category(store_id=None, entity_id=None, current_user=SU, **RANGE))


def test_run_04_category_totals_agree_with_the_whole_business_pnl(db):
    db.orders.insert_one({
        "order_id": "O2", "store_id": "S1", "status": "DELIVERED", "payment_status": "PAID",
        "created_at": datetime(2026, 11, 4, 5, 0), "grand_total": 3000.0, "tax_amount": 142.86,
        "items": [
            {"item_id": "F", "product_id": "PF", "item_type": "FRAME", "quantity": 1, "unit_price": 952.38,
             "item_total": 1000.0, "taxable_value": 952.38, "gst_rate": 5.0, "cost_at_sale": 400.0},
            {"item_id": "L", "product_id": "PL", "item_type": "LENS", "quantity": 2, "unit_price": 952.38,
             "item_total": 2000.0, "taxable_value": 1904.76, "gst_rate": 5.0, "cost_at_sale": 300.0},
        ],
    })
    # One lens back, GOOD and restocked: its value and its cost leave LENS only.
    db.returns.insert_one({
        "return_id": "RC", "order_id": "O2", "store_id": "S1", "status": "COMPLETED",
        "created_at": "2026-11-06T05:00:00+00:00", "restock_applied": True, "restock_stock_ids": ["UL"],
        "items": [{"order_item_id": "L", "product_id": "PL", "return_qty": 1.0, "unit_price": 952.38,
                   "condition": "GOOD", "restock": True}],
        "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62},
    })
    pnl = _pnl()
    cat = _by_category()
    rows = {c["category"]: c for c in cat["categories"]}
    assert cat["total_revenue"] == pnl["revenue"] == 1904.76
    assert round(sum(c["cogs"] for c in rows.values()), 2) == pnl["cogs"] == 700.0
    assert (rows["FRAME"]["revenue"], rows["FRAME"]["cogs"]) == (952.38, 400.0)
    assert (rows["LENS"]["revenue"], rows["LENS"]["cogs"]) == (952.38, 300.0)


def _online_return(rid, refund_id, created_at, **kw):
    doc = {**_ret(rid, created_at, restock=False), "order_id": "O9", "shopify_refund_id": refund_id}
    doc.update(kw)
    return doc


def test_run_04_goods_back_on_a_shopify_refund_gives_the_cost_back(db):
    db.orders.insert_one(_order("O9"))  # October sale, cost 500 a piece
    db.stock_units.insert_many([{"stock_id": u, "product_id": "P1"} for u in ("U1", "U2")])
    db.returns.insert_many([
        # Refunded in October with the goods still out; the goods came back in November.
        _online_return("RA", "RF-A", "2026-10-20T05:00:00+00:00"),
        # Goods back pressed in October BEFORE the November confirm: November's.
        _online_return("RB", "RF-B", "2026-11-02T05:00:00+00:00"),
        # A historical order: booked to a stock-in task, no unit put back.
        _online_return("RC", "RF-C", "2026-10-20T05:00:00+00:00"),
        # Goods back in December: not November's.
        _online_return("RD", "RF-D", "2026-10-20T05:00:00+00:00"),
    ])
    db.shopify_refund_review.insert_many([
        {"review_id": "VA", "shopify_refund_id": "RF-A", "goods_back_at": "2026-11-08T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": ["U1"]}},
        {"review_id": "VB", "shopify_refund_id": "RF-B", "goods_back_at": "2026-10-25T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": ["U2"]}},
        {"review_id": "VC", "shopify_refund_id": "RF-C", "goods_back_at": "2026-11-08T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": [], "stock_in_task": "T1"}},
        {"review_id": "VD", "shopify_refund_id": "RF-D", "goods_back_at": "2026-12-01T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": ["U9"]}},
    ])
    nov = _pnl()
    assert nov["returns_taxable"] == 952.38  # RB's credit note only
    assert nov["returns_cogs_reversal"] == 1000.0  # U1 and U2, once each
    assert _by_store()["S1"]["cogs"] == -1000.0
    oct_ = _pnl_range("2026-10-01", "2026-10-31")
    assert oct_["returns_cogs_reversal"] == 0.0  # October is never changed


def test_run_04_a_retried_restock_gives_the_cost_back_in_the_retry_month(db):
    db.orders.insert_one(_order("O9"))
    db.returns.insert_one({**_ret("RR", "2026-10-20T05:00:00+00:00"), "order_id": "O9",
                           "restock_completed_at": "2026-11-08T05:00:00+00:00"})
    oct_ = _pnl_range("2026-10-01", "2026-10-31")
    assert (oct_["returns_taxable"], oct_["returns_cogs_reversal"]) == (952.38, 0.0)
    nov = _pnl()
    assert (nov["returns_taxable"], nov["returns_cogs_reversal"]) == (0.0, 500.0)


def test_l3_04_a_stock_typed_services_bill_is_not_charged_twice(db):
    db.vendor_bills.insert_many([_bill("STOCK", expense_type="STOCK"), _bill("REPAIRS")])
    assert _pnl()["total_expenses"] == 1000.0
    assert _by_store()["S1"]["expenses"] == 1000.0


# --------------------------------------------------------------------------
# Review round 3 (PR #1423, 1423-MF2) and decision D42
# --------------------------------------------------------------------------


def _confirmed_online_return(rid, refund_id, created_at, **kw):
    # As the real confirm / restock retry writes it when Goods back had put
    # the unit back first: the webhook's lines (GOOD, restock True) kept,
    # restock "applied" with nothing left to restock, no unit of its own.
    doc = {**_ret(rid, created_at), "order_id": "O9", "shopify_refund_id": refund_id,
           "restock_stock_ids": []}
    doc.update(kw)
    return doc


def test_run_04_one_piece_gives_its_cost_back_once_whichever_door_moved_it(db):
    db.orders.insert_one(_order("O9", items=[
        {"item_id": "L1", "product_id": "P1", "item_type": "FRAME", "quantity": 4, "unit_price": 1000.0,
         "item_total": 4000.0, "taxable_value": 3809.52, "gst_rate": 5.0, "cost_at_sale": 500.0}]))
    db.stock_units.insert_many([{"stock_id": u, "product_id": "P1"} for u in ("U1", "U2", "U3", "U4")])
    db.returns.insert_many([
        # Route B: Goods back pressed BEFORE the November confirm.
        _confirmed_online_return("RB", "RF-B", "2026-11-02T05:00:00+00:00"),
        # Route A: refunded in October, restock blocked; Goods back in November,
        # then the restock retry marked it applied with nothing to restock.
        _confirmed_online_return("RA", "RF-A", "2026-10-20T05:00:00+00:00",
                                 restock_completed_at="2026-11-10T05:00:00+00:00"),
        # The return's own restock and a Goods-back row name the same unit.
        _confirmed_online_return("RS", "RF-S", "2026-11-03T05:00:00+00:00", restock_stock_ids=["U3"]),
        # A counter return whose unit really went back: still given back.
        {**_ret("RC", "2026-11-04T05:00:00+00:00"), "order_id": "O9", "restock_stock_ids": ["U4"]},
    ])
    db.shopify_refund_review.insert_many([
        {"review_id": "VB", "shopify_refund_id": "RF-B", "goods_back_at": "2026-10-25T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": ["U1"]}},
        {"review_id": "VA", "shopify_refund_id": "RF-A", "goods_back_at": "2026-11-08T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": ["U2"]}},
        {"review_id": "VS", "shopify_refund_id": "RF-S", "goods_back_at": "2026-11-05T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": ["U3"]}},
    ])
    nov = _pnl()
    # Four physical pieces back on sale (U1..U4), Rs 500 each, once.
    assert nov["returns_cogs_reversal"] == 2000.0
    assert _by_store()["S1"]["cogs"] == round(nov["cogs"], 2)
    cat = _by_category()
    assert round(sum(c["cogs"] for c in cat["categories"]), 2) == nov["cogs"]
    assert _pnl_range("2026-10-01", "2026-10-31")["returns_cogs_reversal"] == 0.0


def test_run_04_a_restock_that_put_nothing_back_gives_no_cost_back(db):
    # restock_applied with no unit landed (nothing resellable, a line the
    # online claim never covered): the piece is not on sale, its cost stays.
    db.orders.insert_one(_order("O9"))
    db.returns.insert_one({**_ret("RN", "2026-11-04T05:00:00+00:00"), "order_id": "O9",
                           "restock_stock_ids": []})
    assert _pnl()["returns_cogs_reversal"] == 0.0


def _historical(oid, **kw):
    # Our Shopify order-history import: a real sale, no cost snapshot (the
    # P&L used the 60% estimate: 0.6 x 952.38 = 571.43 a piece).
    doc = _order(oid, status="REFUNDED", historical=True, import_source="shopify_order_history",
                 items=[{"item_id": f"{oid}-L1", "product_id": "PH", "quantity": 2, "unit_price": 1000.0,
                         "item_total": 2000.0, "taxable_value": 1904.76, "gst_rate": 5.0}],
                 grand_total=2000.0, tax_amount=95.24)
    doc.update(kw)
    return doc


def _hist_note(oid, refund_id, created_at, taxable, tax, **kw):
    # As shopify_ingest._book_historical_refund_credit_notes stamps it: no lines.
    doc = {"return_id": f"RET-HIST-{refund_id}", "shopify_refund_id": refund_id, "order_id": oid,
           "store_id": "S1", "return_type": "CREDIT_NOTE", "source": "shopify_order_history",
           "status": "COMPLETED", "historical": True, "created_at": created_at,
           "gst_breakup": {"gross": round(taxable + tax, 2), "taxable": taxable, "tax": tax}}
    doc.update(kw)
    return doc


def _GOODS(oid):
    return [{"order_item_id": f"{oid}-L1", "product_id": "PH", "return_qty": 1.0, "unit_price": 1000.0}]


def test_d42_c_a_refunded_historical_order_gives_its_estimated_cost_back_with_its_credit_note(db):
    h3 = _historical("H3")
    # Goods back booked RH3's piece on its order line (D65: the press gives
    # back only the pieces it booked).
    h3["items"][0].update(returned_qty=1.0, restocked_refunds={"RF-H3": 1.0})
    db.orders.insert_many([_historical("H1"), _historical("H2"), h3])
    db.returns.insert_many([
        # The whole order refunded by amount-only refunds: the whole-order
        # note, in November. No line says goods came back (D45 b): no cost.
        _hist_note("H1", "orderfull:H1", "2026-11-06T05:00:00+00:00", 1904.76, 95.24),
        # A refund covered by the whole-order note: no money of its own.
        {**_hist_note("H1", "RH1b", "2026-11-06T05:00:00+00:00", 0, 0), "gst_breakup": {},
         "covered_by_ref": "SHOPIFY-HIST-REFUND-orderfull:1"},
        # Half the order refunded (one of two pieces): the importer stamps
        # the goods line the refund gave back.
        _hist_note("H2", "RH2", "2026-11-07T05:00:00+00:00", 952.38, 47.62, goods_lines=_GOODS("H2")),
        # A later refund through the live review on an imported order, its
        # line not restocked by Shopify, then Goods back (the goods came back
        # after all; it hands them to stock-in, no unit): its lines, any
        # condition.
        {**_ret("RH3", "2026-11-08T05:00:00+00:00", condition="DAMAGED", restock=False),
         "order_id": "H3", "shopify_refund_id": "RF-H3",
         "items": [{"order_item_id": "H3-L1", "product_id": "PH", "return_qty": 1.0, "unit_price": 952.38,
                    "condition": "DAMAGED", "restock": False}]},
        # December's note is December's.
        _hist_note("H3", "RH3-DEC", "2026-12-02T05:00:00+00:00", 952.38, 47.62, goods_lines=_GOODS("H3")),
    ])
    db.shopify_refund_review.insert_one(
        {"review_id": "VH3", "shopify_refund_id": "RF-H3", "goods_back_at": "2026-11-09T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": [], "stock_in_task": "T1"}})
    piece = 0.6 * 952.38  # the sale's own 60% estimate, a piece
    nov = _pnl()
    assert nov["returns_taxable"] == round(1904.76 + 952.38 + 952.38, 2)
    assert nov["returns_cogs_reversal"] == round(2 * piece, 2)  # H2 one, H3 one; H1's note none
    assert _by_store()["S1"]["cogs"] == nov["cogs"]
    cat = _by_category()
    assert cat["total_revenue"] == nov["revenue"]
    assert round(sum(c["cogs"] for c in cat["categories"]), 2) == nov["cogs"]
    # The sale's month is untouched; December gets its own note's cost.
    assert _pnl_range("2026-12-01", "2026-12-31")["returns_cogs_reversal"] == round(piece, 2)


def test_d42_b_a_services_bill_typed_stock_is_refused_at_the_bill_door():
    from fastapi import HTTPException

    from api.routers import vendors as vd
    from api.services import ap_engine

    bill = vd.VendorBillCreate(bill_number="S-1", bill_date="2026-11-04", taxable_amount=1000.0,
                               total_amount=1000.0, bill_kind="SERVICES", expense_type="stock")
    with pytest.raises(HTTPException) as err:
        asyncio.run(vd.create_vendor_bill("V", bill, current_user=ACCT))
    assert err.value.status_code == 422
    assert err.value.detail["code"] == "SERVICES_BILL_NOT_STOCK"
    assert "goods bill" in err.value.detail["message"] and "goods receipt" in err.value.detail["message"]
    # One rule: goods with a receipt may still be typed STOCK.
    assert ap_engine.services_stock_refusal(ap_engine.BILL_KIND_GOODS, "STOCK") is None
    assert ap_engine.services_stock_refusal(ap_engine.BILL_KIND_SERVICES, "REPAIRS") is None


# --------------------------------------------------------------------------
# Decision D45 (b) and (c), PR #1423 round 5
# --------------------------------------------------------------------------


def test_d45_b_only_a_note_that_brought_goods_back_gives_a_historical_cost_back(db):
    # Rs 10,000 imported orders (cost 6,000 each); G2 also billed Rs 200 shipping.
    big = [{"item_id": "G-L1", "product_id": "PG", "item_type": "FRAME", "quantity": 1, "unit_price": 10000.0,
            "item_total": 10000.0, "taxable_value": 9523.81, "gst_rate": 5.0, "cost_at_sale": 6000.0}]
    ship = {"item_id": "G-S", "item_type": "SHIPPING", "quantity": 1, "unit_price": 200.0, "item_total": 200.0,
            "taxable_value": 169.49, "gst_rate": 18.0}
    db.orders.insert_many([
        _historical("G1", items=big), _historical("G2", items=big + [ship]),
        _historical("G3", items=big), _historical("G4", items=big), _historical("G5", items=big)])
    db.returns.insert_many([
        # A Rs 500 goodwill refund, stamped by the importer with no lines.
        _hist_note("G1", "RG1", "2026-11-06T05:00:00+00:00", 476.19, 23.81),
        # A shipping-only refund: the importer's note, and the live review's line.
        _hist_note("G2", "RG2", "2026-11-06T05:00:00+00:00", 169.49, 30.51),
        {**_ret("RG2L", "2026-11-07T05:00:00+00:00", restock=False), "order_id": "G2",
         "shopify_refund_id": "RF-G2",
         "items": [{"order_item_id": "G-S", "return_qty": 1.0, "unit_price": 200.0, "condition": "GOOD",
                    "restock": False}],
         "gst_breakup": {"gross": 200.0, "taxable": 169.49, "tax": 30.51}},
        # The whole order refunded (amount-only refunds): the whole-order
        # note. Nothing says goods came back: no cost back.
        _hist_note("G3", "orderfull:G3", "2026-11-08T05:00:00+00:00", 9523.81, 476.19),
        # A full refund of the frame line: the importer stamped its goods line.
        _hist_note("G4", "RG4", "2026-11-09T05:00:00+00:00", 9523.81, 476.19, goods_lines=[
            {"order_item_id": "G-L1", "product_id": "PG", "return_qty": 1.0, "unit_price": 10000.0}]),
        # A live refund of the frame in full that Shopify did NOT restock
        # (no_restock: the customer kept it), and no Goods back: money only.
        {**_ret("RG5", "2026-11-10T05:00:00+00:00", restock=False), "order_id": "G5",
         "shopify_refund_id": "RF-G5",
         "items": [{"order_item_id": "G-L1", "product_id": "PG", "return_qty": 1.0, "unit_price": 10000.0,
                    "condition": "GOOD", "restock": False}],
         "gst_breakup": {"gross": 10000.0, "taxable": 9523.81, "tax": 476.19}},
    ])
    nov = _pnl()
    assert nov["returns_cogs_reversal"] == 6000.0  # G4 only; G1, G2, G3 and G5 give back 0
    assert _by_store()["S1"]["cogs"] == nov["cogs"]
    cat = _by_category()
    assert cat["total_revenue"] == nov["revenue"]
    assert round(sum(c["cogs"] for c in cat["categories"]), 2) == nov["cogs"]


def test_d45_b_the_importer_stamps_only_goods_refunded_in_full(db, monkeypatch):
    from api.routers import returns as rr
    from api.services import shopify_ingest

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)

    order = {**_historical("I1", items=[
        {"item_id": "I1-L1", "product_id": "PI", "shopify_line_item_id": "LI1", "quantity": 1,
         "unit_price": 1000.0, "item_total": 1000.0, "taxable_value": 952.38, "tax_amount": 47.62,
         "gst_rate": 5.0},
        {"item_id": "I1-S", "item_type": "SHIPPING", "quantity": 1, "unit_price": 100.0, "item_total": 100.0,
         "taxable_value": 84.75, "tax_amount": 15.25, "gst_rate": 18.0}]), "shopify_order_id": "SH-I1"}
    db.orders.insert_one(dict(order))

    def refund(rid, paid, lines=True, shipping=0.0):
        return {"id": rid, "processed_at": "2026-11-06T05:00:00+00:00",
                "refund_line_items": [{"line_item_id": "LI1", "quantity": 1, "subtotal": 952.38,
                                       "total_tax": 47.62}] if lines else [],
                "refund_shipping_lines": [{"subtotal": 84.75, "total_tax": 15.25}] if shipping else [],
                "transactions": [{"kind": "refund", "status": "success", "amount": f"{paid:.2f}"}]}

    shopify_ingest._book_historical_refund_credit_notes(db, {
        "id": "SH-I1", "financial_status": "partially_refunded", "refunds": [
            refund("FULL", 1000.0), refund("GOODWILL", 500.0), refund("SHIP", 100.0, lines=False, shipping=100.0)]},
        order)
    docs = {d["shopify_refund_id"]: d for d in db.returns.find({}, {"_id": 0})}
    assert set(docs) == {"FULL", "GOODWILL", "SHIP"}
    assert [g["order_item_id"] for g in docs["FULL"]["goods_lines"]] == ["I1-L1"]
    assert "goods_lines" not in docs["GOODWILL"] and "goods_lines" not in docs["SHIP"]
    assert "items" not in docs["FULL"]  # the restock caps never read these


def test_d45_b_the_importer_stamps_no_goods_for_a_line_shopify_did_not_restock(db, monkeypatch):
    from api.routers import returns as rr
    from api.services import shopify_ingest

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)

    order = {**_historical("I2", items=[
        {"item_id": "I2-L1", "product_id": "PI", "shopify_line_item_id": "LI2", "quantity": 1,
         "unit_price": 1000.0, "item_total": 1000.0, "taxable_value": 952.38, "tax_amount": 47.62,
         "gst_rate": 5.0, "cost_at_sale": 600.0}]), "shopify_order_id": "SH-I2"}
    db.orders.insert_one(dict(order))
    # Refunded in full, "Restock" unticked (no_restock): the customer kept it.
    shopify_ingest._book_historical_refund_credit_notes(db, {
        "id": "SH-I2", "financial_status": "refunded", "refunds": [
            {"id": "KEPT", "processed_at": "2026-11-06T05:00:00+00:00",
             "refund_line_items": [{"line_item_id": "LI2", "quantity": 1, "subtotal": 952.38,
                                    "total_tax": 47.62, "restock_type": "no_restock"}],
             "transactions": [{"kind": "refund", "status": "success", "amount": "1000.00"}]}]},
        order)
    doc = db.returns.find_one({"shopify_refund_id": "KEPT"}, {"_id": 0})
    assert doc["gst_breakup"]["taxable"] == 952.38  # the GST note is booked as before
    assert "goods_lines" not in doc
    assert _pnl()["returns_cogs_reversal"] == 0.0


def _gb_review(db, monkeypatch, results):
    from api.services import shopify_refund as sr

    db.shopify_refund_review.insert_one({"review_id": "VG", "shopify_refund_id": "RF-G", "order_id": "O9",
                                         "store_id": "S1", "return_id": "RG", "proposed_restock": []})
    monkeypatch.setattr(sr, "_merge_fulfilment_context", lambda _o: True)
    monkeypatch.setattr(sr, "_cap_restock_to_returnable", lambda lines, _o, _r: (lines, None, False))
    feed = iter(results)
    monkeypatch.setattr(sr, "_restock_booked", lambda *_a, **_k: next(feed))
    return lambda: sr.goods_back(db, db.shopify_refund_review.find_one({"review_id": "VG"}), user_id="u")


def _two_piece_order(db):
    db.orders.insert_one(_order("O9", items=[
        {"item_id": "L1", "product_id": "P1", "quantity": 2, "unit_price": 1000.0, "item_total": 2000.0,
         "taxable_value": 1904.76, "gst_rate": 5.0, "cost_at_sale": 500.0}]))
    db.stock_units.insert_many([{"stock_id": u, "product_id": "P1"} for u in ("U1", "U2")])


def test_d45_c1_a_partial_goods_back_gives_each_landed_piece_its_cost_back_once(db, monkeypatch):
    _two_piece_order(db)
    db.returns.insert_one({**_online_return("RG", "RF-G", "2026-11-02T05:00:00+00:00"),
                           "items": [{"order_item_id": "L1", "product_id": "P1", "return_qty": 2.0,
                                      "condition": "GOOD", "restock": False}],
                           "gst_breakup": {"gross": 2000.0, "taxable": 1904.76, "tax": 95.24}})
    press = _gb_review(db, monkeypatch, [
        {"applied": False, "restock_stock_ids": ["U1"]},  # U2's write failed
        {"applied": True, "restock_stock_ids": ["U2"]},   # the re-press puts U2 back
    ])
    assert press()["status"] == "not_restocked"
    row = db.shopify_refund_review.find_one({"review_id": "VG"})
    assert row["goods_back_at"] is None  # released for the re-press
    assert [e["stock_id"] for e in row["goods_landed"]] == ["U1"]
    assert _pnl()["returns_cogs_reversal"] == 500.0  # U1 is back on sale: its cost is back
    assert press()["status"] == "restocked"
    assert press()["status"] == "duplicate"
    nov = _pnl()
    assert nov["returns_cogs_reversal"] == 1000.0  # U1 and U2, once each
    assert _by_store()["S1"]["cogs"] == nov["cogs"]
    assert round(sum(c["cogs"] for c in _by_category()["categories"]), 2) == nov["cogs"]


def test_d45_c2_a_restock_waiting_for_its_retry_gives_landed_pieces_back_when_they_landed(db):
    _two_piece_order(db)
    db.returns.insert_one({**_ret("RP", "2026-10-20T05:00:00+00:00"), "order_id": "O9",
                           "restock_applied": False, "restock_stock_ids": ["U1"],
                           "items": [{"order_item_id": "L1", "product_id": "P1", "return_qty": 2.0,
                                      "condition": "GOOD", "restock": True}],
                           "gst_breakup": {"gross": 2000.0, "taxable": 1904.76, "tax": 95.24}})
    db.stock_audit.insert_one({"stock_id": "U1", "prior_status": "SOLD", "new_status": "AVAILABLE",
                               "source": "RETURN_RESTOCK", "return_id": "RP", "at": "2026-10-20T05:00:01+00:00"})
    assert _pnl_range("2026-10-01", "2026-10-31")["returns_cogs_reversal"] == 500.0  # U1 landed in October
    assert _pnl()["returns_cogs_reversal"] == 0.0
    # The November retry puts U2 back; U1 is never counted again.
    db.returns.update_one({"return_id": "RP"}, {"$set": {
        "restock_applied": True, "restock_stock_ids": ["U1", "U2"],
        "restock_completed_at": "2026-11-08T05:00:00+00:00"}})
    db.stock_audit.insert_one({"stock_id": "U2", "prior_status": "SOLD", "new_status": "AVAILABLE",
                               "source": "RETURN_RESTOCK", "return_id": "RP", "at": "2026-11-08T05:00:00+00:00"})
    assert _pnl_range("2026-10-01", "2026-10-31")["returns_cogs_reversal"] == 500.0
    nov = _pnl()
    assert nov["returns_cogs_reversal"] == 500.0
    assert _by_store()["S1"]["cogs"] == nov["cogs"]


def test_d45_c_a_landed_piece_whose_id_was_not_saved_still_gives_its_cost_back_once(db):
    # The unit went back on sale (its audit row is written) but the door then
    # failed to save the id on the return / review row (a fail-soft write).
    _two_piece_order(db)
    db.returns.insert_one({**_online_return("RG", "RF-G", "2026-11-02T05:00:00+00:00"),
                           "items": [{"order_item_id": "L1", "product_id": "P1", "return_qty": 2.0,
                                      "condition": "GOOD", "restock": False}],
                           "gst_breakup": {"gross": 2000.0, "taxable": 1904.76, "tax": 95.24}})
    db.stock_audit.insert_many([
        # Goods back keys its restock on the refund; a second row for the same unit counts once.
        {"stock_id": "U1", "new_status": "AVAILABLE", "source": "RETURN_RESTOCK", "return_id": "RF-G",
         "at": "2026-11-05T05:00:00+00:00"},
        {"stock_id": "U1", "new_status": "AVAILABLE", "source": "RETURN_RESTOCK", "return_id": "RG",
         "at": "2026-11-06T05:00:00+00:00"},
        # Another return's piece, and a set-aside move, are not this return's landing.
        {"stock_id": "U2", "new_status": "AVAILABLE", "source": "RETURN_RESTOCK", "return_id": "OTHER",
         "at": "2026-11-05T05:00:00+00:00"},
        {"stock_id": "U2", "new_status": "QUARANTINED", "source": "RETURN_RESTOCK", "return_id": "RG",
         "at": "2026-11-05T05:00:00+00:00"},
    ])
    nov = _pnl()
    assert nov["returns_cogs_reversal"] == 500.0
    assert _by_store()["S1"]["cogs"] == nov["cogs"]
    assert round(sum(c["cogs"] for c in _by_category()["categories"]), 2) == nov["cogs"]


# --------------------------------------------------------------------------
# Decision D64 (b), PR #1423 round 8
# --------------------------------------------------------------------------


def test_d64_b_goods_back_on_a_not_restocked_historical_refund_gives_cost_back_when_the_goods_landed(db):
    # Rs 10,000 imported orders (cost 6,000 each). Each refund's frame line
    # Shopify did NOT restock; Goods back later said the goods came back.
    big = [{"item_id": "G-L1", "product_id": "PG", "item_type": "FRAME", "quantity": 1, "unit_price": 10000.0,
            "item_total": 10000.0, "taxable_value": 9523.81, "gst_rate": 5.0, "cost_at_sale": 6000.0}]
    # Each press booked its refund's piece on the order line (D65).
    db.orders.insert_many([_historical(o, items=[{**big[0], "returned_qty": 1.0,
                                                  "restocked_refunds": {f"RF-{o}": 1.0}}])
                           for o in ("K1", "K2", "K3")])

    def _live(rid, oid, refund, at):
        return {**_ret(rid, at, restock=False), "order_id": oid, "shopify_refund_id": refund,
                "items": [{"order_item_id": "G-L1", "product_id": "PG", "return_qty": 1.0, "unit_price": 10000.0,
                           "condition": "GOOD", "restock": False}],
                "gst_breakup": {"gross": 10000.0, "taxable": 9523.81, "tax": 476.19}}

    db.returns.insert_many([
        _live("RK1", "K1", "RF-K1", "2026-11-10T05:00:00+00:00"),  # Goods back in December
        _live("RK2", "K2", "RF-K2", "2026-11-12T05:00:00+00:00"),  # Goods back in October, before the note
        _live("RK3", "K3", "RF-K3", "2026-11-14T05:00:00+00:00"),  # Goods back pressed, no landing date
    ])
    # As goods_back stamps a historical order (stock-in task, no unit).
    stamp = {"restock_applied": True, "restock_stock_ids": [], "stock_in_task": "T"}
    db.shopify_refund_review.insert_many([
        {"review_id": "VK1", "shopify_refund_id": "RF-K1", "goods_back_at": "2026-12-03T05:00:00+00:00",
         "goods_restock": stamp},
        {"review_id": "VK2", "shopify_refund_id": "RF-K2", "goods_back_at": "2026-10-30T05:00:00+00:00",
         "goods_restock": stamp},
        {"review_id": "VK3", "shopify_refund_id": "RF-K3", "goods_back_at": None, "goods_restock": stamp},
    ])
    nov = _pnl()
    # The money stays with each credit note, in November.
    assert nov["returns_taxable"] == round(3 * 9523.81, 2)
    # K2 landed before its note: dated at the note. K1 landed in December:
    # November's P&L is never changed by it. K3 has no landing date: no cost.
    assert nov["returns_cogs_reversal"] == 6000.0
    assert _by_store()["S1"]["cogs"] == nov["cogs"]
    assert round(sum(c["cogs"] for c in _by_category()["categories"]), 2) == nov["cogs"]
    dec = _pnl_range("2026-12-01", "2026-12-31")
    assert dec["returns_cogs_reversal"] == 6000.0  # K1, the month its goods landed
    assert dec["returns_taxable"] == 0.0  # no second credit note
    assert _pnl_range("2026-10-01", "2026-10-31")["returns_cogs_reversal"] == 0.0  # never before the note


# --------------------------------------------------------------------------
# Decision D65, PR #1423 round 9: a piece's cost comes back once
# --------------------------------------------------------------------------


def test_d65_goods_back_gives_back_only_the_pieces_it_booked_never_one_a_counter_return_took_back(db):
    # An imported order: frames A and B (Rs 10,000 each, cost 6,000 each) and
    # two pieces of C (Rs 10,000 each, cost 6,000 each).
    def frame(iid, pid, qty=1, **kw):
        return {"item_id": iid, "product_id": pid, "item_type": "FRAME", "quantity": qty, "unit_price": 10000.0,
                "item_total": 10000.0 * qty, "taxable_value": 9523.81 * qty, "gst_rate": 5.0,
                "cost_at_sale": 6000.0, **kw}

    db.orders.insert_one(_historical("M1", items=[
        # Each order line as the doors leave it (returns._claim_returnable_qty):
        # returned_qty counts every unit taken back, restocked_refunds what
        # this refund's restock booked. A: booked by the Goods back press.
        frame("M-A", "PA", returned_qty=1.0, restocked_refunds={"RF-M": 1.0}),
        # B: the counter return took it back; the press capped it out.
        frame("M-B", "PB", returned_qty=1.0),
        # C: one piece back at the counter, the other restocked by the
        # refund's confirm (its mark); the press had nothing left to book.
        frame("M-C", "PC", qty=2, returned_qty=2.0, restocked_refunds={"RF-M": 1.0}),
    ]))

    def piece(iid, pid, **kw):
        return {"order_item_id": iid, "product_id": pid, "return_qty": 1.0, "unit_price": 10000.0,
                "condition": "GOOD", **kw}

    money = {"gross": 10000.0, "taxable": 9523.81, "tax": 476.19}
    db.returns.insert_many([
        # The counter return of B and of one C, in November: their cost comes
        # back with their notes.
        {"return_id": "RC-M", "order_id": "M1", "store_id": "S1", "status": "COMPLETED",
         "created_at": "2026-11-05T05:00:00+00:00", "items": [piece("M-B", "PB"), piece("M-C", "PC")],
         "gst_breakup": {k: 2 * v for k, v in money.items()}},
        # Then Shopify refunds A (not restocked: no_restock), B and both C.
        # The proposal capped B and one C to not restocked (the counter
        # already took them back: the 'IMS already booked a return' row the
        # accountant confirmed anyway).
        {"return_id": "RS-M", "order_id": "M1", "store_id": "S1", "status": "COMPLETED",
         "shopify_refund_id": "RF-M", "created_at": "2026-11-10T05:00:00+00:00",
         "items": [piece("M-A", "PA", restock=False), piece("M-B", "PB", restock=False),
                   piece("M-C", "PC", restock=True), piece("M-C", "PC", restock=False)],
         "gst_breakup": {k: 4 * v for k, v in money.items()}},
    ])
    # Goods back pressed in December: A booked, its stock-in task raised.
    db.shopify_refund_review.insert_one(
        {"review_id": "VM", "shopify_refund_id": "RF-M", "goods_back_at": "2026-12-03T05:00:00+00:00",
         "goods_restock": {"restock_applied": True, "restock_stock_ids": [], "stock_in_task": "T"}})
    nov = _pnl()
    # B and one C at the counter, the other C restocked by the refund.
    assert nov["returns_cogs_reversal"] == 3 * 6000.0
    assert _by_store()["S1"]["cogs"] == nov["cogs"]
    assert round(sum(c["cogs"] for c in _by_category()["categories"]), 2) == nov["cogs"]
    # December: A only, the one piece the press booked. B and the second C
    # came back once already; their cost is never given back a second time.
    dec = _pnl_range("2026-12-01", "2026-12-31")
    assert dec["returns_cogs_reversal"] == 6000.0
    # Four pieces came back in all: four costs, across both months.
    assert nov["returns_cogs_reversal"] + dec["returns_cogs_reversal"] == 4 * 6000.0


# --------------------------------------------------------------------------
# Decision D66, PR #1423 round 10: one source for a historical piece's cost
# --------------------------------------------------------------------------

# Every order the counter return, the webhook, the confirm (or AUTO post) and
# Goods back can run in. counter: B at the counter; webhook: Shopify refunds A
# and B; confirm: the accountant confirms the queued row (blocked: the confirm
# could not book and left the restock open); goods: Goods back is pressed.
D66_DOORS = [
    ("off", "counter webhook confirm"), ("off", "counter webhook confirm goods"),
    ("off", "webhook counter confirm"), ("off", "webhook counter confirm goods"),
    ("off", "webhook counter goods confirm"), ("off", "webhook goods counter confirm"),
    ("off", "webhook confirm counter"), ("off", "webhook confirm goods counter"),
    ("off", "webhook blocked counter goods"), ("off", "webhook counter blocked goods"),
    # The /restock retry door on a blocked row: its stock-in task not saved
    # (nothing booked, yet the door stamps restock_completed_at), then saved.
    ("off", "webhook blocked retryfail goods"), ("off", "webhook blocked retryfail retry goods"),
    ("off", "webhook blocked retryfail counter goods"),
    ("on", "counter webhook confirm"), ("on", "counter webhook confirm goods"),
    ("on", "webhook counter"), ("on", "webhook counter goods"),
]


class _Positional:
    """mongomock writes items.$ to the FIRST element whatever $elemMatch
    matched (real Mongo writes the matched one) and cannot $addToSet by
    index, so the real order-line claims run through this: the element is
    found the way Mongo finds it, then written."""

    def __init__(self, coll):
        self.coll = coll

    def __getattr__(self, name):
        return getattr(self.coll, name)

    def find_one_and_update(self, match, update, **_kw):
        doc = self.coll.find_one(match)
        if doc is None:
            return None
        probe = mongomock.MongoClient().p.items
        probe.insert_many([{"_i": i, **it} for i, it in enumerate(doc["items"])])
        el = doc["items"][probe.find_one(match["items"]["$elemMatch"])["_i"]]
        for op, body in update.items():  # $inc / $addToSet / $pull on items.$.<path>
            for key, v in body.items():
                *path, last = key.split(".")[2:]
                tgt = el
                for p in path:
                    tgt = tgt.setdefault(p, {})
                if op == "$inc":
                    tgt[last] = tgt.get(last, 0) + v
                elif op == "$addToSet":
                    tgt[last] = tgt.get(last, []) + ([] if v in tgt.get(last, []) else [v])
                else:
                    tgt[last] = [x for x in tgt.get(last, []) if x != v]
        self.coll.replace_one({"_id": doc["_id"]}, doc)
        return doc


@pytest.mark.parametrize("a_kept", ["return", "no_restock"])
@pytest.mark.parametrize("auto, doors", D66_DOORS)
def test_d66_each_piece_cost_comes_back_once_whatever_order_the_doors_run(db, monkeypatch, auto, doors, a_kept):
    """Each door books through the REAL code (the counter's atomic claim,
    the Shopify caps and _hold_returned_qty's per-refund mark); the doc
    inserts mirror the doors. A (FRAME) and B (SUNGLASS) are one piece each,
    cost 6,000. B always comes back once (the counter, or the refund). A
    comes back when Shopify restocked it or Goods back booked it. Each door
    runs on its own day; no door may change a day already closed."""
    from api.routers import returns as rr
    from api.routers.finance._shared import customer_return_adjustments
    from api.services import shopify_refund as sr

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)
    line = {"quantity": 1, "unit_price": 10000.0, "item_total": 10000.0, "taxable_value": 9523.81,
            "gst_rate": 5.0, "cost_at_sale": 6000.0}
    db.orders.insert_one(_historical("D66", items=[
        {**line, "item_id": "D66-A", "product_id": "PA", "item_type": "FRAME"},
        {**line, "item_id": "D66-B", "product_id": "PB", "item_type": "SUNGLASS"}]))
    money = {"gross": 10000.0, "taxable": 9523.81, "tax": 476.19}
    st = {"day": 1, "queued": False, "posted": False, "pressed": False, "proposed": []}

    def order():
        return db.orders.find_one({"order_id": "D66"}, {"_id": 0})

    def cap(lines):
        return sr._cap_restock_to_returnable(lines, order(), "RF-D66")

    def post(at, blocked=False):
        st["posted"] = True
        db.returns.insert_one({
            "return_id": "RS-D66", "order_id": "D66", "store_id": "S1", "status": "COMPLETED",
            "shopify_refund_id": "RF-D66", "created_at": at, "restock_applied": not blocked,
            "items": [ln.model_dump() for ln in st["proposed"]],
            "gst_breakup": {k: 2 * v for k, v in money.items()}})
        if not blocked:  # _post_locked: re-capped, then booked (_restock_booked)
            sr._hold_returned_qty(order(), cap(st["proposed"])[0], "RF-D66")

    def run(door, at):
        if door == "counter":
            b = order()["items"][1]
            if rr._line_purchased_qty(b) - rr._units_already_back("D66", b)[0] >= 1 \
                    and rr._claim_returnable_qty("D66", b, 1.0):
                db.returns.insert_one({
                    "return_id": "RC-D66", "order_id": "D66", "store_id": "S1", "status": "COMPLETED",
                    "created_at": at, "gst_breakup": money,
                    "items": [{"order_item_id": "D66-B", "product_id": "PB", "return_qty": 1.0,
                               "unit_price": 9523.81, "condition": "GOOD"}]})
        elif door == "webhook":
            lines = [rr.ReturnLine(order_item_id="D66-A", product_id="PA", return_qty=1, unit_price=9523.81,
                                   restock=a_kept == "return"),
                     rr.ReturnLine(order_item_id="D66-B", product_id="PB", return_qty=1, unit_price=9523.81)]
            st["proposed"], overlapped, _ = cap(lines)
            st["queued"] = auto == "off" or overlapped
            if st["queued"]:
                db.shopify_refund_review.insert_one({
                    "review_id": "V-D66", "shopify_refund_id": "RF-D66", "order_id": "D66", "store_id": "S1",
                    "return_id": "RS-D66", "goods_back_at": None,
                    "proposed_restock": [ln.model_dump() for ln in st["proposed"]]})
            else:
                post(at)
        elif door in ("confirm", "blocked"):
            if st["queued"] and not st["posted"]:
                post(at, blocked=door == "blocked")
        elif door in ("retry", "retryfail") and st["posted"]:
            # returns.retry_restock: _restock_booked, then the doc stamped
            # whatever it booked (restock_applied False when nothing was).
            monkeypatch.setattr(sr, "raise_stock_in_task", lambda *a, **k: {
                "applied": door == "retry", "restocked": [], "restock_stock_ids": []})
            res = sr._restock_booked(order(), cap(st["proposed"])[0], "RF-D66", lambda _l: {}) or {"applied": True}
            db.returns.update_one({"return_id": "RS-D66"}, {"$set": {
                "restock_applied": bool(res.get("applied")), "restock_completed_at": at}})
        elif door == "goods" and st["queued"]:  # the REAL Goods back door
            res = _real_goods_back(db, monkeypatch, sr, rr, at)("V-D66")
            st["pressed"] = st["pressed"] or any(b["order_item_id"] == "D66-A" for b in res["booked_lines"])

    closed = []
    for door in doors.split():
        st["day"] += 1
        run(door, f"2026-11-{st['day']:02d}T05:00:00+00:00")
        to = f"2026-11-{st['day']:02d}"
        closed.append((to, customer_return_adjustments(db, {}, from_date="2026-11-01", to_date=to)))
    out = customer_return_adjustments(db, {}, by="category")
    a_back = a_kept == "return" or st["pressed"]
    assert out.get("FRAME", {}).get("cogs_reversal", 0.0) == (6000.0 if a_back else 0.0)
    assert out["SUNGLASS"]["cogs_reversal"] == 6000.0, "B's cost comes back exactly once"
    for to, then in closed:  # D64 b: a closed day is never changed after the fact
        assert customer_return_adjustments(db, {}, from_date="2026-11-01", to_date=to) == then, to


def test_d66_the_counter_never_takes_back_a_piece_an_imported_note_already_gave_back(db, monkeypatch):
    """An imported RET-HIST note's goods_lines are what the P&L gives cost
    back for, so the counter's returnable count (the one every door reads)
    counts them too: on a partly refunded imported order the counter can no
    longer take back (refund and give cost back for) the same piece again."""
    from api.routers import returns as rr
    from api.routers.finance._shared import customer_return_adjustments

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)
    db.orders.insert_one(_historical("HC", status="PARTIALLY_REFUNDED"))  # 2 pieces of HC-L1
    db.returns.insert_one(_hist_note("HC", "RHC", "2026-11-03T05:00:00+00:00", 952.38, 47.62,
                                     goods_lines=_GOODS("HC")))

    def counter(n, at):  # the counter door's own check, then its atomic claim
        line = db.orders.find_one({"order_id": "HC"})["items"][0]
        if rr._line_purchased_qty(line) - rr._units_already_back("HC", line)[0] < 1 \
                or not rr._claim_returnable_qty("HC", line, 1.0):
            return False
        db.returns.insert_one({"return_id": f"RC-HC{n}", "order_id": "HC", "store_id": "S1",
                               "status": "COMPLETED", "created_at": at,
                               "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62},
                               "items": [{"order_item_id": "HC-L1", "product_id": "PH", "return_qty": 1.0,
                                          "unit_price": 952.38, "condition": "GOOD"}]})
        return True

    assert counter(1, "2026-11-05T05:00:00+00:00")  # the piece Shopify never refunded
    assert not counter(2, "2026-11-06T05:00:00+00:00")  # the imported note already took the other back
    # Two pieces came back in all: two costs (the sale's 60% estimate each).
    assert customer_return_adjustments(db, {})["S1"]["cogs_reversal"] == round(2 * 0.6 * 952.38, 2)


def _real_goods_back(db, monkeypatch, sr, rr, at):
    """The REAL Goods back door (shopify_refund.goods_back), pressed at `at`:
    the order read from this db, the stock-in task saved."""
    from datetime import timezone as _tz

    class _At(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(at).astimezone(tz or _tz.utc)

    monkeypatch.setattr(sr, "datetime", _At)
    monkeypatch.setattr(rr, "_load_order_for_restock",
                        lambda oid: db.orders.find_one({"order_id": oid}, {"_id": 0}))
    monkeypatch.setattr(sr, "raise_stock_in_task", lambda *a, **k: {
        "applied": True, "restocked": [], "restock_stock_ids": [], "stock_in_task": "T"})
    return lambda review_id: sr.goods_back(db, db.shopify_refund_review.find_one({"review_id": review_id}),
                                           user_id="u")


def test_d64_b_a_goods_back_unit_on_a_line_the_confirm_also_lists_is_dated_at_the_press(db, monkeypatch):
    """Codex/Claude review of 34048de7: one Shopify refund lists order line L
    twice (no_restock 1, cancel 1). AUTO off. The counter takes one piece of
    L back, the confirm then books nothing (the cap), and the days to the
    confirm are closed. Goods back later books the other piece: its cost
    comes back on the press day, never inside the closed days."""
    from api.routers import returns as rr
    from api.routers.finance._shared import customer_return_adjustments
    from api.services import shopify_refund as sr

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)
    db.orders.insert_one(_historical("SL", items=[
        {"item_id": "SL-L", "product_id": "PL", "item_type": "FRAME", "quantity": 2, "unit_price": 10000.0,
         "item_total": 20000.0, "taxable_value": 19047.62, "gst_rate": 5.0, "cost_at_sale": 6000.0}]))
    money = {"gross": 10000.0, "taxable": 9523.81, "tax": 476.19}

    def order():
        return db.orders.find_one({"order_id": "SL"}, {"_id": 0})

    def cap(lines):
        return sr._cap_restock_to_returnable(lines, order(), "RF-SL")[0]

    # Day 2, the webhook (AUTO off): queued with the capped proposal.
    proposed = cap([rr.ReturnLine(order_item_id="SL-L", product_id="PL", return_qty=1, unit_price=9523.81,
                                  restock=False),
                    rr.ReturnLine(order_item_id="SL-L", product_id="PL", return_qty=1, unit_price=9523.81)])
    assert [(ln.return_qty, ln.restock) for ln in proposed] == [(1, False), (1, True)]
    db.shopify_refund_review.insert_one({
        "review_id": "V-SL", "shopify_refund_id": "RF-SL", "order_id": "SL", "store_id": "S1",
        "return_id": "RS-SL", "goods_back_at": None, "proposed_restock": [ln.model_dump() for ln in proposed]})
    # Day 3, the counter takes one piece of L back.
    line = order()["items"][0]
    assert rr._line_purchased_qty(line) - rr._units_already_back("SL", line)[0] >= 1
    assert rr._claim_returnable_qty("SL", line, 1.0)
    db.returns.insert_one({"return_id": "RC-SL", "order_id": "SL", "store_id": "S1", "status": "COMPLETED",
                           "created_at": "2026-11-03T05:00:00+00:00", "gst_breakup": money,
                           "items": [{"order_item_id": "SL-L", "product_id": "PL", "return_qty": 1.0,
                                      "unit_price": 9523.81, "condition": "GOOD"}]})
    # Day 4, the confirm: re-capped, booked through _restock_booked (nothing
    # left to book), the doc keeps the webhook's lines.
    monkeypatch.setattr(sr, "raise_stock_in_task", lambda *a, **k: {
        "applied": True, "restocked": [], "restock_stock_ids": []})
    assert sr._restock_booked(order(), cap(proposed), "RF-SL", lambda _l: {})["applied"]
    assert float((order()["items"][0].get("restocked_refunds") or {}).get("RF-SL") or 0) == 0.0
    db.returns.insert_one({"return_id": "RS-SL", "order_id": "SL", "store_id": "S1", "status": "COMPLETED",
                           "shopify_refund_id": "RF-SL", "created_at": "2026-11-04T05:00:00+00:00",
                           "restock_applied": True, "items": [ln.model_dump() for ln in proposed],
                           "gst_breakup": {k: 2 * v for k, v in money.items()}})
    closed = customer_return_adjustments(db, {}, from_date="2026-11-01", to_date="2026-11-05")
    assert closed["S1"]["cogs_reversal"] == 6000.0  # the counter's piece only
    # Day 6, Goods back books the other piece.
    res = _real_goods_back(db, monkeypatch, sr, rr, "2026-11-06T05:00:00+00:00")("V-SL")
    assert res["status"] == "stock_in", res
    assert float(order()["items"][0]["restocked_refunds"]["RF-SL"]) == 1.0
    # The closed days never change; the press's piece lands on its own day.
    assert customer_return_adjustments(db, {}, from_date="2026-11-01", to_date="2026-11-05") == closed
    assert customer_return_adjustments(db, {}, from_date="2026-11-06", to_date="2026-11-06")["S1"][
        "cogs_reversal"] == 6000.0
    assert customer_return_adjustments(db, {})["S1"]["cogs_reversal"] == 2 * 6000.0  # two pieces, once each


def _import_refund(db, order, refund_id, line_item_id, at="2026-11-08T05:00:00+00:00"):
    from api.services import shopify_ingest

    shopify_ingest._book_historical_refund_credit_notes(db, {
        "id": order["shopify_order_id"], "financial_status": "partially_refunded", "refunds": [
            {"id": refund_id, "processed_at": at,
             "refund_line_items": [{"line_item_id": line_item_id, "quantity": 1, "subtotal": 952.38,
                                    "total_tax": 47.62}],
             "transactions": [{"kind": "refund", "status": "success", "amount": "1000.00"}]}]}, order)


def _imported_two_piece(db, oid):
    order = {**_historical(oid, items=[
        {"item_id": f"{oid}-L1", "product_id": "PI", "shopify_line_item_id": f"LI-{oid}", "quantity": 2,
         "unit_price": 1000.0, "item_total": 2000.0, "taxable_value": 1904.76, "tax_amount": 95.24,
         "gst_rate": 5.0, "cost_at_sale": 600.0}]), "shopify_order_id": f"SH-{oid}"}
    db.orders.insert_one(dict(order))
    return order


def _counter_claim(db, rr, oid, n, at, precheck=True):
    line = db.orders.find_one({"order_id": oid})["items"][0]
    if precheck and rr._line_purchased_qty(line) - rr._units_already_back(oid, line)[0] < 1:
        return False
    if not rr._claim_returnable_qty(oid, line, 1.0):
        return False
    db.returns.insert_one({"return_id": f"RC-{oid}{n}", "order_id": oid, "store_id": "S1", "status": "COMPLETED",
                           "created_at": at, "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62},
                           "items": [{"order_item_id": f"{oid}-L1", "product_id": "PI", "return_qty": 1.0,
                                      "unit_price": 952.38, "condition": "GOOD"}]})
    return True


def test_d66_a_re_import_never_gives_back_cost_for_a_piece_the_counter_already_took_back(db, monkeypatch):
    """Both pieces of an imported order went back at the counter; Shopify
    then refunded one of them too, its webhook lost, and the import was run
    again. The re-import's credit note is booked (the money is the GST
    note's), but it brought no goods back: each piece's cost comes back once."""
    from api.routers import returns as rr

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)
    order = _imported_two_piece(db, "RI")
    assert _counter_claim(db, rr, "RI", 1, "2026-11-03T05:00:00+00:00")
    assert _counter_claim(db, rr, "RI", 2, "2026-11-04T05:00:00+00:00")
    _import_refund(db, order, "RF-RI", "LI-RI")
    note = db.returns.find_one({"shopify_refund_id": "RF-RI"}, {"_id": 0})
    assert note["gst_breakup"]["taxable"] == 952.38  # the GST note is booked as before
    assert not note.get("goods_lines")
    assert _pnl()["returns_cogs_reversal"] == 2 * 600.0  # two pieces, once each


def test_d66_the_imported_note_books_its_piece_so_two_counter_returns_at_once_cannot_both_take_the_last(
        db, monkeypatch):
    """The import books the piece its note brought back on the order line (the
    counter's own atomic claim, with the refund's mark), so the atomic claim --
    the only guard two counter returns running at the same moment both pass
    the pre-check of -- sees it."""
    from api.routers import returns as rr

    monkeypatch.setattr(rr, "_orders_coll", lambda: _Positional(db.orders))
    monkeypatch.setattr(rr, "_returns_coll", lambda: db.returns)
    order = _imported_two_piece(db, "RJ")
    _import_refund(db, order, "RF-RJ", "LI-RJ")
    line = db.orders.find_one({"order_id": "RJ"})["items"][0]
    assert line.get("returned_qty") == 1.0 and line["restocked_refunds"]["RF-RJ"] == 1.0
    # Both pass the pre-check before either claims (the race), then claim.
    assert rr._line_purchased_qty(line) - rr._units_already_back("RJ", line)[0] == 1.0
    assert _counter_claim(db, rr, "RJ", 1, "2026-11-09T05:00:00+00:00", precheck=False)
    assert not _counter_claim(db, rr, "RJ", 2, "2026-11-09T05:00:00+00:00", precheck=False)
    assert _pnl()["returns_cogs_reversal"] == 2 * 600.0
