"""
IMS 2.0 -- the rules under 'Purchases this month' (audit F56 + F63)
===================================================================
backend/tests/test_purchases_this_month.py pins the screens; this pins the two
rules they stand on, each written out by hand:

  1. A bill carries the shop its goods landed in (F63). Every door stamps it
     at booking -- the receipt's shop, else the booker's -- and the backfill
     places old bills the same way. Every Purchase tab filters on it.
  2. What we owe is the supplier ledger (F56). AP aging nets money paid past
     a bill's total exactly like on-account money, so aging == ledger for any
     vendor, not just the audit's.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_bill_shop_and_payable_rule.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mongomock  # noqa: E402
import pytest  # noqa: E402

from api.services import ap_engine  # noqa: E402
from test_purchase_bill_one_rule import (  # noqa: E402,F401
    TestEveryDoorEveryReader as _Doors,  # not Test*-named here: not re-collected
    _book_from_grn,
    _door,
    _restore_vendors_and_itc,
)
from test_purchase_invoice import _restore_router  # noqa: E402,F401


# ============================================================================
# 1. Every bill door stamps the shop the goods landed in
# ============================================================================


def test_every_bill_door_stamps_the_shop_the_goods_landed_in():
    """GA was received at S1, GC at S2, GB at S1; the accountant sits at S2.
    A receipt's bill carries the RECEIPT's shop whoever books it; a services
    bill with no receipt carries the booker's."""
    db, cli = _Doors()._world(active="S2")
    assert _book_from_grn(cli, "GA", "A-1")["store_id"] == "S1"
    assert _book_from_grn(cli, "GC", "C-1")["store_id"] == "S2"

    header = _door(
        cli, "V2", bill_number="B-9", bill_date="2026-05-09", grn_id="GB",
        taxable_amount=1000, tax_amount=50, total_amount=1050,
    )
    assert header.status_code == 201, header.text
    assert header.json()["store_id"] == "S1"

    freight = _door(
        cli, "V1", bill_number="FR-9", bill_date="2026-05-09", bill_kind="SERVICES",
        taxable_amount=1000, tax_amount=180, total_amount=1180,
    )
    assert freight.status_code == 201, freight.text
    assert freight.json()["store_id"] == "S2"

    stored = {b["bill_number"]: b.get("store_id") for b in db["vendor_bills"].find()}
    assert stored == {"A-1": "S1", "C-1": "S2", "B-9": "S1", "FR-9": "S2"}


def test_the_backfill_places_old_bills_the_way_booking_does():
    from scripts.backfill_bill_store_id import backfill

    db = mongomock.MongoClient().db
    db["grns"].insert_many(
        [{"grn_id": "G1", "store_id": "S1"}, {"grn_id": "D1", "store_id": "S2"}]
    )
    db["vendor_bills"].insert_many(
        [
            {"bill_id": "B1", "grn_id": "G1"},  # a receipt's bill
            {"bill_id": "B2", "linked_dc_ids": ["D1"]},  # a challan bill
            {"bill_id": "B3", "to_store_id": "S3"},  # a transfer's mirror bill
            {"bill_id": "B4"},  # freight, no receipt
            {"bill_id": "B5", "grn_id": "G1", "store_id": "S9"},  # already placed
        ]
    )

    def shops():
        return {b["bill_id"]: b.get("store_id") for b in db["vendor_bills"].find()}

    before = shops()
    assert backfill(db)["placed"] == 3 and shops() == before  # dry run writes nothing

    out = backfill(db, commit=True)
    assert (out["placed"], out["unplaced"]) == (3, 1)
    assert shops() == {"B1": "S1", "B2": "S2", "B3": "S3", "B4": None, "B5": "S9"}
    assert db["audit_logs"].count_documents({"action": "BILL_STORE_BACKFILL"}) == 3
    assert backfill(db, commit=True)["placed"] == 0  # idempotent


# ============================================================================
# 2. AP aging owes what the ledger owes, whatever the payments look like
# ============================================================================


@pytest.mark.parametrize(
    "payments,owed",
    [
        # 1200 paid against a 1000 bill: the extra 200 still reduces what we owe
        ([{"vendor_id": "V", "bill_id": "A", "amount": 1200.0}], 300.0),
        # on-account money (no bill) nets the same way
        ([{"vendor_id": "V", "bill_id": "A", "amount": 1000.0},
          {"vendor_id": "V", "bill_id": None, "amount": 200.0}], 300.0),
        # paid in full and then some: we owe nothing (aging floors at 0)
        ([{"vendor_id": "V", "bill_id": "A", "amount": 1600.0}], 0.0),
    ],
)
def test_ap_aging_owes_exactly_what_the_supplier_ledger_owes(payments, owed):
    bills = [
        {"bill_id": "A", "vendor_id": "V", "total_amount": 1000.0, "bill_date": "2026-09-01", "due_date": "2026-09-30"},
        {"bill_id": "B", "vendor_id": "V", "total_amount": 500.0, "bill_date": "2026-09-02", "due_date": "2026-10-02"},
    ]
    ledger = ap_engine.build_ledger(bills, payments, [])["closing_balance"]
    assert max(ledger, 0.0) == owed
    assert ap_engine.build_aging(bills, payments, [])["net_payable"] == owed
    by_vendor = ap_engine.build_aging_by_vendor(bills, payments, [])
    assert by_vendor["vendors"][0]["net_payable"] == owed
    assert by_vendor["totals"]["net_payable"] == owed
