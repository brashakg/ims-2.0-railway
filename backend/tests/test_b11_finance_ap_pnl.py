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
    return {"return_id": rid, "order_id": "O1", "store_id": "S1", "status": status,
            "created_at": created_at, "restock_applied": applied,
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
        "created_at": "2026-11-06T05:00:00+00:00", "restock_applied": True,
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
