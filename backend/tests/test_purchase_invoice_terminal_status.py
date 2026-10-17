"""As-of settlement must not revive cancelled supplier invoices."""

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from api.routers import purchase_invoices as pi
from strict_fakes import StrictDB


@pytest.mark.parametrize("terminal", ["CANCELLED", "cancelled", "VOID", "voided"])
def test_terminal_bill_is_never_shown_as_payable_or_lost_from_status_filter(monkeypatch, terminal):
    db = StrictDB()
    db.seed("vendor_bills", [
        {"bill_id": "DEAD", "vendor_id": "V1", "bill_date": "2020-01-01", "total_amount": 1000, "status": terminal},
        {"bill_id": "LIVE", "vendor_id": "V1", "bill_date": "2020-01-02", "total_amount": 500, "status": "OUTSTANDING"},
    ])
    monkeypatch.setattr(pi, "_get_db", lambda: db)

    def listed(status):
        return asyncio.run(pi.list_purchase_invoices(
            vendor_id=None, status=status, unmatched=None,
            current_user={"user_id": "admin", "roles": ["ADMIN"]}, store_id=None,
        ))["purchase_invoices"]

    dead = listed(terminal)
    assert [(b["bill_id"], b["status"], b["outstanding"]) for b in dead] == [("DEAD", terminal, 0)]
    assert [b["bill_id"] for b in listed("OUTSTANDING")] == ["LIVE"]
    # A presentation stamp never rewrites the financial source document.
    assert db.vendor_bills.find_one({"bill_id": "DEAD"})["status"] == terminal


def test_live_bill_still_uses_payment_date_for_as_of_settlement():
    db = StrictDB()
    db.seed("vendor_payments", [
        {"bill_id": "B1", "amount": 200, "payment_date": "2020-01-01"},
        {"bill_id": "B1", "amount": 300, "payment_date": "2999-01-01"},
    ])
    bill = {"bill_id": "B1", "total_amount": 500, "status": "PAID"}
    pi.stamp_bills_as_of(db, [bill])
    assert (bill["status"], bill["outstanding"], bill["post_dated_money"]) == ("PARTIAL", 300, 300)
